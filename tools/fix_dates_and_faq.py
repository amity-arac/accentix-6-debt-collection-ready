#!/usr/bin/env python3
"""Three data/content corrections agreed with the product owner.

    python3 tools/fix_dates_and_faq.py [--dry-run]

1. WRONG WEEKDAYS IN THE FIXTURES. `2026-05-15 (Thursday)` is a Friday and
   `2026-05-20 (Tuesday)` is a Wednesday. Speech survives — fill_template silently
   repairs the weekday — but a model that echoes the CRM's own `due_date` into
   record_verbal_commitment / payment_date / callback_datetime gets it rejected as
   `date_format_invalid`, i.e. the CRM hands the agent a value its own backend will
   not accept. The ISO date is the fact; the weekday is derived, so recompute it.

2. "ครบกำหนดชำระวันนี้" ON AN ACCOUNT MONTHS OVERDUE. `{{if due_upcoming}}…{{else}}
   ครบกำหนดชำระวันนี้{{/if}}` is a two-way branch over three real situations
   (upcoming / due today / overdue), and `due_upcoming` is set by nothing outside
   the demo session — so the else branch fires by default and every AEON call
   claimed the balance was due TODAY. The fix needs no new field: the else branch
   states the actual due date, which is true whether the date is today or past.

3. SKL'S OUT-OF-SCOPE LIST WAS CREDIT-CARD TOPICS. SKL is hire-purchase leasing;
   `credit_limit` / `card_available` / `reduce_balance` are meaningless there, while
   the questions a leasing customer actually asks (early settlement, late fees, the
   registration book, repossession, the guarantor) were absent. That list is what
   the renderer prints as the model's out-of-scope classifier.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
DRY = "--dry-run" in sys.argv
DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) \((\w+)\)$")

SKL_OUT_OF_SCOPE = ("นอกขอบเขต: ปิดบัญชีก่อนกำหนด/ยอดปิดบัญชี, ค่าปรับล่าช้า/ดอกเบี้ยผิดนัด, "
                    "โอนเล่มทะเบียน/ไฟแนนซ์หมด, ยึดรถ/คืนรถ, เปลี่ยนผู้เช่าซื้อ/ผู้ค้ำประกัน, "
                    "ขอลดค่างวด/ปรับโครงสร้างหนี้, ขอใบเสร็จ/ใบกำกับภาษี")


def fix_weekday(value: str) -> tuple[str, bool]:
    m = DATE_RE.match(str(value or ""))
    if not m:
        return value, False
    iso, said = m.groups()
    try:
        real = dt.date.fromisoformat(iso).strftime("%A")
    except ValueError:
        return value, False
    if real == said:
        return value, False
    return f"{iso} ({real})", True


def walk(obj, path=""):
    """Yield (container, key, value) for every string that looks like a canonical date."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str):
                yield obj, k, v, f"{path}.{k}"
            else:
                yield from walk(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, str):
                yield obj, i, v, f"{path}[{i}]"
            else:
                yield from walk(v, f"{path}[{i}]")


def fix_dates() -> list[str]:
    notes = []
    targets = [REPO / "data" / "test-cases" / "_builder_personas.json",
               REPO / "tools" / "gen_mockoon.py"]
    for path in targets:
        if path.suffix == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
            changed = False
            for container, key, value, where in walk(data):
                fixed, did = fix_weekday(value)
                if did:
                    container[key] = fixed
                    notes.append(f"{path.name}{where}: {value} -> {fixed}")
                    changed = True
            if changed and not DRY:
                path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")
        else:   # the generator's inline fixtures
            text = path.read_text(encoding="utf-8")
            out = text
            for m in re.finditer(r'"(\d{4}-\d{2}-\d{2}) \((\w+)\)"', text):
                fixed, did = fix_weekday(f"{m.group(1)} ({m.group(2)})")
                if did:
                    out = out.replace(m.group(0), f'"{fixed}"')
                    notes.append(f"{path.name}: {m.group(0)} -> \"{fixed}\"")
            if out != text and not DRY:
                path.write_text(out, encoding="utf-8")
    return notes


def fix_due_branch() -> list[str]:
    notes = []
    for path in (REPO / "data" / "pre-scripts").glob("*.json"):
        rows = json.loads(path.read_text(encoding="utf-8"))
        changed = False
        for r in rows:
            t = r.get("template") or ""
            if "{{else}}ครบกำหนดชำระวันนี้{{/if}}" in t:
                r["template"] = t.replace("{{else}}ครบกำหนดชำระวันนี้{{/if}}",
                                          "{{else}}ครบกำหนดชำระ {due_date} {{/if}}")
                notes.append(f"{path.name} {r['text_id']}: else-branch states the real "
                             f"due date instead of claiming 'วันนี้'")
                changed = True
        if changed and not DRY:
            path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
    return notes


def fix_skl_faq() -> list[str]:
    path = REPO / "data" / "flows" / "SKL-outbound-remind.json"
    spec = json.loads(path.read_text(encoding="utf-8"))
    notes = []
    for r in (spec.get("faq_routing") or {}).get("routes", []):
        if r.get("intent") == "out_of_scope" and r.get("desc") != SKL_OUT_OF_SCOPE:
            r["desc"] = SKL_OUT_OF_SCOPE
            notes.append("out_of_scope: credit-card topics -> hire-purchase topics")
    if notes and not DRY:
        path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    return notes


def main() -> None:
    for title, fn in (("weekdays", fix_dates),
                      ("due-date branch", fix_due_branch),
                      ("SKL out-of-scope", fix_skl_faq)):
        print(f"\n{title}")
        for n in fn() or ["   (nothing to change)"]:
            print("   -", n if n.strip().startswith("(") is False else n)
    print("\n(dry run — nothing written)" if DRY else "\nwritten")


if __name__ == "__main__":
    main()
