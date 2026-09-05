import { useMemo, useState } from "react";
import type { Account } from "../lib/types";
import { useDataset } from "../lib/hooks";
import { Filters, SectionHeader, EmptyState } from "../components/common";

export function AccountsView({ caseId }: { caseId: string }) {
  const { data, loading } = useDataset<Account>(caseId, "accounts");
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    const q = query.toLowerCase();
    return data.filter(
      (a) => !q || a.name.toLowerCase().includes(q) || a.type.toLowerCase().includes(q) || (a.app || "").toLowerCase().includes(q)
    );
  }, [data, query]);

  if (loading) return <div className="p-8 text-muted">Loading accounts…</div>;
  if (data.length === 0)
    return (
      <EmptyState
        dataset="accounts"
        title="No accounts acquired"
        detail="Device accounts require the Tier-1 Collector helper's full collection (GET_ACCOUNTS). On Android 8+, visibility is authenticator-dependent, so some accounts may not be listed."
      />
    );

  return (
    <div className="p-6 h-full flex flex-col">
      <SectionHeader title="Accounts" sub={`${data.length} device account(s) — proves which app identities exist`} />
      <Filters query={query} onQuery={setQuery} placeholder="Search account, type, or app…" />
      <div className="card overflow-auto flex-1">
        <table className="w-full text-sm">
          <thead>
            <tr>
              <th className="th">Account name</th>
              <th className="th">App</th>
              <th className="th">Authenticator type</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((a, i) => (
              <tr key={i}>
                <td className="td font-medium">{a.name}</td>
                <td className="td">{a.app ? <span className="text-accent">{a.app}</span> : <span className="text-muted">—</span>}</td>
                <td className="td font-mono text-xs text-muted">{a.type}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
