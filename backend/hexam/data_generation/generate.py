"""Generate anonymous synthetic students enrolled in real Fall sections.

    python -m hexam.data_generation.generate --num-students 1000 --seed 42

Algorithm (explainable, deterministic for a given snapshot + config + seed):

1. **Student profile** — major ~ real/assumed headcount weights (see ``population``);
   year ~ that major's year-in-program distribution; course load k ~
   ``course_load_distribution`` (number of *subset* courses, 3–6).
2. **Course weights** — for each subset course c:
   ``w = curriculum(major, year, c) × level(c, year) × sections(c)^α × eligible(c)``
   where curriculum = the major's own year weights for c if listed, else the sum
   of its track multipliers × track year weights; level = suitability of the
   course level (1xxx–4xxx) for the year; sections(c) = number of real eligible
   primary sections (popularity proxy); eligible = some section's OSCAR
   restrictions (major / class / college / level) admit this student.
3. **Selection without replacement** — draw k courses ∝ w; after each draw:
   remove its exclusive alternatives, multiply courses linked to it by an OSCAR
   prerequisite relation by ``prerequisite_same_term_factor``, and multiply
   affine courses by their ``pair_affinities`` factor (co-enrollment).
4. **Synthetic capacities** — real capacities are not public in our sources, so
   after drafting every student's courses, each section gets a *sample* capacity
   ``ceil(course demand × (1 + slack) / sections) + 1`` (components likewise).
   These are recorded as synthetic and never presented as real.
5. **Section assignment** — backtracking search per student (most constrained
   course first): eligible primary section with free capacity and no meeting
   overlap, plus a linked or free component (studio/lab/recitation) when the
   course has them. Section order is randomized, weighted by remaining capacity.
6. **Repair** — if no conflict-free assignment exists, the blocking course is
   replaced by a fresh weighted draw (bounded attempts); a student who still
   cannot reach ``min_courses_per_student`` is redrawn and counted as a failure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..schemas import DataSource, MeetingTime, Provenance, Section, Student
from ..schemas.students import ClassStanding
from ..storage import read_models, write_models
from .config import YEARS, GenerationConfig, load_config
from .paths import DataPaths
from .population import MajorPopulation, build_population, population_summary
from .sources import load_manifest

GENERATOR_ID = "hexam.data_generation.generate"
GENERATOR_VERSION = "1.0"

CLASS_NAMES = {
    ClassStanding.FIRST_YEAR: "Freshman",
    ClassStanding.SECOND_YEAR: "Sophomore",
    ClassStanding.THIRD_YEAR: "Junior",
    ClassStanding.FOURTH_YEAR: "Senior",
    ClassStanding.FIFTH_YEAR_PLUS: "Senior",
}


# ------------------------------------------------------------------ catalog view


@dataclass
class SectionInfo:
    section_id: str
    course_key: str
    primary: bool
    component_of: str | None
    meetings: list[MeetingTime]
    allowed: dict[str, list[str]]
    disallowed: dict[str, list[str]]


@dataclass
class CourseInfo:
    key: str
    course_id: str
    level: str
    primaries: list[str]
    linked_components: dict[str, list[str]]  # primary section -> its components
    free_components: list[str]
    prerequisites: set[str]


@dataclass
class Catalog:
    courses: dict[str, CourseInfo]  # by course key
    sections: dict[str, SectionInfo]

    @classmethod
    def load(cls, paths: DataPaths) -> Catalog:
        sections = read_models(paths.dataset / "sections.json", Section)
        smeta = json.loads(paths.section_metadata.read_text(encoding="utf-8"))
        cmeta = json.loads(paths.course_metadata.read_text(encoding="utf-8"))
        infos = {
            s.section_id: SectionInfo(
                s.section_id,
                smeta[s.section_id]["course_key"],
                smeta[s.section_id]["primary"],
                smeta[s.section_id]["component_of"],
                list(s.meeting_times),
                smeta[s.section_id]["restrictions_allowed"],
                smeta[s.section_id]["restrictions_disallowed"],
            )
            for s in sections
        }
        courses: dict[str, CourseInfo] = {}
        for cid in sorted(cmeta):
            m = cmeta[cid]
            linked: dict[str, list[str]] = defaultdict(list)
            free: list[str] = []
            for sid in m["component_sections"]:
                (linked[infos[sid].component_of].append(sid) if infos[sid].component_of else free.append(sid))  # type: ignore[index]
            courses[m["key"]] = CourseInfo(
                key=m["key"],
                course_id=cid,
                level=m["key"].split()[1][0],
                primaries=list(m["primary_sections"]),
                linked_components={k: sorted(v) for k, v in sorted(linked.items())},
                free_components=sorted(free),
                prerequisites=set(m["prerequisite_keys_in_subset"]),
            )
        return cls(courses, infos)


def section_eligible(sec: SectionInfo, major: str, college: str, year: ClassStanding) -> bool:
    """Apply OSCAR section restrictions (as published) to a synthetic student profile."""
    cls_name = CLASS_NAMES[year]
    tag = f"({major})"
    for cat, vals in sec.allowed.items():
        if cat == "Majors" and not any(v.endswith(tag) for v in vals):
            return False
        if cat == "Classes" and not any(v.startswith(cls_name) for v in vals):
            return False
        if cat == "Colleges" and college not in vals:
            return False
        if cat == "Levels" and not any("Undergraduate" in v for v in vals):
            return False
        if cat not in ("Majors", "Classes", "Colleges", "Levels", "Campuses"):
            return False  # unmodelled restriction: be conservative
    for cat, vals in sec.disallowed.items():
        if cat == "Majors" and any(v.endswith(tag) for v in vals):
            return False
        if cat == "Classes" and any(v.startswith(cls_name) for v in vals):
            return False
        if cat == "Colleges" and college in vals:
            return False
        if cat == "Levels" and any("Undergraduate" in v for v in vals):
            return False
    return True


# -------------------------------------------------------------------- generator


@dataclass
class Profile:
    index: int
    major: str
    year: ClassStanding
    load: int
    courses: list[str] = field(default_factory=list)


@dataclass
class GenerationStats:
    profiles_drawn: int = 0
    student_redraws: int = 0
    course_replacements: int = 0
    courses_dropped: int = 0
    load_shortfall: int = 0  # students who ended with fewer subset courses than drawn
    search_nodes: int = 0
    redraw_reasons: Counter = field(default_factory=Counter)


class Generator:
    def __init__(self, cfg: GenerationConfig, catalog: Catalog, population: list[MajorPopulation]):
        self.cfg = cfg
        self.cat = catalog
        self.pop = population
        self.rng = random.Random(cfg.seed)
        self.stats = GenerationStats()
        self.course_keys = sorted(catalog.courses)
        self.affinity: dict[str, list[tuple[str, float]]] = defaultdict(list)
        for p in cfg.pair_affinities:
            self.affinity[p.a].append((p.b, p.factor))
            self.affinity[p.b].append((p.a, p.factor))
        self.exclusive: dict[str, set[str]] = defaultdict(set)
        for g in cfg.exclusive_groups:
            for k in g:
                self.exclusive[k] |= set(g) - {k}
        self.prereq_related: dict[str, set[str]] = defaultdict(set)
        for k, c in catalog.courses.items():
            for p in c.prerequisites:
                if p in catalog.courses:
                    self.prereq_related[k].add(p)
                    self.prereq_related[p].add(k)
        self._base_cache: dict[tuple[str, ClassStanding], dict[str, float]] = {}
        self.capacity: dict[str, int] = {}
        self.used: Counter[str] = Counter()

    # ---- weights

    def _eligible_sections(self, course: str, major: str, year: ClassStanding) -> list[str]:
        college = self.cfg.majors[major].college
        return [s for s in self.cat.courses[course].primaries if section_eligible(self.cat.sections[s], major, college, year)]

    def base_weights(self, major: str, year: ClassStanding) -> dict[str, float]:
        key = (major, year)
        if key in self._base_cache:
            return self._base_cache[key]
        mc = self.cfg.majors[major]
        yi = YEARS.index(year)
        out: dict[str, float] = {}
        for c in self.course_keys:
            if c in mc.courses:
                curriculum = mc.courses[c][yi]
            else:
                curriculum = sum(mult * self.cfg.tracks[t].get(c, [0.0] * 5)[yi] for t, mult in sorted(mc.tracks.items()))
            if curriculum <= 0:
                continue
            n_elig = len(self._eligible_sections(c, major, year))
            if n_elig == 0:
                continue
            level = self.cfg.level_suitability.get(self.cat.courses[c].level, [1.0] * 5)[yi]
            w = curriculum * level * n_elig**self.cfg.popularity_exponent
            if w > 0:
                out[c] = w
        self._base_cache[key] = out
        return out

    def _apply_pick(self, w: dict[str, float], pick: str) -> None:
        w.pop(pick, None)
        for other in sorted(self.exclusive.get(pick, ())):
            w.pop(other, None)
        for other in sorted(self.prereq_related.get(pick, ())):
            if other in w:
                w[other] *= self.cfg.prerequisite_same_term_factor
        for other, f in self.affinity.get(pick, ()):
            if other in w:
                w[other] *= f

    def _weighted_choice(self, w: dict[str, float]) -> str | None:
        keys = [k for k in sorted(w) if w[k] > 0]
        if not keys:
            return None
        return self.rng.choices(keys, weights=[w[k] for k in keys], k=1)[0]

    def current_weights(self, major: str, year: ClassStanding, chosen: list[str], banned: set[str]) -> dict[str, float]:
        w = dict(self.base_weights(major, year))
        for c in chosen:
            self._apply_pick(w, c)
        for b in sorted(banned):
            w.pop(b, None)
        return w

    def draw_profile(self, index: int) -> Profile:
        self.stats.profiles_drawn += 1
        majors = [p.major for p in self.pop]
        major = self.rng.choices(majors, weights=[p.headcount for p in self.pop], k=1)[0]
        mp = next(p for p in self.pop if p.major == major)
        year = self.rng.choices(YEARS, weights=mp.year_weights, k=1)[0]
        loads = sorted(self.cfg.course_load_distribution)
        load = self.rng.choices(loads, weights=[self.cfg.course_load_distribution[k] for k in loads], k=1)[0]
        prof = Profile(index, major, year, load)
        w = dict(self.base_weights(major, year))
        while len(prof.courses) < load:
            pick = self._weighted_choice(w)
            if pick is None:
                break
            prof.courses.append(pick)
            self._apply_pick(w, pick)
        return prof

    # ---- capacities

    def set_capacities(self, profiles: list[Profile]) -> None:
        demand = Counter(c for p in profiles for c in p.courses)
        slack = 1 + self.cfg.capacity_slack
        for key in self.course_keys:
            c = self.cat.courses[key]
            d = demand.get(key, 0)
            per_primary = math.ceil(d * slack / max(1, len(c.primaries))) + 1
            for s in c.primaries:
                self.capacity[s] = per_primary
                comps = c.linked_components.get(s, [])
                for comp in comps:
                    self.capacity[comp] = math.ceil(per_primary * slack / len(comps)) + 1
            for comp in c.free_components:
                self.capacity[comp] = math.ceil(d * slack / len(c.free_components)) + 1

    # ---- section assignment

    def _ordered(self, candidates: list[str]) -> list[str]:
        """Random order biased toward sections with more remaining capacity (Efraimidis–Spirakis keys)."""
        keyed = []
        for s in candidates:
            room = self.capacity[s] - self.used[s]
            u = self.rng.random()
            keyed.append((u ** (1.0 / room), s))
        return [s for _, s in sorted(keyed, reverse=True)]

    def _fits(self, sid: str, taken: list[MeetingTime]) -> bool:
        if self.used[sid] >= self.capacity[sid]:
            return False
        return not any(m.overlaps(t) for m in self.cat.sections[sid].meetings for t in taken)

    def _options(self, course: str, major: str, year: ClassStanding, taken: list[MeetingTime]) -> list[tuple[str, str | None]]:
        c = self.cat.courses[course]
        college = self.cfg.majors[major].college
        out = []
        for p in self._ordered([s for s in self._eligible_sections(course, major, year) if self._fits(s, taken)]):
            p_meet = taken + self.cat.sections[p].meetings
            comps = c.linked_components.get(p) or c.free_components
            if not comps:
                out.append((p, None))
                continue
            ok = [x for x in comps if section_eligible(self.cat.sections[x], major, college, year) and self._fits(x, p_meet)]
            if ok:
                out.append((p, self._ordered(ok)[0]))
        return out

    def assign(self, prof: Profile) -> tuple[dict[str, tuple[str, str | None]] | None, str | None]:
        """Backtracking search. Returns (course -> (primary, component)) or (None, blocking course)."""
        order = sorted(prof.courses, key=lambda c: (len(self._eligible_sections(c, prof.major, prof.year)), c))
        chosen: dict[str, tuple[str, str | None]] = {}
        fail_count: Counter[str] = Counter()
        budget = [4000]

        def rec(i: int, taken: list[MeetingTime]) -> bool:
            if i == len(order):
                return True
            course = order[i]
            for p, comp in self._options(course, prof.major, prof.year, taken):
                budget[0] -= 1
                self.stats.search_nodes += 1
                if budget[0] < 0:
                    return False
                extra = self.cat.sections[p].meetings + (self.cat.sections[comp].meetings if comp else [])
                chosen[course] = (p, comp)
                if rec(i + 1, taken + extra):
                    return True
                del chosen[course]
            fail_count[course] += 1
            return False

        if rec(0, []):
            return chosen, None
        blocker = max(sorted(fail_count), key=lambda c: (fail_count[c], -order.index(c))) if fail_count else order[-1]
        return None, blocker

    def realize(self, prof: Profile) -> dict[str, tuple[str, str | None]] | None:
        """Assign sections, replacing blocking courses as needed. Commits capacity usage on success."""
        drawn = len(prof.courses)
        banned: set[str] = set()
        for _ in range(self.cfg.max_replacement_attempts + 1):
            result, blocker = self.assign(prof)
            if result is not None:
                for p, comp in result.values():
                    self.used[p] += 1
                    if comp:
                        self.used[comp] += 1
                if len(prof.courses) < drawn:
                    self.stats.load_shortfall += 1
                return result
            assert blocker is not None
            prof.courses.remove(blocker)
            banned.add(blocker)
            pick = self._weighted_choice(self.current_weights(prof.major, prof.year, prof.courses, banned))
            if pick is not None and len(prof.courses) + 1 <= prof.load:
                prof.courses.append(pick)
                self.stats.course_replacements += 1
            else:
                self.stats.courses_dropped += 1
                if len(prof.courses) < self.cfg.min_courses_per_student:
                    break
        return None

    def run(self, n: int) -> list[Student]:
        profiles = [self.draw_profile(i) for i in range(n)]
        self.set_capacities(profiles)
        students: list[Student] = []
        for prof in profiles:
            for attempt in range(50):
                if len(prof.courses) >= self.cfg.min_courses_per_student:
                    result = self.realize(prof)
                    if result is not None:
                        break
                    self.stats.redraw_reasons["no_feasible_sections"] += 1
                else:
                    self.stats.redraw_reasons["too_few_eligible_courses"] += 1
                self.stats.student_redraws += 1
                prof = self.draw_profile(prof.index)
            else:
                raise RuntimeError(f"could not generate student {prof.index} after 50 redraws")
            section_ids = []
            for course in sorted(result):
                p, comp = result[course]
                section_ids.append(p)
                if comp:
                    section_ids.append(comp)
            students.append(
                Student(
                    student_id=f"S{prof.index + 1:06d}",
                    major=prof.major,
                    year=prof.year,
                    section_ids=section_ids,
                    provenance=Provenance.synthetic(
                        f"{GENERATOR_ID}@{GENERATOR_VERSION}",
                        notes=f"seed={self.cfg.seed}; anonymous synthetic student; not a real Georgia Tech record",
                    ),
                )
            )
        return students


# ------------------------------------------------------------------------- I/O


def canonical_sha(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def catalog_fingerprint(sections: list[Section], dataset_dir: Path) -> dict[str, str]:
    """Hashes of generator inputs, ignoring fields the generator itself rewrites (enrollment)."""
    secs = [s.model_dump(mode="json", exclude={"enrollment", "provenance"}) for s in sections]
    return {
        "sections": canonical_sha(secs),
        "section_metadata": canonical_sha(json.loads((dataset_dir / "section_metadata.json").read_text(encoding="utf-8"))),
        "course_metadata": canonical_sha(json.loads((dataset_dir / "course_metadata.json").read_text(encoding="utf-8"))),
    }


def generate(cfg: GenerationConfig, paths: DataPaths) -> tuple[list[Student], Generator, list[MajorPopulation]]:
    manifest = load_manifest(paths.sources_manifest)
    pop = build_population(cfg, manifest, paths.raw)
    gen = Generator(cfg, Catalog.load(paths), pop)
    return gen.run(cfg.num_students), gen, pop


def run(cfg: GenerationConfig, paths: DataPaths, log=print) -> list[Student]:
    students, gen, pop = generate(cfg, paths)
    sections = read_models(paths.dataset / "sections.json", Section)
    fingerprint = catalog_fingerprint(sections, paths.dataset)

    counts = Counter(sid for st in students for sid in st.section_ids)
    for s in sections:
        s.enrollment = counts.get(s.section_id, 0)
        s.provenance = s.provenance.model_copy(
            update={"field_sources": {**s.provenance.field_sources, "enrollment": DataSource.SYNTHETIC}}
        )
    write_models(paths.dataset / "sections.json", sections)
    write_models(paths.dataset / "students.json", students)

    st = gen.stats
    meta = {
        "generator": GENERATOR_ID,
        "generator_version": GENERATOR_VERSION,
        "term": cfg.term,
        "seed": cfg.seed,
        "num_students": cfg.num_students,
        "config_sha256": canonical_sha(cfg.model_dump(mode="json")),
        "input_fingerprint": fingerprint,
        "output_sha256": {
            "students": canonical_sha([s.model_dump(mode="json") for s in students]),
            "section_enrollment": canonical_sha({s.section_id: s.enrollment for s in sections}),
        },
        "data_categories": {
            "gt_public": "courses, sections (CRN, instructors, meeting times, credits, restrictions), exam periods, matrix exam groups, baseline schedule",
            "assumed": "curriculum/track weights, pair affinities, exclusive alternatives, course-load distribution, non-engineering major shares, synthetic section capacities",
            "synthetic": "every student record, every registration, Section.enrollment (count of synthetic registrations)",
        },
        "sample_note": (
            f"{cfg.num_students} synthetic students are a sample of a STEM sub-population, not Georgia Tech's full enrollment; "
            "section enrollment counts reflect this sample only and real section sizes are unknown."
        ),
        "population_model": population_summary(pop),
        "synthetic_section_capacities": {
            "source": "assumed",
            "rule": f"ceil(course demand x (1 + {cfg.capacity_slack}) / number of primary sections) + 1; components split their primary's capacity",
            "capacities": dict(sorted(gen.capacity.items())),
        },
        "stats": {
            "profiles_drawn": st.profiles_drawn,
            "student_redraws": st.student_redraws,
            "redraw_reasons": dict(st.redraw_reasons),
            "course_replacements": st.course_replacements,
            "courses_dropped": st.courses_dropped,
            "students_with_load_shortfall": st.load_shortfall,
            "search_nodes": st.search_nodes,
        },
        "config": cfg.model_dump(mode="json"),
    }
    paths.generation_metadata.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    log(
        f"generated {len(students)} students / {sum(len(s.section_ids) for s in students)} section registrations "
        f"(seed {cfg.seed}); redraws={st.student_redraws}, replacements={st.course_replacements}, dropped={st.courses_dropped}"
    )
    log(f"students sha256 {meta['output_sha256']['students']}")
    return students


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--num-students", type=int, default=None)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--config", type=Path, default=None)
    ap.add_argument("--data-dir", type=Path, default=None)
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    upd = {k: v for k, v in {"num_students": args.num_students, "seed": args.seed}.items() if v is not None}
    cfg = cfg.model_copy(update=upd) if upd else cfg
    cfg = GenerationConfig.model_validate(cfg.model_dump())
    paths = DataPaths(**({"root": args.data_dir} if args.data_dir else {}), dataset_name=cfg.dataset_name)
    run(cfg, paths)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
