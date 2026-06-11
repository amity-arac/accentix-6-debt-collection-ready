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

Concurrent requests for the same text wait on a per-text asyncio.Lock and
share one underlying gRPC stream. The concatenated bytes are cached so
subsequent calls (or `prewarm`-ed cache hits) emit instantly.
"""

from __future__ import annotations

import asyncio
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
_CHUNK_TARGET: Final[int] = 30
_CHUNK_MAX: Final[int] = 80

# In-process cache keyed by exact text → concatenated OGG bytes.
_CACHE: dict[str, bytes] = {}
_CACHE_LOCKS: dict[str, asyncio.Lock] = {}

# Sentinel signaling "stream finished cleanly" from the worker thread.
_STREAM_DONE: Final[object] = object()


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
    """Yield audio chunks for `text`.

    Cache HIT → yield cached bytes (one chunk, ~instant).
    Cache MISS → kick off a gRPC streaming synth on a worker thread, drain
    chunks via an asyncio queue, yield each to the caller as it arrives,
    and cache the concatenation on success.
    """
    text = text.strip()
    if not text:
        return

    cached = _CACHE.get(text)
    if cached is not None:
        yield cached
        return

    lock = _CACHE_LOCKS.setdefault(text, asyncio.Lock())
    async with lock:
        cached = _CACHE.get(text)
        if cached is not None:
            yield cached
            return

        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()
        threading.Thread(
            target=_run_grpc_stream,
            args=(text, loop, q),
            daemon=True,
        ).start()

        collected: list[bytes] = []
        while True:
            item = await q.get()
            if item is _STREAM_DONE:
                break
            if isinstance(item, Exception):
                raise item
            collected.append(item)  # type: ignore[arg-type]
            yield item  # type: ignore[misc]
        _CACHE[text] = b"".join(collected)


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
