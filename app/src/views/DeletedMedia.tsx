import { useEffect, useState } from "react";
import { AlertTriangle } from "lucide-react";
import { api } from "../lib/api";
import type { MediaStoreTrash, MediaStoreTrashItem } from "../lib/types";
import { SectionHeader, EmptyState } from "../components/common";
import { ConfidenceBadge } from "../components/Badges";

const fmtBytes = (n: number) =>
  n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1048576).toFixed(1)} MB`;

/**
 * Deleted / trashed media recovered from the MediaStore trash — the highest-yield
 * NON-ROOT deleted-media technique on Android 11+. Two evidence classes, never mixed:
 * files we hold (RECOVERED_VERIFIED) and deletions we can only prove (DELETION_DETECTED).
 */
export function DeletedMediaView({ caseId }: { caseId: string }) {
  const [data, setData] = useState<MediaStoreTrash | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let live = true;
    api
      .dataset<MediaStoreTrash>(caseId, "mediastore_trash")
      .then((d) => live && setData(d && Array.isArray(d.items) ? d : { items: [], summary: emptySummary() }))
      .catch(() => live && setData({ items: [], summary: emptySummary() }))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [caseId]);

  if (loading) return <div className="p-8 text-muted">Loading deleted media…</div>;

  const items = data?.items ?? [];
  const s = data?.summary;

  return (
    <div className="p-6 h-full overflow-auto">
      <SectionHeader
        title="Deleted &amp; Trashed Media"
        sub="MediaStore trash — non-root recovery on Android 11+. Deleted media stays intact for ~30 days; each item's deletion time is derived from its auto-purge date. Verify every item."
      />

      {s && s.total > 0 && (
        <div className="flex flex-wrap gap-2 mb-4">
          <Pill n={s.total} label="items" cls="text-ink border-line bg-panel" />
          <Pill n={s.file_recovered} label="files recovered" cls="text-live border-live/40 bg-live/10" />
          <Pill
            n={s.deletion_detected_only}
            label="deletion-detected"
            cls="text-critical border-critical/40 bg-critical/10"
          />
          {s.recovered_bytes > 0 && (
            <Pill n={0} label={fmtBytes(s.recovered_bytes)} cls="text-muted border-line bg-panel" hideN />
          )}
          {s.expiring_within_3_days > 0 && (
            <Pill
              n={s.expiring_within_3_days}
              label="auto-purge < 3 days"
              cls="text-warn border-warn/50 bg-warn/10"
            />
          )}
        </div>
      )}

      {s && s.expiring_within_3_days > 0 && (
        <div className="rounded-md border border-warn/50 bg-warn/5 p-3 mb-4 text-xs text-warn">
          <span className="inline-flex items-center gap-1">
            <AlertTriangle className="inline h-3.5 w-3.5" strokeWidth={1.75} aria-hidden />
            {s.expiring_within_3_days} recovered item(s) will be auto-purged by Android within 3 days.
          </span>{" "}
          Preserve the exported evidence now — once purged, the content is unrecoverable.
        </div>
      )}

      {items.length === 0 ? (
        <EmptyState
          dataset="mediastore_trash"
        title="No trashed or pending media"
          detail="Nothing was found in the MediaStore trash. On non-rooted devices this is the primary deleted-media source; on a full-file-system extraction, app recycle bins may hold more."
        />
      ) : (
        <div className="space-y-2">
          {items.map((it, i) => (
            <TrashCard key={`${it.original_name}-${i}`} it={it} />
          ))}
          <p className="text-[11px] text-muted mt-3 leading-relaxed">
            Deletion times are estimates: Android's default 30-day retention subtracted from each item's
            auto-purge date. Some OEMs change the window — the exact expiry is shown per item. All items
            require examiner verification against the source artifact.
          </p>
        </div>
      )}
    </div>
  );
}

function TrashCard({ it }: { it: MediaStoreTrashItem }) {
  return (
    <div className="card p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2 flex-wrap">
          <ConfidenceBadge c={it.confidence} />
          <span className="text-sm font-medium text-ink">{it.original_name}</span>
          <span className="text-[10px] uppercase rounded border border-line px-1 text-muted">
            {it.state}
          </span>
          {it.file_recoverable ? (
            <span className="text-[10px] rounded border border-live/40 text-live px-1">file held</span>
          ) : (
            <span className="text-[10px] rounded border border-critical/40 text-critical px-1">
              content not recovered
            </span>
          )}
        </div>
        <span className="text-[11px] text-muted shrink-0">{fmtBytes(it.size_bytes)}</span>
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted mt-1.5">
        <span>{it.kind}</span>
        {it.owner_app && <span>app: {it.owner_app}</span>}
        {it.estimated_deleted_at && (
          <span className="text-warn">deleted ~{it.estimated_deleted_at.slice(0, 10)}</span>
        )}
        {it.expires_at && <span>expires {it.expires_at.slice(0, 10)}</span>}
        {it.days_until_auto_purge !== null && (
          <span className={it.days_until_auto_purge <= 3 ? "text-warn" : ""}>
            purge in {it.days_until_auto_purge}d
          </span>
        )}
        <span className="uppercase tracking-wide">src: {it.source}</span>
      </div>
      <div className="text-[11px] text-muted italic mt-1">{it.note}</div>
    </div>
  );
}

function Pill({
  n,
  label,
  cls,
  hideN,
}: {
  n: number;
  label: string;
  cls: string;
  hideN?: boolean;
}) {
  return (
    <span className={`rounded border px-2 py-0.5 text-xs font-semibold ${cls}`}>
      {hideN ? label : `${n} ${label}`}
    </span>
  );
}

function emptySummary(): MediaStoreTrash["summary"] {
  return {
    total: 0,
    trashed: 0,
    pending: 0,
    file_recovered: 0,
    deletion_detected_only: 0,
    recovered_bytes: 0,
    expiring_within_3_days: 0,
    note: "",
  };
}
