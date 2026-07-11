import { useState } from "react";
import type { CallRecord } from "../lib/types";
import { useDataset, fmtTs } from "../lib/hooks";
import { SectionHeader } from "../components/common";

export function CallsView({ caseId }: { caseId: string }) {
  const { data, loading } = useDataset<CallRecord>(caseId, "calls");
  const [query, setQuery] = useState("");

  if (loading) return <div className="p-8 text-muted">Loading calls…</div>;

  // Honest empty state: the call log is one of the most intrusive artifacts to reach
  // without root (default-Dialer role swap). If we didn't get it, say exactly why —
  // a better demo beat than a missing tab, and it reinforces the honesty pitch.
  if (data.length === 0) {
    return (
      <div className="p-6">
        <SectionHeader title="Calls" />
        <div className="card p-6 max-w-2xl">
          <div className="text-warn font-semibold mb-2">Not acquired at this tier</div>
          <p className="text-sm text-muted leading-relaxed">
            The call log is gated by <code className="text-ink">READ_CALL_LOG</code>, a
            <em> hard-restricted</em> Android permission. Reaching it without root requires
            temporarily reassigning the device's default Dialer role to the Collector helper —
            a more invasive, state-changing step than this Tier-0/Tier-1 acquisition performed.
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

  const filtered = data.filter(
    (c) => !query || c.number.includes(query) || c.name.toLowerCase().includes(query.toLowerCase()),
  );

  return (
    <div className="p-6 h-full flex flex-col">
      <SectionHeader title="Calls" sub={`${data.length} call records`} />
      <input className="input max-w-xs mb-3" placeholder="Search number or name…" value={query} onChange={(e) => setQuery(e.target.value)} />
      <div className="card overflow-auto flex-1">
        <table className="w-full text-sm">
          <thead>
            <tr>
              <th className="th w-40">Time</th>
              <th className="th">Number</th>
              <th className="th">Name</th>
              <th className="th w-28">Type</th>
              <th className="th w-24">Duration</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((c, i) => (
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
