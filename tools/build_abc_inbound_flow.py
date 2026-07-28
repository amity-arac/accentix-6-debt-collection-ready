#!/usr/bin/env python3
"""Author ABC's flow as an INBOUND customer-service triage line (hub-and-spoke) —
different from the outbound-overdue flow (AEON/JAI/KS/AIS) AND from NEWCO's
pre-due/autopay flow. Customer calls in; agent verifies, then a `hub` state
routes to spokes (pay / balance / installment / hardship / agent) and each spoke
returns to the hub until the caller is done."""
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from demo.server.flow.flowspec import validate_flow_spec  # noqa

CO, DISPLAY = "ABC", "เอบีซี"
LIB = json.loads((ROOT / "data/flows/intent_cues.json").read_text(encoding="utf-8"))

# fine_state -> (thai, is_closer, is_demand, expects_response)
T = {
    "greet_inbound": ("บริษัทเอบีซี สวัสดี{suffix} ยินดีให้บริการ{suffix} ขอทราบชื่อ-นามสกุลเพื่อยืนยันตัวตนก่อนนะ{q_suffix}", 0, 0, 1),
    "ask_help":      ("ไม่ทราบว่าให้{pronoun}ช่วยเรื่องใด{q_suffix}", 0, 1, 1),
    "ask_pay_detail": ("ได้{suffix} ไม่ทราบว่าคุณลูกค้าสะดวกชำระวันไหน จำนวนเท่าไหร่{q_suffix}", 0, 1, 1),
    "disclose_balance_inbound": ("ยอดคงค้างของคุณลูกค้าคือ {amount} บาท ครบกำหนดวันที่ {due_date} {suffix}", 0, 0, 0),
    "offer_installment": ("บริษัทมีบริการผ่อนชำระเป็นงวด{suffix} ไม่ทราบว่าคุณลูกค้าสนใจแบ่งจ่ายกี่งวด{q_suffix}", 0, 1, 1),
    "ack_hardship":  ("{pronoun}เข้าใจสถานการณ์ของคุณลูกค้า{suffix}", 0, 0, 0),
    "offer_options": ("เรามีทางเลือกช่วยเหลือ เช่น ผ่อนชำระ หรือเลื่อนกำหนด ไม่ทราบว่าคุณลูกค้าสะดวกแบบไหน{q_suffix}", 0, 1, 1),
    "confirm_info":  ("รับทราบ{suffix} {pronoun}บันทึกให้เรียบร้อยแล้ว{suffix}", 0, 0, 0),
    "close_thanks":  ("ขอบคุณที่ติดต่อบริษัทเอบีซี{suffix} สวัสดี{suffix}", 1, 0, 0),
    "offer_callback": ("ได้{suffix} {pronoun}จะให้เจ้าหน้าที่ติดต่อกลับในเวลาที่สะดวกนะ{q_suffix}", 0, 0, 0),
    "handoff_line":  ("สักครู่นะ{suffix} {pronoun}จะโอนสายให้เจ้าหน้าที่ดูแลต่อ{suffix}", 1, 0, 0),
    "ai_disclosure": ("{pronoun}เป็นผู้ช่วยอัตโนมัติของบริษัทเอบีซี{suffix} ยินดีให้บริการ{suffix}", 0, 0, 0),
    "faq_caller":    ("ที่นี่บริษัทเอบีซี ศูนย์บริการลูกค้า{suffix}", 0, 0, 0),
    "faq_amount":    ("ยอดคงค้างของคุณลูกค้าคือ {amount} บาท{suffix}", 0, 0, 0),
    "offer_channel": ("ชำระได้ผ่านแอปเอบีซี เคาน์เตอร์เซอร์วิส หรือโอนผ่านธนาคาร{suffix}", 0, 0, 0),
    "other":         ("ไม่ทราบว่าคุณลูกค้าสะดวกแบบไหน{q_suffix}", 0, 0, 1),
    "apology":       ("ต้องขออภัยด้วย{suffix}", 0, 0, 0),
}

def cat_entry(fs, tid):
    thai, closer, demand, expects = T[fs]
    return {"company": CO, "text_id": tid, "intent_name": fs, "category": "A",
            "state": fs.split("_")[0], "template": thai, "is_closer": bool(closer),
            "is_demand": bool(demand), "is_acknowledgment": False,
            "expects_response": bool(expects), "_fine_state": fs}
catalog = [cat_entry(fs, 4000 + i) for i, fs in enumerate(T)]

def ev(desc, cues): return {"desc": desc, "cues": cues}
events = {
    "name_confirmed":    ev("ยืนยันตัวตน/บอกชื่อ",
                            ["ใช่", "ครับ", "ค่ะ", "ผมเอง", "ดิฉันเอง", "ผมสมชายเอง", "ยืนยัน", "ใช่ครับ", "ใช่ค่ะ"] + LIB["name_confirmed"]),
    "wants_pay":         ev("ต้องการชำระเงิน",
                            ["จะจ่าย", "มาจ่าย", "จ่ายเงิน", "ขอชำระ", "โอนเงิน", "จะชำระหนี้", "อยากจ่าย"] + LIB["agrees_to_pay"][:6]),
    "asks_balance":      ev("ถามยอดคงค้าง",
                            ["ยอดเท่าไหร่", "เช็คยอด", "ค้างเท่าไหร่", "ขอเช็คยอด", "ยอดคงเหลือ", "สอบถามยอด", "ต้องจ่ายเท่าไหร่", "เหลือเท่าไหร่"]),
    "wants_installment": ev("ขอผ่อน/แบ่งจ่าย",
                            ["ขอผ่อน", "แบ่งจ่าย", "ผ่อนได้ไหม", "ขอผ่อนชำระ", "แบ่งงวด", "ผ่อนเป็นงวด", "ขอแบ่งชำระ", "จ่ายเป็นงวดได้ไหม"]),
    "reports_hardship":  ev("แจ้งว่าจ่ายลำบาก",
                            ["ไม่มีเงิน", "จ่ายไม่ไหว", "ตกงาน", "เงินไม่พอ", "ลำบาก", "ยังไม่มีจ่าย"] + LIB["hardship_other"][:4]),
    "wants_agent":       ev("ขอคุยเจ้าหน้าที่",
                            ["ขอคุยเจ้าหน้าที่", "ต่อคน", "ขอสายเจ้าหน้าที่", "คุยกับคน", "ขอพนักงาน", "ต่อเจ้าหน้าที่หน่อย", "อยากคุยกับคน"]),
    "done_no":           ev("ไม่มีอะไรเพิ่ม/จบสาย",
                            ["ไม่มีแล้ว", "พอแล้ว", "แค่นี้", "ไม่แล้วขอบคุณ", "หมดแล้ว", "เท่านี้", "ไม่มีอะไรแล้ว", "จบเลย"]),
    "ack":               ev("รับทราบ (กลับไปถามต่อ)",
                            ["รับทราบ", "โอเค", "อืม", "เข้าใจแล้ว", "ได้", "อ๋อ", "ครับผม", "ค่ะรับทราบ"]),
    "agrees_to_pay":     ev("รับปากจะชำระ", LIB["agrees_to_pay"]),
    "reschedule_request": ev("ขอให้ติดต่อกลับทีหลัง", LIB["reschedule_request"]),
    "refuses":           ev("ปฏิเสธ", LIB["refuses"]),
    "stop_signal":       ev("ขอไม่ให้ติดต่ออีก", LIB["stop_signal"]),
    "no_input":          ev("เงียบ/ไม่ตอบ", LIB["no_input"]),
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

HUB_ROUTES = [("wants_pay", "take_payment"), ("asks_balance", "balance_info"),
              ("wants_installment", "installment"), ("reports_hardship", "hardship_help"),
              ("wants_agent", "handoff")]

states = [
    st("greet_inbound", "opening", ["greet_inbound"], initial=True,
       note="รับสายเข้า + ยืนยันตัวตนก่อนเปิดเผยข้อมูลบัญชี", on=[
        ("name_confirmed", "hub"), ("stop_signal", "close_optout"), ("no_input", "close_confirm_info")]),
    st("hub", "main", ["ask_help"], entry_tools=["check_account_status"],
       note="ศูนย์กลาง (hub) — ถามว่าให้ช่วยเรื่องใด แล้วแตกไป spoke; กลับมาที่นี่หลังจบแต่ละเรื่อง",
       on=HUB_ROUTES + [("done_no", "close_thanks"), ("stop_signal", "close_optout"), ("no_input", "close_confirm_info")]),
    st("take_payment", "main", ["ask_pay_detail"], note="spoke: รับชำระ", on=[
        ("agrees_to_pay", "ptp_capture"), ("ack", "hub"), ("reschedule_request", "callback_close"),
        ("no_input", "close_confirm_info")]),
    st("balance_info", "main", ["disclose_balance_inbound"], note="spoke: แจ้งยอด แล้วกลับ hub", on=[
        ("ack", "hub"), ("agrees_to_pay", "ptp_capture"), ("wants_installment", "installment"),
        ("no_input", "close_confirm_info")]),
    st("installment", "main", ["offer_installment"], note="spoke: เสนอผ่อนชำระ", on=[
        ("agrees_to_pay", "ptp_capture"), ("refuses", "hub"), ("wants_agent", "handoff"),
        ("no_input", "close_confirm_info")]),
    st("hardship_help", "main", ["ack_hardship", "offer_options"], note="spoke: ช่วยเหลือกรณีจ่ายลำบาก", on=[
        ("agrees_to_pay", "ptp_capture"), ("wants_installment", "installment"),
        ("reschedule_request", "callback_close"), ("refuses", "hub"), ("no_input", "close_confirm_info")]),
    # terminals
    st("ptp_capture", "close", ["confirm_info", "close_thanks"], terminal=True,
       entry_tools=["record_verbal_commitment", "payment_date"],
       outcome={"result": "ptp", "reasons": ["ptp", "minimum"]}),
    st("handoff", "close", ["handoff_line"], terminal=True,
       outcome={"result": "tcb", "reasons": ["agent"]}),
    st("callback_close", "close", ["offer_callback"], terminal=True, entry_tools=["callback_datetime"],
       outcome={"result": "tcb", "reasons": ["callback"]}),
    st("close_thanks", "close", ["close_thanks"], terminal=True,
       outcome={"result": "reached", "reasons": ["done"]}),
    st("close_confirm_info", "close", ["confirm_info"], terminal=True,
       outcome={"result": "reached", "reasons": ["no_input"]}),
    st("close_optout", "close", ["close_thanks"], terminal=True,
       outcome={"result": "refused", "reasons": ["dnc"]}),
]

aeon = json.loads((ROOT / "data/flows/AEON-outbound-remind.json").read_text(encoding="utf-8"))
tools = json.loads(json.dumps(aeon["tools"]))
for d in tools["declarations"]:
    if d["name"] == "check_account_status":
        d["gating"] = {"after_event": "name_confirmed", "required_before_state": "hub",
                       "max_calls_per_conversation": 1}

def route(intent, desc, tmpls, then=None, outcome=None):
    r = {"intent": intent, "desc": desc, "templates": [{"fine_state": t} for t in tmpls]}
    r["then"] = {"outcome": outcome, "terminal": True} if outcome else (then or "resume")
    return r

faq_routing = {"note": "FAQ ระหว่างสาย: ตอบแล้ว resume; agent → ปิดโอนคน",
    "routes": [
        route("caller", "ที่นี่ที่ไหน", ["faq_caller"]),
        route("bot", "เป็นบอทไหม", ["ai_disclosure"]),
        route("amount", "ถามยอด", ["faq_amount"]),
        route("channel", "ช่องทางชำระ", ["offer_channel"]),
        route("agent", "ขอเจ้าหน้าที่", ["handoff_line"], outcome={"result": "tcb", "reasons": ["agent"]}),
        route("out_of_scope", "นอกขอบเขต", ["other"]),
    ]}

spec = {
    "spec_version": 2, "flow_id": f"{CO}-inbound-service", "company": CO,
    "description": f"Flow แตกต่าง — {DISPLAY} inbound customer-service triage (hub-and-spoke), ไม่ใช่ outbound.",
    "role": "รับสายลูกค้าโทรเข้า ศูนย์บริการ — ยืนยันตัวตน แล้วช่วยตามเรื่องที่ลูกค้าต้องการ (จ่าย/เช็คยอด/ผ่อน/ช่วยเหลือ/ต่อเจ้าหน้าที่)",
    "catalog": f"data/pre-scripts/{CO.lower()}_inbound_catalog.json",
    "goal": "รับสายเข้า → ยืนยันตัวตน → ถามว่าให้ช่วยเรื่องใด → แตกไปช่วยตามเรื่อง → วนกลับถามต่อจนจบ",
    "crm_fields": ["today", "customer_name", "amount", "due_date", "due_status", "company_phone"],
    "events": events, "tools": tools, "states": states, "faq_routing": faq_routing,
    "auxiliary_templates": {"note": "template ตามบริบท", "allowed": [
        {"fine_state": "offer_channel", "desc": "ช่องทางชำระ"},
        {"fine_state": "other", "desc": "ทั่วไป"},
        {"fine_state": "apology", "desc": "ขออภัย"},
        {"fine_state": "ai_disclosure", "desc": "แจ้งว่าเป็นระบบอัตโนมัติ"}]},
    "constraints": [
        {"id": "repeat_only_on_no_input", "type": "repeat_only_on", "event": "no_input",
         "enforce": ["prompt"], "desc": "พูดซ้ำได้เฉพาะตอนลูกค้าเงียบ"}],
    "outcomes": {"required_at_close": True, "results": {
        "ptp": {"reasons": ["ptp", "minimum"], "desc": "รับปากชำระ/ตกลงผ่อน"},
        "tcb": {"reasons": ["agent", "callback"], "desc": "โอนเจ้าหน้าที่/นัดโทรกลับ"},
        "refused": {"reasons": ["dnc"], "desc": "ขอไม่ให้ติดต่ออีก"},
        "reached": {"reasons": ["done", "no_input"], "desc": "ให้บริการเสร็จ/ได้ข้อมูลแล้ว"}}},
}

errs, _ = validate_flow_spec(spec, catalog)
if errs:
    print("VALIDATION ERRORS:")
    for e in errs: print("  -", e)
    sys.exit(1)

(ROOT / f"data/flows/{CO}-inbound-service.json").write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
(ROOT / f"data/pre-scripts/{CO.lower()}_inbound_catalog.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"OK — {len(states)} states / {len(events)} events / {len(catalog)} templates. Validation clean.")
print("states:", " ".join(s["id"] for s in states))
