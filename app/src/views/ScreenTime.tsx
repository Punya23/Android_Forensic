import { useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import { useDataset, fmtTs } from "../lib/hooks";
import { SectionHeader } from "../components/common";

// ---------------------------------------------------------------------------
// Types — declared locally (this view owns its contract with the engine).
// Every field is optional except the ones the parser always emits, because a
// dataset written by an older run must degrade to "not reported", never to a
// confident zero.
// ---------------------------------------------------------------------------

/** One screen state transition parsed out of `dumpsys power`. */
export interface ScreenEvent {
  timestamp?: string;
  event_type?: string;
  /** Free-text explanation of how the event was derived, when the parser supplies one. */
  summary?: string;
  reason?: string;
  confidence?: string;
  /** Raw wakefulness token (Awake/Asleep/Dozing) when the event came from state, not a timestamp. */
  wakefulness?: string;
  /** Which sub-parser produced the row: wakefulness_state | power_timestamp | history_line. */
  source?: string;
  raw_epoch_ms?: number;
  caveats?: string[];
  warnings?: string[];
}

/** Per-package foreground usage merged from `dumpsys batterystats` + `usagestats`. */
export interface ScreenAppUsage {
  package: string;
  foreground_ms?: number;
  foreground_min?: number;
  last_used?: string;
  is_suspicious?: boolean;
  /** -1 means batterystats did not report a battery share for this package. */
  battery_pct_used?: number;
  caveats?: string[];
  warnings?: string[];
}

/**
 * Per-package foreground-usage telemetry from the Tier-1 Collector helper APK's
 * `usage.json` — a DIFFERENT collection method from `ScreenAppUsage` above.
 *
 * The helper (`io.erakshak.collector`) calls
 * `UsageStatsManager.queryUsageStats(INTERVAL_DAILY, now - 30d, now)` and sums
 * `totalTimeInForeground` per package over that fixed, deterministic 30-day window —
 * it does not read the OS's own rolling dumpsys buffer. Getting this required
 * installing the helper APK and granting it the `GET_USAGE_STATS` appop, both
 * device-altering Tier-1 steps. See engine/triage/parsers/collector.py::parse_usage
 * and apk/.../SystemCollectors.kt::UsageCollector for the exact source.
 */
export interface HelperAppUsage {
  package: string;
  total_foreground_ms?: number;
  total_foreground_min?: number;
  last_used?: string | null;
  /** Looked up from a small known-package table by the parser; not device-reported. */
  friendly_name?: string | null;
  /** Always "other" in the current parser — no real classification is performed yet. */
  category?: string;
  source_file?: string;
}

/** One `collectors[]` entry from `collector_manifest.json` (per-run helper audit record). */
interface CollectorRunEntry {
  collector?: string;
  file?: string;
  count?: number;
  /** ok | denied | error | unsupported | empty */
  status?: string;
  error?: string;
}

interface CollectorManifest {
  collectors?: CollectorRunEntry[];
}

/** A heuristic observation raised over the usage rows. */
export interface UsagePattern {
  pattern?: string;
  description?: string;
  severity?: string;
  evidence?: Record<string, string | number | boolean | null>;
  caveats?: string[];
  warnings?: string[];
}

/**
 * `most_active_hours` is emitted as a rank-ordered list of hour numbers.
 * The object form is accepted defensively in case a future engine build
 * attaches per-hour counts.
 */
export type ActiveHour = number | { hour: number; count?: number };

export interface ScreenTimeSummary {
  total_sessions?: number;
  total_screen_time_min?: number;
  most_used_apps?: string[];
  most_active_hours?: ActiveHour[];
  daily_average_min?: number;
  suspicious_apps?: string[];
}

// ---------------------------------------------------------------------------
// Badges / small presentational pieces
// ---------------------------------------------------------------------------

const EVENT_COLORS: Record<string, { bg: string; text: string }> = {
  ON: { bg: "#e4f4ea", text: "#1c7d3f" },
  UNLOCK: { bg: "#e2ecfa", text: "#2258a8" },
  OFF: { bg: "#f6dedd", text: "#a5322f" },
  DOZE: { bg: "#f6ecd4", text: "#a6741a" },
};

function EventBadge({ value }: { value: string }) {
  const key = value.toUpperCase();
  const c = EVENT_COLORS[key] ?? { bg: "#f6ecd4", text: "#a6741a" };
  return (
    <span
      style={{
        display: "inline-block",
        padding: "1px 8px",
        borderRadius: 4,
        fontSize: 11,
        fontWeight: 600,
        color: c.text,
        background: c.bg,
        whiteSpace: "nowrap",
      }}
    >
      {key || "UNKNOWN"}
    </span>
  );
}

/**
 * The suspicious flag is a substring match on the package name
 * (vpn/proxy/tor/vault/cleaner/…). It is an index, not a determination, and the
 * label has to say so wherever it appears.
 */
function HeuristicFlag() {
  return (
    <span
      title="Substring match on the package name (vpn, proxy, tor, vault, cleaner, incognito, …). Not a finding."
      style={{
        display: "inline-block",
        padding: "1px 8px",
        borderRadius: 4,
        fontSize: 11,
        fontWeight: 600,
        color: "#a6741a",
        background: "#f6ecd4",
        whiteSpace: "nowrap",
      }}
    >
      KEYWORD MATCH
    </span>
  );
}

function CaveatList({ items }: { items: string[] }) {
  if (items.length === 0) return null;
  return (
    <ul className="mt-1 space-y-0.5">
      {items.map((c, i) => (
        <li key={i} className="text-[11px] text-warn leading-snug">
          ⚠ {c}
        </li>
      ))}
    </ul>
  );
}

function StatTile({ value, label, note, tone }: { value: string; label: string; note?: string; tone?: string }) {
  return (
    <div className="card px-4 py-3 min-w-[140px] flex-1">
      <div className={`text-2xl font-bold ${tone ?? "text-ink"}`}>{value}</div>
      <div className="text-[11px] uppercase tracking-wider text-muted mt-0.5">{label}</div>
      {note && <div className="text-[10px] text-muted mt-1 leading-snug">{note}</div>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmtMinutes(min: number | undefined): string {
  if (min == null || !Number.isFinite(min) || min <= 0) return "—";
  if (min < 60) return `${Math.round(min * 10) / 10}m`;
  const h = Math.floor(min / 60);
  const m = Math.round(min - h * 60);
  return `${h}h ${m}m`;
}

interface RankedHour {
  hour: number;
  count?: number;
  rank: number;
}

function normalizeHours(raw: ActiveHour[] | undefined): RankedHour[] {
  if (!Array.isArray(raw)) return [];
  const out: RankedHour[] = [];
  raw.forEach((h, idx) => {
    if (typeof h === "number" && Number.isFinite(h)) {
      out.push({ hour: Math.trunc(h), rank: idx });
    } else if (h && typeof h === "object" && Number.isFinite(h.hour)) {
      out.push({ hour: Math.trunc(h.hour), count: h.count, rank: idx });
    }
  });
  return out.filter((h) => h.hour >= 0 && h.hour <= 23);
}

function caveatsOf(row: { caveats?: string[]; warnings?: string[] }): string[] {
  return [...(row.caveats ?? []), ...(row.warnings ?? [])];
}

// ---------------------------------------------------------------------------
// Hour-of-day chart — pure CSS, no charting library.
// ---------------------------------------------------------------------------

function HourChart({ hours }: { hours: RankedHour[] }) {
  const hasCounts = hours.some((h) => typeof h.count === "number" && Number.isFinite(h.count));
  const maxCount = hasCounts ? Math.max(...hours.map((h) => h.count ?? 0), 1) : 0;
  const byHour = new Map<number, RankedHour>();
  hours.forEach((h) => {
    if (!byHour.has(h.hour)) byHour.set(h.hour, h);
  });

  return (
    <div className="card p-4">
      <div className="flex items-baseline justify-between mb-1 gap-3 flex-wrap">
        <h3 className="text-sm font-semibold text-ink">Hour-of-day activity</h3>
        <span className="text-[11px] text-warn">
          {hasCounts ? "Bar height = screen-on sessions in that hour" : "Approximate — bar height encodes RANK, not volume"}
        </span>
      </div>
      <p className="text-[11px] text-muted leading-snug mb-3">
        {hasCounts
          ? "Counted from screen-ON events that carried a parseable timestamp."
          : "The engine records only the rank-ordered top active hours, not a per-hour session count. Unranked hours are drawn flat: that means \"not in the top ranks\", NOT \"zero activity\"."}{" "}
        Hours are <strong className="text-ink">UTC</strong>, taken from the event timestamp — they are not
        converted to the device's local timezone, so they may not match the user's wall clock.
      </p>

      <div className="flex items-end gap-[3px] h-24" role="img" aria-label="Hour of day activity chart">
        {Array.from({ length: 24 }, (_, hour) => {
          const hit = byHour.get(hour);
          let pct = 6;
          if (hit) {
            pct = hasCounts
              ? Math.max(10, ((hit.count ?? 0) / maxCount) * 100)
              : Math.max(24, 100 - hit.rank * 18);
          }
          return (
            <div key={hour} className="flex-1 flex flex-col justify-end h-full">
              <div
                title={
                  hit
                    ? hasCounts
                      ? `${String(hour).padStart(2, "0")}:00 UTC — ${hit.count} session(s)`
                      : `${String(hour).padStart(2, "0")}:00 UTC — rank #${hit.rank + 1} most active (no count recorded)`
                    : `${String(hour).padStart(2, "0")}:00 UTC — not among the ranked active hours`
                }
                style={{
                  height: `${pct}%`,
                  background: hit ? "#d8823c" : "#2b323a",
                  borderRadius: 2,
                }}
              />
            </div>
          );
        })}
      </div>
      <div className="flex gap-[3px] mt-1">
        {Array.from({ length: 24 }, (_, hour) => (
          <div key={hour} className="flex-1 text-center text-[9px] text-muted font-mono">
            {hour % 3 === 0 ? String(hour).padStart(2, "0") : ""}
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// View
// ---------------------------------------------------------------------------

export function ScreenTimeView({ caseId }: { caseId: string }) {
  const { data: events, loading: loadingEvents } = useDataset<ScreenEvent>(caseId, "screen_events");
  const { data: usage, loading: loadingUsage } = useDataset<ScreenAppUsage>(caseId, "screen_app_usage");
  const { data: patterns } = useDataset<UsagePattern>(caseId, "usage_patterns");
  // Tier-1 helper-APK usage telemetry — a distinct dataset/collection method from
  // `screen_app_usage` above (see HelperAppUsage doc comment). Named `helperUsage`
  // here specifically so it is never confused with the `usage` variable convention
  // used elsewhere for screen_app_usage-derived data.
  const { data: helperUsage, loading: loadingHelperUsage } = useDataset<HelperAppUsage>(caseId, "usage");
  const [summary, setSummary] = useState<ScreenTimeSummary>({});
  const [manifest, setManifest] = useState<CollectorManifest | null>(null);
  const [filter, setFilter] = useState("");
  const [helperFilter, setHelperFilter] = useState("");

  useEffect(() => {
    let alive = true;
    api
      .dataset<ScreenTimeSummary>(caseId, "screen_time_summary")
      .then((s) => alive && setSummary(s ?? {}))
      .catch(() => alive && setSummary({}));
    return () => {
      alive = false;
    };
  }, [caseId]);

  useEffect(() => {
    let alive = true;
    api
      .dataset<unknown>(caseId, "collector_manifest")
      // read_derived() falls back to [] (not {}) when the file was never written, so
      // guard against an array before trusting this as the manifest object.
      .then((d) => alive && setManifest(d && typeof d === "object" && !Array.isArray(d) ? (d as CollectorManifest) : {}))
      .catch(() => alive && setManifest({}));
    return () => {
      alive = false;
    };
  }, [caseId]);

  const sortedUsage = useMemo(
    () => [...usage].sort((a, b) => (b.foreground_ms ?? 0) - (a.foreground_ms ?? 0)),
    [usage],
  );

  const filteredUsage = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return sortedUsage;
    return sortedUsage.filter((a) => (a.package ?? "").toLowerCase().includes(q));
  }, [sortedUsage, filter]);

  const sortedEvents = useMemo(
    () => [...events].sort((a, b) => (b.timestamp ?? "").localeCompare(a.timestamp ?? "")),
    [events],
  );

  const rankedHours = useMemo(() => normalizeHours(summary.most_active_hours), [summary]);

  const sortedHelperUsage = useMemo(
    () => [...helperUsage].sort((a, b) => (b.total_foreground_ms ?? 0) - (a.total_foreground_ms ?? 0)),
    [helperUsage],
  );

  const filteredHelperUsage = useMemo(() => {
    const q = helperFilter.trim().toLowerCase();
    if (!q) return sortedHelperUsage;
    return sortedHelperUsage.filter(
      (a) => (a.package ?? "").toLowerCase().includes(q) || (a.friendly_name ?? "").toLowerCase().includes(q),
    );
  }, [sortedHelperUsage, helperFilter]);

  const helperTotalMin = useMemo(
    () => helperUsage.reduce((sum, a) => sum + (a.total_foreground_min ?? 0), 0),
    [helperUsage],
  );

  // Distinguishes "the appop was denied" from "it ran and found nothing" from "it never
  // ran at all" — three different facts a zero-row table cannot tell apart on its own.
  const usageCollectorEntry = useMemo(
    () => (manifest?.collectors ?? []).find((c) => c.collector === "usage") ?? null,
    [manifest],
  );

  if (loadingEvents || loadingUsage || loadingHelperUsage || manifest === null) {
    return <div className="p-8 text-muted text-sm animate-pulse">Loading screen and app-usage data…</div>;
  }

  // ------------------------------------------------------------------------
  // Tier-1 helper-APK usage section — rendered in BOTH the Tier-0-empty early
  // return below and the full-page return, since it is an independent
  // collection method and must show up even when dumpsys yielded nothing.
  // ------------------------------------------------------------------------
  const helperSection = (
    <section className="mt-10">
      <SectionHeader
        title="App usage — Tier-1 helper APK"
        sub={`${helperUsage.length} package(s) · UsageStatsManager, 30-day window · io.erakshak.collector`}
        right={
          <span className="text-xs font-normal text-muted bg-panel-2 border border-line rounded px-2 py-0.5">
            Tier 1 — Helper APK
          </span>
        }
      />

      <div className="card p-3 mb-4 border-warn/40 bg-warn/5 text-xs text-warn leading-relaxed">
        <span className="font-semibold">A different collection method from the section below. </span>
        These rows come from <code className="font-mono">usage.json</code>, written by the installed
        Collector helper (<code className="font-mono">io.erakshak.collector</code>) calling{" "}
        <code className="font-mono">UsageStatsManager.queryUsageStats(INTERVAL_DAILY, …)</code> over a
        fixed, deterministic <strong>30-day window</strong> ending at collection time — not the OS's own
        rolling buffer read passively by the Tier-0 section above. Obtaining it required installing the
        helper APK and granting it the <code className="font-mono">GET_USAGE_STATS</code> appop, both
        device-altering Tier-1 steps recorded in the chain-of-custody trail. The two sections measure
        overlapping but distinct things — a different window, a different API — and their figures must{" "}
        <strong>never be summed together</strong>; treat disagreement between them as expected, not
        contradictory.
      </div>

      {helperUsage.length === 0 ? (
        <div className="card p-8 text-center text-muted">
          <div className="text-3xl mb-3 opacity-40">📲</div>
          <div className="text-ink font-medium mb-1">
            {usageCollectorEntry?.status === "denied"
              ? "Usage-stats access was denied"
              : usageCollectorEntry
                ? "Collected — UsageStatsManager returned no rows"
                : "Not collected"}
          </div>
          <p className="text-sm leading-relaxed max-w-lg mx-auto">
            {usageCollectorEntry?.status === "denied" ? (
              <>
                The helper ran, but the <code className="font-mono">GET_USAGE_STATS</code> appop grant was
                refused (
                <code className="font-mono">{usageCollectorEntry?.error || "reason not recorded"}</code>
                ), so it could not call <code className="font-mono">queryUsageStats</code>. This is a{" "}
                <strong>denied</strong> read, not evidence the device had no app usage.
              </>
            ) : usageCollectorEntry ? (
              <>
                The helper successfully queried <code className="font-mono">UsageStatsManager</code> and
                the 30-day window contained no packages with foreground time or a last-used record. This
                is a genuine empty result, not a denial.
              </>
            ) : (
              <>
                No <code className="font-mono">collector_manifest</code> entry for the{" "}
                <code className="font-mono">usage</code> collector was found for this case, meaning the
                Collector helper APK was never installed and run for Tier-1 collection. This is an{" "}
                <strong>un-acquired</strong> artefact, not a finding of no usage.
              </>
            )}
          </p>
        </div>
      ) : (
        <>
          <div className="flex flex-wrap gap-3 mb-4">
            <StatTile
              value={String(helperUsage.length)}
              label="Packages with usage rows"
              note="30-day UsageStatsManager window"
            />
            <StatTile
              value={fmtMinutes(helperTotalMin)}
              label="Total foreground time"
              note="Summed across all packages, 30 days"
              tone="text-accent"
            />
            <StatTile
              value={sortedHelperUsage[0]?.friendly_name || sortedHelperUsage[0]?.package || "—"}
              label="Top app by foreground time"
            />
          </div>

          <div className="mb-2 flex items-center justify-between gap-3 flex-wrap">
            <h3 className="text-sm font-semibold text-ink">Packages by 30-day foreground time</h3>
            <input
              className="input max-w-xs"
              placeholder="Filter by package or app name…"
              value={helperFilter}
              onChange={(e) => setHelperFilter(e.target.value)}
            />
          </div>

          <div className="card overflow-auto">
            <table className="w-full text-sm">
              <thead>
                <tr>
                  <th className="th">Package</th>
                  <th className="th w-40">App name</th>
                  <th className="th w-32">Foreground (30d)</th>
                  <th className="th w-44">Last used</th>
                  <th className="th w-32">Source file</th>
                </tr>
              </thead>
              <tbody>
                {filteredHelperUsage.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="td text-center text-muted text-xs py-6">
                      No packages match your filter.
                    </td>
                  </tr>
                ) : (
                  filteredHelperUsage.map((a, i) => (
                    <tr key={`${a.package}-${i}`}>
                      <td className="td font-mono text-xs break-all">
                        {a.package || <span className="text-muted italic">—</span>}
                      </td>
                      <td className="td text-xs">
                        {a.friendly_name || <span className="text-muted italic">— not resolved</span>}
                      </td>
                      <td className="td font-mono text-xs">
                        {fmtMinutes(a.total_foreground_min ?? (a.total_foreground_ms ?? 0) / 60000)}
                      </td>
                      <td className="td font-mono text-xs text-muted">{fmtTs(a.last_used)}</td>
                      <td className="td font-mono text-[11px] text-muted">{a.source_file || "usage.json"}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>

          {filteredHelperUsage.length < helperUsage.length && (
            <p className="text-xs text-muted mt-2">
              Showing {filteredHelperUsage.length} of {helperUsage.length} packages
            </p>
          )}

          <p className="text-[11px] text-muted mt-3 leading-snug">
            The parser does not yet classify these rows by category — every row currently reports{" "}
            <code className="text-ink">category: "other"</code>, so no category column is shown here. No
            keyword/suspicious heuristic is computed for this dataset either; that flag exists only on the
            Tier-0 table above.
          </p>
        </>
      )}
    </section>
  );

  // Honest empty state — say which of "not acquired" / "not present" applies and why.
  if (events.length === 0 && usage.length === 0) {
    return (
      <div className="p-6">
        <SectionHeader
          title="Screen Time & App Usage"
          sub="dumpsys power · batterystats · usagestats"
          right={
            <span className="text-xs font-normal text-muted bg-panel-2 border border-line rounded px-2 py-0.5">
              Tier 0 — Read-only
            </span>
          }
        />
        <div className="card p-6 max-w-2xl">
          <div className="text-warn font-semibold mb-2">Not acquired, or the buffers were empty at capture</div>
          <p className="text-sm text-muted leading-relaxed">
            This view is built from three read-only shell reads —{" "}
            <code className="text-ink">dumpsys power</code>,{" "}
            <code className="text-ink">dumpsys batterystats</code> and{" "}
            <code className="text-ink">dumpsys usagestats</code>. An empty dataset means either the
            acquisition never reached that step (device disconnected, shell unavailable) or the services
            returned nothing parseable.
          </p>
          <p className="text-sm text-muted leading-relaxed mt-2">
            An empty result is <em>not</em> evidence that the device was unused. These are{" "}
            <strong className="text-ink">rolling in-memory buffers</strong>: they are reset on reboot and on{" "}
            <code className="text-ink">dumpsys batterystats --reset</code>, and several OEM builds trim or
            withhold <code className="text-ink">usagestats</code> from a non-privileged shell entirely.
          </p>
          <p className="text-sm text-muted leading-relaxed mt-2">
            Root is not required for any of these reads — check the chain-of-custody trail for a{" "}
            <code className="text-ink">shell.dumpsys</code> event to confirm whether the step ran.
          </p>
        </div>

        {helperSection}
      </div>
    );
  }

  const suspiciousCount = usage.filter((a) => a.is_suspicious).length;

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <SectionHeader
        title="Screen Time & App Usage"
        sub={`${events.length} screen events · ${usage.length} apps with usage rows`}
        right={
          <span className="text-xs font-normal text-muted bg-panel-2 border border-line rounded px-2 py-0.5">
            Tier 0 — Read-only
          </span>
        }
      />

      {/* Forensic caveat — the single most important thing about this dataset. */}
      <div className="card p-3 mb-4 border-warn/40 bg-warn/5 text-xs text-warn leading-relaxed">
        <span className="font-semibold">Forensic notice: </span>
        These figures come from <code className="font-mono">dumpsys power</code>,{" "}
        <code className="font-mono">batterystats</code> and <code className="font-mono">usagestats</code>, which are{" "}
        <strong>rolling buffers, not a device-lifetime log</strong>. They cover a recent window — commonly hours
        to a few days — and are cleared by a reboot or a stats reset. Absence of an app or an hour here does not
        show it was never used; presence shows only that the OS still held the record at capture time. All screen
        durations are <strong>approximate</strong>, reconstructed by pairing ON/OFF transitions; unpaired
        transitions are dropped.
      </div>

      {/* Summary tiles */}
      <div className="flex flex-wrap gap-3 mb-4">
        <StatTile
          value={summary.total_sessions != null ? String(summary.total_sessions) : "—"}
          label="Screen-on sessions"
          note="Paired ON→OFF transitions only"
        />
        <StatTile
          value={fmtMinutes(summary.total_screen_time_min)}
          label="Screen-on time (approx.)"
          note="Sum of paired sessions in the buffer window"
          tone="text-accent"
        />
        <StatTile
          value={fmtMinutes(summary.daily_average_min)}
          label="Avg per session (approx.)"
          note="Engine key is daily_average_min but it is total ÷ session count — a per-session mean, not a per-day mean"
        />
        <StatTile
          value={String(usage.length)}
          label="Apps with usage rows"
          note="Merged batterystats + usagestats"
        />
        <StatTile
          value={String(suspiciousCount)}
          label="Keyword-matched apps"
          note="Heuristic index only — not a finding"
          tone={suspiciousCount > 0 ? "text-warn" : "text-ink"}
        />
      </div>

      {/* Hour chart */}
      <div className="mb-4">
        {rankedHours.length > 0 ? (
          <HourChart hours={rankedHours} />
        ) : (
          <div className="card p-4 text-xs text-muted leading-relaxed">
            <h3 className="text-sm font-semibold text-ink mb-1">Hour-of-day activity</h3>
            No hour-of-day distribution is available. The summary derives active hours from screen-ON events
            that carry a parseable timestamp; the events in this case either have no timestamp (state-only
            wakefulness rows) or no ON transitions survived in the buffer.
          </div>
        )}
      </div>

      {/* Patterns — heuristics raised by the engine, shown as such */}
      {patterns.length > 0 && (
        <div className="card p-4 mb-4">
          <h3 className="text-sm font-semibold text-ink mb-1">Automated observations</h3>
          <p className="text-[11px] text-muted mb-3 leading-snug">
            Generated by threshold and keyword rules over the usage rows above. Each is a prompt for an
            examiner to look, <strong className="text-warn">not a conclusion</strong>.
          </p>
          <ul className="space-y-2">
            {patterns.map((p, i) => (
              <li key={i} className="flex gap-2 items-start">
                <span
                  className={`text-[10px] uppercase tracking-wider font-semibold mt-0.5 shrink-0 ${
                    p.severity === "critical" ? "text-deletion" : p.severity === "warn" ? "text-warn" : "text-info"
                  }`}
                >
                  {p.severity ?? "info"}
                </span>
                <div>
                  <div className="text-sm text-ink">{p.description ?? p.pattern ?? "(no description)"}</div>
                  {p.pattern && <div className="text-[10px] font-mono text-muted mt-0.5">{p.pattern}</div>}
                  <CaveatList items={caveatsOf(p)} />
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Top apps */}
      <div className="mb-2 flex items-center justify-between gap-3 flex-wrap">
        <h3 className="text-sm font-semibold text-ink">Apps by foreground time</h3>
        <input
          className="input max-w-xs"
          placeholder="Filter by package…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
      </div>

      {usage.length === 0 ? (
        <div className="card p-6 text-sm text-muted leading-relaxed mb-4">
          No per-app usage rows. <code className="text-ink">batterystats</code> and{" "}
          <code className="text-ink">usagestats</code> returned nothing parseable — on many OEM builds
          usagestats is withheld from a non-privileged shell. Screen events below were still recovered.
        </div>
      ) : (
        <div className="card overflow-auto mb-4">
          <table className="w-full text-sm">
            <thead>
              <tr>
                <th className="th">Package</th>
                <th className="th w-32">Foreground</th>
                <th className="th w-28">Battery %</th>
                <th className="th w-44">Last used</th>
                <th className="th w-36">Keyword flag</th>
              </tr>
            </thead>
            <tbody>
              {filteredUsage.length === 0 ? (
                <tr>
                  <td colSpan={5} className="td text-center text-muted text-xs py-6">
                    No packages match your filter.
                  </td>
                </tr>
              ) : (
                filteredUsage.map((a, i) => {
                  const ms = a.foreground_ms ?? 0;
                  const batt = a.battery_pct_used;
                  const rowCaveats = caveatsOf(a);
                  return (
                    <tr key={`${a.package}-${i}`}>
                      <td className="td font-mono text-xs break-all">
                        {a.package || <span className="text-muted italic">—</span>}
                        {rowCaveats.length > 0 && <CaveatList items={rowCaveats} />}
                      </td>
                      <td className="td font-mono text-xs">
                        {ms > 0 ? (
                          <span title={`${ms} ms as reported`}>{fmtMinutes(a.foreground_min ?? ms / 60000)}</span>
                        ) : (
                          <span className="text-muted italic" title="Row came from usagestats, which reported no duration">
                            not reported
                          </span>
                        )}
                      </td>
                      <td className="td font-mono text-xs">
                        {batt != null && batt >= 0 ? (
                          `${batt}%`
                        ) : (
                          <span className="text-muted italic" title="batterystats did not attribute a battery share to this package">
                            not reported
                          </span>
                        )}
                      </td>
                      <td className="td font-mono text-xs text-muted">{fmtTs(a.last_used)}</td>
                      <td className="td">{a.is_suspicious ? <HeuristicFlag /> : <span className="text-muted text-xs">—</span>}</td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      )}

      <p className="text-[11px] text-muted mb-6 leading-snug">
        <strong className="text-warn">Keyword flag:</strong> a case-insensitive substring match on the package
        name against a fixed word list (vpn, proxy, tor, anon, hide, cleaner, eraser, vault, secret, encrypt,
        ghost, incognito). It is a triage index — a heuristic flag, not a finding. A flagged package may be
        entirely benign (a corporate VPN, a storage cleaner), and an unflagged package may be the relevant one.
      </p>

      {/* Screen event feed */}
      <h3 className="text-sm font-semibold text-ink mb-2">Screen event feed</h3>
      {sortedEvents.length === 0 ? (
        <div className="card p-6 text-sm text-muted leading-relaxed">
          No screen ON/OFF/UNLOCK transitions were parsed from <code className="text-ink">dumpsys power</code>.
          App-usage rows above came from a different service, so their presence does not imply this read failed —
          the power buffer may simply have held no transition history at capture time.
        </div>
      ) : (
        <div className="card overflow-auto">
          <table className="w-full text-sm">
            <thead>
              <tr>
                <th className="th w-44">Time (UTC)</th>
                <th className="th w-28">Event</th>
                <th className="th">Detail</th>
                <th className="th w-40">Derived from</th>
              </tr>
            </thead>
            <tbody>
              {sortedEvents.map((e, i) => {
                const rowCaveats = caveatsOf(e);
                return (
                  <tr key={i}>
                    <td className="td font-mono text-xs text-muted whitespace-nowrap">
                      {e.timestamp ? (
                        fmtTs(e.timestamp)
                      ) : (
                        <span className="italic" title="State-only row: dumpsys reported the current wakefulness with no time">
                          no timestamp
                        </span>
                      )}
                    </td>
                    <td className="td">
                      <EventBadge value={e.event_type ?? ""} />
                    </td>
                    <td className="td text-xs">
                      {e.summary || e.reason || (
                        e.wakefulness ? (
                          <span className="text-muted">
                            wakefulness = <span className="font-mono text-ink">{e.wakefulness}</span>{" "}
                            <span className="italic">(current state at capture, not a transition)</span>
                          </span>
                        ) : (
                          <span className="text-muted">—</span>
                        )
                      )}
                      {e.confidence && (
                        <span className="ml-2 text-[10px] uppercase tracking-wider text-muted">
                          confidence: {e.confidence}
                        </span>
                      )}
                      <CaveatList items={rowCaveats} />
                    </td>
                    <td className="td font-mono text-[11px] text-muted">{e.source || "dumpsys power"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {helperSection}
    </div>
  );
}
