# Real-data ingestion & synthetic enrollment (`hexam.data_generation`)

Builds `data/fall2026/`: **real** Georgia Tech Fall 2026 courses, sections and
official final-exam periods, plus **1,000 synthetic students** with valid,
conflict-free section schedules and an **official-matrix baseline** exam schedule.
Loadable with `hexam.storage.load_dataset("data/fall2026")`.

```bash
cd backend
.venv/Scripts/python -m pip install -e ".[dev]"            # adds pypdf (PDF parsing)
.venv/Scripts/python -m hexam.data_generation.build         # offline, from the committed snapshot
```

| Step | Command (from `backend/`) | Network |
|---|---|---|
| Fetch + cache sources | `python -m hexam.data_generation.fetch_sources --term 202608 [--force] [--offline]` | yes |
| Normalize real data | `python -m hexam.data_generation.normalize [--granularity meeting_time\|meeting_pattern\|course]` | no |
| Generate students | `python -m hexam.data_generation.generate --num-students 1000 --seed 42` | no |
| Validate | `python -m hexam.data_generation.validate [--skip-reproducibility]` | no |
| Statistics | `python -m hexam.data_generation.stats [--json]` | no |
| All of the above | `python -m hexam.data_generation.build [--fetch]` | only with `--fetch` |

## Sources (status as of 2026-11-14, see `data/provenance/sources.json`)

| Source | Status | Used for |
|---|---|---|
| GT Scheduler crawler-v2 `index.json` / `202608.json` | fetched (term present in index) | courses, sections, CRNs, instructors, meeting times, credits, prerequisites, section restrictions |
| Registrar Fall 2026 final exam matrix PDF (`…202608 Fall_3.pdf`) | fetched; confirmed linked from the current-term matrix page | 21 exam periods, 129 meeting-pattern rows, 5 common-exam rows, reading & conflict periods |
| Registrar exam guidelines page | fetched | rule: lecture courses of ≥ 2 credits get a matrix final; conflict rules |
| IRP COE "Enrollment by Major" dashboard | fetched | Fall 2026–27 undergraduate headcount by year (1–5) for 11 engineering majors |
| IRP 2025 Fact Book PDF | fetched (PDF git-ignored; extract committed) | Fall 2025 undergraduate totals by college (Computing, Sciences) |
| OSCAR / schedule-of-classes pages | landing pages reachable; **not scraped** | capacities/enrollment would need Banner class search (also in a registrar-announced maintenance window Nov 13–16) |
| catalog.gatech.edu (CS courses, CS BS) | **failed** (connection timeout/refused) | fallback: curriculum weights are labelled `assumed`; prerequisites come from OSCAR via the crawler |

Every download is cached in `data/raw/` with its SHA-256; `validate` fails if
a cached file no longer matches the manifest.

## How the real data is parsed

**Crawler JSON** (`adapters/gt_scheduler.py`) — positional tuples decoded per
`src/types.ts` of the crawler: `Section = [crn, meetings, credits, scheduleTypeIdx,
campusIdx, attributeIdxs, gradeBaseIdx, title, restrictions]`, `Meeting =
[periodIdx, days, room, locationIdx, instructors, dateRangeIdx, finalDateIdx,
finalTimeIdx]`; `periods` are `"HHMM - HHMM"`/`"TBA"`; restriction values index
`caches.campuses` for campus and `caches.restrictionValues[category]` otherwise.
The crawler's own `finalDates/finalTimes` caches are empty for 202608, and it has
no capacity/enrollment fields, so neither is used or invented.

**Exam matrix PDF** (`adapters/exam_matrix.py`) — plain text extraction scrambles
the two-column layout (merged "Exam Date/Time" cells come out detached). The
parser reads every text fragment *with coordinates*, pairs each date cell with
the time range under it, and assigns each class-meeting row to the nearest date
cell at or above it in the same column (cells are top-aligned with their block).
Fused left/right rows on one baseline are split. Checks: weekday names match
dates, all 129 rows assigned, one exam length (2 h 50 m), no exam in a reading
period. Block sizes and 14 specific rows are pinned in tests against the PDF.
Finding: `WF 8:25–9:15` → Fri Dec 11 but `WF 8:25–10:20` → Wed Dec 16, so the
end time matters for that one pattern (its exam groups carry the end time).

## Normalization rules (`normalize.py`)

* Subset: 64 courses (CS, MATH, PHYS, ECE, ME, ISYE, AE, COE, CHEM, BIOS, BMED,
  CHBE, CEE, MSE, gen-ed) from `config/fall2026.json`.
* Section kept if Atlanta campus, full term, has a scheduled meeting, not
  graduate-only, not reserved (cohort/program/permit/department restrictions).
* *Primary* = credit-bearing section; 0-credit sections are *components*
  (studio/lab/recitation) — linked by code (`A01` → `A`) or free (PHYS `L01`).
  OSCAR sometimes labels types inversely (MATH 1551's 2-credit primary is a
  "Studio"), so credit, not type, decides.
* Exam-bearing primary: course has an official common exam, or it is a Lecture
  of ≥ 2 credits. Its period = matrix row for its class meeting (exact; else
  days+start when unambiguous — 5 sections). 2 sections with non-standard
  patterns (no matrix row) are excluded rather than guessed.
* ECE 3710's common exam applies only to online sections, so Atlanta sections use
  the meeting-pattern matrix (logged as a warning).
* `capacity` stays `null` (unknown); `enrollment` = synthetic registration count,
  labelled via `provenance.field_sources.enrollment = "synthetic"`.

**Exam groups** (`exam_group_granularity`) follow the Bucknell paper
([arXiv 2509.11031](https://arxiv.org/html/2509.11031v1), §3.1 and App. A): "course
groups" are (1) one per multi-section course with a coordinated/common exam and
(2) one per *regular meeting time* (days + start + end) for everything else.
* `meeting_time` (**default, as in the paper**): e.g. `EG-MWF-0930-1020`;
  **52 groups** (47 meeting-time + 5 common). Each equals one GT matrix row, so
  every group has exactly one official baseline period. Sections whose several
  meetings map to different periods are flagged in
  `normalization_report.json → ambiguous_meeting_times` (none in Fall 2026).
* `meeting_pattern`: days + start only (end kept only for `WF 08:25`), e.g.
  `EG-MWF-0930`; 38 groups.
* `course`: one group per (course, meeting time), e.g. `EG-CS-1331-MWF-0930-1020`; 256 groups.
* Common exams (`EG-COMMON-MATH-1553-1554`, …) are `kind=common` with
  `fixed_timeslot_id` = the official period in every mode (the paper's r_gt).

Because the generator forbids overlapping class meetings and every section in a
meeting-time group meets at the same time, no student can have two courses in one
group, so the paper's "forced overlaps" are zero by construction.

Paper features outside this data step: the optimizer implements the per-slot student cap
(M₁ → `ExamTimeslot.max_students` or a run-level cap) and the two-phase heuristic
(largest groups first, 18 by default). Faculty overlap / back-to-back penalties
(derivable from `Section.instructors`) and a weight portfolio are not implemented.

**Timeslots**: the 21 official exam periods (Dec 10–17). The 3 reading periods
and 2 conflict periods (Dec 18) are not schedulable timeslots; they are kept in
`exam_matrix.json` (`special_periods`).

**Baseline schedule** (`schedules.json`, `baseline-gt-matrix-202608`): every group
in its official period, common exams `locked`. It is the registrar's rule applied
to this subset — not an approved Hexam schedule.

## Synthetic course selection (`generate.py`)

1. **Profile**: major ∝ headcount (IRP for engineering; Fact Book college total ×
   assumed share for CS 0.85 / Applied Math 0.12 / Physics 0.08); year ∝ the
   major's IRP year distribution (engineering average for non-IRP majors); load
   k ∈ {3,4,5,6} with P = {.12,.40,.38,.10}.
2. **Weights** per course: `curriculum(major, year) × level(course level, year) ×
   (#real eligible sections)^0.5`, zero if no section's OSCAR restrictions
   (major / class / college / level) admit the student. Curriculum = shared
   tracks (calculus, physics, chemistry, computing, mechanics, gen-ed) × major
   multipliers, overridden by major-specific year weights.
3. **Sequential draw without replacement**: after each pick, remove exclusive
   alternatives (MATH 1553/1554, CS 1301/1371, CHEM 1310/1211K, one stats
   course), multiply OSCAR-prerequisite-linked courses by 0.05 (no CS 1331 with
   CS 1332), and multiply affine courses by their factor (e.g. MATH 1552 + PHYS
   2211 × 2.0).
4. **Synthetic sample capacities**: `ceil(demand × 1.15 / #sections) + 1`.
5. **Sections**: backtracking, most-constrained course first; eligible primary
   with room and no meeting overlap, plus one linked/free component when the
   course has them; random order weighted by remaining capacity.
6. **Repair**: an unplaceable course is replaced by a new weighted draw (≤ 25
   attempts); otherwise the student is redrawn (none needed at seed 42).

Deterministic: one `random.Random(seed)`, sorted iteration everywhere
(verified identical under different `PYTHONHASHSEED`s); output hashes are
stored in `generation_metadata.json` and re-checked by `validate`.

## Dataset at seed 42 (from `dataset_stats.json`)

* 1,000 students, 4,432 course registrations (6,326 incl. studios/labs); 4.43
  courses/student (3: 131, 4: 397, 5: 381, 6: 91).
* Majors: CS 242, ME 181, IE 116, CMPE 86, AE 85, BMED 74, EE 71, CHBE 54, CE 41,
  MATH 23, MSE 19, PHYS 8. Years 1–5+: 265 / 309 / 220 / 146 / 60.
* 919 distinct course sets; largest identical set shared by 5 students; mean
  pairwise Jaccard 0.08; all 64 courses used; top course MATH 2551 (28.5%).
* Top pairs: MATH 2551 + PHYS 2212 (123), MATH 2551 + 2552 (102), MATH 1552 +
  1554 (101), APPH 1040 + MATH 1552 (83).
* Largest exam groups: EG-TR-1230-1345 (306), EG-TR-1530-1645 (293),
  EG-TR-1400-1515 (287), PHYS 2212 common (255), MATH 1553/1554 common (253).
  `EG-MW-1830-1945` (one ECE 2020 section) has no sampled student.
* 327 / 337 primary sections used; 50 at their synthetic capacity.
* Provenance: courses 64, sections 756, exam groups 52, timeslots 21, baseline 1
  → `gt_public`; students 1,000 and all section enrollment counts → `synthetic`.

## Consuming the dataset (evaluator / optimizer)

```python
from hexam.storage import load_dataset, load_schedules
ds = load_dataset("../data/fall2026")          # strict cross-record validation
idx = ds.build_index()
idx.student_exam_groups["S000001"]             # exam groups per student (derived via sections)
idx.group_students["EG-TR-1230-1345"]          # students per exam group
idx.timeslots_by_day                           # chronological slot ids per campus day
baseline = load_schedules("../data/fall2026")[0].as_mapping()   # {exam_group_id: timeslot_id}
fixed = {g.exam_group_id: g.fixed_timeslot_id for g in ds.exam_groups if g.fixed_timeslot_id}
```

* Decision variables: the paper's x_gt, one per (non-fixed exam group, timeslot) —
  47 × 21 at the default granularity; common groups are pinned by `fixed_timeslot_id`.
* Compare optimized schedules against `baseline-gt-matrix-202608` with the same
  `ScheduleEvaluation` metrics.
* Components (`exam_group_id = null`) never sit exams; ignore them in the solver.
* Conflict periods (Dec 18) are in `exam_matrix.json` if a run wants to model
  conflict resolution explicitly.
* Results are about a synthetic sample: improvements are evidence for the method,
  not claims about real GT students or an officially approvable schedule.
