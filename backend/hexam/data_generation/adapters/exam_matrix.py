"""Parser for the registrar's one-page Final Exam Matrix PDF.

Plain text extraction scrambles this document: it is a two-column sheet whose
"Exam Date/Time" cells are merged across several class-meeting rows, and the
extractor emits those merged cells separately from their rows. We therefore
extract every text fragment *with its page coordinates* (pypdf visitor) and
rebuild the tables geometrically:

* a class-meeting row is ``<DAYS> <h:mm AM> <h:mm PM>``; the left and right
  tables' rows on the same baseline arrive fused in one fragment, so the first
  match of a fragment starting in the left half belongs to the left table;
* each merged date cell (``Thursday, December 17`` + ``8:00 AM - 10:50 AM``)
  is top-aligned with the first row of its block, so a row belongs to the
  nearest date cell at or above it in the same column;
* "Common Exams" and "Reading and Conflict Periods" rows are matched by their
  own patterns.

``validate_matrix`` cross-checks the result (weekday names vs dates, every
row assigned, consistent exam windows, unique pattern keys).
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time
from pathlib import Path

_T = r"\d{1,2}:\d{2}\s?[AP]M"
_ROW = re.compile(rf"\b([MTWRFSU]{{1,5}}) ({_T}) ({_T})(?=\s|$)")
_DATE_CELL = re.compile(r"^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), (January|February|March|April|May|June|July|August|September|October|November|December) (\d{1,2})$")
_TIME_CELL = re.compile(rf"^({_T}) - ({_T})$")
_SHORT_DATE = r"(Mon|Tues|Tue|Wed|Thurs|Thu|Fri|Sat|Sun), (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{1,2})"
_COMMON = re.compile(rf"^(?P<courses>[A-Z]{{2,4}} \d{{4}}.*?) {_SHORT_DATE} (?P<start>{_T}) - (?P<end>{_T})")
_SPECIAL = re.compile(rf"^(?P<kind>Reading Period|Conflict Period) {_SHORT_DATE} (?P<start>{_T}) - (?P<end>{_T})")
_TITLE = re.compile(r"(FALL|SPRING|SUMMER) (\d{4}) FINAL EXAM MATRIX")

_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_WEEKDAY_ABBR = {"mon": 0, "tue": 1, "tues": 1, "wed": 2, "thu": 3, "thurs": 3, "fri": 4, "sat": 5, "sun": 6}
_WEEKDAY_FULL = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}

COLUMN_SPLIT_X = 300.0  # page is 612pt wide; the right-hand tables start at x≈318


def _t(s: str) -> time:
    return datetime.strptime(s.replace(" ", "").upper(), "%I:%M%p").time()


@dataclass(frozen=True)
class ExamPeriod:
    date: date
    start: time
    end: time

    @property
    def key(self) -> str:
        return f"{self.date:%Y%m%d}-{self.start:%H%M}"


@dataclass(frozen=True)
class MatrixRow:
    """One class meeting pattern and the exam period it maps to."""

    days: str
    class_start: time
    class_end: time
    exam: ExamPeriod
    column: str  # "left" | "right" (for diagnostics)

    @property
    def pattern_key(self) -> str:
        """Same format as ``MeetingTime.pattern_key``: ``"MWF 09:30"``."""
        return f"{self.days} {self.class_start:%H:%M}"


@dataclass(frozen=True)
class CommonExam:
    label: str  # as printed, e.g. "MATH 1553/1554"
    course_keys: tuple[str, ...]  # ("MATH 1553", "MATH 1554")
    qualifier: str | None  # e.g. "online" for "ECE 3710 *online"
    exam: ExamPeriod


@dataclass(frozen=True)
class SpecialPeriod:
    kind: str  # "reading" | "conflict"
    period: ExamPeriod


@dataclass
class ExamMatrix:
    term_label: str  # "FALL 2026"
    year: int
    rows: list[MatrixRow]
    common_exams: list[CommonExam]
    special_periods: list[SpecialPeriod]
    issues: list[str] = field(default_factory=list)

    @property
    def exam_periods(self) -> list[ExamPeriod]:
        """Every regular exam period (from rows and common exams), chronological."""
        ps = {r.exam for r in self.rows} | {c.exam for c in self.common_exams}
        return sorted(ps, key=lambda p: (p.date, p.start))

    @property
    def windows(self) -> list[tuple[time, time]]:
        return sorted({(p.start, p.end) for p in self.exam_periods})

    def lookup(self, days: str, start: time, end: time) -> tuple[ExamPeriod | None, str]:
        """Exam period for a class meeting: exact (days, start, end) match first,
        then (days, start) if all rows sharing it agree. Returns (period, how)."""
        exact = [r.exam for r in self.rows if r.days == days and r.class_start == start and r.class_end == end]
        if exact:
            return exact[0], "matrix_exact"
        by_start = {r.exam for r in self.rows if r.days == days and r.class_start == start}
        if len(by_start) == 1:
            return next(iter(by_start)), "matrix_days_start"
        return None, "no_match" if not by_start else "ambiguous"

    @property
    def end_sensitive_patterns(self) -> set[str]:
        """``days+start`` keys whose exam period depends on the class end time
        (Fall 2026: ``WF 08:25`` → 9:15 end is Fri Dec 11, 10:20 end is Wed Dec 16)."""
        by_start: dict[str, set[ExamPeriod]] = defaultdict(set)
        for r in self.rows:
            by_start[r.pattern_key].add(r.exam)
        return {k for k, ps in by_start.items() if len(ps) > 1}

    def common_exam_for(self, course_key: str) -> CommonExam | None:
        return next((c for c in self.common_exams if course_key in c.course_keys), None)

    def to_json(self) -> dict:
        def period(p: ExamPeriod) -> dict:
            return {"date": p.date.isoformat(), "start": p.start.strftime("%H:%M"), "end": p.end.strftime("%H:%M")}

        return {
            "term_label": self.term_label,
            "exam_windows": [{"start": s.strftime("%H:%M"), "end": e.strftime("%H:%M")} for s, e in self.windows],
            "exam_periods": [period(p) for p in self.exam_periods],
            "meeting_pattern_rows": [
                {
                    "days": r.days,
                    "class_start": r.class_start.strftime("%H:%M"),
                    "class_end": r.class_end.strftime("%H:%M"),
                    "pattern_key": r.pattern_key,
                    "exam": period(r.exam),
                }
                for r in sorted(self.rows, key=lambda r: (r.exam.date, r.exam.start, r.days, r.class_start, r.class_end))
            ],
            "common_exams": [
                {"label": c.label, "course_keys": list(c.course_keys), "qualifier": c.qualifier, "exam": period(c.exam)}
                for c in self.common_exams
            ],
            "special_periods": [{"kind": s.kind, **period(s.period)} for s in self.special_periods],
            "end_sensitive_patterns": sorted(self.end_sensitive_patterns),
            "parse_issues": self.issues,
        }


@dataclass(frozen=True)
class _Frag:
    x: float
    y: float
    text: str


def extract_fragments(pdf_path: Path) -> list[_Frag]:
    try:
        import pypdf
    except ImportError as e:  # pragma: no cover - environment dependent
        raise RuntimeError("parsing the exam matrix PDF needs pypdf: pip install -e '.[data]'") from e

    reader = pypdf.PdfReader(str(pdf_path))
    if len(reader.pages) != 1:
        raise ValueError(f"expected a one-page matrix, got {len(reader.pages)} pages")
    frags: list[_Frag] = []

    def visit(text, cm, tm, _font, _size):
        if text and text.strip():
            x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
            y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
            frags.append(_Frag(round(x, 1), round(y, 1), " ".join(text.split())))

    reader.pages[0].extract_text(visitor_text=visit)
    return frags


def _short_date(year: int, wd: str, mon: str, day: str) -> date:
    d = date(year, _MONTHS[mon.lower()[:3]], int(day))
    if d.weekday() != _WEEKDAY_ABBR[wd.lower()]:
        raise ValueError(f"weekday {wd!r} does not match {d.isoformat()}")
    return d


def parse_fragments(frags: list[_Frag]) -> ExamMatrix:
    issues: list[str] = []
    title = next((m for f in frags if (m := _TITLE.search(f.text))), None)
    if title is None:
        raise ValueError("matrix title (e.g. 'FALL 2026 FINAL EXAM MATRIX') not found")
    year = int(title.group(2))

    # 1. Merged date cells: a full date followed (≈10pt lower, same x) by a time range.
    date_cells: list[tuple[str, float, ExamPeriod]] = []  # (column, y, period)
    used: set[int] = set()
    for i, f in enumerate(frags):
        m = _DATE_CELL.match(f.text)
        if not m:
            continue
        d = date(year, _MONTHS[m.group(2).lower()[:3]], int(m.group(3)))
        if d.weekday() != _WEEKDAY_FULL[m.group(1).lower()]:
            issues.append(f"date cell {f.text!r} weekday does not match {d}")
        below = [
            (j, g)
            for j, g in enumerate(frags)
            if abs(g.x - f.x) < 2 and 0 < f.y - g.y < 15 and _TIME_CELL.match(g.text)
        ]
        if not below:
            issues.append(f"date cell {f.text!r} at y={f.y} has no time range below it")
            continue
        j, g = min(below, key=lambda jg: f.y - jg[1].y)
        tm = _TIME_CELL.match(g.text)
        assert tm is not None
        used.update({i, j})
        col = "left" if f.x < COLUMN_SPLIT_X else "right"
        date_cells.append((col, f.y, ExamPeriod(d, _t(tm.group(1)), _t(tm.group(2)))))

    # 2. Merge remaining fragments that share a baseline into lines (handles split cells).
    lines: dict[float, list[_Frag]] = defaultdict(list)
    for i, f in enumerate(frags):
        if i in used:
            continue
        key = next((y for y in lines if abs(y - f.y) <= 1.5), f.y)
        lines[key].append(f)

    rows: list[MatrixRow] = []
    common: list[CommonExam] = []
    special: list[SpecialPeriod] = []
    unassigned = 0
    for y, parts in sorted(lines.items(), key=lambda kv: -kv[0]):
        parts.sort(key=lambda f: f.x)
        text = " ".join(p.text for p in parts)
        x0 = parts[0].x

        # Common-exam and reading/conflict rows live in the left column; strip them first.
        rest = text
        if x0 < COLUMN_SPLIT_X:
            if m := _COMMON.match(text):
                exam = ExamPeriod(_short_date(year, m.group(2), m.group(3), m.group(4)), _t(m.group("start")), _t(m.group("end")))
                common.extend(_split_common(m.group("courses"), exam))
                rest = text[m.end():]
            elif m := _SPECIAL.match(text):
                kind = "reading" if m.group("kind").startswith("Reading") else "conflict"
                p = ExamPeriod(_short_date(year, m.group(2), m.group(3), m.group(4)), _t(m.group("start")), _t(m.group("end")))
                special.append(SpecialPeriod(kind, p))
                rest = text[m.end():]

        matches = list(_ROW.finditer(rest))
        for k, m in enumerate(matches):
            if rest is text and x0 < COLUMN_SPLIT_X and k == 0 and m.start() == 0:
                col = "left"
            else:
                col = "right"
            if col == "left" and len(matches) > 2 or col == "right" and k > 1:
                issues.append(f"unexpected row layout at y={y}: {text!r}")
            above = [(cy, p) for c, cy, p in date_cells if c == col and cy >= y - 2]
            if not above:
                unassigned += 1
                issues.append(f"row {m.group(0)!r} ({col}, y={y}) has no exam date cell above it")
                continue
            _, period = min(above, key=lambda t: t[0] - y)
            rows.append(MatrixRow(m.group(1), _t(m.group(2)), _t(m.group(3)), period, col))

    return ExamMatrix(title.group(0).replace(" FINAL EXAM MATRIX", ""), year, rows, common, special, issues)


def _split_common(label: str, exam: ExamPeriod) -> list[CommonExam]:
    """"ECE 3710 *online, MATH 1551" -> two entries; "MATH 1553/1554" -> one entry, two courses."""
    out = []
    for part in (p.strip() for p in label.split(",")):
        qualifier = None
        if "*" in part:
            part, q = part.split("*", 1)
            part, qualifier = part.strip(), q.strip() or None
        m = re.match(r"^([A-Z]{2,4}) ([\d/]+)$", part)
        if not m:
            raise ValueError(f"cannot parse common exam course list {label!r}")
        keys = tuple(f"{m.group(1)} {n}" for n in m.group(2).split("/"))
        out.append(CommonExam(part, keys, qualifier, exam))
    return out


def parse_matrix(pdf_path: Path) -> ExamMatrix:
    matrix = parse_fragments(extract_fragments(pdf_path))
    matrix.issues.extend(validate_matrix(matrix))
    return matrix


def validate_matrix(m: ExamMatrix) -> list[str]:
    """Structural checks; returns human-readable problems (empty = consistent)."""
    problems: list[str] = []
    if not m.rows:
        problems.append("no meeting-pattern rows parsed")
    seen: dict[tuple[str, time, time], ExamPeriod] = {}
    for r in m.rows:
        k = (r.days, r.class_start, r.class_end)
        if k in seen and seen[k] != r.exam:
            problems.append(f"pattern {k} maps to two exam periods")
        seen[k] = r.exam
        if r.class_end <= r.class_start:
            problems.append(f"row {r.pattern_key} ends before it starts")
    windows = m.windows
    durations = {(e.hour * 60 + e.minute) - (s.hour * 60 + s.minute) for s, e in windows}
    if len(durations) != 1:
        problems.append(f"exam windows have differing lengths: {windows}")
    special = {(s.period.date, s.period.start) for s in m.special_periods if s.kind == "reading"}
    for p in m.exam_periods:
        if (p.date, p.start) in special:
            problems.append(f"exam period {p.key} coincides with a reading period")
    if not any(s.kind == "conflict" for s in m.special_periods):
        problems.append("no conflict period found")
    return problems


def matrix_summary(m: ExamMatrix) -> dict:
    return {
        "rows": len(m.rows),
        "exam_periods": len(m.exam_periods),
        "common_exams": [asdict(c)["label"] for c in m.common_exams],
        "special_periods": len(m.special_periods),
        "issues": len(m.issues),
    }
