#!/usr/bin/env python3
"""Remove the constraint types that duplicate something the spec already declares.

Each of these five said, in a second place, a thing the states / tools / faq_routing
section already say. A second copy is not free: it can disagree with the first, and
one of them did — the gold eval read `max_templates_per_reply.exceptions` (a hand-kept
list of the chain states) instead of the states themselves, and judged replies against
a stale list.

  max_templates_per_reply  -> states[].templates (is_chain_state derives the same pairs)
  resume_after_interrupt   -> faq_routing.routes[].then == "resume"
  require_tool_before_end  -> tools.declarations[].gating.required_at == "end_of_call"
  tool_pair                -> tools.declarations[].gating.must_precede
  forbid_after_event       -> the state machine cannot re-enter those states anyway

`tool_pair` carries one thing gating does not — `args_must_match` — so it is only
dropped when the ordering it states is already covered by a must_precede declaration
AND it asks for no argument matching. Anything else stays and is reported.

    python3 tools/drop_dup_constraints.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]
FLOWS = REPO / "data" / "flows"

DUP_TYPES = {
    "max_templates_per_reply": "states[].templates (chain)",
    "resume_after_interrupt": 'faq_routing.routes[].then="resume"',
    "require_tool_before_end": 'gating.required_at="end_of_call"',
    "forbid_after_event": "state machine (states cannot be re-entered)",
}


def must_precede_pairs(spec: dict) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for d in (spec.get("tools") or {}).get("declarations", []):
        mp = (d.get("gating") or {}).get("must_precede")
        for other in ([mp] if isinstance(mp, str) else list(mp or [])):
            out.add((d["name"], other))
    return out


def end_of_call_tools(spec: dict) -> set[str]:
    return {d["name"] for d in (spec.get("tools") or {}).get("declarations", [])
            if (d.get("gating") or {}).get("required_at") == "end_of_call"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    for path in sorted(FLOWS.glob("*.json")):
        if path.name in ("flow_registry.json", "intent_cues.json"):
            continue
        try:
            spec = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        cons = spec.get("constraints")
        if not isinstance(cons, list):
            continue

        pairs = must_precede_pairs(spec)
        closers = end_of_call_tools(spec)
        kept, dropped = [], []
        for c in cons:
            t = c.get("type")
            if t in DUP_TYPES:
                if t == "require_tool_before_end" and c.get("tool") not in closers:
                    kept.append(c)          # nothing else declares this closer
                    continue
                dropped.append((t, DUP_TYPES[t]))
                continue
            if t == "tool_pair":
                covered = (c.get("first"), c.get("second")) in pairs or \
                          (c.get("second"), c.get("first")) in pairs
                if covered and not c.get("args_must_match"):
                    dropped.append((t, "gating.must_precede"))
                    continue
            kept.append(c)

        if not dropped:
            continue
        print(f"{path.name}: {len(cons)} -> {len(kept)}")
        for t, why in dropped:
            print(f"    ตัด {t:26} (ซ้ำกับ {why})")
        if not args.dry_run:
            spec["constraints"] = kept
            path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")


if __name__ == "__main__":
    main()
