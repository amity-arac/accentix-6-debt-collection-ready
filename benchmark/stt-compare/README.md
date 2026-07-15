# STT head-to-head: customer Zipformer vs Google Chirp

Phase 1 of the STT swap — **prove the win before integrating.** Runs the same Thai
clips through both engines, real-time-paced (mirroring the live mic), and compares
the number that drives conversational latency: **end-of-audio → final**.

- **Zipformer** (customer's self-hosted streaming server, Singapore) emits partials
  and decodes *during* speech → end-of-audio→final should be tiny.
- **Chirp 3** (Google, `us`) finalizes the whole utterance at stream close for Thai
  → end-of-audio→final ≈ full recognition (~1s), the bottleneck we've been chasing.

## Install

```bash
pip install websockets soundfile numpy resampy
# Chirp side reuses the repo's services/speech (needs google-cloud-speech + GOOGLE
# creds — already present in the aax6 env). Use --no-chirp to skip it anywhere else.
```

## Run

```bash
# From the pod (can reach the customer server AND has Chirp creds) — full head-to-head:
python compare_stt.py --server ws://34.87.38.92:2997 --limit 20

# Match the demo mic's 100ms chunking, add debt-collection hotwords:
python compare_stt.py --server ws://YOUR_STT --chunk-ms 100 --hotwords "AEON,KMOBILE,ค่ะ,ครับ" --boost 6

# Zipformer only (no Chirp creds where you're running):
python compare_stt.py --server ws://YOUR_STT --no-chirp
```

Point `--server` at your actual Zipformer server (the `34.87…` default is from the
customer's example client).

## Output

Per-clip lines + a summary per engine (p50/p95/mean) for **end-of-audio→final**,
**time-to-first-partial**, and **RTF**, plus a headline `Nx faster` ratio and a raw
JSON under `results/`. If the numbers confirm the win, we proceed to Phase 2
(replace Chirp in the demo's `stt_ws.py`).

## Notes

- **Real-time paced.** The customer's own client blasts chunks; we pace at
  `--chunk-ms` real-time so `end-of-audio→final` reflects the live-mic experience.
- **8 kHz.** The Zipformer server is telephony-grade 8 kHz; clips are resampled
  (band-limited, via `resampy`) to 8 kHz for it and 16 kHz for Chirp.
- Measures **latency**, not field accuracy (clean clips). Eyeball the printed
  transcripts for a rough agreement check; use hotwords for domain terms.
