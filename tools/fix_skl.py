#!/usr/bin/env python3
"""Fix the SKL defects that are wrong regardless of business context.

    python3 tools/fix_skl.py [--dry-run]

Also corrects one KBANK state note that promised behaviour its catalog never had —
verified against the mined catalogs: AEON and KBANK both close a PTP without reading
it back, so NOT reading it back is the norm and the note was the thing out of step.
"""
from __future__ import annotations

import collections
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
SKL_SPEC = REPO / "data" / "flows" / "SKL-outbound-remind.json"
SKL_CAT = REPO / "data" / "pre-scripts" / "skl_catalog.json"
KBANK_SPEC = REPO / "data" / "flows" / "KBANK-outbound-remind.json"
DRY = "--dry-run" in sys.argv

REWRITE = {
    # SPOKE ONE NUMBER, RECORDED ANOTHER. `[minimum_payment]` is the CRM minimum, not
    # what the customer just promised — so a customer offering 1,500 heard the agent
    # confirm 3,000 while the CRM stored 1,500. And the date was never said at all.
    # The dynamic slots carry what the model passed to record_verbal_commitment, and
    # degrade to a neutral Thai phrase when absent — vague beats confidently wrong.
    1048: "ดิฉันขออนุญาตบันทึกนัดชำระ [promised_amount] บาท [promised_date] {suffix} "
          "เมื่อชำระค่างวดแล้ว เก็บใบเสร็จที่ชำระไว้เป็นหลักฐานก่อนนะ{q_suffix} "
          "เมื่อยอดชำระเข้าระบบของทางบริษัทฯแล้ว จะมีข้อความแจ้งกลับไปนะ{q_suffix}",

    # BOOKED A CALLBACK IT NEVER SPOKE. callback_datetime writes a concrete date; the
    # only reply on that path said "ติดต่อใหม่ภายหลัง", so the customer never heard —
    # let alone agreed to — the appointment that was just written.
    1051: "ขออนุญาตติดต่อกลับอีกครั้ง [callback_date] นะ{q_suffix} สวัสดี{suffix}",

    # NOT A QUESTION. The whole opening depends on an answer, but the line just
    # trails off with a particle; AEON and KBANK both ask outright.
    1000: "สวัสดี{suffix} {pronoun}ติดต่อจากบริษัท[company_name] ไม่ทราบว่ากำลังเรียนสาย"
          "อยู่กับคุณ [customer_name] ใช่หรือไม่{q_suffix}",

    # DIDN'T PROBE. `convince` uses this optional beat to find out WHY before picking
    # a convince variant, but the text was a third pay-ask — which also blew the
    # 2-ask budget the spec sets. Ask the question the state says it asks.
    1041: "รบกวนสอบถามเพิ่มเติมว่า ติดปัญหาด้านใดหรือเปล่า{q_suffix}",
}

# A closing apology is a closer, not a demand; the flags drive the reply guard's
# chain rules and any pay-ask counting.
REFLAG = {
    1036: {"is_closer": True, "is_demand": False, "expects_response": False},
}


def fix_skl_catalog() -> list[str]:
    rows = json.loads(SKL_CAT.read_text(encoding="utf-8"))
    by_id = {r["text_id"]: r for r in rows}
    notes = []
    for tid, text in REWRITE.items():
        r = by_id.get(tid)
        if r and r["template"] != text:
            r["template"] = text
            notes.append(f"{tid} ({r.get('_fine_state')}): rewritten")
    for tid, flags in REFLAG.items():
        r = by_id.get(tid)
        for k, v in (flags.items() if r else []):
            if r.get(k) != v:
                r[k] = v
                notes.append(f"{tid}: {k} -> {v}")
    # `intent_name` is a grouping key; three different beats all claimed
    # "negotiation_ask_pay_today", merging disclosure, closing and handoff into
    # "asked to pay" for anything that counts by intent.
    for r in rows:
        fs, iname = r.get("_fine_state"), r.get("intent_name")
        if fs and iname and fs not in iname:
            r["intent_name"] = fs
            notes.append(f"{r['text_id']}: intent_name {iname} -> {fs}")
    if notes and not DRY:
        SKL_CAT.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    return notes


def fix_skl_spec() -> list[str]:
    spec = json.loads(SKL_SPEC.read_text(encoding="utf-8"),
                      object_pairs_hook=collections.OrderedDict)
    notes = []
    states = {s["id"]: s for s in spec["states"]}

    # THANKED A STRANGER FOR BEING A CUSTOMER. The two templates render AND (no
    # `group`), so this terminal state said "ขอขอบคุณที่ใช้บริการบริษัท… สวัสดีค่ะ"
    # followed by "ขออภัยที่รบกวน… สวัสดีค่ะ" — two farewells, and the first one to
    # someone who is reached by wrong number or never answered.
    st = states.get("close_unreachable")
    if st and [t.get("fine_state") for t in st["templates"]] == ["close", "apology"]:
        st["templates"] = [collections.OrderedDict([("fine_state", "apology")])]
        notes.append("close_unreachable: close+apology -> apology only "
                     "(no thanking a non-customer, one farewell)")

    # ENDED WITHOUT A FAREWELL: the only line was "ลูกค้ารับทราบข้อมูลครบถ้วนแล้วนะคะ"
    # and then the call dropped. The pair is already permitted by the spec's own
    # one_template_per_turn exception.
    st = states.get("close_confirm_info")
    if st and [t.get("fine_state") for t in st["templates"]] == ["confirm_info"]:
        st["templates"].append(collections.OrderedDict([("fine_state", "close")]))
        notes.append("close_confirm_info: += close (was hanging up mid-sentence)")

    # The state composes ptp_record+close, which its own constraint forbade.
    for c in spec.get("constraints", []):
        if c.get("id") == "one_template_per_turn":
            ex = c.setdefault("exceptions", [])
            if ["ptp_record", "close"] not in ex:
                ex.append(["ptp_record", "close"])
                notes.append("one_template_per_turn: += [ptp_record, close] "
                             "(the pair ptp_capture actually speaks)")

    if notes and not DRY:
        SKL_SPEC.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
    return notes


def fix_kbank_note() -> list[str]:
    spec = json.loads(KBANK_SPEC.read_text(encoding="utf-8"),
                      object_pairs_hook=collections.OrderedDict)
    notes = []
    for st in spec["states"]:
        if st["id"] == "ptp_capture" and "สรุปนัด" in str(st.get("note", "")):
            st["note"] = ("รับทราบการนัดชำระแล้วปิดสาย — catalog ที่สกัดจากสายจริง "
                          "ไม่ได้ทวนยอด/วันซ้ำ (AEON ก็ไม่ทวน) ห้ามแต่งประโยคทวนเอง")
            notes.append("ptp_capture.note: no longer promises a read-back the "
                         "mined catalog never had")
    if notes and not DRY:
        KBANK_SPEC.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
    return notes


def main() -> None:
    print("SKL catalog")
    for n in fix_skl_catalog():
        print("   -", n)
    print("\nSKL spec")
    for n in fix_skl_spec():
        print("   -", n)
    print("\nKBANK spec")
    for n in fix_kbank_note():
        print("   -", n)
    print("\n(dry run — nothing written)" if DRY else "\nwritten")


if __name__ == "__main__":
    main()
