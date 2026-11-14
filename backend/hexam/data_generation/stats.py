"""Dataset statistics and suspicious-pattern diagnostics.

    python -m hexam.data_generation.stats [--json]

Also writes ``data/<dataset>/dataset_stats.json``. Descriptive only: no exam
schedule quality metrics (that is the evaluator's job).
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from itertools import combinations
from pathlib import Path
from typing import Any

from ..schemas import Dataset
from ..storage import load_dataset, load_schedules
from .config import YEARS, load_config
from .paths import DataPaths


def compute_stats(ds: Dataset, meta: dict[str, Any], gen_meta: dict[str, Any] | None, thresholds: dict[str, float]) -> dict[str, Any]:
    idx = ds.build_index()
    n = len(ds.students)
    primary = {sid for sid, m in meta.items() if m["primary"]}
    course_sets = [tuple(sorted({idx.sections[s].course_id for s in st.section_ids if s in primary})) for st in ds.students]
    per_student = Counter(len(cs) for cs in course_sets)
    course_counts = Counter(c for cs in course_sets for c in cs)
    pair_counts = Counter(p for cs in course_sets for p in combinations(cs, 2))
    group_sizes = {g: idx.group_size(g) for g in sorted(idx.exam_groups)}
    groups_per_student = Counter(len(g) for g in idx.student_exam_groups.values())

    caps = (gen_meta or {}).get("synthetic_section_capacities", {}).get("capacities", {})
    reg = Counter(s for st in ds.students for s in st.section_ids)
    util = {sid: reg.get(sid, 0) / caps[sid] for sid in sorted(caps) if caps[sid]}
    primaries_used = sum(1 for s in primary if reg.get(s, 0) > 0)

    def prov(items) -> dict[str, int]:
        return dict(sorted(Counter(i.provenance.source.value for i in items).items()))

    schedules = []
    flags: list[str] = []
    top_share = max(course_counts.values()) / n if n else 0
    if top_share > thresholds["max_course_share_warning"]:
        c, k = course_counts.most_common(1)[0]
        flags.append(f"course concentration: {c} taken by {k / n:.0%} of students")
    set_counts = Counter(course_sets)
    common_set, common_set_n = set_counts.most_common(1)[0] if set_counts else ((), 0)
    if n and common_set_n / n > thresholds["max_identical_schedule_share"]:
        flags.append(f"identical schedules: {common_set_n} students share {list(common_set)}")
    top5_share = sum(k for _, k in course_counts.most_common(5)) / max(1, sum(course_counts.values()))
    if top5_share > 0.5:
        flags.append(f"top-5 courses hold {top5_share:.0%} of all course registrations")
    never = sorted(set(idx.courses) - set(course_counts))
    if never:
        flags.append(f"{len(never)} subset courses selected by no student: {never}")
    # Mean pairwise Jaccard similarity of course sets (exact over all pairs is cheap at n=1000)
    sets = [set(cs) for cs in course_sets]
    jac_sum, pairs = 0.0, 0
    step = max(1, len(sets) // 400)  # subsample for large cohorts
    sample = sets[::step]
    for a, b in combinations(sample, 2):
        jac_sum += len(a & b) / len(a | b)
        pairs += 1
    mean_jaccard = jac_sum / pairs if pairs else 0.0
    if mean_jaccard > 0.3:
        flags.append(f"schedules are very similar (mean Jaccard {mean_jaccard:.2f})")

    return {
        "students": n,
        "section_registrations": sum(len(st.section_ids) for st in ds.students),
        "course_registrations": sum(len(cs) for cs in course_sets),
        "avg_courses_per_student": round(sum(len(cs) for cs in course_sets) / n, 3) if n else 0,
        "courses_per_student": dict(sorted(per_student.items())),
        "distinct_course_sets": len(set_counts),
        "most_common_course_set": {"courses": list(common_set), "students": common_set_n},
        "mean_pairwise_jaccard": round(mean_jaccard, 4),
        "by_major": dict(sorted(Counter(st.major for st in ds.students).items())),
        "by_year": {y.value: sum(st.year == y for st in ds.students) for y in YEARS},
        "by_major_year": {m: {y.value: sum(st.major == m and st.year == y for st in ds.students) for y in YEARS} for m in sorted({st.major for st in ds.students})},
        "top_courses": [{"course": c, "students": k, "share": round(k / n, 4)} for c, k in course_counts.most_common(20)],
        "courses_selected": len(course_counts),
        "top_course_pairs": [{"pair": list(p), "students": k} for p, k in pair_counts.most_common(20)],
        "exam_groups": len(idx.exam_groups),
        "exam_group_enrollment": group_sizes,
        "exam_groups_per_student": dict(sorted(groups_per_student.items())),
        "section_utilization": {
            "primary_sections_used": primaries_used,
            "primary_sections_total": len(primary),
            "sections_at_synthetic_capacity": sum(1 for u in util.values() if u >= 1.0),
            "max_utilization": round(max(util.values()), 3) if util else None,
            "mean_utilization_of_used_sections": round(sum(u for u in util.values() if u > 0) / max(1, sum(1 for u in util.values() if u > 0)), 3),
        },
        "provenance": {
            "course": prov(ds.courses),
            "section": prov(ds.sections),
            "section_enrollment_field": dict(Counter(s.provenance.source_for("enrollment").value for s in ds.sections)),
            "exam_group": prov(ds.exam_groups),
            "timeslot": prov(ds.timeslots),
            "student": prov(ds.students),
            "schedule": schedules,
        },
        "flags": flags,
    }


def run(paths: DataPaths, *, as_json: bool = False, log=print) -> dict[str, Any]:
    cfg = load_config()
    ds = load_dataset(paths.dataset, strict=False)
    meta = json.loads(paths.section_metadata.read_text(encoding="utf-8"))
    gen_meta = json.loads(paths.generation_metadata.read_text(encoding="utf-8")) if paths.generation_metadata.exists() else None
    stats = compute_stats(ds, meta, gen_meta, {"max_course_share_warning": cfg.max_course_share_warning, "max_identical_schedule_share": cfg.max_identical_schedule_share})
    stats["provenance"]["schedule"] = dict(Counter(s.provenance.source.value for s in load_schedules(paths.dataset)))
    (paths.dataset / "dataset_stats.json").write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")
    if as_json:
        log(json.dumps(stats, indent=2))
    else:
        log(format_stats(stats))
    return stats


def format_stats(s: dict[str, Any]) -> str:
    L = []
    L.append(f"Students: {s['students']}   section registrations: {s['section_registrations']}   course registrations: {s['course_registrations']}")
    L.append(f"Avg courses/student: {s['avg_courses_per_student']}   distribution: {s['courses_per_student']}")
    L.append(f"Distinct course sets: {s['distinct_course_sets']}   mean pairwise Jaccard: {s['mean_pairwise_jaccard']}")
    L.append(f"By major: {s['by_major']}")
    L.append(f"By year:  {s['by_year']}")
    L.append("Top courses:")
    L += [f"  {t['course']:<11} {t['students']:>4}  ({t['share']:.1%})" for t in s["top_courses"][:12]]
    L.append("Top co-enrolled pairs:")
    L += [f"  {p['pair'][0]:<11} + {p['pair'][1]:<11} {p['students']:>4}" for p in s["top_course_pairs"][:12]]
    L.append(f"Exam groups: {s['exam_groups']}   exam groups per student: {s['exam_groups_per_student']}")
    sizes = sorted(s["exam_group_enrollment"].items(), key=lambda kv: -kv[1])
    L.append("Exam-group enrollment: " + ", ".join(f"{g}={k}" for g, k in sizes))
    u = s["section_utilization"]
    L.append(
        f"Sections: {u['primary_sections_used']}/{u['primary_sections_total']} primary sections used; "
        f"{u['sections_at_synthetic_capacity']} at synthetic capacity; max util {u['max_utilization']}, mean (used) {u['mean_utilization_of_used_sections']}"
    )
    L.append(f"Provenance: {json.dumps(s['provenance'])}")
    L.append("Flags: " + ("; ".join(s["flags"]) if s["flags"] else "none"))
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--data-dir", type=Path, default=None)
    args = ap.parse_args(argv)
    paths = DataPaths(**({"root": args.data_dir} if args.data_dir else {}), dataset_name=load_config().dataset_name)
    run(paths, as_json=args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
