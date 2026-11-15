"""Full pipeline integration: normalize + generate 1,000 students from the cached
snapshot in a temporary data directory, then validate, mutate, and re-validate."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from hexam.data_generation import generate, normalize, stats
from hexam.data_generation.config import ExamGroupGranularity, load_config
from hexam.data_generation.paths import DATA_DIR, DataPaths
from hexam.data_generation.validate import run_checks
from hexam.schemas import DataSource, ExamGroupKind, Student
from hexam.storage import load_dataset, load_schedules, read_models, write_models

pytest.importorskip("pypdf")
if not (DATA_DIR / "provenance" / "sources.json").exists():
    pytest.skip("cached source snapshot not present", allow_module_level=True)


def _copy_snapshot(dst: Path) -> DataPaths:
    shutil.copytree(DATA_DIR / "raw", dst / "raw")
    shutil.copytree(DATA_DIR / "provenance", dst / "provenance")
    return DataPaths(root=dst, dataset_name="fall2026")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    cfg = load_config()
    paths = _copy_snapshot(tmp_path_factory.mktemp("data"))
    normalize.run(paths, cfg, log=lambda *_: None)
    generate.run(cfg, paths, log=lambda *_: None)
    return cfg, paths


def test_full_dataset_validates(built):
    cfg, paths = built
    report = run_checks(paths, cfg, reproducibility=True)
    assert report.valid, [i.message for i in report.issues if i.severity.value == "error"][:10]
    assert report.warning_count == 0


def test_dataset_shape(built):
    cfg, paths = built
    ds = load_dataset(paths.dataset)  # strict: raises on cross-record errors
    assert len(ds.students) == 1000
    assert len({s.student_id for s in ds.students}) == 1000
    assert len(ds.timeslots) == 21
    # Course groups: common-exam courses + one group per regular meeting time
    assert cfg.exam_group_granularity is ExamGroupGranularity.MEETING_TIME
    assert len(ds.exam_groups) == 52
    pattern = {g.exam_group_id: g for g in ds.exam_groups if g.kind is ExamGroupKind.MEETING_PATTERN}
    assert pattern["EG-MWF-0930-1020"].meeting_pattern == "MWF 09:30-10:20"
    assert "EG-MW-1100-1150" in pattern and "EG-MW-1100-1215" in pattern  # same block, separate groups
    report = json.loads(paths.normalization_report.read_text())
    assert report["ambiguous_meeting_times"] == {}
    common = {g.exam_group_id: g for g in ds.exam_groups if g.kind is ExamGroupKind.COMMON}
    assert common["EG-COMMON-MATH-1553-1554"].fixed_timeslot_id == "TS-20261215-1800"
    assert common["EG-COMMON-PHYS-2211"].fixed_timeslot_id == "TS-20261211-1800"
    assert {s.provenance.source for s in ds.students} == {DataSource.SYNTHETIC}
    assert {c.provenance.source for c in ds.courses} == {DataSource.GT_PUBLIC}
    assert all(s.capacity is None for s in ds.sections)
    baseline = load_schedules(paths.dataset)[0]
    assert {a.exam_group_id for a in baseline.assignments} == {g.exam_group_id for g in ds.exam_groups}


def test_statistics_are_plausible(built):
    cfg, paths = built
    s = stats.run(paths, log=lambda *_: None)
    assert 4.0 <= s["avg_courses_per_student"] <= 5.0
    assert set(s["courses_per_student"]) <= {3, 4, 5, 6}
    assert s["courses_per_student"][4] + s["courses_per_student"][5] > 0.6 * 1000
    assert s["flags"] == []
    assert s["distinct_course_sets"] > 800
    assert len(s["by_major"]) >= 10
    assert (paths.dataset / "dataset_stats.json").exists()


def test_same_seed_same_output_different_seed_differs(built):
    cfg, paths = built
    a, _, _ = generate.generate(cfg, paths)
    b, _, _ = generate.generate(cfg, paths)
    c, _, _ = generate.generate(cfg.model_copy(update={"seed": 7}), paths)
    dump = lambda xs: [x.model_dump(mode="json") for x in xs]  # noqa: E731
    assert dump(a) == dump(b)
    assert dump(a) != dump(c)
    meta = json.loads(paths.generation_metadata.read_text())
    assert generate.canonical_sha(dump(a)) == meta["output_sha256"]["students"]


def test_validator_catches_injected_problems(built, tmp_path):
    cfg, paths = built
    work = DataPaths(root=tmp_path, dataset_name="fall2026")
    shutil.copytree(paths.root, tmp_path, dirs_exist_ok=True)
    students = read_models(work.dataset / "students.json", Student)
    smeta = json.loads(work.section_metadata.read_text())
    # register student 0 for a second primary section of a course they already take
    st = students[0]
    first = next(s for s in st.section_ids if smeta[s]["primary"])
    course_key = smeta[first]["course_key"]
    other = next(sid for sid, m in smeta.items() if m["course_key"] == course_key and m["primary"] and sid != first)
    students[0] = st.model_copy(update={"section_ids": [*st.section_ids, other]})
    # give student 1 a section of a new course that meets at the same time as one of theirs
    ds = load_dataset(work.dataset, strict=False)
    st1 = students[1]
    taken_courses = {smeta[s]["course_key"] for s in st1.section_ids}
    mine = [mt for s in st1.section_ids for mt in ds.build_index().sections[s].meeting_times]
    clash = next(
        s.section_id
        for s in ds.sections
        if smeta[s.section_id]["course_key"] not in taken_courses and any(a.overlaps(b) for a in s.meeting_times for b in mine)
    )
    students[1] = st1.model_copy(update={"section_ids": [*st1.section_ids, clash]})
    write_models(work.dataset / "students.json", students)
    codes = run_checks(work, cfg, reproducibility=True).codes("error")
    assert "meeting_overlap" in codes
    assert "duplicate_course_registration" in codes
    assert "reproducibility" in codes
    assert "enrollment_mismatch" in codes


@pytest.mark.parametrize(
    "granularity,expected_groups",
    [(ExamGroupGranularity.MEETING_PATTERN, 38), (ExamGroupGranularity.COURSE, 256)],
)
def test_alternative_granularities_build(tmp_path, granularity, expected_groups):
    cfg = load_config().model_copy(update={"exam_group_granularity": granularity, "num_students": 300})
    paths = _copy_snapshot(tmp_path)
    n = normalize.run(paths, cfg, log=lambda *_: None)
    assert n.report["counts"]["exam_groups"] == expected_groups
    generate.run(cfg, paths, log=lambda *_: None)
    assert run_checks(paths, cfg, reproducibility=False).valid


def test_committed_dataset_is_current():
    """The dataset checked into data/fall2026 must match what the code produces."""
    cfg = load_config()
    paths = DataPaths()
    if not paths.generation_metadata.exists():
        pytest.skip("no committed dataset")
    report = run_checks(paths, cfg, reproducibility=True)
    assert report.valid, [i.message for i in report.issues if i.severity.value == "error"][:10]
