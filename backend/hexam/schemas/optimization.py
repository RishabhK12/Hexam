"""Optimization run configuration (input) and result (output)."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from .base import (
    CampusDateTime,
    ConfigId,
    ExamGroupId,
    HexamModel,
    RunId,
    ScheduleId,
    TimeslotId,
    find_duplicates,
    now_campus,
)
from .metrics import ImpactMetricKey, ScheduleEvaluation, StudentImpactMetrics


class PenaltyWeights(HexamModel):
    """Soft-constraint weights. Field names == ``ImpactMetricKey`` values.

    Integers because CP-SAT objectives require integer coefficients.
    Defaults are assumptions to tune (the paper does not publish its weights).
    """

    simultaneous_conflicts: int = Field(default=1000, ge=0)
    back_to_back: int = Field(default=10, ge=0)
    night_to_morning: int = Field(default=5, ge=0)
    three_in_one_day: int = Field(default=100, ge=0)
    three_in_24h: int = Field(default=50, ge=0)
    four_in_48h: int = Field(default=75, ge=0)

    def get(self, key: ImpactMetricKey | str) -> int:
        return getattr(self, ImpactMetricKey(key).value)

    def score(self, metrics: StudentImpactMetrics) -> int:
        return sum(self.get(k) * metrics.get(k).count for k in ImpactMetricKey)


class HardConstraints(HexamModel):
    forbid_simultaneous_conflicts: bool = Field(
        default=False,
        description="Treat unforced student conflicts as infeasible instead of penalized.",
    )
    max_exams_per_student_per_slot: int = Field(default=2, ge=1)
    max_exams_per_student_per_day: int | None = Field(default=None, ge=1)
    enforce_timeslot_capacity: bool = True
    enforce_group_restrictions: bool = Field(
        default=True, description="Honor ExamGroup fixed/allowed/forbidden timeslots."
    )


class SolverSettings(HexamModel):
    time_limit_seconds: float = Field(default=60.0, gt=0, le=3600)
    num_workers: int = Field(default=8, ge=1, le=64)
    random_seed: int = Field(default=0, ge=0)
    relative_gap_limit: float | None = Field(default=None, ge=0, le=1)
    use_baseline_hint: bool = Field(default=True, description="Warm-start CP-SAT from the baseline.")
    log_search_progress: bool = False


class FixedAssignment(HexamModel):
    exam_group_id: ExamGroupId
    timeslot_id: TimeslotId
    reason: str | None = None


class OptimizationConfig(HexamModel):
    config_id: ConfigId
    name: str = Field(min_length=1)
    description: str | None = None
    baseline_schedule_id: ScheduleId | None = None
    weights: PenaltyWeights = Field(default_factory=PenaltyWeights)
    hard_constraints: HardConstraints = Field(default_factory=HardConstraints)
    solver: SolverSettings = Field(default_factory=SolverSettings)
    fixed_assignments: list[FixedAssignment] = Field(default_factory=list)
    excluded_timeslot_ids: list[TimeslotId] = Field(
        default_factory=list, description="Slots unavailable for this run (what-if analysis)."
    )
    created_at: CampusDateTime = Field(default_factory=now_campus)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if dupes := find_duplicates([f.exam_group_id for f in self.fixed_assignments]):
            raise ValueError(f"exam groups fixed more than once: {dupes}")
        if dupes := find_duplicates(self.excluded_timeslot_ids):
            raise ValueError(f"duplicate excluded_timeslot_ids: {dupes}")
        excluded = set(self.excluded_timeslot_ids)
        if clash := [f.exam_group_id for f in self.fixed_assignments if f.timeslot_id in excluded]:
            raise ValueError(f"fixed assignments target excluded timeslots: {clash}")
        return self

    def fixed_mapping(self) -> dict[str, str]:
        return {f.exam_group_id: f.timeslot_id for f in self.fixed_assignments}


class SolverStatus(StrEnum):
    """CP-SAT status names, plus ERROR for exceptions outside the solver."""

    OPTIMAL = "OPTIMAL"
    FEASIBLE = "FEASIBLE"
    INFEASIBLE = "INFEASIBLE"
    MODEL_INVALID = "MODEL_INVALID"
    UNKNOWN = "UNKNOWN"
    ERROR = "ERROR"

    @property
    def has_solution(self) -> bool:
        return self in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)


class OptimizationResult(HexamModel):
    """One solver run. The produced schedule is stored separately as an
    ``ExamSchedule`` (kind=optimized) and referenced by ID."""

    run_id: RunId
    config_id: ConfigId
    status: SolverStatus
    started_at: CampusDateTime
    finished_at: CampusDateTime
    runtime_seconds: float = Field(ge=0)
    objective_value: float | None = None
    best_objective_bound: float | None = None
    objective_breakdown: dict[ImpactMetricKey, float] = Field(default_factory=dict)
    baseline_schedule_id: ScheduleId | None = None
    optimized_schedule_id: ScheduleId | None = None
    before: ScheduleEvaluation | None = None
    after: ScheduleEvaluation | None = None
    message: str | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.finished_at < self.started_at:
            raise ValueError("finished_at precedes started_at")
        if self.status.has_solution:
            if self.optimized_schedule_id is None or self.after is None:
                raise ValueError(f"{self.status} result needs optimized_schedule_id and after")
            if self.objective_value is None:
                raise ValueError(f"{self.status} result needs objective_value")
        elif self.optimized_schedule_id is not None or self.after is not None:
            raise ValueError(f"{self.status} result cannot carry an optimized schedule")
        if self.after and self.after.schedule_id != self.optimized_schedule_id:
            raise ValueError("after.schedule_id must equal optimized_schedule_id")
        if self.before:
            if self.baseline_schedule_id is None:
                raise ValueError("before evaluation requires baseline_schedule_id")
            if self.before.schedule_id != self.baseline_schedule_id:
                raise ValueError("before.schedule_id must equal baseline_schedule_id")
        return self

    @property
    def relative_gap(self) -> float | None:
        if self.objective_value is None or self.best_objective_bound is None:
            return None
        denom = max(abs(self.objective_value), 1e-9)
        return abs(self.objective_value - self.best_objective_bound) / denom
