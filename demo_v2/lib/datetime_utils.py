"""Canonical date/time formats, used at every machine boundary (tool args,
dynamic_vars):

  date       "YYYY-MM-DD (Weekday)"      e.g. "2026-05-23 (Saturday)"
  time       "HH:MM" 24-hour             e.g. "14:00"
  datetime   "<date> <time>"

Weekday names are English, and the weekday in the string MUST match the calendar —
`2026-05-23 (Sunday)` is rejected, that day is a Saturday.

"Today" is the real current date in Asia/Bangkok (UTC+7, no DST), so every date the
agent speaks tracks the live Thai calendar.
"""

import datetime as _dt
import re

WEEKDAYS_EN = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
WEEKDAYS_TH = ("จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์")
MONTHS_TH = (
    "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
    "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม",
)

_WEEKDAY_PATTERN = "|".join(WEEKDAYS_EN)
DATE_RE = re.compile(
    rf"^(\d{{4}})-(\d{{2}})-(\d{{2}}) \(({_WEEKDAY_PATTERN})\)$"
)
TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
DATETIME_RE = re.compile(
    rf"^(\d{{4}})-(\d{{2}})-(\d{{2}}) \(({_WEEKDAY_PATTERN})\) ([01]\d|2[0-3]):([0-5]\d)$"
)


# Thailand observes Indochina Time (UTC+7) year-round with no DST, so a fixed
# offset is exact and avoids any dependency on the system tz database.
BANGKOK_TZ = _dt.timezone(_dt.timedelta(hours=7), name="ICT")


def now_bangkok() -> _dt.datetime:
    """Current wall-clock time in the Asia/Bangkok zone (UTC+7, no DST)."""
    return _dt.datetime.now(BANGKOK_TZ)


def _today() -> _dt.date:
    """Today's date in the Asia/Bangkok zone — the live anchor for every
    'today'/'tomorrow'/... the agent computes (replaces the old fixed
    SIMULATION_DATE)."""
    return now_bangkok().date()


def _format_date(d: _dt.date) -> str:
    return f"{d.isoformat()} ({WEEKDAYS_EN[d.weekday()]})"


def is_valid_date(s: str) -> bool:
    """True iff s matches `YYYY-MM-DD (Weekday)` AND the calendar agrees."""
    if not isinstance(s, str):
        return False
    m = DATE_RE.match(s)
    if not m:
        return False
    year, month, day, weekday = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)
    try:
        d = _dt.date(year, month, day)
    except ValueError:
        return False
    return WEEKDAYS_EN[d.weekday()] == weekday


def is_valid_time(s: str) -> bool:
    return isinstance(s, str) and bool(TIME_RE.match(s))


def parse_date(s: str) -> _dt.date:
    """Strict parse. Raises ValueError on format/calendar/weekday mismatch."""
    if not is_valid_date(s):
        raise ValueError(f"invalid date string: {s!r}")
    m = DATE_RE.match(s)
    return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def render_date_thai(s: str) -> str:
    """`2026-05-23 (Saturday)` → `วันเสาร์ที่ 23 พฤษภาคม 2026`, or the English
    reading of the same date when the session is in English.

    The name is kept because a dozen call sites spell it, and every one of them
    means "say this date the way the agent speaks" — which is exactly what should
    follow the session's language. Renaming it would have been the honest thing
    and a wider diff; this comment is the compromise."""
    d = parse_date(s)
    from demo_v2.lib import lang as _lang
    if _lang.current() == _lang.EN:
        return d.strftime("%A %-d %B %Y")
    return f"วัน{WEEKDAYS_TH[d.weekday()]}ที่ {d.day} {MONTHS_TH[d.month - 1]} {d.year}"


def render_time_thai(s: str) -> str:
    """`14:00` → `14:00 น.`.

    Note: no `เวลา` prefix on purpose — templates that need it already have
    "เวลา [callback_time]" or "ช่วงเวลา [callback_time]" inline, and
    duplicating the prefix produces "เวลา เวลา 14:00 น." in the rendered
    reply. Standalone "[callback_time]" still reads naturally as "14:00 น.".
    """
    if not is_valid_time(s):
        raise ValueError(f"invalid time string: {s!r}")
    from demo_v2.lib import lang as _lang
    if _lang.current() == _lang.EN:
        return s            # "14:00" already reads as a time in English
    return f"{s} น."


def today_iso() -> str:
    """Real Asia/Bangkok 'today' formatted as `YYYY-MM-DD (Weekday)`."""
    return _format_date(_today())


def future_date(days: int) -> str:
    """A real date `days` from today (Asia/Bangkok), as `YYYY-MM-DD (Weekday)`.
    Used to resolve a persona's `due_offset_days` into a live due_date so a
    pre-due (Remind) case is always genuinely in the future."""
    return _format_date(_today() + _dt.timedelta(days=int(days)))


_TH_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")
_TH_WEEKDAY = {"จันทร์": 0, "อังคาร": 1, "พุธ": 2, "พฤหัส": 3, "ศุกร์": 4, "เสาร์": 5, "อาทิตย์": 6}
_TH_MONTH = {"มกราคม": 1, "กุมภาพันธ์": 2, "มีนาคม": 3, "เมษายน": 4, "พฤษภาคม": 5, "มิถุนายน": 6,
             "กรกฎาคม": 7, "สิงหาคม": 8, "กันยายน": 9, "ตุลาคม": 10, "พฤศจิกายน": 11, "ธันวาคม": 12,
             "ม.ค.": 1, "ก.พ.": 2, "มี.ค.": 3, "เม.ย.": 4, "พ.ค.": 5, "มิ.ย.": 6,
             "ก.ค.": 7, "ส.ค.": 8, "ก.ย.": 9, "ต.ค.": 10, "พ.ย.": 11, "ธ.ค.": 12}


def resolve_spoken_date(text: str, today: "_dt.date | None" = None) -> "_dt.date | None":
    """A spoken Thai date → a date (future only). None = unparseable, or already ISO.

    This lets a tool accept the words the customer actually said and have the code
    do the conversion. The model only carries over what it heard — copying, which a
    small model does reliably — while the calendar arithmetic (crossing a month,
    end of month, next weekday) stays with the code. It is the standard voice-bot
    move (Duckling, a date node), and it is tied to the Thai language, not to any
    company or domain.
    """
    import re as _re
    base = today or _today()
    t = str(text or "").translate(_TH_DIGITS)
    if not t or _re.match(r"^\s*\d{4}-\d{2}", t):
        return None
    if _re.search(r"มะรืน", t):
        return base + _dt.timedelta(days=2)
    if _re.search(r"พรุ่งนี้", t):
        return base + _dt.timedelta(days=1)
    m = _re.search(r"อีก\s*(\d{1,2})\s*วัน", t)
    if m:
        return base + _dt.timedelta(days=int(m.group(1)))
    import calendar as _cal
    end_this = base.replace(day=_cal.monthrange(base.year, base.month)[1])
    if _re.search(r"(สิ้นเดือน|ปลายเดือน)\s*หน้า", t):
        nxt = end_this + _dt.timedelta(days=1)
        return nxt.replace(day=_cal.monthrange(nxt.year, nxt.month)[1])
    if _re.search(r"(สิ้นเดือน|ปลายเดือน)", t):
        return end_this
    if _re.search(r"(อาทิตย์|สัปดาห์)หน้า", t) and not _re.search(r"วันอาทิตย์", t):
        return base + _dt.timedelta(days=7)
    for name, wd in _TH_WEEKDAY.items():
        if _re.search("วัน" + name + "|" + name + r"(นี้|หน้า)", t):
            d = (wd - base.weekday()) % 7 or 7
            if _re.search(name + r"\s*หน้า", t):      # "X หน้า" = X of next week
                d += 7 if d <= 7 else 0
                if d > 14:
                    d -= 7
            return base + _dt.timedelta(days=d)
    for name, mo in _TH_MONTH.items():
        m = _re.search(r"(?:วันที่\s*)?(\d{1,2})\s*" + _re.escape(name), t)
        if m:
            day = int(m.group(1))
            yr = base.year + (1 if (mo, day) <= (base.month, base.day) else 0)
            try:
                cand = _dt.date(yr, mo, day)
            except ValueError:
                return None
            return cand if cand > base else None
    m = _re.search(r"วันที่\s*(\d{1,2})", t)
    if m:
        day = int(m.group(1))
        if 1 <= day <= 31:
            force_next = bool(_re.search(r"เดือนหน้า", t))
            for add in ((1,) if force_next else (0, 1)):
                mo, yr = base.month + add, base.year
                if mo > 12:
                    mo, yr = mo - 12, yr + 1
                try:
                    cand = base.replace(year=yr, month=mo, day=day)
                except ValueError:
                    continue
                if cand > base:
                    return cand
    return None
