# Manual — Creating a New Tenant on Accentix

This document describes how to create a new company (tenant) on Accentix, where **one company is one JSON file** holding everything about that company.

Uploading the JSON file is how a company is created, and deleting it through the UI is how a company is removed. The application holds no business logic tied to any particular company.

## Prerequisites

What you need:

* A web browser
* The URL of the web application
* No additional software to install
* No access to the system's source code
* Your company's API — one endpoint per tool declared in the specification
* Customer data for test calls, which can be supplied through the uploaded file; connecting a real CRM is not required yet

The system has two documents:

| Document | Description |
| -------- | ----------- |
| [ไทย](MANUAL.md) · **MANUAL.en.md** (this document) | How to author a company's specification and take it into the web app |
| [SPEC_LOCKED.en.md](SPEC_LOCKED.en.md) · [ไทย](SPEC_LOCKED.md) | Every key in `<CODE>.company.json`, used as the reference document |
| [SERVING.md](SERVING.md) (ไทย) | Serving the model with vLLM so the app can reach it |
| [CODE.md](CODE.md) | How the app is built — part A for whoever demos it, part B for whoever takes over the code |

This document has two parts:

1. Authoring `<CODE>.company.json` in seven steps
2. Using the Demo App: uploading, holding a test conversation, and diagnosing why an agent answered incorrectly

> **The platform provides the mechanism; the tenant defines the policy.**
> Rules specific to your company belong in `constraints`, which become text in the prompt, or in your own API, which refuses a request that does not meet its conditions.
> A rule with no mechanism behind it cannot actually be enforced.

---

# 1. Authoring `<CODE>.company.json`

Start with **Upload → Download template** in the web app to download a JSON skeleton containing every key in the shape the app accepts, then fill it in following the seven steps below.

The filename is the company's identity, so you do not need to declare `flow_id` or `spec_version` yourself.

`company` goes inside `spec`, because that is what identifies the company code at upload time.

## Required keys

Five keys are required:

* `events`
* `states`
* `tools`
* `faq_routing`
* `constraints`

If any one of them is missing, validation fails.

With no FAQ, set `faq_routing` to `{"routes": []}`; with no additional restrictions, set `constraints` to `[]`. The keys themselves must still be present.

`catalog` is not a required key. Without it, however, the agent has no sentences to use in a conversation.

```text
1. events        what the caller can do or respond with, and how the call can end
2. states        the map: conversational beats, tool calls, state changes, and how the call ends
3. tools         the facts the system must answer, rather than leaving the model to guess
4. catalog       a sentence for every beat the map refers to
5. faq_routing   handling questions raised mid-flow and returning to the original flow
6. crm/session   where customer data comes from, what the model may access, and what happens when the API does not respond
7. constraints   your company's rules — declare only the necessary ones, adding them one at a time with measurement
```

## The agent's identity

Before defining `events`, consider the three fields that state the agent's role and purpose:

| field | Where it is used | Default when not set |
| ----- | ---------------- | -------------------- |
| `display_name` | The name shown in the UI and substituted for `[company]` | The filename |
| `agent_role` | The first line of the prompt, stating who the agent is | `"a debt collection officer of <CODE>"` (in Thai) |
| `goal` | The line after `agent_role`, stating the purpose of the call | That line is omitted |

The default for `agent_role` was written for debt-collection flows specifically, so **a tenant not working in debt collection should set `agent_role` explicitly.** Otherwise the prompt will describe the agent as a debt collection officer.

Always define `states` before `catalog`, because the map determines which kinds of sentences must exist — not the other way round, writing sentences first and then finding a place for them.

---

## Step 1 — `events`

Begin by identifying how the caller can respond and how the call can end.

Every event name referred to in `states[].on[].event` or `constraints[].event` must be declared in `events`, or validation fails.

```jsonc
"events": {
  "confirms_return": {
    "desc": "acknowledges, will return it on time",
    "cues": ["ได้ครับ", "คืนตามกำหนด", "จะไปคืน", "รับทราบ"]
  },
  "request_extend": {
    "desc": "asks to extend the loan",
    "cues": ["ขอต่อ", "ต่ออายุได้ไหม", "ขอยืมต่อ", "เลื่อนคืนได้ไหม"]
  },
  "gives_date": {
    "desc": "names the date they want to return it",
    "cues": ["วันที่ 20", "ศุกร์หน้า", "สัปดาห์หน้า", "สิ้นเดือน"]
  },
  "no_input": {
    "desc": "no answer / silence",
    "cues": ["…"]
  }
}
```

An event must be an object with `desc` and `cues`, not a string. A string fails `validate_strict`, and the system reports the correct shape.

`cues` are example phrases that give the event meaning for the model. They are rendered into the prompt, trimmed to the first four:

```text
confirms_return (acknowledges, will return it on time — e.g. ได้ครับ, คืนตามกำหนด, จะไปคืน, รับทราบ)
→ close_confirmed
```

`cues` may be omitted. The prompt then shows only `desc`, and the model has to interpret the event from that description alone.

---

## Step 2 — `states`

`states` is the backbone of the flow. It defines the conversational beats, the tools that must be called, and the transitions between states.

```jsonc
{
  "id": "greet",
  "phase": "opening",
  "initial": true,
  "templates": [{"fine_state": "greet_remind"}],
  "entry_tools": ["check_account_status"],
  "on": [
    {"event": "confirms_pay", "to": "close_confirmed"},
    {"event": "reschedule_request", "to": "ask_new_date"}
  ],
  "verify_required": true,
  "outcome": {"args": {"result": "confirmed"}},
  "terminal": false,
  "max_visits": 2
}
```

### `outcome.args`

`outcome.args` holds the arguments the closing tool will receive, written with the argument names that tool declares.

For example, if an appointment tool declares `save_appointment(status, …)`, the value in `outcome.args` must use the name `status`.

The first argument a tool declares is the call result, and it is what ties the closing sentence to the result recorded.

Each terminal state must define its own result value; a single outcome should not be used for every ending path.

`outcome` belongs on a terminal state. If a terminal state has no `outcome`, the call ends with no result to record in the CRM.

Every terminal state should also carry the closing tool in its `entry_tools`. Without it, the call ends with nothing recorded, and there is no dedicated check for this case.

### `verify_required`

`verify_required: true` only takes effect when a tool declares `provides: "verified"` together with `verified_when`, which states how to read the API's answer.

```jsonc
"states": [
  {
    "id": "disclose",
    "verify_required": true
  }
],
"tools": {
  "declarations": [
    {
      "name": "verify_identity",
      "provides": "verified",
      "verified_when": {
        "field": "match",
        "equals": true
      }
    }
  ]
}
```

If either half is missing, `_verify_gate()` cannot build a verification gate. The state is then not blocked at all, and nothing warns you.

---

## Step 3 — `tools`

The guiding principle is that **the system should supply the facts** and the model should interpret or act on what the system returns.

Data such as dates, amounts, ceilings, entitlements and balances should come from a tool rather than being calculated or guessed by the model.

`tools` must be an object, and every declaration belongs inside `tools.declarations`.

```jsonc
"tools": {
  "declarations": [
    {
      "...": "..."
    }
  ]
}
```

### The closing tool

There must be exactly one closing tool, declared with:

```jsonc
"gating": {
  "max_successful_calls": 1,
  "required_at": "end_of_call"
}
```

Only one closing tool may exist per specification, and the system provides no default.

If a specification has a state with an `outcome` but no closing tool, it may pass validation and still be unable to assemble a prompt, raising `ValueError: spec declares no closing tool`.

Example:

```jsonc
{
  "name": "record_call_result",
  "impl": "http",
  "url": "https://api.yourcompany.co.th/aax/record_call_result",
  "method": "POST",
  "headers": {
    "Authorization": "Bearer YOUR_TOKEN"
  },
  "timeout": 8,
  "desc": "record the call result",
  "args": {
    "result": {
      "type": "string",
      "enum": ["confirmed", "handoff"]
    }
  },
  "returns": {
    "saved": {
      "type": "boolean"
    }
  },
  "gating": {
    "max_successful_calls": 1,
    "required_at": "end_of_call"
  }
}
```

### Tools must point at the tenant's own endpoints

Every declaration must set:

```json
"impl": "http"
```

and give the `url` of an endpoint the company operates.

The tenant is responsible for providing one endpoint per declared tool. The system has no central API to stand in for a tenant's endpoint.

Do not copy the `{API_BASE}` that appears in the system's own example specifications: it is an environment variable belonging to the server that runs the platform.

In a tenant's specification, give the endpoint's full URL.

| key | Description |
| --- | ----------- |
| `url` | The endpoint's full URL, e.g. `https://…` |
| `method` | Defaults to `POST` |
| `headers` | For a token or API key; values may be substituted from the CRM, as in `url` and `body` |
| `timeout` | In seconds, default 8; exceeding it counts as a refusal |
| `body` | Optional; when not set, the system uses the standard shape |

There is an `impl: "generic"` for drafting a specification while the endpoints are not ready, but it is not suitable for a specification in real use, because nothing is recorded to the tenant's systems.

### The contract between the system and your endpoint

#### ① Request

When `body` is not set, the system sends the same standard request shape for every tool:

```jsonc
POST https://api.yourcompany.co.th/aax/record_call_result

{
  "tool": "record_call_result",
  "args": {
    "result": "confirmed"
  },
  "ref": {
    "msisdn": "081-234-5678",
    "customer_phone": "…",
    "last_4_digits": "…",
    "case_id": "…"
  }
}
```

One handler can serve every request, switching on the `tool` field.

`ref` contains only the keys that hold data in that call's CRM.

If an endpoint has a request schema of its own, set `body` to describe the shape you want.

#### ② Response

The endpoint must answer with a JSON object. That JSON becomes the tool's result directly and is merged into `customer_data`, so the next sentence can refer to it.

Nested data can be referred to both by its full path, e.g. `appointment.doctor`, and by the leaf field name, e.g. `doctor`.

Declare every field the endpoint can return in `returns`.

| Response | What the model receives |
| -------- | ----------------------- |
| `200` + JSON object | The whole JSON object, flattened |
| `200` + plain text such as `OK` | `{"recorded": true, "response": "OK"}` — counted as success, but with no data for the next sentence to refer to |
| non-2xx / timeout / connection failure | `{"error": "http_error", "detail": "http_404: …"}`, handed back to the model to read |

#### ③ Refusing a request

An endpoint can refuse a request by returning a field named `error`.

The system reads `error` as a refusal signal in the same way as its internal gates, and the model receives the reason so it can try again within the same turn. The caller never hears the refused attempt.

```jsonc
{
  "error": "date_out_of_range",
  "valid_dates": [
    "2026-09-12 (Saturday)",
    "…"
  ]
}
```

Enforcing your company's rules should therefore happen in an endpoint that can genuinely refuse. `constraints` is a requirement passed to the model and is not an enforcement mechanism in itself.

Example tool:

```jsonc
{
  "name": "check_new_date",
  "impl": "http",
  "url": "https://api.yourcompany.co.th/aax/check_new_date",
  "method": "POST",
  "desc": "check whether the date the caller named is within the shop's window — the system decides",
  "args": {
    "date": {
      "type": "string",
      "format": "YYYY-MM-DD (Weekday)"
    }
  },
  "returns": {
    "in_range": {
      "type": "boolean"
    },
    "valid_dates": {
      "type": "array"
    }
  },
  "gating": {
    "after_event": "gives_date",
    "must_precede": ["record_call_result"]
  }
}
```

`returns` is not only documentation: it is what the system merges into `customer_data` for use in the next sentence.

### `one_of_from`

If a tool's argument must be chosen from values the system offered, declare `one_of_from`.

```jsonc
"new_date": {
  "type": "string",
  "optional": true,
  "required_when": {
    "arg": "result",
    "equals": "rescheduled"
  },
  "one_of_from": {
    "tool": "check_new_date",
    "field": "valid_dates"
  }
}
```

Here `new_date` must be one of the values `check_new_date.valid_dates` has offered.

`one_of_from` can only check when the source tool has been called and there is a list of offered values. With no call to the source tool, there is nothing for the check to compare against.

To require the tool be called first, use `gating` as well. In general, declare both:

* `one_of_from` to prevent a value that did not come from the system
* `gating` to prevent a step being skipped

---

## Step 4 — `catalog`

`catalog` is the store of sentences used by each beat.

```jsonc
{
  "text_id": 2341,
  "_fine_state": "greet_remind",
  "template": "สวัสดี{suffix} ร้านค้าโทรมาแจ้งเตือนงวดของคุณ [customer_name] ที่ครบกำหนด [due_date]{suffix}"
}
```

A `template` may use the following forms, in the order they resolve:

```text
{suffix} {q_suffix} {pronoun}
{{if field}}…{{else}}…{{/if}}
[field]
{field}
```

`[field]` and `{field}` behave identically, except that `{field}` also collapses a duplicated prefix — `"คุณ นายเอกชัย"` becomes `"นายเอกชัย"`.

Further detail on placeholders, the mapping, the fallback to a Thai phrase, and date validation is in [SPEC_LOCKED.en.md §3](SPEC_LOCKED.en.md).

Pick one form, `[field]` or `{field}`, and use it consistently within a new file, even though the system currently accepts both.

### `text_id` and `_fine_state`

Keep the two meanings clearly apart:

| | `_fine_state` (beat) | `text_id` |
| - | -------------------- | --------- |
| Meaning | Which subject is being spoken about | Which wording is used |
| Unit | One beat in the flow | One sentence |
| Count | AEON has 40 beats, for instance | AEON has 58 sentences, for instance |
| Referred to by | `states`, `faq_routing`, `fallback_fine_state`, `constraints` | The model, when it calls `reply(text_ids=[…])` |
| Durability | A structural name | An identifier for a sentence, which may change |

How it works:

```text
the model calls reply(text_ids=[1047])
        ↓
_by_id[1047]
        ↓
beat "close"
        ↓
the state that owns the beat, via _beat_states()
        ↓
check that state's entry_tools
        ↓
allowed to speak
```

Separating the two levels means wording can be added or changed without editing the flow, as long as the beat's intent stays the same.

`text_id` should not be used as an identifier with business meaning, because the system can renumber it for each test call and in training data.

When writing a rule or a hint, refer to the beat, and give the `text_id` alongside it as a reading aid — but do not use the number as the primary reference.

### How `states` and `catalog` relate

```text
states[]                                  catalog[]._fine_state + text_id
─────────────────────────────────         ─────────────────────────────────
state "disclose_ask"
  verify_required: true
  templates:
    { any_of: [
        "disclose_balance" ───→ 1018   "ขออนุญาตแจ้งยอด…"
        "ask_pay_today"    ───→ 1020   "ตามที่ท่านได้นัดชำระ…"
      ]
      note: "one sentence states the         1021   "ถ้าเป็นยอดขั้นต่ำ สะดวกวันนี้เลยมั้ย"
             balance and asks at once"       1023   "ชำระยอดขั้นต่ำภายในวันนี้ก่อนได้ไหม"
                                             1028   "ไม่ทราบว่าสะดวกชำระภายในวันนี้มั้ย"
  on:                                        1039   "รอบบิล ครบกำหนด [due_date]…"
    agrees_to_pay → ptp_capture              1066   "ชำระยอดขั้นต่ำภายในวันนี้ก่อนได้มั้ย"
    already_paid → close_paid
```

The map refers to beats; the catalog binds a beat to the sentences that may be used for it.

### Chain state

What marks a state as a chain is `when_event`, not the number of templates.

| Shape of `templates` | Meaning |
| -------------------- | ------- |
| One entry | Not a chain |
| ≥2 entries, at least one with `when_event` | Alternatives selected by event |
| ≥2 entries, none with `when_event` | A chain — every entry is used in the same turn |
| `any_of` inside one entry | A choice within that entry, unrelated to chaining |

A chain:

```jsonc
"templates": [
  {"fine_state": "close"},
  {"fine_state": "apology"}
]
```

Alternatives selected by event:

```jsonc
"templates": [
  {
    "fine_state": "convince_lost_job",
    "when_event": "hardship_lost_job"
  },
  {
    "fine_state": "convince_sick",
    "when_event": "hardship_sick"
  },
  {
    "when_event": "hardship_other",
    "any_of": ["convince_other", "convince_pay"]
  },
  {
    "fine_state": "probe_hardship",
    "optional": true,
    "counts_as": false
  }
]
```

The chain states in the current tenants are:

```text
AEON / AEONLITE   close_unreachable · close_new_phone
KBANK             ptp_capture · close_unreachable
SKL               disclose_ask · ptp_capture · close_confirm_info
AMT / SHOP / LIB  none
```

If `when_event` is not set on every entry of a state that should offer alternatives, the model may be instructed to speak several of them in one turn.

Conversely, leaving out a template that belongs to a chain means the model may say only part of it and be caught by `incomplete_chain`.

### Deciding between a new beat and a new wording

Use this rule:

```text
same effect on the flow, only the wording differs
→ add a text_id

different effect on the flow
→ add a fine_state
```

For example, LIB's `faq_due` can have three wordings — short, normal, and detailed with the fine — while remaining one beat, because all three have the same effect on the flow.

Conversely, `confirm_new_due` and `date_too_far` must be separate beats, because the flow goes to different places.

> `_fine_state` = the intent and its position in the flow
> `text_id` = the form of words used to convey that same intent

### The naming each side requires

```jsonc
"catalog": [
  {
    "text_id": 2341,
    "_fine_state": "greet_remind"
  }
]

"states": [
  {
    "templates": [
      {
        "fine_state": "greet_remind"
      }
    ]
  }
]
```

In `catalog` the key is `_fine_state`, with the leading `_`. In `states` it is `fine_state`, without it.

Swapping the two may let an entry pass some checks while failing to bind to the catalogue correctly; `validate_strict` detects this and reports the incorrect form.

---

## Step 5 — `faq_routing`

`faq_routing` handles questions that arise mid-flow: the question is answered and the call returns to the state it was in.

```jsonc
"faq_routing": {
  "routes": [
    {
      "intent": "hours",
      "desc": "asks the shop's opening hours",
      "templates": [
        {
          "fine_state": "faq_hours"
        }
      ],
      "then": "resume"
    }
  ]
}
```

| key | Meaning |
| --- | ------- |
| `intent` | The route's name, as used in the prompt |
| `desc` | What case the route applies to; when unset it becomes `""` |
| `templates` | The beat that answers, which needs a sentence in `catalog` |
| `then` | `"resume"` to return to the original flow, or `{"terminal": true, "outcome": {"args": {...}}}` to end the call |
| `verify_required` | Requires identity verification before answering, when the answer discloses customer data |

`faq_routing` is a required key. With no FAQ, set it to:

```json
{
  "routes": []
}
```

An FAQ beat belongs to no state directly, so it should not be judged from the state graph alone.

When checking for orphans, consider the beats referred to in:

* `states`
* `faq_routing.routes`
* `fallback_fine_state`

---

## Step 6 — CRM and session

An example CRM configuration:

```jsonc
"crm_fields": [
  "today",
  "customer_name",
  "book_title",
  "due_date",
  "fine_per_day",
  "library_phone"
],
"crm_labels": {
  "book_title": "ชื่อหนังสือ",
  "fine_per_day": "ค่าปรับต่อวัน"
},
"session_init": {
  "url": "https://api.yourcompany.co.th/aax/init?msisdn={msisdn}",
  "method": "GET",
  "timeout": 8,
  "headers": {
    "Authorization": "Bearer YOUR_TOKEN"
  },
  "on_failure": {
    "fine_state": "close_handoff",
    "outcome": {
      "args": {
        "result": "handoff",
        "reason": "ระบบไม่ตอบ ให้เจ้าหน้าที่ติดต่อกลับ"
      }
    }
  }
}
```

`crm_fields` names the fields the model can see. A field that is not declared is one the model cannot reach.

`crm_labels` sets the Thai labels shown in the CRM Snapshot; without them the system uses the field names.

`session_init` is called once, when the call opens.

If `session_init` is declared, `on_failure` must be declared too. Without `on_failure` the system cannot open the call correctly.

Dates in a persona should not be fixed values. Use `due_date_offset_days: 1` so that the application and the mock both compute from the current date.

---

## Step 7 — `constraints`

`constraints` declares the requirements passed to the model.

```jsonc
"constraints": [
  {
    "id": "one_template_per_turn",
    "type": "max_templates_per_reply",
    "max": 1,
    "desc": "พูดหนึ่งจังหวะต่อเทิร์น",
    "enforce": ["prompt"]
  },
  {
    "id": "greeting_once",
    "type": "once_per_call",
    "template_fine_states": ["greet_remind"],
    "desc": "ทักทายแจ้งกำหนดคืนครั้งเดียวต่อสาย",
    "enforce": ["prompt"]
  },
  {
    "id": "system_counts_days",
    "enforce": ["prompt"],
    "desc": "ห้ามนับวันเอง — ลูกค้าบอกวันแล้วต้องเรียก check_extend แล้วอ่าน in_range"
  }
]
```

`enforce` must be a list, and its one value is `prompt`, which renders `desc` into
the "หลักการ (⛔ กฎสูงสุด)" section. A constraint without `prompt` is not placed in
the prompt at all.

`backend` and `reward` are no longer supported, and `validate_strict` rejects them.

Runtime enforcement should go through a tool's `gating`.

`desc` is the part that actually reaches the model, so `constraints` should carry only the rules the work requires, added one at a time with measurement — this avoids a large set of rules that the model generalises into something broader than intended.

---

# The smallest complete document that runs

The following example is a coffee-shop company calling a customer to say an order is ready to collect. It consists of 3 states, 2 tools, 5 sentences and 1 FAQ route.

This file can be saved as `.json` and uploaded through the Demo App's Upload screen.

| Outer key | Description |
| --------- | ----------- |
| `display_name` · `agent_name` | The company and agent names shown in the UI |
| `crm` | Customer data for the test call, with dates given as `<field>_offset_days` |
| `spec` | The company's specification, following the seven steps, with `company` inside it |
| `catalog` | Every sentence; `text_id` may be omitted and the system will assign one |

This example file is designed to pass:

* `validate_strict`
* `validate_flow_spec`
* Creating a company through the real upload path
* Assembling the prompt

There is also a test (`tests/test_manual_example_runs.py`) that takes this example, creates a company for real, and deletes it when the test finishes. If the implementation changes such that the example can no longer be uploaded, the test fails and reveals the inconsistency between the document and the system.

<!-- MINIMAL-EXAMPLE -->

```json
{
  "display_name": "ร้านกาแฟตัวอย่าง",
  "agent_name": "น้องกาแฟ",
  "crm": {
    "customer_name": "สมชาย ใจดี",
    "order_no": "A-1042",
    "ready_date_offset_days": 1
  },
  "spec": {
    "company": "CAFE",
    "agent_role": "พนักงานร้านกาแฟตัวอย่าง",
    "goal": "แจ้งว่าของถึงแล้ว → ถามว่าจะมารับไหม → บันทึกผล",
    "crm_fields": ["today", "customer_name", "order_no", "ready_date"],
    "crm_labels": {
      "order_no": "เลขที่ออเดอร์",
      "ready_date": "วันที่ของถึง"
    },
    "events": {
      "confirms_pickup": {
        "desc": "รับทราบ จะมารับ",
        "cues": ["ได้ครับ", "จะไปรับ", "รับทราบ"]
      },
      "asks_hours": {
        "desc": "ถามเวลาเปิด-ปิดร้าน",
        "cues": ["เปิดกี่โมง", "ปิดกี่โมง"]
      },
      "cannot_come": {
        "desc": "มารับไม่ได้",
        "cues": ["ไม่ว่าง", "ให้โทรมาใหม่"]
      },
      "no_input": {
        "desc": "เงียบ ไม่ตอบ",
        "cues": ["…", "(เงียบ)"]
      }
    },
    "tools": {
      "declarations": [
        {
          "name": "check_order",
          "impl": "http",
          "method": "POST",
          "url": "https://api.yourcompany.co.th/aax/check_order",
          "headers": {
            "Authorization": "Bearer YOUR_TOKEN"
          },
          "desc": "ถามระบบว่าออเดอร์นี้พร้อมรับวันไหน — ห้ามตอบจากความจำ",
          "args": {},
          "returns": {
            "ready_date": {
              "type": "string",
              "format": "YYYY-MM-DD (Weekday)"
            }
          }
        },
        {
          "name": "record_call_result",
          "impl": "http",
          "method": "POST",
          "url": "https://api.yourcompany.co.th/aax/record_call_result",
          "headers": {
            "Authorization": "Bearer YOUR_TOKEN"
          },
          "desc": "บันทึกผลสาย",
          "args": {
            "result": {
              "type": "string",
              "enum": ["confirmed", "handoff"]
            }
          },
          "returns": {
            "saved": {
              "type": "boolean"
            }
          },
          "gating": {
            "max_successful_calls": 1,
            "required_at": "end_of_call"
          }
        }
      ]
    },
    "states": [
      {
        "id": "greet",
        "phase": "opening",
        "initial": true,
        "max_visits": 2,
        "templates": [
          {
            "fine_state": "greet_pickup"
          }
        ],
        "entry_tools": ["check_order"],
        "on": [
          {
            "event": "confirms_pickup",
            "to": "close_confirmed"
          },
          {
            "event": "cannot_come",
            "to": "close_handoff"
          },
          {
            "event": "no_input",
            "to": "close_handoff"
          }
        ]
      },
      {
        "id": "close_confirmed",
        "phase": "close",
        "terminal": true,
        "templates": [
          {
            "fine_state": "close_thanks"
          }
        ],
        "entry_tools": ["record_call_result"],
        "outcome": {
          "args": {
            "result": "confirmed"
          },
          "reasons": ["confirmed"],
          "desc": "รับปากจะมารับ"
        }
      },
      {
        "id": "close_handoff",
        "phase": "close",
        "terminal": true,
        "templates": [
          {
            "fine_state": "close_handoff"
          }
        ],
        "entry_tools": ["record_call_result"],
        "outcome": {
          "args": {
            "result": "handoff"
          },
          "reasons": ["handoff"],
          "desc": "ให้พนักงานติดต่อกลับ"
        }
      }
    ],
    "faq_routing": {
      "routes": [
        {
          "intent": "hours",
          "desc": "ถามเวลาเปิด-ปิดร้าน",
          "templates": [
            {
              "fine_state": "faq_hours"
            }
          ],
          "then": "resume"
        }
      ]
    },
    "fallback_fine_state": "faq_repeat",
    "constraints": [
      {
        "id": "one_template_per_turn",
        "type": "max_templates_per_reply",
        "max": 1,
        "desc": "พูดหนึ่งจังหวะต่อเทิร์น",
        "enforce": ["prompt"]
      },
      {
        "id": "greeting_once",
        "type": "once_per_call",
        "template_fine_states": ["greet_pickup"],
        "desc": "ทักทายแจ้งออเดอร์ครั้งเดียวต่อสาย",
        "enforce": ["prompt"]
      },
      {
        "id": "system_owns_the_date",
        "enforce": ["prompt"],
        "desc": "ห้ามบอกวันที่ของถึงจากความจำ — ต้องอ่านจาก ready_date ที่ check_order คืนมา"
      }
    ]
  },
  "catalog": [
    {
      "text_id": 9001,
      "_fine_state": "greet_pickup",
      "template": "สวัสดี{suffix} ร้านกาแฟโทรแจ้งคุณ [customer_name] ว่าออเดอร์ [order_no] พร้อมรับได้ตั้งแต่ [ready_date] ไม่ทราบว่าสะดวกมารับไหม{q_suffix}"
    },
    {
      "text_id": 9002,
      "_fine_state": "faq_hours",
      "template": "ร้านเปิดทุกวัน 08:00 ถึง 20:00 {suffix}"
    },
    {
      "text_id": 9003,
      "_fine_state": "faq_repeat",
      "template": "ขออภัย{suffix} ขออนุญาตแจ้งอีกครั้งนะ{suffix}"
    },
    {
      "text_id": 9004,
      "_fine_state": "close_thanks",
      "template": "ขอบคุณ{suffix} ทางร้านรอรับคุณ [customer_name] ในวัน [ready_date] {suffix}"
    },
    {
      "text_id": 9005,
      "_fine_state": "close_handoff",
      "template": "รับทราบ{suffix} เดี๋ยวพนักงานติดต่อกลับอีกครั้ง{suffix}"
    }
  ]
}
```

---

# 2. Using the Demo App

Once the specification file is written, everything else — bringing the specification into the system, holding a test conversation, inspecting the prompt, analysing the results, and deleting the company — can be done through the Demo App, with no access to files on the server.

## Uploading a specification

```text
open the Demo App → Upload

① Download template
   download an empty JSON with every key in the shape the system accepts

② Fill it in
   fill in the specification, or start from the example in §1

③ Upload
   bring the JSON file into the system
```

The system validates the specification against its defined validation and lists the errors until the company can be created successfully.

Once validation passes, the system writes the specification, registers the company, and creates a test call, with no restart required.

The company can then be selected to start a conversation, and can be deleted through the same UI.

Deleting through:

```text
DELETE /api/flow/company/<CODE>
```

removes that company's specification file and its test call.

Companies that ship with the system can be deleted in the same way, because each tenant has its own specification file and no business logic is shared.

---

## Where the CRM for a test call comes from

The `crm` block in the uploaded file is the data source for a test call.

The test call the app creates (`_demo_persona`) carries no CRM of its own, because customer data belongs to the tenant.

There are two sources of CRM data for a test call:

| Source | When to use it |
| ------ | -------------- |
| `crm` in the uploaded file | No CRM connected yet, and you want to test through the web app |
| `session_init` | The tenant's real API is connected |

With neither source, sentences referring to a field cannot be filled, and `[customer_name]` may appear in the text that goes out.

The system checks for this at upload time and reports it through `unfillable_placeholders`.

If `session_init` is present but the API does not respond, or returns `{}`, the system follows `on_failure` as the specification defines, and will not open the call using stale data that may still be in memory.

---

# The screen layout

The Demo App divides its work into three parts:

```text
pick a company → pick a mode → Playground

the companies in the system
+
create / delete buttons

modes:
· Playground
· Read instruction
```

### Playground

Used to hold a test conversation with the agent, in the same manner as a real call.

### Read instruction

Shows the prompt the agent receives. The prompt is assembled by the same mechanism the session itself uses, and is rendered from the current specification.

It is therefore the right place to check whether the flow, the rules and the catalogue are being applied as intended.

---

# How to create a company

There are two main ways:

| | **＋ New company** | **New / Upload** |
| - | ----------------- | ---------------- |
| How you fill it in | A form, one field per beat | A whole JSON file |
| Flow | Uses the base AEON remind flow | You define the whole flow |
| Adding beats | Yes, bound to the phase you choose | Yes |
| tools / events / gating | As in the base flow | You define them |
| Suited to | Adjusting wording on the existing flow | A different kind of flow, such as appointments, return reminders, or notifications |

If the tenant's work is not debt collection, use **Upload**, because creating through the form uses the base debt-collection flow structure.

---

# Settings before starting a call

| Item | Description |
| ---- | ----------- |
| **Model** | `Qwen` or `Gemini` |
| **version** | The vLLM checkpoint, shown when Qwen is selected; the list refreshes every 8 seconds |
| **Voice** | `Female`, `Male` or `Text` |
| **Start** | Starts the call; the settings are locked once it begins |

`Text` synthesises no audio, which suits testing where response time should be kept low.

Once a call starts, the Model, version and Voice options are hidden, and can only be changed after resetting the call.

---

# Controls during a conversation

| Control | Description |
| ------- | ----------- |
| Mic | The mic button or `Space` to unmute/mute |
| `Esc` | Skip the audio being spoken, or interrupt |
| `P` | pause / resume |
| `R` | Reset the call, with a confirmation screen |
| `K` | Show/hide the customer panel |
| `?` | Show the list of keyboard shortcuts |
| Latency | Shows `VAD`, `STT`, `LLM`, `TTS` per turn, with more detail when expanded |
| Save | Saves the conversation for use as a replay/eval case |

If the microphone is blocked, the system shows how to grant permission for that browser, and offers a **Type instead** button for entering text.

STT uses Chirp over a WebSocket where the browser supports it; where it does not, or cannot connect, the system falls back to the browser's Web Speech API.

`Save` writes to:

```text
data/demo-saved-trajectory/<dd-mm-yy>/
```

A note can be added, such as `"agent ข้าม KYC"`, for later evaluation or replay.

---

# Reading a turn

Every event within a turn is shown as a bubble, in the order it actually happened.

| Bubble | Description | What to check |
| ------ | ----------- | ------------- |
| `user` | The message sent into the system, with a mic icon when it came from speech | Whether ASR transcribed it correctly |
| `reply` | The sentence the agent spoke, with its `text_id` | The wording chosen, and the placeholders |
| `tool_call` | The tool's name and arguments | Which tool was called and what values were sent |
| `tool_result` | The tool's result, with the JSON when expanded | The response, and what was merged into `customer_data` |
| `warning` | A notice raised by a gate | `missing_tools`, `missing_beats`, `empty_slots` and other problems |

`warning` and `tool_result` are the important ones for diagnosis.

When a gate refuses a request, the refused message is not sent to the caller; the model retries within the same turn.

Read both `warning` and `tool_result` to establish how many refusals occurred and what caused them.

---

# CRM Snapshot

The customer panel on the left shows the data named in `crm_fields`, with the labels from `crm_labels`.

A field that is empty or shown as a dash means the model has no data for that field either.

Before a call starts, the panel can be opened to change the company's persona; only the selected company's personas are listed.

---

# Diagnosis table

| Symptom | Meaning | Where to fix it |
| ------- | ------- | --------------- |
| `[customer_name]` appears in a sentence | The field is not present in `customer_data` at all | `crm` or `session_init` |
| `ยังไม่มีค่าให้พูด: …` | The field exists but holds no value | Check whether a tool recorded the value the caller gave |
| `missing_required_tools` | The beat belongs to a state whose `entry_tools` have not all run | The `entry_tools` of the state that owns the beat |
| `incomplete_chain` | A chain state did not speak every template | Check `templates` and `when_event` |
| `unknown_text_id` | The model cited a `text_id` absent from the catalogue | Check any rule or hint citing numbers directly |
| `value_not_offered` | An argument does not match a value the system offered | Check `one_of_from` and `gating` |
| `verify_required` | A beat requiring verification was spoken before it | Add a tool declaring `provides: "verified"` |
| Incorrect dates | The model calculated the date itself | Have a tool return the date |
| The same sentence repeated | No rule prevents repetition | Add `once_per_call` |
| Slow or silent responses | The system may have looped through refusals up to `FLOW_MAX_TOOL_LOOPS = 8` | Check `warning` and the cause of the refusals |

The codes that appear in the `warning` and `tool_result` bubbles are what to work from, and the table above covers the common ones.

The complete list of codes — those raised before a tool is called and those raised while calling it — is read from the source with the command below. It belongs to whoever operates the platform, not to the tenant:

```bash
PYTHONPATH=. python3 tools/list_reject_codes.py
```

---

# The system's checkpoints

There are four points at which to check the system:

| Stage | What the system checks |
| ----- | ---------------------- |
| ① Upload | Keys, beats, events, states and the `to` targets they refer to must be valid |
| ② After upload | `unfillable_placeholders` lists the fields with no data source |
| ③ Read instruction | Inspect the prompt the model actually receives |
| ④ During the conversation | Inspect refusals, warnings, and the endpoint's results |

The presence of `unfillable_placeholders` does not always make company creation fail, but it should be resolved before holding a real conversation, to keep a placeholder from reaching the caller.

---

# Testing the flow

Test at least four paths:

```text
① The straightforward path
   the caller agrees → the call closes → the result is recorded

② A reschedule or negotiation
   the caller does not agree immediately and enters an alternative path

③ A refusal
   the caller refuses and the flow can end without looping

④ A question mid-call
   the question is answered → the call returns to the original state via faq_routing
```

For every path, check three things:

1. Whether the agent spoke at the right beat
2. Whether a placeholder appeared in the text
3. Whether the recorded result matches what happened in the conversation

---

# Checking the endpoint before testing through the web app

Because every tool is a tenant endpoint, test the endpoint directly before taking the specification into the Demo App, so that a problem in the specification can be told apart from a problem in the endpoint.

Example:

```bash
curl -sS -X POST https://api.yourcompany.co.th/aax/record_call_result \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{"tool":"record_call_result",
       "args":{"result":"confirmed"},
       "ref":{"msisdn":"081-234-5678"}}'
```

Check at least three things:

1. Whether the response is a JSON object
2. Whether the fields returned match `returns`
3. Whether the response takes less than `timeout`

When testing in the web app, expand `tool_result` and compare it with the result from `curl`.

If they differ, check `headers`, `body` and `url` in the specification before concluding that the flow is at fault.

Where an endpoint answers `200` with plain text such as `OK`, the system treats the request as successful even though there is no data for the next sentence to refer to — so check the response directly with `curl` as well.

---

# The limits of checking through a browser

`/api/health` can answer `ok` even when the model behind it is not available, so checking the health endpoint alone is not sufficient.

Hold at least one turn of real conversation.

If the system does not respond, or a stream error occurs during the conversation, that belongs to the part of the system serving the model, not to the validation of the tenant's specification.
