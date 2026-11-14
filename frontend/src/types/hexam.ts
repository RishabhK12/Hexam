/**
 * Hexam shared types — mirror of backend/hexam/schemas (Pydantic).
 *
 * These describe JSON as the backend *serializes* it: every field is present,
 * optional values are `null` (never omitted). For request bodies where the
 * backend fills defaults, use `Partial<...>`.
 *
 * Kept in sync by backend/tests/test_ts_sync.py — update both sides together.
 */

// ---------------------------------------------------------------- primitives

/** Stable ID: letters, digits, `_ . : -`; no spaces. */
export type ID = string;
/** `<SUBJECT>-<NUMBER>`, e.g. "CS-1331". */
export type CourseId = string;
/** Anonymous synthetic ID, e.g. "S000042". */
export type StudentId = string;
/** ISO 8601 datetime with offset, normalized to America/New_York, e.g. "2026-12-07T08:00:00-05:00". */
export type ISODateTime = string;
/** ISO 8601 calendar date, "YYYY-MM-DD" (campus-local). */
export type ISODate = string;
/** Campus wall-clock time, "HH:MM:SS" (America/New_York, no offset). */
export type LocalTime = string;

export const CAMPUS_TIMEZONE = "America/New_York" as const;
export type CampusTimezone = typeof CAMPUS_TIMEZONE;

// ---------------------------------------------------------------- provenance

export type DataSource = "gt_public" | "synthetic" | "assumed";

export interface Provenance {
  source: DataSource;
  source_ref: string | null;
  retrieved_at: ISODateTime | null;
  notes: string | null;
  /** Per-field override of `source`, e.g. { enrollment: "synthetic" }. */
  field_sources: Record<string, DataSource>;
}

// ------------------------------------------------------- courses & sections

export type Weekday = "M" | "T" | "W" | "R" | "F" | "S" | "U";
export type SectionType = "lecture" | "lab" | "recitation" | "studio" | "other";

export interface MeetingTime {
  days: Weekday[];
  start_time: LocalTime;
  end_time: LocalTime;
  location: string | null;
}

export interface Course {
  course_id: CourseId;
  subject: string;
  number: string;
  title: string;
  credit_hours: number | null;
  provenance: Provenance;
}

export interface Section {
  section_id: ID;
  course_id: CourseId;
  section_code: string;
  crn: string | null;
  section_type: SectionType;
  instructors: string[];
  enrollment: number;
  capacity: number | null;
  meeting_times: MeetingTime[];
  /** null = no final exam. Single source of truth for section → exam group. */
  exam_group_id: ID | null;
  provenance: Provenance;
}

// ------------------------------------------------------------------ students

export type ClassStanding =
  | "first_year"
  | "second_year"
  | "third_year"
  | "fourth_year"
  | "fifth_year_plus"
  | "graduate";

export interface Student {
  student_id: StudentId;
  major: string;
  year: ClassStanding;
  section_ids: ID[];
  provenance: Provenance;
}

// --------------------------------------------------------------- exam groups

export type ExamGroupKind = "meeting_pattern" | "common" | "special";

export interface ExamGroup {
  exam_group_id: ID;
  name: string;
  kind: ExamGroupKind;
  meeting_pattern: string | null;
  description: string | null;
  fixed_timeslot_id: ID | null;
  /** null = every timeslot allowed. */
  allowed_timeslot_ids: ID[] | null;
  forbidden_timeslot_ids: ID[];
  provenance: Provenance;
}

// ----------------------------------------------------------------- timeslots

export interface ExamTimeslot {
  timeslot_id: ID;
  start: ISODateTime;
  end: ISODateTime;
  label: string | null;
  max_students: number | null;
  max_exam_groups: number | null;
  provenance: Provenance;
}

// ----------------------------------------------------------------- schedules

export type ScheduleKind = "baseline" | "optimized" | "manual";

export interface ExamAssignment {
  exam_group_id: ID;
  timeslot_id: ID;
  locked: boolean;
}

export interface ExamSchedule {
  schedule_id: ID;
  name: string;
  kind: ScheduleKind;
  assignments: ExamAssignment[];
  created_at: ISODateTime;
  source_run_id: ID | null;
  provenance: Provenance;
}

// ------------------------------------------------------------------- metrics

export type ImpactMetricKey =
  | "simultaneous_conflicts"
  | "back_to_back"
  | "night_to_morning"
  | "three_in_one_day"
  | "three_in_24h"
  | "four_in_48h";

export const IMPACT_METRIC_KEYS: readonly ImpactMetricKey[] = [
  "simultaneous_conflicts",
  "back_to_back",
  "night_to_morning",
  "three_in_one_day",
  "three_in_24h",
  "four_in_48h",
] as const;

export interface MetricValue {
  count: number;
  students_affected: number;
}

export interface StudentImpactMetrics {
  students_evaluated: number;
  exam_enrollments: number;
  students_with_any_issue: number;
  simultaneous_conflicts: MetricValue;
  back_to_back: MetricValue;
  night_to_morning: MetricValue;
  three_in_one_day: MetricValue;
  three_in_24h: MetricValue;
  four_in_48h: MetricValue;
}

export interface StudentImpactDetail {
  student_id: StudentId;
  exam_count: number;
  exam_timeslot_ids: ID[];
  simultaneous_conflicts: number;
  back_to_back: number;
  night_to_morning: number;
  three_in_one_day: number;
  three_in_24h: number;
  four_in_48h: number;
}

export interface TimeslotLoad {
  timeslot_id: ID;
  exam_group_count: number;
  student_count: number;
  over_capacity: boolean;
}

export interface ScheduleEvaluation {
  schedule_id: ID;
  evaluated_at: ISODateTime;
  metrics: StudentImpactMetrics;
  weighted_penalty: number | null;
  timeslot_loads: TimeslotLoad[];
  worst_students: StudentImpactDetail[];
}

export interface MetricDelta {
  key: ImpactMetricKey;
  before: number;
  after: number;
  change: number;
  percent_change: number | null;
}

// -------------------------------------------------------------- optimization

/** Field names equal ImpactMetricKey. Integers (CP-SAT coefficients). */
export interface PenaltyWeights {
  simultaneous_conflicts: number;
  back_to_back: number;
  night_to_morning: number;
  three_in_one_day: number;
  three_in_24h: number;
  four_in_48h: number;
}

export interface HardConstraints {
  forbid_simultaneous_conflicts: boolean;
  max_exams_per_student_per_slot: number;
  max_exams_per_student_per_day: number | null;
  enforce_timeslot_capacity: boolean;
  enforce_group_restrictions: boolean;
}

export interface SolverSettings {
  time_limit_seconds: number;
  num_workers: number;
  random_seed: number;
  relative_gap_limit: number | null;
  use_baseline_hint: boolean;
  log_search_progress: boolean;
}

export interface FixedAssignment {
  exam_group_id: ID;
  timeslot_id: ID;
  reason: string | null;
}

export interface OptimizationConfig {
  config_id: ID;
  name: string;
  description: string | null;
  baseline_schedule_id: ID | null;
  weights: PenaltyWeights;
  hard_constraints: HardConstraints;
  solver: SolverSettings;
  fixed_assignments: FixedAssignment[];
  excluded_timeslot_ids: ID[];
  created_at: ISODateTime;
}

export type SolverStatus =
  | "OPTIMAL"
  | "FEASIBLE"
  | "INFEASIBLE"
  | "MODEL_INVALID"
  | "UNKNOWN"
  | "ERROR";

export interface OptimizationResult {
  run_id: ID;
  config_id: ID;
  status: SolverStatus;
  started_at: ISODateTime;
  finished_at: ISODateTime;
  runtime_seconds: number;
  objective_value: number | null;
  best_objective_bound: number | null;
  objective_breakdown: Partial<Record<ImpactMetricKey, number>>;
  baseline_schedule_id: ID | null;
  optimized_schedule_id: ID | null;
  before: ScheduleEvaluation | null;
  after: ScheduleEvaluation | null;
  message: string | null;
}

// ------------------------------------------------------------------- dataset

export interface MajorProfile {
  major: string;
  weight: number;
  core_course_ids: CourseId[];
}

export interface SyntheticGenerationConfig {
  seed: number;
  num_students: number;
  majors: MajorProfile[];
  year_distribution: Partial<Record<ClassStanding, number>>;
  min_sections_per_student: number;
  max_sections_per_student: number;
  avoid_meeting_time_conflicts: boolean;
}

export interface Dataset {
  courses: Course[];
  sections: Section[];
  exam_groups: ExamGroup[];
  timeslots: ExamTimeslot[];
  students: Student[];
}

// ---------------------------------------------------------------- validation

export type Severity = "error" | "warning";

export interface ValidationIssue {
  severity: Severity;
  code: string;
  message: string;
  entity_type: string | null;
  entity_id: string | null;
}

export interface ValidationReport {
  issues: ValidationIssue[];
  valid: boolean;
  error_count: number;
  warning_count: number;
}

// ----------------------------------------------------------------------- API

export interface DatasetSummary {
  course_count: number;
  section_count: number;
  exam_group_count: number;
  timeslot_count: number;
  student_count: number;
  provenance_breakdown: Record<string, Partial<Record<DataSource, number>>>;
}

/** Exactly one of config_id / config. */
export interface RunOptimizationRequest {
  config_id: ID | null;
  config: OptimizationConfig | null;
}

export interface RunOptimizationResponse {
  result: OptimizationResult;
  schedule: ExamSchedule | null;
}

/** Exactly one of schedule_id / assignments. */
export interface EvaluateScheduleRequest {
  schedule_id: ID | null;
  assignments: ExamAssignment[] | null;
  weights: PenaltyWeights | null;
}

// ------------------------------------------------------------------- helpers

/** Format an ISODateTime in campus time regardless of the viewer's timezone. */
export function formatCampusTime(
  iso: ISODateTime,
  opts: Intl.DateTimeFormatOptions = { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" },
): string {
  return new Intl.DateTimeFormat("en-US", { ...opts, timeZone: CAMPUS_TIMEZONE }).format(new Date(iso));
}

/** Campus-local calendar date ("YYYY-MM-DD") of an ISODateTime. */
export function campusDate(iso: ISODateTime): ISODate {
  // en-CA formats as YYYY-MM-DD
  return new Intl.DateTimeFormat("en-CA", { timeZone: CAMPUS_TIMEZONE }).format(new Date(iso));
}

export function compareMetrics(before: StudentImpactMetrics, after: StudentImpactMetrics): MetricDelta[] {
  return IMPACT_METRIC_KEYS.map((key) => {
    const b = before[key].count;
    const a = after[key].count;
    return {
      key,
      before: b,
      after: a,
      change: a - b,
      percent_change: b === 0 ? null : Math.round((10000 * (a - b)) / b) / 100,
    };
  });
}
