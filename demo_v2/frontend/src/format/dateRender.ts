/**
 * Port of simulator/datetime_utils.py's render helpers, but emitting English
 * chrome strings instead of Thai. Used to render canonical
 *   "YYYY-MM-DD (Weekday)"          e.g. "2026-04-20 (Monday)"
 *   "YYYY-MM-DD (Weekday) HH:MM"    e.g. "2026-04-20 (Monday) 14:00"
 * into friendly forms for the Customer Panel and tool-result tables.
 *
 * Lenient: if the input doesn't match the canonical format, return it
 * unchanged. The chrome should never crash because the backend handed us
 * something unexpected.
 */

const WEEKDAY_SHORT = [
  "Mon",
  "Tue",
  "Wed",
  "Thu",
  "Fri",
  "Sat",
  "Sun",
] as const;

const WEEKDAY_LONG = [
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
  "Sunday",
];

const MONTH_SHORT = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

const DATE_RE =
  /^(\d{4})-(\d{2})-(\d{2}) \((Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\)$/;
const DATETIME_RE =
  /^(\d{4})-(\d{2})-(\d{2}) \((Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\) ([01]\d|2[0-3]):([0-5]\d)$/;

function pyWeekday(d: Date): number {
  // JS getDay: 0 = Sunday … 6 = Saturday
  // Python weekday: 0 = Monday … 6 = Sunday
  const js = d.getUTCDay();
  return (js + 6) % 7;
}

/** Render a canonical date string as e.g. `Mon, 20 Apr 2026`. */
export function renderDate(s: string): string {
  if (typeof s !== "string") return String(s ?? "—");
  const m = DATE_RE.exec(s);
  if (!m) return s;
  const [, yearS, monthS, dayS, weekdayLong] = m;
  const year = Number(yearS);
  const month = Number(monthS);
  const day = Number(dayS);
  // Calendar sanity — guard against e.g. 2026-02-30
  const d = new Date(Date.UTC(year, month - 1, day));
  if (
    d.getUTCFullYear() !== year ||
    d.getUTCMonth() !== month - 1 ||
    d.getUTCDate() !== day
  ) {
    return s;
  }
  const expected = WEEKDAY_LONG[pyWeekday(d)];
  if (expected !== weekdayLong) {
    // Weekday doesn't match calendar; emit best-effort short form using calendar weekday.
    return `${WEEKDAY_SHORT[pyWeekday(d)]}, ${day} ${MONTH_SHORT[month - 1]} ${year}`;
  }
  return `${WEEKDAY_SHORT[pyWeekday(d)]}, ${day} ${MONTH_SHORT[month - 1]} ${year}`;
}

/** Render a canonical datetime string as e.g. `Mon, 20 Apr 2026 · 14:00`. */
export function renderDateTime(s: string): string {
  if (typeof s !== "string") return String(s ?? "—");
  const m = DATETIME_RE.exec(s);
  if (!m) return renderDate(s);
  const dateOnly = s.slice(0, s.lastIndexOf(" "));
  const time = s.slice(s.lastIndexOf(" ") + 1);
  return `${renderDate(dateOnly)} · ${time}`;
}

/** Heuristic helper: detect canonical date string. */
export function looksLikeCanonicalDate(s: unknown): boolean {
  return typeof s === "string" && DATE_RE.test(s);
}

/** Heuristic helper: detect canonical datetime string. */
export function looksLikeCanonicalDateTime(s: unknown): boolean {
  return typeof s === "string" && DATETIME_RE.test(s);
}
