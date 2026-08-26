---
name: new-company
description: "Turn a written requirement into a working outbound-agent tenant for this platform — a single `<CODE>.company.json` — and hand the requester something they can approve. Use when onboarding a new company/flow, reworking an existing tenant's flow, or reviewing a spec someone else wrote. Interviews for the gaps a requirement always leaves, authors the spec, lints it for the defects a schema validator cannot see, renders the flow as a diagram, and drives real conversations through the demo so the requester hears the agent before signing off."
---

# New company — requirement → tenant spec → proof

One tenant is one file: `data/flows/<CODE>.company.json`. Dropping it in creates the
company; deleting it removes it. The platform holds no logic about any tenant, so
**everything the agent does comes from this file**, and every defect in it ships.

The format reference is [docs/SPEC_FORMAT.md](../../../docs/SPEC_FORMAT.md); the
field-by-field authoring guide is [docs/NEW_COMPANY.md](../../../docs/NEW_COMPANY.md).
This skill is the process around them.

## Why the steps are in this order

Writing the JSON is the easy part — a requirement can be transcribed into a valid spec
in minutes. What has actually cost weeks is **a spec that validates and is still
wrong**: a rule naming one beat beside another beat's id, a terminal state that
records nothing, a sentence no state can reach, a business rule stated only in prose.
None of those fail validation and none show up in a score. So: interview for what the
requirement left out, author, **lint**, then make the requester *hear* it.

---

## 1 · Interview before writing

A requirement describes the happy path in the requester's head. Ask about the rest —
and write the answers down, because they become the assumptions list in step 6.

- **Which decisions are the system's, not the agent's?** Anything involving date
  arithmetic, limits, eligibility, or lookups belongs in a tool that returns the
  answer. Measured: the model does not do date arithmetic reliably. If the
  requirement states a rule ("no more than 7 days") with no mechanism, that is a
  missing API, not a prompt instruction.
- **What is referenced but never defined?** Requirements routinely name a fallback
  ("…then retry") that has no content.
- **Which endings are missing?** Count the ways the call can end. A flow with no path
  for "customer refuses" will strand every refusal.
- **What does the CRM already know, and what must be fetched mid-call?**
- **Company code and display name.**
- **Is there a real API yet?** If not, declare `impl: "generic"` or a spec-level
  `mock` and say so.

Ask these as questions, not as a form. If the requester cannot answer one, that is an
assumption — record it, do not bury it.

## 2 · Author `<CODE>.company.json`

Follow docs/NEW_COMPANY.md in its order. Two habits that prevent the known defects:

- **Never write a beat name next to a number that is not its id.** When a rule
  mentions a template, copy the id from the catalog in the same edit.
- **Declare what a tool returns.** `returns: {field: {type, desc}}` is what lets the
  mock be faithful, lets `one_of_from` be checked, and tells the reader what the agent
  will see. A tool without it is a black box to every other tool in the file.

Start with the **fewest rules that work**. Constraints are not free: adding six
correct-in-isolation rules in one batch has lowered the score and introduced
fabricated commitments, because three of them ended in "close the call" and the model
generalised the ending. Rules that transfer look like *correct sentence + forbidden
alternative + semantic reason*, and never end in a procedure.

## 3 · Lint

```bash
PYTHONPATH=. python3 .claude/skills/new-company/scripts/lint_spec.py data/flows/<CODE>.company.json
```

Runs `validate_strict` plus the checks that catch what it cannot see: name/id
disagreement in prose, a state note prescribing another state's line, terminal states
that record nothing, unreachable states, dangling transitions, undeclared events,
outcomes outside the declared vocabulary, `one_of_from` pointing at a field no tool
returns, conditions stated only in prose, sentences nothing can reach, and
placeholders nothing supplies.

**ERROR must be zero before going further.** Read every WARN out loud — most of them
are real. `sentence_never_used` in particular has found beats that three rounds of
prompt rules could not make the model speak, for the plain reason that no state or FAQ
route ever offered them.

## 4 · Diagram

```bash
PYTHONPATH=. python3 .claude/skills/new-company/scripts/spec_to_mermaid.py data/flows/<CODE>.company.json
```

Each node shows what the caller hears (beat + text ids), what the system does there
(entry tools), and how the call ends (outcome). Check it against the requirement
yourself first: every ending reachable, every path the requester described present.

## 5 · Make them hear it

A diagram cannot answer "is this what you wanted". Generate the mock, restart it, then
play the paths:

```bash
PYTHONPATH=. python3 tools/gen_mockoon.py        # reads every tenant file, incl. `mock`
bash run_mock.sh && bash run_demo.sh             # or the deployment's equivalents
PYTHONPATH=. python3 .claude/skills/new-company/scripts/smoke_company.py <CODE> \
    --case TC-<CODE>-BUILD-001 --scenarios scenarios.json
```

Cover, at minimum: the happy path, **each** ending, one path where a tool's answer
changes which sentence is correct, and one where the customer says something the flow
has no branch for. Watch for `⚠` warnings and rejected tool calls — a clean transcript
with warnings in it is not clean.

A tenant needs a demo persona in `data/test-cases/_builder_personas.json` (id
`TC-<CODE>-BUILD-001`) carrying every CRM field the templates speak.

## 6 · Report — assumptions first

Hand back, in this order:

1. **Assumptions** — everything decided on the requester's behalf, as a list they can
   tick or correct. A spec that validates but rests on five silent assumptions is the
   most dangerous artifact this process can produce.
2. **New API surface** — every endpoint the tenant must implement, with what it takes
   and returns, and which ones the requirement did not ask for and why.
3. **Diagram.**
4. **Transcripts** from step 5.
5. **Lint output**, including the warnings not acted on.

## What good looks like

`data/flows/SHOP.company.json` was built by this process from a written requirement:
8 sentences, 7 events, 7 states, 2 tools, 8 rules; lint clean; four scripted paths
correct on the first run, including picking a different sentence depending on whether
the API said the requested date was in range. Read it beside
`data/flows/AMT.company.json` — the same shape (check the system's options → record
the result) fits most outbound work.

## Do not

- Do not add a rule to fix behaviour that a **mechanism** should fix. If the agent
  guesses a date, give it a tool; if it must not speak before a condition, gate it.
- Do not batch rule changes. One or two, then measure.
- Do not put anything about the tenant in application code. If a check needs company
  knowledge, express it in the spec and teach the executor to read it.
- Do not report a score without repeats: identical configuration, temperature 0, has
  varied by 6 points out of 45 between runs.
