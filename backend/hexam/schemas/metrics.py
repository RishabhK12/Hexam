"""Student-impact metrics and schedule evaluations.

Metric definitions (shared by evaluator, solver objective, and UI). For one
student, consider the timeslots holding their exams:

* ``simultaneous_conflicts`` — per (student, slot) with k ≥ 2 exams, adds k−1.
* ``back_to_back`` — per pair of consecutive slots on the same day both holding
  one of the student's exams.
* ``night_to_morning`` — per evening slot followed by the first slot of the next
  calendar day, both holding the student's exams.
* ``three_in_one_day`` — per (student, campus calendar day) with ≥ 3 exams.
* ``three_in_24h`` — 1 per student having 3 exams whose start times fall in a
  window shorter than 24 h (counted once per student, as in the paper).
* ``four_in_48h`` — 1 per student having 4 exams starting within < 48 h.

``count`` sums occurrences; ``students_affected`` counts distinct students.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from .base import CampusDateTime, HexamModel, ScheduleId, StudentId, TimeslotId, now_campus


class ImpactMetricKey(StrEnum):
    SIMULTANEOUS_CONFLICTS = "simultaneous_conflicts"
    BACK_TO_BACK = "back_to_back"
    NIGHT_TO_MORNING = "night_to_morning"
    THREE_IN_ONE_DAY = "three_in_one_day"
    THREE_IN_24H = "three_in_24h"
    FOUR_IN_48H = "four_in_48h"


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
    three_in_one_day: MetricValue
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
    three_in_one_day: int = Field(ge=0)
    three_in_24h: int = Field(ge=0, le=1)
    four_in_48h: int = Field(ge=0, le=1)


class TimeslotLoad(HexamModel):
    timeslot_id: TimeslotId
    exam_group_count: int = Field(ge=0)
    student_count: int = Field(ge=0, description="Distinct students with an exam in the slot.")
    over_capacity: bool = False


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
