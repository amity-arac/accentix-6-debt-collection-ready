# VENDORED from R&D repo (accentix-6-debt-collector) for sft_v12 — DO NOT hand-edit.
# Logic must stay byte-identical to src/aax6/core/flowspec.py. Only imports are adapted.
# v11.2/sft_v11 path does NOT use this module (it keeps flow/flowspec*.py + spec_backend.py).

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

from pathlib import Path as _P
REPO_ROOT = _P(__file__).resolve().parents[3]
FLOWS_DIR: Path = REPO_ROOT / "data" / "flows"

# Backend behaviors a tool declaration can bind to via `impl` (spec_version 2).
# The builtin names mirror CaseBackend.dispatch(); "generic" is the declarative
# executor in spec_backend.py. A declaration's `name` (what the model sees) is
# free — synthetic flows rename tools without touching any implementation.
KNOWN_IMPLS = frozenset({
    "verify_identity",
    "check_account_status",
    "callback_datetime",
    "record_verbal_commitment",
    "payment_date",
    "get_current_datetime",
    "transfer_to_human_agent",
    "record_outcome",
    "update_phone",
    "generic",
})

# Mirrors backend.RESULT_CODES.
RESULT_CODES = frozenset({"ptp", "refused", "unreachable", "reached", "tcb", "tin"})

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

_REQUIRED_TOP_KEYS = (
    "spec_version", "flow_id", "company", "events", "tools",
    "states", "faq_routing", "constraints", "outcomes",
)


def load_flow_spec(path: str | Path) -> dict:
    """Load a FlowSpec JSON. Accepts an absolute/relative path or a bare
    flow_id (resolved under ``data/flows/``)."""
    p = Path(path)
    if not p.suffix:
        p = FLOWS_DIR / f"{p.name}.json"
    with open(p, encoding="utf-8") as f:
        return json.load(f)


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
            yield f"state:{st.get('id')}", t.get("fine_state")
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
            other = g.get(ref)
            if other and other not in enabled:
                errors.append(f"tool {dn}: gating {ref} references undeclared tool: {other}")

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
            if out.get("result") not in RESULT_CODES:
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
            if out.get("result") not in RESULT_CODES:
                errors.append(f"faq {intent}: terminal route outcome invalid: {out.get('result')}")

    # --- constraints ---
    seen_cids: set[str] = set()
    for c in spec["constraints"]:
        cid = c.get("id", "?")
        if cid in seen_cids:
            errors.append(f"constraint duplicate id: {cid}")
        seen_cids.add(cid)
        ctype = c.get("type")
        if ctype not in CONSTRAINT_TYPES:
            errors.append(f"constraint {cid}: unknown type: {ctype}")
        layers = set(c.get("enforce", []))
        if not layers:
            errors.append(f"constraint {cid}: missing enforce layers")
        elif not layers <= {"prompt", "reward", "backend", "session"}:
            # session = enforce ที่ชั้น reply-gate (block template ก่อน render)
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
        if result not in RESULT_CODES:
            errors.append(f"outcomes: invalid result code: {result}")

    # --- catalog cross-check ---
    if catalog is not None:
        by_fine: dict[str, list[int]] = {}
        for entry in catalog:
            by_fine.setdefault(entry.get("_fine_state", ""), []).append(entry["text_id"])
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


def flow_meta(spec: dict) -> dict:
    """Flatten the spec facts that per-turn consumers (state tracker, GRPO
    reward, adherence scorer) all need: tool name→impl, outcome vocabulary,
    disclose group, pay-ask groups + quota."""
    disclose_fs = None
    max_pay_asks = None
    for c in spec.get("constraints", []):
        if c["id"] == "disclose_once":
            disclose_fs = c.get("template_fine_state", "disclose_balance")
        if c["id"] == "max_pay_asks":
            max_pay_asks = c.get("max")
    pay_ask_fs: set[str] = set()
    for st in spec["states"]:
        if st.get("counts_as") == "pay_ask":
            pay_ask_fs |= {t["fine_state"] for t in st.get("templates", [])}
    pay_ask_fs.discard(disclose_fs)
    return {
        "tool_impls": {d["name"]: d.get("impl", "generic")
                       for d in spec["tools"].get("declarations", [])},
        "outcome_results": {r: info.get("reasons", [])
                            for r, info in spec.get("outcomes", {}).get("results", {}).items()},
        "require_kyc": bool(spec["tools"].get("require_kyc")),
        "disclose_fs": disclose_fs,
        "pay_ask_fs": sorted(pay_ask_fs),
        "max_pay_asks": max_pay_asks,
    }


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
    "FLOWS_DIR",
    "KNOWN_IMPLS",
    "RESULT_CODES",
    "CONSTRAINT_TYPES",
    "load_flow_spec",
    "load_catalog",
    "validate_flow_spec",
    "declared_tools",
    "build_tool_schemas",
    "resolve_templates",
]
