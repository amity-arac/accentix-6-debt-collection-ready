"""Communicator agents for the debt-collection simulation pipeline.

Three concrete classes:
- CommunicatorQwenPreScript:   Qwen via vLLM, OpenAI-compatible tool calling
- CommunicatorGeminiPreScript: Gemini 3.1 Pro, function calling
- CommunicatorGeminiFreeform:  Gemini 3.1 Pro, free-form streaming (no tools)

Both pre-script classes expose 5 tools: `reply` (terminal, customer-facing) plus
`verify_identity`, `check_account_status`, `callback_datetime`, `payment_date`
(non-reply, routed through CaseBackend). `reply()` runs an internal multi-hop loop
until the LLM emits the terminal `reply` tool, capped by MAX_TOOL_HOPS.
"""

import json
import os
import re
import sys
import time
import uuid

import httpx
from google.genai import types


def _hop_log(verbose: bool, hop_idx: int, kind: str, *, name: str = None,
             args=None, result=None, text: str = None, elapsed_ms: float = None) -> None:
    """Write a single hop's log line to stderr (bypasses any stdout redirection).

    Activated by per-instance `self.verbose=True` on the Communicator.
    """
    if not verbose:
        return
    def trim(s: str, n: int = 140) -> str:
        s = (s or "").replace("\n", " ").strip()
        return s if len(s) <= n else s[:n] + "…"
    suffix = f" [{elapsed_ms:.0f}ms]" if elapsed_ms is not None else ""
    if kind == "tool_call":
        try:
            args_repr = json.dumps(args, ensure_ascii=False)
        except Exception:
            args_repr = str(args)
        print(f"    hop {hop_idx}: call {name}({trim(args_repr)}){suffix}", file=sys.stderr, flush=True)
    elif kind == "tool_result":
        try:
            r_repr = json.dumps(result, ensure_ascii=False)
        except Exception:
            r_repr = str(result)
        print(f"    hop {hop_idx}:   → {trim(r_repr)}", file=sys.stderr, flush=True)
    elif kind == "rendered_text":
        print(f"    hop {hop_idx}:   → text: {trim(text)}", file=sys.stderr, flush=True)

from simulator.config import get_client, MAX_TOOL_HOPS, FILLER_TEXT, V6_ACTIVE
from simulator.datetime_utils import is_valid_time, is_within_legal_hours
from agents.helper import calculate_cost, retry_transient
from agents.prescript import (
    DYNAMIC_PLACEHOLDERS,
    DateFormatError,
    build_script_catalog,
    fill_template,
    leaked_placeholders,
    missing_required_dynamic_vars,
)


# Stable order so both tool schemas list dynamic var names identically.
_DYNAMIC_VAR_NAMES = sorted(DYNAMIC_PLACEHOLDERS)

NON_REPLY_TOOLS = (
    "verify_identity",
    "check_account_status",
    "callback_datetime",
    "payment_date",
    "record_verbal_commitment",  # Phase G (v6) — 3-Element Enforcer precondition
    "get_current_datetime",      # Phase H (v6) — date format anchor
)

FALLBACK_TEXT = "ขออภัยค่ะ ระบบขัดข้อง ขอติดต่อกลับใหม่นะคะ"


def _parse_dynamic_vars(raw) -> dict[str, str]:
    """Convert tool-call dynamic_vars (array of {name, value}) to a flat {name: value} dict.

    Unknown variable names are silently dropped — the LLM cannot smuggle
    system-placeholder values (customer_name, amount, etc.) through this path.
    """
    if not raw:
        return {}
    parsed: dict[str, str] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        value = item.get("value")
        if name in DYNAMIC_PLACEHOLDERS and value not in (None, ""):
            parsed[name] = str(value)
    return parsed


def _coerce_text_ids(raw) -> list[int]:
    """Normalize a tool-call `text_ids` argument into a clean list of ints.

    The model is told `text_ids` is an integer array, but in practice it can
    arrive as: a single int/float, a JSON-encoded string ("[1, 2]"), a
    delimiter-separated string ("1\n2" / "1, 2"), or a list whose elements are
    strings (possibly with surrounding whitespace/newlines). Iterating a bare
    string yielded characters, so a literal like "1\n2" reached `int('\n')` and
    raised `ValueError: invalid literal for int() with base 10: '\n'`. This
    helper tolerates all those shapes and drops empty/non-numeric tokens.
    """
    if raw is None:
        return []
    if isinstance(raw, bool):  # bool is an int subclass; treat as invalid
        return []
    if isinstance(raw, (int, float)):
        return [int(raw)]
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return []
        try:
            decoded = json.loads(s)
        except (ValueError, TypeError):
            decoded = None
        if decoded is not None and not isinstance(decoded, str):
            return _coerce_text_ids(decoded)
        # Fall back to splitting on any non-digit run (commas, whitespace,
        # newlines, brackets) and keeping the integer groups.
        return [int(tok) for tok in re.findall(r"-?\d+", s)]
    # list / tuple / other iterable of elements
    out: list[int] = []
    try:
        items = list(raw)
    except TypeError:
        return []
    for item in items:
        if isinstance(item, bool):
            continue
        if isinstance(item, (int, float)):
            out.append(int(item))
        elif isinstance(item, str):
            out.extend(int(tok) for tok in re.findall(r"-?\d+", item))
    return out


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

def _openai_tool_schemas(valid_text_ids: list[int]) -> list[dict]:
    """Return the 5-tool OpenAI/Qwen tool schema list."""
    return [
        {
            "type": "function",
            "function": {
                "name": "reply",
                "description": "Speak to the customer using one or more pre-scripts. Choose appropriate scripts from the catalog. If any selected script lists `Vars: [...]`, provide those values via `dynamic_vars`. This is the only tool that produces customer-visible text; it terminates the turn.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text_ids": {
                            "type": "array",
                            "items": {"type": "integer", "enum": valid_text_ids},
                            "description": "List of pre-script IDs to send (in order). Use 1 for simple replies, 2-3 for compound responses.",
                        },
                        "dynamic_vars": {
                            "type": "array",
                            "description": "List of {name, value} pairs to fill DYNAMIC placeholders in the chosen text_ids. Provide only variables listed in each script's `Vars: [...]` field. Do NOT pass system variables like customer_name, amount, or due_date — backend fills those automatically.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {
                                        "type": "string",
                                        "enum": _DYNAMIC_VAR_NAMES,
                                        "description": "Dynamic variable name. Must appear in the chosen script's Vars: [...] list.",
                                    },
                                    "value": {
                                        "type": "string",
                                        "description": "Value to inject. Must reflect what the customer stated, not the system-of-record.",
                                    },
                                },
                                "required": ["name", "value"],
                            },
                        },
                    },
                    "required": ["text_ids"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "verify_identity",
                "description": "Verify the customer's identity by checking the last 4 digits of their national ID against CRM. Call this AFTER the customer states their digits and BEFORE disclosing any debt information.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "last_4_digits": {"type": "string", "description": "The 4-digit string the customer just stated."},
                    },
                    "required": ["last_4_digits"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "check_account_status",
                "description": "Read the customer's current CRM record (name, loan_type, total_amount_due, due_date, due_status, case_status, case_status_note, etc.). Inspect case_status — 'normal' means proceed; 'pending_review' means the account is under dispute/recalculation: DO NOT press for payment, DO NOT call payment_date or callback_datetime (both will reject), use the dispute-acknowledge → reschedule chain 1092→1096 (AEON) / 2109→2112 (JAI) / 3127→3131 (KS); 'closed' means the account is already settled or written off — apologize via 1097/2113/3132 and end the call.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "callback_datetime",
                "description": "Record a customer's request to be called back at a specific date. Does NOT require KYC — a callback discloses no debt info, so pass last_4_digits=None when the caller hasn't passed identity verification (no need to verify first). Still rejects if case_status is 'pending_review' (use reschedule template 1096/2112/3131 instead) or 'closed' (use apology template 1097/2113/3132). Under v6 (Phase H) also rejects with 'date_format_invalid' if `date` is not in canonical ISO format 'YYYY-MM-DD (Weekday)' — call get_current_datetime first to get the standard string.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "last_4_digits": {"type": "string", "description": "Customer's 4-digit ID if verified; pass null when the caller hasn't passed KYC (callback does not require verification)."},
                        "date": {"type": "string", "description": "Callback date in canonical ISO format 'YYYY-MM-DD (Weekday)', e.g. '2026-05-23 (Saturday)'. Get the exact string by calling get_current_datetime — DO NOT pass natural-language values like 'พรุ่งนี้'."},
                    },
                    "required": ["date"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "payment_date",
                "description": "Record the customer's promise to pay (amount + date + channel). Re-validates last_4_digits against CRM. Rejects with 'identity_mismatch' on KYC fail, 'account_under_review' on pending_review (use 1092→1096 / 2109→2112 / 3127→3131 chain instead), 'account_closed' on closed status, 'channel_required' if channel is missing (ask customer using template 1095/2111/3130 first), or 'channel_invalid' if outside the enum. Under v6 (Phase G): also rejects with 'verbal_commitment_missing_or_mismatch' if record_verbal_commitment was not called first OR if the (amount, date, channel) args do not match the prior verbal commitment. Under v6 (Phase H): rejects with 'date_format_invalid' if `date` is not in canonical ISO format 'YYYY-MM-DD (Weekday)'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "last_4_digits": {"type": "string", "description": "Customer's verified 4-digit ID."},
                        "amount": {"type": "number", "description": "Amount the customer promised to pay (the negotiated amount the customer stated, not the CRM total)."},
                        "date": {"type": "string", "description": "Promised payment date in canonical ISO format 'YYYY-MM-DD (Weekday)', e.g. '2026-05-23 (Saturday)'. Call get_current_datetime first if needed — DO NOT pass natural-language values like 'พรุ่งนี้'."},
                        "channel": {
                            "type": "string",
                            "enum": ["mobile_app", "counter_service", "branch", "bank_transfer", "atm", "other"],
                            "description": "Customer's stated payment channel. If the customer has not stated one, send template 1095/2111/3130 (ask_payment_channel_preference) and wait for their answer before calling this tool.",
                        },
                    },
                    "required": ["last_4_digits", "amount", "date", "channel"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "record_verbal_commitment",
                "description": "Phase G (v6) — STEP 1 of the 3-step close-out for Track A. Records the customer's verbal commitment to (amount, date, channel) in conversation state. DOES NOT WRITE TO CRM — payment_date does that. After this returns {recorded: true}, you MUST immediately call payment_date(last_4_digits, amount, date, channel) with the same args, THEN send the closing reply ([A_Negotiation_InformPromiseSummary, B_Closing_CloseCallSuccess]). All three steps happen in the same turn through the multi-hop loop. Under v6 (Phase H): rejects with 'date_format_invalid' if `date` is not in canonical ISO format 'YYYY-MM-DD (Weekday)'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "amount": {"type": "string", "description": "Amount the customer verbally agreed to pay (numeric string, e.g. '500')."},
                        "date": {"type": "string", "description": "Date the customer verbally agreed to pay in canonical ISO format 'YYYY-MM-DD (Weekday)', e.g. '2026-05-23 (Saturday)'. MUST match the date you pass to payment_date. Call get_current_datetime first."},
                        "channel": {
                            "type": "string",
                            "enum": ["mobile_app", "counter_service", "branch", "bank_transfer", "atm", "other"],
                            "description": "Customer's stated payment channel.",
                        },
                    },
                    "required": ["amount", "date", "channel"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_current_datetime",
                "description": "Phase H (v6) — return today's date plus standard offsets (tomorrow / day_after_tomorrow / in_one_week) in the canonical ISO format 'YYYY-MM-DD (Weekday)'. ALWAYS call this BEFORE proposing, recording, or speaking any non-today date. The strings returned are pass-through values: paste them verbatim into `dynamic_vars[promised_date|callback_date|target_date]` or into `date` args for callback_datetime / payment_date / record_verbal_commitment. Calendar math is done for you; do not modify the strings.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "transfer_to_human_agent",
                "description": "Hand the case off to a HUMAN specialist when the situation is genuinely beyond automated handling. Use ONLY when no script/tool can resolve it: a foreign-language caller you cannot serve, an active legal/bankruptcy/lawyer process, a deceased debtor, a data-removal/wrong-number request, an account-ownership dispute, suspected impersonation, or a customer in severe emotional crisis. DO NOT use it when a callback, partial payment, or dispute ticket would resolve the case, and DO NOT use it merely because the customer asks to speak to a person (acknowledge + offer options instead). No KYC required. After this returns, send the closing handoff reply (A_Context_HumanHandoff).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {
                            "type": "string",
                            "enum": ["language_barrier", "legal_proceeding", "deceased", "data_removal_request", "account_dispute", "fraud_suspected", "customer_distress", "other"],
                            "description": "Why the case is being escalated to a human.",
                        },
                    },
                    "required": ["reason"],
                },
            },
        },
    ]


def _gemini_tool_declarations() -> list[types.FunctionDeclaration]:
    """Return the 5-tool Gemini function declarations."""
    return [
        types.FunctionDeclaration(
            name="reply",
            description="Speak to the customer using one or more pre-scripts. Choose appropriate scripts from the catalog. If any selected script lists `Vars: [...]`, provide those values via `dynamic_vars`. This is the only tool that produces customer-visible text; it terminates the turn.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "text_ids": types.Schema(
                        type="ARRAY",
                        items=types.Schema(type="INTEGER"),
                        description="List of pre-script IDs to send (in order).",
                    ),
                    "dynamic_vars": types.Schema(
                        type="ARRAY",
                        description="List of {name, value} pairs to fill DYNAMIC placeholders in the chosen text_ids. Provide only variables listed in each script's `Vars: [...]` field. Do NOT pass system variables like customer_name, amount, or due_date — backend fills those automatically.",
                        items=types.Schema(
                            type="OBJECT",
                            properties={
                                "name": types.Schema(type="STRING", enum=_DYNAMIC_VAR_NAMES),
                                "value": types.Schema(type="STRING"),
                            },
                            required=["name", "value"],
                        ),
                    ),
                },
                required=["text_ids"],
            ),
        ),
        types.FunctionDeclaration(
            name="verify_identity",
            description="Verify the customer's identity by checking the last 4 digits of their national ID against CRM. Call this AFTER the customer states their digits and BEFORE disclosing any debt information.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "last_4_digits": types.Schema(type="STRING", description="The 4-digit string the customer just stated."),
                },
                required=["last_4_digits"],
            ),
        ),
        types.FunctionDeclaration(
            name="check_account_status",
            description="Read the customer's current CRM record (name, loan_type, total_amount_due, due_date, due_status, case_status, case_status_note, etc.). Inspect case_status — 'normal' means proceed; 'pending_review' means do NOT press for payment (amount may be under recalculation/dispute), instead offer to reschedule or open an investigation ticket; 'closed' means the account is already settled or written off — apologize and close the call.",
            parameters=types.Schema(type="OBJECT", properties={}, required=[]),
        ),
        types.FunctionDeclaration(
            name="callback_datetime",
            description="Record a customer's request to be called back. Does NOT require KYC — a callback discloses no debt info, so pass last_4_digits=None when the caller hasn't passed identity verification (no need to verify first). Still rejects if case_status is 'pending_review' (use reschedule template 1096/2112/3131 instead) or 'closed' (use apology template 1097/2113/3132). Under v6 (Phase H) also rejects with 'date_format_invalid' if `date` is not in canonical ISO format 'YYYY-MM-DD (Weekday)' — call get_current_datetime first.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "last_4_digits": types.Schema(type="STRING", description="Customer's 4-digit ID if verified; pass null when the caller hasn't passed KYC (callback does not require verification)."),
                    "date": types.Schema(type="STRING", description="Callback date in canonical ISO format 'YYYY-MM-DD (Weekday)', e.g. '2026-05-23 (Saturday)'. Get the exact string from get_current_datetime — DO NOT pass natural-language."),
                },
                required=["date"],
            ),
        ),
        types.FunctionDeclaration(
            name="payment_date",
            description="Record the customer's promise to pay (amount + date + channel). Re-validates last_4_digits against CRM. Rejects with 'identity_mismatch' on KYC fail, 'account_under_review' on pending_review (use 1092→1096 / 2109→2112 / 3127→3131 chain instead), 'account_closed' on closed status, 'channel_required' if channel is missing (ask customer using template 1095/2111/3130 first), or 'channel_invalid' if outside the enum. Under v6 (Phase G): also rejects with 'verbal_commitment_missing_or_mismatch' if record_verbal_commitment was not called first OR if the (amount, date, channel) args do not match the prior verbal commitment. Under v6 (Phase H): rejects with 'date_format_invalid' if `date` is not in canonical ISO format 'YYYY-MM-DD (Weekday)'.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "last_4_digits": types.Schema(type="STRING", description="Customer's verified 4-digit ID."),
                    "amount": types.Schema(type="NUMBER", description="Amount the customer promised to pay (the negotiated amount the customer stated, not the CRM total)."),
                    "date": types.Schema(type="STRING", description="Promised payment date in canonical ISO format 'YYYY-MM-DD (Weekday)', e.g. '2026-05-23 (Saturday)'. Call get_current_datetime first."),
                    "channel": types.Schema(
                        type="STRING",
                        enum=["mobile_app", "counter_service", "branch", "bank_transfer", "atm", "other"],
                        description="Customer's stated payment channel. If the customer has not stated one, send template 1095/2111/3130 (ask_payment_channel_preference) first.",
                    ),
                },
                required=["last_4_digits", "amount", "date", "channel"],
            ),
        ),
        types.FunctionDeclaration(
            name="record_verbal_commitment",
            description="Phase G (v6) — STEP 1 of the 3-step close-out for Track A. Records the customer's verbal commitment to (amount, date, channel) in conversation state. DOES NOT WRITE TO CRM — payment_date does that. After this returns {recorded: true}, you MUST immediately call payment_date(last_4_digits, amount, date, channel) with the same args, THEN send the closing reply ([A_Negotiation_InformPromiseSummary, B_Closing_CloseCallSuccess]). All three steps happen in the same turn through the multi-hop loop. Under v6 (Phase H): rejects with 'date_format_invalid' if `date` is not in canonical ISO format 'YYYY-MM-DD (Weekday)'.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "amount": types.Schema(type="STRING", description="Amount the customer verbally agreed to pay (numeric string, e.g. '500')."),
                    "date": types.Schema(type="STRING", description="Date the customer verbally agreed to pay in canonical ISO format 'YYYY-MM-DD (Weekday)', e.g. '2026-05-23 (Saturday)'. MUST match the date arg you pass to payment_date. Call get_current_datetime first."),
                    "channel": types.Schema(
                        type="STRING",
                        enum=["mobile_app", "counter_service", "branch", "bank_transfer", "atm", "other"],
                        description="Customer's stated payment channel.",
                    ),
                },
                required=["amount", "date", "channel"],
            ),
        ),
        types.FunctionDeclaration(
            name="get_current_datetime",
            description="Phase H (v6) — return today's date plus standard offsets (tomorrow / day_after_tomorrow / in_one_week) in the canonical ISO format 'YYYY-MM-DD (Weekday)'. ALWAYS call this BEFORE proposing, recording, or speaking any non-today date. The returned strings are pass-through values — paste them verbatim into `dynamic_vars[promised_date|callback_date|target_date]` or into `date` args for callback_datetime / payment_date / record_verbal_commitment. Calendar math is done for you.",
            parameters=types.Schema(type="OBJECT", properties={}, required=[]),
        ),
        types.FunctionDeclaration(
            name="transfer_to_human_agent",
            description="Hand the case off to a HUMAN specialist when the situation is genuinely beyond automated handling. Use ONLY when no script/tool can resolve it: a foreign-language caller you cannot serve, an active legal/bankruptcy/lawyer process, a deceased debtor, a data-removal/wrong-number request, an account-ownership dispute, suspected impersonation, or a customer in severe emotional crisis. DO NOT use it when a callback, partial payment, or dispute ticket would resolve the case, and DO NOT use it merely because the customer asks to speak to a person (acknowledge + offer options instead). No KYC required. After this returns, send the closing handoff reply (A_Context_HumanHandoff).",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "reason": types.Schema(
                        type="STRING",
                        enum=["language_barrier", "legal_proceeding", "deceased", "data_removal_request", "account_dispute", "fraud_suspected", "customer_distress", "other"],
                        description="Why the case is being escalated to a human.",
                    ),
                },
                required=["reason"],
            ),
        ),
    ]


# ---------------------------------------------------------------------------
# Free-form tool schemas — same backend ACTION tools, but NO `reply`.
# Free-form modes generate their own Thai text (the model's plain text output IS
# the customer-facing reply); they still get the deterministic CaseBackend tools
# so KYC / payment / callback enforcement is measured the same way as pre-script.
# ---------------------------------------------------------------------------

def _openai_freeform_tool_schemas() -> list[dict]:
    """OpenAI/Qwen schema list for the 6 backend action tools (no `reply`)."""
    return [t for t in _openai_tool_schemas([]) if t["function"]["name"] != "reply"]


def _gemini_freeform_tool_declarations() -> list[types.FunctionDeclaration]:
    """Gemini function declarations for the 6 backend action tools (no `reply`)."""
    return [d for d in _gemini_tool_declarations() if d.name != "reply"]


# ---------------------------------------------------------------------------
# Reply rendering (shared)
# ---------------------------------------------------------------------------

# Phase G — payment-amount slots blocked on pending_review accounts.
_PAYMENT_SLOTS = ("[target_amount]", "[promised_amount]", "[micro_amount]", "[minimum_payment]")

# Phase G — state pairs forbidden in a single chain. Same-state pairs are always OK.
# All other pairs default to OK unless listed here. Inform_* templates retain
# cross-state flexibility (caller's responsibility to tag them with a single state).
_INCOMPATIBLE_STATE_PAIRS = frozenset({
    frozenset({"kyc", "closing"}),
    frozenset({"kyc", "dispute"}),
    frozenset({"kyc", "hardship"}),
    frozenset({"opening", "closing"}),
    frozenset({"opening", "negotiation"}),
})


def _validate_chain(
    text_ids: list[int],
    script_lookup: dict[int, dict],
    *,
    case_status: str = "normal",
) -> str | None:
    """Phase F + G: catch incompatible chains.

    Phase F (v5 flags): closer + question, closer + demand.
    Phase G (v6 metadata): Category Lock (B + B), State Lock (kyc + closing,
    etc.), Dispute Lock (any payment-slot template on pending_review).

    The v6 checks no-op when the catalog lacks `category` / `state` fields —
    preserves v5 behavior unchanged. The Dispute Lock is body-text-based and
    fires regardless of catalog version, since it's a strict improvement.

    Returns None when the chain is OK, or a short reason string when a check
    fails. The caller converts a non-None result into a tool rejection so the
    agent retries with a different chain.
    """
    # Phase G — Dispute Lock applies even to single-template chains.
    if case_status == "pending_review":
        for tid in text_ids:
            s = script_lookup.get(int(tid))
            if s is None:
                continue
            body = s.get("template", "")
            for slot in _PAYMENT_SLOTS:
                if slot in body:
                    return (
                        f"dispute_lock: text_id {tid} contains payment-amount slot "
                        f"{slot} but case_status is pending_review. Use a "
                        f"dispute-acknowledge or callback-time template (no payment ask)."
                    )

    if len(text_ids) < 2:
        return None

    flags: list[dict] = []
    for tid in text_ids:
        s = script_lookup.get(int(tid))
        if s is None:
            continue
        flags.append({
            "id": int(tid),
            "closer": bool(s.get("is_closer")),
            "demand": bool(s.get("is_demand")),
            "ack": bool(s.get("is_acknowledgment")),
            "q": bool(s.get("expects_response")),
            "category": s.get("category"),
            "state": s.get("state"),
        })

    for i, a in enumerate(flags):
        for b in flags[i + 1:]:
            # Phase F — closer + question, closer + demand
            if a["closer"] and b["q"]:
                return f"incompatible_chain: {a['id']} is_closer paired with {b['id']} expects_response"
            if a["q"] and b["closer"]:
                return f"incompatible_chain: {a['id']} expects_response paired with {b['id']} is_closer"
            if a["closer"] and b["demand"]:
                return f"incompatible_chain: {a['id']} is_closer paired with {b['id']} is_demand"
            if a["demand"] and b["closer"]:
                return f"incompatible_chain: {a['id']} is_demand paired with {b['id']} is_closer"
            # Phase G — Category Lock (no-op when category absent → v5 catalog passes through)
            if a["category"] == "B" and b["category"] == "B":
                return (
                    f"category_lock: {a['id']} and {b['id']} are both Category B (probe). "
                    f"A chain needs one Category A (acknowledge/inform) + one Category B."
                )
            # Phase G — State Lock (no-op when state absent)
            if a["state"] and b["state"] and a["state"] != b["state"]:
                if frozenset({a["state"], b["state"]}) in _INCOMPATIBLE_STATE_PAIRS:
                    return (
                        f"state_lock: {a['id']} ({a['state']}) and {b['id']} ({b['state']}) "
                        f"are not compatible in one chain"
                    )
    return None


def _render_reply(script_db: list[dict], script_lookup: dict[int, dict],
                  agent_context_data: dict, text_ids: list[int],
                  dynamic_vars: dict[str, str]) -> tuple[str, list[int], list[str]]:
    """Resolve text_ids → templates → filled Thai. Returns (text, valid_ids, intent_names).

    Under v6 (V6_ACTIVE=True) the underlying fill_template runs in strict mode:
    DATE_PLACEHOLDERS / TIME_PLACEHOLDERS values must match the canonical ISO
    format, otherwise DateFormatError propagates to the reply tool handler
    which converts it to `{sent: False, reason: "date_format_invalid", ...}`.
    """
    scripts = []
    for tid in text_ids:
        s = script_lookup.get(int(tid))
        if s is not None:
            scripts.append(s)
    if not scripts:
        scripts = [script_db[0]]
    filled_parts = [
        fill_template(s["template"], agent_context_data, dynamic_vars=dynamic_vars,
                      strict_dates=V6_ACTIVE)
        for s in scripts
    ]
    return (
        " ".join(filled_parts),
        [s["text_id"] for s in scripts],
        [s.get("intent_name", "") for s in scripts],
    )


# ---------------------------------------------------------------------------
# Qwen Pre-Script
# ---------------------------------------------------------------------------

class CommunicatorQwenPreScript:
    """Pre-script communicator using Qwen via vLLM (OpenAI-compatible tool calling)."""

    MODEL = "Qwen/Qwen3.5-9B"
    BASE_URL = "http://localhost:8000/v1"

    # Hot-path LLM client hardening. The OpenAI SDK defaults are unsafe for an
    # interactive tool loop: a 600s read timeout lets a stalled vLLM hang a
    # single turn for ~10 minutes, and 2 silent exponential-backoff retries
    # (0.5→8s) on any 429/5xx turn an intermittent blip into invisible
    # multi-second stalls plus 2 extra full generations. Fail fast instead, and
    # cap output tokens so a runaway decode can't run to the 32k context limit
    # (a reply is just text_ids + dynamic_vars; a non-reply tool call is tiny).
    REQUEST_TIMEOUT = httpx.Timeout(connect=5.0, read=90.0, write=10.0, pool=5.0)
    MAX_RETRIES = 1
    MAX_OUTPUT_TOKENS = 1024

    def __init__(
        self,
        system_prompt: str,
        script_db: list[dict],
        agent_context_data: dict,
        *,
        model: str | None = None,
        base_url: str | None = None,
        stream_tool_calls: bool = False,
        append_script_catalog: bool = True,
        tool_choice: str = "auto",
        temperature: float | None = None,
        seed: int | None = None,
    ) -> None:
        from openai import OpenAI

        self.model = model or self.MODEL
        self.base_url = base_url or self.BASE_URL
        self.client = OpenAI(
            base_url=self.base_url,
            api_key=os.getenv("VLLM_API_KEY", "unused"),
            timeout=self.REQUEST_TIMEOUT,
            max_retries=self.MAX_RETRIES,
        )

        self.script_db = script_db
        self.agent_context_data = agent_context_data
        self.script_lookup: dict[int, dict] = {s["text_id"]: s for s in script_db}
        self.all_valid_ids = [s["text_id"] for s in script_db]

        # Demo's qwen-demo variant hand-curates a catalog directly in the .md
        # body, so we skip the runtime append to keep Qwen from seeing two
        # different catalogs (the curated one + the full 58-template one).
        # Default True preserves the benchmark contract.
        if append_script_catalog:
            catalog = build_script_catalog(script_db, compact=True)
            self.system_prompt = f"{system_prompt}\n\n{catalog}"
        else:
            self.system_prompt = system_prompt
        self.history: list[dict] = []
        self.tools = _openai_tool_schemas(self.all_valid_ids)
        self.verbose = False  # set externally to enable per-hop stderr progress logs
        # Optional sink invoked as each hop is appended during reply(). Set
        # externally by demo/server/sessions.py to stream hops to the frontend
        # in real time. Default None preserves the benchmark contract.
        self.on_hop = None  # type: ignore[assignment]
        # Demo-only: when True, use streaming chat completions and emit a
        # `tool_call_pending` hop as soon as the tool name is decoded from
        # the first delta. Lets the UI render the "รบกวนรอซักครู่ค่ะ" filler
        # ~500-1000ms earlier on KYC/non-reply turns. Default False preserves
        # the benchmark contract (simulator passes the default).
        self.stream_tool_calls = stream_tool_calls
        # tool_choice for vLLM chat.completions. "auto" (free generation in the
        # trained qwen3_xml format, parsed afterwards) is the validated default for
        # the SFT model. "required" applies grammar-constrained decoding that
        # perturbs the model's marginal tool-preference logits and flips the argmax
        # ~50% even at temp=0 (proven in the sft_v0 overfit gate: required gave
        # check_account_status 4/8 vs verify_identity 4/8 on the same request;
        # "auto" gave 8/8 correct). The SFT model always emits a tool call
        # (no_tool rate 0.0), so "auto" never degenerates to free text. Pass
        # "required" for base / un-finetuned Qwen that needs forcing.
        self.tool_choice = tool_choice
        # Pinned sampling for reproducible benchmarking. Both default None →
        # vLLM server default (~1.0), preserving prior behavior. Set from run.py
        # via --agent-temperature / --seed; temp 0 makes the agent deterministic
        # so the only remaining benchmark stochasticity is the simulated customer.
        self.temperature = temperature
        self.seed = seed

    def _sampling_kwargs(self) -> dict:
        kw: dict = {}
        if self.temperature is not None:
            kw["temperature"] = self.temperature
        if self.seed is not None:
            kw["seed"] = self.seed
        return kw

    def _emit(self, hop: dict) -> None:
        cb = self.on_hop
        if cb is not None:
            try:
                cb(hop)
            except Exception:
                pass

    def _make_hops_list(self) -> list:
        """When on_hop is set, return a list whose append() also calls on_hop."""
        cb = self.on_hop
        if cb is None:
            return []

        class _EmittingList(list):
            def append(self, item, _cb=cb):
                list.append(self, item)
                try:
                    _cb(item)
                except Exception:
                    pass

        return _EmittingList()

    def reply(self, message: str, backend) -> dict:
        self.history.append({"role": "user", "content": message})

        hops = self._make_hops_list()
        any_non_reply = False
        total_ms = 0.0
        total_cost = 0.0

        for hop_idx in range(MAX_TOOL_HOPS + 1):
            messages = [{"role": "system", "content": self.system_prompt}] + self.history

            t_start = time.perf_counter()
            if self.stream_tool_calls:
                name, args, tool_call_id, raw_args_str = self._llm_streamed(messages)
            else:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=self.tools,
                    tool_choice=self.tool_choice,
                    max_tokens=self.MAX_OUTPUT_TOKENS,
                    **self._sampling_kwargs(),
                    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
                )
                name, args, tool_call_id, raw_args_str = self._extract_first_tool_call(response)
            hop_ms = (time.perf_counter() - t_start) * 1000
            total_ms += hop_ms

            if name == "reply":
                text_ids = _coerce_text_ids(args.get("text_ids", []))
                # Phase F: pairwise chain-compatibility check. Reject incompatible
                # chains (e.g., closer + question) and feed the rejection back to
                # the LLM as a tool result so it can retry with a different chain.
                chain_err = _validate_chain(
                    text_ids,
                    self.script_lookup,
                    case_status=backend.customer_data.get("case_status") or "normal",
                )
                if chain_err is not None:
                    result = {"sent": False, "reason": chain_err}
                    _hop_log(self.verbose, hop_idx + 1, "tool_call",
                             name="reply", args={"text_ids": text_ids}, elapsed_ms=hop_ms)
                    _hop_log(self.verbose, hop_idx + 1, "tool_result", result=result)
                    hops.append({"kind": "tool_call", "name": "reply", "args": {"text_ids": text_ids}})
                    hops.append({"kind": "tool_result", "name": "reply", "result": result})
                    self.history.append({
                        "role": "assistant",
                        "tool_calls": [{
                            "id": tool_call_id,
                            "type": "function",
                            "function": {"name": "reply", "arguments": raw_args_str},
                        }],
                    })
                    self.history.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": json.dumps(result, ensure_ascii=False),
                    })
                    continue
                dynamic_vars = _parse_dynamic_vars(args.get("dynamic_vars"))
                # Pre-render guard: a chosen template references a concrete date/time
                # the agent didn't supply → its Thai fallback would leak into a
                # customer-facing confirmation (e.g. a callback with no real date).
                # Agent-fixable: reject + retry so it supplies the dynamic_var.
                missing_dyn = missing_required_dynamic_vars(self.script_lookup, text_ids, dynamic_vars)
                if missing_dyn:
                    result = {
                        "sent": False, "reason": "dynamic_var_required", "missing": missing_dyn,
                        "hint": "These templates need concrete values — supply them via dynamic_vars "
                                "(e.g. callback_date='2026-05-23 (Saturday)', callback_time='14:00'). "
                                "Call get_current_datetime() first for dates.",
                    }
                    _hop_log(self.verbose, hop_idx + 1, "tool_call",
                             name="reply", args={"text_ids": text_ids, "dynamic_vars": dynamic_vars},
                             elapsed_ms=hop_ms)
                    _hop_log(self.verbose, hop_idx + 1, "tool_result", result=result)
                    hops.append({"kind": "tool_call", "name": "reply", "args": {"text_ids": text_ids, "dynamic_vars": dynamic_vars}})
                    hops.append({"kind": "tool_result", "name": "reply", "result": result})
                    self.history.append({
                        "role": "assistant",
                        "tool_calls": [{
                            "id": tool_call_id,
                            "type": "function",
                            "function": {"name": "reply", "arguments": raw_args_str},
                        }],
                    })
                    self.history.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": json.dumps(result, ensure_ascii=False),
                    })
                    continue
                # Legal-hours guard (พ.ร.บ.การทวงถามหนี้ §9(2)): a well-formed callback_time
                # outside 08:00–20:00 is an auto-compliance-violation. Agent-fixable: reject + retry.
                cb_time = dynamic_vars.get("callback_time")
                if cb_time and is_valid_time(cb_time) and not is_within_legal_hours(cb_time):
                    result = {
                        "sent": False, "reason": "callback_time_out_of_legal_hours", "got": cb_time,
                        "hint": "Callbacks must be within legal contact hours 08:00–20:00 "
                                "(พ.ร.บ.การทวงถามหนี้ §9(2)). Propose a time within that window.",
                    }
                    _hop_log(self.verbose, hop_idx + 1, "tool_call",
                             name="reply", args={"text_ids": text_ids, "dynamic_vars": dynamic_vars},
                             elapsed_ms=hop_ms)
                    _hop_log(self.verbose, hop_idx + 1, "tool_result", result=result)
                    hops.append({"kind": "tool_call", "name": "reply", "args": {"text_ids": text_ids, "dynamic_vars": dynamic_vars}})
                    hops.append({"kind": "tool_result", "name": "reply", "result": result})
                    self.history.append({
                        "role": "assistant",
                        "tool_calls": [{
                            "id": tool_call_id,
                            "type": "function",
                            "function": {"name": "reply", "arguments": raw_args_str},
                        }],
                    })
                    self.history.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": json.dumps(result, ensure_ascii=False),
                    })
                    continue
                try:
                    text, final_ids, intent_names = _render_reply(
                        self.script_db, self.script_lookup, self.agent_context_data,
                        text_ids, dynamic_vars,
                    )
                except DateFormatError as e:
                    result = {
                        "sent": False, "reason": "date_format_invalid",
                        "placeholder": e.placeholder, "got": e.got, "expected": e.expected,
                        "hint": "Call get_current_datetime() and paste the standard string verbatim.",
                    }
                    _hop_log(self.verbose, hop_idx + 1, "tool_call",
                             name="reply", args={"text_ids": text_ids, "dynamic_vars": dynamic_vars},
                             elapsed_ms=hop_ms)
                    _hop_log(self.verbose, hop_idx + 1, "tool_result", result=result)
                    hops.append({"kind": "tool_call", "name": "reply", "args": {"text_ids": text_ids, "dynamic_vars": dynamic_vars}})
                    hops.append({"kind": "tool_result", "name": "reply", "result": result})
                    self.history.append({
                        "role": "assistant",
                        "tool_calls": [{
                            "id": tool_call_id,
                            "type": "function",
                            "function": {"name": "reply", "arguments": raw_args_str},
                        }],
                    })
                    self.history.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": json.dumps(result, ensure_ascii=False),
                    })
                    continue
                # Post-render: surface (don't retry-loop on) unfilled SYSTEM/unknown
                # [name] literals — a missing-CRM-field or stale-template content bug,
                # not agent-fixable. Tagged on the hop for measurement.
                leaks = leaked_placeholders(text)
                if leaks:
                    print(f"    placeholder_leak in rendered reply: {leaks} (text_ids={final_ids})",
                          file=sys.stderr, flush=True)
                _hop_log(self.verbose, hop_idx + 1, "tool_call",
                         name="reply", args={"text_ids": final_ids, "dynamic_vars": dynamic_vars},
                         elapsed_ms=hop_ms)
                _hop_log(self.verbose, hop_idx + 1, "rendered_text", text=text)
                # Single combined assistant message: tool_calls + content.
                self.history.append({
                    "role": "assistant",
                    "tool_calls": [{
                        "id": tool_call_id,
                        "type": "function",
                        "function": {"name": "reply", "arguments": raw_args_str},
                    }],
                    "content": text,
                })
                hops.append({"kind": "tool_call", "name": "reply", "args": {"text_ids": final_ids, "dynamic_vars": dynamic_vars}})
                hops.append({"kind": "rendered_text", "text": text, **({"placeholder_leak": leaks} if leaks else {})})
                agent_messages = [FILLER_TEXT, text] if any_non_reply else [text]
                return {
                    "text": text,
                    "agent_messages": agent_messages,
                    "hops": hops,
                    "text_ids": final_ids,
                    "intent_names": intent_names,
                    "dynamic_vars": dynamic_vars,
                    "ttft_ms": total_ms,
                    "total_ms": total_ms,
                    "cost": total_cost,
                }

            # Non-reply: dispatch via backend.
            any_non_reply = True
            result = backend.dispatch(name, args)
            _hop_log(self.verbose, hop_idx + 1, "tool_call", name=name, args=args, elapsed_ms=hop_ms)
            _hop_log(self.verbose, hop_idx + 1, "tool_result", result=result)
            hops.append({"kind": "tool_call", "name": name, "args": args})
            hops.append({"kind": "tool_result", "name": name, "result": result})
            self.history.append({
                "role": "assistant",
                "tool_calls": [{
                    "id": tool_call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": raw_args_str},
                }],
            })
            self.history.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": json.dumps(result, ensure_ascii=False),
            })

        # Exhausted hops — synthesize a fallback reply.
        if self.verbose:
            print(f"    MAX_TOOL_HOPS exhausted — falling back", file=sys.stderr, flush=True)
        return self._fallback_return(hops, any_non_reply, total_ms, total_cost)

    def _extract_first_tool_call(self, response) -> tuple[str, dict, str, str]:
        try:
            tc = response.choices[0].message.tool_calls[0]
            name = tc.function.name
            raw_args_str = tc.function.arguments or "{}"
            args = json.loads(raw_args_str)
            # vLLM tool parsers occasionally emit non-object JSON for arguments
            # (an int, string, or array). backend.dispatch unconditionally calls
            # .get(...) on args, so a non-dict crashes the hop with AttributeError.
            # Coerce to {} so the backend tool rejects cleanly with missing-arg
            # semantics rather than blowing up.
            if not isinstance(args, dict):
                args = {}
                raw_args_str = "{}"
            return name, args, tc.id, raw_args_str
        except (IndexError, KeyError, TypeError, AttributeError, json.JSONDecodeError):
            return "reply", {"text_ids": [self.script_db[0]["text_id"]]}, str(uuid.uuid4()), json.dumps({"text_ids": [self.script_db[0]["text_id"]]})

    def _llm_streamed(self, messages: list[dict]) -> tuple[str, dict, str, str]:
        """Streaming variant of the chat-completion call (demo only).

        Emits `{"kind": "tool_call_pending", "name": <tool_name>}` via on_hop
        as soon as the tool name is decoded from the first delta — well before
        the full arguments are generated. demo/server/sessions.py consumes
        this signal to render the "รบกวนรอซักครู่ค่ะ" filler ~500ms-1s earlier
        on non-reply turns. Returns the same tuple as `_extract_first_tool_call`
        so the rest of `reply()` is unchanged.
        """
        stream = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=self.tools,
            tool_choice=self.tool_choice,
            stream=True,
            max_tokens=self.MAX_OUTPUT_TOKENS,
            **self._sampling_kwargs(),
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )

        name: str | None = None
        tool_call_id: str | None = None
        args_parts: list[str] = []
        pending_emitted = False

        try:
            for chunk in stream:
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                delta = getattr(choices[0], "delta", None)
                tcs = getattr(delta, "tool_calls", None) if delta is not None else None
                if not tcs:
                    continue
                tc = tcs[0]
                tc_id = getattr(tc, "id", None)
                if tc_id:
                    tool_call_id = tc_id
                fn = getattr(tc, "function", None)
                if fn is None:
                    continue
                fn_name = getattr(fn, "name", None)
                if fn_name and name is None:
                    name = fn_name
                    if not pending_emitted and name != "reply":
                        pending_emitted = True
                        self._emit({"kind": "tool_call_pending", "name": name})
                fn_args = getattr(fn, "arguments", None)
                if fn_args:
                    args_parts.append(fn_args)
        except Exception:
            # If the stream breaks mid-flight, fall through to the fallback
            # branch below — matches _extract_first_tool_call's behavior.
            pass

        # Two valid tool-call streaming protocols exist among vLLM tool parsers:
        # (a) OpenAI spec — each delta carries an arguments FRAGMENT; concat them.
        # (b) Some vLLM parsers — each delta carries the FULL accumulated args.
        # Protocol (b) makes naive concat produce `{"x":1}{"x":1}{"x":1}` which
        # is invalid JSON. If concat fails to parse, fall back to the last chunk
        # that is itself valid JSON (protocol b's payload is complete each time).
        raw_concat = "".join(args_parts)
        args: dict | None = None
        if raw_concat:
            try:
                args = json.loads(raw_concat)
            except json.JSONDecodeError:
                for chunk in reversed(args_parts):
                    chunk_s = chunk.strip()
                    if not chunk_s:
                        continue
                    try:
                        args = json.loads(chunk_s)
                        break
                    except json.JSONDecodeError:
                        continue
        # Coerce to dict — vLLM tool parsers occasionally emit non-object JSON
        # (int/string/array) for arguments. backend.dispatch calls .get() on
        # args, so a non-dict crashes the hop with AttributeError instead of
        # being rejected cleanly as a missing-arg tool result.
        if not isinstance(args, dict):
            args = {}

        if name is None:
            # Fallback identical to _extract_first_tool_call's except branch.
            fallback_args = {"text_ids": [self.script_db[0]["text_id"]]}
            return "reply", fallback_args, str(uuid.uuid4()), json.dumps(fallback_args)
        if tool_call_id is None:
            tool_call_id = str(uuid.uuid4())
        # ALWAYS serialize to canonical JSON. This is the string that gets
        # stored in self.history as the assistant message's tool_call arguments
        # and sent BACK to vLLM on the next hop. If we returned the raw concat
        # under protocol (b) it would crash vLLM's chat-template renderer in
        # _postprocess_messages with `JSONDecodeError: Extra data`.
        raw_args_str = json.dumps(args, ensure_ascii=False)
        return name, args, tool_call_id, raw_args_str

    def prewarm_cache(self) -> None:
        """Fire a tiny completion to populate vLLM's prefix cache.

        Called by demo/server/app.py as a background task immediately after
        session creation. The first real hop then hits a warm prefix instead
        of paying full prompt-processing cost. Fire-and-forget: any error is
        swallowed since the worst case is just slower-first-turn.

        Uses tool_choice="none" so vLLM still renders the tool schemas into
        the chat template (keeping the cached prefix aligned with the real
        request's prefix) but skips invoking the Hermes tool parser on the
        output. With tool_choice="auto" + max_tokens=1 the parser regex
        matched an empty fragment and crashed with JSONDecodeError —
        harmless (the request still 200'd) but noisy in vLLM's server log.
        """
        try:
            self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": "ping"},
                ],
                tools=self.tools,
                tool_choice="none",
                max_tokens=1,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
        except Exception:
            pass

    def _fallback_return(self, hops, any_non_reply, total_ms, total_cost) -> dict:
        fallback_tid = self.script_db[0]["text_id"]
        text, final_ids, intent_names = _render_reply(
            self.script_db, self.script_lookup, self.agent_context_data,
            [fallback_tid], {},
        )
        text = FALLBACK_TEXT
        self.history.append({"role": "assistant", "content": text})
        hops.append({"kind": "tool_call", "name": "reply", "args": {"text_ids": final_ids, "dynamic_vars": {}, "fallback": True}})
        hops.append({"kind": "rendered_text", "text": text})
        agent_messages = [FILLER_TEXT, text] if any_non_reply else [text]
        return {
            "text": text,
            "agent_messages": agent_messages,
            "hops": hops,
            "text_ids": final_ids,
            "intent_names": intent_names,
            "dynamic_vars": {},
            "ttft_ms": total_ms,
            "total_ms": total_ms,
            "cost": total_cost,
        }

    def reset(self) -> None:
        self.history.clear()


# ---------------------------------------------------------------------------
# Qwen Freeform (tool-enabled — free Thai text reply + backend actions)
# ---------------------------------------------------------------------------

class CommunicatorQwenFreeform:
    """Free-form communicator using Qwen via vLLM (OpenAI-compatible).

    The Qwen analog of CommunicatorGeminiFreeform: NO `reply` tool and NO script
    catalog — the model writes its own Thai (its plain `content` IS the reply) —
    but it DOES get the 6 deterministic CaseBackend action tools via
    `tool_choice="auto"`, so KYC / payment / callback go through the same backend
    as pre-script mode. `reply()` loops until the model returns a content turn
    (no tool call).
    """

    MODEL = "Qwen/Qwen3.5-9B"
    BASE_URL = "http://localhost:8000/v1"

    def __init__(
        self,
        system_prompt: str,
        *,
        model: str | None = None,
        base_url: str | None = None,
        tool_choice: str = "auto",
        temperature: float | None = None,
        seed: int | None = None,
    ) -> None:
        from openai import OpenAI

        self.model = model or self.MODEL
        self.base_url = base_url or self.BASE_URL
        self.client = OpenAI(base_url=self.base_url, api_key=os.getenv("VLLM_API_KEY", "unused"))
        self.system_prompt = system_prompt
        self.history: list[dict] = []
        self.tools = _openai_freeform_tool_schemas()
        # "auto" lets the model choose between speaking (free text) and calling a
        # backend action tool — the essence of free-form. (Pre-script forces a
        # tool every turn; free-form must be able to emit plain text.)
        self.tool_choice = tool_choice
        # Pinned sampling (None → vLLM default ~1.0). Set from run.py for reproducible benchmarks.
        self.temperature = temperature
        self.seed = seed
        self.verbose = False

    def _sampling_kwargs(self) -> dict:
        kw: dict = {}
        if self.temperature is not None:
            kw["temperature"] = self.temperature
        if self.seed is not None:
            kw["seed"] = self.seed
        return kw

    def _extract(self, response) -> tuple[str | None, dict, str | None, str, str]:
        """Return (fn_name|None, args, tool_call_id, raw_args_str, content)."""
        try:
            msg = response.choices[0].message
        except (IndexError, AttributeError, TypeError):
            return None, {}, None, "{}", ""
        content = msg.content or ""
        tcs = getattr(msg, "tool_calls", None)
        if tcs:
            tc = tcs[0]
            name = tc.function.name
            raw = tc.function.arguments or "{}"
            try:
                args = json.loads(raw)
            except json.JSONDecodeError:
                args, raw = {}, "{}"
            if not isinstance(args, dict):
                args, raw = {}, "{}"
            return name, args, tc.id, raw, content
        return None, {}, None, "{}", content

    def reply(self, message: str, backend) -> dict:
        self.history.append({"role": "user", "content": message})

        hops: list[dict] = []
        any_non_reply = False
        total_ms = 0.0
        total_cost = 0.0  # local vLLM — no API cost (mirrors CommunicatorQwenPreScript)

        for hop_idx in range(MAX_TOOL_HOPS + 1):
            messages = [{"role": "system", "content": self.system_prompt}] + self.history
            t_start = time.perf_counter()
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=self.tools,
                tool_choice=self.tool_choice,
                **self._sampling_kwargs(),
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            hop_ms = (time.perf_counter() - t_start) * 1000
            total_ms += hop_ms

            name, args, tool_call_id, raw_args_str, content = self._extract(response)

            if name and backend is not None:
                any_non_reply = True
                result = backend.dispatch(name, args)
                _hop_log(self.verbose, hop_idx + 1, "tool_call", name=name, args=args, elapsed_ms=hop_ms)
                _hop_log(self.verbose, hop_idx + 1, "tool_result", result=result)
                hops.append({"kind": "tool_call", "name": name, "args": args})
                hops.append({"kind": "tool_result", "name": name, "result": result})
                self.history.append({
                    "role": "assistant",
                    "tool_calls": [{
                        "id": tool_call_id,
                        "type": "function",
                        "function": {"name": name, "arguments": raw_args_str},
                    }],
                })
                self.history.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": json.dumps(result, ensure_ascii=False),
                })
                continue

            # Content turn → customer-facing reply.
            text = content or FALLBACK_TEXT
            self.history.append({"role": "assistant", "content": text})
            _hop_log(self.verbose, hop_idx + 1, "rendered_text", text=text)
            hops.append({"kind": "rendered_text", "text": text})
            agent_messages = [FILLER_TEXT, text] if any_non_reply else [text]
            return {
                "text": text,
                "agent_messages": agent_messages,
                "hops": hops,
                "ttft_ms": total_ms,
                "total_ms": total_ms,
                "cost": total_cost,
            }

        # Exhausted hops — synthesize a fallback reply.
        if self.verbose:
            print(f"    MAX_TOOL_HOPS exhausted — falling back", file=sys.stderr, flush=True)
        self.history.append({"role": "assistant", "content": FALLBACK_TEXT})
        hops.append({"kind": "rendered_text", "text": FALLBACK_TEXT})
        agent_messages = [FILLER_TEXT, FALLBACK_TEXT] if any_non_reply else [FALLBACK_TEXT]
        return {
            "text": FALLBACK_TEXT,
            "agent_messages": agent_messages,
            "hops": hops,
            "ttft_ms": total_ms,
            "total_ms": total_ms,
            "cost": total_cost,
        }

    def reset(self) -> None:
        self.history.clear()


# ---------------------------------------------------------------------------
# Gemini Pre-Script
# ---------------------------------------------------------------------------

class CommunicatorGeminiPreScript:
    """Pre-script communicator using Gemini function calling."""

    MODEL = "gemini-3.1-pro-preview"

    def __init__(
        self,
        system_prompt: str,
        script_db: list[dict],
        agent_context_data: dict,
        *,
        model: str | None = None,
        temperature: float | None = None,
        seed: int | None = None,
    ) -> None:
        self.model = model or self.MODEL
        self.client = get_client()

        self.script_db = script_db
        self.agent_context_data = agent_context_data
        self.script_lookup: dict[int, dict] = {s["text_id"]: s for s in script_db}

        catalog = build_script_catalog(script_db, compact=True)
        full_system = f"{system_prompt}\n\n{catalog}"

        # Pro models reject thinking_level="minimal"; use LOW. Non-pro (flash) keeps minimal.
        thinking = (
            types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW)
            if "pro" in self.model
            else types.ThinkingConfig(thinking_level="minimal")
        )
        config_kwargs = dict(
            thinking_config=thinking,
            system_instruction=full_system,
            tools=[types.Tool(function_declarations=_gemini_tool_declarations())],
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode="ANY")
            ),
            http_options=types.HttpOptions(timeout=180_000),  # 3-min hard cap; kills hung requests
        )
        # Pinned sampling (None → Gemini default ~1.0). Set from run.py for reproducible benchmarks.
        if temperature is not None:
            config_kwargs["temperature"] = temperature
        if seed is not None:
            config_kwargs["seed"] = seed
        self.config = types.GenerateContentConfig(**config_kwargs)
        self.history: list[types.Content] = []
        self.verbose = False  # set externally to enable per-hop stderr progress logs
        # Optional sink invoked as each hop is appended during reply(). Set
        # externally by demo/server/sessions.py to stream hops to the frontend
        # in real time. Default None preserves the benchmark contract.
        self.on_hop = None  # type: ignore[assignment]

    def _emit(self, hop: dict) -> None:
        cb = self.on_hop
        if cb is not None:
            try:
                cb(hop)
            except Exception:
                pass

    def _make_hops_list(self) -> list:
        """When on_hop is set, return a list whose append() also calls on_hop."""
        cb = self.on_hop
        if cb is None:
            return []

        class _EmittingList(list):
            def append(self, item, _cb=cb):
                list.append(self, item)
                try:
                    _cb(item)
                except Exception:
                    pass

        return _EmittingList()

    def reply(self, message: str, backend) -> dict:
        self.history.append(
            types.Content(role="user", parts=[types.Part(text=message)])
        )

        hops: list[dict] = self._make_hops_list()
        any_non_reply = False
        total_ms = 0.0
        total_cost = 0.0

        for hop_idx in range(MAX_TOOL_HOPS + 1):
            t_start = time.perf_counter()
            print("send customer message to gemini api", file=sys.stderr, flush=True)
            response = retry_transient(lambda: self.client.models.generate_content(
                model=self.model,
                contents=self.history,
                config=self.config,
            ))
            print("response received", file=sys.stderr, flush=True)
            hop_ms = (time.perf_counter() - t_start) * 1000
            total_ms += hop_ms

            usage = getattr(response, "usage_metadata", None)
            if usage:
                total_cost += calculate_cost(self.model, {
                    "prompt_token_count": getattr(usage, "prompt_token_count", 0),
                    "cached_content_token_count": getattr(usage, "cached_content_token_count", 0),
                    "candidates_token_count": getattr(usage, "candidates_token_count", 0),
                    "thoughts_token_count": getattr(usage, "thoughts_token_count", 0),
                })

            name, args, fc_part = self._extract_first_function_call(response)
            model_content = response.candidates[0].content if (
                getattr(response, "candidates", None) and len(response.candidates) > 0
            ) else None

            if name == "reply":
                text_ids = _coerce_text_ids(args.get("text_ids", []))
                # Phase F chain-compat gate (advisory; agent retries on reject).
                chain_err = _validate_chain(
                    text_ids,
                    self.script_lookup,
                    case_status=backend.customer_data.get("case_status") or "normal",
                )
                if chain_err is not None:
                    result = {"sent": False, "reason": chain_err}
                    _hop_log(self.verbose, hop_idx + 1, "tool_call",
                             name="reply", args={"text_ids": text_ids}, elapsed_ms=hop_ms)
                    _hop_log(self.verbose, hop_idx + 1, "tool_result", result=result)
                    hops.append({"kind": "tool_call", "name": "reply", "args": {"text_ids": text_ids}})
                    hops.append({"kind": "tool_result", "name": "reply", "result": result})
                    # Preserve the model's function_call Content (thought_signature) on history.
                    if model_content is not None:
                        self.history.append(model_content)
                    else:
                        self.history.append(types.Content(
                            role="model",
                            parts=[types.Part(function_call=types.FunctionCall(name="reply", args=dict(args)))],
                        ))
                    self.history.append(types.Content(
                        role="user",
                        parts=[types.Part(function_response=types.FunctionResponse(name="reply", response=result))],
                    ))
                    continue
                dynamic_vars = _parse_dynamic_vars(args.get("dynamic_vars"))
                # Pre-render guard (see CommunicatorQwenPreScript): require concrete
                # date/time dynamic_vars when the chosen templates reference them.
                missing_dyn = missing_required_dynamic_vars(self.script_lookup, text_ids, dynamic_vars)
                if missing_dyn:
                    result = {
                        "sent": False, "reason": "dynamic_var_required", "missing": missing_dyn,
                        "hint": "These templates need concrete values — supply them via dynamic_vars "
                                "(e.g. callback_date='2026-05-23 (Saturday)', callback_time='14:00'). "
                                "Call get_current_datetime() first for dates.",
                    }
                    _hop_log(self.verbose, hop_idx + 1, "tool_call",
                             name="reply", args={"text_ids": text_ids, "dynamic_vars": dynamic_vars},
                             elapsed_ms=hop_ms)
                    _hop_log(self.verbose, hop_idx + 1, "tool_result", result=result)
                    hops.append({"kind": "tool_call", "name": "reply", "args": {"text_ids": text_ids, "dynamic_vars": dynamic_vars}})
                    hops.append({"kind": "tool_result", "name": "reply", "result": result})
                    if model_content is not None:
                        self.history.append(model_content)
                    else:
                        self.history.append(types.Content(
                            role="model",
                            parts=[types.Part(function_call=types.FunctionCall(name="reply", args=dict(args)))],
                        ))
                    self.history.append(types.Content(
                        role="user",
                        parts=[types.Part(function_response=types.FunctionResponse(name="reply", response=result))],
                    ))
                    continue
                # Legal-hours guard (พ.ร.บ.การทวงถามหนี้ §9(2)): see CommunicatorQwenPreScript.
                cb_time = dynamic_vars.get("callback_time")
                if cb_time and is_valid_time(cb_time) and not is_within_legal_hours(cb_time):
                    result = {
                        "sent": False, "reason": "callback_time_out_of_legal_hours", "got": cb_time,
                        "hint": "Callbacks must be within legal contact hours 08:00–20:00 "
                                "(พ.ร.บ.การทวงถามหนี้ §9(2)). Propose a time within that window.",
                    }
                    _hop_log(self.verbose, hop_idx + 1, "tool_call",
                             name="reply", args={"text_ids": text_ids, "dynamic_vars": dynamic_vars},
                             elapsed_ms=hop_ms)
                    _hop_log(self.verbose, hop_idx + 1, "tool_result", result=result)
                    hops.append({"kind": "tool_call", "name": "reply", "args": {"text_ids": text_ids, "dynamic_vars": dynamic_vars}})
                    hops.append({"kind": "tool_result", "name": "reply", "result": result})
                    if model_content is not None:
                        self.history.append(model_content)
                    else:
                        self.history.append(types.Content(
                            role="model",
                            parts=[types.Part(function_call=types.FunctionCall(name="reply", args=dict(args)))],
                        ))
                    self.history.append(types.Content(
                        role="user",
                        parts=[types.Part(function_response=types.FunctionResponse(name="reply", response=result))],
                    ))
                    continue
                try:
                    text, final_ids, intent_names = _render_reply(
                        self.script_db, self.script_lookup, self.agent_context_data,
                        text_ids, dynamic_vars,
                    )
                except DateFormatError as e:
                    result = {
                        "sent": False, "reason": "date_format_invalid",
                        "placeholder": e.placeholder, "got": e.got, "expected": e.expected,
                        "hint": "Call get_current_datetime() and paste the standard string verbatim.",
                    }
                    _hop_log(self.verbose, hop_idx + 1, "tool_call",
                             name="reply", args={"text_ids": text_ids, "dynamic_vars": dynamic_vars},
                             elapsed_ms=hop_ms)
                    _hop_log(self.verbose, hop_idx + 1, "tool_result", result=result)
                    hops.append({"kind": "tool_call", "name": "reply", "args": {"text_ids": text_ids, "dynamic_vars": dynamic_vars}})
                    hops.append({"kind": "tool_result", "name": "reply", "result": result})
                    if model_content is not None:
                        self.history.append(model_content)
                    else:
                        self.history.append(types.Content(
                            role="model",
                            parts=[types.Part(function_call=types.FunctionCall(name="reply", args=dict(args)))],
                        ))
                    self.history.append(types.Content(
                        role="user",
                        parts=[types.Part(function_response=types.FunctionResponse(name="reply", response=result))],
                    ))
                    continue
                _hop_log(self.verbose, hop_idx + 1, "tool_call",
                         name="reply", args={"text_ids": final_ids, "dynamic_vars": dynamic_vars},
                         elapsed_ms=hop_ms)
                leaks = leaked_placeholders(text)
                if leaks:
                    print(f"    placeholder_leak in rendered reply: {leaks} (text_ids={final_ids})",
                          file=sys.stderr, flush=True)
                _hop_log(self.verbose, hop_idx + 1, "rendered_text", text=text)
                # Append the model's actual function_call Part (carries thought_signature)
                # plus our rendered Thai as a text Part on the same model turn.
                if fc_part is not None:
                    model_parts = [fc_part, types.Part(text=text)]
                else:
                    model_parts = [
                        types.Part(function_call=types.FunctionCall(name="reply", args=dict(args))),
                        types.Part(text=text),
                    ]
                self.history.append(types.Content(role="model", parts=model_parts))
                hops.append({"kind": "tool_call", "name": "reply", "args": {"text_ids": final_ids, "dynamic_vars": dynamic_vars}})
                hops.append({"kind": "rendered_text", "text": text, **({"placeholder_leak": leaks} if leaks else {})})
                agent_messages = [FILLER_TEXT, text] if any_non_reply else [text]
                return {
                    "text": text,
                    "agent_messages": agent_messages,
                    "hops": hops,
                    "text_ids": final_ids,
                    "intent_names": intent_names,
                    "dynamic_vars": dynamic_vars,
                    "ttft_ms": total_ms,
                    "total_ms": total_ms,
                    "cost": total_cost,
                }

            # Non-reply: dispatch via backend.
            any_non_reply = True
            result = backend.dispatch(name, args)
            _hop_log(self.verbose, hop_idx + 1, "tool_call", name=name, args=args, elapsed_ms=hop_ms)
            _hop_log(self.verbose, hop_idx + 1, "tool_result", result=result)
            hops.append({"kind": "tool_call", "name": name, "args": args})
            hops.append({"kind": "tool_result", "name": name, "result": result})
            # Append the model's actual response Content (preserves thought_signature on the function_call Part).
            if model_content is not None:
                self.history.append(model_content)
            else:
                self.history.append(types.Content(
                    role="model",
                    parts=[types.Part(function_call=types.FunctionCall(name=name, args=dict(args)))],
                ))
            self.history.append(types.Content(
                role="user",
                parts=[types.Part(function_response=types.FunctionResponse(name=name, response=result))],
            ))

        if self.verbose:
            print(f"    MAX_TOOL_HOPS exhausted — falling back", file=sys.stderr, flush=True)
        return self._fallback_return(hops, any_non_reply, total_ms, total_cost)

    def _extract_first_function_call(self, response) -> tuple[str, dict, object | None]:
        """Return (name, args, the function_call Part) from the response.

        The Part is returned intact so the caller can echo it back into history
        — Gemini 3.x requires thought_signature, which lives on the Part.
        """
        try:
            for part in response.candidates[0].content.parts:
                if getattr(part, "function_call", None) is not None and part.function_call.name:
                    fc = part.function_call
                    args = dict(fc.args) if fc.args else {}
                    return fc.name, args, part
        except (IndexError, AttributeError, TypeError):
            pass
        return "reply", {"text_ids": [self.script_db[0]["text_id"]]}, None

    def _fallback_return(self, hops, any_non_reply, total_ms, total_cost) -> dict:
        fallback_tid = self.script_db[0]["text_id"]
        text = FALLBACK_TEXT
        _, final_ids, intent_names = _render_reply(
            self.script_db, self.script_lookup, self.agent_context_data,
            [fallback_tid], {},
        )
        self.history.append(types.Content(
            role="model", parts=[types.Part(text=text)],
        ))
        hops.append({"kind": "tool_call", "name": "reply", "args": {"text_ids": final_ids, "dynamic_vars": {}, "fallback": True}})
        hops.append({"kind": "rendered_text", "text": text})
        agent_messages = [FILLER_TEXT, text] if any_non_reply else [text]
        return {
            "text": text,
            "agent_messages": agent_messages,
            "hops": hops,
            "text_ids": final_ids,
            "intent_names": intent_names,
            "dynamic_vars": {},
            "ttft_ms": total_ms,
            "total_ms": total_ms,
            "cost": total_cost,
        }

    def reset(self) -> None:
        self.history.clear()


# ---------------------------------------------------------------------------
# Gemini Freeform (now tool-enabled — free Thai text reply + backend actions)
# ---------------------------------------------------------------------------

class CommunicatorGeminiFreeform:
    """Free-form communicator using Gemini function calling.

    Unlike the pre-script class there is NO `reply` tool: the model writes its own
    Thai directly (a plain-text response IS the customer-facing reply). It DOES
    get the 6 deterministic CaseBackend action tools (verify_identity, payment_date,
    etc.) via `FunctionCallingConfig(mode="AUTO")`, so KYC / payment / callback are
    enforced through the backend exactly as in pre-script mode. `reply()` runs a
    multi-hop loop until the model emits a text turn (no function call).
    """

    MODEL = "gemini-3.1-pro-preview"

    def __init__(self, system_prompt: str, *, model: str | None = None,
                 temperature: float | None = None, seed: int | None = None) -> None:
        self.model = model or self.MODEL
        self.client = get_client()
        thinking = (
            types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW)
            if "pro" in self.model
            else types.ThinkingConfig(thinking_level="minimal")
        )
        config_kwargs = dict(
            thinking_config=thinking,
            system_instruction=system_prompt,
            tools=[types.Tool(function_declarations=_gemini_freeform_tool_declarations())],
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode="AUTO")
            ),
            http_options=types.HttpOptions(timeout=180_000),
        )
        # Pinned sampling (None → Gemini default ~1.0). Set from run.py for reproducible benchmarks.
        if temperature is not None:
            config_kwargs["temperature"] = temperature
        if seed is not None:
            config_kwargs["seed"] = seed
        self.config = types.GenerateContentConfig(**config_kwargs)
        self.history: list[types.Content] = []
        self.verbose = False

    @staticmethod
    def _extract(response) -> tuple[str | None, dict, object | None, str]:
        """Return (fn_name, args, fn_part, text) from a response candidate."""
        name, args, fc_part, text_parts = None, {}, None, []
        try:
            parts = response.candidates[0].content.parts
        except (IndexError, AttributeError, TypeError):
            parts = []
        for part in parts or []:
            fc = getattr(part, "function_call", None)
            if fc is not None and fc.name and name is None:
                name = fc.name
                args = dict(fc.args) if fc.args else {}
                fc_part = part
            elif getattr(part, "text", None):
                text_parts.append(part.text)
        return name, args, fc_part, "".join(text_parts)

    def reply(self, message: str, backend=None) -> dict:
        self.history.append(
            types.Content(role="user", parts=[types.Part(text=message)])
        )

        hops: list[dict] = []
        any_non_reply = False
        total_ms = 0.0
        total_cost = 0.0

        for hop_idx in range(MAX_TOOL_HOPS + 1):
            t_start = time.perf_counter()
            response = retry_transient(lambda: self.client.models.generate_content(
                model=self.model,
                contents=self.history,
                config=self.config,
            ))
            hop_ms = (time.perf_counter() - t_start) * 1000
            total_ms += hop_ms

            usage = getattr(response, "usage_metadata", None)
            if usage:
                total_cost += calculate_cost(self.model, {
                    "prompt_token_count": getattr(usage, "prompt_token_count", 0),
                    "cached_content_token_count": getattr(usage, "cached_content_token_count", 0),
                    "candidates_token_count": getattr(usage, "candidates_token_count", 0),
                    "thoughts_token_count": getattr(usage, "thoughts_token_count", 0),
                })

            name, args, fc_part, text = self._extract(response)
            model_content = response.candidates[0].content if (
                getattr(response, "candidates", None) and len(response.candidates) > 0
            ) else None

            if name and backend is not None:
                any_non_reply = True
                result = backend.dispatch(name, args)
                _hop_log(self.verbose, hop_idx + 1, "tool_call", name=name, args=args, elapsed_ms=hop_ms)
                _hop_log(self.verbose, hop_idx + 1, "tool_result", result=result)
                hops.append({"kind": "tool_call", "name": name, "args": args})
                hops.append({"kind": "tool_result", "name": name, "result": result})
                if model_content is not None:
                    self.history.append(model_content)
                else:
                    self.history.append(types.Content(
                        role="model",
                        parts=[types.Part(function_call=types.FunctionCall(name=name, args=dict(args)))],
                    ))
                self.history.append(types.Content(
                    role="user",
                    parts=[types.Part(function_response=types.FunctionResponse(name=name, response=result))],
                ))
                continue

            # Text turn → customer-facing reply.
            if not text:
                text = FALLBACK_TEXT
            if model_content is not None:
                self.history.append(model_content)
            else:
                self.history.append(types.Content(role="model", parts=[types.Part(text=text)]))
            _hop_log(self.verbose, hop_idx + 1, "rendered_text", text=text)
            hops.append({"kind": "rendered_text", "text": text})
            agent_messages = [FILLER_TEXT, text] if any_non_reply else [text]
            return {
                "text": text,
                "agent_messages": agent_messages,
                "hops": hops,
                "ttft_ms": total_ms,
                "total_ms": total_ms,
                "cost": total_cost,
            }

        # MAX_TOOL_HOPS exhausted — emit a safe fallback reply.
        self.history.append(types.Content(role="model", parts=[types.Part(text=FALLBACK_TEXT)]))
        hops.append({"kind": "rendered_text", "text": FALLBACK_TEXT})
        agent_messages = [FILLER_TEXT, FALLBACK_TEXT] if any_non_reply else [FALLBACK_TEXT]
        return {
            "text": FALLBACK_TEXT,
            "agent_messages": agent_messages,
            "hops": hops,
            "ttft_ms": total_ms,
            "total_ms": total_ms,
            "cost": total_cost,
        }

    def reset(self) -> None:
        self.history.clear()
