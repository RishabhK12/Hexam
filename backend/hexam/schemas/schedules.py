"""Exam schedules: a mapping of exam groups to timeslots."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from .base import (
    CampusDateTime,
    ExamGroupId,
    HexamModel,
    Provenance,
    ProvenancedModel,
    RunId,
    ScheduleId,
    TimeslotId,
    find_duplicates,
    now_campus,
)


class ScheduleKind(StrEnum):
    BASELINE = "baseline"  # GT's published/assumed schedule, the "before"
    OPTIMIZED = "optimized"  # produced by a solver run
    MANUAL = "manual"  # hand-edited in the UI


class ExamAssignment(HexamModel):
    exam_group_id: ExamGroupId
    timeslot_id: TimeslotId
    locked: bool = Field(default=False, description="Pinned by a fixed assignment or the user.")


class ExamSchedule(ProvenancedModel):
    """Each exam group may appear at most once (otherwise the schedule is
    self-conflicting). Completeness and restriction checks need the dataset —
    see ``validate_schedule``."""

    schedule_id: ScheduleId
    name: str = Field(min_length=1)
    kind: ScheduleKind
    assignments: list[ExamAssignment]
    created_at: CampusDateTime = Field(default_factory=now_campus)
    source_run_id: RunId | None = Field(
        default=None, description="OptimizationResult.run_id that produced this schedule."
    )
    provenance: Provenance

    @model_validator(mode="after")
    def _check(self) -> Self:
        if dupes := find_duplicates([a.exam_group_id for a in self.assignments]):
            raise ValueError(f"exam groups assigned more than once: {dupes}")
        if self.kind is ScheduleKind.OPTIMIZED and self.source_run_id is None:
            raise ValueError("optimized schedules must reference source_run_id")
        return self

    def as_mapping(self) -> dict[str, str]:
        """``{exam_group_id: timeslot_id}`` for the solver/evaluator."""
        return {a.exam_group_id: a.timeslot_id for a in self.assignments}
