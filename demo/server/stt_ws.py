"""Streaming Zipformer backend speech-to-text over a WebSocket, with live interim words.

The browser streams PCM16 @ 16 kHz mono frames; a server-side **Silero VAD** gates
them into utterances; each utterance is transcribed by the customer's self-hosted
**streaming Zipformer** WebSocket server (`ZipformerSTTService`). Silero owns
endpointing + barge-in; the recognizer streams partials *during* speech and
finalizes ~immediately at end-of-speech.

Why Zipformer (vs the previous Chirp 3 path): Phase 1
(`benchmark/stt-compare/compare_stt.py`) measured the in-region streaming server at
**~134 ms end-of-audio→final** (flat) vs Chirp-for-Thai's ~744 ms p50 (up to ~2.8 s
tail; Chirp finalizes the whole utterance at stream close, cross-Pacific). This is a
single-path swap — Chirp and its batch/speculative machinery were removed.

Worker threads keep things responsive:
  - the **VAD gate** thread does endpointing (speech_begin / end-of-speech via
    SILENCE_HANG_MS) and barge-in, opens a streaming session at speech start and
    feeds it live PCM, and closes it at end-of-speech. It is never blocked by the
    recognizer, so end-of-speech is detected promptly.
  - the **streaming STT** thread drains each session through the Zipformer WS and
    emits `stt_interim` (partials) + `stt_final` (end-of-speech). `recognize_ms` is
    measured end-of-audio → final, so it stays comparable to the old batch number
    and to the client's `stt_final − speech_end`.

This socket does speech-to-text only; the final transcript feeds the existing
/api/session turn flow. Events (a drop-in for the browser Web Speech API the
frontend used):

    {"type": "ready",       "sample_rate": 16000}
    {"type": "speech_begin"}                         # caller started → barge-in
    {"type": "stt_interim", "text": "<thai-so-far>"} # growing transcript
    {"type": "speech_end",  "endpoint_ms": <float>}  # trailing silence detected
    {"type": "stt_final",   "text": "<thai>", "recognize_ms": <float>}  # finalized → send a turn
    {"type": "turn_empty",  "recognize_ms": <float>} # utterance was silence/noise
    {"type": "error", "message": "...", "fatal": bool}

torch / silero-vad are imported lazily inside this module's functions (never at
import time), and the Zipformer engine (numpy / websockets) is imported lazily in
`_build_engines`, so importing `demo.server.app` stays light — preserving CLAUDE.md
gotcha 11. If the engines can't be built (missing torch, …) we send a `fatal` error
and the frontend falls back to the browser Web Speech API.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import threading
import time
from typing import Any, Callable

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger("demo.stt")

# Audio contract with the mic-worklet (demo/frontend/public/mic-worklet.js).
STT_SAMPLE_RATE = 16000
_BYTES_PER_MS = STT_SAMPLE_RATE * 2 // 1000  # 16-bit mono

# Silero gating — matches the reference (web/server.py).
# All three are env-tunable latency knobs (aggressive defaults; raise to trade
# latency back for robustness). See README "Latency tuning".
SILERO_THRESHOLD = float(os.environ.get("AAX6_VAD_THRESHOLD", "0.4"))
# Trailing silence (after speech) that ends an utterance → finalize + transcribe.
# Default lowered 500 → 350 → 250 ms for snappier endpointing; this is the
# leading term of time-to-first-audio on EVERY turn. Raise if callers get cut
# off mid-thought or TTS echo trips a false barge-in (validate live on the host).
SILENCE_HANG_MS = int(os.environ.get("AAX6_VAD_SILENCE_HANG_MS", "250"))
# Sustained speech required before we emit `speech_begin`. The reference's
# primary endpointer fires on the first speech frame; we add a small gate
# because this single continuous stream ALSO drives barge-in, and we don't want
# transient noise / TTS echo leaking a false interrupt. ~100 ms stays snappy.
MIN_SPEECH_MS = int(os.environ.get("AAX6_VAD_MIN_SPEECH_MS", "100"))

# Safety cap: never buffer more than this into one utterance. Forces a finalize so
# a stuck stream can't grow unbounded.
MAX_UTTERANCE_MS = 30_000

# Process-wide STT engine (cheap to build; connections are per-utterance) + a
# one-time warmup flag. The VADService is per-connection because it is stateful.
_stt_singleton: Any = None
_stt_warmed = False
_engine_lock = threading.Lock()


def _build_engines():
    """Lazily build (ZipformerSTTService, VADService). Heavy imports (torch via the
    VAD, numpy/websockets via the STT) happen here, off the import path. May block
    (torch.hub.load + a short warmup connect) — call via asyncio.to_thread.
    """
    global _stt_singleton, _stt_warmed
    with _engine_lock:
        if _stt_singleton is None:
            from services.speech.zipformer_stt import ZipformerSTTService

            # URL + optional hotwords/boost are read from env inside the service
            # (AAX6_ZIPFORMER_URL / _HOTWORDS / _BOOST).
            _stt_singleton = ZipformerSTTService()
        stt = _stt_singleton
        if not _stt_warmed:
            logger.info("[stt] warming up Zipformer connection...")
            stt.warmup(sample_rate=STT_SAMPLE_RATE)
            _stt_warmed = True
            logger.info("[stt] warmup done")

    # Fresh VAD per connection — Silero state is stateful and not shareable.
    from services.speech.vad import VADService

    vad = VADService(threshold=SILERO_THRESHOLD, sample_rate=STT_SAMPLE_RATE)
    return stt, vad


class _StreamSession:
    """One utterance's live streaming-STT connection.

    The gate creates one at `speech_begin`, feeds PCM frames into `chunk_q` as
    they arrive, and drops a `None` sentinel at end-of-speech (stamping
    `sentinel_stamp` first). The streaming worker drains the queue into the
    Zipformer engine and emits `stt_interim` / `stt_final`. `recognize_ms` is
    measured end-of-audio → final, so it stays comparable to the client's
    `stt_final − speech_end`.
    """

    __slots__ = ("chunk_q", "sentinel_stamp")

    def __init__(self) -> None:
        self.chunk_q: "queue.Queue[bytes | None]" = queue.Queue()
        self.sentinel_stamp = 0.0


def _run_stream_session(
    stt,
    session: "_StreamSession",
    stop: threading.Event,
    send: Callable[[dict], None],
) -> None:
    """Drive ONE utterance through the streaming engine and emit its events.
    Consumes `transcribe_streaming_events` (raw PCM @ 16 kHz, interim_results);
    the engine resamples to 8 kHz internally for Zipformer. The chunk generator
    blocks on `session.chunk_q` and returns at the `None` end-of-speech sentinel,
    which tells the engine to send `eos`; the final is emitted once the event
    iterator drains."""

    def chunks():
        while True:
            c = session.chunk_q.get()
            if c is None:  # end-of-speech (or shutdown) sentinel
                return
            yield c

    finals: list[str] = []
    last_interim: str | None = None
    try:
        for evt in stt.transcribe_streaming_events(
            chunks(),
            raw_pcm=True,
            sample_rate=STT_SAMPLE_RATE,
            interim_results=True,
        ):
            if stop.is_set():
                return
            etype = evt.get("type")
            if etype == "partial":
                text = (evt.get("text") or "").strip()
                if text and text != last_interim:
                    last_interim = text
                    send({"type": "stt_interim", "text": text})
            elif etype == "final":
                text = (evt.get("text") or "").strip()
                if text:
                    finals.append(text)
    except Exception as e:  # noqa: BLE001 — surface, keep listening
        logger.exception("[stt] streaming recognize failed")
        send({"type": "error", "message": f"STT: {e}"})
        return

    # End-of-audio → final latency (comparable to the old batch recognize_ms).
    recognize_ms = (
        round((time.perf_counter() - session.sentinel_stamp) * 1000, 1)
        if session.sentinel_stamp
        else 0.0
    )
    text = " ".join(finals).strip()
    if text:
        send({"type": "stt_final", "text": text, "recognize_ms": recognize_ms})
    else:
        send({"type": "turn_empty", "recognize_ms": recognize_ms})


def _stt_streaming_worker(
    stt,
    stream_q: "queue.Queue[Any]",
    stop: threading.Event,
    send: Callable[[dict], None],
) -> None:
    """Transcribe each utterance via the streaming engine. Processes sessions
    serially — each utterance's stream is fully drained (interims + final emitted)
    before the next begins, which conversational turn-taking guarantees, so
    transcript ordering needs no generation guard."""
    while not stop.is_set():
        session = stream_q.get()
        if session is None:
            break
        _run_stream_session(stt, session, stop, send)


def _vad_gate_worker(
    vad,
    pcm_q: "queue.Queue[bytes | None]",
    stream_q: "queue.Queue[Any]",
    gen_box: "list[int]",
    stop: threading.Event,
    send: Callable[[dict], None],
) -> None:
    """Endpointing + barge-in (runs on its own thread, never blocked by STT).

    Runs Silero frame-by-frame over inbound PCM. Emits `speech_begin` when the
    caller starts talking (opening a streaming session and feeding it live PCM),
    and on SILENCE_HANG_MS of trailing silence emits `speech_end` and closes the
    stream (which finalizes). A hard MAX_UTTERANCE_MS cap forces a finalize.
    """
    prev_chunk = b""  # ~100 ms pre-roll so we don't clip the utterance onset
    in_speech = False
    silent_ms = 0.0
    run_ms = 0.0  # sustained-speech accumulator (pre-`speech_begin` gate)
    utt_bytes = 0  # bytes captured in the current utterance (for the hard cap)
    t_last_voice = 0.0  # perf_counter() at the last super-threshold frame → endpoint_ms
    stream: "_StreamSession | None" = None  # in-flight streaming session

    max_bytes = MAX_UTTERANCE_MS * _BYTES_PER_MS
    vad.reset()

    def finalize() -> None:
        nonlocal in_speech, silent_ms, run_ms, prev_chunk, utt_bytes, t_last_voice, stream
        # Endpoint dead-time: wall-clock from the last voiced frame to this
        # finalize decision (≈ SILENCE_HANG_MS). This is the user-perceived "VAD
        # latency" — the trailing silence the caller waits through after they
        # stop talking, before the turn is finalized and STT can start.
        endpoint_ms = round((time.perf_counter() - t_last_voice) * 1000, 1) if t_last_voice else None
        # Advance the utterance generation so any late artifact can be dropped.
        gen_box[0] += 1
        send({"type": "speech_end", "endpoint_ms": endpoint_ms})
        # End-of-speech → close the live stream so the engine finalizes. The
        # streaming worker measures recognize_ms from this stamp and emits
        # stt_final once the event iterator drains.
        if stream is not None:
            stream.sentinel_stamp = time.perf_counter()
            stream.chunk_q.put(None)
        stream = None
        in_speech = False
        silent_ms = 0.0
        run_ms = 0.0
        prev_chunk = b""
        utt_bytes = 0
        t_last_voice = 0.0
        vad.reset()

    while not stop.is_set():
        chunk = pcm_q.get()
        if chunk is None:
            if stream is not None:
                # Shutdown mid-utterance → unblock the streaming worker's
                # chunk_q.get() so it finalizes/drains and exits cleanly.
                stream.chunk_q.put(None)
                stream = None
            break

        try:
            probs = vad.iter_frame_probs(chunk)
        except Exception as e:  # noqa: BLE001
            logger.exception("[vad] inference failed")
            send({"type": "error", "message": f"VAD: {e}"})
            continue

        finalized = False
        for prob in probs:
            if prob >= SILERO_THRESHOLD:
                run_ms += vad.frame_ms
                silent_ms = 0.0
                t_last_voice = time.perf_counter()
                if not in_speech and run_ms >= MIN_SPEECH_MS:
                    in_speech = True
                    send({"type": "speech_begin"})
                    # Open the live stream only when speech starts (don't stream
                    # silence). Feed the ~100 ms pre-roll first.
                    stream = _StreamSession()
                    stream_q.put(stream)
                    if prev_chunk:
                        stream.chunk_q.put(prev_chunk)
                        utt_bytes += len(prev_chunk)
            else:
                if in_speech:
                    silent_ms += vad.frame_ms
                else:
                    # Decay so isolated noise frames don't eventually trip the gate.
                    run_ms = max(0.0, run_ms - vad.frame_ms)

            if in_speech and silent_ms >= SILENCE_HANG_MS:
                finalized = True
                break

        if in_speech:
            utt_bytes += len(chunk)
            if stream is not None:
                stream.chunk_q.put(chunk)
        prev_chunk = chunk

        if finalized:
            finalize()
        elif in_speech and utt_bytes >= max_bytes:
            finalize()  # hard cap mid-speech


async def run_session(ws: WebSocket) -> None:
    """Drive one STT WebSocket connection. Caller has already `accept()`-ed.

    Builds the engines off the event loop; on failure sends a fatal error (the
    frontend then falls back to the browser recognizer). Otherwise pumps inbound
    PCM frames into the VAD-gate thread and relays transcription events back.
    """
    loop = asyncio.get_running_loop()

    async def safe_send(obj: dict) -> None:
        try:
            await ws.send_text(json.dumps(obj, ensure_ascii=False))
        except Exception:  # noqa: BLE001 — socket may already be gone
            pass

    try:
        stt, vad = await asyncio.to_thread(_build_engines)
    except Exception as e:  # noqa: BLE001
        logger.exception("[stt] engine build failed")
        await safe_send({"type": "error", "fatal": True, "message": f"STT unavailable: {e}"})
        return

    await safe_send({"type": "ready", "sample_rate": STT_SAMPLE_RATE})

    pcm_q: "queue.Queue[bytes | None]" = queue.Queue()
    stream_q: "queue.Queue[Any]" = queue.Queue()
    # Utterance generation counter (single writer: the gate, in finalize()).
    gen_box: "list[int]" = [0]
    stop = threading.Event()

    def send_threadsafe(obj: dict) -> None:
        # Fire-and-forget hop from a worker thread onto the event loop.
        asyncio.run_coroutine_threadsafe(safe_send(obj), loop)

    gate = threading.Thread(
        target=_vad_gate_worker,
        args=(vad, pcm_q, stream_q, gen_box, stop, send_threadsafe),
        daemon=True,
    )
    streamer = threading.Thread(
        target=_stt_streaming_worker,
        args=(stt, stream_q, stop, send_threadsafe),
        daemon=True,
    )
    workers = [gate, streamer]
    for w in workers:
        w.start()

    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            chunk = msg.get("bytes")
            if chunk:
                pcm_q.put(chunk)
                continue
            text = msg.get("text")
            if text:
                try:
                    data = json.loads(text)
                except Exception:  # noqa: BLE001
                    data = {}
                if data.get("type") == "bye":
                    break
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        logger.exception("[stt] session loop error")
    finally:
        stop.set()
        pcm_q.put(None)
        stream_q.put(None)
        for w in workers:
            await asyncio.to_thread(w.join, 2.0)
