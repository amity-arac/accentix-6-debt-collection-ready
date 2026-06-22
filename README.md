# Accentix-6 — Thai Debt-Collection Agent (Qwen v2) · Live Demo

A self-contained package to **stand up the fine-tuned Qwen debt-collection agent and talk to it live** in your browser. You play the debtor; the agent (a QLoRA fine-tune of Qwen3.5-9B, "sft_v2_2") negotiates a payment arrangement in Thai following the company playbook and Thai Debt Collection Act compliance rules.

The agent supports four companies — **AEON, JAI, KS, AIS** — and runs the same deterministic backend tools (identity verification, payment recording, callback scheduling, etc.) used to train and benchmark it.

---

## Architecture

Three local processes:

```
┌─────────────────┐      ┌──────────────────────┐      ┌─────────────────────┐
│  Browser UI     │ ───▶ │  Demo backend        │ ───▶ │  vLLM server        │
│  (Vite/React)   │ HTTP │  (FastAPI)           │ HTTP │  Qwen3.5-9B + sft_v2_2│
│  localhost:5173 │ ◀─── │  localhost:4100      │ ◀─── │  localhost:8000     │
└─────────────────┘      └──────────────────────┘      └─────────────────────┘
        you type            orchestrates the agent          the LoRA model
       (the debtor)         + deterministic tools           (needs a GPU)
```

- **vLLM** serves the model and applies the `sft_v2_2` LoRA adapter (requires an NVIDIA GPU).
- **Backend** drives the agent: builds the per-company system prompt + tool catalog, runs the agent's tool-call loop against the deterministic `CaseBackend`, and streams hops to the UI.
- **Frontend** is the chat interface.

No Gemini / OpenAI API key is required to talk to the agent. (A Google Cloud project is needed *only* if you turn on the optional text-to-speech voice.)

---

## Prerequisites

- **NVIDIA GPU** with ~40 GB+ VRAM (Qwen3.5-9B + LoRA + KV cache). A100/H100-class recommended; tune `--max-model-len` for smaller cards.
- **CUDA** drivers compatible with vLLM 0.19.0.
- **Python 3.11**
- **Node.js 18+** and **npm**
- **git** + **git-LFS** (the adapter ships via LFS)
- **Hugging Face access** — the base model `Qwen/Qwen3.5-9B` auto-downloads on first serve (~18 GB).

---

## Repository layout

```
accentix-6-debt-collection-ready/
├── scripts/serve_qwen.sh    # start the vLLM server (base + sft_v2_2 LoRA)
├── checkpoints/sft_v2_2/      # the fine-tuned LoRA adapter (git-LFS)
├── demo/server/             # FastAPI backend (app, sessions, tts)
├── demo/frontend/           # React + Vite chat UI
├── agents/ simulator/ services/   # the agent, tools, prompt loading
├── data/                    # v8 prompts, the v6 tool catalog, demo cases
└── requirements.txt
```

---

## Setup

```bash
# 1. Clone, then fetch the LoRA adapter (git-LFS)
git clone <your-repo-url> accentix-6-debt-collection-ready
cd accentix-6-debt-collection-ready
git lfs install
git lfs pull                      # downloads checkpoints/sft_v2_2/adapter_model.safetensors

# 2. Python backend environment
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. vLLM (on the GPU host)
pip install vllm==0.19.0          # the version this adapter was validated under

# 4. Frontend dependencies
cd demo/frontend && npm install && cd ../..
```

---

## Configuration

Create a `.env` file **in the repository root** (this directory). The backend loads it automatically. Minimum settings for the live Qwen demo:

```ini
AAX6_V6_ACTIVE=1
AAX6_PROMPT_VERSION=v9
AAX6_DEMO_MODE=live
AAX6_DEMO_AGENT=qwen
AAX6_DEMO_CASE_ID=TC-AEON-AAX-025
AAX6_VLLM_BASE_URL=http://localhost:8000/v1
AAX6_VLLM_MODEL=sft_v2_2
```

| Variable | Required | Meaning |
|---|---|---|
| `AAX6_V6_ACTIVE` | yes (`1`) | Enables the v6 tool catalog + backend semantics. |
| `AAX6_PROMPT_VERSION` | yes (`v9`) | Loads the v9 per-company prompt: honest-AI disclosure (admits it's an automated assistant when asked) + the `transfer_to_human_agent` escalation for out-of-scope cases. **`sft_v2_2` was distilled under v9, so keep this `v9`** to match its training. |
| `AAX6_DEMO_MODE` | `live` | Live agent (default). |
| `AAX6_DEMO_AGENT` | `qwen` | Use the Qwen agent (default). |
| `AAX6_DEMO_CASE_ID` | optional | The persona loaded **on startup**. Default `TC-AEON-AAX-025`. You normally don't need to set this — use the in-app persona picker instead (below). Any id in `data/test-cases/personas_data.json` works. |
| `AAX6_VLLM_BASE_URL` | yes | vLLM endpoint, e.g. `http://localhost:8000/v1`. |
| `AAX6_VLLM_MODEL` | yes (`sft_v2_2`) | **Must be `sft_v2_2`** — the LoRA module name, *not* the base model. This is what applies the fine-tune. |

> Choosing a persona sets the company (the prefix after `TC-`, e.g. `AEON`) and the debtor's profile (name, debt amount, due date, the 4-digit ID for KYC). **All 152 personas** ship in `data/test-cases/personas_data.json` (≈38 per company across AEON / AIS / JAI / KS) and can be browsed and switched from the UI — see *Choosing a persona* below.

---

## Run

Open three terminals (all from the repository root unless noted).

```bash
# Terminal 1 — serve the model (first run downloads ~18 GB base model)
bash scripts/serve_qwen.sh
# Wait until ready, then confirm the adapter is loaded:
curl -s http://localhost:8000/v1/models     # should list "sft_v2_2"

# Terminal 2 — backend (reads your .env)
source .venv/bin/activate
uvicorn demo.server.app:app --port 4100

# Terminal 3 — frontend
cd demo/frontend && npm run dev
```

Then open **http://localhost:5173** and start typing the debtor's side of the conversation (in Thai). You'll see the agent's replies plus the tool calls it makes (identity verification, payment/callback recording, etc.) in the stream.

**Saving a conversation:** click the **Save** button in the control bar (any time after the first exchange) or on the end-of-call card. Each save writes a JSON file to `data/demo-saved-trajectory/<dd-mm-yy>/<case_id>-<HH-MM-SS>.json` — a canonical, replay-/eval-compatible trajectory (`conversation` + `full-trajectory`) plus the raw model message history (`agent_messages`).

---

## Choosing a persona

The card in the top-left shows the persona currently loaded. **Before starting a call**, click its header (company + case id) to open the persona picker — a pop-up listing all 152 personas. Filter by **company** (AEON / AIS / JAI / KS) or **track**, click a persona to see its account details and scenario (including the **last-4 digits** you'll need to pass the agent's KYC), then **Talk to this persona** to load it. Once a call has started the header is locked; **Reset** the call to switch again.

---

## Optional: text-to-speech (agent voice)

TTS is **off by default** and the UI works fully without it. To enable the Thai voice you need a Google Cloud project with the Text-to-Speech API enabled, then set either:

```ini
GOOGLE_CLOUD_PROJECT=your-gcp-project
GOOGLE_CREDENTIALS_JSON={...service account json...}
# or
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
```

If these are unset, audio requests fail silently and the chat continues normally.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `curl /v1/models` doesn't list `sft_v2_2` | Adapter not fetched — run `git lfs pull`. Confirm `checkpoints/sft_v2_2/adapter_model.safetensors` exists (~232 MB, not a tiny LFS pointer). |
| Backend error "model not found" / no fine-tune behavior | `AAX6_VLLM_MODEL` must be **`sft_v2_2`**, not `Qwen/Qwen3.5-9B`. |
| vLLM out-of-memory at startup | Lower `--max-model-len` in `scripts/serve_qwen.sh`, or use a larger GPU. |
| Agent replies but ignores the playbook / wrong language | Ensure `AAX6_V6_ACTIVE=1` and `AAX6_PROMPT_VERSION=v9` are set in `.env`. |
| Tool calls show as plain text | vLLM must run with `--tool-call-parser qwen3_xml` (set by `serve_qwen.sh`); keep vLLM at 0.19.0. |
| `KeyError: case_id …` on session start | Use a case id present in `data/test-cases/personas_data.json` (the default `TC-AEON-AAX-025` is valid), or just pick a persona from the in-app picker. |
| First serve is very slow | The base model (~18 GB) downloads from Hugging Face once; subsequent starts are fast. |
| `git: 'lfs' is not a git command` | Install git-LFS first (`brew install git-lfs` / `apt install git-lfs`), then `git lfs install && git lfs pull`. |

---

## What's included / not included

**Included:** the Qwen agent, its deterministic tool backend, the v9 per-company prompts + the full v6 tool catalog, all 152 demo personas (`personas_data.json`, selectable from the in-app picker), the **`sft_v2_2`** LoRA adapter (default), the web demo (backend + frontend), and the serve script. The previous `sft_v2` adapter is also bundled as a fallback — serve it with `AAX6_VLLM_MODEL=sft_v2 bash scripts/serve_qwen.sh` (and set the backend's `AAX6_VLLM_MODEL=sft_v2` to match).

**Not included** (by design): model training, the automated evaluator/benchmark harness, the Gemini-driven simulated customer, and other experimental agents. This package is scoped to *serving and talking to* the v2 agent.

---

## How it works (brief)

The agent operates a **closed catalog** of vetted Thai reply templates plus deterministic backend tools, so its outputs stay on-policy and compliant:

- **KYC**: it verifies the debtor's identity (`verify_identity`) before disclosing debt details.
- **Payment**: it captures a verbal commitment, then records the arrangement (`record_verbal_commitment` → `payment_date`).
- **Callbacks**: it can schedule a callback (`callback_datetime`) — no identity verification required for a callback.
- **Dates**: all dates are normalized via `get_current_datetime` to a canonical format.

`sft_v2_2` is a QLoRA fine-tune of Qwen3.5-9B distilled from a strong teacher under the **v9** prompt; it runs here under that same v9 prompt and full catalog. v9 adds two behaviors over the earlier `sft_v2`: it **discloses honestly** that it's an automated assistant when asked (never claims to be a human), and it **escalates to a human** (`transfer_to_human_agent`) on genuinely out-of-scope cases (deceased debtor, legal representation, etc.) instead of defaulting to a callback.
