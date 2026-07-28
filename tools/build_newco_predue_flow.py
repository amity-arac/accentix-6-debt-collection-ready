#!/usr/bin/env python3
"""Author NEWCO's flow as a PRE-DUE courtesy-reminder + autopay-enrollment flow —
deliberately different from the outbound-overdue-collection flow every other
company shares. Writes spec + catalog, validates, prints a summary."""
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from demo.server.flow.flowspec import validate_flow_spec  # noqa

CO = "NEWCO"
DISPLAY = "นิวโค"
LIB = json.loads((ROOT / "data/flows/intent_cues.json").read_text(encoding="utf-8"))

# ---- catalog templates: fine_state -> (thai, is_closer, is_demand, expects_response) ----
T = {
    # opening
    "greet_verify":  ("สวัสดี{suffix} {pronoun}ติดต่อจากบริษัทนิวโค กำลังเรียนสายอยู่กับคุณ {customer_name} ถูกต้องหรือไม่{q_suffix}", 0, 0, 1),
    "verify_name":   ("ไม่ทราบว่า {pronoun}กำลังเรียนสายอยู่กับคุณ {customer_name} ถูกต้องหรือไม่{q_suffix}", 0, 0, 1),
    "third_party":   ("ขออภัยที่รบกวน{suffix} ไม่ทราบว่าคุณมีเบอร์ติดต่อคุณ {customer_name} หรือไม่{q_suffix}", 0, 0, 1),
    # main — pre-due reminder + autopay pitch
    "courtesy_remind": ("{pronoun}ติดต่อมาเพื่อแจ้งเตือนล่วงหน้า{suffix} ยอด {amount} บาท จะครบกำหนดชำระวันที่ {due_date} {suffix}", 0, 0, 0),
    "ask_autopay":   ("เพื่อความสะดวก ไม่ต้องกังวลว่าจะลืมชำระ ไม่ทราบว่าคุณลูกค้าสนใจสมัครบริการหักบัญชีอัตโนมัติไหม{q_suffix}", 0, 1, 1),
    "remind_due_date": ("ไม่เป็นไร{suffix} รบกวนคุณลูกค้าชำระภายในวันที่ {due_date} นะ{q_suffix}", 0, 0, 0),
    "ask_confirm_pay": ("ไม่ทราบว่าคุณลูกค้าสะดวกชำระด้วยตนเองภายในกำหนดใช่ไหม{q_suffix}", 0, 1, 1),
    # close
    "confirm_autopay": ("รับทราบ{suffix} {pronoun}จะดำเนินการสมัครบริการหักบัญชีอัตโนมัติให้ ระบบจะหักยอด {amount} บาทในวันที่ {due_date} ให้โดยอัตโนมัติ{suffix}", 1, 0, 0),
    "confirm_info":  ("คุณลูกค้าได้รับข้อมูลครบถ้วนแล้วนะ{q_suffix}", 0, 0, 0),
    "close":         ("ขอบคุณที่ให้เวลา{suffix} สวัสดี{suffix}", 1, 0, 0),
    "offer_callback": ("ได้{suffix} {pronoun}ขออนุญาตติดต่อกลับใหม่ในเวลาที่สะดวกนะ{q_suffix} ขอบคุณ{suffix}", 0, 0, 0),
    "apology":       ("ขออภัยในความไม่สะดวก{suffix}", 0, 0, 0),
    # faq / aux
    "ai_disclosure": ("{pronoun}เป็นผู้ช่วยอัตโนมัติจากบริษัทนิวโค{suffix} ติดต่อมาเพื่อแจ้งเตือนกำหนดชำระ{suffix}", 0, 0, 0),
    "faq_caller":    ("{pronoun}ติดต่อจากบริษัทนิวโค เป็นผู้ช่วยอัตโนมัติ เรื่องแจ้งเตือนกำหนดชำระของคุณลูกค้า{suffix}", 0, 0, 0),
    "faq_repeat":    ("ได้{suffix} ยอด {amount} บาท จะครบกำหนดวันที่ {due_date} {suffix}", 0, 0, 0),
    "faq_amount":    ("ยอดที่จะครบกำหนดคือ {amount} บาท{suffix}", 0, 0, 0),
    "faq_due":       ("ครบกำหนดชำระวันที่ {due_date} {suffix}", 0, 0, 0),
    "faq_autopay_how": ("บริการหักบัญชีอัตโนมัติคือ ระบบจะตัดยอดจากบัญชีที่ผูกไว้ในวันครบกำหนดให้โดยอัตโนมัติ คุณลูกค้าไม่ต้องโอนเอง{suffix}", 0, 0, 0),
    "offer_channel": ("ชำระได้ผ่านแอปนิวโค เคาน์เตอร์เซอร์วิส หรือโอนผ่านธนาคาร{suffix}", 0, 0, 0),
    "other":         ("ไม่ทราบว่าคุณลูกค้าสะดวกแบบไหน{q_suffix}", 0, 0, 1),
    "faq_handoff":   ("ได้{suffix} {pronoun}จะโอนสายให้เจ้าหน้าที่ติดต่อกลับนะ{q_suffix}", 1, 0, 0),
}

def cat_entry(fs, tid):
    thai, closer, demand, expects = T[fs]
    return {"company": CO, "text_id": tid, "intent_name": fs, "category": "A",
            "state": fs.split("_")[0], "template": thai, "is_closer": bool(closer),
            "is_demand": bool(demand), "is_acknowledgment": False,
            "expects_response": bool(expects), "_fine_state": fs}

catalog = [cat_entry(fs, 3000 + i) for i, fs in enumerate(T)]

# ---- events (reuse cue library where names match; author the autopay-specific ones) ----
def ev(desc, cues): return {"desc": desc, "cues": cues}
events = {
    "name_confirmed":    ev("ยืนยันว่าใช่ตัวลูกค้า",
                            ["ใช่", "ใช่ครับ", "ใช่ค่ะ", "ครับ", "ค่ะ", "ผมเอง", "ดิฉันเอง",
                             "พูดอยู่", "กำลังเรียนสาย", "ใช่ผมเอง", "ผมนี่แหละ", "ใช่ ผมเองครับ"]
                            + LIB["name_confirmed"]),
    "no_input":          ev("เงียบ/ฟังไม่ชัด/ไม่ตอบ", LIB["no_input"]),
    "third_party":       ev("คนรับสายไม่ใช่ตัวลูกค้า", LIB["third_party"]),
    "gives_new_phone":   ev("ให้เบอร์ใหม่/บอกให้ติดต่อที่อื่น", LIB["gives_new_phone"]),
    "refuses":           ev("ปฏิเสธ/ไม่ให้ความร่วมมือ", LIB["refuses"]),
    "reschedule_request": ev("ขอให้ติดต่อกลับทีหลัง", LIB["reschedule_request"]),
    "stop_signal":       ev("ขอไม่ให้โทรมาอีก (DNC)", LIB["stop_signal"]),
    "will_pay_manually": ev("จะชำระเอง ไม่เอา autopay", LIB["agrees_to_pay"]),
    "already_autopay":   ev("หักอัตโนมัติอยู่แล้ว/สมัครไว้แล้ว", LIB["already_paid"]),
    "agrees_autopay":    ev("ตกลงสมัครหักบัญชีอัตโนมัติ",
                            ["สมัครเลย", "เอาสิ", "เอาค่ะ", "โอเคหักได้", "ให้หักเลย", "สะดวกหักอัตโนมัติ",
                             "ตกลงหักบัตร", "สมัคร autopay", "ดีเลยสมัครให้หน่อย", "เอา ผูกบัญชีเลย"]),
    "declines_autopay":  ev("ไม่สมัคร autopay แต่ยังคุยต่อ",
                            ["ไม่สมัคร", "ไม่เอา", "ขอจ่ายเอง", "ไม่หักดีกว่า", "ไม่สะดวกให้หัก",
                             "ยังไม่เอา", "ขอคิดดูก่อน", "ไม่อยากผูกบัญชี", "ไว้ก่อน"]),
}

def st(sid, phase, tmpls, on=None, initial=False, terminal=False, entry_tools=None, outcome=None, note=None):
    d = {"id": sid, "phase": phase, "templates": [{"fine_state": t} for t in tmpls]}
    if initial: d["initial"] = True
    if terminal: d["terminal"] = True
    if entry_tools: d["entry_tools"] = entry_tools
    if note: d["note"] = note
    d["on"] = [{"event": e, "to": t} for e, t in (on or [])]
    if outcome: d["outcome"] = outcome
    return d

states = [
    st("greet", "opening", ["greet_verify"], initial=True, on=[
        ("name_confirmed", "remind"), ("no_input", "verify_retry"), ("third_party", "third_party")]),
    st("verify_retry", "opening", ["verify_name"], on=[
        ("name_confirmed", "remind"), ("third_party", "third_party"), ("no_input", "close_unreachable")]),
    st("third_party", "opening", ["third_party"], on=[
        ("gives_new_phone", "close_unreachable"), ("no_input", "close_unreachable"), ("refuses", "close_unreachable")]),
    st("remind", "main", ["courtesy_remind", "ask_autopay"],
       note="แจ้งเตือนล่วงหน้า (ยังไม่ครบกำหนด) แล้วเสนอสมัครหักบัญชีอัตโนมัติ", on=[
        ("agrees_autopay", "enroll_autopay"), ("already_autopay", "close_already"),
        ("will_pay_manually", "manual_ptp"), ("declines_autopay", "offer_reminder"),
        ("reschedule_request", "callback_close"), ("stop_signal", "close_optout"),
        ("no_input", "close_confirm_info")]),
    st("offer_reminder", "main", ["remind_due_date", "ask_confirm_pay"],
       note="ลูกค้าไม่เอา autopay → ย้ำวันครบกำหนด + ยืนยันว่าจะชำระเอง", on=[
        ("will_pay_manually", "manual_ptp"), ("agrees_autopay", "enroll_autopay"),
        ("stop_signal", "close_optout"), ("no_input", "close_confirm_info")]),
    # terminals
    st("enroll_autopay", "close", ["confirm_autopay", "close"], terminal=True,
       entry_tools=["record_verbal_commitment", "payment_date"],
       outcome={"result": "ptp", "reasons": ["autopay"]}),
    st("manual_ptp", "close", ["confirm_info", "close"], terminal=True,
       entry_tools=["record_verbal_commitment", "payment_date"],
       outcome={"result": "ptp", "reasons": ["manual"]}),
    st("close_already", "close", ["close"], terminal=True,
       outcome={"result": "ptp", "reasons": ["autopay_active"]}),
    st("callback_close", "close", ["offer_callback"], terminal=True, entry_tools=["callback_datetime"],
       outcome={"result": "tcb", "reasons": ["callback"]}),
    st("close_optout", "close", ["close"], terminal=True,
       outcome={"result": "refused", "reasons": ["dnc"]}),
    st("close_unreachable", "close", ["close", "apology"], terminal=True,
       outcome={"result": "unreachable", "reasons": ["other_person", "wrong_number", "no_input"]}),
    st("close_confirm_info", "close", ["confirm_info"], terminal=True,
       outcome={"result": "reached", "reasons": ["no_input"]}),
]

# reuse AEON's tool declarations verbatim (so impl matches the live backend), retarget the gate
aeon = json.loads((ROOT / "data/flows/AEON-outbound-remind.json").read_text(encoding="utf-8"))
tools = json.loads(json.dumps(aeon["tools"]))
for d in tools["declarations"]:
    if d["name"] == "check_account_status":
        d["gating"] = {"after_event": "name_confirmed", "required_before_state": "remind",
                       "max_calls_per_conversation": 1}

def route(intent, desc, tmpls, then=None, outcome=None):
    r = {"intent": intent, "desc": desc, "templates": [{"fine_state": t} for t in tmpls]}
    r["then"] = {"outcome": outcome, "terminal": True} if outcome else (then or "resume")
    return r

faq_routing = {"note": "FAQ ระหว่างสาย: ตอบแล้วกลับเข้า flow เดิม (resume) เว้น agent/wrong_name ที่ปิดสาย",
    "routes": [
        route("caller", "โทรมาจากไหน", ["faq_caller"]),
        route("bot", "เป็นบอท/AI ใช่ไหม", ["ai_disclosure"]),
        route("repeat", "ขอฟังยอด/วันซ้ำ", ["faq_repeat"]),
        route("amount", "ถามยอด", ["faq_amount"]),
        route("due", "ถามวันครบกำหนด", ["faq_due"]),
        route("autopay_how", "หักอัตโนมัติทำงานยังไง", ["faq_autopay_how"]),
        route("channel", "ถามช่องทางชำระ", ["offer_channel"]),
        route("agent", "ขอคุยเจ้าหน้าที่", ["faq_handoff"], outcome={"result": "tcb", "reasons": ["agent"]}),
        route("out_of_scope", "นอกขอบเขต", ["other"]),
    ]}

spec = {
    "spec_version": 2, "flow_id": f"{CO}-predue-autopay", "company": CO,
    "description": f"Flow แตกต่าง — {DISPLAY} pre-due courtesy reminder + autopay enrollment (ไม่ใช่ทวงยอดค้าง).",
    "role": "บอทโทรแจ้งเตือนล่วงหน้าก่อนครบกำหนด + ชวนสมัครหักบัญชีอัตโนมัติ (สุภาพ ไม่ทวง)",
    "catalog": f"data/pre-scripts/{CO.lower()}_predue_catalog.json",
    "goal": "ทักทาย/ยืนยันชื่อ → แจ้งเตือนล่วงหน้าว่าใกล้ครบกำหนด → เสนอสมัครหักบัญชีอัตโนมัติ → (ไม่เอา) ยืนยันชำระเอง → ปิดสายสุภาพ",
    "crm_fields": ["today", "customer_name", "amount", "due_date", "due_status", "company_phone"],
    "events": events, "tools": tools, "states": states, "faq_routing": faq_routing,
    "auxiliary_templates": {"note": "template ตามบริบท", "allowed": [
        {"fine_state": "offer_channel", "desc": "บอกช่องทางชำระ"},
        {"fine_state": "faq_autopay_how", "desc": "อธิบายวิธีหักอัตโนมัติ"},
        {"fine_state": "other", "desc": "ทั่วไป"},
        {"fine_state": "apology", "desc": "ขออภัยตามบริบท"},
        {"fine_state": "ai_disclosure", "desc": "แจ้งว่าเป็นระบบอัตโนมัติ"}]},
    "constraints": [
        {"id": "remind_once", "type": "once_per_call", "template_fine_state": "courtesy_remind",
         "enforce": ["prompt"], "desc": "แจ้งเตือนยอด/วันครบกำหนดครั้งเดียวต่อสาย — ถามซ้ำใช้ faq_repeat"},
        {"id": "repeat_only_on_no_input", "type": "repeat_only_on", "event": "no_input",
         "enforce": ["prompt"], "desc": "พูดซ้ำได้เฉพาะตอนลูกค้าเงียบ/ไม่ตอบ"}],
    "outcomes": {"required_at_close": True, "results": {
        "ptp": {"reasons": ["autopay", "manual", "autopay_active"],
                "desc": "ผลบวก — สมัครหักอัตโนมัติ / รับปากชำระเอง / มีหักอัตโนมัติอยู่แล้ว"},
        "tcb": {"reasons": ["callback", "agent"], "desc": "ขอให้ติดต่อกลับ / ขอเจ้าหน้าที่"},
        "refused": {"reasons": ["dnc"], "desc": "ขอไม่ให้ติดต่ออีก (DNC)"},
        "unreachable": {"reasons": ["other_person", "wrong_number", "no_input"], "desc": "ไม่ถึงตัวลูกค้า"},
        "reached": {"reasons": ["no_input"], "desc": "ได้รับข้อมูลแล้วปิดสาย"}}},
}

errs, _ = validate_flow_spec(spec, catalog)
if errs:
    print("VALIDATION ERRORS:")
    for e in errs: print("  -", e)
    sys.exit(1)

(ROOT / f"data/flows/{CO}-predue-autopay.json").write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
(ROOT / f"data/pre-scripts/{CO.lower()}_predue_catalog.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"OK — spec {len(states)} states / {len(events)} events, catalog {len(catalog)} templates. Validation clean.")
print("states:", " ".join(s["id"] for s in states))
