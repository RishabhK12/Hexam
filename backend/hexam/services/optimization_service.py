"""Runs the optimizer, evaluates independently, verifies, compares, and stores runs.

Runs execute one at a time on a single worker thread (CP-SAT already uses
every core; serializing also keeps shared state consistent). Results are held
in memory, keyed by run ID; a validated precomputed demo run is loaded from
``data/<dataset>/precomputed_run.json`` when present.
"""

from __future__ import annotations

import logging
import secrets
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from hexam.evaluation import EvaluationOutcome
from hexam.optimization import Problem, solve
from hexam.schemas import (
    ExamAssignment,
    ExamSchedule,
    ImpactMetricKey,
    IntegrityCheck,
    OptimizationConfig,
    OptimizationResult,
    OptimizationRun,
    OptimizeRequest,
    Provenance,
    RunState,
    RunSummary,
    ScheduleKind,
    SolverSettings,
    SolverStatus,
    now_campus,
)

from .comparison_service import compare
from .dataset_service import DatasetHandle, DatasetRegistry
from .integrity import registrations_fingerprint, verify_schedule

log = logging.getLogger("hexam.optimization")
PRECOMPUTED_FILE = "precomputed_run.json"


class InvalidRequest(ValueError):
    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


def new_run_id() -> str:
    return f"run-{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"


def build_config(req: OptimizeRequest, handle: DatasetHandle, run_id: str) -> OptimizationConfig:
    return OptimizationConfig(
        config_id=f"cfg-{run_id}",
        name=f"API run {run_id}",
        baseline_schedule_id=handle.baseline.schedule_id if handle.baseline else None,
        weights=req.weights,
        hard_constraints=req.hard_constraints,
        solver=SolverSettings(
            time_limit_seconds=req.time_limit_seconds,
            num_workers=req.num_workers,
            random_seed=req.random_seed,
            strategy=req.strategy,
            use_baseline_hint=req.use_baseline_hint,
        ),
        evaluation=req.evaluation,
        fixed_assignments=req.fixed_assignments,
        excluded_timeslot_ids=req.excluded_timeslot_ids,
    )


def validate_request(handle: DatasetHandle, config: OptimizationConfig) -> Problem:
    """Reject malformed locks/references before any solver time is spent."""
    idx = handle.index
    problems = [f"unknown timeslot {t} in excluded_timeslot_ids" for t in config.excluded_timeslot_ids if t not in idx.timeslots]
    prob = Problem.build(idx, config)
    problems += [i.message for i in prob.issues if i.category == "locks"]
    if problems:
        raise InvalidRequest(sorted(set(problems)))
    return prob


@dataclass
class RunArtifacts:
    before: EvaluationOutcome
    after: EvaluationOutcome | None


def _message(status: str, relaxed: bool, hypothetical: bool, strategy: str) -> str:
    prefix = ("HYPOTHETICAL — " if hypothetical else "") + ("RELAXED — " if relaxed else "")
    return prefix + {
        "OPTIMAL": "Optimal schedule found and proven optimal by CP-SAT for this model.",
        "FEASIBLE": (
            "Feasible schedule found (two-phase heuristic; not a global optimum)."
            if strategy == "two_phase"
            else "Feasible schedule found; the time limit was reached before optimality was proven."
        ),
        "INFEASIBLE": "No schedule satisfies all hard constraints. No optimized schedule was produced.",
        "UNKNOWN": "The solver found no schedule within the time limit. No optimized schedule was produced.",
        "MODEL_INVALID": "The model was rejected by CP-SAT. No optimized schedule was produced.",
    }.get(status, status)


def execute(handle: DatasetHandle, config: OptimizationConfig, run_id: str, prob: Problem | None = None) -> tuple[OptimizationRun, RunArtifacts]:
    """Synchronous run: solve → independent evaluation → integrity → comparison."""
    if handle.baseline is None:
        raise InvalidRequest([f"dataset {handle.dataset_id} has no baseline schedule"])
    created = now_campus()
    idx = handle.index
    fp_before = registrations_fingerprint(idx)
    prob = prob or validate_request(handle, config)
    evaluator = handle.evaluator(config.evaluation)
    base_map = handle.baseline_mapping
    before = evaluator.evaluate(handle.baseline, weights=config.weights)

    started = now_campus()
    out = solve(prob, config, hint=base_map)
    finished = now_campus()

    status = SolverStatus(out.status) if out.status in SolverStatus.__members__ else SolverStatus.ERROR
    schedule = None
    after = None
    integrity = None
    comparison = None
    if out.has_solution:
        assert out.assignments is not None
        schedule = ExamSchedule(
            schedule_id=f"opt-{run_id}",
            name=f"Hexam optimized ({out.status.lower()}, {config.solver.strategy.value})",
            kind=ScheduleKind.OPTIMIZED,
            assignments=[
                ExamAssignment(exam_group_id=g, timeslot_id=t, locked=g in prob.pinned)
                for g, t in sorted(out.assignments.items())
            ],
            source_run_id=run_id,
            provenance=Provenance.synthetic(
                f"hexam-cp-sat:{run_id}",
                notes="Produced by the Hexam CP-SAT optimizer from synthetic registrations; not an official schedule.",
            ),
        )
        after = evaluator.evaluate(schedule, weights=config.weights)
        integrity = verify_schedule(
            idx, out.assignments, config,
            registrations_fingerprint_before=fp_before,
            registrations_fingerprint_after=registrations_fingerprint(idx),
        )
        true_pen = after.evaluation.weighted_penalty
        obj = out.objective_value
        ok = obj >= true_pen and (out.status != "OPTIMAL" or obj == true_pen)
        if obj == true_pen:
            msg = f"solver objective {obj:.0f} equals the independent evaluator penalty"
        elif ok:
            msg = (f"solver objective {obj:.0f} ≥ evaluator penalty {true_pen:.0f}: at a time limit some penalty "
                   "indicators are not yet tight; the evaluator value is the reported result")
        else:
            msg = f"solver objective {obj} inconsistent with evaluator penalty {true_pen}"
        integrity.checks.append(IntegrityCheck(name="solver_objective_consistent", passed=ok, message=msg))
        integrity.passed = all(c.passed for c in integrity.checks)
        comparison = compare(
            dataset_id=handle.dataset_id, dataset_version=handle.version,
            before=before, after=after, weights=config.weights, settings=config.evaluation,
            before_mapping=base_map, after_mapping=out.assignments,
        )

    result = OptimizationResult(
        run_id=run_id,
        config_id=config.config_id,
        status=status,
        started_at=started,
        finished_at=max(finished, started),
        runtime_seconds=out.runtime_seconds,
        objective_value=out.objective_value,
        best_objective_bound=out.best_objective_bound,
        objective_breakdown={ImpactMetricKey(k): v for k, v in out.objective_breakdown.items()},
        baseline_schedule_id=handle.baseline.schedule_id,
        optimized_schedule_id=schedule.schedule_id if schedule else None,
        before=before.evaluation,
        after=after.evaluation if after else None,
        message=_message(out.status, out.relaxed, out.hypothetical, out.strategy.value),
        relaxed=out.relaxed,
        hypothetical=out.hypothetical,
        strategy=out.strategy,
        warnings=out.warnings,
        infeasibility_hints=out.infeasibility_hints,
        model_stats=out.model_stats,
    )
    run = OptimizationRun(
        run_id=run_id,
        dataset_id=handle.dataset_id,
        dataset_version=handle.version,
        state=RunState.COMPLETED,
        created_at=created,
        finished_at=now_campus(),
        config=config,
        result=result,
        schedule=schedule,
        comparison=comparison,
        integrity=integrity,
    )
    return run, RunArtifacts(before, after)


class RunStore:
    """In-memory, thread-safe store + single-worker job queue."""

    def __init__(self, registry: DatasetRegistry, max_runs: int = 50):
        self.registry = registry
        self.max_runs = max_runs
        self._runs: dict[str, OptimizationRun] = {}
        self._artifacts: dict[str, RunArtifacts] = {}
        self._futures: dict[str, Future] = {}
        self._lock = threading.RLock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hexam-solver")

    # ---------------------------------------------------------------- jobs

    def submit(self, req: OptimizeRequest) -> OptimizationRun:
        handle = self.registry.get(req.dataset_id)
        run_id = new_run_id()
        config = build_config(req, handle, run_id)
        prob = validate_request(handle, config)  # raises InvalidRequest before queueing
        run = OptimizationRun(
            run_id=run_id, dataset_id=handle.dataset_id, dataset_version=handle.version,
            state=RunState.QUEUED, created_at=now_campus(), config=config,
        )
        with self._lock:
            self._runs[run_id] = run
            self._trim()
            fut = self._pool.submit(self._work, handle, config, run_id, prob)
            self._futures[run_id] = fut
        if req.wait:
            fut.result()
        return self.get(run_id)

    def _work(self, handle: DatasetHandle, config: OptimizationConfig, run_id: str, prob: Problem) -> None:
        self._update(run_id, state=RunState.RUNNING)
        try:
            run, art = execute(handle, config, run_id, prob)
            with self._lock:
                run = run.model_copy(update={"created_at": self._runs[run_id].created_at})
                self._runs[run_id] = run
                self._artifacts[run_id] = art
        except Exception as exc:  # surfaced to the client, never swallowed
            log.exception("optimization run %s failed", run_id)
            self._update(run_id, state=RunState.FAILED, error=f"{type(exc).__name__}: {exc}", finished_at=now_campus())

    def _update(self, run_id: str, **kw) -> None:
        with self._lock:
            if run_id in self._runs:
                self._runs[run_id] = self._runs[run_id].model_copy(update=kw)

    def _trim(self) -> None:
        done = [r for r in self._runs.values() if r.state in (RunState.COMPLETED, RunState.FAILED) and not r.precomputed]
        while len(self._runs) > self.max_runs and done:
            old = min(done, key=lambda r: r.created_at)
            done.remove(old)
            self._runs.pop(old.run_id, None)
            self._artifacts.pop(old.run_id, None)

    # ---------------------------------------------------------------- access

    def get(self, run_id: str) -> OptimizationRun:
        with self._lock:
            return self._runs[run_id]

    def artifacts(self, run_id: str) -> RunArtifacts | None:
        with self._lock:
            art = self._artifacts.get(run_id)
            run = self._runs.get(run_id)
        if art is None and run is not None and run.state is RunState.COMPLETED:
            art = self._rebuild_artifacts(run)
            with self._lock:
                self._artifacts[run_id] = art
        return art

    def _rebuild_artifacts(self, run: OptimizationRun) -> RunArtifacts:
        h = self.registry.get(run.dataset_id)
        ev = h.evaluator(run.config.evaluation)
        before = ev.evaluate(h.baseline, weights=run.config.weights)
        after = ev.evaluate(run.schedule, weights=run.config.weights) if run.schedule else None
        return RunArtifacts(before, after)

    def list(self, dataset_id: str | None = None) -> list[RunSummary]:
        with self._lock:
            runs = list(self._runs.values())
        out = []
        for r in sorted(runs, key=lambda r: r.created_at, reverse=True):
            if dataset_id and r.dataset_id != dataset_id:
                continue
            res = r.result
            out.append(RunSummary(
                run_id=r.run_id, dataset_id=r.dataset_id, state=r.state, created_at=r.created_at,
                precomputed=r.precomputed, label=r.label,
                status=res.status.value if res else None,
                runtime_seconds=res.runtime_seconds if res else None,
                penalty_before=res.before.weighted_penalty if res and res.before else None,
                penalty_after=res.after.weighted_penalty if res and res.after else None,
            ))
        return out

    def active_count(self) -> int:
        with self._lock:
            return sum(r.state in (RunState.QUEUED, RunState.RUNNING) for r in self._runs.values())

    def latest_completed(self, dataset_id: str) -> OptimizationRun | None:
        with self._lock:
            done = [r for r in self._runs.values()
                    if r.dataset_id == dataset_id and r.state is RunState.COMPLETED and r.schedule is not None]
        return max(done, key=lambda r: (not r.precomputed, r.created_at), default=None)

    # ------------------------------------------------------------- precomputed

    def load_precomputed(self, dataset_id: str) -> OptimizationRun | None:
        """Load a saved demo run, but only if it still matches the data and re-scores identically."""
        h = self.registry.get(dataset_id)
        path = Path(h.path) / PRECOMPUTED_FILE
        if not path.exists():
            return None
        run = OptimizationRun.model_validate_json(path.read_bytes())
        if run.dataset_version != h.version:
            log.warning("precomputed run %s is for dataset version %s (now %s); ignored", run.run_id, run.dataset_version, h.version)
            return None
        art = self._rebuild_artifacts(run)
        stored = run.result.after.weighted_penalty if run.result and run.result.after else None
        if art.after is None or art.after.evaluation.weighted_penalty != stored:
            log.warning("precomputed run %s does not re-score to its stored penalty; ignored", run.run_id)
            return None
        run = run.model_copy(update={"precomputed": True})
        with self._lock:
            self._runs[run.run_id] = run
            self._artifacts[run.run_id] = art
        return run

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
