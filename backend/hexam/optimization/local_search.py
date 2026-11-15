"""Group-level move scorer and Kempe-chain local search (numpy only, no OR-Tools).

The CP-SAT model proves things but explores slowly; this module does the
opposite. It scores a schedule from the same precomputed structures the model
uses (``Problem``: co-enrollment edges, distinct student exam-sets, slot
relations), so a *move* costs a few array operations instead of a pass over
every student.

Scoring (identical to the evaluator's weighted penalty; tests assert equality)
    pair metrics   cost[t, u] per timeslot pair, weighted by shared students:
                   back-to-back, late→early, and conflicts in relaxed mode
    set metrics    per distinct student exam-set, the number of its exams in
                   each rolling window; a set is hit when any window reaches
                   the threshold (3 in 24 h, 4 in 48 h), counted once

Hard constraints (a move that breaks one is never taken)
    eligible slots (restrictions, exclusions, official fixes, user locks),
    strict no-conflict rule, aggregate capacity caps, per-day cap, and the
    relaxed-mode cap on one student's overlapping exams

Search
    Under the strict no-conflict rule very few single-group moves are legal
    (145 of 940 on the Fall 2026 data), so the basic move is a Kempe chain:
    send group g to slot u, send the groups in u that share students with g
    back to g's old slot, and so on until nothing clashes. Simulated annealing
    proposes random chains in rounds, each restarting from the best schedule
    with the temperature reset; every round ends with a steepest descent over
    single moves, all chains, whole-slot swaps and whole-day swaps.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

import numpy as np

from hexam.schemas import ImpactMetricKey, PenaltyWeights

from .problem import Problem

K = ImpactMetricKey
_BIG = np.iinfo(np.int64).max // 4


@dataclass(slots=True)
class _Family:
    """Sets of timeslots ("windows") a student exam-set is counted against."""

    key: str | None  # metric key for a penalized family; None for a hard cap
    member: np.ndarray  # (T, W) float32: member[t, w] = 1 when slot t is in window w
    threshold: int  # the family is hit when a count reaches this
    weight: int  # penalty per affected student (0 for a hard cap)
    counts: np.ndarray  # (W, sets) float32: exams of each student set per window, for the current schedule

    @property
    def hard(self) -> bool:
        return self.key is None


@dataclass(slots=True)
class LocalSearchResult:
    assignments: dict[str, str]
    penalty: int
    breakdown: dict[str, int]
    start_penalty: int
    rounds: int  # annealing rounds started
    proposals: int  # Kempe-chain moves scored
    accepted: int  # ... and taken
    seconds: float


class MoveScorer:
    """Scores schedules and single-group moves for one ``Problem``."""

    def __init__(self, prob: Problem, weights: PenaltyWeights, *, strict: bool = True, capacity: bool = True,
                 per_day_cap: int | None = None, max_per_slot: int = 2):
        rel = prob.relations
        self.groups = prob.groups
        self.slot_ids = rel.slot_ids
        G, T = len(self.groups), len(self.slot_ids)
        self.G, self.T = G, T
        self.strict = strict
        gi = {g: i for i, g in enumerate(self.groups)}
        si = {t: i for i, t in enumerate(self.slot_ids)}
        self._gi, self._si = gi, si

        self.allowed = np.zeros((G, T), dtype=bool)
        for g, slots in prob.eligible.items():
            for t in slots:
                self.allowed[gi[g], si[t]] = True
        self.cols = [np.flatnonzero(self.allowed[g]) for g in range(G)]
        self.movable = [g for g in range(G) if len(self.cols[g]) > 1]

        # co-enrollment graph
        self.N = np.zeros((G, G), dtype=np.int64)
        for (a, b), n in prob.edges.items():
            self.N[gi[a], gi[b]] = self.N[gi[b], gi[a]] = n
        edges = sorted((gi[a], gi[b], n) for (a, b), n in prob.edges.items())
        self.eg = np.array([e[0] for e in edges], dtype=np.int64)
        self.eh = np.array([e[1] for e in edges], dtype=np.int64)
        self.en = np.array([e[2] for e in edges], dtype=np.int64)

        # timeslot-pair relations (symmetric: the order of the two exams does not matter for the count)
        def matrix(pairs) -> np.ndarray:
            m = np.zeros((T, T), dtype=np.int64)
            for t, u in pairs:
                m[si[t], si[u]] = m[si[u], si[t]] = 1
            return m

        self.overlap = matrix((t, u) for t, others in rel.overlap.items() for u in others)
        self.pair: dict[str, np.ndarray] = {}
        if not strict and weights.simultaneous_conflicts:
            self.pair[K.SIMULTANEOUS_CONFLICTS.value] = weights.simultaneous_conflicts * self.overlap
        if weights.back_to_back:
            self.pair[K.BACK_TO_BACK.value] = weights.back_to_back * matrix(rel.b2b)
        if weights.night_to_morning:
            self.pair[K.NIGHT_TO_MORNING.value] = weights.night_to_morning * matrix(rel.late_early)
        self.pair_total = sum(self.pair.values(), np.zeros((T, T), dtype=np.int64))

        # distinct student exam-sets: membership matrix (sets × groups) and the flat list of
        # (set, group) memberships, so every "what if g moved" question is one matrix product
        sets = list(prob.student_sets.items())
        width = max((len(s) for s, _ in sets), default=1)
        self.set_n = np.array([n for _, n in sets], dtype=np.int64)
        self.set_matrix = np.zeros((G, len(sets)), dtype=np.float32)  # [g, s] = 1 when set s holds group g
        mem_s, mem_g = [], []
        for i, (s, _) in enumerate(sets):
            for g in s:
                self.set_matrix[gi[g], i] = 1
                mem_s.append(i)
                mem_g.append(gi[g])
        self.mem_s = np.array(mem_s, dtype=np.int64)
        self.mem_g = np.array(mem_g, dtype=np.int64)
        # group_rows[g, m] = students holding membership m when it belongs to g, else 0
        self.group_rows = np.zeros((G, len(mem_s)), dtype=np.float64)
        self.group_rows[self.mem_g, np.arange(len(mem_s))] = self.set_n[self.mem_s]
        self.sets_of = [self.mem_s[self.mem_g == g] for g in range(G)]

        self.families: list[_Family] = []

        def family(key: str | None, subsets, threshold: int, weight: int) -> None:
            subsets = list(dict.fromkeys(tuple(sorted(s)) for s in subsets if len(s)))
            if not subsets or width < threshold:  # no student has enough exams to reach the threshold
                return
            member = np.zeros((T, len(subsets)), dtype=np.float32)
            for w, sub in enumerate(subsets):
                for t in sub:
                    member[si[t], w] = 1
            self.families.append(_Family(key, member, threshold, weight, np.zeros((len(subsets), len(sets)), np.float32)))

        if weights.three_in_24h:
            family(K.THREE_IN_24H.value, rel.windows24, 3, weights.three_in_24h)
        if weights.four_in_48h:
            family(K.FOUR_IN_48H.value, rel.windows48, 4, weights.four_in_48h)
        if per_day_cap is not None:
            family(None, rel.days.values(), per_day_cap + 1, 0)
        if not strict:
            family(None, (rel.overlap[t] for t in prob.slots), max_per_slot + 1, 0)

        # aggregate capacity
        self.size = np.array([prob.group_size[g] for g in self.groups], dtype=np.int64)
        self.cap_students = np.full(T, _BIG, dtype=np.int64)
        self.cap_groups = np.full(T, _BIG, dtype=np.int64)
        if capacity:
            for t, cap in prob.slot_student_cap.items():
                self.cap_students[si[t]] = cap
            for t, cap in prob.slot_group_cap.items():
                self.cap_groups[si[t]] = cap
        self.has_caps = bool((self.cap_students < _BIG).any() or (self.cap_groups < _BIG).any())

        self.days = [np.array([si[t] for t in day], dtype=np.int64) for day in rel.days.values()]
        self.nbrs = [set(np.flatnonzero(self.N[g]).tolist()) for g in range(G)]

        # current schedule (set by ``load``)
        self.a = np.zeros(G, dtype=np.int64)
        self.load_students = np.zeros(T, dtype=np.int64)
        self.load_groups = np.zeros(T, dtype=np.int64)
        self._rows = np.arange(G)

    # ------------------------------------------------------------ conversions

    def encode(self, mapping: dict[str, str]) -> np.ndarray:
        """Slot index per group; -1 for a group the mapping leaves out."""
        a = np.full(self.G, -1, dtype=np.int64)
        for g, t in mapping.items():
            a[self._gi[g]] = self._si[t]
        return a

    def decode(self, a: np.ndarray | None = None) -> dict[str, str]:
        a = self.a if a is None else a
        return {g: self.slot_ids[int(a[i])] for i, g in enumerate(self.groups)}

    # ------------------------------------------------------------ full scoring

    def _counts(self, fam: _Family, a: np.ndarray) -> np.ndarray:
        return fam.member[a].T @ self.set_matrix  # (W, sets): small integers, exact in float32

    def evaluate(self, a: np.ndarray) -> tuple[bool, int, dict[str, int]]:
        """(all hard constraints hold, weighted penalty, penalty per metric) for a complete schedule.
        Does not touch the current state."""
        T = self.T
        body = a
        breakdown = {k.value: 0 for k in K}
        if (body < 0).any():
            return False, 0, breakdown
        ok = bool(self.allowed[self._rows, body].all())
        s, t = a[self.eg], a[self.eh]
        if self.strict and len(self.en):
            ok = ok and not bool((self.overlap[s, t] * self.en).any())
        for key, m in self.pair.items():
            breakdown[key] = int(m[s, t] @ self.en) if len(self.en) else 0
        for fam in self.families:
            hit = self._counts(fam, a).max(axis=0) >= fam.threshold
            if fam.hard:
                ok = ok and not bool(hit.any())
            else:
                breakdown[fam.key] = fam.weight * int(self.set_n @ hit)
        if self.has_caps:
            students = np.bincount(body, weights=self.size, minlength=T).astype(np.int64)
            groups = np.bincount(body, minlength=T)
            ok = ok and bool((students <= self.cap_students).all() and (groups <= self.cap_groups).all())
        return ok, sum(breakdown.values()), breakdown

    def score(self, mapping: dict[str, str]) -> tuple[bool, int, dict[str, int]]:
        return self.evaluate(self.encode(mapping))

    # ------------------------------------------------------------ incremental state

    def load(self, a: np.ndarray) -> None:
        """Make ``a`` the current schedule."""
        self.a = a.copy()
        body = self.a
        for fam in self.families:
            fam.counts = self._counts(fam, self.a)
        self.load_students = np.bincount(body, weights=self.size, minlength=self.T).astype(np.int64)
        self.load_groups = np.bincount(body, minlength=self.T).astype(np.int64)

    def apply(self, g: int, t: int) -> None:
        """Move group ``g`` to slot ``t`` in the current schedule."""
        c = int(self.a[g])
        if c == t:
            return
        idx = self.sets_of[g]
        for fam in self.families:
            fam.counts[:, idx] += (fam.member[t] - fam.member[c])[:, None]
        self.load_students[c] -= self.size[g]
        self.load_students[t] += self.size[g]
        self.load_groups[c] -= 1
        self.load_groups[t] += 1
        self.a[g] = t

    def move_costs(self) -> np.ndarray:
        """(G, T) change in weighted penalty for moving each group to each slot, from the current
        schedule. ``inf`` marks a move that is not allowed (or leaves the group where it is)."""
        body = self.a
        pair = self.N @ self.pair_total[body]  # pair[g, t]: pair cost of g at t against every other group
        delta = (pair - pair[self._rows, body][:, None]).astype(np.float64)
        bad = ~self.allowed
        if self.strict:
            bad |= (self.N @ self.overlap[body]) > 0
        if self.has_caps:
            bad |= self.load_students[None, :] + self.size[:, None] > self.cap_students[None, :]
            bad |= self.load_groups[None, :] + 1 > self.cap_groups[None, :]
        if len(self.mem_s):
            for fam in self.families:
                # one row per (set, group) membership: the set's window counts with that group taken out
                without = fam.counts[:, self.mem_s] - fam.member[body[self.mem_g]].T  # (W, memberships)
                short = (without >= fam.threshold - 1).astype(np.float32)  # one exam short in this window
                hit = (fam.member @ short) > 0  # (T, memberships): placing the group in t completes a window
                hit |= (without.max(axis=0) >= fam.threshold)[None, :]  # hit regardless of this group
                per_group = self.group_rows @ hit.T  # (G, T): students hit among the sets of g, with g in t
                if fam.hard:
                    bad |= per_group > 0
                else:
                    delta += fam.weight * (per_group - per_group[self._rows, body][:, None])
        delta[bad] = np.inf
        delta[self._rows, body] = np.inf
        return delta

    # ------------------------------------------------------------ compound moves

    def cost_of(self, new: np.ndarray) -> tuple[bool, int]:
        """(hard constraints hold, weighted penalty) of ``new``, scored as a change from the current schedule."""
        ch = np.flatnonzero(new != self.a)
        if not self.allowed[ch, new[ch]].all():
            return False, 0
        s, t = new[self.eg], new[self.eh]
        if self.strict and self.overlap[s, t].any():
            return False, 0
        total = int(self.pair_total[s, t] @ self.en) if len(self.en) else 0
        for fam in self.families:
            shift = (fam.member[new[ch]] - fam.member[self.a[ch]]).T @ self.set_matrix[ch]  # (W, sets)
            hit = (fam.counts + shift).max(axis=0) >= fam.threshold
            if fam.hard:
                if hit.any():
                    return False, 0
            else:
                total += fam.weight * int(self.set_n @ hit)
        if self.has_caps:
            students = np.bincount(new, weights=self.size, minlength=self.T)
            groups = np.bincount(new, minlength=self.T)
            if (students > self.cap_students).any() or (groups > self.cap_groups).any():
                return False, 0
        return True, total

    def kempe(self, g: int, u: int) -> tuple[np.ndarray, np.ndarray]:
        """Move ``g`` to slot ``u`` and let every group that would then share students with a
        newcomer trade places, transitively, between the two slots. Returns (schedule, moved groups)."""
        body = self.a
        t = int(body[g])
        chain, seen, stack = [g], {g}, [g]
        while stack:
            y = stack.pop()
            other = u if body[y] == t else t
            for z in self.nbrs[y]:
                if z not in seen and body[z] == other:
                    seen.add(z)
                    chain.append(z)
                    stack.append(z)
        idx = np.array(chain, dtype=np.int64)
        new = body.copy()
        new[idx] = t + u - new[idx]
        return new, idx

    def remap(self, target: dict[int, int]) -> np.ndarray:
        """Send the contents of each slot to its target slot, leaving groups that may not go there in place."""
        new = self.a.copy()
        for g in self.movable:
            dest = target.get(int(self.a[g]))
            if dest is not None and self.allowed[g, dest]:
                new[g] = dest
        return new

    def compound_candidates(self):
        """Schedules several groups away from the current one (deduplicated):

        * Kempe chains — for two slots, a connected component of the co-enrollment graph among the
          groups in them trades places, so no new conflict appears between the two slots;
        * slot swaps — two slots exchange their whole contents;
        * day swaps — two days with the same number of slots exchange their whole contents.
        """
        body = self.a
        T = self.T
        seen = {body.tobytes()}
        by_slot: list[list[int]] = [[] for _ in range(T)]
        for g in range(self.G):
            by_slot[int(body[g])].append(g)

        def fresh(new: np.ndarray) -> bool:
            key = new.tobytes()
            if key in seen:
                return False
            seen.add(key)
            return True

        for t in range(T):
            for u in range(t + 1, T):
                if not by_slot[t] and not by_slot[u]:
                    continue
                new = self.remap({t: u, u: t})
                if fresh(new):
                    yield new
                if not by_slot[t] or not by_slot[u]:
                    continue
                nodes = set(by_slot[t]) | set(by_slot[u])
                while nodes:
                    x = nodes.pop()
                    chain, stack = [x], [x]
                    while stack:
                        y = stack.pop()
                        for z in self.nbrs[y] & nodes:
                            if body[z] != body[y]:
                                nodes.discard(z)
                                chain.append(z)
                                stack.append(z)
                    if len(chain) > 1:  # a chain of one is a plain single move, already covered
                        idx = np.array(chain, dtype=np.int64)
                        new = body.copy()
                        new[idx] = t + u - new[idx]
                        if fresh(new):
                            yield new
        for i, d1 in enumerate(self.days):
            for d2 in self.days[i + 1:]:
                if len(d1) != len(d2):
                    continue
                target = {int(x): int(y) for x, y in zip(d1, d2)} | {int(y): int(x) for x, y in zip(d1, d2)}
                new = self.remap(target)
                if fresh(new):
                    yield new

    def best_compound(self, current: int, deadline: float) -> tuple[np.ndarray | None, int]:
        """The best compound move that improves on ``current`` (None when there is none)."""
        found, found_cost = None, current
        for new in self.compound_candidates():
            ok, cost = self.cost_of(new)
            if ok and cost < found_cost:
                found, found_cost = new, cost
            if time.perf_counter() >= deadline:
                break
        return found, found_cost

    def descend(self, current: int, deadline: float) -> int:
        """Steepest descent from the current schedule: best single move while one improves, otherwise the
        best compound move, until neither helps. Returns the penalty reached."""
        while time.perf_counter() < deadline:
            delta = self.move_costs()
            lowest = delta.min()
            if lowest < 0:
                g, t = np.unravel_index(int(np.argmin(delta)), delta.shape)
                self.apply(int(g), int(t))
                current += int(lowest)
                continue
            found, cost = self.best_compound(current, deadline)
            if found is None:
                break
            self.load(found)
            current = cost
        return current


START_TEMPERATURE_FACTOR = 0.5  # start temperature = this × the mean worsening move
COOLING_RATIO = 150.0  # start temperature / end temperature within a round


def _start_temperature(scorer: MoveScorer, rng: np.random.Generator, current: int, samples: int = 400) -> float:
    """Annealing start temperature: the typical cost of a worsening move from the starting schedule,
    so the scale follows the penalty weights instead of being a fixed number."""
    worse = []
    for _ in range(samples):
        g = scorer.movable[int(rng.integers(len(scorer.movable)))]
        cols = scorer.cols[g]
        u = int(cols[int(rng.integers(len(cols)))])
        if u == scorer.a[g]:
            continue
        ok, cost = scorer.cost_of(scorer.kempe(g, u)[0])
        if ok and cost > current:
            worse.append(cost - current)
    return max(1.0, float(np.mean(worse)) * START_TEMPERATURE_FACTOR) if worse else 1.0


def local_search(scorer: MoveScorer, start: dict[str, str], *, time_limit: float, seed: int = 0,
                 round_moves: int | None = None, max_rounds: int | None = None,
                 stall_rounds: int = 3) -> LocalSearchResult | None:
    """Kempe-chain simulated annealing from ``start``, which must satisfy every hard constraint
    (returns None otherwise). The schedule returned is never worse than ``start`` and always legal.

    Runs rounds of ``round_moves`` proposals (default: 40 per movable group and slot, at most 40,000)
    until ``time_limit`` seconds, ``max_rounds`` rounds, ``stall_rounds`` rounds in a row without a
    better schedule, or a penalty of zero. With ``max_rounds`` set and enough time, the result
    depends only on ``seed``.
    """
    t0 = time.perf_counter()
    deadline = t0 + time_limit
    a0 = scorer.encode(start)
    ok, start_penalty, _ = scorer.evaluate(a0)
    if not ok:
        return None
    scorer.load(a0)
    rng = np.random.default_rng(seed)
    best = scorer.descend(start_penalty, deadline) if scorer.movable else start_penalty
    best_a = scorer.a.copy()
    rounds = proposals = accepted = stall = 0

    if scorer.movable:
        if round_moves is None:
            round_moves = min(40_000, 40 * len(scorer.movable) * scorer.T)
        hot = _start_temperature(scorer, rng, best)
        cooling = (1.0 / COOLING_RATIO) ** (1.0 / max(1, round_moves))
    while scorer.movable and best > 0 and time.perf_counter() < deadline and stall < stall_rounds:
        if max_rounds is not None and rounds >= max_rounds:
            break
        rounds += 1
        scorer.load(best_a)
        cur, temp = best, hot
        round_best, round_a = best, best_a
        for i in range(round_moves):
            if i % 256 == 0 and time.perf_counter() >= deadline:
                break
            temp *= cooling
            g = scorer.movable[int(rng.integers(len(scorer.movable)))]
            cols = scorer.cols[g]
            u = int(cols[int(rng.integers(len(cols)))])
            if u == scorer.a[g]:
                continue
            if scorer.strict or rng.random() < 0.5:
                new, idx = scorer.kempe(g, u)
            else:  # relaxed mode: a plain move may also create a (penalized) conflict
                new, idx = scorer.a.copy(), np.array([g], dtype=np.int64)
                new[g] = u
            proposals += 1
            ok, cost = scorer.cost_of(new)
            if not ok:
                continue
            if cost <= cur or rng.random() < math.exp((cur - cost) / temp):
                for x in idx:
                    scorer.apply(int(x), int(new[x]))
                cur = cost
                accepted += 1
                if cur < round_best:
                    round_best, round_a = cur, scorer.a.copy()
        scorer.load(round_a)
        round_best = scorer.descend(round_best, deadline)
        if round_best < best:
            best, best_a, stall = round_best, scorer.a.copy(), 0
        else:
            stall += 1

    ok, penalty, breakdown = scorer.evaluate(best_a)
    assert ok and penalty == best, "local search state drifted from the full score"
    return LocalSearchResult(
        assignments=scorer.decode(best_a),
        penalty=int(best),
        breakdown=breakdown,
        start_penalty=int(start_penalty),
        rounds=rounds,
        proposals=proposals,
        accepted=accepted,
        seconds=round(time.perf_counter() - t0, 3),
    )
