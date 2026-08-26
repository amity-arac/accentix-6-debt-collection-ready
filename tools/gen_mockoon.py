#!/usr/bin/env python3
"""Generate the Mockoon environment that stands in for a customer's real backend.

    python3 tools/gen_mockoon.py                 # -> mock/aax6-mock.json
    npx -y @mockoon/cli@latest start --data mock/aax6-mock.json --port 3001

WHY A MOCK AT ALL

Every FlowSpec tool is `impl: "http"`, so the app itself holds no business logic: it
calls an API or it replies. That is the shape a real deployment has — but a demo
needs something on the other end of the wire, and a customer needs a written
contract to implement against. This file is both.

EVERYTHING HERE IS DERIVED, NOT DECLARED
Routes come from each spec's own `tools.declarations`; the accepted argument values
come from each spec's own `enum`s; the CRM payload comes from the shipped persona
for that company. A hand-written mock drifts from the specs the moment either
changes — and it did: an invented outcome vocabulary
(`partial_ptp|dispute|callback|…`) rejected `reached`/`tcb`/`tin`, which every debt
spec declares and the RL environment accepts, silently breaking four terminal paths
per company so none of them could close its call. Read the vocabulary from the spec
and that class of bug cannot recur.

Add a company to `data/flows/flow_registry.json` and it gets its routes here with no
edit to this file.

WHAT IT SERVES  (`/:company/...`)

    GET  /:company/init            the call's context (becomes the render slots)
    POST /:company/<tool>          one route per spec-declared tool

The app's default request body (SpecBackend._dispatch_http):

    {"tool": "payment_date",
     "args": {"amount": 4500, "date": "2026-06-02 (Tuesday)", ...},
     "ref":  {"msisdn": "081-234-5678", "last_4_digits": "1234"}}

The mock reproduces the SIGNALS the policy was trained to read, not just 200s: a
malformed date returns `date_format_invalid`, an out-of-vocabulary enum value
returns `invalid_<arg>`. A backend that accepted everything would teach the model
nothing and mask its mistakes.
"""
from __future__ import annotations

import json
import pathlib
import uuid

REPO = pathlib.Path(__file__).resolve().parents[1]
FLOWS = REPO / "data" / "flows"
OUT = REPO / "mock" / "aax6-mock.json"
PORT = 3001

# The canonical date shape every date-taking tool validates — the same string the
# specs put in `tools.validation.date_format` and the renderer shows the model.
DATE_RE = r"^\d{4}-\d{2}-\d{2} \(\w+\)$"

# Facts about the COMPANY rather than the customer, so they sit beside the persona
# instead of inside it. Templates speak both ([company_phone], SKL's [num] SLA).
COMPANY_EXTRAS: dict[str, dict] = {
    "AEON":  {"company_phone": "02-035-6666", "num": 3},
    "KBANK": {"company_phone": "02-888-8888", "num": 3},
    "SKL":   {"company_phone": "02-762-8888", "num": 3},
    "AMT":   {"company_phone": "02-514-4141", "num": 3},
}


# --------------------------------------------------------------------------- #
# Sources of truth
# --------------------------------------------------------------------------- #
def load_specs() -> dict[str, dict]:
    reg = json.loads((FLOWS / "flow_registry.json").read_text(encoding="utf-8"))
    return {c: json.loads((FLOWS / e["spec"]).read_text(encoding="utf-8"))
            for c, e in reg.items()}


def persona_for(company: str) -> dict:
    """The shipped demo persona's CRM row, so the mock and the app agree by
    construction rather than by two people remembering to edit both."""
    import sys
    sys.path.insert(0, str(REPO))
    from demo.server import sessions

    rows = [c for c in sessions.list_cases()
            if str(c.get("company", "")).upper() == company.upper()]
    if not rows:
        return {}
    cd = dict(sessions.case_customer_data(rows[0]["id"]))
    cd.update(COMPANY_EXTRAS.get(company.upper(), {}))
    # The confirm-close templates need both ends of a visit window; the CRM row
    # stores only the start, so derive the end rather than let those templates read
    # "[visit_time_end]" to the patient.
    start = str(cd.get("appointment_time") or "")
    if start and ":" in start and not cd.get("visit_time_end"):
        try:
            h, m = (int(x) for x in start.split(":")[:2])
            cd.setdefault("visit_time_start", start)
            cd["visit_time_end"] = f"{(h + 1) % 24:02d}:{m:02d}"
        except ValueError:
            pass
    return {k: v for k, v in cd.items()
            if not str(k).startswith("_") and v is not None}


# --------------------------------------------------------------------------- #
# Mockoon building blocks
# --------------------------------------------------------------------------- #
def _resp(body: dict | str, *, rules: list | None = None, default: bool = False,
          label: str = "", status: int = 200) -> dict:
    return {
        "uuid": str(uuid.uuid4()),
        "body": body if isinstance(body, str) else json.dumps(body, ensure_ascii=False, indent=2),
        "latency": 0, "statusCode": status, "label": label,
        "headers": [{"key": "Content-Type", "value": "application/json"}],
        "bodyType": "INLINE", "filePath": "", "databucketID": "",
        "sendFileAsBody": False, "rules": rules or [], "rulesOperator": "AND",
        "disableTemplating": False, "fallbackTo404": False, "default": default,
        "crudKey": "id", "callbacks": [],
    }


def _route(method: str, endpoint: str, responses: list[dict], doc: str = "") -> dict:
    return {"uuid": str(uuid.uuid4()), "type": "http", "documentation": doc,
            "method": method, "endpoint": endpoint, "responses": responses,
            "responseMode": None, "streamingMode": None, "streamingInterval": 0}


def _company_rule(company: str) -> dict:
    return {"target": "params", "modifier": "company", "value": company,
            "operator": "equals", "invert": False}


def _body_regex(path: str, regex: str, *, invert: bool = False) -> dict:
    return {"target": "body", "modifier": path, "value": regex,
            "operator": "regex", "invert": invert}


# --------------------------------------------------------------------------- #
# Per-tool responses, derived from the declaration
# --------------------------------------------------------------------------- #
def _date_args(decl: dict) -> list[str]:
    """Args this tool declares that carry a canonical date."""
    return [a for a, m in (decl.get("args") or {}).items()
            if a == "date" or str(m.get("format", "")).lower().startswith("yyyy")]


def _enum_args(decl: dict) -> dict[str, list]:
    return {a: m["enum"] for a, m in (decl.get("args") or {}).items() if m.get("enum")}


def _echo(decl: dict) -> dict:
    """Echo the call's own args, so the merged response updates the render context
    with exactly what was agreed (SpecBackend._merge_context)."""
    return {a: "{{body 'args.%s'}}" % a for a in (decl.get("args") or {})}


def _thai_date(canonical: str) -> str:
    """Render "2026-05-25 (Monday)" the way the reply renderer would, so a date the
    API supplies mid-call sounds identical to one that came from the CRM row."""
    import sys
    sys.path.insert(0, str(REPO))
    from simulator import datetime_utils
    try:
        return datetime_utils.render_date_thai(canonical)
    except Exception:      # noqa: BLE001 — a mock must still generate
        return canonical


# The doctor is on duty Mon-Fri, so offer the coming Mon/Tue/Wed. `i` is the ISO
# weekday (1=Mon), so `8 - i` always lands on next Monday.
_NEXT_MON = "(subtract 8 (now 'i'))"
_NEXT_WEEK = [_NEXT_MON, "(add %s 1)" % _NEXT_MON, "(add %s 2)" % _NEXT_MON]
_NEXT_WEEK_TH = ["จันทร์", "อังคาร", "พุธ"]
_TH_MONTHS = ("'มกราคม' 'กุมภาพันธ์' 'มีนาคม' 'เมษายน' 'พฤษภาคม' 'มิถุนายน' "
              "'กรกฎาคม' 'สิงหาคม' 'กันยายน' 'ตุลาคม' 'พฤศจิกายน' 'ธันวาคม'")


def _shift_expr(offset: str, fmt: str) -> str:
    """Bare subexpression — for nesting inside another helper call. Wrapping this in
    `{{ }}` and then nesting it is a Handlebars parse error, not a value."""
    return "(dateTimeShift date=(now) days=%s format='%s')" % (offset, fmt)


def _shift(offset: str, fmt: str) -> str:
    return "{{%s}}" % _shift_expr(offset, fmt)[1:-1]


def _slot_iso(offset: str) -> str:
    """Canonical `YYYY-MM-DD (Weekday)` — the format the backend validates."""
    return "%s (%s)" % (_shift(offset, "yyyy-MM-dd"), _shift(offset, "EEEE"))


def _slot_thai(offset: str, weekday_th: str) -> str:
    """Same date spoken, matching `datetime_utils.render_date_thai`. The weekday is
    a literal because the offset is built to land on it; only the month needs a
    lookup, and Handlebars indexes from 0 while date-fns `M` starts at 1."""
    month = "{{lookup (array %s) (subtract %s 1)}}" % (
        _TH_MONTHS, _shift_expr(offset, "M"))
    return "วัน%sที่ %s %s %s" % (weekday_th, _shift(offset, "d"), month,
                                  _shift(offset, "yyyy"))


def tool_responses(decl: dict, crm: dict) -> list[dict]:
    """Rejections (rule-matched) first, then the success response."""
    name = decl["name"]
    out: list[dict] = []

    for arg in _date_args(decl):
        rules = [_body_regex(f"args.{arg}", DATE_RE, invert=True)]
        if bool(((decl.get("args") or {}).get(arg) or {}).get("optional")):
            # Same trap the enum rules already avoid: an OPTIONAL date that was left
            # out arrives as "" and fails a format regex, so the call is rejected for
            # not supplying something it never had to supply. Gate on presence first.
            rules.insert(0, {"target": "body", "modifier": f"args.{arg}",
                             "value": "", "operator": "null", "invert": True})
        out.append(_resp(
            {"error": "date_format_invalid", "arg": arg,
             "expected": "YYYY-MM-DD (Weekday)", "got": "{{body 'args.%s'}}" % arg},
            rules=rules, label=f"{arg} not canonical"))

    for arg, values in _enum_args(decl).items():
        rules = [_body_regex(f"args.{arg}", "^(%s)$" % "|".join(values), invert=True)]
        if bool(((decl.get("args") or {}).get(arg) or {}).get("optional")):
            # An omitted optional arg is not a violation — but a regex rule against
            # an ABSENT field still fails, and `invert` then turns that failure into
            # a rejection. So gate on presence first: "channel" left out of a
            # pay-today commitment must not come back invalid_channel.
            rules.insert(0, {"target": "body", "modifier": f"args.{arg}",
                             "value": "", "operator": "null", "invert": True})
        out.append(_resp(
            {"error": f"invalid_{arg}", "got": "{{body 'args.%s'}}" % arg,
             "valid": values},
            rules=rules, label=f"{arg} out of vocabulary"))

    if name.startswith("check_account"):
        body = {k: crm[k] for k in
                ("loan_type", "total_amount_due", "minimum_payment_due",
                 "due_date", "due_status") if crm.get(k) is not None}
        body["status"] = crm.get("case_status", "normal")
    elif "datetime" in name and not (decl.get("args") or {}):
        body = {"datetime": "{{now 'yyyy-MM-dd'}} ({{now 'EEEE'}})",
                "time": "{{now 'HH:mm'}}"}
    elif name.startswith("check_doctor"):
        # Keyed on the doctor's NAME, which the agent already has from the CRM.
        # It used to take an optional `date`; the model had no date to give, sent
        # "" , and the mock validated the empty string against the canonical date
        # format and rejected it — so the lookup never succeeded and the reply went
        # out with `[available_dates_text]` still in the sentence.
        body = {k: crm[k] for k in ("doctor_name", "doctor_schedule")
                if crm.get(k) is not None}
        body["name"] = "{{body 'args.name'}}"
        # The spec forbids inventing on-duty dates, so the API has to state them —
        # and a template cannot speak a JSON list, so it also returns the phrasing.
        # Without this the agent had the real dates in context and still offered a
        # weekly pattern ("จันทร์-ศุกร์ 9:00-16:00"), which is the guess the spec bans.
        # Dates must be resolved AT REQUEST TIME, not baked in here. Hardcoded
        # 2026-05 slots were three months behind the `วันนี้` the session puts in
        # the prompt, so "พุธหน้า" computed from today landed nowhere in the offer
        # and the agent sent new_slot="" — it could only match a date when the
        # customer echoed a weekday word verbatim.
        body["available_dates"] = [_slot_iso(o) for o in _NEXT_WEEK]
        body["available_dates_text"] = " / ".join(
            _slot_thai(o, th) for o, th in zip(_NEXT_WEEK, _NEXT_WEEK_TH))
    else:
        body = {"recorded": True,
                f"{name}_id": "%s-{{now 't'}}" % name[:3].upper(), **_echo(decl)}
    out.append(_resp(body, default=True, label="ok"))
    return out


def build() -> dict:
    specs = load_specs()
    crm = {c: persona_for(c) for c in specs}
    routes: list[dict] = []

    routes.append(_route(
        "get", ":company/init",
        [_resp(crm[c], rules=[_company_rule(c)], label=f"{c} context") for c in specs]
        + [_resp({"error": "unknown_company"}, default=True, status=404,
                 label="unknown company")],
        doc="Call context fetched once at session start (spec.session_init)"))

    # One route per DISTINCT tool name; the company URL param selects the behaviour,
    # so two specs may declare the same tool with different args or enums.
    by_name: dict[str, dict[str, dict]] = {}
    for comp, spec in specs.items():
        for d in (spec.get("tools") or {}).get("declarations", []):
            by_name.setdefault(d["name"], {})[comp] = d

    for name, per_company in sorted(by_name.items()):
        responses: list[dict] = []
        for comp, decl in sorted(per_company.items()):
            for r in tool_responses(decl, crm.get(comp, {})):
                r["rules"] = [_company_rule(comp)] + r["rules"]
                r["default"] = False
                r["label"] = f"{comp}: {r['label']}"
                responses.append(r)
        # A company with no rule of its own still gets a working tool. The mock
        # exists so a spec can be written and run without any backend work; a 404
        # default meant every company created after this file was generated — every
        # Builder company, every customer's own — failed on its FIRST tool call,
        # and the model, seeing an error it could not act on, stopped replying.
        # The generic response is deliberately shaped like the per-company ones:
        # ok + a ref + the echoed args, so a new spec sees the same contract.
        responses.append(_resp({"ok": True,
                                "ref": f"{name.upper()[:6]}-{{{{now 't'}}}}",
                                "tool": name,
                                "company": "{{urlParam 'company'}}",
                                "note": "generic mock response — this company has no "
                                        "tool-specific mock rule"},
                               default=True, status=200,
                               label="generic (no company-specific rule)"))
        routes.append(_route("post", f":company/{name}", responses,
                             doc=f"{name} — declared by {', '.join(sorted(per_company))}"))

    return {
        "uuid": str(uuid.uuid4()), "lastMigration": 33,
        "name": "AAX6 mock backend", "endpointPrefix": "", "latency": 0,
        "port": PORT, "hostname": "", "folders": [], "routes": routes,
        "rootChildren": [{"type": "route", "uuid": r["uuid"]} for r in routes],
        "proxyMode": False, "proxyHost": "", "proxyRemovePrefix": False,
        "tlsOptions": {"enabled": False, "type": "CERT", "pfxPath": "",
                       "certPath": "", "keyPath": "", "caPath": "", "passphrase": ""},
        "cors": True, "headers": [],
        "proxyReqHeaders": [{"key": "", "value": ""}],
        "proxyResHeaders": [{"key": "", "value": ""}],
        "data": [], "callbacks": [],
    }


if __name__ == "__main__":
    env = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(env, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO)} — {len(env['routes'])} routes, port {PORT}")
    for r in env["routes"]:
        print(f"  {r['method'].upper():5} /{r['endpoint']:32} {len(r['responses'])} responses")
