"""Speech service package (deliverable subset).

Only `services.speech.config` is used by the demo backend (optional Chirp 3 HD
text-to-speech via `demo/server/tts.py`). The full STT/VAD service factories
from the source project are intentionally omitted here to keep the deliverable
lightweight (no torch / silero-vad / speech-to-text deps). Import config
directly:

    from services.speech.config import get_tts_client, DEFAULT_TTS_VOICE
"""
