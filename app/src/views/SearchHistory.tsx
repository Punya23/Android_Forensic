import { useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import { useDataset, fmtTs } from "../lib/hooks";
import { SectionHeader } from "../components/common";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** One recovered search query. */
export interface SearchRecord {
  query?: string;
  timestamp?: string;
  url?: string;
  /** Set by the cache parser instead of `url`. */
  clicked_url?: string;
  /** "browser" | "chrome_history_db" | "google_cache" */
  source?: string;
  is_suspicious?: boolean;
  visit_count?: number;
  caveats?: string[];
  warnings?: string[];
}

export interface SearchSummary {
  total?: number;
  suspicious?: number;
  unique_queries?: number;
  with_timestamp?: number;
}

// ---------------------------------------------------------------------------
// Presentational helpers
// ---------------------------------------------------------------------------

interface SourceMeta {
  label: string;
  bg: string;
  text: string;
  note: string;
}

function sourceMeta(raw: string | undefined): SourceMeta {
  const s = (raw ?? "").toLowerCase();
  if (s.includes("cache")) {
    return {
      label: "GOOGLE CACHE",
      bg: "#f6ecd4",
      text: "#a6741a",
      note: "Fragment recovered from the Google app's cache — a partial residue, not a complete history.",
    };
  }
  if (s.includes("browser") || s.includes("history") || s.includes("chrome")) {
    return {
      label: "BROWSER",
      bg: "#e2ecfa",
      text: "#2258a8",
      note: "Extracted from a Chromium History database (urls table).",
    };
  }
  return {
    label: (raw || "UNKNOWN").toUpperCase(),
    bg: "#f0f0f0",
    text: "#555",
    note: "Source not recorded by the parser.",
  };
}

function SourceBadge({ value }: { value: string | undefined }) {
  const m = sourceMeta(value);
  return (
    <span
      title={m.note}
      style={{
        display: "inline-block",
        padding: "1px 8px",
        borderRadius: 4,
        fontSize: 11,
        fontWeight: 600,
        color: m.text,
        background: m.bg,
        whiteSpace: "nowrap",
      }}
    >
      {m.label}
    </span>
  );
}

function HeuristicFlag() {
  return (
    <span
      title="Case-insensitive substring match against a fixed word list. Not a finding."
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
// View
// ---------------------------------------------------------------------------

export function SearchHistoryView({ caseId }: { caseId: string }) {
  const { data, loading } = useDataset<SearchRecord>(caseId, "search_history");
  const [summary, setSummary] = useState<SearchSummary>({});
  const [query, setQuery] = useState("");

  useEffect(() => {
    let alive = true;
    api
      .dataset<SearchSummary>(caseId, "search_summary")
      .then((s) => alive && setSummary(s ?? {}))
      .catch(() => alive && setSummary({}));
    return () => {
      alive = false;
    };
  }, [caseId]);

  // Newest first. Rows with no timestamp sort to the bottom — they are not "old",
  // they are undated, and the table says so in the cell.
  const sorted = useMemo(
    () =>
      [...data].sort((a, b) => {
        const at = a.timestamp ?? "";
        const bt = b.timestamp ?? "";
        if (!at && !bt) return 0;
        if (!at) return 1;
        if (!bt) return -1;
        return bt.localeCompare(at);
      }),
    [data],
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return sorted;
    return sorted.filter(
      (r) =>
        (r.query ?? "").toLowerCase().includes(q) ||
        (r.url ?? r.clicked_url ?? "").toLowerCase().includes(q) ||
        (r.source ?? "").toLowerCase().includes(q),
    );
  }, [sorted, query]);

  if (loading) return <div className="p-8 text-muted text-sm animate-pulse">Loading search history…</div>;

  const undated = data.filter((r) => !r.timestamp).length;
  const flagged = data.filter((r) => r.is_suspicious).length;

  // Honest empty state.
  if (data.length === 0) {
    return (
      <div className="p-6">
        <SectionHeader
          title="Search History"
          sub="Browser history databases · Google app cache"
          right={
            <span className="text-xs font-normal text-muted bg-panel-2 border border-line rounded px-2 py-0.5">
              Tier 0 — Read-only
            </span>
          }
        />
        <div className="card p-6 max-w-2xl">
          <div className="text-warn font-semibold mb-2">No search queries recovered</div>
          <p className="text-sm text-muted leading-relaxed">
            Search queries are reconstructed from URLs already present in a pulled Chromium{" "}
            <code className="text-ink">History</code> database — the parser reads the{" "}
            <code className="text-ink">urls</code> table and extracts the <code className="text-ink">q=</code>{" "}
            parameter from search-engine hosts. An empty result means one of three distinct things, and this
            view cannot tell them apart on its own:
          </p>
          <ul className="text-sm text-muted leading-relaxed mt-2 space-y-1 list-disc pl-5">
            <li>
              <strong className="text-ink">Not acquired</strong> — no browser database was pulled. On a
              non-rooted device the Chromium History DB lives in app-private storage and is unreachable at
              Tier 0/1.
            </li>
            <li>
              <strong className="text-ink">Acquired but empty</strong> — the database was read and contained no
              search-engine URLs (history cleared by the user, or a different browser was used).
            </li>
            <li>
              <strong className="text-ink">Present but unparsed</strong> — searches made inside an app's own
              search box, in a private/incognito session, or on a search engine outside the recognised host
              list are never written to this table at all.
            </li>
          </ul>
          <p className="text-sm text-muted leading-relaxed mt-2">
            The chain-of-custody trail distinguishes them: look for a <code className="text-ink">parse.search</code>{" "}
            event and for the browser database in the artifact manifest.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <SectionHeader
        title="Search History"
        sub={`${data.length} queries recovered`}
        right={
          <span className="text-xs font-normal text-muted bg-panel-2 border border-line rounded px-2 py-0.5">
            Tier 0 — Read-only
          </span>
        }
      />

      {/* Forensic caveat — attribution is the whole problem with this artifact. */}
      <div className="card p-3 mb-4 border-warn/40 bg-warn/5 text-xs text-warn leading-relaxed">
        <span className="font-semibold">Forensic notice: </span>
        A query in browser history establishes that the query was{" "}
        <strong>issued from this browser profile on this device</strong> — nothing more. It does not identify
        who typed it. Shared devices, an unlocked handset, an autocomplete suggestion accepted by mistake, a
        redirect, a page prefetch, or a synced profile from another device all produce identical rows. History
        is also fully <strong>user-editable</strong>: entries can be deleted individually or cleared wholesale,
        so this list is a floor, never a complete record of activity. Rows sourced from the Google app{" "}
        <strong>cache are fragments</strong> — residue that happened to survive, not a history.
      </div>

      {/* Summary tiles */}
      <div className="flex flex-wrap gap-3 mb-4">
        <StatTile value={String(summary.total ?? data.length)} label="Queries" note="Rows recovered, after de-duplication" />
        <StatTile
          value={String(summary.unique_queries ?? new Set(data.map((r) => (r.query ?? "").toLowerCase())).size)}
          label="Distinct query strings"
        />
        <StatTile
          value={String(summary.with_timestamp ?? data.length - undated)}
          label="With a timestamp"
          note={undated > 0 ? `${undated} row(s) carry no usable time` : undefined}
        />
        <StatTile
          value={String(summary.suspicious ?? flagged)}
          label="Keyword-matched"
          note="Heuristic index only — not a finding"
          tone={(summary.suspicious ?? flagged) > 0 ? "text-warn" : "text-ink"}
        />
      </div>

      {/* Filter */}
      <div className="mb-3">
        <input
          className="input max-w-sm"
          placeholder="Filter by query, URL or source…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      {/* Table */}
      <div className="card overflow-auto">
        <table className="w-full text-sm">
          <thead>
            <tr>
              <th className="th w-44">Time (UTC)</th>
              <th className="th">Query / URL</th>
              <th className="th w-36">Source</th>
              <th className="th w-20">Visits</th>
              <th className="th w-36">Keyword flag</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={5} className="td text-center text-muted text-xs py-6">
                  No queries match your filter.
                </td>
              </tr>
            ) : (
              filtered.map((r, i) => {
                const url = r.url ?? r.clicked_url ?? "";
                const rowCaveats = [...(r.caveats ?? []), ...(r.warnings ?? [])];
                return (
                  <tr key={i}>
                    <td className="td font-mono text-xs text-muted whitespace-nowrap">
                      {r.timestamp ? (
                        fmtTs(r.timestamp)
                      ) : (
                        <span className="italic" title="No visit time survived in the source record">
                          undated
                        </span>
                      )}
                    </td>
                    <td className="td">
                      <div className="text-ink break-words">{r.query || <span className="text-muted italic">(empty query)</span>}</div>
                      {url ? (
                        <div
                          className="text-[11px] font-mono text-muted break-all mt-0.5 select-all"
                          title="Rendered as inert text — this tool never fetches a URL from the evidence"
                        >
                          {url}
                        </div>
                      ) : (
                        <div className="text-[11px] text-muted italic mt-0.5">no URL recorded for this row</div>
                      )}
                      <CaveatList items={rowCaveats} />
                    </td>
                    <td className="td">
                      <SourceBadge value={r.source} />
                    </td>
                    <td className="td font-mono text-xs">
                      {typeof r.visit_count === "number" ? (
                        r.visit_count
                      ) : (
                        <span className="text-muted italic" title="Visit counts exist only for browser-history rows">
                          n/a
                        </span>
                      )}
                    </td>
                    <td className="td">{r.is_suspicious ? <HeuristicFlag /> : <span className="text-muted text-xs">—</span>}</td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      <p className="text-[11px] text-muted mt-3 leading-snug">
        <strong className="text-warn">Keyword flag:</strong> a case-insensitive substring match of the query
        against a fixed word list. It is a heuristic flag, not a finding, and it carries no assessment of
        intent — the same phrase appears in journalism, research, fiction and idle curiosity. Read the query
        text yourself; treat the flag only as a reason to look.
      </p>
      <p className="text-[11px] text-muted mt-1 mb-6 leading-snug">
        <strong className="text-warn">Visit counts</strong> are Chromium's own per-URL counter for the whole
        profile lifetime, not a count of visits inside any window shown here, and they are reset when history
        is cleared.
      </p>
    </div>
  );
}
