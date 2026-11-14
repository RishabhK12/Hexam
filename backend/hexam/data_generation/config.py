"""Typed configuration for normalization and synthetic generation.

The default configuration lives in ``config/fall2026.json``. Everything in it
that is not read from a public source is an **assumption** and is labelled as
such in the outputs: curriculum weights, course-pair affinities, mutually
exclusive alternatives, course-load distribution, and headcounts for majors
the IRP engineering dashboard does not cover.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator

from ..schemas.base import HexamModel, MajorCode
from ..schemas.students import ClassStanding

DEFAULT_CONFIG = Path(__file__).with_name("config") / "fall2026.json"

YEARS: tuple[ClassStanding, ...] = (
    ClassStanding.FIRST_YEAR,
    ClassStanding.SECOND_YEAR,
    ClassStanding.THIRD_YEAR,
    ClassStanding.FOURTH_YEAR,
    ClassStanding.FIFTH_YEAR_PLUS,
)

YearWeights = list[float]
"""Five non-negative weights for years 1, 2, 3, 4, 5+."""


def _check_year_weights(name: str, w: list[float]) -> None:
    if len(w) != 5 or any(x < 0 for x in w):
        raise ValueError(f"{name}: expected 5 non-negative year weights, got {w}")


class ExamGroupGranularity(StrEnum):
    """How exam-bearing sections are bundled into exam groups (all non-common modes keep
    official common exams as one group per common-exam row).

    * ``meeting_time`` — Bucknell paper (arXiv 2509.11031, §3.1): one *course group* per
      common-exam course, every other section grouped by its regular meeting time
      (days + start + end), e.g. ``MWF 09:30-10:20``. Also exactly one GT matrix row.
    * ``meeting_pattern`` — coarser: days + start only (end added only where the GT
      matrix needs it); mirrors GT's blocks more closely, fewer groups.
    * ``course`` — finer: one group per (course, meeting time).
    """

    MEETING_TIME = "meeting_time"
    MEETING_PATTERN = "meeting_pattern"
    COURSE = "course"


class HeadcountSpec(HexamModel):
    """Where a major's relative size comes from."""

    source: Literal["irp_coe", "factbook_college", "assumed"]
    irp_major_name: str | None = None  # for irp_coe, e.g. "Mechanical Engineering"
    college: str | None = None  # for factbook_college, e.g. "College of Computing"
    share: float = Field(default=1.0, gt=0, le=1, description="assumed share of the college total")
    value: int | None = Field(default=None, gt=0, description="for source=assumed")
    notes: str | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        need = {"irp_coe": self.irp_major_name, "factbook_college": self.college, "assumed": self.value}[self.source]
        if need is None:
            raise ValueError(f"headcount source {self.source} is missing its required field")
        return self


class MajorConfig(HexamModel):
    name: str
    headcount: HeadcountSpec
    college: str = Field(description="OSCAR 'Colleges' restriction value, e.g. 'College of Engineering (E)'.")
    # OSCAR 'Majors' restriction values end in "(<code>)", e.g. "Computer Science (CS)"; matched by code.
    year_distribution: Literal["irp", "engineering_average"] = "irp"
    tracks: dict[str, float] = Field(default_factory=dict, description="track name -> multiplier")
    courses: dict[str, YearWeights] = Field(default_factory=dict, description="course key -> year weights")

    @model_validator(mode="after")
    def _check(self) -> Self:
        for c, w in self.courses.items():
            _check_year_weights(f"{self.name}/{c}", w)
        return self


class PairAffinity(HexamModel):
    a: str
    b: str
    factor: float = Field(gt=0)
    reason: str


class SubsetCourse(HexamModel):
    key: str  # crawler course key, e.g. "CS 1331"
    role: str  # short label: "foundation", "major_core", "upper_level", "gen_ed"


class GenerationConfig(HexamModel):
    term: str = "202608"
    dataset_name: str = "fall2026"
    exam_group_granularity: ExamGroupGranularity = ExamGroupGranularity.MEETING_TIME

    # --- subset / normalization
    courses: list[SubsetCourse] = Field(min_length=1)
    reserved_restriction_categories: list[str] = Field(
        default_factory=lambda: ["Cohorts", "Programs", "Degrees", "Special Approvals", "Concentrations", "Minors", "Attributes", "Departments"],
        description="Sections whose *allowed* list uses these categories are reserved for populations we do not model; they are excluded.",
    )
    min_exam_credit_hours: float = Field(default=2.0, description="Registrar: lecture courses of >= 2 credits get a matrix final.")

    # --- population
    num_students: int = Field(default=1000, ge=1, le=100_000)
    seed: int = Field(default=42, ge=0)
    majors: dict[MajorCode, MajorConfig] = Field(min_length=1)
    course_load_distribution: dict[int, float] = Field(
        default_factory=lambda: {3: 0.12, 4: 0.40, 5: 0.38, 6: 0.10},
        description="Number of subset courses per student (assumed).",
    )

    # --- course selection
    tracks: dict[str, dict[str, YearWeights]] = Field(default_factory=dict)
    level_suitability: dict[str, YearWeights] = Field(
        default_factory=lambda: {
            "1": [1.0, 0.8, 0.5, 0.4, 0.4],
            "2": [0.6, 1.0, 0.9, 0.6, 0.5],
            "3": [0.15, 0.6, 1.0, 0.9, 0.8],
            "4": [0.05, 0.25, 0.7, 1.0, 1.0],
        },
        description="Multiplier by course level (first digit of the number) and year.",
    )
    popularity_exponent: float = Field(
        default=0.5, ge=0, description="weight *= (number of real eligible sections) ** exponent"
    )
    prerequisite_same_term_factor: float = Field(
        default=0.05, ge=0, le=1, description="Applied when one course lists the other as a prerequisite (OSCAR data)."
    )
    exclusive_groups: list[list[str]] = Field(default_factory=list, description="Alternatives: at most one per student.")
    pair_affinities: list[PairAffinity] = Field(default_factory=list)

    # --- section assignment
    capacity_slack: float = Field(default=0.15, ge=0, description="Synthetic sample capacity = demand*(1+slack)/sections + 1.")
    max_replacement_attempts: int = Field(default=25, ge=0)
    min_courses_per_student: int = Field(default=3, ge=1)

    # --- diagnostics thresholds
    max_course_share_warning: float = Field(default=0.45, description="Warn if one course is taken by more than this share.")
    max_identical_schedule_share: float = Field(default=0.05, description="Warn if one exact course set exceeds this share.")

    @model_validator(mode="after")
    def _check(self) -> Self:
        keys = [c.key for c in self.courses]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate course keys in subset")
        known = set(keys)

        def chk(where: str, k: str) -> None:
            if k not in known:
                raise ValueError(f"{where} references {k!r}, which is not in the course subset")

        for t, cs in self.tracks.items():
            for k, w in cs.items():
                chk(f"track {t}", k)
                _check_year_weights(f"track {t}/{k}", w)
        for m, mc in self.majors.items():
            for t in mc.tracks:
                if t not in self.tracks:
                    raise ValueError(f"major {m} uses unknown track {t!r}")
            for k in mc.courses:
                chk(f"major {m}", k)
        for g in self.exclusive_groups:
            for k in g:
                chk("exclusive group", k)
        for p in self.pair_affinities:
            chk("pair affinity", p.a)
            chk("pair affinity", p.b)
        for lvl, w in self.level_suitability.items():
            _check_year_weights(f"level {lvl}", w)
        loads = self.course_load_distribution
        if not loads or min(loads) < self.min_courses_per_student or any(v < 0 for v in loads.values()):
            raise ValueError("course_load_distribution must use loads >= min_courses_per_student with non-negative weights")
        return self


def load_config(path: Path | None = None) -> GenerationConfig:
    return GenerationConfig.model_validate_json((path or DEFAULT_CONFIG).read_bytes())
