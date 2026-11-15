"""Move scorer and Kempe-chain local search: exact agreement with the evaluator, legal results."""

import random

import numpy as np
import pytest

from hexam.evaluation import Evaluator
from hexam.optimization import Problem, solve
from hexam.optimization.local_search import MoveScorer, local_search
from hexam.schemas import HardConstraints, OptimizationConfig, PenaltyWeights, SolverSettings

from .factories import make_dataset, standard_slots

GROUPS = list("abcdefgh")


def students(n=60, seed=3):
    rnd = random.Random(seed)
    return {f"S{i:06d}": sorted(rnd.sample(GROUPS, rnd.randint(2, 5))) for i in range(n)}


def config(**kw):
    hc = kw.pop("hc", {})
    solver = {"time_limit_seconds": 10, "num_workers": 4, "random_seed": 1, **kw.pop("solver", {})}
    return OptimizationConfig(
        config_id="test", name="test", hard_constraints=HardConstraints(**hc), solver=SolverSettings(**solver), **kw
    )


def setup(cfg=None, days=(7, 8, 9), **ds_kw):
    cfg = cfg or config()
    ds = make_dataset(GROUPS, students(), standard_slots(days=days), **ds_kw)
    idx = ds.build_index()
    return idx, cfg, Problem.build(idx, cfg)


def relaxed_scorer(prob, cfg):
    """Scores any schedule: conflicts are penalized, never forbidden."""
    return MoveScorer(prob, cfg.weights, strict=False, max_per_slot=99)


def greedy_start(prob, scorer):
    """A schedule with no conflicts, found by first fit (enough slots in these tests)."""
    a = np.full(scorer.G, -1, dtype=np.int64)
    for g in range(scorer.G):
        for t in scorer.cols[g]:
            if not any(a[h] == t for h in scorer.nbrs[g]):
                a[g] = t
                break
    assert (a >= 0).all()
    return scorer.decode(a)


class TestScorer:
    def test_score_equals_evaluator_on_random_schedules(self):
        idx, cfg, prob = setup()
        scorer = relaxed_scorer(prob, cfg)
        ev = Evaluator(idx, cfg.evaluation)
        rnd = random.Random(5)
        for _ in range(40):
            mapping = {g: rnd.choice(prob.slots) for g in prob.groups}
            e = ev.evaluate(mapping, weights=cfg.weights).evaluation
            ok, total, breakdown = scorer.score(mapping)
            assert total == e.weighted_penalty
            assert breakdown == {k.value: v for k, v in e.penalty_breakdown.items()}

    def test_score_equals_evaluator_on_the_real_dataset(self):
        from hexam.services.dataset_service import DatasetRegistry

        h = DatasetRegistry().get("fall2026")
        cfg = config()
        prob = Problem.build(h.index, cfg)
        scorer = relaxed_scorer(prob, cfg)
        ev = h.evaluator(cfg.evaluation)
        base = ev.evaluate(h.baseline, weights=cfg.weights).evaluation
        assert scorer.score(h.baseline_mapping)[1] == base.weighted_penalty
        rnd = random.Random(2)
        for _ in range(5):
            mapping = {g: rnd.choice(prob.slots) for g in prob.groups}
            assert scorer.score(mapping)[1] == ev.evaluate(mapping, weights=cfg.weights).evaluation.weighted_penalty

    @pytest.mark.parametrize("strict", [True, False])
    def test_move_costs_equal_full_rescoring(self, strict):
        idx, cfg, prob = setup()
        scorer = MoveScorer(prob, cfg.weights, strict=strict, max_per_slot=99)
        start = scorer.encode(greedy_start(prob, scorer))
        scorer.load(start)
        _, current, _ = scorer.evaluate(start)
        delta = scorer.move_costs()
        finite = 0
        for g in range(scorer.G):
            for t in range(scorer.T):
                moved = start.copy()
                moved[g] = t
                ok, total, _ = scorer.evaluate(moved)
                if np.isfinite(delta[g, t]):
                    finite += 1
                    assert ok and total - current == delta[g, t]
                elif t != start[g]:
                    assert not ok  # a forbidden move really breaks a hard constraint
        assert finite > 0

    def test_apply_keeps_incremental_state_exact(self):
        idx, cfg, prob = setup()
        scorer = relaxed_scorer(prob, cfg)
        rnd = random.Random(9)
        scorer.load(scorer.encode({g: rnd.choice(prob.slots) for g in prob.groups}))
        _, current, _ = scorer.evaluate(scorer.a)
        for _ in range(60):
            g, t = rnd.randrange(scorer.G), rnd.randrange(scorer.T)
            if t == scorer.a[g]:
                continue
            current += int(scorer.move_costs()[g, t])
            scorer.apply(g, t)
            assert scorer.evaluate(scorer.a)[1] == current

    def test_cost_of_equals_full_score_for_compound_moves(self):
        idx, cfg, prob = setup()
        scorer = MoveScorer(prob, cfg.weights, strict=True)
        scorer.load(scorer.encode(greedy_start(prob, scorer)))
        seen = 0
        for new in scorer.compound_candidates():
            ok, total, _ = scorer.evaluate(new)
            ok2, total2 = scorer.cost_of(new)
            assert ok == ok2 and (not ok or total == total2)
            seen += 1
        assert seen > 10

    def test_kempe_chain_never_creates_a_conflict(self):
        idx, cfg, prob = setup()
        scorer = MoveScorer(prob, cfg.weights, strict=True)
        scorer.load(scorer.encode(greedy_start(prob, scorer)))
        relaxed = relaxed_scorer(prob, cfg)
        for g in range(scorer.G):
            for u in range(scorer.T):
                if u != scorer.a[g]:
                    new, moved = scorer.kempe(g, u)
                    assert g in moved and new[g] == u
                    assert relaxed.score(scorer.decode(new))[2]["simultaneous_conflicts"] == 0

    def test_hard_constraints_block_moves(self):
        ds_kw = {"fixed": {"a": "T070800"}, "forbidden": {"b": ["T080800", "T081120"]}}
        idx, cfg, prob = setup(config(hc={"max_exam_groups_per_slot": 2, "max_exams_per_student_per_day": 2}), **ds_kw)
        scorer = MoveScorer(prob, cfg.weights, strict=True, per_day_cap=2)
        scorer.load(scorer.encode(greedy_start(prob, scorer)))
        delta = scorer.move_costs()
        a, b = scorer._gi["a"], scorer._gi["b"]
        assert not np.isfinite(delta[a]).any()  # official fix: cannot move at all
        assert not np.isfinite(delta[b, scorer._si["T080800"]])
        for g, t in zip(*np.nonzero(np.isfinite(delta))):
            moved = scorer.a.copy()
            moved[g] = t
            assert scorer.evaluate(moved)[0]


class TestLocalSearch:
    def test_result_is_legal_exact_and_no_worse(self):
        idx, cfg, prob = setup()
        scorer = MoveScorer(prob, cfg.weights, strict=True)
        start = greedy_start(prob, scorer)
        out = local_search(scorer, start, time_limit=20, seed=4, max_rounds=2)
        ev = Evaluator(idx, cfg.evaluation).evaluate(out.assignments, weights=cfg.weights).evaluation
        assert ev.valid and ev.metrics.simultaneous_conflicts.count == 0
        assert out.penalty == ev.weighted_penalty <= out.start_penalty
        assert out.penalty < out.start_penalty  # first fit packs exams together; there is plenty to gain

    def test_same_seed_same_schedule(self):
        idx, cfg, prob = setup()
        runs = []
        for _ in range(2):
            scorer = MoveScorer(prob, cfg.weights, strict=True)
            runs.append(local_search(scorer, greedy_start(prob, scorer), time_limit=30, seed=7, max_rounds=1))
        assert runs[0].assignments == runs[1].assignments and runs[0].penalty == runs[1].penalty

    def test_rejects_an_illegal_start(self):
        idx, cfg, prob = setup()
        scorer = MoveScorer(prob, cfg.weights, strict=True)
        clash = {g: prob.slots[0] for g in prob.groups}
        assert local_search(scorer, clash, time_limit=1) is None

    def test_respects_fixes_restrictions_and_caps(self):
        ds_kw = {"fixed": {"a": "T071800"}, "allowed": {"c": ["T090800", "T091120", "T091440"]}}
        idx, cfg, prob = setup(config(hc={"max_exam_groups_per_slot": 1}), **ds_kw)
        scorer = MoveScorer(prob, cfg.weights, strict=True)
        out = local_search(scorer, greedy_start_capped(scorer), time_limit=20, seed=0, max_rounds=1)
        assert out.assignments["a"] == "T071800"
        assert out.assignments["c"] in {"T090800", "T091120", "T091440"}
        assert len(set(out.assignments.values())) == len(GROUPS)  # one group per slot

    def test_relaxed_mode_scores_conflicts_like_the_evaluator(self):
        cfg = config(hc={"forbid_simultaneous_conflicts": False})
        idx, _, prob = setup(cfg, days=(7,))  # 4 slots for 8 groups: conflicts are unavoidable
        scorer = MoveScorer(prob, cfg.weights, strict=False, max_per_slot=2)
        start = solve(prob, config(hc={"forbid_simultaneous_conflicts": False},
                                   solver={"strategy": "single_pass", "time_limit_seconds": 2})).assignments
        out = local_search(scorer, start, time_limit=20, seed=1, max_rounds=1)
        ev = Evaluator(idx, cfg.evaluation).evaluate(out.assignments, weights=cfg.weights).evaluation
        assert out.penalty == ev.weighted_penalty
        assert out.breakdown["simultaneous_conflicts"] == ev.penalty_breakdown["simultaneous_conflicts"] > 0


def greedy_start_capped(scorer):
    """First fit with one group per slot (for the group-cap test)."""
    a = np.full(scorer.G, -1, dtype=np.int64)
    order = sorted(range(scorer.G), key=lambda g: len(scorer.cols[g]))
    for g in order:
        a[g] = next(t for t in scorer.cols[g] if t not in a)
    return scorer.decode(a)


class TestHybridStrategy:
    def test_hybrid_is_the_default_and_reports_local_search_stats(self):
        idx, cfg, prob = setup()
        assert cfg.solver.strategy.value == "hybrid"
        out = solve(prob, cfg)
        assert out.has_solution and out.strategy.value == "hybrid"
        ev = Evaluator(idx, cfg.evaluation).evaluate(out.assignments, weights=cfg.weights).evaluation
        assert ev.valid and ev.metrics.simultaneous_conflicts.count == 0
        assert out.objective_value >= ev.weighted_penalty
        for key in ("local_search_ms", "local_search_start_penalty", "local_search_penalty", "local_search_rounds"):
            assert key in out.model_stats
        assert ev.weighted_penalty <= out.model_stats["local_search_penalty"] <= out.model_stats["local_search_start_penalty"]
        assert out.best_objective_bound is not None and out.best_objective_bound <= ev.weighted_penalty

    def test_hybrid_never_worse_than_its_local_search(self):
        idx, _, prob = setup()
        cfg = config(solver={"time_limit_seconds": 3})
        out = solve(prob, cfg)
        ev = Evaluator(idx, cfg.evaluation).evaluate(out.assignments, weights=cfg.weights).evaluation
        assert ev.weighted_penalty <= out.model_stats["local_search_penalty"]

    def test_hybrid_with_local_search_disabled_still_solves(self):
        idx, _, prob = setup()
        out = solve(prob, config(solver={"local_search_seconds": 0, "time_limit_seconds": 5}))
        assert out.has_solution

    def test_hybrid_reports_infeasible_inputs_like_single_pass(self):
        ds = make_dataset(list("ab"), {"S000001": ["a", "b"]}, standard_slots(days=(7,))[:1])
        idx = ds.build_index()
        cfg = config()
        out = solve(Problem.build(idx, cfg), cfg)
        assert out.status == "INFEASIBLE" and out.assignments is None
        assert any("local search" in w for w in out.warnings)
