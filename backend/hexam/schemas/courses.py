"""Courses (catalog entries) and sections (term offerings with CRNs)."""

from __future__ import annotations

from datetime import time
from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, StringConstraints, field_validator, model_validator

from .base import (
    CourseId,
    Crn,
    ExamGroupId,
    HexamModel,
    Provenance,
    ProvenancedModel,
    SectionId,
    find_duplicates,
)


class Weekday(StrEnum):
    """Banner/OSCAR day codes (R = Thursday)."""

    MON = "M"
    TUE = "T"
    WED = "W"
    THU = "R"
    FRI = "F"
    SAT = "S"
    SUN = "U"


class SectionType(StrEnum):
    LECTURE = "lecture"
    LAB = "lab"
    RECITATION = "recitation"
    STUDIO = "studio"
    OTHER = "other"


class MeetingTime(HexamModel):
    """A weekly recurring class meeting, in campus wall-clock time."""

    days: list[Weekday] = Field(min_length=1)
    start_time: time
    end_time: time
    location: str | None = None

    @field_validator("start_time", "end_time")
    @classmethod
    def _naive_wall_clock(cls, v: time) -> time:
        if v.tzinfo is not None:
            raise ValueError("meeting times are campus wall-clock times; omit the UTC offset")
        return v

    @model_validator(mode="after")
    def _check(self) -> Self:
        if dupes := find_duplicates(self.days):
            raise ValueError(f"duplicate meeting days: {dupes}")
        if self.end_time <= self.start_time:
            raise ValueError("meeting end_time must be after start_time")
        return self

    def overlaps(self, other: MeetingTime) -> bool:
        return bool(set(self.days) & set(other.days)) and (
            self.start_time < other.end_time and other.start_time < self.end_time
        )

    @property
    def pattern_key(self) -> str:
        """Days + start, e.g. ``"MWF 09:30"``. Exam groups keyed on the full meeting
        time (``ExamGroup.meeting_pattern`` like ``"MWF 09:30-10:20"``) append the end."""
        return f"{''.join(d.value for d in self.days)} {self.start_time:%H:%M}"


class Course(ProvenancedModel):
    """A catalog course, independent of term/section, e.g. CS 1331."""

    course_id: CourseId
    subject: Annotated[str, StringConstraints(pattern=r"^[A-Z]{2,4}$")]
    number: Annotated[str, StringConstraints(pattern=r"^\d{4}[A-Z]?$")]
    title: str = Field(min_length=1)
    credit_hours: float | None = Field(default=None, ge=0, le=12)
    provenance: Provenance

    @model_validator(mode="after")
    def _id_matches_subject_number(self) -> Self:
        if self.course_id != f"{self.subject}-{self.number}":
            raise ValueError(
                f"course_id {self.course_id!r} must equal '{self.subject}-{self.number}'"
            )
        return self


class Section(ProvenancedModel):
    """One offering of a course in the dataset's term.

    ``exam_group_id`` is the single source of truth for which exam a section
    sits; ``None`` means the section has no scheduled final exam.
    ``enrollment`` is the (real or synthetic) headcount used for display and
    capacity planning; the solver uses actual student registrations.
    """

    section_id: SectionId
    course_id: CourseId
    section_code: Annotated[str, StringConstraints(pattern=r"^[A-Z0-9]{1,4}$")]
    crn: Crn | None = None
    section_type: SectionType = SectionType.LECTURE
    instructors: list[str] = Field(default_factory=list)
    enrollment: int = Field(ge=0)
    capacity: int | None = Field(default=None, ge=0)
    meeting_times: list[MeetingTime] = Field(default_factory=list)
    exam_group_id: ExamGroupId | None
    provenance: Provenance

    @model_validator(mode="after")
    def _check(self) -> Self:
        if dupes := find_duplicates(self.instructors):
            raise ValueError(f"duplicate instructors: {dupes}")
        for i, a in enumerate(self.meeting_times):
            for b in self.meeting_times[i + 1 :]:
                if a.overlaps(b):
                    raise ValueError(f"section meeting times overlap: {a.pattern_key} / {b.pattern_key}")
        return self

    @property
    def has_final_exam(self) -> bool:
        return self.exam_group_id is not None
