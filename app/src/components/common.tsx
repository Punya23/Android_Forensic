import { useMemo, useState, type ReactNode } from "react";
import { DatasetEmpty } from "../lib/capabilities";

/**
 * A stat tile. Pass `onClick` to make it a filter toggle (e.g. "click 'critical' to
 * show only critical rows in the table below") — `active` rings it when that filter is
 * currently applied. With no `onClick` it renders exactly as before: a plain number.
 * Found (2026-09) to have no click-through in any of its ~13 call sites — every view
 * could only ever *display* an aggregate, never drill into the rows behind it.
 */
export function StatCard({
  n,
  label,
  tone,
  onClick,
  active,
}: {
  n: ReactNode;
  label: string;
  tone?: string;
  onClick?: () => void;
  active?: boolean;
}) {
  const clickable = !!onClick;
  return (
    <div
      className={`card p-3 text-center ${clickable ? "cursor-pointer transition-colors hover:bg-panel-2" : ""} ${
        active ? "ring-1 ring-accent border-accent/50" : ""
      }`}
      onClick={onClick}
      role={clickable ? "button" : undefined}
      tabIndex={clickable ? 0 : undefined}
      onKeyDown={clickable ? (e) => (e.key === "Enter" || e.key === " ") && onClick!() : undefined}
    >
      <div className={`text-2xl font-bold ${tone ?? "text-ink"}`}>{n}</div>
      <div className="text-[11px] uppercase tracking-wider text-muted mt-0.5">{label}</div>
    </div>
  );
}

// --- Sortable tables ---------------------------------------------------------
// Nothing in the dashboard had a click-to-sort column header — every table rendered
// in one hardcoded order. One generic hook + header cell, reused across every table
// view rather than each reimplementing its own sort state.

export type SortDir = "asc" | "desc";

/**
 * Click-to-sort state for a row array. `sortBy(key, getValue)` toggles direction on a
 * repeat click of the same column, defaults to descending on a new one (so "sort by
 * time" or "sort by size" lands on the more useful newest/largest-first order without
 * an extra click). Stable for equal/missing values — a row with no value for the sort
 * key sinks to the end rather than jumping unpredictably.
 */
export function useSort<T>(rows: T[]) {
  const [state, setState] = useState<{ key: string; get: (r: T) => unknown; dir: SortDir } | null>(
    null
  );

  function sortBy(key: string, get: (r: T) => unknown) {
    setState((prev) =>
      prev?.key === key ? { key, get, dir: prev.dir === "asc" ? "desc" : "asc" } : { key, get, dir: "desc" }
    );
  }

  const sorted = useMemo(() => {
    if (!state) return rows;
    const { get, dir } = state;
    const withIdx = rows.map((r, i) => [r, i] as const);
    withIdx.sort(([a, ai], [b, bi]) => {
      const av = get(a);
      const bv = get(b);
      const aNull = av === null || av === undefined || av === "";
      const bNull = bv === null || bv === undefined || bv === "";
      if (aNull && bNull) return ai - bi; // stable
      if (aNull) return 1; // missing values always sink to the end
      if (bNull) return -1;
      let cmp: number;
      if (typeof av === "string" && typeof bv === "string") {
        cmp = av.localeCompare(bv);
      } else {
        const an = av as number;
        const bn = bv as number;
        cmp = an < bn ? -1 : an > bn ? 1 : 0;
      }
      return dir === "asc" ? cmp : -cmp;
    });
    return withIdx.map(([r]) => r);
  }, [rows, state]);

  return { sorted, sortKey: state?.key ?? null, sortDir: state?.dir ?? ("desc" as SortDir), sortBy };
}

/** A `<th>` that sorts its column on click, via the `useSort` state passed in as `sort`. */
export function SortTh<T>({
  label,
  sortKeyName,
  getValue,
  sort,
  className,
}: {
  label: ReactNode;
  sortKeyName: string;
  getValue: (row: T) => unknown;
  sort: ReturnType<typeof useSort<T>>;
  className?: string;
}) {
  const active = sort.sortKey === sortKeyName;
  return (
    <th
      className={`select-none cursor-pointer hover:text-ink ${className ?? ""}`}
      onClick={() => sort.sortBy(sortKeyName, getValue)}
    >
      <span className="inline-flex items-center gap-1">
        {label}
        <span className={`text-[9px] ${active ? "text-accent" : "text-muted/40"}`}>
          {active ? (sort.sortDir === "asc" ? "▲" : "▼") : "▾"}
        </span>
      </span>
    </th>
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
