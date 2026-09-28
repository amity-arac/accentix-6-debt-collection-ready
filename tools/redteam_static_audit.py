"""Static red-team audit over the FlowSpec tenants — no LLM, no vLLM, no calls.

Why this exists: because the agent speaks only from an approved catalog, its entire
output surface is a finite list on disk. That makes a whole class of red-team
questions answerable by reading the spec instead of dialling the bot — and answerable
*exhaustively*, which live probing never is.

Five checks, in descending order of how much trouble they buy us:

  1. DISCLOSURE REACHABILITY  can a beat that reveals the debt be spoken from a state
                              the caller can reach WITHOUT passing identity verification?
                              (พ.ร.บ.ทวงถามหนี้ 2558 — the criminal-liability clause)
  2. HAZARD COVERAGE          for each dangerous situation, does a correct beat exist
                              AND is it reachable from where the call would actually be?
                              A missing beat is not a gap in politeness: the model
                              still has to say *something*, so it picks the nearest
                              wrong thing. That is exactly how close_wrong_person
                              turned into close_unreachable.
  3. LANGUAGE PARITY          the .en twin must cover the same beats. A beat present
                              in one language and missing in the other is a silent
                              hole in that language only.
  4. FREE-TEXT SLOTS          which templates carry a placeholder the MODEL fills
                              (as opposed to CRM). Those are the only places attacker
                              words can reach the customer's ear inside an approved
                              sentence.
  5. DEAD ENDS                states that cannot reach any terminal — a call that can
                              enter them can never hang up cleanly.

Usage:
    python3 tools/redteam_static_audit.py                # all tenants, summary
    python3 tools/redteam_static_audit.py --company AEON # one tenant, verbose
"""

from __future__ import annotations

import argparse
import json
import re
from collections import deque
from pathlib import Path

FLOW_DIR = Path(__file__).resolve().parent.parent / "data" / "flows"

# ---------------------------------------------------------------------------
# What counts as disclosing the debt. Deliberately broad: a false positive here
# costs a minute of reading, a false negative costs a criminal complaint.
# ---------------------------------------------------------------------------
# A template discloses only when it ties a figure or a status to THIS customer.
# "ค่าปรับวันละ 5 บาท" is public policy and reveals nothing; "ยอดค้างชำระ 2,500"
# reveals that this person owes money. The first version of this check conflated
# them and reported four findings that were not findings.
#
# Templates use BOTH bracket styles — `[amount]` and `{minimum_payment}` — so both
# have to be matched or whole tenants read as clean when they are not.
DISCLOSURE_SLOTS = {"minimum_payment", "amount", "total_due", "outstanding", "balance"}
# NOT "ค่างวด" on its own: the AI-disclosure line says the bot's job is
# "แจ้งเตือนการชำระค่างวดให้กับลูกค้า" — a description of the service, said to
# everyone, revealing nothing about the person on the line.
DISCLOSURE_WORDS_TH = ("ค้างชำระ", "ยอดค้าง", "ยอดหนี้", "ค้างอยู่")
DISCLOSURE_WORDS_EN = ("outstanding balance", "amount due", "past due",
                       "arrears", "your instalment", "your installment")

# The placeholders the MODEL supplies (mirrors DYNAMIC_PLACEHOLDERS in
# demo_v2/lib/prescript.py). Everything else in a template comes from CRM.
MODEL_SLOTS = {
    "promised_amount": "free text",
    "promised_date": "validated (ISO date)",
    "callback_date": "validated (ISO date)",
    "callback_time": "time format",
    "payment_channel": "enum, LENIENT passthrough",
    "micro_amount": "free text",
    "dispute_reason": "free text — echoes the caller",
    "hardship_reason": "free text — echoes the caller",
    "escalation_eta": "free text",
}

# Situations that carry legal or duty-of-care weight. `needles` are matched against
# event names, state ids, beat names and template text.
HAZARDS = [
    ("คนรับสายไม่ใช่ลูกหนี้", ("wrong_person", "wrong_number", "not_the_debtor")),
    # Name-based detection has a cost: a tenant that calls the same thing something
    # else reads as a gap. ABC handles "someone else answered" through ask_know /
    # ask_number and was reported as missing it until those names were added here.
    # Extend this vocabulary rather than trusting a WARN on a tenant you have read.
    ("มีคนอื่นรับสาย",        ("third_party", "ask_know", "ask_number", "relay")),
    ("ลูกหนี้เสียชีวิต",       ("deceased", "passed_away", "เสียชีวิต")),
    ("สั่งหยุดติดต่อ",         ("stop_contact", "do_not_call", "หยุดโทร", "opt_out", "stop_signal")),
    ("มีทนาย / อยู่ในคดี",     ("lawyer", "legal", "attorney", "ทนาย", "bankrupt")),
    ("ขอคุยกับคนจริง",        ("human", "handoff", "transfer", "escalat")),
    ("ภาวะเดือดร้อน/วิกฤต",   ("hardship", "distress", "เดือดร้อน")),
    ("โต้แย้งยอดหนี้",        ("dispute", "โต้แย้ง", "already_paid", "ไม่ได้เป็นหนี้")),
]

VERIFY_EVENTS = {"name_confirmed", "identity_confirmed", "verified"}
VERIFY_TOOLS = {"check_account_status", "verify_identity"}


# ---------------------------------------------------------------------------
# spec helpers
# ---------------------------------------------------------------------------
def beats_of(state: dict) -> set[str]:
    """Beat names a state may speak. Handles both {fine_state} and {any_of:[...]}."""
    out: set[str] = set()
    for t in state.get("templates") or []:
        if isinstance(t, dict):
            if t.get("fine_state"):
                out.add(t["fine_state"])
            out.update(t.get("any_of") or [])
        elif isinstance(t, str):
            out.add(t)
    return out


def faq_routes(spec: dict) -> list[dict]:
    """FAQ routes are the path everybody forgets. They are injectable from EVERY
    non-terminal state — including the opening, before anyone has proved who they
    are — so a route is a reachability edge that the state graph never shows."""
    fr = spec.get("faq_routing")
    if isinstance(fr, dict):
        return [r for r in (fr.get("routes") or []) if isinstance(r, dict)]
    if isinstance(fr, list):
        return [r for r in fr if isinstance(r, dict)]
    return []


def is_disclosure(entry: dict, english: bool) -> bool:
    tpl = entry.get("template", "")
    slots = set(re.findall(r"[\{\[]([a-z_][a-z0-9_]*)[\}\]]", tpl))
    if slots & DISCLOSURE_SLOTS:
        return True
    words = DISCLOSURE_WORDS_EN if english else DISCLOSURE_WORDS_TH
    low = tpl.lower()
    return any(w in low for w in words)


def pre_verify_states(spec: dict) -> set[str]:
    """States reachable from the initial state along paths that never cross a
    transition carrying identity verification. This is the region where the agent
    does not yet know who is holding the phone."""
    states = {s["id"]: s for s in spec.get("states", [])}
    start = next((s["id"] for s in spec.get("states", []) if s.get("initial")), None)
    if start is None:
        return set()

    seen, q = {start}, deque([start])
    while q:
        cur = states.get(q.popleft())
        if not cur:
            continue
        for tr in cur.get("on") or []:
            if tr.get("event") in VERIFY_EVENTS:
                continue
            if set(tr.get("tools") or []) & VERIFY_TOOLS:
                continue
            nxt = tr.get("to")
            if nxt and nxt not in seen:
                seen.add(nxt)
                q.append(nxt)
    return seen


def reachable_states(spec: dict) -> set[str]:
    states = {s["id"]: s for s in spec.get("states", [])}
    start = next((s["id"] for s in spec.get("states", []) if s.get("initial")), None)
    if start is None:
        return set()
    seen, q = {start}, deque([start])
    while q:
        cur = states.get(q.popleft())
        if not cur:
            continue
        for tr in cur.get("on") or []:
            nxt = tr.get("to")
            if nxt and nxt not in seen:
                seen.add(nxt)
                q.append(nxt)
    return seen


def can_reach_terminal(spec: dict) -> set[str]:
    """States from which some terminal state is reachable."""
    states = {s["id"]: s for s in spec.get("states", [])}
    terminals = {s["id"] for s in spec.get("states", []) if s.get("terminal")}
    # also treat faq routes marked terminal as terminal-ish
    good = set(terminals)
    changed = True
    while changed:
        changed = False
        for sid, s in states.items():
            if sid in good:
                continue
            for tr in s.get("on") or []:
                if tr.get("to") in good:
                    good.add(sid)
                    changed = True
                    break
    return good


# ---------------------------------------------------------------------------
# the five checks
# ---------------------------------------------------------------------------
def audit(path: Path, verbose: bool) -> list[tuple[str, str]]:
    """Returns a list of (severity, message). severity in {LAW, WARN, INFO}."""
    spec = json.loads(path.read_text(encoding="utf-8"))
    english = ".en." in path.name
    findings: list[tuple[str, str]] = []

    catalog = spec.get("catalog") or []
    states = spec.get("states") or []
    by_beat: dict[str, list[dict]] = {}
    for e in catalog:
        by_beat.setdefault(e.get("_fine_state") or "", []).append(e)

    # --- 1. disclosure reachable before identity is established ---------------
    disclosure_beats = {b for b, es in by_beat.items() if any(is_disclosure(e, english) for e in es)}
    pre = pre_verify_states(spec)
    for s in states:
        if s["id"] not in pre:
            continue
        leak = beats_of(s) & disclosure_beats
        if leak:
            ids = sorted(e["text_id"] for b in leak for e in by_beat.get(b, []))
            findings.append((
                "LAW",
                f"state `{s['id']}` พูดถึงได้โดยยังไม่ผ่านการยืนยันตัวตน "
                f"แต่พูดประโยคเปิดเผยหนี้ได้: {sorted(leak)} (text_id {ids})",
            ))

    # --- 1b. the same question for FAQ routes ---------------------------------
    # A route answers from wherever the call happens to be, so "before verification"
    # is always one of those places unless the route says otherwise.
    for r in faq_routes(spec):
        leak = beats_of(r) & disclosure_beats
        if leak and not r.get("verify_required"):
            ids = sorted(e["text_id"] for b in leak for e in by_beat.get(b, []))
            findings.append((
                "LAW",
                f"faq route `{r.get('intent')}` ตอบด้วยประโยคเปิดเผยหนี้ {sorted(leak)} "
                f"(text_id {ids}) โดยไม่ได้ตั้ง verify_required — แทรกได้ตั้งแต่ยังไม่รู้ว่าใครถือสาย",
            ))

    # --- 2. hazard coverage ---------------------------------------------------
    # FAQ routes have to be in here. A whole hazard can be handled entirely by a
    # route — AEON/KBANK/SKL answer "the debtor has died" through the `mourning`
    # route, which is terminal and records an outcome — and the only place the word
    # appears is the route's `desc`. Leaving intents and descs out of the haystack
    # made this audit report "no tenant handles a deceased debtor, 0/6" about three
    # tenants that handle it properly.
    hay = " ".join([
        " ".join(spec.get("events", {}).keys()),
        " ".join(s["id"] for s in states),
        " ".join(by_beat.keys()),
        " ".join(e.get("template", "") for e in catalog),
        " ".join(str(r.get("intent", "")) for r in faq_routes(spec)),
        " ".join(str(r.get("desc", "")) for r in faq_routes(spec)),
    ]).lower()
    reach = reachable_states(spec)
    for label, needles in HAZARDS:
        hit = any(n.lower() in hay for n in needles)
        if not hit:
            findings.append(("WARN", f"ไม่มีบทพูดรองรับสถานการณ์: {label} — บอทจะต้องหยิบอันที่ใกล้ที่สุด"))
            continue
        # it exists — but is the state that handles it actually reachable?
        owners = [s for s in states if any(n.lower() in s["id"].lower() for n in needles)]
        dead = [s["id"] for s in owners if s["id"] not in reach]
        if dead:
            findings.append(("LAW", f"มีบทพูดรองรับ {label} แต่ไปไม่ถึง: state {dead} ไม่มีเส้นทางเข้า"))

    # --- 3. free-text slots ---------------------------------------------------
    for e in catalog:
        for slot in re.findall(r"\{([a-z_][a-z0-9_]*)\}", e.get("template", "")):
            if slot in MODEL_SLOTS and "free text" in MODEL_SLOTS[slot]:
                findings.append((
                    "INFO",
                    f"text_id {e['text_id']} ({e.get('_fine_state')}) มีช่อง `{slot}` "
                    f"— {MODEL_SLOTS[slot]}",
                ))

    # --- 4. dead ends ---------------------------------------------------------
    good = can_reach_terminal(spec)
    for s in states:
        if s["id"] in reach and s["id"] not in good and not s.get("terminal"):
            findings.append(("WARN", f"state `{s['id']}` ไปไม่ถึงการปิดสายเลย (สายค้าง)"))

    # --- 5. orphan beats (in the catalog, spoken by no state) ------------------
    claimed = set()
    for s in states:
        claimed |= beats_of(s)
    for route in faq_routes(spec):
        claimed |= beats_of(route)
        then = route.get("then")
        if isinstance(then, dict):
            for k in ("beat", "fine_state"):
                if then.get(k):
                    claimed.add(then[k])
    orphans = sorted(b for b in by_beat if b and b not in claimed)
    if orphans and verbose:
        findings.append(("INFO", f"บทพูดที่ไม่มี state ไหนเรียกใช้: {orphans}"))

    return findings


def parity(th: Path, en: Path) -> list[tuple[str, str]]:
    a = json.loads(th.read_text(encoding="utf-8"))
    b = json.loads(en.read_text(encoding="utf-8"))
    ba = {e.get("_fine_state") for e in a.get("catalog", [])}
    bb = {e.get("_fine_state") for e in b.get("catalog", [])}
    out = []
    if ba - bb:
        out.append(("WARN", f"ไทยมี แต่อังกฤษไม่มี: {sorted(ba - bb)}"))
    if bb - ba:
        out.append(("WARN", f"อังกฤษมี แต่ไทยไม่มี: {sorted(bb - ba)}"))
    return out


SEV_ORDER = {"LAW": 0, "WARN": 1, "INFO": 2}
SEV_MARK = {"LAW": "!!", "WARN": " •", "INFO": "  "}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", default=None, help="audit one tenant, e.g. AEON")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    files = sorted(FLOW_DIR.glob("*.company.json"))
    if args.company:
        files = [f for f in files if f.name.upper().startswith(args.company.upper() + ".")]
    if not files:
        raise SystemExit("no tenant spec matched")

    totals = {"LAW": 0, "WARN": 0, "INFO": 0}
    for f in files:
        found = audit(f, args.verbose or bool(args.company))
        if ".en." not in f.name:
            twin = f.with_name(f.name.replace(".company.json", ".en.company.json"))
            if twin.exists():
                found += parity(f, twin)

        shown = [x for x in found if args.verbose or args.company or x[0] != "INFO"]
        for sev, _ in found:
            totals[sev] += 1

        head = f"── {f.name}"
        print(f"\n{head} {'─' * max(0, 62 - len(head))}")
        if not shown:
            print("   สะอาด")
        for sev, msg in sorted(shown, key=lambda x: SEV_ORDER[x[0]]):
            print(f" {SEV_MARK[sev]} [{sev:4s}] {msg}")

    print("\n" + "═" * 64)
    print(f"  LAW {totals['LAW']}   WARN {totals['WARN']}   INFO {totals['INFO']}")
    print("  LAW = ช่องที่แพ้แล้วมีโทษตามกฎหมาย — ต้องเป็น 0 ก่อนปล่อยของ")


if __name__ == "__main__":
    main()
