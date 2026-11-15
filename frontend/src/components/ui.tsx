import type { ReactNode } from "react";
import { classNames, num, pct } from "../lib/format";
import type { SolverStatus } from "../types/hexam";

export function Card({ title, sub, right, children, className }: { title?: ReactNode; sub?: ReactNode; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={classNames("card card-pad", className)}>
      {(title || right) && (
        <header className="mb-4 flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
          <div>
            {title && <h2 className="h-section">{title}</h2>}
            {sub && <p className="sub mt-0.5">{sub}</p>}
          </div>
          {right}
        </header>
      )}
      {children}
    </section>
  );
}

/** Lower is better for every Hexam metric: a negative change is an improvement. */
export function Delta({ before, after, percent, invert = false }: { before: number; after: number; percent?: number | null; invert?: boolean }) {
  const change = after - before;
  const p = percent !== undefined ? percent : before === 0 ? null : (100 * change) / before;
  const good = invert ? change > 0 : change < 0;
  const bad = invert ? change < 0 : change > 0;
  return (
    <span
      className={classNames(
        "chip tabular",
        good && "border-good/30 bg-good/10 text-good",
        bad && "border-bad/30 bg-bad/10 text-bad",
        !good && !bad && "border-line bg-raised text-muted",
      )}
    >
      <span aria-hidden>{good ? "▼" : bad ? "▲" : "•"}</span>
      {change === 0 ? "no change" : p == null ? `${change > 0 ? "+" : ""}${num(change)} (n/a %)` : pct(p)}
    </span>
  );
}

export function Kpi({
  label,
  unit,
  baseline,
  optimized,
  percent,
  hint,
}: {
  label: string;
  unit?: string;
  baseline: number | null | undefined;
  optimized?: number | null;
  percent?: number | null;
  hint?: string;
}) {
  const hasOpt = optimized !== undefined && optimized !== null;
  return (
    <div className="card flex flex-col gap-2 p-4" title={hint}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-[13px] font-medium text-muted">{label}</span>
        {hasOpt && baseline != null && <Delta before={baseline} after={optimized!} percent={percent} />}
      </div>
      {hasOpt ? (
        <div className="flex items-baseline gap-3">
          <span className="tabular text-[28px] font-semibold leading-none tracking-tight">{num(optimized)}</span>
          <span className="tabular text-sm text-faint">
            from <span className="font-medium text-muted line-through decoration-faint/60">{num(baseline)}</span>
          </span>
        </div>
      ) : (
        <span className="tabular text-[28px] font-semibold leading-none tracking-tight">{num(baseline)}</span>
      )}
      {unit && <span className="text-xs text-faint">{unit}</span>}
    </div>
  );
}

export function StatusPill({ status, relaxed, hypothetical }: { status: SolverStatus | string; relaxed?: boolean; hypothetical?: boolean }) {
  const tone =
    status === "OPTIMAL"
      ? "border-good/30 bg-good/10 text-good"
      : status === "FEASIBLE"
        ? "border-after/30 bg-after/10 text-after"
        : "border-bad/30 bg-bad/10 text-bad";
  const icon = status === "OPTIMAL" ? "✓" : status === "FEASIBLE" ? "◐" : "✕";
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <span className={classNames("chip", tone)}>
        <span aria-hidden>{icon}</span>
        {status}
      </span>
      {relaxed && <span className="chip border-warn/30 bg-warn/10 text-warn">RELAXED</span>}
      {hypothetical && <span className="chip border-warn/30 bg-warn/10 text-warn">HYPOTHETICAL</span>}
    </span>
  );
}

export function Spinner({ className }: { className?: string }) {
  return <span className={classNames("inline-block h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent", className)} aria-hidden />;
}

export function ErrorBox({ title, problems, action }: { title: string; problems: string[]; action?: ReactNode }) {
  return (
    <div role="alert" className="rounded-xl border border-bad/30 bg-bad/5 p-4 text-sm">
      <p className="font-semibold text-bad">{title}</p>
      <ul className="mt-1 list-disc space-y-0.5 pl-5 text-ink/80">
        {problems.map((p, i) => (
          <li key={i}>{p}</li>
        ))}
      </ul>
      {action && <div className="mt-3">{action}</div>}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-xl border border-dashed border-line p-8 text-center text-sm text-muted">{children}</div>;
}

export function Swatch({ kind }: { kind: "base" | "opt" }) {
  return <span className={classNames("inline-block h-2.5 w-2.5 rounded-sm", kind === "base" ? "bg-before" : "bg-after")} aria-hidden />;
}

export function Legend() {
  return (
    <div className="flex shrink-0 items-center gap-4 whitespace-nowrap text-xs text-muted">
      <span className="inline-flex items-center gap-1.5">
        <Swatch kind="base" /> Baseline
      </span>
      <span className="inline-flex items-center gap-1.5">
        <Swatch kind="opt" /> Optimized
      </span>
    </div>
  );
}

export function Segmented<T extends string>({ value, options, onChange, label }: { value: T; options: { value: T; label: string; disabled?: boolean }[]; onChange: (v: T) => void; label: string }) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded-lg border border-line bg-raised p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          role="radio"
          aria-checked={value === o.value}
          disabled={o.disabled}
          onClick={() => onChange(o.value)}
          className={classNames(
            "rounded-md px-3 py-1.5 text-[13px] font-medium transition disabled:opacity-40",
            value === o.value ? "bg-surface text-ink shadow-card" : "text-muted hover:text-ink",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
