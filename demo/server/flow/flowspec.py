"""FlowSpec: declarative, machine-readable call-flow definitions.

A FlowSpec is the single source of truth for one company's call flow — the
same information the per-company instruction .md files carry in prose
(states, transitions, tool gating, constraints, FAQ routing, outcomes),
formalized as JSON under ``data/flows/<flow_id>.json``. One spec feeds three
consumers that today each hard-code their own copy of the rules:

1. the prompt renderer (spec → instruction .md),
2. the backend interpreter (spec → tool gating / validation),
3. the GRPO reward (spec → mechanical trajectory scoring).

Design rules the schema enforces:

- **Template binding is id-agnostic.** States reference catalog entries by
  ``fine_state`` (the semantic unit), never by ``text_id`` — text_ids can be
  remapped per training example without touching the spec.
- **Every constraint declares its enforcement layer** (``enforce`` ⊆
  {prompt, reward, backend}) so nothing is silently "guidance only".
- **Inferred policy is marked.** Anything not explicit in the source
  instruction carries ``"inferred": true`` — the spec never silently invents
  policy.

This module is pure stdlib: load, structurally validate, cross-check a spec
against its pre-script catalog, and resolve state → text_id bindings.
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
    "resume_after_interrupt",
    "require_tool_before_end",
    "outcome_precondition",
    "tool_pair",
})

# `company` and `flow_id` are NOT required in the file: `load_tenant_spec` fills them
# from the filename, which is the one place they cannot disagree with. `spec_version` is
# implied by the shape the loader accepts.
_REQUIRED_TOP_KEYS = (
    "events", "tools", "states", "faq_routing", "constraints", "outcomes",
)


# --------------------------------------------------------------------------- #
# The locked shape. Anything outside these sets is rejected, not ignored.
#
# Until this existed, `validate_flow_spec` checked that the keys it KNEW about were
# well-formed and said nothing about the rest — so a typo (`fine_states`, `entry_tool`)
# validated clean and then did nothing at runtime, and every retired key
# (`compose`, `group`, `template_mode`) could quietly come back. A format is only
# locked if something refuses what is not in it.
# --------------------------------------------------------------------------- #
TOP_KEYS = frozenset({
    # identity + presentation
    "display_name",
    # who the agent is, in the model's words
    "role", "agent_role", "goal", "legal_note",
    # what the agent knows about the customer
    "crm_fields", "crm_labels", "session_init",
    # what it can say, and when
    "catalog", "events", "states", "faq_routing", "constraints", "outcomes",
    "auxiliary_templates", "fallback_fine_state",
    # what it can do
    "tools",
    # which beats count as verify / disclose / close (the training env reads this)
    "compliance",
    # accepted from the older shape so an existing file still loads
    "spec_version", "company", "flow_id", "catalog_inline",
})
STATE_KEYS = frozenset({
    "id", "phase", "initial", "terminal", "templates", "on", "entry_tools",
    "outcome", "note", "spec_note", "counts_as", "max_visits", "inferred",
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
CATALOG_KEYS = frozenset({
    "text_id", "_fine_state", "template",                 # the three that matter
    "hint",                                               # เมื่อไหร่ควรใช้สำนวนนี้
    "company", "state", "intent_name", "category",        # derived; accepted if present
    "fine_state", "_hint_where", "_example_AEON", "is_closer", "is_demand",
    "is_acknowledgment", "expects_response", "note", "desc",
})
# Retired keys, named so the error can say what replaced them.
RETIRED = {
    "compose": "หลาย template ใน state เดียว (ไม่มี when_event) = chain อยู่แล้ว",
    "render_all_templates": "เหมือน compose",
    "group": "ใช้ when_event แยกทางเลือก / ไม่ใส่ = chain",
    "template_mode": "อนุมานจาก when_event",
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
    for i, e in enumerate(catalog or []):
        _check_keys(f"catalog[{i}]", e, CATALOG_KEYS, errors, allow_underscore=True)
        if not (e.get("_fine_state") or e.get("fine_state")):
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


def load_tenant_spec(path: "Path | str") -> dict:
    """Read one tenant file and fill in the identity the filename already carries.

    A spec used to repeat its own `company` and `flow_id`, which is data that can
    disagree with the file it lives in — and did: the blank template said
    `company: "YOURCO"` while sitting in `_TEMPLATE.company.json`. Deriving them here
    means every reader downstream still sees a complete spec while the file on disk
    says each fact once.

    `<CODE>.company.json`  -> company = CODE
    `<FLOW_ID>.json`       -> flow_id = FLOW_ID, company = the part before the first `-`
    """
    p = Path(path)
    spec = json.loads(p.read_text(encoding="utf-8"))
    stem = p.name[: -len(".company.json")] if p.name.endswith(".company.json") else p.stem
    if p.name.endswith(".company.json"):
        spec.setdefault("company", stem)
        spec.setdefault("flow_id", f"{stem}-outbound-call")
    else:
        spec.setdefault("flow_id", stem)
        spec.setdefault("company", stem.split("-")[0])
    spec.setdefault("spec_version", 2)
    return spec


def resolve_catalog(spec: dict, flows_dir: Path | None = None) -> list[dict]:
    """The catalog for a spec, whichever layout it uses.

    - **single file** — ``catalog_inline`` holds the templates (``catalog`` may say
      ``"__inline__"``). One company, one file, nothing to keep in sync.
    - **split** — ``catalog`` names a file, resolved under ``data/pre-scripts/``
      (or repo-root-relative if it contains a separator).

    Ported from ``aax6.core.flowspec.resolve_catalog`` in the training repo so both
    sides read the SAME two layouts. This is the one place that decides which, so
    every caller stays agnostic.
    """
    if isinstance(spec.get("catalog"), list):
        return spec["catalog"]                      # current shape: `catalog` IS the list
    if spec.get("catalog") == "__inline__" or spec.get("catalog_inline") is not None:
        # older shape kept readable: `catalog: "__inline__"` + a separate `catalog_inline`
        cat = spec.get("catalog_inline")
        if not isinstance(cat, list):
            raise ValueError("catalog_inline ต้องเป็น list ของ template")
        return cat
    name = spec.get("catalog")
    if not name:
        raise ValueError("spec ไม่มีทั้ง catalog_inline และ catalog")
    path = Path(name)
    if not path.is_absolute():
        root = (flows_dir or FLOWS_DIR).parent.parent
        path = (root / name) if ("/" in str(name) or "\\" in str(name)) \
            else (root / "data" / "pre-scripts" / name)
    return load_catalog(path)


def load_catalog(path: str | Path) -> list[dict]:
    """Load a pre-script catalog (flat list of template entries)."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"catalog {path} is not a flat list")
    return data


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
    valid_results = set((spec.get("outcomes") or {}).get("results", {}).keys())
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
            if out.get("result") not in valid_results:
                errors.append(f"state {sid}: outcome result invalid: {out.get('result')}")
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
            if out.get("result") not in valid_results:
                errors.append(f"faq {intent}: terminal route outcome invalid: {out.get('result')}")

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
        elif ctype not in CONSTRAINT_TYPES:
            errors.append(f"constraint {cid}: unknown type: {ctype}")
        layers = set(c.get("enforce", []))
        if not layers:
            errors.append(f"constraint {cid}: missing enforce layers")
        elif not layers <= {"prompt", "reward", "backend", "session"}:
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

    # --- outcomes ---
    for result in spec["outcomes"].get("results", {}):
        if result not in valid_results:
            errors.append(f"outcomes: invalid result code: {result}")

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
    "CONSTRAINT_TYPES",
    "load_flow_spec",
    "load_catalog",
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
    """Fill in the catalog fields that can be DERIVED, so an author only has to
    write the three that carry meaning: ``text_id``, ``_fine_state``, ``template``.

    Everything else was duplicated information an author had to keep in sync by
    hand:

    * ``company``    — the spec already says which company this is.
    * ``state``      — the spec already says which state binds this beat; that IS
      the binding. Written by hand it could disagree with the spec, and the
      prompt would then group the line under a state that never reaches it.
    * ``intent_name``— a label for the prompt line; the fine_state is the name.
    * ``category``   — A (say something) / B (ask something). Inferred from the
      beat name when unset: a beat that asks (``ask_``/``probe_``/``request_``)
      is B, everything else is A.

    Only MISSING keys are filled, so the shipped catalogs — which spell all of
    this out — keep their exact current values and prompt layout.
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
        if not e.get("category"):
            asks = fs.startswith(("ask_", "probe_", "request_")) or fs.endswith("_ask")
            e["category"] = "B" if asks else "A"
        out.append(e)
    return out
