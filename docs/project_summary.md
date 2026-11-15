# Hexam — granular project summary

*Written 2026-11-14 from the working tree on branch `improve-algorithm`, which has uncommitted changes, and updated the same day after the loose ends in section 15 were fixed. Sources: every Markdown file plus the backend, frontend, scripts and data files.*

---

## 1. What Hexam is

Hexam is a hackathon proof of concept that **re-assigns Georgia Tech Fall 2026 final-exam groups to the 21 official exam periods**. It uses Google **OR-Tools CP-SAT** to minimize student exam-week congestion. Every result is scored by an **independent pure-Python evaluator** and compared against the registrar's **official final-exam matrix**, which serves as the baseline.

- **Real public data:** courses, sections, CRNs, meeting times, restrictions, the 21 exam periods, the 5 common exams, and IRP enrollment statistics.
- **Synthetic data:** all **1,000 students** and every registration (seed 42).
- **Stack:** Python ≥ 3.11 (Pydantic v2, FastAPI, OR-Tools, uvicorn) and React 18 + TypeScript + Tailwind + Recharts (Vite). There is no database, no auth and no API keys.
- **Inspiration:** Bucknell paper [arXiv 2509.11031](https://arxiv.org/html/2509.11031v1), which covers exam-group timeslot optimization, meeting-time "course groups" and the two-phase heuristic.
- **Disclaimer (shown in the UI and in the API):** synthetic students only; not an official GT system.

---

## 2. Repository layout

```
.claude/launch.json            preview configs: hexam-api (uvicorn :8000), hexam-web (vite :5173)
.gitattributes                 data/raw/** -text  (stops CRLF rewriting from breaking SHA-256 checks)
.gitignore                     venv, node_modules, dist, data/raw/irp_factbook_2025.pdf, *.tsbuildinfo
README.md                      product overview, quick start, metrics, model, API, troubleshooting
handoff.md                     progress log / status / caveats (per user memory: kept for long tasks)
docs/schemas.md                schema reference (entities, time handling, metrics, provenance, validation)
docs/data_pipeline.md          real-data ingestion + synthetic generation reference
backend/
  pyproject.toml               package "hexam" 1.0.0 (= hexam.__version__, used by the API); extras: data=[pypdf], dev=[pytest,pypdf,httpx]
  requirements.txt             ortools, fastapi, uvicorn[standard], pydantic, tzdata, reportlab
  hexam/
    schemas/       (11 files)  Pydantic models — the single contract
    data_generation/ (13 + 3 adapters + config JSON)  real-data ETL + synthetic students
    evaluation/    (rules.py 135 lines, evaluator.py 311 lines)  independent scorer
    optimization/  (problem.py 190 lines, solver.py 461 lines)   CP-SAT model
    services/      dataset, optimization runs, integrity, comparison, student views, PDF report
    api/           main.py (app factory) + routes.py (thin handlers)
    storage.py     JSON-array-per-collection file storage
  tests/           11 test modules (286 tests pass, counting parametrized cases)
frontend/          Vite app: App.tsx shell, 5 pages, 3 component files, hook/context, api client, types mirror
data/
  raw/             cached public downloads (crawler JSON, matrix PDF, registrar + IRP HTML, Fact Book extract)
  provenance/sources.json   manifest: URL, status, HTTP code, retrieved_at, sha256, bytes
  fall2026/        the real dataset + side files + precomputed_run.json
  sample/          tiny hand-built fixture dataset used by schema tests
scripts/           dev.ps1, dev.sh (start both servers), precompute_demo.py
```

The largest files are the data files: `sections.json` (25k lines), `section_metadata.json` (21k), `students.json` (20k), `course_metadata.json` (4k), `generation_metadata.json` (2.4k) and `precomputed_run.json` (2.2k). The largest code files are `solver.py` (461), `normalize.py` (537), `generate.py` (526) and `frontend/src/types/hexam.ts` (655).

---

## 3. Current state (git)

- The branch is `improve-algorithm`, based on `main` after merging PR #3 ("Built algorithm and dashboard"). The earlier PRs were #1 (schemas) and #2 (synthetic data).
- The working tree has uncommitted changes that do four things:
  - It removes the separate **3-exams-in-one-day** metric, which three-in-24 h already covers. The merged `three_in_24h` takes its weight of 100.
  - It keeps the **late-evening → early-morning** pair metric (`night_to_morning`). Separate per-exam late and early metrics were tried and reverted.
  - It adds a **PDF comparison report** (`GET /api/runs/{id}/report.pdf`, built with ReportLab) and a **Download PDF report** button.
  - It trims the dashboard: the Overview's day-load and period-concentration charts, the How it works tab and the synthetic-data banner are gone, and schedule cells say "students" instead of "seats".
  - It also regenerates `precomputed_run.json`, re-scores the sample results, and updates tests and docs.

---

## 4. Data model — `backend/hexam/schemas/`

All models extend `HexamModel`: `extra="forbid"`, strings are trimmed, assignments are validated. Records that carry provenance extend `ProvenancedModel`. Each such subclass must declare `provenance` as its last field, and `field_sources` keys must name real fields. `schemas/__init__.py` re-exports everything, and `__all__` is built dynamically.

### 4.1 Primitives (`base.py`)

| Item | Rule |
|---|---|
| `SCHEMA_VERSION` | `"1.0"` |
| `CAMPUS_TZ` | `America/New_York` (zoneinfo; `tzdata` dependency for Windows) |
| `EntityId` | `^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,63}$`, used for section, exam-group, timeslot, schedule, config and run IDs |
| `CourseId` | `^[A-Z]{2,4}-\d{4}[A-Z]?$`, e.g. `CS-1331`, `CHEM-1211K` |
| `StudentId` | `^S\d{6}$` (never a real GTID) |
| `Crn` | 5 digits |
| `MajorCode` | `^[A-Z]{2,6}$` |
| `CampusDateTime` | aware datetime, converted to New York; **naive input is rejected** |
| `DataSource` | `gt_public` \| `synthetic` \| `assumed` |
| `Provenance` | `source`, `source_ref`, `retrieved_at`, `notes`, `field_sources`; `gt_public` **requires** `source_ref`; helpers `.synthetic()` and `.assumed()` |
| `find_duplicates` | returns repeated values in first-seen order (used by many validators) |

### 4.2 Entities

| Model | File | Key fields and validators |
|---|---|---|
| `Weekday` | courses.py | OSCAR codes M T W R F S U (R = Thursday) |
| `SectionType` | courses.py | lecture, lab, recitation, studio, other |
| `MeetingTime` | courses.py | `days` (≥1, unique), naive `start_time`/`end_time` (end > start), `location`; `.overlaps()`; `.pattern_key` → `"MWF 09:30"` |
| `Course` | courses.py | `course_id` must equal `f"{subject}-{number}"`; `title`; `credit_hours` 0–12 |
| `Section` | courses.py | `section_id` (`CS-1331-A`), `section_code` `[A-Z0-9]{1,4}`, `crn`, type, instructors (unique), `enrollment ≥ 0`, `capacity` (null = unknown), `meeting_times` (no self-overlap), **`exam_group_id`**: the *only* section→exam link; `None` = no final |
| `ClassStanding` | students.py | first_year … fifth_year_plus, graduate |
| `Student` | students.py | `student_id`, `major`, `year`, `section_ids` (≥1, unique); **provenance must be synthetic**; exam groups are derived, never stored |
| `ExamTimeslot` | timeslots.py | aware `start`/`end`, same calendar day, end > start; `label`; optional `max_students` / `max_exam_groups`; `.is_evening` (≥ 17:00, `EVENING_START_HOUR`), `.duration_minutes`, `.overlaps()` |
| `ExamGroupKind` | exam_groups.py | meeting_pattern, common, special |
| `ExamGroup` | exam_groups.py | `name`, `kind`, `meeting_pattern` (required for meeting_pattern kind), `fixed_timeslot_id`, `allowed_timeslot_ids` (None = any, never empty), `forbidden_timeslot_ids`; allowed and forbidden must not overlap; the fixed slot must pass `.permits()` |
| `ScheduleKind` | schedules.py | baseline, optimized, manual |
| `ExamAssignment` | schedules.py | `exam_group_id`, `timeslot_id`, `locked` |
| `ExamSchedule` | schedules.py | each group at most once; optimized ⇒ `source_run_id` required; `.as_mapping()` → `{group: slot}` |

### 4.3 Metrics (`metrics.py`) — the shared contract between evaluator, solver and UI

`ImpactMetricKey` has 5 keys:

| Key | Unit counted | Per-student detail |
|---|---|---|
| `simultaneous_conflicts` | exam **pairs** with overlapping intervals (`a.start < b.end and b.start < a.end`; touching intervals do not count) | pairs |
| `back_to_back` | non-overlapping exam **pairs** with `0 ≤ gap ≤ back_to_back_max_gap_minutes` (30) | pairs |
| `night_to_morning` | exam **pairs**: prev starts ≥ `evening_start` (17:00), next starts < `morning_end` (10:00) on a later day, break ≤ `overnight_max_gap_hours` (14) | pairs |
| `three_in_24h` | **students** with 3 starts inside a window < 24 h (covers 3 in one day) | 0/1 |
| `four_in_48h` | **students** with 4 starts inside a window < 48 h | 0/1 |

Supporting models:

- `EvaluationSettings`: `back_to_back_max_gap_minutes` (default 30, range 0–720), `evening_start` (17:00), `morning_end` (10:00), `overnight_max_gap_hours` (14).
- `MetricValue`: `count`, `students_affected`. Affected students can't exceed the count, and a non-zero count needs at least one affected student.
- `StudentImpactMetrics`: totals plus `students_with_any_issue`, with consistency bounds.
- `StudentImpactDetail`: per-student counts, `exam_timeslot_ids`, `exam_group_ids`, `weighted_penalty`.
- `TimeslotLoad`, `DayLoad`, `EvaluationDiagnostics`: unassigned groups, pattern counts, `max_exams_one_day`, exam-count histogram.
- `ScheduleEvaluation`: the evaluator output. It holds metrics, `weighted_penalty`, `penalty_breakdown`, `weights_used`, settings, `valid` and `issues`, timeslot and day loads, the worst 25 students and diagnostics.
- `MetricDelta` and `compare_metrics()`: before/after/change/percent, where percent is None if before = 0.

### 4.4 Optimization config and result (`optimization.py`)

- **`PenaltyWeights`** are integers, as CP-SAT requires. Each is limited to 0–1,000,000. Defaults: conflicts **1000**, three_in_24h **100**, back_to_back **30**, four_in_48h **15**, night_to_morning **10**. `.score(metrics)` = Σ weight × count.
- **`HardConstraints`**:

  | Field | Default | Meaning |
  |---|---|---|
  | `forbid_simultaneous_conflicts` | True | strict mode |
  | `max_exams_per_student_per_slot` | 2 | relaxed mode only |
  | `max_exams_per_student_per_day` | None | optional hard cap |
  | `enforce_timeslot_capacity` | True | honor the caps below |
  | `max_students_per_slot` | None | aggregate student cap |
  | `max_exam_groups_per_slot` | None | aggregate group cap |
  | `enforce_group_restrictions` | True | apply allowed/forbidden lists |
  | `allow_moving_fixed_exams` | False | HYPOTHETICAL mode |

- **`OptimizationStrategy`**: `single_pass` (default) or `two_phase`.
- **`SolverSettings`**: time limit 60 s (≤ 3600), 8 workers (1–64), seed 0, optional relative gap, `use_baseline_hint=True`, `log_search_progress`, strategy, `two_phase_large_groups=18`.
- **`OptimizationConfig`**: id, name, baseline id, weights, hard constraints, solver, evaluation, `fixed_assignments` (user locks; no group twice), `excluded_timeslot_ids` (unique; must not hold a lock).
- **`SolverStatus`**: OPTIMAL, FEASIBLE, INFEASIBLE, MODEL_INVALID, UNKNOWN, ERROR; `.has_solution`.
- **`OptimizationResult`**: status, timestamps, runtime, objective, bound, breakdown, before/after evaluations, message, `relaxed`, `hypothetical`, strategy, warnings, `infeasibility_hints`, `model_stats`. Validators: a solution needs a schedule id, `after` and an objective; a non-solution must not carry a schedule; ids must match. It also has a `.relative_gap` property.

### 4.5 Dataset, index and cross-record validation (`dataset.py`, `validation.py`)

- `Dataset` = courses + sections + exam_groups + timeslots + students.
- **`DatasetIndex`** is a frozen dataclass with O(1) lookups: dicts by id, `sections_by_course`, `sections_by_group`, `student_exam_groups` (sorted tuple of *distinct* groups per student), `group_students` (frozensets) and `timeslots_by_day` (chronological). `group_size()` is a helper. Time relations between slots live in `SlotRelations` (§6.1), not in the index.
- **`ValidationReport`** collects `ValidationIssue(severity, code, message, entity_type, entity_id)`. It has computed `valid`, `error_count` and `warning_count`, and `raise_if_invalid()` raises `DatasetValidationError`, which previews up to 20 errors.
- `validate_dataset` reports:
  - **Errors:** `duplicate_id`, `duplicate_crn`, `duplicate_section_code`, `missing_reference`, `duplicate_course_registration`, `overlapping_timeslots`, `capacity_exceeded` (too many fixed groups in one slot).
  - **Warnings:** `empty_exam_group`, `split_common_exam`, `forced_conflict`, `registration_time_conflict`, `enrollment_mismatch`.
- `validate_schedule`: missing references, `conflicting_assignment` (fixed group moved), `restriction_violation`, and `unassigned_exam_group` (an error for optimized schedules, a warning otherwise). Capacity problems are warnings.
- `validate_config`: unknown excluded slots, all slots excluded, locks that conflict with fixed slots or restrictions, groups left with no permitted slot, unknown baseline id.

### 4.6 API envelopes (`api.py`, `api_views.py`)

- **Request models:**
  - `OptimizeRequest`: defaults dataset `fall2026`, time limit **20 s** (≤ 300), 8 workers (≤ 32), `wait=False`.
  - `EvaluateScheduleRequest`: exactly one of `schedule_id` or `assignments`.
  - `CompareRequest`: `after_assignments` map, weights and settings.
  - `RunOptimizationRequest`: legacy; exactly one of `config_id` or `config`.
- **Response and view models:**
  - `DatasetInfo`: counts, term, baseline info, `dataset_version` hash, seed, provenance breakdown, data categories, synthetic disclosure, exam days.
  - `ExamGroupView`, `StudentSummary`/`StudentPage`, `StudentSectionView`, `StudentDetailView` (`synthetic=True`).
  - `StudentExamView` (with `fixed` and `moved` flags), `StudentScheduleImpact` (exams + detail + human-readable findings), `ImpactVerdict`, `StudentImpactResponse`, `StudentRankItem`.
  - `ScheduleComparison`: count and affected deltas, penalties, improved/worsened/unchanged students, groups moved, `metrics_worsened`.
  - `IntegrityCheck` and `IntegrityReport`.
  - `RunState` (queued, running, completed, failed), `OptimizationRun` (config, result, schedule, comparison, integrity, error, `precomputed`, `label`), `RunSummary`, `EvaluateResponse`, `HealthResponse`, `DatasetSummary`.

### 4.7 Generation config schema (`generation.py`)

`MajorProfile` and `SyntheticGenerationConfig` are an older, generic generator config: a year distribution of 27/25/23/20/5 %, 3–6 sections and a conflict-avoidance flag. The real pipeline uses its own `data_generation/config.py`, described next.

---

## 5. Data pipeline — `backend/hexam/data_generation/`

Each step is a CLI module run from `backend/`. `build.py` runs all of them offline by default; `--fetch` hits the network.

| Step | Module | Network | Output |
|---|---|---|---|
| Fetch and cache | `fetch_sources --term 202608 [--force] [--offline]` | yes | `data/raw/*`, `data/provenance/sources.json` |
| Normalize | `normalize [--granularity meeting_time\|meeting_pattern\|course]` | no | courses, sections, exam groups, timeslots, schedules, metadata, report |
| Generate | `generate --num-students 1000 --seed 42` | no | students.json, section enrollment, generation_metadata.json |
| Validate | `validate [--skip-reproducibility]` | no | pass/fail report |
| Stats | `stats [--json]` | no | dataset_stats.json |

### 5.1 Configuration (`config.py` + `config/fall2026.json`)

- `GenerationConfig`:
  - Basics: term `202608`, dataset `fall2026`, granularity `meeting_time`, `num_students` 1000, seed 42.
  - Exclusion and course load: `reserved_restriction_categories` (Cohorts, Programs, Degrees, Special Approvals, Concentrations, Minors, Attributes, Departments), `min_exam_credit_hours` 2.0, course load {3: .12, 4: .40, 5: .38, 6: .10}.
  - Weighting: `level_suitability` per course level × year, `popularity_exponent` 0.5, `prerequisite_same_term_factor` 0.05.
  - Section assignment: `capacity_slack` 0.15, `max_replacement_attempts` 25, `min_courses_per_student` 3.
  - Warning thresholds: course share 45 %, identical schedule share 5 %.
  - The validator checks that every referenced course key exists in the subset.
- The JSON file holds:
  - **64 courses**: MATH ×11, PHYS ×3, CHEM ×2, BIOS, CS ×13, ECE ×6, ME ×7, COE ×2, ISYE ×6, AE ×4, BMED, CHBE, CEE, MSE, ECON ×2, PSYC, HIST, APPH.
  - **12 majors**: CS, CMPE, EE, ME, AE, IE, BMED, CHBE, CE, MSE, MATH, PHYS.
  - **7 tracks**: calculus, physics, chemistry_engr, chemistry_sci, computing_engr, engr_mechanics, gen_ed.
  - **4 exclusive groups**: MATH 1553/1554; CS 1301/1371; CHEM 1310/1211K; ISYE 3770 / MATH 3670 / MATH 3215 / ECE 3077.
  - **23 pair affinities**, for example MATH 1552 + PHYS 2211 ×2.0 and CS 1332 + CS 2050 ×2.0.
- Headcount sources: 9 engineering majors come from IRP (`irp_coe`). CS, MATH and PHYS come from the Fact Book college total × an assumed share (CS 0.85 of Computing; MATH 0.12 and PHYS 0.08 of Sciences).

### 5.2 Sources (`sources.py`, `fetch_sources.py`)

- The `SourceSpec` registry has 11 sources: GT Scheduler crawler-v2 `index.json` and `202608.json`, the registrar exam guidelines, the registrar matrix landing page, the IRP COE enrollment dashboard, the IRP 2025 Fact Book PDF (git-ignored, with a JSON extract committed), OSCAR and the schedule-of-classes page (probe only), catalog CS courses and the CS BS page (both failed: timeouts), and the official **Final Exam Matrix 202608 PDF**.
- Each source has a content validator (required JSON keys, `%PDF-` header, required HTML strings).
- `fetch_source` statuses: `fetched`, `cached`, `probe_ok`, `failed`. A failed download falls back to a validated cached copy and never produces data.
- `verify_cache` re-hashes every cached file against the manifest.
- `resolve_term` substitutes the most recent available Fall term if the requested term is missing.
- The User-Agent is generic and the timeout is 45 s.

### 5.3 Adapters

- **`gt_scheduler.py`** decodes the crawler's positional tuples (course, section, meeting and restriction indices into `caches`), parses `"HHMM - HHMM"` or `TBA` periods and extracts prerequisite course ids from the prerequisite tree. The crawler has no capacity or enrollment fields.
- **`exam_matrix.py`** works around plain-text extraction scrambling the two-column PDF. It pulls every text fragment *with coordinates* via a pypdf visitor, pairs merged date cells with their time ranges, and assigns each class-meeting row to the nearest date cell at or above it in the same column (column split at x = 300 pt). It also splits fused left/right rows and parses "Common Exams" and "Reading/Conflict Period" rows.
  - Validation: weekday names match dates, all **129 rows** are assigned, there is one exam length (2 h 50 m), and no exam falls in a reading period.
  - `lookup(days, start, end)` tries an exact match first, then days + start when that is unambiguous.
  - `end_sensitive_patterns`: `WF 8:25–9:15` → Fri Dec 11, but `WF 8:25–10:20` → Wed Dec 16.
- **`irp.py`** has an HTML table parser for COE enrollment by major × year (1–5) and a Fact Book PDF parser for undergraduate totals by college.

### 5.4 Normalization rules (`normalize.py`)

1. A section is **kept** if it is on the Atlanta campus, runs the full term (the most common date range), has a scheduled meeting, is not graduate-only and is not reserved. Exclusion reasons are recorded.
2. **Primary** sections carry credit. 0-credit sections are **components** linked by code prefix (`A01`→`A`) or are free (PHYS `L01`). Credit, not OSCAR's schedule type, decides; MATH 1551's primary is labeled "Studio".
3. A section is **exam-bearing** if its course has an official common exam, or it is a Lecture of ≥ 2 credits. Its period is the matrix row for its first mapped meeting. Sections with non-standard patterns are excluded, not guessed (2 sections). 5 sections used the days + start fallback.
4. ECE 3710's common exam applies only to online sections, so Atlanta sections use the matrix instead, with a warning.
5. `capacity` stays null. `enrollment` starts at 0 with provenance `assumed`; `generate` later sets it to the synthetic count and relabels it `synthetic`.
6. **Exam-group granularity:**

   | Mode | Group per | Example id | Groups |
   |---|---|---|---|
   | `meeting_time` (default) | days + start + end | `EG-MWF-0930-1020` | **52** (47 + 5 common) |
   | `meeting_pattern` | days + start | `EG-MWF-0930` | 38 |
   | `course` | course + time | `EG-CS-1331-MWF-0930-1020` | 256 |

7. **Common exams** become `EG-COMMON-…` groups (`kind=common`) with `fixed_timeslot_id` set: MATH 1551, MATH 1552, MATH 1553/1554, PHYS 2211, PHYS 2212.
8. **Timeslots** are the 21 official periods, with ids like `TS-YYYYMMDD-HHMM` and labels like "Thu Dec 10, 2:40 PM–5:30 PM". The 3 reading periods and 2 conflict periods (Dec 18) are kept only in `exam_matrix.json` under `special_periods`.
9. The **baseline** schedule `baseline-gt-matrix-202608` places every group in its matrix period; common exams are `locked`.
10. Outputs: courses, sections, exam_groups, timeslots, schedules, `exam_matrix.json`, `course_metadata.json` (prerequisites, roles, sections, exclusions), `section_metadata.json` (CRN, primary/component, exam mapping, restrictions) and `normalization_report.json` (counts, methods, ambiguous meeting times — none in Fall 2026 — exclusions, warnings). `students.json` is reset to `[]`.

### 5.5 Synthetic students (`population.py`, `generate.py`)

1. **Population:** each major's headcount comes from IRP (gt_public) or from the Fact Book × share (assumed). The year mix uses the major's IRP year distribution, or the engineering average for non-IRP majors. The academic year is derived from the term (202608 → 2026–2027).
2. **Profile:** major ∝ headcount, year ∝ the major's year weights, load k ∈ {3, 4, 5, 6}.
3. **Course weight** = curriculum(major, year) × level suitability × (number of eligible real sections)^0.5. It is zero if no section's OSCAR restrictions (Majors, Classes, Colleges, Levels) admit the student. Unmodeled restriction categories count as ineligible, to be conservative.
4. **Sequential weighted draw without replacement.** After each pick, the generator removes exclusive alternatives, multiplies prerequisite-linked courses by 0.05 and multiplies affine courses by their factor.
5. **Synthetic capacities** = `ceil(demand × 1.15 / #primaries) + 1`; components split their primary's capacity.
6. **Section assignment** is a backtracking search, most-constrained course first, with a budget of 4,000 nodes. It needs an eligible primary with room and no meeting overlap, plus one eligible component if the course has any. Order is randomized by Efraimidis–Spirakis keys weighted by remaining capacity.
7. **Repair:** the blocking course is replaced by a fresh draw (≤ 25 attempts). Otherwise the student is redrawn (≤ 50 times); seed 42 needed none.
8. **Determinism:** one `random.Random(seed)` and sorted iteration everywhere. Input and output SHA-256 hashes are stored in `generation_metadata.json`.
9. Students get ids `S000001`… and provenance `synthetic` with the source `hexam.data_generation.generate@1.0`.

### 5.6 Validation and statistics

- `validate.run_checks` re-runs schema validation and checks:
  - the cache hashes;
  - provenance separation (catalog records are gt_public, students synthetic, enrollment synthetic, capacity null);
  - matrix fidelity (timeslots equal the official periods, the baseline slot equals the matrix period, common exams are fixed);
  - student rules (capacity, enrollment equal to registrations, OSCAR restrictions, load 3–6, exactly one primary, exactly one matching component, no meeting overlap);
  - **reproducibility**: regenerating gives byte-identical output.
- `registration_time_conflict` is promoted from a warning to an error for generated data.
- `stats.compute_stats`: course and pair counts, Jaccard similarity, group sizes, utilization, provenance, and concentration flags.

### 5.7 Dataset at seed 42 (`docs/data_pipeline.md`, `dataset_stats.json`)

- **Students and registrations:** 1,000 students; 4,432 course registrations (6,326 including labs and studios); 4.43 courses per student (3: 131, 4: 397, 5: 381, 6: 91).
- **Majors:** CS 242, ME 181, IE 116, CMPE 86, AE 85, BMED 74, EE 71, CHBE 54, CE 41, MATH 23, MSE 19, PHYS 8.
- **Years 1–5+:** 265 / 309 / 220 / 146 / 60.
- **Course sets:** 919 distinct sets; the largest identical set is shared by 5 students; mean Jaccard 0.08; the top course is MATH 2551 (28.5 %).
- **Exam groups:** the largest are EG-TR-1230-1345 (306), EG-TR-1530-1645 (293), EG-TR-1400-1515 (287), PHYS 2212 common (255) and MATH 1553/1554 common (253). EG-MW-1830-1945 has no students.
- **Sections:** 327 of 337 primary sections used; 50 are at their synthetic capacity.
- **Provenance:** courses (64), sections (756), exam groups (52), timeslots (21) and the baseline are `gt_public`; students and enrollment counts are `synthetic`.

**Exam periods (21):**

| Day | Periods |
|---|---|
| Thu Dec 10 | 14:40, 18:00 |
| Fri Dec 11 | 08:00, 11:20, 14:40, 18:00 |
| Mon Dec 14 | 08:00, 11:20, 14:40, 18:00 |
| Tue Dec 15 | 11:20, 14:40, 18:00 |
| Wed Dec 16 | 08:00, 11:20, 14:40, 18:00 |
| Thu Dec 17 | 08:00, 11:20, 14:40, 18:00 |

Every period lasts 2 h 50 m with 30-minute gaps. This means **back-to-back = consecutive same-day periods** (the 30-minute gap is ≤ the 30-minute threshold), and **late → early = an 18:00 period followed by the next day's 08:00 period**.

---

## 6. Independent evaluator — `backend/hexam/evaluation/`

### 6.1 `rules.py` — the only definitions of the time predicates

- `campus()` rejects naive datetimes. `campus_date()` is the day an exam *starts*.
- `overlaps()` uses half-open intervals. `is_back_to_back()` requires `0 ≤ next.start − prev.end ≤ gap`.
- `is_late_to_early()`: prev starts ≥ 17:00, next starts < 10:00 on a later campus day, break ≤ 14 h.
- `count_windows(starts, k, window)` returns (any k starts within < window, number of distinct k-subsets inside the window).
- **`SlotRelations.build()`** precomputes the same rules over pairs of slots for the solver: `overlap[t]` (including t itself), ordered `b2b` and `late_early` pairs, `days`, and `windows24` / `windows48`. A window holds the slots starting in `[t.start, t.start + W)`, and windows that are strict subsets of another are dropped. Sharing these definitions is what keeps the solver objective and the evaluator in agreement.

### 6.2 `evaluator.py`

- `Evaluator(index, settings)` keeps students sorted. `evaluate(schedule | mapping, weights, worst_n=25, require_complete=True)` returns an `EvaluationOutcome(evaluation, students, findings, exams)`.
- For each student, it collects distinct exam groups, so two sections in one common exam count once. It sorts exams by start time and runs `_score_student`:
  - an O(n²) pass over pairs that counts conflicts and otherwise back-to-back;
  - late→early pairs;
  - the 3-in-24 h and 4-in-48 h windows.
- Aggregates: totals, students affected, `students_with_any_issue`, timeslot loads (with over-capacity flags), day loads, the worst 25 students, the penalty breakdown and diagnostics.
- `_validate` reports unknown groups or slots and restriction violations as errors. A moved fixed exam is only a **warning** (`fixed_exam_moved`), so hypothetical runs can still be scored. An unassigned group is an error when the schedule must be complete and the group has students.
- `StudentFindings` keeps the concrete pairs and exams behind each count so the UI can explain them. The evaluator is fully deterministic.

---

## 7. CP-SAT optimizer — `backend/hexam/optimization/`

### 7.1 `problem.py` — precomputation

`Problem.build(index, config)` reduces everything to exam-group-level structures:

- `slots`: all slots minus excluded ones.
- `pinned`: official fixed exams (unless hypothetical mode) plus user locks. A lock that moves an official exam becomes a "locks" issue. `pin_reason` is `official` or `user`.
- `permitted[g]`: slots after exclusions and restrictions. `eligible[g]` is the same list narrowed to the pin; a pin outside the permitted set leaves no slot and records an issue.
- `student_sets`: distinct exam-group sets → number of students (**974 sets** for 1,000 students).
- `edges`: the co-enrollment graph, (g, h) → shared students (**792 edges**).
- Aggregate caps: the tighter of each slot's data cap and the run-level cap. A warning states that there is no room data and capacity is aggregate only.
- `_precheck` explains cases that are certainly infeasible without stopping the solver: pinned groups that overlap and share students, pinned load over a cap, and pigeonhole checks on total group and student capacity.
- Helpers: `restricted_sets(groups)` and `largest_groups(k)`.

### 7.2 `solver.py` — the model

**Variables:** `x[g,t] ∈ {0,1}`, created only for eligible pairs. Hard constraints (letters as in the module docstring):

| Constraint | Encoding |
|---|---|
| A — exactly one slot | `add_exactly_one(x[g,·])`; an empty list makes the model infeasible, which is then reported |
| B, C, F — pins, restrictions, locks | handled by the variable domains |
| D — strict mode | for each co-enrolled pair and each slot t of the group with fewer options: `x[a,t] + Σ_{u overlaps t} x[b,u] ≤ 1` |
| E — capacity | `Σ size_g·x[g,t] ≤ cap_students(t)`, `Σ x[g,t] ≤ cap_groups(t)` |
| optional — per-day cap | per student set and day: `Σ y ≤ cap` |
| relaxed mode — per-slot cap | per student set and slot: overlapping exams ≤ `max_per_slot` |

**Objective** (equal to the evaluator penalty at optimality):

- **Pair metrics** (back-to-back always; conflicts only in relaxed mode): one Boolean `p[g,h]` per edge, forced by `x[a,t] + Σ_{u∈R(t)} x[b,u] ≤ 1 + p`, with cost weight × shared students.
- **Late → early:** a pair indicator per co-enrolled pair, like back-to-back, with cost weight × shared students.
- **Windows** (3 in 24 h, 4 in 48 h): one Boolean `q[S]` per distinct student set S with |S| ≥ k. For each window W: `Σ_{g∈S} y[g,W] + c ≤ k−1 + (c+|v|−k+1)·q`, with cost weight × number of students holding S.
  - `y[g,W]` is cached. It is the constant 0 or 1 when all or none of g's eligible slots fall inside W; otherwise it is a new Boolean equal to Σ x.
- The indicators are only bounded from below. At a time limit the solver objective can therefore be **≥** the true penalty, so the evaluator's value is always the one reported.

**Run flow** (`solve(prob, cfg, hint=baseline)`):

1. **Two-phase** (optional): solve the largest 18 movable groups plus the pinned groups with 40 % of the time budget, then pin the result. Phase 2 solves the rest, and the result is never reported as OPTIMAL or with a bound. If phase 1 fails, it falls back to a single pass.
2. **Warm start:** a model with hard constraints only minimizes the number of groups moved from the baseline. Its budget is `min(3 s, max(0.5 s, 15 % of the limit))`. The baseline has 30 conflicts and is infeasible in strict mode; the warm start moved 6 groups to fix that.
3. **Build** the main model. `_full_hint` clones it, fixes x to the warm schedule, solves (pure propagation, 5 s limit) and hints **every** variable. This yields an `_Incumbent`.
4. **Solve** with the remaining **wall-clock** budget (the time limit covers warm start + build + search). Workers, seed and gap come from the config.
5. Interpret the status:
   - UNKNOWN with an incumbent → return the warm schedule as FEASIBLE, with a warning and no bound.
   - OPTIMAL or FEASIBLE → extract the schedule and breakdown. The objective is rounded and the bound is ceiled, since both are integers.
   - INFEASIBLE → `_diagnose` re-solves with one constraint family removed at a time (strict rule, capacity, per-day cap, user locks) and reports which family is responsible.
   - UNKNOWN without an incumbent → a hint to try a longer limit.
6. `SolveOutcome` carries the status, assignments, objective, bound, breakdown, runtime, strategy, relaxed and hypothetical flags, warnings, hints and `model_stats`: groups, slots, pinned groups, edges, sets, warm-start ms and groups moved, variables, constraints, build ms and solve ms.

---

## 8. Services — `backend/hexam/services/`

| Module | Responsibility |
|---|---|
| `dataset_service.py` | `DatasetRegistry` finds `data/<id>/` directories that hold all 5 collection files (`fall2026` first) and lazily loads them with strict validation, a cached index, the baseline and metadata. `DatasetHandle` provides a per-settings `Evaluator` cache (thread lock), `version` (the first 16 hex characters of SHA-256 over students + sections + exam_groups + timeslots), `info` (`DatasetInfo` including the synthetic disclosure), `group_courses` and `baseline_mapping`. |
| `optimization_service.py` | `build_config(OptimizeRequest)` → `OptimizationConfig`. `validate_request` rejects bad locks and unknown exclusions **before** queueing (`InvalidRequest` → 422). `execute()` runs the steps in order: fingerprint registrations → evaluate baseline → `solve` → build the optimized `ExamSchedule` (synthetic provenance, `locked` = pinned) → evaluate independently → `verify_schedule` → objective-consistency check → `compare`. It returns an `OptimizationRun` with a human message that has HYPOTHETICAL and RELAXED prefixes. **`RunStore`** is an in-memory store with an RLock and a **single-worker ThreadPoolExecutor**: runs execute one at a time and the last 50 non-precomputed runs are kept. It provides `submit` (optionally waits), `list`, `get`, `artifacts` (rebuilt lazily), `latest_completed` (prefers non-precomputed runs, then the newest), and `load_precomputed`, which accepts the saved file **only if** the dataset version matches **and** the stored schedule re-scores to the stored penalty. |
| `integrity.py` | `verify_schedule` re-derives the 8 hard-constraint checks from the data alone: every group scheduled once, valid slot references, official fixed exams preserved, user locks respected, only permitted slots, no student conflicts (with real intervals; relaxed mode reports the count but passes), capacity respected, registrations unchanged (SHA-256 fingerprint). `execute` adds a 9th check, `solver_objective_consistent`: objective ≥ evaluated penalty, and equal when OPTIMAL. |
| `comparison_service.py` | `compare()` refuses to compare evaluations made with different weights or settings, or on different student sets. It computes count and affected deltas, penalty change and percent, improved/worsened/unchanged students, groups moved and `metrics_worsened`. |
| `report_service.py` | `build_run_report(handle, run, artifacts)` renders the PDF comparison report with ReportLab: a headline and KPI strip, run details, the all-metrics table (count, change, %, students, weight, penalty), a students-affected chart, a students-per-period chart with a day-grouped axis, period and day load tables, **every exam group's official vs optimized time** (moved rows highlighted), the 10 most improved / most worsened / highest remaining students, integrity checks, solver notes and metric definitions. It uses the built-in Helvetica font, so `_latin1()` replaces glyphs it lacks (→, ≥, Σ). |
| `student_service.py` | `search` (filter by id substring, major, year, course prefix; paginated), `detail` (sections with meeting summaries), `impact` (baseline vs optimized `StudentScheduleImpact` with readable findings such as "Back-to-back: CS 1331 ends 10:50 AM, MATH 2551 starts 11:20 AM (Fri Dec 11)" and "3 exams within 24 hours (4 on Fri Dec 11)", plus a verdict), and `ranking` (order = improved, worsened, remaining or baseline). Labels use the student's *own* course in a group, not every course sharing the slot. |

`storage.py` defines `DATASET_FILES` (5 collections) and the optional `schedules.json`, `optimization_configs.json` and `optimization_results.json`. It reads and writes them through `TypeAdapter(list[Model])`, pretty-printed. `load_dataset(strict=True)` raises on cross-record errors.

---

## 9. REST API — `backend/hexam/api/`

`create_app(registry, load_precomputed=True)` sets up the lifespan: it creates the registry and `RunStore`, loads the precomputed runs (a bad file never blocks startup) and shuts the pool down on exit. CORS origins come from `HEXAM_CORS_ORIGINS` (default `localhost` / `127.0.0.1` on port 5173). Exception handlers: `DatasetNotFound` → 404, `StudentNotFound` → 404, `InvalidRequest` → 422 with a list of problems. The router is mounted at `/api`.

| Method | Route | Behavior |
|---|---|---|
| GET | `/health` | status, version (`hexam.__version__`, 1.0.0), OR-Tools version, datasets, active runs |
| GET | `/datasets`, `/datasets/{id}` | `DatasetInfo` |
| GET | `/exams?dataset_id&course&department&kind` | `ExamGroupView[]` with courses, departments, section and student counts, restrictions and baseline slot |
| GET | `/slots` | timeslots sorted by start |
| GET | `/students?q&major&year&course&offset&limit≤200` | `StudentPage` |
| GET | `/students/{id}` | `StudentDetailView` (id upper-cased) |
| GET | `/students/{id}/impact?run_id` | against the given run, or the latest completed run, or baseline only |
| GET | `/schedules/baseline` | official-matrix baseline |
| POST | `/evaluate` | score the baseline, a stored run schedule or ad-hoc assignments; `include_students` returns the full per-student list |
| POST | `/optimize` | **202** plus polling, or 200 when `wait=true`; 422 for bad locks |
| GET | `/runs?dataset_id`, `/runs/{id}` | summaries / full run (404 when unknown) |
| GET | `/runs/{id}/students?order&limit≤500` | ranking; 409 if the run has no schedule |
| GET | `/runs/{id}/report.pdf` | PDF comparison report as an attachment; 404 if the run is unknown, 409 if it has no optimized schedule or the dataset version is stale |
| GET | `/compare?dataset_id&run_id` | stored comparison; 404 if no run, 409 if no comparison or the dataset version is stale |
| POST | `/compare` | ad-hoc assignments vs the baseline under the given weights and settings |

---

## 10. Frontend — `frontend/`

**Toolchain:** React 18.3.1, Recharts 2.13.3, Tailwind 3.4.16, TypeScript 5.6 (strict, no unused locals or params), Vite 5.4, Vitest 2.1 with jsdom and Testing Library.

- The Vite dev server proxies `/api` to `HEXAM_API`, default `http://127.0.0.1:8000`. The build splits `react` and `charts` into manual chunks.
- Fonts are Inter and JetBrains Mono from Google Fonts. The favicon is `/hexam.svg`.

### 10.1 Architecture

- **`types/hexam.ts`** (655 lines) mirrors every Pydantic model and enum. Its helpers are `formatCampusTime`, `campusDate` and `compareMetrics`. **`backend/tests/test_ts_sync.py` parses this file** and checks that field names, nullability (`| null` exactly when Python accepts None) and enum unions all match.
- **`services/api.ts`** is a typed `fetch` client. `VITE_API_BASE` is an optional prefix. `ApiError(status, problems)` flattens FastAPI `detail` (strings or `{loc,msg}` objects); a network failure becomes "Cannot reach the Hexam backend…".
- **`hooks/useHexam.tsx`** provides a `HexamProvider` context:
  - On load it calls `datasets`, then in parallel `exams`, `slots`, `baseline`, `evaluate(baseline)` and `runs`, then fetches the latest completed run.
  - It holds the form state. `DEFAULT_WEIGHTS` match the backend; `DEFAULT_FORM` uses 20 s, seed 0 and single pass.
  - `runOptimization()` posts `/optimize` and polls `/runs/{id}` every **700 ms** until the run completes or fails, then refreshes the run list.
  - `selectRun` and `optimizedMapping()` are exported helpers.
- **`lib/format.ts`** formats everything in campus time with `Intl` (`campusTime`, `campusDay`, `campusDateKey`, `campusHHMM`, `dayFromKey`). It also has `num`, `pct` and `courseLabel`. **`METRICS`** lists the 5 metrics in priority order (label, short label, unit, help text). It also defines `STANDING` and `classNames`.
- **`App.tsx`** is the shell. Tabs are driven by the URL hash (`#overview`, `#schedule`, `#students`, `#optimize`). The sticky header has the brand, desktop and mobile navs, a dataset blurb, a status pill and a global **Run optimization** button. Loading and error states (with Retry) and a footer frame the pages.
- **`index.css`** defines design tokens as RGB triplets, GT navy and gold, chart series colors (baseline `#b8892a`, optimized `#2a5fa8`), automatic dark mode under `prefers-color-scheme` guarded by `data-theme`, and component classes (`card`, `btn-primary`, `btn-ghost`, `chip`, `input`, `label`, `h-section`, `tabular`, …). `tailwind.config.js` maps the tokens to colors with alpha support.

### 10.2 Components

- **`components/ui.tsx`:**
  - `Card`;
  - `Delta` (▼ shows an improvement because lower is better; it can be inverted);
  - `Kpi`: the optimized value is large, with the baseline struck through;
  - `StatusPill`: OPTIMAL ✓, FEASIBLE ◐, other ✕, plus RELAXED and HYPOTHETICAL chips;
  - `Spinner`, `ErrorBox`, `Empty`, `Swatch`, `Legend`, and an accessible `Segmented` radio group.
- **`components/charts.tsx`:** `PairBars` shows baseline vs optimized bars on one axis, horizontal or vertical (the Overview uses horizontal only).
  - Colors come from a theme hook (`useChartTokens`) that re-reads the CSS variables when the OS color scheme changes.
  - `PairTooltip` shows the baseline, optimized and change values. Animation is off.
- **`components/ReportButton.tsx`:** fetches `/api/runs/{id}/report.pdf` as a blob and saves it as `hexam-report-<run>.pdf`. It shows "Generating PDF…" while waiting and the backend's error message if generation fails. It renders nothing for a run without an optimized schedule.

### 10.3 Pages

| Page | Content |
|---|---|
| **Overview** | Hero sentence: "lowered inconvenience by X % … with zero exam conflicts", or the baseline-only "N of 1,000 face a problem". Run, Adjust weights, Explore a student and **Download PDF report** buttons; `RunMeta` card. **6 KPIs**: students evaluated, groups scheduled, conflicts, students with 3 in 24 h, back-to-back pairs, inconvenience score. **Mini stats**: better off, unchanged, groups moved. **2 charts**: students affected per metric; penalty points per metric (weight × count). **All-metrics table** with count, delta, students affected (baseline → optimized), weight, and a total row. |
| **Exam schedule** | A **Baseline / Optimized / Compare** segmented control (optimized modes are disabled with no run). Department, course and "only moved" filters. A day × start-time **timetable** with per-period exam counts and student totals (exam sittings in the period, not room capacity). `ExamCard` shows courses and size, 🔒 for common exams, and "from Fri 8 AM" for moved exams; dashed "↗ … moved out" ghosts mark old positions. A sticky `ExamDetail` side panel shows students, sections, meeting pattern, baseline vs optimized slot and source. |
| **Student impact** | Left list in **Improved / Worse / Highest / Search** modes (search by id plus a major dropdown, 60 results). Rankings come from `/runs/{id}/students` (40). The top student is selected by default. Right panel: synthetic id, major, year, number of exams, a verdict chip, and personal score before → after. Course chips. Two **MiniWeek** calendars (7:30–21:30, days with ≥ 3 exams or an overlap flagged ⚠, overlapping exams drawn side by side and outlined in red, moved exams ringed in gold) with finding bullets. A per-metric table with deltas; each row's unit is shown as (pairs) or (yes/no). |
| **Optimize** | Sliders for the 4 soft weights, 0–200 in steps of 5. The conflict slider (100–5000) is disabled in strict mode. Strict/relaxed and single/two-phase toggles, a time limit of 5–120 s, the seed, max exams per day, students per period (aggregate cap), groups per period, and a HYPOTHETICAL "allow moving common exams" checkbox. **Lock exam groups** (pick a group and a period, add or remove). Run and Reset buttons. `JobStatus` shows elapsed time against the limit; the bar explicitly says it is not solution progress. `RunDetails` shows status, integrity checks, warnings, infeasibility hints, and model stats and weights in a `<details>`. **Run history** with View buttons. The selected run's `RunMeta` includes the **Download PDF report** button. |
| `RunMeta` (shared) | Precomputed notice, runtime, evaluated score, solver objective, best bound ("not reported" when null), optimality gap %, strategy, finish time, an honest status sentence, "N/9 integrity checks passed" and, when not compact, the report button. |

### 10.4 Frontend tests (`src/__tests__/app.test.tsx`, 13 tests)

They mock `fetch` with fixture responses and cover:

- format helpers (campus time regardless of the viewer's timezone; labels and percentages);
- `Delta` and `Kpi`;
- App loading baseline metrics, the backend-unreachable error with Retry, showing a completed run, submitting the config and showing the result, showing backend validation errors, switching between baseline and optimized schedule modes, downloading the PDF report, and showing the backend's error when the report fails.

`test-setup.ts` stubs `ResizeObserver` for Recharts.

---

## 11. Data files

| Path | Content |
|---|---|
| `data/raw/` | `gt_scheduler_202608.json`, `gt_scheduler_index.json`, `final_exam_matrix_202608.pdf`, `registrar_exam_guidelines.html`, `registrar_matrix_page.html`, `irp_coe_enrollment.html`, `irp_factbook_2025_ug_by_college.json` (the Fact Book PDF itself is git-ignored) |
| `data/provenance/sources.json` | fetch manifest with SHA-256 for each source |
| `data/fall2026/` | `courses` (64), `sections` (756), `exam_groups` (52), `timeslots` (21), `students` (1,000), `schedules` (the baseline), `exam_matrix`, `course_metadata`, `section_metadata`, `normalization_report`, `generation_metadata`, `dataset_stats`, **`precomputed_run.json`** |
| `data/sample/` | a hand-built fixture: 11 course codes, 18 sections, 12 groups (9 meeting-pattern + 3 common), 19 assumed slots (Dec 7–11), 60 students, baseline and optimized schedules, 2 configs and 1 result. `optimization_results.json` is hand-written, not optimizer output. |

---

## 12. Results — precomputed demo run (actual file contents)

`run-20261115-000737-0de9`: default weights, **120 s** limit, seed 0, 8 workers. Status **FEASIBLE** (not proven optimal); runtime 120.99 s; objective **17,765**; best bound **1,475**, so the gap is about 92 %. The run has 5,294 variables and 47,945 constraints; the warm start took 199 ms and moved 6 groups. Dataset version `6c0f9920ccea90d0`. **All 9 integrity checks passed.**

| Metric (unit) | Baseline → Optimized | Students affected | Weighted points |
|---|---|---|---|
| Simultaneous conflicts (pairs) | 30 → **0** | 30 → 0 | 30,000 → 0 |
| 3 in 24 h (students) | 187 → **66** | 187 → 66 | 18,700 → 6,600 |
| Back-to-back (pairs) | 554 → **319** | 449 → 283 | 16,620 → 9,570 |
| 4 in 48 h (students) | 150 → **73** | 150 → 73 | 2,250 → 1,095 |
| Late → early (pairs) | 123 → **50** | 119 → 50 | 1,230 → 500 |
| **Total score** | **68,800 → 17,765 (−74.2 %)** | any issue: 534 → 330 | |

- Students: **424** better off, **173** worse off, **403** unchanged. **40 of 52** groups moved. Every metric improves.
- Separate per-exam late-evening and early-morning metrics were tried (−65 % to −68 %, "any issue" 637 to 966) and reverted in favor of this pair metric.
- Baseline diagnostics: 283 distinct 3-in-24 h triples, 243 4-in-48 h quadruples, at most 4 exams in one day.

---

## 13. Tests — `backend/tests/` (pytest; `pythonpath=.`)

| Module | Tests | Focus |
|---|---|---|
| `test_records.py` | 41 | per-record validators: timezones (UTC → New York, DST in May, naive rejected), meeting times, ID formats, CRN, synthetic-only students, provenance rules, exam-group restrictions, schedules, MetricValue invariants |
| `test_dataset.py` | 36 | sample-dataset integrity, round trip, New York offsets, index derivation (incl. chronological `timeslots_by_day`), every cross-record error and warning code, schedule and config validation, default weight ordering |
| `test_evaluator.py` | 25 | touching intervals = back-to-back not conflict, partial overlap, 3 in one slot = 3 pairs, 3 in a day counts as 3 in 24 h, a window of exactly 24 h is excluded, rolling (not calendar) 48 h, once per student, late→early pairs (and the skipped-day gap limit), configurable thresholds, an exam crossing midnight, DST, multi-section common exams counted once, invalid or missing assignments, breakdown sums, determinism, `SlotRelations` agreeing with the evaluator, window coverage |
| `test_data_generation.py` | 23 | matrix structure, known rows, block sizes, end-sensitive pattern, days+start fallback, common and special periods, geometry on synthetic fragments, wrong weekday, crawler tuple decoding, IRP parsers, fetch caching and fallback, term resolution, section eligibility, config consistency, selection rules |
| `test_optimization.py` | 21 | schema-level config and result validators, weights, `relative_gap`, request exactly-one rules |
| `test_optimizer.py` | 20 | exactly once, no conflicts, unrelated groups may share a slot, fixed exams preserved, restrictions, locks, both capacity caps, per-day cap, infeasibility and diagnosis, pinned-pair explanation, lock to a forbidden slot, hypothetical mode, relaxed mode, known optima, **solver objective == evaluator**, weight trade-offs, exact late→early objective, two-phase reports no bound |
| `test_api.py` | 16 | all endpoints, 404/409/422, infeasible run reported rather than faked, async 202 + polling, PDF report content read back with pypdf |
| `test_data_integration.py` | 7 | full-dataset validation, shape, plausible stats, same seed gives the same output and a different seed differs, injected problems caught, alternative granularities build, committed dataset is current |
| `test_ts_sync.py` | 3 | Pydantic ↔ TypeScript interfaces and unions in sync |
| `test_integration_pipeline.py` | 1 | full dataset end to end: baseline untouched, registrations unchanged, integrity checks pass, genuine improvement |
| `factories.py`, `conftest.py` | — | builders for small datasets; sample-dataset fixtures (deep-copied per test) |

Last run, after the section 15 fixes: **286 backend tests passed, 13 frontend tests passed**, with `tsc` and `vite build` OK.

---

## 14. Scripts and run commands

- `scripts/dev.ps1` (Windows) and `scripts/dev.sh` (macOS, Linux, Git Bash) create the venv and `pip install -e backend[dev]` if missing, run `npm install` if needed, start uvicorn on :8000 in the background and Vite on :5173 in the foreground, and kill the API on exit.
- `scripts/precompute_demo.py --dataset --time-limit --seed --workers` runs `execute()` with default weights. It writes `precomputed_run.json` (labeled and `precomputed=True`) only if a schedule exists **and** integrity passed.
- `.claude/launch.json` holds the `hexam-api` and `hexam-web` preview configs.

```bash
powershell -ExecutionPolicy Bypass -File scripts/dev.ps1
```
```bash
cd backend && .venv/Scripts/python -m pytest -q
```
```bash
cd frontend && npm test && npm run build
```
```bash
backend/.venv/Scripts/python scripts/precompute_demo.py --time-limit 120
```
```bash
cd backend && .venv/Scripts/python -m hexam.data_generation.build
```

---

## 15. Inconsistencies found while reading, and what happened to them

| # | Finding | Status |
|---|---|---|
| 1 | `handoff.md` did not match the metrics in the code at the time. | **Fixed.** It shows the current run (68,800 → 17,765, −74.2 %) with the late → early pair metric, which was briefly replaced by per-exam metrics and then restored. |
| 2 | `docs/schemas.md` defined conflicts as "k − 1 per slot" and back-to-back as "consecutive same-day slots", and quoted the sample penalty as 7,400 → 25. | **Fixed.** Both are now defined as pairs of a student's exams (overlapping intervals; a break of at most 30 min), and the sample figure is 7,110 → 685 (6,795 → 330 after the late-evening metric was removed). |
| 3 | `reportlab` looked like an unused dependency. | **Not an issue.** `services/report_service.py` imports it to build the PDF report. |
| 4 | `DatasetIndex.overnight_pairs` and `consecutive_pairs` were built but unused, and the `EVENING_START_HOUR` docstring mentioned the removed night→morning metric. | **Fixed.** Both tables are removed (the test now checks `timeslots_by_day`), and the docstring now says evening is display only. |
| 5 | The Student impact table labeled late-evening and early-morning rows "(pairs)" (late-evening has since been removed). | **Fixed.** `student exams` units now show "(exams)"; the dead `student-days` branch is gone. |
| 6 | `model_stats.build_ms` included the warm start (and phase 1). | **Fixed.** It now times only the model build and full hint; the warm start keeps its own `warm_start_ms`. |
| 7 | `pyproject.toml` said 0.1.0 while the code, API and frontend said 1.0.0. | **Fixed.** `pyproject.toml` is 1.0.0, and the API reads `hexam.__version__` instead of a hard-coded string. |

---

## 16. Possible next steps (from handoff.md, still applicable)

- Improve solution quality and the bound: symmetry breaking across same-time periods, LNS tuning, or seeding two-phase from the best run.
- Add a test that forces the warm-start incumbent fallback (main search returns UNKNOWN → the warm schedule is returned as FEASIBLE).
- Code-split Recharts (about 526 kB minified, 151 kB gzipped).
