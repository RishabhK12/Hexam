"""Optimization config/result, metrics and API envelope validation."""

import pytest
from pydantic import ValidationError

from hexam.schemas import (
    EvaluateScheduleRequest,
    ImpactMetricKey,
    MetricValue,
    OptimizationConfig,
    OptimizationResult,
    PenaltyWeights,
    RunOptimizationRequest,
    SolverSettings,
    StudentImpactDetail,
    StudentImpactMetrics,
    compare_metrics,
)

METRIC_KEYS = {k.value for k in ImpactMetricKey}


def test_metric_keys_line_up_across_models():
    """Weights, aggregate metrics and per-student details share one key set."""
    assert set(PenaltyWeights.model_fields) == METRIC_KEYS
    assert METRIC_KEYS <= set(StudentImpactMetrics.model_fields)
    assert METRIC_KEYS <= set(StudentImpactDetail.model_fields)


def metrics(**counts):
    vals = {k: MetricValue(count=counts.get(k, 0), students_affected=min(counts.get(k, 0), 1)) for k in METRIC_KEYS}
    return StudentImpactMetrics(
        students_evaluated=10, exam_enrollments=40, students_with_any_issue=1 if any(counts.values()) else 0, **vals
    )


class TestWeights:
    def test_negative_weight_rejected(self):
        with pytest.raises(ValidationError):
            PenaltyWeights(back_to_back=-1)

    def test_weights_are_integers(self):
        with pytest.raises(ValidationError):
            PenaltyWeights(back_to_back=1.5)

    def test_score(self):
        w = PenaltyWeights(simultaneous_conflicts=1000, back_to_back=10)
        assert w.score(metrics(simultaneous_conflicts=1, back_to_back=3)) == 1030


class TestMetrics:
    def test_any_issue_must_cover_each_metric(self):
        with pytest.raises(ValidationError, match="inconsistent"):
            StudentImpactMetrics(
                students_evaluated=10, exam_enrollments=40, students_with_any_issue=0,
                **{k: MetricValue(count=2, students_affected=2) for k in METRIC_KEYS},
            )

    def test_compare(self):
        deltas = {d.key: d for d in compare_metrics(metrics(back_to_back=4), metrics(back_to_back=1))}
        assert deltas["back_to_back"].change == -3 and deltas["back_to_back"].percent_change == -75.0
        assert deltas["simultaneous_conflicts"].percent_change is None


class TestConfig:
    def test_defaults(self):
        c = OptimizationConfig(config_id="c", name="n")
        assert c.solver.time_limit_seconds == 60 and c.hard_constraints.max_exams_per_student_per_slot == 2

    @pytest.mark.parametrize("limit", [0, -1, 7200])
    def test_time_limit_bounds(self, limit):
        with pytest.raises(ValidationError):
            SolverSettings(time_limit_seconds=limit)

    def test_duplicate_fixed_group(self):
        with pytest.raises(ValidationError, match="fixed more than once"):
            OptimizationConfig(config_id="c", name="n", fixed_assignments=[
                {"exam_group_id": "EG-1", "timeslot_id": "TS-1"},
                {"exam_group_id": "EG-1", "timeslot_id": "TS-2"},
            ])

    def test_fixed_into_excluded_slot(self):
        with pytest.raises(ValidationError, match="excluded"):
            OptimizationConfig(
                config_id="c", name="n", excluded_timeslot_ids=["TS-1"],
                fixed_assignments=[{"exam_group_id": "EG-1", "timeslot_id": "TS-1"}],
            )


class TestResult:
    def base(self, **kw):
        ev = {"schedule_id": "opt", "metrics": metrics().model_dump()}
        data = dict(
            run_id="r", config_id="c", dataset_id="d", status="FEASIBLE",
            started_at="2026-11-14T12:00:00-05:00", finished_at="2026-11-14T12:01:00-05:00",
            runtime_seconds=60, objective_value=0, optimized_schedule_id="opt", after=ev,
        )
        return OptimizationResult(**(data | kw))

    def test_valid(self):
        assert self.base().status.has_solution

    def test_feasible_requires_schedule(self):
        with pytest.raises(ValidationError, match="needs optimized_schedule_id"):
            self.base(optimized_schedule_id=None, after=None)

    def test_feasible_requires_objective(self):
        with pytest.raises(ValidationError, match="objective_value"):
            self.base(objective_value=None)

    def test_infeasible_cannot_carry_schedule(self):
        with pytest.raises(ValidationError, match="cannot carry"):
            self.base(status="INFEASIBLE")
        r = self.base(status="INFEASIBLE", optimized_schedule_id=None, after=None, objective_value=None)
        assert not r.status.has_solution

    def test_finished_before_started(self):
        with pytest.raises(ValidationError, match="precedes"):
            self.base(finished_at="2026-11-14T11:00:00-05:00")

    def test_after_must_match_schedule(self):
        with pytest.raises(ValidationError, match="after.schedule_id"):
            self.base(optimized_schedule_id="other")

    def test_before_requires_baseline_id(self):
        with pytest.raises(ValidationError, match="baseline_schedule_id"):
            self.base(before={"schedule_id": "base", "metrics": metrics().model_dump()})

    def test_unknown_status(self):
        with pytest.raises(ValidationError):
            self.base(status="DONE")

    def test_relative_gap(self):
        assert self.base(objective_value=100, best_objective_bound=90).relative_gap == pytest.approx(0.1)


class TestApiEnvelopes:
    def test_run_request_exactly_one(self):
        RunOptimizationRequest(config_id="cfg-default")
        RunOptimizationRequest(config={"config_id": "c", "name": "n"})
        for bad in ({}, {"config_id": "a", "config": {"config_id": "c", "name": "n"}}):
            with pytest.raises(ValidationError, match="exactly one"):
                RunOptimizationRequest(**bad)

    def test_evaluate_request_exactly_one(self):
        EvaluateScheduleRequest(assignments=[{"exam_group_id": "EG-1", "timeslot_id": "TS-1"}])
        with pytest.raises(ValidationError, match="exactly one"):
            EvaluateScheduleRequest()
