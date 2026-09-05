import { useState } from "react";
import type { CallRecord } from "../lib/types";
import { useDataset, fmtTs } from "../lib/hooks";
import { SectionHeader, SortTh, useSort } from "../components/common";

export function CallsView({ caseId }: { caseId: string }) {
  const { data, loading } = useDataset<CallRecord>(caseId, "calls");
  const [query, setQuery] = useState("");
  // Hooks must run unconditionally on every render — computed here, before either
  // early return below, rather than after the empty-state check.
  const filtered = data.filter(
    (c) => !query || c.number.includes(query) || c.name.toLowerCase().includes(query.toLowerCase()),
  );
  const sort = useSort<CallRecord>(filtered);

  if (loading) return <div className="p-8 text-muted">Loading calls…</div>;

  // Honest empty state: the call log is one of the most intrusive artifacts to reach
  // without root — it needs the Collector helper installed and READ_CALL_LOG granted.
  // If we didn't get it, say exactly why, and describe the mechanism the engine actually
  // uses (`pm grant`, not a Dialer role swap — see pipeline._run_tier1_calllog_helper).
  if (data.length === 0) {
    return (
      <div className="p-6">
        <SectionHeader title="Calls" />
        <div className="card p-6 max-w-2xl">
          <div className="text-warn font-semibold mb-2">Not acquired at this tier</div>
          <p className="text-sm text-muted leading-relaxed">
            The call log is gated by <code className="text-ink">READ_CALL_LOG</code>, a
            <em> hard-restricted</em> Android permission. Reaching it without root requires
            installing the Collector helper APK and granting that permission with{" "}
            <code className="text-ink">pm grant</code> — a state-changing step this
            Tier-0/Tier-1 acquisition did not perform. The device's default Dialer role is
            never reassigned.
          </p>
          <p className="text-sm text-muted leading-relaxed mt-2">
            This is an intentional scope choice, not a gap: enable the call-log path in
            Advanced Mode only with explicit authority, and it will be logged and reverted in
            the audit trail.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 h-full flex flex-col">
      <SectionHeader title="Calls" sub={`${data.length} call records`} />
      <input className="input max-w-xs mb-3" placeholder="Search number or name…" value={query} onChange={(e) => setQuery(e.target.value)} />
      <div className="card overflow-auto flex-1">
        <table className="w-full text-sm">
          <thead>
            <tr>
              <SortTh className="th w-40" label="Time" sortKeyName="timestamp" getValue={(c) => c.timestamp} sort={sort} />
              <SortTh className="th" label="Number" sortKeyName="number" getValue={(c) => c.number} sort={sort} />
              <SortTh className="th" label="Name" sortKeyName="name" getValue={(c) => c.name} sort={sort} />
              <SortTh className="th w-28" label="Type" sortKeyName="call_type" getValue={(c) => c.call_type} sort={sort} />
              <SortTh className="th w-24" label="Duration" sortKeyName="duration_s" getValue={(c) => c.duration_s} sort={sort} />
            </tr>
          </thead>
          <tbody>
            {sort.sorted.map((c, i) => (
              <tr key={i}>
                <td className="td font-mono text-xs text-muted">{fmtTs(c.timestamp)}</td>
                <td className="td font-mono">{c.number}</td>
                <td className="td">{c.name || "—"}</td>
                <td className="td">
                  <span className={typeColor(c.call_type)}>{c.call_type}</span>
                </td>
                <td className="td font-mono text-xs">{c.duration_s != null ? `${c.duration_s}s` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function typeColor(t: string) {
  if (t === "missed" || t === "rejected") return "text-deletion";
  if (t === "outgoing") return "text-recovered";
  if (t === "incoming") return "text-live";
  return "text-muted";
}
