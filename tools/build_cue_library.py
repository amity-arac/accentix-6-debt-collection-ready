#!/usr/bin/env python3
"""Build data/flows/intent_cues.json — an event -> [cue phrases] library derived
from the debt-collection intent taxonomy (data/flows/intents.source.md).

Each flow event has a `cues` field: example customer phrases that let the model
recognise when to fire the transition. The intent taxonomy already lists rich
Thai/English example phrases per intent, so we parse them out and map intents
onto flow event names.

Regenerate:  python tools/build_cue_library.py
Consumed by: tools/backfill_flow_cues.py, the flow editor "suggest cues" button,
             and create_flow_company() (new-company seeding).
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "flows" / "intents.source.md"
OUT = ROOT / "data" / "flows" / "intent_cues.json"

INTENT_LINE = re.compile(r"^\s*-\s*([A-Za-z0-9_-]+)\s*:")
QUOTED = re.compile(r'"([^"]+)"')

# event (flow) -> intents (taxonomy) whose example phrases become that event's cues
EVENT_INTENT_MAP = {
    "name_confirmed": ["confirm_identity", "ack_positive"],
    "agrees_to_pay": [
        "promise_to_pay", "pay_today", "pay_future_date", "pay_scheduled",
        "pay_amount_full", "pay_amount_statement", "pay_amount_partial",
        "pay_amount_minimum",
    ],
    "already_paid": ["already_paid", "paid_cycle_issue", "autopay"],
    "hardship_lost_job": ["temporary_pending", "hardship_chronic"],
    "hardship_sick": ["chronic_permanent"],
    "hardship_other": ["payment_hardship", "hardship_temporary", "temporary_shortage", "forget_payment"],
    "refuses": ["refuse_payment"],
    "reschedule_request": ["reschedule_contact", "contact_later", "contact_unavailable"],
    "gives_new_phone": ["update_phone_number"],
    "third_party": ["contact_third-party", "third-party_speaking", "third-party_referral"],
    "stop_signal": ["contact_status", "contact_customer"],
    "no_input": ["fallback_unrecognized", "contact_voicemail"],
    "dispute": ["dispute_payment", "payment_accuracy", "payment_status"],
    "negotiation": ["negotiation_installment", "reduction_interest", "reduction_fee"],
    "faq": [
        "info_identity", "info_purpose", "info_legitimacy", "info_entity",
        "info_source", "info_frequency", "info_product", "info_target",
        "amount_total", "amount_minimum", "amount_payoff", "logistics_method",
        "logistics_date", "document_billing",
    ],
}

CAP_PER_EVENT = 12  # keep cue lists short — they are recognition hints, not an exhaustive list


def parse_intents() -> dict[str, list[str]]:
    """intent name -> list of example phrases (brackets/meta dropped, deduped)."""
    out: dict[str, list[str]] = {}
    for raw in SRC.read_text(encoding="utf-8").splitlines():
        m = INTENT_LINE.match(raw)
        if not m:
            continue
        name = m.group(1)
        phrases = []
        for p in QUOTED.findall(raw):
            p = p.strip()
            if not p or "[" in p or "]" in p:  # drop meta like "[Machine Voice]"
                continue
            if p not in phrases:
                phrases.append(p)
        if phrases:
            out[name] = phrases
    return out


def build() -> dict[str, list[str]]:
    intents = parse_intents()
    missing = {i for keys in EVENT_INTENT_MAP.values() for i in keys} - intents.keys()
    if missing:
        print(f"WARNING: intents referenced in map but not found in source: {sorted(missing)}")
    lib: dict[str, list[str]] = {}
    for event, keys in EVENT_INTENT_MAP.items():
        cues: list[str] = []
        for k in keys:
            for p in intents.get(k, []):
                if p not in cues:
                    cues.append(p)
        lib[event] = cues[:CAP_PER_EVENT]
    return lib


if __name__ == "__main__":
    lib = build()
    OUT.write_text(json.dumps(lib, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(len(v) for v in lib.values())
    print(f"wrote {OUT.relative_to(ROOT)} — {len(lib)} events, {total} cues")
    for ev, cues in lib.items():
        print(f"  {ev:22} {len(cues):2}  {cues[:3]}")
