import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ApiError, api } from "../services/api";
import type {
  DatasetInfo,
  EvaluateResponse,
  ExamGroupView,
  ExamSchedule,
  ExamTimeslot,
  FixedAssignment,
  HardConstraints,
  OptimizationRun,
  OptimizationStrategy,
  PenaltyWeights,
  RunSummary,
} from "../types/hexam";

export const DEFAULT_WEIGHTS: PenaltyWeights = {
  simultaneous_conflicts: 1000,
  three_in_24h: 100,
  back_to_back: 30,
  four_in_48h: 15,
  night_to_morning: 10,
};

export const DEFAULT_HARD: HardConstraints = {
  forbid_simultaneous_conflicts: true,
  max_exams_per_student_per_slot: 2,
  max_exams_per_student_per_day: null,
  enforce_timeslot_capacity: true,
  max_students_per_slot: null,
  max_exam_groups_per_slot: null,
  enforce_group_restrictions: true,
  allow_moving_fixed_exams: false,
};

export interface OptimizeForm {
  weights: PenaltyWeights;
  time_limit_seconds: number;
  random_seed: number;
  strategy: OptimizationStrategy;
  hard_constraints: HardConstraints;
  fixed_assignments: FixedAssignment[];
}

export const DEFAULT_FORM: OptimizeForm = {
  weights: DEFAULT_WEIGHTS,
  time_limit_seconds: 20,
  random_seed: 0,
  strategy: "single_pass",
  hard_constraints: DEFAULT_HARD,
  fixed_assignments: [],
};

export interface Job {
  runId: string | null;
  state: "submitting" | "queued" | "running" | "completed" | "failed";
  startedAt: number;
  timeLimit: number;
}

interface Ctx {
  status: "loading" | "ready" | "error";
  loadError: string[] | null;
  retry: () => void;
  datasets: DatasetInfo[];
  datasetId: string;
  setDatasetId: (id: string) => void;
  info: DatasetInfo | null;
  exams: ExamGroupView[];
  examById: Map<string, ExamGroupView>;
  slots: ExamTimeslot[];
  slotById: Map<string, ExamTimeslot>;
  baseline: ExamSchedule | null;
  baselineEval: EvaluateResponse | null;
  runs: RunSummary[];
  activeRun: OptimizationRun | null;
  selectRun: (id: string | null) => Promise<void>;
  form: OptimizeForm;
  setForm: (f: OptimizeForm) => void;
  job: Job | null;
  jobError: string[] | null;
  runOptimization: () => Promise<void>;
}

const HexamContext = createContext<Ctx | null>(null);

export function useHexam(): Ctx {
  const c = useContext(HexamContext);
  if (!c) throw new Error("useHexam must be used inside <HexamProvider>");
  return c;
}

const problemsOf = (e: unknown) => (e instanceof ApiError ? e.problems : [String(e)]);

export function HexamProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Ctx["status"]>("loading");
  const [loadError, setLoadError] = useState<string[] | null>(null);
  const [nonce, setNonce] = useState(0);
  const [datasets, setDatasets] = useState<DatasetInfo[]>([]);
  const [datasetId, setDatasetId] = useState("fall2026");
  const [exams, setExams] = useState<ExamGroupView[]>([]);
  const [slots, setSlots] = useState<ExamTimeslot[]>([]);
  const [baseline, setBaseline] = useState<ExamSchedule | null>(null);
  const [baselineEval, setBaselineEval] = useState<EvaluateResponse | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [activeRun, setActiveRun] = useState<OptimizationRun | null>(null);
  const [form, setForm] = useState<OptimizeForm>(DEFAULT_FORM);
  const [job, setJob] = useState<Job | null>(null);
  const [jobError, setJobError] = useState<string[] | null>(null);
  const pollRef = useRef<number | null>(null);

  // Load everything for the selected dataset.
  useEffect(() => {
    let cancelled = false;
    setStatus("loading");
    setLoadError(null);
    (async () => {
      try {
        const ds = await api.datasets();
        if (cancelled) return;
        setDatasets(ds);
        const id = ds.some((d) => d.dataset_id === datasetId) ? datasetId : ds[0]?.dataset_id;
        if (!id) throw new ApiError(404, ["No datasets are available on the backend."]);
        if (id !== datasetId) {
          setDatasetId(id);
          return;
        }
        const baseId = ds.find((d) => d.dataset_id === id)?.baseline_schedule_id;
        if (!baseId) throw new ApiError(404, [`Dataset ${id} has no baseline schedule.`]);
        const [ex, sl, base, evb, rs] = await Promise.all([
          api.exams(id),
          api.slots(id),
          api.baseline(id),
          // baseline scored by the backend's independent evaluator (default weights)
          api.evaluate({ dataset_id: id, schedule_id: baseId }),
          api.runs(id),
        ]);
        if (cancelled) return;
        setExams(ex);
        setSlots(sl);
        setBaseline(base);
        setRuns(rs);
        setBaselineEval(evb);
        const latest = rs.find((r) => r.state === "completed" && r.penalty_after != null);
        setActiveRun(latest ? await api.run(latest.run_id) : null);
        if (!cancelled) setStatus("ready");
      } catch (e) {
        if (!cancelled) {
          setLoadError(problemsOf(e));
          setStatus("error");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [datasetId, nonce]);

  useEffect(() => () => {
    if (pollRef.current) window.clearInterval(pollRef.current);
  }, []);

  const selectRun = useCallback(async (id: string | null) => {
    if (!id) return setActiveRun(null);
    setActiveRun(await api.run(id));
  }, []);

  const runOptimization = useCallback(async () => {
    if (job && (job.state === "queued" || job.state === "running" || job.state === "submitting")) return;
    setJobError(null);
    const startedAt = Date.now();
    setJob({ runId: null, state: "submitting", startedAt, timeLimit: form.time_limit_seconds });
    let run: OptimizationRun;
    try {
      run = await api.optimize({
        dataset_id: datasetId,
        weights: form.weights,
        time_limit_seconds: form.time_limit_seconds,
        random_seed: form.random_seed,
        strategy: form.strategy,
        hard_constraints: form.hard_constraints,
        fixed_assignments: form.fixed_assignments,
      });
    } catch (e) {
      setJob({ runId: null, state: "failed", startedAt, timeLimit: form.time_limit_seconds });
      setJobError(problemsOf(e));
      return;
    }
    setJob({ runId: run.run_id, state: run.state, startedAt, timeLimit: form.time_limit_seconds });
    const finish = async (r: OptimizationRun) => {
      if (pollRef.current) window.clearInterval(pollRef.current);
      pollRef.current = null;
      setJob({ runId: r.run_id, state: r.state, startedAt, timeLimit: form.time_limit_seconds });
      if (r.state === "failed") setJobError([r.error ?? "The optimization run failed."]);
      setActiveRun(r);
      setRuns(await api.runs(datasetId));
    };
    if (run.state === "completed" || run.state === "failed") return finish(run);
    pollRef.current = window.setInterval(async () => {
      try {
        const r = await api.run(run.run_id);
        if (r.state === "completed" || r.state === "failed") await finish(r);
        else setJob((j) => (j ? { ...j, state: r.state } : j));
      } catch (e) {
        if (pollRef.current) window.clearInterval(pollRef.current);
        pollRef.current = null;
        setJob((j) => (j ? { ...j, state: "failed" } : j));
        setJobError(problemsOf(e));
      }
    }, 700);
  }, [datasetId, form, job]);

  const value = useMemo<Ctx>(
    () => ({
      status,
      loadError,
      retry: () => setNonce((n) => n + 1),
      datasets,
      datasetId,
      setDatasetId,
      info: datasets.find((d) => d.dataset_id === datasetId) ?? null,
      exams,
      examById: new Map(exams.map((e) => [e.exam_group_id, e])),
      slots,
      slotById: new Map(slots.map((s) => [s.timeslot_id, s])),
      baseline,
      baselineEval,
      runs,
      activeRun,
      selectRun,
      form,
      setForm,
      job,
      jobError,
      runOptimization,
    }),
    [status, loadError, datasets, datasetId, exams, slots, baseline, baselineEval, runs, activeRun, selectRun, form, job, jobError, runOptimization],
  );
  return <HexamContext.Provider value={value}>{children}</HexamContext.Provider>;
}

/** The run's optimized schedule mapping, if it produced one. */
export function optimizedMapping(run: OptimizationRun | null): Map<string, string> | null {
  if (!run?.schedule) return null;
  return new Map(run.schedule.assignments.map((a) => [a.exam_group_id, a.timeslot_id]));
}
