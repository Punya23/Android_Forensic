import { useMemo, useState } from "react";
import type { MediaItem } from "../lib/types";
import { useDataset } from "../lib/hooks";
import { api } from "../lib/api";
import { SectionHeader, EmptyState, bytes } from "../components/common";

export function MediaView({ caseId }: { caseId: string }) {
  const { data, loading } = useDataset<MediaItem>(caseId, "media");
  const [filter, setFilter] = useState<"all" | "image" | "video" | "trashed" | "gps">("all");
  const [selected, setSelected] = useState<MediaItem | null>(null);

  const filtered = useMemo(() => {
    return data.filter((m) => {
      if (filter === "all") return true;
      if (filter === "trashed") return m.trashed;
      if (filter === "gps") return !!m.gps;
      return m.kind === filter;
    });
  }, [data, filter]);

  if (loading) return <div className="p-8 text-muted">Loading media…</div>;
  if (data.length === 0) return <EmptyState title="No media pulled" />;

  const chips: { k: typeof filter; label: string; n: number }[] = [
    { k: "all", label: "All", n: data.length },
    { k: "image", label: "Images", n: data.filter((m) => m.kind === "image").length },
    { k: "video", label: "Videos", n: data.filter((m) => m.kind === "video").length },
    { k: "trashed", label: "Recovered from Trash", n: data.filter((m) => m.trashed).length },
    { k: "gps", label: "Geotagged", n: data.filter((m) => m.gps).length },
  ];

  return (
    <div className="p-6 h-full flex flex-col">
      <SectionHeader title="Media" sub={`${data.length} files`} />
      <div className="flex flex-wrap gap-2 mb-4">
        {chips.map((c) => (
          <button
            key={c.k}
            onClick={() => setFilter(c.k)}
            className={`px-3 py-1 rounded-full text-xs border transition-colors ${
              filter === c.k ? "border-accent bg-accent/15 text-accent" : "border-line text-muted hover:border-muted"
            }`}
          >
            {c.label} <span className="opacity-60">{c.n}</span>
          </button>
        ))}
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-3 overflow-auto flex-1 pb-4">
        {filtered.map((m) => (
          <button key={m.artifact_id} onClick={() => setSelected(m)} className="card overflow-hidden group text-left">
            <div className="aspect-square bg-panel flex items-center justify-center relative overflow-hidden">
              {m.kind === "image" ? (
                <img
                  src={api.mediaUrl(caseId, m.artifact_id)}
                  alt=""
                  className="w-full h-full object-cover group-hover:scale-105 transition-transform"
                  loading="lazy"
                />
              ) : (
                <span className="text-3xl opacity-40">{m.kind === "video" ? "🎬" : m.kind === "audio" ? "🎵" : "📄"}</span>
              )}
              {m.trashed && (
                <span className="absolute top-1 left-1 bg-deletion/90 text-white text-[9px] px-1 rounded">TRASH</span>
              )}
              {m.gps && (
                <span className="absolute top-1 right-1 bg-recovered/90 text-white text-[9px] px-1 rounded">GPS</span>
              )}
              {m.app && (
                <span className="absolute bottom-1 left-1 bg-black/70 text-[9px] px-1 rounded">{m.app}</span>
              )}
            </div>
            <div className="p-1.5">
              <div className="text-[10px] truncate text-muted font-mono">{m.stored_path.split("/").pop()}</div>
              <div className="text-[10px] text-muted/60">{bytes(m.size_bytes)}</div>
            </div>
          </button>
        ))}
      </div>

      {selected && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-8" onClick={() => setSelected(null)}>
          <div className="card max-w-2xl w-full p-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex justify-between items-start mb-3">
              <h3 className="font-mono text-sm">{selected.stored_path.split("/").pop()}</h3>
              <button className="text-muted hover:text-ink" onClick={() => setSelected(null)}>✕</button>
            </div>
            {selected.kind === "image" && (
              <img src={api.mediaUrl(caseId, selected.artifact_id)} alt="" className="max-h-[50vh] mx-auto rounded" />
            )}
            <div className="mt-3 space-y-1 text-sm">
              <MetaRow k="Kind" v={selected.kind} />
              <MetaRow k="Size" v={bytes(selected.size_bytes)} />
              <MetaRow k="App" v={selected.app || "—"} />
              <MetaRow k="Trashed" v={selected.trashed ? "yes (recovered from MediaStore trash)" : "no"} />
              <MetaRow k="GPS" v={selected.gps ? `${selected.gps.lat}, ${selected.gps.lon}` : "—"} />
              <MetaRow k="SHA-256" v={selected.sha256} mono />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function MetaRow({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <div className="flex justify-between gap-4">
      <span className="text-muted">{k}</span>
      <span className={`text-right break-all ${mono ? "font-mono text-[11px]" : ""}`}>{v}</span>
    </div>
  );
}
