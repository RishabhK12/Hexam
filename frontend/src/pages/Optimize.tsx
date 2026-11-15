import { useEffect, useState } from "react";
import { Card, ErrorBox, Segmented, Spinner, StatusPill } from "../components/ui";
import { DEFAULT_FORM, useHexam, type OptimizeForm } from "../hooks/useHexam";
import { METRICS, campusDay, campusTime, classNames, courseLabel, num } from "../lib/format";
import type { ImpactMetricKey, OptimizationRun } from "../types/hexam";
import { RunMeta } from "./RunMeta";

const WEIGHT_KEYS: ImpactMetricKey[] = ["three_in_24h", "back_to_back", "four_in_48h", "late_evening", "early_morning"];
/** Per-exam metrics occur thousands of times, so they need small weights and a finer slider. */
const SLIDER: Partial<Record<ImpactMetricKey, { max: number; step: number }>> = {
  late_evening: { max: 50, step: 1 },
  early_morning: { max: 50, step: 1 },
};

export function Optimize() {
  const { form, setForm, runOptimization, job, jobError, activeRun, runs, selectRun, exams, slots } = useHexam();
  const busy = !!job && ["submitting", "queued", "running"].includes(job.state);
  const set = (patch: Partial<OptimizeForm>) => setForm({ ...form, ...patch });
  const hc = form.hard_constraints;
  const setHc = (patch: Partial<typeof hc>) => set({ hard_constraints: { ...hc, ...patch } });

  const [lockGroup, setLockGroup] = useState("");
  const [lockSlot, setLockSlot] = useState("");
  const movable = exams.filter((e) => !e.fixed_timeslot_id || hc.allow_moving_fixed_exams);

  return (
    <div className="grid gap-4 xl:grid-cols-[420px_1fr]">
      <div className="space-y-4">
        <Card title="Objective weights" sub="Penalty per occurrence. Higher = the optimizer works harder to avoid it.">
          <div className="space-y-4">
            {WEIGHT_KEYS.map((k) => {
              const m = METRICS.find((x) => x.key === k)!;
              return (
                <label key={k} className="block">
                  <div className="flex items-baseline justify-between text-sm">
                    <span className="font-medium" title={m.help}>
                      {m.label}
                    </span>
                    <span className="tabular text-muted">
                      {form.weights[k]} <span className="text-xs text-faint">/ {m.unit.replace("exam ", "")}</span>
                    </span>
                  </div>
                  <input
                    type="range"
                    min={0}
                    max={SLIDER[k]?.max ?? 200}
                    step={SLIDER[k]?.step ?? 5}
                    value={form.weights[k]}
                    onChange={(e) => set({ weights: { ...form.weights, [k]: Number(e.target.value) } })}
                    className="mt-1 w-full"
                    aria-label={`${m.label} weight`}
                  />
                </label>
              );
            })}
            <label className={classNames("block", hc.forbid_simultaneous_conflicts && "opacity-50")}>
              <div className="flex items-baseline justify-between text-sm">
                <span className="font-medium">Simultaneous conflicts</span>
                <span className="tabular text-muted">{hc.forbid_simultaneous_conflicts ? "forbidden (hard)" : form.weights.simultaneous_conflicts}</span>
              </div>
              <input
                type="range"
                min={100}
                max={5000}
                step={100}
                disabled={hc.forbid_simultaneous_conflicts}
                value={form.weights.simultaneous_conflicts}
                onChange={(e) => set({ weights: { ...form.weights, simultaneous_conflicts: Number(e.target.value) } })}
                className="mt-1 w-full"
                aria-label="Simultaneous conflict weight"
              />
            </label>
          </div>
        </Card>

        <Card title="Constraints & solver">
          <div className="space-y-4 text-sm">
            <div className="flex items-center justify-between gap-3">
              <span className="font-medium">Student conflicts</span>
              <Segmented
                label="Conflict mode"
                value={hc.forbid_simultaneous_conflicts ? "strict" : "relaxed"}
                onChange={(v) => setHc({ forbid_simultaneous_conflicts: v === "strict" })}
                options={[
                  { value: "strict", label: "Strict (forbid)" },
                  { value: "relaxed", label: "Relaxed (penalize)" },
                ]}
              />
            </div>
            <div className="flex items-center justify-between gap-3">
              <span className="font-medium">Strategy</span>
              <Segmented
                label="Strategy"
                value={form.strategy}
                onChange={(v) => set({ strategy: v })}
                options={[
                  { value: "single_pass", label: "Single pass" },
                  { value: "two_phase", label: "Two-phase" },
                ]}
              />
            </div>
            <label className="block">
              <div className="flex justify-between">
                <span className="font-medium">Time limit</span>
                <span className="tabular text-muted">{form.time_limit_seconds} s</span>
              </div>
              <input type="range" min={5} max={120} step={5} value={form.time_limit_seconds} onChange={(e) => set({ time_limit_seconds: Number(e.target.value) })} className="mt-1 w-full" aria-label="Time limit" />
            </label>
            <div className="grid grid-cols-2 gap-3">
              <label className="flex flex-col gap-1">
                <span className="label">Random seed</span>
                <input className="input" type="number" min={0} value={form.random_seed} onChange={(e) => set({ random_seed: Math.max(0, Number(e.target.value) || 0) })} />
              </label>
              <label className="flex flex-col gap-1">
                <span className="label">Max exams / day</span>
                <input
                  className="input"
                  type="number"
                  min={1}
                  placeholder="none"
                  value={hc.max_exams_per_student_per_day ?? ""}
                  onChange={(e) => setHc({ max_exams_per_student_per_day: e.target.value ? Math.max(1, Number(e.target.value)) : null })}
                />
              </label>
              <label className="flex flex-col gap-1">
                <span className="label" title="Aggregate cap on students sitting exams in one period (all rooms together). Not a room assignment.">
                  Seats / period
                </span>
                <input
                  className="input"
                  type="number"
                  min={1}
                  placeholder="no data"
                  value={hc.max_students_per_slot ?? ""}
                  onChange={(e) => setHc({ max_students_per_slot: e.target.value ? Math.max(1, Number(e.target.value)) : null })}
                />
              </label>
              <label className="flex flex-col gap-1">
                <span className="label">Groups / period</span>
                <input
                  className="input"
                  type="number"
                  min={1}
                  placeholder="no limit"
                  value={hc.max_exam_groups_per_slot ?? ""}
                  onChange={(e) => setHc({ max_exam_groups_per_slot: e.target.value ? Math.max(1, Number(e.target.value)) : null })}
                />
              </label>
            </div>
            <p className="text-xs text-faint">No real room capacities are available, so capacity is off by default. Caps above are aggregate, not room assignments.</p>
            <label className="flex items-start gap-2 rounded-lg border border-warn/30 bg-warn/5 p-3">
              <input type="checkbox" className="mt-0.5" checked={hc.allow_moving_fixed_exams} onChange={(e) => setHc({ allow_moving_fixed_exams: e.target.checked })} />
              <span>
                <span className="font-medium">Hypothetical: allow moving official common exams</span>
                <span className="block text-xs text-muted">Results are labeled HYPOTHETICAL. Off = registrar-fixed exams stay put.</span>
              </span>
            </label>
          </div>
        </Card>

        <Card title="Lock exam groups" sub="Pin a group to a period before optimizing (validated by the backend).">
          <div className="flex gap-2">
            <select className="input" value={lockGroup} onChange={(e) => setLockGroup(e.target.value)} aria-label="Exam group to lock">
              <option value="">Exam group…</option>
              {movable.map((e) => (
                <option key={e.exam_group_id} value={e.exam_group_id}>
                  {courseLabel(e.course_ids, 2)} ({e.student_count})
                </option>
              ))}
            </select>
            <select className="input" value={lockSlot} onChange={(e) => setLockSlot(e.target.value)} aria-label="Period">
              <option value="">Period…</option>
              {slots.map((s) => (
                <option key={s.timeslot_id} value={s.timeslot_id}>
                  {campusDay(s.start)} {campusTime(s.start)}
                </option>
              ))}
            </select>
            <button
              className="btn-ghost"
              disabled={!lockGroup || !lockSlot}
              onClick={() => {
                set({
                  fixed_assignments: [...form.fixed_assignments.filter((f) => f.exam_group_id !== lockGroup), { exam_group_id: lockGroup, timeslot_id: lockSlot, reason: "user lock" }],
                });
                setLockGroup("");
              }}
            >
              Add
            </button>
          </div>
          {form.fixed_assignments.length > 0 && (
            <ul className="mt-3 space-y-1 text-sm">
              {form.fixed_assignments.map((f) => {
                const e = exams.find((x) => x.exam_group_id === f.exam_group_id);
                const s = slots.find((x) => x.timeslot_id === f.timeslot_id);
                return (
                  <li key={f.exam_group_id} className="flex items-center justify-between rounded-md bg-raised px-2.5 py-1.5">
                    <span>
                      🔒 {e ? courseLabel(e.course_ids, 2) : f.exam_group_id} → {s ? `${campusDay(s.start)} ${campusTime(s.start)}` : f.timeslot_id}
                    </span>
                    <button className="text-xs text-muted hover:text-bad" onClick={() => set({ fixed_assignments: form.fixed_assignments.filter((x) => x !== f) })}>
                      Remove
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </Card>

        <div className="flex gap-2">
          <button className="btn-primary flex-1 py-2.5" onClick={() => runOptimization()} disabled={busy}>
            {busy ? <Spinner /> : <span aria-hidden>▶</span>} {busy ? "Optimizing…" : "Run optimization"}
          </button>
          <button className="btn-ghost" onClick={() => setForm(DEFAULT_FORM)} disabled={busy}>
            Reset
          </button>
        </div>
      </div>

      <div className="space-y-4">
        <JobStatus />
        {jobError && <ErrorBox title="Optimization request failed" problems={jobError} />}
        {activeRun && <RunDetails run={activeRun} />}
        <Card title="Run history" sub="Kept in memory on the backend. Each run is scored with its own weights, so compare scores only between runs with the same weights.">
          {runs.length === 0 ? (
            <p className="text-sm text-muted">No runs yet.</p>
          ) : (
            <ul className="divide-y divide-line text-sm">
              {runs.map((r) => (
                <li key={r.run_id} className="flex items-center justify-between gap-3 py-2">
                  <div className="min-w-0">
                    <p className="truncate font-mono text-xs">{r.run_id}</p>
                    <p className="text-xs text-faint">
                      {r.precomputed ? "precomputed · " : ""}
                      {campusDay(r.created_at)} {campusTime(r.created_at)}
                      {r.runtime_seconds != null && ` · ${r.runtime_seconds.toFixed(1)} s`}
                    </p>
                  </div>
                  <div className="flex items-center gap-3">
                    {r.status ? <StatusPill status={r.status} /> : <span className="chip border-line text-muted">{r.state}</span>}
                    <span className="tabular w-28 text-right text-xs text-muted">
                      {r.penalty_after != null ? `${num(r.penalty_before)} → ${num(r.penalty_after)}` : "—"}
                    </span>
                    <button className="btn-ghost px-2 py-1 text-xs" disabled={activeRun?.run_id === r.run_id || r.state !== "completed"} onClick={() => selectRun(r.run_id)}>
                      {activeRun?.run_id === r.run_id ? "Viewing" : "View"}
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}

function JobStatus() {
  const { job } = useHexam();
  const [now, setNow] = useState(Date.now());
  const active = !!job && ["submitting", "queued", "running"].includes(job.state);
  useEffect(() => {
    if (!active) return;
    const t = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(t);
  }, [active]);
  if (!job || !active) return null;
  const elapsed = (now - job.startedAt) / 1000;
  const frac = Math.min(1, elapsed / (job.timeLimit + 2));
  return (
    <div className="card card-pad" aria-live="polite">
      <div className="flex items-center gap-3">
        <Spinner className="text-after" />
        <div className="flex-1">
          <p className="font-semibold">
            {job.state === "queued" ? "Queued behind another run…" : job.state === "submitting" ? "Validating request…" : "CP-SAT is searching for a better schedule"}
          </p>
          <p className="text-sm text-muted">
            {elapsed.toFixed(1)} s elapsed · time limit {job.timeLimit} s · the solver stops early if it proves optimality
          </p>
        </div>
      </div>
      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-raised">
        <div className="h-full rounded-full bg-after transition-all" style={{ width: `${frac * 100}%` }} />
      </div>
      <p className="mt-2 text-xs text-faint">The bar is elapsed time against the limit, not solution progress.</p>
    </div>
  );
}

function RunDetails({ run }: { run: OptimizationRun }) {
  const r = run.result;
  if (run.state === "failed") return <ErrorBox title={`Run ${run.run_id} failed`} problems={[run.error ?? "unknown error"]} />;
  if (!r) return null;
  const solved = !!r.after;
  return (
    <Card title="Selected run" sub={<span className="font-mono">{run.run_id}</span>} right={<StatusPill status={r.status} relaxed={r.relaxed} hypothetical={r.hypothetical} />}>
      <div className="grid gap-6 md:grid-cols-2">
        <RunMeta run={run} />
        <div className="space-y-3 text-sm">
          {!solved && (
            <ErrorBox
              title={r.status === "INFEASIBLE" ? "No schedule satisfies every hard constraint" : `Solver status ${r.status}: no schedule produced`}
              problems={r.infeasibility_hints.length ? r.infeasibility_hints : [r.message ?? "No diagnostics available."]}
            />
          )}
          {run.integrity && (
            <div>
              <p className="label mb-1.5">Independent checks</p>
              <ul className="space-y-1">
                {run.integrity.checks.map((c) => (
                  <li key={c.name} className="flex gap-2 text-xs">
                    <span className={c.passed ? "text-good" : "text-bad"} aria-label={c.passed ? "passed" : "failed"}>
                      {c.passed ? "✓" : "✕"}
                    </span>
                    <span className="text-muted">{c.message}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {r.warnings.length > 0 && (
            <div>
              <p className="label mb-1.5">Notes & warnings</p>
              <ul className="list-disc space-y-1 pl-4 text-xs text-muted">
                {r.warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </div>
          )}
          <details className="text-xs text-muted">
            <summary className="cursor-pointer">Model size & weights</summary>
            <p className="mt-1 font-mono">
              {Object.entries(r.model_stats)
                .map(([k, v]) => `${k}=${v}`)
                .join(" · ")}
            </p>
            <p className="mt-1 font-mono">
              {Object.entries(run.config.weights)
                .map(([k, v]) => `${k}=${v}`)
                .join(" · ")}
            </p>
          </details>
        </div>
      </div>
    </Card>
  );
}
