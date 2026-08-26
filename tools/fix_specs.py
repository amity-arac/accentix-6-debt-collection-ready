#!/usr/bin/env python3
"""Apply the structural corrections the spec audit found, to every shipped spec.

    python3 tools/fix_specs.py [--dry-run]

Each fix below cites the defect it closes. Written as a script rather than done by
hand because the same defect recurs across companies and across AEON's pinned
variants (`__v11`, `__v11.1`, `__v11.2`, `__v12`) — the variant the shipped model
actually loads is `__v11.2`, so a hand-edit of the canonical file alone changes
nothing at runtime.
"""
from __future__ import annotations

import collections
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))   # the validator's constraint vocabulary lives in the app
FLOWS = REPO / "data" / "flows"
DRY = "--dry-run" in sys.argv

# Bare acknowledgements. Every debt spec's own KYC constraint says these do NOT
# confirm identity ("คำรับสั้นๆ กำกวมอย่าง 'ครับ / ค่ะ / อือ / อ๋อ / โอเค' ไม่นับว่า
# ยืนยันตัวตน"), yet they were listed as `name_confirmed` cues — so a bare "ครับ"
# from whoever answered the phone fired check_account_status and disclosed the
# balance. The cue list and the constraint cannot both be right.
BARE_ACKS = {"ครับ", "ค่ะ", "คับ", "จ้า", "อือ", "อ๋อ", "โอเค", "โอเคค่ะ", "โอเคครับ",
             "ได้ครับ", "ได้ค่ะ", "okay", "ok", "right", "yes", "yeah", "uh-huh"}

# Not illness. These route to a "get well soon, but please pay today" script; the
# specs already have correct handlers (mourning / wrong-party) for them.
NOT_ILLNESS = {"ล้มละลาย", "ติดคุก", "เสียชีวิต", "ตายแล้ว", "bankrupt", "in jail",
               "deceased", "จำคุก", "ถูกจับ"}

# Tools whose date argument must be canonical. `get_current_datetime` is what makes
# it canonical, so it has to run first — and `must_precede` is the only ordering key
# SpecGate actually enforces (`required_before` reaches the prompt and nothing else).
DATE_CONSUMERS = ["record_verbal_commitment", "payment_date", "callback_datetime"]


def log(*a) -> None:
    print(*a)


def fix_spec(company: str, path: pathlib.Path, catalog_name: str,
             catalog: list[dict]) -> list[str]:
    spec = json.loads(path.read_text(encoding="utf-8"),
                      object_pairs_hook=collections.OrderedDict)
    decls = {d["name"]: d for d in (spec.get("tools") or {}).get("declarations", [])}
    fine_states = {c.get("_fine_state") for c in catalog if c.get("_fine_state")}
    changes: list[str] = []

    # 1. `catalog: "__inline__"` is not a path any loader resolves. sessions.py does
    #    REPO_ROOT / spec["catalog"] whenever a v12 model is selected, so this
    #    raises FileNotFoundError and the session cannot be created at all. The
    #    duplicated `catalog_inline` copy is read by nothing and had already drifted
    #    from the served file (it still carried Thai unit-doubling fixed weeks ago),
    #    so the next person to edit the spec would edit the dead copy.
    if spec.get("catalog") in (None, "", "__inline__"):
        spec["catalog"] = f"data/pre-scripts/{catalog_name}"
        changes.append(f'catalog -> data/pre-scripts/{catalog_name}')
    if "catalog_inline" in spec:
        spec.pop("catalog_inline")
        changes.append("removed dead catalog_inline duplicate")

    # 2. Ordering that is actually enforced.
    gcd = decls.get("get_current_datetime")
    if gcd is not None:
        g = gcd.setdefault("gating", collections.OrderedDict())
        targets = [t for t in DATE_CONSUMERS if t in decls]
        if g.get("must_precede") != targets:
            g.pop("required_before", None)      # no consumer reads this key
            g["must_precede"] = targets
            changes.append(f"get_current_datetime.must_precede -> {targets}")

    # 3. `channel` must be optional: "pay today" needs no channel, and neither KBANK
    #    nor SKL has a beat that can even ask for one — yet build_tool_schemas puts
    #    every non-optional arg in `required`, forcing the model to invent a value.
    for tname in ("record_verbal_commitment", "payment_date"):
        ch = ((decls.get(tname) or {}).get("args") or {}).get("channel")
        if ch is not None and not ch.get("optional"):
            ch["optional"] = True
            changes.append(f"{tname}.channel -> optional")

    # 4. Train/serve parity: the RL environment's update_phone takes `phone`
    #    (verl/config/debt_tool_config.yaml). AEON declared `number`, so the model
    #    emits the argument it was trained on and the API receives nothing.
    up = decls.get("update_phone")
    if up is not None and "number" in (up.get("args") or {}):
        up["args"] = collections.OrderedDict(
            ("phone", v) if k == "number" else (k, v) for k, v in up["args"].items())
        changes.append("update_phone.args.number -> phone (matches the trained tool)")

    # 5. A close-of-call tool the spec declares `required_at: end_of_call` must have
    #    an argument vocabulary, or nothing constrains what gets written: AMT's
    #    save_appointment accepted status "ptp" because its allowed values lived in
    #    a `desc` string, which neither the model's schema nor the API can read.
    results = list((spec.get("outcomes") or {}).get("results", {}))
    for d in decls.values():
        if (d.get("gating") or {}).get("required_at") != "end_of_call":
            continue
        for arg in ("result", "status"):
            meta = (d.get("args") or {}).get(arg)
            # `outcomes.results` is the vocabulary; the closing tool's enum must
            # track it, not merely be non-empty. Adding an outcome (AMT gained
            # `cancelled`/`unreachable`) has to reach the model's schema and the API
            # in the same edit, or the flow declares an ending it cannot write.
            if meta is not None and results and meta.get("enum") != results:
                meta["enum"] = results
                changes.append(f"{d['name']}.{arg}.enum -> {results} (from outcomes)")

    # 6. Cues that contradict the spec's own constraints.
    ev = spec.get("events") or {}
    nc = ev.get("name_confirmed")
    if isinstance(nc, dict) and nc.get("cues"):
        kept = [c for c in nc["cues"] if str(c).strip().lower() not in BARE_ACKS]
        if len(kept) != len(nc["cues"]):
            dropped = [c for c in nc["cues"] if c not in kept]
            nc["cues"] = kept
            changes.append(f"name_confirmed: dropped bare acks {dropped}")
    hs = ev.get("hardship_sick")
    if isinstance(hs, dict) and hs.get("cues"):
        kept = [c for c in hs["cues"] if str(c).strip().lower() not in NOT_ILLNESS]
        if len(kept) != len(hs["cues"]):
            dropped = [c for c in hs["cues"] if c not in kept]
            hs["cues"] = kept
            changes.append(f"hardship_sick: dropped non-illness cues {dropped}")

    # 7. Auxiliary bindings that resolve to nothing. These are hard ERRORS from the
    #    repo's own validate_flow_spec, and the renderer still advertises the beats
    #    to the model — offering it lines that have no text behind them.
    aux = (spec.get("auxiliary_templates") or {}).get("allowed")
    if isinstance(aux, list):
        fixed, dropped = [], []
        for a in aux:
            fs = a.get("fine_state")
            if fs in fine_states:
                fixed.append(a)
            elif f"{fs}_only" in fine_states:      # offer_channel -> offer_channel_only
                a["fine_state"] = f"{fs}_only"
                fixed.append(a)
                changes.append(f"auxiliary: {fs} -> {fs}_only")
            else:
                dropped.append(fs)
        if dropped:
            changes.append(f"auxiliary: dropped unresolvable {dropped}")
        spec["auxiliary_templates"]["allowed"] = fixed

    # 8. The KYC gate's own escape hatch was unreachable. Both KYC constraints tell
    #    the model to answer with `verify_first`, but no state, FAQ route or
    #    auxiliary entry bound it — so resolve_templates never yields it and the
    #    beat cannot legally be selected. Bind it where it is needed: before
    #    identity is confirmed.
    if "verify_first" in fine_states:
        bound = {t.get("fine_state")
                 for st in spec.get("states", []) for t in (st.get("templates") or [])}
        bound |= {a.get("fine_state") for a in
                  ((spec.get("auxiliary_templates") or {}).get("allowed") or [])}
        if "verify_first" not in bound:
            spec.setdefault("auxiliary_templates", collections.OrderedDict()) \
                .setdefault("allowed", []).append(collections.OrderedDict([
                    ("fine_state", "verify_first"),
                    ("desc", "ตอบก่อนยืนยันตัวตน — ห้ามแจ้งยอดจนกว่าจะยืนยันชื่อ"),
                ]))
            changes.append("auxiliary: bound verify_first (KYC gate's own escape hatch)")

    # 8b. Any beat the spec's own prose ORDERS the model to use must be selectable.
    #     `verify_first` above was the loud case; the same hole exists wherever a
    #     constraint or a state note names a beat — e.g. `below_minimum`, which
    #     `partial_payment_floor` requires for a sub-minimum offer, and
    #     `ask_pay_today`, listed in KBANK's one_template_per_turn exception. Unbound
    #     means resolve_templates yields nothing and the instruction never offers it,
    #     so the rule is unfollowable by construction.
    prose = " ".join(
        [str(c.get("desc", "")) for c in (spec.get("constraints") or [])]
        + [str(st.get("note", "")) for st in spec.get("states", [])]
        + [str(r.get("note", "")) for r in
           ((spec.get("faq_routing") or {}).get("routes") or [])])
    bound = {t.get("fine_state")
             for st in spec.get("states", []) for t in (st.get("templates") or [])}
    bound |= {a.get("fine_state") for a in
              ((spec.get("auxiliary_templates") or {}).get("allowed") or [])}
    bound |= {t.get("fine_state") for r in
              ((spec.get("faq_routing") or {}).get("routes") or [])
              for t in (r.get("templates") or [])}
    for fs in sorted(fine_states - bound):
        if fs and fs in prose:
            spec.setdefault("auxiliary_templates", collections.OrderedDict()) \
                .setdefault("allowed", []).append(collections.OrderedDict([
                    ("fine_state", fs),
                    ("desc", "อ้างถึงในกฎของ spec — ต้องเลือกได้จริง"),
                ]))
            changes.append(f"auxiliary: bound {fs} (named by a rule but unreachable)")

    # 8c. A constraint type the validator does not know makes the whole spec fail
    #     validation, which blocks the builder/editor endpoints. These constraints
    #     are prose the renderer prints; a typeless prose constraint is the schema's
    #     own supported shape for that, so drop the invented type rather than the rule.
    from demo.server.flow.flowspec import CONSTRAINT_TYPES
    for c in spec.get("constraints") or []:
        t = c.get("type")
        if t and t not in CONSTRAINT_TYPES:
            c.pop("type")
            changes.append(f"constraint {c.get('id', '?')}: dropped unknown type {t!r} "
                           f"(kept as prose)")

    # 9. Terminal states must write the outcome. The spec requires it
    #    (`outcome_required`, `outcomes.required_at_close`) but entry_tools — the
    #    only list the step-completeness gate reads — omitted record_outcome on
    #    every close but one, so the call ends with nothing recorded.
    closer = next((n for n, d in decls.items()
                   if (d.get("gating") or {}).get("required_at") == "end_of_call"), None)
    if closer:
        for st in spec.get("states", []):
            if not st.get("terminal"):
                continue
            et = st.setdefault("entry_tools", [])
            if closer not in et:
                et.append(closer)
                changes.append(f"state {st.get('id')}: entry_tools += {closer}")

    # 10. `outcome.reason` (singular string) is read by nothing; the renderer and
    #     every other spec use `reasons` (list). Left as-is, the close line renders
    #     as "reason: -".
    for st in spec.get("states", []):
        oc = st.get("outcome")
        if isinstance(oc, dict) and "reason" in oc and "reasons" not in oc:
            oc["reasons"] = [oc.pop("reason")]
            changes.append(f"state {st.get('id')}: outcome.reason -> reasons[]")

    if changes and not DRY:
        path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    return changes


def main() -> None:
    reg = json.loads((FLOWS / "flow_registry.json").read_text(encoding="utf-8"))
    for company, entry in reg.items():
        catalog_name = entry["catalog"]
        catalog = json.loads(
            (REPO / "data" / "pre-scripts" / catalog_name).read_text(encoding="utf-8"))
        stem = pathlib.Path(entry["spec"]).stem
        for path in sorted(FLOWS.glob(f"{stem}*.json")):
            changes = fix_spec(company, path, catalog_name, catalog)
            if changes:
                log(f"\n{path.name}")
                for c in changes:
                    log(f"   - {c}")
    log("\n(dry run — nothing written)" if DRY else "\nwritten")


if __name__ == "__main__":
    main()
