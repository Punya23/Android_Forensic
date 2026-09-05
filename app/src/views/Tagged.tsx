import { useState } from "react";
import { Star } from "lucide-react";
import { useTags } from "../lib/tagStore";
import type { ViewKey } from "../components/Sidebar";
import type { Tag } from "../lib/types";
import { SectionHeader, EmptyState, SortTh, useSort } from "../components/common";
import { fmtTs } from "../lib/hooks";

const KIND_TO_VIEW: Record<string, ViewKey> = {
  message: "messages",
  recovered: "recovered",
  media: "media",
  contact: "contacts",
  call: "calls",
  browser: "browser",
};

export function TaggedView({ caseId, setView }: { caseId: string; setView: (v: ViewKey) => void }) {
  const { tags, remove } = useTags();
  const [query, setQuery] = useState("");
  // Hooks must run unconditionally on every render — computed here, before the
  // empty-state check below, rather than after it.
  const filtered = tags.filter((t) => {
    if (!query) return true;
    const q = query.toLowerCase();
    return (
      t.label.toLowerCase().includes(q) ||
      t.kind.toLowerCase().includes(q) ||
      (t.note ?? "").toLowerCase().includes(q)
    );
  });
  const sort = useSort<Tag>(filtered);

  if (tags.length === 0)
    return (
      <EmptyState
        title="No tagged items yet"
        detail="Bookmark items of interest with the star button in any view. Tagged items are collected here and included in the report — the on-scene tagging commercial field tools provide."
      />
    );

  return (
    <div className="p-6 h-full flex flex-col">
      <SectionHeader title="Tagged Items" sub={`${tags.length} bookmarked for follow-up`} />
      <input
        className="input max-w-xs mb-3"
        placeholder="Search label, kind, or note…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      <div className="card overflow-auto flex-1">
        <table className="w-full text-sm">
          <thead>
            <tr>
              <SortTh className="th w-24" label="Kind" sortKeyName="kind" getValue={(t: Tag) => t.kind} sort={sort} />
              <th className="th">Label</th>
              <SortTh className="th w-44" label="Tagged" sortKeyName="at" getValue={(t: Tag) => t.at} sort={sort} />
              <th className="th w-24"></th>
            </tr>
          </thead>
          <tbody>
            {sort.sorted.map((t) => (
              <tr key={t.id}>
                <td className="td">
                  <span className="text-[10px] uppercase font-mono text-accent">{t.kind}</span>
                </td>
                <td className="td">
                  <div className="flex items-center gap-2">
                    <Star className="inline h-3.5 w-3.5 text-accent" strokeWidth={1.75} fill="currentColor" aria-hidden />
                    <span>{t.label}</span>
                  </div>
                  <div className="text-[10px] text-muted font-mono">{t.ref}</div>
                </td>
                <td className="td text-xs text-muted font-mono">
                  {fmtTs(t.at)}
                  <div>by {t.by}</div>
                </td>
                <td className="td">
                  <div className="flex gap-2">
                    {KIND_TO_VIEW[t.kind] && (
                      <button className="text-xs text-recovered hover:underline" onClick={() => setView(KIND_TO_VIEW[t.kind])}>
                        go to
                      </button>
                    )}
                    <button className="text-xs text-deletion hover:underline" onClick={() => remove(t.id)}>
                      remove
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
