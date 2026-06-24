"""Per-case simulated backend: CRM read + write tools for the pre-script agent.

One `CaseBackend` is instantiated per test case and injected into the communicator.
The communicator's tool-call loop routes every non-`reply` tool through `dispatch()`,
which returns a JSON-serializable dict that the LLM sees as the tool result.

`payment_date` re-validates `last_4_digits` against the CRM and rejects on mismatch —
KYC enforcement is in the tool, not the prompt. `callback_datetime` does NOT require
KYC (a callback discloses no debt info and the caller may not be the verified debtor),
so it accepts but does not validate `last_4_digits`. Both still reject on `case_status`
of `pending_review` or `closed` (Phase F): the agent must use the reschedule / apology
templates instead of recording a write.
`payment_date` additionally requires `channel` (one of a small enum) — forces the
agent to capture the customer's stated payment channel before recording.

Phase G (v6) adds a 3-Element Enforcer: per-case `_commitment` state seeded by a
new `record_verbal_commitment(amount, date, channel)` tool the agent calls *before*
`payment_date`. When `v6_active=True`, `payment_date` rejects with
`verbal_commitment_missing_or_mismatch` if the args don't match the prior verbal
commitment — moves the instruction-only "verbally confirm 3 elements first" rule
to a hard tool-level check, mirroring the Phase D KYC → tool pattern.
"""

import secrets

from simulator import datetime_utils
from simulator.tool_logging import LOG_TOOLS, logger as tlog, short


def _gen_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(3).upper()}"


# Valid `reason` values for transfer_to_human_agent — kept in sync with the tool
# schemas in agents/communicator.py.
HANDOFF_REASONS = (
    "language_barrier",
    "legal_proceeding",
    "deceased",
    "data_removal_request",
    "account_dispute",
    "fraud_suspected",
    "customer_distress",
    "other",
)


def _date_format_error(got: str) -> dict:
    """Standard rejection payload for malformed date args under v6.

    Includes a `expected_weekday` hint when the YYYY-MM-DD prefix parses cleanly
    — helps the LLM correct a weekday-mismatch without another tool round-trip.
    """
    payload = {
        "recorded": False,
        "reason": "date_format_invalid",
        "got": got,
        "expected": "YYYY-MM-DD (Weekday), e.g. 2026-05-23 (Saturday)",
        "hint": (
            "Call get_current_datetime() to get today's date and standard offsets, "
            "then pass an exact-format string."
        ),
    }
    # Best-effort weekday hint: if the prefix is a valid calendar date, tell the
    # LLM which weekday it really falls on. Cheap heuristic, no exceptions.
    if isinstance(got, str) and len(got) >= 10:
        wd = datetime_utils.expected_weekday_for(got[:10])
        if wd is not None:
            payload["expected_weekday"] = wd
    return payload


VALID_PAYMENT_CHANNELS = {
    "mobile_app",
    "counter_service",
    "branch",
    "bank_transfer",
    "atm",
    "other",
}


class CaseBackend:
    def __init__(self, customer_data: dict, *, v6_active: bool = False) -> None:
        self.customer_data = customer_data
        self._crm_digits = str(customer_data.get("last_4_digits", ""))
        self.v6_active = v6_active
        # Phase G: per-case verbal commitment state. Set by record_verbal_commitment;
        # checked by payment_date when v6_active. None until the agent calls the tool.
        self._commitment: dict[str, str | None] = {"amount": None, "date": None, "channel": None}

    def _case_status(self) -> str:
        return self.customer_data.get("case_status") or "normal"

    def verify_identity(self, last_4_digits: str) -> dict:
        return {"verified": str(last_4_digits) == self._crm_digits}

    def check_account_status(self) -> dict:
        out = {k: v for k, v in self.customer_data.items() if k != "last_4_digits"}
        out.setdefault("case_status", "normal")
        out.setdefault("case_status_note", None)
        return out

    def callback_datetime(self, last_4_digits: str | None = None, date: str = "") -> dict:
        # Callback scheduling does NOT require KYC: it discloses no debt info, and
        # the caller may not be the debtor (or can't verify right now), so
        # `last_4_digits` is accepted but not validated — the v6/v8 prompts send
        # None for unverified callers. Account-state + date gates still apply.
        status = self._case_status()
        if status == "pending_review":
            return {"recorded": False, "reason": "account_under_review"}
        if status == "closed":
            return {"recorded": False, "reason": "account_closed"}
        if self.v6_active and not datetime_utils.is_valid_date(date):
            return _date_format_error(date)
        return {"recorded": True, "id": _gen_id("CB")}

    def record_verbal_commitment(self, amount: str, date: str, channel: str) -> dict:
        """Phase G: record the customer's verbal commitment to (amount, date, channel)
        before payment_date writes. No KYC check — this is conversation-state tracking,
        not a CRM write. v5 doesn't call this; v6 system instructions require it.

        Returns a `next_action` hint on success so the LLM gets a runtime reminder
        that the CRM write still needs to happen via payment_date.
        """
        amt = str(amount).strip()
        dt = str(date).strip()
        ch = str(channel).strip()
        if not amt or not dt or not ch:
            missing = [k for k, v in (("amount", amt), ("date", dt), ("channel", ch)) if not v]
            return {"recorded": False, "reason": "incomplete_commitment", "missing": missing}
        if ch not in VALID_PAYMENT_CHANNELS:
            return {"recorded": False, "reason": "channel_invalid", "valid_channels": sorted(VALID_PAYMENT_CHANNELS)}
        if self.v6_active and not datetime_utils.is_valid_date(dt):
            return _date_format_error(dt)
        self._commitment = {"amount": amt, "date": dt, "channel": ch}
        return {
            "recorded": True,
            "next_action": (
                f"Verbal commitment captured. CRM write still pending. "
                f"Now call payment_date(last_4_digits, amount={amt}, date={dt!r}, channel={ch!r}) "
                f"with the same values, THEN send the closing reply "
                f"([A_Negotiation_InformPromiseSummary, B_Closing_CloseCallSuccess])."
            ),
        }

    def payment_date(self, last_4_digits: str, amount: float, date: str, channel: str = "") -> dict:
        status = self._case_status()
        if status == "pending_review":
            return {"recorded": False, "reason": "account_under_review"}
        if status == "closed":
            return {"recorded": False, "reason": "account_closed"}
        if str(last_4_digits) != self._crm_digits:
            return {"recorded": False, "reason": "identity_mismatch"}
        if not channel:
            return {"recorded": False, "reason": "channel_required"}
        if channel not in VALID_PAYMENT_CHANNELS:
            return {"recorded": False, "reason": "channel_invalid", "valid_channels": sorted(VALID_PAYMENT_CHANNELS)}
        if self.v6_active and not datetime_utils.is_valid_date(date):
            return _date_format_error(date)
        if self.v6_active:
            # 3-Element Enforcer: payment_date must match prior verbal commitment.
            # Normalize amount as string so "500" == 500.0 == "500.00".
            try:
                want_amt = f"{float(self._commitment['amount']):g}" if self._commitment["amount"] is not None else None
                got_amt = f"{float(amount):g}"
            except (TypeError, ValueError):
                want_amt = self._commitment["amount"]
                got_amt = str(amount)
            missing = []
            if want_amt is None or want_amt != got_amt:
                missing.append("amount")
            if self._commitment["date"] is None or self._commitment["date"] != date:
                missing.append("date")
            if self._commitment["channel"] is None or self._commitment["channel"] != channel:
                missing.append("channel")
            if missing:
                return {
                    "recorded": False,
                    "reason": "verbal_commitment_missing_or_mismatch",
                    "missing": missing,
                    "expected": dict(self._commitment),
                    "hint": "Call record_verbal_commitment(amount, date, channel) first with values the customer verbally agreed to, then retry payment_date with matching args.",
                }
        return {"recorded": True, "id": _gen_id("PP")}

    def get_current_datetime(self) -> dict:
        """Phase H: return the standard-format anchors — computed from the real
        current Asia/Bangkok date/time — the LLM should use for any non-today
        date. Independent of CRM / KYC state.
        """
        return datetime_utils.datetime_lookup_table()

    def transfer_to_human_agent(self, reason: str = "other") -> dict:
        """Hand the case off to a human specialist when the situation is
        genuinely beyond automated handling. Records a handoff ticket and
        returns it. Does NOT require KYC — an escalation discloses no debt
        info, so it is callable for unverified callers (foreigners,
        wrong-number, impersonators, bereaved family).
        """
        if reason not in HANDOFF_REASONS:
            reason = "other"
        return {"transferred": True, "ticket_id": _gen_id("HUM"), "reason": reason}

    def dispatch(self, name: str, args: dict) -> dict:
        """Route a non-reply tool call to its handler, logging the model's call
        and the deterministic result so the demo console shows exactly what the
        model asked for and how the backend handled it (see simulator.tool_logging).
        The customer-facing `reply` tool never reaches here — it (and its guard
        rejections / fallbacks) is logged from agents.communicator instead.
        """
        if LOG_TOOLS:
            tlog.info("[tool-call] model → %s(%s)", name, short(args))
        result = self._dispatch(name, args)
        if LOG_TOOLS:
            tlog.info("[backend]   handled %s → %s", name, short(result))
        return result

    def _dispatch(self, name: str, args: dict) -> dict:
        if name == "verify_identity":
            return self.verify_identity(args.get("last_4_digits", ""))
        if name == "check_account_status":
            return self.check_account_status()
        if name == "callback_datetime":
            return self.callback_datetime(
                args.get("last_4_digits", ""), args.get("date", "")
            )
        if name == "payment_date":
            return self.payment_date(
                args.get("last_4_digits", ""),
                args.get("amount", 0),
                args.get("date", ""),
                args.get("channel", ""),
            )
        if name == "record_verbal_commitment":
            return self.record_verbal_commitment(
                args.get("amount", ""),
                args.get("date", ""),
                args.get("channel", ""),
            )
        if name == "get_current_datetime":
            return self.get_current_datetime()
        if name == "transfer_to_human_agent":
            return self.transfer_to_human_agent(args.get("reason", "other"))
        return {"error": "unknown_tool", "name": name}
