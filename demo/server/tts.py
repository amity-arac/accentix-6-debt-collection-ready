"""Google Chirp 3 HD streaming TTS proxy.

Mirrors the official `streaming_synthesize` pattern: the input text is split
into a few short chunks and yielded one at a time inside a
`StreamingSynthesizeRequest`, while audio bytes flow back as soon as the
model has them. First audio reaches the browser within a few hundred ms,
much earlier than waiting for the full clip to synthesize.

We stream **raw PCM** (`audio_encoding=PCM`, headerless little-endian signed
16-bit @ 24 kHz) — NOT a container. The browser's `<audio>` element can't play
headerless PCM, so the client does not use `<audio>` at all: it reads the byte
stream with `fetch` + `AudioContext` and schedules each chunk on the Web Audio
graph (see `demo/frontend/src/audio.ts`). PCM has no container to demux and no
codec to decode, so the first samples are audible on arrival — this removes the
native-`<audio>` OGG/Opus decode-startup + readiness-watermark floor that made
first-audio land ~1.4s after the request even though bytes arrived in ~5ms.

Concurrent requests for the same text FAN OUT off ONE underlying gRPC synth: the
first caller starts a detached producer task; every caller (including a
fire-and-forget `prefetch`) subscribes and receives each audio chunk *as it is
produced*. This matters because `prefetch(text)` and the immediately-following
`play(text)` request the same text — with a plain per-text lock the second
request would block until the first finished the WHOLE clip, so the client
heard nothing until full synth (~1-2s) instead of the ~140ms first
byte. Fan-out lets `play` stream progressively while `prefetch` drains in
parallel. The producer is detached, so a subscriber disconnecting (barge-in) does
NOT abort the synth — the concatenated bytes still land in `_CACHE`, making later
calls (or `prewarm`-ed hits) emit instantly.
"""

from __future__ import annotations

import asyncio
import os
import threading
from typing import AsyncIterator, Final, Iterator

from functools import lru_cache

from google.cloud import texttospeech

from services.speech.config import (
    DEFAULT_LANGUAGE_CODE,
    DEFAULT_SAMPLE_RATE,
    DEFAULT_TTS_VOICE,
    get_tts_client,
)


@lru_cache(maxsize=8)
def _streaming_config_for(voice_name: str) -> texttospeech.StreamingSynthesizeConfig:
    """One StreamingSynthesizeConfig per Chirp 3 HD voice name, built lazily and
    cached — lets /api/tts pick a voice per-request (e.g. the demo's Male/Female
    toggle) instead of a single process-wide voice."""
    full_name = f"{DEFAULT_LANGUAGE_CODE}-Chirp3-HD-{voice_name}"
    return texttospeech.StreamingSynthesizeConfig(
        voice=texttospeech.VoiceSelectionParams(
            name=full_name,
            language_code=DEFAULT_LANGUAGE_CODE,
        ),
        streaming_audio_config=texttospeech.StreamingAudioConfig(
            # PCM = headerless little-endian signed 16-bit (raw LINEAR16, NO WAV
            # header). Streaming supports only PCM/ALAW/MULAW/OGG_OPUS; LINEAR16
            # errors in streaming mode. The client plays these bytes directly via
            # Web Audio (no container demux, no codec decode) for first-audio on
            # arrival — see the module docstring.
            audio_encoding=texttospeech.AudioEncoding.PCM,
            sample_rate_hertz=DEFAULT_SAMPLE_RATE,
            speaking_rate=1.2,
        ),
    )

# Raw PCM is not a self-describing media type; the client reads the body as
# binary and feeds it to an AudioContext, so the MIME is cosmetic.
AUDIO_MEDIA_TYPE: Final[str] = "application/octet-stream"

# Thai sentence-ending particles + western punctuation. We break the text
# into chunks at these boundaries (with a length floor) so the request
# generator yields ~30-80 char chunks instead of one big blob.
_BREAK_MARKERS: Final[tuple[str, ...]] = (
    "ค่ะ", "ครับ", "คะ", "ครับผม", ". ", "? ", "! ",
)
# First-chunk flush target: the earliest break-marker at/after this many chars
# flushes the first audio chunk (minimal TTFB). Env-tunable so the host can A/B a
# lower value for earlier reply first-audio — but listen for Thai prosody
# artifacts before lowering, and note short replies with an early particle are
# unaffected. Default 30 = no behavior change.
_CHUNK_TARGET: Final[int] = int(os.environ.get("AAX6_TTS_CHUNK_TARGET", "30"))
_CHUNK_MAX: Final[int] = 80

# In-process cache keyed by (exact text, voice name) → concatenated PCM bytes.
# Voice is part of the key so switching the demo's Male/Female toggle doesn't
# serve stale audio synthesized in the other voice.
_CacheKey = tuple[str, str]
_CACHE: dict[_CacheKey, bytes] = {}

# Cache toggle (default ON). Set AAX6_TTS_CACHE=0 to disable the cross-turn text
# cache AND prewarm, so every /api/tts request does a REAL cold synth. This is for
# latency benchmarking: a repetitive clip set makes the LLM emit near-identical
# replies whose cached audio (an instant one-blob hit) would understate true
# TTS/TTFA. The in-turn fan-out (`_INFLIGHT`: prefetch + play sharing one
# producer) is unaffected — only cross-turn reuse is suppressed.
_CACHE_ENABLED: Final[bool] = (
    os.environ.get("AAX6_TTS_CACHE", "1").strip().lower() not in ("0", "false", "")
)

# Sentinel signaling "stream finished cleanly" from the worker thread.
_STREAM_DONE: Final[object] = object()


class _Broadcast:
    """One in-flight synth's fan-out state: chunks produced so far, the live
    subscriber queues to push new chunks to, and terminal state. A single
    detached producer task fills this; N `stream_synth` callers read from it."""

    __slots__ = ("chunks", "subscribers", "done", "error", "task")

    def __init__(self) -> None:
        self.chunks: list[bytes] = []
        self.subscribers: list["asyncio.Queue"] = []
        self.done: bool = False
        self.error: Exception | None = None
        self.task: "asyncio.Task | None" = None


# (text, voice) → in-flight broadcast. Present only while a synth is running;
# removed when the producer finishes (the bytes then live in _CACHE).
_INFLIGHT: dict[_CacheKey, _Broadcast] = {}


async def _produce(text: str, voice_name: str, key: _CacheKey, bc: _Broadcast) -> None:
    """Detached producer: drive ONE gRPC synth, append each chunk to `bc` and
    push it to every current subscriber, then cache the concatenation. Runs to
    completion independent of any subscriber (so barge-in still populates cache)."""
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()
    threading.Thread(target=_run_grpc_stream, args=(text, voice_name, loop, q), daemon=True).start()
    try:
        while True:
            item = await q.get()
            if item is _STREAM_DONE:
                break
            if isinstance(item, Exception):
                bc.error = item
                break
            bc.chunks.append(item)  # type: ignore[arg-type]
            for sub in list(bc.subscribers):
                sub.put_nowait(item)
        if bc.error is None and _CACHE_ENABLED:
            _CACHE[key] = b"".join(bc.chunks)
    except Exception as e:  # noqa: BLE001 — surface to subscribers, don't crash the loop
        bc.error = e
    finally:
        bc.done = True
        # Wake every waiting subscriber: the error (→ they re-raise) or a clean end.
        terminal: object = bc.error if bc.error is not None else _STREAM_DONE
        for sub in list(bc.subscribers):
            sub.put_nowait(terminal)
        _INFLIGHT.pop(key, None)


def is_cached(text: str, voice_name: str = DEFAULT_TTS_VOICE) -> bool:
    """True if `text` is already synthesized (in this voice) in the in-process
    cache (→ a /api/tts request emits instantly). The route uses this to tag the
    response's cache state so the client can attribute TTS latency (hit ≈ 0 vs
    cold synth). Always False when the cache is disabled (AAX6_TTS_CACHE=0)."""
    return _CACHE_ENABLED and (text.strip(), voice_name) in _CACHE


def _chunk_text(text: str) -> Iterator[str]:
    """Split text into chunks at natural sentence boundaries.

    Breaks preferentially at Thai sentence-ending particles (ค่ะ / ครับ / คะ /
    ครับผม) and western terminal punctuation; falls back to whitespace and
    finally a hard length cut at `_CHUNK_MAX`. The *earliest* break at-or-past
    `_CHUNK_TARGET` wins, so the first chunk flushes as soon as a clean
    boundary appears inside the [target, max] window — minimal TTFB without
    cutting a Thai cluster mid-word. No artificial pacing: the gRPC stream
    paces itself.
    """
    text = text.strip()
    if not text:
        return

    pos = 0
    n = len(text)
    while pos < n:
        # Whole remainder fits in one chunk — emit and stop.
        if n - pos <= _CHUNK_MAX:
            yield text[pos:]
            return

        lo = pos + _CHUNK_TARGET
        hi = pos + _CHUNK_MAX
        cut = -1
        for marker in _BREAK_MARKERS:
            # Start the search so the marker, if found, ends at >= lo.
            start = max(pos, lo - len(marker))
            idx = text.find(marker, start, hi)
            if idx != -1:
                end = idx + len(marker)
                if cut == -1 or end < cut:
                    cut = end
        if cut == -1:
            idx = text.find(" ", lo, hi)
            cut = idx + 1 if idx != -1 else hi

        yield text[pos:cut]
        pos = cut


def _run_grpc_stream(
    text: str,
    voice_name: str,
    loop: asyncio.AbstractEventLoop,
    q: "asyncio.Queue[bytes | object | Exception]",
) -> None:
    """Worker-thread entry point: drive the bidirectional gRPC stream and
    forward each `audio_content` payload onto the asyncio queue."""
    try:
        client = get_tts_client()
        streaming_config = _streaming_config_for(voice_name)

        def request_generator() -> Iterator[texttospeech.StreamingSynthesizeRequest]:
            # First message: config only.
            yield texttospeech.StreamingSynthesizeRequest(
                streaming_config=streaming_config
            )
            # Subsequent messages: text chunks.
            for chunk in _chunk_text(text):
                yield texttospeech.StreamingSynthesizeRequest(
                    input=texttospeech.StreamingSynthesisInput(text=chunk)
                )

        for response in client.streaming_synthesize(request_generator()):
            audio = response.audio_content
            if audio:
                loop.call_soon_threadsafe(q.put_nowait, audio)
        loop.call_soon_threadsafe(q.put_nowait, _STREAM_DONE)
    except Exception as e:
        loop.call_soon_threadsafe(q.put_nowait, e)


async def stream_synth(text: str, voice_name: str = DEFAULT_TTS_VOICE) -> AsyncIterator[bytes]:
    """Yield audio chunks for `text` in `voice_name`, SUBSCRIBING to a shared
    fan-out synth keyed by (text, voice_name).

    Cache HIT → yield cached bytes (one chunk, ~instant).
    Otherwise → start the detached producer if this is the first caller, then
    subscribe: replay any chunks already produced, then yield each new chunk as
    the producer emits it. Because prefetch and play share one producer, `play`'s
    first audio arrives at ~first-byte latency instead of after the full synth.
    """
    text = text.strip()
    if not text:
        return
    key: _CacheKey = (text, voice_name)

    cached = _CACHE.get(key) if _CACHE_ENABLED else None
    if cached is not None:
        yield cached
        return

    bc = _INFLIGHT.get(key)
    if bc is None:
        bc = _Broadcast()
        _INFLIGHT[key] = bc
        bc.task = asyncio.get_running_loop().create_task(_produce(text, voice_name, key, bc))

    # Subscribe atomically: snapshot already-produced chunks and register our
    # queue with NO await between them, so the producer (same event loop) can't
    # slip a chunk into the gap — every chunk is delivered exactly once.
    q: asyncio.Queue = asyncio.Queue()
    already = list(bc.chunks)
    bc.subscribers.append(q)
    try:
        for chunk in already:
            yield chunk
        if bc.done:
            # Producer finished before/at subscription: emit anything appended
            # after our snapshot, then honor a terminal error.
            for chunk in bc.chunks[len(already):]:
                yield chunk
            if bc.error is not None:
                raise bc.error
            return
        while True:
            item = await q.get()
            if item is _STREAM_DONE:
                break
            if isinstance(item, Exception):
                raise item
            yield item  # type: ignore[misc]
    finally:
        try:
            bc.subscribers.remove(q)
        except ValueError:
            pass


async def synth(text: str, voice_name: str = DEFAULT_TTS_VOICE) -> bytes:
    """Non-streaming wrapper used by `prewarm` to populate the cache."""
    text = text.strip()
    if not text:
        return b""
    cached = _CACHE.get((text, voice_name)) if _CACHE_ENABLED else None
    if cached is not None:
        return cached
    parts: list[bytes] = []
    async for chunk in stream_synth(text, voice_name):
        parts.append(chunk)
    return b"".join(parts)


async def prewarm(texts: list[str], voice_name: str = DEFAULT_TTS_VOICE) -> None:
    """Fire-and-forget pre-cache for a list of texts. No-op when the cache is
    disabled (AAX6_TTS_CACHE=0) — nothing would be stored, so don't burn a synth."""
    if not _CACHE_ENABLED:
        return
    for t in texts:
        try:
            await synth(t, voice_name)
        except Exception:
            # Demo-grade: a bad text shouldn't sink session creation.
            pass
