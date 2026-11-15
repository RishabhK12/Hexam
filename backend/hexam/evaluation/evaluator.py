"""Independent exam-schedule evaluator (no OR-Tools).

Scores any schedule — baseline, optimized, or hand-edited — against the
dataset's synthetic registrations using the metric contract documented in
``hexam.schemas.metrics``. Deterministic: students, exams and pairs are
iterated in sorted order and nothing is randomized.

    ev = Evaluator(index)
    out = ev.evaluate(schedule, weights=PenaltyWeights())
    out.evaluation.weighted_penalty, out.students["S000001"]
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime

from hexam.schemas import (
    DatasetIndex,
    DayLoad,
    EvaluationDiagnostics,
    EvaluationSettings,
    ExamSchedule,
    ImpactMetricKey,
    MetricValue,
    PenaltyWeights,
    ScheduleEvaluation,
    StudentImpactDetail,
    StudentImpactMetrics,
    TimeslotLoad,
    ValidationReport,
)

from .rules import (
    WINDOW_24H,
    WINDOW_48H,
    campus_date,
    count_windows,
    is_back_to_back,
    is_early_morning,
    is_late_evening,
    overlaps,
)

K = ImpactMetricKey


@dataclass(frozen=True, slots=True)
class StudentExam:
    exam_group_id: str
    timeslot_id: str
    start: datetime
    end: datetime


@dataclass(frozen=True, slots=True)
class StudentFindings:
    """Concrete instances behind one student's counts (for UI explanations)."""

    conflict_pairs: list[tuple[str, str]] = field(default_factory=list)
    back_to_back_pairs: list[tuple[str, str]] = field(default_factory=list)
    late_exams: list[str] = field(default_factory=list)  # exam groups starting in the evening
    early_exams: list[str] = field(default_factory=list)  # exam groups starting early morning
    three_in_24h_patterns: int = 0
    four_in_48h_patterns: int = 0


@dataclass(slots=True)
class EvaluationOutcome:
    evaluation: ScheduleEvaluation
    students: dict[str, StudentImpactDetail]
    findings: dict[str, StudentFindings]
    exams: dict[str, list[StudentExam]]  # student -> chronologically sorted exams

    def penalty(self) -> int:
        return int(self.evaluation.weighted_penalty or 0)


def _schedule_mapping(schedule: ExamSchedule | Mapping[str, str]) -> tuple[str, dict[str, str]]:
    if isinstance(schedule, ExamSchedule):
        return schedule.schedule_id, schedule.as_mapping()
    return "adhoc", dict(schedule)


class Evaluator:
    """Holds the dataset index; evaluate many schedules cheaply."""

    def __init__(self, index: DatasetIndex, settings: EvaluationSettings | None = None):
        self.index = index
        self.settings = settings or EvaluationSettings()
        # Sorted for determinism. Distinct exam groups per student (dedupes a
        # student holding several sections of one common exam).
        self._students = sorted(index.student_exam_groups.items())

    # ------------------------------------------------------------------ main

    def evaluate(
        self,
        schedule: ExamSchedule | Mapping[str, str],
        *,
        weights: PenaltyWeights | None = None,
        schedule_id: str | None = None,
        worst_n: int = 25,
        require_complete: bool = True,
    ) -> EvaluationOutcome:
        idx, cfg = self.index, self.settings
        weights = weights or PenaltyWeights()
        sid, mapping = _schedule_mapping(schedule)
        sid = schedule_id or sid

        report = self._validate(mapping, sid, require_complete)
        unassigned = sorted(g for g in idx.exam_groups if g not in mapping and idx.group_size(g) > 0)

        totals: Counter[str] = Counter()
        affected: Counter[str] = Counter()
        any_issue = 0
        enrollments = 0
        students_missing = 0
        details: dict[str, StudentImpactDetail] = {}
        findings: dict[str, StudentFindings] = {}
        exams_by_student: dict[str, list[StudentExam]] = {}
        diag = Counter()
        max_day = 0
        hist: Counter[int] = Counter()

        slot_students: dict[str, set[str]] = defaultdict(set)
        day_students: dict[date, set[str]] = defaultdict(set)
        day_sittings: Counter[date] = Counter()

        for student_id, groups in self._students:
            exams: list[StudentExam] = []
            missing = False
            for g in groups:
                t_id = mapping.get(g)
                slot = idx.timeslots.get(t_id) if t_id is not None else None
                if slot is None:
                    missing = True
                    continue
                exams.append(StudentExam(g, t_id, slot.start, slot.end))
            exams.sort(key=lambda e: (e.start, e.end, e.exam_group_id))
            exams_by_student[student_id] = exams
            students_missing += missing
            enrollments += len(exams)
            hist[len(groups)] += 1
            for e in exams:
                slot_students[e.timeslot_id].add(student_id)
                d = campus_date(e.start)
                day_students[d].add(student_id)
                day_sittings[d] += 1

            counts, f = self._score_student(exams, cfg)
            for k, v in counts.items():
                totals[k] += v
                affected[k] += v > 0
            if any(counts.values()):
                any_issue += 1
            diag["p24"] += f.three_in_24h_patterns
            diag["p48"] += f.four_in_48h_patterns
            per_day = Counter(campus_date(e.start) for e in exams)
            max_day = max(max_day, max(per_day.values(), default=0))

            details[student_id] = StudentImpactDetail(
                student_id=student_id,
                exam_count=len(exams),
                exam_timeslot_ids=[e.timeslot_id for e in exams],
                exam_group_ids=[e.exam_group_id for e in exams],
                weighted_penalty=sum(weights.get(k) * v for k, v in counts.items()),
                **counts,
            )
            findings[student_id] = f

        metrics = StudentImpactMetrics(
            students_evaluated=len(self._students),
            exam_enrollments=enrollments,
            students_with_any_issue=any_issue,
            **{k.value: MetricValue(count=totals[k.value], students_affected=affected[k.value]) for k in K},
        )
        breakdown = {k: weights.get(k) * totals[k.value] for k in K}

        groups_per_slot = Counter(t for g, t in mapping.items() if g in idx.exam_groups)
        loads = []
        for t_id, t in idx.timeslots.items():
            n_students = len(slot_students.get(t_id, ()))
            over = (t.max_exam_groups is not None and groups_per_slot[t_id] > t.max_exam_groups) or (
                t.max_students is not None and n_students > t.max_students
            )
            loads.append(
                TimeslotLoad(
                    timeslot_id=t_id,
                    exam_group_count=groups_per_slot[t_id],
                    student_count=n_students,
                    over_capacity=over,
                )
            )
        all_days = sorted({campus_date(t.start) for t in idx.timeslots.values()})
        groups_per_day = Counter(
            campus_date(idx.timeslots[t].start) for g, t in mapping.items() if t in idx.timeslots and g in idx.exam_groups
        )
        day_loads = [
            DayLoad(
                day=d,
                exam_group_count=groups_per_day[d],
                student_exam_count=day_sittings[d],
                student_count=len(day_students.get(d, ())),
            )
            for d in all_days
        ]

        worst = sorted(details.values(), key=lambda d: (-d.weighted_penalty, d.student_id))
        worst = [d for d in worst if d.weighted_penalty > 0][:worst_n]

        evaluation = ScheduleEvaluation(
            schedule_id=sid,
            metrics=metrics,
            weighted_penalty=float(sum(breakdown.values())),
            timeslot_loads=loads,
            worst_students=worst,
            penalty_breakdown=breakdown,
            weights_used={k: weights.get(k) for k in K},
            settings=cfg,
            valid=report.valid,
            issues=report.issues,
            day_loads=day_loads,
            diagnostics=EvaluationDiagnostics(
                unassigned_exam_group_ids=unassigned,
                students_with_unassigned_exams=students_missing,
                three_in_24h_patterns=diag["p24"],
                four_in_48h_patterns=diag["p48"],
                max_exams_one_day=max_day,
                exam_count_histogram=dict(sorted(hist.items())),
            ),
        )
        return EvaluationOutcome(evaluation, details, findings, exams_by_student)

    # ------------------------------------------------------------- per student

    @staticmethod
    def _score_student(exams: list[StudentExam], cfg: EvaluationSettings) -> tuple[dict[str, int], StudentFindings]:
        """Counts for one student's chronologically sorted exams."""
        conflicts, b2b = [], []
        n = len(exams)
        for i in range(n):
            a = exams[i]
            for j in range(i + 1, n):
                b = exams[j]  # b.start >= a.start
                if overlaps(a.start, a.end, b.start, b.end):
                    conflicts.append((a.exam_group_id, b.exam_group_id))
                    continue
                first, second = (a, b) if a.end <= b.start else (b, a)
                if is_back_to_back(first.end, second.start, cfg):
                    b2b.append((first.exam_group_id, second.exam_group_id))

        late = [e.exam_group_id for e in exams if is_late_evening(e.start, cfg)]
        early = [e.exam_group_id for e in exams if is_early_morning(e.start, cfg)]
        starts = [e.start for e in exams]
        hit24, p24 = count_windows(starts, 3, WINDOW_24H)
        hit48, p48 = count_windows(starts, 4, WINDOW_48H)

        counts = {
            K.SIMULTANEOUS_CONFLICTS.value: len(conflicts),
            K.BACK_TO_BACK.value: len(b2b),
            K.LATE_EVENING.value: len(late),
            K.EARLY_MORNING.value: len(early),
            K.THREE_IN_24H.value: int(hit24),
            K.FOUR_IN_48H.value: int(hit48),
        }
        return counts, StudentFindings(conflicts, b2b, late, early, p24, p48)

    # -------------------------------------------------------------- validity

    def _validate(self, mapping: dict[str, str], sid: str, require_complete: bool) -> ValidationReport:
        idx = self.index
        r = ValidationReport()
        for g, t in sorted(mapping.items()):
            grp = idx.exam_groups.get(g)
            if grp is None:
                r.error("missing_reference", f"schedule assigns unknown exam group {g}", "schedule", sid)
            if t not in idx.timeslots:
                r.error("missing_reference", f"{g} is assigned to unknown timeslot {t}", "schedule", sid)
                continue
            if grp is None:
                continue
            if grp.fixed_timeslot_id is not None and t != grp.fixed_timeslot_id:
                r.warning(
                    "fixed_exam_moved",
                    f"{g} is officially fixed to {grp.fixed_timeslot_id} but scheduled in {t}",
                    "schedule", sid,
                )
            elif not grp.permits(t):
                r.error("restriction_violation", f"{g} may not be scheduled in {t}", "schedule", sid)
        for g in sorted(idx.exam_groups):
            if g not in mapping and idx.sections_by_group.get(g):
                msg = f"exam group {g} is not assigned a timeslot"
                if require_complete and idx.group_size(g) > 0:
                    r.error("unassigned_exam_group", msg, "schedule", sid)
                else:
                    r.warning("unassigned_exam_group", msg, "schedule", sid)
        return r


def evaluate_schedule(
    index: DatasetIndex,
    schedule: ExamSchedule | Mapping[str, str],
    *,
    weights: PenaltyWeights | None = None,
    settings: EvaluationSettings | None = None,
    **kw,
) -> EvaluationOutcome:
    return Evaluator(index, settings).evaluate(schedule, weights=weights, **kw)
