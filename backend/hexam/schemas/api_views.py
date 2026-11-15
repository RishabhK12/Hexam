"""Response/request models for the Hexam REST API (``hexam.api``)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from .base import (
    CampusDateTime,
    CourseId,
    DataSource,
    EntityId,
    ExamGroupId,
    HexamModel,
    MajorCode,
    RunId,
    ScheduleId,
    StudentId,
    TimeslotId,
)
from .exam_groups import ExamGroupKind
from .metrics import EvaluationSettings, ImpactMetricKey, MetricDelta, ScheduleEvaluation, StudentImpactDetail
from .optimization import (
    FixedAssignment,
    HardConstraints,
    OptimizationConfig,
    OptimizationResult,
    OptimizationStrategy,
    PenaltyWeights,
)
from .schedules import ExamSchedule
from .students import ClassStanding


class DatasetInfo(HexamModel):
    """GET /api/datasets — one entry per data directory."""

    dataset_id: EntityId
    name: str
    term: str | None
    student_count: int
    exam_group_count: int
    timeslot_count: int
    course_count: int
    section_count: int
    has_baseline: bool
    baseline_schedule_id: ScheduleId | None
    baseline_provenance: str | None = Field(description="How the baseline was obtained (official vs assumed).")
    dataset_version: str = Field(description="Content hash of students + sections + exam groups + timeslots.")
    generation_seed: int | None
    provenance_breakdown: dict[str, dict[DataSource, int]]
    data_categories: dict[str, str] = Field(default_factory=dict, description="source -> what it covers")
    synthetic_disclosure: str
    exam_days: list[str] = Field(default_factory=list)


class ExamGroupView(HexamModel):
    """GET /api/exams — an exam group with its courses and baseline slot."""

    exam_group_id: ExamGroupId
    name: str
    kind: ExamGroupKind
    meeting_pattern: str | None
    description: str | None
    course_ids: list[CourseId]
    departments: list[str]
    section_count: int
    student_count: int
    fixed_timeslot_id: TimeslotId | None
    allowed_timeslot_ids: list[TimeslotId] | None
    forbidden_timeslot_ids: list[TimeslotId]
    baseline_timeslot_id: TimeslotId | None
    source: DataSource


class StudentSummary(HexamModel):
    student_id: StudentId
    major: MajorCode
    year: ClassStanding
    course_ids: list[CourseId]
    exam_group_ids: list[ExamGroupId]


class StudentPage(HexamModel):
    items: list[StudentSummary]
    total: int
    offset: int
    limit: int


class StudentSectionView(HexamModel):
    section_id: EntityId
    course_id: CourseId
    course_title: str
    section_code: str
    section_type: str
    meeting_summary: str
    exam_group_id: ExamGroupId | None


class StudentDetailView(HexamModel):
    """GET /api/students/{id} — synthetic student, never a real identity."""

    student_id: StudentId
    major: MajorCode
    year: ClassStanding
    sections: list[StudentSectionView]
    exam_group_ids: list[ExamGroupId]
    synthetic: bool = True


class StudentExamView(HexamModel):
    exam_group_id: ExamGroupId
    exam_group_name: str
    course_ids: list[CourseId]
    timeslot_id: TimeslotId | None
    start: CampusDateTime | None
    end: CampusDateTime | None
    fixed: bool
    moved: bool = Field(description="Slot differs from the baseline.")


class StudentScheduleImpact(HexamModel):
    schedule_id: ScheduleId
    exams: list[StudentExamView]
    detail: StudentImpactDetail
    findings: list[str] = Field(description="Human-readable issues, e.g. 3 exams on Mon Dec 14.")


class ImpactVerdict(StrEnum):
    IMPROVED = "improved"
    WORSENED = "worsened"
    UNCHANGED = "unchanged"
    NOT_AVAILABLE = "not_available"


class StudentImpactResponse(HexamModel):
    """GET /api/students/{id}/impact — baseline vs optimized calendar for one student."""

    student: StudentDetailView
    dataset_id: EntityId
    run_id: RunId | None
    baseline: StudentScheduleImpact
    optimized: StudentScheduleImpact | None
    penalty_change: int | None
    verdict: ImpactVerdict


class StudentRankItem(HexamModel):
    student_id: StudentId
    major: MajorCode
    year: ClassStanding
    penalty_before: int
    penalty_after: int
    change: int


class ScheduleComparison(HexamModel):
    """Baseline vs optimized, scored by the same evaluator, data and weights."""

    dataset_id: EntityId
    dataset_version: str
    before_schedule_id: ScheduleId
    after_schedule_id: ScheduleId
    weights: PenaltyWeights
    settings: EvaluationSettings
    count_deltas: list[MetricDelta]
    students_affected_deltas: list[MetricDelta]
    penalty_before: float
    penalty_after: float
    penalty_change: float
    penalty_percent_change: float | None = Field(description="None when the baseline penalty is 0.")
    students_with_any_issue_before: int
    students_with_any_issue_after: int
    students_improved: int
    students_worsened: int
    students_unchanged: int
    exam_groups_moved: int
    metrics_worsened: list[ImpactMetricKey] = Field(default_factory=list)


class IntegrityCheck(HexamModel):
    name: str
    passed: bool
    message: str


class IntegrityReport(HexamModel):
    passed: bool
    checks: list[IntegrityCheck]


class OptimizeRequest(HexamModel):
    """POST /api/optimize."""

    dataset_id: EntityId = "fall2026"
    weights: PenaltyWeights = Field(default_factory=PenaltyWeights)
    time_limit_seconds: float = Field(default=20.0, gt=0, le=300)
    random_seed: int = Field(default=0, ge=0)
    num_workers: int = Field(default=8, ge=1, le=32)
    strategy: OptimizationStrategy = OptimizationStrategy.HYBRID
    hard_constraints: HardConstraints = Field(default_factory=HardConstraints)
    evaluation: EvaluationSettings = Field(default_factory=EvaluationSettings)
    fixed_assignments: list[FixedAssignment] = Field(default_factory=list)
    excluded_timeslot_ids: list[TimeslotId] = Field(default_factory=list)
    use_baseline_hint: bool = True
    wait: bool = Field(default=False, description="Block until the run finishes (otherwise poll GET /api/runs/{id}).")


class RunState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class OptimizationRun(HexamModel):
    """A stored optimization run (in memory; the precomputed demo run is loaded from disk)."""

    run_id: RunId
    dataset_id: EntityId
    dataset_version: str
    state: RunState
    created_at: CampusDateTime
    finished_at: CampusDateTime | None = None
    precomputed: bool = False
    label: str | None = None
    config: OptimizationConfig
    result: OptimizationResult | None = None
    schedule: ExamSchedule | None = None
    comparison: ScheduleComparison | None = None
    integrity: IntegrityReport | None = None
    error: str | None = None


class RunSummary(HexamModel):
    run_id: RunId
    dataset_id: EntityId
    state: RunState
    created_at: CampusDateTime
    precomputed: bool
    label: str | None
    status: str | None
    runtime_seconds: float | None
    penalty_before: float | None
    penalty_after: float | None


class EvaluateResponse(HexamModel):
    evaluation: ScheduleEvaluation
    students: list[StudentImpactDetail] | None


class HealthResponse(HexamModel):
    status: str
    version: str
    ortools_version: str | None
    datasets: list[EntityId]
    active_runs: int


class CompareRequest(HexamModel):
    """POST /api/compare — compare two arbitrary assignments under one weight set."""

    dataset_id: EntityId = "fall2026"
    before_schedule_id: ScheduleId | None = Field(default=None, description="Defaults to the dataset baseline.")
    after_assignments: dict[ExamGroupId, TimeslotId]
    weights: PenaltyWeights = Field(default_factory=PenaltyWeights)
    settings: EvaluationSettings = Field(default_factory=EvaluationSettings)
