"""Hexam data schemas. Import everything from here: ``from hexam.schemas import Section``."""

from .api import (
    DatasetSummary,
    EvaluateScheduleRequest,
    RunOptimizationRequest,
    RunOptimizationResponse,
)
from .api_views import (
    CompareRequest,
    DatasetInfo,
    EvaluateResponse,
    ExamGroupView,
    HealthResponse,
    ImpactVerdict,
    IntegrityCheck,
    IntegrityReport,
    OptimizationRun,
    OptimizeRequest,
    RunState,
    RunSummary,
    ScheduleComparison,
    StudentDetailView,
    StudentExamView,
    StudentImpactResponse,
    StudentPage,
    StudentRankItem,
    StudentScheduleImpact,
    StudentSectionView,
    StudentSummary,
)
from .base import (
    CAMPUS_TZ,
    CAMPUS_TZ_NAME,
    SCHEMA_VERSION,
    CampusDateTime,
    DataSource,
    HexamModel,
    Provenance,
    ProvenancedModel,
    now_campus,
)
from .courses import Course, MeetingTime, Section, SectionType, Weekday
from .dataset import (
    Dataset,
    DatasetIndex,
    validate_config,
    validate_dataset,
    validate_schedule,
)
from .exam_groups import ExamGroup, ExamGroupKind
from .generation import MajorProfile, SyntheticGenerationConfig
from .metrics import (
    DayLoad,
    EvaluationDiagnostics,
    EvaluationSettings,
    ImpactMetricKey,
    MetricDelta,
    MetricValue,
    ScheduleEvaluation,
    StudentImpactDetail,
    StudentImpactMetrics,
    TimeslotLoad,
    compare_metrics,
)
from .optimization import (
    FixedAssignment,
    HardConstraints,
    OptimizationConfig,
    OptimizationResult,
    OptimizationStrategy,
    PenaltyWeights,
    SolverSettings,
    SolverStatus,
)
from .schedules import ExamAssignment, ExamSchedule, ScheduleKind
from .students import ClassStanding, Student
from .timeslots import EVENING_START_HOUR, ExamTimeslot
from .validation import DatasetValidationError, Severity, ValidationIssue, ValidationReport

import types as _types

__all__ = [n for n, v in dict(globals()).items() if not n.startswith("_") and not isinstance(v, _types.ModuleType)]
