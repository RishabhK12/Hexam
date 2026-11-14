# Hexam — handoff

Status: 2026-11-14. **All five stages are implemented and verified end to end.** Branch `algorithm`; **nothing committed yet**.

## Done

| Stage | Where | Verification |
|---|---|---|
| Pipeline check | `data/fall2026` | 1,000 synthetic students, 52 exam groups (5 fixed common), 21 official periods. Fixed 4 pre-existing failing tests: CRLF rewrote `data/raw/*.html` → added `.gitattributes`. |
| Evaluator | `backend/hexam/evaluation/` | `tests/test_evaluator.py` (25 tests: touching intervals, midnight, 2-day triples, shared groups, multi-section common exams, missing/invalid assignments, timezone, slot relations = evaluator) |
| CP-SAT optimizer | `backend/hexam/optimization/` | `tests/test_optimizer.py` (20 tests: exactly-once, fixed, restrictions, locks, capacity, per-day cap, infeasibility + diagnosis, relaxation, known optima, objective == evaluator, weight trade-offs, two-phase) |
| Comparison + integrity | `backend/hexam/services/` | `tests/test_integration_pipeline.py` (full dataset: baseline untouched, registrations unchanged, 9 integrity checks, genuine improvement) |
| FastAPI | `backend/hexam/api/` | `tests/test_api.py` (16 tests incl. 422/404/409, infeasible run, async job polling) |
| Frontend | `frontend/` | `src/__tests__/app.test.tsx` (10 vitest tests); `tsc` + `vite build` pass; verified live in a browser (load, run optimization, schedule modes, student impact, methodology) |
| Docs / scripts | `README.md`, `scripts/dev.ps1`, `scripts/dev.sh`, `scripts/precompute_demo.py` | — |

**Test results (last run):** backend `281 passed`; frontend `10 passed`; production build OK.

## Real results (precomputed demo run, `data/fall2026/precomputed_run.json`)
Default weights, 120 s limit, seed 0, 8 workers → **FEASIBLE** (not proven optimal), 121.6 s, objective 16,675, best bound 1,430.

| Metric | Baseline → Optimized | Students affected |
|---|---|---|
| Simultaneous conflicts (pairs) | 30 → 0 | 30 → 0 |
| 3+ exams in a day (student-days) | 90 → 34 | 89 → 34 |
| Back-to-back (pairs) | 554 → 304 | 449 → 283 |
| 3 in 24 h (students) | 187 → 96 | 187 → 96 |
| 4 in 48 h (students) | 150 → 75 | 150 → 75 |
| Late → early (pairs) | 123 → 111 | 119 → 111 |
| **Weighted score** | **62,840 → 16,675 (−73.5 %)** | any issue: 534 → 379 |

424 students are better off, **221 worse off**, 355 unchanged; 46 of 52 groups moved. Shorter interactive runs reach roughly 16k–30k depending on the time limit and weights.

## Possible next steps
- Improve solution quality and the bound: symmetry breaking across same-time periods, LNS parameter tuning, or the two-phase heuristic seeded from the best run.
- Add a unit test that forces the "warm-start incumbent" fallback path (the main search returns UNKNOWN → the warm schedule is returned as FEASIBLE).
- Optional: code-split Recharts (about 526 kB minified, 151 kB gzipped).

## Key decisions / caveats
- **Metric units:** conflicts, back-to-back and late→early are *pairs*; 3/day is *student-days*; 3/24h and 4/48h are *students*. Penalty = Σ weight × count.
  Thresholds: back-to-back ≤ 30 min; late = start ≥ 17:00; early = start < 10:00; overnight gap ≤ 14 h.
- **Defaults changed:** strict mode on by default; weights re-ordered to the spec's priorities (conflict 1000 > 3/day 100 > b2b 30 > 3/24h 20 > 4/48h 15 > late→early 10).
  `tests/test_dataset.py::test_weights_default_ordering` was updated to match.
- **The solver objective can exceed the evaluated score** before optimality is proven (indicators are only lower-bounded). Reported results always come from the evaluator;
  an integrity check enforces `objective ≥ evaluated`.
- **Warm start:** the baseline has 30 conflicts (infeasible in strict mode). A fast hard-constraints-only solve finds the closest feasible schedule; all variables are
  hinted from it. If the main search finds nothing new in time, that warm schedule (a verified solution of the same model) is returned as FEASIBLE with a warning and no bound.
- **The time limit is a wall-clock budget** for the whole run (warm start + build + search).
- **Not deterministic:** multi-worker CP-SAT under a time limit isn't reproducible run to run. The precomputed run is saved and re-verified on load.
- **Capacity:** there is no real seat/room data; capacity is off by default, with optional *aggregate* caps clearly labeled.
- Pigeonhole capacity infeasibility can come back as UNKNOWN within short limits; the real status is returned with a pre-check hint that proves it.
- `data/sample/optimization_results.json` is a hand-written schema fixture, not optimizer output.

## Commands
```bash
powershell -ExecutionPolicy Bypass -File scripts/dev.ps1                   # both servers (Windows)
cd backend && .venv/Scripts/python -m pytest -q                            # backend tests
cd backend && .venv/Scripts/python -m uvicorn hexam.api.main:app --port 8000
cd frontend && npm install && npm run dev                                  # http://localhost:5173
cd frontend && npm test && npm run build
backend/.venv/Scripts/python scripts/precompute_demo.py --time-limit 120
```
