# E2E voice-latency benchmark (real demo, headless browser)

Measures the demo's **real** end-to-end voice latency by driving the actual demo
in a headless Chromium and harvesting the demo's **own** per-turn numbers —
VAD, STT, LLM, TTS, end-to-end — the exact values shown in the in-app control
bar, aggregated over N turns (p50 / p95 / mean).

**There are no models and no constants.** Unlike `scripts/measure_ttfa.py` (a
server-side probe that *estimates* the browser leg with calibrated constants),
this harness measures the browser leg *for real*: it goes through the real
WebSocket, the real proxy, the real network, the real Silero VAD, the real
Chirp STT, the real Qwen/Gemini LLM, the real Chirp TTS, and the real Web Audio
playback. The figures it prints are the demo's, captured from the demo.

> Use this one for executive-facing numbers. Use `measure_ttfa.py` for fast
> server-side iteration where you don't need browser fidelity.

## How it works

- Overrides `getUserMedia` in the page (before any app script) to return a
  synthetic microphone driven by Web Audio. A per-turn `__benchPlayPCM(clip)`
  plays one fixed Thai WAV into that mic; the demo's real AudioWorklet
  downsamples and streams it over the real `/api/stt` WebSocket exactly as a
  live mic would. Between clips the mic is silent, so the server-side Silero VAD
  segments each utterance into a turn — just like a real call.
- One persistent browser session for the whole run, so realistic warm/idle
  connection state (and therefore real idle-reconnect behaviour) is preserved.
- Reads each turn's finalized latency record from the `?bench=1` console
  telemetry emitted by `demo/frontend/src/latency.ts`.

## Prerequisites

1. **The demo must be running in LIVE mode** (`AAX6_DEMO_MODE=live`) at the URL
   you point `--url` at — this is the real Qwen + Chirp path, not replay.
2. **The instrumented frontend must be deployed.** The `?bench=1` telemetry hook
   lives in `latency.ts`; rebuild + redeploy the frontend so it's in the served
   bundle. (The harness aborts with a clear error if the hook isn't present.)
3. **Run from a network location representative of your users.** The double-hop
   RTT to the backends is part of the real number — running from right next to
   the server will understate what a real user in-region experiences.

## Install & run

```bash
cd benchmark/e2e
npm install
npx playwright install chromium      # one-time: fetch the browser binary

# 20 measured turns (+1 warmup) against the deployed demo:
npm run bench -- --url https://<your-deployed-demo> --turns 20

# Watch it drive the browser (debugging), pick the agent, tune the idle gap:
npm run bench -- --url https://<demo> --turns 20 --headed --agent qwen --gap-ms 6000
```

> **Measure TTS cold — disable the cache.** The Thai clip set is repetitive, so the
> agent emits near-identical replies; with the TTS cache on, turns 2..N are instant
> cache hits and the TTS/TTFA numbers are the best case, not the real one. Start the
> **backend** with `AAX6_TTS_CACHE=0` so every turn does a true cold synth, then read
> the **Reply-only** table (the Tool table's first audio is the "please wait" filler,
> which is legitimately a cache hit in production, so it reads pessimistic when cold).
> Each table prints a **`TTS cache:` hit-rate** line — a cold run should show `0% hit`.

### Flags

| Flag | Default | Meaning |
|------|---------|---------|
| `--url` | *(required)* | Demo URL to drive (must be LIVE mode + instrumented bundle). |
| `--turns` | `20` | Measured turns. Use ≥20 for stable p95. |
| `--warmup` | `1` | Leading turns discarded (first turn is often cold). |
| `--reset-every` | `8` | Start a **fresh conversation** every N measured turns. A real debt-collection call is only a handful of coherent turns; one long session with unrelated clips drifts out-of-distribution and inflates LLM latency (the agent rambles to its 1024-token cap). `0` = one continuous session. Requires the `?bench=1` reset hook in the deployed build. |
| `--clips` | `../../scripts/thai-wav-dataset` | Directory of Thai `.wav` clips (16-bit PCM). Cycled if fewer than `--turns`. |
| `--gap-ms` | `4000` | Idle gap between turns. Realistic call pacing; also exercises idle-reconnect. |
| `--agent` | *(demo default = qwen)* | `qwen` or `gemini` — clicked pre-start, best-effort. |
| `--turn-timeout-ms` | `45000` | Max wait for a turn to complete before skipping it. |
| `--headed` | off | Show the browser window. |
| `--out` | `./results` | Directory for the raw JSON output. |

## Output

- A console table per **All / Tool / Reply-only** turns with **p50 / p95 / mean**
  for each stage (matching the control bar's stage names).
- `results/bench-<timestamp>.json` — every turn's raw record + summaries, for
  reproducibility and for building an exec chart later.

## What the numbers mean (say this to executives)

> Every figure is the demo's own measurement, captured live from the production
> deployment over N real turns — not an estimate.

One inherited definition to be aware of: **TTS** is timed to the first audio
buffer being *scheduled* (~80 ms optimistic versus truly audible). That is
exactly how the in-app control bar defines it, so the benchmark matches the demo
rather than inventing a stricter metric.

## Caveats

- The synthetic mic feeds clean pre-recorded audio (no room noise / echo). STT
  accuracy on these clips may be better than a noisy live caller — this measures
  *latency*, not field WER.
- Requires a Chromium that supports the fake-audio + Web Audio path (installed by
  `npx playwright install chromium`).
