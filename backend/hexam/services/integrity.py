"""Independent post-optimization verification of hard constraints.

Re-derives every check from the dataset and the config; nothing is taken from
the solver except the produced assignment.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from hexam.evaluation.rules import overlaps
from hexam.schemas import DatasetIndex, IntegrityCheck, IntegrityReport, OptimizationConfig


def verify_schedule(
    index: DatasetIndex,
    mapping: dict[str, str],
    config: OptimizationConfig,
    *,
    registrations_fingerprint_before: str | None = None,
    registrations_fingerprint_after: str | None = None,
) -> IntegrityReport:
    hc = config.hard_constraints
    checks: list[IntegrityCheck] = []

    def add(name: str, bad: list[str], ok_msg: str) -> None:
        msg = ok_msg if not bad else f"{len(bad)} problem(s): " + "; ".join(bad[:5]) + (" …" if len(bad) > 5 else "")
        checks.append(IntegrityCheck(name=name, passed=not bad, message=msg))

    groups = set(index.exam_groups)
    add(
        "every_group_scheduled_once",
        [f"{g} unassigned" for g in sorted(groups - set(mapping))] + [f"unknown group {g}" for g in sorted(set(mapping) - groups)],
        f"all {len(groups)} exam groups scheduled exactly once",
    )
    add(
        "valid_timeslot_references",
        [f"{g} → unknown slot {t}" for g, t in sorted(mapping.items()) if t not in index.timeslots],
        "all assignments reference known timeslots",
    )
    if not hc.allow_moving_fixed_exams:
        fixed = {g: e.fixed_timeslot_id for g, e in index.exam_groups.items() if e.fixed_timeslot_id}
        add(
            "official_fixed_exams_preserved",
            [f"{g} moved {t} → {mapping.get(g)}" for g, t in sorted(fixed.items()) if mapping.get(g) != t],
            f"{len(fixed)} official common exams kept in their official slots",
        )
    locks = config.fixed_assignments
    add(
        "user_locks_respected",
        [f"{f.exam_group_id} locked to {f.timeslot_id} but in {mapping.get(f.exam_group_id)}"
         for f in locks if mapping.get(f.exam_group_id) != f.timeslot_id],
        f"{len(locks)} user lock(s) respected",
    )
    excluded = set(config.excluded_timeslot_ids)
    bad = []
    for g, t in sorted(mapping.items()):
        grp = index.exam_groups.get(g)
        if t in excluded:
            bad.append(f"{g} in excluded slot {t}")
        elif grp and hc.enforce_group_restrictions and not grp.permits(t):
            bad.append(f"{g} in restricted slot {t}")
    add("only_permitted_slots_used", bad, "only official exam periods permitted for each group are used")

    # conflicts from actual student registrations and real intervals
    conflicts = 0
    affected = set()
    for s, gs in index.student_exam_groups.items():
        ex = [index.timeslots[mapping[g]] for g in gs if mapping.get(g) in index.timeslots]
        for i in range(len(ex)):
            for j in range(i + 1, len(ex)):
                if overlaps(ex[i].start, ex[i].end, ex[j].start, ex[j].end):
                    conflicts += 1
                    affected.add(s)
    if hc.forbid_simultaneous_conflicts:
        checks.append(IntegrityCheck(
            name="no_student_conflicts",
            passed=conflicts == 0,
            message="no student has overlapping exams" if conflicts == 0
            else f"{conflicts} overlapping exam pairs for {len(affected)} students",
        ))
    else:
        checks.append(IntegrityCheck(
            name="no_student_conflicts", passed=True,
            message=f"relaxation mode: {conflicts} overlapping pairs ({len(affected)} students) allowed and penalized",
        ))

    # capacity (aggregate)
    bad = []
    if hc.enforce_timeslot_capacity:
        students: dict[str, set[str]] = defaultdict(set)
        ngroups: Counter[str] = Counter(mapping.values())
        for g, t in mapping.items():
            students[t] |= index.group_students.get(g, frozenset())
        for t, ts in index.timeslots.items():
            caps_s = [c for c in (ts.max_students, hc.max_students_per_slot) if c is not None]
            caps_g = [c for c in (ts.max_exam_groups, hc.max_exam_groups_per_slot) if c is not None]
            if caps_s and len(students[t]) > min(caps_s):
                bad.append(f"{t}: {len(students[t])} students > {min(caps_s)}")
            if caps_g and ngroups[t] > min(caps_g):
                bad.append(f"{t}: {ngroups[t]} groups > {min(caps_g)}")
    add("capacity_respected", bad, "aggregate per-slot caps respected (none configured)" if not (
        hc.max_students_per_slot or hc.max_exam_groups_per_slot) else "aggregate per-slot caps respected")

    if registrations_fingerprint_before is not None:
        same = registrations_fingerprint_before == registrations_fingerprint_after
        checks.append(IntegrityCheck(
            name="registrations_unchanged", passed=same,
            message="student registrations identical before and after optimization" if same
            else "student registrations changed during the run",
        ))
    return IntegrityReport(passed=all(c.passed for c in checks), checks=checks)


def registrations_fingerprint(index: DatasetIndex) -> str:
    import hashlib

    h = hashlib.sha256()
    for s in sorted(index.student_exam_groups):
        h.update(s.encode())
        h.update(",".join(index.students[s].section_ids).encode())
        h.update(b";")
    return h.hexdigest()
