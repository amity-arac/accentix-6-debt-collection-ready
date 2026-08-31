"""FlowSpec → communicator instruction .md renderer (Step 2a of the
flow-interpreter plan).

Generates the per-company pre-script instruction file from a FlowSpec —
replacing the hand-written ``{ver}_communicator_instruction-{company}.md``
lineage. The output keeps ``[placeholder]`` tokens intact (fill_template
substitutes them at load time) and emits NO text_ids: templates are
referenced by ``fine_state`` only, and the concrete catalog is auto-appended
at runtime by the communicator exactly as before. One renderer, N specs —
synthetic flows from the flow generator render through this same code path.

CLI::

    python -m aax6.core.flowspec_render data/flows/AEON-outbound-remind.json
    # → data/system_instructions/pre-script/v12_communicator_instruction-AEON.md
"""
from __future__ import annotations

import argparse
from pathlib import Path

from demo.server.flow.flowspec import derive_outcomes, load_flow_spec, validate_flow_spec, is_chain_state

RENDER_VERSION = "v12"

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
        s = "(" + " หรือ ".join(f"`{b}`" for b in t["any_of"]) + ")"
    else:
        s = f"`{t['fine_state']}`"
    if t.get("when_event"):
        s += f" (เมื่อ {t['when_event']})"
    if t.get("optional"):
        s += " (ข้ามได้)" if chain else " (ถ้าจำเป็น)"
    return s


def _fmt_templates(templates: list[dict], chain: bool = False) -> str:
    """Chain → `a` → `b`; alternatives → `a` / `b`. Word-for-word the training
    renderer, so the served instruction says exactly what the model was trained
    on. Chain-ness comes from `is_chain_state`, never from a hand-set flag."""
    parts = [_label_template(t, chain) for t in templates]
    return (" → " if chain else " / ").join(parts)


def _fmt_event(spec: dict, event: str) -> str:
    ev = spec["events"].get(event, {})
    cues = ev.get("cues")
    if cues:
        return f"{event} ({ev.get('desc', '')} — เช่น {', '.join(cues[:4])})"
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


def _render_state(spec: dict, st: dict) -> list[str]:
    """One state as the lines the model reads.

    What it says (the beats, marked as a chain or as alternatives), what runs first
    (`entry_tools`), where each customer event leads, and what gets recorded if the
    call ends here. This is the only place the flow graph becomes prose — the guards
    read the same spec directly, so the two cannot drift.
    """
    lines = [f"**{st['id']}**" + (" ← เริ่มที่นี่" if st.get("initial") else "")]
    if st.get("templates"):
        if is_chain_state(st):
            lines.append(f"- **พูดต่อกันในเทิร์นเดียว (chain) ตามลำดับ:** "
                         f"{_fmt_templates(st['templates'], chain=True)} "
                         f"— เรียก `reply(text_ids=[...])` ใส่หลาย id เรียงตามนี้")
        else:
            lines.append(f"- template: {_fmt_templates(st['templates'])}")
    if st.get("entry_tools"):
        chain = " → ".join(f"`{t}`" for t in st["entry_tools"])
        lines.append(f"- เมื่อเข้า state นี้ เรียก (silent): {chain}")
    if st.get("note"):
        lines.append(f"- {st['note']}")
    if st.get("max_visits"):
        lines.append(f"- เข้า state นี้ได้สูงสุด {st['max_visits']} ครั้งต่อสาย")
    for tr in st.get("on", []):
        arrow = f"  - {_fmt_event(spec, tr['event'])} → **{tr['to']}**"
        if tr.get("tools"):
            arrow += " [เรียก " + ", ".join(f"`{t}`" for t in tr["tools"]) + " ก่อน]"
        if tr.get("note"):
            arrow += f" — {tr['note']}"
        lines.append(arrow)
    out = st.get("outcome")
    if out:
        reasons = "/".join(out.get("reasons", [])) or "-"
        closer, _ = _closing_tool(spec)
        lines.append(f"  - จบสาย: `{closer}(\"{out['result']}\", reason: {reasons})`")
    return lines


def render_instruction(spec: dict) -> str:
    """Render a FlowSpec into a complete pre-script instruction .md (Thai),
    section-for-section equivalent to the hand-written v11 lineage."""
    company = spec["company"]
    tools = spec["tools"]
    decls = tools.get("declarations", [])
    validation = tools.get("validation", {})
    sec: list[str] = []

    # --- header ---
    # Role and governing law come FROM THE SPEC. Both used to be hardcoded to debt
    # collection, so the hospital appointment flow opened its prompt by declaring
    # the agent a debt collector bound by the Debt Collection Act — while its own
    # `agent_role` and `legal_note` sat in the file, read by nothing. A spec that
    # says nothing keeps the debt default, so the collection specs are unchanged.
    # `agent_role` is the IDENTITY (who the agent is). `role` is a style note in the
    # collection specs ("พูดคุยกระชับ สุภาพ…") which reads as nonsense in the identity
    # slot, so it stays a modifier appended after — exactly as it rendered before.
    identity = spec.get("agent_role") or ""
    header = (f"คุณรับบทเป็น **{identity}**" if identity
              else f"คุณรับบทเป็นเจ้าหน้าที่ติดตามทวงถามหนี้ของ **บริษัท {company}**")
    if spec.get("role"):
        header += f" — {spec['role']}"
    legal = spec.get("legal_note")
    if legal is None:
        legal = "ปฏิบัติตาม พ.ร.บ. การทวงถามหนี้ พ.ศ. 2558"
    sec.append(header + f"\n\n**เป้าหมาย: {spec.get('goal', '')}**"
               + (f" {legal}" if legal else ""))

    # --- CRM snapshot (placeholders intact; fill_template substitutes at load) ---
    labels = {**_CRM_LABELS, **(spec.get("crm_labels") or {})}
    crm = ["## ข้อมูลลูกค้า (CRM Snapshot)"]
    for field in spec.get("crm_fields", []):
        label = labels.get(field, field)
        crm.append(f"- **{label}:** {{{field}}}")
    sec.append("\n".join(crm))

    # --- reply format + tools ---
    fmt = [
        "## วิธีตอบ (Reply Format)",
        "ตอบลูกค้าโดยเรียก `reply(text_ids=[...])` เลือกจาก **Available Pre-Scripts** ที่ระบบต่อท้ายให้เท่านั้น — **ห้ามสร้างข้อความอิสระ** ระบบเติม slot ({customer_name}/{amount}/...) อัตโนมัติ",
        "",
        "**เครื่องมือ silent (ไม่มีข้อความถึงลูกค้า — เรียกก่อน `reply`):**",
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
            extras.append(f"เรียกได้หลัง event `{g['after_event']}` เท่านั้น")
        if g.get("max_calls_per_conversation"):
            extras.append(f"สูงสุด {g['max_calls_per_conversation']} ครั้งต่อสาย")
        if g.get("must_precede"):
            extras.append(f"ต้องเรียกก่อน `{g['must_precede']}` เสมอ")
        if g.get("requires_prior"):
            # Wording kept byte-identical to what it was when `args_must_match` was the
            # hardcoded ("amount","date","channel"): a refactor that moves a list from
            # code into the spec must not also change what the model reads, or the next
            # measurement cannot attribute the difference (§6.14).
            extras.append(f"ต้องมี `{g['requires_prior']}` "
                          + ("ค่าตรงกัน" if g.get("args_must_match") else "") + "มาก่อน")
        if g.get("required_before") == "non_today_date_in_args_or_reply":
            extras.append("เรียกก่อนพูด/บันทึกวันที่ที่ไม่ใช่วันนี้")
        if g.get("required_at") == "end_of_call":
            extras.append("**เรียกตอนจบสายเสมอ ครั้งเดียว**")
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
        fmt.append("\nเรียกผิดลำดับ/เรียกซ้ำ จะถูก reject พร้อมเหตุผล — อ่าน hint "
                   "แล้วทำตาม ห้ามเรียกซ้ำแบบเดิม")

    notes = tools.get("notes", [])
    if notes:
        fmt.append("\n" + " / ".join(f"**{n}**" for n in notes))
    if validation.get("date_format"):
        fmt.append(f"\nวันที่ทุกค่าใช้รูปแบบ `{validation['date_format']}` · `channel` ∈ {', '.join(validation.get('payment_channels', []))}")
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
    pr = ["## หลักการ (⛔ กฎสูงสุด)"]
    for i, c in enumerate(prompt_rules, 1):
        pr.append(f"{i}. {c['desc']}")
    if backend_rules:
        pr.append("\n**กติกาที่ระบบบังคับเอง (เรียกผิดจะถูก reject พร้อมเหตุผล — อ่าน hint แล้วแก้):**")
        for c in backend_rules:
            pr.append(f"- {c['desc']}")
    sec.append("\n".join(pr))

    # --- FAQ routing ---
    faq = ["## FAQ (ตอบคำถามแทรก แล้วกลับเข้า flow)"]
    for route in spec.get("faq_routing", {}).get("routes", []):
        tmpl = _fmt_templates(route.get("templates", []))
        line = f"- **{route['intent']}** \"{route.get('desc', '')}\" → {tmpl}"
        then = route.get("then")
        if then == "resume":
            line += " → กลับเข้า flow เดิม"
        else:
            out = (then or {}).get("outcome", {})
            reasons = "/".join(out.get("reasons", [])) or "-"
            line += f" → `{_closing_tool(spec)[0]}(\"{out.get('result')}\", \"{reasons}\")` ปิดสาย"
        if route.get("note"):
            line += f" — {route['note']}"
        faq.append(line)
    sec.append("\n".join(faq))

    # --- outcomes summary ---
    results = derive_outcomes(spec)
    if results:
        try:
            _closer, _cargs = _closing_tool(spec)
            head = f"## Outcome (จบสายต้องเรียก `{_closer}({', '.join(_cargs)})` เสมอ)"
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
          "เลือก text_id จาก catalog ที่ระบบต่อท้ายให้ (รายการเต็มต่อท้ายอัตโนมัติ) — สรุปกลุ่มตาม state:"]
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
    aux = spec.get("auxiliary_templates", {}).get("allowed", [])
    if aux:
        ov.append("- **ตามบริบท** — " + ", ".join(f"`{t['fine_state']}`" for t in aux))
    faq_groups = []
    for route in spec.get("faq_routing", {}).get("routes", []):
        for t in route.get("templates", []):
            if t["fine_state"] not in faq_groups:
                faq_groups.append(t["fine_state"])
    if faq_groups:
        ov.append("- **faq** — " + ", ".join(f"`{g}`" for g in faq_groups))
    sec.append("\n".join(ov))

    return "\n\n".join(sec) + "\n"


def default_output_path(spec: dict, version: str = RENDER_VERSION) -> Path:
    return (Path(__file__).resolve().parents[3] / "data/system_instructions/pre-script"
            / f"{version}_communicator_instruction-{spec['company']}.md")


def main() -> None:
    ap = argparse.ArgumentParser(description="Render a FlowSpec into a communicator instruction .md")
    ap.add_argument("spec", help="path to FlowSpec JSON (or bare flow_id under data/flows/)")
    ap.add_argument("-o", "--output", default=None, help="output .md path (default: pre-script dir, version prefix)")
    ap.add_argument("--version", default=RENDER_VERSION, help=f"version prefix for the default filename (default {RENDER_VERSION})")
    args = ap.parse_args()

    spec = load_flow_spec(args.spec)
    errors, warnings = validate_flow_spec(spec)
    if errors:
        raise SystemExit("FlowSpec invalid:\n" + "\n".join(f"- {e}" for e in errors))
    for w in warnings:
        print(f"warning: {w}")

    out = Path(args.output) if args.output else default_output_path(spec, args.version)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_instruction(spec), encoding="utf-8")
    print(f"rendered {spec['flow_id']} -> {out}")


if __name__ == "__main__":
    main()
