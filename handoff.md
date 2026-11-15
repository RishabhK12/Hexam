# Hexam — handoff

Status: 2026-11-15. **All five stages are implemented and verified end to end.** Branch `improve-algorithm`: removed the separate 3-per-day metric (merged into 3-in-24 h), split late→early into separate late-evening and early-morning metrics, and added a PDF comparison report (committed in 68de5ff). Committed in b8a5241: removed the Overview "exam load by day" and "concentration across periods" charts, the How it works tab and the synthetic-data banner, renamed "seats" to "students" in schedule cells and the Optimize cap, removed the "students worse off" box from the Overview, drew overlapping exams side by side in the Student impact calendars, and **went back to the single late-evening → early-morning pair metric** (`night_to_morning`, weight 10) instead of separate late/early metrics.

**2026-11-15, uncommitted:** added the **hybrid strategy** (now the default in the API and UI): a numpy move scorer and Kempe-chain simulated annealing (`backend/hexam/optimization/local_search.py`) run between the warm start and CP-SAT. See "Hybrid strategy results" below.

## Done

| Stage | Where | Verification |
|---|---|---|
| Pipeline check | `data/fall2026` | 1,000 synthetic students, 52 exam groups (5 fixed common), 21 official periods. Fixed 4 pre-existing failing tests: CRLF rewrote `data/raw/*.html` → added `.gitattributes`. |
| Evaluator | `backend/hexam/evaluation/` | `tests/test_evaluator.py` (27 tests: touching intervals, midnight, 2-day triples, shared groups, multi-section common exams, missing/invalid assignments, timezone, slot relations = evaluator) |
| CP-SAT optimizer | `backend/hexam/optimization/` | `tests/test_optimizer.py` (21 tests: exactly-once, fixed, restrictions, locks, capacity, per-day cap, infeasibility + diagnosis, relaxation, known optima, objective == evaluator, weight trade-offs, two-phase) |
| Move scorer + local search (hybrid strategy) | `backend/hexam/optimization/local_search.py`, wired in `solver.py` | `tests/test_local_search.py` (17 tests: score == evaluator on random schedules and the real dataset, move deltas == full re-scoring, compound-move scoring, Kempe chains never create conflicts, hard constraints block moves, same seed → same schedule, relaxed mode, hybrid end to end incl. infeasible input) |
| Comparison + integrity | `backend/hexam/services/` | `tests/test_integration_pipeline.py` (full dataset: baseline untouched, registrations unchanged, 9 integrity checks, genuine improvement) |
| FastAPI | `backend/hexam/api/` | `tests/test_api.py` (17 tests incl. 422/404/409, infeasible run, async job polling, PDF report content) |
| PDF report | `backend/hexam/services/report_service.py`, `GET /api/runs/{id}/report.pdf`, `frontend/src/components/ReportButton.tsx` | ReportLab; API test reads the PDF back with pypdf; pages checked visually; button verified in the browser |
| Frontend | `frontend/` | `src/__tests__/app.test.tsx` (13 vitest tests); `tsc` + `vite build` pass; verified live in a browser (load, run optimization, schedule modes, student impact) |
| Docs / scripts | `README.md`, `scripts/dev.ps1`, `scripts/dev.sh`, `scripts/precompute_demo.py` | — |

**Test results (last run, 2026-11-15):** backend `303 passed`; frontend `13 passed`; `tsc` and `vite build` OK. Counts are collected cases (parametrized cases count separately).

## Hybrid strategy results (2026-11-15, default weights, seed 0, 8 workers, real dataset)

| Run | Evaluated score | Notes |
|---|---|---|
| Single pass, 20 s | 19,265 | CP-SAT alone |
| Single pass, 120 s (the previous demo run) | 17,765 | CP-SAT alone |
| **Hybrid, 20 s** | **16,060 to 16,730** (three runs) | local search used about 10 s |
| **Hybrid, 120 s** | **16,060 and 15,705** (two runs; the second is the saved demo run) | local search stopped after 25 to 27 s |

- Every hybrid run passed all 9 integrity checks. Best seen from the search alone with longer budgets: 15,705.
- **CP-SAT did not improve the local-search schedule in any real-data run** (`cp_sat_improvement=0` in the model stats), even with 95 s left. Its job in hybrid is the feasible warm start, the hard-constraint check of the result, the lower bound (still about 1,470, so the gap stays large) and optimality proofs on small instances.
- Results vary run to run because the CP-SAT warm start is not deterministic (start 35,845 or 37,225). The local search itself is deterministic for a seed when it is not cut off by time.
- The 16,060 schedule has 54 students with 3 in 24 h (previous single-pass demo run: 66) and 60 with 4 in 48 h (73), but 193 students worse off than the baseline (previous demo run: 173). The saved hybrid demo run (15,705) has 166 worse off.
- Why Kempe chains: in strict mode only 145 of 940 single-group moves are legal on this data. Tabu search and annealing over single moves both stalled near 19,900; chains reached about 16,000 in 10 s (about 13,000 chain moves scored per second).
- Also checked on the real data at 12 s: relaxed mode (hybrid 17,115 vs single pass 20,320) and aggregate caps of 450 students / 4 groups per period (17,385 vs 20,930); infeasible inputs give the same status and hints in both strategies.
- The precomputed demo run (`data/fall2026/precomputed_run.json`) was regenerated with the hybrid default on 2026-11-15 and verified: it loads through `RunStore.load_precomputed` and re-scores to its stored penalty.

## Real results (precomputed demo run, `data/fall2026/precomputed_run.json`)
Refreshed 2026-11-15 with the **hybrid** strategy: default weights, 120 s limit, seed 0, 8 workers → **FEASIBLE** (not proven optimal), 120.1 s, objective 15,705, best bound 1,465. The local search reached 15,705 in 27 s; CP-SAT did not improve it. The previous single-pass demo run scored 17,765.

| Metric | Baseline → Optimized | Students affected |
|---|---|---|
| Simultaneous conflicts (pairs) | 30 → 0 | 30 → 0 |
| 3 in 24 h, incl. 3+ in one day (students) | 187 → 56 | 187 → 56 |
| Back-to-back (pairs) | 554 → 281 | 449 → 250 |
| 4 in 48 h (students) | 150 → 65 | 150 → 65 |
| Late evening → early morning (pairs) | 123 → 70 | 119 → 69 |
| **Weighted score** | **68,800 → 15,705 (−77.2 %)** | any issue: 534 → 323 |

433 students are better off, 166 worse off, 401 unchanged; 44 of 52 groups moved. Every metric improves.
For comparison, the separate per-exam metrics reached −65 % to −68 % and left "any issue" at 637 to 966 students.

## Possible next steps
- Reuse the move scorer (`MoveScorer.move_costs()` gives the full group × period cost matrix in about 3 ms) for live drag-and-drop scoring and a "best periods for this exam" heatmap.
- Remaining proposals from the paper comparison: do-no-harm fairness (cap students made worse off), change-budget frontier (at most k groups moved), held-out validation on a second synthetic population, difficulty-aware weights.
- The lower bound is still weak (1,465 against 15,705 in the saved run); nothing here improves it.
- Add a unit test that forces the "warm-start incumbent" fallback path (the main search returns UNKNOWN → the warm schedule is returned as FEASIBLE).
- Optional: code-split Recharts (about 526 kB minified, 151 kB gzipped).

## Key decisions / caveats
- **Metric units:** conflicts, back-to-back and late→early are *pairs*; 3/24h and 4/48h are *students*. Penalty = Σ weight × count.
- **Late→early is a pair metric** (`night_to_morning`): an exam starting ≥ 17:00, then one starting < 10:00 on a later day, break ≤ 14 h. Separate per-exam late-evening and early-morning metrics were tried and dropped: penalizing every 8 AM or 6 PM sitting made "any issue" nearly universal, and the user preferred the pair metric. In CP-SAT it is a pair indicator, like back-to-back.
- **No separate 3-per-day metric:** three exam starts on one calendar day always span < 24 h, so it was a subset of 3-in-24 h and double-counted.
  It was removed everywhere (schemas, evaluator, solver, API data, UI). The merged `three_in_24h` metric takes the old 3/day weight (100).
  The student page still names the heavy day ("3 exams within 24 hours (4 on Fri Dec 11)"). The optional *hard* per-day cap remains.
  Thresholds: back-to-back ≤ 30 min; late = start ≥ 17:00; early = start < 10:00; overnight gap ≤ 14 h.
- **Defaults:** hybrid strategy (API, UI and `SolverSettings`); strict mode on by default; weights in priority order (conflict 1000 > 3/24h 100 > b2b 30 > 4/48h 15 > late→early 10).
  `tests/test_dataset.py::test_weights_default_ordering` was updated to match.
- **The solver objective can exceed the evaluated score** before optimality is proven (indicators are only lower-bounded). Reported results always come from the evaluator;
  an integrity check enforces `objective ≥ evaluated`.
- **Warm start:** the baseline has 30 conflicts (infeasible in strict mode). A fast hard-constraints-only solve finds the closest feasible schedule; all variables are
  hinted from it. In hybrid the local search improves that schedule first, and CP-SAT is hinted with the result. If the main search finds nothing new in time, the starting schedule (a verified solution of the same model) is returned as FEASIBLE with a warning and no bound. If CP-SAT ends on a worse schedule than the one it started from, the starting schedule is returned.
- **The time limit is a wall-clock budget** for the whole run (warm start + build + search).
- **Not deterministic:** multi-worker CP-SAT under a time limit isn't reproducible run to run, and the local search stops on a wall clock. The precomputed run is saved and re-verified on load.
- **Capacity:** there is no real seat/room data; capacity is off by default, with optional *aggregate* caps clearly labeled.
- Pigeonhole capacity infeasibility can come back as UNKNOWN within short limits; the real status is returned with a pre-check hint that proves it.
- `data/sample/optimization_results.json` is a hand-written schema fixture, not optimizer output; its before/after evaluations were re-scored with the evaluator after the metric changes.
- **PDF report** uses ReportLab's built-in Helvetica (WinAnsi only), so report text avoids →, ≥, Σ; `_latin1()` sanitizes data strings. New dependency: `reportlab>=4.2`.
- **numpy** (`>=1.26`) is now an explicit dependency for the move scorer; it was already installed with OR-Tools.

## Commands
```bash
powershell -ExecutionPolicy Bypass -File scripts/dev.ps1                   # both servers (Windows)
cd backend && .venv/Scripts/python -m pytest -q                            # backend tests
cd backend && .venv/Scripts/python -m uvicorn hexam.api.main:app --port 8000
cd frontend && npm install && npm run dev                                  # http://localhost:5173
cd frontend && npm test && npm run build
backend/.venv/Scripts/python scripts/precompute_demo.py --time-limit 120
```
