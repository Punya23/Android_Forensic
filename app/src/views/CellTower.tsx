import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { useDataset, fmtTs } from "../lib/hooks";
import { SectionHeader, StatCard } from "../components/common";

// ---------------------------------------------------------------------------
// Types — declared locally (the orchestrator owns lib/types.ts).
// The producer is a best-effort `dumpsys telephony.registry` parser whose field set
// varies by Android release and OEM skin, so every field is optional and both the
// dBm and the ASU forms of signal strength are accepted.
// ---------------------------------------------------------------------------

export interface CellTowerRecord {
  cell_id?: number | string;
  /** Location Area Code (2G/3G). */
  lac?: number | string;
  /** Tracking Area Code (LTE/NR) — the LTE-era equivalent of the LAC. */
  tac?: number | string;
  mcc?: number | string;
  mnc?: number | string;
  operator?: string;
  /** LTE / NR / GSM / WCDMA. Some builds report this as `network_type`. */
  technology?: string;
  network_type?: string;
  signal_dbm?: number;
  /** Arbitrary Strength Unit — vendor-defined, only loosely convertible to dBm. */
  signal_asu?: number;
  signal_label?: string;
  timestamp?: string;
  is_registered?: boolean;
  confidence?: string;
}

export interface CellTowerSummary {
  total?: number;
  unique_towers?: number;
  by_operator?: Record<string, number>;
  by_network_type?: Record<string, number>;
  by_technology?: Record<string, number>;
  by_signal?: Record<string, number>;
}

// ---------------------------------------------------------------------------
// Helpers
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

/** Render a numeric identifier, treating the parser's -1 / empty sentinels as "not recorded". */
function idText(v: number | string | undefined): string | null {
  if (v === undefined || v === null || v === "") return null;
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isNaN(n) && n < 0) return null;
  return String(v);
}

function IdCell({ value }: { value: number | string | undefined }) {
  const t = idText(value);
  return t ? (
    <span className="font-mono text-xs">{t}</span>
  ) : (
    <span className="text-muted italic text-xs">—</span>
  );
}

function technologyOf(r: CellTowerRecord): string {
  return (r.technology || r.network_type || "").toUpperCase();
}

/**
 * Signal strength, always labelled for what it is. dBm is reported directly by the radio;
 * ASU is a vendor-defined unit whose conversion to dBm differs per radio technology, so an
 * ASU-derived reading is shown as ASU and flagged approximate rather than silently converted.
 */
function SignalCell({ record }: { record: CellTowerRecord }) {
  if (typeof record.signal_dbm === "number") {
    return (
      <span className="text-xs">
        <span className="font-mono">{record.signal_dbm} dBm</span>
        {record.signal_label && <span className="block text-[10px] text-muted">{record.signal_label}</span>}
      </span>
    );
  }
  if (typeof record.signal_asu === "number" && record.signal_asu >= 0) {
    return (
      <span className="text-xs">
        <span className="font-mono">{record.signal_asu} ASU</span>
        <span className="block text-[10px] text-warn">
          approximate — vendor-defined unit, not converted to dBm
        </span>
        {record.signal_label && <span className="block text-[10px] text-muted">{record.signal_label}</span>}
      </span>
    );
  }
  if (record.signal_label) {
    return (
      <span className="text-xs">
        {record.signal_label}
        <span className="block text-[10px] text-warn">approximate — qualitative label only</span>
      </span>
    );
  }
  return <span className="text-muted italic text-xs">— not recorded</span>;
}

/** A small "label × count" breakdown bar built from a summary map. */
function Breakdown({ title, map }: { title: string; map?: Record<string, number> }) {
  const entries = Object.entries(map ?? {})
    .filter(([, n]) => typeof n === "number")
    .sort((a, b) => b[1] - a[1]);
  if (entries.length === 0) return null;
  return (
    <div className="card p-3">
      <div className="text-[11px] uppercase tracking-wider text-muted mb-2">{title}</div>
      <div className="flex flex-wrap gap-2">
        {entries.map(([label, n]) => (
          <span
            key={label}
            className="text-xs border border-line rounded px-2 py-0.5 bg-panel whitespace-nowrap"
          >
            {label || "unknown"} <span className="text-accent font-semibold ml-1">{n}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// View
// ---------------------------------------------------------------------------

export function CellTowerView({ caseId }: { caseId: string }) {
  const { data: towers, loading } = useDataset<CellTowerRecord>(caseId, "celltower");
  const [summary, setSummary] = useState<CellTowerSummary | null>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    let alive = true;
    api
      .dataset<CellTowerSummary>(caseId, "celltower_summary")
      .then((d) => alive && setSummary(d ?? {}))
      .catch(() => alive && setSummary({}));
    return () => {
      alive = false;
    };
  }, [caseId]);

  if (loading || summary === null) {
    return <div className="p-8 text-muted text-sm animate-pulse">Loading cell-tower records…</div>;
  }

  // A written summary proves the telephony read ran; no summary means it never did.
  const collected = Object.keys(summary).length > 0;

  const q = query.toLowerCase();
  const filtered = towers.filter(
    (t) =>
      !q ||
      (t.operator || "").toLowerCase().includes(q) ||
      technologyOf(t).toLowerCase().includes(q) ||
      String(t.cell_id ?? "").includes(q) ||
      String(t.lac ?? "").includes(q) ||
      String(t.tac ?? "").includes(q) ||
      String(t.mcc ?? "").includes(q) ||
      String(t.mnc ?? "").includes(q),
  );

  const techMap = summary.by_technology ?? summary.by_network_type;

  return (
    <div className="p-6 max-w-5xl mx-auto">
      {/* Header */}
      <div className="mb-5">
        <h1 className="text-xl font-bold mb-1 flex items-center gap-2">
          <span>📡</span> Cell Towers
          <span className="text-xs font-normal text-muted bg-panel-2 border border-line rounded px-2 py-0.5 ml-1">
            Tier 0 — Read-only
          </span>
        </h1>
        <p className="text-sm text-muted">
          Serving-cell identifiers read from{" "}
          <code className="font-mono">adb shell dumpsys telephony.registry</code>. These are network
          identifiers, not positions.
        </p>
      </div>

      {/* THE caveat. Cell identifiers are routinely over-read as locations. */}
      <div className="card p-3 mb-3 border-deletion/50 bg-deletion/5 text-xs text-deletion leading-relaxed">
        <span className="font-semibold">A cell ID is not a location. </span>
        A serving-cell identifier places the handset somewhere inside that cell's coverage area —
        which can span many square kilometres in rural deployments, overlaps neighbouring cells, and
        changes shape with terrain, load and antenna tilt. It is <strong>not</strong> a GPS fix and
        carries no accuracy radius. This tool does <strong>not</strong> resolve cell IDs to
        coordinates, and deliberately draws no map: converting a CID/LAC to a point requires an
        operator's own cell database and would manufacture a precision the evidence does not have.
        Report these as network identifiers, and seek subscriber/cell-site records from the operator
        if a geographic claim is needed.
      </div>

      <div className="card p-3 mb-4 border-warn/40 bg-warn/5 text-xs text-warn leading-relaxed">
        <span className="font-semibold">This is volatile state, not a location history. </span>
        <code className="font-mono">dumpsys telephony.registry</code> reports the{" "}
        <em>current and recently observed</em> serving cell held in memory by the telephony
        subsystem. It is not a stored log, it does not survive a reboot, and it says nothing about
        where the device was before the acquisition window. Any timestamp below is the moment the
        registry recorded that cell — treat the set as a snapshot, not as a movement trail.
      </div>

      {!collected && towers.length === 0 ? (
        <div className="card p-10 text-center text-muted">
          <div className="text-4xl mb-3 opacity-40">📡</div>
          <div className="text-ink font-medium mb-1">Cell-tower data was not collected</div>
          <p className="text-sm leading-relaxed max-w-lg mx-auto">
            No <code className="font-mono">celltower_summary</code> was written for this case, so the
            telephony registry read either did not run or produced nothing the parser could
            interpret. This is a gap in acquisition — it is <strong>not</strong> a finding that the
            device had no network registration.
          </p>
        </div>
      ) : towers.length === 0 ? (
        <div className="card p-10 text-center text-muted">
          <div className="text-4xl mb-3 opacity-40">📡</div>
          <div className="text-ink font-medium mb-1">Collected — no serving cell reported</div>
          <p className="text-sm leading-relaxed max-w-lg mx-auto">
            The telephony registry was read and contained no usable cell identity. This is the
            expected result for a device with no SIM, in airplane mode, out of coverage, or whose
            OEM build reports cell identity only to privileged callers. It does not establish that
            the device was never registered to a network.
          </p>
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-3">
            <StatCard n={summary.total ?? towers.length} label="Records" />
            <StatCard
              n={summary.unique_towers ?? new Set(towers.map((t) => `${t.cell_id}/${t.lac ?? t.tac}`)).size}
              label="Unique cells"
            />
            <StatCard
              n={towers.filter((t) => t.is_registered === true).length}
              label="Marked registered"
            />
            <StatCard n={Object.keys(summary.by_operator ?? {}).length} label="Operators seen" />
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-4">
            <Breakdown title="By technology" map={techMap} />
            <Breakdown title="By operator" map={summary.by_operator} />
          </div>

          <SectionHeader
            title="Serving-cell records"
            sub={`${towers.length} record${towers.length === 1 ? "" : "s"} — network identifiers only, no coordinates`}
          />

          <input
            className="input max-w-sm mb-3"
            placeholder="Filter by operator, technology, CID, LAC/TAC or MCC/MNC…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />

          <div className="card overflow-auto">
            <table className="w-full text-sm">
              <thead>
                <tr>
                  <th className="th w-44">
                    Registry time
                    <span className="block normal-case tracking-normal text-[10px] text-warn font-normal">
                      when observed, not a fix
                    </span>
                  </th>
                  <th className="th w-24">Cell ID</th>
                  <th className="th w-24">LAC / TAC</th>
                  <th className="th w-24">MCC / MNC</th>
                  <th className="th">Operator</th>
                  <th className="th w-24">Technology</th>
                  <th className="th w-36">Signal</th>
                  <th className="th w-28">Registration</th>
                  <th className="th w-28">Confidence</th>
                </tr>
              </thead>
              <tbody>
                {filtered.length === 0 ? (
                  <tr>
                    <td className="td text-center text-muted text-xs py-6" colSpan={9}>
                      No records match your filter.
                    </td>
                  </tr>
                ) : (
                  filtered.map((t, i) => {
                    const lacTac = idText(t.lac) ?? idText(t.tac);
                    const isTac = idText(t.lac) === null && idText(t.tac) !== null;
                    const mcc = idText(t.mcc);
                    const mnc = idText(t.mnc);
                    return (
                      <tr key={i}>
                        <td className="td font-mono text-xs text-muted">
                          {t.timestamp ? (
                            fmtTs(t.timestamp)
                          ) : (
                            <span className="italic">— no timestamp in dump</span>
                          )}
                        </td>
                        <td className="td">
                          <IdCell value={t.cell_id} />
                        </td>
                        <td className="td">
                          {lacTac ? (
                            <span className="font-mono text-xs">
                              {lacTac}
                              <span className="block font-sans text-[10px] text-muted">
                                {isTac ? "TAC" : "LAC"}
                              </span>
                            </span>
                          ) : (
                            <span className="text-muted italic text-xs">—</span>
                          )}
                        </td>
                        <td className="td font-mono text-xs">
                          {mcc || mnc ? (
                            `${mcc ?? "?"} / ${mnc ?? "?"}`
                          ) : (
                            <span className="text-muted italic">—</span>
                          )}
                        </td>
                        <td className="td text-xs">
                          {t.operator || <span className="text-muted italic">— not reported</span>}
                        </td>
                        <td className="td text-xs">
                          {technologyOf(t) || <span className="text-muted italic">unstated</span>}
                        </td>
                        <td className="td">
                          <SignalCell record={t} />
                        </td>
                        <td className="td text-xs">
                          {t.is_registered === true ? (
                            <span className="text-live">serving (registered)</span>
                          ) : t.is_registered === false ? (
                            <span className="text-muted">neighbour / not registered</span>
                          ) : (
                            <span className="text-muted italic">unstated</span>
                          )}
                        </td>
                        <td className="td">
                          <ConfidenceBadge value={t.confidence} />
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>

          {filtered.length < towers.length && (
            <p className="text-xs text-muted mt-2">
              Showing {filtered.length} of {towers.length} records
            </p>
          )}

          <p className="text-[11px] text-muted mt-3 leading-relaxed">
            Cell identifiers above are reproduced exactly as the telephony registry reported them.
            No coordinate lookup, triangulation, or coverage estimate has been performed by this
            tool.
          </p>
        </>
      )}
    </div>
  );
}
