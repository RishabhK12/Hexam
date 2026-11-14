"""Exact metric calculations on small hand-built datasets."""

from datetime import time

import pytest

from hexam.evaluation import Evaluator, SlotRelations
from hexam.schemas import EvaluationSettings, PenaltyWeights

from .factories import dt, make_dataset, slot, standard_slots

W = PenaltyWeights(
    simultaneous_conflicts=1000, back_to_back=10, night_to_morning=5,
    three_in_one_day=100, three_in_24h=50, four_in_48h=75,
)


def evaluate(ds, mapping, **kw):
    return Evaluator(ds.build_index(), kw.pop("settings", None)).evaluate(mapping, weights=W, **kw)


def m(out, key):
    return out.evaluation.metrics.get(key)


# ------------------------------------------------------------- conflicts


class TestSimultaneous:
    def test_touching_intervals_are_back_to_back_not_conflict(self):
        slots = [slot("A", dt(7, 9), dt(7, 12)), slot("B", dt(7, 12), dt(7, 15))]
        ds = make_dataset(["g1", "g2"], {"S000001": ["g1", "g2"]}, slots)
        out = evaluate(ds, {"g1": "A", "g2": "B"})
        assert m(out, "simultaneous_conflicts").count == 0
        assert m(out, "back_to_back").count == 1

    def test_partial_overlap_uses_real_intervals(self):
        # different slot IDs, but the intervals overlap by 30 min
        slots = [slot("A", dt(7, 9), dt(7, 12)), slot("B", dt(7, 11, 30), dt(7, 14))]
        ds = make_dataset(["g1", "g2"], {"S000001": ["g1", "g2"]}, slots)
        out = evaluate(ds, {"g1": "A", "g2": "B"})
        assert m(out, "simultaneous_conflicts").count == 1
        assert m(out, "back_to_back").count == 0  # overlapping pairs are never back-to-back

    def test_three_in_one_slot_is_three_pairs(self):
        ds = make_dataset(["g1", "g2", "g3"], {"S000001": ["g1", "g2", "g3"]}, standard_slots())
        out = evaluate(ds, {"g1": "T070800", "g2": "T070800", "g3": "T070800"})
        v = m(out, "simultaneous_conflicts")
        assert (v.count, v.students_affected) == (3, 1)
        assert out.students["S000001"].simultaneous_conflicts == 3

    def test_concurrent_groups_without_shared_students_are_fine(self):
        ds = make_dataset(["g1", "g2"], {"S000001": ["g1"], "S000002": ["g2"]}, standard_slots())
        out = evaluate(ds, {"g1": "T070800", "g2": "T070800"})
        assert m(out, "simultaneous_conflicts").count == 0
        assert out.evaluation.weighted_penalty == 0


# --------------------------------------------------------------- per day


class TestDayAndWindows:
    def test_three_in_one_day(self):
        ds = make_dataset(["a", "b", "c", "d"], {"S000001": ["a", "b", "c", "d"]}, standard_slots())
        out = evaluate(ds, {"a": "T070800", "b": "T071120", "c": "T071440", "d": "T071800"})
        v = m(out, "three_in_one_day")
        assert (v.count, v.students_affected) == (1, 1)
        assert out.evaluation.diagnostics.three_in_one_day_excess_exams == 2
        assert m(out, "back_to_back").count == 3
        assert m(out, "three_in_24h").count == 1
        assert out.evaluation.diagnostics.three_in_24h_patterns == 4  # C(4,3)
        assert m(out, "four_in_48h").count == 1

    def test_three_exams_spanning_two_days(self):
        # 14:40 and 18:00 on Dec 7, 08:00 Dec 8: not 3-in-a-day, but 3 within 24 h
        ds = make_dataset(["a", "b", "c"], {"S000001": ["a", "b", "c"]}, standard_slots())
        out = evaluate(ds, {"a": "T071440", "b": "T071800", "c": "T080800"})
        assert m(out, "three_in_one_day").count == 0
        assert m(out, "three_in_24h").count == 1
        assert m(out, "night_to_morning").count == 1
        assert m(out, "back_to_back").count == 1  # 17:30 → 18:00

    def test_exactly_24h_apart_is_not_within_24h(self):
        ds = make_dataset(["a", "b", "c"], {"S000001": ["a", "b", "c"]}, standard_slots())
        out = evaluate(ds, {"a": "T070800", "b": "T071440", "c": "T080800"})
        assert m(out, "three_in_24h").count == 0

    def test_four_in_48h_rolling_not_calendar(self):
        # Dec 7 18:00, Dec 8 08:00 + 14:40, Dec 9 11:20 → span 41h20m < 48h
        ds = make_dataset(["a", "b", "c", "d"], {"S000001": ["a", "b", "c", "d"]}, standard_slots())
        out = evaluate(ds, {"a": "T071800", "b": "T080800", "c": "T081440", "d": "T091120"})
        assert m(out, "four_in_48h").count == 1
        assert m(out, "three_in_24h").count == 1  # first three span 20h40m
        assert m(out, "three_in_one_day").count == 0

    def test_students_counted_once_for_rolling_windows(self):
        ds = make_dataset(list("abcde"), {"S000001": list("abcde")}, standard_slots())
        out = evaluate(ds, {"a": "T070800", "b": "T071120", "c": "T071440", "d": "T071800", "e": "T080800"})
        assert out.students["S000001"].three_in_24h == 1
        assert out.evaluation.diagnostics.three_in_24h_patterns > 1


# ---------------------------------------------------- late-to-early, midnight


class TestLateToEarlyAndMidnight:
    def test_evening_then_next_morning(self):
        ds = make_dataset(["a", "b"], {"S000001": ["a", "b"]}, standard_slots())
        out = evaluate(ds, {"a": "T071800", "b": "T080800"})
        assert m(out, "night_to_morning").count == 1
        assert m(out, "back_to_back").count == 0

    def test_evening_then_late_morning_is_not_early(self):
        ds = make_dataset(["a", "b"], {"S000001": ["a", "b"]}, standard_slots())
        assert m(evaluate(ds, {"a": "T071800", "b": "T081120"}), "night_to_morning").count == 0

    def test_gap_limit_excludes_weekend(self):
        ds = make_dataset(["a", "b"], {"S000001": ["a", "b"]}, standard_slots(days=(7, 9)))
        assert m(evaluate(ds, {"a": "T071800", "b": "T090800"}), "night_to_morning").count == 0

    def test_thresholds_are_configurable(self):
        ds = make_dataset(["a", "b"], {"S000001": ["a", "b"]}, standard_slots())
        s = EvaluationSettings(morning_end=time(12, 0), overnight_max_gap_hours=16)
        assert m(evaluate(ds, {"a": "T071800", "b": "T081120"}, settings=s), "night_to_morning").count == 1
        s = EvaluationSettings(back_to_back_max_gap_minutes=20)
        assert m(evaluate(ds, {"a": "T070800", "b": "T071120"}, settings=s), "back_to_back").count == 0

    def test_exam_crossing_midnight(self):
        slots = [
            slot("LATE", dt(7, 21), dt(7, 23, 50)),
            slot("EARLY", dt(8, 0, 10), dt(8, 2)),  # starts 20 min after LATE ends, on the next calendar day
            slot("MORN", dt(8, 8), dt(8, 10, 50)),
        ]
        ds = make_dataset(["a", "b", "c"], {"S000001": ["a", "b", "c"]}, slots)
        out = evaluate(ds, {"a": "LATE", "b": "EARLY", "c": "MORN"})
        d = out.students["S000001"]
        assert d.back_to_back == 1  # LATE→EARLY across midnight
        assert d.three_in_one_day == 0  # exams are attributed to the day they start
        assert d.night_to_morning == 2  # LATE→EARLY and LATE→MORN
        assert d.three_in_24h == 1

    def test_dst_timezone_offsets_are_respected(self):
        # Same wall-clock day, given in UTC: 13:00Z = 08:00 EST
        from datetime import datetime, timezone

        a = slot("A", datetime(2026, 12, 7, 13, tzinfo=timezone.utc), datetime(2026, 12, 7, 15, 50, tzinfo=timezone.utc))
        b = slot("B", dt(7, 16, 20), dt(7, 19, 10))
        assert a.start.hour == 8  # normalized to campus time
        ds = make_dataset(["a", "b"], {"S000001": ["a", "b"]}, [a, b])
        assert m(evaluate(ds, {"a": "A", "b": "B"}), "back_to_back").count == 0  # 10:50 → 16:20


# ------------------------------------------------- data shape, robustness


class TestDataHandling:
    def test_shared_groups_count_per_student(self):
        students = {f"S00000{i}": ["a", "b"] for i in range(1, 6)}
        ds = make_dataset(["a", "b"], students, standard_slots())
        out = evaluate(ds, {"a": "T070800", "b": "T071120"})
        v = m(out, "back_to_back")
        assert (v.count, v.students_affected) == (5, 5)
        assert out.evaluation.weighted_penalty == 50

    def test_multiple_sections_in_one_group_count_once(self):
        # student takes two courses whose sections share common exam "c"
        ds = make_dataset(["c", "x"], {"S000001": ["c", "c-X1", "x"]}, standard_slots(), extra_sections={"c": 1})
        out = evaluate(ds, {"c": "T070800", "x": "T071120"})
        d = out.students["S000001"]
        assert d.exam_count == 2 and d.simultaneous_conflicts == 0 and d.back_to_back == 1
        assert out.evaluation.metrics.exam_enrollments == 2

    def test_missing_assignment_is_invalid_and_reported(self):
        ds = make_dataset(["a", "b"], {"S000001": ["a", "b"]}, standard_slots())
        out = evaluate(ds, {"a": "T070800"})
        ev = out.evaluation
        assert not ev.valid
        assert "unassigned_exam_group" in {i.code for i in ev.issues}
        assert ev.diagnostics.unassigned_exam_group_ids == ["b"]
        assert ev.diagnostics.students_with_unassigned_exams == 1
        assert out.students["S000001"].exam_count == 1

    def test_unknown_slot_and_group_are_errors(self):
        ds = make_dataset(["a"], {"S000001": ["a"]}, standard_slots())
        ev = evaluate(ds, {"a": "NOPE", "ghost": "T070800"}).evaluation
        assert not ev.valid
        assert sum(i.code == "missing_reference" for i in ev.issues) == 2

    def test_restriction_violation_and_moved_fixed_exam(self):
        ds = make_dataset(
            ["a", "b"], {"S000001": ["a", "b"]}, standard_slots(),
            fixed={"a": "T070800"}, forbidden={"b": ["T071120"]},
        )
        ev = evaluate(ds, {"a": "T071440", "b": "T071120"}).evaluation
        codes = {(i.severity.value, i.code) for i in ev.issues}
        assert ("warning", "fixed_exam_moved") in codes
        assert ("error", "restriction_violation") in codes

    def test_breakdown_sums_and_student_penalties_add_up(self):
        ds = make_dataset(list("abcd"), {"S000001": list("abcd"), "S000002": ["a", "c"]}, standard_slots())
        out = evaluate(ds, {"a": "T070800", "b": "T071120", "c": "T071440", "d": "T080800"})
        ev = out.evaluation
        assert sum(ev.penalty_breakdown.values()) == ev.weighted_penalty == W.score(ev.metrics)
        assert sum(d.weighted_penalty for d in out.students.values()) == ev.weighted_penalty

    def test_deterministic(self):
        ds = make_dataset(list("abcd"), {"S000002": list("abcd"), "S000001": ["a", "c"]}, standard_slots())
        mp = {"a": "T070800", "b": "T071120", "c": "T071440", "d": "T080800"}
        a = evaluate(ds, mp).evaluation.model_dump(exclude={"evaluated_at"})
        b = evaluate(ds, dict(reversed(mp.items()))).evaluation.model_dump(exclude={"evaluated_at"})
        assert a == b


# ------------------------------------------------- slot relations match


def test_slot_relations_agree_with_evaluator():
    """Every slot pair: the optimizer's precomputed relation == evaluator count."""
    slots = standard_slots(days=(7, 8, 10))
    rel = SlotRelations.build(slots, EvaluationSettings())
    ds = make_dataset(["a", "b"], {"S000001": ["a", "b"]}, slots)
    ev = Evaluator(ds.build_index())
    for s1 in rel.slot_ids:
        for s2 in rel.slot_ids:
            d = ev.evaluate({"a": s1, "b": s2}, weights=W).students["S000001"]
            assert d.simultaneous_conflicts == int(s2 in rel.overlap[s1])
            assert d.back_to_back == int((s1, s2) in rel.b2b or (s2, s1) in rel.b2b)
            assert d.night_to_morning == int((s1, s2) in rel.late_early or (s2, s1) in rel.late_early)


@pytest.mark.parametrize("n", [3, 4])
def test_windows_cover_every_close_start_set(n):
    from itertools import combinations

    slots = standard_slots(days=(7, 8, 9))
    rel = SlotRelations.build(slots, EvaluationSettings())
    wins = [set(w) for w in (rel.windows24 if n == 3 else rel.windows48)]
    limit = 24 if n == 3 else 48
    by_id = {s.timeslot_id: s for s in slots}
    for combo in combinations(rel.slot_ids, n):
        starts = sorted(by_id[c].start for c in combo)
        close = (starts[-1] - starts[0]).total_seconds() < limit * 3600
        assert close == any(set(combo) <= w for w in wins)
