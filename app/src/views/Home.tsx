import { useEffect, useState } from "react";
import {
  ArrowUpRight,
  BookOpen,
  Clock,
  FileText,
  MessageSquareText,
  Network,
  Plus,
  Recycle,
} from "lucide-react";
import { api } from "../lib/api";
import { Select } from "../components/fields";
import { isDemoCase } from "../lib/caseNarrative";
import { DotGlobe, type GlobePoint } from "../components/DotGlobe";
import type { CaseSummary, Health, RegistryCase } from "../lib/types";
import type { ViewKey } from "../components/Sidebar";

const fmtBytes = (n: number) =>
  n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : n >= 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`;

const LEVEL: Record<string, { label: string; cls: string }> = {
  red: { label: "High priority", cls: "bg-deletion/15 text-deletion border-deletion/30" },
  amber: { label: "Medium priority", cls: "bg-warn/15 text-warn border-warn/30" },
  green: { label: "Low priority", cls: "bg-live/15 text-live border-live/30" },
};

const bloom = (x: string, y: string) => ({ ["--bloom-x" as string]: x, ["--bloom-y" as string]: y });

/** Everything on this page describes one case: the one open in the app, or the one picked here. */
export function HomeView({
  caseId,
  onSelectCase,
  username,
  health,
  setView,
  onOpenCase,
}: {
  /** The case currently open in the app, if any — Home shows it. */
  caseId: string | null;
  /** Make a case the open one (enables the sidebar for it) without leaving Home. */
  onSelectCase: (id: string) => void;
  username: string | null;
  health: Health | null;
  setView: (v: ViewKey) => void;
  onOpenCase: (id: string, view?: ViewKey) => void;
}) {
  const [cases, setCases] = useState<RegistryCase[]>([]);
  const [summary, setSummary] = useState<CaseSummary | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [places, setPlaces] = useState<GlobePoint[]>([]);

  useEffect(() => {
    api
      .registryCases({ sort: "-updated_at" })
      .then((r) => setCases(r.cases))
      .catch(() => {
        /* recorded by api.get — the banner shows it */
      })
      .finally(() => setLoaded(true));
  }, []);

  const current = cases.find((c) => c.case_id === caseId) ?? cases[0];
  const id = current?.case_id;

  // The case Home shows is the app's open case, so the sidebar is live for it: when none is
  // open yet, the one Home falls back to (the newest) becomes the open case.
  useEffect(() => {
    if (!caseId && id) onSelectCase(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caseId, id]);

  useEffect(() => {
    setSummary(null);
    setPlaces([]);
    if (!id) return;
    api
      .caseOverview(id)
      .then(setSummary)
      .catch(() => setSummary(null));
    // Every recorded coordinate for this case: the unified trace when it has rows, else the
    // photo/EXIF locations. Points at 0,0 are a missing fix, not a place, and are dropped.
    type Row = { latitude?: number | null; longitude?: number | null; label?: string };
    const toPoints = (rows: Row[]): GlobePoint[] =>
      (rows ?? [])
        .filter((r) => typeof r.latitude === "number" && typeof r.longitude === "number" && !(r.latitude === 0 && r.longitude === 0))
        .map((r) => ({ lat: r.latitude as number, lon: r.longitude as number, label: r.label }));
    api
      .dataset<Row[]>(id, "location_traces")
      .then((t) => {
        const pts = toPoints(t);
        if (pts.length) return setPlaces(pts);
        return api.dataset<Row[]>(id, "locations").then((l) => setPlaces(toPoints(l)));
      })
      .catch(() => setPlaces([]));
  }, [id]);

  // Opening one of this case's pages makes it the app's open case.
  const go = (v: ViewKey) => id && onOpenCase(id, v);

  const risk = summary?.risk;
  const level = risk ? LEVEL[risk.level] : undefined;
  const c = summary?.counts ?? {};
  const hv = summary?.hash_verification;
  const device = summary?.case.device;

  const bars: { label: string; n: number; view: ViewKey }[] = [
    { label: "Messages", n: c.messages ?? 0, view: "messages" },
    { label: "Calls", n: c.calls ?? 0, view: "calls" },
    { label: "Contacts", n: c.contacts ?? 0, view: "contacts" },
    { label: "Photos & videos", n: c.media ?? 0, view: "media" },
    { label: "Locations", n: c.locations ?? 0, view: "locations" },
    { label: "Browser", n: c.browser ?? 0, view: "browser" },
    { label: "Recovered / deleted", n: c.recovered ?? 0, view: "recovered" },
  ];
  const maxBar = Math.max(1, ...bars.map((b) => b.n));

  const integrity =
    !hv ? { value: "—", note: "verification did not run" }
    : hv.failed ? { value: `${hv.failed}`, note: `of ${(hv.verified ?? 0) + hv.failed} files FAILED hash check` }
    : { value: `${hv.verified ?? 0}`, note: "files match recorded hashes" };

  const tiles: { label: string; value: string | number; note: string; view: ViewKey; x: string; y: string }[] = [
    { label: "Messages", value: c.messages ?? "—", note: c.message_placeholders ? `${c.message_placeholders} encrypted backup(s) unreadable` : "recovered from the phone", view: "messages", x: "100%", y: "0%" },
    { label: "Recovered", value: c.recovered ?? "—", note: "deleted or carved items", view: "recovered", x: "0%", y: "0%" },
    { label: "People", value: summary?.graph_stats.participants ?? "—", note: `${summary?.graph_stats.interactions ?? 0} recorded interactions`, view: "graph", x: "100%", y: "100%" },
    { label: "Integrity", value: integrity.value, note: integrity.note, view: "custody", x: "0%", y: "100%" },
  ];

  const facts: [string, string][] = summary
    ? [
        ["Case", summary.case.case_id],
        ["Device", [device?.manufacturer, device?.model].filter(Boolean).join(" ") || "unknown"],
        ["Android", device?.android_version ? `${device.android_version} (SDK ${device.sdk})` : "unknown"],
        ["Examiner", summary.case.examiner || "—"],
        ["Opened", summary.case.created_at ? summary.case.created_at.slice(0, 10) : "—"],
        ["Collected", `${summary.artifact_count} files · ${fmtBytes(summary.total_bytes)}`],
        ["Device-altering actions", String(summary.device_altering_actions)],
        ["Audit events", String(summary.audit_event_count)],
      ]
    : [];

  const quick: { icon: typeof Plus; title: string; text: string; view: ViewKey }[] = [
    { icon: MessageSquareText, title: "Ask this case", text: "Plain-words questions over the evidence", view: "ask" },
    { icon: Network, title: "Communication network", text: "Who is linked to whom", view: "graph" },
    { icon: Clock, title: "Timeline", text: "Everything in time order", view: "timeline" },
    { icon: Recycle, title: "Recovered / deleted", text: "What was deleted and brought back", view: "recovered" },
    { icon: FileText, title: "Report", text: "Generate or review the case report", view: "report" },
    { icon: BookOpen, title: "Overview", text: "Plain-language summary and findings", view: "overview" },
  ];

  return (
    <div className="p-4 space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Dashboard</h1>
          <p className="text-sm text-muted mt-1">
            {username ? `Welcome, ${username}. ` : ""}
            {id ? "Everything below is about the selected case." : "Field triage for Android phones — every step hashed and logged."}
          </p>
        </div>
        <div className="flex items-center gap-2 text-xs">
          {cases.length > 0 && (
            <Select
              className="input w-auto font-mono text-xs !py-1.5"
              value={id ?? ""}
              onChange={onSelectCase}
              ariaLabel="Case shown on the dashboard"
              options={cases.map((k) => ({ value: k.case_id, label: `${k.case_id} · ${k.device_model || "—"}` }))}
            />
          )}
          <span className="glass !rounded-full px-3 py-1.5">ADB {health?.adb ? "ready" : "not found"}</span>
          <button className="btn-accent text-xs flex items-center gap-1.5" onClick={() => setView("acquire")}>
            <Plus className="h-3.5 w-3.5" strokeWidth={2.5} aria-hidden /> New acquisition
          </button>
        </div>
      </div>

      {!id ? (
        <div className="glass p-8 text-center">
          <div className="text-base font-semibold">{loaded ? "No case yet" : "Loading…"}</div>
          {loaded && <p className="text-sm text-muted mt-1">Start a new acquisition to create the first case.</p>}
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-3">
          {/* Posture + what the case holds */}
          <div className="glass p-5 lg:col-span-7 lg:order-1" style={bloom("0%", "0%")}>
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="text-base font-semibold">Case posture</div>
                <div className="text-xs text-muted mt-0.5">
                  {id}
                  {summary && isDemoCase(summary) && " · demonstration data, not a real device"}
                </div>
              </div>
              {level && (
                <span className={`text-[11px] font-medium rounded-full border px-2.5 py-1 ${level.cls}`}>{level.label}</span>
              )}
            </div>
            {risk ? (
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
              </>
            ) : (
              <p className="text-sm text-muted mt-6">Loading this case…</p>
            )}
            <div className="mt-5">
              <div className="text-[11px] uppercase tracking-wider text-muted mb-2">What was collected</div>
              <div className="space-y-1">
                {bars.map((b) => (
                  <button
                    key={b.label}
                    onClick={() => go(b.view)}
                    className="w-full flex items-center gap-3 text-left rounded-lg px-2 py-1 text-xs transition-colors hover:bg-ink/5"
                  >
                    <span className="w-36 shrink-0 text-muted">{b.label}</span>
                    <span className="flex-1 h-1.5 rounded-full bg-ink/10 overflow-hidden">
                      <span className="block h-full rounded-full bg-accent/80" style={{ width: `${Math.round((b.n / maxBar) * 100)}%` }} />
                    </span>
                    <span className="font-mono w-14 text-right">{b.n.toLocaleString()}</span>
                  </button>
                ))}
              </div>
            </div>
          </div>

          {/* Case numbers */}
          <div className="grid grid-cols-2 gap-3 lg:col-span-5 lg:order-2">
            {tiles.map((t) => (
              <button
                key={t.label}
                onClick={() => go(t.view)}
                className="glass glass-link text-left p-4 flex flex-col justify-between min-h-[8.5rem]"
                style={bloom(t.x, t.y)}
              >
                <div className="flex items-center justify-between text-xs text-muted">
                  {t.label}
                  <ArrowUpRight className="h-4 w-4" strokeWidth={1.75} aria-hidden />
                </div>
                <div>
                  <div className="font-dot text-4xl font-bold leading-none tracking-tight">
                    {typeof t.value === "number" ? t.value.toLocaleString() : t.value}
                  </div>
                  <div className="text-[11px] text-muted mt-2">{t.note}</div>
                </div>
              </button>
            ))}
          </div>

          {/* Where the phone was */}
          <div className="glass p-5 lg:col-span-5 lg:order-4 min-h-[18rem] flex flex-col" style={bloom("100%", "100%")}>
            <div className="flex items-center justify-between mb-1">
              <div className="text-base font-semibold">Where it was</div>
              <button className="text-xs text-accent hover:underline" onClick={() => go("loctrace")}>
                Open trace
              </button>
            </div>
            <div className="text-xs text-muted mb-2">
              {places.length ? `${places.length} recorded location${places.length === 1 ? "" : "s"} — drag to turn, scroll to zoom` : "No coordinates were recorded for this case."}
            </div>
            <div className="flex-1 min-h-[14rem]">
              <DotGlobe points={places} />
            </div>
          </div>

          {/* The case file */}
          <div className="glass p-5 lg:col-span-7 lg:order-3" style={bloom("100%", "0%")}>
            <div className="text-base font-semibold mb-3">Case file</div>
            {facts.length === 0 ? (
              <p className="text-sm text-muted">Loading this case…</p>
            ) : (
              <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-8 gap-y-2 text-sm">
                {facts.map(([k, v]) => (
                  <div key={k} className="flex justify-between gap-3 border-b border-ink/10 pb-1.5">
                    <dt className="text-muted">{k}</dt>
                    <dd className="text-right font-medium truncate">{v}</dd>
                  </div>
                ))}
              </dl>
            )}
          </div>

          {/* Ways in */}
          <div className="glass p-5 lg:col-span-12 lg:order-5" style={bloom("0%", "100%")}>
            <div className="text-base font-semibold mb-3">Explore this case</div>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
              {quick.map((q) => {
                const Icon = q.icon;
                return (
                  <button
                    key={q.title}
                    onClick={() => go(q.view)}
                    className="text-left rounded-xl border border-ink/10 bg-ink/[0.03] p-3 transition-all duration-200 hover:-translate-y-0.5 hover:border-accent/40 hover:bg-ink/[0.06] active:scale-[0.97]"
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
      )}
    </div>
  );
}
