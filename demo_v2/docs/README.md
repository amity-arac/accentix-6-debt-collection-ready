# Demo (Runs on H20)

|          |                              |
| -------- | ---------------------------- |
| URL      | https://101.47.153.162:10000 |
| User     | `demoapp`                    |
| Password | `demoapp`                    |

---

# Documentation

1. `MANUAL.md` and `MANUAL.en.md` explain how to create a new company in the demo.
2. `SPEC_LOCKED.md` and `SPEC_LOCKED.en.md` provide the full details of the JSON spec.

---

## Performance Test Results

**Criteria** — p50 ≤ 1s per turn, with 5s of caller think-time. Model inference time only; VAD, ASR, and TTS are excluded.

| Metric                        | Result             | Note                                                              |
| ----------------------------- | ------------------ | ----------------------------------------------------------------- |
| Concurrent calls · A100 80GB  | **24–32 calls**    | —                                                                 |
| Concurrent calls · A100 40GB  | **~16 calls**      | —                                                                 |
| Recommended rate limit        | **12 req/s**       | Queue remains empty; p50 = 745 ms                                 |
| Hardware ceiling              | **~14 req/s**      | Beyond this point, the queue grows and p50 exceeds 1s             |
| Prompt 5k–17.5k tokens        | **24–32 calls**    | Prompt size has little impact in this range                       |
| Prompt ~24.5k / ~34.5k tokens | **~16 / ~8 calls** | The cost of prompt length becomes significant above 24.5k tokens  |
| Mixed tenants on one GPU      | **≤16 calls**      | Beyond this point, short-prompt calls slow down by up to 76%      |
| Stability                     | **0 errors**       | Ramp tested up to 96 calls; nothing was dropped, only slowed down |
| Quality at long context       | **No degradation** | Gold 43/43 remained stable up to ~157k tokens                     |

**Note:** AEON is currently our largest flow, at approximately 13k tokens.
