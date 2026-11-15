import { CAMPUS_TIMEZONE, type ImpactMetricKey, type ISODateTime } from "../types/hexam";

const fmt = (opts: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat("en-US", { ...opts, timeZone: CAMPUS_TIMEZONE });
const timeF = fmt({ hour: "numeric", minute: "2-digit" });
const dayF = fmt({ weekday: "short", month: "short", day: "numeric" });

export const campusTime = (iso: ISODateTime) => timeF.format(new Date(iso));
export const campusDay = (iso: ISODateTime) => dayF.format(new Date(iso));
export const timeRange = (start: ISODateTime, end: ISODateTime) => `${campusTime(start)}–${campusTime(end)}`;

/** "YYYY-MM-DD" in campus time. */
export const campusDateKey = (iso: ISODateTime) => new Intl.DateTimeFormat("en-CA", { timeZone: CAMPUS_TIMEZONE }).format(new Date(iso));
/** "HH:MM" 24h campus time (row key for the timetable). */
export const campusHHMM = (iso: ISODateTime) =>
  new Intl.DateTimeFormat("en-GB", { timeZone: CAMPUS_TIMEZONE, hour: "2-digit", minute: "2-digit" }).format(new Date(iso));

export const dayFromKey = (key: string) => {
  const [y, m, d] = key.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d, 12)).toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" });
};

export const num = (n: number | null | undefined, digits = 0) =>
  n == null ? "—" : n.toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: digits });

export const pct = (p: number | null | undefined) => (p == null ? "n/a" : `${p > 0 ? "+" : ""}${p.toFixed(1)}%`);

export const courseLabel = (ids: string[], max = 3) =>
  ids.length === 0 ? "—" : ids.slice(0, max).map((c) => c.replace("-", " ")).join(" / ") + (ids.length > max ? ` +${ids.length - max}` : "");

export const METRICS: { key: ImpactMetricKey; label: string; short: string; unit: string; help: string }[] = [
  {
    key: "simultaneous_conflicts",
    label: "Simultaneous conflicts",
    short: "Conflicts",
    unit: "exam pairs",
    help: "Pairs of a student's exams whose actual time intervals overlap.",
  },
  {
    key: "three_in_24h",
    label: "3 exams within 24 h",
    short: "3 in 24 h",
    unit: "students",
    help: "Students with three exam starts inside a rolling window shorter than 24 hours (includes 3+ exams on one calendar day).",
  },
  {
    key: "back_to_back",
    label: "Back-to-back exams",
    short: "Back-to-back",
    unit: "exam pairs",
    help: "Non-overlapping exam pairs separated by at most the back-to-back gap (default 30 min).",
  },
  {
    key: "four_in_48h",
    label: "4 exams within 48 h",
    short: "4 in 48 h",
    unit: "students",
    help: "Students with four exam starts inside a rolling window shorter than 48 hours.",
  },
  {
    key: "night_to_morning",
    label: "Late-evening → early-morning",
    short: "Late → early",
    unit: "exam pairs",
    help: "An exam starting at/after 5 PM followed by one starting before 10 AM the next day (break ≤ 14 h).",
  },
];

export const metricLabel = (k: ImpactMetricKey) => METRICS.find((m) => m.key === k)?.label ?? k;

export const STANDING: Record<string, string> = {
  first_year: "1st year",
  second_year: "2nd year",
  third_year: "3rd year",
  fourth_year: "4th year",
  fifth_year_plus: "5th year+",
  graduate: "Graduate",
};

export function classNames(...xs: (string | false | null | undefined)[]) {
  return xs.filter(Boolean).join(" ");
}
