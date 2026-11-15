import { useEffect, useState } from "react";
import { ErrorBox, Spinner, StatusPill } from "./components/ui";
import { HexamProvider, useHexam } from "./hooks/useHexam";
import { classNames, num } from "./lib/format";
import { Optimize } from "./pages/Optimize";
import { Overview } from "./pages/Overview";
import { Schedule } from "./pages/Schedule";
import { Students } from "./pages/Students";

const TABS = [
  { id: "overview", label: "Overview" },
  { id: "schedule", label: "Exam schedule" },
  { id: "students", label: "Student impact" },
  { id: "optimize", label: "Optimize" },
] as const;
type TabId = (typeof TABS)[number]["id"];

function useHashTab(): [TabId, (t: string) => void] {
  const read = () => {
    const h = window.location.hash.replace("#", "") as TabId;
    return TABS.some((t) => t.id === h) ? h : "overview";
  };
  const [tab, setTab] = useState<TabId>(read);
  useEffect(() => {
    const on = () => setTab(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return [tab, (t: string) => (window.location.hash = t)];
}

function Shell() {
  const { status, loadError, retry, info, activeRun, job, runOptimization } = useHexam();
  const [tab, go] = useHashTab();
  const busy = !!job && ["submitting", "queued", "running"].includes(job.state);

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 border-b border-line bg-surface/90 backdrop-blur">
        <div className="mx-auto flex max-w-[1440px] items-center gap-4 px-4 py-3 md:px-6">
          <a href="#overview" className="flex items-center gap-2.5">
            <img src="/hexam.svg" alt="" className="h-8 w-8" />
            <div className="leading-tight">
              <p className="text-[17px] font-bold tracking-tight">Hexam</p>
              <p className="whitespace-nowrap text-[11px] text-muted">Smarter Finals. Better Schedules.</p>
            </div>
          </a>
          <nav className="ml-2 hidden gap-1 lg:flex" aria-label="Sections">
            {TABS.map((t) => (
              <a
                key={t.id}
                href={`#${t.id}`}
                aria-current={tab === t.id ? "page" : undefined}
                className={classNames("whitespace-nowrap rounded-md px-3 py-1.5 text-sm font-medium transition", tab === t.id ? "bg-navy/10 text-navy" : "text-muted hover:text-ink")}
              >
                {t.label}
              </a>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3">
            {info && (
              <div className="hidden whitespace-nowrap text-right text-xs leading-tight 2xl:block">
                <p className="font-medium">Georgia Tech · {info.term}</p>
                <p className="text-faint">
                  {num(info.student_count)} synthetic students · {info.exam_group_count} exam groups
                </p>
              </div>
            )}
            {activeRun?.result && !busy && <span className="hidden xl:inline"><StatusPill status={activeRun.result.status} relaxed={activeRun.result.relaxed} hypothetical={activeRun.result.hypothetical} /></span>}
            <button className="btn-primary whitespace-nowrap" onClick={() => runOptimization()} disabled={busy || status !== "ready"}>
              {busy ? <Spinner /> : <span aria-hidden>▶</span>}
              <span className="hidden sm:inline">{busy ? "Optimizing…" : "Run optimization"}</span>
            </button>
          </div>
        </div>
        <nav className="flex gap-1 overflow-x-auto px-4 pb-2 lg:hidden" aria-label="Sections">
          {TABS.map((t) => (
            <a key={t.id} href={`#${t.id}`} className={classNames("whitespace-nowrap rounded-md px-3 py-1.5 text-sm font-medium", tab === t.id ? "bg-navy/10 text-navy" : "text-muted")}>
              {t.label}
            </a>
          ))}
        </nav>
      </header>

      <main className="mx-auto max-w-[1440px] px-4 py-6 md:px-6">
        {status === "loading" && (
          <div className="flex items-center justify-center gap-3 py-24 text-muted">
            <Spinner /> Loading dataset and baseline evaluation…
          </div>
        )}
        {status === "error" && (
          <ErrorBox
            title="Could not load Hexam data"
            problems={loadError ?? ["Unknown error"]}
            action={
              <button className="btn-ghost" onClick={retry}>
                Retry
              </button>
            }
          />
        )}
        {status === "ready" && (
          <>
            {tab === "overview" && <Overview go={go} />}
            {tab === "schedule" && <Schedule />}
            {tab === "students" && <Students />}
            {tab === "optimize" && <Optimize />}
          </>
        )}
      </main>
      <footer className="mx-auto max-w-[1440px] px-4 pb-8 text-xs text-faint md:px-6">
        Hexam · Georgia Tech hackathon prototype · OR-Tools CP-SAT · numbers from the independent evaluator
      </footer>
    </div>
  );
}

export default function App() {
  return (
    <HexamProvider>
      <Shell />
    </HexamProvider>
  );
}
