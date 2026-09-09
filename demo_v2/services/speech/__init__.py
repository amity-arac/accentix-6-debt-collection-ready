"""Speech service package (deliverable subset).

Stays IMPORT-LIGHT on purpose: importing `services.speech` must not pull torch or
google-cloud-speech, so the backend starts fast and runs with neither installed.
This `__init__` therefore imports no submodule. Import what you need directly:

    from demo_v2.services.speech.config import get_tts_client   # light
    from demo_v2.services.speech.zipformer_stt import ZipformerSTTService  # numpy, websockets
    from demo_v2.services.speech.vad import VADService          # torch (Silero VAD)
    from demo_v2.services.speech.stt import STTService          # google-cloud-speech

`stt_ws.py` imports the recognizer and the VAD lazily, only when a client connects
to /api/stt, which is what keeps them off the startup path.
"""
