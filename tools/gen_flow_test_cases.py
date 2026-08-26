#!/usr/bin/env python3
"""Generate golden expected-flow test cases (CSV) for every company by WALKING
each company's real FlowSpec. Each scenario = an event sequence; we walk the
state machine and emit the turn-by-turn flow that SHOULD happen."""
import json, csv, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agents.prescript import fill_template

REG = {
    "AEON": ("AEON-outbound-remind.json", "v10_pre_script_database_parameterized.json"),
    "JAI":  ("JAI-outbound-remind.json", "v11_jai_probe_catalog.json"),
    "KS":   ("KS-outbound-remind.json", "v11_ks_probe_catalog.json"),
    "AIS":  ("AIS-outbound-remind.json", "v11_ais_probe_catalog.json"),
    "NEWCO": ("NEWCO-collect-telco.json", "newco_collect_catalog.json"),
    "ABC":  ("ABC-collect-auto.json", "abc_collect_catalog.json"),
}
cases = json.load((ROOT/"data/test-cases/personas_data.json").open(encoding="utf-8"))
persona = {}
for c in cases: persona.setdefault(c["id"].split("-")[1], c["customer_data"])
persona.setdefault("NEWCO", {"customer_name":"คุณสมชาย ใจดี","loan_type":"ค่าบริการมือถือ","total_amount_due":890,
    "minimum_payment_due":890,"due_date":"2026-05-15 (Friday)","due_status":"overdue","last_4_digits":"4321","customer_phone":"081-222-3333","msisdn":"081-222-3333"})
persona.setdefault("ABC", {"customer_name":"คุณมานี รักดี","loan_type":"เช่าซื้อรถยนต์","total_amount_due":12500,
    "minimum_payment_due":12500,"due_date":"2026-05-15 (Friday)","due_status":"overdue","last_4_digits":"4321","customer_phone":"081-222-3333","vehicle_brand":"Toyota","vehicle_registration":"1กก1234","month":"พฤษภาคม","collection_fee":200,"insurance_fee":300,"late_fee":100})

# scenarios as ordered event lists (walk stops at terminal or when event unavailable)
SCENARIOS = [
    ("S1 ยืนยัน→จ่ายวันนี้ (happy path PTP)",        ["name_confirmed", "agrees_to_pay"]),
    ("S2 ยืนยัน→จ่ายแล้ว",                            ["name_confirmed", "already_paid"]),
    ("S3 ยืนยัน→ลำบาก(ตกงาน)→โน้มน้าว→จ่าย",          ["name_confirmed", "hardship_lost_job", "agrees_to_pay"]),
    ("S4 ยืนยัน→ลำบาก→ปฏิเสธ",                        ["name_confirmed", "hardship_other", "refuses"]),
    ("S5 ยืนยัน→ขอเลื่อน/โทรกลับ",                     ["name_confirmed", "reschedule_request"]),
    ("S6 ยืนยัน→ขอไม่ให้โทร (DNC)",                    ["name_confirmed", "stop_signal"]),
    ("S7 ไม่ใช่เจ้าตัว (third party)",                 ["third_party", "gives_new_phone"]),
    ("S8 เงียบ/ไม่ตอบ",                               ["no_input", "no_input"]),
]

def cue(spec, ev):
    cues = spec.get("events", {}).get(ev, {}).get("cues", [])
    thai = [c for c in cues if any("฀" <= ch <= "๿" for ch in c)]
    return (thai or cues or ["—"])[0]

def render_beats(cat_by_fs, fss, cd):
    out = []
    for fs in fss:
        e = cat_by_fs.get(fs, [{}])[0]
        t = e.get("template", "")
        out.append(fill_template(t, cd, gender="F")[:80] if t else f"[{fs}?]")
    return " ⏎ ".join(out)

rows = []
for co, (spf, cf) in REG.items():
    spec = json.loads((ROOT/"data/flows"/spf).read_text(encoding="utf-8"))
    cat = json.loads((ROOT/"data/pre-scripts"/cf).read_text(encoding="utf-8"))
    cd = dict(persona[co]); cd.setdefault("company_phone","02-000-0000"); cd.setdefault("company_name",co)
    cd.setdefault("agent_name","ผู้ช่วย"); cd.setdefault("today","2026-05-22 (Thursday)")
    st_by_id = {s["id"]: s for s in spec["states"]}
    cat_by_fs = {}
    for e in cat: cat_by_fs.setdefault(e.get("_fine_state"), []).append(e)
    init = next(s for s in spec["states"] if s.get("initial"))

    for scen, events in SCENARIOS:
        cur = init
        # opening (agent greets)
        beats = [t["fine_state"] for t in cur.get("templates", [])]
        rows.append([co, scen, 1, "AGENT (เปิดสาย)", "", "", cur["id"],
                     ",".join(beats), render_beats(cat_by_fs, beats, cd),
                     ",".join(cur.get("entry_tools") or []), ""])
        turn = 2
        for ev in events:
            trans = {o["event"]: o for o in cur.get("on", [])}
            if ev not in trans:
                rows.append([co, scen, turn, "— N/A —", f"(event '{ev}' ไม่มีใน state {cur['id']})",
                             ev, cur["id"], "", "", "", ""]); break
            # customer turn
            rows.append([co, scen, turn, "CUSTOMER", cue(spec, ev), ev, cur["id"], "", "", "", ""])
            turn += 1
            nxt = st_by_id[trans[ev]["to"]]
            beats = [t["fine_state"] for t in nxt.get("templates", [])]
            tools = (nxt.get("entry_tools") or []) + (trans[ev].get("tools") or [])
            outcome = ""
            if nxt.get("terminal"):
                o = nxt.get("outcome", {})
                outcome = f"{o.get('result','')}:{','.join(o.get('reasons',[]))}"
            rows.append([co, scen, turn, "AGENT", "", ev, cur["id"] + "→" + nxt["id"],
                         ",".join(beats), render_beats(cat_by_fs, beats, cd),
                         ",".join(dict.fromkeys(tools)), outcome])
            turn += 1
            cur = nxt
            if cur.get("terminal"): break

OUT = ROOT/"data/test-cases/flow_expected_cases.csv"
with OUT.open("w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(["company","scenario","turn","speaker","customer_says(example)","event",
                "state(from→to)","expected_beats(fine_state)","expected_agent_line(rendered)",
                "expected_tools","expected_outcome"])
    w.writerows(rows)
print(f"wrote {OUT}  ({len(rows)} rows, {len(REG)} companies x {len(SCENARIOS)} scenarios)")
