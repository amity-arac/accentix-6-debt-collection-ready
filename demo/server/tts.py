"""Google Chirp 3 HD streaming TTS proxy.

Mirrors the official `streaming_synthesize` pattern: the input text is split
into a few short chunks and yielded one at a time inside a
`StreamingSynthesizeRequest`, while audio bytes flow back as soon as the
model has them. First audio reaches the browser within a few hundred ms,
much earlier than waiting for the full clip to synthesize.

The streaming endpoint defaults to raw PCM (LINEAR16 @ 24 kHz), which the
browser's `<audio>` element can't play without a WAV header. We pin
`audio_encoding=OGG_OPUS` via `streaming_audio_config` — a self-describing
container the browser decodes progressively as bytes arrive, exactly the
same way it handled our previous MP3 stream.

Concurrent requests for the same text FAN OUT off ONE underlying gRPC synth: the
first caller starts a detached producer task; every caller (including a
fire-and-forget `prefetch`) subscribes and receives each audio chunk *as it is
produced*. This matters because `prefetch(text)` and the immediately-following
`play(text)` request the same text — with a plain per-text lock the second
request would block until the first finished the WHOLE clip, so the browser's
`<audio>` heard nothing until full synth (~1-2s) instead of the ~140ms first
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

from google.cloud import texttospeech

from services.speech.config import (
    DEFAULT_LANGUAGE_CODE,
    DEFAULT_TTS_VOICE,
    get_tts_client,
)

# Chirp 3 HD voice. `Kore` is the firm female default in services/speech.
_FULL_VOICE_NAME: Final[str] = f"{DEFAULT_LANGUAGE_CODE}-Chirp3-HD-{DEFAULT_TTS_VOICE}"

_STREAMING_CONFIG = texttospeech.StreamingSynthesizeConfig(
    voice=texttospeech.VoiceSelectionParams(
        name=_FULL_VOICE_NAME,
        language_code=DEFAULT_LANGUAGE_CODE,
    ),
    streaming_audio_config=texttospeech.StreamingAudioConfig(
        audio_encoding=texttospeech.AudioEncoding.OGG_OPUS,
        speaking_rate=1.2
    ),
)

AUDIO_MEDIA_TYPE: Final[str] = "audio/ogg"

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

# In-process cache keyed by exact text → concatenated OGG bytes.
_CACHE: dict[str, bytes] = {}

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


# Text → in-flight broadcast. Present only while a synth is running; removed when
# the producer finishes (the bytes then live in _CACHE).
_INFLIGHT: dict[str, _Broadcast] = {}


async def _produce(text: str, bc: _Broadcast) -> None:
    """Detached producer: drive ONE gRPC synth, append each chunk to `bc` and
    push it to every current subscriber, then cache the concatenation. Runs to
    completion independent of any subscriber (so barge-in still populates cache)."""
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()
    threading.Thread(target=_run_grpc_stream, args=(text, loop, q), daemon=True).start()
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
        if bc.error is None:
            _CACHE[text] = b"".join(bc.chunks)
    except Exception as e:  # noqa: BLE001 — surface to subscribers, don't crash the loop
        bc.error = e
    finally:
        bc.done = True
        # Wake every waiting subscriber: the error (→ they re-raise) or a clean end.
        terminal: object = bc.error if bc.error is not None else _STREAM_DONE
        for sub in list(bc.subscribers):
            sub.put_nowait(terminal)
        _INFLIGHT.pop(text, None)


def is_cached(text: str) -> bool:
    """True if `text` is already synthesized in the in-process cache (→ a
    /api/tts request emits instantly). The route uses this to tag the response's
    cache state so the client can attribute TTS latency (hit ≈ 0 vs cold synth)."""
    return text.strip() in _CACHE


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
    loop: asyncio.AbstractEventLoop,
    q: "asyncio.Queue[bytes | object | Exception]",
) -> None:
    """Worker-thread entry point: drive the bidirectional gRPC stream and
    forward each `audio_content` payload onto the asyncio queue."""
    try:
        client = get_tts_client()

        def request_generator() -> Iterator[texttospeech.StreamingSynthesizeRequest]:
            # First message: config only.
            yield texttospeech.StreamingSynthesizeRequest(
                streaming_config=_STREAMING_CONFIG
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


async def stream_synth(text: str) -> AsyncIterator[bytes]:
    """Yield audio chunks for `text`, SUBSCRIBING to a shared fan-out synth.

    Cache HIT → yield cached bytes (one chunk, ~instant).
    Otherwise → start the detached producer if this is the first caller, then
    subscribe: replay any chunks already produced, then yield each new chunk as
    the producer emits it. Because prefetch and play share one producer, `play`'s
    first audio arrives at ~first-byte latency instead of after the full synth.
    """
    text = text.strip()
    if not text:
        return

    cached = _CACHE.get(text)
    if cached is not None:
        yield cached
        return

    bc = _INFLIGHT.get(text)
    if bc is None:
        bc = _Broadcast()
        _INFLIGHT[text] = bc
        bc.task = asyncio.get_running_loop().create_task(_produce(text, bc))

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


async def synth(text: str) -> bytes:
    """Non-streaming wrapper used by `prewarm` to populate the cache."""
    text = text.strip()
    if not text:
        return b""
    cached = _CACHE.get(text)
    if cached is not None:
        return cached
    parts: list[bytes] = []
    async for chunk in stream_synth(text):
        parts.append(chunk)
    return b"".join(parts)


async def prewarm(texts: list[str]) -> None:
    """Fire-and-forget pre-cache for a list of texts."""
    for t in texts:
        try:
            await synth(t)
        except Exception:
            # Demo-grade: a bad text shouldn't sink session creation.
            pass
