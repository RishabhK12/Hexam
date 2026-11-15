import { ReportButton } from "../components/ReportButton";
import { StatusPill } from "../components/ui";
import { campusDay, campusTime, num } from "../lib/format";
import type { OptimizationRun } from "../types/hexam";

/** Honest solver facts for a run: status, runtime, objective vs bound, provenance. */
export function RunMeta({ run, compact = false }: { run: OptimizationRun; compact?: boolean }) {
  const r = run.result;
  if (!r) return null;
  const gap =
    r.objective_value != null && r.best_objective_bound != null && r.objective_value > 0
      ? (100 * (r.objective_value - r.best_objective_bound)) / r.objective_value
      : null;
  return (
    <div className="space-y-3 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-semibold">{run.precomputed ? "Precomputed run" : "Latest run"}</span>
        <StatusPill status={r.status} relaxed={r.relaxed} hypothetical={r.hypothetical} />
      </div>
      {run.precomputed && (
        <p className="rounded-md border border-gold/40 bg-gold/10 px-2.5 py-1.5 text-xs text-ink/80">
          Saved from a real CP-SAT run on this exact dataset version and re-verified on load. Click “Re-run optimization” to compute a fresh one.
        </p>
      )}
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5">
        <dt className="text-muted">Solver runtime</dt>
        <dd className="tabular text-right font-medium">{r.runtime_seconds.toFixed(1)} s</dd>
        <dt className="text-muted">Evaluated score</dt>
        <dd className="tabular text-right font-medium">{num(r.after?.weighted_penalty)}</dd>
        <dt className="text-muted">Solver objective</dt>
        <dd className="tabular text-right">{num(r.objective_value)}</dd>
        <dt className="text-muted">Best bound</dt>
        <dd className="tabular text-right">{r.best_objective_bound == null ? "not reported" : num(r.best_objective_bound)}</dd>
        {!compact && gap != null && (
          <>
            <dt className="text-muted">Optimality gap</dt>
            <dd className="tabular text-right">{gap.toFixed(1)}%</dd>
          </>
        )}
        <dt className="text-muted">Strategy</dt>
        <dd className="text-right">{r.strategy === "two_phase" ? "Two-phase" : "Single pass"}</dd>
        <dt className="text-muted">Finished</dt>
        <dd className="text-right">
          {campusDay(r.finished_at)}, {campusTime(r.finished_at)}
        </dd>
      </dl>
      <p className="text-xs leading-relaxed text-muted">
        {r.status === "OPTIMAL"
          ? "CP-SAT proved this schedule optimal for the model."
          : r.status === "FEASIBLE"
            ? "A valid schedule, but the time limit was reached before optimality was proven. It is not claimed to be globally optimal."
            : r.message}
      </p>
      {run.integrity && (
        <p className={`text-xs ${run.integrity.passed ? "text-good" : "text-bad"}`}>
          {run.integrity.passed ? "✓" : "✕"} {run.integrity.checks.filter((c) => c.passed).length}/{run.integrity.checks.length} independent integrity checks
          passed
        </p>
      )}
      {!compact && <ReportButton run={run} />}
    </div>
  );
}
