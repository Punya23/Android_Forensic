import { useEffect, useMemo, useState } from "react";
import { ArrowUpRight, Flag, MessageSquareText, FileText, LayoutDashboard, ShieldCheck, Users } from "lucide-react";
import { api } from "../lib/api";
import { isDemoCase } from "../lib/caseNarrative";
import { DotGlobe, type GlobePoint } from "../components/DotGlobe";
import { ActivityTerrain, FlowFeed, TickMeter, plausibleTime } from "../components/HomeCharts";
import type { CaseSummary, Flag as FlagRow, RegistryCase, TimelineEvent } from "../lib/types";
import type { ViewKey } from "../components/Sidebar";

const fmtBytes = (n: number) =>
  n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : n >= 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`;

const LEVEL: Record<string, { label: string; cls: string }> = {
  red: { label: "High priority", cls: "bg-deletion/25 text-deletion border-deletion/70" },
  amber: { label: "Medium priority", cls: "bg-warn/25 text-warn border-warn/70" },
  green: { label: "Low priority", cls: "bg-live/25 text-live border-live/70" },
};
const SEVERITY_CHIP: Record<string, string> = {
  critical: "text-deletion border-deletion/60",
  warn: "text-warn border-warn/60",
};

const two = (n: number) => String(n).padStart(2, "0");

/** Everything on this page describes one case: the one open in the app (or the newest, which then opens). */
export function HomeView({
  caseId,
  onSelectCase,
  username,
  setView,
  onOpenCase,
}: {
  /** The case currently open in the app, if any — Home shows it. */
  caseId: string | null;
  /** Make a case the open one (enables the sidebar for it) without leaving Home. */
  onSelectCase: (id: string) => void;
  username: string | null;
  setView: (v: ViewKey) => void;
  onOpenCase: (id: string, view?: ViewKey) => void;
}) {
  const [cases, setCases] = useState<RegistryCase[]>([]);
  const [summary, setSummary] = useState<CaseSummary | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [places, setPlaces] = useState<GlobePoint[]>([]);
  const [flags, setFlags] = useState<FlagRow[]>([]);
  const [stamps, setStamps] = useState<string[]>([]);
  const [caps, setCaps] = useState<{ selected_files: number; available_files: number; total_cap_bytes: number; bucket_cap_bytes: number } | null>(null);

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
    setFlags([]);
    setStamps([]);
    setCaps(null);
    if (!id) return;
    api
      .dataset<{ selected_files?: number }>(id, "acquisition_caps")
      .then((c) => setCaps(c && typeof c.selected_files === "number" ? (c as never) : null))
      .catch(() => setCaps(null));
    api.caseOverview(id).then(setSummary).catch(() => setSummary(null));
    api.dataset<FlagRow[]>(id, "flags").then((f) => setFlags(f ?? [])).catch(() => setFlags([]));
    api
      .dataset<TimelineEvent[]>(id, "timeline")
      .then((t) => setStamps((t ?? []).map((e) => e.timestamp).filter(Boolean)))
      .catch(() => setStamps([]));
    // Every recorded coordinate: the unified trace when it has rows, else photo/EXIF locations.
    // 0,0 is a missing fix, not a place.
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
  const critical = flags.filter((f) => f.severity === "critical").length;
  const undated = useMemo(() => stamps.filter((s) => plausibleTime(s) === null).length, [stamps]);
  const people = (summary?.graph_stats.top_contacts ?? []).slice(0, 12);

  const stat: { icon: typeof Flag; value: string | number; label: string; lines: [string, string | number][]; view: ViewKey; x: string; y: string }[] = [
    { icon: Flag, value: flags.length, label: "Flagged items", lines: [["Critical", critical], ["Watch-list", flags.length - critical]], view: "overview", x: "100%", y: "0%" },
    { icon: Users, value: summary?.graph_stats.participants ?? "—", label: "People", lines: [["Interactions", summary?.graph_stats.interactions ?? 0], ["Channels", summary?.graph_stats.channels?.length ?? 0]], view: "graph", x: "0%", y: "0%" },
    {
      icon: ShieldCheck,
      value: hv ? (hv.verified ?? 0) : "—",
      label: "Files verified",
      lines: [["Failed", hv ? (hv.failed ?? 0) : "—"], ["Device-altering", summary?.device_altering_actions ?? 0]],
      view: "custody",
      x: "100%",
      y: "100%",
    },
  ];

  const bars: { label: string; n: number; view: ViewKey }[] = [
    { label: "Messages", n: c.messages ?? 0, view: "messages" },
    { label: "Recovered / deleted", n: c.recovered ?? 0, view: "recovered" },
    { label: "Browser entries", n: c.browser ?? 0, view: "browser" },
    { label: "Locations", n: c.locations ?? 0, view: "locations" },
    { label: "Photos & videos", n: c.media ?? 0, view: "media" },
    { label: "Contacts", n: c.contacts ?? 0, view: "contacts" },
    { label: "Calls", n: c.calls ?? 0, view: "calls" },
  ];
  const maxBar = Math.max(1, ...bars.map((b) => b.n));

  const facts: [string, string][] = summary
    ? [
        ["Device", [device?.manufacturer, device?.model].filter(Boolean).join(" ") || "unknown"],
        ["Android", device?.android_version ? `${device.android_version} (SDK ${device.sdk})` : "unknown"],
        ["Examiner", summary.case.examiner || "—"],
        ["Opened", summary.case.created_at ? summary.case.created_at.slice(0, 10) : "—"],
        ["Collected", `${summary.artifact_count} files · ${fmtBytes(summary.total_bytes)}`],
        ...(caps
          ? ([["Scope", `Capped run: ${caps.selected_files.toLocaleString()} of ${caps.available_files.toLocaleString()} files`]] as [string, string][])
          : []),
        ["Audit events", String(summary.audit_event_count)],
      ]
    : [];

  if (!id) {
    return (
      <div className="p-4">
        <div className="glass p-8 text-center">
          <div className="text-base font-semibold">{loaded ? "No case yet" : "Loading…"}</div>
          {loaded && <p className="text-sm text-muted mt-1">Start a new acquisition to create the first case.</p>}
        </div>
      </div>
    );
  }

  return (
    <div className="p-4 space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Dashboard</h1>
          <p className="text-sm text-muted mt-1">
            {username ? `${username} · ` : ""}
            {id}
            {summary && isDemoCase(summary) && " · demonstration data, not a real device"}
          </p>
        </div>
        <div className="flex gap-2">
          <button className="btn-ghost text-xs flex items-center gap-1.5" onClick={() => go("overview")}>
            <LayoutDashboard className="h-3.5 w-3.5" aria-hidden /> Overview
          </button>
          <button className="btn-ghost text-xs flex items-center gap-1.5" onClick={() => go("ask")}>
            <MessageSquareText className="h-3.5 w-3.5" aria-hidden /> Ask this case
          </button>
          <button className="btn-accent text-xs flex items-center gap-1.5" onClick={() => go("report")}>
            <FileText className="h-3.5 w-3.5" aria-hidden /> Report
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-3">
        {/* Posture: score over the activity terrain */}
        <div className="glass p-5 lg:col-span-4 flex flex-col">
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="text-base font-semibold">Case posture</div>
              <div className="text-xs text-muted mt-0.5">Triage priority from the evidence</div>
            </div>
            {level && <span className={`text-[11px] font-medium rounded-full border px-2.5 py-1 ${level.cls}`}>{level.label}</span>}
          </div>
          {risk ? (
            <div className="flex items-baseline gap-2 mt-3">
              <span className="font-dot text-6xl font-bold leading-none tracking-tight">{risk.score}</span>
              <span className="font-dot text-xl text-muted">/100</span>
            </div>
          ) : (
            <div className="text-sm text-muted mt-6">Loading this case…</div>
          )}
          <div className="mt-2">
            <ActivityTerrain stamps={stamps} />
            <div className="text-[11px] text-muted">
              Events per hour, one ridge per day{undated > 0 ? ` · ${undated} event${undated === 1 ? "" : "s"} with no usable date left out` : ""}
            </div>
          </div>
          {risk && <p className="text-sm text-ink/80 mt-3">{risk.headline}</p>}
        </div>

        {/* Three numbers */}
        <div className="grid gap-3 lg:col-span-3 lg:grid-rows-3">
          {stat.map((s) => {
            const Icon = s.icon;
            return (
              <button key={s.label} onClick={() => go(s.view)} className="glass glass-link text-left p-4 flex flex-col justify-between">
                <div className="flex items-center justify-between">
                  <Icon className="h-4 w-4 text-ink/80" strokeWidth={1.75} aria-hidden />
                  <ArrowUpRight className="h-4 w-4 text-muted" strokeWidth={1.75} aria-hidden />
                </div>
                <div className="flex items-end justify-between gap-3 mt-3">
                  <div>
                    <div className="font-dot text-4xl font-bold leading-none tracking-tight">{typeof s.value === "number" ? two(s.value) : s.value}</div>
                    <div className="text-sm mt-1.5">{s.label}</div>
                  </div>
                  <div className="text-right text-xs text-muted space-y-0.5">
                    {s.lines.map(([k, v]) => (
                      <div key={k}>
                        {k} <span className="text-ink font-mono">{typeof v === "number" ? two(v) : v}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </button>
            );
          })}
        </div>

        {/* Communication flow */}
        <div className="glass p-5 lg:col-span-5">
          <div className="flex items-center justify-between mb-1">
            <div className="text-base font-semibold">Communication flow</div>
            <button className="text-xs text-accent hover:underline" onClick={() => go("graph")}>
              Open network
            </button>
          </div>
          <div className="text-xs text-muted">Top participants by recorded interactions — hover a lane</div>
          <FlowFeed people={people} total={summary?.graph_stats.interactions ?? 0} />
        </div>

        {/* Findings */}
        <div className="glass p-5 lg:col-span-4">
          <div className="flex items-center justify-between mb-2">
            <div className="text-base font-semibold">Key findings</div>
            <button className="text-xs text-accent hover:underline" onClick={() => go("overview")}>
              All findings
            </button>
          </div>
          {risk && risk.reasons.length ? (
            <div className="divide-y divide-ink/10">
              {risk.reasons.slice(0, 5).map((r, i) => (
                <div key={i} className="py-2.5 first:pt-0 last:pb-0">
                  <div className="flex items-start justify-between gap-3">
                    <div className="text-sm font-medium">{r.label}</div>
                    <span className={`shrink-0 text-[10px] rounded border px-1.5 py-0.5 font-mono ${SEVERITY_CHIP[r.severity] ?? "text-muted border-ink/20"}`}>+{r.points}</span>
                  </div>
                  <div className="text-xs text-muted mt-0.5">{r.detail}</div>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted">{risk ? "No findings contributed to the score." : "Loading this case…"}</p>
          )}
        </div>

        {/* What was collected */}
        <div className="glass p-5 lg:col-span-8">
          <div className="text-base font-semibold">What was collected</div>
          <div className="text-xs text-muted mb-3">
            {summary ? `${summary.artifact_count} files · ${fmtBytes(summary.total_bytes)}` : ""}
            {c.message_placeholders ? ` · ${c.message_placeholders} encrypted backup(s) could not be read` : ""}
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {bars.map((b) => (
              <button
                key={b.label}
                onClick={() => go(b.view)}
                className="text-left rounded-xl border border-ink/10 bg-ink/[0.03] p-3 transition-all duration-200 hover:-translate-y-0.5 hover:border-accent/50 active:scale-[0.97]"
              >
                <div className="font-dot text-3xl font-bold leading-none">{b.n.toLocaleString()}</div>
                <div className="text-sm mt-1.5">{b.label}</div>
                <div className="mt-2">
                  <TickMeter value={b.n} max={maxBar} />
                </div>
              </button>
            ))}
          </div>
        </div>

        {/* Where it was + case file */}
        <div className="glass p-5 lg:col-span-5 min-h-[18rem] flex flex-col">
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
        <div className="glass p-5 lg:col-span-7">
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
      </div>
    </div>
  );
}
