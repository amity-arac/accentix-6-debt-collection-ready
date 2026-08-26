#!/usr/bin/env python3
"""Fix the utterance defects the catalog audit found, across every shipped catalog.

    python3 tools/fix_catalogs.py [--dry-run]

These are things a customer HEARS, so each one is quoted in the audit with the
rendered string it produced. Mechanical and reversible; anything needing new Thai
copy is deliberately left out and handled per-company.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "data" / "pre-scripts"
DRY = "--dry-run" in sys.argv

# A company that identifies itself by a name hardcoded in its templates cannot be
# corrected from CRM data. NOTE: the SKL templates were RIGHT — it really is
# สยามคูโบต้าลีสซิ่ง; the registry and persona rows said "ส่งเสริมลีสซิ่ง" and were
# the wrong ones (now corrected). The slot is still the better shape: the name now
# comes from the CRM row, so a second leasing customer needs no template edit. The
# rendered sentence is byte-identical to the hardcoded original.
BRAND_TO_SLOT = {
    "บริษัทสยามคูโบต้าลีสซิ่ง": "บริษัท[company_name]",
    "สยามคูโบต้าลีสซิ่ง": "[company_name]",
}

# Same inversion here: "บัตรเครดิต" was correct and the persona's loan_type
# ("สินเชื่อ") was wrong. Reading the product from the CRM row keeps the same
# sentence while letting one catalog serve a portfolio with more than one product.
PRODUCT_TO_SLOT = {
    "KBANK": [("ว่าบัตรเครดิต ที่ลงท้าย", "ว่า[loan_type] ที่ลงท้าย")],
}

# The renderer already turns a date into "วันศุกร์ที่ 15 พฤษภาคม 2026" and a time
# into "10:00 น.", so a template that also writes the unit says it twice:
# "ครบกำหนดชำระ วันที่ วันศุกร์ที่ 15 …", "…10:00 น. - 11:00 น. น.".
UNIT_DOUBLING = [
    (re.compile(r"วันที่\s*(\[|\{)(due_date|appointment_date|callback_date|promised_date)"),
     r"\1\2"),
    (re.compile(r"(\[|\{)(visit_time_end|callback_time|appointment_time)(\]|\})\s*น\."),
     r"\1\2\3"),
]

# Money read aloud with no unit: "ยอดเรียกเก็บจำนวน 3000 ค่ะ". Every AEON equivalent
# says "บาท"; KBANK/SKL dropped it.
MONEY_SLOTS = ("amount", "minimum_payment", "total_amount_due", "minimum_payment_due",
               "promised_amount")


def add_baht(text: str) -> tuple[str, bool]:
    changed = False
    for slot in MONEY_SLOTS:
        for opener, closer in (("[", "]"), ("{", "}")):
            tok = f"{opener}{slot}{closer}"
            i = 0
            while (i := text.find(tok, i)) != -1:
                after = text[i + len(tok):]
                # Only where the sentence continues without a unit — never before
                # another word that already reads as one.
                if not after.lstrip().startswith(("บาท", "%")):
                    text = text[:i + len(tok)] + " บาท" + after
                    changed = True
                    i += len(tok) + 4
                else:
                    i += len(tok)
    return text, changed


def fix_catalog(path: pathlib.Path, company: str) -> list[str]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    notes: list[str] = []
    for r in rows:
        t = r.get("template")
        if not t:
            continue
        orig = t
        for brand, slot in BRAND_TO_SLOT.items():
            if brand in t:
                t = t.replace(brand, slot)
                notes.append(f"{r.get('text_id')}: brand {brand!r} -> {slot}")
        for old, new in PRODUCT_TO_SLOT.get(company, []):
            if old in t:
                t = t.replace(old, new)
                notes.append(f"{r.get('text_id')}: product -> [loan_type]")
        for rx, repl in UNIT_DOUBLING:
            t2 = rx.sub(repl, t)
            if t2 != t:
                notes.append(f"{r.get('text_id')}: dropped a doubled unit word")
                t = t2
        t, money = add_baht(t)
        if money:
            notes.append(f"{r.get('text_id')}: money slot += บาท")
        if t != orig:
            r["template"] = t
    if notes and not DRY:
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    return notes


def main() -> None:
    reg = json.loads((REPO / "data" / "flows" / "flow_registry.json").read_text())
    for company, entry in reg.items():
        path = SCRIPTS / entry["catalog"]
        notes = fix_catalog(path, company)
        print(f"\n{company}  {path.name}  ({len(notes)} change(s))")
        for n in notes:
            print("   -", n)
    print("\n(dry run — nothing written)" if DRY else "\nwritten")


if __name__ == "__main__":
    main()
