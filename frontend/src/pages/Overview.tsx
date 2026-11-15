import { useMemo } from "react";
import { PairBars, type PairRow } from "../components/charts";
import { ReportButton } from "../components/ReportButton";
import { Card, Delta, Empty, Kpi, Legend, Spinner } from "../components/ui";
import { useHexam } from "../hooks/useHexam";
import { METRICS, campusDay, dayFromKey, num, pct, shortDay, campusTime, weekday } from "../lib/format";
import type { ScheduleEvaluation } from "../types/hexam";
import { RunMeta } from "./RunMeta";

export function Overview({ go }: { go: (tab: string) => void }) {
  const { info, baselineEval, activeRun, slots, job, runOptimization, exams } = useHexam();
  const run = activeRun?.result?.after ? activeRun : null;
  const before: ScheduleEvaluation | null = run?.result?.before ?? baselineEval?.evaluation ?? null;
  const after: ScheduleEvaluation | null = run?.result?.after ?? null;
  const cmp = run?.comparison ?? null;
  const busy = job && ["submitting", "queued", "running"].includes(job.state);

  const affectedRows: PairRow[] = useMemo(
    () =>
      before
        ? METRICS.map((m) => ({
            label: m.short,
            detail: m.label,
            base: before.metrics[m.key].students_affected,
            opt: after ? after.metrics[m.key].students_affected : null,
          }))
        : [],
    [before, after],
  );
  const penaltyRows: PairRow[] = useMemo(
    () =>
      before
        ? METRICS.map((m) => ({
            label: m.short,
            detail: `weight ${before.weights_used[m.key] ?? "?"} × ${m.unit}`,
            base: before.penalty_breakdown[m.key] ?? 0,
            opt: after ? (after.penalty_breakdown[m.key] ?? 0) : null,
          }))
        : [],
    [before, after],
  );
  const dayRows: PairRow[] = useMemo(() => {
    if (!before) return [];
    const a = new Map(after?.day_loads.map((d) => [d.day, d]) ?? []);
    return before.day_loads.map((d) => ({
      label: dayFromKey(d.day),
      detail: "student-exam sittings",
      base: d.student_exam_count,
      opt: after ? (a.get(d.day)?.student_exam_count ?? 0) : null,
    }));
  }, [before, after]);
  const slotRows: PairRow[] = useMemo(() => {
    if (!before) return [];
    const byId = new Map(slots.map((s) => [s.timeslot_id, s]));
    const a = new Map(after?.timeslot_loads.map((d) => [d.timeslot_id, d]) ?? []);
    return [...before.timeslot_loads]
      .sort((x, y) => (byId.get(x.timeslot_id)?.start ?? "").localeCompare(byId.get(y.timeslot_id)?.start ?? ""))
      .map((d) => {
        const s = byId.get(d.timeslot_id);
        return {
          label: s ? `${campusDay(s.start)} · ${campusTime(s.start)}` : d.timeslot_id,
          group: s ? weekday(s.start) : undefined,
          groupSub: s ? shortDay(s.start) : undefined,
          minor: s ? campusTime(s.start).replace(/ [AP]M$/, "") : undefined,
          detail: s ? `${d.exam_group_count} → ${a.get(d.timeslot_id)?.exam_group_count ?? "–"} exam groups` : undefined,
          base: d.student_count,
          opt: after ? (a.get(d.timeslot_id)?.student_count ?? 0) : null,
        };
      });
  }, [before, after, slots]);

  if (!before || !info) return <Empty>No evaluation available.</Empty>;
  const m = before.metrics;
  const fixedCount = exams.filter((e) => e.fixed_timeslot_id).length;

  return (
    <div className="space-y-6">
      {/* Hero */}
      <section className="card overflow-hidden">
        <div className="grid gap-6 p-6 md:grid-cols-[1.4fr_1fr] md:p-8">
          <div>
            <p className="label text-gold">Impact summary · {info.term ?? info.dataset_id}</p>
            {cmp ? (
              <h2 className="mt-2 text-2xl font-semibold leading-tight tracking-tight md:text-[28px]">
                Hexam lowered total exam-week inconvenience by{" "}
                <span className="text-after">{pct(cmp.penalty_percent_change).replace("-", "")}</span> for {num(m.students_evaluated)} synthetic students
                {after!.metrics.simultaneous_conflicts.count === 0 && m.simultaneous_conflicts.count > 0 ? ", with zero exam conflicts." : "."}
              </h2>
            ) : (
              <h2 className="mt-2 text-2xl font-semibold leading-tight tracking-tight md:text-[28px]">
                Under the official matrix, <span className="text-before">{num(m.students_with_any_issue)}</span> of {num(m.students_evaluated)} synthetic students face at least one exam-week
                problem.
              </h2>
            )}
            <p className="sub mt-3 max-w-2xl leading-relaxed">
              Georgia Tech assigns finals from a fixed matrix keyed to class meeting times. Hexam keeps the {fixedCount} official common exams fixed and
              re-assigns the remaining exam groups to the same official periods with OR-Tools CP-SAT, minimizing congestion for the students actually
              enrolled together. Every number here is computed by the independent evaluator on the same synthetic students.
            </p>
            <div className="mt-5 flex flex-wrap items-center gap-3">
              <button className="btn-primary" onClick={() => runOptimization()} disabled={!!busy}>
                {busy ? <Spinner /> : <span aria-hidden>▶</span>}
                {busy ? "Optimizing…" : cmp ? "Re-run optimization" : "Run optimization"}
              </button>
              <button className="btn-ghost" onClick={() => go("optimize")}>
                Adjust weights
              </button>
              <button className="btn-ghost" onClick={() => go("students")}>
                Explore a student
              </button>
              {cmp && run && <ReportButton run={run} />}
            </div>
          </div>
          <div className="rounded-xl border border-line bg-raised p-5">
            {cmp && run ? (
              <RunMeta run={run} compact />
            ) : (
              <div className="space-y-2 text-sm">
                <p className="font-semibold">No optimization run yet</p>
                <p className="text-muted">Showing the baseline only. Run the optimizer to compare.</p>
              </div>
            )}
          </div>
        </div>
      </section>

      {/* KPIs */}
      <section className="grid grid-cols-2 gap-4 lg:grid-cols-3 xl:grid-cols-6" aria-label="Key metrics">
        <Kpi label="Students evaluated" baseline={m.students_evaluated} unit="synthetic students" />
        <Kpi label="Exam groups scheduled" baseline={info.exam_group_count} unit={`${info.timeslot_count} official periods · ${fixedCount} fixed common exams`} />
        <Kpi
          label="Simultaneous conflicts"
          baseline={m.simultaneous_conflicts.count}
          optimized={after?.metrics.simultaneous_conflicts.count}
          unit={`overlapping exam pairs · ${num(m.simultaneous_conflicts.students_affected)} students in baseline`}
        />
        <Kpi
          label="Students with 3 exams in 24 h"
          baseline={m.three_in_24h.students_affected}
          optimized={after?.metrics.three_in_24h.students_affected}
          unit="students (incl. 3+ in one day)"
        />
        <Kpi label="Back-to-back exams" baseline={m.back_to_back.count} optimized={after?.metrics.back_to_back.count} unit="exam pairs ≤ 30 min apart" />
        <Kpi
          label="Inconvenience score"
          baseline={before.weighted_penalty}
          optimized={after?.weighted_penalty}
          percent={cmp?.penalty_percent_change}
          unit="Σ weight × count (lower is better)"
        />
      </section>

      {cmp && (
        <section className="grid gap-4 md:grid-cols-4">
          <MiniStat label="Students better off" value={cmp.students_improved} tone="good" total={m.students_evaluated} />
          <MiniStat label="Students worse off" value={cmp.students_worsened} tone="bad" total={m.students_evaluated} />
          <MiniStat label="Unchanged" value={cmp.students_unchanged} total={m.students_evaluated} />
          <MiniStat label="Exam groups moved" value={cmp.exam_groups_moved} total={info.exam_group_count} />
        </section>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Students experiencing each problem" sub="Distinct students affected (one axis, unit: students)" right={<Legend />}>
          <PairBars data={affectedRows} unit="students" horizontal height={280} />
        </Card>
        <Card title="Where the inconvenience score comes from" sub="Weighted penalty points per metric (weight × count)" right={<Legend />}>
          <PairBars data={penaltyRows} unit="penalty points" horizontal height={280} />
        </Card>
        <Card title="Exam load by day" sub="Student-exam sittings per final-exam day" right={<Legend />}>
          <PairBars data={dayRows} unit="sittings" height={260} />
        </Card>
        <Card title="Concentration across periods" sub="Distinct students sitting an exam in each official period, grouped by day (periods start 8:00, 11:20, 2:40 and 6:00)" right={<Legend />}>
          <PairBars data={slotRows} unit="students" height={260} />
        </Card>
      </div>

      <Card title="All metrics" sub="Counts and students affected, baseline vs optimized — same students, same evaluator, same weights">
        <MetricsTable before={before} after={after} />
      </Card>
    </div>
  );
}

function MiniStat({ label, value, total, tone }: { label: string; value: number; total: number; tone?: "good" | "bad" }) {
  return (
    <div className="card flex flex-col gap-1 p-4">
      <span className="text-[13px] text-muted">{label}</span>
      <span className={`tabular whitespace-nowrap text-xl font-semibold ${tone === "good" ? "text-good" : tone === "bad" ? "text-bad" : ""}`}>
        {num(value)}
        <span className="ml-1 text-xs font-normal text-faint">/ {num(total)}</span>
      </span>
    </div>
  );
}

export function MetricsTable({ before, after }: { before: ScheduleEvaluation; after: ScheduleEvaluation | null }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] text-sm">
        <thead>
          <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-faint">
            <th className="py-2 pr-4 font-medium">Metric</th>
            <th className="py-2 pr-4 font-medium">Unit</th>
            <th className="py-2 pr-4 text-right font-medium">Baseline</th>
            {after && <th className="py-2 pr-4 text-right font-medium">Optimized</th>}
            {after && <th className="py-2 pr-4 text-right font-medium">Change</th>}
            <th className="py-2 pr-4 text-right font-medium">Students (base{after ? " → opt" : ""})</th>
            <th className="py-2 text-right font-medium">Weight</th>
          </tr>
        </thead>
        <tbody>
          {METRICS.map((m) => {
            const b = before.metrics[m.key];
            const a = after?.metrics[m.key];
            return (
              <tr key={m.key} className="border-b border-line/60 last:border-0">
                <td className="py-2.5 pr-4 font-medium" title={m.help}>
                  {m.label}
                </td>
                <td className="py-2.5 pr-4 text-muted">{m.unit}</td>
                <td className="tabular py-2.5 pr-4 text-right">{num(b.count)}</td>
                {a && <td className="tabular py-2.5 pr-4 text-right font-semibold">{num(a.count)}</td>}
                {a && (
                  <td className="py-2.5 pr-4 text-right">
                    <Delta before={b.count} after={a.count} />
                  </td>
                )}
                <td className="tabular py-2.5 pr-4 text-right text-muted">
                  {num(b.students_affected)}
                  {a && <> → {num(a.students_affected)}</>}
                </td>
                <td className="tabular py-2.5 text-right text-muted">{before.weights_used[m.key] ?? "—"}</td>
              </tr>
            );
          })}
          <tr className="bg-raised/60">
            <td className="py-2.5 pr-4 font-semibold">Total inconvenience score</td>
            <td className="py-2.5 pr-4 text-muted">points</td>
            <td className="tabular py-2.5 pr-4 text-right">{num(before.weighted_penalty)}</td>
            {after && <td className="tabular py-2.5 pr-4 text-right font-semibold">{num(after.weighted_penalty)}</td>}
            {after && (
              <td className="py-2.5 pr-4 text-right">
                <Delta before={before.weighted_penalty ?? 0} after={after.weighted_penalty ?? 0} />
              </td>
            )}
            <td className="tabular py-2.5 pr-4 text-right text-muted">
              {num(before.metrics.students_with_any_issue)}
              {after && <> → {num(after.metrics.students_with_any_issue)}</>}
            </td>
            <td />
          </tr>
        </tbody>
      </table>
    </div>
  );
}
