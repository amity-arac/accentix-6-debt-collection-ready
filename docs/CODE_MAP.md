# Reading demo_v2

Not an API reference. This is the map for someone opening the repo for the first
time: where to start, and how one thing travels through the files.

Everything here points at a **name**, not a line number — line numbers drifted within
a single commit the first time this was written. `grep -n` the symbol.

The tenant file format is [SPEC_LOCKED.md](SPEC_LOCKED.md).
Building a new flow is [FLOW_WALKTHROUGH.md](FLOW_WALKTHROUGH.md).
The same shape as pictures — architecture, sequence, decision flow — is
[DIAGRAMS.md](DIAGRAMS.md).

---

## Four files carry the system

```
demo_v2/server/
  app.py                  557   HTTP + streaming. Very thin — almost no logic.
  sessions.py           2,106   The heart. FlowLiveSession lives here.
  flow/flowspec.py        672   Load + validate a spec, build tool schemas.
  flow/spec_backend.py    270   Actually call a tool.

  flow/spec_gate.py       194   ← read when you care what blocks a bad write
  flow/flowspec_render.py 317   ← read when you care what the prompt looks like
  flow/session_init.py    232   ← read when you care where the CRM row comes from

  tts.py · stt_ws.py      739   Voice. Independent of call logic.
lib/                      699   prescript (slot filling) · datetime_utils — vendored
services/speech/        1,030   TTS/STT engines
```

**About 80% of what you need is in `sessions.py`.** The rest is services around it.

---

## One call, end to end

### Opening a session — `app.py create_session()` → `sessions.build()` → `FlowLiveSession.__init__`

`__init__` is ~230 lines and does six things in order. Read it top to bottom.

```
1  load the tenant spec       _flow_spec_path() → load_tenant_spec()
2  load the catalog           _read_catalog()  — it is inline in the spec
3  fetch the CRM row          session_init.fetch_context()
                              → merged into self.customer_data
                              → failed + on_failure declared = say that line, hang up
                              → failed + nothing declared    = refuse to open at all
4  build the system prompt    render_instruction(spec)
                              + build_script_catalog(catalog)
                              + fill the CRM values into it
5  build the backend          SpecBackend(customer_data, spec)
6  build the tool schemas     build_tool_schemas(spec)
```

After this, `self._messages` holds one system message and the session is ready.

> `instruction_version` is still a parameter and is **ignored**. The
> `{stem}__{version}.json` override files are gone; a company is one file.

### The first line — `_greeting_hops()`

**No model call.** The app takes the `initial: true` state, its first beat, and that
beat's sentence from the catalog — then appends it to `self._messages` as an assistant
turn, so the order is greeting → customer → agent, matching what the model trained on.

### Every turn after — `_aiter_run()`

This is the function to understand. Everything happens here.

```python
for _loop in range(FLOW_MAX_TOOL_LOOPS):          # 8
    resp = _flow_vllm_chat(...)                   # call vLLM
    for tc in tool_calls:
        if tc.name == "reply":
            ids, text, dyn = self._render_reply(args)    # fill the slots
            # ── GUARD 1, before speaking ─────────────
            #   empty_slot · date_format_invalid
            #   missing_required_tools
            #   incomplete_chain
            #   too_many_beats
            # rejected → push a tool_result back into messages → continue
            push({"kind": "reply", ...})           # passed → the caller hears it
        else:
            result = self._backend.dispatch(...)   # GUARD 2 lives in here
            push({"kind": "tool_result", ...})

if not agent_text:
    ids, text = self._fallback_reply()             # loop ran out with nothing said
```

**The one thing to internalise:** a rejection does not raise and does not break out.
It is appended to `self._messages` in the same shape as any tool result, so the model
sees it on the next pass of the *same* `for _loop` and corrects itself.

---

## Three data shapes

### 1 — spec: a plain dict loaded from `<CODE>.company.json`

No class, no dataclass. Read with `.get()` everywhere.
The parts you touch most: `spec["states"]`, `spec["catalog"]`,
`spec["tools"]["declarations"]`.

`load_tenant_spec(path)` fills `company` / `flow_id` from the filename.

### 2 — catalog: a list of sentences

```python
{"text_id": 9004, "_fine_state": "confirm_new_date", "template": "รับทราบค่ะ ... [new_date] ..."}
```

The model refers to a sentence by `text_id`; the code refers to it by `_fine_state`.
`normalize_catalog()` fills in the fields that can be derived (`state`, `category`).

### 3 — hop: what streams to the UI

Four kinds, and only four.

```python
{"kind": "reply",       "text": ..., "text_ids": [...], "dynamic_vars": {...}}
{"kind": "tool_call",   "name": ..., "args": {...}}
{"kind": "tool_result", "name": ..., "result": {...}}
{"kind": "warning",     "text": ...}
```

`app.py _stream_turn()` turns them into NDJSON, one hop per line.
`frontend/src/components/Bubble.tsx` is the only place that decides how each kind
renders. Adding a new kind always means editing both.

---

## What each `flow/` module owns

| file | owns | entry point |
|---|---|---|
| `flowspec.py` | **the format** — allowed keys, validation, tool schemas | `load_tenant_spec` · `validate_strict` · `build_tool_schemas` |
| `flowspec_render.py` | **the prompt** — spec → the Thai instruction the model reads | `render_instruction(spec)` |
| `spec_backend.py` | **calling a tool** — check the args, then make the HTTP call | `dispatch(name, args)` |
| `spec_gate.py` | **guard 2** — counting, ordering, value matching | `check(name, args, call_log)` |
| `session_init.py` | **the CRM** — one call at session start, token substitution, flatten | `fetch_context(spec, seed)` |

`spec_gate.check()` is called from `spec_backend.dispatch()` and from nowhere else.

---

## Invariants the code holds but never states

**One state is one turn.** Every template in a state must be spoken together, unless it
carries `when_event`, which makes them alternatives. `is_chain_state()` in `flowspec.py`
is the single definition — both the prompt and the guard read that one function.

**`entry_tools` run before *speaking*, not on entering the state.** The app does not
track a current state: it resolves the beat the model chose back to the state that owns
it, then checks that state's tools. This is why a model that simply never speaks a
state's beat is never asked for its tools.

**`customer_data` is one dict, shared.** `FlowLiveSession` and `SpecBackend` hold the
same reference — not a copy. That is how a tool's result reaches the next sentence:
`_merge_context()` writes into it. **A defensive copy here breaks tool results silently**,
which has happened.

**A rejection never breaks the loop.** Both guards append to `_messages` and `continue`.
No exception, no early return.

**The two guards have different ceilings.** Guard 1 counts `_step_nudges < 2` and then
lets the reply through — a live caller must not be left hanging. Guard 2 has no ceiling,
because a bad write cannot be taken back.

---

## Where to change things

| you want to | file |
|---|---|
| add or change a spec key | `flow/flowspec.py` — `TOP_KEYS`, `STATE_KEYS`, … then `validate_strict` |
| change how the prompt reads | `flow/flowspec_render.py` — `render_instruction()` |
| add a check before a tool call | `flow/spec_gate.py` — `check()` |
| add a check before speaking | `sessions.py` — the `if fn["name"] == "reply"` branch inside `_aiter_run` |
| change how the LLM is called | `sessions.py` — `_flow_vllm_chat()` |
| add an endpoint | `app.py`, then tell `frontend/src/api.ts` |
| change slot filling | `lib/prescript.py` — `fill_template()` |
| anything about audio | `tts.py` · `stt_ws.py` · `services/speech/` |

---

## What you can skip

```
services/speech/*       STT/TTS engines — only when working on audio
lib/datetime_utils.py   Thai ↔ canonical date rendering
frontend/src/data/      static data
```

And names that **no longer exist** here. If you meet one in an older document, that
document is behind the code:

```
ReplaySession · LiveSession · flow/*_v12.py · _is_v12 · demo/server/prescript.py
```

---

## Running it

```bash
PYTHONPATH=. uvicorn demo_v2.server.app:app --host 127.0.0.1 --port 4100
```

Needs `AAX6_VLLM_BASE_URLS` (the model) and `AAX6_API_BASE` (the mock CRM).
Audio also needs `GOOGLE_APPLICATION_CREDENTIALS`; without it everything runs, just
without sound.

To confirm a change did not break anything:

```bash
PYTHONPATH=. python3 tools/eval_demo.py --model grpo400 --out /tmp/x.json
```

⚠️ One run is not evidence. The same configuration has moved 6 points out of 45 between
runs — do at least three and report the range.
