import type { ReactNode } from "react";

export function StatCard({ n, label, tone }: { n: ReactNode; label: string; tone?: string }) {
  return (
    <div className="card p-3 text-center">
      <div className={`text-2xl font-bold ${tone ?? "text-ink"}`}>{n}</div>
      <div className="text-[11px] uppercase tracking-wider text-muted mt-0.5">{label}</div>
    </div>
  );
}

export function EmptyState({ title, detail }: { title: string; detail?: string }) {
  return (
    <div className="flex flex-col items-center justify-center h-full text-center py-16 px-6">
      <div className="text-muted text-sm max-w-md">
        <div className="text-ink font-medium mb-1">{title}</div>
        {detail && <p className="leading-relaxed">{detail}</p>}
      </div>
    </div>
  );
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
