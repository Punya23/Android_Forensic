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
import { ArrowRight, RadioTower } from "lucide-react";
import { api } from "../lib/api";
import { fmtTs } from "../lib/hooks";
import { bytes, SortTh, useSort } from "../components/common";

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

/** One shell command the collector ran — always recorded, success or failure. */
export interface WifiLiveCommand {
  command: string;
  ok: boolean;
  bytes: number;
  error: string;
}

// ---------------------------------------------------------------------------
// Hotspot types — mirror the backend hotspot.py payload
// ---------------------------------------------------------------------------

/** Details sub-object from hotspot.analyze_hotspot_indicators() */
export interface HotspotDetails {
  hosted_evidence: string[];
  connected_evidence: string[];
  traffic_evidence: string[];
}

/**
 * Hotspot / tethering posture payload embedded in wifi_live.
 * Every field is tri-state where applicable: null means the dump did not say
 * (a different finding from false = "the dump said no").
 */
export interface WifiLiveHotspot {
  /** null = AP state not reported by this build; true/false = active/off */
  hosted_indicator: boolean | null;
  /** null = no saved-network list available (root needed); true/false from name heuristic */
  connected_indicator: boolean | null;
  /** True when WifiConfigStoreSoftAp.xml was present (configured, not necessarily active) */
  hosted_configured: boolean;
  details: HotspotDetails;
  caveats: string[];
}

export interface WifiLiveReport {
  current?: WifiLiveCurrent | null;
  saved?: WifiLiveSavedNetwork[];
  scan_results?: WifiLiveScanResult[];
  usage?: WifiLiveUsageBucket[];
  connectivity?: WifiLiveConnectivity;
  commands?: WifiLiveCommand[];
  /** Hotspot / tethering posture — absent when the hotspot sub-step was not collected */
  hotspot?: WifiLiveHotspot | null;
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
// Hotspot Posture section
// ---------------------------------------------------------------------------

/** Three-state badge for the active-tethering indicator. */
function HostedStateBadge({ state }: { state: boolean | null | undefined }) {
  if (state === true) {
    return (
      <span className="inline-block px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wide bg-deletion/10 text-deletion border border-deletion/30 whitespace-nowrap">
        Active at collection
      </span>
    );
  }
  if (state === false) {
    return (
      <span className="inline-block px-2 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide bg-muted/10 text-muted border border-line whitespace-nowrap">
        Off at collection
      </span>
    );
  }
  // null / undefined — the build did not report an AP state at all
  return (
    <span className="inline-block px-2 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide bg-warn/10 text-warn border border-warn/30 whitespace-nowrap">
      Unknown — not reported
    </span>
  );
}

function HotspotPostureSection({ hotspot }: { hotspot: WifiLiveHotspot | null | undefined }) {
  // The backend always emits the hotspot key when the sub-step ran, even with empty
  // evidence. If the key is entirely absent, the step was not collected.
  //
  // The wording matches the capability layer's `not_collected` vocabulary on purpose
  // (`lib/capabilities.tsx`): this section makes the same re-runnability claim the
  // "Opt-in — re-run to collect" badge makes, about a Tier-0 step that genuinely does
  // run again when it is re-enabled, and an examiner should not have to learn that two
  // different sentences on two screens describe one state.
  if (hotspot === undefined) {
    return (
      <Section
        title="Hotspot Posture"
        note="Opt-in — re-run to collect: the Wi-Fi live collection step was off for this acquisition, so hotspot state was never captured. Re-run with it enabled."
      >
        <div className="card p-4 text-sm text-muted leading-relaxed">
          <strong className="text-warn">Opt-in — left off for this acquisition.</strong>{" "}
          The hotspot sub-step did not run. Absence here is not evidence that the device
          had no hotspot activity — it is a gap in the acquisition, not a finding.
        </div>
      </Section>
    );
  }

  if (hotspot === null) {
    return (
      <Section title="Hotspot Posture">
        <div className="card p-4 text-sm text-muted leading-relaxed">
          <strong className="text-warn">Collection failed.</strong>{" "}
          The hotspot analysis step ran but returned no data (an error may appear in the
          collector caveats above). This is not evidence that the device had no hotspot
          activity.
        </div>
      </Section>
    );
  }

  const { hosted_indicator, connected_indicator, hosted_configured, details, caveats } =
    hotspot;
  const connectedNames = details.connected_evidence
    .map((e) => {
      const m = e.match(/Known network '([^']+)'/);
      return m ? m[1] : null;
    })
    .filter(Boolean) as string[];
  const distinctHotspotCount = new Set(connectedNames).size;
  const trafficEvidence = details.traffic_evidence;

  return (
    <Section title="Hotspot Posture">
      {/* Standing forensic caveat — always visible */}
      <div className="card p-3 mb-4 border-warn/40 bg-warn/5 text-xs text-warn leading-relaxed">
        <span className="font-semibold">Standing forensic caveats: </span>
        <ul className="list-disc pl-4 mt-1 space-y-1">
          <li>
            <strong>Active state only.</strong> Android keeps no history of past hotspot
            sessions. A reading of "off at collection" does not exclude prior use.
          </li>
          <li>
            <strong>Name-based matching is a heuristic.</strong> Any home router can be
            named "AndroidAP1234". A match is a lead for investigation, not a finding.
          </li>
          <li>
            <strong>Configured ≠ enabled.</strong> A saved SoftAp configuration only proves
            the hotspot was set up, not that it was ever switched on.
          </li>
        </ul>
      </div>

      {/* Sub-section grid */}
      <div className="space-y-4">
        {/* 1. Current tethering / SoftAP state */}
        <div className="card p-4">
          <div className="text-[10px] uppercase tracking-wider text-muted font-semibold mb-2">
            Current tethering / SoftAP state
            <span className="ml-2 text-muted/60 font-normal normal-case">
              (volatile — captured at acquisition time only)
            </span>
          </div>
          <div className="flex items-center gap-3 flex-wrap">
            <HostedStateBadge state={hosted_indicator} />
            {hosted_configured && (
              <span className="inline-block px-2 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide bg-recovered/10 text-recovered border border-recovered/30 whitespace-nowrap">
                Hotspot configured (SoftAp.xml present)
              </span>
            )}
          </div>
          {details.hosted_evidence.length > 0 && (
            <ul className="mt-2 list-disc pl-4 space-y-0.5">
              {details.hosted_evidence.map((e, i) => (
                <li key={i} className="text-xs text-ink font-mono">
                  {e}
                </li>
              ))}
            </ul>
          )}
          {hosted_indicator === false && (
            <p className="text-xs text-muted leading-relaxed mt-2">
              The device reported its hotspot as <em>off</em> at collection time. This
              is a snapshot reading of the current state — Android keeps no hotspot
              history, so earlier hotspot use is neither shown nor excluded.
            </p>
          )}
          {hosted_indicator === null && (
            <p className="text-xs text-warn leading-relaxed mt-2">
              No SoftAp state was reported by this build's dumpsys output. This is
              not a finding that the hotspot was off — it means the state was not
              observable at Tier 0.
            </p>
          )}
          {hosted_configured && (
            <p className="text-xs text-muted leading-relaxed mt-2">
              A saved SoftAp configuration (SSID and passphrase) exists on the device.
              This proves the hotspot was <strong>configured</strong> — not that it was
              ever switched on, and the record carries no date.
            </p>
          )}
        </div>

        {/* 2. Probable hotspot networks joined */}
        <div className="card p-4">
          <div className="text-[10px] uppercase tracking-wider text-muted font-semibold mb-2">
            Probable hotspot networks joined (name-based heuristic)
          </div>
          {connected_indicator === null ? (
            <div className="text-sm text-muted leading-relaxed">
              <strong className="text-warn">Saved-network list unavailable.</strong>{" "}
              Android 10+ hides this list from non-root shells. The naming check could
              not run. This is not evidence that no hotspot network was joined.
            </div>
          ) : connected_indicator === false ? (
            <div className="text-sm text-muted leading-relaxed">
              No known network is named like a phone hotspot. Because the check is only a
              naming convention, this <em>does not exclude</em> hotspot use — the hotspot
              could have been renamed.
            </div>
          ) : (
            <>
              <div className="flex items-baseline gap-3 mb-2">
                <span className="text-2xl font-bold text-accent">{distinctHotspotCount}</span>
                <span className="text-xs text-muted">
                  distinct probable hotspot network{distinctHotspotCount !== 1 ? "s" : ""} connected to
                </span>
              </div>
              <div className="card overflow-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr>
                      <th className="th">SSID (name match)</th>
                      <th className="th">Evidence note</th>
                    </tr>
                  </thead>
                  <tbody>
                    {details.connected_evidence.map((e, i) => {
                      const ssid = connectedNames[i] ?? "—";
                      return (
                        <tr key={i}>
                          <td className="td font-medium">{ssid}</td>
                          <td className="td text-xs text-muted leading-relaxed">
                            <span className="inline-block px-1 py-0.5 rounded text-[10px] font-semibold bg-carved/10 text-carved border border-carved/30 mr-1 uppercase">
                              probable historical connection
                            </span>
                            {e.replace(`Known network '${ssid}' `, "")}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>

        {/* 3. Traffic evidence */}
        {trafficEvidence.length > 0 && (
          <div className="card p-4">
            <div className="text-[10px] uppercase tracking-wider text-muted font-semibold mb-2">
              Data-usage evidence over hotspot-named SSIDs (netstats)
            </div>
            <p className="text-xs text-muted leading-relaxed mb-2">
              The following byte-counter records appear in{" "}
              <code className="font-mono">dumpsys netstats</code> for SSIDs matching phone
              hotspot naming. Netstats uses hour-long buckets — these counters prove data
              moved, but cannot establish precise connection times or durations.
            </p>
            <ul className="list-disc pl-4 space-y-1">
              {trafficEvidence.map((e, i) => (
                <li key={i} className="text-xs text-ink font-mono">
                  {e}
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Caveats from the backend */}
        <CaveatList items={caveats.slice(1)} title="Additional caveats from the collector" />
      </div>
    </Section>
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

  const current = report?.current ?? null;
  const saved = report?.saved ?? [];
  const scans = report?.scan_results ?? [];
  const usage = report?.usage ?? [];
  const connectivity = report?.connectivity ?? {};
  const commands = report?.commands ?? [];
  const caveats = report?.caveats ?? [];
  // hotspot is `undefined` when the key is absent (not collected), `null` when it
  // failed, and a WifiLiveHotspot object when it ran successfully.
  const hotspot = report !== null ? report?.hotspot : null;

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

  // Hooks must run unconditionally on every render — computed here, before the
  // loading/error/empty-state early returns below, one independent useSort instance
  // per table so each of the three tables sorts on its own column/direction.
  const savedSort = useSort<WifiLiveSavedNetwork>(savedFiltered);
  const scansSort = useSort<WifiLiveScanResult>(scansFiltered);
  const usageSort = useSort<WifiLiveUsageBucket>(usageFiltered);

  const anyRandomised =
    Boolean(current?.randomized_mac) || saved.some((s) => s.randomized_mac);

  const nothingCollected =
    !current &&
    saved.length === 0 &&
    scans.length === 0 &&
    usage.length === 0 &&
    Object.keys(connectivity).length === 0;

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

  const header = (
    <div className="mb-5">
      <h1 className="text-xl font-bold mb-1 flex items-center gap-2">
        <RadioTower className="h-4 w-4" strokeWidth={1.75} aria-hidden /> Wi-Fi — Live State
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
                <SortTh className="th" label="SSID" sortKeyName="ssid" getValue={(s) => s.ssid} sort={savedSort} />
                <SortTh className="th w-20" label="Net ID" sortKeyName="network_id" getValue={(s) => s.network_id} sort={savedSort} />
                <SortTh className="th" label="Key mgmt" sortKeyName="key_mgmt" getValue={(s) => s.key_mgmt} sort={savedSort} />
                <SortTh className="th w-28" label="Ever connected" sortKeyName="has_ever_connected" getValue={(s) => s.has_ever_connected} sort={savedSort} />
                <SortTh className="th w-24" label="Assoc. count" sortKeyName="num_association" getValue={(s) => s.num_association} sort={savedSort} />
                <SortTh className="th w-28" label="MAC mode" sortKeyName="randomized_mac" getValue={(s) => s.randomized_mac} sort={savedSort} />
                <SortTh className="th" label="Last seen (not a join time)" sortKeyName="last_seen" getValue={(s) => s.last_seen} sort={savedSort} />
                <SortTh className="th" label="Source" sortKeyName="source" getValue={(s) => s.source} sort={savedSort} />
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
                savedSort.sorted.map((s, i) => (
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
                <SortTh className="th" label="SSID" sortKeyName="ssid" getValue={(s) => s.ssid} sort={scansSort} />
                <SortTh className="th" label="BSSID" sortKeyName="bssid" getValue={(s) => s.bssid} sort={scansSort} />
                <SortTh className="th w-24" label="Freq" sortKeyName="frequency_mhz" getValue={(s) => s.frequency_mhz} sort={scansSort} />
                <SortTh className="th w-24" label="Level" sortKeyName="level_dbm" getValue={(s) => s.level_dbm} sort={scansSort} />
                <SortTh className="th" label="Capabilities" sortKeyName="capabilities" getValue={(s) => s.capabilities} sort={scansSort} />
                <SortTh className="th" label="Seen at (approximate — derived from age)" sortKeyName="seen_at" getValue={(s) => s.seen_at} sort={scansSort} />
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
                scansSort.sorted.map((s, i) => (
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
                <SortTh className="th" label="SSID" sortKeyName="ssid" getValue={(u) => u.ssid} sort={usageSort} />
                <SortTh className="th w-28" label="Interface" sortKeyName="iface" getValue={(u) => u.iface} sort={usageSort} />
                <SortTh className="th" label="Bucket window (APPROXIMATE — hour-bucketed)" sortKeyName="bucket_start" getValue={(u) => u.bucket_start} sort={usageSort} />
                <SortTh className="th w-32" label="Duration (approximate)" sortKeyName="duration_ms" getValue={(u) => u.duration_ms} sort={usageSort} />
                <SortTh className="th w-28" label="Rx" sortKeyName="rx_bytes" getValue={(u) => u.rx_bytes} sort={usageSort} />
                <SortTh className="th w-28" label="Tx" sortKeyName="tx_bytes" getValue={(u) => u.tx_bytes} sort={usageSort} />
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
                usageSort.sorted.map((u, i) => (
                  <tr key={i}>
                    <td className="td font-medium">
                      {u.ssid || <span className="text-muted italic">—</span>}
                    </td>
                    <td className="td font-mono text-xs text-muted">{u.iface || "—"}</td>
                    <td className="td">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-mono text-xs inline-flex items-center gap-1">
                          ≈ {fmtTs(u.bucket_start)}{" "}
                          <ArrowRight className="inline h-3.5 w-3.5" strokeWidth={1.75} aria-hidden />{" "}
                          ≈ {fmtTs(u.bucket_end)}
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

      {/* ---------------- Hotspot Posture ---------------- */}
      <HotspotPostureSection hotspot={hotspot} />

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
          <div className="card p-3 space-y-1.5">
            {commands.map((c, i) => (
              <div key={i} className="flex items-start gap-2 font-mono text-[11px]">
                <span
                  className={`shrink-0 px-1 rounded text-[10px] font-semibold uppercase ${
                    c.ok ? "text-live" : "text-deletion"
                  }`}
                >
                  {c.ok ? "ok" : "failed"}
                </span>
                <span className="text-accent shrink-0">$</span>
                <span className="text-muted break-all">{c.command}</span>
                <span className="text-muted/70 shrink-0 ml-auto">
                  {c.ok ? `${c.bytes} B` : c.error || "no output"}
                </span>
              </div>
            ))}
          </div>
        </Section>
      )}
    </div>
  );
}
