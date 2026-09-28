"""FlowSpec → the instruction text the model reads.

The output keeps `[placeholder]` tokens intact (fill_template substitutes them per
call) and emits no text_ids: templates are referenced by `fine_state`, and the
concrete catalog is appended separately.

The prompt is assembled in memory every time a call opens (`sessions.py` calls
`render_instruction()` directly) — there is no pre-rendered instruction file on disk
to generate or keep in sync.
"""
from __future__ import annotations

from demo_v2.lib import lang as _L


from demo_v2.lib.prescript import CHAIN_RULE
from demo_v2.server.flow.flowspec import (derive_outcomes, is_chain_state, outcome_args)


# CRM field labels come from the spec (`crm_labels`), because what a field is called
# in the customer's language is part of that company's flow, not of this renderer. The
# hardcoded map here read like a glossary but was a debt collector's: an appointment
# flow's `doctor_name` fell through to the raw field name while `minimum_payment` —
# a field it does not have — was the one spelled out in Thai.
_CRM_LABELS: dict[str, str] = {}

_PHASE_TITLES = {"opening": "OPENING DIALOG", "main": "MAIN DIALOG", "close": "CLOSING"}


def _label_template(t: dict, chain: bool = False) -> str:
    """Label one beat for the instruction: its name, whether it is optional, and —
    in a chain — that it must be said in the same turn as its neighbours.
    """
    if t.get("any_of"):                       # one step that accepts any of several beats
        s = "(" + _L.frame("or").join(f"`{b}`" for b in t["any_of"]) + ")"
    else:
        s = f"`{t['fine_state']}`"
    if t.get("when_event"):
        s += _L.frame("when", ev=t["when_event"])
    if t.get("optional"):
        s += _L.frame("optional") if chain else _L.frame("if_needed")
    return s


def _fmt_templates(templates: list[dict], chain: bool = False) -> str:
    """Chain → `a` → `b`; alternatives → `a` / `b`. Word-for-word the training
    renderer, so the served instruction says exactly what the model was trained
    on. Chain-ness comes from `is_chain_state`, never from a hand-set flag."""
    parts = [_label_template(t, chain) for t in templates]
    return (" → " if chain else " / ").join(parts)


def _fmt_event(spec: dict, event: str) -> str:
    ev = spec["events"].get(event, {})
    # The right shape is {desc, cues}, but the docs once described it as
    # {name: description}, so people wrote a bare string and the renderer died with
    # an AttributeError while building the session — accept both shapes
    if isinstance(ev, str):
        ev = {"desc": ev}
    cues = ev.get("cues")
    if cues:
        return _L.frame("ev", event=event, desc=ev.get("desc", ""), cues=", ".join(cues[:4]))
    return f"{event} ({ev.get('desc', '')})" if ev.get("desc") else event


def _closing_tool(spec: dict) -> tuple[str, list[str]]:
    """The tool this spec closes a call with, and its argument names.

    Was hardcoded to `record_outcome` in three places, so the appointment flow —
    whose closer is `save_appointment` — was instructed to call a tool absent from
    its own schema, at the exact moment it had to write the booking. The closer is
    whichever tool declares `gating.required_at: "end_of_call"`. There is no fallback:
    defaulting to the collection name is how the appointment flow came to be told to
    call a tool it does not have, and a silent wrong name is worse than a load error.
    """
    for d in (spec.get("tools") or {}).get("declarations", []):
        if (d.get("gating") or {}).get("required_at") == "end_of_call":
            return d["name"], list((d.get("args") or {}).keys())
    raise ValueError(
        "spec declares no closing tool — exactly one tool must carry "
        'gating.required_at: "end_of_call"')


def _fmt_outcome_args(spec: dict, outcome: dict) -> str:
    """The closing tool's arguments, written with **that tool's real argument
    names**.

    This used to print a fixed `closer("<result>", reason: …)`, so AMT — which closes
    with `save_appointment(status, new_slot)` — was taught to send a value to an
    argument named `reason` that it does not have, while `new_slot` (required when a
    visit is rescheduled) never appeared on this line at all.
    """
    args = outcome_args(spec, outcome)
    return ", ".join(f'{k}="{v}"' for k, v in args.items())


def _fmt_reasons(outcome: dict) -> str:
    """`reasons` is this result's set of reason codes, not the value of an argument —
    appended as guidance."""
    rs = outcome.get("reasons") or []
    return f" — reason: {'/'.join(rs)}" if rs else ""


def _render_state(spec: dict, st: dict) -> list[str]:
    """One state as the lines the model reads.

    What it says (the beats, marked as a chain or as alternatives), what runs first
    (`entry_tools`), where each customer event leads, and what gets recorded if the
    call ends here. This is the only place the flow graph becomes prose — the guards
    read the same spec directly, so the two cannot drift.
    """
    lines = [f"**{st['id']}**" + (_L.frame("st_start") if st.get("initial") else "")]
    if st.get("templates"):
        if is_chain_state(st):
            lines.append(_L.frame("st_chain")
                         + f"{_fmt_templates(st['templates'], chain=True)} "
                         + _L.frame("st_chain2"))
        else:
            lines.append(f"- template: {_fmt_templates(st['templates'])}")
    if st.get("entry_tools"):
        chain = " → ".join(f"`{t}`" for t in st["entry_tools"])
        lines.append(_L.frame("st_silent", chain=chain))
    if st.get("note"):
        lines.append(f"- {st['note']}")
    if st.get("max_visits"):
        lines.append(_L.frame("st_visits", n=st["max_visits"]))
    for tr in st.get("on", []):
        arrow = f"  - {_fmt_event(spec, tr['event'])} → **{tr['to']}**"
        if tr.get("tools"):
            arrow += _L.frame("call_first") + ", ".join(f"`{t}`" for t in tr["tools"]) + _L.frame("call_first2")
        if tr.get("note"):
            arrow += f" — {tr['note']}"
        lines.append(arrow)
    out = st.get("outcome")
    if out:
        closer, _ = _closing_tool(spec)
        lines.append(_L.frame("st_end", call=f"{closer}({_fmt_outcome_args(spec, out)})")
                     + _fmt_reasons(out))
    return lines


def render_crm_block(spec: dict) -> str:
    """The CRM snapshot section on its own, placeholders intact.

    Callers that place it themselves (the live session puts it AFTER the catalog so
    everything above is identical across calls) render the instruction with
    `crm="omit"` and append this."""
    labels = {**_CRM_LABELS, **(spec.get("crm_labels") or {})}
    out = [_L.frame("h_crm")]
    for field in spec.get("crm_fields", []):
        out.append(f"- **{labels.get(field, field)}:** {{{field}}}")
    return "\n".join(out)


def render_instruction(spec: dict, crm: str = "inline") -> str:
    """Render a FlowSpec into a complete pre-script instruction .md (Thai),
    section-for-section equivalent to the hand-written v11 lineage.

    `crm` places the CRM snapshot: "inline" (section 2, the original), "end"
    (after everything), or "omit" (the caller renders it with `render_crm_block`).
    The CRM block is the ONLY part of this instruction that differs between calls
    (it is the only place `{field}` placeholders appear), so with it at the top the
    prompt diverges ~130 tokens in and nothing after that can be shared between
    calls. Moving it behind everything else makes the whole instruction identical
    across calls of the same tenant — which is what lets vLLM reuse the prefix.""" 
    company = spec["company"]
    tools = spec["tools"]
    decls = tools.get("declarations", [])
    validation = tools.get("validation", {})
    sec: list[str] = []

    # --- header ---
    # Identity comes from the spec (`agent_role`); hardcoding it once made a clinic's
    # appointment flow open by declaring the agent a debt collector. A spec that says
    # nothing keeps the debt default. `role` (a tone note) and `legal_note` were
    # removed from the format instead — see SPEC_LOCKED, "keys that were removed".
    identity = spec.get("agent_role") or ""
    header = (_L.frame("role_generic", identity=identity) if identity
              else _L.frame("role_debt", company=company))
    sec.append(header + _L.frame("goal", goal=spec.get("goal", "")))

    # --- CRM snapshot (placeholders intact; fill_template substitutes at load) ---
    labels = {**_CRM_LABELS, **(spec.get("crm_labels") or {})}
    crm_lines = [_L.frame("h_crm")]
    for field in spec.get("crm_fields", []):
        label = labels.get(field, field)
        crm_lines.append(f"- **{label}:** {{{field}}}")
    crm_block = "\n".join(crm_lines)
    if crm == "inline":
        sec.append(crm_block)

    # --- reply format + tools ---
    fmt = [
        _L.frame("h_reply"),
        _L.frame("reply_howto"),
        "",
        # The chain rule used to sit at the head of the template block. It moved here,
        # next to how-to-reply, because it is an instruction rather than data, and the
        # block is now a plain list — the same shape the training and eval side uses
        CHAIN_RULE,
        "",
        _L.frame("silent_tools"),
    ]
    for d in decls:
        arg_names = list(d.get("args", {}).keys())
        sig = f"({', '.join(arg_names)})" if arg_names else "()"
        line = f"- `{d['name']}{sig}` — {d.get('desc', '')}"
        g = d.get("gating", {})
        extras = []
        if g.get("note"):
            extras.append(g["note"])
        if g.get("after_event"):
            extras.append(_L.frame("g_after", ev=g["after_event"]))
        if g.get("max_calls_per_conversation"):
            extras.append(_L.frame("g_max", n=g["max_calls_per_conversation"]))
        if g.get("must_precede"):
            extras.append(_L.frame("g_precede", tool=g["must_precede"]))
        if g.get("requires_prior"):
            # Wording kept byte-identical to what it was when `args_must_match` was the
            # hardcoded ("amount","date","channel"): a refactor that moves a list from
            # code into the spec must not also change what the model reads, or the next
            # measurement cannot attribute the difference (§6.14).
            extras.append(_L.frame("g_prior", tool=g["requires_prior"])
                          + (_L.frame("g_same") if g.get("args_must_match") else "") + _L.frame("g_before"))
        if g.get("required_before") == "non_today_date_in_args_or_reply":
            extras.append(_L.frame("g_datetime"))
        if g.get("required_at") == "end_of_call":
            extras.append(_L.frame("g_closing"))
        if extras:
            line += " — " + " · ".join(extras)
        fmt.append(line)
    # The "a rejected call comes back with a reason" framing used to ride on a
    # `tool_pair` constraint, which was a third way of declaring an ordering that
    # `gating.requires_prior` already declares. The framing is not that rule — it is true
    # of every spec that declares any enforced gating — so it is derived here instead.
    _ENFORCED = ("max_successful_calls", "max_calls_per_conversation",
                 "requires_prior", "must_precede", "required_at")
    if any(k in (d.get("gating") or {}) for d in decls for k in _ENFORCED):
        fmt.append(_L.frame("reject_hint") + _L.frame("reject_hint2"))

    notes = tools.get("notes", [])
    if notes:
        fmt.append("\n" + " / ".join(f"**{n}**" for n in notes))
    if validation.get("date_format"):
        fmt.append(_L.frame("date_fmt", fmt=validation["date_format"], channels=", ".join(validation.get("payment_channels", []))))
    sec.append("\n".join(fmt))

    # --- flow state machine, grouped by phase ---
    flow = ["## Flow (State Machine)"]
    for phase in ("opening", "main", "close"):
        states = [st for st in spec["states"] if st.get("phase") == phase]
        if not states:
            continue
        flow.append(f"\n═══ {_PHASE_TITLES.get(phase, phase.upper())} ═══")
        for st in states:
            flow.extend(_render_state(spec, st))
            flow.append("")
    faq_note = spec.get("faq_routing", {}).get("note")
    if faq_note:
        flow.append(f"*{faq_note}*")
    sec.append("\n".join(flow))

    # --- principles: prompt/reward constraints as the numbered rule list ---
    prompt_rules = [c for c in spec["constraints"] if "prompt" in c.get("enforce", [])]
    backend_rules = [c for c in spec["constraints"] if c.get("enforce") == ["backend"]]
    pr = [_L.frame("h_principles")]
    for i, c in enumerate(prompt_rules, 1):
        pr.append(f"{i}. {c['desc']}")
    if backend_rules:
        pr.append(_L.frame("enforced"))
        for c in backend_rules:
            pr.append(f"- {c['desc']}")
    sec.append("\n".join(pr))

    # --- FAQ routing ---
    routes = spec.get("faq_routing", {}).get("routes", [])
    faq = [_L.frame("h_faq")] if routes else []
    for route in routes:
        tmpl = _fmt_templates(route.get("templates", []))
        line = f"- **{route['intent']}** \"{route.get('desc', '')}\" → {tmpl}"
        then = route.get("then")
        if then == "resume":
            line += _L.frame("faq_resume")
        else:
            out = (then or {}).get("outcome", {})
            line += (_L.frame("faq_close",
                              call=f"{_closing_tool(spec)[0]}({_fmt_outcome_args(spec, out)})")
                     + _fmt_reasons(out))
        if route.get("note"):
            line += f" — {route['note']}"
        faq.append(line)
    if len(faq) > 1:          # skip the heading when there is nothing under it
                              # (SHOP declares routes: [])
        sec.append("\n".join(faq))

    # --- outcomes summary ---
    results = derive_outcomes(spec)
    if results:
        try:
            _closer, _cargs = _closing_tool(spec)
            head = _L.frame("h_outcome", call=f"{_closer}({', '.join(_cargs)})")
        except ValueError:
            # a flow may record nothing — say what the results mean without promising a
            # call that does not exist
            head = "## Outcome"
        oc = [head]
        for result, info in results.items():
            reasons = "/".join(info.get("reasons", [])) or "-"
            oc.append(f"- `{result}` (reason: {reasons}) — {info.get('desc', '')}")
        sec.append("\n".join(oc))

    # --- pre-script overview (fine_states only; full catalog appended at runtime) ---
    ov = ["## Available Pre-Scripts",
          _L.frame("catalog_howto")]
    for phase in ("opening", "main", "close"):
        groups: list[str] = []
        for st in spec["states"]:
            if st.get("phase") == phase:
                for t in st.get("templates", []):
                    # an `any_of` step names its beats in a list instead of `fine_state`
                    for fs in ([t["fine_state"]] if t.get("fine_state") else t.get("any_of") or []):
                        if fs not in groups:
                            groups.append(fs)
        if groups:
            ov.append(f"- **{phase}** — " + ", ".join(f"`{g}`" for g in groups))
    faq_groups = []
    for route in spec.get("faq_routing", {}).get("routes", []):
        for t in route.get("templates", []):
            if t["fine_state"] not in faq_groups:
                faq_groups.append(t["fine_state"])
    if faq_groups:
        ov.append("- **faq** — " + ", ".join(f"`{g}`" for g in faq_groups))
    sec.append("\n".join(ov))

    if crm == "end":
        sec.append(crm_block)
    return "\n\n".join(sec) + "\n"


