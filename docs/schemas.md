# Hexam data schemas

Source of truth: `backend/hexam/schemas/` (Pydantic v2). Frontend mirror:
`frontend/src/types/hexam.ts` (kept in sync by `backend/tests/test_ts_sync.py`).

## Entities and relationships

```
Course 1──* Section ──0..1──> ExamGroup <──── ExamAssignment ────> ExamTimeslot
                ▲                                    │
Student ──*─────┘ (section_ids)                ExamSchedule (baseline | optimized | manual)
                                                     ▲
OptimizationConfig ──> baseline_schedule_id          │ optimized_schedule_id / baseline_schedule_id
OptimizationResult ──> config_id ────────────────────┘ + before/after ScheduleEvaluation
```

| Model | Key | Notes |
|---|---|---|
| `Course` | `course_id` = `SUBJ-NNNN` | Catalog entry, term-independent. |
| `Section` | `section_id` (e.g. `CS-1331-A`), unique `crn` | `exam_group_id` is the **only** section→exam link; `null` = no final. |
| `Student` | `student_id` = `S######` | Synthetic only (enforced). Holds `section_ids`; exam groups are derived. |
| `ExamGroup` | `exam_group_id` | `meeting_pattern` / `common` / `special`. Data-level `fixed`/`allowed`/`forbidden` slots. |
| `ExamTimeslot` | `timeslot_id` | Aware `start`/`end` in America/New_York; optional `max_students` / `max_exam_groups`. |
| `ExamSchedule` | `schedule_id` | List of `{exam_group_id, timeslot_id, locked}`; each group at most once. |
| `OptimizationConfig` | `config_id` | Integer `PenaltyWeights`, `HardConstraints`, `SolverSettings`, per-run `fixed_assignments`, `excluded_timeslot_ids`. |
| `OptimizationResult` | `run_id` | CP-SAT status, runtime, objective/bound, breakdown, before/after `ScheduleEvaluation`. |

`Dataset` bundles courses + sections + exam_groups + timeslots + students (one dataset per data directory).
`DatasetIndex` (`dataset.build_index()`) gives the solver/evaluator O(1) lookups,
`student_exam_groups`, `group_students`, chronological `timeslots_by_day`,
same-day `consecutive_pairs`, and evening→next-morning `overnight_pairs`.

## Time handling

* Exam instants: ISO 8601 with offset, normalized to America/New_York
  (`2026-12-07T08:00:00-05:00`; May exams serialize as `-04:00`). Naive input is rejected.
* Class meetings: naive campus wall-clock `HH:MM[:SS]` with OSCAR day codes (`M T W R F S U`).
* Frontend: use `formatCampusTime` / `campusDate` from `hexam.ts` so display is campus time regardless of viewer TZ.

## Student-impact metrics

Same keys in `PenaltyWeights`, `StudentImpactMetrics`, `StudentImpactDetail`, `objective_breakdown`:

| Key | Counted as |
|---|---|
| `simultaneous_conflicts` | per (student, slot) with k ≥ 2 exams: k − 1 |
| `back_to_back` | per consecutive same-day slot pair, both occupied |
| `night_to_morning` | per evening slot → first slot next calendar day, both occupied |
| `three_in_one_day` | per (student, day) with ≥ 3 exams |
| `three_in_24h` | once per student with 3 exam starts within < 24 h |
| `four_in_48h` | once per student with 4 exam starts within < 48 h |

Evening = slot starting at or after 17:00 (`EVENING_START_HOUR`).

## Provenance

Every course, section, exam group, timeslot, schedule and student carries
`provenance: {source: gt_public | synthetic | assumed, source_ref, retrieved_at, notes, field_sources}`.
`gt_public` requires `source_ref`. `field_sources` overrides per field
(e.g. a real section with `{"enrollment": "synthetic"}`).

## Validation

1. **Per record** (Pydantic, raises `ValidationError`): ID formats, `end > start`,
   same-day slots, naive/aware time rules, duplicate registrations/days/assignments,
   fixed slot vs. own allowed/forbidden lists, solver status ↔ schedule consistency.
2. **Cross record** (returns `ValidationReport`, never raises; `load_dataset(strict=True)` raises on errors):

| Function | Errors | Warnings |
|---|---|---|
| `validate_dataset` | `duplicate_id`, `duplicate_crn`, `duplicate_section_code`, `missing_reference`, `duplicate_course_registration`, `overlapping_timeslots`, `capacity_exceeded` (fixed groups) | `empty_exam_group`, `split_common_exam`, `forced_conflict`, `registration_time_conflict`, `enrollment_mismatch` |
| `validate_schedule` | `missing_reference`, `conflicting_assignment`, `restriction_violation`, `unassigned_exam_group` (optimized) | `unassigned_exam_group` (baseline/manual), `capacity_exceeded` |
| `validate_config` | `missing_reference`, `conflicting_assignment`, `restriction_violation`, `no_available_timeslots` | — |

## Storage

`data/<dataset>/` holds one JSON array file per collection: `courses`, `sections`, `exam_groups`, `timeslots`, `students`,
`schedules`, `optimization_configs`, `optimization_results`. See `hexam/storage.py`.

## Sample data (`data/sample/`)

Fall 2026 sample: 11 real GT course codes (titles marked `assumed` until verified
against the catalog), 18 synthetic sections, 12 exam groups (9 meeting-pattern,
3 common: one fixed, one evening-only, one no-evening), 19 assumed 2h50m slots
(Dec 7–11), 60 synthetic students, a baseline and optimized schedule, two configs,
and one result whose metrics are exact for this data (baseline penalty 7400 → 25).
