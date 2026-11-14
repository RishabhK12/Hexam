"""Hexam data schemas. Import everything from here: ``from hexam.schemas import Section``."""

from .api import (
    DatasetSummary,
    EvaluateScheduleRequest,
    RunOptimizationRequest,
    RunOptimizationResponse,
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
