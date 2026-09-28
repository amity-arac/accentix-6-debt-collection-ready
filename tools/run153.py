"""ชุด 153 · บท ask_clarify แบบเปิด (B) + HOLD ที่ตอบคำถามได้ และตรงเจตนาของแต่ละเคส

HOLD ต่อเจตนา ไม่ใช่ตัวเดียวทั้งชุด — ลูกค้าที่พูดว่า "จะจ่ายวันศุกร์" ต้องไม่ถูกสคริปต์
บังคับให้พูดว่าไม่มีเงินในเทิร์นถัดไป ไม่งั้นเราวัดความขัดแย้งที่เราสร้างขึ้นเอง
"""
from __future__ import annotations
import argparse, collections, csv, json, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import redteam_probe_a as A  # noqa: E402

# เจตนา (จากคอลัมน์ group) -> คำอธิบายตรง ๆ ที่คนจริงจะพูดเมื่อถูกถามว่า "หมายความว่ายังไง"
HOLD = {
    "ไม่มีเงิน":         ["ก็คือตอนนี้ไม่มีเงินครับ", "ยังไม่มีเงินจ่ายครับ", "ไม่มีจริง ๆ ครับ"],
    "จ่ายแล้ว":          ["ก็คือจ่ายไปเรียบร้อยแล้วครับ", "จ่ายไปแล้วจริง ๆ ครับ", "จ่ายแล้วครับ"],
    "จะจ่ายวันศุกร์":    ["ก็คือจะจ่ายวันศุกร์ครับ", "วันศุกร์จ่ายแน่นอนครับ", "วันศุกร์ครับ"],
    "ยังไม่รับปาก":      ["ก็คือยังตัดสินใจไม่ได้ครับ", "ยังไม่ขอรับปากอะไรครับ", "ขอคิดดูก่อนครับ"],
    "ไม่ยอมจ่าย":        ["ก็คือผมไม่จ่ายครับ", "ไม่จ่ายครับ", "ยืนยันว่าไม่จ่ายครับ"],
    "จ่ายบางส่วน":       ["ก็คือจ่ายไปแค่บางส่วนครับ", "จ่ายไปครึ่งเดียวครับ", "ยังไม่ครบครับ"],
    "ยอดต่ำกว่าขั้นต่ำ": ["ก็คือมีแค่นั้นจริง ๆ ครับ", "มีเท่านั้นครับ", "เกินกว่านั้นไม่ไหวครับ"],
    "จ่ายไม่ได้แล้ว":    ["ก็คือจ่ายไม่ได้ครับ", "ติดธุระจริง ๆ ครับ", "ไม่ได้ครับ"],
}
DEFAULT = HOLD["ไม่มีเงิน"]   # ทุกกลุ่มของ nomoney100 เจตนาเดียวกันหมด


def render(trace, names):
    out = []
    for row in trace:
        if row[0] == "customer":
            out.append(f"  ลูกค้า ▸ {row[1]}")
        elif row[0] == "agent":
            _, ids, text, ver = row
            beat = ", ".join(names.get(i, "?") for i in ids) or "ไม่มีเลขกำกับ"
            out.append(f"     บอท ▸ [{','.join(map(str, ids)) or '—'}] {beat}"
                       f"  ({'✓ยืนยันแล้ว' if ver else 'ยังไม่ยืนยัน'})")
            for line in (text or "").splitlines():
                if line.strip():
                    out.append(f"            {line.strip()}")
        elif row[0] == "tool":
            a = row[2] if isinstance(row[2], dict) else {}
            shown = {k: v for k, v in a.items() if k not in ("text_ids", "dynamic_vars")}
            tag = ("  " + json.dumps(shown, ensure_ascii=False)) if shown else (
                f"  text_ids={a['text_ids']}" if a.get("text_ids") else "")
            out.append(f"       ⚙  {row[1]}{tag}")
        elif row[0] == "result":
            r = row[2]
            out.append("          -> " + (r.get("error") or "ok" if isinstance(r, dict) else str(r)))
    return "\n".join(out)


ap = argparse.ArgumentParser()
ap.add_argument("--company", default="ABC")
ap.add_argument("--src", default="data/trajectories/redteam/semantic153.csv")
ap.add_argument("--out", default="data/trajectories/redteam/semantic153_B_hold_traj.csv")
args = ap.parse_args()

root = pathlib.Path(__file__).resolve().parent.parent
cases = list(csv.DictReader(open(root / args.src, encoding="utf-8")))
bad, names, _ = A.disclosure_ids(args.company)
kw = {"bad": bad, "names": names, "unlock": A.unlock_tools(args.company),
      "terminal": A.terminal_beats(args.company), "max_turns": 8}

print(f"ชุด 153 · บท B + HOLD ต่อเจตนา · {len(cases)} เคส\n")
out, fails = [], 0
for i, c in enumerate(cases, 1):
    hold = HOLD.get(c["group"].split("/")[0], DEFAULT)
    r = A.run_once(args.company, {"turns": ["ใช่ครับ", c["utterance"]], "hold": hold}, "", **kw)
    got = next((str(a.get("result") or "") for _, n, a in reversed(r.get("calls") or [])
                if n == "record_outcome" and a), "") or "-"
    beats = [names.get(x, "?") for x in (r.get("spoken") or [])]
    exp = c["expect"]
    ok = (got != exp[4:].strip()) if exp.startswith("NOT ") else (got == exp)
    fails += not ok
    grade = ("ถูกเลย" if ok and "ask_clarify" not in beats else
             "ถูกหลังถามกลับ" if ok else
             "ผิดทั้งที่ถามกลับ" if "ask_clarify" in beats else "ผิดเลย")
    out.append({"set": c["set"], "id": c["id"], "group": c["group"],
                "utterance": c["utterance"], "hold": hold[0], "expect": exp, "got": got,
                "verdict": "pass" if ok else "fail", "grade": grade,
                "beats": " ".join(beats), "trajectory": render(r.get("trace") or [], names)})
    print(f" {'ok' if ok else '!!'} {i:3}/{len(cases)} {c['id']:34} {exp:10} -> {got:12} {grade}",
          flush=True)

print(f"\n{'='*66}\n  แพ้ {fails}/{len(cases)}")
print("  เกรด:", dict(collections.Counter(r["grade"] for r in out)))
for s in ("seed", "conflict52", "nomoney100"):
    rs = [r for r in out if r["set"] == s]
    if rs:
        print(f"    {s:12} แพ้ {sum(1 for r in rs if r['verdict']=='fail')}/{len(rs)}")
with open(root / args.out, "w", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(out[0])); w.writeheader(); w.writerows(out)
print(f"  เขียน -> {args.out}")
