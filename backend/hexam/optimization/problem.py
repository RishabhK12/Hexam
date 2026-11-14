"""Precomputation shared by every CP-SAT model build.

Everything the solver needs is reduced to exam-group-level structures so the
model never iterates student × exam × slot:

* ``eligible[g]`` — slots group g may use (exclusions, restrictions, official
  fixes, user locks applied).
* ``student_sets`` — distinct exam-group sets → number of students holding it
  (1,000 students → ~970 sets; identical schedules share one set of Booleans).
* ``edges`` — co-enrollment graph: (g, h) → number of shared students.
* ``relations`` — time relations between slots (``SlotRelations``), built from
  the same rule functions the evaluator uses.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import combinations

from hexam.evaluation.rules import SlotRelations
from hexam.schemas import DatasetIndex, OptimizationConfig


@dataclass(frozen=True, slots=True)
class InputIssue:
    category: str  # "locks", "restrictions", "capacity", "conflicts", "per_day"
    message: str
    fatal: bool = True  # makes the strict model infeasible for certain


@dataclass(slots=True)
class Problem:
    groups: tuple[str, ...]
    slots: tuple[str, ...]
    eligible: dict[str, tuple[str, ...]]  # after pins
    permitted: dict[str, tuple[str, ...]]  # before pins (exclusions + restrictions only)
    pinned: dict[str, str]  # group -> slot, from official fixes and user locks
    pin_reason: dict[str, str]  # group -> "official" | "user"
    group_size: dict[str, int]
    student_sets: dict[tuple[str, ...], int]
    edges: dict[tuple[str, str], int]
    relations: SlotRelations
    slot_student_cap: dict[str, int]
    slot_group_cap: dict[str, int]
    issues: list[InputIssue]
    warnings: list[str]

    @classmethod
    def build(cls, index: DatasetIndex, config: OptimizationConfig) -> Problem:
        hc = config.hard_constraints
        excluded = set(config.excluded_timeslot_ids)
        rel = SlotRelations.build(index.timeslots.values(), config.evaluation)
        slots = tuple(t for t in rel.slot_ids if t not in excluded)
        groups = tuple(sorted(index.exam_groups))
        issues: list[InputIssue] = []
        warnings: list[str] = []

        for t in sorted(excluded - set(index.timeslots)):
            issues.append(InputIssue("locks", f"excluded timeslot {t} does not exist"))

        pinned: dict[str, str] = {}
        reason: dict[str, str] = {}
        if not hc.allow_moving_fixed_exams:
            for g in groups:
                fx = index.exam_groups[g].fixed_timeslot_id
                if fx:
                    pinned[g], reason[g] = fx, "official"
        for f in config.fixed_assignments:
            if f.exam_group_id not in index.exam_groups:
                issues.append(InputIssue("locks", f"lock references unknown exam group {f.exam_group_id}"))
                continue
            if f.timeslot_id not in index.timeslots:
                issues.append(InputIssue("locks", f"lock puts {f.exam_group_id} in unknown timeslot {f.timeslot_id}"))
                continue
            prev = pinned.get(f.exam_group_id)
            if prev and prev != f.timeslot_id:
                issues.append(
                    InputIssue("locks", f"lock moves officially fixed {f.exam_group_id} from {prev} to {f.timeslot_id} "
                               "(enable the hypothetical 'allow moving fixed exams' scenario to do this)")
                )
                continue
            pinned[f.exam_group_id] = f.timeslot_id
            reason.setdefault(f.exam_group_id, "user")

        eligible: dict[str, tuple[str, ...]] = {}
        permitted: dict[str, tuple[str, ...]] = {}
        for g in groups:
            grp = index.exam_groups[g]
            cand = [t for t in slots if not hc.enforce_group_restrictions or grp.permits(t)]
            permitted[g] = tuple(cand)
            if g in pinned:
                p = pinned[g]
                if p not in cand:
                    why = "excluded" if p in excluded else "not permitted by the group's restrictions"
                    issues.append(InputIssue(reason[g] == "official" and "restrictions" or "locks",
                                             f"{g} is pinned to {p}, which is {why}"))
                    cand = []
                else:
                    cand = [p]
            if not cand:
                issues.append(InputIssue("restrictions", f"{g} has no eligible timeslot"))
            eligible[g] = tuple(cand)

        sets = Counter(index.student_exam_groups.values())
        student_sets = {s: n for s, n in sorted(sets.items()) if s}
        edges: Counter[tuple[str, str]] = Counter()
        for s, n in student_sets.items():
            for a, b in combinations(s, 2):
                edges[(a, b)] += n

        # Capacity: aggregate per-slot caps (not rooms). Slot-level data cap and run-level override; the tighter wins.
        stud_cap: dict[str, int] = {}
        grp_cap: dict[str, int] = {}
        if hc.enforce_timeslot_capacity:
            for t in slots:
                ts = index.timeslots[t]
                c1 = [c for c in (ts.max_students, hc.max_students_per_slot) if c is not None]
                c2 = [c for c in (ts.max_exam_groups, hc.max_exam_groups_per_slot) if c is not None]
                if c1:
                    stud_cap[t] = min(c1)
                if c2:
                    grp_cap[t] = min(c2)
            if not stud_cap and not grp_cap:
                warnings.append(
                    "No seat/room capacity data exists for this dataset; no capacity constraint was applied. "
                    "Set an aggregate per-slot cap to model one (rooms are not assigned)."
                )
            else:
                warnings.append(
                    "Capacity is modeled as an aggregate per-slot cap on students/exam groups, not as room assignments."
                )

        sizes = {g: index.group_size(g) for g in groups}
        prob = cls(groups, slots, eligible, permitted, pinned, reason, sizes, student_sets, dict(edges), rel,
                   stud_cap, grp_cap, issues, warnings)
        prob._precheck(config)
        return prob

    def _precheck(self, config: OptimizationConfig) -> None:
        """Cheap certain-infeasibility checks among pinned groups (explanations only;
        the solver still runs and reports its own status)."""
        hc = config.hard_constraints
        pinned = self.pinned
        if hc.forbid_simultaneous_conflicts:
            for (a, b), n in self.edges.items():
                if a in pinned and b in pinned and pinned[b] in self.relations.overlap.get(pinned[a], ()):
                    self.issues.append(InputIssue(
                        "conflicts",
                        f"pinned groups {a} ({pinned[a]}) and {b} ({pinned[b]}) overlap and share {n} student(s); "
                        "strict mode cannot schedule this",
                    ))
        load_s: Counter[str] = Counter()
        load_g: Counter[str] = Counter()
        for g, t in pinned.items():
            load_s[t] += self.group_size[g]
            load_g[t] += 1
        for t, cap in self.slot_student_cap.items():
            if load_s[t] > cap:
                self.issues.append(InputIssue("capacity", f"pinned groups put {load_s[t]} students in {t} (cap {cap})"))
        for t, cap in self.slot_group_cap.items():
            if load_g[t] > cap:
                self.issues.append(InputIssue("capacity", f"{load_g[t]} groups pinned to {t} (cap {cap})"))
        if self.slot_group_cap and len(self.slot_group_cap) == len(self.slots):
            room = sum(self.slot_group_cap.values())
            if room < len(self.groups):
                self.issues.append(InputIssue(
                    "capacity", f"{len(self.groups)} exam groups but only {room} group places across all slots "
                    "(per-slot group cap × slots) — provably infeasible"))
        total = sum(self.group_size.values())
        if self.slot_student_cap and len(self.slot_student_cap) == len(self.slots):
            if sum(self.slot_student_cap.values()) < total:
                self.issues.append(InputIssue(
                    "capacity", f"total slot capacity {sum(self.slot_student_cap.values())} < {total} exam sittings"))

    # ---------------------------------------------------------- helpers

    def restricted_sets(self, groups: Iterable[str]) -> dict[tuple[str, ...], int]:
        keep = set(groups)
        out: Counter[tuple[str, ...]] = Counter()
        for s, n in self.student_sets.items():
            r = tuple(g for g in s if g in keep)
            if r:
                out[r] += n
        return dict(sorted(out.items()))

    def largest_groups(self, k: int) -> list[str]:
        free = [g for g in self.groups if g not in self.pinned]
        return sorted(free, key=lambda g: (-self.group_size[g], g))[:k]
