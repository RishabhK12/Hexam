import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import { Delta, Kpi } from "../components/ui";
import { campusDateKey, campusHHMM, campusTime, courseLabel, pct } from "../lib/format";
import { layoutDay } from "../pages/Students";
import type { ImpactMetricKey, OptimizationRun, ScheduleEvaluation } from "../types/hexam";

// ------------------------------------------------------------------ fixtures

const KEYS: ImpactMetricKey[] = ["simultaneous_conflicts", "back_to_back", "night_to_morning", "three_in_24h", "four_in_48h"];
const W = { simultaneous_conflicts: 1000, back_to_back: 30, night_to_morning: 10, three_in_24h: 100, four_in_48h: 15 };

function evaluation(id: string, scale: number): ScheduleEvaluation {
  const metrics = Object.fromEntries(KEYS.map((k) => [k, { count: 10 * scale, students_affected: 5 * scale }]));
  return {
    schedule_id: id,
    evaluated_at: "2026-11-14T12:00:00-05:00",
    metrics: { students_evaluated: 2, exam_enrollments: 4, students_with_any_issue: 2 * scale, ...metrics } as ScheduleEvaluation["metrics"],
    weighted_penalty: 1000 * scale,
    timeslot_loads: [{ timeslot_id: "TS-A", exam_group_count: 1, student_count: 2, over_capacity: false }],
    worst_students: [],
    penalty_breakdown: Object.fromEntries(KEYS.map((k) => [k, 100 * scale])),
    weights_used: W,
    settings: null,
    valid: true,
    issues: [],
    day_loads: [{ day: "2026-12-10", exam_group_count: 1, student_exam_count: 2, student_count: 2 }],
    diagnostics: {
      unassigned_exam_group_ids: [],
      students_with_unassigned_exams: 0,
      three_in_24h_patterns: 0,
      four_in_48h_patterns: 0,
      max_exams_one_day: 1,
      exam_count_histogram: {},
    },
  };
}

const dataset = {
  dataset_id: "fall2026",
  name: "GT Fall 2026",
  term: "Fall 2026",
  student_count: 2,
  exam_group_count: 2,
  timeslot_count: 2,
  course_count: 2,
  section_count: 2,
  has_baseline: true,
  baseline_schedule_id: "baseline",
  baseline_provenance: "gt_public: matrix",
  dataset_version: "abc",
  generation_seed: 42,
  provenance_breakdown: {},
  data_categories: { synthetic: "students" },
  synthetic_disclosure: "All student records are SYNTHETIC.",
  exam_days: ["2026-12-10"],
};
const slots = [
  { timeslot_id: "TS-A", start: "2026-12-10T08:00:00-05:00", end: "2026-12-10T10:50:00-05:00", label: null, max_students: null, max_exam_groups: null, provenance: { source: "gt_public", source_ref: "x", retrieved_at: null, notes: null, field_sources: {} } },
  { timeslot_id: "TS-B", start: "2026-12-10T11:20:00-05:00", end: "2026-12-10T14:10:00-05:00", label: null, max_students: null, max_exam_groups: null, provenance: { source: "gt_public", source_ref: "x", retrieved_at: null, notes: null, field_sources: {} } },
];
const exam = (id: string, course: string, fixed: string | null = null) => ({
  exam_group_id: id,
  name: id,
  kind: fixed ? "common" : "meeting_pattern",
  meeting_pattern: fixed ? null : "MWF 09:30",
  description: null,
  course_ids: [course],
  departments: [course.split("-")[0]],
  section_count: 1,
  student_count: 2,
  fixed_timeslot_id: fixed,
  allowed_timeslot_ids: null,
  forbidden_timeslot_ids: [],
  baseline_timeslot_id: "TS-A",
  source: "gt_public",
});
const exams = [exam("EG-1", "CS-1331"), exam("EG-2", "MATH-1554", "TS-A")];
const baseline = {
  schedule_id: "baseline",
  name: "Baseline",
  kind: "baseline",
  assignments: [
    { exam_group_id: "EG-1", timeslot_id: "TS-A", locked: false },
    { exam_group_id: "EG-2", timeslot_id: "TS-A", locked: true },
  ],
  created_at: "2026-11-14T12:00:00-05:00",
  source_run_id: null,
  provenance: { source: "gt_public", source_ref: "x", retrieved_at: null, notes: null, field_sources: {} },
};
const completedRun: OptimizationRun = {
  run_id: "run-1",
  dataset_id: "fall2026",
  dataset_version: "abc",
  state: "completed",
  created_at: "2026-11-14T12:00:00-05:00",
  finished_at: "2026-11-14T12:00:20-05:00",
  precomputed: false,
  label: null,
  config: { weights: W } as unknown as OptimizationRun["config"],
  result: {
    run_id: "run-1",
    config_id: "c",
    status: "FEASIBLE",
    started_at: "2026-11-14T12:00:00-05:00",
    finished_at: "2026-11-14T12:00:20-05:00",
    runtime_seconds: 20.4,
    objective_value: 500,
    best_objective_bound: 100,
    objective_breakdown: {},
    baseline_schedule_id: "baseline",
    optimized_schedule_id: "opt-run-1",
    before: evaluation("baseline", 2),
    after: evaluation("opt-run-1", 1),
    message: "Feasible",
    relaxed: false,
    hypothetical: false,
    strategy: "single_pass",
    warnings: [],
    infeasibility_hints: [],
    model_stats: {},
  },
  schedule: { ...baseline, schedule_id: "opt-run-1", kind: "optimized", source_run_id: "run-1", assignments: [{ exam_group_id: "EG-1", timeslot_id: "TS-B", locked: false }, { exam_group_id: "EG-2", timeslot_id: "TS-A", locked: true }] } as OptimizationRun["schedule"],
  comparison: {
    penalty_before: 2000,
    penalty_after: 1000,
    penalty_percent_change: -50,
    students_improved: 2,
    students_worsened: 0,
    students_unchanged: 0,
    exam_groups_moved: 1,
  } as OptimizationRun["comparison"],
  integrity: { passed: true, checks: [{ name: "x", passed: true, message: "ok" }] },
  error: null,
};

type Handler = (url: string, init?: RequestInit) => unknown;

function mockFetch(routes: Record<string, Handler>) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      const path = url.split("?")[0];
      const h = routes[`${init?.method ?? "GET"} ${path}`];
      if (!h) return new Response(JSON.stringify({ detail: `no mock for ${path}` }), { status: 404 });
      const body = h(url, init);
      if (body instanceof Response) return body;
      return new Response(JSON.stringify(body), { status: 200 });
    }),
  );
  return calls;
}

const baseRoutes = (runs: unknown[] = []): Record<string, Handler> => ({
  "GET /api/datasets": () => [dataset],
  "GET /api/exams": () => exams,
  "GET /api/slots": () => slots,
  "GET /api/schedules/baseline": () => baseline,
  "POST /api/evaluate": () => ({ evaluation: evaluation("baseline", 2), students: null }),
  "GET /api/runs": () => runs,
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.location.hash = "";
});

// ------------------------------------------------------------------ units

describe("format helpers", () => {
  it("formats in campus time regardless of viewer timezone", () => {
    expect(campusTime("2026-12-10T13:00:00Z")).toBe("8:00 AM");
    expect(campusHHMM("2026-12-10T23:30:00Z")).toBe("18:30");
    expect(campusDateKey("2026-12-11T03:00:00Z")).toBe("2026-12-10"); // 10 PM previous day in Atlanta
  });
  it("labels and percentages", () => {
    expect(courseLabel(["CS-1331", "CS-1332", "MATH-1554"], 2)).toBe("CS 1331 / CS 1332 +1");
    expect(pct(null)).toBe("n/a");
    expect(pct(-12.345)).toBe("-12.3%");
  });
});

describe("components", () => {
  it("places overlapping exams side by side and flags them", () => {
    const ex = (id: string, start: string, end: string) =>
      ({ exam_group_id: id, course_ids: [id], start: `2026-12-07T${start}:00-05:00`, end: `2026-12-07T${end}:00-05:00` }) as never;
    const l = layoutDay([ex("a", "08:00", "10:50"), ex("b", "09:00", "11:50"), ex("c", "14:40", "17:30")]);
    expect(l.get("a")).toEqual({ lane: 0, lanes: 2, conflict: true });
    expect(l.get("b")).toEqual({ lane: 1, lanes: 2, conflict: true });
    expect(l.get("c")).toEqual({ lane: 0, lanes: 1, conflict: false });
  });

  it("Delta shows improvement, worsening and undefined percentages", () => {
    const { rerender } = render(<Delta before={10} after={5} />);
    expect(screen.getByText("-50.0%")).toBeInTheDocument();
    rerender(<Delta before={0} after={3} />);
    expect(screen.getByText(/n\/a %/)).toBeInTheDocument();
    rerender(<Delta before={4} after={4} />);
    expect(screen.getByText("no change")).toBeInTheDocument();
  });
  it("Kpi renders baseline and optimized values", () => {
    render(<Kpi label="Back-to-back" baseline={554} optimized={336} unit="pairs" />);
    expect(screen.getByText("336")).toBeInTheDocument();
    expect(screen.getByText("554")).toBeInTheDocument();
  });
});

// ------------------------------------------------------------------ app

describe("App", () => {
  it("loads real backend data and renders baseline metrics", async () => {
    mockFetch(baseRoutes());
    render(<App />);
    expect(await screen.findByText(/face at least one exam-week problem/)).toBeInTheDocument();
    expect(screen.getAllByText(/synthetic/i).length).toBeGreaterThan(0);
    expect(screen.getByText("No optimization run yet")).toBeInTheDocument();
  });

  it("shows a backend connection error with retry", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("Failed to fetch"))));
    render(<App />);
    expect(await screen.findByText("Could not load Hexam data")).toBeInTheDocument();
    expect(screen.getByText(/Cannot reach the Hexam backend/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("shows a completed run with real status and improvement", async () => {
    mockFetch({
      ...baseRoutes([{ run_id: "run-1", state: "completed", penalty_after: 1000 }]),
      "GET /api/runs/run-1": () => completedRun,
    });
    render(<App />);
    expect(await screen.findByText(/lowered total exam-week inconvenience by/)).toBeInTheDocument();
    expect(screen.getByText("50.0%")).toBeInTheDocument();
    expect(screen.getAllByText("FEASIBLE").length).toBeGreaterThan(0);
    expect(screen.getByText(/not claimed to be globally optimal/)).toBeInTheDocument();
  });

  it("downloads the PDF comparison report for the run", async () => {
    const calls = mockFetch({
      ...baseRoutes([{ run_id: "run-1", state: "completed", penalty_after: 1000 }]),
      "GET /api/runs/run-1": () => completedRun,
      "GET /api/runs/run-1/report.pdf": () => new Response(new Blob(["%PDF-1.4"], { type: "application/pdf" }), { status: 200 }),
    });
    const createObjectURL = vi.fn(() => "blob:report");
    Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /Download PDF report/ }));
    await waitFor(() => expect(click).toHaveBeenCalled());
    expect(calls.some((c) => c.url.endsWith("/api/runs/run-1/report.pdf"))).toBe(true);
    expect(createObjectURL).toHaveBeenCalled();
    click.mockRestore();
  });

  it("shows the backend error if the report cannot be generated", async () => {
    mockFetch({
      ...baseRoutes([{ run_id: "run-1", state: "completed", penalty_after: 1000 }]),
      "GET /api/runs/run-1": () => completedRun,
      "GET /api/runs/run-1/report.pdf": () => new Response(JSON.stringify({ detail: "run run-1 has no optimized schedule" }), { status: 409 }),
    });
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /Download PDF report/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("no optimized schedule");
  });

  it("submits the optimization config and displays the result", async () => {
    let posted: Record<string, unknown> | null = null;
    mockFetch({
      ...baseRoutes(),
      "POST /api/optimize": (_u, init) => {
        posted = JSON.parse(String(init?.body));
        return completedRun;
      },
      "GET /api/runs": () => [],
    });
    window.location.hash = "optimize";
    render(<App />);
    const slider = await screen.findByLabelText("Back-to-back exams weight");
    fireEvent.change(slider, { target: { value: "75" } });
    fireEvent.click(screen.getAllByRole("button", { name: /Run optimization/ }).at(-1)!);
    await waitFor(() => expect(posted).not.toBeNull());
    expect((posted as unknown as { weights: { back_to_back: number } }).weights.back_to_back).toBe(75);
    expect((posted as unknown as { hard_constraints: { forbid_simultaneous_conflicts: boolean } }).hard_constraints.forbid_simultaneous_conflicts).toBe(true);
    expect(await screen.findByText("Selected run")).toBeInTheDocument();
  });

  it("shows validation errors from the backend", async () => {
    mockFetch({
      ...baseRoutes(),
      "POST /api/optimize": () => new Response(JSON.stringify({ detail: ["lock moves officially fixed EG-2"] }), { status: 422 }),
    });
    window.location.hash = "optimize";
    render(<App />);
    await screen.findByLabelText("Back-to-back exams weight");
    fireEvent.click(screen.getAllByRole("button", { name: /Run optimization/ }).at(-1)!);
    expect(await screen.findByText("Optimization request failed")).toBeInTheDocument();
    expect(screen.getByText(/lock moves officially fixed/)).toBeInTheDocument();
  });

  it("switches between baseline and optimized schedules", async () => {
    mockFetch({
      ...baseRoutes([{ run_id: "run-1", state: "completed", penalty_after: 1000 }]),
      "GET /api/runs/run-1": () => completedRun,
    });
    window.location.hash = "schedule";
    render(<App />);
    const group = await screen.findByRole("radiogroup", { name: "Schedule view" });
    // compare mode (default with a run): EG-1 moved to TS-B and is marked with its origin
    expect(await screen.findByText(/from Thu 8 AM/)).toBeInTheDocument();
    fireEvent.click(within(group).getByRole("radio", { name: "Baseline" }));
    expect(screen.queryByText(/from Thu/)).not.toBeInTheDocument();
    expect(within(group).getByRole("radio", { name: "Baseline" })).toHaveAttribute("aria-checked", "true");
  });
});
