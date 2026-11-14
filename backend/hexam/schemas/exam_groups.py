"""Exam groups: the unit the solver schedules. All member sections share one slot."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from .base import ExamGroupId, Provenance, ProvenancedModel, TimeslotId, find_duplicates


class ExamGroupKind(StrEnum):
    MEETING_PATTERN = "meeting_pattern"  # GT default: exam slot keyed by class meeting time
    COMMON = "common"  # multi-section course(s) sharing a single common exam
    SPECIAL = "special"  # department/registrar-requested one-off grouping


class ExamGroup(ProvenancedModel):
    """Sections join a group via ``Section.exam_group_id``.

    Restrictions here are *data-level* rules that hold for every run:
    ``fixed_timeslot_id`` pins the group; ``allowed_timeslot_ids`` (``None`` =
    any slot) whitelists; ``forbidden_timeslot_ids`` blacklists. Per-run pins
    live in ``OptimizationConfig.fixed_assignments``.
    """

    exam_group_id: ExamGroupId
    name: str = Field(min_length=1)
    kind: ExamGroupKind
    meeting_pattern: str | None = Field(
        default=None, description="e.g. 'MWF 09:30-10:20' or 'MWF 09:30'; required for meeting_pattern groups."
    )
    description: str | None = None
    fixed_timeslot_id: TimeslotId | None = None
    allowed_timeslot_ids: list[TimeslotId] | None = None
    forbidden_timeslot_ids: list[TimeslotId] = Field(default_factory=list)
    provenance: Provenance

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.kind is ExamGroupKind.MEETING_PATTERN and not self.meeting_pattern:
            raise ValueError("meeting_pattern groups require meeting_pattern")
        if dupes := find_duplicates(self.forbidden_timeslot_ids):
            raise ValueError(f"duplicate forbidden_timeslot_ids: {dupes}")
        forbidden = set(self.forbidden_timeslot_ids)
        if self.allowed_timeslot_ids is not None:
            if not self.allowed_timeslot_ids:
                raise ValueError("allowed_timeslot_ids must be non-empty or null")
            if dupes := find_duplicates(self.allowed_timeslot_ids):
                raise ValueError(f"duplicate allowed_timeslot_ids: {dupes}")
            if both := sorted(forbidden & set(self.allowed_timeslot_ids)):
                raise ValueError(f"timeslots both allowed and forbidden: {both}")
        if self.fixed_timeslot_id is not None and not self.permits(self.fixed_timeslot_id):
            raise ValueError(
                f"fixed_timeslot_id {self.fixed_timeslot_id!r} violates the group's own restrictions"
            )
        return self

    def permits(self, timeslot_id: str) -> bool:
        """Whether allowed/forbidden lists permit this slot (ignores fixed pin)."""
        if timeslot_id in self.forbidden_timeslot_ids:
            return False
        return self.allowed_timeslot_ids is None or timeslot_id in self.allowed_timeslot_ids
