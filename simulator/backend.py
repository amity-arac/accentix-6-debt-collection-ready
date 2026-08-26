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

# v10: valid call-disposition codes for record_outcome (mirrors the production
# flow's result/reason stamping). Kept in sync with the tool schemas in
# agents/communicator.py.
RESULT_CODES = frozenset({"ptp", "refused", "unreachable", "reached", "tcb", "tin"})


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
    def __init__(self, customer_data: dict, *, v6_active: bool = False,
                 require_kyc: bool = True, valid_results: set[str] | None = None) -> None:
        self.customer_data = customer_data
        # Instruction-grounded outcome vocab: whatever THIS flow's spec declares under
        # outcomes.results (appointment flows use confirmed/rescheduled, surveys use
        # completed/declined, …). None → fall back to the debt default RESULT_CODES.
        # Without this a non-debt company can never close a call: its own declared
        # result code is rejected as `invalid_result`.
        self.valid_results = set(valid_results) if valid_results else None
        self._crm_digits = str(customer_data.get("last_4_digits", ""))
        self.v6_active = v6_active
        # v10 clone is name-only (the real bot it clones never asks for 4 digits);
        # the agent still records payment silently, so payment_date skips the
        # identity check when require_kyc=False.
        self.require_kyc = require_kyc
        # Phase G: per-case verbal commitment state. Set by record_verbal_commitment;
        # checked by payment_date when v6_active. None until the agent calls the tool.
        self._commitment: dict[str, str | None] = {"amount": None, "date": None, "channel": None}
        # v11 Tier-3 flow guards (see documentation/v10-teacher-flow-map.md):
        # last recorded payment_date (for the overdue-ptp-date rule) and whether
        # the call has connected (check_account_status fired — busy/voice_mail
        # are pre-connection-only after that).
        self._last_payment_date: str | None = None
        self._account_status_checked = False

    def _case_status(self) -> str:
        return self.customer_data.get("case_status") or "normal"

    def verify_identity(self, last_4_digits: str) -> dict:
        return {"verified": str(last_4_digits) == self._crm_digits}

    def check_account_status(self) -> dict:
        self._account_status_checked = True
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

    def record_verbal_commitment(self, amount: str, date: str, channel: str = "") -> dict:
        """Phase G: record the customer's verbal commitment to (amount, date[, channel])
        before payment_date writes. No KYC check — this is conversation-state tracking,
        not a CRM write. v5 doesn't call this; v6 system instructions require it.

        `channel` is OPTIONAL: for the common "pay minimum today" close the agent need
        not ask which channel. If the customer volunteers a channel (e.g. an already-paid
        readback) it is validated + stored so payment_date can echo it back.

        Returns a `next_action` hint on success so the LLM gets a runtime reminder
        that the CRM write still needs to happen via payment_date.
        """
        amt = str(amount).strip()
        dt = str(date).strip()
        ch = str(channel).strip()
        if not amt or not dt:
            missing = [k for k, v in (("amount", amt), ("date", dt)) if not v]
            return {"recorded": False, "reason": "incomplete_commitment", "missing": missing}
        if ch and ch not in VALID_PAYMENT_CHANNELS:
            return {"recorded": False, "reason": "channel_invalid", "valid_channels": sorted(VALID_PAYMENT_CHANNELS)}
        if self.v6_active and not datetime_utils.is_valid_date(dt):
            return _date_format_error(dt)
        self._commitment = {"amount": amt, "date": dt, "channel": ch}
        _ch_arg = f", channel={ch!r}" if ch else ""
        return {
            "recorded": True,
            "next_action": (
                f"Verbal commitment captured. CRM write still pending. "
                f"Now call payment_date(last_4_digits, amount={amt}, date={dt!r}{_ch_arg}) "
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
        if self.require_kyc and str(last_4_digits) != self._crm_digits:
            return {"recorded": False, "reason": "identity_mismatch"}
        if channel and channel not in VALID_PAYMENT_CHANNELS:
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
            # channel is optional: only enforce a match when one was actually committed
            if self._commitment["channel"] and self._commitment["channel"] != channel:
                missing.append("channel")
            if missing:
                return {
                    "recorded": False,
                    "reason": "verbal_commitment_missing_or_mismatch",
                    "missing": missing,
                    "expected": dict(self._commitment),
                    "hint": "Call record_verbal_commitment(amount, date, channel) first with values the customer verbally agreed to, then retry payment_date with matching args.",
                }
        self._last_payment_date = date
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

    def record_outcome(self, result: str, reason: str = "", remark: str = "") -> dict:
        """v10: record the call disposition (mirrors the production flow's result/reason
        stamping). result in RESULT_CODES; reason is a free sub-code (paid/minimum/agent/
        wrong_name/...); remark holds the collected free-text utterance. No KYC — this is
        outcome logging, not a CRM write.

        v11 Tier-3 flow guards (documentation/v10-teacher-flow-map.md):
        - overdue account + ptp dated after today -> reject (a future promise on an
          already-overdue account is a refusal, not a ptp).
        - busy/voice_mail after the call has connected -> reject (those are
          pre-connection-only outcomes)."""
        valid = self.valid_results or RESULT_CODES
        if result not in valid:
            return {"recorded": False, "reason": "invalid_result", "valid": sorted(valid)}

        reason_norm = str(reason).strip().lower().replace(" ", "_")

        if (
            self.v6_active
            and result == "ptp"
            and self.customer_data.get("due_status") == "overdue"
            and self._last_payment_date is not None
        ):
            pay_date = datetime_utils.parse_date_prefix(self._last_payment_date)
            if pay_date is not None and pay_date > datetime_utils.simulation_date():
                return {
                    "recorded": False,
                    "reason": "future_ptp_on_overdue_account_requires_refused",
                    "hint": (
                        "This account is already overdue and the recorded payment_date "
                        f"({self._last_payment_date}) is after today. A promise to pay on a "
                        "future date does not count as a ptp on an overdue account — call "
                        "record_outcome(result='refused', ...) instead, or renegotiate for "
                        "a same-day payment before recording ptp."
                    ),
                }

        if reason_norm in {"busy", "voice_mail"} and self._account_status_checked:
            return {
                "recorded": False,
                "reason": "busy_or_voicemail_invalid_after_connected_call",
                "hint": (
                    f"reason={reason_norm!r} means the line never connected — invalid once "
                    "check_account_status has been called, since the call clearly connected. "
                    "Classify by what actually happened (e.g. reached/refused), no busy/voice_mail."
                ),
            }

        return {"recorded": True, "id": _gen_id("OUT"),
                "result": result, "reason": str(reason).strip(), "remark": str(remark).strip()}

    def update_phone(self, number: str) -> dict:
        """v10: record a customer's new contact number (production flow's
        'Change Phone Number' node)."""
        n = str(number).strip()
        if not n:
            return {"recorded": False, "reason": "empty_number"}
        return {"recorded": True, "id": _gen_id("PH"), "number": n}

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
        if name == "record_outcome":
            return self.record_outcome(
                args.get("result", ""), args.get("reason", ""), args.get("remark", "")
            )
        if name == "update_phone":
            return self.update_phone(args.get("number", ""))
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
