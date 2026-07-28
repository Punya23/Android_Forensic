/**
 * TimelineView — Cross-Artifact Chronological Event Feed
 *
 * Merges messages, calls, geotagged media, and location points into a single
 * vertical timeline, grouped by calendar date.  Supports:
 *
 *   • Keyword text search across event summaries
 *   • Date-range filter (from / to)
 *   • Kind-pill toggle (click to show only that type)
 *   • Click-to-expand for full event detail and source provenance
 *   • Stats bar: total events, filtered count, date span, event-kind breakdown
 *   • CSV export of the current filtered view
 */
import { useMemo, useState, useCallback } from "react";
import type { TimelineEvent } from "../lib/types";
import { useDataset, fmtTs } from "../lib/hooks";
import { ConfidenceBadge } from "../components/Badges";
import { Filters, SectionHeader, EmptyState } from "../components/common";

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const KIND_META: Record<string, { icon: string; color: string; label: string }> = {
  message:  { icon: "💬", color: "border-blue-400",   label: "Message"  },
  call:     { icon: "📞", color: "border-green-400",  label: "Call"     },
  media:    { icon: "🖼",  color: "border-orange-400", label: "Media"    },
  location: { icon: "📍", color: "border-accent",     label: "Location" },
  wifi:     { icon: "📶", color: "border-purple-400", label: "Wi-Fi"    },
  app:      { icon: "📱", color: "border-pink-400",   label: "App Use"  },
  recovery: { icon: "🔍", color: "border-yellow-400", label: "Recovered"},
};

const DEFAULT_META = { icon: "•", color: "border-muted", label: "Event" };

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function groupByDate(events: TimelineEvent[]): Map<string, TimelineEvent[]> {
  const map = new Map<string, TimelineEvent[]>();
  for (const e of events) {
    const key = e.timestamp ? e.timestamp.slice(0, 10) : "Unknown date";
    const bucket = map.get(key) ?? [];
    bucket.push(e);
    map.set(key, bucket);
  }
  return map;
}

function fmtDate(iso: string): string {
  if (iso === "Unknown date") return iso;
  try {
    return new Date(iso + "T12:00:00Z").toLocaleDateString(undefined, {
      weekday: "short", year: "numeric", month: "long", day: "numeric",
    });
  } catch {
    return iso;
  }
}

function exportCsv(events: TimelineEvent[]) {
  const header = "timestamp,kind,summary,confidence,source\n";
  const rows = events.map((e) =>
    [
      e.timestamp ?? "",
      e.kind,
      `"${(e.summary ?? "").replace(/"/g, '""')}"`,
      e.confidence ?? "",
      `"${((e as any).source ?? "").replace(/"/g, '""')}"`,
    ].join(",")
  );
  const blob = new Blob([header + rows.join("\n")], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = "timeline.csv"; a.click();
  URL.revokeObjectURL(url);
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function StatsBar({
  total,
  filtered,
  events,
}: {
  total: number;
  filtered: number;
  events: TimelineEvent[];
}) {
  const kinds = events.reduce<Record<string, number>>((acc, e) => {
    acc[e.kind] = (acc[e.kind] ?? 0) + 1;
    return acc;
  }, {});

  const timestamps = events.map((e) => e.timestamp).filter(Boolean) as string[];
  const earliest = timestamps.length ? timestamps[timestamps.length - 1] : null;
  const latest   = timestamps.length ? timestamps[0] : null;

  return (
    <div className="flex flex-wrap items-center gap-4 mb-3 text-xs text-muted bg-panel-2 rounded-lg px-4 py-2.5">
      <span className="font-mono">
        <strong className="text-ink">{filtered}</strong>
        {filtered !== total && <span> / {total}</span>} events
      </span>
      {earliest && latest && earliest !== latest && (
        <span className="font-mono">
          {earliest.slice(0, 10)} → {latest.slice(0, 10)}
        </span>
      )}
      <span className="border-l border-line h-4" />
      {Object.entries(kinds)
        .sort((a, b) => b[1] - a[1])
        .map(([k, n]) => {
          const m = KIND_META[k] ?? DEFAULT_META;
          return (
            <span key={k} className="flex items-center gap-1">
              {m.icon} {n} {m.label}
            </span>
          );
        })}
    </div>
  );
}

function EventCard({ e, expanded, onToggle }: {
  e: TimelineEvent;
  expanded: boolean;
  onToggle: () => void;
}) {
  const meta = KIND_META[e.kind] ?? DEFAULT_META;
  const src = (e as any).source as string | undefined;
  const app = (e as any).app as string | undefined;
  const extra = (e as any).extra as Record<string, unknown> | undefined;

  return (
    <div
      className="relative pl-6 pb-4 cursor-pointer group"
      onClick={onToggle}
    >
      {/* Timeline dot */}
      <div
        className={`absolute -left-[7px] top-1.5 h-3 w-3 rounded-full bg-panel border-2 transition-transform
          group-hover:scale-125 ${meta.color}`}
      />

      {/* Timestamp + confidence */}
      <div className="flex items-center gap-2 text-[11px] text-muted font-mono leading-none mb-0.5">
        <span>{fmtTs(e.timestamp)}</span>
        {e.confidence && e.confidence !== "live" && <ConfidenceBadge c={e.confidence} />}
        {app && <span className="text-muted/60">· {app}</span>}
      </div>

      {/* Summary */}
      <div className="text-sm leading-snug">
        <span className="mr-1.5 text-base">{meta.icon}</span>
        {e.summary}
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div className="mt-2 ml-0.5 border border-line rounded-lg bg-panel-2 p-3 text-xs text-muted space-y-1.5 select-text"
          onClick={(ev) => ev.stopPropagation()}>
          {src && (
            <div className="font-mono break-all">
              <span className="text-muted/60 mr-1">source:</span>{src}
            </div>
          )}
          {e.confidence && (
            <div>
              <span className="text-muted/60 mr-1">confidence:</span>
              <span className="capitalize">{e.confidence}</span>
            </div>
          )}
          {extra && Object.keys(extra).length > 0 && (
            <pre className="font-mono text-[10px] overflow-auto max-h-32 bg-panel rounded p-2">
              {JSON.stringify(extra, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main view
// ---------------------------------------------------------------------------

export function TimelineView({ caseId }: { caseId: string }) {
  const { data, loading } = useDataset<TimelineEvent>(caseId, "timeline");
  const [query,    setQuery]    = useState("");
  const [from,     setFrom]     = useState("");
  const [to,       setTo]       = useState("");
  const [kinds,    setKinds]    = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  const filtered = useMemo(() => {
    const q = query.toLowerCase();
    return data.filter((e) => {
      if (q && !e.summary.toLowerCase().includes(q)) return false;
      if (kinds.size && !kinds.has(e.kind)) return false;
      if (from && e.timestamp && e.timestamp < from) return false;
      if (to   && e.timestamp && e.timestamp > to + "T23:59:59") return false;
      return true;
    });
  }, [data, query, from, to, kinds]);

  const grouped = useMemo(() => groupByDate(filtered), [filtered]);

  const toggleKind = useCallback((k: string) => {
    setKinds((prev) => {
      const next = new Set(prev);
      next.has(k) ? next.delete(k) : next.add(k);
      return next;
    });
  }, []);

  const toggleExpanded = useCallback((idx: number) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(idx) ? next.delete(idx) : next.add(idx);
      return next;
    });
  }, []);

  if (loading) return <div className="p-8 text-muted">Loading timeline…</div>;
  if (data.length === 0)
    return (
      <EmptyState
        title="No timeline events"
        detail="Timeline requires timestamped artifacts — run acquisition with messaging, calls, or geotagged media."
      />
    );

  const allKinds = Array.from(new Set(data.map((e) => e.kind)));
  // Running global index so each event has a stable ID for expand tracking
  let globalIdx = 0;

  return (
    <div className="p-6 h-full flex flex-col">
      {/* Header */}
      <SectionHeader
        title="Timeline"
        sub={`${data.length} events across calls, messages, media & locations`}
        right={
          <button
            id="btn-export-timeline-csv"
            className="btn-ghost text-xs py-1"
            onClick={() => exportCsv(filtered)}
            title="Download filtered events as CSV"
          >
            ⬇ CSV
          </button>
        }
      />

      {/* Filters */}
      <Filters
        query={query}
        onQuery={setQuery}
        from={from}
        to={to}
        onFrom={setFrom}
        onTo={setTo}
        placeholder="Search events…"
      />

      {/* Kind-pill toggles */}
      <div className="flex flex-wrap gap-2 mb-3">
        {allKinds.map((k) => {
          const meta = KIND_META[k] ?? DEFAULT_META;
          const active = kinds.size === 0 || kinds.has(k);
          return (
            <button
              key={k}
              id={`kind-filter-${k}`}
              onClick={() => toggleKind(k)}
              className={`flex items-center gap-1 px-3 py-1 rounded-full text-xs border transition-colors ${
                active
                  ? "border-accent/70 text-ink bg-accent/10"
                  : "border-line text-muted bg-transparent"
              }`}
            >
              <span>{meta.icon}</span>
              <span>{meta.label}</span>
            </button>
          );
        })}
        {kinds.size > 0 && (
          <button
            className="text-xs text-muted underline"
            onClick={() => setKinds(new Set())}
          >
            clear
          </button>
        )}
      </div>

      {/* Stats bar */}
      <StatsBar total={data.length} filtered={filtered.length} events={filtered} />

      {/* Empty filtered state */}
      {filtered.length === 0 && (
        <div className="flex-1 flex items-center justify-center text-muted text-sm">
          No events match your current filters.
        </div>
      )}

      {/* Main feed — grouped by date */}
      {filtered.length > 0 && (
        <div className="overflow-auto flex-1 pl-2">
          <div className="border-l-2 border-line ml-2">
            {Array.from(grouped.entries()).map(([date, evts]) => (
              <div key={date}>
                {/* Date separator */}
                <div className="relative pl-6 pb-2 pt-4 -ml-[1px]">
                  <div className="absolute -left-[9px] top-5 h-4 w-4 rounded-full
                    bg-panel border-2 border-muted flex items-center justify-center">
                    <span className="text-[8px] text-muted">📅</span>
                  </div>
                  <span className="text-xs font-semibold text-muted tracking-wide uppercase">
                    {fmtDate(date)}
                    <span className="ml-2 font-normal text-muted/60">
                      ({evts.length} event{evts.length !== 1 ? "s" : ""})
                    </span>
                  </span>
                </div>

                {/* Events in this day bucket */}
                {evts.map((e) => {
                  const idx = globalIdx++;
                  return (
                    <EventCard
                      key={idx}
                      e={e}
                      expanded={expanded.has(idx)}
                      onToggle={() => toggleExpanded(idx)}
                    />
                  );
                })}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
