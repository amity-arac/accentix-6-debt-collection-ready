# Drives scripted conversations against the running demo so a tenant hears the agent.
"""Talk to a tenant's agent and print what the caller would hear.

A diagram says what the flow is; only a transcript answers "is this what you wanted".
Each scenario is a list of customer lines; the script opens a session, plays them, and
prints every reply, tool call and app warning in order — the same stream the browser
shows, so nothing is summarised away.

The demo and its mock must already be running (run_demo.sh / run_mock.sh).

Usage:
    python3 smoke_company.py SHOP --case TC-SHOP-BUILD-001 \
        --say "ขอเลื่อนหน่อยครับ" "ขอเป็นวันที่ 28 สิงหา" "โอเคครับ"
    python3 smoke_company.py SHOP --case TC-SHOP-BUILD-001 --scenarios scenarios.json

`scenarios.json` is `[{"name": "...", "says": ["...", "..."]}, ...]`.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import urllib.request

BASE = os.environ.get("AAX6_DEMO_URL", "http://127.0.0.1:4100")


def _post(path: str, body: dict) -> list[dict]:
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    out = []
    for line in urllib.request.urlopen(req, timeout=300).read().decode().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def _open(case_id: str, model: str) -> str:
    url = f"{BASE}/api/session?case_id={case_id}&flow=1&model={model}"
    for line in urllib.request.urlopen(url, timeout=120).read().decode().splitlines():
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        if o.get("session_id"):
            return o["session_id"]
    raise SystemExit("the demo did not return a session — is it running?")


def _show(objs: list[dict], keep: tuple[str, ...]) -> dict:
    """Print the stream; return a small tally the caller can assert on."""
    tally = {"replies": 0, "tools": [], "warnings": 0, "rejected": 0}
    for o in objs:
        h = o.get("hop") or {}
        kind = h.get("kind")
        if kind == "reply":
            tally["replies"] += 1
            print("   AGENT %-14s %s" % (h.get("text_ids"), (h.get("text") or "")[:88]))
        elif kind == "tool_call" and h.get("name") != "reply":
            tally["tools"].append(h["name"])
            print("   TOOL   %-13s %s" % (h["name"], json.dumps(h.get("args") or {}, ensure_ascii=False)[:72]))
        elif kind == "tool_result":
            r = h.get("result") or {}
            if r.get("error"):
                tally["rejected"] += 1
            shown = {k: r[k] for k in keep if k in r} or (
                {"error": r["error"]} if r.get("error") else {})
            if shown:
                print("   RES    %s" % json.dumps(shown, ensure_ascii=False)[:88])
        elif kind == "warning" or o.get("warning"):
            tally["warnings"] += 1
            w = h if kind == "warning" else o["warning"]
            print("   ⚠      %s" % (w.get("text") or json.dumps(w, ensure_ascii=False))[:88])
    return tally


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("company")
    ap.add_argument("--case", required=True, help="persona id, e.g. TC-SHOP-BUILD-001")
    ap.add_argument("--model", default=os.environ.get("AAX6_FLOW_MODEL", "grpo400"))
    ap.add_argument("--say", nargs="*", default=None, help="one scenario, given inline")
    ap.add_argument("--scenarios", help="json file of named scenarios")
    ap.add_argument("--keep", default="in_range,valid_dates,recorded,result,available_dates",
                    help="tool-result fields worth printing")
    a = ap.parse_args()

    if a.scenarios:
        scenarios = json.loads(pathlib.Path(a.scenarios).read_text("utf-8"))
    elif a.say:
        scenarios = [{"name": "scenario", "says": a.say}]
    else:
        raise SystemExit("give --say or --scenarios")

    keep = tuple(x.strip() for x in a.keep.split(",") if x.strip())
    for scn in scenarios:
        print("\n════", scn.get("name", "scenario"))
        sid = _open(a.case, a.model)
        totals = _show(_post(f"/api/session/{sid}/opening", {}), keep)
        for line in scn["says"]:
            print("   CUST:  %s" % line)
            t = _show(_post(f"/api/session/{sid}/turn", {"message": line}), keep)
            for k in ("replies", "warnings", "rejected"):
                totals[k] += t[k]
            totals["tools"] += t["tools"]
        print("   ── %d reply · tools %s · %d warning · %d tool ถูกปฏิเสธ" % (
            totals["replies"], totals["tools"] or "—", totals["warnings"], totals["rejected"]))


if __name__ == "__main__":
    main()
