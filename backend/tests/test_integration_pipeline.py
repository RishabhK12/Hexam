"""End to end on the real fall2026 dataset: baseline → optimize → independent
evaluation → comparison → hard-constraint validation. Records actual metrics."""

import json

import pytest

from hexam.schemas import OptimizationConfig, SolverSettings
from hexam.services.dataset_service import DatasetRegistry
from hexam.services.integrity import registrations_fingerprint
from hexam.services.optimization_service import execute


@pytest.fixture(scope="module")
def handle():
    return DatasetRegistry().get("fall2026")


@pytest.fixture(scope="module")
def outcome(handle):
    baseline_before = handle.baseline.model_dump_json()
    fp = registrations_fingerprint(handle.index)
    cfg = OptimizationConfig(
        config_id="it", name="integration",
        baseline_schedule_id=handle.baseline.schedule_id,
        solver=SolverSettings(time_limit_seconds=10, num_workers=8, random_seed=7),
    )
    run, art = execute(handle, cfg, "run-integration")
    return run, art, baseline_before, fp


def test_pipeline(handle, outcome, capsys):
    run, art, baseline_before, fp = outcome
    res = run.result
    assert res.status in ("OPTIMAL", "FEASIBLE")
    # baseline and population untouched
    assert handle.baseline.model_dump_json() == baseline_before
    assert registrations_fingerprint(handle.index) == fp
    assert res.before.metrics.students_evaluated == res.after.metrics.students_evaluated == 1000
    # independent hard-constraint verification
    assert run.integrity.passed, [c.message for c in run.integrity.checks if not c.passed]
    names = {c.name for c in run.integrity.checks}
    assert {"every_group_scheduled_once", "official_fixed_exams_preserved", "only_permitted_slots_used",
            "no_student_conflicts", "registrations_unchanged", "solver_objective_consistent"} <= names
    # genuine improvement, measured by the evaluator (not the solver)
    c = run.comparison
    assert c.penalty_after < c.penalty_before
    assert res.after.metrics.simultaneous_conflicts.count == 0
    assert res.before.metrics.simultaneous_conflicts.count > 0
    assert c.penalty_after == art.after.evaluation.weighted_penalty
    # percentages: None only when baseline is 0
    for d in c.count_deltas:
        assert (d.percent_change is None) == (d.before == 0)
    with capsys.disabled():
        print("\n[integration] status", res.status, "runtime", res.runtime_seconds, "s;",
              "penalty", c.penalty_before, "->", c.penalty_after, f"({c.penalty_percent_change}%)")
        print("[integration]", json.dumps({d.key.value: [d.before, d.after] for d in c.count_deltas}))
