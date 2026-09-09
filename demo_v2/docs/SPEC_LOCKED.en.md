# `<CODE>.company.json` — locked format

*(ฉบับภาษาไทย: [SPEC_LOCKED.md](SPEC_LOCKED.md) · the manual: [MANUAL.en.md](MANUAL.en.md))*

**Status: LOCKED** — this document defines the schema in use. A key outside the schema is **rejected** (`validate_strict` in `demo_v2/server/flow/flowspec.py`), so adding or removing a key is a change to the format, not a change to a spec.

One company = one file
Add the file = create the company
Delete the file = remove the company

The app holds no company-specific business logic. Everything the agent does comes from this file.

The filename is the identity:

* `AEON.company.json` → `company: "AEON"`
* `flow_id` is filled in as `AEON` — the filename — with `setdefault`, **only when the file does not set it itself**
* if the file sets it, that value wins (the shipped `AEON` uses `AEON-outbound-remind`)
* a new file therefore need not include `company` or `flow_id`
* a file beginning with `_` is treated as a template and is not registered

---

## Design principle

> **The platform provides the mechanism · the tenant provides the policy**

**Mechanism** is what every tenant uses alike and what can be inferred from the structure of a spec: `text_id`, slot filling, tool calls, the rules for templates in a state, `entry_tools`, and the argument contract.

**Policy** is a rule only some companies have. Even when it can be parameterised through the spec it remains a tenant's rule, so it belongs in `constraints` or is enforced by the tenant's API.

The essentials:

* what the app can enforce identically for every tenant → make it mechanism
* what is policy specific to a tenant → make it a `constraints` entry or an API
* do not add a company-specific gate to the app merely because it could be parameterised from the spec

---

# 1 · The 19 keys

Five are required; the rest are optional.

| key | type | when it is used | if omitted |
| --- | ---- | --------------- | ---------- |
| **`events`** ✱ | `{name: {desc, cues}}` | the events `states[].on[].event` and `constraints[].event` refer to | validation fails |
| **`tools`** ✱ | `{declarations, …}` | builds the tool schema and calls the API | validation fails |
| **`states`** ✱ | `[…]` | the whole flow | validation fails |
| **`faq_routing`** ✱ | `{routes: […]}` | questions raised mid-flow | validation fails; `{"routes":[]}` is allowed |
| **`constraints`** ✱ | `[…]` | rules passed into the prompt | validation fails; `[]` is allowed |
| `catalog` | `[…]` | the sentence store, `text_id ↔ template` | the agent has no sentence to speak |
| `display_name` | `str` | the name in the UI, and `[company]` | falls back to `company` |
| `agent_role` | `str` | the first line of the prompt | `"a debt collection officer of <CODE>"` (in Thai) |
| `goal` | `str` | the purpose of the call | omitted |
| `crm_fields` | `[str]` | the whitelist of CRM data the model can see | the model sees no customer data |
| `crm_labels` | `{field: Thai label}` | the labels in the CRM Snapshot | the raw field names are used |
| `session_init` | `{url, method, headers, body, timeout, note, on_failure}` | fetches customer data when the call opens | no CRM; slots are left as `[placeholder]` |
| `fallback_fine_state` | `str` | the beat spoken when the model replies with nothing | `faq_repeat` is used |
| `company` `flow_id` `spec_version` | `str` | compatibility with older files | can be filled from the filename |
| `auxiliary_templates` | `{allowed: […]}` | legacy format | **do not use in a new file** |

### Keys that were removed

`validate_strict` rejects these:

| key | why |
| --- | --- |
| `role` | free text with no runtime mechanism behind it; the actual wording comes from `catalog.template`, so tone is controlled in the catalogue |
| `legal_note` | there is no mechanism to check that a statement about the law is correct, and no spec ever used the key; an enforceable rule belongs in `constraints` or in the API |
| `catalog_inline` | the templates always live in this file's own `catalog` — a shape that puts them in another key (or another file) makes "one company, one file" untrue |
| `outcomes` | a call's results come from `states[].outcome` and `faq_routing` alone; this block was an index of those, and being a copy it drifted |

---

# 2 · `states` — the flow

```json
{
  "id": "disclose_ask",
  "phase": "main",
  "initial": false,
  "terminal": false,
  "templates": [
    {"fine_state": "disclose_balance"},
    {"fine_state": "ask_pay_today"}
  ],
  "entry_tools": ["check_account_status"],
  "on": [
    {"event": "agrees", "to": "close_ptp", "tools": ["record_verbal_commitment"]}
  ],
  "outcome": {
    "args": {"result": "ptp"},
    "reasons": ["ptp"],
    "desc": "รับปากจ่าย"
  },
  "max_visits": 2,
  "verify_required": true,
  "note": "…",
  "spec_note": "…",
  "inferred": true,
  "counts_as": false
}
```

| key | value | meaning |
| --- | ----- | ------- |
| `id` ✱ | `str` | the state name that `on[].to` and `constraints[].to` refer to |
| `templates` ✱ | `[…]` | the state's beats; several without `when_event` must all be spoken in one turn |
| `phase` | `opening` \| `main` \| `close` | grouping in the prompt (see below) |
| `initial` | `true` | the first state; only one allowed |
| `terminal` | `true` | entering it ends the call |
| `entry_tools` | `[tool name]` | called before speaking, every time the state is entered |
| `on` | `[{event,to,tools?}]` | event → destination state, optionally calling tools |
| `outcome` | see §2.2 | the result when the call ends at this state |
| `max_visits` | `int` | the maximum number of times the state may be entered |
| `verify_required` | `true` | verification must pass before the state's beats may be spoken |
| `counts_as` | `false` | does not increment the `pay_ask` counter |
| `note` | `str` | goes into the prompt |
| `spec_note` `inferred` | `str` `bool` | metadata for the author; not in the prompt |

### `phase` must use a defined value

There are only:

```text
opening
main
close
```

Another value may pass some validation, but `render_instruction()` renders only these three, so that state disappears from the prompt with no warning.

Therefore **do not use a value the schema does not define, even where the validator accepts it.**

---

## 2.1 · `templates[]`

There are 7 keys:

| key | meaning |
| --- | ------- |
| `fine_state` | a single beat |
| `any_of` | speak one of the listed beats |
| `when_event` | use the template when this event occurs; the group becomes alternatives |
| `optional` | may be left unsaid |
| `counts_as` | does not increment the counter |
| `note` | metadata |
| `inferred` | metadata |

### The chain / choice rule

> Several templates in one state with **no `when_event` = a chain**, and all must be spoken in one turn.
> With `when_event`, they are alternatives.

These older keys are rejected:

```text
compose
group
template_mode
render_all_templates
```

Use `templates`, `any_of`, `when_event` and `optional` instead.

---

## 2.2 · `outcome`

```json
{
  "args": {"result": "ptp"},
  "reasons": ["ptp", "minimum"],
  "desc": "…"
}
```

`args` must use **the closing tool's real argument names.**

If the tool declares:

```text
save_appointment(status, new_slot)
```

then write:

```json
{"args": {"status": "rescheduled"}}
```

An argument whose value comes from the conversation, such as `new_slot`, does not belong here; let that argument's `desc` or `required_when` tell the model.

| key | meaning |
| --- | ------- |
| `args` | the arguments the closing tool will receive |
| `reasons` | reason codes used as guidance in the prompt |
| `desc` | what the result means, for the model |
| `note` `spec_note` `inferred` `reason_by_event` | metadata; not read at runtime |

The closing tool's first argument is taken as **the call result**, and is what `derive_outcomes` and the closing gate use.

### Legacy outcome

The older shape:

```json
{"result": "ptp"}
```

still loads, and is converted into:

```json
{"args": {"<first_tool_arg>": "ptp"}}
```

together with a single `reason` when one is present.

There is no separate `outcomes` block any more. A flow's results come entirely from:

* `states[].outcome`
* `faq_routing.routes[].then.outcome`

A flow with no outcome at all is therefore valid.

### Validation

At upload time these are checked:

1. the argument must be declared on the tool
2. an enum value must be within that argument's `enum`
3. the result must be in the flow's outcome catalogue
4. the check covers `states[]`, `faq_routing` and `session_init.on_failure`

---

# 3 · `catalog` — the sentence store

```json
{
  "text_id": 1018,
  "_fine_state": "disclose_balance",
  "template": "ยอดค้างชำระ [amount] บาท ครบกำหนด [due_date] ค่ะ",
  "hint": "ใช้ตอนแจ้งยอดครั้งแรกหลังยืนยันตัวตน",
  "state": "disclose_ask",
  "intent_name": "…"
}
```

| key | required | meaning |
| --- | -------- | ------- |
| `text_id` ✱ | ✅ | the id the model calls, e.g. `reply([1018])` |
| `_fine_state` ✱ | ✅ | the beat the sentence belongs to |
| `template` ✱ | ✅ | the actual text, with placeholders |
| `hint` | | tells the model when to use it |
| `state` | | groups it in the prompt |
| `intent_name` | | a label for the sentence |
| `is_closer` `is_demand` `is_acknowledgment` `expects_response` | | behavioural labels |
| `company` `note` `desc` `_hint_where` `_example_AEON` | | accepted |
| other keys beginning with `_` | | pass, but nothing reads them at runtime |

The leading `_` on `_fine_state` is deliberate:

* catalog → `_fine_state`
* states / templates → `fine_state`

Putting either in the wrong place is rejected.

---

## 3.1 · Placeholders

`fill_template()` resolves in this order:

| order | form | meaning |
| ----- | ---- | ------- |
| 1 | `{suffix}` `{q_suffix}` `{pronoun}` | the voice's gender |
| 2 | `{{if field}}…{{else}}…{{/if}}` | a conditional |
| 3 | `[field]` | a data field |
| 4 | `{field}` | a data field, as in 3 |
| 5 | — | collapses a duplicated honorific |

For example:

```text
F → สวัสดีค่ะ ไหมคะ ดิฉันขอเรียน
M → สวัสดีครับ ไหมครับ ผมขอเรียน
```

from:

```text
สวัสดี{suffix} ไหม{q_suffix} {pronoun}ขอเรียน
```

### `[field]` and `{field}`

Both forms behave identically.

Existing files use both, but a new file should **pick one style per file**, so that it reads clearly and slots are easy to search for.

To search for both forms:

```regex
[\[\{]([a-z_0-9]+)[\]\}]
```

### Conditionals

`{{if field}}` tests a boolean by its truth value:

* `False` → the false branch
* other types → the presence of the field

so a value of `0` or `""` still works according to its type.

---

## 3.2 · Where placeholders come from

| source | count | behaviour |
| ------ | ----: | --------- |
| `SYSTEM_PLACEHOLDERS` | 21 | aliases such as `[amount]` → `total_amount_due`; if the mapping misses, the literal field name is tried |
| `DYNAMIC_PLACEHOLDERS` | 9 | supplied through `dynamic_vars`; when absent, a neutral Thai phrase is used |
| `crm_fields` / `tool.returns` | per spec | literal fields from the CRM / tool data |

Dates and times coming from the model are validated when `strict_dates=True`:

* `DATE_PLACEHOLDERS`: `callback_date`, `promised_date`
* `TIME_PLACEHOLDERS`: `callback_time`

The value must be in the canonical format before rendering, or a `DateFormatError` is raised and the caller turns it into:

```json
{"sent": false, "reason": "date_format_invalid"}
```

---

## 3.3 · A new wording, or a new beat?

If the call goes the same way → add a `text_id` under the existing `_fine_state`.

If the call goes differently → create a new `_fine_state` and add it to the map.

Several wordings differing only in phrasing rarely make the model choose differently, because the temperature is 0 and the prompt is unchanged. What does make a difference:

* what the wording conveys
* `hint`
* the conditions for using it

### An important mistake

**Never write a beat's name beside the wrong `text_id`.**

This is a significant cause of the model choosing the wrong sentence, closing sentences especially. The system checks this consistency at upload time.

---

# 4 · `tools`

```json
"tools": {
  "declarations": [{
    "name": "check_account_status",
    "desc": "อ่านข้อมูลบัญชี",
    "impl": "http",
    "url": "{API_BASE}/AEON/check_account_status",
    "method": "POST",
    "args": {
      "last_4_digits": {
        "type": "string",
        "optional": true
      }
    },
    "returns": {
      "amount": {
        "type": "number",
        "desc": "ยอดค้าง"
      }
    },
    "gating": {
      "max_successful_calls": 1,
      "required_at": "end_of_call"
    },
    "mock": {
      "rules": [{
        "when": {
          "arg": "date",
          "matches": "2026-0[89]"
        },
        "body": {"in_range": true},
        "label": "in range"
      }],
      "default": {"in_range": false}
    }
  }],
  "validation": {
    "date_format": "YYYY-MM-DD (Weekday)",
    "payment_channels": ["…"]
  },
  "notes": ["…"]
}
```

## 4.1 · `declarations[]`

| key | value | meaning |
| --- | ----- | ------- |
| `name` ✱ | `str` | the tool's name |
| `desc` ✱ | `str` | the description in the schema |
| `impl` ✱ | `http` \| `generic` | `http` calls the API; `generic` answers from the declaration |
| `url` | `str` | used with `http`; a tenant must give its own URL |
| `method` | `POST` \| `GET` | default = `POST` |
| `args` | `{name: contract}` | arguments |
| `returns` | `{field: {type, desc}}` | the response schema |
| `gating` | `{…}` | the runtime gate |
| `provides` | `"verified"` | declares that this tool unlocks verification |
| `verified_when` | `{field, equals}` \| `{any_success:true}` | the condition counted as verified |
| `mock` | `{rules, default}` | the stand-in answers for a fake API during testing |

The `{API_BASE}` used in the examples is the server's env var (`AAX6_API_BASE`), not a URL a tenant should use.

---

## 4.2 · The argument contract

| key | value | runtime |
| --- | ----- | ------- |
| `type` | `string` `number` `boolean` `array` | goes into the schema |
| `optional` | `true` | absent = required |
| `enum` | `[str]` | limits the value |
| `format` | `"YYYY-MM-DD (Weekday)"` / `"HH:MM"` | malformed → `date_format_invalid` |
| `desc` | `str` | the description |
| `required_when` | `{arg, equals}` | required when another argument holds a given value |
| `one_of_from` | `{tool, field}` | must be a value that tool has returned |

`required_when` + `one_of_from` prevents the case of `optional: true` and then sending `""` to slip past validation.

---

## 4.3 · `gating`

There are 10 keys:

| key | runtime |
| --- | ------- |
| `max_successful_calls` | limits the number of successful calls |
| `max_calls_per_conversation` | limits calls per conversation, refused attempts included |
| `requires_prior` | the named tool must be called first |
| `must_precede` | this tool must come before the named tool |
| `args_must_match` | the arguments must match the `requires_prior` call |
| `required_at` | `"end_of_call"` = the closing tool |
| `after_event` | prompt only |
| `note` | prompt only |
| `required_before_state` | prompt only |
| `required_before` | prompt only |

`args_must_match` must be used together with `requires_prior`.

The closing tool (`required_at: "end_of_call"`) may be declared on **one tool per spec**.

For ordering, use:

* `requires_prior` when a tool must follow one particular tool
* `must_precede` when one tool must come before several

`constraints.type: tool_pair` is retired.

Any key outside `GATING_KEYS` is rejected.

---

## 4.4 · `mock`

`when` supports two forms:

```jsonc
"when": {
  "arg": "date",
  "matches": "^2026-09"
}
```

or:

```jsonc
"when": {
  "arg": "date",
  "within_days_of": {
    "field": "due_date",
    "days_field": "max_extend_days"
  }
}
```

The `within_days_of` form lets the generator compute real dates from the CRM, which suits a requirement such as "no more than N days".

**Do not guess dates with a regex**: a regex pinned to a month or year can accept a date outside the window.

Mockoon has no comparison helper that works as needed (`lte` / `gt`), so the response the generator computed is embedded.

The real check belongs on `one_of_from`, which compares against a freshly computed `valid_dates`.

---

## 4.5 · Dates the model speaks

An argument declaring:

```text
format: "YYYY-MM-DD"
```

accepts natural speech, such as:

```text
พรุ่งนี้
มะรืน
สิ้นเดือน
สิ้นเดือนหน้า
เสาร์หน้า
อีก 3 วัน
วันที่ 15 เดือนหน้า
20 มิถุนายน
```

The app converts it to ISO before calling the API.

* already ISO → left alone
* not convertible → passed on for format validation to judge
* the API always sees ISO

The reason is that a 9B model converts spoken dates imprecisely, month boundaries especially. Converting in the app raised accuracy from about 62% to 92% in the original testing.

---

## 4.6 · Dates must relate to today

Do not hard-code a date in a persona.

Use an offset, such as:

```json
"appointment_date_offset_days": 2,
"due_date_offset_days": -7
```

The app resolves it when the session opens.

`due_offset_days` is the legacy name and still points at `due_date`.

---

## 4.7 · The other keys in `tools`

| key | status |
| --- | ------ |
| `validation.date_format` | goes into the prompt |
| `validation.payment_channels` | goes into the prompt |
| `notes` | goes into the prompt |

---

# 5 · `constraints` — the rules

For example:

```json
{
  "id": "max_pay_asks",
  "type": "max_occurrences",
  "counts": "pay_ask",
  "max": 2,
  "on_exceed": {
    "to": "close_refused"
  },
  "enforce": ["prompt"],
  "desc": "ถามจ่ายได้ไม่เกิน 2 ครั้ง …"
}
```

`desc` is the part the model actually reads.
`type` exists to classify the rule and to help validation.

## 5.1 · `enforce`

There is one value:

| value | effect |
| ----- | ------ |
| `prompt` | `desc` goes into the **หลักการ (⛔ กฎสูงสุด)** section |

A constraint without `prompt` in `enforce` is not placed in the prompt at all.

`backend` and `reward` were both removed and `validate_strict` rejects them
(`reward` was a label for the training side with no runtime effect).

Runtime enforcement has to use `tools.gating`.

`session` was removed along with the reply gate.

Verification uses:

```text
state/route.verify_required
+
tool.provides / tool.verified_when
```

not `constraints`.

> **Every `constraints` entry is an instruction in the prompt**, not runtime enforcement.
> A rule that must not break has to be enforced by the tenant's API.

---

## 5.2 · Constraint types

There are 7 types:

| type | fields | meaning |
| ---- | ------ | ------- |
| `max_occurrences` | `counts`, `max`, `on_exceed.to` | over the count → the destination state |
| `once_per_call` | `template_fine_states` | may be spoken once per call |
| `repeat_only_on` | `event`, `template_fine_states` | may be repeated only when the event occurs |
| `forbid_after_event` | `event`, `template_fine_states`, `inverted` | forbidden after the event; `inverted:true` = forbidden before |
| `no_repeat_answered_request` | `template_fine_states` | once answered, do not answer again |
| `immediate_transition_on` | `event`, `to` | on the event → move at once |
| `max_templates_per_reply` | `max` | limits the sentences per turn |

A constraint with no `type` is a text-only rule, using just:

```text
id
desc
enforce
```

That form is the normal one, not a fallback.

### `CONSTRAINT_KEYS`

Only these:

```text
id
type
desc
enforce
event
max
counts
to
on_exceed
template_fine_states
inverted
note
spec_note
inferred
```

Use `template_fine_states`, which is always a list. There is no singular `template_fine_state` any more.

### Retired forms

| was | use instead |
| --- | ----------- |
| `tool_pair` | `gating.requires_prior` |
| `require_tool_before_end` | `gating.required_at` |
| `resume_after_interrupt` | `faq_routing.routes[].then = "resume"` |

The original rule can still be kept as a `desc` if the policy needs stating to the model.

### Principles for writing constraints

Do not add many rules at once without measuring the effect.

The rules that transfer well between companies tend to be:

* the correct sentence
* the forbidden alternative
* a reason in meaning

A "procedure", by contrast, usually should not be turned into a general rule.

---

# 6 · `faq_routing`

```json
{
  "intent": "amount",
  "desc": "ถามยอด",
  "templates": [
    {"fine_state": "faq_amount"}
  ],
  "verify_required": true,
  "then": "resume"
}
```

`verify_required` behaves as it does on a state: if the FAQ discloses customer data, verification must pass first.

`then` has two forms:

```json
"then": "resume"
```

answer, then return to the flow.

Or:

```json
"then": {
  "terminal": true,
  "outcome": {
    "args": {"result": "tcb"},
    "reasons": ["…"]
  }
}
```

answer, then end the call.

An FAQ outcome counts towards the flow's outcome catalogue just as a state outcome does.

> An FAQ beat need not belong to a state.
> Do not judge a beat unusable from the state graph; read the catalogue and the routing.

---

# 7 · CRM

## 7.1 · `session_init`

`session_init.url` is called once when the call opens and should return a dict of customer data.

A caller identifier such as `{msisdn}` may be used in:

* `url`
* `headers`
* `body`

An example for the specs that ship:

```json
"url": "{API_BASE}/AEON/init?msisdn={msisdn}"
```

An example for a tenant:

```json
"url": "https://api.yourcompany.co.th/aax/init?msisdn={msisdn}"
```

Every tool receives:

```json
"ref": {
  "case_id": "...",
  "msisdn": "...",
  "customer_phone": "...",
  "last_4_digits": "..."
}
```

automatically.

The agent therefore never has to send an identifier itself to select a record.

---

## 7.2 · CRM visibility

`crm_fields` is a **whitelist** of the fields the model sees.

`crm_labels` holds the Thai labels in:

```text
## ข้อมูลลูกค้า (CRM Snapshot)
```

Both are needed for a field to appear in the prompt:

1. the field is in `crm_fields`
2. the API returns a real value

Template slots draw on the same dict.

---

## 7.3 · `session_init.on_failure`

```json
{
  "on_failure": {
    "fine_state": "apology_close",
    "outcome": {
      "args": {
        "result": "unreachable",
        "reason": "ระบบไม่ตอบ"
      }
    }
  }
}
```

If the CRM does not answer:

1. speak `fine_state`
2. call the closing tool with the outcome, if given
3. end the call

The app **does not fall back to stale local data.**

Declaring `session_init` without `on_failure` means the session cannot open, because the app will neither invent a sentence nor use old data.

---

# 8 · `verify_required`

Verification has to be declared on **both sides.**

### Side one — the state / FAQ says verification is required

```json
{
  "id": "disclose_ask",
  "verify_required": true
}
```

### Side two — a tool says what unlocks it

Reading a flag from the API:

```json
{
  "name": "verify_identity",
  "provides": "verified",
  "verified_when": {
    "field": "verified",
    "equals": true
  }
}
```

Or a successful call counting as verification:

```json
{
  "name": "check_account_status",
  "provides": "verified",
  "verified_when": {
    "any_success": true
  }
}
```

`verified_when` has only two forms:

```text
{field, equals}
{any_success: true}
```

The `field` form reads both the top level of the payload and `data`.

### Both halves are required

With `verify_required` present but no tool declaring `provides: verified`, there is no real gate.

That is deliberate: a spec asking for verification with no tool able to unlock it would, read literally, block disclosure for the whole call.

---

# 9 · The order to write it in

Always write `states` before `catalog`, because the flow determines which beats must exist.

```text
1. events
2. states
3. tools
4. catalog
5. faq_routing
6. crm_* + session_init
7. constraints
```

The idea:

* `events` — what the caller can do
* `states` — where the flow goes
* `tools` — what the system must compute, check or record
* `catalog` — what the agent says
* `faq_routing` — interrupting questions
* `crm_*` — the data the agent uses
* `constraints` — the policy that is genuinely needed

### What should be a tool?

These belong in an API / tool, not in the prompt:

* date arithmetic
* limits / eligibility
* entitlement checks
* lookups

If a requirement says "no more than 7 days" but there is no API to check that condition, treat it as **a missing tool**, not as a line to add to `constraints`.

Dates in a mock should also relate to "today".
