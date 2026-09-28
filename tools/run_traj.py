"""รันเคสจาก CSV แล้วเก็บ trajectory เต็มลงไฟล์ — ใช้กับเคสที่แพ้เพื่อไปอ่านว่าพังตรงไหน"""
from __future__ import annotations
import argparse, csv, json, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import redteam_probe_a as A  # noqa: E402

HOLD = ["ก็ตามที่บอกครับ", "ก็แบบนั้นแหละครับ", "ครับ"]


def render(trace: list[tuple], names: dict) -> str:
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
            s = r.get("error") or ("ok" if isinstance(r, dict) else str(r)) if isinstance(r, dict) else str(r)
            out.append(f"          -> {s}")
    return "\n".join(out)


ap = argparse.ArgumentParser()
ap.add_argument("--src", required=True)
ap.add_argument("--company", default="ABC")
ap.add_argument("--only-fail", action="store_true")
ap.add_argument("--out", required=True)
args = ap.parse_args()

root = pathlib.Path(__file__).resolve().parent.parent
cases = list(csv.DictReader(open(root / args.src, encoding="utf-8")))
if args.only_fail:
    cases = [c for c in cases if c.get("verdict") == "fail"]

bad, names, _ = A.disclosure_ids(args.company)
kw = {"bad": bad, "names": names, "unlock": A.unlock_tools(args.company),
      "terminal": A.terminal_beats(args.company), "max_turns": 8}

print(f"เก็บ trajectory {len(cases)} เคส -> {args.out}")
rows = []
for i, c in enumerate(cases, 1):
    r = A.run_once(args.company, {"turns": ["ใช่ครับ", c["utterance"]], "hold": HOLD}, "", **kw)
    got = next((str(a.get("result") or "") for _, n, a in reversed(r.get("calls") or [])
                if n == "record_outcome" and a), "") or "-"
    rows.append({"set": c.get("set", ""), "id": c["id"], "group": c.get("group", ""),
                 "utterance": c["utterance"], "expect": c["expect"], "got": got,
                 "verdict": "pass" if got == c["expect"] else "fail",
                 "beats": " ".join(names.get(x, "?") for x in r.get("spoken") or []),
                 "trajectory": render(r.get("trace") or [], names)})
    print(f"  {i:3}/{len(cases)} {c['id']:8} {got}", flush=True)

with open(root / args.out, "w", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
print("เขียนแล้ว", args.out)
