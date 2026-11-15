"""Student-level views: listing, detail, and baseline-vs-optimized impact."""

from __future__ import annotations

from collections import Counter

from hexam.evaluation import EvaluationOutcome
from hexam.evaluation.rules import campus, campus_date
from hexam.schemas import (
    ExamSchedule,
    ImpactVerdict,
    StudentDetailView,
    StudentExamView,
    StudentImpactResponse,
    StudentPage,
    StudentRankItem,
    StudentScheduleImpact,
    StudentSectionView,
    StudentSummary,
)

from .dataset_service import DatasetHandle


class StudentNotFound(KeyError):
    pass


def _fmt_day(dt) -> str:
    return campus(dt).strftime("%a %b %d").replace(" 0", " ")


def _fmt_time(dt) -> str:
    return campus(dt).strftime("%I:%M %p").lstrip("0")


def summary(h: DatasetHandle, student_id: str) -> StudentSummary:
    st = h.index.students[student_id]
    return StudentSummary(
        student_id=st.student_id, major=st.major, year=st.year,
        course_ids=h.student_course_ids(student_id),
        exam_group_ids=list(h.index.student_exam_groups.get(student_id, ())),
    )


def search(h: DatasetHandle, *, q: str | None, major: str | None, year: str | None, course: str | None,
           offset: int, limit: int) -> StudentPage:
    ids = sorted(h.index.students)
    out = []
    qn = (q or "").strip().upper()
    cn = (course or "").strip().upper().replace(" ", "-")
    for sid in ids:
        st = h.index.students[sid]
        if qn and qn not in sid:
            continue
        if major and st.major != major.upper():
            continue
        if year and st.year.value != year:
            continue
        if cn and not any(c.startswith(cn) for c in h.student_course_ids(sid)):
            continue
        out.append(sid)
    page = out[offset: offset + limit]
    return StudentPage(items=[summary(h, s) for s in page], total=len(out), offset=offset, limit=limit)


def detail(h: DatasetHandle, student_id: str) -> StudentDetailView:
    idx = h.index
    if student_id not in idx.students:
        raise StudentNotFound(student_id)
    st = idx.students[student_id]
    sections = []
    for sid in st.section_ids:
        sec = idx.sections.get(sid)
        if sec is None:
            continue
        course = idx.courses.get(sec.course_id)
        meets = "; ".join(
            f"{''.join(d.value for d in m.days)} {m.start_time:%H:%M}-{m.end_time:%H:%M}" for m in sec.meeting_times
        ) or "TBA"
        sections.append(StudentSectionView(
            section_id=sid, course_id=sec.course_id, course_title=course.title if course else sec.course_id,
            section_code=sec.section_code, section_type=sec.section_type.value, meeting_summary=meets,
            exam_group_id=sec.exam_group_id,
        ))
    return StudentDetailView(
        student_id=st.student_id, major=st.major, year=st.year, sections=sections,
        exam_group_ids=list(idx.student_exam_groups.get(student_id, ())),
    )


def _own_courses_by_group(h: DatasetHandle, student_id: str) -> dict[str, list[str]]:
    """Exam group -> the student's own course(s) whose registered section sits that exam."""
    out: dict[str, set[str]] = {}
    for sid in h.index.students[student_id].section_ids:
        sec = h.index.sections.get(sid)
        if sec is not None and sec.exam_group_id is not None:
            out.setdefault(sec.exam_group_id, set()).add(sec.course_id)
    return {g: sorted(cs) for g, cs in out.items()}


def _label(h: DatasetHandle, g: str, student_id: str | None = None) -> str:
    cs = h.group_courses.get(g) or [g]
    if student_id:
        cs = _own_courses_by_group(h, student_id).get(g) or cs
    return "/".join(c.replace("-", " ") for c in cs[:3]) + (" …" if len(cs) > 3 else "")


def _schedule_impact(h: DatasetHandle, student_id: str, outcome: EvaluationOutcome, mapping: dict[str, str],
                     schedule_id: str) -> StudentScheduleImpact:
    idx = h.index
    base = h.baseline_mapping
    mine = _own_courses_by_group(h, student_id)
    exams = []
    for g in idx.student_exam_groups.get(student_id, ()):
        t = mapping.get(g)
        ts = idx.timeslots.get(t) if t else None
        grp = idx.exam_groups[g]
        exams.append(StudentExamView(
            # the student's own course(s) in this exam group, not every course sharing the slot
            exam_group_id=g, exam_group_name=grp.name,
            course_ids=mine.get(g) or h.group_courses.get(g, []),
            timeslot_id=t, start=ts.start if ts else None, end=ts.end if ts else None,
            fixed=grp.fixed_timeslot_id is not None, moved=t != base.get(g),
        ))
    exams.sort(key=lambda e: (e.start is None, e.start, e.exam_group_id))
    f = outcome.findings[student_id]
    d = outcome.students[student_id]
    slot_of = {e.exam_group_id: idx.timeslots[e.timeslot_id] for e in outcome.exams[student_id]}
    msgs = []
    for a, b in f.conflict_pairs:
        msgs.append(f"Overlapping exams: {_label(h, a, student_id)} and {_label(h, b, student_id)} ({_fmt_day(slot_of[a].start)})")
    for a, b in f.back_to_back_pairs:
        msgs.append(f"Back-to-back: {_label(h, a, student_id)} ends {_fmt_time(slot_of[a].end)}, "
                    f"{_label(h, b, student_id)} starts {_fmt_time(slot_of[b].start)} ({_fmt_day(slot_of[b].start)})")
    for a, b in f.late_early_pairs:
        msgs.append(f"Late→early: {_label(h, a, student_id)} at {_fmt_time(slot_of[a].start)} {_fmt_day(slot_of[a].start)}, "
                    f"then {_label(h, b, student_id)} at {_fmt_time(slot_of[b].start)} next morning")
    if d.three_in_24h:
        # 3 exams on one calendar day is the tightest form of 3-in-24 h; name the day(s).
        per_day = Counter(campus_date(e.start) for e in outcome.exams[student_id])
        heavy = [f"{n} on {day.strftime('%a %b %d').replace(' 0', ' ')}" for day, n in sorted(per_day.items()) if n >= 3]
        msgs.append("3 exams within 24 hours" + (f" ({', '.join(heavy)})" if heavy else ""))
    if d.four_in_48h:
        msgs.append("4 exams within 48 hours")
    return StudentScheduleImpact(schedule_id=schedule_id, exams=exams, detail=d, findings=msgs)


def impact(h: DatasetHandle, student_id: str, *, run_id: str | None, before: EvaluationOutcome,
           after: EvaluationOutcome | None, schedule: ExamSchedule | None) -> StudentImpactResponse:
    if student_id not in h.index.students:
        raise StudentNotFound(student_id)
    b = _schedule_impact(h, student_id, before, h.baseline_mapping, h.baseline.schedule_id)
    a = None
    change = None
    verdict = ImpactVerdict.NOT_AVAILABLE
    if after is not None and schedule is not None:
        a = _schedule_impact(h, student_id, after, schedule.as_mapping(), schedule.schedule_id)
        change = a.detail.weighted_penalty - b.detail.weighted_penalty
        verdict = ImpactVerdict.IMPROVED if change < 0 else ImpactVerdict.WORSENED if change > 0 else ImpactVerdict.UNCHANGED
    return StudentImpactResponse(
        student=detail(h, student_id), dataset_id=h.dataset_id, run_id=run_id,
        baseline=b, optimized=a, penalty_change=change, verdict=verdict,
    )


def ranking(h: DatasetHandle, before: EvaluationOutcome, after: EvaluationOutcome, order: str, limit: int) -> list[StudentRankItem]:
    items = []
    for sid, d in before.students.items():
        st = h.index.students[sid]
        pa = after.students[sid].weighted_penalty
        items.append(StudentRankItem(student_id=sid, major=st.major, year=st.year,
                                     penalty_before=d.weighted_penalty, penalty_after=pa, change=pa - d.weighted_penalty))
    key = {
        "improved": lambda i: (i.change, i.student_id),
        "worsened": lambda i: (-i.change, i.student_id),
        "remaining": lambda i: (-i.penalty_after, i.student_id),
        "baseline": lambda i: (-i.penalty_before, i.student_id),
    }[order]
    items.sort(key=key)
    if order == "improved":
        items = [i for i in items if i.change < 0]
    elif order == "worsened":
        items = [i for i in items if i.change > 0]
    return items[:limit]
