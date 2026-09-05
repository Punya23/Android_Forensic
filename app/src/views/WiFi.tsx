import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { WifiNetwork } from "../lib/types";
import { SortTh, useSort } from "../components/common";

const CONF_COLORS: Record<string, { bg: string; text: string; border: string }> = {
  live:      { bg: "#e4f4ea", text: "#1c7d3f", border: "#1c7d3f" },
  recovered: { bg: "#e2ecfa", text: "#2258a8", border: "#2258a8" },
  carved:    { bg: "#f6ecd4", text: "#a6741a", border: "#a6741a" },
  deletion:  { bg: "#f6dedd", text: "#a5322f", border: "#a5322f" },
};

const SEC_COLORS: Record<string, { bg: string; text: string }> = {
  "WPA/WPA2": { bg: "#e2ecfa", text: "#2258a8" },
  "WPA3":     { bg: "#e4f4ea", text: "#1c7d3f" },
  "WEP":      { bg: "#f6ecd4", text: "#a6741a" },
  "OPEN":     { bg: "#f6dedd", text: "#a5322f" },
};

function ConfidenceBadge({ value }: { value: string }) {
  const c = CONF_COLORS[value] ?? CONF_COLORS.live;
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

function SecurityBadge({ value }: { value: string }) {
  const c = SEC_COLORS[value] ?? { bg: "#f0f0f0", text: "#555" };
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
      {value || "OPEN"}
    </span>
  );
}

function PasswordCell({ password }: { password: string }) {
  const [revealed, setRevealed] = useState(false);
  if (!password) {
    return <span className="text-muted text-xs italic">— open / enterprise</span>;
  }
  return (
    <span className="flex items-center gap-2">
      <span
        className="font-mono text-sm select-all"
        style={{ letterSpacing: revealed ? "0" : "0.15em" }}
      >
        {revealed ? password : "•".repeat(Math.min(password.length, 16))}
      </span>
      <button
        className="text-xs text-accent underline underline-offset-2 shrink-0"
        onClick={() => setRevealed((r) => !r)}
        title={revealed ? "Hide password" : "Reveal password"}
      >
        {revealed ? "hide" : "reveal"}
      </button>
    </span>
  );
}

// ---------------------------------------------------------------------------
// Non-root helper capture (Tier 1) — dataset "collector_wifi"
//
// A DIFFERENT acquisition method from the root credential pull above: this is the
// Collector helper APK's own WifiManager scan (wifi.json, via the dump_all action),
// not a read of /data/misc/wifi/. It carries no passwords and cannot carry any.
// Per the engine's own convention (pipeline.py, next to the write_derived calls for
// these two datasets): "Kept separate ... the helper's JSON has a different shape,
// and merging the two would mean a row's fields no longer imply how it was obtained."
//
// Shape verified against engine/triage/parsers/collector.py::parse_wifi_json. Rows
// keep an APK-assigned `type` discriminator, and NOT every field applies to every
// type — a scan_result row has no ip_address/network_id, a saved_network row has no
// frequency_mhz/level_dbm, etc. A blank cell therefore means "not applicable to this
// row type" as often as it means "not reported"; this view does not try to guess
// which, and says so once, visibly, rather than mislabel every blank cell.
// ---------------------------------------------------------------------------

interface CollectorWifiItem {
  type: string; // "current_connection" | "saved_network" | "scan_result" | "unknown"
  ssid: string;
  bssid: string;
  hidden: boolean;
  frequency_mhz: number | null;
  level_dbm: number | null; // merged from the APK's level_dbm (scan) or rssi (current)
  capabilities: string;
  status: string;
  network_id: number | string | null;
  ip_address: string;
  source_file: string;
}

const ROW_TYPE_COLORS: Record<string, { bg: string; text: string; border: string }> = {
  current_connection: { bg: "#e4f4ea", text: "#1c7d3f", border: "#1c7d3f" },
  saved_network:       { bg: "#e2ecfa", text: "#2258a8", border: "#2258a8" },
  scan_result:          { bg: "#f6ecd4", text: "#a6741a", border: "#a6741a" },
  unknown:               { bg: "#f0f0f0", text: "#555555", border: "#999999" },
};

function RowTypeBadge({ value }: { value: string }) {
  const c = ROW_TYPE_COLORS[value] ?? ROW_TYPE_COLORS.unknown;
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
      {value.replace(/_/g, " ")}
    </span>
  );
}

function CollectorWifiSection({ caseId }: { caseId: string }) {
  const [rows, setRows] = useState<CollectorWifiItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    let alive = true;
    api
      .dataset<CollectorWifiItem[]>(caseId, "collector_wifi")
      .then((d) => alive && setRows(Array.isArray(d) ? d : []))
      .catch((e) => {
        if (!alive) return;
        setError(e instanceof Error ? e.message : String(e));
        setRows([]);
      });
    return () => {
      alive = false;
    };
  }, [caseId]);

  const q = filter.toLowerCase();
  const filtered = (rows ?? []).filter(
    (r) =>
      !q ||
      r.ssid.toLowerCase().includes(q) ||
      r.bssid.toLowerCase().includes(q) ||
      r.type.toLowerCase().includes(q),
  );

  const byType = (rows ?? []).reduce<Record<string, number>>((acc, r) => {
    acc[r.type] = (acc[r.type] ?? 0) + 1;
    return acc;
  }, {});

  const sort = useSort<CollectorWifiItem>(filtered);

  return (
    <section className="mt-10 pt-8 border-t-2 border-dashed border-line">
      <div className="mb-4">
        <h2 className="text-lg font-semibold text-ink flex items-center gap-2">
          <span>📡</span> Non-root helper capture (association/saved/scan)
          <span className="text-xs font-normal text-recovered bg-recovered/10 border border-recovered/30 rounded px-2 py-0.5 ml-1">
            Tier 1 — Non-root helper
          </span>
        </h2>
        <p className="text-sm text-muted mt-1 leading-relaxed">
          A separate acquisition, by a separate mechanism, from the root credential pull above.
          These rows come from the Collector helper APK's own <code className="font-mono">WifiManager</code>{" "}
          scan (<code className="font-mono">wifi.json</code>, via its <code className="font-mono">dump_all</code>{" "}
          action) — no root required, and no plaintext passwords are or can be present here.
        </p>
      </div>

      <div className="card p-3 mb-4 border-recovered/40 bg-recovered/5 text-xs text-recovered leading-relaxed">
        <span className="font-semibold">Different method, different fields. </span>
        Each row keeps the helper's own <code className="font-mono">type</code> discriminator —{" "}
        <em>current_connection</em>, <em>saved_network</em> or <em>scan_result</em> — and each type
        populates a different subset of columns by design (e.g. a scan result carries no IP
        address or network ID; a saved network carries no signal level). A blank cell below
        therefore often means <strong>not applicable to this row's type</strong>, not that a value
        was lost. Separately: without <code className="font-mono">ACCESS_FINE_LOCATION</code> granted
        to the helper, Android 8.1+ hands back these networks with blank SSIDs — an OS
        restriction on the acquisition, not evidence of an empty network history.
      </div>

      {error && (
        <div className="p-4 text-sm text-deletion">
          Failed to load helper Wi-Fi capture: {error}
        </div>
      )}

      {rows === null ? (
        <div className="p-8 text-muted text-sm animate-pulse">Loading helper capture…</div>
      ) : rows.length === 0 ? (
        <div className="card p-10 text-center text-muted">
          <div className="text-4xl mb-3 opacity-40">📡</div>
          <div className="font-medium mb-1">No helper-captured Wi-Fi data for this case</div>
          <div className="text-sm max-w-lg mx-auto leading-relaxed">
            Absent here means the Tier-1 "collect all" helper stage either never ran on this
            acquisition, or ran and returned nothing its parser could interpret. This is{" "}
            <strong>not</strong> a finding that the device has no Wi-Fi configuration — check the
            chain-of-custody audit trail for whether <em>tier1.helper.collect_all</em> /{" "}
            <em>tier1.helper.dump_all</em> was attempted on this run.
          </div>
        </div>
      ) : (
        <>
          <div className="flex flex-wrap gap-3 mb-4">
            {[
              { label: "Total rows", value: rows.length },
              { label: "Current connection", value: byType.current_connection ?? 0 },
              { label: "Saved networks", value: byType.saved_network ?? 0 },
              { label: "Scan results", value: byType.scan_result ?? 0 },
            ].map(({ label, value }) => (
              <div
                key={label}
                className="card px-4 py-2 flex flex-col items-center min-w-[110px]"
              >
                <span className="text-xl font-bold text-recovered">{value}</span>
                <span className="text-xs text-muted mt-0.5">{label}</span>
              </div>
            ))}
          </div>

          <div className="mb-3">
            <input
              className="input max-w-sm"
              placeholder="Filter by SSID, BSSID or row type…"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
          </div>

          <div className="card overflow-auto">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="border-b border-line text-xs uppercase tracking-wider text-muted">
                  <SortTh className="text-left py-2 px-3 font-semibold" label="Row type" sortKeyName="type" getValue={(r) => r.type} sort={sort} />
                  <SortTh className="text-left py-2 px-3 font-semibold" label="SSID" sortKeyName="ssid" getValue={(r) => r.ssid} sort={sort} />
                  <SortTh className="text-left py-2 px-3 font-semibold" label="BSSID" sortKeyName="bssid" getValue={(r) => r.bssid} sort={sort} />
                  <SortTh className="text-left py-2 px-3 font-semibold" label="Freq / level" sortKeyName="frequency_mhz" getValue={(r) => r.frequency_mhz} sort={sort} />
                  <SortTh className="text-left py-2 px-3 font-semibold" label="Network ID" sortKeyName="network_id" getValue={(r) => r.network_id} sort={sort} />
                  <SortTh className="text-left py-2 px-3 font-semibold" label="Status / capabilities" sortKeyName="status" getValue={(r) => r.status} sort={sort} />
                  <SortTh className="text-left py-2 px-3 font-semibold" label="IP address" sortKeyName="ip_address" getValue={(r) => r.ip_address} sort={sort} />
                  <SortTh className="text-left py-2 px-3 font-semibold" label="Hidden" sortKeyName="hidden" getValue={(r) => r.hidden} sort={sort} />
                </tr>
              </thead>
              <tbody>
                {sort.sorted.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="text-center py-8 text-muted text-xs">
                      No rows match your filter.
                    </td>
                  </tr>
                ) : (
                  sort.sorted.map((n, i) => (
                    <tr
                      key={i}
                      className="border-b border-line/50 hover:bg-panel-2/50 transition-colors"
                    >
                      <td className="py-2.5 px-3">
                        <RowTypeBadge value={n.type} />
                      </td>
                      <td className="py-2.5 px-3 font-medium">
                        {n.ssid || <span className="text-muted italic">— blank (see notice above)</span>}
                      </td>
                      <td className="py-2.5 px-3 font-mono text-xs">
                        {n.bssid || <span className="text-muted italic">— not recorded</span>}
                      </td>
                      <td className="py-2.5 px-3 font-mono text-xs text-muted">
                        {n.frequency_mhz != null ? `${n.frequency_mhz} MHz` : "—"}
                        {n.level_dbm != null && <span className="block">{n.level_dbm} dBm</span>}
                      </td>
                      <td className="py-2.5 px-3 font-mono text-xs text-muted">
                        {n.network_id != null ? String(n.network_id) : "—"}
                      </td>
                      <td className="py-2.5 px-3 text-xs">
                        {n.status || <span className="text-muted italic">—</span>}
                        {n.capabilities && (
                          <span className="block font-mono text-[11px] text-muted break-all">
                            {n.capabilities}
                          </span>
                        )}
                      </td>
                      <td className="py-2.5 px-3 font-mono text-xs text-muted">
                        {n.ip_address || "—"}
                      </td>
                      <td className="py-2.5 px-3 text-xs">{n.hidden ? "yes" : "no"}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>

          {filtered.length < rows.length && (
            <p className="text-xs text-muted mt-2">
              Showing {filtered.length} of {rows.length} rows
            </p>
          )}
        </>
      )}
    </section>
  );
}

export function WifiView({ caseId }: { caseId: string }) {
  const [networks, setNetworks] = useState<WifiNetwork[] | null>(null);
  const [filter, setFilter] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .dataset<WifiNetwork[]>(caseId, "wifi")
      .then(setNetworks)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [caseId]);

  // Hooks must run unconditionally on every render — computed here, before either
  // early return below, rather than after the error/loading checks.
  const filtered = (networks ?? []).filter((n) => {
    const q = filter.toLowerCase();
    return (
      n.ssid.toLowerCase().includes(q) ||
      n.security.toLowerCase().includes(q) ||
      n.source_file.toLowerCase().includes(q)
    );
  });
  const sort = useSort<WifiNetwork>(filtered);

  if (error) {
    return (
      <div className="p-8 text-sm text-deletion">
        Failed to load Wi-Fi data: {error}
      </div>
    );
  }

  if (networks === null) {
    return (
      <div className="p-8 text-muted text-sm animate-pulse">
        Loading Wi-Fi credentials…
      </div>
    );
  }

  const withPassword = networks.filter((n) => n.password).length;

  return (
    <div className="p-6 max-w-5xl mx-auto">
      {/* Header */}
      <div className="mb-5">
        <h1 className="text-xl font-bold mb-1 flex items-center gap-2">
          <span>📶</span> Wi-Fi Passwords
          <span className="text-xs font-normal text-muted bg-panel-2 border border-line rounded px-2 py-0.5 ml-1">
            Tier 2 — Root
          </span>
        </h1>
        <p className="text-sm text-muted">
          Stored Wi-Fi credentials recovered from the device's system
          configuration file. No active cracking was performed — passwords are
          reproduced verbatim from the OS's plaintext storage.
        </p>
      </div>

      {/* Forensic disclaimer */}
      <div className="card p-3 mb-4 border-warn/40 bg-warn/5 text-xs text-warn leading-relaxed">
        <span className="font-semibold">Forensic notice: </span>
        Credentials were pulled via{" "}
        <code className="font-mono">su&nbsp;-c&nbsp;cp</code> from{" "}
        <code className="font-mono">/data/misc/wifi/</code> (root required). The
        original file was not modified. Every step is recorded in the chain-of-custody
        audit trail under <em>tier2.wifi.*</em> events.
      </div>

      {networks.length === 0 ? (
        /* Empty state */
        <div className="card p-10 text-center text-muted">
          <div className="text-4xl mb-3 opacity-40">📶</div>
          <div className="font-medium mb-1">No Wi-Fi credentials recovered</div>
          <div className="text-sm">
            Enable <strong>Tier-2 Wi-Fi Credentials</strong> on the next
            acquisition, or the device may have no saved networks.
          </div>
        </div>
      ) : (
        <>
          {/* Summary bar */}
          <div className="flex flex-wrap gap-3 mb-4">
            {[
              { label: "Total networks", value: networks.length },
              { label: "With password", value: withPassword },
              { label: "Open / enterprise", value: networks.length - withPassword },
            ].map(({ label, value }) => (
              <div
                key={label}
                className="card px-4 py-2 flex flex-col items-center min-w-[110px]"
              >
                <span className="text-xl font-bold text-accent">{value}</span>
                <span className="text-xs text-muted mt-0.5">{label}</span>
              </div>
            ))}
          </div>

          {/* Filter */}
          <div className="mb-3">
            <input
              className="input max-w-sm"
              placeholder="Filter by SSID, security, or source…"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
          </div>

          {/* Table */}
          <div className="card overflow-auto">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="border-b border-line text-xs uppercase tracking-wider text-muted">
                  <SortTh className="text-left py-2 px-3 font-semibold" label="SSID" sortKeyName="ssid" getValue={(n) => n.ssid} sort={sort} />
                  <SortTh className="text-left py-2 px-3 font-semibold" label="Security" sortKeyName="security" getValue={(n) => n.security} sort={sort} />
                  <SortTh className="text-left py-2 px-3 font-semibold" label="Password" sortKeyName="password" getValue={(n) => n.password} sort={sort} />
                  <SortTh className="text-left py-2 px-3 font-semibold" label="Confidence" sortKeyName="confidence" getValue={(n) => n.confidence} sort={sort} />
                  <SortTh className="text-left py-2 px-3 font-semibold" label="Source file" sortKeyName="source_file" getValue={(n) => n.source_file} sort={sort} />
                </tr>
              </thead>
              <tbody>
                {sort.sorted.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="text-center py-8 text-muted text-xs">
                      No networks match your filter.
                    </td>
                  </tr>
                ) : (
                  sort.sorted.map((n, i) => (
                    <tr
                      key={i}
                      className="border-b border-line/50 hover:bg-panel-2/50 transition-colors"
                    >
                      <td className="py-2.5 px-3 font-medium">{n.ssid || <span className="text-muted italic">—</span>}</td>
                      <td className="py-2.5 px-3">
                        <SecurityBadge value={n.security} />
                      </td>
                      <td className="py-2.5 px-3">
                        <PasswordCell password={n.password} />
                      </td>
                      <td className="py-2.5 px-3">
                        <ConfidenceBadge value={n.confidence} />
                      </td>
                      <td className="py-2.5 px-3 font-mono text-xs text-muted">
                        {n.source_file}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>

          {filtered.length < networks.length && (
            <p className="text-xs text-muted mt-2">
              Showing {filtered.length} of {networks.length} networks
            </p>
          )}
        </>
      )}

      <CollectorWifiSection caseId={caseId} />
    </div>
  );
}
