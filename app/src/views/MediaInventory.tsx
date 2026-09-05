/**
 * MediaInventory — the MediaStore catalogue (metadata-only, non-root Tier-1).
 *
 * A media-triage surface: filter by kind, owning app, and the trashed / favorite / has-GPS
 * flags Android exposes — so an examiner can spot recently-deleted or geotagged media without
 * pulling gigabytes. This complements the pulled-file Media gallery.
 */
import { useMemo, useState } from "react";
import { Star } from "lucide-react";
import type { MediaInventoryItem } from "../lib/types";
import { useDataset, fmtTs } from "../lib/hooks";
import { SectionHeader, EmptyState, StatCard, Filters, bytes, SortTh, useSort } from "../components/common";

type Flag = "all" | "trashed" | "favorite" | "gps";

export function MediaInventoryView({ caseId }: { caseId: string }) {
  const { data, loading } = useDataset<MediaInventoryItem>(caseId, "media_inventory");
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<string>("all");
  const [flag, setFlag] = useState<Flag>("all");

  const kinds = useMemo(() => Array.from(new Set(data.map((m) => m.kind))).sort(), [data]);

  const filtered = useMemo(() => {
    const q = query.toLowerCase();
    return data
      .filter((m) => kind === "all" || m.kind === kind)
      .filter((m) =>
        flag === "all"
          ? true
          : flag === "trashed"
            ? m.is_trashed
            : flag === "favorite"
              ? m.is_favorite
              : !!m.gps
      )
      .filter(
        (m) =>
          !q ||
          m.display_name.toLowerCase().includes(q) ||
          (m.owner_app || "").toLowerCase().includes(q) ||
          (m.relative_path || "").toLowerCase().includes(q)
      )
      .sort((a, b) => (b.date_taken || b.date_added || "").localeCompare(a.date_taken || a.date_added || ""));
  }, [data, query, kind, flag]);
  // Hooks must run unconditionally on every render — sort is computed here, on the
  // already-filtered rows, before either early return below (see Calls.tsx precedent).
  const sort = useSort<MediaInventoryItem>(filtered);

  if (loading) return <div className="p-8 text-muted">Loading media inventory…</div>;
  if (data.length === 0)
    return (
      <EmptyState dataset="media_inventory"
        title="No media inventory acquired"
        detail="The MediaStore catalogue requires the Tier-1 Collector helper's full collection (dump_all). It surfaces every media file's metadata — including trashed/favorite flags and owning app — without pulling the files. Pulled files still appear under Media."
      />
    );

  const trashed = data.filter((m) => m.is_trashed).length;
  const favorite = data.filter((m) => m.is_favorite).length;
  const withGps = data.filter((m) => m.gps).length;

  const chip = (v: Flag, label: string, tone?: string) => (
    <button
      onClick={() => setFlag(flag === v ? "all" : v)}
      className={`text-xs px-2.5 py-1 rounded border transition-colors ${
        flag === v ? "border-accent bg-accent/15 text-accent" : "border-line text-muted hover:border-muted"
      } ${tone ?? ""}`}
    >
      {label}
    </button>
  );

  return (
    <div className="p-6 h-full flex flex-col">
      <SectionHeader title="Media Inventory" sub={`${data.length} MediaStore entries (Tier 1 · metadata only)`} />

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-4">
        <StatCard n={data.length} label="Total media" />
        <StatCard n={trashed} label="Trashed" tone={trashed ? "text-deletion" : "text-ink"} />
        <StatCard n={favorite} label="Favorited" tone="text-recovered" />
        <StatCard n={withGps} label="Geotagged" tone="text-accent" />
      </div>

      <div className="flex flex-wrap items-center gap-2 mb-3">
        <Filters query={query} onQuery={setQuery} placeholder="Search name, app, or path…" />
        <select className="input w-auto" value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="all">All kinds</option>
          {kinds.map((k) => (
            <option key={k} value={k}>{k}</option>
          ))}
        </select>
        {chip("trashed", `Trashed (${trashed})`)}
        {chip("favorite", `Favorite (${favorite})`)}
        {chip("gps", `Geotagged (${withGps})`)}
      </div>

      <div className="card overflow-auto flex-1">
        <table className="w-full text-sm">
          <thead>
            <tr>
              <SortTh className="th" label="Name" sortKeyName="display_name" getValue={(m) => m.display_name} sort={sort} />
              <SortTh className="th w-20" label="Kind" sortKeyName="kind" getValue={(m) => m.kind} sort={sort} />
              <SortTh className="th w-28" label="Owner app" sortKeyName="owner_app" getValue={(m) => m.owner_app} sort={sort} />
              <SortTh className="th w-24" label="Size" sortKeyName="size_bytes" getValue={(m) => m.size_bytes} sort={sort} />
              <SortTh className="th w-40" label="Date taken" sortKeyName="date_taken" getValue={(m) => m.date_taken || m.date_added} sort={sort} />
              <SortTh
                className="th w-28"
                label="Flags"
                sortKeyName="flags"
                getValue={(m) => (m.is_trashed ? 4 : 0) + (m.is_favorite ? 2 : 0) + (m.gps ? 1 : 0)}
                sort={sort}
              />
            </tr>
          </thead>
          <tbody>
            {sort.sorted.slice(0, 2000).map((m, i) => (
              <tr key={i} className={m.is_trashed ? "bg-deletion/5" : ""}>
                <td className="td font-medium truncate max-w-xs" title={m.relative_path}>{m.display_name || "—"}</td>
                <td className="td text-xs">{m.kind}</td>
                <td className="td text-xs">{m.owner_app || <span className="text-muted">—</span>}</td>
                <td className="td text-xs font-mono">{bytes(m.size_bytes)}</td>
                <td className="td text-xs font-mono text-muted">{fmtTs(m.date_taken || m.date_added)}</td>
                <td className="td">
                  <div className="flex gap-1">
                    {m.is_trashed && <span className="text-[10px] px-1 rounded bg-deletion/20 text-deletion">trash</span>}
                    {m.is_favorite && (
                      <span className="text-[10px] px-1 rounded bg-recovered/20 text-recovered">
                        <Star className="inline h-3.5 w-3.5" strokeWidth={1.75} fill="currentColor" aria-hidden />
                      </span>
                    )}
                    {m.gps && <span className="text-[10px] px-1 rounded bg-accent/20 text-accent">gps</span>}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
