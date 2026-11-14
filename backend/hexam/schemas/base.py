"""Shared primitives: base model, ID types, campus timezone handling, provenance.

Conventions used across every Hexam schema:

* IDs are stable, human-readable strings (no spaces). Relationships are always
  stored as an ID on exactly one side; reverse lookups come from ``DatasetIndex``.
* Absolute instants (exam start/end, run timestamps) are timezone-aware ISO 8601
  datetimes, normalized to America/New_York, e.g. ``2026-12-07T08:00:00-05:00``.
  Naive datetimes are rejected so DST can never be silently guessed.
* Recurring class meeting times are naive wall-clock ``HH:MM`` campus times.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Any, Final, Self
from zoneinfo import ZoneInfo

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

SCHEMA_VERSION: Final = "1.0"
CAMPUS_TZ_NAME: Final = "America/New_York"
CAMPUS_TZ: Final = ZoneInfo(CAMPUS_TZ_NAME)


class HexamModel(BaseModel):
    """Base for every schema: unknown keys are errors, strings are trimmed."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


# --------------------------------------------------------------------------- IDs

_GENERIC_ID = r"^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,63}$"

EntityId = Annotated[str, StringConstraints(pattern=_GENERIC_ID)]
"""Generic stable ID: letters, digits, ``_ . : -``; no spaces; max 64 chars."""

SectionId = EntityId  # convention: "<course_id>-<section_code>", e.g. "CS-1331-A"
ExamGroupId = EntityId  # convention: "EG-MWF-0930", "EG-COMMON-MATH-1554"
TimeslotId = EntityId  # convention: "TS-20261207-0800"
ScheduleId = EntityId
ConfigId = EntityId
RunId = EntityId
DatasetId = EntityId

CourseId = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2,4}-\d{4}[A-Z]?$")]
"""``<SUBJECT>-<NUMBER>``, e.g. ``CS-1331`` or ``CHEM-1211K``."""

StudentId = Annotated[str, StringConstraints(pattern=r"^S\d{6}$")]
"""Anonymous synthetic student ID, e.g. ``S000042``. Never a real GTID."""

Crn = Annotated[str, StringConstraints(pattern=r"^\d{5}$")]
"""Banner course reference number (5 digits, unique within a term)."""

TermCode = Annotated[str, StringConstraints(pattern=r"^\d{4}(02|05|08)$")]
"""GT term code: YYYY + 02 (Spring) / 05 (Summer) / 08 (Fall), e.g. ``202608``."""

MajorCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2,6}$")]


# --------------------------------------------------------------------- datetimes


def _to_campus_tz(value: datetime) -> datetime:
    return value.astimezone(CAMPUS_TZ)


CampusDateTime = Annotated[AwareDatetime, AfterValidator(_to_campus_tz)]
"""Aware datetime normalized to America/New_York. Naive input is rejected."""


def now_campus() -> datetime:
    return datetime.now(CAMPUS_TZ)


# -------------------------------------------------------------------- provenance


class DataSource(StrEnum):
    GT_PUBLIC = "gt_public"  # taken from public Georgia Tech data (catalog, OSCAR, registrar)
    SYNTHETIC = "synthetic"  # produced by the Hexam synthetic generator
    ASSUMED = "assumed"  # hand-entered assumption/placeholder pending real data


class Provenance(HexamModel):
    """Where a record came from. ``field_sources`` overrides ``source`` per field,
    e.g. a real course section (gt_public) whose ``enrollment`` is synthetic."""

    source: DataSource
    source_ref: str | None = Field(
        default=None, description="URL, file name, or generator identifier."
    )
    retrieved_at: CampusDateTime | None = None
    notes: str | None = None
    field_sources: dict[str, DataSource] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _public_data_must_cite_source(self) -> Self:
        if self.source is DataSource.GT_PUBLIC and not self.source_ref:
            raise ValueError("gt_public provenance requires source_ref")
        return self

    def source_for(self, field_name: str) -> DataSource:
        return self.field_sources.get(field_name, self.source)

    @classmethod
    def synthetic(cls, source_ref: str = "hexam-synthetic-generator", **kw: Any) -> Provenance:
        return cls(source=DataSource.SYNTHETIC, source_ref=source_ref, **kw)

    @classmethod
    def assumed(cls, notes: str | None = None, **kw: Any) -> Provenance:
        return cls(source=DataSource.ASSUMED, notes=notes, **kw)


class ProvenancedModel(HexamModel):
    """Base for records that carry provenance; checks ``field_sources`` keys.

    Subclasses must declare ``provenance: Provenance`` themselves, as their
    last field, so IDs come first in serialized JSON (Pydantic would otherwise
    put an inherited field first).
    """

    if TYPE_CHECKING:
        provenance: Provenance

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        if "provenance" not in cls.model_fields:
            raise TypeError(f"{cls.__name__} must declare a 'provenance: Provenance' field")

    @model_validator(mode="after")
    def _field_sources_reference_real_fields(self) -> Self:
        unknown = set(self.provenance.field_sources) - set(type(self).model_fields)
        if unknown:
            raise ValueError(f"provenance.field_sources has unknown fields: {sorted(unknown)}")
        return self


def find_duplicates(values: list[Any]) -> list[Any]:
    """Return values appearing more than once, in first-seen order."""
    seen: set[Any] = set()
    dupes: list[Any] = []
    for v in values:
        if v in seen and v not in dupes:
            dupes.append(v)
        seen.add(v)
    return dupes
