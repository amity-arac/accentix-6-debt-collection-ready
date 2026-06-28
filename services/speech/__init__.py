"""Speech service package (deliverable subset).

This package intentionally stays IMPORT-LIGHT: importing `services.speech` (or
`services.speech.config`) must not pull torch / google-cloud-speech, so the demo
backend imports fast and torch-free (see CLAUDE.md gotcha 11). Therefore this
`__init__` deliberately does NOT eagerly import the `.stt` / `.tts` / `.vad`
submodules. Import what you need directly:

    from services.speech.config import get_tts_client, DEFAULT_TTS_VOICE  # light
    from services.speech.stt import STTService    # pulls google-cloud-speech
    from services.speech.vad import VADService    # pulls torch (Silero VAD)

Used by the demo backend:
  - `config`     — optional Chirp 3 HD text-to-speech (demo/server/tts.py).
  - `stt` + `vad` — optional Chirp 3 speech-to-text over a WebSocket
    (demo/server/stt_ws.py); both are imported LAZILY there, only when a client
    connects to /api/stt, so this stays off the startup import path.
"""
