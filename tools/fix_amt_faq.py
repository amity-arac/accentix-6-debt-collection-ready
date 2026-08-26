#!/usr/bin/env python3
"""Give the AMT flow the answers and the exits a real appointment call needs.

    python3 tools/fix_amt_faq.py [--dry-run]

The audit's remaining AMT items, all reachable on an ordinary call:

1. EVERY PATIENT QUESTION WAS UNANSWERABLE. `faq_routing.routes` and
   `auxiliary_templates.allowed` were both empty while the reply rule is
   "ห้ามสร้างข้อความอิสระ" over six beats — so "นัดอะไรคะ", "ค่าใช้จ่ายเท่าไหร่",
   "ต้องงดน้ำไหม", "ไปยังไง" could only be answered by re-speaking a beat. There was
   also NO AI-disclosure template, so "คนหรือบอทคะ" had no honest answer — the same
   failure class the v9 work treated as a compliance dealbreaker.

2. "ขอเปลี่ยนหมอ วันเดิม" HAD NO EVENT. `slot_unavailable` is defined as a date
   problem and its cues are all date phrases, so a doctor request fell through to
   `reschedule_request` and the agent offered the SAME doctor's schedule.

3. `offer_other_doctor` HAD NO EXIT if the patient refuses a second time — only
   confirms / reschedule_request / no_input — so the call had nowhere to go.

4. `check_doctor_time` TOOK NO ARGUMENTS, so when the agent promised "จะจัดแพทย์
   ท่านอื่นที่เข้าตรวจในวันนั้นให้" it had no way to check that anyone is on duty
   that day — while the spec forbids guessing availability.
"""
from __future__ import annotations

import collections
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
SPEC = REPO / "data" / "flows" / "AMT-outbound-appointment.json"
CATALOG = REPO / "data" / "pre-scripts" / "amt_catalog.json"
DRY = "--dry-run" in sys.argv

NEW_TEMPLATES = [
    (8550, "faq_purpose", "faq",
     "เป็นนัดตรวจตามแผนการรักษากับ [doctor_name] ค่ะ หากต้องการรายละเอียดของการตรวจ "
     "เจ้าหน้าที่คลินิกยินดีแจ้งให้ทราบที่ [company_phone] นะคะ"),
    (8551, "faq_cost", "faq",
     "ค่าใช้จ่ายขึ้นอยู่กับรายการตรวจที่แพทย์พิจารณาในวันนั้นค่ะ ดิฉันขออนุญาตให้เจ้าหน้าที่"
     "แจ้งรายละเอียดที่ [company_phone] นะคะ"),
    (8552, "faq_preparation", "faq",
     "การเตรียมตัวก่อนตรวจขึ้นอยู่กับรายการที่แพทย์นัดค่ะ รบกวนสอบถามเจ้าหน้าที่คลินิกที่ "
     "[company_phone] เพื่อความถูกต้องนะคะ"),
    (8553, "faq_location", "faq",
     "นัดของคุณ [customer_name] อยู่ที่โรงพยาบาล AMT ค่ะ หากต้องการรายละเอียดการเดินทาง "
     "หรือจุดติดต่อ สอบถามได้ที่ [company_phone] นะคะ"),
    # Honest disclosure. Never claim to be a person.
    (8554, "ai_disclosure", "faq",
     "ดิฉันเป็นผู้ช่วยอัตโนมัติของโรงพยาบาล AMT ค่ะ โทรมาเพื่อยืนยันนัดพบแพทย์ "
     "หากต้องการคุยกับเจ้าหน้าที่ ติดต่อได้ที่ [company_phone] นะคะ"),
    (8555, "faq_repeat", "faq",
     "ได้ค่ะ ดิฉันขออนุญาตแจ้งอีกครั้งนะคะ คุณ [customer_name] มีนัดพบ [doctor_name] "
     "[appointment_date] เวลา [appointment_time] ค่ะ"),
    # A second refusal is a real outcome, not a dead end.
    (8560, "handoff_schedule", "closing",
     "ไม่เป็นไรค่ะ ดิฉันขออนุญาตให้เจ้าหน้าที่ติดต่อกลับเพื่อจัดวันนัดใหม่ให้คุณ [customer_name] "
     "อีกครั้งนะคะ ขอบคุณค่ะ สวัสดีค่ะ"),
]

FAQ_ROUTES = [
    ("purpose", "ถามว่านัดเรื่องอะไร / ตรวจอะไร", "faq_purpose"),
    ("cost", "ถามค่าใช้จ่าย", "faq_cost"),
    ("preparation", "ถามการเตรียมตัว เช่น งดน้ำงดอาหาร", "faq_preparation"),
    ("location", "ถามสถานที่ / การเดินทาง", "faq_location"),
    ("ai_or_human", "ถามว่าเป็นคนหรือระบบอัตโนมัติ", "ai_disclosure"),
    ("repeat", "ขอให้ทวนนัดอีกครั้ง / ฟังไม่ชัด", "faq_repeat"),
]


def main() -> None:
    spec = json.loads(SPEC.read_text(encoding="utf-8"),
                      object_pairs_hook=collections.OrderedDict)
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    by_id = {c["text_id"] for c in catalog}
    changes: list[str] = []

    for tid, fs, state, text in NEW_TEMPLATES:
        if tid in by_id:
            continue
        catalog.append(collections.OrderedDict([
            ("company", "AMT"), ("text_id", tid), ("intent_name", fs),
            ("_fine_state", fs), ("category", "C" if state == "faq" else "A"),
            ("state", state), ("template", text),
            ("expects_response", False), ("is_acknowledgment", False),
            ("is_demand", False), ("is_closer", state == "closing"),
        ]))
        changes.append(f"catalog += {tid} ({fs})")

    fr = spec.setdefault("faq_routing", collections.OrderedDict())
    fr.setdefault("note", "ตอบคำถามสั้นๆ แล้วกลับไปทำ flow ต่อทันที "
                          "เรื่องที่เกินขอบเขต ให้ส่งต่อเจ้าหน้าที่ ห้ามเดาข้อมูลทางการแพทย์")
    routes = fr.setdefault("routes", [])
    have = {r.get("intent") for r in routes}
    for intent, desc, fs in FAQ_ROUTES:
        if intent in have:
            continue
        routes.append(collections.OrderedDict([
            ("intent", intent), ("desc", desc),
            ("templates", [collections.OrderedDict([("fine_state", fs)])]),
            ("then", "resume"),
        ]))
        changes.append(f"faq route += {intent} -> {fs}")

    # A doctor request is not a date problem.
    ev = spec.setdefault("events", collections.OrderedDict())
    if "other_doctor_request" not in ev:
        ev["other_doctor_request"] = collections.OrderedDict([
            ("desc", "ผู้ป่วยขอเปลี่ยนแพทย์ (วันเดิมหรือวันใดก็ได้)"),
            ("cues", ["ขอเปลี่ยนหมอ", "เปลี่ยนแพทย์", "หมอท่านอื่น", "ขอหมอคนอื่น",
                      "ไม่อยากเจอหมอคนนี้"]),
        ])
        changes.append("event += other_doctor_request")

    states = {s["id"]: s for s in spec["states"]}
    for sid in ("greet", "greet_retry", "check_slot"):
        st = states.get(sid)
        if st and not any(t.get("event") == "other_doctor_request"
                          for t in st.get("on", [])):
            st.setdefault("on", []).append(collections.OrderedDict(
                [("event", "other_doctor_request"), ("to", "offer_other_doctor")]))
            changes.append(f"{sid}: += other_doctor_request -> offer_other_doctor")

    # Second refusal: hand it to staff instead of looping.
    res = spec["outcomes"]["results"]
    if "unresolved" not in res:
        res["unresolved"] = collections.OrderedDict([
            ("reasons", ["no_slot_agreed"]),
            ("desc", "ตกลงวันนัดไม่ได้ในสายนี้ — ส่งต่อเจ้าหน้าที่จัดคิว")])
        changes.append("outcome += unresolved")
    if "handoff_close" not in states:
        spec["states"].append(collections.OrderedDict([
            ("id", "handoff_close"), ("phase", "close"),
            ("entry_tools", ["save_appointment"]),
            ("templates", [collections.OrderedDict([("fine_state", "handoff_schedule")])]),
            ("note", "ไม่มีวันไหนตรงกัน — ส่งต่อเจ้าหน้าที่ ห้ามบันทึกว่าเลื่อนสำเร็จ"),
            ("terminal", True),
            ("outcome", collections.OrderedDict(
                [("result", "unresolved"), ("reasons", ["no_slot_agreed"])])),
        ]))
        changes.append("state += handoff_close")
    ood = states.get("offer_other_doctor")
    if ood and not any(t.get("event") == "slot_unavailable" for t in ood.get("on", [])):
        ood["on"].append(collections.OrderedDict(
            [("event", "slot_unavailable"), ("tools", ["save_appointment"]),
             ("to", "handoff_close")]))
        changes.append("offer_other_doctor: += slot_unavailable -> handoff_close")

    # Let the tool answer "is anyone on duty that day?", which the spec's own
    # constraint requires before promising a substitute.
    for d in spec["tools"]["declarations"]:
        if d["name"] == "check_doctor_time" and "date" not in (d.get("args") or {}):
            d.setdefault("args", collections.OrderedDict())["date"] = \
                collections.OrderedDict([
                    ("type", "string"), ("format", "YYYY-MM-DD (Weekday)"),
                    ("optional", True)])
            d["desc"] = (d.get("desc") or "").rstrip() + \
                " — ใส่ date เพื่อเช็คว่ามีแพทย์เข้าตรวจในวันที่ผู้ป่วยขอหรือไม่"
            changes.append("check_doctor_time.args += date (optional)")

    if not DRY:
        SPEC.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        CATALOG.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    for c in changes:
        print("  -", c)
    print("\n(dry run — nothing written)" if DRY else f"\n{len(changes)} change(s) written")


if __name__ == "__main__":
    main()
