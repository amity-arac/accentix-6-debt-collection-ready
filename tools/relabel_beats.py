#!/usr/bin/env python3
"""แยก beat ที่ชื่อเดียวแต่ครอบหลายเรื่อง ให้ชื่อตรงกับสิ่งที่ประโยคนั้นพูดจริง

ปัญหา: catalog หลายบรรทัดใช้ `_fine_state` เดียวกันทั้งที่พูดคนละเรื่อง — `other` มีทั้ง
"ได้ค่ะ ถ้าสะดวกแล้วแจ้งนะคะ" และ "02-035-6666 ค่ะ" ซึ่งใช้แทนกันไม่ได้เลย ผลคือ
spec สั่ง beat หนึ่ง แล้วโมเดลพูดอีกเรื่องได้โดยไม่ผิดกฎ, eval นับว่าผ่าน, และ reward
ให้คะแนนเท่ากันทุกสำนวน — ทั้งสามชั้นตัดสินจากชื่อที่ไม่ได้แปลว่าอะไร

การแยกทำสองอย่างพร้อมกัน: เปลี่ยนชื่อใน catalog และผูกชื่อใหม่เข้า spec ไม่งั้น
template จะกลายเป็นของกำพร้าที่ไม่มี state ไหนเรียกถึง

    python3 tools/relabel_beats.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]

# text_id -> ชื่อใหม่ (อ่านทีละบรรทัดแล้วจัดกลุ่มด้วยมือ — วิธีอัตโนมัติด้วยความคล้ายตัวอักษร
# ให้ผลผิดทั้งสองทาง: ตัดสินสำนวนซ้ำของ AMT ว่าคนละเรื่อง และเกือบปล่อย third_party ผ่าน)
RELABEL: dict[str, dict[int, str]] = {
    "v11_aeon_probe_catalog.json": {
        # verify_name เดิม 7 สำนวน = 4 เรื่อง
        1004: "third_party_know",       # "คุณรู้จักคุณ X หรือไม่" — ถามคนอื่น ไม่ใช่ยืนยันตัวเจ้าตัว
        1006: "third_party_know",
        1007: "ai_disclosure_verify",   # เปิดเผยว่าเป็น AI แล้วค่อยยืนยันชื่อ
        1015: "ai_disclosure_verify",
        1016: "ask_convenient",         # "สะดวกคุยสายหรือยัง" — ไม่เกี่ยวกับการยืนยันชื่อ
        # ask_pay_today เดิม 9 สำนวน — 3 ตัวเป็นการโน้มน้าวหลังถูกปฏิเสธ ไม่ใช่การขอครั้งแรก
        # (1020/1039 ทวนยอดก่อนขอ — ยังเป็น "ขอให้จ่าย" อยู่ จึงคงชื่อเดิม)
        1041: "convince_pay",           # "เข้าใจในสถานการณ์ แต่..." — โน้มน้าวหลังถูกปฏิเสธ
        1043: "convince_pay",
        1044: "convince_pay",
        # other เดิม 4 สำนวน = 4 เรื่อง ไม่มีอันไหนเกี่ยวกันเลย
        1030: "ack_hold",               # "ได้ค่ะ ถ้าสะดวกแล้วแจ้งนะคะ"
        1032: "give_phone",             # เบอร์บริษัทเปล่าๆ
        1035: "thank_close",            # "ขอบคุณสำหรับข้อมูลนะคะ สวัสดีค่ะ"
        1038: "ask_callback_time",      # ซ้ำกับ beat ที่มีอยู่แล้ว (1056/1063)
        # apology เดิม 4 สำนวน = 3 เรื่อง (สัญญาคนละอย่าง)
        1036: "apology_contact",        # ขออภัย + ให้เบอร์ติดต่อ
        1054: "apology_close",          # ขออภัย + วางสาย
        # 1055/1059 = ขออภัย + จะติดต่อกลับ — เป็นสำนวนซ้ำกันจริง คงชื่อ apology ไว้
    },
}

# ชื่อใหม่ต้องมี state เรียกถึง ไม่งั้นเป็นของกำพร้า
REBIND: dict[str, dict] = {
    "AEON-outbound-remind.json": {
        # ถามว่ารู้จักไหม = ทางเลือกของ state third_party (ไม่ใช่ขั้นที่ต้องพูดเพิ่ม)
        "third_party": {"any_of": ["third_party", "third_party_know"]},
        # โน้มน้าวหลังถูกปฏิเสธ = ทางเลือกใน state convince
        "convince": {"add_any_of": "convince_pay"},
    },
    # ที่เหลือเป็นบทพูดแทรก ไม่ผูกกับ state ใด -> auxiliary_templates.allowed
    "_auxiliary": ["ai_disclosure_verify", "ask_convenient", "ack_hold",
                   "give_phone", "thank_close", "apology_contact", "apology_close"],
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    for cat_name, mapping in RELABEL.items():
        path = REPO / "data" / "pre-scripts" / cat_name
        cat = json.loads(path.read_text(encoding="utf-8"))
        changed = 0
        for e in cat:
            new = mapping.get(e.get("text_id"))
            if new and e.get("_fine_state") != new:
                print(f"  {e['text_id']}  {e['_fine_state']:18} -> {new:22} "
                      f"{str(e.get('template',''))[:52]}")
                e["_fine_state"] = new
                e["intent_name"] = new
                changed += 1
        print(f"{cat_name}: เปลี่ยนชื่อ {changed} บรรทัด")
        if not args.dry_run:
            path.write_text(json.dumps(cat, ensure_ascii=False, indent=1) + "\n",
                            encoding="utf-8")

    spec_path = REPO / "data" / "flows" / "AEON-outbound-remind.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    for st in spec["states"]:
        rule = REBIND["AEON-outbound-remind.json"].get(st["id"])
        if not rule:
            continue
        if "any_of" in rule:
            st["templates"] = [{"any_of": rule["any_of"]}]
        elif "add_any_of" in rule:
            for t in st["templates"]:
                if t.get("fine_state") == "convince_other":
                    t.pop("fine_state")
                    t["any_of"] = ["convince_other", rule["add_any_of"]]
        print(f"  ผูกใหม่ state {st['id']}: {st['templates']}")

    aux = spec.setdefault("auxiliary_templates", {}).setdefault("allowed", [])
    have = {t.get("fine_state") for t in aux}
    for fs in REBIND["_auxiliary"]:
        if fs not in have:
            aux.append({"fine_state": fs})
            print(f"  เพิ่ม auxiliary: {fs}")
    if not args.dry_run:
        spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")


if __name__ == "__main__":
    main()
