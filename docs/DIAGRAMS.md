# demo_v2 in three diagrams

Companion to [CODE_MAP.md](CODE_MAP.md), which is the prose version.

1. [Architecture](#1--architecture) — what talks to what
2. [Sequence](#2--sequence-one-customer-turn) — one customer turn, end to end
3. [Decision flow](#3--decision-flow-one-model-command) — what happens to one model command

Every box and arrow here was checked against the source. GitHub renders these; in a
terminal, read [CODE_MAP.md](CODE_MAP.md) instead.

---

## 1 · Architecture

Four things are outside this service and can each fail independently: the model, the
tenant's API, and the two speech engines.

```mermaid
flowchart LR
    subgraph BROWSER["Browser"]
        UI["React app<br/>frontend/src"]
    end

    subgraph SVC["demo_v2 — one FastAPI process"]
        APP["app.py<br/>routes · NDJSON streaming"]
        SESS["sessions.py<br/>FlowLiveSession · the turn loop"]
        REND["flow/flowspec_render.py<br/>spec → instruction"]
        SPEC["flow/flowspec.py<br/>load · validate · tool schemas"]
        BACK["flow/spec_backend.py<br/>+ spec_gate.py<br/>tool dispatch · GUARD 2"]
        INIT["flow/session_init.py<br/>fetch the CRM row"]
        TTS["tts.py"]
        STT["stt_ws.py"]
    end

    subgraph DATA["On disk"]
        FLOWS["data/flows/<br/>&lt;CODE&gt;.company.json"]
        CASES["data/test-cases/<br/>personas"]
    end

    subgraph OUT["External — each can fail on its own"]
        VLLM["vLLM<br/>AAX6_VLLM_BASE_URLS"]
        CRM["tenant API<br/>AAX6_API_BASE"]
        GTTS["Google Chirp 3 TTS"]
        ASR["Zipformer ASR<br/>(or Chirp, AAX6_STT_ENGINE)"]
    end

    UI -->|"GET /api/session<br/>POST /turn"| APP
    UI -->|"GET /api/tts"| TTS
    UI <-->|"WS /api/stt"| STT

    APP --> SESS
    SESS --> SPEC
    SESS --> REND
    SESS --> INIT
    SESS --> BACK

    SPEC --> FLOWS
    SESS --> CASES

    SESS -->|"chat/completions"| VLLM
    INIT -->|"one call at session start"| CRM
    BACK -->|"one call per tool"| CRM
    TTS --> GTTS
    STT --> ASR

    classDef ui   fill:#ffffff,stroke:#6b7684,color:#1b2430
    classDef core fill:#e6f1f0,stroke:#22706c,color:#1b2430
    classDef disk fill:#f1f2f4,stroke:#8b94a0,color:#1b2430
    classDef ext  fill:#f6eddc,stroke:#8a5a12,color:#1b2430
    class UI ui
    class APP,SESS,REND,SPEC,BACK,INIT,TTS,STT core
    class FLOWS,CASES disk
    class VLLM,CRM,GTTS,ASR ext
```

Worth noticing:

- **The tenant's API is reached from two places** — once by `session_init` when the call
  opens, then once per tool by `spec_backend`. Nothing else in the process talks to it.
- **Nothing holds a database.** State for a call lives in the `FlowLiveSession` object;
  the tenant's system is the record.
- **Voice is a side branch.** `tts.py` and `stt_ws.py` never touch `sessions.py` — pull
  them out and the call still runs, silently.
- **`data/flows/` is the whole product surface.** Dropping in a file adds a company.

---

## 2 · Sequence: one customer turn

What happens between the caller finishing a sentence and hearing the next one.
The dotted return is the part people miss: a rejection goes back to the model as a
tool result, inside the same turn.

```mermaid
sequenceDiagram
    autonumber
    participant U as Browser
    participant A as app.py
    participant S as FlowLiveSession
    participant M as vLLM
    participant B as spec_backend<br/>+ spec_gate
    participant C as tenant API

    U->>A: POST /api/session/{id}/turn
    A->>S: aiter_turn(msg)
    Note over S: _aiter_run — up to 8 passes

    rect rgba(230,241,240,.55)
        S->>M: chat/completions (messages + tool schemas)
        M-->>S: tool_call: check_account_status
        S->>B: dispatch(name, args)
        B->>B: GUARD 2 — args · order · caps
        B->>C: POST /AEON/check_account_status
        C-->>B: {amount, due_date, …}
        B-->>S: result (merged into customer_data)
        S-->>A: hop tool_call + tool_result
        A-->>U: NDJSON line
    end

    rect rgba(248,233,231,.6)
        S->>M: chat/completions
        M-->>S: tool_call: reply([1047])
        S->>S: GUARD 1 — entry_tools not done
        S--)M: tool_result {sent:false, missing_tools:[…]}
        Note right of S: nothing spoken · same turn continues
    end

    rect rgba(230,241,240,.55)
        M-->>S: the missing tool calls, then reply([1047])
        S->>S: GUARD 1 passes
        S-->>A: hop reply
        A-->>U: NDJSON line
    end

    U->>A: GET /api/tts?text=…
    A->>U: audio stream
```

The loop bound is `FLOW_MAX_TOOL_LOOPS = 8`. If it runs out with nothing spoken,
`_fallback_reply()` says the tenant's fallback line rather than leaving the line silent.

---

## 3 · Decision flow: one model command

The model emits one command at a time. This is the whole of what the app does with it.

```mermaid
flowchart TD
    M["model emits one command"]
    M --> Q{"which kind?"}

    Q -->|"reply(text_ids)"| B["resolve text_id → beat<br/>1047 → close"]
    B --> S["beat → the state that owns it<br/>close → ptp_capture"]
    S --> E{"that state's entry_tools<br/>— all done?"}
    E -->|yes| SAY["caller hears it"]
    E -->|no| R1["not spoken<br/>reply with what is missing"]

    Q -->|"tool(args)"| G{"the tool's own gating<br/>— does this call pass?"}
    G -->|yes| API["call the tenant's API"]
    G -->|no| R2["not called<br/>reply with what is wrong"]

    R1 --> LOOP["model reads the reason<br/>and tries again, same turn"]
    R2 --> LOOP
    LOOP --> M

    classDef g1 fill:#e6f1f0,stroke:#22706c,color:#1b2430
    classDef g2 fill:#f6eddc,stroke:#8a5a12,color:#1b2430
    classDef rj fill:#f8e9e7,stroke:#a1372f,color:#1b2430
    classDef pl fill:#ffffff,stroke:#6b7684,color:#1b2430
    class B,S,E,SAY g1
    class G,API g2
    class R1,R2 rj
    class M,Q,LOOP pl
```

| | GUARD 1 — before speaking | GUARD 2 — before writing |
|---|---|---|
| stops | an incomplete or out-of-order sentence | wrong data reaching the tenant's system |
| cost of being wrong | the caller hears something odd; fixable next turn | written, and not retractable |
| ceiling | 2 nudges per turn, then let through | none — refused every time |
| where the rules come from | the state that owns the beat | the tool's own `gating` |
| does the app know the domain | no | no |

Guard 1 lets a reply through after two nudges because the person on the line is real,
and dead air is worse than an imperfect sentence. Every time it does, it logs a warning.

---

## Keeping these honest

The three diagrams describe code that changes. If you edit the flow, check these:

```bash
grep -n "FLOW_MAX_TOOL_LOOPS" demo_v2/server/sessions.py     # the loop bound in §2
grep -n "def dispatch"        demo_v2/server/flow/spec_backend.py   # GUARD 2's entry
grep -n "_step_nudges < 2"    demo_v2/server/sessions.py     # GUARD 1's ceiling
grep -rn "AAX6_API_BASE"      demo_v2                        # who reaches the tenant API
```

The last one is the check that matters most for §1: if it returns something outside
`session_init.py` and `spec_backend.py`, the architecture diagram is out of date.
