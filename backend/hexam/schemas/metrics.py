"""Student-impact metrics and schedule evaluations.

Metric definitions — the single contract shared by the evaluator
(``hexam.evaluation``), the CP-SAT objective (``hexam.optimization``) and the UI.
All times are actual exam start/end instants in America/New_York; a student's
exams are the *distinct* exam groups reached through their sections (two
sections in one common exam count once). Thresholds come from
``EvaluationSettings``.

=====================  ====================================================  ==========================
key                    counted as (unit of ``MetricValue.count``)            per-student detail
=====================  ====================================================  ==========================
simultaneous_conflicts exam *pairs* whose intervals overlap                  pairs
                       (``a.start < b.end and b.start < a.end``; touching
                       end==start is not a conflict)
back_to_back           non-overlapping exam *pairs* with
                       ``0 <= next.start - prev.end <= back_to_back_max_gap``  pairs
night_to_morning       exam *pairs* where ``prev`` starts at/after
                       ``evening_start``, ``next`` starts before
                       ``morning_end`` on a later campus date, and
                       ``next.start - prev.end <= overnight_max_gap``        pairs
three_in_24h           *students* with 3 exam starts inside a rolling
                       window shorter than 24 h (once per student). This
                       subsumes "3 exams in one calendar day": three starts
                       on one day always span < 24 h                          0/1
four_in_48h            *students* with 4 exam starts inside a rolling
                       window shorter than 48 h (once per student)            0/1
=====================  ====================================================  ==========================

Weighted penalty (identical in evaluator and solver)::

    penalty = Σ_k  weight_k × count_k        (k over ImpactMetricKey)

``count`` sums occurrences; ``students_affected`` counts distinct students.
"""

from __future__ import annotations

from datetime import date, time
from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from .base import CampusDateTime, ExamGroupId, HexamModel, ScheduleId, StudentId, TimeslotId, now_campus
from .validation import ValidationIssue


class ImpactMetricKey(StrEnum):
    SIMULTANEOUS_CONFLICTS = "simultaneous_conflicts"
    BACK_TO_BACK = "back_to_back"
    NIGHT_TO_MORNING = "night_to_morning"
    THREE_IN_24H = "three_in_24h"
    FOUR_IN_48H = "four_in_48h"


class EvaluationSettings(HexamModel):
    """Thresholds for the time-based metrics (all campus wall-clock / durations)."""

    back_to_back_max_gap_minutes: int = Field(
        default=30, ge=0, le=720,
        description="Two non-overlapping exams separated by at most this many minutes are back-to-back.",
    )
    evening_start: time = Field(default=time(17, 0), description="An exam starting at/after this is 'late'.")
    morning_end: time = Field(default=time(10, 0), description="An exam starting before this is 'early'.")
    overnight_max_gap_hours: float = Field(
        default=14.0, gt=0, le=48,
        description="Late→early pairs count only if the break (prev end → next start) is at most this long.",
    )


class MetricValue(HexamModel):
    count: int = Field(ge=0)
    students_affected: int = Field(ge=0)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.students_affected > self.count:
            raise ValueError("students_affected cannot exceed count")
        if self.count > 0 and self.students_affected == 0:
            raise ValueError("a non-zero count must affect at least one student")
        return self

    @classmethod
    def zero(cls) -> MetricValue:
        return cls(count=0, students_affected=0)


class StudentImpactMetrics(HexamModel):
    """Aggregate metrics for one schedule. Field names == ``ImpactMetricKey``."""

    students_evaluated: int = Field(ge=0)
    exam_enrollments: int = Field(ge=0, description="Sum over students of distinct exam groups.")
    students_with_any_issue: int = Field(ge=0)
    simultaneous_conflicts: MetricValue
    back_to_back: MetricValue
    night_to_morning: MetricValue
    three_in_24h: MetricValue
    four_in_48h: MetricValue

    @model_validator(mode="after")
    def _check(self) -> Self:
        affected = [self.get(k).students_affected for k in ImpactMetricKey]
        if max(affected) > self.students_evaluated:
            raise ValueError("a metric affects more students than were evaluated")
        if not max(affected) <= self.students_with_any_issue <= min(sum(affected), self.students_evaluated):
            raise ValueError("students_with_any_issue inconsistent with per-metric students_affected")
        return self

    def get(self, key: ImpactMetricKey | str) -> MetricValue:
        return getattr(self, ImpactMetricKey(key).value)


class StudentImpactDetail(HexamModel):
    """Per-student breakdown for UI drill-down (same counting rules as above)."""

    student_id: StudentId
    exam_count: int = Field(ge=0)
    exam_timeslot_ids: list[TimeslotId]
    simultaneous_conflicts: int = Field(ge=0)
    back_to_back: int = Field(ge=0)
    night_to_morning: int = Field(ge=0)
    three_in_24h: int = Field(ge=0, le=1)
    four_in_48h: int = Field(ge=0, le=1)
    exam_group_ids: list[ExamGroupId] = Field(default_factory=list)
    weighted_penalty: int = Field(default=0, ge=0, description="Σ weight × this student's counts.")


class TimeslotLoad(HexamModel):
    timeslot_id: TimeslotId
    exam_group_count: int = Field(ge=0)
    student_count: int = Field(ge=0, description="Distinct students with an exam in the slot.")
    over_capacity: bool = False


class DayLoad(HexamModel):
    day: date
    exam_group_count: int = Field(ge=0)
    student_exam_count: int = Field(ge=0, description="Student-exam sittings that day.")
    student_count: int = Field(ge=0, description="Distinct students with ≥ 1 exam that day.")


class EvaluationDiagnostics(HexamModel):
    unassigned_exam_group_ids: list[ExamGroupId] = Field(default_factory=list)
    students_with_unassigned_exams: int = Field(default=0, ge=0)
    three_in_24h_patterns: int = Field(default=0, ge=0, description="Distinct exam triples inside a < 24 h window.")
    four_in_48h_patterns: int = Field(default=0, ge=0, description="Distinct exam quadruples inside a < 48 h window.")
    max_exams_one_day: int = Field(default=0, ge=0)
    exam_count_histogram: dict[int, int] = Field(default_factory=dict, description="exams per student -> students")


class ScheduleEvaluation(HexamModel):
    """Evaluator output for one schedule."""

    schedule_id: ScheduleId
    evaluated_at: CampusDateTime = Field(default_factory=now_campus)
    metrics: StudentImpactMetrics
    weighted_penalty: float | None = Field(
        default=None, description="Σ weight × metric.count under the run's PenaltyWeights."
    )
    timeslot_loads: list[TimeslotLoad] = Field(default_factory=list)
    worst_students: list[StudentImpactDetail] = Field(
        default_factory=list, description="Most-impacted students, for drill-down."
    )
    penalty_breakdown: dict[ImpactMetricKey, int] = Field(
        default_factory=dict, description="weight_k × count_k per metric (sums to weighted_penalty)."
    )
    weights_used: dict[ImpactMetricKey, int] = Field(default_factory=dict)
    settings: EvaluationSettings | None = None
    valid: bool = Field(default=True, description="False when the schedule has errors (missing/invalid assignments).")
    issues: list[ValidationIssue] = Field(default_factory=list)
    day_loads: list[DayLoad] = Field(default_factory=list)
    diagnostics: EvaluationDiagnostics = Field(default_factory=EvaluationDiagnostics)


class MetricDelta(HexamModel):
    key: ImpactMetricKey
    before: int
    after: int
    change: int
    percent_change: float | None = Field(description="None when before == 0.")


def compare_metrics(before: StudentImpactMetrics, after: StudentImpactMetrics) -> list[MetricDelta]:
    """Before/after deltas on ``count`` for every metric key."""
    out = []
    for key in ImpactMetricKey:
        b, a = before.get(key).count, after.get(key).count
        out.append(
            MetricDelta(
                key=key,
                before=b,
                after=a,
                change=a - b,
                percent_change=None if b == 0 else round(100.0 * (a - b) / b, 2),
            )
        )
    return out
