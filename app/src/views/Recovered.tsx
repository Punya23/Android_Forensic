import { useMemo, useState } from "react";
import type { Confidence, RecoveredRow } from "../lib/types";
import { useDataset } from "../lib/hooks";
import { ConfidenceBadge } from "../components/Badges";
import { TagButton } from "../lib/tagStore";
import { Filters, SectionHeader, EmptyState } from "../components/common";

export function RecoveredView({ caseId }: { caseId: string }) {
  const { data, loading } = useDataset<RecoveredRow>(caseId, "recovered");
  const [query, setQuery] = useState("");
  const [conf, setConf] = useState<Confidence | "all">("all");

  const filtered = useMemo(() => {
    const q = query.toLowerCase();
    return data.filter((r) => {
      if (conf !== "all" && r.confidence !== conf) return false;
      if (!q) return true;
      return r.values.some((v) => typeof v === "string" && v.toLowerCase().includes(q));
    });
  }, [data, query, conf]);

  const byConf = useMemo(() => {
    const m: Record<string, number> = { live: 0, recovered: 0, carved: 0, deletion: 0 };
    data.forEach((r) => (m[r.confidence] = (m[r.confidence] ?? 0) + 1));
    return m;
  }, [data]);

  if (loading) return <div className="p-8 text-muted">Loading recovered data…</div>;
  if (data.length === 0)
    return <EmptyState title="No deleted data recovered" detail="No freelist / freeblock / WAL remnants were carved from the acquired databases." />;

  const chips: { k: Confidence | "all"; label: string; n: number }[] = [
    { k: "all", label: "All", n: data.length },
    { k: "recovered", label: "Recovered–Verified", n: byConf.recovered },
    { k: "carved", label: "Carved–Partial", n: byConf.carved },
    { k: "deletion", label: "Deletion Detected", n: byConf.deletion },
  ];

  return (
    <div className="p-6 h-full flex flex-col">
      <SectionHeader
        title="Recovered / Deleted Data"
        sub="Carved from SQLite freelist, freeblocks, unallocated space & WAL — never shown with the same weight as live data"
      />
      <div className="card border-carved/30 bg-carved/5 p-2.5 mb-3 text-xs text-muted">
        <span className="text-carved font-semibold">Analyst note:</span> Carved rows may be
        corrupt, fragmentary, or belong to an overlapping record. Each carries its byte-level
        provenance (source file · page · offset) so it can be independently verified in a hex viewer.
      </div>
      <div className="flex flex-wrap gap-2 mb-3">
        {chips.map((c) => (
          <button
            key={c.k}
            onClick={() => setConf(c.k)}
            className={`px-3 py-1 rounded-full text-xs border transition-colors ${
              conf === c.k ? "border-accent bg-accent/15 text-accent" : "border-line text-muted hover:border-muted"
            }`}
          >
            {c.label} <span className="opacity-60">{c.n}</span>
          </button>
        ))}
      </div>
      <Filters query={query} onQuery={setQuery} placeholder="Search recovered content…" />
      <div className="card overflow-auto flex-1">
        <table className="w-full text-sm">
          <thead>
            <tr>
              <th className="th w-8"></th>
              <th className="th w-28">Confidence</th>
              <th className="th">Recovered content</th>
              <th className="th w-56">Provenance</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((r, i) => (
              <tr key={i} className="align-top">
                <td className="td"><TagButton refId={`recovered:${i}`} kind="recovered" label={r.values.filter((v) => typeof v === "string").join(" ").slice(0, 40)} /></td>
                <td className="td"><ConfidenceBadge c={r.confidence} title={r.warnings[0]} /></td>
                <td className="td">
                  <div className="flex flex-wrap gap-1.5">
                    {r.values
                      .filter((v) => v !== null && v !== "")
                      .map((v, j) => (
                        <span key={j} className="bg-panel px-1.5 py-0.5 rounded text-xs">
                          {fmtVal(v)}
                        </span>
                      ))}
                  </div>
                  {r.warnings.length > 0 && (
                    <div className="text-[10px] text-carved/80 mt-1">⚠ {r.warnings[0]}</div>
                  )}
                </td>
                <td className="td font-mono text-[11px] text-muted">
                  {r.source_file}
                  <div className="text-muted/60">{r.provenance}</div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function fmtVal(v: string | number | null | { __blob__: string; len: number }): string {
  if (v === null) return "∅";
  if (typeof v === "object" && "__blob__" in v) return `‹blob ${v.len}B›`;
  return String(v);
}
