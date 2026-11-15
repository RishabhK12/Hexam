/** Typed client for the Hexam FastAPI backend (types mirror backend/hexam/schemas). */
import type {
  CompareRequest,
  DatasetInfo,
  EvaluateResponse,
  EvaluateScheduleRequest,
  ExamGroupView,
  ExamSchedule,
  ExamTimeslot,
  HealthResponse,
  OptimizationRun,
  OptimizeRequest,
  RunSummary,
  ScheduleComparison,
  StudentDetailView,
  StudentImpactResponse,
  StudentPage,
  StudentRankItem,
} from "../types/hexam";

const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

export class ApiError extends Error {
  constructor(
    public status: number,
    public problems: string[],
  ) {
    super(problems.join("; ") || `HTTP ${status}`);
  }
}

function detailToProblems(detail: unknown): string[] {
  if (detail == null) return [];
  if (typeof detail === "string") return [detail];
  if (Array.isArray(detail))
    return detail.map((d) => {
      if (typeof d === "string") return d;
      if (d && typeof d === "object" && "msg" in d) {
        const loc = Array.isArray((d as { loc?: unknown[] }).loc) ? (d as { loc: unknown[] }).loc.slice(1).join(".") : "";
        return `${loc ? loc + ": " : ""}${(d as { msg: string }).msg}`;
      }
      return JSON.stringify(d);
    });
  return [JSON.stringify(detail)];
}

async function request<T>(path: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  let res: Response;
  try {
    res = await fetch(BASE + path, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
      body: init?.json !== undefined ? JSON.stringify(init.json) : init?.body,
    });
  } catch {
    throw new ApiError(0, ["Cannot reach the Hexam backend. Is it running on port 8000?"]);
  }
  if (!res.ok) {
    let problems: string[] = [];
    try {
      problems = detailToProblems((await res.json()).detail);
    } catch {
      problems = [res.statusText];
    }
    throw new ApiError(res.status, problems);
  }
  return (await res.json()) as T;
}

const qs = (params: Record<string, string | number | undefined | null>) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
};

export const api = {
  health: () => request<HealthResponse>("/api/health"),
  datasets: () => request<DatasetInfo[]>("/api/datasets"),
  exams: (dataset_id: string) => request<ExamGroupView[]>(`/api/exams${qs({ dataset_id })}`),
  slots: (dataset_id: string) => request<ExamTimeslot[]>(`/api/slots${qs({ dataset_id })}`),
  baseline: (dataset_id: string) => request<ExamSchedule>(`/api/schedules/baseline${qs({ dataset_id })}`),
  evaluate: (body: Partial<EvaluateScheduleRequest>) => request<EvaluateResponse>("/api/evaluate", { method: "POST", json: body }),
  students: (p: { dataset_id: string; q?: string; major?: string; year?: string; course?: string; offset?: number; limit?: number }) =>
    request<StudentPage>(`/api/students${qs(p)}`),
  student: (dataset_id: string, id: string) => request<StudentDetailView>(`/api/students/${encodeURIComponent(id)}${qs({ dataset_id })}`),
  studentImpact: (dataset_id: string, id: string, run_id?: string | null) =>
    request<StudentImpactResponse>(`/api/students/${encodeURIComponent(id)}/impact${qs({ dataset_id, run_id })}`),
  optimize: (body: Partial<OptimizeRequest>) => request<OptimizationRun>("/api/optimize", { method: "POST", json: body }),
  runs: (dataset_id: string) => request<RunSummary[]>(`/api/runs${qs({ dataset_id })}`),
  run: (run_id: string) => request<OptimizationRun>(`/api/runs/${encodeURIComponent(run_id)}`),
  runStudents: (run_id: string, order: "improved" | "worsened" | "remaining" | "baseline", limit = 25) =>
    request<StudentRankItem[]>(`/api/runs/${encodeURIComponent(run_id)}/students${qs({ order, limit })}`),
  compare: (dataset_id: string, run_id: string) => request<ScheduleComparison>(`/api/compare${qs({ dataset_id, run_id })}`),
  compareAdhoc: (body: Partial<CompareRequest>) => request<ScheduleComparison>("/api/compare", { method: "POST", json: body }),
};
