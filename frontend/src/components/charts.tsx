import { useEffect, useRef, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis, type TooltipProps } from "recharts";
import { num } from "../lib/format";

type Tokens = { base: string; opt: string; grid: string; axis: string; ink: string; surface: string; line: string };

function readTokens(): Tokens {
  const cs = getComputedStyle(document.documentElement);
  const v = (n: string, fb: string) => {
    const raw = cs.getPropertyValue(`--${n}`).trim();
    return raw ? `rgb(${raw.split(/\s+/).join(",")})` : fb;
  };
  return {
    base: v("series-base", "#b8892a"),
    opt: v("series-opt", "#2a5fa8"),
    grid: v("line", "#e2e5eb"),
    axis: v("faint", "#8c94a0"),
    ink: v("ink", "#111827"),
    surface: v("surface", "#fff"),
    line: v("line", "#e2e5eb"),
  };
}

/** Theme colors for SVG (re-read when the OS color scheme flips). */
export function useChartTokens(): Tokens {
  const [t, setT] = useState<Tokens>(() => (typeof window === "undefined" ? readTokens() : readTokens()));
  useEffect(() => {
    const mq = window.matchMedia?.("(prefers-color-scheme: dark)");
    const on = () => setT(readTokens());
    mq?.addEventListener?.("change", on);
    return () => mq?.removeEventListener?.("change", on);
  }, []);
  return t;
}

export interface PairRow {
  label: string;
  base: number;
  opt?: number | null;
  detail?: string;
  /** Grouped x-axis (vertical charts): consecutive rows with the same `group` share one label. */
  group?: string;
  groupSub?: string;
  /** Short per-bar label under each bar, shown only when there is room. */
  minor?: string;
}

/** Width of an element, tracked with ResizeObserver. */
function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [w, setW] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    setW(el.getBoundingClientRect().width);
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([e]) => setW(e.contentRect.width));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

const Y_AXIS_W = 48;
const MINOR_MIN_BAND = 27; // px per bar needed for the per-bar label not to collide

type GroupedTickProps = {
  x?: number;
  y?: number;
  payload?: { index: number };
  data: PairRow[];
  band: number;
  showMinor: boolean;
  t: Tokens;
};

/** Two-tier category tick: optional per-bar label, then one centered label per group with dividers. */
function GroupedTick({ x = 0, y = 0, payload, data, band, showMinor, t }: GroupedTickProps) {
  const i = payload?.index ?? 0;
  const row = data[i];
  if (!row) return null;
  let s = i;
  while (s > 0 && data[s - 1].group === row.group) s--;
  let e = i;
  while (e < data.length - 1 && data[e + 1].group === row.group) e++;
  const mid = (s + e) / 2;
  const top = y - 8; // the axis line
  const groupY = top + (showMinor ? 30 : 16);
  return (
    <g>
      {showMinor && row.minor && (
        <text x={x} y={top + 15} textAnchor="middle" fontSize={10} fill={t.axis}>
          {row.minor}
        </text>
      )}
      {i === s && i > 0 && <line x1={x - band / 2} x2={x - band / 2} y1={top} y2={top + (showMinor ? 20 : 7)} stroke={t.axis} strokeOpacity={0.6} />}
      {i === Math.floor(mid) && (
        <>
          <text x={x + (mid - i) * band} y={groupY} textAnchor="middle" fontSize={11} fontWeight={600} fill={t.ink}>
            {row.group}
          </text>
          {row.groupSub && (
            <text x={x + (mid - i) * band} y={groupY + 13} textAnchor="middle" fontSize={10} fill={t.axis}>
              {row.groupSub}
            </text>
          )}
        </>
      )}
    </g>
  );
}

function PairTooltip({ active, payload, label, unit }: TooltipProps<number, string> & { unit: string }) {
  if (!active || !payload?.length) return null;
  const row = payload[0].payload as PairRow;
  const b = row.base;
  const o = row.opt;
  return (
    <div className="rounded-lg border border-line bg-surface px-3 py-2 text-xs shadow-card">
      <p className="mb-1 font-semibold text-ink">{label}</p>
      {row.detail && <p className="mb-1 text-faint">{row.detail}</p>}
      <p className="flex items-center gap-2 text-muted">
        <span className="h-2 w-2 rounded-sm bg-before" /> Baseline <span className="tabular ml-auto font-medium text-ink">{num(b)}</span>
      </p>
      {o != null && (
        <>
          <p className="flex items-center gap-2 text-muted">
            <span className="h-2 w-2 rounded-sm bg-after" /> Optimized <span className="tabular ml-auto font-medium text-ink">{num(o)}</span>
          </p>
          <p className="mt-1 border-t border-line pt-1 text-faint">
            Change <span className="tabular font-medium text-ink">{o - b > 0 ? "+" : ""}{num(o - b)}</span> {unit}
          </p>
        </>
      )}
    </div>
  );
}

/** Baseline vs optimized bars on one shared axis (same unit for every row). */
export function PairBars({ data, unit, horizontal = false, height = 260 }: { data: PairRow[]; unit: string; horizontal?: boolean; height?: number }) {
  const t = useChartTokens();
  const [ref, width] = useWidth<HTMLDivElement>();
  const hasOpt = data.some((d) => d.opt != null);
  const axisStyle = { fontSize: 11, fill: t.axis };
  const grouped = !horizontal && data.some((d) => d.group);
  const band = data.length ? Math.max(0, width - Y_AXIS_W - 24) / data.length : 0;
  const showMinor = grouped && band >= MINOR_MIN_BAND && data.some((d) => d.minor);
  return (
    <div ref={ref} style={{ height }} role="img" aria-label={`Bar chart, ${unit}, baseline${hasOpt ? " versus optimized" : ""}`}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout={horizontal ? "vertical" : "horizontal"} barGap={2} barCategoryGap={horizontal ? "22%" : "18%"} margin={{ top: 4, right: 12, bottom: 0, left: horizontal ? 8 : grouped ? 0 : -12 }}>
          <CartesianGrid stroke={t.grid} strokeDasharray="0" vertical={horizontal} horizontal={!horizontal} />
          {horizontal ? (
            <>
              <XAxis type="number" tick={axisStyle} axisLine={false} tickLine={false} tickFormatter={(v) => num(v)} />
              <YAxis type="category" dataKey="label" tick={{ ...axisStyle, fill: t.ink }} axisLine={false} tickLine={false} width={124} />
            </>
          ) : (
            <>
              {grouped ? (
                <XAxis
                  dataKey="label"
                  axisLine={{ stroke: t.line }}
                  tickLine={false}
                  interval={0}
                  height={showMinor ? 50 : 36}
                  tick={<GroupedTick data={data} band={band} showMinor={showMinor} t={t} />}
                />
              ) : (
                <XAxis dataKey="label" tick={axisStyle} axisLine={{ stroke: t.line }} tickLine={false} interval={0} />
              )}
              <YAxis tick={axisStyle} axisLine={false} tickLine={false} tickFormatter={(v) => num(v)} width={grouped ? Y_AXIS_W : undefined} />
            </>
          )}
          <Tooltip cursor={{ fill: t.grid, fillOpacity: 0.35 }} content={<PairTooltip unit={unit} />} />
          <Bar dataKey="base" name="Baseline" fill={t.base} radius={horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0]} maxBarSize={22} isAnimationActive={false} />
          {hasOpt && <Bar dataKey="opt" name="Optimized" fill={t.opt} radius={horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0]} maxBarSize={22} isAnimationActive={false} />}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
