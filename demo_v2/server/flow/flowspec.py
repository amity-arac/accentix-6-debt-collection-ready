"""FlowSpec: declarative, machine-readable call-flow definitions.

One company's call flow as JSON — states, transitions, tool gating, constraints,
FAQ routing, outcomes — and the single source for three consumers that used to each
carry their own copy of the rules: the prompt renderer, this backend interpreter,
and trajectory scoring on the training side (not part of this app).

Two rules the schema enforces:

- **Template binding is id-agnostic.** A state references a catalog entry by
  ``fine_state``, never by ``text_id``, so ids can be remapped without touching it.
- **Inferred policy is marked.** Anything not explicit in the source instruction
  carries ``"inferred": true`` — the spec never silently invents policy.

Pure stdlib: load, validate, cross-check against the catalog, resolve bindings.
"""
from __future__ import annotations

import json
from pathlib import Path

# Ported from the aax6 research package into the deliverable so flow mode is
# self-contained (no aax6 dependency). Only the flows-dir default differs.
FLOWS_DIR: Path = Path(__file__).resolve().parents[3] / "data" / "flows"

# Backend behaviors a tool declaration can bind to via `impl` (spec_version 2).
# The builtin names mirror CaseBackend.dispatch(); "generic" is the declarative
# executor in spec_backend.py. There are exactly two, because there are exactly
# two things a tool can do here: call an API, or return a canned answer. A tool's
# `name` is free — it is whatever the company calls the operation, and nothing in
# this app may branch on it. Listing business tool names here was stale as well as
# coupled: SpecBackend has no executor for them, so a spec using one validated and
# then failed at dispatch with `impl_not_supported`.
KNOWN_IMPLS = frozenset({
    "http",     # POST the declared url with the call's args (SpecBackend._dispatch_http)
    "generic",  # canned response declared in the tool itself, for a spec drafted
                # before its API exists (SpecBackend._dispatch_generic)
})

# No outcome vocabulary lives here. A flow's result codes are whatever its own
# `outcomes.results` declares — ptp/refused for a collection call, confirmed/rescheduled
# for an appointment, completed/declined for a survey. Carrying a default meant every
# new flow silently inherited a debt collector's vocabulary and validated codes it
# could never emit.

CONSTRAINT_TYPES = frozenset({
    "max_occurrences",
    "once_per_call",
    "repeat_only_on",
    "forbid_after_event",
    "no_repeat_answered_request",
    "immediate_transition_on",
    "max_templates_per_reply",
})
# `tool_pair` was retired: it declared "second requires first", which is the same edge
# `gating.requires_prior` declares on the tool that needs it. Every spec carried that one
# ordering three times (tool_pair + requires_prior + the mirror must_precede) and only the
# first check could ever fire — see RETIRED_CONSTRAINT_TYPES for what an author is told.

# `company` and `flow_id` are NOT required in the file: `load_tenant_spec` fills them
# from the filename, which is the one place they cannot disagree with. `spec_version` is
# implied by the shape the loader accepts.
_REQUIRED_TOP_KEYS = (
    "events", "tools", "states", "faq_routing", "constraints",
)


# --------------------------------------------------------------------------- #
# The locked shape. Anything outside these sets is rejected, not ignored — without
# that, a typo (`fine_states`, `entry_tool`) validates clean and then does nothing
# at runtime. A format is only locked if something refuses what is not in it.
# --------------------------------------------------------------------------- #
TOP_KEYS = frozenset({
    # identity + presentation
    "display_name",
    # who the agent is, in the model's words. `role` (a tone note) and `legal_note`
    # (a statute name) were removed: the model speaks catalog templates verbatim, so a
    # tone request has nothing to act on, and a legal duty only binds when it is
    # written as a `constraints` rule or refused by the tenant's API.
    "agent_role", "goal",
    # what the agent knows about the customer
    "crm_fields", "crm_labels", "session_init",   # session_init: {url, method, headers,
                                                  #   body, timeout, note, on_failure}
    # what it can say, and when
    "catalog", "events", "states", "faq_routing", "constraints",
    "auxiliary_templates", "fallback_fine_state",
    # what it can do
    "tools",
    # accepted from the older shape so an existing file still loads
    "spec_version", "company", "flow_id",
})
STATE_KEYS = frozenset({
    "id", "phase", "initial", "terminal", "templates", "on", "entry_tools",
    "outcome", "note", "spec_note", "counts_as", "max_visits", "inferred",
    # This state's beats cannot be spoken until verification passes — a tool that
    # declares provides:"verified" has to exist alongside it, or the app never opens
    # the gate at all (SPEC_LOCKED §8.1)
    "verify_required",
})
TEMPLATE_KEYS = frozenset({"fine_state", "any_of", "when_event", "optional",
                           "note", "inferred",
                           # a template may opt out of its state's pay-ask count
                           # (`counts_as: false`); aax6.core.flowspec reads it
                           "counts_as"})
# `inferred: true` marks policy this spec added that its source instruction did not
# state — a design rule of this schema (see the module docstring), so it is valid
# anywhere. `spec_note` is the same idea in prose.
TRANSITION_KEYS = frozenset({"event", "to", "tools", "note", "inferred", "spec_note"})
# `constraints[]` had no key lock either: a rule could name `template_fine_stat` and
# validate clean while matching nothing. These are every key the shipped specs use plus
# the ones spec_rules reads.
CONSTRAINT_KEYS = frozenset({
    "id", "type", "desc", "enforce",              # every rule
    "event", "max", "counts", "to", "on_exceed",  # typed rules
    "template_fine_states", "inverted",
    "note", "spec_note", "inferred",
})
# `gating` had no key lock, so a misspelling (`max_sucessful_calls`) validated clean and
# then enforced nothing — the exact failure TOP_KEYS/STATE_KEYS exist to prevent.
# The tool that declares itself the verification unlock. No tool name and no field
# name is hardcoded anywhere; the app reads these two keys (SPEC_LOCKED §8.1)
VERIFIED_WHEN_KEYS = frozenset({"field", "equals", "any_success"})
GATING_KEYS = frozenset({
    # enforced by SpecGate at call time
    "max_successful_calls", "max_calls_per_conversation",
    "requires_prior", "must_precede", "args_must_match", "required_at",
    # rendered into the instruction only — a live caller gives no reliable event tag
    "after_event", "required_before_state", "required_before", "note",
})
CATALOG_KEYS = frozenset({
    "text_id", "_fine_state", "template",                 # the three that matter
    "hint",                                               # when to use this wording
    "company", "state", "intent_name",                    # derived; accepted if present
    "_hint_where", "_example_AEON", "is_closer", "is_demand",
    "is_acknowledgment", "expects_response", "note", "desc",
})
# Retired keys, named so the error can say what replaced them.
RETIRED = {
    "compose": "หลาย template ใน state เดียว (ไม่มี when_event) = chain อยู่แล้ว",
    "render_all_templates": "เหมือน compose",
    "group": "ใช้ when_event แยกทางเลือก / ไม่ใส่ = chain",
    "template_mode": "อนุมานจาก when_event",
}
# Three types said, in a second place, a thing the states / tools / faq_routing section
# already say. A second copy is not free — it can disagree with the first, and one did:
# `max_templates_per_reply.exceptions` was a hand-kept list of the chain states that had
# drifted from the states themselves. The rules they carried are unchanged; they live on
# as prose (`id` + `desc` + `enforce`), which is all that ever reached the model.
RETIRED_CONSTRAINT_TYPES = {
    "tool_pair": "ประกาศลำดับที่ `gating.requires_prior` ของ tool ที่ต้องพึ่งอีกตัว",
    "require_tool_before_end": "ประกาศที่ `gating.required_at: end_of_call` ของ tool นั้น",
    "resume_after_interrupt": 'ประกาศที่ `faq_routing.routes[].then: "resume"` ทีละเส้น',
}


def _check_keys(where: str, obj: dict, allowed: frozenset, errors: list,
                allow_underscore: bool = False) -> None:
    for k in obj:
        # `_`-prefixed keys are annotations about where an entry came from
        # (_synthetic, _real_count, _flow_id). They are read by nothing at runtime,
        # so they are allowed to exist without being enumerated here.
        if allow_underscore and k.startswith("_") and k not in RETIRED:
            continue
        if k in RETIRED:
            errors.append(f"{where}: เลิกใช้ key '{k}' แล้ว — {RETIRED[k]}")
        elif k not in allowed:
            errors.append(f"{where}: ไม่รู้จัก key '{k}' (ที่ใช้ได้: {', '.join(sorted(allowed))})")


def validate_strict(spec: dict, catalog: list[dict] | None = None) -> list[str]:
    """Key-level lock, on top of `validate_flow_spec`'s structural checks."""
    errors: list[str] = []
    _check_keys("spec", spec, TOP_KEYS, errors)
    for st in spec.get("states") or []:
        sid = st.get("id", "?")
        _check_keys(f"state {sid}", st, STATE_KEYS, errors)
        for i, t in enumerate(st.get("templates") or []):
            _check_keys(f"state {sid} template[{i}]", t, TEMPLATE_KEYS, errors)
            if not t.get("fine_state") and not t.get("any_of"):
                errors.append(f"state {sid} template[{i}]: ต้องมี fine_state หรือ any_of")
        for i, tr in enumerate(st.get("on") or []):
            _check_keys(f"state {sid} on[{i}]", tr, TRANSITION_KEYS, errors)
    for st in spec.get("states") or []:
        if isinstance(st.get("outcome"), dict):
            _check_keys(f"state {st.get('id','?')} outcome", st["outcome"],
                        OUTCOME_KEYS, errors)
    si = spec.get("session_init") or {}
    of = si.get("on_failure") or {}
    if of:
        beats = {e.get("_fine_state") for e in (catalog or [])}
        if catalog is not None and of.get("fine_state") not in beats:
            errors.append(f"session_init.on_failure: ไม่มี beat '{of.get('fine_state')}' ในคลัง")
        res = outcome_result(spec, of.get("outcome"))
        if res and res not in set(derive_outcomes(spec)):
            errors.append(f"session_init.on_failure: result '{res}' ไม่อยู่ในผลลัพธ์ที่ flow นี้ประกาศ")
        # On this path **the app calls the tool itself** with args from the spec, so an
        # argument that does not exist or a value outside its enum gets rejected by
        # gate 2 at the moment nobody is watching (the CRM is down) — which is why it
        # has to be checked at upload time
        errors.extend(f"session_init.on_failure: {m}"
                      for m in _check_outcome_args(spec, of.get("outcome")))
    for c in spec.get("constraints") or []:
        _check_keys(f"constraint {c.get('id', c.get('type', '?'))}", c,
                    CONSTRAINT_KEYS, errors)
    for d in (spec.get("tools") or {}).get("declarations") or []:
        _check_keys(f"tool {d.get('name','?')} gating", d.get("gating") or {},
                    GATING_KEYS, errors)
        # The verification unlock: declaring half of it is a trap. `provides` without
        # `verified_when` leaves the app guessing how to read the API's answer, which
        # is exactly what this schema exists to remove, and `verified_when` without
        # `provides` is read by nobody.
        if d.get("provides") and d["provides"] != "verified":
            errors.append(f"tool {d.get('name','?')}: provides รับได้ค่าเดียวคือ \"verified\"")
        if d.get("provides") and not d.get("verified_when"):
            errors.append(f"tool {d.get('name','?')}: ประกาศ provides แล้วต้องมี verified_when "
                          "({field, equals} หรือ {any_success: true})")
        if d.get("verified_when") and not d.get("provides"):
            errors.append(f"tool {d.get('name','?')}: มี verified_when แต่ไม่ได้ประกาศ provides")
        vw = d.get("verified_when") or {}
        if vw:
            _check_keys(f"tool {d.get('name','?')} verified_when", vw, VERIFIED_WHEN_KEYS, errors)
            if not vw.get("any_success") and not vw.get("field"):
                errors.append(f"tool {d.get('name','?')} verified_when: ต้องมี field+equals "
                              "หรือ any_success")
    # events: {name: {desc, cues}} — a bare string still loads (the renderer accepts
    # it) but is reported on write, because `cues` is what tells the model which words
    # count as that event
    for name, ev in (spec.get("events") or {}).items():
        if isinstance(ev, str):
            errors.append(f"events[{name}]: ใช้รูป {{\"desc\": …, \"cues\": [...]}} "
                          "ไม่ใช่ string เปล่า")
        elif isinstance(ev, dict) and not ev.get("desc"):
            errors.append(f"events[{name}]: ต้องมี desc")
    for i, e in enumerate(catalog or []):
        _check_keys(f"catalog[{i}]", e, CATALOG_KEYS, errors, allow_underscore=True)
        # A catalog entry keys its beat `_fine_state`; the bare name belongs to a
        # `states[].templates[]` entry. Accepting both here let an entry validate and
        # then vanish — `normalize_catalog` only ever fills `_fine_state`.
        if "fine_state" in e:
            errors.append(f"catalog[{i}]: ใช้ `_fine_state` ไม่ใช่ `fine_state` "
                          "(ชื่อไม่มี _ ใช้ใน states[].templates)")
        if not e.get("_fine_state"):
            errors.append(f"catalog[{i}]: ต้องมี _fine_state")
        if not e.get("template"):
            errors.append(f"catalog[{i}]: ต้องมี template")
    return errors


def load_flow_spec(path: str | Path) -> dict:
    """Load a FlowSpec JSON. Accepts an absolute/relative path or a bare
    flow_id (resolved under ``data/flows/``)."""
    p = Path(path)
    if not p.suffix:
        p = FLOWS_DIR / f"{p.name}.json"
    with open(p, encoding="utf-8") as f:
        return json.load(f)


OUTCOME_KEYS = frozenset({
    # `args` = the arguments the closing tool has to receive (that tool's own real
    # argument names). `result`/`reason` = the older shape; it still loads and is
    # normalized into `args`.
    "args", "result", "reason", "reasons", "desc",
    # Author's notes — read by no code (like note/spec_note/inferred at state level).
    # AEON's `reason_by_event` writes down which event should get which reason, but
    # **nothing reads it**: the reason actually sent comes from the model, so it is a
    # record of intent, not a rule.
    "note", "spec_note", "inferred", "reason_by_event",
})


def closing_tool(spec: dict) -> dict | None:
    """The closing tool's declaration — the one that declares
    `gating.required_at: "end_of_call"`."""
    for d in (spec.get("tools") or {}).get("declarations", []):
        if (d.get("gating") or {}).get("required_at") == "end_of_call":
            return d
    return None


def outcome_key(spec: dict) -> str:
    """The name of the argument that carries the call result — the first argument the
    closing tool declares.

    The whole system used to hardcode this as `result`, which is the argument name of
    one debt-collection tool: AMT closes with `save_appointment(status, new_slot)`, so
    the prompt taught it to send a value to an argument named `reason` that the tool
    does not have. Reading it from the declaration instead makes the name always
    match.
    """
    d = closing_tool(spec) or {}
    return next(iter((d.get("args") or {})), "result")


def outcome_args(spec: dict, outcome: dict | None) -> dict:
    """`outcome` → the arguments to hand the closing tool (the older shape is
    converted here).

    The current shape declares `args` directly, using that tool's own argument names.
    The older shape (a bare `result` plus `reason`) becomes
    `{<first arg>: result, "reason": reason}`, so a file written earlier keeps
    behaving exactly as it did.
    """
    o = outcome or {}
    if isinstance(o.get("args"), dict):
        return dict(o["args"])
    args: dict = {}
    if o.get("result") is not None:
        args[outcome_key(spec)] = o["result"]
    if o.get("reason") is not None:
        args["reason"] = o["reason"]
    return args


def outcome_result(spec: dict, outcome: dict | None) -> str | None:
    """This outcome's call result — the value of the argument that carries it."""
    v = outcome_args(spec, outcome).get(outcome_key(spec))
    return None if v is None else str(v)


def _check_outcome_args(spec: dict, outcome: dict | None) -> list[str]:
    """A declared `outcome` has to be a call the closing tool can actually accept.

    Two things nothing checked before, each failing differently:
      · an argument absent from the declaration — that value can never be sent (the
        prompt teaches something the tool does not have)
      · a value outside that argument's `enum` — gate 2 rejects it at closing time
        with `<arg>_invalid`, which means the call cannot end at all, and nothing
        warned at upload
    """
    d = closing_tool(spec)
    if not d or not outcome:
        return []
    declared = d.get("args") or {}
    errs: list[str] = []
    for name, val in outcome_args(spec, outcome).items():
        meta = declared.get(name)
        if meta is None:
            errs.append(f"outcome args: `{name}` ไม่ใช่ argument ของ {d.get('name')} "
                        f"(มี: {', '.join(declared) or '—'})")
            continue
        enum = (meta or {}).get("enum")
        if enum and val not in enum:
            errs.append(f"outcome args: {name}={val!r} ไม่อยู่ใน enum ของ "
                        f"{d.get('name')} ({', '.join(map(str, enum))})")
    return errs


def derive_outcomes(spec: dict) -> dict:
    """The call results this flow can produce — assembled from the states.

    A state that ends the call already says what it records, and so does a terminal FAQ
    route. A top-level `outcomes` block was an index of that and, being a copy, drifted
    (AEON listed `refused` with no reasons while its state named three; both AEON and
    KBANK listed results no state could reach). It also forced every flow to HAVE
    results — a survey had to invent them to validate. `desc` rides on the state's own
    `outcome` because it cannot be derived from anything.
    """
    out: dict[str, dict] = {}

    def add(o: dict) -> None:
        res = outcome_result(spec, o)
        if not o or not res:
            return
        e = out.setdefault(res, {"reasons": [], "desc": ""})
        for r in o.get("reasons") or []:
            if r not in e["reasons"]:
                e["reasons"].append(r)
        if o.get("desc") and not e["desc"]:
            e["desc"] = o["desc"]

    for st in spec.get("states", []):
        add(st.get("outcome") or {})
    for route in ((spec.get("faq_routing") or {}).get("routes") or []):
        then = route.get("then")
        if isinstance(then, dict):
            add(then.get("outcome") or {})
    return out


def load_tenant_spec(path: "Path | str") -> dict:
    """Read one tenant file and fill in the identity the filename already carries.

        `<CODE>.company.json`  -> company = CODE,    flow_id = CODE
        `<FLOW_ID>.json`       -> flow_id = FLOW_ID, company = the part before the `-`

    A spec used to repeat its own `company`/`flow_id`, which can disagree with the file
    it lives in — and did. Deriving them here lets the file say each fact once.
    """
    p = Path(path)
    spec = json.loads(p.read_text(encoding="utf-8"))
    stem = p.name[: -len(".company.json")] if p.name.endswith(".company.json") else p.stem
    if p.name.endswith(".company.json"):
        spec.setdefault("company", stem)
        spec.setdefault("flow_id", stem)
    else:
        spec.setdefault("flow_id", stem)
        spec.setdefault("company", stem.split("-")[0])
    spec.setdefault("spec_version", 2)
    return spec


def resolve_catalog(spec: dict, flows_dir: Path | None = None) -> list[dict]:
    """The catalog of a spec — `catalog` IS the list of templates.

    Three shapes used to be accepted: a list; `catalog: "__inline__"` paired with
    `catalog_inline`; and `catalog` naming a *file* to be found under
    `data/pre-scripts/`. The last two are inherited from when a spec and its template
    store were two files — being separate, they drifted apart. No file under
    `data/flows/` uses them any more (measured: all of them hold a list), and
    accepting them made "one company, one file" not quite true. One shape is left.
    """
    cat = spec.get("catalog")
    if isinstance(cat, list):
        return cat
    if isinstance(cat, str) or spec.get("catalog_inline") is not None:
        raise ValueError(
            "`catalog` ต้องเป็น list ของ template ในไฟล์เดียวกับสเปค — "
            "รูป `catalog_inline` และ `catalog` ที่เป็นชื่อไฟล์ไม่รองรับแล้ว")
    raise ValueError("spec ไม่มี `catalog`")


def _template_refs(spec: dict):
    """Yield (where, fine_state) for every template binding in the spec."""
    for st in spec.get("states", []):
        for t in st.get("templates", []):
            # an `any_of` step binds several beats; yielding None for it made the
            # validator report a phantom unresolved binding
            for fs in ([t["fine_state"]] if t.get("fine_state") else (t.get("any_of") or [])):
                yield f"state:{st.get('id')}", fs
    for route in spec.get("faq_routing", {}).get("routes", []):
        for t in route.get("templates", []):
            yield f"faq:{route.get('intent')}", t.get("fine_state")
    for t in spec.get("auxiliary_templates", {}).get("allowed", []):
        yield "auxiliary", t.get("fine_state")
    # `fallback_fine_state` is a real binding — `_fallback_reply()` takes that beat
    # straight from the catalog when the model replies with nothing, so it has to
    # count. Otherwise a spec that declares a fallback is told the sentence is
    # "referred to by nobody", and the author has to declare it a second time under
    # auxiliary_templates.
    if isinstance(spec.get("fallback_fine_state"), str):
        yield "fallback", spec["fallback_fine_state"]


def validate_flow_spec(spec: dict, catalog: list[dict] | None = None) -> tuple[list[str], list[str]]:
    """Structurally validate a spec; cross-check template bindings when a
    catalog is given. Returns ``(errors, warnings)`` — empty errors = valid.

    Warnings flag completeness gaps (e.g. catalog templates no binding
    reaches) that don't make the spec unusable but mean the spec does not
    fully cover its catalog.
    """
    errors: list[str] = []
    warnings: list[str] = []

    for key in _REQUIRED_TOP_KEYS:
        if key not in spec:
            errors.append(f"missing top-level key: {key}")
    if errors:
        return errors, warnings

    # instruction-grounded outcome vocab: this spec's own declared results, and only
    # those. A flow that declares none can emit none.
    valid_results = set(derive_outcomes(spec))
    events = set(spec["events"].keys())
    state_ids = [st.get("id") for st in spec["states"]]
    state_set = set(state_ids)

    if len(state_ids) != len(state_set):
        dupes = sorted({s for s in state_ids if state_ids.count(s) > 1})
        errors.append(f"duplicate state ids: {dupes}")

    initials = [st["id"] for st in spec["states"] if st.get("initial")]
    if len(initials) != 1:
        errors.append(f"expected exactly 1 initial state, got {initials}")

    # --- tools (spec_version 2: declarations) ---
    tools = spec["tools"]
    decls = tools.get("declarations", [])
    if not decls:
        errors.append("tools.declarations missing or empty (spec_version 2 required)")
    names = [d.get("name") for d in decls]
    if len(names) != len(set(names)):
        dupes = sorted({n for n in names if names.count(n) > 1})
        errors.append(f"duplicate tool declaration names: {dupes}")
    enabled = set(names)
    for d in decls:
        dn = d.get("name", "?")
        impl = d.get("impl", "generic")
        if impl not in KNOWN_IMPLS:
            errors.append(f"tool {dn}: unknown impl: {impl}")
        g = d.get("gating", {})
        ev = g.get("after_event")
        if ev and ev not in events:
            errors.append(f"tool {dn}: gating after_event unknown: {ev}")
        st = g.get("required_before_state")
        if st and st not in state_set:
            errors.append(f"tool {dn}: gating required_before_state not a state: {st}")
        for ref in ("must_precede", "requires_prior"):
            # Both keys accept a name OR a list of names — SpecGate has always read
            # them that way (`[x] if isinstance(x, str) else list(x)`). Validating
            # only the scalar form rejected a spec the gate can enforce perfectly
            # well: one get_current_datetime that must precede all three
            # date-taking tools.
            other = g.get(ref)
            for nm in ([other] if isinstance(other, str) else list(other or [])):
                if nm and nm not in enabled:
                    errors.append(
                        f"tool {dn}: gating {ref} references undeclared tool: {nm}")

    # --- states & transitions ---
    reachable_targets: set[str] = set()
    for st in spec["states"]:
        sid = st.get("id", "?")
        if not st.get("templates") and not st.get("entry_tools"):
            warnings.append(f"state {sid}: no templates and no entry_tools")
        for t in st.get("templates", []):
            ev = t.get("when_event")
            if ev and ev not in events:
                errors.append(f"state {sid}: template when_event unknown: {ev}")
        for tool in st.get("entry_tools", []):
            if tool not in enabled:
                errors.append(f"state {sid}: entry_tool not enabled: {tool}")
        transitions = st.get("on", [])
        if not transitions and not st.get("terminal"):
            errors.append(f"state {sid}: non-terminal state has no transitions")
        seen_events: set[str] = set()
        for tr in transitions:
            ev = tr.get("event")
            if ev not in events:
                errors.append(f"state {sid}: transition on unknown event: {ev}")
            elif ev in seen_events:
                errors.append(f"state {sid}: duplicate transition for event: {ev}")
            else:
                seen_events.add(ev)
            target = tr.get("to")
            if target not in state_set:
                errors.append(f"state {sid}: transition target not a state: {target}")
            else:
                reachable_targets.add(target)
            for tool in tr.get("tools", []):
                if tool not in enabled:
                    errors.append(f"state {sid}: transition tool not enabled: {tool}")
        out = st.get("outcome")
        if out:
            if outcome_result(spec, out) not in valid_results:
                errors.append(f"state {sid}: outcome result invalid: {outcome_result(spec, out)}")
            errors.extend(f"state {sid}: {m}" for m in _check_outcome_args(spec, out))
        elif st.get("terminal"):
            warnings.append(f"state {sid}: terminal state without an outcome")

    initial_set = set(initials)
    for sid in sorted(state_set - reachable_targets - initial_set):
        warnings.append(f"state {sid}: unreachable (no transition targets it)")

    # --- faq routing ---
    seen_intents: set[str] = set()
    for route in spec["faq_routing"].get("routes", []):
        intent = route.get("intent", "?")
        if intent in seen_intents:
            errors.append(f"faq: duplicate intent: {intent}")
        seen_intents.add(intent)
        then = route.get("then")
        if then != "resume":
            out = (then or {}).get("outcome", {})
            if outcome_result(spec, out) not in valid_results:
                errors.append(f"faq {intent}: terminal route outcome invalid: "
                              f"{outcome_result(spec, out)}")
            errors.extend(f"faq {intent}: {m}" for m in _check_outcome_args(spec, out))

    # --- constraints ---
    seen_cids: set[str] = set()
    for c in spec["constraints"]:
        cid = c.get("id", "?")
        if "id" in c:
            if cid in seen_cids:
                errors.append(f"constraint duplicate id: {cid}")
            seen_cids.add(cid)
        ctype = c.get("type")
        # A constraint without `type` is a prose rule (guidance rendered into the
        # instruction, no mechanical enforcement); it must still carry a desc.
        if ctype is None:
            if not c.get("desc"):
                errors.append(f"constraint {cid}: prose constraint missing desc")
        elif ctype in RETIRED_CONSTRAINT_TYPES:
            errors.append(f"constraint {cid}: เลิกใช้ type '{ctype}' แล้ว — "
                          f"{RETIRED_CONSTRAINT_TYPES[ctype]}")
        elif ctype not in CONSTRAINT_TYPES:
            errors.append(f"constraint {cid}: unknown type: {ctype}")
        layers = set(c.get("enforce", []))
        if not layers:
            errors.append(f"constraint {cid}: missing enforce layers")
        elif "backend" in layers:
            # `backend` is retired: runtime enforcement moved entirely to each tool's
            # `gating`. The validator used to accept it in silence even though the docs
            # called it retired — reject it and name the right place, so nobody
            # declares it and believes something is enforcing it.
            errors.append(f"constraint {cid}: เลิกใช้ enforce 'backend' แล้ว — "
                          "การบังคับตอนรันประกาศที่ `gating` ของ tool "
                          "(หรือให้ API ของ tenant ปฏิเสธเอง)")
        elif not layers <= {"prompt"}:
            errors.append(f"constraint {cid}: invalid enforce layers: {sorted(layers)}")
        ev = c.get("event")
        if ev and ev not in events:
            errors.append(f"constraint {cid}: unknown event: {ev}")
        target = c.get("to") or (c.get("on_exceed") or {}).get("to")
        if target and target not in state_set:
            errors.append(f"constraint {cid}: target state not found: {target}")
        for tool in filter(None, (c.get("tool"), c.get("first"), c.get("second"))):
            if tool not in enabled:
                errors.append(f"constraint {cid}: references non-enabled tool: {tool}")

    # --- catalog cross-check ---
    if catalog is not None:
        by_fine: dict[str, list[int]] = {}
        for entry in catalog:
            by_fine.setdefault(entry.get("_fine_state", ""), []).append(entry.get("text_id"))
        for where, fine in _template_refs(spec):
            if fine not in by_fine:
                errors.append(f"{where}: template binding unresolved in catalog: {fine}")
        bound = {fine for _, fine in _template_refs(spec)}
        for fine in sorted(set(by_fine) - bound):
            warnings.append(f"catalog fine_state not bound by any spec reference: {fine}")

    return errors, warnings


def declared_tools(spec: dict) -> dict[str, dict]:
    """Map declared tool name → declaration dict."""
    return {d["name"]: d for d in spec["tools"].get("declarations", [])}


def build_tool_schemas(spec: dict) -> list[dict]:
    """Build OpenAI-style function schemas from the spec's tool declarations —
    the `tools=` payload for the API request and (byte-identically) for the
    tokenizer's chat template at training time. The `reply` tool is NOT here:
    it is catalog-dependent and composed by the communicator."""
    schemas = []
    for d in spec["tools"].get("declarations", []):
        props, required = {}, []
        for arg, meta in d.get("args", {}).items():
            p = {"type": meta.get("type", "string")}
            if meta.get("enum"):
                p["enum"] = meta["enum"]
            desc_bits = []
            if meta.get("format"):
                desc_bits.append(f"format: {meta['format']}")
            if desc_bits:
                p["description"] = " · ".join(desc_bits)
            props[arg] = p
            if not meta.get("optional"):
                required.append(arg)
        schemas.append({
            "type": "function",
            "function": {
                "name": d["name"],
                "description": d.get("desc", ""),
                "parameters": {"type": "object", "properties": props, "required": required},
            },
        })
    return schemas


def resolve_templates(spec: dict, catalog: list[dict]) -> dict[str, list[int]]:
    """Resolve every binding to concrete text_ids: ``state:<id>`` /
    ``faq:<intent>`` / ``auxiliary`` → sorted text_id list."""
    by_fine: dict[str, list[int]] = {}
    for entry in catalog:
        by_fine.setdefault(entry.get("_fine_state", ""), []).append(entry["text_id"])
    resolved: dict[str, list[int]] = {}
    for where, fine in _template_refs(spec):
        resolved.setdefault(where, [])
        resolved[where] = sorted(set(resolved[where]) | set(by_fine.get(fine, [])))
    return resolved


__all__ = [
    "is_chain_state",
    "FLOWS_DIR",
    "KNOWN_IMPLS",
    "load_tenant_spec",
    "derive_outcomes",
    "closing_tool",
    "outcome_key",
    "outcome_args",
    "outcome_result",
    "CONSTRAINT_TYPES",
    "GATING_KEYS",
    "CONSTRAINT_KEYS",
    "RETIRED_CONSTRAINT_TYPES",
    "load_flow_spec",
    "validate_flow_spec",
    "declared_tools",
    "build_tool_schemas",
    "resolve_templates",
]


def is_chain_state(state: dict) -> bool:
    """Several beats in ONE turn, or alternatives to pick from?

    The signal is `when_event`: templates bound to customer events are variants
    (one is chosen per event); several plain templates describe one utterance built
    from them, in order. Identical to `aax6.core.flowspec.is_chain_state` in the
    training repo — the instruction the model trained on and the instruction the
    app serves must agree on which states are chains, or the model is asked at
    serve time for something it was never told at train time. `compose` / `group` /
    `template_mode` used to encode this by hand and are no longer read.
    """
    tpl = state.get("templates") or []
    if len(tpl) <= 1:
        return False
    return not any(t.get("when_event") for t in tpl)

def normalize_catalog(catalog: list[dict], spec: dict | None = None) -> list[dict]:
    """Fill in the catalog fields that can be DERIVED, so an author writes only the
    three that carry meaning: ``text_id``, ``_fine_state``, ``template``.

    The rest was duplicated information kept in sync by hand — ``company`` (the spec
    says it), ``state`` (the spec's binding IS it, and a hand-written one could
    disagree, grouping the line under a state that never reaches it), ``intent_name``
    (the fine_state is the name). Only MISSING keys are filled, so a catalog that
    spells them out keeps its exact values and prompt layout.
    """
    spec = spec or {}
    company = spec.get("company", "")

    fs_to_state: dict[str, str] = {}
    for st in spec.get("states", []):
        for t in st.get("templates", []):
            for fs in ([t["fine_state"]] if t.get("fine_state") else (t.get("any_of") or [])):
                fs_to_state.setdefault(fs, st.get("id", ""))
    for route in (spec.get("faq_routing") or {}).get("routes", []):
        for t in route.get("templates", []):
            if t.get("fine_state"):
                fs_to_state.setdefault(t["fine_state"], "faq")

    # text_id is the model's handle on ONE wording. An author who writes a beat
    # with a single wording has nothing to say about it, so it may be left out and
    # is assigned here — deterministically, continuing past whatever ids the file
    # does declare, in file order. (In training the ids are re-shuffled per task
    # anyway; only `_fine_state` survives that, which is why the two are separate
    # names in the first place.)
    next_id = max((int(e["text_id"]) for e in catalog
                   if str(e.get("text_id", "")).lstrip("-").isdigit()), default=999) + 1

    out: list[dict] = []
    for entry in catalog:
        e = dict(entry)
        fs = e.get("_fine_state") or e.get("fine_state") or ""
        e.setdefault("_fine_state", fs)
        if not str(e.get("text_id", "")).lstrip("-").isdigit():
            e["text_id"] = next_id
            next_id += 1
        if company:
            e.setdefault("company", company)
        e.setdefault("intent_name", fs)
        if fs_to_state.get(fs):
            e.setdefault("state", fs_to_state[fs])
        out.append(e)
    return out
