#!/usr/bin/env python3
"""รวม spec + catalog ของแต่ละบริษัทให้เหลือไฟล์เดียว — รูปแบบเดียวกับที่ Builder สร้าง

เดิม 4 บริษัทที่มากับระบบเป็นแบบแยก (spec ไฟล์หนึ่ง catalog อีกไฟล์ registry ชี้ทั้งคู่)
ส่วนบริษัทที่สร้างใหม่จาก Builder เป็นไฟล์เดียว (`catalog_inline`) — สองรูปแบบในระบบเดียว
แปลว่าคนที่แก้ไฟล์ต้องรู้ก่อนว่าบริษัทนี้เป็นแบบไหน

พร้อมกันนี้ตัดกลไก version override ทิ้ง: canonical ของ AEON เขียน
`instruction_version: "v11.2"` ไว้ ทำให้ทุกครั้งที่โหลดมันวิ่งไปอ่าน
`AEON-outbound-remind__v11.2.json` แทนตัวเอง — แก้ตัว canonical ไปเท่าไหร่ก็ไม่มีผล
มีแค่ AEON บริษัทเดียวที่ใช้กลไกนี้

    python3 tools/consolidate_companies.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
FLOWS = REPO / "data" / "flows"
REG = FLOWS / "flow_registry.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    sys.path.insert(0, str(REPO))
    from demo.server.flow.flowspec import normalize_catalog, validate_flow_spec, validate_strict

    reg = json.loads(REG.read_text(encoding="utf-8"))
    new_reg: dict = {}
    for company, entry in reg.items():
        spec_path = FLOWS / entry["spec"]
        spec = json.loads(spec_path.read_text(encoding="utf-8"))

        # เอา override ออกก่อน แล้วอ่านจากตัว canonical จริงๆ
        pinned = spec.pop("instruction_version", None)

        if spec.get("catalog_inline") is not None:
            catalog = spec["catalog_inline"]
        else:
            catalog = json.loads(
                (REPO / "data" / "pre-scripts" / entry["catalog"]).read_text(encoding="utf-8"))
        catalog = normalize_catalog(catalog, spec)

        spec["catalog"] = "__inline__"
        spec["catalog_inline"] = catalog

        errs, warns = validate_flow_spec(spec, catalog)
        errs += validate_strict(spec, catalog)
        out = FLOWS / f"{company}.company.json"
        status = "OK" if not errs else f"ERROR {errs[:2]}"
        print(f"  {company:6} {len(spec['states']):2} states · {len(catalog):3} templates "
              f"-> {out.name:24} {status}"
              + (f"  (ตัด override {pinned})" if pinned else ""))
        for w in warns[:2]:
            print(f"         เตือน: {w}")
        if errs:
            continue
        if not args.dry_run:
            out.write_text(json.dumps(spec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        new_reg[company] = {"spec": out.name, "display_name": entry.get("display_name", company)}
        if entry.get("builder"):
            new_reg[company]["builder"] = True

    if not args.dry_run and len(new_reg) == len(reg):
        REG.write_text(json.dumps(new_reg, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"\nregistry: เหลือ key เดียวต่อบริษัท (spec) — ไม่มี catalog แล้ว")


if __name__ == "__main__":
    main()
