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

## Latency tuning (voice pipeline)

When the voice path is on, time-to-first-audio (TTFA) is the serial chain
**VAD endpointing → STT → LLM → TTS** (VAD *compute* overlaps speech and is off
the critical path; the endpointing *hang* is what you feel). These env knobs trade
latency for robustness/accuracy. Defaults are already tuned for speed; raise them
if quality suffers. All are optional.

| Variable | Default | Effect |
|---|---|---|
| `AAX6_STT_MODEL` | `chirp_3` | STT model. **Thai (`th-TH`) is only served by `chirp_3` / `chirp_2` / `chirp` in `asia-southeast1`** — the low-latency `short` / `long` conformer models return a 400 (`language "th-TH" not supported by model "short"`), so they can't be the Thai default. Try `chirp_2` / `chirp` and measure with the probe if you want lower latency. |
| `AAX6_VAD_SILENCE_HANG_MS` | `250` | Trailing silence (ms) before an utterance is finalized — the leading term of TTFA on *every* turn. Lower = snappier; too low cuts callers off mid-thought / lets TTS echo trip a false barge-in (validate live). Was `500 → 350`. |
| `AAX6_VAD_THRESHOLD` | `0.4` | Silero speech-probability gate (0–1). Higher = stricter (fewer false triggers, may clip soft speech). |
| `AAX6_VAD_MIN_SPEECH_MS` | `100` | Sustained speech required before `speech_begin` fires (barge-in debounce). |
| `AAX6_STT_DIRECT_FINAL` | `1` | Run the final `recognize()` on its own thread so it never queues behind an in-flight interim (~150–500 ms off STT). `0` reverts to the single-worker queue. |
| `AAX6_TTS_PREWARM_FILLER` | `1` | Pre-synthesize the spoken "please wait" filler at startup so its first play (the **first audio** on a tool turn) is a cache hit (~50–100 ms) instead of a cold ~500 ms synth. `0` disables. |
| `AAX6_TTS_PREWARM_REPLY` | `1` | Kick the reply's Chirp synth server-side the moment the reply is emitted, so it overlaps hop delivery + the client prefetch (~50–150 ms, more over WAN). `0` disables. |
| `AAX6_TTS_CHUNK_TARGET` | `30` | First audio-chunk flush target (chars). Lower = earlier reply first-audio on long clauses, but risks Thai prosody artifacts; short replies with an early particle (ค่ะ/ครับ) are unaffected. |
| `AAX6_TTS_ENDPOINT` | *(unset → global)* | Optional regional TTS endpoint, e.g. `asia-southeast1-texttospeech.googleapis.com`, to cut first-chunk latency. ⚠️ Regional endpoints don't carry every model — a wrong value can 404 Chirp-3-HD streaming. Measure a candidate with the probe below before committing. Needs a backend restart. |

> vLLM prefix caching is enabled explicitly in `scripts/serve_qwen.sh` (`--enable-prefix-caching`). Qwen3.5 is a hybrid (gated-deltanet) model, so confirm it's actually taking on the host — scrape `/metrics` for `vllm:prefix_cache_hits_total` vs queries on the first hop; it may need a hybrid-cache alignment flag and may not cache short prefixes. If startup aborts complaining caching is unsupported, remove the flag.

### Measure it

`scripts/measure_ttfa.py` drives real WAV files through the whole pipeline
(VAD → STT → LLM → TTS) and prints the measured per-stage + end-to-end TTFA
(mean / p50 / p95), replacing estimates with real numbers:

```bash
source .venv/bin/activate
# vLLM must be running and .env configured (creds + AAX6_VLLM_BASE_URL/MODEL)
python scripts/measure_ttfa.py --wav-dir scripts/thai-wav-dataset --json ttfa.json
# --stream: measure the REAL streaming first-audio (the "please wait" filler
# fires at first-token, like the live demo). Default is blocking per-hop timing.
python scripts/measure_ttfa.py --wav-dir scripts/thai-wav-dataset --stream
# A/B a knob: rerun with an override to quantify the tradeoff (Thai: chirp_3/chirp_2/chirp)
AAX6_STT_MODEL=chirp_2 python scripts/measure_ttfa.py --wav-dir scripts/thai-wav-dataset
# Sanity-check the pure logic anywhere (no GPU/GCP needed):
python scripts/measure_ttfa.py --self-test
```

The probe auto-detects the served vLLM model from `/v1/models` (pin with `--model`).
In `--stream` mode the report adds an **`LLM first token`** row (when the filler
fires) and its **TTFA-first-audio** reflects that early fire; `--stream` and the
default blocking mode report the same total/substantive-reply numbers.

**Reading the numbers (what the probe does and doesn't model).** The report
separates **measured** stages (STT recognize, LLM hops, TTS) from **added
constants** it can't observe from a WAV. Two of those constants matter on a real
deployment and default conservatively:

- `--stt-queue-ms` (default `300`): models the final `recognize()` queuing behind
  an in-flight interim on the single STT worker. With `AAX6_STT_DIRECT_FINAL=1` (the
  default) the final runs on its own thread and this wait is ~removed — set
  `--stt-queue-ms 0` to model that; keep `300` for the legacy single-worker path.
- `--rtt-ms` (default `0`): browser↔server round-trips (hop NDJSON + `/api/tts`
  GET). ~0 on localhost; set **~400–1000** for a RunPod-proxy-style remote host.

VAD Silero compute is reported **off the critical path** (it runs per-chunk while
the caller is still talking; only ~1–2 ms is serial). The spoken "please wait"
filler is prewarmed into the TTS cache at server startup (`AAX6_TTS_PREWARM_FILLER`),
so its first-audio is a cache hit (~50–100 ms) from the very first turn. The LLM
stage is **deterministic** (temp 0 / seed 1 / one persona /
fresh turn-1) — its spread reflects transcript variation only, not the live demo's
unpinned sampling, retries, or multi-hop turns; read it as a best-case first turn.

**TTS is *client* first-audio, not server synth time.** The in-app control bar measures
TTS as a clip's `a.src`→`playing` time in the browser (Chirp streaming first chunk +
network + the browser's Opus buffer-to-playback) — the number the caller feels. A
server-side probe can't observe the browser leg, so TTS is modeled as calibrated
constants: `--tts-client-ms` (default **550**) for the substantive reply, and
`--filler-tts-client-ms` (default **100**, a warm cache hit; ~**500** cold) for the
spoken filler. The server-side `tts_ttfb` (~120 ms) / `tts_total` (whole-clip synth)
are still measured but shown only informationally. **The filler *is* spoken:** the
"please wait" bubble is emitted as a `reply` hop ([sessions.py](demo/server/sessions.py)
relabels the pending tool_call → `{"kind":"reply","text":FILLER_TEXT}`) and
[useSession.ts](demo/frontend/src/hooks/useSession.ts) speaks every `reply` hop — so on
a tool turn the filler is the **first sound** (the headline `TTFA — first heard` clock),
and the substantive answer follows. **Fragility:** replies are fetched via a
prefetch-then-replay path that serializes on a per-text lock
([tts.py](demo/server/tts.py)), so a *long* reply can spike the substantive clock toward
its full synth — short debt-collection replies hide it.

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
