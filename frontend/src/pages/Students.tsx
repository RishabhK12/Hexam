import { useEffect, useMemo, useState } from "react";
import { Card, Delta, Empty, ErrorBox, Segmented, Spinner } from "../components/ui";
import { useHexam } from "../hooks/useHexam";
import { ApiError, api } from "../services/api";
import { METRICS, STANDING, campusDateKey, campusHHMM, campusTime, classNames, courseLabel, dayFromKey, num } from "../lib/format";
import type { StudentImpactResponse, StudentPage, StudentRankItem, StudentScheduleImpact, ExamTimeslot } from "../types/hexam";

type ListMode = "improved" | "worsened" | "remaining" | "search";

export function Students() {
  const { datasetId, activeRun, slots } = useHexam();
  const runId = activeRun?.schedule ? activeRun.run_id : null;
  const [mode, setMode] = useState<ListMode>(runId ? "improved" : "search");
  const [q, setQ] = useState("");
  const [major, setMajor] = useState("");
  const [page, setPage] = useState<StudentPage | null>(null);
  const [ranked, setRanked] = useState<StudentRankItem[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [impact, setImpact] = useState<StudentImpactResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string[] | null>(null);

  useEffect(() => {
    if (!runId && mode !== "search") setMode("search");
  }, [runId, mode]);

  // list
  useEffect(() => {
    let off = false;
    if (mode === "search") {
      api.students({ dataset_id: datasetId, q, major: major || undefined, limit: 60 }).then((p) => !off && setPage(p)).catch((e) => !off && setError(e instanceof ApiError ? e.problems : [String(e)]));
    } else if (runId) {
      api.runStudents(runId, mode, 40).then((r) => !off && setRanked(r)).catch((e) => !off && setError(e instanceof ApiError ? e.problems : [String(e)]));
    }
    return () => {
      off = true;
    };
  }, [mode, q, major, runId, datasetId]);

  // default selection: the most improved student (a real record from the dataset)
  useEffect(() => {
    if (selected) return;
    if (mode !== "search" && ranked?.length) setSelected(ranked[0].student_id);
    else if (mode === "search" && page?.items.length) setSelected(page.items[0].student_id);
  }, [ranked, page, mode, selected]);

  useEffect(() => {
    if (!selected) return;
    let off = false;
    setLoading(true);
    setError(null);
    api
      .studentImpact(datasetId, selected, runId)
      .then((r) => !off && setImpact(r))
      .catch((e) => !off && setError(e instanceof ApiError ? e.problems : [String(e)]))
      .finally(() => !off && setLoading(false));
    return () => {
      off = true;
    };
  }, [selected, runId, datasetId]);

  const majors = ["AE", "BMED", "CE", "CHBE", "CMPE", "CS", "EE", "IE", "MATH", "ME", "MSE", "PHYS"];

  return (
    <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
      <aside className="card flex max-h-[calc(100vh-140px)] flex-col lg:sticky lg:top-20">
        <div className="space-y-3 border-b border-line p-4">
          <h2 className="h-section">Synthetic students</h2>
          <Segmented
            label="Student list"
            value={mode}
            onChange={(m) => setMode(m)}
            options={[
              { value: "improved", label: "Improved", disabled: !runId },
              { value: "worsened", label: "Worse", disabled: !runId },
              { value: "remaining", label: "Highest", disabled: !runId },
              { value: "search", label: "Search" },
            ]}
          />
          {mode === "search" && (
            <div className="flex gap-2">
              <input className="input" placeholder="Student ID, e.g. S000042" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search by synthetic student ID" />
              <select className="input w-24" value={major} onChange={(e) => setMajor(e.target.value)} aria-label="Major">
                <option value="">Major</option>
                {majors.map((m) => (
                  <option key={m}>{m}</option>
                ))}
              </select>
            </div>
          )}
        </div>
        <ul className="flex-1 overflow-y-auto p-2" aria-label="Students">
          {mode === "search"
            ? page?.items.map((s) => (
                <StudentRow key={s.student_id} id={s.student_id} active={selected === s.student_id} onClick={() => setSelected(s.student_id)} sub={`${s.major} · ${STANDING[s.year]} · ${s.exam_group_ids.length} exams`} />
              ))
            : ranked?.map((s) => (
                <StudentRow
                  key={s.student_id}
                  id={s.student_id}
                  active={selected === s.student_id}
                  onClick={() => setSelected(s.student_id)}
                  sub={`${s.major} · ${STANDING[s.year]}`}
                  right={
                    <span className={classNames("tabular text-xs font-semibold", s.change < 0 ? "text-good" : s.change > 0 ? "text-bad" : "text-muted")}>
                      {num(s.penalty_before)} → {num(s.penalty_after)}
                    </span>
                  }
                />
              ))}
          {mode === "search" && page && page.items.length === 0 && <li className="p-4 text-sm text-muted">No students match.</li>}
          {mode !== "search" && ranked && ranked.length === 0 && <li className="p-4 text-sm text-muted">No students in this list.</li>}
        </ul>
        {mode === "search" && page && <p className="border-t border-line px-4 py-2 text-xs text-faint">Showing {page.items.length} of {num(page.total)} matching</p>}
      </aside>

      <div className="space-y-4">
        {error && <ErrorBox title="Could not load student" problems={error} />}
        {!impact && !error && <Empty>{loading ? <Spinner /> : "Select a student."}</Empty>}
        {impact && <StudentImpactView impact={impact} slots={slots} loading={loading} />}
      </div>
    </div>
  );
}

function StudentRow({ id, sub, right, active, onClick }: { id: string; sub: string; right?: React.ReactNode; active: boolean; onClick: () => void }) {
  return (
    <li>
      <button onClick={onClick} className={classNames("flex w-full items-center justify-between rounded-lg px-3 py-2 text-left transition", active ? "bg-after/10" : "hover:bg-raised")}>
        <span>
          <span className="block font-mono text-[13px] font-medium">{id}</span>
          <span className="block text-xs text-faint">{sub}</span>
        </span>
        {right}
      </button>
    </li>
  );
}

function StudentImpactView({ impact, slots, loading }: { impact: StudentImpactResponse; slots: ExamTimeslot[]; loading: boolean }) {
  const { student, baseline, optimized, verdict, penalty_change } = impact;
  const courses = useMemo(() => {
    const seen = new Map<string, string>();
    for (const s of student.sections) if (!seen.has(s.course_id)) seen.set(s.course_id, s.course_title);
    return [...seen.entries()];
  }, [student]);
  const days = useMemo(() => [...new Set(slots.map((s) => campusDateKey(s.start)))].sort(), [slots]);
  const verdictText = {
    improved: "Better off with Hexam",
    worsened: "Worse off with Hexam",
    unchanged: "No change in inconvenience",
    not_available: "No optimized schedule yet",
  }[verdict];

  return (
    <div className={classNames("space-y-4 transition-opacity", loading && "opacity-60")}>
      <section className="card card-pad">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="label">Synthetic student</p>
            <h2 className="mt-1 font-mono text-2xl font-semibold">{student.student_id}</h2>
            <p className="sub mt-1">
              {student.major} major · {STANDING[student.year]} · {student.exam_group_ids.length} final exams
            </p>
          </div>
          <div className="text-right">
            <span
              className={classNames(
                "chip text-xs",
                verdict === "improved" && "border-good/30 bg-good/10 text-good",
                verdict === "worsened" && "border-bad/30 bg-bad/10 text-bad",
                (verdict === "unchanged" || verdict === "not_available") && "border-line bg-raised text-muted",
              )}
            >
              {verdict === "improved" ? "▼" : verdict === "worsened" ? "▲" : "•"} {verdictText}
            </span>
            {penalty_change != null && (
              <p className="tabular mt-2 text-sm text-muted">
                Personal score {num(baseline.detail.weighted_penalty)} → <span className="font-semibold text-ink">{num(optimized!.detail.weighted_penalty)}</span>
              </p>
            )}
          </div>
        </div>
        <div className="mt-4 flex flex-wrap gap-1.5">
          {courses.map(([id, title]) => (
            <span key={id} className="chip border-line bg-raised text-ink" title={title}>
              {id.replace("-", " ")}
              <span className="font-normal text-faint">· {title.length > 28 ? title.slice(0, 27) + "…" : title}</span>
            </span>
          ))}
        </div>
      </section>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Baseline exam calendar" sub="Official GT matrix">
          <MiniWeek impact={baseline} days={days} tone="before" />
          <Findings items={baseline.findings} />
        </Card>
        <Card title="Optimized exam calendar" sub={optimized ? "Hexam CP-SAT schedule" : "Run the optimizer to compare"}>
          {optimized ? (
            <>
              <MiniWeek impact={optimized} days={days} tone="after" />
              <Findings items={optimized.findings} />
            </>
          ) : (
            <Empty>No optimized schedule yet.</Empty>
          )}
        </Card>
      </div>

      <Card title="This student’s metrics" sub="Same definitions as the dataset-wide evaluation">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-faint">
              <th className="py-2 font-medium">Metric</th>
              <th className="py-2 text-right font-medium">Baseline</th>
              {optimized && <th className="py-2 text-right font-medium">Optimized</th>}
              {optimized && <th className="py-2 text-right font-medium">Change</th>}
            </tr>
          </thead>
          <tbody>
            {METRICS.map((m) => {
              const b = baseline.detail[m.key];
              const a = optimized?.detail[m.key];
              return (
                <tr key={m.key} className="border-b border-line/60 last:border-0">
                  <td className="py-2" title={m.help}>
                    {m.label} <span className="text-xs text-faint">({m.unit === "students" ? "yes/no" : m.unit === "student exams" ? "exams" : "pairs"})</span>
                  </td>
                  <td className="tabular py-2 text-right">{b}</td>
                  {a !== undefined && <td className="tabular py-2 text-right font-semibold">{a}</td>}
                  {a !== undefined && (
                    <td className="py-2 text-right">
                      <Delta before={b} after={a} percent={null} />
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

const DAY_START = 7.5;
const DAY_END = 21.5;

function hoursOf(iso: string) {
  const [h, m] = campusHHMM(iso).split(":").map(Number);
  return h + m / 60;
}

function MiniWeek({ impact, days, tone }: { impact: StudentScheduleImpact; days: string[]; tone: "before" | "after" }) {
  const byDay = new Map<string, typeof impact.exams>();
  for (const e of impact.exams) {
    if (!e.start) continue;
    const k = campusDateKey(e.start);
    if (!byDay.has(k)) byDay.set(k, []);
    byDay.get(k)!.push(e);
  }
  const H = 220;
  const y = (h: number) => ((h - DAY_START) / (DAY_END - DAY_START)) * H;
  return (
    <div className="flex gap-1.5" role="img" aria-label="Exam calendar for the week">
      <div className="relative w-9 shrink-0 text-[10px] text-faint" style={{ height: H + 22 }}>
        {[8, 11, 14, 17, 20].map((h) => (
          <span key={h} className="absolute right-1" style={{ top: 22 + y(h) - 6 }}>
            {h > 12 ? h - 12 : h}
            {h >= 12 ? "p" : "a"}
          </span>
        ))}
      </div>
      {days.map((d) => {
        const list = byDay.get(d) ?? [];
        const heavy = list.length >= 3;
        return (
          <div key={d} className="min-w-0 flex-1">
            <div className={classNames("mb-1 truncate text-center text-[10.5px] font-medium", heavy ? "text-bad" : "text-muted")}>
              {dayFromKey(d).replace(",", "")}
              {heavy && " ⚠"}
            </div>
            <div className={classNames("relative rounded-md border", heavy ? "border-bad/40 bg-bad/5" : "border-line bg-raised/60")} style={{ height: H }}>
              {list.map((e) => {
                const top = y(hoursOf(e.start!));
                const h = Math.max(18, y(hoursOf(e.end!)) - top);
                return (
                  <div
                    key={e.exam_group_id}
                    title={`${courseLabel(e.course_ids, 4)} · ${campusTime(e.start!)}–${campusTime(e.end!)}${e.moved ? " · moved from baseline" : ""}`}
                    className={classNames(
                      "absolute inset-x-0.5 overflow-hidden rounded px-1 py-0.5 text-[10px] leading-tight text-white",
                      tone === "before" ? "bg-before" : "bg-after",
                      e.moved && tone === "after" && "ring-2 ring-gold",
                    )}
                    style={{ top, height: h }}
                  >
                    <span className="block truncate font-semibold">{courseLabel(e.course_ids, 1)}</span>
                    <span className="block truncate opacity-90">{campusTime(e.start!)}</span>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function Findings({ items }: { items: string[] }) {
  if (!items.length) return <p className="mt-3 text-sm text-good">✓ No scheduling problems detected.</p>;
  return (
    <ul className="mt-3 space-y-1 text-[13px]">
      {items.map((f, i) => (
        <li key={i} className="flex gap-2 text-muted">
          <span className="text-warn" aria-hidden>
            ●
          </span>
          {f}
        </li>
      ))}
    </ul>
  );
}
