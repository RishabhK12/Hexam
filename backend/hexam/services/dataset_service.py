"""Loads datasets once and caches the derived index, baseline and evaluator."""

from __future__ import annotations

import hashlib
import json
import threading
from collections import Counter
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

from hexam.evaluation import Evaluator
from hexam.evaluation.rules import campus_date
from hexam.schemas import (
    Dataset,
    DatasetIndex,
    DatasetInfo,
    DatasetSummary,
    EvaluationSettings,
    ExamSchedule,
    ScheduleKind,
)
from hexam.storage import DATASET_FILES, load_dataset, load_schedules

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / "data"

SYNTHETIC_DISCLOSURE = (
    "All student records and course registrations are SYNTHETIC, generated from public Georgia Tech "
    "course, section and enrollment statistics. No real student data is used, and results describe this "
    "synthetic sample, not actual Georgia Tech students. Hexam is a proof of concept, not an official "
    "Georgia Tech scheduling system."
)


class DatasetNotFound(KeyError):
    pass


@dataclass
class DatasetHandle:
    dataset_id: str
    path: Path
    dataset: Dataset
    index: DatasetIndex
    baseline: ExamSchedule | None
    metadata: dict
    _evaluators: dict[str, Evaluator] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def evaluator(self, settings: EvaluationSettings | None = None) -> Evaluator:
        settings = settings or EvaluationSettings()
        key = settings.model_dump_json()
        with self._lock:
            if key not in self._evaluators:
                self._evaluators[key] = Evaluator(self.index, settings)
            return self._evaluators[key]

    @cached_property
    def version(self) -> str:
        """Content hash of the data the evaluator and optimizer consume."""
        h = hashlib.sha256()
        for name in ("students", "sections", "exam_groups", "timeslots"):
            h.update((self.path / DATASET_FILES[name][0]).read_bytes())
        return h.hexdigest()[:16]

    @cached_property
    def info(self) -> DatasetInfo:
        ds = self.dataset
        summary = DatasetSummary.from_dataset(ds)
        meta = self.metadata
        term = meta.get("term")
        term_name = {"202608": "Fall 2026", "202602": "Spring 2026", "202605": "Summer 2026"}.get(term or "", term)
        base_note = None
        if self.baseline:
            base_note = f"{self.baseline.provenance.source.value}: {self.baseline.provenance.notes or self.baseline.name}"
        days = sorted({campus_date(t.start).isoformat() for t in ds.timeslots})
        return DatasetInfo(
            dataset_id=self.dataset_id,
            name=f"Georgia Tech {term_name} finals (synthetic students)" if term_name else self.dataset_id,
            term=term_name,
            student_count=len(ds.students),
            exam_group_count=len(ds.exam_groups),
            timeslot_count=len(ds.timeslots),
            course_count=len(ds.courses),
            section_count=len(ds.sections),
            has_baseline=self.baseline is not None,
            baseline_schedule_id=self.baseline.schedule_id if self.baseline else None,
            baseline_provenance=base_note,
            dataset_version=self.version,
            generation_seed=meta.get("seed"),
            provenance_breakdown=summary.provenance_breakdown,
            data_categories=meta.get("data_categories", {}),
            synthetic_disclosure=SYNTHETIC_DISCLOSURE + (f" {meta['sample_note']}" if meta.get("sample_note") else ""),
            exam_days=days,
        )

    @cached_property
    def group_courses(self) -> dict[str, list[str]]:
        idx = self.index
        return {
            g: sorted({idx.sections[s].course_id for s in idx.sections_by_group.get(g, [])})
            for g in idx.exam_groups
        }

    @cached_property
    def baseline_mapping(self) -> dict[str, str]:
        return self.baseline.as_mapping() if self.baseline else {}

    def student_course_ids(self, student_id: str) -> list[str]:
        st = self.index.students[student_id]
        return sorted({self.index.sections[s].course_id for s in st.section_ids if s in self.index.sections})

    def department_counts(self) -> Counter[str]:
        return Counter(c.subject for c in self.dataset.courses)


class DatasetRegistry:
    """Discovers ``data/<id>/`` directories holding a full dataset."""

    def __init__(self, root: Path = DATA_ROOT):
        self.root = root
        self._handles: dict[str, DatasetHandle] = {}
        self._lock = threading.Lock()

    def available(self) -> list[str]:
        if not self.root.exists():
            return []
        need = [f for f, _ in DATASET_FILES.values()]
        ids = [d.name for d in sorted(self.root.iterdir()) if d.is_dir() and all((d / f).exists() for f in need)]
        # the real-data dataset first (default)
        return sorted(ids, key=lambda i: (i != "fall2026", i))

    def get(self, dataset_id: str) -> DatasetHandle:
        with self._lock:
            if dataset_id in self._handles:
                return self._handles[dataset_id]
            if dataset_id not in self.available():
                raise DatasetNotFound(dataset_id)
            path = self.root / dataset_id
            ds = load_dataset(path)  # strict cross-record validation
            schedules = load_schedules(path)
            baseline = next((s for s in schedules if s.kind is ScheduleKind.BASELINE), None)
            meta_path = path / "generation_metadata.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
            h = DatasetHandle(dataset_id, path, ds, ds.build_index(), baseline, meta)
            self._handles[dataset_id] = h
            return h
