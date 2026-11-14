# Hexam

Georgia Tech final exam schedule optimization: minimize student conflicts,
back-to-backs, and exam clusters (3/day, 3/24h, 4/48h) with OR-Tools CP-SAT.

```
backend/hexam/schemas/   Pydantic models + cross-record validation
backend/hexam/storage.py JSON load/save
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

```python
from hexam.storage import load_dataset, load_schedules
from hexam.schemas import validate_schedule

ds = load_dataset("../data/sample")          # raises DatasetValidationError on bad refs
idx = ds.build_index()                       # lookups for solver/evaluator
for s in load_schedules("../data/sample"):
    print(s.schedule_id, validate_schedule(ds, s, index=idx).valid)
```
