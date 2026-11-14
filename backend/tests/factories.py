"""Tiny hand-built datasets for evaluator/optimizer tests."""

from __future__ import annotations

from datetime import datetime

from hexam.schemas import (
    CAMPUS_TZ,
    Course,
    Dataset,
    ExamGroup,
    ExamTimeslot,
    Provenance,
    Section,
    Student,
)

ASSUMED = Provenance.assumed("test")


def dt(day: int, hh: int, mm: int = 0, month: int = 12) -> datetime:
    return datetime(2026, month, day, hh, mm, tzinfo=CAMPUS_TZ)


def slot(tid: str, start: datetime, end: datetime, **kw) -> ExamTimeslot:
    return ExamTimeslot(timeslot_id=tid, start=start, end=end, provenance=ASSUMED, **kw)


def standard_slots(days=(7, 8, 9)) -> list[ExamTimeslot]:
    """GT-style 2h50m periods: 08:00, 11:20, 14:40, 18:00 on each day."""
    out = []
    for d in days:
        for hh, mm in ((8, 0), (11, 20), (14, 40), (18, 0)):
            start = dt(d, hh, mm)
            end = start.replace(hour=hh + 2, minute=mm + 50) if mm + 50 < 60 else start.replace(hour=hh + 3, minute=mm - 10)
            out.append(slot(f"T{d:02d}{hh:02d}{mm:02d}", start, end))
    return out


def make_dataset(
    groups: list[str],
    students: dict[str, list[str]],
    slots: list[ExamTimeslot],
    *,
    fixed: dict[str, str] | None = None,
    allowed: dict[str, list[str]] | None = None,
    forbidden: dict[str, list[str]] | None = None,
    extra_sections: dict[str, int] | None = None,
) -> Dataset:
    """One course + section per exam group (``extra_sections[g]`` adds more
    courses into group g, sections ``<g>-X1`` ...). Students list *section* ids
    or group ids (a group id means its first section)."""
    fixed, allowed, forbidden, extra_sections = fixed or {}, allowed or {}, forbidden or {}, extra_sections or {}
    courses, sections, egs = [], [], []
    n = 1000
    sec_of_group: dict[str, str] = {}
    for g in groups:
        for k in range(1 + extra_sections.get(g, 0)):
            n += 1
            cid = f"TST-{n}"
            courses.append(Course(course_id=cid, subject="TST", number=str(n), title=f"Course {n}", provenance=ASSUMED))
            sid = g if k == 0 else f"{g}-X{k}"
            sections.append(
                Section(section_id=sid, course_id=cid, section_code="A", enrollment=0, exam_group_id=g, provenance=ASSUMED)
            )
            sec_of_group.setdefault(g, sid)
        egs.append(
            ExamGroup(
                exam_group_id=g, name=g, kind="special",
                fixed_timeslot_id=fixed.get(g), allowed_timeslot_ids=allowed.get(g),
                forbidden_timeslot_ids=forbidden.get(g, []), provenance=ASSUMED,
            )
        )
    studs = [
        Student(student_id=s, major="CS", year="first_year", section_ids=secs, provenance=Provenance.synthetic("test"))
        for s, secs in students.items()
    ]
    return Dataset(courses=courses, sections=sections, exam_groups=egs, timeslots=slots, students=studs)
