"""The one place that knows what changes when the demo speaks English.

A session picks `th` (default) or `en` once, and everything downstream reads it from
here. Three rules shaped this module:

**It is a ContextVar, not a module global.** The server handles several sessions at
once; a global would let a Thai call and an English call overwrite each other's
language mid-turn. `LANG` is per-task, so each request carries its own.

**Only the frame lives here.** The tenant's own words — beats, cue examples, tool
descriptions — come from `<CODE>.en.company.json`, because those are the customer's
text and belong in the customer's file. What is here is the scaffolding the app
writes around them, which is ours: section headings, the role sentence, CRM labels,
the gating clauses, the polite particles, the date format.

**Thai stays the source of truth.** Every table is keyed by language with the Thai
entry first and unchanged, so a missing English key falls back to Thai rather than
rendering blank — a half-translated prompt is worse than a Thai one.
"""
from __future__ import annotations

import contextlib
import contextvars

TH = "th"
EN = "en"
LANGS = (TH, EN)

LANG: contextvars.ContextVar[str] = contextvars.ContextVar("aax6_lang", default=TH)


def current() -> str:
    return LANG.get()


def normalise(value: str | None) -> str:
    """Accept `en`, `EN`, `en-US`, None -> a language this module knows."""
    v = (value or "").strip().lower().replace("_", "-").split("-")[0]
    return v if v in LANGS else TH


@contextlib.contextmanager
def use(value: str | None):
    token = LANG.set(normalise(value))
    try:
        yield LANG.get()
    finally:
        LANG.reset(token)


def pick(table: dict[str, object], lang: str | None = None):
    """`table` is {th: ..., en: ...}. Falls back to Thai when a key is missing."""
    return table.get(lang or current(), table[TH])


# ---------------------------------------------------------------- particles
# Thai carries politeness and speaker gender in the sentence; English does not, so
# the placeholders resolve to nothing and the pronoun becomes a plain "I". Keeping
# the placeholders in the English catalog would be a bug, but leaving them harmless
# means a template written for either language renders in both.
GENDER = {
    TH: {"M": {"suffix": "ครับ", "q_suffix": "ครับ", "pronoun": "ผม"},
         "F": {"suffix": "ค่ะ", "q_suffix": "คะ", "pronoun": "ดิฉัน"}},
    EN: {"M": {"suffix": "", "q_suffix": "", "pronoun": "I"},
         "F": {"suffix": "", "q_suffix": "", "pronoun": "I"}},
}

# ------------------------------------------------------------- CRM snapshot
CRM_LABELS = {
    TH: {"today": "วันนี้", "customer_name": "ชื่อลูกค้า", "amount": "ยอดค้างชำระทั้งหมด",
         "minimum_payment": "ยอดชำระขั้นต่ำ", "due_date": "วันครบกำหนด",
         "due_status": "สถานะ", "company_phone": "เบอร์บริษัท",
         "last_4_digits": "เลข 4 ตัวท้าย", "appointment_date": "วันนัด",
         "appointment_time": "เวลานัด", "doctor_name": "แพทย์ผู้นัด", "service_name": "บริการ"},
    EN: {"today": "Today", "customer_name": "Customer name", "amount": "Total outstanding",
         "minimum_payment": "Minimum payment", "due_date": "Due date",
         "due_status": "Status", "company_phone": "Company phone",
         "last_4_digits": "Last 4 digits", "appointment_date": "Appointment date",
         "appointment_time": "Appointment time", "doctor_name": "Doctor",
         "service_name": "Service"},
}

# ------------------------------------------------------------ prompt frame
FRAME = {
    TH: {
        "role_debt": "คุณรับบทเป็นเจ้าหน้าที่ติดตามทวงถามหนี้ของ **บริษัท {company}**",
        "role_generic": "คุณรับบทเป็น **{identity}**",
        "goal": "\n\n**เป้าหมาย: {goal}**",
        "h_principles": "## หลักการ (⛔ กฎสูงสุด)",
        "h_crm": "## ข้อมูลลูกค้า (CRM Snapshot)",
        "h_reply": "## วิธีตอบ (Reply Format)",
        "h_faq": "## FAQ (ตอบคำถามแทรก แล้วกลับเข้า flow)",
        "h_outcome": "## Outcome (จบสายต้องเรียก `{call}` เสมอ)",
        "h_prescripts": "## Available Pre-Scripts (เลือก text_id จากรายการนี้เท่านั้น)",
        "reply_howto": ("ตอบลูกค้าโดยเรียก `reply(text_ids=[...])` เลือกจาก "
                        "**Available Pre-Scripts** ที่ระบบต่อท้ายให้เท่านั้น — "
                        "**ห้ามสร้างข้อความอิสระ**"),
        "catalog_howto": ("เลือก text_id จาก catalog ที่ระบบต่อท้ายให้ "
                          "(รายการเต็มต่อท้ายอัตโนมัติ) — สรุปกลุ่มตาม state:"),
        "silent_tools": "**เครื่องมือ silent (ไม่มีข้อความถึงลูกค้า — เรียกก่อน `reply`):**",
        "enforced": ("\n**กติกาที่ระบบบังคับเอง (เรียกผิดจะถูก reject พร้อมเหตุผล — "
                     "อ่าน hint แล้วแก้):**"),
        "reject_hint": "\nเรียกผิดลำดับ/เรียกซ้ำ จะถูก reject พร้อมเหตุผล — อ่าน hint ",
        "reject_hint2": "แล้วทำตาม ห้ามเรียกซ้ำแบบเดิม",
        "date_fmt": "\nวันที่ทุกค่าใช้รูปแบบ `{fmt}` · `channel` ∈ {channels}",
        "g_after": "เรียกได้หลัง event `{ev}` เท่านั้น",
        "g_max": "สูงสุด {n} ครั้งต่อสาย",
        "g_precede": "ต้องเรียกก่อน `{tool}` เสมอ",
        "g_prior": "ต้องมี `{tool}` ",
        "g_same": "ค่าตรงกัน",
        "g_before": "มาก่อน",
        "g_datetime": "เรียกก่อนพูด/บันทึกวันที่ที่ไม่ใช่วันนี้",
        "g_closing": "**เรียกตอนจบสายเสมอ ครั้งเดียว**",
        "st_chain": "- **พูดต่อกันในเทิร์นเดียว (chain) ตามลำดับ:** ",
        "st_chain2": "— เรียก `reply(text_ids=[...])` ใส่หลาย id เรียงตามนี้",
        "st_silent": "- เมื่อเข้า state นี้ เรียก (silent): {chain}",
        "st_visits": "- เข้า state นี้ได้สูงสุด {n} ครั้งต่อสาย",
        "st_start": " ← เริ่มที่นี่",
        "st_end": "  - จบสาย: `{call}`",
        "faq_resume": " → กลับเข้า flow เดิม",
        "faq_close": " → `{call}` ปิดสาย",
        "ev": "{event} ({desc} — เช่น {cues})",
        "when": " (เมื่อ {ev})",
        "optional": " (ข้ามได้)",
        "if_needed": " (ถ้าจำเป็น)",
        "call_first": " [เรียก ",
        "call_first2": " ก่อน]",
        "or": " หรือ ",
        "templates_hdr": "TEMPLATES (สำหรับ reply — เลือก text_id ให้ตรงสถานการณ์):",
    },
    EN: {
        "role_debt": "You are a debt collection agent at **{company}**",
        "role_generic": "You are **{identity}**",
        "goal": "\n\n**Goal: {goal}**",
        "h_principles": "## Principles (⛔ overriding rules)",
        "h_crm": "## Customer record (CRM snapshot)",
        "h_reply": "## How to reply (reply format)",
        "h_faq": "## FAQ (answer the interruption, then return to the flow)",
        "h_outcome": "## Outcome (every call must end by calling `{call}`)",
        "h_prescripts": "## Available pre-scripts (choose a text_id from this list only)",
        "reply_howto": ("Answer the customer by calling `reply(text_ids=[...])`, choosing "
                        "only from the **Available pre-scripts** appended below — "
                        "**never write your own text**"),
        "catalog_howto": ("Pick a text_id from the catalog appended below (the full list "
                          "is appended automatically) — grouped by state:"),
        "silent_tools": ("**Silent tools (nothing is said to the customer — call them "
                         "before `reply`):**"),
        "enforced": ("\n**Rules the system enforces itself (a wrong call is rejected with "
                     "a reason — read the hint and fix it):**"),
        "reject_hint": ("\nCalling out of order or twice is rejected with a reason — read "
                        "the hint "),
        "reject_hint2": "and follow it; never repeat the same call",
        "date_fmt": "\nEvery date uses the format `{fmt}` · `channel` ∈ {channels}",
        "g_after": "may only be called after the `{ev}` event",
        "g_max": "at most {n} time(s) per call",
        "g_precede": "must always be called before `{tool}`",
        "g_prior": "requires a prior `{tool}` ",
        "g_same": "with a matching value",
        "g_before": "beforehand",
        "g_datetime": "call it before speaking or recording any date other than today",
        "g_closing": "**always called once, at the end of the call**",
        "st_chain": "- **say these in one turn (chain), in order:** ",
        "st_chain2": "— call `reply(text_ids=[...])` with several ids in this order",
        "st_silent": "- on entering this state, call (silent): {chain}",
        "st_visits": "- this state may be entered at most {n} time(s) per call",
        "st_start": " ← start here",
        "st_end": "  - end the call: `{call}`",
        "faq_resume": " → then return to the flow",
        "faq_close": " → `{call}` then close",
        "ev": "{event} ({desc} — e.g. {cues})",
        "when": " (when {ev})",
        "optional": " (optional)",
        "if_needed": " (if needed)",
        "call_first": " [call ",
        "call_first2": " first]",
        "or": " or ",
        "templates_hdr": ("TEMPLATES (for reply — pick the text_id that fits the "
                          "situation):"),
    },
}


def frame(key: str, lang: str | None = None, **fmt) -> str:
    table = FRAME.get(lang or current(), FRAME[TH])
    s = table.get(key) or FRAME[TH].get(key, "")
    return s.format(**fmt) if fmt else s


# ------------------------------------------------------------------ speech
# Chirp 3 HD names differ per locale, so the voice pair travels with the language.
SPEECH = {
    TH: {"tts_language": "th-TH", "tts_voices": {"F": "Despina", "M": "Alnilam"},
         "stt_language": "th-TH", "stt_engine": "zipformer",
         # The customer's own fine-tuned Thai model, 8 kHz.
         "stt_url_env": "AAX6_ZIPFORMER_URL", "stt_rate": 8000},
    # A SECOND Zipformer, English, 16 kHz. Measured against the alternative: 49 ms
    # end-of-audio -> final here, versus ~2,000 ms for Chirp called from this host
    # (cross-Pacific to Google's `us` region, the only one this project may use).
    # Feeding English to the Thai server is not a fallback — it returns an empty
    # final and no partials at all, which the app then reads as silence.
    EN: {"tts_language": "en-US", "tts_voices": {"F": "Aoede", "M": "Charon"},
         "stt_language": "en-US", "stt_engine": "zipformer",
         "stt_url_env": "AAX6_ZIPFORMER_URL_EN", "stt_rate": 16000},
}


# Tool rejections. The model READS these and is expected to correct itself from
# them, so in an English call they have to be English — a Thai hint in an English
# conversation is an instruction the model has to translate before it can obey.
TOOL_ERR = {
    TH: {"missing_required_args": "Error: missing_required_args — {tool} ต้องมีค่าของ {args}",
         "value_required_when": "ต้องมีค่าของ {arg} เมื่อ {when}=",
         "value_not_offered": "Error: value_not_offered — {arg}={val!r} ไม่ได้อยู่ใน",
         "value_not_offered2": "รายการที่ {tool} คืนมา เลือกจาก: "},
    EN: {"missing_required_args": "Error: missing_required_args — {tool} needs a value for {args}",
         "value_required_when": "needs a value for {arg} when {when}=",
         "value_not_offered": "Error: value_not_offered — {arg}={val!r} is not among",
         "value_not_offered2": "the values {tool} returned; choose from: "},
}


def tool_err(key: str, lang: str | None = None, **fmt) -> str:
    table = TOOL_ERR.get(lang or current(), TOOL_ERR[TH])
    return table.get(key, TOOL_ERR[TH][key]).format(**fmt)


def speech(key: str, lang: str | None = None):
    return SPEECH.get(lang or current(), SPEECH[TH])[key]


# ----------------------------------------------- template fallbacks / enums
# Spoken to the customer when the model leaves a dynamic_var out, so a negotiation
# line degrades to an abstract phrase instead of a hole. They are customer-facing
# text, so they follow the language like everything else here.
DYNAMIC_FALLBACK = {
    TH: {"promised_amount": "ตามที่แจ้ง", "promised_date": "วันที่นัดหมายไว้",
         "callback_date": "วันที่นัดหมาย", "callback_time": "เวลาที่สะดวก",
         "payment_channel": "ช่องทางที่ลูกค้าสะดวก", "micro_amount": "จำนวนเล็กน้อย",
         "dispute_reason": "ตามที่แจ้ง", "hardship_reason": "เหตุที่แจ้ง",
         "escalation_eta": "เร็วที่สุด"},
    EN: {"promised_amount": "the amount you mentioned", "promised_date": "the agreed date",
         "callback_date": "the agreed date", "callback_time": "a time that suits you",
         "payment_channel": "a channel that suits you", "micro_amount": "a small amount",
         "dispute_reason": "the reason you gave", "hardship_reason": "the reason you gave",
         "escalation_eta": "as soon as possible"},
}

# The model often passes the raw enum literal ("bank_transfer") into dynamic_vars;
# this turns it into something speakable.
PAYMENT_CHANNEL = {
    TH: {"mobile_app": "แอปพลิเคชันมือถือ", "counter_service": "เคาน์เตอร์เซอร์วิส",
         "branch": "สาขาธนาคาร", "bank_transfer": "การโอนเงินผ่านธนาคาร",
         "atm": "ตู้ ATM", "other": "ช่องทางอื่น"},
    EN: {"mobile_app": "the mobile app", "counter_service": "a counter service",
         "branch": "a bank branch", "bank_transfer": "a bank transfer",
         "atm": "an ATM", "other": "another channel"},
}
