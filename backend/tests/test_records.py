"""Single-record validation: each model rejects bad data on its own."""

from datetime import datetime, time, timezone

import pytest
from pydantic import ValidationError

from hexam.schemas import (
    CAMPUS_TZ,
    Course,
    ExamAssignment,
    ExamGroup,
    ExamSchedule,
    ExamTimeslot,
    MeetingTime,
    MetricValue,
    Provenance,
    Section,
    Student,
)


def ts(**kw):
    base = dict(
        timeslot_id="TS-1",
        start="2026-12-07T08:00:00-05:00",
        end="2026-12-07T10:50:00-05:00",
        provenance=Provenance.assumed(),
    )
    return ExamTimeslot(**(base | kw))


def section(**kw):
    base = dict(
        section_id="CS-1331-A",
        course_id="CS-1331",
        section_code="A",
        crn="81234",
        enrollment=10,
        exam_group_id="EG-1",
        provenance=Provenance.synthetic(),
    )
    return Section(**(base | kw))


# ------------------------------------------------------------- time handling


class TestCampusTime:
    def test_utc_input_is_normalized_to_new_york(self):
        t = ts(start="2026-12-07T13:00:00Z", end="2026-12-07T15:50:00Z")
        assert t.start.tzinfo == CAMPUS_TZ
        assert (t.start.hour, t.start.utcoffset().total_seconds()) == (8, -5 * 3600)
        assert t.model_dump(mode="json")["start"] == "2026-12-07T08:00:00-05:00"

    def test_daylight_saving_offset_in_may(self):
        t = ts(start="2026-05-04T12:00:00Z", end="2026-05-04T14:50:00Z")
        assert t.model_dump(mode="json")["start"] == "2026-05-04T08:00:00-04:00"

    def test_naive_datetime_rejected(self):
        with pytest.raises(ValidationError, match="timezone"):
            ts(start="2026-12-07T08:00:00", end="2026-12-07T10:50:00")

    def test_end_must_follow_start(self):
        with pytest.raises(ValidationError, match="after start"):
            ts(end="2026-12-07T08:00:00-05:00")

    def test_slot_cannot_cross_midnight(self):
        with pytest.raises(ValidationError, match="same campus calendar day"):
            ts(start="2026-12-07T22:00:00-05:00", end="2026-12-08T00:30:00-05:00")

    def test_evening_and_duration(self):
        assert ts().duration_minutes == 170 and not ts().is_evening
        assert ts(start="2026-12-07T18:00:00-05:00", end="2026-12-07T20:50:00-05:00").is_evening

    def test_negative_capacity_rejected(self):
        with pytest.raises(ValidationError):
            ts(max_students=-1)


class TestMeetingTime:
    def test_end_before_start(self):
        with pytest.raises(ValidationError, match="after start_time"):
            MeetingTime(days=["M"], start_time="10:00", end_time="09:00")

    def test_duplicate_days(self):
        with pytest.raises(ValidationError, match="duplicate meeting days"):
            MeetingTime(days=["M", "M"], start_time="09:00", end_time="10:00")

    def test_tz_aware_time_rejected(self):
        with pytest.raises(ValidationError, match="wall-clock"):
            MeetingTime(days=["T"], start_time=time(9, tzinfo=timezone.utc), end_time="10:00")

    def test_invalid_day_code(self):
        with pytest.raises(ValidationError):
            MeetingTime(days=["Th"], start_time="09:00", end_time="10:00")

    def test_overlap_and_pattern_key(self):
        a = MeetingTime(days=["M", "W", "F"], start_time="09:30", end_time="10:20")
        b = MeetingTime(days=["W"], start_time="10:00", end_time="11:00")
        c = MeetingTime(days=["T", "R"], start_time="09:30", end_time="10:45")
        assert a.overlaps(b) and not a.overlaps(c)
        assert a.pattern_key == "MWF 09:30"


# ------------------------------------------------------ courses and sections


class TestCourseSection:
    def test_course_id_must_match_subject_number(self):
        with pytest.raises(ValidationError, match="must equal"):
            Course(course_id="CS-1332", subject="CS", number="1331", title="x", provenance=Provenance.assumed())

    def test_bad_course_id_format(self):
        with pytest.raises(ValidationError):
            Course(course_id="CS 1331", subject="CS", number="1331", title="x", provenance=Provenance.assumed())

    def test_crn_must_be_five_digits(self):
        with pytest.raises(ValidationError):
            section(crn="1234")

    def test_negative_enrollment(self):
        with pytest.raises(ValidationError):
            section(enrollment=-5)

    def test_overlapping_own_meeting_times(self):
        mt = {"days": ["M"], "start_time": "09:00", "end_time": "10:00"}
        with pytest.raises(ValidationError, match="overlap"):
            section(meeting_times=[mt, mt])

    def test_no_final_exam(self):
        assert not section(exam_group_id=None).has_final_exam

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError, match="Extra inputs"):
            section(room="CULC 152")

    def test_ids_cannot_contain_spaces(self):
        with pytest.raises(ValidationError):
            section(section_id="CS 1331 A")


# ------------------------------------------------------------------- students


class TestStudent:
    def test_duplicate_registration(self):
        with pytest.raises(ValidationError, match="duplicate section registrations"):
            Student(student_id="S000001", major="CS", year="first_year", section_ids=["A", "A"])

    def test_must_be_synthetic(self):
        with pytest.raises(ValidationError, match="must be synthetic"):
            Student(
                student_id="S000001", major="CS", year="first_year", section_ids=["A"],
                provenance=Provenance(source="gt_public", source_ref="x"),
            )

    def test_student_id_format(self):
        with pytest.raises(ValidationError):
            Student(student_id="903123456", major="CS", year="first_year", section_ids=["A"])

    def test_default_provenance_is_synthetic(self):
        s = Student(student_id="S000001", major="CS", year="first_year", section_ids=["A"])
        assert s.provenance.source == "synthetic"

    def test_needs_at_least_one_section(self):
        with pytest.raises(ValidationError):
            Student(student_id="S000001", major="CS", year="first_year", section_ids=[])


# ---------------------------------------------------------------- provenance


class TestProvenance:
    def test_public_data_requires_source(self):
        with pytest.raises(ValidationError, match="source_ref"):
            Provenance(source="gt_public")

    def test_field_sources_must_name_real_fields(self):
        with pytest.raises(ValidationError, match="unknown fields"):
            section(provenance=Provenance(source="gt_public", source_ref="oscar", field_sources={"nope": "synthetic"}))

    def test_field_override(self):
        p = Provenance(source="gt_public", source_ref="oscar", field_sources={"enrollment": "synthetic"})
        s = section(provenance=p)
        assert s.provenance.source_for("enrollment") == "synthetic"
        assert s.provenance.source_for("crn") == "gt_public"

    def test_unknown_source_rejected(self):
        with pytest.raises(ValidationError):
            Provenance(source="scraped")


# --------------------------------------------------------------- exam groups


class TestExamGroup:
    def group(self, **kw):
        base = dict(exam_group_id="EG-1", name="g", kind="common", provenance=Provenance.assumed())
        return ExamGroup(**(base | kw))

    def test_fixed_slot_cannot_be_forbidden(self):
        with pytest.raises(ValidationError, match="violates"):
            self.group(fixed_timeslot_id="TS-1", forbidden_timeslot_ids=["TS-1"])

    def test_fixed_slot_must_be_allowed(self):
        with pytest.raises(ValidationError, match="violates"):
            self.group(fixed_timeslot_id="TS-1", allowed_timeslot_ids=["TS-2"])

    def test_allowed_and_forbidden_disjoint(self):
        with pytest.raises(ValidationError, match="both allowed and forbidden"):
            self.group(allowed_timeslot_ids=["TS-1"], forbidden_timeslot_ids=["TS-1"])

    def test_empty_allowed_list_rejected(self):
        with pytest.raises(ValidationError, match="non-empty"):
            self.group(allowed_timeslot_ids=[])

    def test_meeting_pattern_required(self):
        with pytest.raises(ValidationError, match="require meeting_pattern"):
            self.group(kind="meeting_pattern")

    def test_permits(self):
        g = self.group(allowed_timeslot_ids=["TS-1", "TS-2"], forbidden_timeslot_ids=["TS-3"])
        assert g.permits("TS-1") and not g.permits("TS-3") and not g.permits("TS-9")


# ------------------------------------------------------------------ schedules


class TestSchedule:
    def sched(self, **kw):
        base = dict(
            schedule_id="s", name="n", kind="baseline",
            assignments=[ExamAssignment(exam_group_id="EG-1", timeslot_id="TS-1")],
            provenance=Provenance.assumed(),
        )
        return ExamSchedule(**(base | kw))

    def test_group_assigned_twice(self):
        with pytest.raises(ValidationError, match="more than once"):
            self.sched(assignments=[
                {"exam_group_id": "EG-1", "timeslot_id": "TS-1"},
                {"exam_group_id": "EG-1", "timeslot_id": "TS-2"},
            ])

    def test_optimized_needs_run(self):
        with pytest.raises(ValidationError, match="source_run_id"):
            self.sched(kind="optimized")

    def test_created_at_defaults_to_campus_time(self):
        s = self.sched()
        assert s.created_at.tzinfo == CAMPUS_TZ
        assert abs((datetime.now(CAMPUS_TZ) - s.created_at).total_seconds()) < 60

    def test_as_mapping(self):
        assert self.sched().as_mapping() == {"EG-1": "TS-1"}


class TestMetricValue:
    def test_affected_cannot_exceed_count(self):
        with pytest.raises(ValidationError):
            MetricValue(count=1, students_affected=2)

    def test_nonzero_count_needs_affected_student(self):
        with pytest.raises(ValidationError):
            MetricValue(count=3, students_affected=0)
