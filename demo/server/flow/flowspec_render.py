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

from demo.server.flow.flowspec import load_flow_spec, validate_flow_spec

RENDER_VERSION = "v12"

# Thai labels for the standard CRM snapshot fields; unknown fields fall back
# to the raw field name so novel flows still render.
_CRM_LABELS = {
    "today": "วันนี้",
    "customer_name": "ชื่อลูกค้า",
    "amount": "ยอดค้างชำระทั้งหมด",
    "minimum_payment": "ยอดชำระขั้นต่ำ",
    "due_date": "วันครบกำหนด",
    "due_status": "สถานะ",
    "company_phone": "เบอร์บริษัท",
}

_PHASE_TITLES = {"opening": "OPENING DIALOG", "main": "MAIN DIALOG", "close": "CLOSING"}


def _fmt_templates(templates: list[dict]) -> str:
    parts = []
    for t in templates:
        s = f"`{t['fine_state']}`"
        if t.get("when_event"):
            s += f" (เมื่อ {t['when_event']})"
        if t.get("optional"):
            s += " (ถ้าจำเป็น)"
        parts.append(s)
    return " / ".join(parts)


def _fmt_event(spec: dict, event: str) -> str:
    ev = spec["events"].get(event, {})
    cues = ev.get("cues")
    if cues:
        return f"{event} ({ev.get('desc', '')} — เช่น {', '.join(cues[:4])})"
    return f"{event} ({ev.get('desc', '')})" if ev.get("desc") else event


def _render_state(spec: dict, st: dict) -> list[str]:
    lines = [f"**{st['id']}**" + (" ← เริ่มที่นี่" if st.get("initial") else "")]
    if st.get("templates"):
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
        lines.append(f"  - จบสาย: `record_outcome(\"{out['result']}\", reason: {reasons})`")
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
    role = spec.get("role", "")
    header = f"คุณรับบทเป็นเจ้าหน้าที่ติดตามทวงถามหนี้ของ **บริษัท {company}**"
    if role:
        header += f" — {role}"
    sec.append(
        header + f"\n\n**เป้าหมาย: {spec.get('goal', '')}** ปฏิบัติตาม พ.ร.บ. การทวงถามหนี้ พ.ศ. 2558"
    )

    # --- CRM snapshot (placeholders intact; fill_template substitutes at load) ---
    crm = ["## ข้อมูลลูกค้า (CRM Snapshot)"]
    for field in spec.get("crm_fields", []):
        label = _CRM_LABELS.get(field, field)
        crm.append(f"- **{label}:** [{field}]")
    sec.append("\n".join(crm))

    # --- reply format + tools ---
    fmt = [
        "## วิธีตอบ (Reply Format)",
        "ตอบลูกค้าโดยเรียก `reply(text_ids=[...])` เลือกจาก **Available Pre-Scripts** ที่ระบบต่อท้ายให้เท่านั้น — **ห้ามสร้างข้อความอิสระ** ระบบเติม slot ([customer_name]/[amount]/...) อัตโนมัติ",
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
            extras.append(f"ต้องมี `{g['requires_prior']}` ค่าตรงกันมาก่อน")
        if g.get("required_before") == "non_today_date_in_args_or_reply":
            extras.append("เรียกก่อนพูด/บันทึกวันที่ที่ไม่ใช่วันนี้")
        if g.get("required_at") == "end_of_call":
            extras.append("**เรียกตอนจบสายเสมอ ครั้งเดียว**")
        if extras:
            line += " — " + " · ".join(extras)
        fmt.append(line)
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
            line += f" → `record_outcome(\"{out.get('result')}\", \"{reasons}\")` ปิดสาย"
        if route.get("note"):
            line += f" — {route['note']}"
        faq.append(line)
    sec.append("\n".join(faq))

    # --- outcomes summary ---
    oc = ["## Outcome (จบสายต้อง `record_outcome(result, reason, remark)` เสมอ)"]
    for result, info in spec.get("outcomes", {}).get("results", {}).items():
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
                    if t["fine_state"] not in groups:
                        groups.append(t["fine_state"])
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
