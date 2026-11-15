# Hexam — handoff

Status: 2026-11-14. **All five stages are implemented and verified end to end.** Branch `improve-algorithm`: removed the separate 3-per-day metric (merged into 3-in-24 h), split late→early into separate late-evening and early-morning metrics, added a PDF comparison report, and gave the period-concentration chart a day-grouped x-axis; **not committed yet**.

## Done

| Stage | Where | Verification |
|---|---|---|
| Pipeline check | `data/fall2026` | 1,000 synthetic students, 52 exam groups (5 fixed common), 21 official periods. Fixed 4 pre-existing failing tests: CRLF rewrote `data/raw/*.html` → added `.gitattributes`. |
| Evaluator | `backend/hexam/evaluation/` | `tests/test_evaluator.py` (25 tests: touching intervals, midnight, 2-day triples, shared groups, multi-section common exams, missing/invalid assignments, timezone, slot relations = evaluator) |
| CP-SAT optimizer | `backend/hexam/optimization/` | `tests/test_optimizer.py` (20 tests: exactly-once, fixed, restrictions, locks, capacity, per-day cap, infeasibility + diagnosis, relaxation, known optima, objective == evaluator, weight trade-offs, two-phase) |
| Comparison + integrity | `backend/hexam/services/` | `tests/test_integration_pipeline.py` (full dataset: baseline untouched, registrations unchanged, 9 integrity checks, genuine improvement) |
| FastAPI | `backend/hexam/api/` | `tests/test_api.py` (17 tests incl. 422/404/409, infeasible run, async job polling, PDF report content) |
| PDF report | `backend/hexam/services/report_service.py`, `GET /api/runs/{id}/report.pdf`, `frontend/src/components/ReportButton.tsx` | ReportLab; API test reads the PDF back with pypdf; pages checked visually; button verified in the browser |
| Frontend | `frontend/` | `src/__tests__/app.test.tsx` (12 vitest tests); `tsc` + `vite build` pass; verified live in a browser (load, run optimization, schedule modes, student impact, methodology) |
| Docs / scripts | `README.md`, `scripts/dev.ps1`, `scripts/dev.sh`, `scripts/precompute_demo.py` | — |

**Test results (last run):** backend `285 passed`; frontend `12 passed`; `tsc` and production build OK.

## Real results (precomputed demo run, `data/fall2026/precomputed_run.json`)
Default weights, 120 s limit, seed 0, 8 workers → **FEASIBLE** (not proven optimal), 120.2 s, objective 25,863, best bound 6,688.

| Metric | Baseline → Optimized | Students affected |
|---|---|---|
| Simultaneous conflicts (pairs) | 30 → 0 | 30 → 0 |
| 3 in 24 h, incl. 3+ in one day (students) | 187 → 75 | 187 → 75 |
| Back-to-back (pairs) | 554 → 360 | 449 → 320 |
| 4 in 48 h (students) | 150 → 75 | 150 → 75 |
| Late-evening exams (student exams) | 1,401 → 1,567 | 844 → 857 |
| Early-morning exams (student exams) | 876 → 579 | 656 → 502 |
| **Weighted score** | **74,401 → 25,863 (−65.2 %)** | any issue: 985 → 966 |

529 students are better off, **290 worse off**, 181 unchanged; 39 of 52 groups moved.
"Any issue" is now near 100 % because almost every student has at least one 8 AM or 6 PM exam; the per-metric rows are more informative.

## Possible next steps
- Improve solution quality and the bound: symmetry breaking across same-time periods, LNS parameter tuning, or the two-phase heuristic seeded from the best run.
- Add a unit test that forces the "warm-start incumbent" fallback path (the main search returns UNKNOWN → the warm schedule is returned as FEASIBLE).
- Optional: code-split Recharts (about 526 kB minified, 151 kB gzipped).

## Key decisions / caveats
- **Metric units:** conflicts and back-to-back are *pairs*; 3/24h and 4/48h are *students*; late-evening and early-morning are *student exams* (each sitting counts once). Penalty = Σ weight × count.
- **Late-evening and early-morning are separate metrics** (replacing the old late→early *pair* metric), each with its own weight. They are linear in the CP-SAT model (weight × group size × x[g,t] for evening/early slots), so the solver term is exact. `overnight_max_gap_hours` was removed from `EvaluationSettings`. Sliders for these two are 0–50 in steps of 1 (they occur thousands of times).
- **No separate 3-per-day metric:** three exam starts on one calendar day always span < 24 h, so it was a subset of 3-in-24 h and double-counted.
  It was removed everywhere (schemas, evaluator, solver, API data, UI). The merged `three_in_24h` metric takes the old 3/day weight (100).
  The student page still names the heavy day ("3 exams within 24 hours (4 on Fri Dec 11)"). The optional *hard* per-day cap remains.
  Thresholds: back-to-back ≤ 30 min; late = start ≥ 17:00; early = start < 10:00.
- **Defaults:** strict mode on by default; weights in priority order (conflict 1000 > 3/24h 100 > b2b 30 > 4/48h 15 > late evening 3 = early morning 3).
  `tests/test_dataset.py::test_weights_default_ordering` was updated to match.
- **The solver objective can exceed the evaluated score** before optimality is proven (indicators are only lower-bounded). Reported results always come from the evaluator;
  an integrity check enforces `objective ≥ evaluated`.
- **Warm start:** the baseline has 30 conflicts (infeasible in strict mode). A fast hard-constraints-only solve finds the closest feasible schedule; all variables are
  hinted from it. If the main search finds nothing new in time, that warm schedule (a verified solution of the same model) is returned as FEASIBLE with a warning and no bound.
- **The time limit is a wall-clock budget** for the whole run (warm start + build + search).
- **Not deterministic:** multi-worker CP-SAT under a time limit isn't reproducible run to run. The precomputed run is saved and re-verified on load.
- **Capacity:** there is no real seat/room data; capacity is off by default, with optional *aggregate* caps clearly labeled.
- Pigeonhole capacity infeasibility can come back as UNKNOWN within short limits; the real status is returned with a pre-check hint that proves it.
- `data/sample/optimization_results.json` is a hand-written schema fixture, not optimizer output; its before/after evaluations were re-scored with the evaluator after the metric changes.
- **PDF report** uses ReportLab's built-in Helvetica (WinAnsi only), so report text avoids →, ≥, Σ; `_latin1()` sanitizes data strings. New dependency: `reportlab>=4.2`.

## Commands
```bash
powershell -ExecutionPolicy Bypass -File scripts/dev.ps1                   # both servers (Windows)
cd backend && .venv/Scripts/python -m pytest -q                            # backend tests
cd backend && .venv/Scripts/python -m uvicorn hexam.api.main:app --port 8000
cd frontend && npm install && npm run dev                                  # http://localhost:5173
cd frontend && npm test && npm run build
backend/.venv/Scripts/python scripts/precompute_demo.py --time-limit 120
```
