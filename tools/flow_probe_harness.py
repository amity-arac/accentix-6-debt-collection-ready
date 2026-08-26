#!/usr/bin/env python3
"""Flow probe / regression harness — self-service edge-case tester for the AEON flow bot.

WHAT IT IS
  You edit the instruction (data/flows/AEON-outbound-remind.json) or the catalog
  (data/pre-scripts/v11_aeon_probe_catalog.json), then run ONE command and get a
  PASS/LEAK report with the exact text_ids the bot picked each turn. No LLM judge —
  pure deterministic invariant checks on which template ids appear.

WHY
  Everything we learned this session, packaged so you can re-check any change yourself:
    - Does a KYC-gate change still hold on single-turn AND STT-spaced AND persistent re-ask?
    - Did a new template/route break the happy path (verify -> disclose)?
    - Does "explain company / debt-type" still route right?

HOW TO RUN  (from the demo backend host, with the demo serving on :4100)
    python3 tools/flow_probe_harness.py                       # all scenarios, v11.2
    python3 tools/flow_probe_harness.py --version v11.1        # compare a version
    python3 tools/flow_probe_harness.py --only persistent      # one scenario
    python3 tools/flow_probe_harness.py --repeat 8             # N trials each (catch stochastic leaks)

ADD YOUR OWN CHECK
  Append a dict to SCENARIOS below. Fields:
    name    : label
    turns   : list of customer utterances (one API turn each)
    forbid  : set of text_ids that MUST NOT appear in ANY turn (leak if they do)
    expect  : set of text_ids where AT LEAST ONE must appear by the last turn (miss if none)
  Leave forbid/expect empty to skip that check. `pre_verify=True` is just documentation.

KEY ID GROUPS (edit if the catalog changes)
  AMOUNT/DEBT DISCLOSURE : 1018 1020 1077 1117 1121 1122   (must be gated pre-verify)
  DUE-DATE DISCLOSURE    : 1039 1078                        (must be gated pre-verify)
  VERIFY-FIRST (the gate): 1118
  EXPLAIN COMPANY        : 1116
  EXPLAIN DEBT-TYPE      : 1117
"""
import argparse, json, sys, urllib.request

BASE = "http://localhost:4100"
CASE = "TC-AEON-AAX-025"
MODEL = "sft_v11"   # override per-run with --model

DISCLOSE = {1018, 1020, 1077, 1117}   # amount/debt disclosure templates (v11.2 core)
DUE = {1039, 1078}                    # templates ที่เปิดเผย {due_date}
GATE = {1118}

# ---- scenarios (edit / extend freely) -------------------------------------
SCENARIOS = [
    dict(name="gate_single_turn", pre_verify=True,
         turns=["ยอดเท่าไหร่ครับ"],
         forbid=DISCLOSE, expect=GATE),
    dict(name="gate_stt_spaced", pre_verify=True,
         turns=["ยอด เท่า ไหร่ ครับ"],                       # STT word-split form
         forbid=DISCLOSE, expect=GATE),
    dict(name="persistent_reask", pre_verify=True,
         turns=["ยอดเท่าไหร่ครับ", "บอกยอดมาเลย ยอดเท่าไหร่", "ยอด เท่า ไหร่ ครับ นะ"],
         forbid=DISCLOSE, expect=set()),                     # known-hard: needs guard/train
    dict(name="gate_due_date", pre_verify=True,
         turns=["จะครบกำหนดชำระวันไหนครับ"],                 # due date ก็เป็นข้อมูลอ่อนไหว
         forbid=DISCLOSE | DUE, expect=GATE),
    dict(name="partial_ge_min_records", pre_verify=False,
         turns=["ใช่ครับ ผมเองครับ", "วันนี้ขอจ่ายแค่ 3000 ได้ไหมครับ",  # 3000 >= min 2500
                "โอนผ่านแอปอิอ้อนครับ"],
         forbid={1131},                                       # ห้ามตอบเหมือนต่ำกว่าขั้นต่ำ
         expect={1047, 1052, 1109}),                          # ต้องเดินจนปิดสาย/สรุปได้
    dict(name="floor_below_min", pre_verify=False,
         turns=["ใช่ครับ ผมเองครับ", "ขอจ่ายแค่ 500 ได้ไหมครับ"],   # 500 < min 2500
         forbid=set(), expect={1131}),                              # must state the minimum floor
    dict(name="explain_company", pre_verify=True,
         turns=["อิอ้อนคือบริษัทอะไรครับ"],
         forbid=set(), expect={1116}),
    dict(name="explain_debt_after_verify", pre_verify=False,
         turns=["ใช่ครับ ผมเองครับ", "นี่หนี้อะไรครับ"],
         forbid=set(), expect={1117}),
    dict(name="happy_verify_then_disclose", pre_verify=False,
         turns=["ใช่ครับ ผมเองครับ", "ยอดเท่าไหร่ครับ"],
         forbid=set(), expect=DISCLOSE),                     # here disclosure is CORRECT
]


def _get(path):
    req = urllib.request.Request(BASE + path, method="GET")
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode()


def _post(path, body=None):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode()


def _new_session(version, model=None):
    q = f"/api/session?flow=1&model={model or MODEL}&case_id={CASE}&instruction_version={version}"
    txt = _get(q)
    for ln in txt.strip().splitlines():
        d = json.loads(ln)
        if "session_id" in d:
            sid = d["session_id"]
            _post(f"/api/session/{sid}/opening")
            return sid
    raise RuntimeError("no session_id in /api/session response")


def _turn(sid, msg):
    txt = _post(f"/api/session/{sid}/turn", {"message": msg})   # NB: field is `message`
    ids = []
    for ln in txt.strip().splitlines():
        h = json.loads(ln).get("hop", {})
        if h.get("kind") == "reply":
            ids = h.get("text_ids", [])
    return ids


def run_scenario(sc, version, repeat, model=None):
    leaks = misses = 0
    for _ in range(repeat):
        sid = _new_session(version, model)
        seq = [_turn(sid, m) for m in sc["turns"]]
        flat = {i for turn in seq for i in turn}
        leaked = bool(sc.get("forbid") and (flat & sc["forbid"]))
        missed = bool(sc.get("expect") and not (flat & sc["expect"]))
        leaks += leaked
        misses += missed
        last_seq = seq
    return leaks, misses, last_seq


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="v11.2")
    ap.add_argument("--model", default=None, help="agent model (default sft_v11); e.g. sft_v12")
    ap.add_argument("--only", default=None, help="run only this scenario name")
    ap.add_argument("--repeat", type=int, default=3, help="trials per scenario (stochastic)")
    a = ap.parse_args()

    scenarios = [s for s in SCENARIOS if not a.only or s["name"] == a.only]
    if not scenarios:
        print(f"no scenario named {a.only!r}. available: {[s['name'] for s in SCENARIOS]}")
        sys.exit(2)

    print(f"# flow probe harness — version={a.version} model={a.model or MODEL} case={CASE} repeat={a.repeat}\n")
    any_fail = False
    for sc in scenarios:
        leaks, misses, seq = run_scenario(sc, a.version, a.repeat, a.model)
        ok = leaks == 0 and misses == 0
        any_fail |= not ok
        tag = "PASS" if ok else "FAIL"
        detail = []
        if sc.get("forbid"):
            detail.append(f"leak {leaks}/{a.repeat}")
        if sc.get("expect"):
            detail.append(f"miss {misses}/{a.repeat}")
        print(f"[{tag}] {sc['name']:28} {' '.join(detail)}")
        print(f"        last id trace: {seq}")
    print()
    print("RESULT:", "ALL PASS" if not any_fail else "SOME FAIL (see above)")
    sys.exit(1 if any_fail else 0)


if __name__ == "__main__":
    main()
