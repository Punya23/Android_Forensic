import { useEffect, useMemo, useState } from "react";
import type { BrowserEntry } from "../lib/types";
import { useDataset, fmtTs } from "../lib/hooks";
import { TagButton } from "../lib/tagStore";
import { Filters, SectionHeader, EmptyState } from "../components/common";

// A real Chromium History database can hold tens of thousands of URLs — capped like
// Aleapp.tsx's table, with the same disclosed "show all" rather than a silent slice.
const TABLE_CAP = 1000;

export function BrowserView({ caseId }: { caseId: string }) {
  const { data, loading } = useDataset<BrowserEntry>(caseId, "browser");
  const [query, setQuery] = useState("");
  const [showAll, setShowAll] = useState(false);

  const filtered = useMemo(() => {
    const q = query.toLowerCase();
    return data.filter((h) => !q || h.url.toLowerCase().includes(q) || h.title.toLowerCase().includes(q));
  }, [data, query]);

  useEffect(() => setShowAll(false), [query]);
  const visible = showAll ? filtered : filtered.slice(0, TABLE_CAP);

  if (loading) return <div className="p-8 text-muted">Loading browser history…</div>;
  if (data.length === 0)
    return (
      <EmptyState
        dataset="browser"
        title="No browser history"
        detail="No Chromium History database was available. On a non-rooted device this is typically an app-private artifact (Tier 2 / extracted image)."
      />
    );

  return (
    <div className="p-6 h-full flex flex-col">
      <SectionHeader title="Browser History" sub={`${data.length} URLs`} />
      <Filters query={query} onQuery={setQuery} placeholder="Search URL or title…" />
      <div className="card overflow-auto flex-1">
        <table className="w-full text-sm">
          <thead>
            <tr>
              <th className="th w-8"></th>
              <th className="th w-40">Last visit</th>
              <th className="th w-32">Browser</th>
              <th className="th">Title / URL</th>
              <th className="th w-20">Visits</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((h, i) => (
              <tr key={i}>
                <td className="td"><TagButton refId={`browser:${i}`} kind="browser" label={h.title || h.url} /></td>
                <td className="td font-mono text-xs text-muted whitespace-nowrap">{fmtTs(h.last_visit)}</td>
                <td className="td text-xs text-muted">{h.browser_app || "—"}</td>
                <td className="td">
                  <div className="font-medium">{h.title || "(untitled)"}</div>
                  <a href={h.url} target="_blank" rel="noreferrer" className="text-xs text-recovered/80 hover:underline break-all font-mono">
                    {h.url}
                  </a>
                </td>
                <td className="td font-mono">{h.visit_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!showAll && filtered.length > TABLE_CAP && (
          <div className="text-center py-3">
            <button className="btn-ghost text-xs" onClick={() => setShowAll(true)}>
              Showing first {TABLE_CAP.toLocaleString()} of {filtered.length.toLocaleString()} URLs — click to show all
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
