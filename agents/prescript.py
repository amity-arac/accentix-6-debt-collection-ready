"""Shared pre-script utilities: template filling and script catalog building."""

import logging
import re

from simulator import datetime_utils

logger = logging.getLogger(__name__)


class DateFormatError(ValueError):
    """Raised by fill_template (strict_dates=True) when a date/time
    dynamic_var doesn't match the canonical ISO format. Caught by the reply
    tool handler in agents/communicator.py and surfaced as a structured
    {sent: False, reason: "date_format_invalid", ...} so the agent retries.
    """

    def __init__(self, placeholder: str, got: str, expected: str):
        self.placeholder = placeholder
        self.got = got
        self.expected = expected
        super().__init__(f"[{placeholder}] expected {expected}, got {got!r}")

# Backend-filled placeholders. The LLM never sets these; values come from
# agent_context_data (built once per case in simulator/run.py).
SYSTEM_PLACEHOLDERS = {
    "customer_name": "customer_name",
    "amount": "total_amount_due",
    "minimum_payment": "minimum_payment_due",
    "due_date": "due_date",
    "due_status": "due_status",
    "loan_type": "loan_type",
    "customer_phone": "customer_phone",
    "company_phone": "company_phone",
    "company_name": "company_name",  # Phase G — injected into customer_data from case_id prefix
    "agent_name": "agent_name",      # Phase G — stylized first name per company (น้องอ้อน / น้องใจ / น้องแคร์)
    "today": "today",                # Phase H — real Asia/Bangkok date rendered as "YYYY-MM-DD (Weekday)"; injected at case init
    "vehicle_registration": "vehicle_registration",
    "location": "location",
    "vehicle_brand": "vehicle_brand",
    "late_fee": "late_fee",
    "collection_fee": "collection_fee",
    "field_collection_fee": "field_collection_fee",
    "insurance_fee": "insurance_fee",
    "month": "month",
    "bank_name": "bank_name",
    "msisdn": "msisdn",              # AIS — telecom MSISDN, rendered as-is (no special formatting)
}

# LLM-filled placeholders via reply(dynamic_vars=[...]). Each maps to a Thai
# fallback substituted when the LLM omits the variable — preserves the
# pre-existing abstract-phrasing safety property for negotiation templates.
#
# v6 expansion (Phase G): one Probe_Payment_Target template absorbs what was
# 3 v5 templates (ask_for_full/ask_for_minimum/accept_customer_offer) via
# [target_amount] + [target_date] + [payment_channel] slots. [micro_amount]
# is for good-faith probes; the *_reason / escalation_eta slots feed
# Ack_Hardship_Empathy, Ack_Dispute_Acknowledged, and
# Inform_Specialist_Callback.
DYNAMIC_PLACEHOLDERS = {
    # v5 (kept for backward-compat during transition)
    "promised_amount": "ตามที่แจ้ง",
    "promised_date": "วันที่นัดหมายไว้",
    "callback_date": "วันที่นัดหมาย",
    "callback_time": "เวลาที่สะดวก",
    # v6 additions
    "payment_channel": "ช่องทางที่ลูกค้าสะดวก",
    "micro_amount": "จำนวนเล็กน้อย",
    "dispute_reason": "ตามที่แจ้ง",
    "hardship_reason": "เหตุที่แจ้ง",
    "escalation_eta": "เร็วที่สุด",
}
# Note: `target_amount` / `target_date` were mined by Phase G but only used in
# probe templates that ran BEFORE the customer committed to anything — so the
# LLM had no value to fill, and the Thai fallback ("ตามจำนวนที่ตกลง" /
# "วันที่ตกลง") read awkwardly in customer-facing text. Phase H removed those
# placeholders from the 9 probe bodies (replaced with [due_date] / dropped
# entirely) and dropped them from this dict.

# Phase H — placeholders whose values MUST be in canonical ISO format under v6.
# Under strict mode the LLM-supplied value is validated and then rendered to
# natural Thai (e.g. "2026-05-23 (Saturday)" → "วันเสาร์ที่ 23 พฤษภาคม 2026").
DATE_PLACEHOLDERS = {"promised_date", "callback_date"}
TIME_PLACEHOLDERS = {"callback_time"}

# Phase H — SYSTEM date placeholders (sourced from CRM/customer_data, not the
# LLM). Rendered lenient: canonical ISO → Thai natural; anything else passes
# through unchanged. v4 test corpus normalized to canonical, but legacy fields
# in other call paths might still hold free-form strings.
SYSTEM_DATE_PLACEHOLDERS = {"due_date"}

# Phase H — channel enum → Thai natural language. The LLM often passes the
# raw enum literal ("bank_transfer") into dynamic_vars; render it for the
# customer. When the value doesn't match an enum key (LLM paraphrased), the
# value is passed through unchanged — lenient because the agent legitimately
# enumerates channels in inform templates.
PAYMENT_CHANNEL_THAI = {
    "mobile_app": "แอปพลิเคชันมือถือ",
    "counter_service": "เคาน์เตอร์เซอร์วิส",
    "branch": "สาขาธนาคาร",
    "bank_transfer": "การโอนเงินผ่านธนาคาร",
    "atm": "ตู้ ATM",
    "other": "ช่องทางอื่น",
}
CHANNEL_PLACEHOLDERS = {"payment_channel"}

# Regex for conditional blocks: {{if field}}content{{else}}alt{{/if}}
# Match innermost blocks only; the while-loop in fill_template() peels one nesting layer per iteration.
# Group 1: field name, Group 2: if-branch, Group 3 (optional): else-branch.
CONDITIONAL_RE = re.compile(
    r"\{\{if\s+(\w+)\}\}((?:(?!\{\{if\s+\w+\}\}).)*?)"
    r"(?:\{\{else\}\}((?:(?!\{\{if\s+\w+\}\}).)*?))?\{\{/if\}\}",
    re.DOTALL,
)


def _strip_conditionals(template: str) -> str:
    """Remove {{if}}/{{else}}/{{/if}} markers, keeping inner content, for LLM display."""
    result = re.sub(r"\{\{if\s+\w+\}\}", "", template)
    result = re.sub(r"\{\{else\}\}", "", result)
    result = re.sub(r"\{\{/if\}\}", "", result)
    return re.sub(r" {2,}", " ", result).strip()


def _extract_dynamic_vars_from_template(template: str) -> list[str]:
    """Return the deduplicated list of DYNAMIC_PLACEHOLDERS appearing in template (in order of first occurrence)."""
    seen: list[str] = []
    for match in re.finditer(r"\[([^\]]+)\]", template):
        name = match.group(1)
        if name in DYNAMIC_PLACEHOLDERS and name not in seen:
            seen.append(name)
    return seen


STATE_ORDER = ("opening", "kyc", "negotiation", "dispute", "hardship", "closing")
STATE_HEADERS = {
    "opening":     "Opening",
    "kyc":         "Identity Verification (KYC)",
    "negotiation": "Negotiation (post-KYC, Track A)",
    "dispute":     "Dispute / Pending Review (Track B) — NO payment-amount probes",
    "hardship":    "Crisis / Hardship (Track B) — pure empathy, NO payment probes",
    "closing":     "Closing",
}

V6_CHAIN_RULE = (
    "**Chain rule**: each turn = exactly one Category A (acknowledge/inform) + one "
    "Category B (probe/action). Single-A or single-B turns are allowed (e.g., a "
    "stand-alone probe after a non-answer). Same-state chains preferred; the runtime "
    "validator rejects incompatible cross-state pairings and blocks payment-amount "
    "templates on pending_review accounts. `Close_Call_Success` REQUIRES a prior "
    "`record_verbal_commitment(amount, date, channel)` call with values matching the "
    "args you intend to pass to `payment_date`."
)


def _build_v5_catalog(script_db: list[dict], compact: bool) -> list[str]:
    """Phase F (v5) catalog: flat list, alphabetical by text_id."""
    lines: list[str] = []
    for entry in script_db:
        tid = entry["text_id"]
        intent = entry.get("intent_name", "")
        dynamic_vars = _extract_dynamic_vars_from_template(entry.get("template", ""))
        vars_suffix = f" | Vars: [{', '.join(dynamic_vars)}]"
        if compact:
            lines.append(f"- **{tid}**: {intent}{vars_suffix}")
        else:
            template = _strip_conditionals(entry["template"])
            lines.append(f"- **{tid}** ({intent}){vars_suffix}: {template}")
    return lines


def _build_v6_catalog(script_db: list[dict], compact: bool) -> list[str]:
    """Phase G (v6) catalog: grouped by state with [A]/[B] category prefix."""
    by_state: dict[str, list[dict]] = {}
    for entry in script_db:
        state = entry.get("state") or "_unstated"
        by_state.setdefault(state, []).append(entry)

    lines: list[str] = [V6_CHAIN_RULE, ""]
    for state in STATE_ORDER + ("_unstated",):
        if state not in by_state:
            continue
        header = STATE_HEADERS.get(state, "Unstated")
        lines.append(f"### {header}")
        for entry in sorted(by_state[state], key=lambda e: e["text_id"]):
            tid = entry["text_id"]
            name = entry.get("intent_name", "")
            cat = entry.get("category") or "?"
            body = entry.get("template", "")
            dynamic_vars = _extract_dynamic_vars_from_template(body)
            vars_suffix = f" | Vars: [{', '.join(dynamic_vars)}]" if dynamic_vars else ""
            # Annotate closing templates that commit the 3-element payment.
            # Tied to intent_name (not body slots), so Probe_Payment_Target —
            # which gathers the commitment — is NOT annotated.
            requires = ""
            if name.startswith("Close_Call_Success"):
                requires = " — REQUIRES record_verbal_commitment first"
            if compact:
                lines.append(f"- [{cat}] **{tid}** {name}{vars_suffix}{requires}")
            else:
                template = _strip_conditionals(body)
                lines.append(f"- [{cat}] **{tid}** ({name}){vars_suffix}{requires}: {template}")
        lines.append("")
    return lines


def build_script_catalog(script_db: list[dict], compact: bool = False) -> str:
    """Build a markdown section listing available pre-scripts for the system prompt.

    Compact mode appends a ` | Vars: [...]` suffix per entry listing the
    DYNAMIC placeholders the template uses. SYSTEM placeholders are hidden
    from the LLM (backend handles them).

    Under v6 (entries with `category` / `state` fields), templates are grouped
    by state with a [A]/[B] category prefix — gives the LLM structural hints
    about the 1-Ack + 1-Probe pairing rule. Falls back to v5 alphabetical
    layout when those fields are absent.
    """
    header = [
        "## Available Pre-Scripts",
        "You MUST respond by calling the `reply` tool with text_ids of the most appropriate script(s).",
        "Choose based on the conversation context, customer emotion, and negotiation strategy.",
        "If a script lists `Vars: [...]`, supply those values via the tool's `dynamic_vars` argument; backend fills system placeholders automatically.",
        "",
    ]
    is_v6 = any(s.get("category") in ("A", "B") for s in script_db)
    body = _build_v6_catalog(script_db, compact) if is_v6 else _build_v5_catalog(script_db, compact)
    return "\n".join(header + body)


def fill_template(
    template: str,
    agent_context_data: dict,
    dynamic_vars: dict | None = None,
    strict_dates: bool = False,
) -> str:
    """Replace [placeholder] tokens and resolve {{if field}}...{{/if}} conditional blocks.

    SYSTEM placeholders resolve from agent_context_data (immutable per case).
    DYNAMIC placeholders resolve from dynamic_vars (LLM-supplied), falling
    back to a Thai phrase from DYNAMIC_PLACEHOLDERS when absent — graceful
    degradation that preserves the abstract-phrasing safety baseline.

    When `strict_dates=True` (v6), DATE_PLACEHOLDERS / TIME_PLACEHOLDERS values
    are validated against the canonical ISO format and rendered to natural
    Thai. Malformed values raise DateFormatError, which the reply tool handler
    converts to `{sent: False, reason: "date_format_invalid", ...}`.
    """
    dynamic_vars = dynamic_vars or {}

    # Pass 1: resolve conditional blocks. SYSTEM check first, DYNAMIC second.
    def resolve_conditional(match: re.Match) -> str:
        field_ref = match.group(1)
        if field_ref in SYSTEM_PLACEHOLDERS:
            field_name = SYSTEM_PLACEHOLDERS[field_ref]
            present = agent_context_data.get(field_name) is not None
        elif field_ref in DYNAMIC_PLACEHOLDERS:
            present = field_ref in dynamic_vars and dynamic_vars[field_ref] is not None
        else:
            present = agent_context_data.get(field_ref) is not None
        if present:
            return match.group(2)
        return match.group(3) or ""

    result = template
    while CONDITIONAL_RE.search(result):
        result = CONDITIONAL_RE.sub(resolve_conditional, result)

    # Pass 2: substitute [placeholder] tokens.
    def replacer(match: re.Match) -> str:
        placeholder = match.group(1)
        if placeholder in SYSTEM_PLACEHOLDERS:
            value = agent_context_data.get(SYSTEM_PLACEHOLDERS[placeholder])
            if value is None:
                return match.group(0)
            if isinstance(value, float):
                if value == int(value):
                    return f"{int(value):,}"
                return f"{value:,.2f}"
            value_str = str(value)
            if placeholder in SYSTEM_DATE_PLACEHOLDERS and datetime_utils.is_valid_date(value_str):
                return datetime_utils.render_date_thai(value_str)
            return value_str
        if placeholder in DYNAMIC_PLACEHOLDERS:
            value = dynamic_vars.get(placeholder)
            if value is None or value == "":
                return DYNAMIC_PLACEHOLDERS[placeholder]
            value_str = str(value)
            if strict_dates and placeholder in DATE_PLACEHOLDERS:
                if not datetime_utils.is_valid_date(value_str):
                    raise DateFormatError(
                        placeholder, value_str,
                        "YYYY-MM-DD (Weekday), e.g. 2026-05-23 (Saturday)",
                    )
                return datetime_utils.render_date_thai(value_str)
            if strict_dates and placeholder in TIME_PLACEHOLDERS:
                if not datetime_utils.is_valid_time(value_str):
                    raise DateFormatError(
                        placeholder, value_str,
                        "HH:MM 24-hour, e.g. 14:00",
                    )
                return datetime_utils.render_time_thai(value_str)
            if placeholder in CHANNEL_PLACEHOLDERS:
                # Render enum literal to Thai; pass through paraphrased values.
                return PAYMENT_CHANNEL_THAI.get(value_str.strip(), value_str)
            return value_str
        # Only warn if it LOOKS like a placeholder (identifier-shaped, length≥2).
        # Single-char [A] / [B] (used in v6 instructions to reference the catalog's
        # [A]/[B] category prefix) and non-identifiers like [...] or
        # [{"name": "...", "value": "..."}] are documentation/example text — silent.
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]+", placeholder):
            logger.warning("fill_template: unknown placeholder [%s] left as literal", placeholder)
        return match.group(0)

    result = re.sub(r"\[([^\]]+)\]", replacer, result)

    # Pass 3: normalize whitespace from removed blocks.
    return re.sub(r" {2,}", " ", result).strip()


# Concrete dynamic placeholders whose Thai fallback is NOT acceptable in
# customer-facing text: a callback/payment confirmation needs a real date/time,
# not the abstract "วันที่นัดหมาย / เวลาที่สะดวก" fallback. If a chosen template
# references one of these, the LLM MUST supply it via dynamic_vars (else the
# reply path rejects + retries). Descriptive vars (dispute_reason, hardship_reason,
# escalation_eta, promised_amount, micro_amount, payment_channel) keep their
# graceful fallback and are intentionally NOT required here.
REQUIRED_DYNAMIC_PLACEHOLDERS = DATE_PLACEHOLDERS | TIME_PLACEHOLDERS


def missing_required_dynamic_vars(
    script_lookup: dict, text_ids: list[int], dynamic_vars: dict
) -> list[str]:
    """Concrete date/time dynamic placeholders referenced by the chosen templates
    but not supplied (non-empty) in dynamic_vars. Agent-fixable → reject+retry."""
    needed: set[str] = set()
    for tid in text_ids:
        s = script_lookup.get(tid)
        if not s:
            continue
        for ph in re.findall(r"\[([^\]]+)\]", s.get("template", "")):
            if ph in REQUIRED_DYNAMIC_PLACEHOLDERS:
                needed.add(ph)
    return sorted(
        ph for ph in needed if not str((dynamic_vars or {}).get(ph) or "").strip()
    )


def leaked_placeholders(rendered_text: str) -> list[str]:
    """Identifier-shaped [name] literals remaining after rendering — unfilled
    SYSTEM placeholders (missing CRM field) or stale-template references. NOT
    agent-fixable, so callers should log/tag (not retry-loop) on these."""
    return re.findall(r"\[([A-Za-z_][A-Za-z0-9_]+)\]", rendered_text or "")
