import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { useDataset, fmtTs } from "../lib/hooks";
import { SectionHeader, StatCard } from "../components/common";

// ---------------------------------------------------------------------------
// Types — declared locally on purpose (the orchestrator owns lib/types.ts).
// Every field is optional: both producers are best-effort dumpsys / bt_config.conf
// parsers, and a missing field must render as "not recorded", never as a default.
// ---------------------------------------------------------------------------

/** One device seen by the non-root `dumpsys bluetooth_manager` parser. */
export interface BluetoothDevice {
  mac?: string;
  name?: string;
  bond_state?: string;
  connected?: boolean;
  last_seen?: string;
  device_class?: string;
  is_paired?: boolean;
  confidence?: string;
  source?: string;
}

/** One persistent bond record recovered from `/data/misc/bluedroid/bt_config.conf` (root). */
export interface BluetoothBond {
  address?: string;
  name?: string;
  dev_class_label?: string;
  dev_type_label?: string;
  addr_type_label?: string;
  vendor?: string;
  bond_timestamp?: string;
  /** The engine's own statement of what the timestamp actually means. Always rendered. */
  timestamp_meaning?: string;
  has_link_key?: boolean;
  le_key_types?: string[] | string;
  caveats?: string[];
}

export interface BluetoothSummary {
  total?: number;
  connected?: number;
  paired?: number;
  with_name?: number;
  by_bond_state?: Record<string, number>;
  by_class?: Record<string, number>;
}

export interface BluetoothBondReport {
  adapter?: Record<string, unknown> | null;
  bonds?: BluetoothBond[];
  /** True when the bond store existed but was encrypted (FBE) and could not be parsed. */
  encrypted?: boolean;
  caveats?: string[];
}

/**
 * One row of `collector_bluetooth` — the Tier-1 helper APK's own radio scan
 * (bluetooth.json, via the Collector's dump_all action). A THIRD, distinct source from
 * the two above: not dumpsys, not the root bt_config.conf bond store. Per the engine's
 * own convention (pipeline.py, next to the write_derived call for this dataset):
 * "Kept separate ... the helper's JSON has a different shape, and merging the two would
 * mean a row's fields no longer imply how it was obtained."
 *
 * Shape verified against engine/triage/parsers/collector.py::parse_bluetooth_json. Rows
 * keep an APK-assigned `type` ("adapter" | "bonded_device" | "unknown"), and only a
 * subset of fields is populated per type — `enabled`/`state` come from the adapter row
 * only, `bond_state`/`device_class`/`device_type`/`uuids` from bonded-device rows only.
 * On a SecurityException reading device details, the APK writes the literal string
 * "[permission_denied]" into `name`/`address` rather than omitting the row.
 */
export interface CollectorBluetoothItem {
  type?: string;
  name?: string;
  address?: string;
  bond_state?: string;
  device_type?: string;
  device_class?: number | null;
  enabled?: boolean | null;
  state?: string;
  uuids?: string[];
  source_file?: string;
}

// ---------------------------------------------------------------------------
// Small presentational helpers
// ---------------------------------------------------------------------------

const CONF_COLORS: Record<string, { bg: string; text: string; border: string }> = {
  live: { bg: "#e4f4ea", text: "#1c7d3f", border: "#1c7d3f" },
  recovered: { bg: "#e2ecfa", text: "#2258a8", border: "#2258a8" },
  carved: { bg: "#f6ecd4", text: "#a6741a", border: "#a6741a" },
  deletion: { bg: "#f6dedd", text: "#a5322f", border: "#a5322f" },
};

function ConfidenceBadge({ value }: { value?: string }) {
  if (!value) return <span className="text-muted text-xs italic">unstated</span>;
  const key = value.toLowerCase().split("_")[0];
  const c = CONF_COLORS[key] ?? CONF_COLORS.recovered;
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
      {value.toUpperCase()}
    </span>
  );
}

function TierBadge({ label }: { label: string }) {
  return (
    <span className="text-xs font-normal text-muted bg-panel-2 border border-line rounded px-2 py-0.5">
      {label}
    </span>
  );
}

/** True for the placeholder octets Android 8+ hands to non-privileged dumpsys callers. */
function isOctetRedacted(octet: string): boolean {
  return /^[xX*]{2}$/.test(octet);
}

/**
 * Renders a MAC address, showing an Android-redacted address as visibly partial rather
 * than as a whole identifier. A redacted address identifies at most the last two octets —
 * it cannot be attributed to a specific device on its own.
 */
function MacCell({ mac }: { mac?: string }) {
  if (!mac) return <span className="text-muted text-xs italic">— not recorded</span>;
  const octets = mac.split(":");
  const redactedCount = octets.filter(isOctetRedacted).length;
  if (redactedCount === 0) {
    return <span className="font-mono text-xs">{mac}</span>;
  }
  return (
    <span className="whitespace-nowrap">
      <span className="font-mono text-xs">
        {octets.map((o, i) => (
          <span key={i} className={isOctetRedacted(o) ? "text-muted" : "text-ink font-semibold"}>
            {i > 0 ? ":" : ""}
            {o}
          </span>
        ))}
      </span>
      <span className="ml-2 text-[10px] uppercase tracking-wider text-warn">partial</span>
    </span>
  );
}

/** Address types for which an OUI/vendor lookup carries no meaning. */
function isRandomAddress(addrType?: string): boolean {
  const t = (addrType || "").toLowerCase();
  return (
    t.includes("random") ||
    t.includes("rpa") ||
    t.includes("resolvable") ||
    t.includes("private")
  );
}

function VendorCell({ vendor, addrType }: { vendor?: string; addrType?: string }) {
  if (isRandomAddress(addrType)) {
    // An OUI lookup on a random / resolvable-private address describes nothing about the
    // manufacturer — the top 24 bits are generated, not assigned.
    return <span className="text-muted text-xs italic">n/a — random address</span>;
  }
  if (!vendor) return <span className="text-muted text-xs italic">— not resolved</span>;
  if (!addrType) {
    return (
      <span className="text-xs">
        {vendor}
        <span className="block text-[10px] text-warn">address type unknown — OUI unverified</span>
      </span>
    );
  }
  return <span className="text-xs">{vendor}</span>;
}

function LinkKeyCell({ bond }: { bond: BluetoothBond }) {
  const le = Array.isArray(bond.le_key_types)
    ? bond.le_key_types.filter(Boolean)
    : bond.le_key_types
      ? [bond.le_key_types]
      : [];
  return (
    <span className="text-xs">
      {bond.has_link_key ? (
        // Deliberately renders only the *existence* of key material. No field of this
        // record is ever printed as key bytes, whatever the parser may have put there.
        <span className="text-ink">🔒 link key present (not displayed)</span>
      ) : (
        <span className="text-muted italic">no link key in record</span>
      )}
      {le.length > 0 && (
        <span className="block text-[10px] text-muted mt-0.5">
          LE key types: {le.join(", ")} (types only — key material is never extracted)
        </span>
      )}
    </span>
  );
}

function CaveatList({ items, className }: { items: string[]; className?: string }) {
  if (items.length === 0) return null;
  return (
    <ul className={`list-disc pl-4 space-y-0.5 ${className ?? "text-[11px] text-warn"}`}>
      {items.map((c, i) => (
        <li key={i}>{c}</li>
      ))}
    </ul>
  );
}

function adapterRows(adapter: Record<string, unknown> | null | undefined): [string, string][] {
  if (!adapter || typeof adapter !== "object") return [];
  const out: [string, string][] = [];
  for (const [k, v] of Object.entries(adapter)) {
    if (v === null || v === undefined || v === "") continue;
    if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") {
      out.push([k, String(v)]);
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// View
// ---------------------------------------------------------------------------

export function BluetoothView({ caseId }: { caseId: string }) {
  const { data: devices, loading: devicesLoading } = useDataset<BluetoothDevice>(caseId, "bluetooth");
  const { data: bonds, loading: bondsLoading } = useDataset<BluetoothBond>(caseId, "bluetooth_bonds");
  const { data: collectorBt, loading: collectorBtLoading } = useDataset<CollectorBluetoothItem>(
    caseId,
    "collector_bluetooth",
  );
  const [summary, setSummary] = useState<BluetoothSummary | null>(null);
  const [report, setReport] = useState<BluetoothBondReport | null>(null);
  const [deviceFilter, setDeviceFilter] = useState("");
  const [bondFilter, setBondFilter] = useState("");
  const [collectorBtFilter, setCollectorBtFilter] = useState("");

  useEffect(() => {
    let alive = true;
    api
      .dataset<BluetoothSummary>(caseId, "bluetooth_summary")
      .then((d) => alive && setSummary(d ?? {}))
      .catch(() => alive && setSummary({}));
    api
      .dataset<BluetoothBondReport>(caseId, "bluetooth_bond_report")
      .then((d) => alive && setReport(d ?? {}))
      .catch(() => alive && setReport({}));
    return () => {
      alive = false;
    };
  }, [caseId]);

  if (devicesLoading || bondsLoading || collectorBtLoading || summary === null || report === null) {
    return <div className="p-8 text-muted text-sm animate-pulse">Loading Bluetooth artefacts…</div>;
  }

  // "dumpsys ran and produced a summary" vs "the collector never ran" are different facts,
  // and the empty state must not conflate them.
  const dumpsysRan = Object.keys(summary).length > 0;
  const bondStoreRead = Object.keys(report).length > 0;
  const bondStoreEncrypted = report.encrypted === true;
  const reportBonds = Array.isArray(report.bonds) ? report.bonds : [];
  const allBonds: BluetoothBond[] = bonds.length > 0 ? bonds : reportBonds;
  const reportCaveats = Array.isArray(report.caveats) ? report.caveats.filter(Boolean) : [];
  const adapter = adapterRows(report.adapter);

  const redactedCount = devices.filter((d) =>
    (d.mac || "").split(":").some(isOctetRedacted),
  ).length;

  const dq = deviceFilter.toLowerCase();
  const filteredDevices = devices.filter(
    (d) =>
      !dq ||
      (d.name || "").toLowerCase().includes(dq) ||
      (d.mac || "").toLowerCase().includes(dq) ||
      (d.bond_state || "").toLowerCase().includes(dq) ||
      (d.device_class || "").toLowerCase().includes(dq),
  );

  const bq = bondFilter.toLowerCase();
  const filteredBonds = allBonds.filter(
    (b) =>
      !bq ||
      (b.name || "").toLowerCase().includes(bq) ||
      (b.address || "").toLowerCase().includes(bq) ||
      (b.vendor || "").toLowerCase().includes(bq) ||
      (b.dev_class_label || "").toLowerCase().includes(bq) ||
      (b.dev_type_label || "").toLowerCase().includes(bq),
  );

  const cbq = collectorBtFilter.toLowerCase();
  const filteredCollectorBt = collectorBt.filter(
    (c) =>
      !cbq ||
      (c.name || "").toLowerCase().includes(cbq) ||
      (c.address || "").toLowerCase().includes(cbq) ||
      (c.type || "").toLowerCase().includes(cbq) ||
      (c.bond_state || "").toLowerCase().includes(cbq) ||
      (c.device_type || "").toLowerCase().includes(cbq),
  );
  const collectorBtByType = collectorBt.reduce<Record<string, number>>((acc, c) => {
    const t = c.type || "unknown";
    acc[t] = (acc[t] ?? 0) + 1;
    return acc;
  }, {});
  const isPermissionDenied = (v?: string) => (v || "").startsWith("[");

  return (
    <div className="p-6 max-w-5xl mx-auto">
      {/* Header */}
      <div className="mb-5">
        <h1 className="text-xl font-bold mb-1 flex items-center gap-2">
          <span>🔵</span> Bluetooth
        </h1>
        <p className="text-sm text-muted">
          Devices the subject handset has seen, paired with, or holds a persistent pairing
          record for. Two independent sources are shown separately because they support
          different claims and were acquired at different tiers.
        </p>
      </div>

      {/* ================================================================== */}
      {/* Section 1 — non-root dumpsys                                       */}
      {/* ================================================================== */}
      <section className="mb-10">
        <SectionHeader
          title="Seen / paired (non-root, dumpsys)"
          sub="adb shell dumpsys bluetooth_manager — a snapshot of adapter state at acquisition time"
          right={<TierBadge label="Tier 0 — Read-only" />}
        />

        <div className="card p-3 mb-4 border-warn/40 bg-warn/5 text-xs text-warn leading-relaxed">
          <span className="font-semibold">MAC addresses here are redacted by Android. </span>
          From Android 8.0 onwards, the OS returns Bluetooth (and Wi-Fi) hardware addresses to
          non-privileged <code className="font-mono">dumpsys</code> callers with the first four
          octets masked — they arrive as <code className="font-mono">xx:xx:xx:xx:AB:CD</code>. The
          two visible octets are <em>not</em> a device identifier and must not be attributed to a
          specific handset. Such addresses are drawn below with the masked octets greyed and
          flagged <span className="font-semibold">partial</span>. A full address is only available
          from the root-tier bond store below.
        </div>

        {!dumpsysRan && devices.length === 0 ? (
          <div className="card p-8 text-center text-muted">
            <div className="text-3xl mb-3 opacity-40">🔵</div>
            <div className="text-ink font-medium mb-1">Bluetooth was not collected</div>
            <p className="text-sm leading-relaxed max-w-lg mx-auto">
              No <code className="font-mono">bluetooth_summary</code> was written for this case, so
              the <code className="font-mono">dumpsys bluetooth_manager</code> read either did not
              run or returned nothing the parser could interpret. This is a gap in acquisition — it
              is <strong>not</strong> a finding that the device had no Bluetooth activity.
            </p>
          </div>
        ) : devices.length === 0 ? (
          <div className="card p-8 text-center text-muted">
            <div className="text-3xl mb-3 opacity-40">🔵</div>
            <div className="text-ink font-medium mb-1">Collected — no devices in the dump</div>
            <p className="text-sm leading-relaxed max-w-lg mx-auto">
              <code className="font-mono">dumpsys bluetooth_manager</code> was read successfully and
              contained no device entries. Note that this dump reflects adapter state at the moment
              of acquisition only: a device that was paired and later unpaired, or a disabled
              adapter, produces the same empty result. Absence here is not proof of absence of
              pairing — check the root-tier bond store below.
            </p>
          </div>
        ) : (
          <>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
              <StatCard n={summary.total ?? devices.length} label="Devices in dump" />
              <StatCard n={summary.paired ?? devices.filter((d) => d.is_paired).length} label="Bonded / paired" />
              <StatCard
                n={summary.connected ?? devices.filter((d) => d.connected).length}
                label="Connected at dump"
              />
              <StatCard n={redactedCount} label="Redacted MACs" tone="text-warn" />
            </div>

            <input
              className="input max-w-sm mb-3"
              placeholder="Filter by name, address, bond state or class…"
              value={deviceFilter}
              onChange={(e) => setDeviceFilter(e.target.value)}
            />

            <div className="card overflow-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr>
                    <th className="th">Device name</th>
                    <th className="th">Address</th>
                    <th className="th w-28">Bond state</th>
                    <th className="th w-36">Connected at dump</th>
                    <th className="th w-40">Last seen (as reported)</th>
                    <th className="th w-28">Class</th>
                    <th className="th w-28">Confidence</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredDevices.length === 0 ? (
                    <tr>
                      <td className="td text-center text-muted text-xs py-6" colSpan={7}>
                        No devices match your filter.
                      </td>
                    </tr>
                  ) : (
                    filteredDevices.map((d, i) => (
                      <tr key={i}>
                        <td className="td">
                          {d.name || <span className="text-muted italic text-xs">— no name broadcast</span>}
                        </td>
                        <td className="td">
                          <MacCell mac={d.mac} />
                        </td>
                        <td className="td text-xs">
                          {d.bond_state || <span className="text-muted italic">unstated</span>}
                        </td>
                        <td className="td text-xs">
                          {d.connected === true ? (
                            <span className="text-live">yes — at dump time</span>
                          ) : d.connected === false ? (
                            <span className="text-muted">no</span>
                          ) : (
                            <span className="text-muted italic">unstated</span>
                          )}
                        </td>
                        <td className="td font-mono text-xs text-muted">
                          {d.last_seen ? (
                            <>
                              {fmtTs(d.last_seen)}
                              <span className="block font-sans text-[10px] text-warn">
                                approximate — as reported by dumpsys, source clock unverified
                              </span>
                            </>
                          ) : (
                            <span className="italic">— not recorded</span>
                          )}
                        </td>
                        <td className="td text-xs">
                          {d.device_class || <span className="text-muted italic">unstated</span>}
                        </td>
                        <td className="td">
                          <ConfidenceBadge value={d.confidence} />
                          {d.source && (
                            <span className="block text-[10px] text-muted mt-0.5 font-mono">{d.source}</span>
                          )}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>

            {filteredDevices.length < devices.length && (
              <p className="text-xs text-muted mt-2">
                Showing {filteredDevices.length} of {devices.length} devices
              </p>
            )}
          </>
        )}
      </section>

      {/* ================================================================== */}
      {/* Section 2 — root bond store                                        */}
      {/* ================================================================== */}
      <section>
        <SectionHeader
          title="Persistent bonds (root, bt_config.conf)"
          sub="/data/misc/bluedroid/bt_config.conf — the pairing store that survives reboots"
          right={<TierBadge label="Tier 2 — Root" />}
        />

        {/* The single most over-claimed field in Bluetooth forensics. */}
        <div className="card p-3 mb-4 border-deletion/50 bg-deletion/5 text-xs text-deletion leading-relaxed">
          <span className="font-semibold">
            A bond timestamp is when the pairing record was written — nothing more.
          </span>{" "}
          It is <strong>not</strong> a connection time, <strong>not</strong> a last-used time, and{" "}
          <strong>not</strong> evidence that the two devices were in each other's presence at any
          later moment. A bond persists until it is explicitly removed: the record can be years
          old, and the paired device may never have been near this handset again. Do not present a
          bond timestamp as a meeting, a proximity event, or a co-location.
        </div>

        {bondStoreEncrypted && (
          <div className="card p-3 mb-4 border-warn/40 bg-warn/5 text-xs text-warn leading-relaxed">
            <span className="font-semibold">Bond store was encrypted and could not be parsed. </span>
            <code className="font-mono">bt_config.conf</code> was present on the device but its
            contents were unreadable in the current encryption state (file-based encryption, device
            not unlocked to AFU). This means <strong>the bond list is unknown</strong> — it does{" "}
            <strong>not</strong> mean the device had no bonded peers. Any count shown as zero below
            reflects a failed read, not an empty pairing store.
          </div>
        )}

        {reportCaveats.length > 0 && (
          <div className="card p-3 mb-4">
            <div className="text-[11px] uppercase tracking-wider text-muted mb-1">
              Engine caveats — bond store acquisition
            </div>
            <CaveatList items={reportCaveats} />
          </div>
        )}

        {adapter.length > 0 && (
          <div className="card p-3 mb-4">
            <div className="text-[11px] uppercase tracking-wider text-muted mb-2">
              Local adapter (this handset)
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1">
              {adapter.map(([k, v]) => (
                <div key={k} className="flex justify-between gap-3 text-xs">
                  <span className="text-muted font-mono">{k}</span>
                  <span className="font-mono text-ink truncate" title={v}>
                    {v}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {allBonds.length === 0 ? (
          <div className="card p-8 text-center text-muted">
            <div className="text-3xl mb-3 opacity-40">🔒</div>
            <div className="text-ink font-medium mb-1">
              {bondStoreEncrypted
                ? "Bond store encrypted — contents unknown"
                : bondStoreRead
                  ? "Bond store read — no bond records present"
                  : "Bond store not acquired — root required"}
            </div>
            <p className="text-sm leading-relaxed max-w-lg mx-auto">
              {bondStoreEncrypted ? (
                <>
                  The file exists but is encrypted, so its bonds could not be enumerated. Treat this
                  as <strong>unknown</strong>, never as "no paired devices".
                </>
              ) : bondStoreRead ? (
                <>
                  <code className="font-mono">bt_config.conf</code> was read successfully and
                  contained no bond sections. The device therefore held no persistent pairings at
                  acquisition time — bonds that were removed before seizure leave no entry here.
                </>
              ) : (
                <>
                  <code className="font-mono">/data/misc/bluedroid/bt_config.conf</code> lives in
                  the protected data partition and cannot be read at Tier 0 or Tier 1. This case was
                  acquired without root, so the persistent pairing store was never opened. This is
                  an <strong>un-acquired</strong> artefact, not a finding that the device had no
                  bonds — re-acquire with <strong>Tier 2 (root)</strong> to establish that.
                </>
              )}
            </p>
          </div>
        ) : (
          <>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 mb-4">
              <StatCard n={allBonds.length} label="Persistent bonds" />
              <StatCard n={allBonds.filter((b) => b.has_link_key).length} label="With link key" />
              <StatCard
                n={allBonds.filter((b) => isRandomAddress(b.addr_type_label)).length}
                label="Random addresses"
                tone="text-warn"
              />
            </div>

            <input
              className="input max-w-sm mb-3"
              placeholder="Filter by name, address, vendor or class…"
              value={bondFilter}
              onChange={(e) => setBondFilter(e.target.value)}
            />

            <div className="card overflow-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr>
                    <th className="th">Device name</th>
                    <th className="th w-44">Address</th>
                    <th className="th w-32">Class / type</th>
                    <th className="th w-36">Vendor (OUI)</th>
                    <th className="th w-56">
                      Bond record written
                      <span className="block normal-case tracking-normal text-[10px] text-warn font-normal">
                        not a connection time
                      </span>
                    </th>
                    <th className="th w-52">Key material</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredBonds.length === 0 ? (
                    <tr>
                      <td className="td text-center text-muted text-xs py-6" colSpan={6}>
                        No bonds match your filter.
                      </td>
                    </tr>
                  ) : (
                    filteredBonds.map((b, i) => {
                      const caveats = Array.isArray(b.caveats) ? b.caveats.filter(Boolean) : [];
                      return (
                        <tr key={i}>
                          <td className="td">
                            {b.name || <span className="text-muted italic text-xs">— unnamed in store</span>}
                            {caveats.length > 0 && (
                              <div className="mt-1">
                                <CaveatList items={caveats} />
                              </div>
                            )}
                          </td>
                          <td className="td">
                            <MacCell mac={b.address} />
                            <span className="block text-[10px] text-muted mt-0.5">
                              {b.addr_type_label || "address type not recorded"}
                            </span>
                          </td>
                          <td className="td text-xs">
                            {b.dev_class_label || <span className="text-muted italic">unstated</span>}
                            {b.dev_type_label && (
                              <span className="block text-[10px] text-muted">{b.dev_type_label}</span>
                            )}
                          </td>
                          <td className="td">
                            <VendorCell vendor={b.vendor} addrType={b.addr_type_label} />
                          </td>
                          <td className="td">
                            <span className="font-mono text-xs text-muted">
                              {b.bond_timestamp ? (
                                fmtTs(b.bond_timestamp)
                              ) : (
                                <span className="italic">— no timestamp in record</span>
                              )}
                            </span>
                            <span className="block text-[10px] text-warn mt-0.5 leading-snug">
                              {b.timestamp_meaning ||
                                "meaning not stated by parser — treat as pairing-store write time only, not a connection"}
                            </span>
                          </td>
                          <td className="td">
                            <LinkKeyCell bond={b} />
                          </td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>

            {filteredBonds.length < allBonds.length && (
              <p className="text-xs text-muted mt-2">
                Showing {filteredBonds.length} of {allBonds.length} bonds
              </p>
            )}
          </>
        )}
      </section>

      {/* ================================================================== */}
      {/* Section 3 — Tier-1 non-root helper capture                          */}
      {/* ================================================================== */}
      <section className="mt-10 pt-8 border-t-2 border-dashed border-line">
        <SectionHeader
          title="Non-root helper capture (adapter + bonded devices)"
          sub="bluetooth.json, via the Collector helper APK's own dump_all action — no root required"
          right={
            <span className="text-xs font-normal text-recovered bg-recovered/10 border border-recovered/30 rounded px-2 py-0.5">
              Tier 1 — Non-root helper
            </span>
          }
        />

        <div className="card p-3 mb-4 border-recovered/40 bg-recovered/5 text-xs text-recovered leading-relaxed">
          <span className="font-semibold">A third, distinct source — not dumpsys, not bt_config.conf. </span>
          These rows come from the Collector helper APK's own Android Bluetooth APIs, read
          without root. Each row keeps the helper's own <code className="font-mono">type</code>{" "}
          discriminator — <em>adapter</em> (this handset's own radio) or <em>bonded_device</em>{" "}
          (a paired peer) — and only the fields that apply to that type are populated; a blank
          cell means the field does not apply to this row's type, not that a value was lost. If
          the helper's Bluetooth permissions were denied at runtime, the APK writes the literal
          string <code className="font-mono">[permission_denied]</code> into the name/address
          fields rather than omitting the row — such rows are flagged below, not silently dropped.
        </div>

        {collectorBt.length === 0 ? (
          <div className="card p-8 text-center text-muted">
            <div className="text-3xl mb-3 opacity-40">📡</div>
            <div className="text-ink font-medium mb-1">No helper-captured Bluetooth data for this case</div>
            <p className="text-sm leading-relaxed max-w-lg mx-auto">
              Absent here means the Tier-1 "collect all" helper stage either never ran on this
              acquisition, or ran and returned nothing its parser could interpret. This is{" "}
              <strong>not</strong> a finding that the device has no Bluetooth adapter or bonded
              peers — check the chain-of-custody audit trail for whether{" "}
              <em>tier1.helper.collect_all</em> / <em>tier1.helper.dump_all</em> was attempted on
              this run.
            </p>
          </div>
        ) : (
          <>
            <div className="flex flex-wrap gap-3 mb-4">
              {[
                { label: "Total rows", value: collectorBt.length },
                { label: "Adapter rows", value: collectorBtByType.adapter ?? 0 },
                { label: "Bonded-device rows", value: collectorBtByType.bonded_device ?? 0 },
              ].map(({ label, value }) => (
                <div key={label} className="card px-4 py-2 flex flex-col items-center min-w-[110px]">
                  <span className="text-xl font-bold text-recovered">{value}</span>
                  <span className="text-xs text-muted mt-0.5">{label}</span>
                </div>
              ))}
            </div>

            <input
              className="input max-w-sm mb-3"
              placeholder="Filter by name, address, row type or bond state…"
              value={collectorBtFilter}
              onChange={(e) => setCollectorBtFilter(e.target.value)}
            />

            <div className="card overflow-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr>
                    <th className="th">Row type</th>
                    <th className="th">Name</th>
                    <th className="th w-44">Address</th>
                    <th className="th w-28">Bond state</th>
                    <th className="th w-36">Device type / class</th>
                    <th className="th w-36">Adapter enabled / state</th>
                    <th className="th">UUIDs</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredCollectorBt.length === 0 ? (
                    <tr>
                      <td className="td text-center text-muted text-xs py-6" colSpan={7}>
                        No rows match your filter.
                      </td>
                    </tr>
                  ) : (
                    filteredCollectorBt.map((c, i) => {
                      const denied = isPermissionDenied(c.name) || isPermissionDenied(c.address);
                      return (
                        <tr key={i}>
                          <td className="td text-xs">
                            <span
                              className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide border ${
                                c.type === "adapter"
                                  ? "bg-live/10 text-live border-live/30"
                                  : c.type === "bonded_device"
                                    ? "bg-recovered/10 text-recovered border-recovered/30"
                                    : "bg-muted/10 text-muted border-line"
                              }`}
                            >
                              {c.type || "unknown"}
                            </span>
                          </td>
                          <td className="td">
                            {isPermissionDenied(c.name) ? (
                              <span className="text-warn text-xs font-semibold">[permission_denied]</span>
                            ) : (
                              c.name || <span className="text-muted italic text-xs">— not recorded</span>
                            )}
                          </td>
                          <td className="td">
                            {isPermissionDenied(c.address) ? (
                              <span className="text-warn text-xs font-semibold">[permission_denied]</span>
                            ) : (
                              <span className="font-mono text-xs">
                                {c.address || <span className="text-muted italic">— not recorded</span>}
                              </span>
                            )}
                            {denied && (
                              <span className="block text-[10px] text-warn mt-0.5">
                                helper's Bluetooth permission was denied at runtime for this row
                              </span>
                            )}
                          </td>
                          <td className="td text-xs">
                            {c.bond_state || (
                              <span className="text-muted italic">
                                {c.type === "adapter" ? "n/a — adapter row" : "—"}
                              </span>
                            )}
                          </td>
                          <td className="td text-xs">
                            {c.device_type || c.device_class != null ? (
                              <>
                                {c.device_type || "—"}
                                {c.device_class != null && (
                                  <span className="block text-[10px] text-muted">class {c.device_class}</span>
                                )}
                              </>
                            ) : (
                              <span className="text-muted italic">
                                {c.type === "adapter" ? "n/a — adapter row" : "—"}
                              </span>
                            )}
                          </td>
                          <td className="td text-xs">
                            {c.enabled != null || c.state ? (
                              <>
                                {c.enabled != null && (
                                  <span className={c.enabled ? "text-live" : "text-muted"}>
                                    {c.enabled ? "enabled" : "disabled"}
                                  </span>
                                )}
                                {c.state && <span className="block text-[10px] text-muted">{c.state}</span>}
                              </>
                            ) : (
                              <span className="text-muted italic">
                                {c.type === "bonded_device" ? "n/a — device row" : "—"}
                              </span>
                            )}
                          </td>
                          <td className="td font-mono text-[11px] text-muted break-all">
                            {c.uuids && c.uuids.length > 0 ? (
                              c.uuids.join(", ")
                            ) : (
                              <span className="italic">— none reported</span>
                            )}
                          </td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>

            {filteredCollectorBt.length < collectorBt.length && (
              <p className="text-xs text-muted mt-2">
                Showing {filteredCollectorBt.length} of {collectorBt.length} rows
              </p>
            )}
          </>
        )}
      </section>
    </div>
  );
}
