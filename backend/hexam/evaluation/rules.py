"""Time predicates behind every student-impact metric.

These pure functions are the *only* definitions of "overlap", "back-to-back",
"late evening → early morning", "same day" and the rolling windows. The evaluator applies them to
each student's actual exam instants; the optimizer applies them once to
timeslot pairs (``SlotRelations``) to build its model. Keeping one copy is what
makes the CP-SAT objective and the independent evaluation agree.

See ``hexam.schemas.metrics`` for the counting conventions.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from hexam.schemas import CAMPUS_TZ, EvaluationSettings, ExamTimeslot

WINDOW_24H = timedelta(hours=24)
WINDOW_48H = timedelta(hours=48)


def campus(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("naive datetime; exam instants must be timezone-aware")
    return dt.astimezone(CAMPUS_TZ)


def campus_date(dt: datetime) -> date:
    """Calendar day (America/New_York) an exam belongs to: the day it *starts*."""
    return campus(dt).date()


def overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    """Half-open intervals: an exam ending at 12:00 does not overlap one starting at 12:00."""
    return a_start < b_end and b_start < a_end


def is_back_to_back(prev_end: datetime, next_start: datetime, s: EvaluationSettings) -> bool:
    """``prev`` ends before ``next`` starts, with a break of at most the threshold."""
    gap = next_start - prev_end
    return timedelta(0) <= gap <= timedelta(minutes=s.back_to_back_max_gap_minutes)


def _wall_clock(dt: datetime):
    return campus(dt).timetz().replace(tzinfo=None)


def is_late_to_early(prev_start: datetime, prev_end: datetime, next_start: datetime, s: EvaluationSettings) -> bool:
    """Late exam (starts >= evening_start) followed by an early exam (starts <
    morning_end) on a later campus day, with an overnight break <= the max gap."""
    if campus_date(next_start) <= campus_date(prev_start):
        return False
    if _wall_clock(prev_start) < s.evening_start or _wall_clock(next_start) >= s.morning_end:
        return False
    gap = next_start - prev_end
    return timedelta(0) <= gap <= timedelta(hours=s.overnight_max_gap_hours)


def count_windows(starts: Sequence[datetime], k: int, window: timedelta) -> tuple[bool, int]:
    """For sorted ``starts``: (any k starts within < window, number of distinct
    k-subsets whose first and last start are < window apart)."""
    n = len(starts)
    hit = any(starts[i + k - 1] - starts[i] < window for i in range(n - k + 1))
    patterns = 0
    if hit:
        from itertools import combinations

        for combo in combinations(range(n), k):
            if starts[combo[-1]] - starts[combo[0]] < window:
                patterns += 1
    return hit, patterns


# ------------------------------------------------------------ slot relations


@dataclass(frozen=True, slots=True)
class SlotRelations:
    """The rules above, precomputed over timeslot pairs (for the optimizer).

    ``overlap[t]`` lists slots overlapping t (including t itself).
    ``b2b`` / ``late_early`` hold ordered (earlier, later) pairs.
    ``days`` groups slots by campus start date; ``windows24`` / ``windows48``
    hold, for each slot t, the slots starting in [t.start, t.start + W) — any
    k starts within < W fit in the window anchored at the earliest of them.
    """

    slot_ids: tuple[str, ...]
    overlap: dict[str, frozenset[str]]
    b2b: frozenset[tuple[str, str]]
    late_early: frozenset[tuple[str, str]]
    days: dict[date, tuple[str, ...]]
    windows24: tuple[tuple[str, ...], ...]
    windows48: tuple[tuple[str, ...], ...]

    @classmethod
    def build(cls, timeslots: Iterable[ExamTimeslot], settings: EvaluationSettings) -> SlotRelations:
        slots = sorted(timeslots, key=lambda t: (t.start, t.timeslot_id))
        overlap: dict[str, set[str]] = {t.timeslot_id: set() for t in slots}
        b2b: set[tuple[str, str]] = set()
        late: set[tuple[str, str]] = set()
        days: dict[date, list[str]] = defaultdict(list)
        for a in slots:
            days[campus_date(a.start)].append(a.timeslot_id)
            for b in slots:
                if overlaps(a.start, a.end, b.start, b.end):
                    overlap[a.timeslot_id].add(b.timeslot_id)
                    continue
                if a.end <= b.start:
                    if is_back_to_back(a.end, b.start, settings):
                        b2b.add((a.timeslot_id, b.timeslot_id))
                    if is_late_to_early(a.start, a.end, b.start, settings):
                        late.add((a.timeslot_id, b.timeslot_id))

        def windows(w: timedelta) -> tuple[tuple[str, ...], ...]:
            out = []
            for a in slots:
                win = tuple(b.timeslot_id for b in slots if a.start <= b.start < a.start + w)
                out.append(win)
            # drop windows contained in another (they add only redundant constraints)
            sets = [set(x) for x in out]
            keep = [x for i, x in enumerate(out) if not any(i != j and sets[i] < sets[j] for j in range(len(out)))]
            return tuple(dict.fromkeys(keep))

        return cls(
            slot_ids=tuple(t.timeslot_id for t in slots),
            overlap={k: frozenset(v) for k, v in overlap.items()},
            b2b=frozenset(b2b),
            late_early=frozenset(late),
            days={d: tuple(v) for d, v in days.items()},
            windows24=windows(WINDOW_24H),
            windows48=windows(WINDOW_48H),
        )
