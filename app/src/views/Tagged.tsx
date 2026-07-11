import { useTags } from "../lib/tagStore";
import type { ViewKey } from "../components/Sidebar";
import { SectionHeader, EmptyState } from "../components/common";
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

  if (tags.length === 0)
    return (
      <EmptyState
        title="No tagged items yet"
        detail="Bookmark items of interest with the ☆ button in any view. Tagged items are collected here and included in the report — the on-scene tagging commercial field tools provide."
      />
    );

  return (
    <div className="p-6 h-full flex flex-col">
      <SectionHeader title="Tagged Items" sub={`${tags.length} bookmarked for follow-up`} />
      <div className="card overflow-auto flex-1">
        <table className="w-full text-sm">
          <thead>
            <tr>
              <th className="th w-24">Kind</th>
              <th className="th">Label</th>
              <th className="th w-44">Tagged</th>
              <th className="th w-24"></th>
            </tr>
          </thead>
          <tbody>
            {tags.map((t) => (
              <tr key={t.id}>
                <td className="td">
                  <span className="text-[10px] uppercase font-mono text-accent">{t.kind}</span>
                </td>
                <td className="td">
                  <div className="flex items-center gap-2">
                    <span className="text-accent">★</span>
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
