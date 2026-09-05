/**
 * AppPresence — persistent app-presence reconstruction (Tier 2, root).
 *
 * The finding this view exists for: an application that is NOT on the device now, but whose
 * traces survive in the OS's own bookkeeping (packages.xml history, usagestats event protos,
 * retained APK digests). That is "it was here, and it is gone" — which is a different, and
 * much weaker, claim than "the user ran it". The two are kept visually separate on purpose.
 *
 * Nothing here is an assertion about who acted. Presence records are written by the platform,
 * are shared across users on multi-user devices, and inherit the device's clock.
 */
import { Fragment, useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import { useDataset, fmtTs } from "../lib/hooks";
import { SortTh, StatCard, useSort } from "../components/common";

/** One reconstructed package-presence record. */
export interface AppPresenceRecord {
  package: string;
  currently_installed: boolean;
  ever_installed: boolean;
  ever_executed: boolean;
  first_seen: string | null;
  last_seen: string | null;
  event_count: number;
  evidence_sources: string[];
  confidence: string;
  caveats: string[];
}

/** Optional roll-up written alongside the records. Every field is optional by design. */
export interface AppPresenceSummary {
  total_packages?: number;
  currently_installed?: number;
  uninstalled_with_evidence?: number;
  ever_executed?: number;
  note?: string;
  caveats?: string[];
}

/**
 * A row of the parsed package database (/data/system/packages.xml) — one <package>
 * (or <updated-package>) element, exactly as written by PackageManagerService.
 * Proves the package was known to PackageManager at write time. Never proves execution.
 * Shape matches PackageRecord.to_dict() in engine/triage/parsers/app_presence.py.
 */
export interface PackageEntry {
  package: string;
  code_path?: string | null;
  version_code?: number | null;
  version_name?: string | null;
  first_install?: string | null; // ISO-Z; legacy ('it') — absent from Android 12+
  last_update?: string | null; // ISO-Z ('ut')
  apk_mtime?: string | null; // ISO-Z ('ft') — APK file mtime, NOT an install time
  installer?: string | null;
  install_initiator?: string | null;
  uid?: number | null;
  is_system?: boolean;
  shared_user?: string | null;
  cert_digest?: string | null; // SHA-256 of the DER signing certificate
  granted_permissions?: string[];
  source_file?: string;
  tier?: string;
  confidence?: string;
  caveats?: string[];
}

/**
 * A single usagestats event_log record. Field presence varies by Android release, and
 * only `is_execution: true` event types prove the app's code actually ran — everything
 * else is bookkeeping, notification or device-level noise. Shape matches
 * UsageEvent.to_dict() in engine/triage/parsers/app_presence.py.
 */
export interface UsageEventEntry {
  package: string;
  event_type: number;
  event_label: string; // e.g. "ACTIVITY_RESUMED", "NOTIFICATION_SEEN"
  timestamp?: string | null; // ISO-Z
  class_name?: string | null;
  source_file?: string;
  bucket?: string; // daily | weekly | monthly | yearly
  user_id?: string | null; // Android user id from the path; never merged across users
  is_execution?: boolean;
  tier?: string;
  confidence?: string;
  caveats?: string[];
}

const CONF_COLORS: Record<string, { bg: string; text: string; border: string }> = {
  live: { bg: "#e4f4ea", text: "#1c7d3f", border: "#1c7d3f" },
  recovered: { bg: "#e2ecfa", text: "#2258a8", border: "#2258a8" },
  carved: { bg: "#f6ecd4", text: "#a6741a", border: "#a6741a" },
  deletion: { bg: "#f6dedd", text: "#a5322f", border: "#a5322f" },
};

function ConfidenceBadge({ value }: { value: string }) {
  const c = CONF_COLORS[value] ?? CONF_COLORS.carved;
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
        border: `1px solid ${c.border}`,
        whiteSpace: "nowrap",
      }}
    >
      {(value || "unknown").toUpperCase()}
    </span>
  );
}

/** Evidence basis, shown as chips so the reader can see what each row rests on. */
function EvidenceChips({ sources }: { sources: string[] }) {
  if (!sources || sources.length === 0) {
    return <span className="text-[11px] text-muted italic">no source recorded</span>;
  }
  return (
    <span className="flex flex-wrap gap-1">
      {sources.map((s, i) => (
        <span
          key={i}
          className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-panel border border-line text-muted"
        >
          {s}
        </span>
      ))}
    </span>
  );
}

/** Caveats are never dropped and never hidden behind a tooltip. */
function Caveats({ items }: { items: string[] }) {
  if (!items || items.length === 0) return null;
  return (
    <ul className="mt-1 space-y-0.5">
      {items.map((c, i) => (
        <li key={i} className="text-[11px] text-warn leading-relaxed">
          ⚠ {c}
        </li>
      ))}
    </ul>
  );
}

/** Timestamps here come from usagestats buckets — they are bounded, not exact. */
function ApproxTime({ ts }: { ts: string | null }) {
  if (!ts) return <span className="text-muted">—</span>;
  return (
    <span className="font-mono text-xs">
      <span className="text-muted mr-1" title="Approximate — derived from usagestats buckets">
        ≈
      </span>
      {fmtTs(ts)}
    </span>
  );
}

/** An event's type label, coloured to distinguish execution-class events from noise. */
function EventTypeBadge({ label, isExecution }: { label: string; isExecution?: boolean }) {
  return (
    <span
      className={`text-[10px] px-1.5 py-0.5 rounded font-mono whitespace-nowrap ${
        isExecution ? "bg-accent/15 text-accent" : "bg-panel text-muted"
      }`}
    >
      {label}
    </span>
  );
}

function StatusPill({ installed }: { installed: boolean }) {
  return installed ? (
    <span className="text-[10px] px-1.5 py-0.5 rounded bg-live/15 text-live">installed now</span>
  ) : (
    <span className="text-[10px] px-1.5 py-0.5 rounded bg-deletion/20 text-deletion">not installed now</span>
  );
}

export function AppPresenceView({ caseId }: { caseId: string }) {
  const { data: presence, loading } = useDataset<AppPresenceRecord>(caseId, "app_presence");
  const { data: packages } = useDataset<PackageEntry>(caseId, "packages");
  const { data: usageEvents } = useDataset<UsageEventEntry>(caseId, "usage_events");
  const [summary, setSummary] = useState<AppPresenceSummary>({});
  const [query, setQuery] = useState("");
  const [pkgQuery, setPkgQuery] = useState("");
  const [pkgSortAsc, setPkgSortAsc] = useState(true);
  const [expandedEventsPkg, setExpandedEventsPkg] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .dataset<AppPresenceSummary>(caseId, "app_presence_summary")
      .then((s) => alive && setSummary(s || {}))
      .catch(() => alive && setSummary({}));
    return () => {
      alive = false;
    };
  }, [caseId]);

  /** Events seen per package — corroboration depth for each presence row. */
  const eventsByPackage = useMemo(() => {
    const m = new Map<string, number>();
    for (const e of usageEvents) {
      const p = e.package ?? "";
      if (!p) continue;
      m.set(p, (m.get(p) ?? 0) + 1);
    }
    return m;
  }, [usageEvents]);

  /** The headline population: gone from the device, but the OS still remembers it. */
  const departed = useMemo(
    () =>
      presence
        .filter((r) => !r.currently_installed && (r.ever_installed || r.ever_executed || r.event_count > 0))
        .sort((a, b) => (b.last_seen || "").localeCompare(a.last_seen || "")),
    [presence]
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return presence
      .filter(
        (r) =>
          !q ||
          r.package.toLowerCase().includes(q) ||
          r.evidence_sources.some((s) => s.toLowerCase().includes(q))
      )
      .sort(
        (a, b) =>
          Number(a.currently_installed) - Number(b.currently_installed) ||
          (b.last_seen || "").localeCompare(a.last_seen || "")
      );
  }, [presence, query]);

  // Sortable state for the main package-presence table below. Computed here,
  // unconditionally, before the `if (loading) return` further down — see the
  // Rules-of-Hooks note at the top of this file's sibling views (Calls.tsx).
  const sort = useSort<AppPresenceRecord>(filtered);

  /** Raw packages.xml rows — searchable/sortable by package name, independent of the
   *  reconstructed presence table above. This is the actual parsed row, not a count. */
  const filteredPackages = useMemo(() => {
    const q = pkgQuery.trim().toLowerCase();
    const rows = packages.filter(
      (p) =>
        !q ||
        (p.package || "").toLowerCase().includes(q) ||
        (p.installer || "").toLowerCase().includes(q) ||
        (p.source_file || "").toLowerCase().includes(q)
    );
    rows.sort((a, b) => {
      const cmp = (a.package || "").localeCompare(b.package || "");
      return pkgSortAsc ? cmp : -cmp;
    });
    return rows;
  }, [packages, pkgQuery, pkgSortAsc]);

  /** Raw usage_events rows for whichever package is currently expanded in the table
   *  below, newest first. Exposes the actual per-event records, not just the count. */
  const expandedEvents = useMemo(() => {
    if (!expandedEventsPkg) return [];
    return usageEvents
      .filter((e) => e.package === expandedEventsPkg)
      .sort((a, b) => (b.timestamp || "").localeCompare(a.timestamp || ""));
  }, [usageEvents, expandedEventsPkg]);

  if (loading) return <div className="p-8 text-muted text-sm animate-pulse">Loading app-presence reconstruction…</div>;

  return (
    <div className="p-6 max-w-6xl mx-auto">
      {/* Header */}
      <div className="mb-5">
        <h1 className="text-xl font-bold mb-1 flex items-center gap-2">
          <span>📦</span> App Presence
          <span className="text-xs font-normal text-muted bg-panel-2 border border-line rounded px-2 py-0.5 ml-1">
            Tier 2 — Root
          </span>
        </h1>
        <p className="text-sm text-muted leading-relaxed">
          Which applications have existed on this device — reconstructed from the platform's own
          records rather than from the current app list, so packages removed before seizure can
          still be accounted for.
        </p>
      </div>

      {/* Forensic notice */}
      <div className="card p-3 mb-4 border-warn/40 bg-warn/5 text-xs text-warn leading-relaxed">
        <span className="font-semibold">Forensic notice: </span>
        Records were read with root from <code className="font-mono">/data/system/packages.xml</code>,{" "}
        <code className="font-mono">/data/system/usagestats/</code> and the retained package-usage
        digests. Files were copied, never modified. Timestamps are the device's own and are marked
        approximate where they derive from usagestats buckets. On a multi-user device these stores
        are per-user, so a record does not by itself identify which human used the device.
      </div>

      {presence.length === 0 ? (
        /* Honest empty state — say WHICH of "not acquired" / "not present", and why. */
        <div className="card p-6 max-w-3xl">
          <div className="text-warn font-semibold mb-2">No app-presence reconstruction in this case</div>
          <p className="text-sm text-muted leading-relaxed">
            This dataset is written only by the <strong>Tier-2 (root)</strong> collector.{" "}
            <code className="text-ink">/data/system/packages.xml</code> and{" "}
            <code className="text-ink">/data/system/usagestats/</code> are not readable by any
            non-root path on a stock Android device, so a Tier-0/Tier-1 acquisition cannot produce
            it at all.
          </p>
          <p className="text-sm text-muted leading-relaxed mt-2">
            Empty therefore means <strong>not acquired</strong>, not "no apps were ever installed".
            If root was available and the device was seized <em>before first unlock</em>, the
            usagestats directory sits under credential encryption and is unreadable until the
            device is unlocked once — that is also a "could not read", not a "nothing there".
          </p>
          {packages.length > 0 && (
            <p className="text-xs text-muted mt-3">
              For context, {packages.length} package-database entries were parsed but no presence
              reconstruction was derived from them.
            </p>
          )}
        </div>
      ) : (
        <>
          {/* Summary bar */}
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-4">
            <StatCard n={presence.length} label="Packages tracked" />
            <StatCard
              n={presence.filter((r) => r.currently_installed).length}
              label="Installed now"
              tone="text-live"
            />
            <StatCard n={departed.length} label="Gone, evidenced" tone="text-deletion" />
            <StatCard
              n={presence.filter((r) => r.ever_executed).length}
              label="Execution records"
              tone="text-accent"
            />
            <StatCard n={usageEvents.length} label="Usage events parsed" tone="text-muted" />
          </div>

          {/* Headline section: present, since uninstalled. */}
          <div
            className="card p-4 mb-5"
            style={{ borderColor: "#a5322f", background: "rgba(211,98,95,0.06)" }}
          >
            <div className="flex items-baseline justify-between gap-3 mb-1">
              <h2 className="text-sm font-semibold text-deletion">
                Present on this device, since uninstalled
              </h2>
              <span className="text-[11px] text-muted">{departed.length} package(s)</span>
            </div>
            <p className="text-xs text-muted leading-relaxed mb-3">
              These packages are <strong>not installed now</strong>, yet the platform retained
              install history, usage events or an APK digest for them. That supports "this package
              existed on this device and was later removed". It does not establish who removed it,
              when they removed it, or why — uninstalls also happen through OS cleanup, storage
              managers, enterprise policy and factory-image updates.
            </p>

            {departed.length === 0 ? (
              <p className="text-xs text-muted italic">
                None. Every tracked package is still installed — no removal was evidenced. Note that
                a package uninstalled long enough ago for its usagestats buckets to have rotated out
                would leave nothing to find, so this is not proof that nothing was ever removed.
              </p>
            ) : (
              <div className="space-y-2">
                {departed.map((r, i) => (
                  <div key={i} className="rounded-md border border-line bg-panel-2 p-3">
                    <div className="flex flex-wrap items-center gap-2 mb-1">
                      <span className="font-mono text-sm text-ink">{r.package}</span>
                      <ConfidenceBadge value={r.confidence} />
                      {r.ever_installed && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-recovered/15 text-recovered">
                          install record
                        </span>
                      )}
                      {r.ever_executed && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-accent/15 text-accent">
                          execution record
                        </span>
                      )}
                    </div>
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-[11px] text-muted mb-1">
                      <div>
                        <span className="uppercase tracking-wider">First seen (approx.)</span>
                        <div className="text-ink">
                          <ApproxTime ts={r.first_seen} />
                        </div>
                      </div>
                      <div>
                        <span className="uppercase tracking-wider">Last seen (approx.)</span>
                        <div className="text-ink">
                          <ApproxTime ts={r.last_seen} />
                        </div>
                      </div>
                      <div>
                        <span className="uppercase tracking-wider">Events</span>
                        <div className="text-ink font-mono">{r.event_count}</div>
                      </div>
                      <div>
                        <span className="uppercase tracking-wider">Events in this case</span>
                        <div className="text-ink font-mono">{eventsByPackage.get(r.package) ?? 0}</div>
                      </div>
                    </div>
                    <div className="mb-1">
                      <EvidenceChips sources={r.evidence_sources} />
                    </div>
                    <Caveats items={r.caveats} />
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Footnote the whole view depends on. */}
          <div className="card p-3 mb-4 text-xs text-muted leading-relaxed">
            <span className="font-semibold text-ink">Installation evidence is not execution evidence. </span>
            An <span className="text-recovered">install record</span> shows the package database once
            held an entry for the package — it can be produced by a silent install, a restore from
            backup, a carrier or OEM preload, or an install that was never opened. An{" "}
            <span className="text-accent">execution record</span> shows the activity manager logged a
            foreground event for it. Only the second speaks to the app being used, and neither
            attributes the use to a particular person.
          </div>

          {/* Filter */}
          <div className="mb-3">
            <input
              className="input max-w-sm"
              placeholder="Filter by package or evidence source…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>

          {/* Full table */}
          <div className="card overflow-auto">
            <table className="w-full text-sm">
              <thead>
                <tr>
                  <SortTh className="th" label="Package" sortKeyName="package" getValue={(r) => r.package} sort={sort} />
                  <SortTh className="th w-32" label="Status" sortKeyName="currently_installed" getValue={(r) => r.currently_installed} sort={sort} />
                  <SortTh
                    className="th w-40"
                    label="Install / execution"
                    sortKeyName="install_execution"
                    getValue={(r) => (r.ever_executed ? 2 : 0) + (r.ever_installed ? 1 : 0)}
                    sort={sort}
                  />
                  <SortTh className="th w-40" label="First seen (approx.)" sortKeyName="first_seen" getValue={(r) => r.first_seen} sort={sort} />
                  <SortTh className="th w-40" label="Last seen (approx.)" sortKeyName="last_seen" getValue={(r) => r.last_seen} sort={sort} />
                  <SortTh className="th w-20" label="Events" sortKeyName="event_count" getValue={(r) => r.event_count} sort={sort} />
                  <SortTh
                    className="th"
                    label="Evidence basis"
                    sortKeyName="evidence_sources"
                    getValue={(r) => r.evidence_sources?.length ?? 0}
                    sort={sort}
                  />
                  <SortTh className="th w-28" label="Confidence" sortKeyName="confidence" getValue={(r) => r.confidence} sort={sort} />
                </tr>
              </thead>
              <tbody>
                {filtered.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="text-center py-8 text-muted text-xs">
                      No packages match your filter.
                    </td>
                  </tr>
                ) : (
                  sort.sorted.map((r, i) => {
                    const isExpanded = expandedEventsPkg === r.package;
                    const caseEventCount = eventsByPackage.get(r.package) ?? 0;
                    return (
                      <Fragment key={i}>
                        <tr>
                          <td className="td font-mono text-xs text-ink align-top">
                            {r.package}
                            <Caveats items={r.caveats} />
                          </td>
                          <td className="td align-top">
                            <StatusPill installed={r.currently_installed} />
                          </td>
                          <td className="td align-top">
                            <div className="flex flex-col gap-1">
                              <span
                                className={`text-[10px] px-1.5 py-0.5 rounded self-start ${
                                  r.ever_installed ? "bg-recovered/15 text-recovered" : "bg-panel text-muted"
                                }`}
                              >
                                {r.ever_installed ? "install record" : "no install record"}
                              </span>
                              <span
                                className={`text-[10px] px-1.5 py-0.5 rounded self-start ${
                                  r.ever_executed ? "bg-accent/15 text-accent" : "bg-panel text-muted"
                                }`}
                              >
                                {r.ever_executed ? "execution record" : "no execution record"}
                              </span>
                            </div>
                          </td>
                          <td className="td align-top">
                            <ApproxTime ts={r.first_seen} />
                          </td>
                          <td className="td align-top">
                            <ApproxTime ts={r.last_seen} />
                          </td>
                          <td className="td align-top font-mono text-xs">
                            <button
                              className="underline decoration-dotted hover:text-accent disabled:no-underline disabled:cursor-default disabled:opacity-60"
                              disabled={caseEventCount === 0}
                              onClick={() => setExpandedEventsPkg(isExpanded ? null : r.package)}
                              title={
                                caseEventCount === 0
                                  ? "No raw usage_events rows recovered in this case for this package"
                                  : "Show the raw usage_events rows for this package"
                              }
                            >
                              {r.event_count} {caseEventCount > 0 ? (isExpanded ? "▴" : "▾") : ""}
                            </button>
                          </td>
                          <td className="td align-top">
                            <EvidenceChips sources={r.evidence_sources} />
                          </td>
                          <td className="td align-top">
                            <ConfidenceBadge value={r.confidence} />
                          </td>
                        </tr>
                        {isExpanded && (
                          <tr>
                            <td colSpan={8} className="td bg-panel-2 p-0">
                              <div className="p-3">
                                <div className="text-[10px] uppercase tracking-wider text-muted mb-2">
                                  Raw usagestats event_log rows for{" "}
                                  <span className="font-mono text-ink">{r.package}</span> in this case
                                  ({expandedEvents.length})
                                </div>
                                {expandedEvents.length === 0 ? (
                                  <p className="text-xs text-muted italic">
                                    No raw event rows found in this case's usage_events dataset for this
                                    package, even though {r.event_count} were counted overall — usagestats
                                    retention is capped and rotates out old interval files, so this is not
                                    evidence that nothing happened.
                                  </p>
                                ) : (
                                  <div className="overflow-auto max-h-72">
                                    <table className="w-full text-xs">
                                      <thead>
                                        <tr>
                                          <th className="th">Timestamp</th>
                                          <th className="th">Event type</th>
                                          <th className="th">Class</th>
                                          <th className="th">Bucket</th>
                                          <th className="th">User</th>
                                          <th className="th">Source file</th>
                                        </tr>
                                      </thead>
                                      <tbody>
                                        {expandedEvents.map((e, j) => (
                                          <tr key={j}>
                                            <td className="td align-top">
                                              <ApproxTime ts={e.timestamp ?? null} />
                                            </td>
                                            <td className="td align-top">
                                              <EventTypeBadge label={e.event_label} isExecution={e.is_execution} />
                                            </td>
                                            <td className="td align-top font-mono text-[11px] text-muted break-all">
                                              {e.class_name || "—"}
                                            </td>
                                            <td className="td align-top text-muted">{e.bucket || "—"}</td>
                                            <td className="td align-top font-mono text-muted">
                                              {e.user_id ?? "—"}
                                            </td>
                                            <td className="td align-top font-mono text-[11px] text-muted break-all">
                                              {e.source_file || "—"}
                                            </td>
                                          </tr>
                                        ))}
                                      </tbody>
                                    </table>
                                  </div>
                                )}
                              </div>
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>

          {filtered.length < presence.length && (
            <p className="text-xs text-muted mt-2">
              Showing {filtered.length} of {presence.length} packages
            </p>
          )}

          {/* Corroborating datasets — stated plainly so the basis is auditable. */}
          <p className="text-xs text-muted mt-3 leading-relaxed">
            Derived from {packages.length} package-database entr{packages.length === 1 ? "y" : "ies"}{" "}
            and {usageEvents.length} usagestats event{usageEvents.length === 1 ? "" : "s"} recovered in
            this case. A package with zero usage events is not thereby unused — usagestats rotates,
            and events for a removed app may have aged out entirely.
          </p>

          {/* Engine-authored summary text, rendered verbatim. */}
          {summary.note && (
            <div className="card p-3 mt-3 text-xs text-muted leading-relaxed">
              <div className="text-[10px] uppercase tracking-wider text-muted mb-1">
                Engine summary (verbatim)
              </div>
              <p className="text-ink whitespace-pre-line">{summary.note}</p>
            </div>
          )}
          {summary.caveats && summary.caveats.length > 0 && (
            <div className="card p-3 mt-3">
              <div className="text-[10px] uppercase tracking-wider text-muted mb-1">
                Dataset-level caveats
              </div>
              <Caveats items={summary.caveats} />
            </div>
          )}
        </>
      )}

      {/* Raw packages.xml rows — the underlying record, not just the count. Rendered
          independent of the reconstruction above: packages.xml can be parsed even in
          cases where no presence reconstruction ends up derived from it. */}
      <div className="mt-8 mb-5">
        <h2 className="text-base font-semibold mb-1 flex items-center gap-2">
          <span>🗂</span> Package database (packages.xml)
          <span className="text-xs font-normal text-muted bg-panel-2 border border-line rounded px-2 py-0.5 ml-1">
            Tier 2 — Root
          </span>
        </h2>
        <p className="text-sm text-muted leading-relaxed">
          Every <code className="font-mono">&lt;package&gt;</code> /{" "}
          <code className="font-mono">&lt;updated-package&gt;</code> element PackageManagerService had
          written to <code className="font-mono">/data/system/packages.xml</code> at acquisition — the
          raw record the reconstruction above is built from. On many Android versions this entry can
          survive an uninstall until PackageManager next prunes the file, so a row here is not proof
          the package is still on the device.
        </p>
        <p className="text-sm text-warn leading-relaxed mt-1.5">
          <strong>The Perms column is install-time only.</strong> It reflects packages.xml's{" "}
          <code className="font-mono">&lt;perms&gt;</code> block — permissions granted at install. Every
          runtime (dangerous) permission a user grants afterwards — camera, microphone, SMS, precise
          location, and so on — is recorded in{" "}
          <code className="font-mono">runtime-permissions.xml</code>, which this collector does not
          parse. "none parsed" here is <strong>not</strong> evidence the app holds no permissions.
        </p>
      </div>

      {packages.length === 0 ? (
        <div className="card p-6 max-w-3xl mb-8">
          <div className="text-warn font-semibold mb-2">No packages.xml rows in this case</div>
          <p className="text-sm text-muted leading-relaxed">
            This table is written only by the <strong>Tier-2 (root)</strong> collector from{" "}
            <code className="text-ink">/data/system/packages.xml</code>, which is not readable by any
            non-root path on a stock Android device. Empty therefore means <strong>not acquired</strong>{" "}
            (no root, or the file was ABX-encoded and refused rather than guessed), not "device had no
            packages". On Android 12+ this file is written as binary ABX and must be converted
            on-device with <code className="text-ink">abx2xml</code> before this parser can read it — a
            refusal there is recorded as a caveat, never as zero rows.
          </p>
        </div>
      ) : (
        <>
          <div className="mb-3">
            <input
              className="input max-w-sm"
              placeholder="Filter by package, installer or source file…"
              value={pkgQuery}
              onChange={(e) => setPkgQuery(e.target.value)}
            />
          </div>
          <div className="card overflow-auto mb-2">
            <table className="w-full text-sm">
              <thead>
                <tr>
                  <th className="th">
                    <button
                      className="flex items-center gap-1 hover:text-accent"
                      onClick={() => setPkgSortAsc((v) => !v)}
                      title="Sort by package name"
                    >
                      Package {pkgSortAsc ? "▲" : "▼"}
                    </button>
                  </th>
                  <th className="th w-24">Kind</th>
                  <th className="th w-36">First install (it)</th>
                  <th className="th w-36">Last update (ut)</th>
                  <th className="th w-36">APK mtime (ft)</th>
                  <th className="th">Installer / initiator</th>
                  <th className="th w-20">UID</th>
                  <th className="th">Signing cert (SHA-256)</th>
                  <th
                    className="th w-24"
                    title="Install-time permissions only, parsed from packages.xml's <perms> block. Runtime (dangerous) grants — camera, mic, SMS, location, etc. — live in runtime-permissions.xml, which this collector does not read, and are NOT reflected here."
                  >
                    Perms (install-time)
                  </th>
                </tr>
              </thead>
              <tbody>
                {filteredPackages.length === 0 ? (
                  <tr>
                    <td colSpan={9} className="text-center py-8 text-muted text-xs">
                      No package-database rows match your filter.
                    </td>
                  </tr>
                ) : (
                  filteredPackages.map((p, i) => (
                    <tr key={i}>
                      <td className="td font-mono text-xs text-ink align-top">
                        {p.package}
                        <Caveats items={p.caveats || []} />
                      </td>
                      <td className="td align-top">
                        <span
                          className={`text-[10px] px-1.5 py-0.5 rounded ${
                            p.is_system ? "bg-panel-2 text-muted border border-line" : "bg-live/15 text-live"
                          }`}
                        >
                          {p.is_system ? "system" : "user"}
                        </span>
                      </td>
                      <td className="td align-top">
                        <span className="font-mono text-xs">{fmtTs(p.first_install)}</span>
                      </td>
                      <td className="td align-top">
                        <span className="font-mono text-xs">{fmtTs(p.last_update)}</span>
                      </td>
                      <td className="td align-top">
                        <span className="font-mono text-xs">{fmtTs(p.apk_mtime)}</span>
                      </td>
                      <td className="td align-top text-xs">
                        <div className="text-ink">{p.installer || "—"}</div>
                        {p.install_initiator && (
                          <div className="text-muted text-[10px]">via {p.install_initiator}</div>
                        )}
                      </td>
                      <td className="td align-top font-mono text-xs">
                        {p.uid ?? "—"}
                        {p.shared_user && (
                          <div className="text-muted text-[10px] font-sans">shared: {p.shared_user}</div>
                        )}
                      </td>
                      <td className="td align-top font-mono text-[11px] text-muted break-all">
                        {p.cert_digest || "—"}
                      </td>
                      <td className="td align-top text-xs">
                        {p.granted_permissions && p.granted_permissions.length > 0 ? (
                          <details>
                            <summary className="cursor-pointer text-accent">
                              {p.granted_permissions.length}
                            </summary>
                            <ul className="mt-1 space-y-0.5 max-w-xs">
                              {p.granted_permissions.map((perm, j) => (
                                <li key={j} className="font-mono text-[10px] text-muted break-all">
                                  {perm}
                                </li>
                              ))}
                            </ul>
                          </details>
                        ) : (
                          <span
                            className="text-muted italic"
                            title="No install-time <perms> entries were parsed. This does NOT mean the app holds no permissions — runtime-granted dangerous permissions (camera, mic, SMS, location, ...) are recorded in runtime-permissions.xml, not here."
                          >
                            none parsed
                          </span>
                        )}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
          {filteredPackages.length < packages.length && (
            <p className="text-xs text-muted mb-6">
              Showing {filteredPackages.length} of {packages.length} package-database rows
            </p>
          )}
        </>
      )}
    </div>
  );
}
