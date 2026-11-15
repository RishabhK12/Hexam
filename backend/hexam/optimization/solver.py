"""OR-Tools CP-SAT exam-group → timeslot model.

Decision variables
    x[g,t] ∈ {0,1}   exam group g sits in timeslot t   (only eligible (g,t) pairs)

Hard constraints
    A  Σ_t x[g,t] = 1                                   every group exactly once
    B  official common exams pinned (unless the hypothetical flag is set)
    C  only eligible slots (exclusions, allowed/forbidden lists) — by variable domain
    D  strict: for every co-enrolled pair (g,h) and slot t:
           x[g,t] + Σ_{u overlaps t} x[h,u] ≤ 1
    E  aggregate capacity per slot: Σ_g size_g·x[g,t] ≤ cap_students(t),
           Σ_g x[g,t] ≤ cap_groups(t)                     (only when caps exist)
    F  user locks — by variable domain
    (+) optional: per-student max exams per day

Objective (minimized) — *exactly* the evaluator's weighted penalty:
    pair metrics (conflicts in relaxed mode, back-to-back, late→early):
        one Boolean p[g,h] per co-enrolled pair, forced to 1 by
        x[g,t] + Σ_{u ∈ R(t)} x[h,u] ≤ 1 + p[g,h]   for each t,
        cost = weight × shared_students(g,h) × p[g,h]
    three_in_24h / four_in_48h: per distinct student exam-set S, one Boolean
        q[S] with Σ_{g∈S} y[g,W] ≤ k−1 + (|S|−k+1)·q[S] for every rolling
        window W, where y[g,W] = Σ_{t∈W} x[g,t]. (3 exams in one calendar
        day is a special case of 3 in 24 h, so it has no separate term.)

Auxiliary Booleans are only bounded from below, so for any feasible solution
the solver objective is ≥ the true penalty, with equality at a proven optimum
(minimization drives every indicator down). Before optimality is proven, the
incumbent can carry slack. Hexam therefore always re-scores the schedule
with the independent evaluator and reports that value as the result.

Warm start: the official baseline usually violates strict mode, so a quick
hard-constraints-only model first finds the feasible schedule with the fewest
groups moved from the baseline, and the main model is hinted with it.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field, replace

from ortools.sat.python import cp_model

from hexam.schemas import ImpactMetricKey, OptimizationConfig, OptimizationStrategy, PenaltyWeights

from .problem import Problem

K = ImpactMetricKey


@dataclass(slots=True)
class SolveOutcome:
    status: str  # CP-SAT status name (OPTIMAL, FEASIBLE, INFEASIBLE, MODEL_INVALID, UNKNOWN)
    assignments: dict[str, str] | None
    objective_value: float | None
    best_objective_bound: float | None
    objective_breakdown: dict[str, float]
    runtime_seconds: float
    strategy: OptimizationStrategy
    relaxed: bool
    hypothetical: bool
    warnings: list[str] = field(default_factory=list)
    infeasibility_hints: list[str] = field(default_factory=list)
    model_stats: dict[str, int] = field(default_factory=dict)

    @property
    def has_solution(self) -> bool:
        return self.status in ("OPTIMAL", "FEASIBLE") and self.assignments is not None


@dataclass(slots=True)
class _Built:
    model: cp_model.CpModel
    x: dict[tuple[str, str], cp_model.IntVar]
    terms: dict[str, list[tuple[int, cp_model.IntVar]]]  # metric -> (coef, var)


@dataclass(slots=True)
class _Options:
    strict: bool = True
    capacity: bool = True
    per_day_cap: int | None = None
    max_per_slot: int = 2
    objective: bool = True


def _build(prob: Problem, weights: PenaltyWeights, groups: list[str], pins: dict[str, str], opt: _Options) -> _Built:
    m = cp_model.CpModel()
    rel = prob.relations
    gset = set(groups)
    # pins only ever narrow the permitted set; a pin outside it leaves no slot (infeasible, explained)
    elig = {g: tuple(t for t in prob.permitted[g] if g not in pins or t == pins[g]) for g in groups}

    # A + C + F: variables only for eligible pairs, exactly one per group
    x: dict[tuple[str, str], cp_model.IntVar] = {}
    for g in groups:
        for t in elig[g]:
            x[g, t] = m.new_bool_var(f"x[{g},{t}]")
        m.add_exactly_one([x[g, t] for t in elig[g]])  # empty list -> infeasible (reported)

    def xs(g: str, slots) -> list[cp_model.IntVar]:
        return [x[g, u] for u in slots if (g, u) in x]

    terms: dict[str, list[tuple[int, cp_model.IntVar]]] = {k.value: [] for k in K}
    edges = {e: n for e, n in prob.edges.items() if e[0] in gset and e[1] in gset}

    def neighbors(rel_pairs, t: str) -> set[str]:
        return {b for a, b in rel_pairs if a == t} | {a for a, b in rel_pairs if b == t}

    b2b_nb = {t: neighbors(rel.b2b, t) for t in rel.slot_ids}
    late_nb = {t: neighbors(rel.late_early, t) for t in rel.slot_ids}

    def pair_indicator(g: str, h: str, nb: dict[str, set[str]] | dict[str, frozenset[str]], name: str):
        """Boolean forced to 1 when g's slot and h's slot are related by ``nb``; None if impossible."""
        a, b = (g, h) if len(elig[g]) <= len(elig[h]) else (h, g)
        rows = [(t, xs(b, nb.get(t, ()))) for t in elig[a]]
        rows = [(t, r) for t, r in rows if r]
        if not rows:
            return None
        p = m.new_bool_var(f"{name}[{g},{h}]")
        for t, r in rows:
            m.add(x[a, t] + sum(r) <= 1 + p)
        return p

    # D: conflicts (strict = hard, relaxed = penalized pair Boolean)
    for (g, h), n in edges.items():
        if opt.strict:
            a, b = (g, h) if len(elig[g]) <= len(elig[h]) else (h, g)
            for t in elig[a]:
                r = xs(b, rel.overlap.get(t, ()))
                if r:
                    m.add(x[a, t] + sum(r) <= 1)
        else:
            p = pair_indicator(g, h, rel.overlap, "c")
            if p is not None:
                terms[K.SIMULTANEOUS_CONFLICTS.value].append((weights.simultaneous_conflicts * n, p))

    # soft pair metrics
    if opt.objective:
        for (g, h), n in edges.items():
            if weights.back_to_back:
                p = pair_indicator(g, h, b2b_nb, "b2b")
                if p is not None:
                    terms[K.BACK_TO_BACK.value].append((weights.back_to_back * n, p))
            if weights.night_to_morning:
                p = pair_indicator(g, h, late_nb, "late")
                if p is not None:
                    terms[K.NIGHT_TO_MORNING.value].append((weights.night_to_morning * n, p))

    # day / window indicators per group
    def group_in(g: str, slots, cache: dict) -> cp_model.LinearExpr | int:
        key = (g, slots)
        if key not in cache:
            vs = xs(g, slots)
            if not vs:
                cache[key] = 0
            elif len(vs) == len(elig[g]):
                cache[key] = 1  # every eligible slot of g lies inside: constant
            else:
                y = m.new_bool_var("y")
                m.add(y == sum(vs))
                cache[key] = y
        return cache[key]

    cache: dict = {}
    sets = prob.restricted_sets(groups)
    days = list(rel.days.values())

    def count_expr(s: tuple[str, ...], slots):
        parts = [group_in(g, slots, cache) for g in s]
        const = sum(p for p in parts if isinstance(p, int))
        var = [p for p in parts if not isinstance(p, int)]
        return const, var

    for s, n in sets.items():
        k = len(s)
        # max exams per student per day (hard, optional)
        if opt.per_day_cap is not None and k > opt.per_day_cap:
            for d in days:
                c, v = count_expr(s, d)
                if c + len(v) > opt.per_day_cap:
                    m.add(sum(v) + c <= opt.per_day_cap)
        # relaxed mode: max overlapping exams per student per slot
        if not opt.strict and k > opt.max_per_slot:
            for t in prob.slots:
                c, v = count_expr(s, tuple(sorted(rel.overlap[t])))
                if c + len(v) > opt.max_per_slot:
                    m.add(sum(v) + c <= opt.max_per_slot)
        if not opt.objective:
            continue
        for size, wins, key, w in (
            (3, rel.windows24, K.THREE_IN_24H, weights.three_in_24h),
            (4, rel.windows48, K.FOUR_IN_48H, weights.four_in_48h),
        ):
            if k < size or not w:
                continue
            rows = []
            for win in wins:
                c, v = count_expr(s, win)
                if c + len(v) >= size:
                    rows.append((c, v))
            if rows:
                q = m.new_bool_var("q")
                for c, v in rows:
                    m.add(sum(v) + c <= size - 1 + (c + len(v) - size + 1) * q)
                terms[key.value].append((w * n, q))

    # E: aggregate capacity
    if opt.capacity:
        for t, cap in prob.slot_student_cap.items():
            vs = [(prob.group_size[g], x[g, t]) for g in groups if (g, t) in x and prob.group_size[g]]
            if vs:
                m.add(sum(c * v for c, v in vs) <= cap)
        for t, cap in prob.slot_group_cap.items():
            vs = [x[g, t] for g in groups if (g, t) in x]
            if vs:
                m.add(sum(vs) <= cap)

    if opt.objective:
        m.minimize(sum(c * v for lst in terms.values() for c, v in lst))
    return _Built(m, x, terms)


def _solver(cfg: OptimizationConfig, time_limit: float) -> cp_model.CpSolver:
    s = cp_model.CpSolver()
    p = s.parameters
    p.max_time_in_seconds = max(0.1, time_limit)
    p.num_workers = cfg.solver.num_workers
    p.random_seed = cfg.solver.random_seed
    p.log_search_progress = cfg.solver.log_search_progress
    if cfg.solver.relative_gap_limit is not None:
        p.relative_gap_limit = cfg.solver.relative_gap_limit
    return s


def _hint(built: _Built, hint: dict[str, str] | None) -> None:
    if not hint:
        return
    for (g, t), var in built.x.items():
        if g in hint:
            built.model.add_hint(var, int(hint[g] == t))


def _options(cfg: OptimizationConfig) -> _Options:
    hc = cfg.hard_constraints
    return _Options(
        strict=hc.forbid_simultaneous_conflicts,
        capacity=hc.enforce_timeslot_capacity,
        per_day_cap=hc.max_exams_per_student_per_day,
        max_per_slot=hc.max_exams_per_student_per_slot,
    )


@dataclass(slots=True)
class _Incumbent:
    """A complete solution of the *main* model (x fixed to a known schedule, indicators set tight by CP-SAT)."""

    mapping: dict[str, str]
    objective: float
    breakdown: dict[str, float]


def _full_hint(built: _Built, cfg: OptimizationConfig, mapping: dict[str, str]) -> _Incumbent | None:
    """Hint *every* variable (x and penalty indicators) for a known feasible schedule:
    fix x in a clone, let CP-SAT set the indicators tight (pure propagation), copy all values.
    Returns that solution as an incumbent of the main model."""
    c = built.model.clone()
    for (g, t), v in built.x.items():
        c.add(c.get_bool_var_from_proto_index(v.index) == int(mapping.get(g) == t))
    s = _solver(cfg, 5.0)
    if s.solve(c) not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    for i in range(len(built.model.proto.variables)):
        built.model.add_hint(built.model.get_int_var_from_proto_index(i), s.value(c.get_int_var_from_proto_index(i)))
    val = lambda v: s.value(c.get_int_var_from_proto_index(v.index))  # noqa: E731
    breakdown = {k: float(sum(coef * val(v) for coef, v in lst)) for k, lst in built.terms.items()}
    return _Incumbent(dict(mapping), float(round(s.objective_value)), breakdown)


def _warm_start(prob: Problem, cfg: OptimizationConfig, opt: _Options, groups: list[str], pins: dict[str, str],
                hint: dict[str, str] | None, budget: float) -> dict[str, str] | None:
    """Hard constraints only; minimize the number of groups moved from ``hint``.
    Gives the main model a feasible starting schedule close to the baseline."""
    b = _build(prob, cfg.weights, groups, pins, replace(opt, objective=False))
    if hint:
        b.model.minimize(sum(1 - v for (g, t), v in b.x.items() if hint.get(g) == t))
        _hint(b, hint)
    s = _solver(cfg, budget)
    st = s.solve(b.model)
    if st in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {g: t for (g, t), v in b.x.items() if s.boolean_value(v)}
    return None


def _diagnose(prob: Problem, cfg: OptimizationConfig, opt: _Options, budget: float) -> list[str]:
    """After INFEASIBLE: re-check feasibility with one constraint family removed at a time."""
    probes = []
    if opt.strict:
        probes.append(("strict no-conflict rule", replace(opt, strict=False, max_per_slot=99)))
    if opt.capacity and (prob.slot_student_cap or prob.slot_group_cap):
        probes.append(("capacity caps", replace(opt, capacity=False)))
    if opt.per_day_cap is not None:
        probes.append(("max exams per day", replace(opt, per_day_cap=None)))
    user_locks = {g: t for g, t in prob.pinned.items() if prob.pin_reason.get(g) == "user"}
    out = []
    for name, o in probes:
        o.objective = False
        b = _build(prob, cfg.weights, list(prob.groups), prob.pinned, o)
        st = _solver(cfg, budget).solve(b.model)
        if st in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            out.append(f"Feasible once the {name} is removed — it is part of the infeasibility.")
    if user_locks:
        o = replace(opt, objective=False)
        pins = {g: t for g, t in prob.pinned.items() if g not in user_locks}
        b = _build(prob, cfg.weights, list(prob.groups), pins, o)
        if _solver(cfg, budget).solve(b.model) in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            out.append("Feasible once the user-locked assignments are released — the locks contribute.")
    if not out:
        out.append("No single constraint family explains the infeasibility within the diagnostic time budget.")
    return out


def _extract(solver: cp_model.CpSolver, built: _Built) -> tuple[dict[str, str], dict[str, float]]:
    mapping = {g: t for (g, t), v in built.x.items() if solver.boolean_value(v)}
    breakdown = {k: float(sum(c * solver.value(v) for c, v in lst)) for k, lst in built.terms.items()}
    return mapping, breakdown


def solve(prob: Problem, cfg: OptimizationConfig, hint: dict[str, str] | None = None) -> SolveOutcome:
    """Build and solve. Never raises for infeasible inputs: the CP-SAT status is returned as is."""
    t0 = time.perf_counter()
    opt = _options(cfg)
    hc = cfg.hard_constraints
    strategy = cfg.solver.strategy
    warnings = list(prob.warnings)
    hints = [i.message for i in prob.issues]
    stats: dict[str, int] = {
        "exam_groups": len(prob.groups),
        "timeslots": len(prob.slots),
        "pinned_groups": len(prob.pinned),
        "co_enrollment_edges": len(prob.edges),
        "distinct_student_sets": len(prob.student_sets),
    }
    if hc.allow_moving_fixed_exams:
        warnings.append("HYPOTHETICAL scenario: officially fixed common exams were allowed to move.")
    if not opt.strict:
        warnings.append("Relaxation mode: simultaneous conflicts are allowed and penalized, not forbidden.")
    if hint and not cfg.solver.use_baseline_hint:
        hint = None

    limit = cfg.solver.time_limit_seconds
    groups = list(prob.groups)
    pins = dict(prob.pinned)

    if strategy is OptimizationStrategy.TWO_PHASE and not prob.issues:
        large = prob.largest_groups(cfg.solver.two_phase_large_groups)
        phase1 = sorted(set(large) | set(pins))
        b1 = _build(prob, cfg.weights, phase1, pins, opt)
        _hint(b1, hint)
        s1 = _solver(cfg, limit * 0.4)
        st1 = s1.solve(b1.model)
        stats["phase1_groups"] = len(phase1)
        if st1 in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            placed, _ = _extract(s1, b1)
            pins = {**pins, **{g: placed[g] for g in large}}
            limit = max(0.5, limit - (time.perf_counter() - t0))
            warnings.append(
                f"Two-phase strategy: the {len(large)} largest movable groups were placed first and then held fixed; "
                "the result is not a global optimum and no global bound is reported."
            )
        else:
            warnings.append(f"Two-phase phase 1 returned {s1.status_name(st1)}; fell back to a single pass.")
            strategy = OptimizationStrategy.SINGLE_PASS

    # Warm start: the baseline usually violates strict mode, so first find the
    # feasible schedule closest to it and hint the main model with that.
    warm_budget = min(3.0, max(0.5, limit * 0.15))
    t_w = time.perf_counter()
    warm = _warm_start(prob, cfg, opt, groups, pins, hint, warm_budget)
    stats["warm_start_ms"] = int((time.perf_counter() - t_w) * 1000)
    if warm is not None:
        stats["warm_start_moved_groups"] = sum(1 for g, t in warm.items() if (hint or {}).get(g) not in (None, t))
        hint = warm
    limit = max(0.5, limit - (time.perf_counter() - t_w))

    t_b = time.perf_counter()
    built = _build(prob, cfg.weights, groups, pins, opt)
    incumbent = _full_hint(built, cfg, warm) if warm is not None else None
    if incumbent is None:
        _hint(built, hint)
    proto = built.model.proto
    stats["variables"] = len(proto.variables)
    stats["constraints"] = len(proto.constraints)
    stats["build_ms"] = int((time.perf_counter() - t_b) * 1000)  # model build + full hint only

    # the time limit is a wall-clock budget for the whole run (warm start + build + search)
    remaining = max(1.0, cfg.solver.time_limit_seconds - (time.perf_counter() - t0))
    solver = _solver(cfg, remaining)
    st = solver.solve(built.model)
    status = solver.status_name(st)
    stats["solve_ms"] = int(solver.wall_time * 1000)

    mapping = None
    obj = bound = None
    breakdown: dict[str, float] = {}
    if st == cp_model.UNKNOWN and incumbent is not None:
        # The search found nothing new in the remaining time, but the warm-start schedule is a
        # CP-SAT-verified solution of this same model (all hard constraints hold). Return it,
        # clearly labeled, with no bound claimed.
        st, status = cp_model.FEASIBLE, "FEASIBLE"
        mapping, breakdown, obj = incumbent.mapping, incumbent.breakdown, incumbent.objective
        warnings.extend(f"Input issue (ignored by the model): {i.message}" for i in prob.issues)
        warnings.append(
            "The search did not improve on the warm-start schedule within the time limit; returning that schedule "
            "(feasible for every hard constraint, closest to the baseline). Try a longer time limit."
        )
    elif st in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        warnings.extend(f"Input issue (ignored by the model): {i.message}" for i in prob.issues)
        mapping, breakdown = _extract(solver, built)
        obj = float(round(solver.objective_value))  # integer coefficients; drop float noise
        bound = float(math.ceil(solver.best_objective_bound - 1e-6))  # integer objective ⇒ integer lower bound
        if strategy is OptimizationStrategy.TWO_PHASE:
            bound = None
            if status == "OPTIMAL":
                status = "FEASIBLE"
                warnings.append("Phase 2 was solved to optimality given the phase-1 placements (reported as FEASIBLE).")
        elif status == "FEASIBLE":
            warnings.append(
                f"Time limit reached with a feasible solution; optimality not proven "
                f"(objective {obj:.0f}, best bound {bound:.0f})."
            )
    elif st == cp_model.INFEASIBLE:
        hints = hints + _diagnose(prob, cfg, opt, budget=min(5.0, max(1.0, limit / 4)))
    elif st == cp_model.UNKNOWN:
        hints.append("The solver found no solution within the time limit (status UNKNOWN); try a longer limit.")

    return SolveOutcome(
        status=status,
        assignments=mapping,
        objective_value=obj,
        best_objective_bound=bound,
        objective_breakdown=breakdown,
        runtime_seconds=round(time.perf_counter() - t0, 3),
        strategy=strategy,
        relaxed=not opt.strict,
        hypothetical=hc.allow_moving_fixed_exams,
        warnings=warnings,
        infeasibility_hints=hints if st != cp_model.OPTIMAL and st != cp_model.FEASIBLE else [],
        model_stats=stats,
    )
