"""Shared pre-script utilities: template filling and script catalog building."""

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


def render_gender(template: str, gender: str) -> str:
    """Substitute {suffix}/{q_suffix}/{pronoun} placeholders. No-op if the
    template has none (the older fully-duplicated catalog).

    The values follow the session's language. Thai carries politeness and speaker
    gender inside the sentence; English carries neither, so there the particles
    resolve to nothing and the pronoun becomes "I". An English template that keeps
    a placeholder then still renders correctly, instead of speaking a Thai word in
    the middle of an English line."""
    if not any(p in template for p in ("{suffix}", "{q_suffix}", "{pronoun}")):
        return template
    from demo_v2.lib import lang as _lang
    values = _lang.pick(_lang.GENDER).get(gender, GENDER_SUFFIXES["M"])
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
    "agent_name": "agent_name",      # Phase G — stylized first name per company
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

# Placeholders the model fills via reply(dynamic_vars=[...]). Each maps to a Thai
# fallback used when the model omits it, so a negotiation template degrades to
# abstract phrasing instead of a hole.
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


def _dynamic_fallback() -> dict:
    """The spoken fallback for an omitted dynamic_var, in the session's language."""
    from demo_v2.lib import lang as _lang
    return _lang.pick(_lang.DYNAMIC_FALLBACK)


def _payment_channel() -> dict:
    from demo_v2.lib import lang as _lang
    return _lang.pick(_lang.PAYMENT_CHANNEL)

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


# This string is prepended to EVERY tenant's catalog, so it may only say what the
# runtime actually enforces and what is true of every domain. Two clauses were
# dropped for failing that: an A/B turn-composition prefix (obeyed 3 times in 176
# measured multi-beat turns, enforced by nothing, removal score-neutral) and a
# `record_verbal_commitment` ordering rule that a clinic and a shop were being told
# to follow for a tool neither declares. Per-tool ordering still reaches the prompt
# from each spec's own `requires_prior` / `args_must_match`.
CHAIN_RULE = (
    "**Chain rule**: a state's beats are spoken together in one turn, in the order "
    "listed — send every beat's text_id in a single `reply`. A partial chain is "
    "rejected and you are told which beats are missing. Beats from two different "
    "states may not be paired."
)


def _templates_header() -> str:
    from demo_v2.lib import lang as _lang
    return _lang.frame("templates_hdr")


# Kept as a name for the Thai callers that import it; the block builder asks the
# function instead, so the header follows the session language.
TEMPLATES_HEADER = "TEMPLATES (สำหรับ reply — เลือก text_id ให้ตรงสถานการณ์):"


def build_template_block(script_db: list[dict], compact: bool = False) -> str:
    """The template store as the prompt shows it — the same shape the training and eval
    side uses.

    Two things it deliberately does NOT do. It does not head the block
    `## Available Pre-Scripts`, which collided with the identical heading
    `render_instruction` prints for its index of beat names, so every tenant's prompt
    carried that heading twice with different content. And it does not group by each
    entry's `state` field: that label comes from an older flow and disagrees with the
    spec's `phase` (measured: AEON 15 beats grouped wrongly, KBANK 5, SKL 6), so the
    model was reading two grouping systems at once.

    Two things it keeps, both measured to matter: `‹hint›` (without it the model picks
    the first entry of a group every time) and `| Vars: [...]`, the DYNAMIC placeholder
    names that must arrive in `dynamic_vars`.
    """
    lines = [_templates_header()]
    for entry in script_db:
        tid = entry["text_id"]
        # `_fine_state` first, NOT `intent_name` — the same older-flow label problem as
        # the `state` grouping above. The beat name is what the rest of the prompt calls
        # this line: the flow map says `- template: disclose_balance` and the constraints
        # name that same string. `intent_name` is a v6-era grouping key that several
        # beats deliberately share, so KBANK had disclose_balance, ask_pay_today, apology
        # and handoff_refuse all labelled `negotiation_ask_pay_today` — the catalog
        # offered four lines under one name and none at all under the name the flow map
        # had just told the model to speak (17 of 28 lines mislabelled, AEON 32 of 58).
        name = entry.get("_fine_state") or entry.get("intent_name", "")
        body = entry.get("template", "")
        dyn = _extract_dynamic_vars_from_template(body)
        vars_suffix = f" | Vars: [{', '.join(dyn)}]" if dyn else ""
        hint = entry.get("hint")
        hint_s = f" ‹{hint}›" if hint else ""
        if compact:
            lines.append(f" {tid} [{name}]{vars_suffix}{hint_s}")
        else:
            lines.append(f" {tid} [{name}]{vars_suffix}{hint_s}: {_strip_conditionals(body)}")
    return "\n".join(lines)


def fill_template(
    template: str,
    agent_context_data: dict,
    dynamic_vars: dict | None = None,
    strict_dates: bool = False,
    gender: str | None = None,
) -> str:
    """Replace [placeholder] tokens and resolve {{if field}}…{{/if}} blocks.

    SYSTEM placeholders resolve from agent_context_data (fixed per case); DYNAMIC ones
    from dynamic_vars, falling back to a Thai phrase when the model omits them, so a
    template degrades to abstract phrasing instead of a hole.

    With `strict_dates=True`, date and time values are validated against the canonical
    format and rendered to natural Thai; a malformed one raises DateFormatError, which
    the reply handler turns into `{sent: False, reason: "date_format_invalid"}`.

    `gender` resolves {suffix}/{q_suffix}/{pronoun}.
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
                return _dynamic_fallback()[placeholder]
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
                return _payment_channel().get(value_str.strip(), value_str)
            return value_str
        # A spec-declared field with no registry entry. The registries above are
        # debt-domain, so without this a spec naming its own CRM fields leaked every
        # token to the caller (observed: "มีนัดพบ [doctor_name] วันที่
        # [appointment_date]"). Placeholder name == data key is what makes it
        # spec-driven; dynamic_vars is the second chance, for a field the model
        # supplies rather than the case carrying.
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
        # Single-char tokens like [A] / [B] and non-identifiers like [...] or
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

    # Pass 3: collapse a doubled Thai honorific. A template writes one of its own
    # and a CRM row may already carry one ("นายเอกชัย วัฒนกุล"), which came out as
    # "คุณ นายเอกชัย". Neither side can be fixed alone — collapsing after
    # substitution keeps the more specific of the two.
    result = re.sub(r"คุณ\s*(คุณ|นายสาว|นางสาว|นาย|นาง|ด\.ช\.|ด\.ญ\.)\s*", r"\1", result)

    # Pass 4: normalize whitespace from removed blocks.
    return re.sub(r" {2,}", " ", result).strip()


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
