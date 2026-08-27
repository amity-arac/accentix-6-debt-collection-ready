"""Shared pre-script utilities: template filling and script catalog building."""

import hashlib
import logging
import re

from demo_v2.lib import datetime_utils

logger = logging.getLogger(__name__)

# Render-time gender substitution (v10_pre_script_database_parameterized.json).
# Templates carry {suffix}/{q_suffix}/{pronoun} placeholders instead of a
# baked-in ครับ/ค่ะ/ผม/ดิฉัน — the SAME template produces both genders, so the
# model never chooses a gendered text_id and gender-mixing is structurally
# impossible. No-op on the older fully-duplicated catalog.
GENDER_SUFFIXES = {
    "M": {"suffix": "ครับ", "q_suffix": "ครับ", "pronoun": "ผม"},
    "F": {"suffix": "ค่ะ", "q_suffix": "คะ", "pronoun": "ดิฉัน"},
}


def locked_gender_for_case(case_id: str) -> str:
    """Deterministic per-case gender lock: same case_id always renders the same
    gender. Purely a consistent-voice choice for the synthetic agent."""
    return "M" if int(hashlib.md5(case_id.encode()).hexdigest(), 16) % 2 == 0 else "F"


def render_gender(template: str, gender: str) -> str:
    """Substitute {suffix}/{q_suffix}/{pronoun} placeholders. No-op if the
    template has none (the older fully-duplicated catalog)."""
    if not any(p in template for p in ("{suffix}", "{q_suffix}", "{pronoun}")):
        return template
    values = GENDER_SUFFIXES.get(gender, GENDER_SUFFIXES["M"])
    # Targeted replace (NOT str.format) — data placeholders are now also {curly}
    # ({customer_name}, {amount}, …) and .format() would choke on them.
    for key, val in values.items():
        template = template.replace("{" + key + "}", val)
    return template


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

    # Every state the catalog actually uses gets listed. The known debt-flow
    # states come first, in their canonical reading order; any other state the
    # catalog declares follows, in first-appearance order. Iterating STATE_ORDER
    # alone silently DROPPED every template whose state was not on that fixed
    # list — a company whose spec names its states anything else (AMT's `main`
    # and `faq`, any Builder-created company) had those lines removed from the
    # prompt, so the model was told to choose from a catalog that did not
    # contain them. It then replied with no text_ids at all.
    extra = [st for st in by_state if st not in STATE_ORDER and st != "_unstated"]
    lines: list[str] = [V6_CHAIN_RULE, ""]
    for state in tuple(STATE_ORDER) + tuple(extra) + ("_unstated",):
        if state not in by_state:
            continue
        header = STATE_HEADERS.get(state, state.replace("_", " ").title())
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
            # `hint` says WHEN to use this wording. Without it, several wordings of
            # one beat reach the model as interchangeable lines and it takes the
            # first every time (measured: 9 picks out of 9 were the group's first).
            hint = entry.get("hint")
            hint_s = f" ‹{hint}›" if hint else ""
            if compact:
                lines.append(f"- [{cat}] **{tid}** {name}{vars_suffix}{requires}{hint_s}")
            else:
                template = _strip_conditionals(body)
                lines.append(
                    f"- [{cat}] **{tid}** ({name}){vars_suffix}{requires}{hint_s}: {template}")
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
    gender: str | None = None,
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

    `gender` resolves {suffix}/{q_suffix}/{pronoun} placeholders (parameterized
    catalog only). Defaults to "M" if templates need it but none supplied.
    """
    dynamic_vars = dynamic_vars or {}
    template = render_gender(template, gender or "M")

    # Pass 1: resolve conditional blocks. SYSTEM check first, DYNAMIC second.
    def resolve_conditional(match: re.Match) -> str:
        field_ref = match.group(1)
        if field_ref in SYSTEM_PLACEHOLDERS:
            field_name = SYSTEM_PLACEHOLDERS[field_ref]
            present = agent_context_data.get(field_name) is not None
        elif field_ref in DYNAMIC_PLACEHOLDERS:
            present = field_ref in dynamic_vars and dynamic_vars[field_ref] is not None
        else:
            value = agent_context_data.get(field_ref)
            # A boolean FLAG must be tested for truth, not for presence. `{{if
            # due_upcoming}}` with the flag explicitly False took the if-branch,
            # because `False is not None` — so an account months overdue was
            # announced with the pre-due wording. Non-booleans keep presence
            # semantics: a field can legitimately hold 0 or "".
            present = value if isinstance(value, bool) else value is not None
        if present:
            return match.group(2)
        return match.group(3) or ""

    result = template
    while CONDITIONAL_RE.search(result):
        result = CONDITIONAL_RE.sub(resolve_conditional, result)

    # Pass 2: substitute [placeholder] tokens.
    def _render_date_tolerant(value_str: str) -> str | None:
        """Natural-Thai a date string, tolerating a WRONG weekday in the source
        (bad CRM data must never leak a raw "YYYY-MM-DD (Weekday)" to the ear).
        Returns None when the value isn't a date at all."""
        if datetime_utils.is_valid_date(value_str):
            return datetime_utils.render_date_thai(value_str)
        import datetime as _dt
        m = re.match(r"(\d{4}-\d{2}-\d{2})", value_str)
        if m:
            try:
                d = _dt.date.fromisoformat(m.group(1))
                return datetime_utils.render_date_thai(
                    f"{m.group(1)} ({d.strftime('%A')})")
            except ValueError:
                pass
        return None

    def replacer(match: re.Match) -> str:
        placeholder = match.group(1)
        if placeholder in SYSTEM_PLACEHOLDERS:
            value = agent_context_data.get(SYSTEM_PLACEHOLDERS[placeholder])
            # No early return on a miss: the mapped field may be absent while the
            # data carries the placeholder's own name (a spec naming `amount`
            # rather than `total_amount_due`). Fall through to the generic lookup
            # below, which leaks only if neither name resolves — strictly more
            # capable than the old `return match.group(0)` here.
            if value is not None:
                if isinstance(value, float):
                    if value == int(value):
                        return f"{int(value):,}"
                    return f"{value:,.2f}"
                value_str = str(value)
                if placeholder in SYSTEM_DATE_PLACEHOLDERS:
                    rendered = _render_date_tolerant(value_str)
                    if rendered is not None:
                        return rendered
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
        # Spec-declared field, no registry entry. The two registries above are
        # debt-domain (amount, late_fee, vehicle_registration, …), so a FlowSpec
        # that names its own CRM fields had every token leak to the customer —
        # observed: AMT's greeting said literally "มีนัดพบ [doctor_name] วันที่
        # [appointment_date]". Pass 1's conditional resolver already falls back to
        # agent_context_data by the bare name; Pass 2 must too, or no uploaded spec
        # can render. Placeholder name == data key, which is what makes this
        # spec-driven: a brand-new company works with no code change.
        # dynamic_vars is the second chance, for a field the model supplies rather
        # than the case carrying it.
        for source in (agent_context_data, dynamic_vars):
            if placeholder not in source:
                continue
            value = source[placeholder]
            if value is None:
                break
            if isinstance(value, float):
                return f"{int(value):,}" if value == int(value) else f"{value:,.2f}"
            value_str = str(value)
            # Same courtesy the SYSTEM branch gives: never speak a raw
            # "YYYY-MM-DD (Weekday)" / "HH:MM" at the customer.
            rendered = _render_date_tolerant(value_str)
            if rendered is not None:
                return rendered
            if datetime_utils.is_valid_time(value_str):
                return datetime_utils.render_time_thai(value_str)
            return value_str
        # Only warn if it LOOKS like a placeholder (identifier-shaped, length≥2).
        # Single-char [A] / [B] (used in v6 instructions to reference the catalog's
        # [A]/[B] category prefix) and non-identifiers like [...] or
        # [{"name": "...", "value": "..."}] are documentation/example text — silent.
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]+", placeholder):
            logger.warning("fill_template: unknown placeholder [%s] left as literal", placeholder)
        return match.group(0)

    result = re.sub(r"\[([^\]]+)\]", replacer, result)
    # Pass 2b: same substitution for {placeholder} tokens, so templates can use a
    # single brace style. Runs after render_gender ({suffix}/{q_suffix}/{pronoun}
    # are already resolved) and after {{if}} conditionals, so only data/dynamic
    # placeholders remain. Backward compatible — [placeholder] still works above.
    result = re.sub(r"\{([^{}]+)\}", replacer, result)

    # Pass 3: collapse a doubled Thai honorific. Templates write the honorific
    # themselves ("สวัสดีค่ะคุณ [customer_name]") while a CRM row may already carry
    # one — the shipped persona pool holds "นายเอกชัย วัฒนกุล", "คุณกัญญาพัชร …" —
    # so the agent said "คุณ นายเอกชัย". Neither side can be fixed alone: strip the
    # record and templates without an honorific lose it; drop it from templates and
    # records without one lose it. Collapsing after substitution keeps the more
    # specific of the two, which is what a person would say.
    result = re.sub(r"คุณ\s*(คุณ|นายสาว|นางสาว|นาย|นาง|ด\.ช\.|ด\.ญ\.)\s*", r"\1", result)

    # Pass 4: normalize whitespace from removed blocks.
    return re.sub(r" {2,}", " ", result).strip()


# Concrete dynamic placeholders whose Thai fallback is NOT acceptable in
# customer-facing text: a callback/payment confirmation needs a real date/time,
# not the abstract "วันที่นัดหมาย / เวลาที่สะดวก" fallback. If a chosen template
# references one of these, the LLM MUST supply it via dynamic_vars (else the
# reply path rejects + retries). Descriptive vars (dispute_reason, hardship_reason,
# escalation_eta, promised_amount, micro_amount, payment_channel) keep their
# graceful fallback and are intentionally NOT required here.
REQUIRED_DYNAMIC_PLACEHOLDERS = DATE_PLACEHOLDERS | TIME_PLACEHOLDERS


_PLACEHOLDER_BOTH = re.compile(
    r"\[([A-Za-z_][A-Za-z0-9_]*)\]|\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _placeholder_names(text: str) -> list[str]:
    """Placeholder names in a template, in BOTH brace styles.

    `fill_template` substitutes `[name]` and `{name}` alike (pass 2 / 2b), but the
    two guards below scanned square brackets only — and AEON's 64-entry catalog is
    written entirely in `{curly}`. So the leak guard and the required-dynamic-var
    check were structurally incapable of firing for the company that ships the most
    templates. `{{if …}}` control blocks are stripped first; their keywords are not
    slots.
    """
    stripped = re.sub(r"\{\{[^{}]*\}\}", "", text or "")
    return [sq or cu for sq, cu in _PLACEHOLDER_BOTH.findall(stripped)]


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
        for ph in _placeholder_names(s.get("template", "")):
            if ph in REQUIRED_DYNAMIC_PLACEHOLDERS:
                needed.add(ph)
    return sorted(
        ph for ph in needed if not str((dynamic_vars or {}).get(ph) or "").strip()
    )


def leaked_placeholders(rendered_text: str) -> list[str]:
    """Identifier-shaped `[name]` / `{name}` literals remaining after rendering —
    unfilled SYSTEM placeholders (missing CRM field) or stale-template references.
    NOT agent-fixable, so callers should log/tag (not retry-loop) on these.

    Both brace styles: a leaked `{due_date}` is read to the customer exactly as
    badly as a leaked `[due_date]`, and the shipped catalogs use both."""
    names = [n for n in _placeholder_names(rendered_text) if len(n) > 1]
    # render_gender resolves these from the session voice; if one survives it is a
    # gender-rendering bug, not a missing CRM field — out of scope for this guard.
    return [n for n in names if n not in ("suffix", "q_suffix", "pronoun")]
