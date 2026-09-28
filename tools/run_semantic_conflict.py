"""เคส 'เจตนาเดียว ผิวคำขัดกัน' — วัดว่าโมเดลอ่านเจตนาถูกไหม ไม่ใช่ว่ามันหลีกเลี่ยงคำต้องห้ามได้ไหม

ต่างจาก redteam_suite: เกณฑ์ที่นี่เป็น expect_outcome (เชิงบวก) ไม่ใช่ forbid_* — เพราะทุกเคส
มีเจตนาเดียว คำตอบที่ถูกจึงมีอยู่จริง และเป็นสิ่งที่ suite เดิมวัดไม่ได้เลย

ทุกเคสมีฝาแฝดที่พูดเจตนาเดียวกันแบบตรง ๆ: แพ้ทั้งคู่ = แยกเจตนาไม่ออกตั้งแต่ต้น ·
แพ้เฉพาะตัวที่ขัดกัน = ปัญหาอยู่ที่ภาษา
"""
from __future__ import annotations
import argparse, csv, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import redteam_probe_a as A  # noqa: E402

HOLD = ["ก็ตามที่บอกครับ", "ครับ", "ก็แบบนั้นแหละครับ"]


def load(path: str) -> list[dict]:
    out = []
    for r in csv.DictReader(open(path, encoding="utf-8")):
        base = f"{r['intent']}|{r['slot']}"
        out.append({"id": base, "kind": "conflict", "expect": r["expect"],
                    "turns": ["ใช่ครับ", r["utterance"]], "note": r["note"]})
        out.append({"id": base + "|twin", "kind": "twin", "expect": r["expect"],
                    "turns": ["ใช่ครับ", r["twin"]], "note": "ฝาแฝดพูดตรง ๆ"})
    return out


def verdict(expect: str, got: str) -> str:
    if not got:
        return "no_outcome"
    if expect.startswith("NOT "):
        return "pass" if got != expect[4:].strip() else "fail"
    return "pass" if got == expect else "fail"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", default="ABC")
    ap.add_argument("-n", type=int, default=1)
    ap.add_argument("--draft", default="../data/trajectories/redteam/semantic_conflict_draft.csv")
    ap.add_argument("--csv", default="")
    args = ap.parse_args()

    bad, names, _ = A.disclosure_ids(args.company)
    kw = {"bad": bad, "names": names, "unlock": A.unlock_tools(args.company),
          "terminal": A.terminal_beats(args.company), "max_turns": 8}
    probes = load(str(pathlib.Path(__file__).resolve().parent / args.draft))
    print(f"\nเจตนาเดียว ผิวคำขัดกัน · {args.company} · {len(probes)} เคส ({args.n} รอบ)\n")

    rows, fails = [], 0
    for p in probes:
        got, vs = [], []
        for _ in range(args.n):
            r = A.run_once(args.company, {"turns": p["turns"], "hold": HOLD}, "", **kw)
            o = next((str(a.get("result") or "")
                      for _, n, a in reversed(r.get("calls") or [])
                      if n == "record_outcome" and a), "")
            got.append(o or "-")
            vs.append(verdict(p["expect"], o))
        bad_run = "fail" in vs
        fails += bad_run
        mark = "!!" if bad_run else "ok"
        print(f" {mark} {p['id']:44} คาด {p['expect']:10} ได้ {','.join(got)}")
        rows.append({"id": p["id"], "kind": p["kind"], "utterance": p["turns"][1],
                     "expect": p["expect"], "got": "|".join(got),
                     "verdict": "fail" if bad_run else "pass", "note": p["note"]})
    print(f"\n{'='*62}\n  แพ้อย่างน้อย 1 รอบ {fails}/{len(probes)}")
    if args.csv:
        with open(args.csv, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
        print(f"  เขียน {len(rows)} แถว -> {args.csv}")


if __name__ == "__main__":
    main()
