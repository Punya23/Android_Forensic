import { useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import type { Confidence, RecoveredRow } from "../lib/types";
import { useDataset } from "../lib/hooks";
import { ConfidenceBadge } from "../components/Badges";
import { TagButton } from "../lib/tagStore";
import { Filters, SectionHeader, EmptyState } from "../components/common";

/** A structural deletion finding: proof that records were deleted, with no content. */
export interface DeletionEvidenceRow {
  db_file?: string;
  device_path?: string;
  table?: string;
  mechanism?: string;
  first_missing_rowid?: number | null;
  last_missing_rowid?: number | null;
  missing_count?: number;
  confidence?: string;
  description?: string;
  false_positive_causes?: string[];
  provenance?: string;
  caveats?: string[];
}

/** Aggregate roll-up of deletion_evidence, from deletion_evidence_summary(). */
export interface DeletionEvidenceSummary {
  confidence?: string;
  total_findings?: number;
  by_mechanism?: Record<string, number>;
  by_table?: Record<string, number>;
  tables_affected?: string[];
  total_missing_rowids?: number;
  skipped_tables?: string[];
  recovers_content?: boolean;
  summary?: string;
  caveats?: string[];
}

/**
 * Deletion findings, deliberately rendered ABOVE and APART from carved content.
 *
 * These two answer different questions and merging them loses the stronger one:
 * carved rows say "here is some of what was deleted", while this says "records were
 * deleted here and their content is gone". The second is often the finding that
 * matters, and it disappears if it is folded into a row count.
 */
function DeletionEvidencePanel({ caseId }: { caseId: string }) {
  const { data, loading } = useDataset<DeletionEvidenceRow>(caseId, "deletion_evidence");
  const [summary, setSummary] = useState<DeletionEvidenceSummary | null>(null);
  const [open, setOpen] = useState(true);

  useEffect(() => {
    let alive = true;
    api
      .dataset<DeletionEvidenceSummary>(caseId, "deletion_evidence_summary")
      .then((s) => alive && setSummary(s || null))
      .catch(() => alive && setSummary(null));
    return () => {
      alive = false;
    };
  }, [caseId]);

  if (loading) return null;

  // Zero carved AND zero structural findings does not mean nothing was deleted — a
  // rebuilt/vacuumed/WITHOUT ROWID table leaves no rowid signature at all. Say that
  // honestly instead of rendering nothing, which reads as "clean".
  if (data.length === 0) {
    if (!summary || !summary.summary) return null;
    return (
      <div className="card border-line p-3 mb-3 text-xs text-muted leading-relaxed">
        {summary.summary}
      </div>
    );
  }

  const causes = Array.from(
    new Set(data.flatMap((d) => d.false_positive_causes ?? [])),
  );

  return (
    <div className="card border-deletion/40 bg-deletion/5 p-3 mb-3">
      <button
        className="w-full flex items-center justify-between text-left"
        onClick={() => setOpen((o) => !o)}
      >
        <span className="text-sm font-semibold text-deletion">
          Deletion detected — {data.length} structural finding
          {data.length === 1 ? "" : "s"} (no content recovered)
        </span>
        <span className="text-xs text-muted">{open ? "hide" : "show"}</span>
      </button>
      {open && (
        <>
          <p className="text-xs text-muted mt-2 leading-relaxed">
            These establish, from the databases' own structure, that records{" "}
            <strong>were deleted</strong>. They recover no text. That is a distinct and
            often stronger finding than a carved fragment — and it is reported separately
            so it is never mistaken for recovered content.
          </p>
          {summary && (summary.by_mechanism || summary.tables_affected) && (
            <>
              <div className="flex flex-wrap gap-2 mt-2 text-[11px]">
                {summary.total_missing_rowids != null && (
                  <span
                    className="px-1.5 py-0.5 rounded bg-panel border border-line text-muted"
                    title="Summed across every mechanism below. The same missing rowid can be counted by more than one mechanism, so this is NOT a count of distinct deleted rows."
                  >
                    {summary.total_missing_rowids} missing-rowid mention(s) (mechanisms overlap — not a distinct-row count)
                  </span>
                )}
                {Object.entries(summary.by_mechanism ?? {}).map(([mech, n]) => (
                  <span key={mech} className="px-1.5 py-0.5 rounded bg-panel border border-line text-muted">
                    {mech}: {n}
                  </span>
                ))}
              </div>
              {summary.caveats && summary.caveats.length > 0 && (
                <ul className="mt-2 space-y-0.5">
                  {summary.caveats.map((c, i) => (
                    <li key={i} className="text-[11px] text-warn leading-relaxed">
                      ⚠ {c}
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
          <div className="overflow-auto mt-3">
            <table className="w-full text-xs border-collapse">
              <thead>
                <tr className="border-b border-line text-muted uppercase tracking-wider">
                  <th className="text-left py-1.5 pr-3 font-semibold">Database</th>
                  <th className="text-left py-1.5 pr-3 font-semibold">Table</th>
                  <th className="text-left py-1.5 pr-3 font-semibold">Mechanism</th>
                  <th className="text-left py-1.5 pr-3 font-semibold">Missing</th>
                  <th className="text-left py-1.5 font-semibold">What it means</th>
                </tr>
              </thead>
              <tbody>
                {data.map((d, i) => (
                  <tr key={i} className="border-b border-line/50 align-top">
                    <td className="py-1.5 pr-3 font-mono break-all">
                      {d.device_path ?? d.db_file ?? "—"}
                    </td>
                    <td className="py-1.5 pr-3 font-mono">{d.table ?? "—"}</td>
                    <td className="py-1.5 pr-3">{d.mechanism ?? "—"}</td>
                    <td className="py-1.5 pr-3 font-mono">
                      {d.missing_count ?? "—"}
                      {d.first_missing_rowid != null && (
                        <span className="text-muted">
                          {" "}
                          ({d.first_missing_rowid}–{d.last_missing_rowid})
                        </span>
                      )}
                    </td>
                    <td className="py-1.5 text-muted">{d.description ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {causes.length > 0 && (
            <div className="mt-3 text-xs text-muted leading-relaxed">
              <span className="font-semibold text-warn">
                Innocent explanations that produce the same signal
              </span>{" "}
              and must be excluded before relying on any row above:
              <ul className="list-disc ml-5 mt-1 space-y-0.5">
                {causes.map((c, i) => (
                  <li key={i}>{c}</li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </div>
  );
}

/** One gap in a table's rowid sequence — raw DFIR gap analysis, engine's `rowid_gaps`. */
interface RowidGap {
  after_rowid: number;
  before_rowid: number;
  missing: number;
}

/**
 * Raw rowid-gap analysis across EVERY table of EVERY pulled SQLite database — not just
 * the messaging-app schemas `deletion_evidence` recognises above. A gap here with no
 * matching row above is still a real structural signal (e.g. a contacts or call-log
 * table with a hole in it), so it is surfaced separately rather than silently dropped.
 */
function RowidGapsPanel({ caseId }: { caseId: string }) {
  const [gaps, setGaps] = useState<Record<string, RowidGap[]> | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    api
      .dataset<Record<string, RowidGap[]>>(caseId, "rowid_gaps")
      .then((d) => alive && setGaps(d && typeof d === "object" ? d : null))
      .catch(() => alive && setGaps(null));
    return () => {
      alive = false;
    };
  }, [caseId]);

  const entries = Object.entries(gaps ?? {});
  if (entries.length === 0) return null;
  const totalGaps = entries.reduce((n, [, g]) => n + g.length, 0);
  const totalMissing = entries.reduce((n, [, g]) => n + g.reduce((m, x) => m + x.missing, 0), 0);

  return (
    <div className="card border-line p-3 mb-3">
      <button
        className="w-full flex items-center justify-between text-left"
        onClick={() => setOpen((o) => !o)}
      >
        <span className="text-sm font-semibold text-ink">
          Rowid gaps (raw) — {totalGaps} gap{totalGaps === 1 ? "" : "s"} across{" "}
          {entries.length} table{entries.length === 1 ? "" : "s"}, {totalMissing} rowid(s) missing
        </span>
        <span className="text-xs text-muted">{open ? "hide" : "show"}</span>
      </button>
      {open && (
        <>
          <p className="text-xs text-muted mt-2 leading-relaxed">
            A blind pass over every table's rowid sequence, independent of the app-specific
            deletion analysis above. A gap proves rows once existed at those rowids and are
            gone from this table now — it does not by itself say why (deletion, a rebuilt/
            vacuumed table, or `AUTOINCREMENT` skipping values are all possible).
          </p>
          <div className="overflow-auto mt-3">
            <table className="w-full text-xs border-collapse">
              <thead>
                <tr className="border-b border-line text-muted uppercase tracking-wider">
                  <th className="text-left py-1.5 pr-3 font-semibold">Database :: table</th>
                  <th className="text-left py-1.5 pr-3 font-semibold">Gaps</th>
                  <th className="text-left py-1.5 font-semibold">Rowid ranges missing</th>
                </tr>
              </thead>
              <tbody>
                {entries.map(([key, g]) => (
                  <tr key={key} className="border-b border-line/50 align-top">
                    <td className="py-1.5 pr-3 font-mono break-all">{key}</td>
                    <td className="py-1.5 pr-3 font-mono">{g.length}</td>
                    <td className="py-1.5 text-muted font-mono">
                      {g
                        .slice(0, 12)
                        .map((x) => `${x.after_rowid}–${x.before_rowid} (${x.missing})`)
                        .join(", ")}
                      {g.length > 12 && ` … +${g.length - 12} more`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

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
    return (
      // Zero carved rows does NOT mean nothing was deleted — the structural findings
      // still render here, and this is the case where they matter most.
      <div className="p-6">
        <SectionHeader
          title="Recovered / Deleted Data"
          sub="Carved from SQLite freelist, freeblocks, unallocated space & WAL"
        />
        <DeletionEvidencePanel caseId={caseId} />
        <RowidGapsPanel caseId={caseId} />
        <EmptyState
          dataset="recovered"
        title="No deleted content recovered"
          detail="No freelist / freeblock / WAL remnants were carved from the acquired databases. That is a statement about content recovery only — see any structural deletion findings above."
        />
      </div>
    );

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
      <DeletionEvidencePanel caseId={caseId} />
      <RowidGapsPanel caseId={caseId} />
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
