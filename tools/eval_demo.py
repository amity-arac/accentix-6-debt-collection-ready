"""วัด "เดโมตัวที่เสิร์ฟอยู่จริง" ด้วย gold ชุดเดียวกับ harness ฝั่งเทรน

ทำไมต้องมีตัวนี้: harness ฝั่งเทรนอ่าน spec ของแอปไม่ออก (any_of, impl:"http",
text_id คนละชุด, compliance ไม่มี) — แปลงข้ามฝั่งแล้วเจอชั้นที่ต้องแก้ 7 ชั้น และแต่ละชั้น
ให้ผลที่ดูเหมือนคำตอบทั้งที่เป็นอาการของการแปลงไม่ครบ ตัวนี้เลี่ยงการแปลงทั้งหมด:
ยิงเข้า session ของแอปจริง แล้วเทียบกับ `expect` ที่ gold เขียนไว้ตรงๆ

ผลออกมาเป็น JSON รูปเดียวกับ gold_eval_model.py เพื่อให้ส่งต่อ
verl/eval/gold_eval_to_csv.py ได้ตรงๆ — ห้ามเขียนตัวแปลง CSV ใหม่

    python3 eval_demo.py [--company AEON,KBANK,SKL,AMT] [--out result.json]
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import urllib.request

# Paths are env-overridable so this runs from a checkout, not from one host.
# DEMO_APP is this repo (it holds the tenant specs the app serves); GOLD_DIR is the
# training repo's gold suites, which live in a sibling checkout.
DEMO_APP = os.environ.get("AAX6_DEMO_APP") or str(pathlib.Path(__file__).resolve().parents[1])
GOLD_DIR = pathlib.Path(os.environ.get("AAX6_GOLD_DIR")
                        or pathlib.Path(DEMO_APP).parent / "accentix-6-debt-collector"
                        / "data" / "test-cases")
BASE = os.environ.get("AAX6_DEMO_URL", "http://127.0.0.1:4100")
CASE = {"AEON": "TC-AEON-PREDUE-001", "AEONLITE": "TC-AEONLITE-BUILD-001",
        "KBANK": "TC-KBANK-BUILD-001",
        "SKL": "TC-SKL-BUILD-001", "AMT": "TC-AMT-BUILD-001"}


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


def _spec_of(company: str):
    sys.path.insert(0, DEMO_APP)
    from demo.server.flow.flowspec import normalize_catalog, resolve_catalog
    # one tenant = one `<CODE>.company.json`; there is no index file to consult
    d = pathlib.Path(DEMO_APP) / "data/flows"
    spec = json.loads((d / f"{company}.company.json").read_text("utf-8"))
    cat = normalize_catalog(resolve_catalog(spec), spec)
    closer = next((t["name"] for t in spec["tools"]["declarations"]
                   if (t.get("gating") or {}).get("required_at") == "end_of_call"),
                  "record_outcome")
    # a beat is sayable if the CATALOG has a sentence for it — not if some state
    # lists it. FAQ/disclosure beats deliberately belong to no state (they are
    # interrupts, valid anywhere), so scoring reachability off the state graph
    # would mark them impossible when the model can say them any time.
    reachable = {e["_fine_state"] for e in cat}
    # `any_of` = one step of the flow that several beats satisfy (third_party /
    # third_party_know; apology / apology_close). Gold names one of them, so a strict
    # name match failed runs that took the other — a spec-legal answer scored wrong.
    alias: dict[str, set[str]] = {}
    for st in spec["states"]:
        for t in st.get("templates", []):
            group = t.get("any_of")
            if group:
                for b in group:
                    alias.setdefault(b, set()).update(group)
    return {e["text_id"]: e["_fine_state"] for e in cat}, closer, reachable, alias


def _is_subseq(want: list, got: list, alias: dict | None = None) -> bool:
    alias = alias or {}
    it = iter(got)
    return all(any(g in (alias.get(w) or {w}) for g in it) for w in want)


def _check(ok: bool, **detail) -> dict:
    return {"kind": "CHECK", "ok": bool(ok), "detail": detail}


MODEL = "grpo540"


def run_scenario(company: str, scn: dict, by_id: dict, closer: str, reachable: set,
                 alias: dict) -> dict:
    req = urllib.request.Request(
        f"{BASE}/api/session?case_id={CASE[company]}&flow=1&model={MODEL}")
    lines = urllib.request.urlopen(req, timeout=120).read().decode().splitlines()
    sid = None
    for ln in lines:
        try:
            o = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if o.get("session_id"):
            sid = o["session_id"]

    trace, beats, tools, outcome, warns = [], [], [], None, []

    def absorb(objs: list[dict]) -> None:
        nonlocal outcome
        for o in objs:
            h = o.get("hop") or {}
            kind = h.get("kind")
            if kind == "reply":
                ids = h.get("text_ids") or []
                bs = [by_id[t] for t in ids if t in by_id]
                beats.extend(bs)
                trace.append({"action": "reply", "beats": bs, "spoken": h.get("text") or ""})
            elif kind == "tool_call" and h.get("name") != "reply":
                tools.append(h["name"])
                trace.append({"action": h["name"], "kwargs": h.get("args") or {},
                              "observation": ""})
                if h["name"] == closer:
                    outcome = h.get("args") or {}
            elif kind == "tool_result":
                # the demo streams the result as its own hop; fold it onto the call
                # so one CSV line reads call -> observation like the train harness
                for rec in reversed(trace):
                    if rec["action"] != "reply" and not rec["observation"]:
                        rec["observation"] = json.dumps(h.get("result") or {}, ensure_ascii=False)
                        break
            elif kind == "warning" or o.get("warning"):
                w = h if kind == "warning" else o["warning"]
                warns.append(w.get("text") or json.dumps(w, ensure_ascii=False))

    # the bot greets FIRST (outbound call) and that reply comes from its own
    # endpoint — reading only the session stream loses the opening beat and every
    # scenario looks like it never said hello
    absorb(_post(f"/api/session/{sid}/opening", {}))
    for turn in scn["customer_script"]:
        says = turn["says"]
        for rec in reversed(trace):
            if rec["action"] == "reply":
                rec["user_reply"] = says
                break
        absorb(_post(f"/api/session/{sid}/turn", {"message": says}))

    exp = scn.get("expect") or {}
    want_beats = exp.get("beats_in_order") or []
    # gold names the closer `record_outcome`; a company may declare its own
    want_tools = [closer if t == "record_outcome" else t for t in (exp.get("tools_required") or [])]
    want_out = (exp.get("outcome") or {}).get("result")
    got_out = (outcome or {}).get("result") or (outcome or {}).get("status")
    unreachable = sorted({b for b in want_beats if b not in reachable})

    checks = {
        "beats": _check(_is_subseq(want_beats, beats, alias), want=want_beats, got=beats,
                        unreachable_in_spec=unreachable),
        "tools": _check(all(t in tools for t in want_tools),
                        want=want_tools, called=sorted(set(tools))),
        "outcome": _check(want_out is None or got_out == want_out,
                          want=want_out, got=got_out),
        "spec_constraints": {"kind": "INFO", "detail": {"fail": warns}},
    }
    return {"id": scn["id"], "name": scn.get("name", ""),
            "passed": all(c["ok"] for c in checks.values() if c["kind"] == "CHECK"),
            "checks": checks, "trace": trace,
            "unpassable_by_spec": bool(unreachable)}


def _preflight() -> None:
    """Refuse to score a model the app cannot reach.

    Naming a model that is not being served does not error — every scenario dies on
    `IncompleteRead` and the run reports 0/45, which is indistinguishable from a
    catastrophic regression and was read as one. One session up front turns that into a
    message.
    """
    try:
        req = urllib.request.Request(
            f"{BASE}/api/session?case_id={CASE['AEON']}&flow=1&model={MODEL}")
        body = urllib.request.urlopen(req, timeout=120).read().decode()
    except Exception as e:  # noqa: BLE001 — any failure here is the same advice
        raise SystemExit(f"  เปิด session ไม่ได้ ({type(e).__name__}: {e})\n"
                         f"  demo อยู่ที่ {BASE} จริงไหม (AAX6_DEMO_URL)")
    sid = next((json.loads(ln)["session_id"] for ln in body.splitlines()
                if ln.startswith("{") and json.loads(ln).get("session_id")), None)
    if sid:
        # A session opens for a model that is not served; the failure only surfaces when
        # something has to generate. Play one turn.
        try:
            _post(f"/api/session/{sid}/opening", {})
            hops = _post(f"/api/session/{sid}/turn", {"message": "สวัสดีครับ"})
        except Exception as e:  # noqa: BLE001
            raise SystemExit(f"  model={MODEL!r} เปิด session ได้ แต่ตอบไม่ได้"
                             f" ({type(e).__name__}: {e})\n"
                             f"  โมเดลนั้นถูก serve อยู่จริงไหม — ถ้าไม่"
                             f" ทุกเคสจะตายแล้วรายงานเป็น 0/45")
        if not hops:
            raise SystemExit(f"  model={MODEL!r} ตอบว่างเปล่า — ถูก serve อยู่จริงไหม")
    if not sid:
        raise SystemExit(f"  demo ไม่คืน session สำหรับ model={MODEL!r}\n"
                         f"  โมเดลนั้นถูก serve อยู่จริงไหม — ถ้าไม่ ทุกเคสจะตาย"
                         f" แล้วรายงานเป็น 0/45")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", default="AEON,KBANK,SKL,AMT")
    ap.add_argument("--out", default="demo_eval.json")
    ap.add_argument("--model", default="grpo540")
    a = ap.parse_args()
    global MODEL
    MODEL = a.model
    _preflight()

    detail, tot, ok, blocked = {}, 0, 0, 0
    for co in a.company.split(","):
        gold = GOLD_DIR / f"master_gold_{co.lower()}.json"
        if not gold.exists():
            print(f"  {co}: ไม่มี {gold.name}")
            continue
        scns = json.loads(gold.read_text("utf-8"))["scenarios"]
        by_id, closer, reachable, alias = _spec_of(co)
        rows = []
        for scn in scns:
            try:
                rows.append(run_scenario(co, scn, by_id, closer, reachable, alias))
            except Exception as e:
                rows.append({"id": scn["id"], "name": scn.get("name", ""), "passed": False,
                             "checks": {"run": _check(False, error=f"{type(e).__name__}: {e}")},
                             "trace": [], "unpassable_by_spec": False})
        n = sum(r["passed"] for r in rows)
        b = sum(r["unpassable_by_spec"] for r in rows)
        detail[co] = {"company": co, "rows": rows}
        tot += len(rows); ok += n; blocked += b
        print(f"  {co:6} {n}/{len(rows)}   spec ไม่รองรับ {b}   closer={closer}")
    pathlib.Path(a.out).write_text(
        json.dumps({"guards": "app", "model": MODEL, "detail": detail},
                   ensure_ascii=False, indent=1))
    print(f"  รวม {ok}/{tot}   (ผ่านไม่ได้เพราะ spec {blocked})  -> {a.out}")


if __name__ == "__main__":
    main()
