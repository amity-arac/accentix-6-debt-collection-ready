"""Loaders for per-company communicator system prompts.

Two modes, one file per mode per company:

- Pre-script: tool-calling mode. Placeholder tokens like [customer_name] are
  substituted by fill_template() at load time.

- Free-form: natural-language mode. Customer context is appended as a
  structured block. The Thai Debt Collection Act is appended for reference.

When V6_ACTIVE (set via AAX6_V6_ACTIVE=1), the loader prefers v6_*.md instruction
files. The v6 files live as siblings of the v5 files; the swap is one constant.
"""

import os
from pathlib import Path

from agents.prescript import fill_template
from simulator.config import V6_ACTIVE

PRESCRIPT_INSTRUCTION_DIR = "data/system_instructions/pre-script"
FREEFORM_INSTRUCTION_DIR = "data/system_instructions/free-form"
INSTRUCTION_TEMPLATE = "communicator_instruction-{company}.md"
V6_INSTRUCTION_TEMPLATE = "v6_communicator_instruction-{company}.md"

# Demo-only REPLACEMENT prompts — when the caller passes a `prompt_variant`
# matching one of these keys, this file is loaded as the ENTIRE system prompt,
# ignoring the per-company v6 Thai base. Used by the demo's Qwen path to drive
# Qwen with a short English imperative prompt (the Thai v6 base, even with an
# overlay, was diluting Qwen's tool-call adherence in earlier attempts).
#
# Whitelist — unknown variants are ignored so an arbitrary string can't read
# arbitrary files. fill_template still runs on the loaded body so [agent_name],
# [customer_name], etc. are substituted.
REPLACEMENT_FILES: dict[str, str] = {
    "qwen-demo": "qwen_demo_strict_english.md",
}

# Thai Debt Collection Act. Filename is misspelled on disk ("compilance").
COMPLIANCE_FILE = "data/aax-data/compilance.txt"

# Backend action tools available to free-form mode. Free-form writes its own Thai
# replies (no `reply` tool / no script catalog), but it CAN call these deterministic
# CaseBackend tools so KYC / payment / callback are enforced the same way as
# pre-script mode and the judge sees real tool usage. Appended to every free-form
# system prompt by load_freeform_prompt().
FREEFORM_TOOLS_BLOCK = """\
## เครื่องมือระบบหลังบ้าน (Backend Action Tools)

คุณตอบลูกค้าด้วยข้อความภาษาไทยที่คุณเขียนเองได้อย่างอิสระ (ข้อความธรรมดาที่คุณพิมพ์คือคำตอบที่ส่งถึงลูกค้า) แต่เมื่อต้องดำเนินการกับระบบ ให้ "เรียกเครื่องมือ" (function call) ต่อไปนี้ — อย่าพิมพ์ผลลัพธ์เอง ระบบจะคืนผลให้แล้วคุณค่อยพูดต่อ:

- `verify_identity(last_4_digits)` — ตรวจสอบเลขบัตรประชาชน 4 ตัวท้ายกับ CRM **ต้องเรียกก่อนเปิดเผยข้อมูลหนี้ใด ๆ เสมอ (KYC)**
- `check_account_status()` — อ่านข้อมูลบัญชีปัจจุบัน (ยอดหนี้ วันครบกำหนด สถานะ). ถ้า `case_status` เป็น `pending_review` ห้ามเร่งรัดให้ชำระ ให้เสนอเลื่อนนัด/เปิดเรื่องตรวจสอบแทน; ถ้า `closed` ให้ขออภัยแล้วจบสาย
- `get_current_datetime()` — ขอวันที่มาตรฐานในรูปแบบ `YYYY-MM-DD (Weekday)` **เรียกก่อนพูดหรือบันทึกวันที่ใด ๆ ที่ไม่ใช่วันนี้เสมอ** แล้วนำค่าที่ได้ไปใช้แบบคัดลอกตรง ๆ
- `record_verbal_commitment(amount, date, channel)` — **ขั้นที่ 1** บันทึกคำมั่นด้วยวาจาของลูกค้า (ยังไม่เขียนลง CRM)
- `payment_date(last_4_digits, amount, date, channel)` — **ขั้นที่ 2 (เรียกทันทีหลัง record_verbal_commitment ด้วยค่าตรงกัน)** บันทึกการนัดชำระลง CRM
- `callback_datetime(last_4_digits, date)` — นัดวันเวลาที่ลูกค้าขอให้โทรกลับ

กฎสำคัญ:
1. ยืนยันตัวตน (`verify_identity`) ก่อนเปิดเผยยอดหนี้/ข้อมูลบัญชีเสมอ
2. วันที่ทุกค่าต้องมาจาก `get_current_datetime` และอยู่ในรูปแบบ `YYYY-MM-DD (Weekday)`
3. การปิดการชำระต้องทำตามลำดับ: `record_verbal_commitment` → `payment_date` (ค่า amount/date/channel ต้องตรงกัน) → แล้วจึงสรุปปิดสายด้วยข้อความ
4. `channel` ต้องเป็นหนึ่งใน: mobile_app, counter_service, branch, bank_transfer, atm, other
"""


def load_prescript_prompt(
    base: Path,
    company: str,
    agent_context_data: dict,
    prompt_variant: str | None = None,
) -> str:
    """Load company-specific pre-script prompt and fill [placeholder] tokens.

    Under V6_ACTIVE, prefers the v6_* instruction file; falls back to the v5
    file if the v6 sibling doesn't exist yet (lets v6 land incrementally).

    When `prompt_variant` matches a key in `REPLACEMENT_FILES`, that file is
    loaded as the ENTIRE system prompt instead of the per-company v6 base.
    Used by the demo's Qwen path: a short English imperative prompt drives
    Qwen more reliably than the long Thai v6 base. fill_template still runs
    so placeholders like [agent_name], [customer_name], [today] are
    substituted from agent_context_data. Simulator callers pass `None` →
    byte-identical to the pre-variant behavior.
    """
    if prompt_variant and prompt_variant in REPLACEMENT_FILES:
        replacement_path = base / PRESCRIPT_INSTRUCTION_DIR / REPLACEMENT_FILES[prompt_variant]
        if replacement_path.exists():
            return fill_template(replacement_path.read_text(encoding="utf-8"), agent_context_data)
        # Soft fall-through to the per-company base if the replacement file
        # is missing — keeps the demo running rather than crashing on a typo.

    if V6_ACTIVE:
        # Prompt version is selectable via AAX6_PROMPT_VERSION (default "v6"). e.g.
        # "v7" loads v7_communicator_instruction-{company}.md — lets an improved
        # prompt lineage coexist with v6 (the benchmark baseline) without editing
        # the v6 files in place. Falls back v7 -> v6 -> v5 if a file is missing.
        ver = (os.environ.get("AAX6_PROMPT_VERSION") or "v6").strip() or "v6"
        path = base / PRESCRIPT_INSTRUCTION_DIR / f"{ver}_communicator_instruction-{company}.md"
        if not path.exists() and ver != "v6":
            path = base / PRESCRIPT_INSTRUCTION_DIR / V6_INSTRUCTION_TEMPLATE.format(company=company)
        if not path.exists():
            # Graceful fallback during v6 development: v5 file is used if v6 sibling missing
            path = base / PRESCRIPT_INSTRUCTION_DIR / INSTRUCTION_TEMPLATE.format(company=company)
    else:
        path = base / PRESCRIPT_INSTRUCTION_DIR / INSTRUCTION_TEMPLATE.format(company=company)
    if not path.exists():
        raise FileNotFoundError(
            f"Pre-script instruction file not found for company '{company}': {path}"
        )

    return fill_template(path.read_text(encoding="utf-8"), agent_context_data)


def load_freeform_prompt(
    base: Path,
    company: str,
    agent_context_data: dict,
) -> str:
    """Build a free-form system prompt: raw body + context + statute."""
    path = base / FREEFORM_INSTRUCTION_DIR / INSTRUCTION_TEMPLATE.format(company=company)
    if not path.exists():
        raise FileNotFoundError(
            f"Free-form instruction file not found for company '{company}': {path}"
        )
    parts = [path.read_text(encoding="utf-8").rstrip()]

    # Backend action tools (KYC / payment / callback) — free-form gets the same
    # deterministic enforcement as pre-script while still writing its own Thai.
    parts.append(FREEFORM_TOOLS_BLOCK.rstrip())

    ctx_lines = ["", "## ข้อมูลลูกค้า (Customer Context)"]
    for k, v in agent_context_data.items():
        ctx_lines.append(f"- {k}: {v}")
    parts.append("\n".join(ctx_lines))

    statute = (base / COMPLIANCE_FILE).read_text(encoding="utf-8").rstrip()
    parts.append(
        "\n## กฎหมายอ้างอิง — พระราชบัญญัติการทวงถามหนี้ พ.ศ. ๒๕๕๘\n" + statute
    )

    return "\n\n".join(parts) + "\n"
