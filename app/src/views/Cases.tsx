import { useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import { bytes, EmptyState, SectionHeader, StatCard } from "../components/common";
import { fmtTs } from "../lib/hooks";
import type { RegistryCase, RegistryStats, ReportVersion } from "../lib/types";

export function CasesView({ onOpenCase }: { onOpenCase: (id: string) => void }) {
  const [cases, setCases] = useState<RegistryCase[]>([]);
  const [stats, setStats] = useState<RegistryStats | null>(null);
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState("-updated_at");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const res = await api.registryCases({ q: query.trim() || undefined, sort });
      setCases(res.cases);
      setStats(res.stats);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sort]);

  useEffect(() => {
    const t = window.setTimeout(load, 250);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);

  async function handleDelete(c: RegistryCase) {
    const ok = window.confirm(
      `Permanently delete case ${c.case_id}?\n\nThis removes the case folder — every ` +
        `artifact, report and audit record — from disk. This cannot be undone.`
    );
    if (!ok) return;
    setBusy(c.case_id);
    try {
      await api.deleteCase(c.case_id);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <SectionHeader
        title="Case History"
        sub="Every case ever acquired on this installation, indexed like a database — search, reopen, or pull up any past report."
      />

      {stats && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-5">
          <StatCard n={stats.cases} label="Cases" />
          <StatCard n={stats.artifacts} label="Artifacts" />
          <StatCard n={bytes(stats.bytes)} label="Total Evidence" />
          <StatCard n={stats.reports} label="Reports Generated" />
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2 mb-3">
        <input
          className="input max-w-xs"
          placeholder="Search case ID, examiner, device, crime type…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <select className="input w-auto" value={sort} onChange={(e) => setSort(e.target.value)}>
          <option value="-updated_at">Recently updated</option>
          <option value="-created_at">Newest first</option>
          <option value="created_at">Oldest first</option>
          <option value="examiner">Examiner (A–Z)</option>
          <option value="-artifact_count">Most artifacts</option>
          <option value="-report_count">Most reports</option>
        </select>
      </div>

      {error && (
        <div className="card border-deletion/50 bg-deletion/10 p-3 mb-4 text-sm text-deletion">
          {error}
        </div>
      )}

      {loading && cases.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center">Loading case history…</div>
      ) : cases.length === 0 ? (
        <EmptyState
          title="No cases yet"
          detail="Cases you acquire will be indexed here permanently — searchable and reopenable, with every report you've ever generated kept in its history."
        />
      ) : (
        <div className="card overflow-x-auto">
          <table>
            <thead>
              <tr>
                <th className="th">Case</th>
                <th className="th">Examiner</th>
                <th className="th">Device</th>
                <th className="th">Crime type</th>
                <th className="th">Created</th>
                <th className="th">Artifacts</th>
                <th className="th">Size</th>
                <th className="th">Reports</th>
                <th className="th">Actions</th>
              </tr>
            </thead>
            <tbody>
              {cases.map((c) => (
                <CaseRow
                  key={c.case_id}
                  c={c}
                  expanded={expanded === c.case_id}
                  onToggleExpand={() => setExpanded(expanded === c.case_id ? null : c.case_id)}
                  onOpen={() => onOpenCase(c.case_id)}
                  onDelete={() => handleDelete(c)}
                  busy={busy === c.case_id}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function CaseRow({
  c,
  expanded,
  onToggleExpand,
  onOpen,
  onDelete,
  busy,
}: {
  c: RegistryCase;
  expanded: boolean;
  onToggleExpand: () => void;
  onOpen: () => void;
  onDelete: () => void;
  busy: boolean;
}) {
  return (
    <>
      <tr className="hover:bg-panel/50">
        <td className="td font-mono text-accent">{c.case_id}</td>
        <td className="td">{c.examiner || "—"}</td>
        <td className="td">{c.device_model || "—"}</td>
        <td className="td">
          {c.crime_type ? (
            <span className="text-xs rounded bg-panel px-1.5 py-0.5 border border-line">
              {c.crime_type}
            </span>
          ) : (
            <span className="text-muted">—</span>
          )}
        </td>
        <td className="td whitespace-nowrap">{fmtTs(c.created_at)}</td>
        <td className="td tabular-nums">{c.artifact_count}</td>
        <td className="td tabular-nums">{bytes(c.total_bytes)}</td>
        <td className="td">
          {c.report_count > 0 ? (
            <button className="text-accent hover:underline text-sm" onClick={onToggleExpand}>
              {c.report_count} {c.report_count === 1 ? "version" : "versions"} {expanded ? "▴" : "▾"}
            </button>
          ) : (
            <span className="text-muted text-sm">none yet</span>
          )}
        </td>
        <td className="td whitespace-nowrap">
          <div className="flex gap-2">
            <button className="btn-ghost text-xs px-2 py-1" onClick={onOpen}>
              Open
            </button>
            <button
              className="btn-ghost text-xs px-2 py-1 text-deletion border-deletion/40 hover:bg-deletion/10"
              disabled={busy}
              onClick={onDelete}
            >
              {busy ? "Deleting…" : "Delete"}
            </button>
          </div>
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={9} className="td bg-panel/40">
            <ReportHistory caseId={c.case_id} />
          </td>
        </tr>
      )}
    </>
  );
}

function ReportHistory({ caseId }: { caseId: string }) {
  const [reports, setReports] = useState<ReportVersion[] | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .caseReports(caseId)
      .then((r) => alive && setReports(r))
      .catch(() => alive && setReports([]));
    return () => {
      alive = false;
    };
  }, [caseId]);

  const sorted = useMemo(() => reports ?? [], [reports]);

  if (reports === null) {
    return <div className="text-sm text-muted py-2">Loading report history…</div>;
  }

  return (
    <div className="py-2">
      <div className="text-[11px] uppercase tracking-wider text-muted mb-2">
        Report history — every generation, never overwritten
      </div>
      <div className="space-y-1">
        {sorted.map((r) => (
          <div
            key={r.id}
            className="flex items-center justify-between text-sm border-t border-line/50 pt-1.5 first:border-t-0 first:pt-0"
          >
            <div className="flex items-center gap-3">
              <span className="font-mono text-xs text-muted">{fmtTs(r.generated_at)}</span>
              <span className="text-xs rounded bg-panel px-1.5 py-0.5 border border-line">
                {r.trigger}
              </span>
              <span className="text-xs text-muted">{bytes(r.size_bytes)}</span>
            </div>
            <a
              className="text-accent hover:underline text-xs"
              href={api.reportSnapshotUrl(caseId, r.path)}
              target="_blank"
              rel="noreferrer"
            >
              Open →
            </a>
          </div>
        ))}
      </div>
    </div>
  );
}
