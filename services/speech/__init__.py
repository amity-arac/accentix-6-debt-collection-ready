"""Standalone Chirp 3 Thai speech service with TTS, STT, and VAD.

Factory functions for creating service instances:

    from services.speech import create_tts_service, create_stt_service, create_vad

    # Text-to-Speech (Chirp 3 HD)
    tts = create_tts_service(voice="Kore", language_code="th-TH")
    audio = tts.synthesize("สวัสดีครับ")
    tts.synthesize_to_file("สวัสดีครับ", "output.wav")

    # Speech-to-Text (Chirp 3)
    stt = create_stt_service(language_code="th-TH")
    text = stt.transcribe(audio_bytes)
    text = stt.transcribe_file("recording.wav")

    # Voice Activity Detection (Silero VAD)
    vad = create_vad()
    segments = vad.detect(wav_bytes)  # [{start_ms, end_ms}, ...]
    speech_only = vad.extract_speech(wav_bytes)
"""

from .config import (
    AVAILABLE_VOICES,
    DEFAULT_LANGUAGE_CODE,
    DEFAULT_REGION,
    DEFAULT_STT_MODEL,
    DEFAULT_TTS_VOICE,
    STT_MODEL_SHORT,
)
from .stt import STTService
from .tts import TTSService
from .vad import SpeechSegment, VADService


def create_tts_service(
    voice: str = DEFAULT_TTS_VOICE,
    language_code: str = DEFAULT_LANGUAGE_CODE,
) -> TTSService:
    """Create a Chirp 3 HD Text-to-Speech service.

    Args:
        voice: Voice name (e.g. "Kore", "Puck", "Aoede"). See AVAILABLE_VOICES.
        language_code: BCP-47 language code (default "th-TH").

    Returns:
        Configured TTSService instance.
    """
    return TTSService(voice_name=voice, language_code=language_code)


def create_stt_service(
    language_code: str = DEFAULT_LANGUAGE_CODE,
    region: str = DEFAULT_REGION,
    model: str = DEFAULT_STT_MODEL,
) -> STTService:
    """Create a Chirp 3 Speech-to-Text service.

    Args:
        language_code: BCP-47 language code (default "th-TH").
        region: GCP region (default "us").
        model: STT model name (default "chirp_3").

    Returns:
        Configured STTService instance.
    """
    return STTService(language_code=language_code, region=region, model=model)


def create_vad(
    threshold: float = 0.5,
    sample_rate: int = 16000,
    min_speech_duration_ms: int = 250,
    min_silence_duration_ms: int = 100,
) -> VADService:
    """Create a Silero Voice Activity Detection service.

    Args:
        threshold: Speech probability threshold (0.0-1.0).
        sample_rate: Audio sample rate (8000 or 16000 Hz).
        min_speech_duration_ms: Minimum speech segment duration to keep.
        min_silence_duration_ms: Minimum silence to split segments.

    Returns:
        Configured VADService instance.
    """
    return VADService(
        threshold=threshold,
        sample_rate=sample_rate,
        min_speech_duration_ms=min_speech_duration_ms,
        min_silence_duration_ms=min_silence_duration_ms,
    )


__all__ = [
    "create_tts_service",
    "create_stt_service",
    "create_vad",
    "TTSService",
    "STTService",
    "VADService",
    "SpeechSegment",
    "AVAILABLE_VOICES",
]
