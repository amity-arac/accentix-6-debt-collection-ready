# Accentix — outbound voice agent

A Thai-speaking agent that makes an outbound call and works through a script the
tenant defines. One JSON file per tenant decides everything the agent can say, every
tool it can call, and the order it must do them in. The model is served locally; no
per-token API bill.

Six tenants ship in this repo, and they are deliberately not all the same shape:

| | what the call is | tools |
|---|---|---|
| `AEON` `KBANK` `SKL` | debt collection — negotiate a payment date | 7 |
| `AMT` | a clinic confirming an appointment | 2 |
| `SHOP` | a shop reminding about tomorrow's instalment | 2 |

If a change only works for the debt tenants, it is not finished. That is what the
clinic and the shop are in here for.

---

## Run it

Three processes. The commands below are the ones the deployed host runs; the scripts
are in [ops/](ops/) and take their paths from environment variables.

```bash
bash demo_v2/ops/serve_aax6.sh   # 1 · vLLM on :8001      MODEL=… NAME=… to pick a checkpoint
bash ops/run_mock.sh       # 2 · mock tenant API on :3001  (Mockoon, mock/aax6-mock.json)
bash ops/run_demo.sh       # 3 · the app on :4100    serves the API and the built UI
```

`run_demo.sh` refuses to start if vLLM is down or the built frontend is missing, and
warns if the mock is not answering. Then open `http://127.0.0.1:4100`.

`ops/run_proxy.sh` puts it behind HTTPS + basic auth on :10000 for anyone outside the
host.

Without the scripts, the app is one line:

```bash
PYTHONPATH=. uvicorn demo_v2.server.app:app --host 127.0.0.1 --port 4100
```

The environment it reads:

```
AAX6_VLLM_BASE_URL(S)   where the model is        http://127.0.0.1:8001/v1
AAX6_API_BASE           the tenant's API          http://127.0.0.1:3001
AAX6_DEMO_STATIC        the built frontend        demo_v2/frontend/dist
AAX6_V6_ACTIVE=1        required
GOOGLE_APPLICATION_CREDENTIALS   voice only — without it everything runs, silently
```

Rebuilding the UI after a frontend change:

```bash
cd demo_v2/frontend && pnpm install && pnpm build
rsync -a --delete demo_v2/frontend/dist/ <host>:<app>/demo_v2/frontend/dist/
```

`--delete` matters, or the old hashed bundles pile up beside the new one.

---

## Reading the code

**`demo_v2/` is the product.** Start here and go in this order:

| | |
|---|---|
| [demo_v2/docs/MANUAL.en.md](demo_v2/docs/MANUAL.en.md) | the manual in English: authoring a tenant's spec, then taking it into the web app |
| [demo_v2/docs/MANUAL.md](demo_v2/docs/MANUAL.md) | the same manual in Thai |
| [demo_v2/docs/SPEC_LOCKED.md](demo_v2/docs/SPEC_LOCKED.md) · [English](demo_v2/docs/SPEC_LOCKED.en.md) | the tenant file format, every key |
| [demo_v2/README.md](demo_v2/README.md) | what `demo_v2` dropped from `demo/`, and how self-containment is proved |

About 80% of what you need is in `demo_v2/server/sessions.py`.

---

## Adding a tenant

Two doors, and they are for different people.

**Write the JSON** — the real path. One file, `data/flows/<CODE>.company.json`. Drop it
in and the company exists; delete it and it is gone. There is no index to update.
[SPEC_LOCKED](demo_v2/docs/SPEC_LOCKED.md) is the reference. Upload it through
`POST /api/flow/company/raw` or just put the file there.

**The wizard** — `POST /api/flow/company`, or the Builder in the UI. It clones the debt
template and lets you reword each beat, so what comes out is a debt-collection company.
An appointment flow or anything else uses the first door.

Either way the response names any placeholder nothing can fill, which is the moment to
fix it — after that the agent speaks the bracket out loud.

---

## Checking a change

```bash
python3 -m pytest tests -q                                   # 63 tests, ~1s
PYTHONPATH=. python3 tools/eval_demo.py --out /tmp/x.json
```

`eval_demo.py` plays the gold cases against a running app and scores beats, tools and
outcome per case. `--company AEON,KBANK,SKL,AMT` limits it.

> ⚠️ **One run is not evidence.** The same build, same host, same day has scored
> anywhere from 25 to 36 out of 45. Run at least three and report the range. A
> difference of two or three points is not a result.

---

## What is in here

```
demo_v2/          the product — server, flow interpreter, frontend, voice
data/flows/       one file per tenant. this is the whole product surface
data/test-cases/  gold cases and personas
mock/             Mockoon config standing in for the tenant APIs
ops/              the four scripts the deployed host runs
tools/            eval_demo.py, gen_mockoon.py, spec utilities
docs/             read these
docs/archive/     superseded notes, each stamped with why
tests/            pytest

demo/             THE OLD APP. kept for reference — do not build on it.
agents/ simulator/ services/                       what demo/ imports (kept for it)
checkpoints/      LoRA adapters from the SFT era (sft_v2*, sft_v10) — the served
                  model today is a full fine-tune under demo_v2/ops/serve_aax6.sh
```

`demo_v2` imports nothing from `demo/`, `agents/`, `simulator/` or the root
`services/`. To prove it rather than assume it:

```bash
mv demo /tmp/ && PYTHONPATH=. python3 -c "import demo_v2.server.app" && mv /tmp/demo .
```

A grep for the imports once passed while five modules still reached into `demo/`.
Hiding the directory is what actually shows it.
