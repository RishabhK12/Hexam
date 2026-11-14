"""Filesystem layout of the data pipeline (relative to the repository root)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = REPO_ROOT / "data"
DEFAULT_TERM = "202608"


@dataclass(frozen=True)
class DataPaths:
    """``raw/`` = cached downloads; ``provenance/`` = source manifest;
    ``<dataset>/`` = a dataset directory loadable with ``hexam.storage.load_dataset``."""

    root: Path = DATA_DIR
    dataset_name: str = "fall2026"

    @property
    def raw(self) -> Path:
        return self.root / "raw"

    @property
    def provenance(self) -> Path:
        return self.root / "provenance"

    @property
    def sources_manifest(self) -> Path:
        return self.provenance / "sources.json"

    @property
    def dataset(self) -> Path:
        return self.root / self.dataset_name

    # Dataset-directory side files (not part of hexam.storage.DATASET_FILES)
    @property
    def exam_matrix(self) -> Path:
        return self.dataset / "exam_matrix.json"

    @property
    def section_metadata(self) -> Path:
        return self.dataset / "section_metadata.json"

    @property
    def course_metadata(self) -> Path:
        return self.dataset / "course_metadata.json"

    @property
    def normalization_report(self) -> Path:
        return self.dataset / "normalization_report.json"

    @property
    def generation_metadata(self) -> Path:
        return self.dataset / "generation_metadata.json"

    @property
    def population(self) -> Path:
        return self.dataset / "population.json"
