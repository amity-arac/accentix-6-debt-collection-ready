"""Phase H — standardized date/time format for v6.

Canonical formats used at every machine boundary (tool args + dynamic_vars):

  date       : "YYYY-MM-DD (Weekday)"           e.g. "2026-05-23 (Saturday)"
  time       : "HH:MM" 24-hour                   e.g. "14:00"
  datetime   : "<date> <time>"                   e.g. "2026-05-23 (Saturday) 14:00"

Weekday names are English (Monday..Sunday). The weekday in the string MUST match
the calendar — `2026-05-23 (Sunday)` is rejected (that day is Saturday).

`SIMULATION_DATE` lives in `simulator.config` as a hard-coded constant so
trajectories are reproducible across replays. Bump it manually before each
benchmark by editing `simulator/config.py`.
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


def _simulation_date() -> _dt.date:
    """Read SIMULATION_DATE from config at call time (avoids import cycle)."""
    from simulator.config import SIMULATION_DATE
    return _dt.date.fromisoformat(SIMULATION_DATE)


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


# พ.ร.บ. การทวงถามหนี้ พ.ศ. 2558 §9(2): contact only between 08:00–20:00.
LEGAL_HOUR_START_MIN = 8 * 60   # 08:00
LEGAL_HOUR_END_MIN = 20 * 60    # 20:00 (inclusive boundary)


def is_within_legal_hours(s: str) -> bool:
    """True iff s is a valid `HH:MM` AND falls within debt-collection legal contact
    hours 08:00–20:00 inclusive (§9(2)). 20:01+ and before 08:00 are out of hours."""
    if not is_valid_time(s):
        return False
    minutes = int(s[:2]) * 60 + int(s[3:5])
    return LEGAL_HOUR_START_MIN <= minutes <= LEGAL_HOUR_END_MIN


def is_valid_datetime(s: str) -> bool:
    if not isinstance(s, str):
        return False
    m = DATETIME_RE.match(s)
    if not m:
        return False
    date_part = " ".join(s.rsplit(" ", 1)[:-1])  # everything except final HH:MM
    return is_valid_date(date_part)


def parse_date(s: str) -> _dt.date:
    """Strict parse. Raises ValueError on format/calendar/weekday mismatch."""
    if not is_valid_date(s):
        raise ValueError(f"invalid date string: {s!r}")
    m = DATE_RE.match(s)
    return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def render_date_thai(s: str) -> str:
    """`2026-05-23 (Saturday)` → `วันเสาร์ที่ 23 พฤษภาคม 2026`."""
    d = parse_date(s)
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
    return f"{s} น."


def render_datetime_thai(s: str) -> str:
    """Auto-detect date / time / datetime and render to natural Thai."""
    if is_valid_datetime(s):
        date_part, time_part = s.rsplit(" ", 1)
        return f"{render_date_thai(date_part)} เวลา {render_time_thai(time_part)}"
    if is_valid_date(s):
        return render_date_thai(s)
    if is_valid_time(s):
        return render_time_thai(s)
    raise ValueError(f"not a recognized date/time/datetime string: {s!r}")


def today_iso() -> str:
    """SIMULATION_DATE formatted as `YYYY-MM-DD (Weekday)`."""
    return _format_date(_simulation_date())


def relative_iso(offset_days: int) -> str:
    """today + offset_days, formatted as `YYYY-MM-DD (Weekday)`."""
    return _format_date(_simulation_date() + _dt.timedelta(days=offset_days))


def datetime_lookup_table() -> dict:
    """Anchors returned by the `get_current_datetime` backend tool.

    Pre-computed so the LLM never has to do weekday arithmetic for the most
    common offsets it speaks about. Strict-format strings — pass directly into
    tool args / dynamic_vars without modification.
    """
    return {
        "today": relative_iso(0),
        "tomorrow": relative_iso(1),
        "day_after_tomorrow": relative_iso(2),
        "in_one_week": relative_iso(7),
    }


def expected_weekday_for(date_str_without_weekday: str) -> str | None:
    """Helper for error hints: given 'YYYY-MM-DD', return the correct weekday name.

    Returns None if the input isn't a valid YYYY-MM-DD date.
    """
    try:
        d = _dt.date.fromisoformat(date_str_without_weekday)
    except ValueError:
        return None
    return WEEKDAYS_EN[d.weekday()]
