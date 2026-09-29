"""Google Chirp 3 HD streaming TTS proxy.

The text is split into short chunks and yielded one at a time into a
`StreamingSynthesizeRequest`, so audio bytes flow back as the model produces them.

We stream **raw PCM** (headerless little-endian int16 @ 24 kHz), not a container.
An `<audio>` element cannot play that — which is the point: the client reads the
bytes with `fetch` and schedules each chunk on a Web Audio graph (see
`demo_v2/frontend/src/audio.ts`). No demux, no codec start-up, so the first samples
are audible on arrival instead of ~1.4 s later.

Concurrent requests for the same text fan out off ONE gRPC synth: the first caller
starts a detached producer and every caller subscribes to the chunks as they are
produced. `prefetch(text)` followed immediately by `play(text)` would otherwise
block the second call until the whole clip was done. The producer being detached
means a barge-in does not abort the synth — the bytes still land in `_CACHE`, so a
later call emits instantly.
"""

from __future__ import annotations

import asyncio
import os
import threading
from typing import AsyncIterator, Final, Iterator

from functools import lru_cache

try:
    from google.cloud import texttospeech
except ImportError:  # pragma: no cover - depends on the install
    # A checkout without the speech extra can still run everything that is not audio.
    # It used to fail at import, which took down `demo.server.sessions` with it — and
    # `tools/gen_mockoon.py` imports sessions to read the demo personas, so the mock
    # could only be regenerated on a host with the TTS libraries. It stopped being
    # regenerated anywhere else, and the committed mock lost SHOP's two endpoints.
    texttospeech = None


from demo_v2.services.speech.config import (
    DEFAULT_LANGUAGE_CODE,
    DEFAULT_SAMPLE_RATE,
    DEFAULT_TTS_VOICE,
    get_tts_client,
)


@lru_cache(maxsize=16)
def _streaming_config_for(
    voice_name: str, language_code: str = DEFAULT_LANGUAGE_CODE,
    model_name: str | None = None,
) -> texttospeech.StreamingSynthesizeConfig:
    """One StreamingSynthesizeConfig per (voice, language), built lazily and cached —
    lets /api/tts pick both per request instead of one process-wide voice.

    The language is a PARAMETER, not read from the language ContextVar, because the
    only caller runs on a worker thread started with `threading.Thread`, which does
    not carry the request's context. Reading it there silently fell back to Thai and
    produced `th-TH-Chirp3-HD-Aoede` — a real voice (all 30 Chirp 3 HD names exist in
    both locales), so nothing failed; the English text was simply read with a Thai
    accent. The cache key includes the language for the same reason: keyed on the
    voice alone, the first language to ask for a name would own it.
    """
    # A model-based voice is named bare ("Aoede") and carries `model_name`; a Chirp
    # voice is named by locale ("th-TH-Chirp3-HD-Despina") and must NOT carry one.
    # Swapping the two conventions is a 400 either way.
    if model_name:
        voice = texttospeech.VoiceSelectionParams(
            name=voice_name, language_code=language_code, model_name=model_name)
    else:
        voice = texttospeech.VoiceSelectionParams(
            name=f"{language_code}-Chirp3-HD-{voice_name}", language_code=language_code)
    return texttospeech.StreamingSynthesizeConfig(
        voice=voice,
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
# (text, voice, language): the same sentence in the same voice is DIFFERENT audio
# in two locales, so the language belongs in the key. Without it the first call to
# synthesise a line would own it for both languages.
# The ENGINE is part of the key too. Without it, switching the picker from Chirp to
# Gemini replays whatever the other engine already synthesised for that line — the
# A/B would compare each engine against itself.
_CacheKey = tuple[str, str, str, str]

#: Engine ids the route accepts. "chirp" is the default.
CHIRP: Final[str] = "chirp"

#: Engines that ride THIS module's bidirectional gRPC stream but ask Cloud TTS for a
#: different model. Gemini TTS is reachable here as well as through the Gemini API,
#: and the two are not equivalent — same model, measured on one 132-character line:
#:
#:                          first audio   >6 kHz energy
#:     Chirp 3 HD                1.29 s          1.80 %
#:     gemini-3.1 via Cloud      0.79 s          4.14 %
#:     gemini-3.1 via Gemini API 1.78 s          9.18 %
#:
#: Cloud TTS holds one stream open and takes the text in chunks, so it pays the
#: round trip once; the Gemini API takes the whole sentence and answers once. That
#: accounts for the latency, and it halves the high-frequency artefacts too.
#:
#: Addressed by BARE voice name plus `model_name` — `en-US-Gemini-3.1-Flash-TTS`
#: does not exist, and an API key cannot reach it (the call lands on Vertex and
#: wants `aiplatform.endpoints.predict`), so this path needs ADC.
#: Only 3.1 is served here; both 3.8 ids answer "model is not supported".
CLOUD_MODELS: Final[dict[str, dict[str, str]]] = {
    "gemini-3.1-cloud":     {"model": "gemini-3.1-flash-tts-preview",
                             "label": "Gemini 3.1 Flash TTS (Cloud)"},
    "gemini-2.5-pro-cloud": {"model": "gemini-2.5-pro-preview-tts",
                             "label": "Gemini 2.5 Pro TTS (Cloud)"},
    "gemini-2.5-cloud":     {"model": "gemini-2.5-flash-preview-tts",
                             "label": "Gemini 2.5 Flash TTS (Cloud)"},
}
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


async def _produce(text: str, voice_name: str, language_code: str,
                   key: _CacheKey, bc: _Broadcast, engine: str = CHIRP) -> None:
    """Detached producer: drive ONE gRPC synth, append each chunk to `bc` and
    push it to every current subscriber, then cache the concatenation. Runs to
    completion independent of any subscriber (so barge-in still populates cache)."""
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()
    if engine == CHIRP or engine in CLOUD_MODELS:
        model = CLOUD_MODELS[engine]["model"] if engine in CLOUD_MODELS else None
        worker = _run_grpc_stream
        args = (text, voice_name, language_code, loop, q, model)
    else:
        from demo_v2.server import tts_gemini
        worker = tts_gemini.run_stream
        args = (text, voice_name, language_code, engine, loop, q, _STREAM_DONE)
    threading.Thread(target=worker, args=args, daemon=True).start()
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


def is_cached(text: str, voice_name: str = DEFAULT_TTS_VOICE,
              language_code: str = DEFAULT_LANGUAGE_CODE,
              engine: str = CHIRP) -> bool:
    """True if `text` is already synthesized (in this voice) in the in-process
    cache (→ a /api/tts request emits instantly). The route uses this to tag the
    response's cache state so the client can attribute TTS latency (hit ≈ 0 vs
    cold synth). Always False when the cache is disabled (AAX6_TTS_CACHE=0)."""
    return _CACHE_ENABLED and (text.strip(), voice_name, language_code, engine) in _CACHE


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
    language_code: str,
    loop: asyncio.AbstractEventLoop,
    q: "asyncio.Queue[bytes | object | Exception]",
    model_name: str | None = None,
) -> None:
    """Worker-thread entry point: drive the bidirectional gRPC stream and
    forward each `audio_content` payload onto the asyncio queue."""
    try:
        client = get_tts_client()
        streaming_config = _streaming_config_for(voice_name, language_code, model_name)

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


async def stream_synth(text: str, voice_name: str = DEFAULT_TTS_VOICE,
                       language_code: str = DEFAULT_LANGUAGE_CODE,
                       engine: str = CHIRP) -> AsyncIterator[bytes]:
    """Yield audio chunks for `text` in `voice_name`, SUBSCRIBING to a shared
    fan-out synth keyed by (text, voice_name, language_code).

    Cache HIT → yield cached bytes (one chunk, ~instant).
    Otherwise → start the detached producer if this is the first caller, then
    subscribe: replay any chunks already produced, then yield each new chunk as
    the producer emits it. Because prefetch and play share one producer, `play`'s
    first audio arrives at ~first-byte latency instead of after the full synth.
    """
    text = text.strip()
    if not text:
        return
    key: _CacheKey = (text, voice_name, language_code, engine)

    cached = _CACHE.get(key) if _CACHE_ENABLED else None
    if cached is not None:
        yield cached
        return

    bc = _INFLIGHT.get(key)
    if bc is None:
        bc = _Broadcast()
        _INFLIGHT[key] = bc
        bc.task = asyncio.get_running_loop().create_task(
            _produce(text, voice_name, language_code, key, bc, engine))

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


async def synth(text: str, voice_name: str = DEFAULT_TTS_VOICE,
                language_code: str = DEFAULT_LANGUAGE_CODE,
                engine: str = CHIRP) -> bytes:
    """Non-streaming wrapper used by `prewarm` to populate the cache."""
    text = text.strip()
    if not text:
        return b""
    cached = _CACHE.get((text, voice_name, language_code, engine)) if _CACHE_ENABLED else None
    if cached is not None:
        return cached
    parts: list[bytes] = []
    async for chunk in stream_synth(text, voice_name, language_code, engine):
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
