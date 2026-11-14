"""Synthetic student generator configuration (recorded in ``DatasetMeta``)."""

from __future__ import annotations

from typing import Self

from pydantic import Field, model_validator

from .base import CourseId, HexamModel, MajorCode, find_duplicates
from .students import ClassStanding


class MajorProfile(HexamModel):
    major: MajorCode
    weight: float = Field(gt=0, description="Relative share of generated students.")
    core_course_ids: list[CourseId] = Field(
        default_factory=list, description="Courses this major is likely to take."
    )


class SyntheticGenerationConfig(HexamModel):
    seed: int = Field(ge=0)
    num_students: int = Field(ge=1, le=100_000)
    majors: list[MajorProfile] = Field(min_length=1)
    year_distribution: dict[ClassStanding, float] = Field(
        default_factory=lambda: {
            ClassStanding.FIRST_YEAR: 0.27,
            ClassStanding.SECOND_YEAR: 0.25,
            ClassStanding.THIRD_YEAR: 0.23,
            ClassStanding.FOURTH_YEAR: 0.20,
            ClassStanding.FIFTH_YEAR_PLUS: 0.05,
        }
    )
    min_sections_per_student: int = Field(default=3, ge=1)
    max_sections_per_student: int = Field(default=6, ge=1)
    avoid_meeting_time_conflicts: bool = True

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.min_sections_per_student > self.max_sections_per_student:
            raise ValueError("min_sections_per_student exceeds max_sections_per_student")
        if dupes := find_duplicates([m.major for m in self.majors]):
            raise ValueError(f"duplicate majors: {dupes}")
        if any(v < 0 for v in self.year_distribution.values()) or sum(self.year_distribution.values()) <= 0:
            raise ValueError("year_distribution weights must be non-negative with a positive sum")
        return self
