#!/usr/bin/env python3
"""AEON (อิอ้อน) golden test cases — walk the real FlowSpec, render each expected
agent line with the AEON persona. One row per turn."""
import json, csv, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agents.prescript import fill_template

spec = json.loads((ROOT/"data/flows/AEON-outbound-remind.json").read_text(encoding="utf-8"))
cat = json.loads((ROOT/"data/pre-scripts/v10_pre_script_database_parameterized.json").read_text(encoding="utf-8"))
cases = json.loads((ROOT/"data/test-cases/personas_data.json").read_text(encoding="utf-8"))
cd = dict(next(c["customer_data"] for c in cases if c["id"].split("-")[1] == "AEON"))
for k, v in {"company_phone": "02-035-6666", "company_name": "อิอ้อน", "agent_name": "น้องอิ", "today": "2026-05-22 (Thursday)"}.items():
    cd.setdefault(k, v)

st_by_id = {s["id"]: s for s in spec["states"]}
cat_by_fs = {}
for e in cat:
    cat_by_fs.setdefault(e.get("_fine_state"), []).append(e)
init = next(s for s in spec["states"] if s.get("initial"))

def cue(ev):
    cs = spec["events"].get(ev, {}).get("cues", [])
    th = [c for c in cs if any("฀" <= ch <= "๿" for ch in c)]
    return (th or cs or ["-"])[0]

def line(fss):  # render first template of each beat (choose reason-matched for convince handled by caller)
    out = []
    for fs in fss:
        e = (cat_by_fs.get(fs) or [{}])[0]
        out.append(fill_template(e.get("template", f"[{fs}?]"), cd, gender="F"))
    return " ".join(out)

# scenario = (name, [(event, customer_utterance_override_or_None), ...])
SCEN = [
    ("S1 จ่ายวันนี้", [("name_confirmed", "ใช่ครับ ผมสมชายเอง"), ("agrees_to_pay", "สะดวกครับ จ่ายวันนี้เลย")]),
    ("S2 จ่ายแล้ว", [("name_confirmed", "ครับ"), ("already_paid", "จ่ายไปแล้วนะ ตัดบัตรอัตโนมัติ")]),
    ("S3 ตกงาน→โน้มน้าว→จ่าย", [("name_confirmed", "ครับ"), ("hardship_lost_job", "ตกงานอยู่ครับ ยังไม่มีเงิน"), ("agrees_to_pay", "งั้นเดี๋ยวหาจ่ายขั้นต่ำให้")]),
    ("S4 ป่วย→โน้มน้าว→จ่าย", [("name_confirmed", "ครับ"), ("hardship_sick", "ป่วยเข้าโรงพยาบาลอยู่"), ("agrees_to_pay", "โอเค เดี๋ยวจ่ายให้")]),
    ("S5 เหตุอื่น→โน้มน้าว→ปฏิเสธ", [("name_confirmed", "ครับ"), ("hardship_other", "ช่วงนี้เงินช็อต"), ("refuses", "ไม่จ่าย ยังไงก็ไม่มี")]),
    ("S6 ขอเลื่อน/โทรกลับ", [("name_confirmed", "ครับ"), ("reschedule_request", "ตอนนี้ไม่สะดวก โทรมาใหม่พรุ่งนี้")]),
    ("S7 ขอไม่ให้โทร (DNC)", [("name_confirmed", "ครับ"), ("stop_signal", "อย่าโทรมาอีก จะฟ้อง")]),
    ("S8 ไม่ใช่เจ้าตัว→ให้เบอร์ใหม่", [("third_party", "เขาไม่อยู่ครับ ผมพี่ชาย"), ("gives_new_phone", "เบอร์ใหม่เขา 081-111-2222")]),
    ("S9 เงียบ 2 ครั้ง", [("no_input", "อ้าว...เอ๊ะ"), ("no_input", "...")]),
    ("S10 ยืนยันแล้วเงียบ", [("name_confirmed", "ครับ"), ("no_input", "อืม...")]),
]

rows = []
for name, steps in SCEN:
    cur = init
    beats = [t["fine_state"] for t in cur.get("templates", [])]
    rows.append(["AEON", name, 1, "AGENT (เปิดสาย)", "", "", cur["id"], ",".join(beats), line(beats), "", ""])
    turn = 2
    for ev, utt in steps:
        trans = {o["event"]: o for o in cur.get("on", [])}
        if ev not in trans:
            rows.append(["AEON", name, turn, "— N/A —", f"(event {ev} ไม่มีใน {cur['id']})", ev, cur["id"], "", "", "", ""]); break
        rows.append(["AEON", name, turn, "CUSTOMER", utt or cue(ev), ev, cur["id"], "", "", "", ""]); turn += 1
        nxt = st_by_id[trans[ev]["to"]]
        # convince: pick the reason-matched beat
        beats = [t["fine_state"] for t in nxt.get("templates", [])]
        if nxt["id"] == "convince" and ev.startswith("hardship_"):
            want = "convince_" + ev.split("hardship_")[1]
            beats = [b for b in beats if b == want] or beats[:1]
        tools = list(dict.fromkeys((nxt.get("entry_tools") or []) + (trans[ev].get("tools") or [])))
        outcome = ""
        if nxt.get("terminal"):
            o = nxt.get("outcome", {}); outcome = f"{o.get('result','')}:{','.join(o.get('reasons',[]))}"
        rows.append(["AEON", name, turn, "AGENT", "", ev, cur["id"] + "→" + nxt["id"],
                     ",".join(beats), line(beats), ",".join(tools), outcome]); turn += 1
        cur = nxt
        if cur.get("terminal"): break

# FAQ (handled via faq_routing = ตอบแล้วอยู่ state เดิม/resume) — ตัวอย่างประกอบ
rows.append(["AEON", "FAQ ถามยอดกลางสาย (resume)", 1, "CUSTOMER", "ยอดเท่าไหร่นะ", "faq:amount", "(ระหว่าง disclose_ask)", "", "", "", ""])
rows.append(["AEON", "FAQ ถามยอดกลางสาย (resume)", 2, "AGENT", "", "faq:amount", "resume", "faq_amount", line(["faq_amount"]), "", "then=resume"])
rows.append(["AEON", "FAQ ขอเจ้าหน้าที่ (ปิด tcb)", 1, "CUSTOMER", "ขอคุยเจ้าหน้าที่จริงๆ", "faq:agent", "(ทุก state)", "", "", "", ""])
rows.append(["AEON", "FAQ ขอเจ้าหน้าที่ (ปิด tcb)", 2, "AGENT", "", "faq:agent", "→ปิดสาย", "handoff_refuse", line(["handoff_refuse"]), "", "tcb:agent"])

OUT = ROOT/"data/test-cases/aeon_test_cases.csv"
with OUT.open("w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(["company","scenario","turn","speaker","customer_says","event","state(from→to)",
                "expected_beats","expected_agent_line","expected_tools","expected_outcome"])
    w.writerows(rows)
print(f"wrote {OUT.name} — {len(rows)} rows, {len(SCEN)}+2 scenarios")
