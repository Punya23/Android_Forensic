import { useEffect, useState } from "react";
import { Plus, Archive, MessageSquareText, BookOpen, LayoutDashboard } from "lucide-react";
import { api } from "../lib/api";
import type { Health, RegistryCase, RegistryStats } from "../lib/types";
import type { ViewKey } from "../components/Sidebar";

const fmtBytes = (n: number) =>
  n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : n >= 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`;

const isDemo = (c: RegistryCase) => c.device_model.endsWith("[demo]");

/** Landing page after sign-in: where things are, what is on this installation, jump-off links. */
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

  useEffect(() => {
    api
      .registryCases({ sort: "-updated_at" })
      .then((r) => {
        setCases(r.cases);
        setStats(r.stats);
      })
      .catch(() => {
        /* recorded by api.get — the banner shows it; tiles stay empty */
      });
  }, []);

  const recent = cases.slice(0, 6);
  const latest = cases[0];
  const demoCount = cases.filter(isDemo).length;
  const maxArtifacts = Math.max(1, ...recent.map((c) => c.artifact_count));

  const tiles = [
    { label: "Cases", value: stats ? stats.cases : "—", note: stats ? `${demoCount} demo · ${stats.cases - demoCount} real` : "" },
    { label: "Artifacts", value: stats ? stats.artifacts.toLocaleString() : "—", note: "files collected and hashed" },
    { label: "Evidence size", value: stats ? fmtBytes(stats.bytes) : "—", note: "across all cases" },
    { label: "Reports", value: stats ? stats.reports : "—", note: "generated snapshots" },
  ];

  const actions: { icon: typeof Plus; title: string; text: string; onClick: () => void; disabled?: boolean }[] = [
    { icon: Plus, title: "New acquisition", text: "Connect a phone and start a case.", onClick: () => setView("acquire") },
    { icon: Archive, title: "Case history", text: "Search and reopen earlier cases.", onClick: () => setView("cases") },
    {
      icon: LayoutDashboard,
      title: "Open latest case",
      text: latest ? `${latest.case_id} — summary and findings.` : "No cases yet.",
      onClick: () => latest && onOpenCase(latest.case_id),
      disabled: !latest,
    },
    {
      icon: MessageSquareText,
      title: "Ask a case",
      text: latest ? `Ask questions of ${latest.case_id} in plain words.` : "No cases yet.",
      onClick: () => latest && onOpenCase(latest.case_id, "ask"),
      disabled: !latest,
    },
    { icon: BookOpen, title: "Knowledge base", text: "Reference notes and precedent.", onClick: () => setView("knowledge") },
  ];

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Welcome{username ? `, ${username}` : ""}</h1>
        <p className="text-sm text-muted mt-1 max-w-2xl">
          SNAGR is a field triage tool for Android phones. It collects what the phone allows, hashes and logs every
          step, and turns it into a case you can search, question and report on. It is a triage preview, not a
          replacement for a full laboratory examination.
        </p>
        <div className="flex flex-wrap gap-2 mt-3 text-xs">
          <span className="rounded-full border border-line px-2.5 py-1">Engine {health ? `v${health.version}` : "offline"}</span>
          <span className="rounded-full border border-line px-2.5 py-1">ADB {health?.adb ? "ready" : "not found"}</span>
        </div>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {tiles.map((t) => (
          <div key={t.label} className="card p-4">
            <div className="label">{t.label}</div>
            <div className="text-3xl font-semibold tracking-tight mt-1">{t.value}</div>
            <div className="text-[11px] text-muted mt-0.5">{t.note}</div>
          </div>
        ))}
      </div>

      <div>
        <div className="label mb-2">Start here</div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {actions.map((a) => {
            const Icon = a.icon;
            return (
              <button
                key={a.title}
                onClick={a.onClick}
                disabled={a.disabled}
                className="card p-4 text-left hover:border-accent/50 transition-colors disabled:opacity-40 disabled:hover:border-line"
              >
                <Icon className="h-4 w-4 text-accent" strokeWidth={1.75} aria-hidden />
                <div className="text-sm font-medium mt-2">{a.title}</div>
                <div className="text-xs text-muted mt-0.5">{a.text}</div>
              </button>
            );
          })}
        </div>
      </div>

      <div className="card p-4">
        <div className="label mb-3">Recent cases — artifacts collected</div>
        {recent.length === 0 ? (
          <p className="text-sm text-muted">No cases yet. Start a new acquisition to create one.</p>
        ) : (
          <div className="space-y-2">
            {recent.map((c) => (
              <button
                key={c.case_id}
                onClick={() => onOpenCase(c.case_id)}
                className="w-full flex items-center gap-3 text-left rounded-md px-2 py-1.5 hover:bg-panel"
              >
                <span className="font-mono text-xs text-accent w-28 shrink-0 truncate">{c.case_id}</span>
                <span className="text-xs text-muted w-48 shrink-0 truncate hidden md:block">{c.device_model || "—"}</span>
                <span className="flex-1 h-2 rounded bg-line overflow-hidden">
                  <span
                    className="block h-full bg-accent/70"
                    style={{ width: `${Math.round((c.artifact_count / maxArtifacts) * 100)}%` }}
                  />
                </span>
                <span className="font-mono text-xs w-12 text-right">{c.artifact_count}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
