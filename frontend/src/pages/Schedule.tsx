import { useMemo, useState } from "react";
import { Card, Empty, Segmented } from "../components/ui";
import { optimizedMapping, useHexam } from "../hooks/useHexam";
import { campusDateKey, campusDay, campusHHMM, campusTime, classNames, courseLabel, dayFromKey, num, timeRange } from "../lib/format";
import type { ExamGroupView, ExamTimeslot } from "../types/hexam";

type Mode = "baseline" | "optimized" | "compare";

export function Schedule() {
  const { exams, slots, baseline, activeRun, slotById } = useHexam();
  const opt = optimizedMapping(activeRun);
  const [mode, setMode] = useState<Mode>(opt ? "compare" : "baseline");
  const [dept, setDept] = useState("");
  const [query, setQuery] = useState("");
  const [onlyMoved, setOnlyMoved] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const effectiveMode: Mode = opt ? mode : "baseline";

  const base = useMemo(() => new Map(baseline?.assignments.map((a) => [a.exam_group_id, a.timeslot_id]) ?? []), [baseline]);
  const shown = effectiveMode === "baseline" ? base : opt!;
  const depts = useMemo(() => [...new Set(exams.flatMap((e) => e.departments))].sort(), [exams]);

  const matches = (e: ExamGroupView) => {
    if (dept && !e.departments.includes(dept)) return false;
    const q = query.trim().toUpperCase().replace(" ", "-");
    if (q && !e.course_ids.some((c) => c.includes(q)) && !e.exam_group_id.includes(q)) return false;
    if (onlyMoved && opt && opt.get(e.exam_group_id) === base.get(e.exam_group_id)) return false;
    return true;
  };

  const days = useMemo(() => [...new Set(slots.map((s) => campusDateKey(s.start)))].sort(), [slots]);
  const times = useMemo(() => [...new Set(slots.map((s) => campusHHMM(s.start)))].sort(), [slots]);
  const slotAt = useMemo(() => {
    const m = new Map<string, ExamTimeslot>();
    for (const s of slots) m.set(`${campusDateKey(s.start)}|${campusHHMM(s.start)}`, s);
    return m;
  }, [slots]);

  const bySlot = useMemo(() => {
    const m = new Map<string, ExamGroupView[]>();
    for (const e of exams) {
      const t = shown.get(e.exam_group_id);
      if (!t) continue;
      if (!m.has(t)) m.set(t, []);
      m.get(t)!.push(e);
    }
    for (const list of m.values()) list.sort((a, b) => b.student_count - a.student_count);
    return m;
  }, [exams, shown]);

  const movedOut = useMemo(() => {
    const m = new Map<string, ExamGroupView[]>();
    if (effectiveMode !== "compare" || !opt) return m;
    for (const e of exams) {
      const b = base.get(e.exam_group_id);
      if (b && opt.get(e.exam_group_id) !== b) {
        if (!m.has(b)) m.set(b, []);
        m.get(b)!.push(e);
      }
    }
    return m;
  }, [exams, base, opt, effectiveMode]);

  const movedCount = opt ? exams.filter((e) => opt.get(e.exam_group_id) !== base.get(e.exam_group_id)).length : 0;
  const sel = selected ? exams.find((e) => e.exam_group_id === selected) : null;

  return (
    <div className="space-y-4">
      <Card
        title="Final exam week"
        sub={
          opt
            ? `${movedCount} of ${exams.length} exam groups moved by the optimizer · common exams stay in their official periods`
            : "Baseline: every exam group in the period the official GT matrix assigns to its class meeting time"
        }
        right={
          <Segmented
            label="Schedule view"
            value={effectiveMode}
            onChange={setMode}
            options={[
              { value: "baseline", label: "Baseline" },
              { value: "optimized", label: "Optimized", disabled: !opt },
              { value: "compare", label: "Compare", disabled: !opt },
            ]}
          />
        }
      >
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1">
            <span className="label">Department</span>
            <select className="input w-40" value={dept} onChange={(e) => setDept(e.target.value)}>
              <option value="">All departments</option>
              {depts.map((d) => (
                <option key={d}>{d}</option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="label">Course</span>
            <input className="input w-48" placeholder="e.g. CS 1331" value={query} onChange={(e) => setQuery(e.target.value)} />
          </label>
          {opt && (
            <label className="mb-2 flex items-center gap-2 text-sm text-muted">
              <input type="checkbox" checked={onlyMoved} onChange={(e) => setOnlyMoved(e.target.checked)} /> Only moved exams
            </label>
          )}
          <div className="mb-1.5 ml-auto flex flex-wrap items-center gap-3 text-xs text-muted">
            <span className="inline-flex items-center gap-1">
              <span className="chip border-gold/50 bg-gold/15 text-ink">🔒 fixed</span> official common exam
            </span>
            {effectiveMode === "compare" && (
              <span className="inline-flex items-center gap-1">
                <span className="chip border-after/40 bg-after/10 text-after">moved</span> new period
              </span>
            )}
          </div>
        </div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-[1fr_300px]">
        <div className="card overflow-x-auto">
          <table className="w-full min-w-[980px] table-fixed border-collapse text-sm">
            <thead>
              <tr>
                <th className="w-20 border-b border-line p-2" />
                {days.map((d) => (
                  <th key={d} className="border-b border-l border-line p-2 text-left font-semibold">
                    {dayFromKey(d)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {times.map((tm) => (
                <tr key={tm}>
                  <th className="border-b border-line p-2 text-left align-top text-xs font-medium text-muted">
                    {(() => {
                      const any = slots.find((s) => campusHHMM(s.start) === tm)!;
                      return campusTime(any.start);
                    })()}
                  </th>
                  {days.map((d) => {
                    const s = slotAt.get(`${d}|${tm}`);
                    if (!s)
                      return (
                        <td key={d} className="border-b border-l border-line bg-raised/50 p-2 align-top text-xs italic text-faint">
                          No exam period
                        </td>
                      );
                    const list = (bySlot.get(s.timeslot_id) ?? []).filter(matches);
                    const total = (bySlot.get(s.timeslot_id) ?? []).reduce((n, e) => n + e.student_count, 0);
                    const out = (movedOut.get(s.timeslot_id) ?? []).filter(matches);
                    return (
                      <td key={d} className="border-b border-l border-line p-1.5 align-top">
                        <div className="mb-1 flex items-center justify-between px-0.5 text-[11px] text-faint" title={timeRange(s.start, s.end)}>
                          <span>{list.length} exam{list.length === 1 ? "" : "s"}</span>
                          <span className="tabular" title="Students sitting an exam in this period (exam sittings, not room capacity)">
                            {num(total)} students
                          </span>
                        </div>
                        <div className="space-y-1">
                          {list.map((e) => (
                            <ExamCard
                              key={e.exam_group_id}
                              exam={e}
                              mode={effectiveMode}
                              from={effectiveMode === "compare" && opt && base.get(e.exam_group_id) !== s.timeslot_id ? slotById.get(base.get(e.exam_group_id)!) : undefined}
                              selected={selected === e.exam_group_id}
                              onClick={() => setSelected(selected === e.exam_group_id ? null : e.exam_group_id)}
                            />
                          ))}
                          {out.map((e) => (
                            <button
                              key={`out-${e.exam_group_id}`}
                              onClick={() => setSelected(e.exam_group_id)}
                              className={classNames(
                                "block w-full truncate rounded-md border border-dashed border-line px-2 py-1 text-left text-[11px] text-faint hover:text-muted",
                                selected === e.exam_group_id && "ring-2 ring-after/40",
                              )}
                              title="Was here in the baseline"
                            >
                              ↗ {courseLabel(e.course_ids, 1)} moved out
                            </button>
                          ))}
                          {list.length === 0 && out.length === 0 && <p className="px-1 py-2 text-[11px] text-faint">—</p>}
                        </div>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <aside className="card card-pad h-fit xl:sticky xl:top-20">
          {sel ? <ExamDetail exam={sel} baseSlot={slotById.get(base.get(sel.exam_group_id) ?? "")} optSlot={opt ? slotById.get(opt.get(sel.exam_group_id) ?? "") : undefined} /> : (
            <Empty>Select an exam to see its courses, enrollment and how it moved.</Empty>
          )}
        </aside>
      </div>
      <p className="text-xs text-faint">
        Exam groups shown in the same period run concurrently. That is only a student conflict if they share students; the evaluator counts those
        separately (strict mode forbids them).
      </p>
    </div>
  );
}

function ExamCard({ exam, mode, from, selected, onClick }: { exam: ExamGroupView; mode: Mode; from?: ExamTimeslot; selected: boolean; onClick: () => void }) {
  const fixed = exam.fixed_timeslot_id != null;
  return (
    <button
      onClick={onClick}
      className={classNames(
        "block w-full rounded-md border px-2 py-1.5 text-left transition",
        fixed ? "border-gold/50 bg-gold/10" : from ? "border-after/40 bg-after/[0.07]" : "border-line bg-raised hover:border-faint",
        selected && "ring-2 ring-after/50",
      )}
    >
      <div className="flex items-center justify-between gap-1">
        <span className="truncate text-[12px] font-semibold" title={courseLabel(exam.course_ids, 8)}>{courseLabel(exam.course_ids, 1)}</span>
        <span className="tabular shrink-0 text-[11px] text-muted">{exam.student_count}</span>
      </div>
      <div className="mt-0.5 flex items-center gap-1 text-[10.5px] text-faint">
        {fixed && <span>🔒 common</span>}
        {!fixed && !from && <span className="truncate">{exam.meeting_pattern ?? exam.name}</span>}
        {from && mode === "compare" && <span className="ml-auto shrink-0 font-medium text-after">from {campusDay(from.start).split(",")[0]} {campusTime(from.start).replace(":00", "")}</span>}
      </div>
    </button>
  );
}

function ExamDetail({ exam, baseSlot, optSlot }: { exam: ExamGroupView; baseSlot?: ExamTimeslot; optSlot?: ExamTimeslot }) {
  const moved = optSlot && baseSlot && optSlot.timeslot_id !== baseSlot.timeslot_id;
  return (
    <div className="space-y-3 text-sm">
      <div>
        <p className="label">{exam.kind === "common" ? "Common exam" : "Meeting-time exam group"}</p>
        <h3 className="mt-1 text-base font-semibold">{courseLabel(exam.course_ids, 6)}</h3>
        <p className="font-mono text-[11px] text-faint">{exam.exam_group_id}</p>
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5">
        <dt className="text-muted">Students</dt>
        <dd className="tabular text-right font-medium">{num(exam.student_count)}</dd>
        <dt className="text-muted">Sections</dt>
        <dd className="tabular text-right">{exam.section_count}</dd>
        {exam.meeting_pattern && (
          <>
            <dt className="text-muted">Class meets</dt>
            <dd className="text-right">{exam.meeting_pattern}</dd>
          </>
        )}
        <dt className="text-muted">Baseline</dt>
        <dd className="text-right">{baseSlot ? `${campusDay(baseSlot.start)}, ${campusTime(baseSlot.start)}` : "—"}</dd>
        {optSlot && (
          <>
            <dt className="text-muted">Optimized</dt>
            <dd className={classNames("text-right", moved && "font-semibold text-after")}>
              {campusDay(optSlot.start)}, {campusTime(optSlot.start)}
            </dd>
          </>
        )}
      </dl>
      {exam.fixed_timeslot_id && <p className="rounded-md bg-gold/15 px-2.5 py-1.5 text-xs">Officially fixed by the registrar; never moved in normal optimization.</p>}
      {exam.description && <p className="text-xs leading-relaxed text-muted">{exam.description}</p>}
      <p className="text-[11px] text-faint">Source: {exam.source === "gt_public" ? "public GT data (exam matrix)" : exam.source}</p>
    </div>
  );
}
