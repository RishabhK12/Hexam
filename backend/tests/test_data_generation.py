"""Unit tests for the data ingestion adapters and the synthetic generator's rules."""

from __future__ import annotations

import urllib.error
from datetime import date, time
from pathlib import Path

import pytest

from hexam.data_generation.adapters.exam_matrix import ExamPeriod, _Frag, parse_fragments, parse_matrix
from hexam.data_generation.adapters.gt_scheduler import decode_term, prerequisite_course_ids
from hexam.data_generation.adapters.irp import parse_coe_enrollment
from hexam.data_generation.config import load_config
from hexam.data_generation.fetch_sources import resolve_term
from hexam.data_generation.generate import SectionInfo, section_eligible
from hexam.data_generation.normalize import period_label, timeslot_id
from hexam.data_generation.population import academic_year_for_term
from hexam.data_generation.sources import SourceCategory, SourceSpec, SourceStatus, fetch_source
from hexam.schemas.students import ClassStanding

REPO = Path(__file__).resolve().parents[2]
RAW = REPO / "data" / "raw"
MATRIX_PDF = RAW / "final_exam_matrix_202608.pdf"


# ---------------------------------------------------------------- exam matrix


@pytest.fixture(scope="module")
def matrix():
    pytest.importorskip("pypdf")
    if not MATRIX_PDF.exists():
        pytest.skip("cached matrix PDF not present")
    return parse_matrix(MATRIX_PDF)


def P(d: str, hhmm: str) -> tuple[date, time]:
    return date.fromisoformat(d), time(int(hhmm[:2]), int(hhmm[2:]))


class TestOfficialMatrix:
    """Expected values transcribed by reading the Fall 2026 PDF itself."""

    def test_structure(self, matrix):
        assert matrix.term_label == "FALL 2026"
        assert matrix.issues == []
        assert len(matrix.rows) == 129
        assert len(matrix.exam_periods) == 21
        assert matrix.windows == [(time(8, 0), time(10, 50)), (time(11, 20), time(14, 10)), (time(14, 40), time(17, 30)), (time(18, 0), time(20, 50))]

    @pytest.mark.parametrize(
        "days,start,end,expected",
        [
            ("MWF", "0930", "1020", ("2026-12-16", "0800")),
            ("TR", "0800", "0915", ("2026-12-17", "0800")),
            ("R", "0800", "1045", ("2026-12-14", "0800")),
            ("TR", "1100", "1215", ("2026-12-15", "1120")),
            ("TR", "1230", "1320", ("2026-12-17", "1120")),
            ("F", "1100", "1255", ("2026-12-14", "1120")),
            ("F", "0825", "1020", ("2026-12-14", "1440")),
            ("TR", "1400", "1515", ("2026-12-10", "1440")),
            ("TR", "1530", "1645", ("2026-12-15", "1440")),
            ("M", "1830", "2115", ("2026-12-14", "1800")),
            ("TR", "1700", "1855", ("2026-12-10", "1800")),
            ("F", "1830", "2115", ("2026-12-11", "1800")),
            # end time matters for WF 8:25 (two different blocks)
            ("WF", "0825", "0915", ("2026-12-11", "0800")),
            ("WF", "0825", "1020", ("2026-12-16", "0800")),
        ],
    )
    def test_known_rows(self, matrix, days, start, end, expected):
        t = lambda s: time(int(s[:2]), int(s[2:]))  # noqa: E731
        period, how = matrix.lookup(days, t(start), t(end))
        assert how == "matrix_exact"
        assert (period.date, period.start) == P(*expected)

    def test_block_sizes(self, matrix):
        from collections import Counter

        sizes = Counter(r.exam.key for r in matrix.rows)
        assert sizes["20261211-1800"] == 12 and sizes["20261214-1800"] == 12
        assert sizes["20261215-1120"] == 1 and sizes["20261211-0800"] == 10

    def test_end_sensitive(self, matrix):
        assert matrix.end_sensitive_patterns == {"WF 08:25"}

    def test_days_start_fallback(self, matrix):
        period, how = matrix.lookup("R", time(17, 0), time(18, 15))  # not printed; R 5:00 PM rows agree
        assert how == "matrix_days_start" and (period.date, period.start) == P("2026-12-17", "1800")
        assert matrix.lookup("MF", time(9, 30), time(10, 20)) == (None, "no_match")

    def test_common_exams(self, matrix):
        by_label = {c.label: c for c in matrix.common_exams}
        assert set(by_label) == {"ECE 3710", "MATH 1551", "PHYS 2211", "PHYS 2212", "MATH 1553/1554", "MATH 1552"}
        assert by_label["ECE 3710"].qualifier == "online"
        assert by_label["MATH 1553/1554"].course_keys == ("MATH 1553", "MATH 1554")
        assert (by_label["MATH 1553/1554"].exam.date, by_label["MATH 1553/1554"].exam.start) == P("2026-12-15", "1800")
        assert (by_label["PHYS 2211"].exam.date, by_label["PHYS 2211"].exam.start) == P("2026-12-11", "1800")
        assert matrix.common_exam_for("MATH 1554") is by_label["MATH 1553/1554"]

    def test_special_periods(self, matrix):
        kinds = sorted((s.kind, s.period.date.isoformat(), s.period.start.strftime("%H%M")) for s in matrix.special_periods)
        assert kinds == [
            ("conflict", "2026-12-18", "0800"),
            ("conflict", "2026-12-18", "1120"),
            ("reading", "2026-12-10", "0800"),
            ("reading", "2026-12-10", "1120"),
            ("reading", "2026-12-15", "0800"),
        ]
        reading = {(s.period.date, s.period.start) for s in matrix.special_periods}
        assert not reading & {(p.date, p.start) for p in matrix.exam_periods}


def test_matrix_geometry_on_synthetic_fragments():
    """Rows join the nearest date cell above them in their own column; fused rows split left/right."""
    F = _Frag
    frags = [
        F(227, 925, "FALL 2026 FINAL EXAM MATRIX"),
        F(203, 883, "Thursday, December 17"), F(203, 873, "8:00 AM - 10:50 AM"),
        F(493, 883, "Tuesday, December 15"), F(493, 873, "11:20 AM - 2:10 PM"),
        F(21, 882, "TR 8:00 AM 9:15 AM TR 11:00 AM 12:15 PM"),
        F(21, 871, "T 8:00 AM 10:45 AM"),
        F(493, 861, "Thursday, December 17"), F(493, 851, "11:20 AM - 2:10 PM"),
        F(21, 861, "TWRF 8:25 AM 9:15 AM TWRF 12:30 PM 1:20 PM"),
        F(318, 850, "TR 12:30 PM 1:20 PM"),
    ]
    m = parse_fragments(frags)
    got = {(r.days, r.class_start.strftime("%H%M"), r.column): (r.exam.date.day, r.exam.start.hour) for r in m.rows}
    assert got == {
        ("TR", "0800", "left"): (17, 8),
        ("T", "0800", "left"): (17, 8),
        ("TWRF", "0825", "left"): (17, 8),
        ("TR", "1100", "right"): (15, 11),
        ("TWRF", "1230", "right"): (17, 11),
        ("TR", "1230", "right"): (17, 11),
    }


def test_matrix_rejects_wrong_weekday():
    frags = [_Frag(0, 900, "FALL 2026 FINAL EXAM MATRIX"), _Frag(203, 883, "Friday, December 17"), _Frag(203, 873, "8:00 AM - 10:50 AM")]
    assert any("weekday" in i for i in parse_fragments(frags).issues)


def test_timeslot_ids_and_labels():
    p = ExamPeriod(date(2026, 12, 10), time(14, 40), time(17, 30))
    assert timeslot_id(p) == "TS-20261210-1440"
    assert period_label(p) == "Thu Dec 10, 2:40 PM–5:30 PM"


# --------------------------------------------------------------- GT Scheduler


def _tiny_term() -> dict:
    return {
        "updatedAt": "2026-11-04T15:03:56.566Z",
        "version": 3,
        "caches": {
            "periods": ["0930 - 1020", "TBA"],
            "dateRanges": ["08/24/2026 - 12/17/2026"],
            "scheduleTypes": ["Lecture*", "Studio*"],
            "campuses": ["Georgia Tech-Atlanta *", "Online"],
            "attributes": ["Honors &amp; Things"],
            "restrictions": ["Levels", "Campuses", "Majors"],
            "restrictionValues": {"Levels": ["Graduate Semester (GS)"], "Majors": ["Computer Science (CS)"]},
        },
        "courses": {
            "CS 1331": [
                "Intro-Object Orient Prog",
                {
                    "A": ["84558", [[0, "MWF", "Klaus 1443", 0, ["Ada Lovelace (P)", "Alan Turing"], 0, -1, -1]], 3, 0, 0, [0], -1, "Intro &amp; OOP",
                          [[[1, 0], [2, 0]], [[0, 0]]]],
                    "A01": ["84559", [[1, "", "TBA", 0, ["Ada Lovelace (P)"], 0, -1, -1]], 0, 1, 1, [], -1, "Intro", []],
                },
                ["and", {"id": "CS 1301", "grade": "C"}, ["or", {"id": "CS 1371"}, {"id": "CS 1315"}]],
                "Desc &amp; more",
                [{"id": "CS 1331L"}],
            ],
            "BAD KEY": ["x", {}, [], None, []],
        },
    }


def test_decode_term_tuple_positions():
    t = decode_term(_tiny_term(), "202608")
    assert set(t.courses) == {"CS 1331"}  # non-standard keys are skipped
    c = t.courses["CS 1331"]
    assert (c.course_id, c.subject, c.number, c.description) == ("CS-1331", "CS", "1331", "Desc & more")
    a = c.sections["A"]
    assert a.crn == "84558" and a.credit_hours == 3.0 and a.schedule_type == "Lecture"
    assert a.campus == "Georgia Tech-Atlanta *" and a.attributes == ("Honors & Things",) and a.title == "Intro & OOP"
    m = a.meetings[0]
    assert (m.days, m.start, m.end, m.room) == ("MWF", time(9, 30), time(10, 20), "Klaus 1443")
    assert m.instructors == ("Ada Lovelace", "Alan Turing") and m.primary_instructor == "Ada Lovelace"
    assert a.allowed == {"Campuses": ("Georgia Tech-Atlanta *",), "Majors": ("Computer Science (CS)",)}
    assert a.disallowed == {"Levels": ("Graduate Semester (GS)",)}
    comp = c.sections["A01"]
    assert comp.schedule_type == "Studio" and comp.campus == "Online" and not comp.meetings[0].is_scheduled
    assert comp.allowed == {} and comp.disallowed == {}
    assert prerequisite_course_ids(c.prerequisites) == {"CS 1301", "CS 1371", "CS 1315"}
    assert c.corequisites == ("CS 1331L",)


# ------------------------------------------------------------------------ IRP


IRP_HTML = """<table><tbody>
<tr><td rowspan=15>Mechanical<br>Engineering</td><td rowspan=2>2026-2027</td><td>FT</td><td>700</td><td>600</td><td>400</td><td>300</td><td>80</td><td>2,080</td></tr>
<tr><td>PT</td><td>2</td><td>&nbsp;</td><td>16</td><td>36</td><td>58</td><td>112</td></tr>
<tr><td colspan=2>Total</td><td>702</td><td>600</td><td>416</td><td>336</td><td>138</td><td>2,192</td></tr>
<tr><td rowspan=2>2025-2026</td><td>FT</td><td>1</td><td>1</td><td>1</td><td>1</td><td>1</td><td>5</td></tr>
<tr><td colspan=2>Total</td><td>1</td><td>1</td><td>1</td><td>1</td><td>1</td><td>5</td></tr>
</tbody></table>"""


def test_irp_parser(tmp_path):
    p = tmp_path / "irp.html"
    p.write_text(IRP_HTML)
    recs = parse_coe_enrollment(p)
    assert [(r.major_name, r.academic_year, r.undergrad_by_year, r.undergrad_total) for r in recs] == [
        ("Mechanical Engineering", "2026-2027", (702, 600, 416, 336, 138), 2192),
        ("Mechanical Engineering", "2025-2026", (1, 1, 1, 1, 1), 5),
    ]


def test_irp_parser_checks_totals(tmp_path):
    p = tmp_path / "irp.html"
    p.write_text(IRP_HTML.replace("2,192", "2,193"))
    with pytest.raises(ValueError, match="do not sum"):
        parse_coe_enrollment(p)


def test_irp_real_snapshot():
    path = RAW / "irp_coe_enrollment.html"
    if not path.exists():
        pytest.skip("IRP snapshot not cached")
    me = next(r for r in parse_coe_enrollment(path) if r.major_name == "Mechanical Engineering" and r.academic_year == "2026-2027")
    assert me.undergrad_total == sum(me.undergrad_by_year) > 1000


# ------------------------------------------------------------------- sources


def _spec(validator=None, filename="x.json"):
    return SourceSpec("k", "https://example.invalid/x", SourceCategory.COURSE_DATA, "test", filename, validator)


def test_fetch_source_success_and_cache(tmp_path):
    rec = fetch_source(_spec(), tmp_path, getter=lambda url: (200, b'{"a": 1}'))
    assert rec.status is SourceStatus.FETCHED and rec.http_status == 200 and rec.sha256 and (tmp_path / "x.json").exists()
    again = fetch_source(_spec(), tmp_path, previous=rec, getter=lambda url: pytest.fail("must not download"))
    assert again.status is SourceStatus.CACHED and again.retrieved_at == rec.retrieved_at


def test_fetch_source_failure_falls_back_to_cache(tmp_path):
    def boom(url):
        raise urllib.error.URLError("down")

    assert fetch_source(_spec(), tmp_path, getter=boom).status is SourceStatus.FAILED
    (tmp_path / "x.json").write_bytes(b"{}")
    rec = fetch_source(_spec(), tmp_path, force=True, getter=boom)
    assert rec.status is SourceStatus.CACHED and "download failed" in rec.error


def test_fetch_source_rejects_invalid_content(tmp_path):
    rec = fetch_source(_spec(validator=lambda b: "bad"), tmp_path, getter=lambda url: (200, b"<html>"))
    assert rec.status is SourceStatus.FAILED and not (tmp_path / "x.json").exists()


def test_probe_only(tmp_path):
    rec = fetch_source(_spec(filename=None), tmp_path, getter=lambda url: (200, b"hi"))
    assert rec.status is SourceStatus.PROBE_OK and rec.local_path is None


def test_resolve_term():
    assert resolve_term("202608", ["202508", "202608"]) == ("202608", None)
    term, note = resolve_term("202608", ["202402", "202408", "202508", "202602"])
    assert term == "202508" and "substituted" in note


def test_academic_year():
    assert academic_year_for_term("202608") == "2026-2027"
    assert academic_year_for_term("202702") == "2026-2027"


# --------------------------------------------------------- generator rules


def _sec(allowed=None, disallowed=None):
    return SectionInfo("X-1-A", "X 1", True, None, [], allowed or {}, disallowed or {})


@pytest.mark.parametrize(
    "allowed,disallowed,major,year,ok",
    [
        ({}, {}, "CS", ClassStanding.FIRST_YEAR, True),
        ({"Majors": ["Computer Science (CS)"]}, {}, "CS", ClassStanding.FIRST_YEAR, True),
        ({"Majors": ["Mechanical Engineering (ME)"]}, {}, "CS", ClassStanding.FIRST_YEAR, False),
        ({"Classes": ["Junior (JR)", "Sophomore (SO)"]}, {}, "CS", ClassStanding.FIRST_YEAR, False),
        ({"Classes": ["Junior (JR)"]}, {}, "CS", ClassStanding.THIRD_YEAR, True),
        ({"Classes": ["Senior (SR)"]}, {}, "CS", ClassStanding.FIFTH_YEAR_PLUS, True),
        ({"Levels": ["Graduate Semester (GS)"]}, {}, "CS", ClassStanding.THIRD_YEAR, False),
        ({}, {"Levels": ["Graduate Semester (GS)"]}, "CS", ClassStanding.THIRD_YEAR, True),
        ({}, {"Majors": ["Computer Science (CS)"]}, "CS", ClassStanding.THIRD_YEAR, False),
        ({"Colleges": ["College of Computing (C)"]}, {}, "CS", ClassStanding.THIRD_YEAR, True),
        ({"Colleges": ["College of Engineering (E)"]}, {}, "CS", ClassStanding.THIRD_YEAR, False),
        ({"Cohorts": ["Honors"]}, {}, "CS", ClassStanding.THIRD_YEAR, False),
    ],
)
def test_section_eligibility(allowed, disallowed, major, year, ok):
    assert section_eligible(_sec(allowed, disallowed), major, "College of Computing (C)", year) is ok


def test_default_config_is_consistent():
    cfg = load_config()
    assert cfg.seed == 42 and cfg.num_students == 1000
    assert abs(sum(cfg.course_load_distribution.values()) - 1) < 1e-9
    assert {"CS", "ME", "IE", "EE", "CMPE"} <= set(cfg.majors)
    keys = {c.key for c in cfg.courses}
    assert {"MATH 1554", "PHYS 2211", "CS 1331", "ISYE 3770", "ME 2202", "ECE 2020"} <= keys


def test_selection_rules_with_real_catalog():
    """Exclusive alternatives never co-occur and prerequisite pairs are rare (needs the committed dataset)."""
    from hexam.data_generation.generate import Catalog, Generator
    from hexam.data_generation.paths import DataPaths
    from hexam.data_generation.population import build_population
    from hexam.data_generation.sources import load_manifest

    paths = DataPaths()
    if not paths.section_metadata.exists():
        pytest.skip("normalized dataset not present")
    cfg = load_config()
    gen = Generator(cfg, Catalog.load(paths), build_population(cfg, load_manifest(paths.sources_manifest), paths.raw))
    profiles = [gen.draw_profile(i) for i in range(2000)]
    for p in profiles:
        cs = set(p.courses)
        assert len(cs) == len(p.courses)
        for group in cfg.exclusive_groups:
            assert len(cs & set(group)) <= 1, (p, group)
    both = sum(1 for p in profiles if {"CS 1331", "CS 1332"} <= set(p.courses))
    either = sum(1 for p in profiles if {"CS 1331", "CS 1332"} & set(p.courses))
    assert both <= 0.02 * max(1, either)  # CS 1331 is a prerequisite of CS 1332
    # weights are year-aware: CS 4641 is essentially absent among first-years
    first = [p for p in profiles if p.year is ClassStanding.FIRST_YEAR]
    assert sum("CS 4641" in p.courses for p in first) <= 0.01 * len(first)
