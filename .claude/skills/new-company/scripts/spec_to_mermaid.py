# Renders a tenant spec's state machine as a Mermaid flowchart for human review.
"""Turn `<CODE>.company.json` into a diagram a non-engineer can approve.

The spec is the contract, but nobody reads a 400-line JSON to answer "is this the
call you wanted". Each node carries what the caller will actually hear (the beat and
its text ids), what the system does there (entry tools), and how the call ends
(outcome), so the picture is checkable against the requirement rather than pretty.

Usage:
    python3 spec_to_mermaid.py data/flows/SHOP.company.json            # ```mermaid block
    python3 spec_to_mermaid.py data/flows/SHOP.company.json --raw      # no fence
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))

PHASE_FILL = {"opening": "#e8f0fe", "main": "#fff4e5", "close": "#e8f5e9"}


def _esc(s: str) -> str:
    """Mermaid node labels break on quotes and brackets."""
    return str(s).replace('"', "'").replace("[", "(").replace("]", ")")


def render(spec: dict, catalog: list[dict]) -> str:
    ids_of: dict[str, list[int]] = {}
    for e in catalog:
        ids_of.setdefault(e["_fine_state"], []).append(e["text_id"])

    lines = ["flowchart TD"]
    for st in spec["states"]:
        beats: list[str] = []
        for t in st.get("templates", []):
            beats += t.get("any_of") or ([t["fine_state"]] if t.get("fine_state") else [])
        say = " / ".join(
            "%s (%s)" % (b, ",".join(str(i) for i in sorted(ids_of.get(b, []))) or "ไม่มีประโยค")
            for b in beats) or "—"
        parts = [f"<b>{_esc(st['id'])}</b>", _esc(say)]
        if st.get("entry_tools"):
            parts.append("<i>%s</i>" % _esc(", ".join(st["entry_tools"])))
        oc = st.get("outcome") or {}
        if oc.get("result"):
            parts.append("→ %s" % _esc(oc["result"]))
        label = "<br/>".join(parts)
        shape = f"([\"{label}\"])" if st.get("terminal") else f"[\"{label}\"]"
        lines.append(f"  {st['id']}{shape}")

    for st in spec["states"]:
        for tr in st.get("on") or []:
            lines.append("  %s -->|%s| %s" % (st["id"], _esc(tr.get("event", "")), tr.get("to")))

    routes = (spec.get("faq_routing") or {}).get("routes") or []
    if routes:
        # FAQ routes are interrupts: they fire from any non-terminal state, so drawing an
        # edge from each one would bury the flow. One node listing them keeps the fact
        # visible without pretending it is a transition.
        resume = [r["intent"] for r in routes if r.get("then") == "resume"]
        ends = [r["intent"] for r in routes if r.get("then") != "resume"]
        txt = ["<b>FAQ (แทรกได้ทุกจังหวะ)</b>"]
        if resume:
            txt.append("ตอบแล้วกลับ flow: " + _esc(", ".join(resume)))
        if ends:
            txt.append("ตอบแล้วจบสาย: " + _esc(", ".join(ends)))
        lines.append('  faq_note["%s"]' % "<br/>".join(txt))
        lines.append("  style faq_note fill:#f3e5f5,stroke:#999,stroke-dasharray: 4 3")

    for st in spec["states"]:
        lines.append("  style %s fill:%s,stroke:#999" % (st["id"], PHASE_FILL.get(st.get("phase"), "#eeeeee")))
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("--raw", action="store_true", help="omit the ```mermaid fence")
    a = ap.parse_args()

    from demo.server.flow.flowspec import normalize_catalog, resolve_catalog

    spec = json.loads(pathlib.Path(a.spec).read_text("utf-8"))
    body = render(spec, normalize_catalog(resolve_catalog(spec), spec))
    print(body if a.raw else "```mermaid\n%s\n```" % body)


if __name__ == "__main__":
    main()
