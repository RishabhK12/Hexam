# Hexam

Georgia Tech final exam schedule optimization: minimize student conflicts,
back-to-backs, and exam clusters (3/day, 3/24h, 4/48h) with OR-Tools CP-SAT.

```
backend/hexam/schemas/   Pydantic models + cross-record validation
backend/hexam/storage.py JSON load/save
backend/hexam/data_generation/  real GT data ingestion + synthetic students (docs/data_pipeline.md)
data/raw/                cached public sources (GT Scheduler/OSCAR, exam matrix, IRP)
data/fall2026/           real Fall 2026 subset + 1,000 synthetic students + matrix baseline
backend/tests/           schema tests (incl. Python↔TypeScript drift check)
frontend/src/types/      TypeScript mirror of the schemas
data/sample/             sample dataset, schedules, configs, results
docs/schemas.md          schema reference
```

## Backend setup

```bash
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"   # macOS/Linux: .venv/bin/python
.venv/Scripts/python -m pytest
```

Rebuild the Fall 2026 dataset from the cached snapshot (add `--fetch` to refresh sources):

```bash
.venv/Scripts/python -m hexam.data_generation.build
```

```python
from hexam.storage import load_dataset, load_schedules
from hexam.schemas import validate_schedule

ds = load_dataset("../data/sample")          # raises DatasetValidationError on bad refs
idx = ds.build_index()                       # lookups for solver/evaluator
for s in load_schedules("../data/sample"):
    print(s.schedule_id, validate_schedule(ds, s, index=idx).valid)
```
