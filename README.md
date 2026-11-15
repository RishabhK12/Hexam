# Hexam — Smarter Finals. Better Schedules.

Hexam re-assigns Georgia Tech final-exam groups to the official exam periods with a
**Kempe-chain local search followed by OR-Tools CP-SAT**, minimizing student congestion: overlapping exams, 3 exams in 24 h (which includes 3+ in one day),
back-to-back exams, 4 in 48 h, and a late-evening exam followed by an early-morning exam the next day.
It compares the result against the **official Fall 2026 exam matrix** using an
**independent evaluator**, on **1,000 synthetic students** built from public GT data.

> **Synthetic data.** Courses, sections and exam periods are real public Georgia Tech data.
> Every student and registration is synthetic. Hexam is a hackathon proof of concept, not
> an official Georgia Tech scheduling system.

```
backend/hexam/schemas/          Pydantic models (mirrored in frontend/src/types/hexam.ts)
backend/hexam/data_generation/  real GT data ingestion + synthetic students   (docs/data_pipeline.md)
backend/hexam/evaluation/       independent evaluator (pure Python, no solver)
backend/hexam/optimization/     optimizer (problem.py precompute, local_search.py move scorer + search, solver.py CP-SAT model)
backend/hexam/services/         dataset registry, optimization runs, integrity checks, comparison, student views
backend/hexam/api/              FastAPI app (thin routes)
frontend/                       React + TypeScript + Tailwind + Recharts dashboard (Vite)
data/fall2026/                  dataset, official-matrix baseline, precomputed demo run
scripts/                        dev startup, precomputed demo run
```

## Quick start

Requirements: **Python ≥ 3.11** (tested on 3.14) and **Node.js ≥ 18** (tested on 24). No API keys, database or auth.

```bash
# one command (Windows PowerShell)
powershell -ExecutionPolicy Bypass -File scripts/dev.ps1
# or macOS / Linux / Git Bash
./scripts/dev.sh
```

Then open **http://localhost:5173**. API docs are at http://127.0.0.1:8000/docs.

### Manual setup

```bash
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"        # macOS/Linux: .venv/bin/python
.venv/Scripts/python -m uvicorn hexam.api.main:app --port 8000
```

```bash
cd frontend
npm install
npm run dev            # http://localhost:5173; proxies /api to :8000 (override with HEXAM_API=...)
```

The dataset in `data/fall2026/` is committed and loads automatically. You don't need to
regenerate it, and the app never regenerates students when settings change. To rebuild it
from the cached public sources, see `docs/data_pipeline.md`.

## Demo flow

1. **Overview** shows the baseline (official matrix) and the precomputed run, if present.
2. Click **Run optimization**. The backend runs the optimizer for real (hybrid strategy, 20 s by default) and the
   dashboard updates with the status, runtime and independently evaluated metrics.
3. **Exam schedule** shows the timetable in Baseline, Optimized or Compare mode. Moved exams show where they came from.
4. **Student impact** opens the most-improved, most-worsened or highest-remaining students, or searches by synthetic ID,
   and shows before/after calendars and metrics.
5. **Download PDF report** (Overview, Optimize) saves a comparison report of the official exam times vs the optimized ones, with all the stats.
6. **Optimize** lets you change weights, the time limit, strategy, strict/relaxed mode, aggregate caps and locks, then re-run.

## Metrics (evaluator = source of truth)

All times are actual exam intervals in America/New_York. A student's exams are their
*distinct* exam groups, so two sections in one common exam count once.

| Metric | Counted as |
|---|---|
| Simultaneous conflicts | exam **pairs** whose intervals overlap (end == start is not a conflict) |
| Back-to-back | non-overlapping exam **pairs** with a break ≤ 30 min (configurable) |
| Late → early | exam **pairs**: one starting ≥ 17:00, then one starting < 10:00 the next day, break ≤ 14 h |
| 3 in 24 h | **students** with 3 exam starts within a rolling window < 24 h (once per student). Covers 3+ exams on one calendar day, which always span < 24 h |
| 4 in 48 h | **students** with 4 exam starts within a rolling window < 48 h (once per student) |

`penalty = Σ weight × count`. Default weights: conflict 1000 · 3/24 h 100 · back-to-back 30 ·
4/48 h 15 · late → early 10 (assumptions in the spec's priority order; adjustable in the UI).

## Optimization model (summary)

* `x[g,t] ∈ {0,1}` for each eligible (exam group, official period) pair: 52 groups × 21 periods, with the 5 common exams pinned.
* **Hard:** exactly one period per group; official common exams fixed (unless the labeled
  *hypothetical* mode is on); only permitted periods; **no student conflicts** (strict, the default) on the
  co-enrollment graph using real interval overlap; user locks; optional *aggregate* per-period caps.
* **Objective:** exactly the evaluator's penalty. Pairwise metrics use one Boolean per co-enrolled
  group pair, weighted by shared students. Rolling-window metrics use one Boolean per
  distinct student exam-set with cardinality constraints (about 974 sets for 1,000 students).
* **Warm start:** the official baseline has 30 student conflicts, so a quick hard-constraints-only
  solve first finds the feasible schedule closest to it and hints the main model.
* **Hybrid strategy** (default): after the warm start, a local search (`optimization/local_search.py`, numpy only)
  improves the schedule before CP-SAT sees it. It scores moves from the same group-level structures as the model,
  and its score equals the evaluator's exactly (tested). Under the strict no-conflict rule few single-exam moves are
  legal, so its move is a **Kempe chain**: move one exam group to another period and let the groups that would clash
  trade places between the two periods. Simulated annealing proposes chains in rounds; each round ends with a
  steepest descent over single moves, chains, whole-period swaps and whole-day swaps. CP-SAT is then started from
  that schedule, checks it against every hard constraint, keeps searching and reports the lower bound.
  The search may use up to half the time limit (60 s at most by default) and stops early after five rounds without progress.
* **Single pass** (optional) is CP-SAT alone on the full model, started from the warm schedule.
* **Relaxation mode** (optional) penalizes conflicts instead of forbidding them; results are labeled RELAXED.
* **Two-phase** (optional) places the largest groups first and then the rest. It never reports OPTIMAL or a global bound.
* **Infeasible:** the real CP-SAT status is returned with no schedule, plus diagnostics that re-solve with
  one constraint family removed at a time.

## API

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/health` | service status, OR-Tools version |
| GET | `/api/datasets`, `/api/datasets/{id}` | counts, term, provenance, dataset version, baseline info |
| GET | `/api/exams?course=&department=&kind=` | exam groups with courses, enrollment, restrictions, baseline slot |
| GET | `/api/slots` | official exam periods |
| GET | `/api/students?q=&major=&year=&course=&offset=&limit=` | paginated synthetic students |
| GET | `/api/students/{id}` | sections, courses, exam groups |
| GET | `/api/students/{id}/impact?run_id=` | baseline vs optimized calendar and metrics |
| GET | `/api/schedules/baseline` | official-matrix baseline |
| POST | `/api/evaluate` | score a stored schedule or ad-hoc assignments |
| POST | `/api/optimize` | start a run (202 + poll, or `"wait": true`) |
| GET | `/api/runs`, `/api/runs/{id}` | run list / full run (result, schedule, comparison, integrity) |
| GET | `/api/runs/{id}/students?order=improved\|worsened\|remaining` | per-student ranking |
| GET | `/api/runs/{id}/report.pdf` | PDF report: official vs optimized exam times, every metric, period/day loads, students most affected, integrity checks |
| GET / POST | `/api/compare` | comparison for a run, or for ad-hoc assignments |

Runs execute one at a time on a background worker (CP-SAT uses all cores) and are kept in memory.

## Tests

```bash
cd backend && .venv/Scripts/python -m pytest -q     # schemas, data pipeline, evaluator, local search, optimizer, API, full-dataset integration
cd frontend && npm test && npm run build            # vitest + tsc + production build
```
