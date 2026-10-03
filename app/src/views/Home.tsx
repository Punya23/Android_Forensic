import { useEffect, useMemo, useState } from "react";
import {
  ArrowUpRight,
  Archive,
  BookOpen,
  FileText,
  MessageSquareText,
  Network,
  Plus,
  Clock,
} from "lucide-react";
import { api } from "../lib/api";
import type { CaseSummary, Health, RegistryCase, RegistryStats } from "../lib/types";
import type { ViewKey } from "../components/Sidebar";

const fmtBytes = (n: number) =>
  n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : n >= 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`;

const isDemo = (c: RegistryCase) => c.device_model.endsWith("[demo]");
const dateOnly = (iso: string) => (iso ? iso.slice(0, 10) : "—");

const LEVEL: Record<string, { label: string; cls: string }> = {
  red: { label: "High priority", cls: "bg-deletion/15 text-deletion border-deletion/30" },
  amber: { label: "Medium priority", cls: "bg-warn/15 text-warn border-warn/30" },
  green: { label: "Low priority", cls: "bg-live/15 text-live border-live/30" },
};

/** Artifacts collected per recent case, oldest to newest, as a soft area line. */
function Trend({ values }: { values: number[] }) {
  if (values.length < 2) return null;
  const W = 600;
  const H = 120;
  const max = Math.max(...values, 1);
  const pts = values.map((v, i) => [(i / (values.length - 1)) * W, H - 12 - (v / max) * (H - 28)] as const);
  const line = pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-28" preserveAspectRatio="none" aria-hidden>
      <defs>
        <linearGradient id="home-trend" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="rgb(var(--color-accent))" stopOpacity="0.35" />
          <stop offset="1" stopColor="rgb(var(--color-accent))" stopOpacity="0" />
        </linearGradient>
      </defs>
      {[0.25, 0.5, 0.75].map((f) => (
        <line key={f} x1="0" x2={W} y1={H * f} y2={H * f} stroke="rgb(var(--color-ink))" strokeOpacity="0.06" strokeDasharray="3 5" />
      ))}
      <path d={`${line} L${W},${H} L0,${H} Z`} fill="url(#home-trend)" />
      <path d={line} fill="none" stroke="rgb(var(--color-accent))" strokeWidth="2" vectorEffect="non-scaling-stroke" />
      {pts.map(([x, y], i) => (
        <circle key={i} cx={x} cy={y} r="2.5" fill="rgb(var(--color-accent))" />
      ))}
    </svg>
  );
}

const bloom = (x: string, y: string) => ({ ["--bloom-x" as string]: x, ["--bloom-y" as string]: y });

/** Landing page after sign-in: posture of the latest case, totals, and ways into the data. */
export function HomeView({
  username,
  health,
  setView,
  onOpenCase,
}: {
  username: string | null;
  health: Health | null;
  setView: (v: ViewKey) => void;
  onOpenCase: (id: string, view?: ViewKey) => void;
}) {
  const [cases, setCases] = useState<RegistryCase[]>([]);
  const [stats, setStats] = useState<RegistryStats | null>(null);
  const [latest, setLatest] = useState<CaseSummary | null>(null);

  useEffect(() => {
    api
      .registryCases({ sort: "-updated_at" })
      .then((r) => {
        setCases(r.cases);
        setStats(r.stats);
        if (r.cases[0]) {
          api
            .caseOverview(r.cases[0].case_id)
            .then(setLatest)
            .catch(() => setLatest(null));
        }
      })
      .catch(() => {
        /* recorded by api.get — the banner shows it; tiles stay empty */
      });
  }, []);

  const recent = cases.slice(0, 6);
  const top = cases[0];
  const demoCount = cases.filter(isDemo).length;
  const maxArtifacts = Math.max(1, ...recent.map((c) => c.artifact_count));
  const trend = useMemo(() => [...cases.slice(0, 12)].reverse().map((c) => c.artifact_count), [cases]);
  const risk = latest?.risk;
  const level = risk ? LEVEL[risk.level] : undefined;

  const tiles: { label: string; value: string | number; note: string; go: () => void; x: string; y: string }[] = [
    { label: "Cases", value: stats ? stats.cases : "—", note: stats ? `${demoCount} demo · ${stats.cases - demoCount} real` : "", go: () => setView("cases"), x: "100%", y: "0%" },
    { label: "Artifacts", value: stats ? stats.artifacts.toLocaleString() : "—", note: "collected and hashed", go: () => top && onOpenCase(top.case_id), x: "0%", y: "0%" },
    { label: "Evidence", value: stats ? fmtBytes(stats.bytes) : "—", note: "across all cases", go: () => setView("cases"), x: "100%", y: "100%" },
    { label: "Reports", value: stats ? stats.reports : "—", note: "generated snapshots", go: () => top && onOpenCase(top.case_id, "report"), x: "0%", y: "100%" },
  ];

  const quick: { icon: typeof Plus; title: string; text: string; go: () => void; needsCase?: boolean }[] = [
    { icon: MessageSquareText, title: "Ask this case", text: "Plain-words questions over the evidence", go: () => top && onOpenCase(top.case_id, "ask"), needsCase: true },
    { icon: Network, title: "Communication network", text: "Who is linked to whom", go: () => top && onOpenCase(top.case_id, "graph"), needsCase: true },
    { icon: Clock, title: "Timeline", text: "Everything in time order", go: () => top && onOpenCase(top.case_id, "timeline"), needsCase: true },
    { icon: FileText, title: "Report", text: "Generate or review the case report", go: () => top && onOpenCase(top.case_id, "report"), needsCase: true },
    { icon: Archive, title: "Case history", text: "Search and reopen earlier cases", go: () => setView("cases") },
    { icon: BookOpen, title: "Knowledge base", text: "Reference notes and precedent", go: () => setView("knowledge") },
  ];

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Dashboard</h1>
          <p className="text-sm text-muted mt-1">
            {username ? `Welcome, ${username}. ` : ""}
            Field triage for Android phones — every step hashed and logged. A preview, not a lab examination.
          </p>
        </div>
        <div className="flex items-center gap-2 text-xs">
          <span className="glass !rounded-full px-3 py-1.5">Engine {health ? `v${health.version}` : "offline"}</span>
          <span className="glass !rounded-full px-3 py-1.5">ADB {health?.adb ? "ready" : "not found"}</span>
          <button className="btn-accent text-xs flex items-center gap-1.5" onClick={() => setView("acquire")}>
            <Plus className="h-3.5 w-3.5" strokeWidth={2.5} aria-hidden /> New acquisition
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
        {/* Hero: posture of the latest case */}
        <button
          className="glass glass-link text-left p-5 lg:col-span-7 disabled:cursor-default"
          style={bloom("0%", "0%")}
          disabled={!top}
          onClick={() => top && onOpenCase(top.case_id)}
        >
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="text-base font-semibold">Case posture</div>
              <div className="text-xs text-muted mt-0.5">
                {top ? `${top.case_id} · ${top.device_model || "device unknown"}` : "No case yet"}
              </div>
            </div>
            {level && (
              <span className={`text-[11px] font-medium rounded-full border px-2.5 py-1 ${level.cls}`}>{level.label}</span>
            )}
          </div>
          {top && risk ? (
            <>
              <div className="flex items-baseline gap-2 mt-5">
                <span className="font-dot text-7xl font-bold leading-none tracking-tight">{risk.score}</span>
                <span className="font-dot text-2xl text-muted">/100</span>
              </div>
              <p className="text-sm text-ink/80 mt-3 max-w-xl">{risk.headline}</p>
              <ul className="mt-3 space-y-1 text-xs text-muted">
                {risk.reasons.slice(0, 3).map((r, i) => (
                  <li key={i} className="flex gap-2">
                    <span className="font-mono text-accent shrink-0">+{r.points}</span>
                    <span className="truncate">{r.label} — {r.detail}</span>
                  </li>
                ))}
              </ul>
              <div className="mt-4">
                <div className="text-[11px] uppercase tracking-wider text-muted mb-1">Artifacts per recent case</div>
                <Trend values={trend} />
              </div>
            </>
          ) : top ? (
            <p className="text-sm text-muted mt-6">Loading the latest case…</p>
          ) : (
            <p className="text-sm text-muted mt-6">Start a new acquisition to create the first case.</p>
          )}
        </button>

        {/* Totals */}
        <div className="grid grid-cols-2 gap-4 lg:col-span-5">
          {tiles.map((t) => (
            <button
              key={t.label}
              onClick={t.go}
              disabled={!stats}
              className="glass glass-link text-left p-4 flex flex-col justify-between min-h-[8.5rem]"
              style={bloom(t.x, t.y)}
            >
              <div className="flex items-center justify-between text-xs text-muted">
                {t.label}
                <ArrowUpRight className="h-4 w-4" strokeWidth={1.75} aria-hidden />
              </div>
              <div>
                <div className="font-dot text-4xl font-bold leading-none tracking-tight">{t.value}</div>
                <div className="text-[11px] text-muted mt-2">{t.note}</div>
              </div>
            </button>
          ))}
        </div>

        {/* Recent cases */}
        <div className="glass p-5 lg:col-span-7" style={bloom("100%", "0%")}>
          <div className="flex items-center justify-between mb-3">
            <div className="text-base font-semibold">Recent cases</div>
            <button className="text-xs text-accent hover:underline" onClick={() => setView("cases")}>
              Show all
            </button>
          </div>
          {recent.length === 0 ? (
            <p className="text-sm text-muted">No cases yet.</p>
          ) : (
            <div className="space-y-1">
              {recent.map((c) => (
                <button
                  key={c.case_id}
                  onClick={() => onOpenCase(c.case_id)}
                  className="w-full flex items-center gap-3 text-left rounded-xl px-2.5 py-2 transition-colors hover:bg-ink/5"
                >
                  <span className="font-mono text-xs text-accent w-28 shrink-0 truncate">{c.case_id}</span>
                  <span className="text-xs text-muted w-44 shrink-0 truncate hidden md:block">{c.device_model || "—"}</span>
                  <span className="text-[11px] text-muted w-20 shrink-0 hidden lg:block">{dateOnly(c.updated_at)}</span>
                  <span className="flex-1 h-1.5 rounded-full bg-ink/10 overflow-hidden">
                    <span className="block h-full rounded-full bg-accent/80" style={{ width: `${Math.round((c.artifact_count / maxArtifacts) * 100)}%` }} />
                  </span>
                  <span className="font-mono text-xs w-10 text-right">{c.artifact_count}</span>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Ways in */}
        <div className="glass p-5 lg:col-span-5" style={bloom("0%", "100%")}>
          <div className="text-base font-semibold mb-3">Explore the forensic data</div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {quick.map((q) => {
              const Icon = q.icon;
              return (
                <button
                  key={q.title}
                  onClick={q.go}
                  disabled={q.needsCase && !top}
                  className="text-left rounded-xl border border-ink/10 bg-ink/[0.03] p-3 transition-all duration-200 hover:-translate-y-0.5 hover:border-accent/40 hover:bg-ink/[0.06] active:scale-[0.97] disabled:opacity-40"
                >
                  <Icon className="h-4 w-4 text-accent" strokeWidth={1.75} aria-hidden />
                  <div className="text-sm font-medium mt-1.5">{q.title}</div>
                  <div className="text-[11px] text-muted mt-0.5 leading-snug">{q.text}</div>
                </button>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
