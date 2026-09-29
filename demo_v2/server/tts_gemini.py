"""Gemini TTS, as a second engine behind the same /api/tts contract.

Both engines stream. Chirp drives a bidirectional gRPC stream; Gemini uses
`generate_content_stream`, which emits audio parts as the model produces them.
Either way this module pushes chunks onto the same queue `tts.py` already fans out.

Measured on one 132-character line, cache off, time to FIRST audio:

    gemini-3.8-flash   generate_content        4.20 s     1 chunk
                       generate_content_stream 1.84 s   148 chunks
    gemini-3.1         generate_content        7.97 s     1 chunk
                       generate_content_stream 1.31 s   248 chunks

The non-streaming call was the first implementation here and it is why Gemini
looked two to six times slower than it is: waiting for the whole clip, not the
model, was the cost. Note 3.1 is the FASTEST to first audio once streaming — the
opposite of the ranking the blocking call suggested, because it emits finer chunks.
Chirp is still ahead (0.54 s) and stays the default.

The output is raw PCM, little-endian signed 16-bit @ 24 kHz mono — byte-identical in
shape to what Chirp's `audio_encoding=PCM` yields, so `audio.ts` needs no branch.

Voice names are shared between the two engines (Aoede, Charon, Despina, …), so the
Male/Female toggle and the per-language voice pairs in `lib/lang.py` carry over
without a mapping table.
"""

from __future__ import annotations

import asyncio
import base64
import os
from functools import lru_cache
from typing import Final

try:
    from google import genai
    from google.genai import types as genai_types
except ImportError:  # pragma: no cover — the speech extra is optional
    genai = None
    genai_types = None


#: The picker's options. Key = what the client sends as `?engine=`; value = the
#: model id and the label shown in the dropdown. Verified against `models.list()`
#: on this account rather than typed from memory — a TTS model id that does not
#: exist fails at request time with a 404 the user sees as silence.
MODELS: Final[dict[str, dict[str, str]]] = {
    "gemini-3.1":         {"model": "gemini-3.1-flash-tts-preview",
                           "label": "Gemini 3.1 TTS"},
    "gemini-3.8-flash":   {"model": "gemini-3.8-flash-tts",
                           "label": "Gemini 3.8 Flash TTS"},
    "gemini-3.8-lite":    {"model": "gemini-3.8-flash-lite-tts",
                           "label": "Gemini 3.8 Flash Lite TTS"},
}

_STREAM_DONE_SENTINEL: Final[str] = "__tts_stream_done__"


def available() -> bool:
    """Whether the Gemini engines can be offered at all. Without a key the picker
    should not show options that would produce silence."""
    return genai is not None and bool(
        os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    )


@lru_cache(maxsize=1)
def _client() -> "genai.Client":
    """One client for the process.

    Built per call, the temporary was collected while the request was still in
    flight and every synth died with "Cannot send a request, as the client has
    been closed" — the SDK closes its transport on __del__. Caching it also means
    the TLS connection is reused across turns instead of re-handshaking.
    """
    key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError(
            "gemini tts: no GOOGLE_API_KEY / GEMINI_API_KEY — the Chirp engine uses "
            "Application Default Credentials, this one needs an API key"
        )
    return genai.Client(api_key=key)


def _audio_parts(resp):
    """Yield every audio payload in one streamed response.

    The SDK hands back `inline_data.data` as bytes already decoded, but a raw
    dict path (or an older SDK) leaves base64 text. Guessing wrong yields a clip
    that is silent-but-plausible in length, so handle both rather than assume.
    """
    for cand in (getattr(resp, "candidates", None) or []):
        content = getattr(cand, "content", None)
        for part in (getattr(content, "parts", None) or []):
            blob = getattr(part, "inline_data", None)
            data = getattr(blob, "data", None) if blob is not None else None
            if data is None:
                continue
            yield base64.b64decode(data) if isinstance(data, str) else bytes(data)


def run_stream(
    text: str,
    voice_name: str,
    language_code: str,
    engine: str,
    loop: asyncio.AbstractEventLoop,
    q: "asyncio.Queue",
    done_sentinel: object,
) -> None:
    """Worker-thread entry point with the same contract as `tts._run_grpc_stream`:
    push audio onto `q`, then the done sentinel, or push the exception.

    `language_code` is accepted and deliberately unused: Gemini infers the language
    from the text itself and rejects a locale-qualified voice name, unlike Chirp
    where the name must be `th-TH-Chirp3-HD-Aoede`. Taking the argument keeps one
    call signature for both engines.
    """
    try:
        spec = MODELS.get(engine)
        if spec is None:
            raise RuntimeError(f"gemini tts: unknown engine {engine!r}")
        cfg = genai_types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=genai_types.SpeechConfig(
                voice_config=genai_types.VoiceConfig(
                    prebuilt_voice_config=genai_types.PrebuiltVoiceConfig(
                        voice_name=voice_name
                    )
                )
            ),
        )
        sent = 0
        for part in _client().models.generate_content_stream(
            model=spec["model"], contents=text, config=cfg
        ):
            for audio in _audio_parts(part):
                sent += len(audio)
                loop.call_soon_threadsafe(q.put_nowait, audio)
        if sent == 0:
            raise RuntimeError("gemini tts: stream carried no audio")
        loop.call_soon_threadsafe(q.put_nowait, done_sentinel)
    except Exception as e:  # noqa: BLE001 — surfaced to the subscriber
        loop.call_soon_threadsafe(q.put_nowait, e)
