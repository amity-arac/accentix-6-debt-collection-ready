"""Configuration constants and shared Vertex AI client."""

import json
import os
from functools import lru_cache

from google import genai
from google.oauth2 import service_account

# ── Phase G (v6) activation ────────────────────────────────
# Set AAX6_V6_ACTIVE=1 to activate v6:
#   - Catalog: data/pre-scripts/v6_pre_script_database.json
#   - System instructions: data/system_instructions/pre-script/v6_communicator_instruction-*.md
#   - Backend: CaseBackend(v6_active=True) — 3-Element Enforcer on payment_date
# When unset/0, the v5 catalog and v5 instructions load (backward-compatible).
V6_ACTIVE: bool = os.environ.get("AAX6_V6_ACTIVE", "").strip().lower() in ("1", "true", "yes", "on")

# ── Phase H (v6) simulation date ───────────────────────────
# NOTE: no longer the live "today". simulator/datetime_utils now derives the
# current date from the real Asia/Bangkok clock (get_current_datetime + the
# per-case `today`), so the demo tracks the real calendar. This constant is
# retained only for offline/eval reproducibility. Format: ISO YYYY-MM-DD.
SIMULATION_DATE: str = "2026-05-22"

# ── File paths (relative to copter-directory/) ──────────────
TEST_CASES_FILE = "data/test-cases.json"
SCENARIOS_FILE = "data/scenarios.json"
AGENT_PROMPT_FILE = "data/agent-system-instructions.md"
RESULTS_FILE = "data/results.json"

NATURAL_TTS = os.environ.get("AAX6_NATURAL_TTS", "").strip().lower() in ("1", "true", "yes", "on")

PRE_SCRIPT_DB_FILE = (
    "data/pre-scripts/v6_demo_pre_script_database.json"
    if V6_ACTIVE and NATURAL_TTS
    else "data/pre-scripts/v6_pre_script_database.json"
    if V6_ACTIVE
    else "data/pre-scripts/pre_script_database.json"
)

# ── Simulation settings ─────────────────────────────────────
MAX_TURNS = 20
MAX_REPEATS = 3  # loop detection: N identical messages in a row → stop
MAX_TOOL_HOPS = 6  # safety cap on non-reply tool calls per turn
FILLER_TEXT = "รบกวนรอซักครู่ค่ะ"  # auto-inserted before final reply when any non-reply tool fires in the turn

COMPANY_PHONES = {
    "AEON": "02-035-6666",
    "JAI": "02-078-8899",
    "KS": "02-035-6666",
    "AIS": "1175",                  # AIS customer service hotline
}

# Thai brand names for templates that use [company_name] (v6).
COMPANY_NAMES = {
    "AEON": "อิอ้อน",
    "JAI": "กรุงศรีออโต้",
    "KS": "อยุธยาแคปปิตอล ออโต้ ลีส",
    "AIS": "เอไอเอส",
}

# Stylized agent first names used in greetings — fixes the Compliance Scale-2
# regression where v6 mining dropped the v5 "น้องอ้อน / น้องใจ / น้องแคร์" self-id
# from A_Greeting_Standard (93/119 cases hit "failing to self-identify").
COMPANY_AGENT_NAMES = {
    "AEON": "น้องอ้อน",
    "JAI": "น้องใจ",
    "KS": "น้องแคร์",
    "AIS": "น้องไอ",
}

# ── Phase 2: Multi-agent settings ──────────────────────────
MAX_JUDGE_RETRIES = 2       # max regeneration attempts per turn
EMOTION_WINDOW_SIZE = 5     # rolling window for emotion tracking
NEGATIVE_THRESHOLD = 4      # negative emotions in window to activate HMM


# ── Vertex AI client (singleton) ────────────────────────────
VERTEX_AI_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


@lru_cache(maxsize=1)
def _load_credentials():
    """Load Google credentials from GOOGLE_CREDENTIALS_JSON .env var."""
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if creds_json:
        info = json.loads(creds_json)
        return service_account.Credentials.from_service_account_info(
            info, scopes=VERTEX_AI_SCOPES
        )
    return None


def _env_bool(name: str) -> bool | None:
    """Parse a boolean env var. Returns None when unset or unrecognized."""
    val = os.environ.get(name, "").strip().lower()
    if val in ("1", "true", "yes", "on"):
        return True
    if val in ("0", "false", "no", "off"):
        return False
    return None


@lru_cache(maxsize=1)
def get_client() -> genai.Client:
    """Return a cached genai.Client instance.

    Backend selection (in order):
      1. Explicit `GOOGLE_USE_VERTEX` env var: "true"/"1" forces Vertex,
         "false"/"0" forces the direct Gemini API.
      2. Auto-detect: if a project ID is set, use Vertex; otherwise use
         the direct Gemini API.

    Vertex AI env vars:
      - `GOOGLE_PROJECT_ID` or `GOOGLE_CLOUD_PROJECT` (required) — GCP project ID
      - `GOOGLE_LOCATION` or `GOOGLE_CLOUD_LOCATION` (optional) — region,
        defaults to "us-central1"
      - `GOOGLE_CREDENTIALS_JSON` (optional) — full service-account JSON inline;
        if unset, falls back to Application Default Credentials (gcloud auth
        application-default login).

    Direct Gemini API env vars:
      - `GOOGLE_API_KEY` (required)

    Use the direct Gemini API when a model isn't available on Vertex yet
    (e.g. preview models) by setting `GOOGLE_USE_VERTEX=false`.
    """
    use_vertex_override = _env_bool("GOOGLE_USE_VERTEX")
    project = os.environ.get("GOOGLE_PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT")
    api_key = os.environ.get("GOOGLE_API_KEY")

    # Decide which backend to use
    if use_vertex_override is True:
        use_vertex = True
    elif use_vertex_override is False:
        use_vertex = False
    else:
        # Auto-detect
        use_vertex = bool(project)

    if use_vertex:
        if not project:
            raise ValueError(
                "GOOGLE_USE_VERTEX=true but no project is configured. "
                "Set GOOGLE_PROJECT_ID (or GOOGLE_CLOUD_PROJECT)."
            )
        location = (
            os.environ.get("GOOGLE_LOCATION")
            or os.environ.get("GOOGLE_CLOUD_LOCATION")
            or "us-central1"
        )
        credentials = _load_credentials()  # None = use ADC
        return genai.Client(
            vertexai=True,
            project=project,
            location=location,
            credentials=credentials,
        )

    if not api_key:
        raise ValueError(
            "No Google credentials configured. Set either:\n"
            "  - GOOGLE_PROJECT_ID (+ optional GOOGLE_LOCATION and "
            "GOOGLE_CREDENTIALS_JSON) for Vertex AI, or\n"
            "  - GOOGLE_API_KEY for the direct Gemini API.\n"
            "Override backend selection with GOOGLE_USE_VERTEX=true|false."
        )
    return genai.Client(api_key=api_key)
