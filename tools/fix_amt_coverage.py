#!/usr/bin/env python3
"""Close the AMT appointment flow's coverage holes.

    python3 tools/fix_amt_coverage.py [--dry-run]

Three defects the audit found, all reachable on an ordinary call:

1. CANCELLATION COULD NOT BE EXPRESSED. No `cancel` event, and `save_appointment`
   accepted only confirmed/rescheduled — so "ขอยกเลิก ไม่ไปแล้ว" matched the
   reschedule cues, the agent offered dates to someone who had just cancelled, and
   the only recordable ending was a FALSE "rescheduled". The slot was never released.

2. SILENCE WAS WRITTEN AS CONSENT. `check_slot` and `offer_other_doctor` called
   `save_appointment` on `no_input`, so a quiet patient — or an answering machine —
   had their appointment moved, with `new_slot` empty. `greet.no_input` also looped
   to itself with no retry limit, and no unreachable outcome existed, so voicemail
   looped until the turn cap.

3. NOBODY BUT THE PATIENT COULD END THE CALL. "ไม่ใช่ครับ โทรผิด" had no event, so
   the agent kept pushing confirm/reschedule at a stranger.

NOT changed, by product decision: AMT greets with the appointment details and does
NOT verify identity first.

Also: the close templates never said the new date (`save_appointment` echoes
`new_slot`, and SpecBackend merges it, so it was one placeholder away), and
`check_doctor_time`'s real on-duty dates had no template able to speak them while
the spec forbade guessing.
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

# New utterances. Ids continue the file's 85xx block.
NEW_TEMPLATES = [
    (8503, "greet_retry", "opening",
     "ขออภัยค่ะ สัญญาณอาจไม่ชัดเจน ไม่ทราบว่าได้ยินเสียงดิฉันไหมคะ "
     "ดิฉันติดต่อจากโรงพยาบาล AMT ค่ะ"),
    # Wrong party: apologise and leave without repeating the visit details.
    (8504, "wrong_person", "closing",
     "ขออภัยที่รบกวนค่ะ ดิฉันขออนุญาตวางสาย เนื่องจากต้องเรียนสายกับคุณ [customer_name] "
     "โดยตรงนะคะ ขอบคุณค่ะ สวัสดีค่ะ"),
    (8505, "close_unreachable", "closing",
     "ไม่เป็นไรค่ะ ดิฉันขออนุญาตติดต่อกลับมาอีกครั้งนะคะ ขอบคุณค่ะ สวัสดีค่ะ"),
    (8531, "thank_cancel", "closing",
     "รับทราบค่ะ ดิฉันบันทึกยกเลิกนัดของคุณ [customer_name] กับ [doctor_name] เรียบร้อยแล้วนะคะ "
     "หากต้องการนัดใหม่ ติดต่อได้ที่ [company_phone] ค่ะ ขอบคุณค่ะ สวัสดีค่ะ"),
]

# Existing templates that withheld information the API already returned.
TEMPLATE_EDITS = {
    # Offer the doctor's REAL on-duty dates instead of a weekly pattern. The spec
    # forbids guessing; check_doctor_time returns available_dates_text for this.
    8512: "ไม่เป็นไรค่ะ ดิฉันเช็คตารางให้แล้วนะคะ [doctor_name] เข้าตรวจ [available_dates_text] ค่ะ "
          "ไม่ทราบว่าคุณ [customer_name] สะดวกวันไหนคะ เดี๋ยวดิฉันบันทึกเลื่อนนัดให้เลยค่ะ",
    8513: "ได้ค่ะ ขอเช็คตารางแพทย์ให้สักครู่นะคะ [doctor_name] เข้าตรวจ [available_dates_text] ค่ะ "
          "คุณสะดวกเลื่อนไปวันไหนดีคะ",
    # A patient should not hang up without hearing the new date.
    8530: "รับทราบค่ะ ดิฉันบันทึกเลื่อนนัดของคุณ [customer_name] กับ [doctor_name] "
          "เป็น [new_slot] เรียบร้อยแล้วนะคะ ขอบคุณค่ะ สวัสดีค่ะ",
    8540: "รับทราบค่ะ ดิฉันบันทึกเลื่อนนัดของคุณ [customer_name] เป็น [new_slot] เรียบร้อยแล้ว "
          "โดยจะจัดแพทย์ท่านอื่นที่เข้าตรวจในวันนั้นให้แทนนะคะ ขอบคุณค่ะ สวัสดีค่ะ",
    8541: "ได้ค่ะ ดิฉันบันทึกเลื่อนนัดเป็น [new_slot] ให้แล้วนะคะ วันนั้นจะเป็นแพทย์ท่านอื่น"
          "ที่เข้าตรวจแทน [doctor_name] ค่ะ ขอบคุณค่ะ สวัสดีค่ะ",
}

NEW_EVENTS = {
    "third_party": {
        "desc": "ผู้รับสายไม่ใช่ผู้ป่วย / โทรผิดเบอร์",
        "cues": ["ไม่ใช่ครับ", "ไม่ใช่ค่ะ", "โทรผิด", "ไม่รู้จัก", "ผิดเบอร์",
                 "ไม่มีคนชื่อนี้"],
    },
    "cancel_request": {
        "desc": "ผู้ป่วยขอยกเลิกนัด ไม่ประสงค์เข้ารับการตรวจ",
        "cues": ["ขอยกเลิก", "ยกเลิกนัด", "ไม่ไปแล้ว", "ไม่เอาแล้ว", "ไม่ต้องนัด",
                 "ขอยกเลิกเลย"],
    },
}

NEW_OUTCOMES = {
    "cancelled": {"reasons": ["patient_request"], "desc": "ผู้ป่วยขอยกเลิกนัด"},
    "unreachable": {"reasons": ["wrong_number", "no_input"],
                    "desc": "ติดต่อผู้ป่วยไม่ได้ (ผิดคน / ไม่มีการตอบ)"},
}


def _state(sid, phase, templates, on, *, terminal=False, entry_tools=None,
           outcome=None, note=None, max_visits=None):
    st = collections.OrderedDict([("id", sid), ("phase", phase)])
    if entry_tools:
        st["entry_tools"] = entry_tools
    st["templates"] = [collections.OrderedDict([("fine_state", f)]) for f in templates]
    if note:
        st["note"] = note
    if max_visits:
        st["max_visits"] = max_visits
    if on:
        st["on"] = [collections.OrderedDict(o) for o in on]
    if terminal:
        st["terminal"] = True
    if outcome:
        st["outcome"] = collections.OrderedDict(outcome)
    return st


def main() -> None:
    spec = json.loads(SPEC.read_text(encoding="utf-8"),
                      object_pairs_hook=collections.OrderedDict)
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    by_id = {c["text_id"]: c for c in catalog}
    changes: list[str] = []

    for tid, fine_state, state, text in NEW_TEMPLATES:
        if tid in by_id:
            continue
        catalog.append(collections.OrderedDict([
            ("company", "AMT"), ("text_id", tid), ("intent_name", fine_state),
            ("_fine_state", fine_state), ("category", "A"), ("state", state),
            ("template", text),
            ("expects_response", state == "opening"),
            ("is_acknowledgment", False), ("is_demand", False),
            ("is_closer", state == "closing"),
        ]))
        changes.append(f"catalog += {tid} ({fine_state})")

    for tid, text in TEMPLATE_EDITS.items():
        if tid in by_id and by_id[tid]["template"] != text:
            by_id[tid]["template"] = text
            changes.append(f"catalog {tid}: rewritten to speak data it already had")

    ev = spec.setdefault("events", collections.OrderedDict())
    for name, body in NEW_EVENTS.items():
        if name not in ev:
            ev[name] = collections.OrderedDict(body)
            changes.append(f"event += {name}")

    res = spec.setdefault("outcomes", collections.OrderedDict()) \
              .setdefault("results", collections.OrderedDict())
    for name, body in NEW_OUTCOMES.items():
        if name not in res:
            res[name] = collections.OrderedDict(body)
            changes.append(f"outcome += {name}")

    states = {s["id"]: s for s in spec["states"]}

    # greet keeps announcing the appointment (no identity step, by product decision),
    # but a wrong party and a cancellation now have somewhere to go, and silence gets
    # one retry instead of an unbounded self-loop.
    greet_on = {t["event"]: t for t in states["greet"].get("on", [])}
    greet_on.setdefault("third_party", collections.OrderedDict(
        [("event", "third_party"), ("to", "third_party_close")]))
    greet_on.setdefault("cancel_request", collections.OrderedDict(
        [("event", "cancel_request"), ("tools", ["save_appointment"]),
         ("to", "cancel_close")]))
    if greet_on.get("no_input", {}).get("to") == "greet":
        greet_on["no_input"] = collections.OrderedDict(
            [("event", "no_input"), ("to", "greet_retry"),
             ("note", "เงียบครั้งแรก — ลองใหม่ครั้งเดียว ห้ามวนซ้ำที่ greet")])
        changes.append("greet.no_input: self-loop -> greet_retry (bounded)")
    states["greet"]["on"] = list(greet_on.values())
    changes.append("greet: += third_party, cancel_request")

    new_states = [
        _state("greet_retry", "opening", ["greet_retry"], [
            {"event": "confirms", "tools": ["save_appointment"], "to": "confirm_close"},
            {"event": "reschedule_request", "to": "check_slot"},
            {"event": "cancel_request", "tools": ["save_appointment"], "to": "cancel_close"},
            {"event": "third_party", "to": "third_party_close"},
            {"event": "no_input", "to": "close_unreachable"},
        ], note="ลองอีกครั้งเดียว ถ้ายังเงียบให้ปิดสาย", max_visits=1),
        _state("cancel_close", "close", ["thank_cancel"], [],
               terminal=True, entry_tools=["save_appointment"],
               outcome={"result": "cancelled", "reasons": ["patient_request"]},
               note="บันทึก status=cancelled เพื่อปล่อยคิวคืน ห้ามบันทึกเป็น rescheduled"),
        _state("third_party_close", "close", ["wrong_person"], [],
               terminal=True, entry_tools=["save_appointment"],
               outcome={"result": "unreachable", "reasons": ["wrong_number"]},
               note="ไม่ใช่ผู้ป่วย — ไม่ต้องย้ำรายละเอียดนัด ปิดสายสุภาพ"),
        _state("close_unreachable", "close", ["close_unreachable"], [],
               terminal=True, entry_tools=["save_appointment"],
               outcome={"result": "unreachable", "reasons": ["no_input"]},
               note="ไม่มีการตอบ — ห้ามบันทึกเลื่อนนัดจากความเงียบ"),
    ]
    for st in new_states:
        if st["id"] not in states:
            spec["states"].append(st)
            changes.append(f"state += {st['id']}")

    # Silence must not write to the booking system.
    for sid in ("check_slot", "offer_other_doctor"):
        for tr in states.get(sid, {}).get("on", []):
            if tr.get("event") == "no_input":
                tr.pop("tools", None)
                tr["to"] = "close_unreachable"
                tr["note"] = "เงียบ ≠ ยินยอมเลื่อนนัด — ปิดสายโดยไม่บันทึกการเลื่อน"
                changes.append(f"{sid}.no_input: no longer writes save_appointment")

    # A cancellation is reachable from every point the patient can speak.
    for sid in ("check_slot", "offer_other_doctor"):
        st = states.get(sid)
        if st and not any(t.get("event") == "cancel_request" for t in st.get("on", [])):
            st["on"].append(collections.OrderedDict([
                ("event", "cancel_request"), ("tools", ["save_appointment"]),
                ("to", "cancel_close")]))
            changes.append(f"{sid}: += cancel_request")

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
