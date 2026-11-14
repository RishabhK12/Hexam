"""Dataset bundle, lookup index for solver/evaluator, and cross-record validation.

Per-record rules live on each model (Pydantic validators). Rules that need more
than one record — missing references, duplicate IDs, conflicting assignments,
overlapping timeslots — live here and produce a ``ValidationReport`` instead of
raising, so the API can show every problem at once.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta

from .base import HexamModel, find_duplicates
from .courses import Course, Section
from .exam_groups import ExamGroup, ExamGroupKind
from .optimization import OptimizationConfig
from .schedules import ExamSchedule, ScheduleKind
from .students import Student
from .timeslots import ExamTimeslot
from .validation import ValidationReport


class Dataset(HexamModel):
    """Everything the solver needs for one term. Stored as one JSON file per
    collection (see ``hexam.storage``)."""

    courses: list[Course]
    sections: list[Section]
    exam_groups: list[ExamGroup]
    timeslots: list[ExamTimeslot]
    students: list[Student]

    def build_index(self) -> DatasetIndex:
        return DatasetIndex.build(self)

    def validate_integrity(self) -> ValidationReport:
        return validate_dataset(self)


# ------------------------------------------------------------------------ index


@dataclass(frozen=True, slots=True)
class DatasetIndex:
    """Precomputed lookups shared by the solver, evaluator and API.

    Built tolerantly: dangling references are skipped (``validate_dataset``
    reports them). Timeslot collections are in chronological order.
    """

    courses: dict[str, Course]
    sections: dict[str, Section]
    exam_groups: dict[str, ExamGroup]
    timeslots: dict[str, ExamTimeslot]
    students: dict[str, Student]
    sections_by_course: dict[str, list[str]]
    sections_by_group: dict[str, list[str]]
    student_exam_groups: dict[str, tuple[str, ...]]
    group_students: dict[str, frozenset[str]]
    timeslots_by_day: dict[date, list[str]]
    consecutive_pairs: list[tuple[str, str]]
    overnight_pairs: list[tuple[str, str]]

    @classmethod
    def build(cls, ds: Dataset) -> DatasetIndex:
        slots_sorted = sorted(ds.timeslots, key=lambda t: t.start)
        sections = {s.section_id: s for s in ds.sections}

        sections_by_course: dict[str, list[str]] = defaultdict(list)
        sections_by_group: dict[str, list[str]] = defaultdict(list)
        for s in ds.sections:
            sections_by_course[s.course_id].append(s.section_id)
            if s.exam_group_id is not None:
                sections_by_group[s.exam_group_id].append(s.section_id)

        student_groups: dict[str, tuple[str, ...]] = {}
        group_students: dict[str, set[str]] = defaultdict(set)
        for st in ds.students:
            groups = sorted(
                {
                    sec.exam_group_id
                    for sid in st.section_ids
                    if (sec := sections.get(sid)) is not None and sec.exam_group_id is not None
                }
            )
            student_groups[st.student_id] = tuple(groups)
            for g in groups:
                group_students[g].add(st.student_id)

        by_day: dict[date, list[str]] = defaultdict(list)
        for t in slots_sorted:
            by_day[t.local_date].append(t.timeslot_id)
        slot_map = {t.timeslot_id: t for t in slots_sorted}

        consecutive: list[tuple[str, str]] = []
        overnight: list[tuple[str, str]] = []
        for day, ids in by_day.items():
            consecutive.extend(zip(ids, ids[1:]))
            nxt = by_day.get(day + timedelta(days=1))
            if nxt and slot_map[ids[-1]].is_evening:
                overnight.append((ids[-1], nxt[0]))

        return cls(
            courses={c.course_id: c for c in ds.courses},
            sections=sections,
            exam_groups={g.exam_group_id: g for g in ds.exam_groups},
            timeslots=slot_map,
            students={s.student_id: s for s in ds.students},
            sections_by_course=dict(sections_by_course),
            sections_by_group=dict(sections_by_group),
            student_exam_groups=student_groups,
            group_students={g: frozenset(s) for g, s in group_students.items()},
            timeslots_by_day=dict(by_day),
            consecutive_pairs=consecutive,
            overnight_pairs=overnight,
        )

    def group_size(self, exam_group_id: str) -> int:
        return len(self.group_students.get(exam_group_id, ()))


# ------------------------------------------------------------------- validation


def _check_unique(report: ValidationReport, entity: str, ids: Iterable[str]) -> None:
    for dup in find_duplicates(list(ids)):
        report.error("duplicate_id", f"duplicate {entity} id {dup!r}", entity, dup)


def validate_dataset(ds: Dataset) -> ValidationReport:
    r = ValidationReport()
    _check_unique(r, "course", (c.course_id for c in ds.courses))
    _check_unique(r, "section", (s.section_id for s in ds.sections))
    _check_unique(r, "exam_group", (g.exam_group_id for g in ds.exam_groups))
    _check_unique(r, "timeslot", (t.timeslot_id for t in ds.timeslots))
    _check_unique(r, "student", (s.student_id for s in ds.students))
    for crn in find_duplicates([s.crn for s in ds.sections if s.crn is not None]):
        r.error("duplicate_crn", f"CRN {crn} used by more than one section", "section", crn)
    for course_id, code in find_duplicates([(s.course_id, s.section_code) for s in ds.sections]):
        r.error("duplicate_section_code", f"{course_id} has two sections coded {code!r}", "course", course_id)

    idx = ds.build_index()

    # Sections -> courses / exam groups
    for s in ds.sections:
        if s.course_id not in idx.courses:
            r.error("missing_reference", f"section {s.section_id} references unknown course {s.course_id}", "section", s.section_id)
        if s.exam_group_id is not None and s.exam_group_id not in idx.exam_groups:
            r.error("missing_reference", f"section {s.section_id} references unknown exam group {s.exam_group_id}", "section", s.section_id)

    # Exam groups -> timeslots; structural sanity
    for g in ds.exam_groups:
        refs = [g.fixed_timeslot_id, *(g.allowed_timeslot_ids or []), *g.forbidden_timeslot_ids]
        for t in refs:
            if t is not None and t not in idx.timeslots:
                r.error("missing_reference", f"exam group {g.exam_group_id} references unknown timeslot {t}", "exam_group", g.exam_group_id)
        if not idx.sections_by_group.get(g.exam_group_id):
            r.warning("empty_exam_group", f"exam group {g.exam_group_id} has no sections", "exam_group", g.exam_group_id)
        if g.kind is ExamGroupKind.COMMON:
            for course_id in {idx.sections[s].course_id for s in idx.sections_by_group.get(g.exam_group_id, [])}:
                other = {
                    idx.sections[s].exam_group_id
                    for s in idx.sections_by_course.get(course_id, [])
                    if idx.sections[s].exam_group_id not in (None, g.exam_group_id)
                }
                if other:
                    r.warning("split_common_exam", f"{course_id} is in common exam {g.exam_group_id} but some sections use {sorted(other)}", "exam_group", g.exam_group_id)

    # Groups pinned to the same slot beyond its group capacity
    pinned: dict[str, int] = defaultdict(int)
    for g in ds.exam_groups:
        if g.fixed_timeslot_id:
            pinned[g.fixed_timeslot_id] += 1
    for t_id, n in pinned.items():
        t = idx.timeslots.get(t_id)
        if t and t.max_exam_groups is not None and n > t.max_exam_groups:
            r.error("capacity_exceeded", f"{n} exam groups fixed to {t_id} (max {t.max_exam_groups})", "timeslot", t_id)

    # Timeslots: no overlaps
    slots = sorted(ds.timeslots, key=lambda t: t.start)
    for a, b in zip(slots, slots[1:]):
        if a.overlaps(b):
            r.error("overlapping_timeslots", f"timeslots {a.timeslot_id} and {b.timeslot_id} overlap", "timeslot", b.timeslot_id)

    # Students -> sections; duplicate course registration; forced/meeting conflicts
    registered: dict[str, int] = defaultdict(int)
    for st in ds.students:
        known = []
        for sid in st.section_ids:
            if sid in idx.sections:
                known.append(idx.sections[sid])
                registered[sid] += 1
            else:
                r.error("missing_reference", f"student {st.student_id} registered for unknown section {sid}", "student", st.student_id)
        exam_courses = [s.course_id for s in known if s.has_final_exam]
        for course_id in find_duplicates(exam_courses):
            r.error("duplicate_course_registration", f"student {st.student_id} has multiple exam-bearing sections of {course_id}", "student", st.student_id)
        by_group: dict[str, list[str]] = defaultdict(list)
        for s in known:
            if s.exam_group_id is not None:
                by_group[s.exam_group_id].append(s.course_id)
        for gid, courses in by_group.items():
            if len(set(courses)) > 1:
                r.warning("forced_conflict", f"student {st.student_id} has {sorted(set(courses))} in one exam group {gid}", "student", st.student_id)
        for i, a in enumerate(known):
            for b in known[i + 1 :]:
                if any(ma.overlaps(mb) for ma in a.meeting_times for mb in b.meeting_times):
                    r.warning("registration_time_conflict", f"student {st.student_id}: {a.section_id} and {b.section_id} meet at the same time", "student", st.student_id)

    for sid, n in registered.items():
        if n > idx.sections[sid].enrollment:
            r.warning("enrollment_mismatch", f"section {sid} has {n} registered students but enrollment {idx.sections[sid].enrollment}", "section", sid)

    return r


def validate_schedule(
    ds: Dataset,
    schedule: ExamSchedule,
    *,
    require_complete: bool | None = None,
    index: DatasetIndex | None = None,
) -> ValidationReport:
    """Check a schedule against the dataset. ``require_complete`` defaults to
    True for optimized schedules (missing groups are errors) and False otherwise."""
    r = ValidationReport()
    idx = index or ds.build_index()
    sid = schedule.schedule_id
    if require_complete is None:
        require_complete = schedule.kind is ScheduleKind.OPTIMIZED

    groups_per_slot: dict[str, int] = defaultdict(int)
    students_per_slot: dict[str, set[str]] = defaultdict(set)
    for a in schedule.assignments:
        g = idx.exam_groups.get(a.exam_group_id)
        if g is None:
            r.error("missing_reference", f"schedule {sid} assigns unknown exam group {a.exam_group_id}", "schedule", sid)
        if a.timeslot_id not in idx.timeslots:
            r.error("missing_reference", f"schedule {sid} uses unknown timeslot {a.timeslot_id}", "schedule", sid)
            continue
        if g is None:
            continue
        if g.fixed_timeslot_id is not None and a.timeslot_id != g.fixed_timeslot_id:
            r.error("conflicting_assignment", f"{g.exam_group_id} is fixed to {g.fixed_timeslot_id} but scheduled in {a.timeslot_id}", "schedule", sid)
        elif not g.permits(a.timeslot_id):
            r.error("restriction_violation", f"{g.exam_group_id} may not be scheduled in {a.timeslot_id}", "schedule", sid)
        groups_per_slot[a.timeslot_id] += 1
        students_per_slot[a.timeslot_id] |= idx.group_students.get(g.exam_group_id, frozenset())

    assigned = {a.exam_group_id for a in schedule.assignments}
    for gid in idx.sections_by_group:
        if gid in idx.exam_groups and gid not in assigned:
            msg = f"schedule {sid} does not assign exam group {gid}"
            (r.error if require_complete else r.warning)("unassigned_exam_group", msg, "schedule", sid)

    for t_id, t in idx.timeslots.items():
        if t.max_exam_groups is not None and groups_per_slot[t_id] > t.max_exam_groups:
            r.warning("capacity_exceeded", f"{t_id} holds {groups_per_slot[t_id]} exam groups (max {t.max_exam_groups})", "timeslot", t_id)
        if t.max_students is not None and len(students_per_slot[t_id]) > t.max_students:
            r.warning("capacity_exceeded", f"{t_id} holds {len(students_per_slot[t_id])} students (max {t.max_students})", "timeslot", t_id)
    return r


def validate_config(
    ds: Dataset,
    config: OptimizationConfig,
    *,
    schedules: Iterable[ExamSchedule] | None = None,
    index: DatasetIndex | None = None,
) -> ValidationReport:
    """Check a run config against the dataset (and known schedules, if given)."""
    r = ValidationReport()
    idx = index or ds.build_index()
    cid = config.config_id
    excluded = set(config.excluded_timeslot_ids)

    for t in excluded - set(idx.timeslots):
        r.error("missing_reference", f"config {cid} excludes unknown timeslot {t}", "config", cid)
    if idx.timeslots and excluded >= set(idx.timeslots):
        r.error("no_available_timeslots", f"config {cid} excludes every timeslot", "config", cid)

    for f in config.fixed_assignments:
        g = idx.exam_groups.get(f.exam_group_id)
        if g is None:
            r.error("missing_reference", f"config {cid} fixes unknown exam group {f.exam_group_id}", "config", cid)
        if f.timeslot_id not in idx.timeslots:
            r.error("missing_reference", f"config {cid} fixes {f.exam_group_id} to unknown timeslot {f.timeslot_id}", "config", cid)
        if g is None or not config.hard_constraints.enforce_group_restrictions:
            continue
        if g.fixed_timeslot_id is not None and g.fixed_timeslot_id != f.timeslot_id:
            r.error("conflicting_assignment", f"config fixes {g.exam_group_id} to {f.timeslot_id} but the group is fixed to {g.fixed_timeslot_id}", "config", cid)
        elif not g.permits(f.timeslot_id):
            r.error("restriction_violation", f"config fixes {g.exam_group_id} to disallowed timeslot {f.timeslot_id}", "config", cid)

    if config.hard_constraints.enforce_group_restrictions:
        available = set(idx.timeslots) - excluded
        for g in idx.exam_groups.values():
            if g.fixed_timeslot_id in excluded:
                r.error("conflicting_assignment", f"{g.exam_group_id} is fixed to excluded timeslot {g.fixed_timeslot_id}", "config", cid)
            elif not any(g.permits(t) for t in available):
                r.error("no_available_timeslots", f"{g.exam_group_id} has no permitted timeslot left", "config", cid)

    if schedules is not None and config.baseline_schedule_id is not None:
        if config.baseline_schedule_id not in {s.schedule_id for s in schedules}:
            r.error("missing_reference", f"config {cid} references unknown baseline schedule {config.baseline_schedule_id}", "config", cid)
    return r
