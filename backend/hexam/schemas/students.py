"""Anonymous synthetic students and their section registrations."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from .base import (
    DataSource,
    MajorCode,
    Provenance,
    ProvenancedModel,
    SectionId,
    StudentId,
    find_duplicates,
)


class ClassStanding(StrEnum):
    FIRST_YEAR = "first_year"
    SECOND_YEAR = "second_year"
    THIRD_YEAR = "third_year"
    FOURTH_YEAR = "fourth_year"
    FIFTH_YEAR_PLUS = "fifth_year_plus"
    GRADUATE = "graduate"


class Student(ProvenancedModel):
    """A synthetic student. Exam groups are derived via ``section_ids`` →
    ``Section.exam_group_id``; they are never stored on the student."""

    student_id: StudentId
    major: MajorCode
    year: ClassStanding
    section_ids: list[SectionId] = Field(min_length=1)
    provenance: Provenance = Field(default_factory=Provenance.synthetic)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if dupes := find_duplicates(self.section_ids):
            raise ValueError(f"duplicate section registrations: {dupes}")
        if self.provenance.source is not DataSource.SYNTHETIC:
            raise ValueError("student records must be synthetic (no real student data)")
        return self
