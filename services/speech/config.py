"""Configuration constants and cached clients for the Chirp 3 speech service."""

import json
import os
from functools import lru_cache

from google.api_core.client_options import ClientOptions
from google.oauth2 import service_account

# ── Defaults ────────────────────────────────────────────────
DEFAULT_REGION = "asia-southeast1"
DEFAULT_LANGUAGE_CODE = "th-TH"
DEFAULT_STT_MODEL = "chirp_3"
STT_MODEL_SHORT = "short"  # Traditional model — lower latency (~200-400ms), less accurate
DEFAULT_TTS_VOICE = "Despina"  # Firm female; see full list in tts.py
DEFAULT_SAMPLE_RATE = 24000  # TTS output sample rate (Hz)
DEFAULT_STT_SAMPLE_RATE = 16000  # STT input sample rate (Hz)

# All 30 Chirp 3 HD voice options (language-agnostic names)
AVAILABLE_VOICES = [
    "Achernar", "Achird", "Algenib", "Algieba", "Alnilam",
    "Aoede", "Autonoe", "Callirrhoe", "Charon", "Despina",
    "Enceladus", "Erinome", "Fenrir", "Gacrux", "Iapetus",
    "Kore", "Laomedeia", "Leda", "Orus", "Pulcherrima",
    "Puck", "Rasalgethi", "Sadachbia", "Sadaltager", "Schedar",
    "Sulafat", "Umbriel", "Vindemiatrix", "Zephyr", "Zubenelgenubi",
]

CLOUD_SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
]


@lru_cache(maxsize=1)
def _load_credentials():
    """Load GCP credentials from GOOGLE_CREDENTIALS_JSON env var.

    Falls back to Application Default Credentials if not set.
    """
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if creds_json:
        info = json.loads(creds_json)
        return service_account.Credentials.from_service_account_info(
            info, scopes=CLOUD_SCOPES
        )
    # Fall back to ADC (GOOGLE_APPLICATION_CREDENTIALS file path or gcloud auth)
    return None


def _get_project_id() -> str:
    """Return the GCP project ID from environment or credentials."""
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if project_id:
        return project_id
    # Try extracting from service account JSON
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if creds_json:
        info = json.loads(creds_json)
        project_id = info.get("project_id")
        if project_id:
            return project_id
    raise EnvironmentError(
        "GOOGLE_CLOUD_PROJECT environment variable is required for Chirp 3 speech service. "
        "Set it in your .env file, or provide GOOGLE_CREDENTIALS_JSON with a project_id field."
    )


@lru_cache(maxsize=1)
def get_tts_client():
    """Return a cached Cloud Text-to-Speech client."""
    from google.cloud import texttospeech

    credentials = _load_credentials()
    return texttospeech.TextToSpeechClient(credentials=credentials)


@lru_cache(maxsize=4)
def get_stt_client(region: str = DEFAULT_REGION):
    """Return a cached Cloud Speech-to-Text V2 client for the given region."""
    from google.cloud.speech_v2 import SpeechClient

    credentials = _load_credentials()
    # "global" region uses the default endpoint without region prefix
    if region == "global":
        endpoint = "speech.googleapis.com"
    else:
        endpoint = f"{region}-speech.googleapis.com"
    return SpeechClient(
        credentials=credentials,
        client_options=ClientOptions(
            api_endpoint=endpoint,
        )
    )


def get_recognizer_path(region: str = DEFAULT_REGION) -> str:
    """Return the recognizer resource path for Chirp 3."""
    return f"projects/{_get_project_id()}/locations/{region}/recognizers/_"
