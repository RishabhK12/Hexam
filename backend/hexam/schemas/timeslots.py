"""Exam timeslots: concrete periods during finals week."""

from __future__ import annotations

from datetime import date
from typing import Final, Self

from pydantic import Field, model_validator

from .base import CampusDateTime, Provenance, ProvenancedModel, TimeslotId

EVENING_START_HOUR: Final = 17
"""Slots starting at/after 17:00 campus time count as evening (``ExamTimeslot.is_evening``). The
late→early metric uses ``EvaluationSettings.evening_start``, which has the same default."""


class ExamTimeslot(ProvenancedModel):
    """``start``/``end`` are aware ISO 8601 datetimes in America/New_York.

    Capacity limits are optional global per-slot student caps (not per-room
    capacity); ``None`` means unlimited.
    """

    timeslot_id: TimeslotId
    start: CampusDateTime
    end: CampusDateTime
    label: str | None = Field(default=None, description="Display label, e.g. 'Mon Dec 7, 8:00–10:50 AM'.")
    max_students: int | None = Field(default=None, ge=0)
    max_exam_groups: int | None = Field(default=None, ge=0)
    provenance: Provenance

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.end <= self.start:
            raise ValueError("timeslot end must be after start")
        if self.end.date() != self.start.date():
            raise ValueError("timeslot must start and end on the same campus calendar day")
        return self

    @property
    def local_date(self) -> date:
        return self.start.date()

    @property
    def duration_minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)

    @property
    def is_evening(self) -> bool:
        return self.start.hour >= EVENING_START_HOUR

    def overlaps(self, other: ExamTimeslot) -> bool:
        return self.start < other.end and other.start < self.end
