"""Normalize cached public data into Hexam schema records for the course subset.

    python -m hexam.data_generation.normalize [--config PATH] [--granularity meeting_time|meeting_pattern|course]

Reads only ``data/raw`` (via ``data/provenance/sources.json``) and writes the
real-data part of ``data/<dataset>/``:

* ``courses.json``, ``sections.json``           — GT Scheduler / OSCAR snapshot (``gt_public``)
* ``exam_groups.json``, ``timeslots.json``      — official final exam matrix (``gt_public``)
* ``schedules.json``                             — the official-matrix baseline schedule
* ``exam_matrix.json``                           — full parsed matrix incl. reading/conflict periods
* ``course_metadata.json``, ``section_metadata.json`` — prerequisites, restrictions, component links
* ``normalization_report.json``                  — every inclusion/exclusion decision
* ``students.json``                              — reset to ``[]`` (run ``generate`` next)

Section rules (registrar exam guidelines + data constraints):

* Atlanta campus, full-term, with at least one scheduled meeting; not graduate-only;
  not reserved for a cohort/program/permit population (``reserved_restriction_categories``).
* A *primary* section carries credit; 0-credit sections of the same course are
  *components* (studio/lab/recitation). ``A01`` links to primary ``A``; components
  that match no primary code (e.g. PHYS ``L01``) can pair with any primary.
* A primary is *exam-bearing* if its course has an official common exam, or it is a
  ``Lecture`` of >= ``min_exam_credit_hours`` credits (registrar: 2+ credit lectures
  get a matrix final). Its exam period comes from the matrix row matching the class
  meeting (exact days/start/end, else days/start when unambiguous). Exam-bearing
  sections with non-standard meeting times are excluded — the registrar assigns
  those individually, and we do not guess.
* Real capacity and enrollment are not public in these sources: ``capacity`` is left
  ``null`` and ``enrollment`` is a placeholder 0 until synthetic generation.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..schemas import (
    Course,
    DataSource,
    ExamAssignment,
    ExamGroup,
    ExamGroupKind,
    ExamSchedule,
    ExamTimeslot,
    MeetingTime,
    Provenance,
    ScheduleKind,
    Section,
    SectionType,
    Weekday,
)
from ..schemas.base import CAMPUS_TZ
from ..storage import write_models
from .adapters.exam_matrix import CommonExam, ExamMatrix, ExamPeriod, parse_matrix
from .adapters.gt_scheduler import RawCourse, RawMeeting, RawSection, TermData, load_term, prerequisite_course_ids
from .config import ExamGroupGranularity, GenerationConfig, load_config
from .paths import DataPaths
from .sources import SourceManifest, load_manifest

SECTION_TYPES = {
    "Lecture": SectionType.LECTURE,
    "Studio": SectionType.STUDIO,
    "Recitation": SectionType.RECITATION,
    "Breakout": SectionType.RECITATION,
    "Supervised Laboratory": SectionType.LAB,
    "Unsupervised Laboratory": SectionType.LAB,
    "Mixed Laboratory": SectionType.LAB,
}
ATLANTA = "Georgia Tech-Atlanta"


def timeslot_id(p: ExamPeriod) -> str:
    return f"TS-{p.date:%Y%m%d}-{p.start:%H%M}"


def _fmt12(t) -> str:
    return datetime.combine(datetime(2000, 1, 1).date(), t).strftime("%I:%M %p").lstrip("0")


def period_label(p: ExamPeriod) -> str:
    return f"{p.date:%a %b} {p.date.day}, {_fmt12(p.start)}–{_fmt12(p.end)}"


def section_id(course_id: str, code: str) -> str:
    return f"{course_id}-{code}"


@dataclass
class ExamMapping:
    method: str  # "common" | "matrix_exact" | "matrix_days_start"
    period: ExamPeriod
    group_key: str  # e.g. "MWF-0930" or "COMMON-MATH-1553-1554"
    pattern: str | None  # e.g. "MWF 09:30" (None for common)
    meeting: str | None  # the class meeting used, e.g. "MWF 09:30-10:20"
    common_label: str | None = None
    # other meetings of the section that map to a *different* matrix period (paper: flag as ambiguous)
    ambiguous_alternatives: list[str] = field(default_factory=list)


@dataclass
class Normalized:
    courses: list[Course]
    sections: list[Section]
    exam_groups: list[ExamGroup]
    timeslots: list[ExamTimeslot]
    baseline: ExamSchedule
    course_meta: dict[str, Any]
    section_meta: dict[str, Any]
    report: dict[str, Any]
    matrix_json: dict[str, Any]
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------- filters


def _section_filter(s: RawSection, full_term: str, cfg: GenerationConfig) -> str | None:
    """Return an exclusion reason, or None if the section is usable."""
    if not s.campus.startswith(ATLANTA):
        return "not_atlanta_campus"
    levels = s.allowed.get("Levels", ())
    if levels and all("Graduate" in v for v in levels):
        return "graduate_only"
    reserved = sorted(set(s.allowed) & set(cfg.reserved_restriction_categories))
    if reserved:
        return f"reserved_{reserved[0].lower().replace(' ', '_')}"
    scheduled = [m for m in s.meetings if m.is_scheduled]
    if not scheduled:
        return "no_scheduled_meeting"
    if any(m.date_range != full_term for m in scheduled):
        return "part_of_term"
    return None


def _meetings(s: RawSection) -> list[RawMeeting]:
    """Scheduled meetings with exact duplicates (same days/time) removed, in source order."""
    seen: set[tuple] = set()
    out = []
    for m in s.meetings:
        if not m.is_scheduled:
            continue
        k = (m.days, m.start, m.end)
        if k not in seen:
            seen.add(k)
            out.append(m)
    return out


def _group_key(days: str, start, end, include_end: bool) -> tuple[str, str]:
    key = f"{days}-{start:%H%M}" + (f"-{end:%H%M}" if include_end else "")
    pattern = f"{days} {start:%H:%M}" + (f"-{end:%H:%M}" if include_end else "")
    return key, pattern


def _exam_mapping(
    course: RawCourse, s: RawSection, matrix: ExamMatrix, common: CommonExam | None, *, full_time_key: bool
) -> ExamMapping | None:
    """Exam period + group key for an exam-bearing section.

    The first meeting that maps to the matrix decides (crawler + registrar convention).
    ``full_time_key`` keys the group on days+start+end (paper's "regular meeting time");
    otherwise on days+start, adding the end only where the matrix depends on it.
    """
    if common is not None:
        label = common.label.replace(" ", "-").replace("/", "-")
        return ExamMapping("common", common.exam, f"COMMON-{label}", None, None, common.label)
    sensitive = matrix.end_sensitive_patterns
    mapped = []
    for m in _meetings(s):
        period, how = matrix.lookup(m.days, m.start, m.end)  # type: ignore[arg-type]
        if period is not None:
            mapped.append((m, period, how))
    if not mapped:
        return None
    m, period, how = mapped[0]
    include_end = full_time_key or f"{m.days} {m.start:%H:%M}" in sensitive
    key, pattern = _group_key(m.days, m.start, m.end, include_end)
    alternatives = [
        f"{o.days} {o.start:%H:%M}-{o.end:%H:%M} -> {p.key}" for o, p, _ in mapped[1:] if p != period
    ]
    return ExamMapping(how, period, key, pattern, f"{m.days} {m.start:%H:%M}-{m.end:%H:%M}", ambiguous_alternatives=alternatives)


# ----------------------------------------------------------------- main build


def build(term: TermData, matrix: ExamMatrix, cfg: GenerationConfig, manifest: SourceManifest) -> Normalized:
    crawler = manifest.require("gt_scheduler_term")
    matrix_src = manifest.require("final_exam_matrix")
    crawler_note = (
        f"GT Scheduler crawler-v2 snapshot of OSCAR (crawler updatedAt {term.updated_at:%Y-%m-%dT%H:%MZ}, "
        f"format v{term.version}); section capacity/enrollment are not published in this source."
    )

    def course_prov() -> Provenance:
        return Provenance(source=DataSource.GT_PUBLIC, source_ref=crawler.url, retrieved_at=crawler.retrieved_at, notes=crawler_note)

    def section_prov() -> Provenance:
        return Provenance(
            source=DataSource.GT_PUBLIC,
            source_ref=crawler.url,
            retrieved_at=crawler.retrieved_at,
            notes=crawler_note + " exam_group_id derived from the official final exam matrix.",
            field_sources={"enrollment": DataSource.ASSUMED},
        )

    def matrix_prov(notes: str | None = None) -> Provenance:
        return Provenance(source=DataSource.GT_PUBLIC, source_ref=matrix_src.url, retrieved_at=matrix_src.retrieved_at, notes=notes)

    full_term = Counter(m.date_range for c in term.courses.values() for s in c.sections.values() for m in s.meetings).most_common(1)[0][0]

    courses: list[Course] = []
    sections: list[Section] = []
    course_meta: dict[str, Any] = {}
    section_meta: dict[str, Any] = {}
    mappings: dict[str, ExamMapping] = {}  # section_id -> mapping (exam-bearing primaries)
    excluded: list[dict[str, str]] = []
    warnings: list[str] = []
    subset_keys = {c.key for c in cfg.courses}

    for sc in cfg.courses:
        raw = term.courses.get(sc.key)
        if raw is None:
            warnings.append(f"{sc.key}: not offered in term {term.term}; skipped")
            continue
        common = matrix.common_exam_for(sc.key)
        if common is not None and common.qualifier:
            warnings.append(f"{sc.key}: matrix common exam applies only to '{common.qualifier}' sections, which are not in the Atlanta subset; using the meeting-pattern matrix instead")
            common = None

        primaries = {code: s for code, s in raw.sections.items() if s.credit_hours > 0}
        included_primary: dict[str, RawSection] = {}
        reasons: Counter[str] = Counter()
        course_sections: list[Section] = []

        for code in sorted(primaries):
            s = primaries[code]
            reason = _section_filter(s, full_term, cfg)
            mapping = None
            exam_bearing = common is not None or (s.schedule_type == "Lecture" and s.credit_hours >= cfg.min_exam_credit_hours)
            if reason is None and exam_bearing:
                mapping = _exam_mapping(
                    raw, s, matrix, common, full_time_key=cfg.exam_group_granularity is not ExamGroupGranularity.MEETING_PATTERN
                )
                if mapping is None:
                    reason = "non_standard_meeting_pattern"
            if reason is None:
                included_primary[code] = s
            else:
                reasons[reason] += 1
                excluded.append({"section": f"{sc.key} {code}", "crn": s.crn, "reason": reason})
                continue
            sid = section_id(raw.course_id, code)
            if mapping is not None:
                mappings[sid] = mapping
            section_meta[sid] = _meta(s, primary=True, linked=None, mapping=mapping, exam_bearing=exam_bearing)

        # Components: 0-credit sections linked by code prefix to a primary, or free-floating.
        components: dict[str, tuple[RawSection, str | None]] = {}
        for code in sorted(c for c in raw.sections if c not in primaries):
            s = raw.sections[code]
            linked = max((p for p in primaries if code.startswith(p) and len(p) < len(code)), key=len, default=None)
            reason = _section_filter(s, full_term, cfg)
            if reason is None and linked is not None and linked not in included_primary:
                reason = "linked_primary_excluded"
            if reason is None and not included_primary:
                reason = "course_has_no_primary"
            if reason:
                reasons[reason] += 1
                excluded.append({"section": f"{sc.key} {code}", "crn": s.crn, "reason": reason})
                continue
            components[code] = (s, linked)
            section_meta[section_id(raw.course_id, code)] = _meta(
                s, primary=False, linked=section_id(raw.course_id, linked) if linked else None, mapping=None, exam_bearing=False
            )

        for code, s in [*((c, included_primary[c]) for c in sorted(included_primary)), *((c, components[c][0]) for c in sorted(components))]:
            sid = section_id(raw.course_id, code)
            try:
                course_sections.append(_section_record(raw, s, sid, mappings.get(sid), cfg, section_prov()))
            except ValueError as e:
                reasons["invalid_record"] += 1
                excluded.append({"section": f"{sc.key} {code}", "crn": s.crn, "reason": f"invalid_record: {e}"})
                section_meta.pop(sid, None)
                mappings.pop(sid, None)

        if not any(sec.section_id in mappings or section_meta[sec.section_id]["primary"] for sec in course_sections):
            warnings.append(f"{sc.key}: no usable primary section; course dropped from subset")
            for sec in course_sections:
                section_meta.pop(sec.section_id, None)
                mappings.pop(sec.section_id, None)
            continue

        credit = max(s.credit_hours for s in included_primary.values())
        courses.append(Course(course_id=raw.course_id, subject=raw.subject, number=raw.number, title=raw.title, credit_hours=credit, provenance=course_prov()))
        sections.extend(course_sections)
        prereq_keys = sorted(prerequisite_course_ids(raw.prerequisites))
        course_meta[raw.course_id] = {
            "key": raw.key,
            "title": raw.title,
            "role": sc.role,
            "credit_hours": credit,
            "description": raw.description,
            "prerequisites": raw.prerequisites,
            "prerequisite_keys": prereq_keys,
            "prerequisite_keys_in_subset": [k for k in prereq_keys if k in subset_keys],
            "corequisites": list(raw.corequisites),
            "exam": "common" if common else ("matrix" if any(sid in mappings for sid in (s.section_id for s in course_sections)) else "none"),
            "common_exam_label": common.label if common else None,
            "primary_sections": sorted(s.section_id for s in course_sections if section_meta[s.section_id]["primary"]),
            "component_sections": sorted(s.section_id for s in course_sections if not section_meta[s.section_id]["primary"]),
            "excluded_sections": dict(sorted(reasons.items())),
        }

    # ------------------------------------------------------------- exam groups
    slots = {timeslot_id(p): p for p in matrix.exam_periods}
    timeslots = [
        ExamTimeslot(
            timeslot_id=tid,
            start=datetime.combine(p.date, p.start, CAMPUS_TZ),
            end=datetime.combine(p.date, p.end, CAMPUS_TZ),
            label=period_label(p),
            provenance=matrix_prov(f"{matrix.term_label} final exam period (official matrix)."),
        )
        for tid, p in sorted(slots.items())
    ]

    granular = cfg.exam_group_granularity is ExamGroupGranularity.COURSE
    sec_by_id = {s.section_id: s for s in sections}
    groups: dict[str, ExamGroup] = {}
    group_slot: dict[str, str] = {}
    for sid in sorted(mappings):
        mp = mappings[sid]
        sec = sec_by_id[sid]
        if mp.method == "common":
            gid = f"EG-{mp.group_key}"
        else:
            gid = f"EG-{sec.course_id}-{mp.group_key}" if granular else f"EG-{mp.group_key}"
        sec.exam_group_id = gid
        tid = timeslot_id(mp.period)
        if gid in groups:
            if group_slot[gid] != tid:
                raise ValueError(f"exam group {gid} maps to two periods ({group_slot[gid]}, {tid})")
            continue
        group_slot[gid] = tid
        when = period_label(mp.period)
        if mp.method == "common":
            groups[gid] = ExamGroup(
                exam_group_id=gid,
                name=f"{mp.common_label} common exam",
                kind=ExamGroupKind.COMMON,
                description=f"Official common final exam: {when}. Fixed by the registrar.",
                fixed_timeslot_id=tid,
                provenance=matrix_prov("Common exam row of the official matrix; preserved as a fixed assignment."),
            )
        else:
            name = f"{mp.pattern} classes" if not granular else f"{sec.course_id} ({mp.pattern})"
            groups[gid] = ExamGroup(
                exam_group_id=gid,
                name=name,
                kind=ExamGroupKind.MEETING_PATTERN,
                meeting_pattern=mp.pattern,
                description=f"Official matrix period: {when}.",
                provenance=matrix_prov(
                    "Meeting-pattern row of the official matrix."
                    + (" Class end time not listed in the matrix; matched on days + start time." if mp.method == "matrix_days_start" else "")
                ),
            )
    exam_groups = [groups[g] for g in sorted(groups)]

    baseline = ExamSchedule(
        schedule_id=f"baseline-gt-matrix-{term.term}",
        name=f"GT official {matrix.term_label.title()} final exam matrix (baseline)",
        kind=ScheduleKind.BASELINE,
        assignments=[ExamAssignment(exam_group_id=g.exam_group_id, timeslot_id=group_slot[g.exam_group_id], locked=g.fixed_timeslot_id is not None) for g in exam_groups],
        created_at=matrix_src.retrieved_at,
        provenance=matrix_prov(
            "Each exam group placed in the period the official matrix assigns to its meeting pattern or common exam. "
            "This is the registrar's published rule applied to the subset, not an optimized or approved Hexam schedule."
        ),
    )

    for sid, meta in section_meta.items():
        if sid in sec_by_id:
            meta["exam_group_id"] = sec_by_id[sid].exam_group_id

    report = {
        "term": term.term,
        "crawler_updated_at": term.updated_at.isoformat(),
        "full_term_date_range": full_term,
        "exam_group_granularity": cfg.exam_group_granularity.value,
        "counts": {
            "courses_requested": len(cfg.courses),
            "courses": len(courses),
            "sections": len(sections),
            "primary_sections": sum(1 for s in sections if section_meta[s.section_id]["primary"]),
            "component_sections": sum(1 for s in sections if not section_meta[s.section_id]["primary"]),
            "exam_bearing_sections": len(mappings),
            "exam_groups": len(exam_groups),
            "common_exam_groups": sum(g.kind is ExamGroupKind.COMMON for g in exam_groups),
            "timeslots": len(timeslots),
            "excluded_sections": len(excluded),
        },
        "exam_mapping_methods": dict(Counter(m.method for m in mappings.values())),
        "ambiguous_meeting_times": {
            sid: {"used": m.meeting, "other_meetings": m.ambiguous_alternatives}
            for sid, m in sorted(mappings.items())
            if m.ambiguous_alternatives
        },
        "exclusion_reasons": dict(Counter(e["reason"].split(":")[0] for e in excluded).most_common()),
        "excluded_sections": excluded,
        "matrix_issues": matrix.issues,
        "warnings": warnings,
    }
    return Normalized(courses, sections, exam_groups, timeslots, baseline, course_meta, section_meta, report, matrix.to_json(), warnings)


def _meta(s: RawSection, *, primary: bool, linked: str | None, mapping: ExamMapping | None, exam_bearing: bool) -> dict[str, Any]:
    return {
        "crn": s.crn,
        "course_key": s.course_key,
        "code": s.code,
        "schedule_type": s.schedule_type,
        "credit_hours": s.credit_hours,
        "campus": s.campus,
        "primary": primary,
        "component_of": linked,
        "component_kind": None if primary else ("linked" if linked else "free"),
        "exam_bearing": exam_bearing,
        "exam_mapping": None if mapping is None else {
            "method": mapping.method,
            "class_meeting": mapping.meeting,
            "matrix_pattern": mapping.pattern,
            "common_exam": mapping.common_label,
            "exam_date": mapping.period.date.isoformat(),
            "exam_start": mapping.period.start.strftime("%H:%M"),
            "ambiguous_alternatives": mapping.ambiguous_alternatives,
        },
        "restrictions_allowed": {k: list(v) for k, v in sorted(s.allowed.items())},
        "restrictions_disallowed": {k: list(v) for k, v in sorted(s.disallowed.items())},
    }


def _section_record(raw: RawCourse, s: RawSection, sid: str, mapping: ExamMapping | None, cfg: GenerationConfig, prov: Provenance) -> Section:
    meetings = [
        MeetingTime(
            days=[Weekday(d) for d in m.days],
            start_time=m.start,
            end_time=m.end,
            location=None if m.room == "TBA" else m.room,
        )
        for m in _meetings(s)
    ]
    return Section(
        section_id=sid,
        course_id=raw.course_id,
        section_code=s.code,
        crn=s.crn,
        section_type=SECTION_TYPES.get(s.schedule_type, SectionType.OTHER),
        instructors=s.instructors,
        enrollment=0,
        capacity=None,
        meeting_times=meetings,
        exam_group_id=None,  # set from the matrix mapping after grouping
        provenance=prov,
    )


# ---------------------------------------------------------------------- I/O


def write(n: Normalized, paths: DataPaths) -> None:
    d = paths.dataset
    d.mkdir(parents=True, exist_ok=True)
    write_models(d / "courses.json", n.courses)
    write_models(d / "sections.json", n.sections)
    write_models(d / "exam_groups.json", n.exam_groups)
    write_models(d / "timeslots.json", n.timeslots)
    write_models(d / "schedules.json", [n.baseline])
    (d / "students.json").write_text("[]\n", encoding="utf-8")
    for path, obj in [
        (paths.exam_matrix, n.matrix_json),
        (paths.course_metadata, n.course_meta),
        (paths.section_metadata, n.section_meta),
        (paths.normalization_report, n.report),
    ]:
        path.write_text(json.dumps(obj, indent=2, default=str) + "\n", encoding="utf-8")
    for stale in (paths.generation_metadata,):
        stale.unlink(missing_ok=True)


def run(paths: DataPaths, cfg: GenerationConfig, log=print) -> Normalized:
    manifest = load_manifest(paths.sources_manifest)
    if manifest.term != cfg.term:
        log(f"NOTE: manifest term {manifest.term} differs from config term {cfg.term} ({manifest.term_substitution or 'no substitution note'})")
    term = load_term(paths.raw / manifest.require("gt_scheduler_term").local_path, manifest.term)  # type: ignore[operator]
    matrix = parse_matrix(paths.raw / manifest.require("final_exam_matrix").local_path)  # type: ignore[operator]
    if matrix.issues:
        raise ValueError("final exam matrix failed validation:\n  " + "\n  ".join(matrix.issues))
    n = build(term, matrix, cfg, manifest)
    write(n, paths)
    c = n.report["counts"]
    log(
        f"normalized {c['courses']} courses, {c['sections']} sections ({c['primary_sections']} primary, "
        f"{c['component_sections']} components), {c['exam_groups']} exam groups ({c['common_exam_groups']} common), "
        f"{c['timeslots']} exam periods; excluded {c['excluded_sections']} sections"
    )
    log(f"exclusion reasons: {n.report['exclusion_reasons']}")
    if amb := n.report["ambiguous_meeting_times"]:
        log(f"NOTE: {len(amb)} sections have meetings mapping to different exam periods (first meeting used): {sorted(amb)}")
    for w in n.warnings:
        log(f"WARNING: {w}")
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=None)
    ap.add_argument("--granularity", choices=[g.value for g in ExamGroupGranularity], default=None)
    ap.add_argument("--data-dir", type=Path, default=None)
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    if args.granularity:
        cfg = cfg.model_copy(update={"exam_group_granularity": ExamGroupGranularity(args.granularity)})
    paths = DataPaths(**({"root": args.data_dir} if args.data_dir else {}), dataset_name=cfg.dataset_name)
    run(paths, cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
