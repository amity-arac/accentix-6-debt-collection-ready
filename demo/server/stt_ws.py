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

`AAX6_STT_STREAMING=1` swaps the transcription for TRUE Chirp `streaming_recognize`
(interim results + a final whose upload overlaps speech, so it can land sooner at
end-of-speech). Silero still owns endpointing + barge-in, and the event schema is
identical — so toggling the flag is a clean batch-vs-streaming A/B. Default OFF
(batch, exactly as above).

Worker threads keep things responsive:
  - the **VAD gate** thread does endpointing (speech_begin / end-of-speech via
    SILENCE_HANG_MS) and barge-in, and hands buffer snapshots to the STT threads.
    It is never blocked by a recognize() call, so end-of-speech is detected
    promptly.
  - the **interim STT** thread runs recognize() on the growing buffer for live
    on-screen words, coalescing stale snapshots so it never falls behind.
  - the **final STT** thread (DIRECT_FINAL) transcribes the finished utterance on
    its own thread, so the final never waits behind an in-flight interim. With
    SPECULATIVE_FINAL it fires EARLY — once trailing silence crosses
    SPECULATIVE_SILENCE_MS, before the full hang confirms — overlapping the
    recognize with the remaining hang; the result is emitted only once the hang
    confirms the endpoint, or discarded if the caller resumes talking.

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

# Live interim transcription: re-run recognize() on the growing utterance every
# ~this much newly-captured speech, and don't bother below MIN_INTERIM_MS.
INTERIM_EVERY_MS = 700
MIN_INTERIM_MS = 300
# Safety cap: never buffer more than this into one utterance (Chirp recognize()
# is the <1-min path). Forces a finalize so a stuck stream can't grow unbounded.
MAX_UTTERANCE_MS = 30_000

# Run the FINAL recognize on its own dedicated thread instead of enqueuing it on
# the shared interim worker, so it never waits behind an in-flight ~774ms interim
# recognize() (the bulk of the "STT queue-wait"). google-cloud-speech gRPC
# clients are thread-safe for concurrent unary calls, and STTService holds no
# lock around recognize(), so two concurrent recognize()s are safe. Set 0 to
# revert to the single-worker queue.
DIRECT_FINAL = os.environ.get("AAX6_STT_DIRECT_FINAL", "1").strip().lower() not in ("0", "false", "")

# Speculative early final: once trailing silence reaches SPECULATIVE_SILENCE_MS
# (< SILENCE_HANG_MS), fire the final recognize EARLY on the buffer-so-far as a
# bet that the caller is done — overlapping the recognize with the remaining
# endpoint hang, saving ~(SILENCE_HANG_MS - SPECULATIVE_SILENCE_MS). The audio
# added during the hang is silence, so the early transcript matches the confirmed
# one. If the caller resumes talking before the hang confirms, the speculative
# result is discarded (one wasted recognize, ~$0.002). The result is emitted as
# `stt_final` only once the hang CONFIRMS the endpoint (never before — that would
# start an LLM turn on an unfinished utterance). Needs DIRECT_FINAL (dedicated
# worker). Set AAX6_STT_SPECULATIVE=0 to disable.
SPECULATIVE_FINAL = os.environ.get("AAX6_STT_SPECULATIVE", "1").strip().lower() not in ("0", "false", "")
SPECULATIVE_SILENCE_MS = int(os.environ.get("AAX6_STT_SPECULATIVE_SILENCE_MS", "100"))
_SPECULATE = DIRECT_FINAL and SPECULATIVE_FINAL and 0 < SPECULATIVE_SILENCE_MS < SILENCE_HANG_MS

# True Chirp 3 STREAMING for the STT final (AAX6_STT_STREAMING=1) instead of the
# default post-speech batch recognize(). Each utterance's PCM is fed to Chirp's
# streaming_recognize LIVE as the caller speaks (upload overlaps speech), so the
# FINAL can land sooner at end-of-speech; interim partials render if Chirp emits
# them (historically sparse for Thai — that's the whole thing being A/B-tested).
# Silero still owns endpointing + barge-in and the event schema is unchanged, so
# flipping this is a clean batch-vs-streaming comparison. When on, the batch
# interim/final workers below are NOT started. Default OFF (batch path untouched).
STREAMING = os.environ.get("AAX6_STT_STREAMING", "0").strip().lower() not in ("0", "false", "")

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
            from services.speech.config import DEFAULT_REGION

            # Env-tunable STT model knob. Default "chirp_3": the low-latency
            # "short"/"long" conformer models do NOT support th-TH in
            # asia-southeast1 (Google 400s: language "th-TH" not supported by
            # model "short" in that location), so they can't be the Thai default.
            # chirp_2 / chirp are the other Thai-capable candidates worth A/B-ing.
            model = os.environ.get("AAX6_STT_MODEL", "chirp_3")
            # Env-tunable STT region. Default asia-southeast1 — the region this
            # demo has always used and where th-TH transcription is confirmed
            # working. Chirp 3 is GA in the `us` / `eu` multi-regions; set
            # AAX6_STT_REGION=us (or eu) to A/B them, but VERIFY th-TH is served
            # there first (language support is regional — if Thai isn't offered,
            # recognize() 400s and STT falls back to the browser recognizer).
            region = os.environ.get("AAX6_STT_REGION", DEFAULT_REGION).strip() or DEFAULT_REGION
            logger.info("[stt] model=%s region=%s", model, region)
            _stt_singleton = STTService(model=model, region=region)
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


# Request from the VAD-gate thread to the interim STT thread: (kind, pcm, gen)
# where kind is "interim" or "final" (legacy path) and gen is the utterance
# generation the snapshot was captured in. `None` is the shutdown sentinel.
_Req = "tuple[str, bytes, int] | None"


class _SpecFinal:
    """A final-recognize job on the dedicated worker (DIRECT_FINAL / speculative).

    The gate creates one when trailing silence first crosses the speculative
    threshold; the worker transcribes it; it is emitted as `stt_final` ONLY once
    the endpoint hang commits it (`committed`) — or dropped if the caller resumes
    talking (`cancelled`). All transitions happen under `lock`, and `_emit_final`
    is idempotent, so exactly one of {worker, gate} emits, whichever observes the
    other's flag last. The non-speculative path just creates one already
    `committed` at the endpoint.
    """

    __slots__ = ("pcm", "lock", "done", "text", "recognize_ms",
                 "committed", "cancelled", "emitted")

    def __init__(self, pcm: bytes) -> None:
        self.pcm = pcm
        self.lock = threading.Lock()
        self.done = threading.Event()   # recognize() returned (success or error)
        self.text: str | None = None
        self.recognize_ms = 0.0
        self.committed = False          # endpoint confirmed → should be emitted
        self.cancelled = False          # caller resumed / errored → discard
        self.emitted = False            # guard: emit exactly once


def _emit_final(spec: "_SpecFinal", send: Callable[[dict], None]) -> None:
    """Emit stt_final / turn_empty exactly once. Caller MUST hold `spec.lock`."""
    if spec.emitted or spec.cancelled:
        return
    spec.emitted = True
    if spec.text:
        send({"type": "stt_final", "text": spec.text, "recognize_ms": spec.recognize_ms})
    else:
        send({"type": "turn_empty", "recognize_ms": spec.recognize_ms})


class _StreamSession:
    """One utterance's live streaming-STT connection (AAX6_STT_STREAMING).

    The gate creates one at `speech_begin`, feeds PCM frames into `chunk_q` as
    they arrive, and drops a `None` sentinel at end-of-speech (stamping
    `sentinel_stamp` first). The streaming worker drains the queue into Chirp's
    `streaming_recognize` and emits `stt_interim` / `stt_final`. `recognize_ms`
    is measured end-of-audio → final, so it stays comparable to the batch path's
    recognize wall-time (and to the client's `stt_final − speech_end`).
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
    """Drive ONE utterance through Chirp `streaming_recognize` and emit its
    events. Reuses `STTService.transcribe_streaming_events` unchanged (raw PCM,
    interim_results). The chunk generator blocks on `session.chunk_q` and returns
    at the `None` end-of-speech sentinel, which half-closes the gRPC stream so
    Chirp finalizes; the final is emitted once the response iterator drains."""

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

    # End-of-audio → final latency (comparable to the batch recognize_ms).
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
    """AAX6_STT_STREAMING dispatcher: transcribe each utterance via Chirp
    `streaming_recognize` instead of post-speech batch. Processes sessions
    serially — each utterance's stream is fully drained (interims + final
    emitted) before the next begins, which conversational turn-taking
    guarantees, so transcript ordering needs no generation guard."""
    while not stop.is_set():
        session = stream_q.get()
        if session is None:
            break
        _run_stream_session(stt, session, stop, send)


def _vad_gate_worker(
    vad,
    pcm_q: "queue.Queue[bytes | None]",
    req_q: "queue.Queue[Any]",
    spec_q: "queue.Queue[Any]",
    stream_q: "queue.Queue[Any]",
    gen_box: "list[int]",
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
    t_last_voice = 0.0  # perf_counter() at the last super-threshold frame → endpoint_ms
    spec: "_SpecFinal | None" = None  # in-flight speculative final for this utterance
    stream: "_StreamSession | None" = None  # in-flight streaming session (AAX6_STT_STREAMING)

    interim_every_bytes = INTERIM_EVERY_MS * _BYTES_PER_MS
    min_interim_bytes = MIN_INTERIM_MS * _BYTES_PER_MS
    max_bytes = MAX_UTTERANCE_MS * _BYTES_PER_MS
    vad.reset()

    def finalize() -> None:
        nonlocal buf, in_speech, silent_ms, run_ms, prev_chunk, last_interim_len, t_last_voice, spec, stream
        # Endpoint dead-time: wall-clock from the last voiced frame to this
        # finalize decision (≈ SILENCE_HANG_MS). This is the user-perceived "VAD
        # latency" — the trailing silence the caller waits through after they
        # stop talking, before the turn is finalized and STT can start.
        endpoint_ms = round((time.perf_counter() - t_last_voice) * 1000, 1) if t_last_voice else None
        # Advance the utterance generation. Any interim from THIS utterance —
        # queued or already in-flight — is stamped with the old gen, so the interim
        # worker drops it (it must not land after stt_final, even once the NEXT
        # utterance begins). A single boolean couldn't tell "N finalizing" from
        # "N+1 active"; the counter can.
        gen_box[0] += 1
        send({"type": "speech_end", "endpoint_ms": endpoint_ms})
        if STREAMING:
            # End-of-speech → close the live stream so Chirp finalizes. The
            # streaming worker measures recognize_ms from this stamp and emits
            # stt_final once the response iterator drains.
            if stream is not None:
                stream.sentinel_stamp = time.perf_counter()
                stream.chunk_q.put(None)
            stream = None
        elif DIRECT_FINAL:
            if spec is not None:
                # A speculative recognize is already running (fired at
                # SPECULATIVE_SILENCE_MS) → COMMIT it. If it already finished, emit
                # now; otherwise flag it so the worker emits the moment it returns.
                with spec.lock:
                    if spec.done.is_set():
                        _emit_final(spec, send)
                    elif not spec.cancelled:
                        spec.committed = True
            else:
                # No speculation (disabled, or the utterance ended before the
                # speculative threshold) → recognize the full buffer now, on the
                # dedicated worker, already committed.
                s = _SpecFinal(bytes(buf))
                s.committed = True
                spec_q.put(s)
            spec = None
        else:
            req_q.put(("final", bytes(buf), gen_box[0]))  # legacy single-queue path
        buf = bytearray()
        in_speech = False
        silent_ms = 0.0
        run_ms = 0.0
        prev_chunk = b""
        last_interim_len = 0
        t_last_voice = 0.0
        vad.reset()

    while not stop.is_set():
        chunk = pcm_q.get()
        if chunk is None:
            if STREAMING and stream is not None:
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
        speech_resumed = False  # a voiced frame arrived while a speculative final was pending
        for prob in probs:
            if prob >= SILERO_THRESHOLD:
                run_ms += vad.frame_ms
                silent_ms = 0.0
                t_last_voice = time.perf_counter()
                if spec is not None:
                    speech_resumed = True  # caller talked again after we fired the guess
                if not in_speech and run_ms >= MIN_SPEECH_MS:
                    in_speech = True
                    send({"type": "speech_begin"})
                    if STREAMING:
                        # Open the live stream only when speech starts (don't
                        # stream silence). Feed the ~100ms pre-roll first.
                        stream = _StreamSession()
                        stream_q.put(stream)
                        if prev_chunk:
                            stream.chunk_q.put(prev_chunk)
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
            if STREAMING and stream is not None:
                stream.chunk_q.put(chunk)
        prev_chunk = chunk

        # Speculative-final lifecycle. CANCEL FIRST (even in the chunk that
        # finalizes) if the caller resumed talking after we fired the guess —
        # otherwise finalize() could commit a stale pre-resume snapshot and drop
        # the resumed words. Because ANY voiced frame cancels, a spec that
        # survives to the endpoint only ever gained pure silence, so its
        # transcript matches the full-buffer one.
        if _SPECULATE and spec is not None and speech_resumed:
            with spec.lock:
                spec.cancelled = True
            spec = None

        if finalized:
            finalize()
        elif in_speech:
            # Fire a speculative final once trailing silence crosses the threshold.
            # (Batch path only — streaming's final overlaps speech already.)
            if _SPECULATE and not STREAMING and spec is None and silent_ms >= SPECULATIVE_SILENCE_MS:
                spec = _SpecFinal(bytes(buf))
                spec_q.put(spec)
            if len(buf) >= max_bytes:
                finalize()  # hard cap mid-speech
            elif (
                not STREAMING
                and len(buf) >= min_interim_bytes
                and len(buf) - last_interim_len >= interim_every_bytes
            ):
                last_interim_len = len(buf)
                req_q.put(("interim", bytes(buf), gen_box[0]))


def _stt_worker(
    stt,
    req_q: "queue.Queue[Any]",
    gen_box: "list[int]",
    stop: threading.Event,
    send: Callable[[dict], None],
) -> None:
    """Transcribe interim snapshots handed over by the VAD-gate thread (and the
    final too, when AAX6_STT_DIRECT_FINAL is off).

    Each batch recognize() blocks (~hundreds of ms), so this runs off the gate
    thread. When it falls behind, stale interim requests are coalesced to the
    newest — but a `final` is never skipped. Each interim carries the utterance
    generation it was captured in; once that generation is stale (its utterance
    finalized — `gen != gen_box[0]`), the interim is dropped so it can't land
    after stt_final, even after the NEXT utterance has begun.
    """
    last_interim_text: str | None = None

    while not stop.is_set():
        req = req_q.get()
        if req is None:
            break
        kind, pcm, gen = req

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
                kind, pcm, gen = nxt
                if kind == "final":
                    break

        # The interim's utterance already finalized → its result must not land
        # after that utterance's stt_final; drop it (and skip the recognize).
        # (A queued `final` — AAX6_STT_DIRECT_FINAL off — must still run.)
        if kind == "interim" and gen != gen_box[0]:
            last_interim_text = None
            continue

        t0 = time.perf_counter()
        try:
            text = stt.transcribe_pcm(pcm, sample_rate=STT_SAMPLE_RATE).strip()
        except Exception as e:  # noqa: BLE001 — surface, keep listening
            logger.exception("[stt] recognize failed")
            send({"type": "error", "message": f"STT: {e}"})
            if kind == "final":
                last_interim_text = None
            continue
        # Pure batch recognize() gRPC wall-time (the "STT latency" number). The
        # client-perceived STT also includes queue wait + the WS leg.
        recognize_ms = round((time.perf_counter() - t0) * 1000, 1)

        if kind == "interim":
            # Only push changes — avoids spamming identical interim frames — and
            # never after this utterance finalized (guards a recognize that was
            # in-flight when the generation advanced).
            if text and text != last_interim_text and gen == gen_box[0]:
                last_interim_text = text
                send({"type": "stt_interim", "text": text})
        else:  # final (legacy single-queue path; DIRECT_FINAL uses _stt_final_worker)
            last_interim_text = None
            if text:
                send({"type": "stt_final", "text": text, "recognize_ms": recognize_ms})
            else:
                send({"type": "turn_empty", "recognize_ms": recognize_ms})


def _stt_final_worker(
    stt,
    spec_q: "queue.Queue[Any]",
    stop: threading.Event,
    send: Callable[[dict], None],
) -> None:
    """Transcribe final jobs (speculative or committed) on a dedicated thread
    (DIRECT_FINAL), so the final recognize() never queues behind an in-flight
    interim. Runs one recognize() at a time here, concurrently with the interim
    worker's recognize() on the same thread-safe gRPC client.

    Emits stt_final only for a job the gate has COMMITTED (endpoint confirmed). A
    job that finishes BEFORE commit is HELD (the gate emits it the instant it
    commits); a cancelled job (caller resumed, or recognize errored) is dropped.
    All decisions are made under `spec.lock` and `_emit_final` is idempotent, so
    exactly one side emits."""
    while not stop.is_set():
        spec = spec_q.get()
        if spec is None:
            break
        with spec.lock:
            if spec.cancelled:
                continue  # cancelled while queued (caller resumed) → skip the recognize
        t0 = time.perf_counter()
        try:
            text = stt.transcribe_pcm(spec.pcm, sample_rate=STT_SAMPLE_RATE).strip()
        except Exception as e:  # noqa: BLE001 — surface, keep listening
            logger.exception("[stt] final recognize failed")
            send({"type": "error", "message": f"STT: {e}"})
            with spec.lock:
                spec.cancelled = True  # don't emit a bogus turn for a failed recognize
                spec.done.set()
            continue
        recognize_ms = round((time.perf_counter() - t0) * 1000, 1)
        with spec.lock:
            spec.text = text
            spec.recognize_ms = recognize_ms
            spec.done.set()
            if spec.committed:
                _emit_final(spec, send)  # endpoint already confirmed → emit now
            # else: cancelled → drop; not-yet-committed → HELD (gate emits at endpoint)


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
    spec_q: "queue.Queue[Any]" = queue.Queue()
    stream_q: "queue.Queue[Any]" = queue.Queue()
    # Utterance generation counter (single writer: the gate, in finalize()). The
    # interim worker stamps each request with it and drops results whose gen has
    # gone stale, so a late interim can't land after its utterance's stt_final.
    gen_box: "list[int]" = [0]
    stop = threading.Event()

    def send_threadsafe(obj: dict) -> None:
        # Fire-and-forget hop from a worker thread onto the event loop.
        asyncio.run_coroutine_threadsafe(safe_send(obj), loop)

    gate = threading.Thread(
        target=_vad_gate_worker,
        args=(vad, pcm_q, req_q, spec_q, stream_q, gen_box, stop, send_threadsafe),
        daemon=True,
    )
    if STREAMING:
        # Streaming mode: a single dispatcher runs each utterance through Chirp's
        # streaming_recognize. The batch interim/final workers are NOT started —
        # only streaming runs, for a clean batch-vs-streaming A/B.
        workers = [gate, threading.Thread(
            target=_stt_streaming_worker,
            args=(stt, stream_q, stop, send_threadsafe),
            daemon=True,
        )]
    else:
        transcriber = threading.Thread(
            target=_stt_worker,
            args=(stt, req_q, gen_box, stop, send_threadsafe),
            daemon=True,
        )
        workers = [gate, transcriber]
        if DIRECT_FINAL:
            # Dedicated final-recognize thread → the final never waits behind an
            # in-flight interim, and (with SPECULATIVE_FINAL) can start EARLY
            # during the endpoint hang. Shares the thread-safe stt client.
            workers.append(threading.Thread(
                target=_stt_final_worker,
                args=(stt, spec_q, stop, send_threadsafe),
                daemon=True,
            ))
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
        req_q.put(None)
        spec_q.put(None)
        stream_q.put(None)
        for w in workers:
            await asyncio.to_thread(w.join, 2.0)
