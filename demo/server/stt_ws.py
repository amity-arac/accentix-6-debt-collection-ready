"""Chirp 3 backend speech-to-text over a WebSocket, with live interim words.

Mirrors the source project's `web/server.py` speech path: the browser streams
PCM16 @ 16 kHz mono frames; a server-side **Silero VAD** gates them into
utterances; each utterance is transcribed with **batch Chirp 3 `recognize()`**
(`STTService.transcribe_pcm`).

Why batch and not streaming, even for live words: Chirp streaming barely emits
Thai partials — measured, one partial near the very end of a 7.5 s utterance
(and the `long` model, which streams better, has no `th-TH` here). So to show
words *as the caller speaks*, we instead re-run batch `recognize()` on the
growing buffer every ~`INTERIM_EVERY_MS` and emit those as interim text, with
the final on end-of-speech. Language-agnostic and actually progressive.

Two worker threads keep things responsive:
  - the **VAD gate** thread does endpointing (speech_begin / end-of-speech via
    SILENCE_HANG_MS) and barge-in, and hands buffer snapshots to the STT thread.
    It is never blocked by a recognize() call, so end-of-speech is detected
    promptly.
  - the **STT** thread runs recognize() for interim snapshots + the final,
    coalescing stale interim requests so it never falls behind (but never drops
    a final).

Unlike the reference (whose LLM + TTS also ride its socket), this socket does
speech-to-text only; the final transcript feeds the existing /api/session turn
flow. Events (a drop-in for the browser Web Speech API the frontend used):

    {"type": "ready",       "sample_rate": 16000}
    {"type": "speech_begin"}                         # caller started → barge-in
    {"type": "stt_interim", "text": "<thai-so-far>"} # growing transcript
    {"type": "speech_end"}                           # trailing silence detected
    {"type": "stt_final",   "text": "<thai>"}        # finalized → send a turn
    {"type": "turn_empty"}                           # utterance was silence/noise
    {"type": "error", "message": "...", "fatal": bool}

torch / silero-vad / google-cloud-speech are imported lazily inside this
module's functions (never at import time), so importing `demo.server.app` stays
light and torch-free — preserving CLAUDE.md gotcha 11. If the engines can't be
built (missing torch, missing GCP creds, …) we send a `fatal` error and the
frontend falls back to the browser Web Speech API.
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
from typing import Any, Callable

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger("demo.stt")

# Audio contract with the mic-worklet (demo/frontend/public/mic-worklet.js).
STT_SAMPLE_RATE = 16000
_BYTES_PER_MS = STT_SAMPLE_RATE * 2 // 1000  # 16-bit mono

# Silero gating — matches the reference (web/server.py).
SILERO_THRESHOLD = 0.4
# Trailing silence (after speech) that ends an utterance → finalize + transcribe.
SILENCE_HANG_MS = 500
# Sustained speech required before we emit `speech_begin`. The reference's
# primary endpointer fires on the first speech frame; we add a small gate
# because this single continuous stream ALSO drives barge-in, and we don't want
# transient noise / TTS echo leaking a false interrupt. ~100 ms stays snappy.
MIN_SPEECH_MS = 100

# Live interim transcription: re-run recognize() on the growing utterance every
# ~this much newly-captured speech, and don't bother below MIN_INTERIM_MS.
INTERIM_EVERY_MS = 700
MIN_INTERIM_MS = 300
# Safety cap: never buffer more than this into one utterance (Chirp recognize()
# is the <1-min path). Forces a finalize so a stuck stream can't grow unbounded.
MAX_UTTERANCE_MS = 30_000

# Process-wide STT client (cheap, thread-safe wrapper over an lru_cached gRPC
# client) + one-time gRPC warmup flag. The VADService is per-connection because
# it is stateful.
_stt_singleton: Any = None
_stt_warmed = False
_engine_lock = threading.Lock()


def _build_engines():
    """Lazily build (STTService, VADService). Heavy imports (torch via the VAD,
    google-cloud-speech via the STT) happen here, off the import path. Blocking
    (torch.hub.load + gRPC warmup) — call via asyncio.to_thread.
    """
    global _stt_singleton, _stt_warmed
    with _engine_lock:
        if _stt_singleton is None:
            from services.speech.stt import STTService

            _stt_singleton = STTService(model="chirp_3")
        stt = _stt_singleton
        if not _stt_warmed:
            logger.info("[stt] warming up Chirp gRPC channel...")
            stt.warmup(sample_rate=STT_SAMPLE_RATE)
            _stt_warmed = True
            logger.info("[stt] warmup done")

    # Fresh VAD per connection — Silero state is stateful and not shareable.
    from services.speech.vad import VADService

    vad = VADService(threshold=SILERO_THRESHOLD, sample_rate=STT_SAMPLE_RATE)
    return stt, vad


# Request from the VAD-gate thread to the STT thread: (kind, pcm) where kind is
# "interim" or "final". `None` is the shutdown sentinel.
_Req = "tuple[str, bytes] | None"


def _vad_gate_worker(
    vad,
    pcm_q: "queue.Queue[bytes | None]",
    req_q: "queue.Queue[Any]",
    stop: threading.Event,
    send: Callable[[dict], None],
) -> None:
    """Endpointing + barge-in (runs on its own thread, never blocked by STT).

    Runs Silero frame-by-frame over inbound PCM. Emits `speech_begin` when the
    caller starts talking, queues an ("interim", snapshot) request every
    INTERIM_EVERY_MS of new speech, and on SILENCE_HANG_MS of trailing silence
    emits `speech_end` and queues a ("final", snapshot) request.
    """
    buf = bytearray()
    prev_chunk = b""  # ~100 ms pre-roll so we don't clip the utterance onset
    in_speech = False
    silent_ms = 0.0
    run_ms = 0.0  # sustained-speech accumulator (pre-`speech_begin` gate)
    last_interim_len = 0

    interim_every_bytes = INTERIM_EVERY_MS * _BYTES_PER_MS
    min_interim_bytes = MIN_INTERIM_MS * _BYTES_PER_MS
    max_bytes = MAX_UTTERANCE_MS * _BYTES_PER_MS
    vad.reset()

    def finalize() -> None:
        nonlocal buf, in_speech, silent_ms, run_ms, prev_chunk, last_interim_len
        send({"type": "speech_end"})
        req_q.put(("final", bytes(buf)))
        buf = bytearray()
        in_speech = False
        silent_ms = 0.0
        run_ms = 0.0
        prev_chunk = b""
        last_interim_len = 0
        vad.reset()

    while not stop.is_set():
        chunk = pcm_q.get()
        if chunk is None:
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
                if not in_speech and run_ms >= MIN_SPEECH_MS:
                    in_speech = True
                    send({"type": "speech_begin"})
                    if prev_chunk:
                        buf.extend(prev_chunk)
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
            buf.extend(chunk)
        prev_chunk = chunk

        if finalized:
            finalize()
        elif in_speech:
            if len(buf) >= max_bytes:
                finalize()  # hard cap mid-speech
            elif (
                len(buf) >= min_interim_bytes
                and len(buf) - last_interim_len >= interim_every_bytes
            ):
                last_interim_len = len(buf)
                req_q.put(("interim", bytes(buf)))


def _stt_worker(
    stt,
    req_q: "queue.Queue[Any]",
    stop: threading.Event,
    send: Callable[[dict], None],
) -> None:
    """Transcribe snapshots handed over by the VAD-gate thread.

    Each batch recognize() blocks (~hundreds of ms), so this runs off the gate
    thread. When it falls behind, stale interim requests are coalesced to the
    newest — but a `final` is never skipped.
    """
    last_interim_text: str | None = None

    while not stop.is_set():
        req = req_q.get()
        if req is None:
            break
        kind, pcm = req

        # Coalesce: drop stale interims in favour of the newest snapshot, but
        # stop and switch to a `final` the moment we see one (never skip it).
        if kind == "interim":
            while True:
                try:
                    nxt = req_q.get_nowait()
                except queue.Empty:
                    break
                if nxt is None:
                    req_q.put(None)  # re-arm shutdown for the outer loop
                    break
                kind, pcm = nxt
                if kind == "final":
                    break

        try:
            text = stt.transcribe_pcm(pcm, sample_rate=STT_SAMPLE_RATE).strip()
        except Exception as e:  # noqa: BLE001 — surface, keep listening
            logger.exception("[stt] recognize failed")
            send({"type": "error", "message": f"STT: {e}"})
            if kind == "final":
                last_interim_text = None
            continue

        if kind == "interim":
            # Only push changes — avoids spamming identical interim frames.
            if text and text != last_interim_text:
                last_interim_text = text
                send({"type": "stt_interim", "text": text})
        else:  # final
            last_interim_text = None
            if text:
                send({"type": "stt_final", "text": text})
            else:
                send({"type": "turn_empty"})


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
    req_q: "queue.Queue[Any]" = queue.Queue()
    stop = threading.Event()

    def send_threadsafe(obj: dict) -> None:
        # Fire-and-forget hop from a worker thread onto the event loop.
        asyncio.run_coroutine_threadsafe(safe_send(obj), loop)

    gate = threading.Thread(
        target=_vad_gate_worker,
        args=(vad, pcm_q, req_q, stop, send_threadsafe),
        daemon=True,
    )
    transcriber = threading.Thread(
        target=_stt_worker,
        args=(stt, req_q, stop, send_threadsafe),
        daemon=True,
    )
    gate.start()
    transcriber.start()

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
        req_q.put(None)
        await asyncio.to_thread(gate.join, 2.0)
        await asyncio.to_thread(transcriber.join, 2.0)
