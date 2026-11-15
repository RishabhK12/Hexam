"""Cross-record validation, the lookup index, storage round-trip, and sample data."""

from datetime import timedelta

import pytest

from hexam.schemas import (
    DatasetSummary,
    DatasetValidationError,
    ExamAssignment,
    FixedAssignment,
    OptimizationConfig,
    PenaltyWeights,
    Student,
    validate_config,
    validate_dataset,
    validate_schedule,
)
from hexam.storage import load_dataset, save_dataset


def errors(report):
    return report.codes("error")


def warnings(report):
    return report.codes("warning")


# ------------------------------------------------------------- sample data


class TestSampleData:
    def test_dataset_is_clean(self, ds):
        report = validate_dataset(ds)
        assert report.issues == []

    def test_schedules_valid(self, ds, schedules):
        assert {s.kind for s in schedules.values()} == {"baseline", "optimized"}
        for s in schedules.values():
            assert validate_schedule(ds, s).issues == [], s.schedule_id

    def test_configs_valid(self, ds, configs, schedules):
        for c in configs.values():
            assert validate_config(ds, c, schedules=schedules.values()).issues == [], c.config_id

    def test_results_reference_known_records(self, results, configs, schedules):
        for r in results.values():
            assert r.config_id in configs
            assert r.baseline_schedule_id in schedules and r.optimized_schedule_id in schedules
            assert schedules[r.optimized_schedule_id].source_run_id == r.run_id

    def test_result_scores_are_consistent(self, results, configs):
        for r in results.values():
            w = configs[r.config_id].weights
            assert r.after.weighted_penalty == w.score(r.after.metrics) == r.objective_value
            assert r.before.weighted_penalty == w.score(r.before.metrics)
            assert sum(r.objective_breakdown.values()) == r.objective_value
            assert r.after.weighted_penalty < r.before.weighted_penalty

    def test_provenance_mix(self, ds):
        summary = DatasetSummary.from_dataset(ds)
        assert summary.provenance_breakdown["student"] == {"synthetic": len(ds.students)}
        assert set(summary.provenance_breakdown["timeslot"]) == {"assumed"}

    def test_round_trip(self, ds, tmp_path):
        save_dataset(ds, tmp_path)
        assert load_dataset(tmp_path) == ds

    def test_timestamps_serialize_with_new_york_offset(self, ds):
        dumped = ds.timeslots[0].model_dump(mode="json")
        assert dumped["start"].endswith("-05:00")


# ------------------------------------------------------------------- index


class TestIndex:
    def test_student_groups_derived_via_sections(self, ds):
        idx = ds.build_index()
        st = ds.students[0]
        expected = {idx.sections[s].exam_group_id for s in st.section_ids} - {None}
        assert set(idx.student_exam_groups[st.student_id]) == expected
        for g in expected:
            assert st.student_id in idx.group_students[g]

    def test_sections_without_final_are_skipped(self, ds):
        idx = ds.build_index()
        engl = [s for s in ds.sections if s.exam_group_id is None]
        assert engl and all(s.section_id not in sum(idx.sections_by_group.values(), []) for s in engl)

    def test_slot_adjacency(self, ds):
        idx = ds.build_index()
        # 5 days: Mon-Thu have 4 slots, Fri has 3, each day in chronological order
        assert [len(v) for v in idx.timeslots_by_day.values()] == [4, 4, 4, 4, 3]
        for ids in idx.timeslots_by_day.values():
            assert ids == sorted(ids, key=lambda t: idx.timeslots[t].start)
        assert list(idx.timeslots) == sorted(idx.timeslots, key=lambda t: idx.timeslots[t].start)


# ------------------------------------------------------- dataset integrity


class TestDatasetValidation:
    def test_duplicate_ids(self, ds):
        ds.courses.append(ds.courses[0].model_copy())
        ds.students.append(ds.students[0].model_copy())
        report = validate_dataset(ds)
        assert "duplicate_id" in errors(report) and report.error_count == 2

    def test_duplicate_crn(self, ds):
        ds.sections[1] = ds.sections[1].model_copy(update={"crn": ds.sections[0].crn})
        assert "duplicate_crn" in errors(validate_dataset(ds))

    def test_section_missing_course_and_group(self, ds):
        bad = ds.sections[0].model_copy(update={
            "section_id": "XX-0000-A", "course_id": "XX-0000", "crn": None, "exam_group_id": "EG-NOPE",
        })
        ds.sections.append(bad)
        msgs = [i.message for i in validate_dataset(ds).issues if i.code == "missing_reference"]
        assert any("unknown course" in m for m in msgs) and any("unknown exam group" in m for m in msgs)

    def test_student_missing_section(self, ds):
        ds.students.append(Student(student_id="S999999", major="CS", year="first_year", section_ids=["NOPE-1"]))
        assert "missing_reference" in errors(validate_dataset(ds))

    def test_group_missing_timeslot(self, ds):
        ds.exam_groups[0] = ds.exam_groups[0].model_copy(update={"forbidden_timeslot_ids": ["TS-NOPE"]})
        assert "missing_reference" in errors(validate_dataset(ds))

    def test_duplicate_course_registration(self, ds):
        ds.students.append(Student(student_id="S999999", major="CS", year="first_year",
                                   section_ids=["MATH-1554-A", "MATH-1554-B"]))
        assert "duplicate_course_registration" in errors(validate_dataset(ds))

    def test_forced_conflict_and_meeting_overlap_are_warnings(self, ds):
        # MATH-1554-A (MWF 11:00) and ISYE-3770-A (MWF 11:00): same meeting time
        ds.students.append(Student(student_id="S999999", major="CS", year="first_year",
                                   section_ids=["MATH-1554-A", "ISYE-3770-A"]))
        assert "registration_time_conflict" in warnings(validate_dataset(ds))
        # Put two different courses in one exam group -> forced conflict
        ds.sections = [s.model_copy(update={"exam_group_id": "EG-MWF-0930"}) if s.section_id == "CS-1332-A" else s
                       for s in ds.sections]
        ds.students.append(Student(student_id="S999998", major="CS", year="first_year",
                                   section_ids=["CS-1331-A", "CS-1332-A"]))
        report = validate_dataset(ds)
        assert "forced_conflict" in warnings(report) and report.valid

    def test_empty_group_warning(self, ds):
        ds.exam_groups.append(ds.exam_groups[0].model_copy(update={"exam_group_id": "EG-EMPTY"}))
        report = validate_dataset(ds)
        assert "empty_exam_group" in warnings(report) and report.valid

    def test_split_common_exam_warning(self, ds):
        ds.sections = [s.model_copy(update={"exam_group_id": "EG-TR-0800"}) if s.section_id == "MATH-1554-B" else s
                       for s in ds.sections]
        assert "split_common_exam" in warnings(validate_dataset(ds))

    def test_overlapping_timeslots(self, ds):
        t = ds.timeslots[0]
        ds.timeslots.append(t.model_copy(update={"timeslot_id": "TS-OVERLAP", "start": t.start + timedelta(hours=1),
                                                  "end": t.end + timedelta(hours=1)}))
        assert "overlapping_timeslots" in errors(validate_dataset(ds))

    def test_enrollment_mismatch_warning(self, ds):
        ds.sections[0] = ds.sections[0].model_copy(update={"enrollment": 0})
        assert "enrollment_mismatch" in warnings(validate_dataset(ds))

    def test_strict_load_raises(self, ds, tmp_path):
        ds.students.append(Student(student_id="S999999", major="CS", year="first_year", section_ids=["NOPE-1"]))
        save_dataset(ds, tmp_path)
        with pytest.raises(DatasetValidationError, match="missing_reference") as exc:
            load_dataset(tmp_path)
        assert exc.value.report.error_count == 1
        assert len(load_dataset(tmp_path, strict=False).students) == len(ds.students)


# ------------------------------------------------------ schedule integrity


class TestScheduleValidation:
    @pytest.fixture
    def baseline(self, schedules):
        return schedules["sched-baseline-202608"].model_copy(deep=True)

    def test_unknown_group_and_slot(self, ds, baseline):
        baseline.assignments.append(ExamAssignment(exam_group_id="EG-NOPE", timeslot_id="TS-NOPE"))
        msgs = [i.message for i in validate_schedule(ds, baseline).issues]
        assert any("unknown exam group" in m for m in msgs) and any("unknown timeslot" in m for m in msgs)

    def test_violates_fixed_time(self, ds, baseline):
        baseline.assignments = [
            a.model_copy(update={"timeslot_id": "TS-20261207-0800"}) if a.exam_group_id == "EG-COMMON-MATH-1554" else a
            for a in baseline.assignments
        ]
        assert "conflicting_assignment" in errors(validate_schedule(ds, baseline))

    def test_violates_forbidden_slot(self, ds, baseline):
        baseline.assignments = [
            a.model_copy(update={"timeslot_id": "TS-20261207-1800"}) if a.exam_group_id == "EG-COMMON-PHYS-2211" else a
            for a in baseline.assignments
        ]
        assert "restriction_violation" in errors(validate_schedule(ds, baseline))

    def test_incomplete_schedule(self, ds, baseline, schedules):
        baseline.assignments = baseline.assignments[1:]
        assert "unassigned_exam_group" in warnings(validate_schedule(ds, baseline))
        opt = schedules["sched-opt-run-0001"].model_copy(deep=True)
        opt.assignments = opt.assignments[1:]
        assert "unassigned_exam_group" in errors(validate_schedule(ds, opt))

    def test_capacity_warning(self, ds, baseline):
        slot = baseline.assignments[0].timeslot_id
        ds.timeslots = [t.model_copy(update={"max_students": 0}) if t.timeslot_id == slot else t for t in ds.timeslots]
        assert "capacity_exceeded" in warnings(validate_schedule(ds, baseline))


# -------------------------------------------------------- config integrity


class TestConfigValidation:
    def cfg(self, **kw):
        return OptimizationConfig(config_id="c", name="n", **kw)

    def test_conflicts_with_group_fixed_time(self, ds):
        c = self.cfg(fixed_assignments=[FixedAssignment(exam_group_id="EG-COMMON-MATH-1554",
                                                        timeslot_id="TS-20261207-0800")])
        assert "conflicting_assignment" in errors(validate_config(ds, c))

    def test_fixed_into_forbidden_slot(self, ds):
        c = self.cfg(fixed_assignments=[FixedAssignment(exam_group_id="EG-COMMON-PHYS-2211",
                                                        timeslot_id="TS-20261207-1800")])
        assert "restriction_violation" in errors(validate_config(ds, c))

    def test_restrictions_can_be_disabled(self, ds):
        c = self.cfg(
            hard_constraints={"enforce_group_restrictions": False},
            fixed_assignments=[FixedAssignment(exam_group_id="EG-COMMON-PHYS-2211", timeslot_id="TS-20261207-1800")],
        )
        assert validate_config(ds, c).valid

    def test_excluding_a_groups_fixed_slot(self, ds):
        c = self.cfg(excluded_timeslot_ids=["TS-20261208-1800"])
        assert "conflicting_assignment" in errors(validate_config(ds, c))

    def test_excluding_every_allowed_slot(self, ds):
        evenings = [t.timeslot_id for t in ds.timeslots if t.is_evening]
        c = self.cfg(excluded_timeslot_ids=evenings)  # MATH 2551 may only use evenings
        report = validate_config(ds, c)
        assert any(i.code == "no_available_timeslots" and "MATH-2551" in i.message for i in report.issues)

    def test_unknown_references(self, ds, schedules):
        c = self.cfg(
            baseline_schedule_id="sched-nope",
            excluded_timeslot_ids=["TS-NOPE"],
            fixed_assignments=[FixedAssignment(exam_group_id="EG-NOPE", timeslot_id="TS-20261207-0800")],
        )
        report = validate_config(ds, c, schedules=schedules.values())
        assert sum(i.code == "missing_reference" for i in report.issues) == 3

    def test_report_serializes_for_api(self, ds):
        c = self.cfg(excluded_timeslot_ids=["TS-NOPE"])
        dumped = validate_config(ds, c).model_dump(mode="json")
        assert dumped["valid"] is False and dumped["error_count"] == 1
        assert dumped["issues"][0]["code"] == "missing_reference"


def test_weights_default_ordering():
    """Hexam priority order: conflicts ≫ 3-in-24 h (incl. 3 in a day) > back-to-back > 4-in-48 h > late/early exams."""
    w = PenaltyWeights()
    assert w.simultaneous_conflicts > w.three_in_24h > w.back_to_back > w.four_in_48h
    assert w.four_in_48h > w.late_evening and w.four_in_48h > w.early_morning
