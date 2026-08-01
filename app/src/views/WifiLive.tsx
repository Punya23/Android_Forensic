/**
 * WifiLive.tsx — the NON-ROOT Wi-Fi surface (dataset "wifi_live", an OBJECT).
 *
 * This view is deliberately separate from WiFi.tsx. WiFi.tsx renders the Tier-2
 * (root) credential store read out of /data/misc/wifi — i.e. plaintext PSKs.
 * This view renders what the *shell UID* can see without root: `dumpsys wifi`,
 * the scan cache and NetworkStatsManager buckets. It contains no credentials and
 * cannot contain any.
 *
 * Everything here is volatile runtime state. Two standing caveats apply to the
 * whole page and are rendered unconditionally, not hidden behind a tooltip:
 *   1. dumpsys exposes no reliable per-join timestamp, so "the device connected to
 *      SSID X at time T" cannot be sourced from this dataset.
 *   2. This state is held in memory / a rolling cache and is lost on reboot.
 */
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { api } from "../lib/api";
import { fmtTs } from "../lib/hooks";
import { bytes } from "../components/common";

// ---------------------------------------------------------------------------
// Types (declared locally — this view owns its own contract)
// ---------------------------------------------------------------------------

export interface WifiLiveCurrent {
  ssid: string;
  bssid: string;
  mac_address: string;
  rssi: number | null;
  link_speed_mbps: number | null;
  frequency_mhz: number | null;
  supplicant_state: string;
  is_connected: boolean;
  randomized_mac: boolean;
  captured_at: string | null;
  caveats?: string[];
}

export interface WifiLiveSavedNetwork {
  ssid: string;
  network_id: number | string | null;
  key_mgmt: string;
  has_ever_connected: boolean | null;
  num_association: number | null;
  randomized_mac: boolean;
  is_most_recently_connected: boolean;
  last_seen: string | null;
  source: string;
  caveats?: string[];
}

export interface WifiLiveScanResult {
  ssid: string;
  bssid: string;
  frequency_mhz: number | null;
  level_dbm: number | null;
  capabilities: string;
  seen_at: string | null;
  age_ms: number | null;
}

export interface WifiLiveUsageBucket {
  ssid: string;
  iface: string;
  bucket_start: string | null;
  bucket_end: string | null;
  duration_ms: number | null;
  rx_bytes: number | null;
  tx_bytes: number | null;
  approximate: boolean;
}

/** dumpsys connectivity is a flat property bag; values are scalars only. */
export type WifiLiveConnectivity = Record<string, string | number | boolean | null>;

export interface WifiLiveReport {
  current?: WifiLiveCurrent | null;
  saved?: WifiLiveSavedNetwork[];
  scan_results?: WifiLiveScanResult[];
  usage?: WifiLiveUsageBucket[];
  connectivity?: WifiLiveConnectivity;
  commands?: string[];
  caveats?: string[];
}

// ---------------------------------------------------------------------------
// Small shared pieces
// ---------------------------------------------------------------------------

function ApproxChip({ label }: { label: string }) {
  return (
    <span className="inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide bg-carved/10 text-carved border border-carved/30 whitespace-nowrap">
      {label}
    </span>
  );
}

function RandomisedMacChip() {
  return (
    <span className="inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide bg-recovered/10 text-recovered border border-recovered/30 whitespace-nowrap">
      randomised MAC
    </span>
  );
}

function CaveatList({ items, title }: { items: string[]; title?: string }) {
  if (items.length === 0) return null;
  return (
    <div className="mt-2">
      {title && (
        <div className="text-[10px] uppercase tracking-wider text-warn font-semibold mb-1">
          {title}
        </div>
      )}
      <ul className="list-disc pl-4 space-y-0.5">
        {items.map((c, i) => (
          <li key={i} className="text-xs text-warn leading-relaxed">
            {c}
          </li>
        ))}
      </ul>
    </div>
  );
}

function Field({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-muted mb-0.5">{label}</div>
      <div className="text-sm text-ink font-mono break-all">{value}</div>
    </div>
  );
}

function Section({
  title,
  count,
  note,
  children,
}: {
  title: string;
  count?: number;
  note?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="mb-6">
      <div className="flex items-baseline gap-2 mb-2">
        <h2 className="text-sm font-semibold text-ink uppercase tracking-wider">{title}</h2>
        {count !== undefined && <span className="text-xs text-muted">({count})</span>}
      </div>
      {note && <p className="text-xs text-muted leading-relaxed mb-2">{note}</p>}
      {children}
    </section>
  );
}

/**
 * The MAC-randomisation explainer. This is rendered as visible body copy rather
 * than a tooltip because it changes what the address *means* to an investigator
 * subpoenaing router or ISP logs.
 */
function RandomisedMacExplainer() {
  return (
    <div className="card p-3 mb-3 border-recovered/40 bg-recovered/5 text-xs text-recovered leading-relaxed">
      <span className="font-semibold">MAC randomisation is in effect on one or more entries. </span>
      Android 10+ generates a <em>per-SSID randomised</em> MAC address. The address shown here is
      that randomised address — it is <strong>not</strong> the device's hardware (factory) MAC, and
      it will differ for every other SSID this handset has joined. It is, however, exactly the
      address the access point saw and therefore the value to match against router, captive-portal
      or ISP association logs <em>for that SSID only</em>. Do not present it as a device-unique
      identifier, and do not expect it to correlate across networks.
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main view
// ---------------------------------------------------------------------------

export function WifiLiveView({ caseId }: { caseId: string }) {
  const [report, setReport] = useState<WifiLiveReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    let alive = true;
    setLoading(true);
    api
      .dataset<WifiLiveReport>(caseId, "wifi_live")
      .then((d) => alive && setReport(d && typeof d === "object" ? d : {}))
      .catch((e) => {
        if (!alive) return;
        setError(e instanceof Error ? e.message : String(e));
        setReport({});
      })
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [caseId]);

  if (loading) {
    return (
      <div className="p-8 text-muted text-sm animate-pulse">Loading live Wi-Fi state…</div>
    );
  }

  if (error) {
    return (
      <div className="p-8 text-sm text-deletion">Failed to load live Wi-Fi state: {error}</div>
    );
  }

  const current = report?.current ?? null;
  const saved = report?.saved ?? [];
  const scans = report?.scan_results ?? [];
  const usage = report?.usage ?? [];
  const connectivity = report?.connectivity ?? {};
  const commands = report?.commands ?? [];
  const caveats = report?.caveats ?? [];

  const q = filter.trim().toLowerCase();
  const savedFiltered = q
    ? saved.filter(
        (s) =>
          s.ssid?.toLowerCase().includes(q) ||
          s.key_mgmt?.toLowerCase().includes(q) ||
          s.source?.toLowerCase().includes(q),
      )
    : saved;
  const scansFiltered = q
    ? scans.filter(
        (s) =>
          s.ssid?.toLowerCase().includes(q) ||
          s.bssid?.toLowerCase().includes(q) ||
          s.capabilities?.toLowerCase().includes(q),
      )
    : scans;
  const usageFiltered = q
    ? usage.filter((u) => u.ssid?.toLowerCase().includes(q) || u.iface?.toLowerCase().includes(q))
    : usage;

  const anyRandomised =
    Boolean(current?.randomized_mac) || saved.some((s) => s.randomized_mac);

  const nothingCollected =
    !current &&
    saved.length === 0 &&
    scans.length === 0 &&
    usage.length === 0 &&
    Object.keys(connectivity).length === 0;

  const header = (
    <div className="mb-5">
      <h1 className="text-xl font-bold mb-1 flex items-center gap-2">
        <span>📡</span> Wi-Fi — Live State
        <span className="text-xs font-normal text-muted bg-panel-2 border border-line rounded px-2 py-0.5 ml-1">
          Tier 0 — Read-only, volatile
        </span>
      </h1>
      <p className="text-sm text-muted leading-relaxed">
        The non-root Wi-Fi surface: current association, the saved-network <em>list</em>, the
        scan-result cache and per-SSID data-usage buckets, read as the shell UID. This view holds{" "}
        <strong>no credentials and cannot hold any</strong> — the PSK store is unreadable without
        root. For recovered passwords see <em>Wi-Fi Passwords</em>, which is a separate{" "}
        <span className="text-ink">Tier 2 — Root</span> acquisition of{" "}
        <code className="font-mono">/data/misc/wifi/</code>. The two views describe the same
        networks from two different evidential positions and should be cited separately.
      </p>
    </div>
  );

  // Standing caveats apply whether or not any rows came back.
  const standingNotice = (
    <div className="card p-3 mb-4 border-warn/40 bg-warn/5 text-xs text-warn leading-relaxed">
      <span className="font-semibold">Standing forensic caveats for this entire page: </span>
      <ul className="list-disc pl-4 mt-1 space-y-1">
        <li>
          <strong>No per-join timestamps exist here.</strong> <code className="font-mono">dumpsys wifi</code>{" "}
          does not record when the device associated with a given network. Any claim of the form
          &ldquo;the device connected to SSID X at time T&rdquo; must be sourced from elsewhere —
          system logs, router/ISP records, or a Tier-2 acquisition — and never from this table.
        </li>
        <li>
          <strong>This data is volatile.</strong> The association state, scan cache and supplicant
          state are runtime memory. They are lost on reboot and change continuously while the
          device is powered. What you see is a snapshot taken at acquisition time, not a durable
          record, and it is not reproducible from the same device later.
        </li>
        <li>
          Presence in the saved-network list proves the network was <em>configured</em>. It does not
          prove the device was ever physically in range of it, and{" "}
          <code className="font-mono">has_ever_connected</code> is the OS&apos;s own flag, not an
          independent observation.
        </li>
      </ul>
      <CaveatList items={caveats} title="Caveats reported by the collector" />
    </div>
  );

  if (nothingCollected) {
    return (
      <div className="p-6 max-w-5xl mx-auto">
        {header}
        {standingNotice}
        <div className="card p-8 max-w-3xl">
          <div className="text-warn font-semibold mb-2">
            No live Wi-Fi state was captured for this case
          </div>
          <p className="text-sm text-muted leading-relaxed">
            An empty result here means one of two distinct things, and this tool will not guess
            between them:
          </p>
          <ul className="list-disc pl-5 mt-2 space-y-1.5 text-sm text-muted leading-relaxed">
            <li>
              <strong className="text-ink">Not acquired.</strong> The dumpsys/netstats collection
              step did not run for this acquisition, so no observation was made. This is not
              evidence that the device had no Wi-Fi activity.
            </li>
            <li>
              <strong className="text-ink">Acquired but unreadable.</strong> From Android 11 the
              shell UID is denied large parts of{" "}
              <code className="text-ink font-mono">dumpsys wifi</code>, and per-SSID network stats
              require <code className="text-ink font-mono">PACKAGE_USAGE_STATS</code>. A redacted
              or refused dump also produces an empty dataset.
            </li>
          </ul>
          <p className="text-sm text-muted leading-relaxed mt-3">
            Either way, absence here is <em>absence of observation</em>, not absence of activity.
            Check the chain-of-custody audit trail for whether the collection step was attempted
            and what the shell returned.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 max-w-5xl mx-auto">
      {header}
      {standingNotice}
      {anyRandomised && <RandomisedMacExplainer />}

      {/* Summary */}
      <div className="flex flex-wrap gap-3 mb-4">
        {[
          { label: "Saved networks", value: saved.length },
          { label: "Scan results", value: scans.length },
          { label: "Usage buckets", value: usage.length },
          { label: "Associated now", value: current?.is_connected ? "yes" : "no" },
        ].map(({ label, value }) => (
          <div key={label} className="card px-4 py-2 flex flex-col items-center min-w-[110px]">
            <span className="text-xl font-bold text-accent">{value}</span>
            <span className="text-xs text-muted mt-0.5">{label}</span>
          </div>
        ))}
      </div>

      {/* ---------------- Current association ---------------- */}
      <Section title="Current association">
        {current === null ? (
          <div className="card p-4 text-sm text-muted leading-relaxed">
            No current association was recorded at capture time. This means the snapshot showed no
            connected network — it does <em>not</em> mean the Wi-Fi radio was off, and it says
            nothing about associations before or after the snapshot.
          </div>
        ) : (
          <div className="card p-4">
            <div className="flex flex-wrap items-center gap-2 mb-3">
              <span className="text-base font-semibold text-ink">{current.ssid || "—"}</span>
              <span
                className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide border ${
                  current.is_connected
                    ? "bg-live/10 text-live border-live/30"
                    : "bg-muted/10 text-muted border-line"
                }`}
              >
                {current.is_connected ? "connected" : "not connected"}
              </span>
              {current.randomized_mac && <RandomisedMacChip />}
            </div>

            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <Field label="BSSID (AP)" value={current.bssid || "—"} />
              <Field
                label={current.randomized_mac ? "MAC (randomised, per-SSID)" : "MAC address"}
                value={current.mac_address || "—"}
              />
              <Field label="Supplicant state" value={current.supplicant_state || "—"} />
              <Field
                label="RSSI"
                value={current.rssi != null ? `${current.rssi} dBm` : "—"}
              />
              <Field
                label="Link speed"
                value={current.link_speed_mbps != null ? `${current.link_speed_mbps} Mbps` : "—"}
              />
              <Field
                label="Frequency"
                value={current.frequency_mhz != null ? `${current.frequency_mhz} MHz` : "—"}
              />
              <Field label="Captured at" value={fmtTs(current.captured_at)} />
            </div>

            {current.randomized_mac && (
              <p className="text-xs text-recovered leading-relaxed mt-3">
                The MAC above is the randomised address this device presents to{" "}
                <strong>{current.ssid || "this SSID"}</strong> only. Match it against that access
                point&apos;s association logs; it is not the hardware MAC and will not appear on any
                other network.
              </p>
            )}

            <p className="text-xs text-muted leading-relaxed mt-2">
              <strong className="text-warn">Captured at</strong> is the time the tool read the
              dump — it is <em>not</em> the time the device joined this network. No join time is
              available from this source.
            </p>

            <CaveatList items={current.caveats ?? []} title="Caveats" />
          </div>
        )}
      </Section>

      {/* Filter */}
      <div className="mb-3">
        <input
          className="input max-w-sm"
          placeholder="Filter tables by SSID, BSSID, key mgmt or interface…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
      </div>

      {/* ---------------- Saved networks ---------------- */}
      <Section
        title="Saved networks"
        count={saved.length}
        note={
          <>
            The configured-network list only. Passwords are not present in this source and were not
            requested. <span className="text-warn">Last seen is not a connection time</span> — it is
            whatever the configuration record last recorded, and cannot support a &ldquo;connected
            at&rdquo; assertion.
          </>
        }
      >
        <div className="card overflow-auto">
          <table className="w-full text-sm">
            <thead>
              <tr>
                <th className="th">SSID</th>
                <th className="th w-20">Net ID</th>
                <th className="th">Key mgmt</th>
                <th className="th w-28">Ever connected</th>
                <th className="th w-24">Assoc. count</th>
                <th className="th w-28">MAC mode</th>
                <th className="th">Last seen (not a join time)</th>
                <th className="th">Source</th>
              </tr>
            </thead>
            <tbody>
              {savedFiltered.length === 0 ? (
                <tr>
                  <td colSpan={8} className="td text-center text-muted text-xs py-6">
                    {saved.length === 0
                      ? "No saved-network list was returned by dumpsys — either not acquired, or redacted for the shell UID on Android 11+."
                      : "No saved networks match your filter."}
                  </td>
                </tr>
              ) : (
                savedFiltered.map((s, i) => (
                  <tr key={i}>
                    <td className="td font-medium">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span>{s.ssid || <span className="text-muted italic">—</span>}</span>
                        {s.is_most_recently_connected && (
                          <span className="inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide bg-live/10 text-live border border-live/30 whitespace-nowrap">
                            most recent
                          </span>
                        )}
                      </div>
                      <CaveatList items={s.caveats ?? []} />
                    </td>
                    <td className="td font-mono text-xs text-muted">
                      {s.network_id != null ? String(s.network_id) : "—"}
                    </td>
                    <td className="td font-mono text-xs">{s.key_mgmt || "—"}</td>
                    <td className="td text-xs">
                      {s.has_ever_connected == null ? (
                        <span className="text-muted italic">not stated</span>
                      ) : s.has_ever_connected ? (
                        <span className="text-live">yes (OS flag)</span>
                      ) : (
                        <span className="text-muted">no (OS flag)</span>
                      )}
                    </td>
                    <td className="td font-mono text-xs">
                      {s.num_association != null ? s.num_association : "—"}
                    </td>
                    <td className="td">
                      {s.randomized_mac ? (
                        <RandomisedMacChip />
                      ) : (
                        <span className="text-xs text-muted">hardware MAC</span>
                      )}
                    </td>
                    <td className="td font-mono text-xs text-muted">{fmtTs(s.last_seen)}</td>
                    <td className="td font-mono text-[11px] text-muted break-all">
                      {s.source || "—"}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
        {anyRandomised && (
          <p className="text-xs text-muted leading-relaxed mt-2">
            Rows marked <span className="text-recovered">randomised MAC</span> use a per-SSID
            address. That address — not the hardware MAC — is what the access point logged, and it
            is the value to compare against router/ISP records for that SSID.
          </p>
        )}
      </Section>

      {/* ---------------- Scan results ---------------- */}
      <Section
        title="Scan-result cache"
        count={scans.length}
        note={
          <>
            Access points the radio saw during its last scans. Presence here shows the AP was
            <em> in range at scan time</em>; it does not show the device connected to it. Timestamps
            are derived from the cache age relative to the capture and are therefore approximate.
          </>
        }
      >
        <div className="card overflow-auto">
          <table className="w-full text-sm">
            <thead>
              <tr>
                <th className="th">SSID</th>
                <th className="th">BSSID</th>
                <th className="th w-24">Freq</th>
                <th className="th w-24">Level</th>
                <th className="th">Capabilities</th>
                <th className="th">Seen at (approximate — derived from age)</th>
              </tr>
            </thead>
            <tbody>
              {scansFiltered.length === 0 ? (
                <tr>
                  <td colSpan={6} className="td text-center text-muted text-xs py-6">
                    {scans.length === 0
                      ? "No scan cache was returned. Scan results are short-lived and are cleared on reboot or radio toggle — absence proves nothing about coverage."
                      : "No scan results match your filter."}
                  </td>
                </tr>
              ) : (
                scansFiltered.map((s, i) => (
                  <tr key={i}>
                    <td className="td font-medium">
                      {s.ssid || <span className="text-muted italic">&lt;hidden&gt;</span>}
                    </td>
                    <td className="td font-mono text-xs">{s.bssid || "—"}</td>
                    <td className="td font-mono text-xs">
                      {s.frequency_mhz != null ? `${s.frequency_mhz} MHz` : "—"}
                    </td>
                    <td className="td font-mono text-xs">
                      {s.level_dbm != null ? `${s.level_dbm} dBm` : "—"}
                    </td>
                    <td className="td font-mono text-[11px] text-muted break-all">
                      {s.capabilities || "—"}
                    </td>
                    <td className="td">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-mono text-xs text-muted">
                          {s.seen_at ? `≈ ${fmtTs(s.seen_at)}` : "—"}
                        </span>
                        <ApproxChip label="approximate" />
                      </div>
                      <div className="text-[10px] text-muted mt-0.5">
                        age at capture:{" "}
                        {s.age_ms != null ? `${(s.age_ms / 1000).toFixed(1)} s` : "not reported"}
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </Section>

      {/* ---------------- Usage buckets ---------------- */}
      <Section
        title="Per-SSID data usage"
        count={usage.length}
        note={
          <>
            NetworkStatsManager totals. <span className="text-carved font-semibold">Every window
            below is hour-bucketed and approximate</span> — the platform aligns usage to bucket
            boundaries, so a bucket start is not the moment traffic began and a bucket end is not
            the moment it stopped. Byte counts are per bucket, not per session.
          </>
        }
      >
        <div className="card overflow-auto">
          <table className="w-full text-sm">
            <thead>
              <tr>
                <th className="th">SSID</th>
                <th className="th w-28">Interface</th>
                <th className="th">Bucket window (APPROXIMATE — hour-bucketed)</th>
                <th className="th w-32">Duration (approximate)</th>
                <th className="th w-28">Rx</th>
                <th className="th w-28">Tx</th>
              </tr>
            </thead>
            <tbody>
              {usageFiltered.length === 0 ? (
                <tr>
                  <td colSpan={6} className="td text-center text-muted text-xs py-6">
                    {usage.length === 0
                      ? "No per-SSID usage buckets. NetworkStatsManager needs PACKAGE_USAGE_STATS, which a Tier-0 read does not hold — treat this as not acquired, not as zero usage."
                      : "No usage buckets match your filter."}
                  </td>
                </tr>
              ) : (
                usageFiltered.map((u, i) => (
                  <tr key={i}>
                    <td className="td font-medium">
                      {u.ssid || <span className="text-muted italic">—</span>}
                    </td>
                    <td className="td font-mono text-xs text-muted">{u.iface || "—"}</td>
                    <td className="td">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-mono text-xs">
                          ≈ {fmtTs(u.bucket_start)} → ≈ {fmtTs(u.bucket_end)}
                        </span>
                        <ApproxChip label="approximate · hour bucket" />
                      </div>
                      <div className="text-[10px] text-carved mt-0.5">
                        Approximate: aligned to an hour boundary by the platform, not an observed
                        start/stop time.
                        {u.approximate === false && (
                          <> (collector did not flag this row, but the bucketing still applies)</>
                        )}
                      </div>
                    </td>
                    <td className="td">
                      <div className="flex items-center gap-1.5 flex-wrap">
                        <span className="font-mono text-xs">
                          {u.duration_ms != null
                            ? `≈ ${(u.duration_ms / 60000).toFixed(0)} min`
                            : "—"}
                        </span>
                        <ApproxChip label="approx" />
                      </div>
                    </td>
                    <td className="td font-mono text-xs">
                      {u.rx_bytes != null ? bytes(u.rx_bytes) : "—"}
                    </td>
                    <td className="td font-mono text-xs">
                      {u.tx_bytes != null ? bytes(u.tx_bytes) : "—"}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </Section>

      {/* ---------------- Connectivity ---------------- */}
      {Object.keys(connectivity).length > 0 && (
        <Section
          title="Connectivity state"
          note="Raw scalar properties from the connectivity dump, reproduced verbatim and uninterpreted."
        >
          <div className="card p-4 grid grid-cols-2 md:grid-cols-3 gap-3">
            {Object.entries(connectivity).map(([k, v]) => (
              <Field key={k} label={k} value={v === null ? "—" : String(v)} />
            ))}
          </div>
        </Section>
      )}

      {/* ---------------- Provenance ---------------- */}
      {commands.length > 0 && (
        <Section
          title="Commands executed"
          count={commands.length}
          note="Exact shell commands this dataset was derived from. All are read-only; none modify device state. The same list is recorded in the chain-of-custody audit trail."
        >
          <div className="card p-3 space-y-1">
            {commands.map((c, i) => (
              <div key={i} className="font-mono text-[11px] text-muted break-all">
                <span className="text-accent">$</span> {c}
              </div>
            ))}
          </div>
        </Section>
      )}
    </div>
  );
}
