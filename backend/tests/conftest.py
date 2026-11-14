from pathlib import Path

import pytest

from hexam.schemas import Dataset, Provenance
from hexam.storage import load_configs, load_dataset, load_results, load_schedules

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DIR = REPO_ROOT / "data" / "sample"


@pytest.fixture(scope="session")
def sample_dir() -> Path:
    return SAMPLE_DIR


@pytest.fixture(scope="session")
def _sample_dataset() -> Dataset:
    return load_dataset(SAMPLE_DIR)


@pytest.fixture
def ds(_sample_dataset: Dataset) -> Dataset:
    """Fresh deep copy of the sample dataset, safe to mutate."""
    return _sample_dataset.model_copy(deep=True)


@pytest.fixture(scope="session")
def schedules():
    return {s.schedule_id: s for s in load_schedules(SAMPLE_DIR)}


@pytest.fixture(scope="session")
def configs():
    return {c.config_id: c for c in load_configs(SAMPLE_DIR)}


@pytest.fixture(scope="session")
def results():
    return {r.run_id: r for r in load_results(SAMPLE_DIR)}


@pytest.fixture
def syn() -> Provenance:
    return Provenance.synthetic("test")
