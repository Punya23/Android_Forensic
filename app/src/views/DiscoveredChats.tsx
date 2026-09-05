/**
 * DiscoveredChats — output of the generic "Dynamic App Finder".
 *
 * Shows chat-like tables auto-discovered in otherwise-unrecognised SQLite databases (the
 * open-source analogue of Cellebrite App Genie / Magnet Dynamic App Finder), plus every
 * message extracted from them with its confidence badge.
 */
import { useEffect, useMemo, useState } from "react";
import type { DiscoveredChats } from "../lib/types";
import { ConfidenceBadge } from "../components/Badges";
import { SectionHeader, EmptyState, Filters, StatCard } from "../components/common";
import { fmtTs } from "../lib/hooks";
import { api } from "../lib/api";

export function DiscoveredChatsView({ caseId }: { caseId: string }) {
  const [data, setData] = useState<DiscoveredChats | null>(null);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  // Which summary-table row (db + table) the messages list below is scoped to, if any.
  const [selectedTable, setSelectedTable] = useState<{ db: string; table: string } | null>(null);

  useEffect(() => {
    setLoading(true);
    setSelectedTable(null); // a case switch can drop the db/table this was scoped to
    api
      .discoveredChats(caseId)
      .then((d: DiscoveredChats) => setData(d))
      .catch(() => setData({ tables: [], messages: [] }))
      .finally(() => setLoading(false));
  }, [caseId]);

  const messages = data?.messages ?? [];
  const filtered = useMemo(() => {
    const q = query.toLowerCase();
    return messages.filter((m) => {
      const matchesQuery =
        !q || (m.body || "").toLowerCase().includes(q) || (m.app || "").toLowerCase().includes(q);
      if (!matchesQuery) return false;
      if (!selectedTable) return true;
      // m.app is stamped "<db-label>:<table>" and m.source_file is the db filename (see
      // engine/triage/parsers/appfinder.py scan_sqlite_for_chats) — the same two fields the
      // summary row below is built from (t.db / t.table), so match on both.
      if (m.source_file !== selectedTable.db) return false;
      const app = m.app ?? "";
      const table = app.includes(":") ? app.slice(app.lastIndexOf(":") + 1) : app;
      return table === selectedTable.table;
    });
  }, [messages, query, selectedTable]);

  if (loading) return <div className="p-8 text-muted">Scanning discovered chats…</div>;
  if (!data || (data.tables.length === 0 && messages.length === 0))
    return (
      <EmptyState
        dataset="discovered_chats"
        title="No unknown-app chats discovered"
        detail="The Dynamic App Finder scans every unrecognised SQLite database for chat-like tables (a text column + a timestamp column) and auto-classifies them. Nothing matched in this acquisition."
      />
    );

  const live = messages.filter((m) => m.confidence === "live").length;
  const recovered = messages.length - live;

  return (
    <div className="p-6 h-full flex flex-col">
      <SectionHeader
        title="Discovered Chats"
        sub="Generic Dynamic App Finder — chat tables auto-detected in unknown app databases"
      />

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-4">
        <StatCard n={data.tables.length} label="Tables found" />
        <StatCard n={messages.length} label="Messages" />
        <StatCard n={live} label="Live" tone="text-live" />
        <StatCard n={recovered} label="Recovered/Carved" tone="text-carved" />
      </div>

      {data.tables.length > 0 && (
        <div className="card overflow-auto mb-4">
          <table className="w-full text-sm">
            <thead>
              <tr>
                <th className="th">Database</th>
                <th className="th">Table</th>
                <th className="th">Text col</th>
                <th className="th">Time col</th>
                <th className="th">Sender col</th>
                <th className="th">Live</th>
                <th className="th">Recovered</th>
              </tr>
            </thead>
            <tbody>
              {data.tables.map((t, i) => {
                const isActive = selectedTable?.db === t.db && selectedTable?.table === t.table;
                return (
                  <tr
                    key={i}
                    onClick={() =>
                      setSelectedTable(isActive ? null : { db: t.db, table: t.table })
                    }
                    className={`cursor-pointer transition-colors ${
                      isActive ? "bg-accent/15" : "hover:bg-panel-2/50"
                    }`}
                    title={isActive ? "Click to clear filter" : "Click to show only this table's messages"}
                  >
                    <td className="td font-mono text-xs">{t.db}</td>
                    <td className="td font-medium">{t.table}</td>
                    <td className="td font-mono text-xs text-muted">{t.roles.text ?? "—"}</td>
                    <td className="td font-mono text-xs text-muted">{t.roles.timestamp ?? "—"}</td>
                    <td className="td font-mono text-xs text-muted">{t.roles.sender ?? "—"}</td>
                    <td className="td">{t.live}</td>
                    <td className="td">{t.recovered}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {selectedTable && (
        <div className="flex items-center gap-2 mb-3 text-xs">
          <span className="text-muted">
            Showing messages from{" "}
            <span className="font-mono text-ink">
              {selectedTable.db}:{selectedTable.table}
            </span>{" "}
            only
          </span>
          <button className="btn-ghost text-xs" onClick={() => setSelectedTable(null)}>
            Clear filter
          </button>
        </div>
      )}

      <Filters query={query} onQuery={setQuery} placeholder="Search messages or app…" />
      <div className="card overflow-auto flex-1">
        <table className="w-full text-sm">
          <thead>
            <tr>
              <th className="th w-44">App : Table</th>
              <th className="th">Message</th>
              <th className="th w-36">Sender</th>
              <th className="th w-40">Timestamp</th>
              <th className="th w-28">Confidence</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((m, i) => (
              <tr key={i}>
                <td className="td font-mono text-xs text-muted">{m.app ?? "—"}</td>
                <td className="td">{m.body}</td>
                <td className="td text-xs">{m.sender_name || "—"}</td>
                <td className="td text-xs font-mono">{fmtTs(m.timestamp)}</td>
                <td className="td"><ConfidenceBadge c={m.confidence} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
