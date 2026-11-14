"""Decoder for GT Scheduler crawler-v2 term files (``<term>.json``).

The format is documented in ``src/types.ts`` of github.com/gt-scheduler/crawler-v2.
Courses, sections and meetings are positional tuples whose strings are interned
in ``caches``::

    Course  = [fullName, sections{code: Section}, prerequisites, description, corequisites]
    Section = [crn, meetings[], creditHours, scheduleTypeIndex, campusIndex,
               attributeIndices[], gradeBaseIndex, sectionTitle, restrictionData]
    Meeting = [periodIndex, days, room, locationIndex, instructors[],
               dateRangeIndex, finalDateIndex, finalTimeIndex]
    restrictionData = [allowed[[categoryIdx, valueIdx]], disallowed[...]] | []

``periods`` are ``"HHMM - HHMM"`` or ``"TBA"``. Restriction values index
``caches.campuses`` for the campus category and ``caches.restrictionValues[category]``
otherwise (``src/steps/restrictions/parse.ts``). The crawler does **not** include
section capacity or enrollment, so neither is decoded here.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, time
from pathlib import Path
from typing import Any

# Section tuple positions
S_CRN, S_MEETINGS, S_CREDITS, S_SCHEDTYPE, S_CAMPUS, S_ATTRS, S_GRADEBASE, S_TITLE, S_RESTRICTIONS = range(9)
# Meeting tuple positions
M_PERIOD, M_DAYS, M_ROOM, M_LOCATION, M_INSTRUCTORS, M_DATERANGE, M_FINAL_DATE, M_FINAL_TIME = range(8)
# Course tuple positions
C_NAME, C_SECTIONS, C_PREREQS, C_DESCRIPTION, C_COREQS = range(5)

_COURSE_KEY = re.compile(r"^([A-Z]{2,4}) (\d{4}[A-Z]?)$")
_PERIOD = re.compile(r"^(\d{2})(\d{2}) - (\d{2})(\d{2})$")
_PRIMARY = re.compile(r"\s*\(P\)\s*$")


@dataclass(frozen=True)
class RawMeeting:
    days: str  # OSCAR day codes, e.g. "MWF"; "" when TBA
    start: time | None
    end: time | None
    room: str
    instructors: tuple[str, ...]  # "(P)" marker stripped
    primary_instructor: str | None
    date_range: str

    @property
    def is_scheduled(self) -> bool:
        return bool(self.days) and self.start is not None and self.end is not None


@dataclass(frozen=True)
class RawSection:
    course_key: str  # "CS 1331"
    code: str  # "A", "A01", ...
    crn: str
    meetings: tuple[RawMeeting, ...]
    credit_hours: float
    schedule_type: str  # cache value without trailing "*", e.g. "Lecture"
    campus: str
    attributes: tuple[str, ...]
    title: str
    allowed: dict[str, tuple[str, ...]] = field(default_factory=dict)  # restriction category -> values
    disallowed: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def instructors(self) -> list[str]:
        seen: dict[str, None] = {}
        for m in self.meetings:
            for name in m.instructors:
                seen.setdefault(name, None)
        return list(seen)


@dataclass(frozen=True)
class RawCourse:
    key: str  # "CS 1331"
    subject: str
    number: str
    title: str
    sections: dict[str, RawSection]
    prerequisites: Any  # crawler prefix tree, e.g. ["and", {"id": "CS 1331", "grade": "C"}, ["or", ...]]
    description: str | None
    corequisites: tuple[str, ...]

    @property
    def course_id(self) -> str:
        return f"{self.subject}-{self.number}"


@dataclass(frozen=True)
class TermData:
    term: str
    updated_at: datetime
    version: int
    courses: dict[str, RawCourse]
    caches: dict[str, Any]


def _parse_period(period: str) -> tuple[time | None, time | None]:
    m = _PERIOD.match(period)
    if not m:
        return None, None  # "TBA"
    h1, m1, h2, m2 = (int(g) for g in m.groups())
    return time(h1, m1), time(h2, m2)


def _decode_restrictions(raw: list, caches: dict[str, Any]) -> tuple[dict, dict]:
    if not raw:
        return {}, {}
    cats: list[str] = caches.get("restrictions", [])
    values: dict[str, list[str]] = caches.get("restrictionValues", {})
    out: list[dict[str, list[str]]] = [{}, {}]
    for side, pairs in enumerate(raw[:2]):
        for cat_idx, val_idx in pairs:
            cat = cats[cat_idx]
            pool = caches["campuses"] if "campus" in cat.lower() else values.get(cat, [])
            val = html.unescape(pool[val_idx]) if 0 <= val_idx < len(pool) else f"<unknown {val_idx}>"
            out[side].setdefault(cat, []).append(val)
    return ({k: tuple(v) for k, v in out[0].items()}, {k: tuple(v) for k, v in out[1].items()})


def decode_term(obj: dict[str, Any], term: str) -> TermData:
    caches = obj["caches"]
    periods = caches["periods"]
    date_ranges = caches["dateRanges"]
    sched_types = caches["scheduleTypes"]
    campuses = caches["campuses"]
    attributes = caches["attributes"]

    courses: dict[str, RawCourse] = {}
    for key, c in obj["courses"].items():
        m = _COURSE_KEY.match(key)
        if not m:
            continue  # non-standard course keys are not representable as CourseId
        sections: dict[str, RawSection] = {}
        for code, s in c[C_SECTIONS].items():
            meetings = []
            for mt in s[S_MEETINGS]:
                start, end = _parse_period(periods[mt[M_PERIOD]])
                names = tuple(_PRIMARY.sub("", n).strip() for n in mt[M_INSTRUCTORS])
                primary = next((_PRIMARY.sub("", n).strip() for n in mt[M_INSTRUCTORS] if _PRIMARY.search(n)), None)
                dr = mt[M_DATERANGE]
                meetings.append(
                    RawMeeting(
                        days=mt[M_DAYS],
                        start=start,
                        end=end,
                        room=html.unescape(mt[M_ROOM]),
                        instructors=names,
                        primary_instructor=primary,
                        date_range=date_ranges[dr] if 0 <= dr < len(date_ranges) else "",
                    )
                )
            allowed, disallowed = _decode_restrictions(s[S_RESTRICTIONS] if len(s) > S_RESTRICTIONS else [], caches)
            sections[code] = RawSection(
                course_key=key,
                code=code,
                crn=s[S_CRN],
                meetings=tuple(meetings),
                credit_hours=float(s[S_CREDITS]),
                schedule_type=sched_types[s[S_SCHEDTYPE]].rstrip("*").strip(),
                campus=html.unescape(campuses[s[S_CAMPUS]]),
                attributes=tuple(html.unescape(attributes[i]) for i in s[S_ATTRS]),
                title=html.unescape(s[S_TITLE]),
                allowed=allowed,
                disallowed=disallowed,
            )
        courses[key] = RawCourse(
            key=key,
            subject=m.group(1),
            number=m.group(2),
            title=html.unescape(c[C_NAME]),
            sections=sections,
            prerequisites=c[C_PREREQS] if len(c) > C_PREREQS else [],
            description=html.unescape(c[C_DESCRIPTION]) if len(c) > C_DESCRIPTION and c[C_DESCRIPTION] else None,
            corequisites=tuple(x["id"] for x in (c[C_COREQS] if len(c) > C_COREQS else [])),
        )
    updated = datetime.fromisoformat(obj["updatedAt"].replace("Z", "+00:00"))
    return TermData(term=term, updated_at=updated, version=int(obj["version"]), courses=courses, caches=caches)


def load_term(path: Path, term: str) -> TermData:
    return decode_term(json.loads(path.read_bytes()), term)


def prerequisite_course_ids(tree: Any) -> set[str]:
    """All course keys mentioned anywhere in a crawler prerequisite tree."""
    out: set[str] = set()
    if isinstance(tree, dict) and "id" in tree:
        out.add(tree["id"])
    elif isinstance(tree, list):
        for item in tree:
            out |= prerequisite_course_ids(item)
    return out


def index_terms(obj: dict[str, Any]) -> list[str]:
    return [t["term"] for t in obj.get("terms", [])]
