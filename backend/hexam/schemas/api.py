"""Request/response envelopes for FastAPI endpoints.

Domain models (Dataset, ExamSchedule, OptimizationResult, ...) are returned
directly; these wrap only what doesn't map 1:1 onto a domain model.
"""

from __future__ import annotations

from collections import Counter
from typing import Self

from pydantic import Field, model_validator

from .base import ConfigId, DataSource, HexamModel, ScheduleId
from .dataset import Dataset
from .optimization import OptimizationConfig, OptimizationResult, PenaltyWeights
from .schedules import ExamAssignment, ExamSchedule


class DatasetSummary(HexamModel):
    """GET /dataset — counts and provenance mix for the dashboard header."""

    course_count: int
    section_count: int
    exam_group_count: int
    timeslot_count: int
    student_count: int
    provenance_breakdown: dict[str, dict[DataSource, int]] = Field(
        description="entity type -> data source -> record count"
    )

    @classmethod
    def from_dataset(cls, ds: Dataset) -> DatasetSummary:
        collections = {
            "course": ds.courses,
            "section": ds.sections,
            "exam_group": ds.exam_groups,
            "timeslot": ds.timeslots,
            "student": ds.students,
        }
        return cls(
            course_count=len(ds.courses),
            section_count=len(ds.sections),
            exam_group_count=len(ds.exam_groups),
            timeslot_count=len(ds.timeslots),
            student_count=len(ds.students),
            provenance_breakdown={
                name: dict(Counter(item.provenance.source for item in items))
                for name, items in collections.items()
            },
        )


class RunOptimizationRequest(HexamModel):
    """POST /optimize — pass a saved ``config_id`` or an inline ``config``."""

    config_id: ConfigId | None = None
    config: OptimizationConfig | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        if (self.config_id is None) == (self.config is None):
            raise ValueError("provide exactly one of config_id or config")
        return self


class RunOptimizationResponse(HexamModel):
    result: OptimizationResult
    schedule: ExamSchedule | None = Field(description="The optimized schedule, if one was found.")


class EvaluateScheduleRequest(HexamModel):
    """POST /evaluate — score a saved schedule or ad-hoc assignments (UI what-if)."""

    schedule_id: ScheduleId | None = None
    assignments: list[ExamAssignment] | None = None
    weights: PenaltyWeights | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        if (self.schedule_id is None) == (self.assignments is None):
            raise ValueError("provide exactly one of schedule_id or assignments")
        return self
