import { useMemo, useState } from "react";
import type { Contact } from "../lib/types";
import { useDataset } from "../lib/hooks";
import { Filters, SectionHeader, EmptyState } from "../components/common";

export function ContactsView({ caseId }: { caseId: string }) {
  const { data, loading } = useDataset<Contact>(caseId, "contacts");
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    const q = query.toLowerCase();
    return data.filter((c) => !q || c.name.toLowerCase().includes(q) || c.number.includes(q));
  }, [data, query]);

  if (loading) return <div className="p-8 text-muted">Loading contacts…</div>;
  if (data.length === 0)
    return (
      <EmptyState
        title="No contacts acquired"
        detail="Contacts require the Tier-1 Collector helper (a single READ_CONTACTS grant). This device was acquired at Tier 0 only, or the helper was not run."
      />
    );

  return (
    <div className="p-6 h-full flex flex-col">
      <SectionHeader title="Contacts" sub={`${data.length} contacts (Tier 1 · helper APK)`} />
      <Filters query={query} onQuery={setQuery} placeholder="Search name or number…" />
      <div className="card overflow-auto flex-1">
        <table className="w-full text-sm">
          <thead>
            <tr>
              <th className="th">Name</th>
              <th className="th">Number</th>
              <th className="th">Email</th>
              <th className="th w-40">Source</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((c, i) => (
              <tr key={i}>
                <td className="td font-medium">{c.name}</td>
                <td className="td font-mono">{c.number || "—"}</td>
                <td className="td text-muted">{c.email || "—"}</td>
                <td className="td text-xs text-muted font-mono">{c.source_file}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
