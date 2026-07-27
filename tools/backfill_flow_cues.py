#!/usr/bin/env python3
"""Backfill event `cues` in every data/flows/*-outbound-remind.json from the
intent cue library (data/flows/intent_cues.json).

For each event whose name is known to the library, merge the library's cues in
(existing curated cues are kept and take priority; library cues are appended,
deduped, capped). Custom event names not in the library are left untouched.

Run after build_cue_library.py:  python tools/backfill_flow_cues.py [--dry]
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FLOWS = ROOT / "data" / "flows"
LIB = json.loads((FLOWS / "intent_cues.json").read_text(encoding="utf-8"))
CAP = 14
DRY = "--dry" in sys.argv


def merge(existing: list[str], lib: list[str]) -> list[str]:
    out = list(existing)
    for p in lib:
        if p not in out:
            out.append(p)
    return out[:CAP]


def main() -> None:
    files = sorted(FLOWS.glob("*-outbound-remind.json"))
    for fp in files:
        if fp.stem.startswith("_"):  # skip _TEMPLATE
            continue
        spec = json.loads(fp.read_text(encoding="utf-8"))
        events = spec.get("events") or {}
        touched = []
        for name, ev in events.items():
            lib_cues = LIB.get(name)
            if not lib_cues:
                continue
            before = ev.get("cues") or []
            after = merge(before, lib_cues)
            if after != before:
                ev["cues"] = after
                touched.append(f"{name}({len(before)}->{len(after)})")
        if touched:
            print(f"{fp.name}: {', '.join(touched)}")
            if not DRY:
                fp.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            print(f"{fp.name}: (no known events / already full)")
    if DRY:
        print("\n[dry run — nothing written]")


if __name__ == "__main__":
    main()
