"""JSON file storage. One file per collection; each file is a JSON array
(``meta.json`` is a single object). Swap this module out if a DB is ever needed."""

from __future__ import annotations

from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, TypeAdapter

from .schemas import (
    Course,
    Dataset,
    DatasetMeta,
    ExamGroup,
    ExamSchedule,
    ExamTimeslot,
    OptimizationConfig,
    OptimizationResult,
    Section,
    Student,
    validate_dataset,
)

M = TypeVar("M", bound=BaseModel)

META_FILE = "meta.json"
DATASET_FILES: dict[str, tuple[str, type[BaseModel]]] = {
    "courses": ("courses.json", Course),
    "sections": ("sections.json", Section),
    "exam_groups": ("exam_groups.json", ExamGroup),
    "timeslots": ("timeslots.json", ExamTimeslot),
    "students": ("students.json", Student),
}
SCHEDULES_FILE = "schedules.json"
CONFIGS_FILE = "optimization_configs.json"
RESULTS_FILE = "optimization_results.json"


def read_models(path: Path, model: type[M]) -> list[M]:
    return TypeAdapter(list[model]).validate_json(path.read_bytes())  # type: ignore[valid-type]


def write_models(path: Path, items: list[M]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    model = type(items[0]) if items else BaseModel
    path.write_bytes(TypeAdapter(list[model]).dump_json(items, indent=2) + b"\n")  # type: ignore[valid-type]


def load_dataset(directory: str | Path, *, strict: bool = True) -> Dataset:
    """Load and parse a dataset. ``strict`` raises ``DatasetValidationError``
    on cross-record errors (warnings never raise)."""
    d = Path(directory)
    meta = DatasetMeta.model_validate_json((d / META_FILE).read_bytes())
    parts = {name: read_models(d / fname, model) for name, (fname, model) in DATASET_FILES.items()}
    ds = Dataset(meta=meta, **parts)
    if strict:
        validate_dataset(ds).raise_if_invalid()
    return ds


def save_dataset(ds: Dataset, directory: str | Path) -> None:
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    (d / META_FILE).write_text(ds.meta.model_dump_json(indent=2) + "\n", encoding="utf-8")
    for name, (fname, _) in DATASET_FILES.items():
        write_models(d / fname, getattr(ds, name))


def _load_optional(path: Path, model: type[M]) -> list[M]:
    return read_models(path, model) if path.exists() else []


def load_schedules(directory: str | Path) -> list[ExamSchedule]:
    return _load_optional(Path(directory) / SCHEDULES_FILE, ExamSchedule)


def load_configs(directory: str | Path) -> list[OptimizationConfig]:
    return _load_optional(Path(directory) / CONFIGS_FILE, OptimizationConfig)


def load_results(directory: str | Path) -> list[OptimizationResult]:
    return _load_optional(Path(directory) / RESULTS_FILE, OptimizationResult)


def save_schedules(items: list[ExamSchedule], directory: str | Path) -> None:
    write_models(Path(directory) / SCHEDULES_FILE, items)


def save_configs(items: list[OptimizationConfig], directory: str | Path) -> None:
    write_models(Path(directory) / CONFIGS_FILE, items)


def save_results(items: list[OptimizationResult], directory: str | Path) -> None:
    write_models(Path(directory) / RESULTS_FILE, items)
