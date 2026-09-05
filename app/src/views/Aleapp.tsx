/**
 * Aleapp.tsx — generic viewer for ALEAPP (Android Logs Events And Protobuf
 * Parser) third-party artifact output.
 *
 * ALEAPP is a broad, actively-maintained third-party Android artifact parser
 * (100+ module types) that SNAGR shells out to rather than reimplementing
 * (see engine/triage/aleapp.py::run_aleapp). Each module's rows carry an
 * ARBITRARY, dynamic set of columns — whatever that specific ALEAPP parser
 * happened to extract — so this view cannot hardcode a schema. Columns are
 * derived at render time from the rows themselves.
 *
 * Dataset shape (`GET /api/case/<id>/aleapp`, written by run_aleapp()):
 *   {
 *     available: boolean;                  // false => ALEAPP binary not found / timed out
 *     artifacts: Record<string, Row[]>;     // module_name -> raw parsed row objects
 *     report_dir: string;
 *     error: string | null;
 *   }
 *
 * Provenance note (load-bearing, not decorative): everything rendered here is
 * THIRD-PARTY-PARSED. SNAGR did not verify ALEAPP's extraction logic — any
 * provenance/confidence language embedded in a row's own fields is ALEAPP's
 * claim about itself, not an SNAGR chain-of-custody guarantee.
 */
import { useEffect, useMemo, useState } from "react";
import { FlaskConical, X } from "lucide-react";
import { api } from "../lib/api";
import { EmptyState } from "../components/common";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type AleappRow = Record<string, unknown>;

interface AleappResult {
  available: boolean;
  artifacts: Record<string, AleappRow[]>;
  report_dir: string;
  error: string | null;
}

// Modules the engine ALSO folds into other dashboard views (Messages, Calls,
// Contacts, Browser History) with `source: "aleapp"` tagging — kept in sync by
// hand with PROMOTED_MODULES in engine/triage/aleapp.py. Rows from these
// modules may legitimately appear twice: verbatim here, and merged into the
// relevant dashboard view elsewhere.
const PROMOTED_MODULES = new Set([
  "accounts_ce",
  "accounts_de",
  "sms",
  "calls",
  "contacts",
  "installed_apps",
  "recent_activity",
  "chrome_downloads",
  "chrome_history",
  "gps",
  "wifi_profiles",
  "bluetooth_devices",
  "notifications",
]);

const MAX_COLUMNS = 12;
const SAMPLE_ROWS_FOR_COLUMNS = 50;
const TABLE_CAP = 500;
const CELL_MAX_CHARS = 300;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function stringifyCell(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  let s: string;
  if (typeof value === "object") {
    try {
      s = JSON.stringify(value);
    } catch {
      s = String(value);
    }
  } else {
    s = String(value);
  }
  if (s.length > CELL_MAX_CHARS) return `${s.slice(0, CELL_MAX_CHARS)}…`;
  return s;
}

/** Union of keys across the first N rows, in first-seen order — not every module's rows share an identical shape. */
function deriveColumns(rows: AleappRow[]): string[] {
  const seen = new Set<string>();
  const cols: string[] = [];
  for (const row of rows.slice(0, SAMPLE_ROWS_FOR_COLUMNS)) {
    for (const key of Object.keys(row)) {
      if (!seen.has(key)) {
        seen.add(key);
        cols.push(key);
      }
    }
  }
  return cols;
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function ModuleListItem({
  name,
  count,
  selected,
  onSelect,
}: {
  name: string;
  count: number;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      onClick={onSelect}
      className={`w-full text-left px-4 py-2.5 border-b border-line transition-colors ${
        selected ? "bg-accent/15 border-l-2 border-l-accent" : "hover:bg-panel-2"
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-xs text-ink truncate">{name}</span>
        <span className="text-[10px] text-muted shrink-0">{count.toLocaleString()}</span>
      </div>
      {PROMOTED_MODULES.has(name) && (
        <div className="text-[9px] text-accent/80 mt-0.5">also folded into other views</div>
      )}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Main view
// ---------------------------------------------------------------------------

export function AleappView({ caseId }: { caseId: string }) {
  const [result, setResult] = useState<AleappResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedModule, setSelectedModule] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    api
      .dataset<AleappResult>(caseId, "aleapp")
      .then((d) => {
        if (!alive) return;
        const safe = d && typeof d === "object" ? d : null;
        setResult(safe);
        const mods = safe?.artifacts ? Object.keys(safe.artifacts).sort() : [];
        setSelectedModule(mods.length > 0 ? mods[0] : null);
      })
      .catch(() => {
        if (alive) {
          setResult(null);
          setSelectedModule(null);
        }
      })
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [caseId]);

  const modules = useMemo(
    () => (result?.artifacts ? Object.keys(result.artifacts).sort() : []),
    [result]
  );

  const rows = useMemo(
    () => (selectedModule && result?.artifacts ? result.artifacts[selectedModule] ?? [] : []),
    [result, selectedModule]
  );

  const columns = useMemo(() => deriveColumns(rows), [rows]);
  const visibleColumns = columns.slice(0, MAX_COLUMNS);
  const hiddenColumnCount = columns.length - visibleColumns.length;

  const filteredRows = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter((row) =>
      Object.values(row).some((v) => stringifyCell(v).toLowerCase().includes(q))
    );
  }, [rows, search]);

  useEffect(() => setShowAll(false), [selectedModule, search]);

  const visibleRows = showAll ? filteredRows : filteredRows.slice(0, TABLE_CAP);

  const header = (
    <div className="mb-4">
      <h1 className="text-xl font-bold mb-1 flex items-center gap-2">
        <FlaskConical className="h-4 w-4" strokeWidth={1.75} aria-hidden /> ALEAPP Artifacts
        <span className="text-xs font-normal text-muted bg-panel-2 border border-line rounded px-2 py-0.5 ml-1">
          third-party parser
        </span>
      </h1>
      <p className="text-sm text-muted leading-relaxed max-w-3xl">
        ALEAPP (Android Logs Events And Protobuf Parser) is a third-party, open-source Android
        artifact parser that SNAGR shells out to for broad OS/app coverage it does not
        implement itself. <strong className="text-ink">These rows are third-party-parsed</strong> —
        SNAGR did not verify ALEAPP&apos;s extraction logic, so any provenance or confidence
        language that appears inside a row&apos;s own fields is ALEAPP&apos;s claim about itself,
        not an SNAGR chain-of-custody guarantee.
      </p>
    </div>
  );

  if (loading) {
    return <div className="p-8 text-muted text-sm animate-pulse">Loading ALEAPP artifacts…</div>;
  }

  if (!result || result.available === false) {
    return (
      <div className="p-6 max-w-3xl mx-auto">
        {header}
        <div className="card p-8">
          <div className="text-warn font-semibold mb-2">ALEAPP did not run for this case</div>
          <p className="text-sm text-muted leading-relaxed mb-3">
            This is a <strong className="text-ink">tooling gap</strong>, not evidence that the
            device holds nothing. ALEAPP is a third-party tool that must be installed and reachable
            on this workstation — on <code className="font-mono text-ink">PATH</code> as{" "}
            <code className="font-mono text-ink">aleapp</code>/<code className="font-mono text-ink">aleapp.py</code>,
            or pointed to via the <code className="font-mono text-ink">ALEAPP_PATH</code>{" "}
            environment variable — for its broader artifact coverage to populate here. An empty
            page on this view means the parser never ran or never finished, never that nothing was
            found on the device.
          </p>
          <div className="rounded-md border border-deletion/40 bg-deletion/5 p-3">
            <div className="text-xs font-semibold text-deletion mb-1">Reported error</div>
            <pre className="text-xs text-deletion font-mono whitespace-pre-wrap break-all">
              {result?.error || "(no error message was recorded)"}
            </pre>
          </div>
        </div>
      </div>
    );
  }

  const totalRows = modules.reduce((acc, m) => acc + (result.artifacts[m]?.length ?? 0), 0);

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="shrink-0 px-6 pt-5">{header}</div>

      {result.error && (
        <div className="shrink-0 mx-6 mb-3 rounded-md border border-warn/40 bg-warn/5 p-3">
          <div className="text-xs font-semibold text-warn mb-1">
            ALEAPP ran but reported an error — some modules may be missing or incomplete
          </div>
          <pre className="text-xs text-warn font-mono whitespace-pre-wrap break-all">
            {result.error}
          </pre>
        </div>
      )}

      {modules.length === 0 ? (
        <div className="p-6">
          <EmptyState
            dataset="aleapp"
        title="ALEAPP ran but parsed no artifact modules."
            detail="The output directory produced no readable TSV files for this acquisition — check the audit log around the ALEAPP stage for details."
          />
        </div>
      ) : (
        <div className="flex flex-1 overflow-hidden border-t border-line">
          {/* Left panel — module list */}
          <div className="w-64 shrink-0 border-r border-line overflow-y-auto">
            <div className="px-3 py-2 text-[10px] text-muted uppercase tracking-wider font-semibold border-b border-line">
              Modules ({modules.length}) · {totalRows.toLocaleString()} row(s)
            </div>
            {modules.map((m) => (
              <ModuleListItem
                key={m}
                name={m}
                count={result.artifacts[m]?.length ?? 0}
                selected={selectedModule === m}
                onSelect={() => {
                  setSelectedModule(m);
                  setSearch("");
                }}
              />
            ))}
          </div>

          {/* Right panel — generic table */}
          <div className="flex-1 flex flex-col overflow-hidden">
            <div className="shrink-0 px-4 py-2 border-b border-line bg-panel-2 flex items-center gap-2">
              <span className="font-mono text-xs text-accent truncate">{selectedModule}</span>
              <input
                type="text"
                placeholder="Search this module's rows…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted"
              />
              {search && (
                <button onClick={() => setSearch("")} className="text-muted hover:text-fg text-xs">
                  <X className="inline h-3.5 w-3.5" strokeWidth={1.75} aria-hidden />
                </button>
              )}
              <span className="text-xs text-muted shrink-0">
                {filteredRows.length.toLocaleString()} / {rows.length.toLocaleString()} row(s)
              </span>
            </div>

            {hiddenColumnCount > 0 && (
              <div className="shrink-0 px-4 py-1.5 text-[11px] text-muted border-b border-line bg-panel-2/60">
                Showing {visibleColumns.length} of {columns.length} field(s) seen in this module's
                rows as columns — +{hiddenColumnCount} more field(s) not shown.
              </div>
            )}

            <div className="flex-1 overflow-auto px-4 py-3">
              {filteredRows.length === 0 ? (
                <EmptyState
                  title="No rows match the current filter."
                  detail={
                    rows.length === 0
                      ? "This module produced no rows."
                      : "Try changing the search term."
                  }
                />
              ) : (
                <div className="card overflow-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr>
                        {visibleColumns.map((c) => (
                          <th key={c} className="th font-mono">
                            {c}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {visibleRows.map((row, i) => (
                        <tr key={i}>
                          {visibleColumns.map((c) => (
                            <td
                              key={c}
                              className="td font-mono break-all"
                              title={stringifyCell(row[c])}
                            >
                              {stringifyCell(row[c])}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {!showAll && filteredRows.length > TABLE_CAP && (
                <div className="text-center py-3">
                  <button className="btn-ghost text-xs" onClick={() => setShowAll(true)}>
                    Showing first {TABLE_CAP.toLocaleString()} of{" "}
                    {filteredRows.length.toLocaleString()} rows — click to show all
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
