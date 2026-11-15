"""PDF comparison report: official (baseline) exam times vs an optimized run.

Everything in the report comes from the stored run (its evaluator-scored
before/after evaluations, comparison and integrity checks) plus the dataset,
so the PDF shows the same numbers as the dashboard. Built with ReportLab's
standard fonts, which only cover Latin-1/WinAnsi: text sticks to those glyphs
(no arrows, ≥ or Σ).

    pdf_bytes = build_run_report(handle, run, artifacts)
"""

from __future__ import annotations

from datetime import datetime
from io import BytesIO

from reportlab.graphics.charts.barcharts import HorizontalBarChart, VerticalBarChart
from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    CondPageBreak,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from hexam.evaluation.rules import campus
from hexam.schemas import ImpactMetricKey, OptimizationRun, ScheduleEvaluation

from . import student_service
from .dataset_service import DatasetHandle
from .optimization_service import RunArtifacts

K = ImpactMetricKey

# Mirrors frontend/src/lib/format.ts METRICS (label, unit), in display order.
METRICS: list[tuple[ImpactMetricKey, str, str, str]] = [
    (K.SIMULTANEOUS_CONFLICTS, "Simultaneous conflicts", "exam pairs", "A student's two exams overlap in time."),
    (K.THREE_IN_24H, "3 exams within 24 h", "students",
     "Three exam starts inside a rolling window under 24 hours (includes 3+ exams on one day)."),
    (K.BACK_TO_BACK, "Back-to-back exams", "exam pairs", "Two exams separated by at most the back-to-back gap."),
    (K.FOUR_IN_48H, "4 exams within 48 h", "students", "Four exam starts inside a rolling window under 48 hours."),
    (K.NIGHT_TO_MORNING, "Late evening to early morning", "exam pairs",
     "An evening exam followed by an exam early the next morning (short overnight break)."),
]

# Same series colors as the dashboard.
BASE = colors.HexColor("#b8892a")
OPT = colors.HexColor("#2a5fa8")
INK = colors.HexColor("#111827")
MUTED = colors.HexColor("#5b6472")
LINE = colors.HexColor("#d9dde4")
RAISED = colors.HexColor("#f3f5f8")
GOOD = colors.HexColor("#1f7a4d")
BAD = colors.HexColor("#b42318")
MOVED_BG = colors.HexColor("#eaf1fb")

PAGE_W, PAGE_H = LETTER
MARGIN = 0.6 * inch
CONTENT_W = PAGE_W - 2 * MARGIN


# ------------------------------------------------------------------ format


def _num(v: float | int | None, digits: int = 0) -> str:
    if v is None:
        return "-"
    return f"{v:,.{digits}f}"


def _signed(v: float | int) -> str:
    return ("+" if v > 0 else "") + _num(v)


def _pct(p: float | None) -> str:
    return "n/a" if p is None else f"{'+' if p > 0 else ''}{p:.1f}%"


def _pct_change(before: float, after: float) -> float | None:
    return None if before == 0 else 100.0 * (after - before) / before


def _day(dt: datetime) -> str:
    return campus(dt).strftime("%a %b %d").replace(" 0", " ")


def _time(dt: datetime) -> str:
    return campus(dt).strftime("%I:%M %p").lstrip("0")


def _when(dt: datetime) -> str:
    return f"{_day(dt)}, {_time(dt)}"


def _change_color(before: float, after: float):
    return GOOD if after < before else BAD if after > before else MUTED


# ------------------------------------------------------------------ styles


def _styles() -> dict[str, ParagraphStyle]:
    ss = getSampleStyleSheet()
    base = ParagraphStyle("base", parent=ss["Normal"], fontName="Helvetica", fontSize=9, leading=12, textColor=INK)
    return {
        "base": base,
        "small": ParagraphStyle("small", parent=base, fontSize=7.5, leading=9.5, textColor=MUTED),
        "cell": ParagraphStyle("cell", parent=base, fontSize=7.5, leading=9),
        "title": ParagraphStyle("title", parent=base, fontName="Helvetica-Bold", fontSize=20, leading=24, spaceAfter=2),
        "subtitle": ParagraphStyle("subtitle", parent=base, fontSize=10.5, leading=14, textColor=MUTED, spaceAfter=10),
        "h2": ParagraphStyle("h2", parent=base, fontName="Helvetica-Bold", fontSize=12.5, leading=16,
                             spaceBefore=12, spaceAfter=6, alignment=TA_LEFT),
        "lead": ParagraphStyle("lead", parent=base, fontSize=11, leading=15, spaceAfter=8),
        "kpi_label": ParagraphStyle("kpi_label", parent=base, fontSize=7.5, leading=9, textColor=MUTED),
        "kpi_value": ParagraphStyle("kpi_value", parent=base, fontName="Helvetica-Bold", fontSize=15, leading=18),
        "kpi_sub": ParagraphStyle("kpi_sub", parent=base, fontSize=7.5, leading=9, textColor=MUTED),
    }


def _table(data, col_widths, *, header_rows: int = 1, align_right_from: int | None = None, extra=(), font_size=8):
    t = Table(data, colWidths=col_widths, repeatRows=header_rows)
    style = [
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), font_size),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("FONTNAME", (0, 0), (-1, header_rows - 1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, header_rows - 1), font_size - 0.5),
        ("TEXTCOLOR", (0, 0), (-1, header_rows - 1), MUTED),
        ("LINEBELOW", (0, header_rows - 1), (-1, header_rows - 1), 0.75, LINE),
        ("LINEBELOW", (0, header_rows), (-1, -1), 0.25, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    if align_right_from is not None:
        style.append(("ALIGN", (align_right_from, 0), (-1, -1), "RIGHT"))
    style.extend(extra)
    t.setStyle(TableStyle(style))
    return t


# ------------------------------------------------------------------ charts


def _legend(d: Drawing, x: float, y: float) -> None:
    for i, (label, c) in enumerate((("Baseline (official)", BASE), ("Optimized", OPT))):
        ox = x + i * 95
        d.add(Rect(ox, y, 7, 7, fillColor=c, strokeColor=None))
        d.add(String(ox + 10, y + 0.5, label, fontName="Helvetica", fontSize=7.5, fillColor=MUTED))


def _students_affected_chart(before: ScheduleEvaluation, after: ScheduleEvaluation) -> Drawing:
    h = 190
    d = Drawing(CONTENT_W, h)
    c = HorizontalBarChart()
    c.x, c.y, c.width, c.height = 118, 14, CONTENT_W - 140, h - 40
    labels = [m[1] for m in METRICS][::-1]  # first metric on top
    # series 0 is drawn lowest in each group, so optimized goes first to put baseline on top
    c.data = [
        [after.metrics.get(k).students_affected for k, *_ in METRICS][::-1],
        [before.metrics.get(k).students_affected for k, *_ in METRICS][::-1],
    ]
    c.categoryAxis.categoryNames = labels
    c.categoryAxis.labels.fontName = "Helvetica"
    c.categoryAxis.labels.fontSize = 7.5
    c.categoryAxis.labels.boxAnchor = "e"
    c.categoryAxis.labels.dx = -4
    c.categoryAxis.strokeColor = LINE
    c.categoryAxis.visibleTicks = False
    c.valueAxis.valueMin = 0
    c.valueAxis.labels.fontName = "Helvetica"
    c.valueAxis.labels.fontSize = 7
    c.valueAxis.labels.fillColor = MUTED
    c.valueAxis.strokeColor = LINE
    c.valueAxis.visibleGrid = True
    c.valueAxis.gridStrokeColor = LINE
    c.valueAxis.gridStrokeWidth = 0.4
    c.valueAxis.labelTextFormat = lambda v: f"{v:,.0f}"
    c.bars[0].fillColor = OPT
    c.bars[1].fillColor = BASE
    c.bars.strokeColor = None
    c.barSpacing = 1
    c.groupSpacing = 6
    c.barLabelFormat = "%d"
    c.barLabels.fontName = "Helvetica"
    c.barLabels.fontSize = 6.5
    c.barLabels.fillColor = MUTED
    c.barLabels.boxAnchor = "w"
    c.barLabels.dx = 2
    d.add(c)
    _legend(d, c.x, h - 12)
    return d


def _period_chart(rows: list[dict]) -> Drawing:
    """Students per official period, baseline vs optimized, with a day-grouped axis."""
    h = 215
    d = Drawing(CONTENT_W, h)
    c = VerticalBarChart()
    c.x, c.y, c.width, c.height = 36, 44, CONTENT_W - 44, h - 70
    c.data = [[r["base_students"] for r in rows], [r["opt_students"] for r in rows]]
    c.categoryAxis.categoryNames = [r["time_short"] for r in rows]
    c.categoryAxis.labels.fontName = "Helvetica"
    c.categoryAxis.labels.fontSize = 6.3
    c.categoryAxis.labels.fillColor = MUTED
    c.categoryAxis.labels.dy = -2
    c.categoryAxis.strokeColor = LINE
    c.categoryAxis.visibleTicks = False
    c.valueAxis.valueMin = 0
    c.valueAxis.labels.fontName = "Helvetica"
    c.valueAxis.labels.fontSize = 7
    c.valueAxis.labels.fillColor = MUTED
    c.valueAxis.strokeColor = None
    c.valueAxis.visibleGrid = True
    c.valueAxis.gridStrokeColor = LINE
    c.valueAxis.gridStrokeWidth = 0.4
    c.valueAxis.labelTextFormat = lambda v: f"{v:,.0f}"
    c.bars[0].fillColor = BASE
    c.bars[1].fillColor = OPT
    c.bars.strokeColor = None
    c.barSpacing = 0.5
    c.groupSpacing = 3
    d.add(c)

    # second tier: one label per day, centered under that day's periods, with dividers
    band = c.width / max(1, len(rows))
    groups: list[tuple[str, int, int]] = []
    for i, r in enumerate(rows):
        if groups and groups[-1][0] == r["day"]:
            groups[-1] = (r["day"], groups[-1][1], i)
        else:
            groups.append((r["day"], i, i))
    for gi, (day, s, e) in enumerate(groups):
        cx = c.x + band * (s + e + 1) / 2
        d.add(String(cx, c.y - 24, day, fontName="Helvetica-Bold", fontSize=7, fillColor=INK, textAnchor="middle"))
        if gi:
            x = c.x + band * s
            d.add(Line(x, c.y, x, c.y - 28, strokeColor=LINE, strokeWidth=0.6))
    _legend(d, c.x, h - 12)
    return d


# ------------------------------------------------------------------ report


def build_run_report(h: DatasetHandle, run: OptimizationRun, art: RunArtifacts) -> bytes:
    """Render the comparison report for a completed run with an optimized schedule."""
    if run.result is None or run.result.before is None or run.result.after is None or run.schedule is None:
        raise ValueError(f"run {run.run_id} has no optimized schedule to report on")
    if art is None or art.after is None:
        raise ValueError(f"run {run.run_id} has no evaluation artifacts")

    st = _styles()
    idx = h.index
    info = h.info
    res = run.result
    before, after = res.before, res.after
    cmp = run.comparison
    base_map = h.baseline_mapping
    new_map = run.schedule.as_mapping()
    slots = idx.timeslots
    weights = run.config.weights
    settings = run.config.evaluation
    generated = datetime.now(tz=campus(run.created_at).tzinfo)

    story: list = []

    # ---------------------------------------------------------------- header
    story.append(Paragraph("Final exam schedule comparison", st["title"]))
    story.append(Paragraph(
        f"{info.term or info.dataset_id} &middot; official exam matrix (baseline) vs Hexam optimized schedule", st["subtitle"]))

    pb, pa = before.weighted_penalty or 0, after.weighted_penalty or 0
    pct = cmp.penalty_percent_change if cmp else _pct_change(pb, pa)
    conflicts_note = (
        ", with zero simultaneous exam conflicts"
        if after.metrics.simultaneous_conflicts.count == 0 and before.metrics.simultaneous_conflicts.count > 0 else ""
    )
    verb = "lowered" if pa < pb else "raised" if pa > pb else "left unchanged"
    story.append(Paragraph(
        f"The optimized schedule {verb} total exam-week inconvenience by <b>{_pct(pct).lstrip('+-')}</b> "
        f"({_num(pb)} to {_num(pa)} points) for {_num(before.metrics.students_evaluated)} synthetic students{conflicts_note}. "
        "Both schedules are scored by the same independent evaluator, on the same students, with the same weights.",
        st["lead"]))

    # KPI strip
    def kpi(label: str, value: str, sub: str):
        return [Paragraph(label, st["kpi_label"]), Paragraph(value, st["kpi_value"]), Paragraph(sub, st["kpi_sub"])]

    kpis = [
        kpi("Inconvenience score", f"{_num(pb)} to {_num(pa)}", f"{_pct(pct)} (lower is better)"),
        kpi("Exam groups moved", f"{_num(cmp.exam_groups_moved) if cmp else '-'}", f"of {_num(len(idx.exam_groups))} exam groups"),
    ]
    kt = Table([kpis], colWidths=[CONTENT_W / 2] * 2)
    kt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), RAISED),
        ("BOX", (0, 0), (-1, -1), 0.5, LINE),
        ("LINEAFTER", (0, 0), (-2, -1), 0.5, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(kt)

    # ---------------------------------------------------------------- run details
    story.append(Paragraph("Run details", st["h2"]))
    flags = []
    if res.relaxed:
        flags.append("RELAXED (conflicts penalized, not forbidden)")
    if res.hypothetical:
        flags.append("HYPOTHETICAL (official common exams allowed to move)")
    bound = "-" if res.best_objective_bound is None else _num(res.best_objective_bound)
    details = [
        ["Run", run.run_id + (" (precomputed)" if run.precomputed else ""), "Solver status", res.status.value],
        ["Dataset", f"{info.dataset_id} (version {run.dataset_version[:12]})", "Runtime", f"{res.runtime_seconds:,.1f} s"],
        ["Run created", _when(run.created_at), "Objective / best bound", f"{_num(res.objective_value)} / {bound}"],
        ["Report generated", _when(generated), "Strategy", res.strategy.value.replace("_", " ")],
        ["Baseline", _baseline_label(info.baseline_provenance), "Mode", ", ".join(flags) or "strict (no conflicts allowed)"],
    ]
    details = [[Paragraph(_latin1(str(c)), st["cell"]) for c in row] for row in details]
    w4 = [0.95 * inch, CONTENT_W / 2 - 0.95 * inch, 1.35 * inch, CONTENT_W / 2 - 1.35 * inch]
    story.append(_table(details, w4, header_rows=0, extra=[
        ("TEXTCOLOR", (0, 0), (0, -1), MUTED), ("TEXTCOLOR", (2, 0), (2, -1), MUTED),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, LINE),
    ]))
    if res.message:
        story.append(Spacer(1, 4))
        story.append(Paragraph(_latin1(res.message), st["small"]))

    # ---------------------------------------------------------------- metrics
    story.append(Paragraph("All metrics, baseline vs optimized", st["h2"]))
    rows = [["Metric", "Unit", "Baseline", "Optimized", "Change", "%", "Students\nbaseline", "Students\noptimized", "Weight", "Penalty\nbaseline", "Penalty\noptimized"]]
    extra = []
    for i, (k, label, unit, _help) in enumerate(METRICS, start=1):
        b, a = before.metrics.get(k), after.metrics.get(k)
        rows.append([
            label, unit, _num(b.count), _num(a.count), _signed(a.count - b.count), _pct(_pct_change(b.count, a.count)),
            _num(b.students_affected), _num(a.students_affected), str(weights.get(k)),
            _num(before.penalty_breakdown.get(k, 0)), _num(after.penalty_breakdown.get(k, 0)),
        ])
        extra.append(("TEXTCOLOR", (4, i), (5, i), _change_color(b.count, a.count)))
    rows.append([
        "Total inconvenience score", "points", _num(pb), _num(pa), _signed(pa - pb), _pct(pct),
        _num(before.metrics.students_with_any_issue), _num(after.metrics.students_with_any_issue), "", _num(pb), _num(pa),
    ])
    last = len(rows) - 1
    extra += [
        ("FONTNAME", (0, last), (-1, last), "Helvetica-Bold"),
        ("BACKGROUND", (0, last), (-1, last), RAISED),
        ("TEXTCOLOR", (4, last), (5, last), _change_color(pb, pa)),
        ("FONTNAME", (3, 1), (3, last), "Helvetica-Bold"),
    ]
    cw = [1.45 * inch, 0.8 * inch] + [(CONTENT_W - 2.25 * inch) / 9] * 9
    story.append(_table(rows, cw, align_right_from=2, extra=extra, font_size=7.5))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        "Students columns count distinct students affected; the last row counts students with at least one issue. "
        "Penalty = weight x count, and the inconvenience score is the sum of the penalties.", st["small"]))

    story.append(KeepTogether([
        Paragraph("Students experiencing each problem", st["h2"]),
        _students_affected_chart(before, after),
    ]))

    # ---------------------------------------------------------------- periods
    story.append(CondPageBreak(3.6 * inch))  # keep the period chart with its heading
    story.append(Paragraph("Load across official exam periods", st["h2"]))
    tl_b = {t.timeslot_id: t for t in before.timeslot_loads}
    tl_a = {t.timeslot_id: t for t in after.timeslot_loads}
    period_rows = []
    for t_id in sorted(slots, key=lambda t: (slots[t].start, t)):
        s = slots[t_id]
        b, a = tl_b.get(t_id), tl_a.get(t_id)
        period_rows.append({
            "id": t_id, "day": _day(s.start), "time": f"{_time(s.start)} - {_time(s.end)}",
            "time_short": _time(s.start).replace(" AM", "a").replace(" PM", "p").replace(":00", ""),
            "base_groups": b.exam_group_count if b else 0, "opt_groups": a.exam_group_count if a else 0,
            "base_students": b.student_count if b else 0, "opt_students": a.student_count if a else 0,
        })
    story.append(Paragraph("Distinct students sitting an exam in each period, grouped by exam day.", st["small"]))
    story.append(_period_chart(period_rows))

    rows = [["Day", "Period", "Exam groups\nbaseline", "Exam groups\noptimized", "Students\nbaseline", "Students\noptimized", "Change"]]
    extra = []
    prev_day = None
    for i, r in enumerate(period_rows, start=1):
        rows.append([
            r["day"] if r["day"] != prev_day else "", r["time"], str(r["base_groups"]), str(r["opt_groups"]),
            _num(r["base_students"]), _num(r["opt_students"]), _signed(r["opt_students"] - r["base_students"]),
        ])
        if r["day"] != prev_day and prev_day is not None:
            extra.append(("LINEABOVE", (0, i), (-1, i), 0.75, LINE))
        prev_day = r["day"]
    cw = [1.0 * inch, 1.55 * inch] + [(CONTENT_W - 2.55 * inch) / 5] * 5
    story.append(_table(rows, cw, align_right_from=2, extra=extra, font_size=7.5))

    story.append(KeepTogether([
        Paragraph("Load by exam day", st["h2"]),
        _day_table(before, after),
    ]))

    # ---------------------------------------------------------------- schedule
    story.append(PageBreak())
    story.append(Paragraph("Exam times: official vs optimized", st["h2"]))
    story.append(Paragraph(
        "Every exam group, ordered by its official time. Highlighted rows were moved by the optimizer; "
        "official common exams stay fixed unless the run was hypothetical.", st["small"]))
    story.append(Spacer(1, 4))

    def slot_text(t_id: str | None) -> str:
        if t_id is None or t_id not in slots:
            return "unassigned"
        return _when(slots[t_id].start)

    def official_order(g: str) -> tuple[float, str]:
        t = base_map.get(g)
        return (slots[t].start.timestamp() if t in slots else float("inf"), g)

    groups = sorted((g for g in idx.exam_groups if idx.group_size(g) > 0 or g in base_map or g in new_map), key=official_order)
    rows = [["Exam group", "Courses", "Students", "Official time", "Optimized time", "Status"]]
    extra = []
    moved_n = 0
    for i, g in enumerate(groups, start=1):
        grp = idx.exam_groups[g]
        b_t, a_t = base_map.get(g), new_map.get(g)
        moved = b_t != a_t
        moved_n += moved
        fixed = grp.fixed_timeslot_id is not None
        status = "Moved" if moved else ("Fixed (common exam)" if fixed else "Unchanged")
        courses = ", ".join(c.replace("-", " ") for c in h.group_courses.get(g, []))
        rows.append([
            Paragraph(_latin1(grp.name), st["cell"]), Paragraph(_latin1(courses or "-"), st["cell"]), _num(idx.group_size(g)),
            slot_text(b_t), slot_text(a_t), status,
        ])
        if moved:
            extra += [
                ("BACKGROUND", (0, i), (-1, i), MOVED_BG),
                ("TEXTCOLOR", (4, i), (5, i), OPT),
                ("FONTNAME", (4, i), (5, i), "Helvetica-Bold"),
            ]
        else:
            extra.append(("TEXTCOLOR", (5, i), (5, i), MUTED))
    cw = [1.4 * inch, 1.65 * inch, 0.55 * inch, 1.3 * inch, 1.3 * inch, CONTENT_W - 6.2 * inch]
    sched_table = _table(rows, cw, extra=extra + [("ALIGN", (2, 0), (2, -1), "RIGHT")], font_size=7.3)
    story.append(sched_table)
    story.append(Spacer(1, 4))
    story.append(Paragraph(f"{moved_n} of {len(groups)} exam groups moved.", st["small"]))

    # ---------------------------------------------------------------- students
    story.append(PageBreak())
    story.append(Paragraph("Students most affected", st["h2"]))
    story.append(Paragraph(
        "Per-student inconvenience score (sum of weight x count over that student's own issues). "
        "Student IDs are synthetic.", st["small"]))
    for title, order in (("Most improved", "improved"), ("Most worsened", "worsened"), ("Highest remaining", "remaining")):
        items = student_service.ranking(h, art.before, art.after, order, 10)
        heading = Paragraph(title, ParagraphStyle("h3", parent=st["base"], fontName="Helvetica-Bold", spaceBefore=8, spaceAfter=4))
        if not items:
            story.append(KeepTogether([heading, Paragraph("None.", st["small"])]))
            continue
        rows = [["Student", "Major", "Year", "Score baseline", "Score optimized", "Change", "Issues after optimization"]]
        extra = []
        for i, it in enumerate(items, start=1):
            rows.append([
                it.student_id, it.major, str(it.year).replace("_", " "), _num(it.penalty_before), _num(it.penalty_after),
                _signed(it.change), Paragraph(_issue_summary(art.after.students[it.student_id]), st["cell"]),
            ])
            extra.append(("TEXTCOLOR", (5, i), (5, i), _change_color(it.penalty_before, it.penalty_after)))
        cw = [0.65 * inch, 0.5 * inch, 0.75 * inch, 0.75 * inch, 0.8 * inch, 0.5 * inch, CONTENT_W - 3.95 * inch]
        story.append(KeepTogether([heading, _table(rows, cw, extra=extra + [("ALIGN", (3, 0), (5, -1), "RIGHT")], font_size=7.3)]))

    # ---------------------------------------------------------------- integrity + notes
    story.append(CondPageBreak(2.2 * inch))
    story.append(Paragraph("Integrity checks", st["h2"]))
    if run.integrity and run.integrity.checks:
        rows = [["Check", "Result", "Detail"]]
        extra = []
        for i, c in enumerate(run.integrity.checks, start=1):
            rows.append([Paragraph(_latin1(c.name), st["cell"]), "PASS" if c.passed else "FAIL", Paragraph(_latin1(c.message), st["cell"])])
            extra += [("TEXTCOLOR", (1, i), (1, i), GOOD if c.passed else BAD), ("FONTNAME", (1, i), (1, i), "Helvetica-Bold")]
        story.append(_table(rows, [1.9 * inch, 0.55 * inch, CONTENT_W - 2.45 * inch], extra=extra, font_size=7.3))
    else:
        story.append(Paragraph("No integrity report stored for this run.", st["small"]))

    if res.warnings:
        story.append(Paragraph("Solver notes", st["h2"]))
        for w in res.warnings:
            story.append(Paragraph(f"&bull; {_latin1(w)}", st["small"]))

    story.append(Paragraph("Definitions", st["h2"]))
    rows = [["Metric", "Counted as", "Weight"]]
    for k, label, unit, help_ in METRICS:
        rows.append([label, Paragraph(f"{help_} Unit: {unit}.", st["cell"]), str(weights.get(k))])
    story.append(_table(rows, [1.6 * inch, CONTENT_W - 2.2 * inch, 0.6 * inch], extra=[("ALIGN", (2, 0), (2, -1), "RIGHT")], font_size=7.3))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        f"Thresholds: back-to-back gap up to {settings.back_to_back_max_gap_minutes} min; late to early = an exam starting at or after "
        f"{settings.evening_start.strftime('%I:%M %p').lstrip('0')}, then one starting before "
        f"{settings.morning_end.strftime('%I:%M %p').lstrip('0')} the next day, at most "
        f"{settings.overnight_max_gap_hours:g} h apart. All times America/New_York.", st["small"]))
    story.append(Spacer(1, 8))
    story.append(Paragraph(_latin1(info.synthetic_disclosure), st["small"]))

    buf = BytesIO()
    footer = f"Hexam comparison report  |  {info.term or info.dataset_id}  |  {run.run_id}"

    def on_page(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(MUTED)
        canvas.setStrokeColor(LINE)
        canvas.line(MARGIN, 0.45 * inch, PAGE_W - MARGIN, 0.45 * inch)
        canvas.drawString(MARGIN, 0.32 * inch, footer)
        canvas.drawRightString(PAGE_W - MARGIN, 0.32 * inch, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        buf, pagesize=LETTER, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN, bottomMargin=0.7 * inch,
        title=f"Hexam comparison report - {run.run_id}", author="Hexam", subject="Final exam schedule comparison",
    )
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue()


def _day_table(before: ScheduleEvaluation, after: ScheduleEvaluation) -> Table:
    a_by = {d.day: d for d in after.day_loads}
    rows = [["Day", "Exam groups\nbaseline", "Exam groups\noptimized", "Sittings\nbaseline", "Sittings\noptimized",
             "Students\nbaseline", "Students\noptimized"]]
    for d in before.day_loads:
        a = a_by.get(d.day)
        rows.append([
            d.day.strftime("%a %b %d").replace(" 0", " "), str(d.exam_group_count), str(a.exam_group_count if a else 0),
            _num(d.student_exam_count), _num(a.student_exam_count if a else 0),
            _num(d.student_count), _num(a.student_count if a else 0),
        ])
    cw = [1.2 * inch] + [(CONTENT_W - 1.2 * inch) / 6] * 6
    return _table(rows, cw, align_right_from=1, font_size=7.5)


def _issue_summary(d) -> str:
    parts = []
    names = {
        "simultaneous_conflicts": "conflict", "back_to_back": "back-to-back",
        "night_to_morning": "late to early", "three_in_24h": "3 in 24 h", "four_in_48h": "4 in 48 h",
    }
    for k, label in names.items():
        v = getattr(d, k)
        if v:
            parts.append(label if v == 1 and k in ("three_in_24h", "four_in_48h") else f"{v} {label}")
    return ", ".join(p.replace(" ", "&nbsp;") for p in parts) or "none"  # never break inside one issue


def _baseline_label(provenance: str | None) -> str:
    """First sentence of the dataset's baseline provenance, without its ``source:`` prefix."""
    if not provenance:
        return "Official exam matrix"
    text = provenance.split(":", 1)[-1].strip()
    return text.split(". ")[0].rstrip(".") + "."


def _latin1(text: str) -> str:
    """Replace glyphs the standard PDF fonts lack, then escape for Paragraph markup."""
    for a, b in (("→", "to"), ("≥", ">="), ("≤", "<="), ("−", "-"), ("Σ", "sum"), ("≫", ">>"), ("×", "x")):
        text = text.replace(a, b)
    text = text.encode("cp1252", "replace").decode("cp1252")
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
