"""Run the real optimizer once (default strategy: local search, then CP-SAT) and save the result as the dashboard's
precomputed demo run (``data/<dataset>/precomputed_run.json``).

The API loads it on startup only if the dataset version still matches and the
stored schedule re-scores to the stored penalty, so it cannot go stale or be
hand-edited. Usage (from the repo root):

    backend/.venv/Scripts/python scripts/precompute_demo.py --time-limit 60
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from hexam.schemas import OptimizationConfig, SolverSettings  # noqa: E402
from hexam.services.dataset_service import DatasetRegistry  # noqa: E402
from hexam.services.optimization_service import PRECOMPUTED_FILE, execute, new_run_id  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default="fall2026", help="dataset id under data/ (default: %(default)s)")
    ap.add_argument("--time-limit", type=float, default=60, help="solver time limit in seconds (default: %(default)s)")
    ap.add_argument("--seed", type=int, default=0, help="random seed for the solver (default: %(default)s)")
    ap.add_argument("--workers", type=int, default=8, help="CP-SAT worker threads (default: %(default)s)")
    args = ap.parse_args()

    h = DatasetRegistry().get(args.dataset)
    run_id = new_run_id()
    cfg = OptimizationConfig(
        config_id=f"cfg-{run_id}",
        name="Precomputed demo run (default weights)",
        baseline_schedule_id=h.baseline.schedule_id,
        solver=SolverSettings(time_limit_seconds=args.time_limit, num_workers=args.workers, random_seed=args.seed),
    )
    run, _ = execute(h, cfg, run_id)
    res = run.result
    print(f"status={res.status} runtime={res.runtime_seconds}s objective={res.objective_value} bound={res.best_objective_bound}")
    if run.schedule is None or not run.integrity.passed:
        print("No valid schedule; nothing saved.", [c.message for c in (run.integrity.checks if run.integrity else [])])
        return 1
    c = run.comparison
    print(f"penalty {c.penalty_before:.0f} -> {c.penalty_after:.0f} ({c.penalty_percent_change}%)")
    run = run.model_copy(update={
        "precomputed": True,
        "label": f"Precomputed demo run — real {cfg.solver.strategy.value.replace('_', ' ')} run, {args.time_limit:.0f}s limit, seed {args.seed}, default weights",
    })
    out = h.path / PRECOMPUTED_FILE
    out.write_text(run.model_dump_json(indent=1) + "\n", encoding="utf-8")
    print(f"saved {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
