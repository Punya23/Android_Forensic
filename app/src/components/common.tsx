import type { ReactNode } from "react";
import { DatasetEmpty } from "../lib/capabilities";

export function StatCard({ n, label, tone }: { n: ReactNode; label: string; tone?: string }) {
  return (
    <div className="card p-3 text-center">
      <div className={`text-2xl font-bold ${tone ?? "text-ink"}`}>{n}</div>
      <div className="text-[11px] uppercase tracking-wider text-muted mt-0.5">{label}</div>
    </div>
  );
}

/**
 * Empty state for a view. Pass `dataset` — the name the view fetches — and the engine's
 * own account of *why* it is empty replaces the generic text: checked and empty, gated
 * off by an acquisition flag, unreachable without root, or not built yet. `title` and
 * `detail` remain the fallback for views with no single backing dataset.
 */
export function EmptyState({
  title,
  detail,
  dataset,
}: {
  title: string;
  detail?: string;
  dataset?: string;
}) {
  return <DatasetEmpty dataset={dataset} title={title} detail={detail} />;
}

export function SectionHeader({ title, sub, right }: { title: string; sub?: string; right?: ReactNode }) {
  return (
    <div className="flex items-start justify-between mb-4">
      <div>
        <h2 className="text-lg font-semibold text-ink">{title}</h2>
        {sub && <p className="text-sm text-muted mt-0.5">{sub}</p>}
      </div>
      {right}
    </div>
  );
}

export function bytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

export function Filters({
  query,
  onQuery,
  from,
  to,
  onFrom,
  onTo,
  placeholder,
}: {
  query: string;
  onQuery: (v: string) => void;
  from?: string;
  to?: string;
  onFrom?: (v: string) => void;
  onTo?: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 mb-3">
      <input
        className="input max-w-xs"
        placeholder={placeholder ?? "Keyword filter…"}
        value={query}
        onChange={(e) => onQuery(e.target.value)}
      />
      {onFrom && (
        <>
          <span className="text-xs text-muted">from</span>
          <input type="date" className="input w-auto" value={from} onChange={(e) => onFrom(e.target.value)} />
          <span className="text-xs text-muted">to</span>
          <input type="date" className="input w-auto" value={to} onChange={(e) => onTo?.(e.target.value)} />
        </>
      )}
    </div>
  );
}
