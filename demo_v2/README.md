# demo_v2 — the playground, without the lineages it outgrew

One kind of session: a tenant's `<CODE>.company.json` drives the call. What the old
`demo/` also carried, and this does not:

| dropped | why |
|---|---|
| `ReplaySession` | played a recorded 2026 trajectory; the product is live |
| `LiveSession` | the pre-flow agent (`agents.communicator` + `simulator.backend`) |
| `flow/*_v12.py` ×3 | a second copy of the interpreter for a checkpoint nobody serves |
| `_is_v12` branches | 10 of them through the live session |
| `demo/server/prescript.py` | byte-identical to `agents/prescript.py`, imported by nobody |
| `/api/mock-crm` | no caller |

```
demo/server/sessions.py  2,489 บรรทัด
demo_v2/server/sessions.py 2,118
```

แผนที่โค้ดสำหรับคนอ่านครั้งแรก: [docs/CODE_MAP.md](../docs/CODE_MAP.md) · ไดอะแกรม: [docs/DIAGRAMS.md](../docs/DIAGRAMS.md)

## Layout

```
server/     app.py · sessions.py · tts.py · stt_ws.py
server/flow flowspec · flowspec_render · spec_backend · spec_gate · session_init
lib/        prescript.py · datetime_utils.py   (vendored from agents/ and simulator/)
services/   speech — TTS/STT (vendored from services/)
frontend/   unchanged
```

## Run

```bash
PYTHONPATH=. uvicorn demo_v2.server.app:app --host 127.0.0.1 --port 4100
```

Same env as `demo/`: `AAX6_VLLM_BASE_URLS`, `AAX6_API_BASE`, `AAX6_DEMO_STATIC`,
`GOOGLE_APPLICATION_CREDENTIALS` for voice.

## Before trusting it

`tools/eval_demo.py` must score the same as it does against `demo/`. Anything else
means something still-used was cut.

## Frontend

Built from `demo_v2/frontend/src`, not copied. The bundle differs from `demo/`'s by 70
bytes — the lucide-react version string inside its license comments (v1.16.0 → v1.25.0,
same length, same file size). The dev-server proxy points at 4200.

```bash
cd demo_v2/frontend && pnpm install && pnpm build
rsync -a --delete demo_v2/frontend/dist/ <host>:<app>/demo_v2/frontend/dist/
```

`--delete` matters: without it the old hashed bundles pile up beside the new one.


## Self-contained

Nothing under `demo_v2/` imports `agents/`, `simulator/` or the root `services/`:

```bash
grep -rn "^from \(agents\|simulator\|services\)" --include=*.py demo_v2   # no hits
```

`_gen_id` (two lines) is vendored into `flow/spec_backend.py` rather than importing
`simulator.backend`, which would pull the whole pre-flow backend in for it.

Verified after the rewire: 35/36/37 on the same gold, TTS 200 through the proxy.
