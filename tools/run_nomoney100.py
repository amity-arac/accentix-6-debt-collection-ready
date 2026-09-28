"""100 ประโยค 'ไม่มีเงินจ่าย' — เจตนาเดียว 7 ชนิดความกำกวม

ทุกเคสคาด `refused` เพราะเจตนาเดียวกันหมด (cannot_pay -> convince -> ปิดสาย)
คอลัมน์ pred_outcome คือค่าที่ทำนายไว้ว่าโมเดลเล็กน่าจะพลาดไปเป็น — ใช้ดูว่า
เวลาพลาด มันพลาดไปทางที่ทำนายไว้หรือทางอื่น ซึ่งบอกว่าสมมติฐานเรื่องความกำกวมถูกไหม
"""
from __future__ import annotations
import argparse, collections, csv, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import redteam_probe_a as A  # noqa: E402

HOLD = ["ก็คือตอนนี้ไม่มีเงินครับ", "ยังไม่มีเงินจ่ายครับ", "ไม่มีจริง ๆ ครับ"]

ap = argparse.ArgumentParser()
ap.add_argument("--company", default="ABC")
ap.add_argument("-n", type=int, default=1)
ap.add_argument("--src", default="data/trajectories/redteam/nomoney100.csv")
ap.add_argument("--csv", default="data/trajectories/redteam/nomoney100_result.csv")
ap.add_argument("--limit", type=int, default=0)
args = ap.parse_args()

root = pathlib.Path(__file__).resolve().parent.parent
cases = list(csv.DictReader(open(root / args.src, encoding="utf-8")))
if args.limit:
    cases = cases[: args.limit]

bad, names, _ = A.disclosure_ids(args.company)
kw = {"bad": bad, "names": names, "unlock": A.unlock_tools(args.company),
      "terminal": A.terminal_beats(args.company), "max_turns": 8}

print(f"\nไม่มีเงินจ่าย · {args.company} · {len(cases)} เคส ({args.n} รอบ) · คาด refused ทุกข้อ\n")
out, fails = [], 0
for c in cases:
    got, asked, beats = [], [], []
    for _ in range(args.n):
        r = A.run_once(args.company, {"turns": ["ใช่ครับ", c["utterance"]], "hold": HOLD},
                       "", **kw)
        got.append(next((str(a.get("result") or "")
                         for _, n, a in reversed(r.get("calls") or [])
                         if n == "record_outcome" and a), "") or "-")
        b = [names.get(x, "?") for x in (r.get("spoken") or [])]
        beats.append(" ".join(b))
        asked.append("ask_clarify" in b)
    ok = all(g == "refused" for g in got)
    fails += not ok
    # อ่านถูกตั้งแต่เทิร์นแรก ≠ อ่านถูกหลังถามกลับ — สองอย่างนี้คนละคุณภาพ
    grade = ("ถูกเลย" if ok and not any(asked) else
             "ถูกหลังถามกลับ" if ok else
             "ผิดทั้งที่ถามกลับ" if any(asked) else "ผิดเลย")
    hit = "" if ok else ("ตรงที่ทำนาย" if c["pred_outcome"] and c["pred_outcome"] in got
                         else "คนละทางกับที่ทำนาย")
    print(f" {'ok' if ok else '!!'} {c['id']} {c['type']:16} {','.join(got):12}"
          f" {grade:18} {hit}   {c['utterance'][:38]}")
    out.append({**c, "got": "|".join(got), "verdict": "pass" if ok else "fail",
                "grade": grade, "beats": beats[0], "vs_prediction": hit})

print(f"\n{'='*70}\n  แพ้ {fails}/{len(cases)}")
byt = collections.defaultdict(lambda: [0, 0])
for r in out:
    byt[r["type"]][0] += 1
    byt[r["type"]][1] += r["verdict"] == "fail"
for k, (n, f) in byt.items():
    print(f"    {k:16} แพ้ {f}/{n}")
print("  เกรด:", dict(collections.Counter(r["grade"] for r in out)))
print("  พลาดไปเป็น:", dict(collections.Counter(
    g for r in out if r["verdict"] == "fail" for g in r["got"].split("|") if g != "refused")))
with open(root / args.csv, "w", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(out[0])); w.writeheader(); w.writerows(out)
print(f"  เขียน -> {args.csv}")
