"""Validate the generated dataset end to end.

    python -m hexam.data_generation.validate [--skip-reproducibility]

Runs ``hexam.schemas.validate_dataset`` / ``validate_schedule`` plus pipeline
checks that the schema layer cannot know about:

* source cache matches the manifest hashes (fixed snapshot);
* provenance separation: catalog/exam records are ``gt_public``; every student is
  ``synthetic``; section enrollment is ``synthetic``; capacity stays unknown (null);
* every exam-bearing section maps to an exam group whose baseline slot equals the
  official matrix period; common exams are fixed to their official period;
* students: no duplicate course, no meeting overlaps, exactly one component when
  the course has components (and it belongs to the chosen primary), OSCAR
  restrictions respected, synthetic section capacities respected, 3–6 courses;
* reproducibility: regenerating with the same snapshot + seed is byte-identical.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from ..schemas import DataSource, ExamGroupKind, ValidationReport, validate_dataset, validate_schedule
from ..storage import load_dataset, load_schedules
from .adapters.exam_matrix import parse_matrix
from .config import GenerationConfig, load_config
from .generate import CLASS_NAMES, canonical_sha, catalog_fingerprint, generate, section_eligible, SectionInfo
from .normalize import timeslot_id
from .paths import DataPaths
from .sources import load_manifest, verify_cache


def run_checks(paths: DataPaths, cfg: GenerationConfig, *, reproducibility: bool = True) -> ValidationReport:
    r = ValidationReport()
    manifest = load_manifest(paths.sources_manifest)
    for problem in verify_cache(manifest, paths.raw):
        r.error("source_cache_mismatch", problem, "source")

    ds = load_dataset(paths.dataset, strict=False)
    r.extend(validate_dataset(ds))
    idx = ds.build_index()
    schedules = load_schedules(paths.dataset)
    baseline = next((s for s in schedules if s.kind.value == "baseline"), None)
    if baseline is None:
        r.error("missing_baseline", "no baseline schedule in schedules.json", "schedule")
    else:
        r.extend(validate_schedule(ds, baseline, require_complete=True, index=idx))

    smeta = json.loads(paths.section_metadata.read_text(encoding="utf-8"))
    cmeta = json.loads(paths.course_metadata.read_text(encoding="utf-8"))
    gen_meta = json.loads(paths.generation_metadata.read_text(encoding="utf-8")) if paths.generation_metadata.exists() else None
    if gen_meta is None:
        r.error("missing_generation_metadata", "generation_metadata.json not found; run generate", "dataset")

    # ---- provenance separation
    for kind, items in (("course", ds.courses), ("section", ds.sections), ("exam_group", ds.exam_groups), ("timeslot", ds.timeslots)):
        for it in items:
            if it.provenance.source is not DataSource.GT_PUBLIC:
                r.error("provenance", f"{kind} record is {it.provenance.source.value}, expected gt_public", kind, getattr(it, f"{kind}_id"))
    for s in ds.sections:
        if ds.students and s.provenance.source_for("enrollment") is not DataSource.SYNTHETIC:
            r.error("provenance", "section enrollment must be labelled synthetic", "section", s.section_id)
        if s.capacity is not None:
            r.error("provenance", "section capacity is not public in our sources and must stay null", "section", s.section_id)
    for st in ds.students:
        if st.provenance.source is not DataSource.SYNTHETIC:
            r.error("provenance", "student must be synthetic", "student", st.student_id)
    if not ds.students:
        r.error("no_students", "students.json is empty; run generate", "dataset")

    # ---- matrix fidelity
    mrec = manifest.require("final_exam_matrix")
    matrix = parse_matrix(paths.raw / mrec.local_path)  # type: ignore[operator]
    for issue in matrix.issues:
        r.error("matrix_parse", issue, "matrix")
    official = {timeslot_id(p) for p in matrix.exam_periods}
    if official != set(idx.timeslots):
        r.error("timeslots", f"timeslots differ from official matrix periods: {sorted(official ^ set(idx.timeslots))}", "timeslot")
    slot_of = baseline.as_mapping() if baseline else {}
    for sid, m in smeta.items():
        sec = idx.sections.get(sid)
        if sec is None:
            r.error("metadata", "section_metadata lists a section missing from sections.json", "section", sid)
            continue
        em = m["exam_mapping"]
        if (em is None) != (sec.exam_group_id is None):
            r.error("exam_mapping", "exam_group_id disagrees with exam mapping metadata", "section", sid)
        if em and sec.exam_group_id in slot_of:
            expected = f"TS-{em['exam_date'].replace('-', '')}-{em['exam_start'].replace(':', '')}"
            if slot_of[sec.exam_group_id] != expected:
                r.error("baseline_mismatch", f"baseline puts {sec.exam_group_id} in {slot_of[sec.exam_group_id]}, matrix says {expected}", "section", sid)
    for g in ds.exam_groups:
        if g.kind is ExamGroupKind.COMMON and (g.fixed_timeslot_id is None or slot_of.get(g.exam_group_id) != g.fixed_timeslot_id):
            r.error("common_exam", "common exam must be fixed to its official period", "exam_group", g.exam_group_id)

    # ---- students
    caps = (gen_meta or {}).get("synthetic_section_capacities", {}).get("capacities", {})
    used = Counter(s for st in ds.students for s in st.section_ids)
    for sid, n in used.items():
        if sid in caps and n > caps[sid]:
            r.error("capacity_exceeded", f"{n} synthetic registrations > synthetic capacity {caps[sid]}", "section", sid)
        if idx.sections[sid].enrollment != n:
            r.error("enrollment_mismatch", f"enrollment {idx.sections[sid].enrollment} != registrations {n}", "section", sid)
    comp_courses = {m["key"] for m in cmeta.values() if m["component_sections"]}
    infos = {
        sid: SectionInfo(sid, m["course_key"], m["primary"], m["component_of"], list(idx.sections[sid].meeting_times), m["restrictions_allowed"], m["restrictions_disallowed"])
        for sid, m in smeta.items()
        if sid in idx.sections
    }
    lo, hi = min(cfg.course_load_distribution), max(cfg.course_load_distribution)
    for st in ds.students:
        by_course: dict[str, list[str]] = defaultdict(list)
        for sid in st.section_ids:
            by_course[smeta[sid]["course_key"]].append(sid)
            if not section_eligible(infos[sid], st.major, cfg.majors[st.major].college, st.year):
                r.error("restriction_violation", f"{sid} restrictions do not admit {st.major} {CLASS_NAMES[st.year]}", "student", st.student_id)
        if not lo <= len(by_course) <= hi:
            r.error("course_load", f"{len(by_course)} courses outside [{lo}, {hi}]", "student", st.student_id)
        for course, sids in by_course.items():
            prim = [s for s in sids if smeta[s]["primary"]]
            comps = [s for s in sids if not smeta[s]["primary"]]
            if len(prim) != 1:
                r.error("duplicate_course_registration", f"{course}: {len(prim)} primary sections", "student", st.student_id)
            if course in comp_courses and len(comps) != 1:
                r.error("component", f"{course}: expected exactly one component, got {len(comps)}", "student", st.student_id)
            for c in comps:
                link = smeta[c]["component_of"]
                if link is not None and prim and link != prim[0]:
                    r.error("component", f"{c} belongs to {link}, not {prim[0]}", "student", st.student_id)
        meets = [(sid, mt) for sid in st.section_ids for mt in idx.sections[sid].meeting_times]
        for i, (sa, a) in enumerate(meets):
            for sb, b in meets[i + 1 :]:
                if sa != sb and a.overlaps(b):
                    r.error("meeting_overlap", f"{sa} and {sb} overlap", "student", st.student_id)

    # registration_time_conflict is only a warning in the schema layer; it is an error for generated data
    for issue in r.issues:
        if issue.code == "registration_time_conflict":
            issue.severity = issue.severity.ERROR

    # ---- reproducibility
    if reproducibility and gen_meta:
        regen_cfg = GenerationConfig.model_validate(gen_meta["config"])
        sections_now = [s.model_copy() for s in ds.sections]
        if catalog_fingerprint(sections_now, paths.dataset) != gen_meta["input_fingerprint"]:
            r.error("reproducibility", "catalog changed since generation (input fingerprint mismatch)", "dataset")
        students, _, _ = generate(regen_cfg, paths)
        if canonical_sha([s.model_dump(mode="json") for s in students]) != gen_meta["output_sha256"]["students"]:
            r.error("reproducibility", "regenerating with the recorded config/seed does not reproduce students.json", "dataset")
        if canonical_sha([s.model_dump(mode="json") for s in ds.students]) != gen_meta["output_sha256"]["students"]:
            r.error("reproducibility", "students.json differs from the hash recorded at generation", "dataset")
    return r


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-reproducibility", action="store_true")
    ap.add_argument("--data-dir", type=Path, default=None)
    args = ap.parse_args(argv)
    cfg = load_config()
    paths = DataPaths(**({"root": args.data_dir} if args.data_dir else {}), dataset_name=cfg.dataset_name)
    report = run_checks(paths, cfg, reproducibility=not args.skip_reproducibility)
    by_code = Counter((i.severity.value, i.code) for i in report.issues)
    for (sev, code), n in sorted(by_code.items()):
        print(f"  {sev:7} {code:32} x{n}")
    for i in [i for i in report.issues if i.severity.value == "error"][:20]:
        print(f"  ERROR [{i.code}] {i.entity_type or ''} {i.entity_id or ''}: {i.message}")
    print(f"validation: {'PASSED' if report.valid else 'FAILED'} ({report.error_count} errors, {report.warning_count} warnings)")
    return 0 if report.valid else 1


if __name__ == "__main__":
    sys.exit(main())
