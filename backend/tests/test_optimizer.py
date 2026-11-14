"""CP-SAT optimizer on small problems with known answers."""

import pytest

from hexam.evaluation import Evaluator
from hexam.optimization import Problem, solve
from hexam.schemas import (
    FixedAssignment,
    HardConstraints,
    OptimizationConfig,
    PenaltyWeights,
    SolverSettings,
)

from .factories import make_dataset, standard_slots

W = PenaltyWeights()


def config(**kw):
    hc = kw.pop("hc", {})
    solver = {"time_limit_seconds": 10, "num_workers": 4, "random_seed": 1, **kw.pop("solver", {})}
    return OptimizationConfig(
        config_id="test", name="test", hard_constraints=HardConstraints(**hc), solver=SolverSettings(**solver), **kw
    )


def run(ds, cfg=None, hint=None):
    cfg = cfg or config()
    idx = ds.build_index()
    out = solve(Problem.build(idx, cfg), cfg, hint)
    return out, idx, cfg


def penalty(idx, mapping, cfg):
    return Evaluator(idx, cfg.evaluation).evaluate(mapping, weights=cfg.weights).evaluation.weighted_penalty


class TestHardConstraints:
    def test_every_group_once_and_no_conflicts(self):
        students = {"S000001": ["a", "b", "c"], "S000002": ["b", "d"], "S000003": ["a", "d"]}
        ds = make_dataset(list("abcd"), students, standard_slots(days=(7,)))
        out, idx, cfg = run(ds)
        assert out.status == "OPTIMAL"
        assert set(out.assignments) == set("abcd")
        ev = Evaluator(idx).evaluate(out.assignments, weights=cfg.weights).evaluation
        assert ev.metrics.simultaneous_conflicts.count == 0 and ev.valid

    def test_unrelated_groups_may_share_a_slot(self):
        ds = make_dataset(list("ab"), {"S000001": ["a"], "S000002": ["b"]}, standard_slots(days=(7,))[:1])
        out, *_ = run(ds)
        assert out.status == "OPTIMAL" and out.assignments == {"a": "T070800", "b": "T070800"}

    def test_official_fixed_exam_is_preserved(self):
        ds = make_dataset(list("ab"), {"S000001": ["a", "b"]}, standard_slots(days=(7, 8)), fixed={"a": "T071800"})
        out, *_ = run(ds)
        assert out.assignments["a"] == "T071800"

    def test_allowed_forbidden_and_excluded_slots(self):
        slots = standard_slots(days=(7, 8))
        ds = make_dataset(
            list("abc"), {"S000001": list("abc")}, slots,
            allowed={"a": ["T080800", "T081120"]}, forbidden={"b": ["T070800", "T071120", "T071440"]},
        )
        out, *_ = run(ds, config(excluded_timeslot_ids=["T081120"]))
        assert out.assignments["a"] == "T080800"
        assert out.assignments["b"] not in {"T070800", "T071120", "T071440", "T081120"}
        assert "T081120" not in out.assignments.values()

    def test_user_lock(self):
        ds = make_dataset(list("ab"), {"S000001": ["a", "b"]}, standard_slots(days=(7, 8)))
        out, *_ = run(ds, config(fixed_assignments=[FixedAssignment(exam_group_id="b", timeslot_id="T071120")]))
        assert out.assignments["b"] == "T071120"

    def test_capacity_groups_per_slot(self):
        ds = make_dataset(list("abc"), {"S000001": ["a"], "S000002": ["b"], "S000003": ["c"]}, standard_slots(days=(7,)))
        out, *_ = run(ds, config(hc={"max_exam_groups_per_slot": 1}))
        assert len(set(out.assignments.values())) == 3

    def test_capacity_students_per_slot(self):
        students = {f"S{i:06d}": ["a"] for i in range(3)} | {f"S{i:06d}": ["b"] for i in range(3, 6)}
        ds = make_dataset(list("ab"), students, standard_slots(days=(7,)))
        out, *_ = run(ds, config(hc={"max_students_per_slot": 4}))
        assert out.assignments["a"] != out.assignments["b"]
        assert any("aggregate" in w for w in out.warnings)

    def test_max_exams_per_day(self):
        ds = make_dataset(list("abc"), {"S000001": list("abc")}, standard_slots(days=(7, 8)))
        out, idx, _ = run(ds, config(hc={"max_exams_per_student_per_day": 1}, weights=PenaltyWeights(
            simultaneous_conflicts=0, back_to_back=0, night_to_morning=0, three_in_one_day=0, three_in_24h=0, four_in_48h=0)))
        assert out.status == "INFEASIBLE"  # 3 exams, 2 days, ≤ 1 per day


class TestInfeasibility:
    def test_strict_conflict_infeasible(self):
        ds = make_dataset(list("ab"), {"S000001": ["a", "b"]}, standard_slots(days=(7,))[:1])
        out, *_ = run(ds)
        assert out.status == "INFEASIBLE"
        assert out.assignments is None and out.objective_value is None
        assert any("no-conflict" in h for h in out.infeasibility_hints)

    def test_pinned_pair_conflict_is_explained(self):
        ds = make_dataset(list("ab"), {"S000001": ["a", "b"]}, standard_slots(days=(7,)),
                          fixed={"a": "T070800", "b": "T070800"})
        out, *_ = run(ds)
        assert out.status == "INFEASIBLE"
        assert any("pinned groups a" in h for h in out.infeasibility_hints)

    def test_lock_to_forbidden_slot(self):
        ds = make_dataset(list("ab"), {"S000001": ["a", "b"]}, standard_slots(days=(7,)), forbidden={"a": ["T070800"]})
        out, *_ = run(ds, config(fixed_assignments=[FixedAssignment(exam_group_id="a", timeslot_id="T070800")]))
        assert out.status == "INFEASIBLE"
        assert any("not permitted" in h for h in out.infeasibility_hints)

    def test_lock_cannot_move_official_exam_unless_hypothetical(self):
        ds = make_dataset(list("ab"), {"S000001": ["a", "b"]}, standard_slots(days=(7,)), fixed={"a": "T070800"})
        lock = [FixedAssignment(exam_group_id="a", timeslot_id="T071800")]
        out, *_ = run(ds, config(fixed_assignments=lock))
        assert any("officially fixed" in i for i in out.infeasibility_hints + out.warnings) or out.assignments["a"] == "T070800"
        out2, *_ = run(ds, config(fixed_assignments=lock, hc={"allow_moving_fixed_exams": True}))
        assert out2.assignments["a"] == "T071800" and out2.hypothetical
        assert any("HYPOTHETICAL" in w for w in out2.warnings)

    def test_capacity_infeasible_diagnosed(self):
        ds = make_dataset(list("abc"), {"S000001": ["a"], "S000002": ["b"], "S000003": ["c"]}, standard_slots(days=(7,))[:2])
        out, *_ = run(ds, config(hc={"max_exam_groups_per_slot": 1}))
        assert out.status == "INFEASIBLE"
        assert any("capacity" in h for h in out.infeasibility_hints)

    def test_relaxation_mode_allows_penalized_conflict(self):
        ds = make_dataset(list("ab"), {"S000001": ["a", "b"]}, standard_slots(days=(7,))[:1])
        out, idx, cfg = run(ds, config(hc={"forbid_simultaneous_conflicts": False}))
        assert out.status == "OPTIMAL" and out.relaxed
        assert out.objective_value == W.simultaneous_conflicts == penalty(idx, out.assignments, cfg)


class TestObjective:
    def test_known_optimum_spreads_exams(self):
        # one student, 3 exams, 3 days available → zero penalty is achievable
        ds = make_dataset(list("abc"), {"S000001": list("abc")}, standard_slots(days=(7, 9, 11)))
        out, idx, cfg = run(ds)
        assert out.status == "OPTIMAL" and out.objective_value == 0
        assert penalty(idx, out.assignments, cfg) == 0

    def test_forced_cluster_has_exact_known_cost(self):
        # 3 shared exams but only one day with 3 consecutive slots
        slots = standard_slots(days=(7,))[:3]
        ds = make_dataset(list("abc"), {"S000001": list("abc"), "S000002": list("abc")}, slots)
        out, idx, cfg = run(ds)
        expected = 2 * (2 * W.back_to_back + W.three_in_one_day + W.three_in_24h)
        assert out.status == "OPTIMAL"
        assert out.objective_value == expected == penalty(idx, out.assignments, cfg)
        assert out.objective_breakdown["back_to_back"] == 4 * W.back_to_back

    @pytest.mark.parametrize("strict", [True, False])
    def test_solver_objective_equals_evaluator(self, strict):
        students = {
            "S000001": list("abcd"), "S000002": list("bcde"), "S000003": ["a", "e"],
            "S000004": list("ace"), "S000005": ["d", "f"], "S000006": list("abf"),
        }
        ds = make_dataset(list("abcdef"), students, standard_slots(days=(7, 8)))
        out, idx, cfg = run(ds, config(hc={"forbid_simultaneous_conflicts": strict}))
        assert out.status == "OPTIMAL"
        ev = Evaluator(idx).evaluate(out.assignments, weights=cfg.weights).evaluation
        assert out.objective_value == ev.weighted_penalty
        for k, v in ev.penalty_breakdown.items():
            assert out.objective_breakdown[k.value] == v

    def test_weights_change_the_tradeoff(self):
        # two exams, two days: evening + next morning (late→early) vs back-to-back same day
        from .factories import dt, slot

        slots = [slot("D7E", dt(7, 18), dt(7, 20, 50)), slot("D8M", dt(8, 8), dt(8, 10, 50)), slot("D8L", dt(8, 11, 20), dt(8, 14, 10))]
        ds = make_dataset(list("ab"), {"S000001": ["a", "b"]}, slots, fixed={"a": "D8M"})
        w1 = PenaltyWeights(back_to_back=100, night_to_morning=1)
        out1, *_ = run(ds, config(weights=w1))
        assert out1.assignments["b"] == "D7E"
        w2 = PenaltyWeights(back_to_back=1, night_to_morning=100)
        out2, *_ = run(ds, config(weights=w2))
        assert out2.assignments["b"] == "D8L"


def test_two_phase_reports_no_global_bound():
    students = {f"S{i:06d}": [g for j, g in enumerate("abcdef") if (i >> j) & 1 or j == i % 6] for i in range(40)}
    students = {k: v for k, v in students.items() if v}
    ds = make_dataset(list("abcdef"), students, standard_slots(days=(7, 8, 9)))
    out, idx, cfg = run(ds, config(solver={"strategy": "two_phase", "two_phase_large_groups": 2}))
    assert out.status == "FEASIBLE"  # never claims global optimality
    assert out.best_objective_bound is None
    assert out.objective_value == penalty(idx, out.assignments, cfg)
