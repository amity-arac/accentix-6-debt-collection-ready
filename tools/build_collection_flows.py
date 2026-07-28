#!/usr/bin/env python3
"""Rebuild NEWCO + ABC as IN-FAMILY debt-collection flows (the goal = generalize
across debt-collection bots, not arbitrary topologies). Both reuse AEON's proven
outbound-remind graph (the flow model is trained on it → it adheres) but differ
where real collection flows differ: product, tone, catalog, and which recovery
levers the convince beats pull.
  NEWCO = telco postpaid overdue (lever: service suspension)
  ABC   = auto / hire-purchase overdue (lever: repossession + restructure)
"""
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from demo.server.flow.flowspec import validate_flow_spec  # noqa

aeon = json.loads((ROOT / "data/flows/AEON-outbound-remind.json").read_text(encoding="utf-8"))
aeon_cat = json.loads((ROOT / "data/pre-scripts/v10_pre_script_database_parameterized.json").read_text(encoding="utf-8"))
proto = {}
for e in aeon_cat:
    proto.setdefault(e["_fine_state"], e)

# fine_states AEON references (must all be present in each company's catalog)
USED = []
for s in aeon["states"]:
    for t in s.get("templates", []): USED.append(t["fine_state"])
for r in aeon.get("faq_routing", {}).get("routes", []):
    for t in r.get("templates", []): USED.append(t["fine_state"])
for t in aeon.get("auxiliary_templates", {}).get("allowed", []): USED.append(t["fine_state"])
USED = list(dict.fromkeys(USED))

NEWCO_TEXT = {
    "greet_verify": "สวัสดี{suffix} ติดต่อจากนิวโคโมบาย กำลังเรียนสายอยู่กับคุณ {customer_name} ใช่หรือไม่{q_suffix}",
    "verify_name": "ไม่ทราบว่า {pronoun}กำลังเรียนสายอยู่กับคุณ {customer_name} ใช่หรือไม่{q_suffix}",
    "third_party": "ขออภัยที่รบกวน{suffix} รบกวนฝากแจ้งคุณ {customer_name} ติดต่อกลับนิวโคโมบายด้วย{suffix}",
    "disclose_balance": "แจ้งยอดค่าบริการมือถือค้างชำระ {amount} บาท ครบกำหนดวันที่ {due_date} {suffix} หากเลยกำหนดอาจถูกระงับสัญญาณ ไม่ทราบว่าคุณลูกค้าสะดวกชำระภายในวันนี้ไหม{q_suffix}",
    "ask_pay_today": "ยอดค่าบริการค้างชำระ {amount} บาท{suffix} เพื่อไม่ให้ถูกระงับสัญญาณ ไม่ทราบว่าคุณลูกค้าสะดวกชำระภายในวันนี้เลยไหม{q_suffix}",
    "convince_lost_job": "{pronoun}เข้าใจสถานการณ์{suffix} แต่เพื่อไม่ให้เบอร์ถูกระงับ รบกวนคุณลูกค้าชำระบางส่วนก่อนได้ไหม{q_suffix}",
    "convince_sick": "{pronoun}เข้าใจสถานการณ์และขอให้หายไวๆ นะ{q_suffix} เพื่อไม่ให้ถูกระงับสัญญาณ รบกวนชำระก่อนได้ไหม{q_suffix}",
    "convince_other": "{pronoun}เข้าใจสถานการณ์{suffix} เพื่อรักษาเบอร์ไม่ให้ถูกระงับ รบกวนคุณลูกค้าชำระก่อนได้ไหม{q_suffix}",
    "probe_hardship": "คุณลูกค้าชำระได้ผ่านแอปนิวโคโมบาย เคาน์เตอร์เซอร์วิส หรือทรูมันนี่{suffix} รบกวนชำระเข้ามาภายในวันนี้ก่อนได้ไหม{q_suffix}",
    "confirm_info": "คุณลูกค้าได้รับข้อมูลครบถ้วนแล้วนะ{q_suffix}",
    "close": "ขอบคุณที่ใช้บริการนิวโคโมบาย{suffix} สวัสดี{suffix}",
    "offer_callback": "ได้{suffix} {pronoun}ขออนุญาตติดต่อใหม่ภายหลังนะ{q_suffix} ขอบคุณ{suffix}",
    "apology": "ขออภัยในความไม่สะดวกด้วย{suffix} หากต้องการสอบถามเพิ่มเติม ติดต่อ {company_phone}{suffix}",
    "faq_caller": "{pronoun}ติดต่อจากนิวโคโมบาย{suffix} เป็นผู้ช่วยอัตโนมัติ เรื่องค่าบริการค้างชำระ{suffix}",
    "ai_disclosure": "{pronoun}เป็นผู้ช่วยอัตโนมัติของนิวโคโมบาย{suffix} ติดต่อเรื่องค่าบริการมือถือค้างชำระ{suffix}",
    "faq_hold": "ได้{suffix} ถ้าสะดวกแล้วแจ้งนะ{q_suffix}",
    "faq_repeat": "ได้{suffix} ยอดค่าบริการค้างชำระ {amount} บาท ครบกำหนด {due_date} {suffix}",
    "handoff_refuse": "ขออภัยในความไม่สะดวก{suffix} ปัจจุบันยังไม่สามารถโอนสายเจ้าหน้าที่ได้นะ{q_suffix} ติดต่อ {company_phone}{suffix}",
    "faq_scam": "{pronoun}เป็นผู้ช่วยอัตโนมัติของนิวโคโมบายจริงๆ{suffix} ไม่ใช่มิจฉาชีพ หากไม่มั่นใจ ติดต่อ {company_phone} ได้{suffix}",
    "faq_annoyed": "ขออภัยที่รบกวนนะ{q_suffix} {pronoun}ขออนุญาตแจ้งเรื่องสำคัญสั้นๆ{suffix}",
    "offer_channel_only": "ชำระผ่านแอปนิวโคโมบาย เคาน์เตอร์เซอร์วิส หรือทรูมันนี่ได้เลย{suffix}",
    "faq_amount": "ยอดค่าบริการค้างชำระ {amount} บาท{suffix}",
    "faq_due": "ครบกำหนดชำระวันที่ {due_date} {suffix}",
    "faq_wrong_name": "ขออภัยด้วยนะ{q_suffix} รบกวนคุณลูกค้ายืนยันชื่ออีกครั้งได้ไหม{q_suffix}",
    "faq_mourning": "ขอแสดงความเสียใจด้วยนะ{q_suffix} {pronoun}ขออนุญาตบันทึกเรื่องและให้เจ้าหน้าที่ติดต่อกลับนะ{q_suffix}",
    "faq_faq_referral": "เรื่องนี้รบกวนติดต่อสอบถามที่ {company_phone} ได้{suffix}",
    "offer_channel": "ไม่ทราบว่าคุณลูกค้าชำระวันที่เท่าไหร่ ผ่านช่องทางไหน{suffix}",
    "other": "ได้{suffix} ถ้าสะดวกแล้วแจ้งนะ{q_suffix}",
}

ABC_TEXT = {
    "greet_verify": "สวัสดี{suffix} ติดต่อจากเอบีซี ลิสซิ่ง กำลังเรียนสายอยู่กับคุณ {customer_name} ใช่หรือไม่{q_suffix}",
    "verify_name": "ไม่ทราบว่า {pronoun}กำลังเรียนสายอยู่กับคุณ {customer_name} ใช่หรือไม่{q_suffix}",
    "third_party": "ขออภัยที่รบกวน{suffix} รบกวนฝากแจ้งคุณ {customer_name} ติดต่อกลับเอบีซี ลิสซิ่งด้วย{suffix}",
    "disclose_balance": "แจ้งยอดค่างวดรถค้างชำระ {amount} บาท ครบกำหนดวันที่ {due_date} {suffix} หากค้างหลายงวดอาจเข้าสู่กระบวนการยึดรถ ไม่ทราบว่าคุณลูกค้าสะดวกชำระภายในวันนี้ไหม{q_suffix}",
    "ask_pay_today": "ยอดค่างวดค้างชำระ {amount} บาท{suffix} เพื่อรักษาสิทธิ์ในรถ ไม่ทราบว่าคุณลูกค้าสะดวกชำระภายในวันนี้เลยไหม{q_suffix}",
    "convince_lost_job": "{pronoun}เข้าใจสถานการณ์{suffix} เพื่อไม่ให้รถถูกยึด รบกวนชำระค่างวดก่อนได้ไหม{q_suffix} หากจำเป็น เรามีบริการปรับโครงสร้างหนี้{suffix}",
    "convince_sick": "{pronoun}เข้าใจสถานการณ์และขอให้หายไวๆ นะ{q_suffix} เพื่อรักษารถไว้ รบกวนชำระก่อน หรือขอปรับโครงสร้างหนี้ได้{suffix}",
    "convince_other": "{pronoun}เข้าใจสถานการณ์{suffix} เพื่อไม่ให้เข้าสู่การยึดรถ รบกวนชำระก่อน หรือคุยเรื่องปรับโครงสร้างหนี้ได้{suffix}",
    "probe_hardship": "หากผ่อนไม่ไหว เรามีบริการปรับโครงสร้างหนี้หรือพักชำระได้{suffix} หรือชำระค่างวดผ่านแอปเอบีซี เคาน์เตอร์เซอร์วิส{suffix} ไม่ทราบว่าคุณลูกค้าสะดวกแบบไหน{q_suffix}",
    "confirm_info": "คุณลูกค้าได้รับข้อมูลครบถ้วนแล้วนะ{q_suffix}",
    "close": "ขอบคุณที่ใช้บริการเอบีซี ลิสซิ่ง{suffix} สวัสดี{suffix}",
    "offer_callback": "ได้{suffix} {pronoun}ขออนุญาตติดต่อใหม่ภายหลังนะ{q_suffix} ขอบคุณ{suffix}",
    "apology": "ขออภัยในความไม่สะดวกด้วย{suffix} หากต้องการสอบถามเพิ่มเติม ติดต่อ {company_phone}{suffix}",
    "faq_caller": "{pronoun}ติดต่อจากเอบีซี ลิสซิ่ง{suffix} เป็นผู้ช่วยอัตโนมัติ เรื่องค่างวดรถค้างชำระ{suffix}",
    "ai_disclosure": "{pronoun}เป็นผู้ช่วยอัตโนมัติของเอบีซี ลิสซิ่ง{suffix} ติดต่อเรื่องค่างวดรถค้างชำระ{suffix}",
    "faq_hold": "ได้{suffix} ถ้าสะดวกแล้วแจ้งนะ{q_suffix}",
    "faq_repeat": "ได้{suffix} ยอดค่างวดค้างชำระ {amount} บาท ครบกำหนด {due_date} {suffix}",
    "handoff_refuse": "ขออภัยในความไม่สะดวก{suffix} ปัจจุบันยังไม่สามารถโอนสายเจ้าหน้าที่ได้นะ{q_suffix} ติดต่อ {company_phone}{suffix}",
    "faq_scam": "{pronoun}เป็นผู้ช่วยอัตโนมัติของเอบีซี ลิสซิ่งจริงๆ{suffix} ไม่ใช่มิจฉาชีพ หากไม่มั่นใจ ติดต่อ {company_phone} ได้{suffix}",
    "faq_annoyed": "ขออภัยที่รบกวนนะ{q_suffix} {pronoun}ขออนุญาตแจ้งเรื่องสำคัญสั้นๆ{suffix}",
    "offer_channel_only": "ชำระค่างวดผ่านแอปเอบีซี เคาน์เตอร์เซอร์วิส หรือโอนผ่านธนาคารได้เลย{suffix}",
    "faq_amount": "ยอดค่างวดรถค้างชำระ {amount} บาท{suffix}",
    "faq_due": "ครบกำหนดชำระวันที่ {due_date} {suffix}",
    "faq_wrong_name": "ขออภัยด้วยนะ{q_suffix} รบกวนคุณลูกค้ายืนยันชื่ออีกครั้งได้ไหม{q_suffix}",
    "faq_mourning": "ขอแสดงความเสียใจด้วยนะ{q_suffix} {pronoun}ขออนุญาตบันทึกเรื่องและให้เจ้าหน้าที่ติดต่อกลับนะ{q_suffix}",
    "faq_faq_referral": "เรื่องนี้รบกวนติดต่อสอบถามที่ {company_phone} ได้{suffix}",
    "offer_channel": "ไม่ทราบว่าคุณลูกค้าชำระค่างวดวันที่เท่าไหร่ ผ่านช่องทางไหน{suffix}",
    "other": "ได้{suffix} ถ้าสะดวกแล้วแจ้งนะ{q_suffix}",
}

COMPANIES = [
    {"code": "NEWCO", "display": "นิวโคโมบาย", "flow_id": "NEWCO-collect-telco",
     "spec_file": "NEWCO-collect-telco.json", "cat_file": "newco_collect_catalog.json",
     "base_id": 3000, "text": NEWCO_TEXT,
     "role": "บอทโทรทวงค่าบริการมือถือ postpaid ค้างชำระ (โทนสั้น กระชับ; เลเวอเรจ = เสี่ยงระงับสัญญาณ)",
     "goal": "ทักทาย/ยืนยันชื่อ → แจ้งยอดค่าบริการค้าง → ชวนชำระวันนี้ (กันระงับสัญญาณ) → (ลังเล) โน้มน้าว → ปิดสาย/นัด",
     "desc": "นิวโคโมบาย — outbound ทวงค่าบริการมือถือ postpaid ค้างชำระ (โครงเดียวกับ AEON outbound-remind, คนละโปรดักต์/แคตตาล็อก)"},
    {"code": "ABC", "display": "เอบีซี ลิสซิ่ง", "flow_id": "ABC-collect-auto",
     "spec_file": "ABC-collect-auto.json", "cat_file": "abc_collect_catalog.json",
     "base_id": 4000, "text": ABC_TEXT,
     "role": "บอทโทรทวงค่างวดรถ/เช่าซื้อค้างชำระ (เลเวอเรจ = เสี่ยงยึดรถ + เสนอปรับโครงสร้างหนี้)",
     "goal": "ทักทาย/ยืนยันชื่อ → แจ้งยอดค่างวดค้าง → ชวนชำระวันนี้ (รักษาสิทธิ์ในรถ) → (ลังเล) โน้มน้าว/ปรับโครงสร้าง → ปิดสาย/นัด",
     "desc": "เอบีซี ลิสซิ่ง — outbound ทวงค่างวดรถ/เช่าซื้อค้างชำระ (โครงเดียวกับ AEON outbound-remind, คนละโปรดักต์/แคตตาล็อก)"},
]

for c in COMPANIES:
    missing = [fs for fs in USED if fs not in c["text"]]
    assert not missing, f"{c['code']} missing text for: {missing}"
    catalog, tid = [], c["base_id"]
    for fs in USED:
        p = proto.get(fs, {})
        catalog.append({
            "company": c["code"], "text_id": tid, "intent_name": p.get("intent_name", fs),
            "category": p.get("category", "A"), "state": p.get("state", fs.split("_")[0]),
            "template": c["text"][fs], "is_closer": p.get("is_closer", False),
            "is_demand": p.get("is_demand", False), "is_acknowledgment": p.get("is_acknowledgment", False),
            "expects_response": p.get("expects_response", True), "_fine_state": fs,
        })
        tid += 1
    spec = json.loads(json.dumps(aeon))
    spec["company"] = c["code"]
    spec["flow_id"] = c["flow_id"]
    spec["catalog"] = f"data/pre-scripts/{c['cat_file']}"
    spec["description"] = c["desc"]
    spec["role"] = c["role"]
    spec["goal"] = c["goal"]
    errs, _ = validate_flow_spec(spec, catalog)
    if errs:
        print(f"{c['code']} VALIDATION ERRORS:", *errs, sep="\n  - "); sys.exit(1)
    (ROOT / "data/flows" / c["spec_file"]).write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    (ROOT / "data/pre-scripts" / c["cat_file"]).write_text(json.dumps(catalog, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"OK {c['code']}: {c['spec_file']} + {c['cat_file']} ({len(catalog)} templates) — clean")
