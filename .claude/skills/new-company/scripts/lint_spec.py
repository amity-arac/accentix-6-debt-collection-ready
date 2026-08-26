# Checks a tenant's <CODE>.company.json for the defects a schema validator cannot see.
"""Lint one tenant spec.

`validate_strict` answers "are the keys legal". These checks answer "does the file
say the same thing to every reader" — which is where the expensive defects have all
been. Each check below exists because it shipped once:

  · a rule naming one beat beside another beat's text id closed promise-to-pay calls
    with "thank you for your payment"
  · a terminal state without the end-of-call tool ended calls recording nothing
  · a sentence no state can reach was written, reviewed, and never spoken
  · `one_of_from` pointing at a field the tool does not return silently never fired
  · a condition stated only in prose ("required when status=rescheduled") is invisible
    to the executor, so the argument is either always required or never

Usage:
    python3 lint_spec.py data/flows/AEON.company.json [--json]
    python3 lint_spec.py data/flows/*.company.json
Exit code is 1 if any ERROR was found (WARN alone exits 0).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))


def _beats_of(state: dict) -> list[str]:
    out: list[str] = []
    for t in state.get("templates", []):
        out += t.get("any_of") or ([t["fine_state"]] if t.get("fine_state") else [])
    return out


def _prose(spec: dict) -> list[tuple[str, str]]:
    """Every free-text field the renderer puts in front of the model."""
    out = [(f"constraints[{i}]", c.get("desc") or "")
           for i, c in enumerate(spec.get("constraints") or [], 1)]
    out += [(f"states[{s.get('id')}].note", s.get("note") or "") for s in spec.get("states", [])]
    for d in spec.get("tools", {}).get("declarations", []):
        out.append((f"tools[{d['name']}].desc", d.get("desc") or ""))
        out.append((f"tools[{d['name']}].gating.note", (d.get("gating") or {}).get("note") or ""))
        for a, m in (d.get("args") or {}).items():
            out.append((f"tools[{d['name']}].args.{a}.desc", (m or {}).get("desc") or ""))
    return out


def lint(spec: dict, catalog: list[dict]) -> list[tuple[str, str, str]]:
    """-> [(level, check, message)] where level is ERROR or WARN."""
    f: list[tuple[str, str, str]] = []
    err = lambda c, m: f.append(("ERROR", c, m))
    warn = lambda c, m: f.append(("WARN", c, m))

    by_id = {e["text_id"]: e["_fine_state"] for e in catalog}
    names = set(by_id.values())
    states = spec.get("states", [])
    state_ids = [s.get("id") for s in states]
    events = set(spec.get("events") or {})
    decls = {d["name"]: d for d in spec.get("tools", {}).get("declarations", [])}

    # --- catalog integrity ---------------------------------------------------
    seen: dict[int, str] = {}
    for e in catalog:
        tid = e["text_id"]
        if tid in seen and seen[tid] != e["_fine_state"]:
            err("duplicate_text_id", f"text_id {tid} used by both `{seen[tid]}` and `{e['_fine_state']}`")
        seen[tid] = e["_fine_state"]

    # --- the beat/id agreement that cost the most ----------------------------
    for where, text in _prose(spec):
        for m in re.finditer(r"([a-z_][a-z_0-9]{3,29})[^\w\n]{0,12}\(?(?:text\s*)?(\d{3,5})\b", text):
            nm, tid = m.group(1), int(m.group(2))
            if nm in names and tid in by_id and by_id[tid] != nm:
                err("name_id_mismatch",
                    f"{where}: writes `{nm}` next to id {tid}, but {tid} is `{by_id[tid]}`")

    # --- a state's note must not prescribe another state's line --------------
    for s in states:
        own, note = set(_beats_of(s)), (s.get("note") or "")
        for m in re.finditer(r"(?:ปิดด้วย|ตอบด้วย|พูด|ใช้)[^.\n]{0,40}?(\d{3,5})", note):
            tid = int(m.group(1))
            fs = by_id.get(tid)
            if fs and fs not in own and not re.search(rf"ห้าม[^.\n]{{0,30}}{tid}", note):
                err("note_prescribes_foreign_beat",
                    f"states[{s['id']}].note tells the agent to use {tid} (`{fs}`), "
                    f"but this state only has {sorted(own)}")

    # --- flow wiring ---------------------------------------------------------
    if len(set(state_ids)) != len(state_ids):
        err("duplicate_state_id", "two states share an id")
    initial = [s["id"] for s in states if s.get("initial")]
    if len(initial) != 1:
        err("initial_state", f"exactly one state must be `initial: true` (found {initial})")
    for s in states:
        for tr in s.get("on") or []:
            if tr.get("to") not in set(state_ids):
                err("dangling_transition", f"states[{s['id']}] --{tr.get('event')}--> {tr.get('to')} (no such state)")
            if tr.get("event") not in events:
                err("undeclared_event", f"states[{s['id']}] uses event `{tr.get('event')}`, not in `events`")
    if initial:
        reach, frontier = {initial[0]}, [initial[0]]
        by_state = {s["id"]: s for s in states}
        while frontier:
            for tr in by_state[frontier.pop()].get("on") or []:
                if tr.get("to") in by_state and tr["to"] not in reach:
                    reach.add(tr["to"]); frontier.append(tr["to"])
        for sid in sorted(set(state_ids) - reach):
            err("unreachable_state", f"states[{sid}] cannot be reached from `{initial[0]}`")

    # --- closing ------------------------------------------------------------
    closers = [n for n, d in decls.items() if (d.get("gating") or {}).get("required_at") == "end_of_call"]
    if len(closers) != 1:
        err("closing_tool", f"exactly one tool must declare gating.required_at: end_of_call (found {closers})")
    else:
        for s in states:
            if (s.get("terminal") or s.get("phase") == "close") and closers[0] not in (s.get("entry_tools") or []):
                err("terminal_records_nothing",
                    f"states[{s['id']}] ends the call without `{closers[0]}` in entry_tools")

    # --- outcomes ------------------------------------------------------------
    declared = set((spec.get("outcomes") or {}).get("results") or {})
    if not declared:
        err("no_outcomes", "outcomes.results is empty — a flow that declares no result can close with none")
    for s in states:
        r = (s.get("outcome") or {}).get("result")
        if r and r not in declared:
            err("outcome_not_declared", f"states[{s['id']}] closes with `{r}`, not in outcomes.results")

    # --- tool argument contracts --------------------------------------------
    for name, d in decls.items():
        for a, m in (d.get("args") or {}).items():
            m = m or {}
            rw = m.get("required_when")
            if rw and rw.get("arg") not in (d.get("args") or {}):
                err("required_when_unknown_arg",
                    f"tools[{name}].args.{a}.required_when names `{rw.get('arg')}`, which {name} does not take")
            src = m.get("one_of_from")
            if src:
                other = decls.get(src.get("tool"))
                if other is None:
                    err("one_of_from_unknown_tool",
                        f"tools[{name}].args.{a}.one_of_from names tool `{src.get('tool')}`, which is not declared")
                elif "returns" in other and src.get("field") not in (other["returns"] or {}):
                    err("one_of_from_unknown_field",
                        f"tools[{name}].args.{a}.one_of_from reads `{src.get('field')}` off "
                        f"`{src.get('tool')}`, which does not declare it in `returns`")
                elif "returns" not in other:
                    warn("returns_undeclared",
                         f"tools[{src['tool']}] has no `returns`, so nothing can check that "
                         f"`{src.get('field')}` is real or build a faithful mock")
            if not m.get("optional") and m.get("required_when"):
                warn("required_when_on_required_arg",
                     f"tools[{name}].args.{a} is required anyway; `required_when` only has an effect "
                     "on an optional argument")
        # a condition that exists only in prose cannot be enforced
        for a, m in (d.get("args") or {}).items():
            desc = ((m or {}).get("desc") or "")
            if re.search(r"บังคับเมื่อ|required when|only when", desc) and not (m or {}).get("required_when"):
                warn("condition_only_in_prose",
                     f"tools[{name}].args.{a}.desc states a condition the executor cannot read — "
                     "declare it as `required_when`")

    # --- what the agent can actually say ------------------------------------
    in_states = {b for s in states for b in _beats_of(s)}
    in_faq = {t.get("fine_state") for r in ((spec.get("faq_routing") or {}).get("routes") or [])
              for t in (r.get("templates") or [])}
    for b in sorted(in_states - names):
        err("beat_without_sentence", f"states reference beat `{b}`, which has no template in the catalog")
    for b in sorted(names - in_states - in_faq):
        warn("sentence_never_used", f"`{b}` has a template but no state or FAQ route can reach it")

    # --- placeholders --------------------------------------------------------
    fillable = set(spec.get("crm_fields") or [])
    for d in decls.values():
        fillable |= set(d.get("args") or {}) | set(d.get("returns") or {})
    fillable |= {"suffix", "q_suffix", "pronoun", "company_phone", "company_name", "agent_name"}
    # A spec that declares what `session_init` returns can be checked exactly; one that
    # does not leaves a legitimate field indistinguishable from a typo, so say so once
    # and report the rest as warnings rather than crying wolf on every shipped tenant.
    init_declared = bool((spec.get("session_init") or {}).get("returns"))
    fillable |= set(((spec.get("session_init") or {}).get("returns") or {}))
    if not init_declared:
        warn("session_init_undeclared",
             "session_init has no `returns`, so a placeholder it supplies cannot be told "
             "apart from a typo — placeholder findings below are warnings only")
    hit = err if init_declared else warn
    # `{{else}}` / `{{if x}}` are template control, not slots — a naive [{ }] match
    # reported the conditional in AEON's disclose line as a missing field.
    CONTROL = {"else", "endif", "end"}
    for e in catalog:
        tpl = re.sub(r"\{\{[^}]*\}\}", " ", e.get("template", ""))
        for ph in re.findall(r"[\[{]([a-z_][a-z_0-9]*)[\]}]", tpl):
            if ph in CONTROL or ph in fillable:
                continue
            hit("unfillable_placeholder",
                f"text {e['text_id']} (`{e['_fine_state']}`) speaks [{ph}], which nothing declares")
    return f


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("specs", nargs="+")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args()

    from demo.server.flow.flowspec import normalize_catalog, resolve_catalog, validate_strict

    worst, report = 0, {}
    for path in a.specs:
        spec = json.loads(pathlib.Path(path).read_text("utf-8"))
        try:
            catalog = normalize_catalog(resolve_catalog(spec), spec)
        except Exception as e:  # noqa: BLE001 — an unreadable catalog is itself the finding
            print(f"  {path}: catalog unreadable — {e}")
            worst = 1
            continue
        findings = [("ERROR", "schema", m) for m in (validate_strict(spec, catalog) or [])]
        findings += lint(spec, catalog)
        report[path] = findings
        if any(lv == "ERROR" for lv, _, _ in findings):
            worst = 1
        if a.json:
            continue
        co = spec.get("company", pathlib.Path(path).stem)
        if not findings:
            print(f"  ✅ {co:<10} ผ่านทุกเช็ค")
            continue
        print(f"  {'❌' if worst else '⚠️ '} {co}")
        for lv, check, msg in findings:
            print(f"       {'ERROR' if lv == 'ERROR' else 'warn '} [{check}] {msg}")
    if a.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
