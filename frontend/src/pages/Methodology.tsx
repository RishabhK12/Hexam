import { Card } from "../components/ui";
import { useHexam } from "../hooks/useHexam";
import { METRICS, num } from "../lib/format";
import { RunMeta } from "./RunMeta";

const PAPER = "https://arxiv.org/html/2509.11031v1";

export function Methodology() {
  const { info, activeRun, exams } = useHexam();
  const fixed = exams.filter((e) => e.fixed_timeslot_id).length;
  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_360px]">
      <div className="space-y-4">
        <Card title="What Hexam optimizes">
          <div className="space-y-3 text-sm leading-relaxed text-ink/90">
            <p>
              Each <b>exam group</b> (all sections sharing a class meeting time, or a multi-section common exam) is assigned to one official final-exam period.
              Binary variables <code className="rounded bg-raised px-1">x[g,t]</code> mark “group g sits in period t”, created only for permitted pairs.
            </p>
            <p>
              The objective minimizes the same weighted score the evaluator reports: <code className="rounded bg-raised px-1">Σ weight × count</code> over the
              six metrics below. Pairwise metrics are modeled on the co-enrollment graph (one Boolean per pair of groups sharing students, weighted by how many
              students they share). Rolling-window metrics (3 in 24 h, which also covers 3+ exams in one day, and 4 in 48 h) use one Boolean per distinct student exam-set with cardinality constraints. Late-evening and early-morning exams are weighted separately and are linear: weight × students in the group, for each group placed in an evening (5 PM or later) or early (before 10 AM) period. The solver never
              iterates every student × exam × period.
            </p>
            <p>
              The schedule the solver returns is re-scored by an <b>independent evaluator</b> (plain Python, no solver), and every hard constraint is re-checked.
              All numbers in this dashboard come from that evaluator, not from the solver’s internal objective. When the time limit stops the search early, the
              solver objective can sit slightly above the evaluated score. Both are shown.
            </p>
          </div>
        </Card>

        <div className="grid gap-4 md:grid-cols-2">
          <Card title="Hard constraints (never violated)">
            <ul className="list-disc space-y-1.5 pl-5 text-sm text-ink/90">
              <li>Every exam group gets exactly one period.</li>
              <li>The {fixed} official common exams stay in their registrar-assigned periods.</li>
              <li>Only the 21 official exam periods are used. Reading and conflict periods are excluded.</li>
              <li>No student has two overlapping exams (strict mode, the default), checked with real time intervals.</li>
              <li>User locks and any configured aggregate capacity caps are respected.</li>
            </ul>
          </Card>
          <Card title="Weighted objectives (soft)">
            <ul className="space-y-1.5 text-sm">
              {METRICS.map((m) => (
                <li key={m.key}>
                  <span className="font-medium">{m.label}</span> <span className="text-faint">({m.unit})</span>
                  <span className="block text-xs text-muted">{m.help}</span>
                </li>
              ))}
            </ul>
          </Card>
        </div>

        <Card title="Data provenance">
          <div className="space-y-3 text-sm">
            <p className="rounded-lg border border-gold/40 bg-gold/10 p-3 leading-relaxed">{info?.synthetic_disclosure}</p>
            {info && (
              <dl className="grid gap-x-6 gap-y-2 md:grid-cols-[160px_1fr]">
                {Object.entries(info.data_categories).map(([k, v]) => (
                  <div key={k} className="contents">
                    <dt className="font-medium">{k === "gt_public" ? "Real (public GT data)" : k === "synthetic" ? "Synthetic" : "Assumed"}</dt>
                    <dd className="text-muted">{v}</dd>
                  </div>
                ))}
                <dt className="font-medium">Baseline</dt>
                <dd className="text-muted">{info.baseline_provenance}</dd>
                <dt className="font-medium">Dataset version</dt>
                <dd className="font-mono text-xs text-muted">
                  {info.dataset_version} · generator seed {info.generation_seed ?? "n/a"} · {num(info.student_count)} students
                </dd>
              </dl>
            )}
            <p className="text-muted">
              The baseline applies the registrar’s official Fall 2026 matrix rule to this course subset. Improvements are measured against that rule-based
              baseline using synthetic enrollments; they are evidence for the method, not claims about real Georgia Tech students.
            </p>
          </div>
        </Card>

        <Card title="What is not modeled">
          <ul className="list-disc space-y-1 pl-5 text-sm text-muted">
            <li>Room assignment, proctors, instructor availability and accessibility accommodations.</li>
            <li>Real seat capacities (none are public). Optional aggregate caps are not room plans.</li>
            <li>Real student registrations. All students are synthetic.</li>
          </ul>
          <p className="mt-3 text-sm">
            Inspired by{" "}
            <a className="font-medium text-after underline underline-offset-2" href={PAPER} target="_blank" rel="noreferrer">
              arXiv 2509.11031
            </a>{" "}
            (exam-group timeslot optimization). Hexam is a hackathon proof of concept, not an official Georgia Tech scheduling system.
          </p>
        </Card>
      </div>
      <aside className="space-y-4">
        <Card title="Current run">{activeRun?.result ? <RunMeta run={activeRun} /> : <p className="text-sm text-muted">No optimization run loaded.</p>}</Card>
      </aside>
    </div>
  );
}
