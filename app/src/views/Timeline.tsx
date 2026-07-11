import { useMemo, useState } from "react";
import type { TimelineEvent } from "../lib/types";
import { useDataset, fmtTs } from "../lib/hooks";
import { ConfidenceBadge } from "../components/Badges";
import { Filters, SectionHeader, EmptyState } from "../components/common";

const KIND_META: Record<string, { icon: string; color: string }> = {
  message: { icon: "💬", color: "border-recovered" },
  call: { icon: "📞", color: "border-live" },
  media: { icon: "🖼", color: "border-carved" },
  location: { icon: "📍", color: "border-accent" },
};

export function TimelineView({ caseId }: { caseId: string }) {
  const { data, loading } = useDataset<TimelineEvent>(caseId, "timeline");
  const [query, setQuery] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [kinds, setKinds] = useState<Set<string>>(new Set());

  const filtered = useMemo(() => {
    const q = query.toLowerCase();
    return data.filter((e) => {
      if (q && !e.summary.toLowerCase().includes(q)) return false;
      if (kinds.size && !kinds.has(e.kind)) return false;
      if (from && e.timestamp < from) return false;
      if (to && e.timestamp > to + "T23:59:59") return false;
      return true;
    });
  }, [data, query, from, to, kinds]);

  if (loading) return <div className="p-8 text-muted">Loading timeline…</div>;
  if (data.length === 0)
    return <EmptyState title="No timeline events" detail="Timeline needs timestamped artifacts (messages, calls, geotagged media)." />;

  const allKinds = Array.from(new Set(data.map((e) => e.kind)));

  function toggleKind(k: string) {
    const next = new Set(kinds);
    next.has(k) ? next.delete(k) : next.add(k);
    setKinds(next);
  }

  return (
    <div className="p-6 h-full flex flex-col">
      <SectionHeader title="Timeline" sub={`${data.length} events across calls, messages, media & locations`} />
      <Filters query={query} onQuery={setQuery} from={from} to={to} onFrom={setFrom} onTo={setTo} placeholder="Search events…" />
      <div className="flex gap-2 mb-4">
        {allKinds.map((k) => (
          <button
            key={k}
            onClick={() => toggleKind(k)}
            className={`px-3 py-1 rounded-full text-xs border transition-colors ${
              kinds.has(k) || kinds.size === 0 ? "border-accent/60 text-ink" : "border-line text-muted"
            }`}
          >
            {KIND_META[k]?.icon} {k}
          </button>
        ))}
      </div>
      <div className="overflow-auto flex-1 pl-2">
        <div className="border-l-2 border-line ml-2">
          {filtered.map((e, i) => {
            const meta = KIND_META[e.kind] ?? { icon: "•", color: "border-muted" };
            return (
              <div key={i} className="relative pl-6 pb-4">
                <div className={`absolute -left-[7px] top-1 h-3 w-3 rounded-full bg-panel-2 border-2 ${meta.color}`} />
                <div className="flex items-center gap-2 text-xs text-muted font-mono">
                  {fmtTs(e.timestamp)}
                  {e.confidence !== "live" && <ConfidenceBadge c={e.confidence} />}
                </div>
                <div className="text-sm mt-0.5">
                  <span className="mr-1.5">{meta.icon}</span>
                  {e.summary}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
