#!/usr/bin/env python3
"""Fix the AEON defects that are wrong regardless of business context.

    python3 tools/fix_aeon.py [--dry-run]

Every item is a template bound to the wrong beat, so the flow could legally say it
at a moment it must not. Binding is by `_fine_state`, so retagging a catalog entry
is how you move an utterance out of a state's reach — the spec need not change.
"""
from __future__ import annotations

import collections
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
CATALOG = REPO / "data" / "pre-scripts" / "v11_aeon_probe_catalog.json"
FLOWS = REPO / "data" / "flows"
DRY = "--dry-run" in sys.argv

# text_id -> new _fine_state, with the reason it had to move.
RETAG = {
    # Byte-identical to 1031/1040, which are correctly tagged handoff_refuse. Tagged
    # verify_name, it was bound to `verify_retry` — so the retry-after-silence state
    # could answer a customer who had said nothing with "I can't transfer you to an
    # agent".
    1017: ("handoff_refuse", "text is the agent-handoff refusal, not a name check"),

    # "ขอบคุณสำหรับการชำระเงิน" — thank you FOR THE PAYMENT. Tagged `close`, it was
    # reachable from close_refused and close_unreachable, i.e. thanking a customer
    # who just refused, or a stranger who was never the debtor, for paying.
    1052: ("close_paid", "thanks for a payment; only the paid close may say it"),

    # Questions bound under `apology` inside the TERMINAL close_unreachable: the call
    # ends immediately after asking, so the answer can never arrive.
    1061: ("ask_phone_again", "asks for the number again — cannot be a closing line"),
    1056: ("ask_callback_time", "asks for a callback time — cannot be a closing line"),
    1063: ("ask_callback_time", "asks for a callback time — cannot be a closing line"),

    # `probe_hardship` is used before choosing a convince variant. Only 1042 asks
    # anything; the rest are full pay-asks (is_demand), so probing burned an ask and
    # blew the 2-ask budget in a single visit to `convince`.
    1041: ("ask_pay_today", "a pay-ask, not a probe"),
    1043: ("ask_pay_today", "a pay-ask, not a probe"),
    1044: ("ask_pay_today", "a pay-ask, not a probe"),
}

# Metadata that contradicts the text. The flags drive the reply guard's chain rules
# and any pay-ask counting, so a closing apology marked as a demand corrupts both.
REFLAG = {
    1036: {"is_demand": False, "is_closer": True, "expects_response": False},
    1031: {"is_demand": False},
    1017: {"is_demand": False},
}

# The parameterized catalog exists so a male-voice session never mixes particles.
# These two hardcode female forms, so ~half of sessions said ครับ everywhere else
# and ค่ะ / ดิฉัน here.
GENDER_FIX = {
    1116: [("คุณลูกค้าค่ะ", "คุณลูกค้า{suffix}")],
    1118: [("ไม่ทราบว่า ดิฉันกำลังเรียนสาย", "ไม่ทราบว่า {pronoun}กำลังเรียนสาย")],
}

# Relationship denial is not a refusal to pay. Routed through stop_signal it closed
# as `refused`, i.e. the CRM recorded a wrong-party call as a debtor who said no.
# AEON already has a `wrong_name` FAQ route that records `tin`.
STOP_SIGNAL_DROP = {"relationship denial.", "i'm not your customer",
                    "i don't have an account with you", "ไม่เคยใช้บริการ",
                    "ลบชื่อออกไปเลย", "ไม่ใช่ลูกค้า"}


def fix_catalog() -> list[str]:
    rows = json.loads(CATALOG.read_text(encoding="utf-8"))
    by_id = {r["text_id"]: r for r in rows}
    notes: list[str] = []
    for tid, (fs, why) in RETAG.items():
        r = by_id.get(tid)
        if r and r.get("_fine_state") != fs:
            notes.append(f"{tid}: {r.get('_fine_state')} -> {fs}  ({why})")
            r["_fine_state"] = fs
    for tid, flags in REFLAG.items():
        r = by_id.get(tid)
        if not r:
            continue
        for k, v in flags.items():
            if r.get(k) != v:
                r[k] = v
                notes.append(f"{tid}: {k} -> {v}")
    for tid, subs in GENDER_FIX.items():
        r = by_id.get(tid)
        if not r:
            continue
        for old, new in subs:
            if old in r["template"]:
                r["template"] = r["template"].replace(old, new)
                notes.append(f"{tid}: hardcoded gender -> parameterized")
    if notes and not DRY:
        CATALOG.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    return notes


def fix_spec(path: pathlib.Path) -> list[str]:
    spec = json.loads(path.read_text(encoding="utf-8"),
                      object_pairs_hook=collections.OrderedDict)
    notes: list[str] = []

    # The paid close is the only state allowed to thank someone for paying.
    for st in spec["states"]:
        if st["id"] == "close_paid":
            fs = [t.get("fine_state") for t in st.get("templates", [])]
            if "close_paid" not in fs:
                st["templates"] = [collections.OrderedDict([("fine_state", "close_paid")])]
                notes.append("close_paid.templates -> close_paid (was the shared `close`)")

    # The two question beats freed from `apology` need a legal home, or they become
    # unbound and unreachable. Auxiliary is the spec's own "use when the moment calls
    # for it" list — and unlike a terminal state, it does not end the call.
    aux = spec.setdefault("auxiliary_templates", collections.OrderedDict()) \
              .setdefault("allowed", [])
    have = {a.get("fine_state") for a in aux}
    for fs, desc in (("ask_phone_again", "ขอเบอร์ติดต่ออีกครั้งเมื่อฟังไม่ชัด"),
                     ("ask_callback_time", "ถามเวลาที่สะดวกให้ติดต่อกลับ")):
        if fs not in have:
            aux.append(collections.OrderedDict([("fine_state", fs), ("desc", desc)]))
            notes.append(f"auxiliary += {fs}")

    # The agent asks for a callback TIME in three templates but the tool stored only
    # a date, so the answer had nowhere to go.
    for d in (spec.get("tools") or {}).get("declarations", []):
        if d["name"] == "callback_datetime" and "time" not in (d.get("args") or {}):
            d.setdefault("args", collections.OrderedDict())["time"] = \
                collections.OrderedDict([("type", "string"), ("format", "HH:MM"),
                                         ("optional", True)])
            notes.append("callback_datetime.args += time (optional, HH:MM)")

    ss = (spec.get("events") or {}).get("stop_signal")
    if isinstance(ss, dict) and ss.get("cues"):
        kept = [c for c in ss["cues"] if str(c).strip().lower() not in STOP_SIGNAL_DROP]
        if len(kept) != len(ss["cues"]):
            notes.append("stop_signal: dropped wrong-party cues "
                         f"{[c for c in ss['cues'] if c not in kept]} "
                         "(they belong to the wrong_name route, which records tin)")
            ss["cues"] = kept

    if notes and not DRY:
        path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    return notes


def main() -> None:
    print("catalog v11_aeon_probe_catalog.json")
    for n in fix_catalog():
        print("   -", n)
    for path in sorted(FLOWS.glob("AEON-outbound-remind*.json")):
        notes = fix_spec(path)
        if notes:
            print(f"\n{path.name}")
            for n in notes:
                print("   -", n)
    print("\n(dry run — nothing written)" if DRY else "\nwritten")


if __name__ == "__main__":
    main()
