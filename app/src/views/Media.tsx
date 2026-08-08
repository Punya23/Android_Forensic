import { useMemo, useState } from "react";
import type { MediaItem, Screenshot, WhatsAppMediaItem } from "../lib/types";
import { useDataset, fmtTs } from "../lib/hooks";
import { api } from "../lib/api";
import { SectionHeader, EmptyState, Filters, bytes } from "../components/common";

type Source = "pulled" | "screenshots" | "whatsapp";

export function MediaView({ caseId }: { caseId: string }) {
  const { data, loading } = useDataset<MediaItem>(caseId, "media");
  const { data: screenshots, loading: screenshotsLoading } = useDataset<Screenshot>(caseId, "screenshots");
  const { data: waMedia, loading: waLoading } = useDataset<WhatsAppMediaItem>(caseId, "whatsapp_media");

  const [source, setSource] = useState<Source>("pulled");

  if (loading || screenshotsLoading || waLoading) return <div className="p-8 text-muted">Loading media…</div>;

  if (data.length === 0 && screenshots.length === 0 && waMedia.length === 0) {
    return <EmptyState title="No media pulled" />;
  }

  const tabs: { k: Source; label: string; n: number }[] = [
    { k: "pulled", label: "Pulled Media", n: data.length },
    { k: "screenshots", label: "Screenshots", n: screenshots.length },
    { k: "whatsapp", label: "WhatsApp Media (on-device)", n: waMedia.length },
  ];

  return (
    <div className="p-6 h-full flex flex-col">
      <SectionHeader
        title="Media"
        sub={`${data.length} pulled · ${screenshots.length} screen capture${screenshots.length === 1 ? "" : "s"} · ${waMedia.length} on-device WhatsApp media`}
      />
      <div className="flex flex-wrap gap-2 mb-4">
        {tabs.map((t) => (
          <button
            key={t.k}
            onClick={() => setSource(t.k)}
            className={`px-3 py-1 rounded-full text-xs border transition-colors ${
              source === t.k ? "border-accent bg-accent/15 text-accent" : "border-line text-muted hover:border-muted"
            }`}
          >
            {t.label} <span className="opacity-60">{t.n}</span>
          </button>
        ))}
      </div>

      {source === "pulled" && <PulledMediaSection caseId={caseId} data={data} />}
      {source === "screenshots" && <ScreenshotsSection caseId={caseId} data={screenshots} />}
      {source === "whatsapp" && <WhatsAppMediaSection data={waMedia} />}
    </div>
  );
}

// ── Pulled Media (existing gallery, unchanged behaviour) ─────────────────────

function PulledMediaSection({ caseId, data }: { caseId: string; data: MediaItem[] }) {
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

  if (data.length === 0)
    return (
      <EmptyState
        title="No media pulled"
        detail="The Tier-0 shared-storage pull found no eligible files (DCIM, Pictures, Download, Movies, Music, Documents, or the WhatsApp/Telegram media trees), or the pull did not run."
      />
    );

  const chips: { k: typeof filter; label: string; n: number }[] = [
    { k: "all", label: "All", n: data.length },
    { k: "image", label: "Images", n: data.filter((m) => m.kind === "image").length },
    { k: "video", label: "Videos", n: data.filter((m) => m.kind === "video").length },
    { k: "trashed", label: "Recovered from Trash", n: data.filter((m) => m.trashed).length },
    { k: "gps", label: "Geotagged", n: data.filter((m) => m.gps).length },
  ];

  return (
    <>
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
    </>
  );
}

// ── Screenshots (manual screen capture, Tier 0) ───────────────────────────────

function ScreenshotsSection({ caseId, data }: { caseId: string; data: Screenshot[] }) {
  const [selected, setSelected] = useState<Screenshot | null>(null);

  if (data.length === 0)
    return (
      <EmptyState
        title="No screen capture in this acquisition"
        detail="This is a single read-only framebuffer grab (exec-out screencap -p) taken once, at pull time — not a scan of screenshots already on the device. Either the manual screen-capture step (capture_screenshot) was disabled for this run, or the capture failed."
      />
    );

  return (
    <>
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-3 overflow-auto flex-1 pb-4">
        {data.map((s) => (
          <button key={s.artifact_id} onClick={() => setSelected(s)} className="card overflow-hidden group text-left">
            <div className="aspect-square bg-panel flex items-center justify-center relative overflow-hidden">
              <img
                src={api.mediaUrl(caseId, s.artifact_id)}
                alt=""
                className="w-full h-full object-cover group-hover:scale-105 transition-transform"
                loading="lazy"
              />
              <span className="absolute bottom-1 left-1 bg-black/70 text-[9px] px-1 rounded">screen capture</span>
            </div>
            <div className="p-1.5">
              <div className="text-[10px] truncate text-muted font-mono">{s.stored_path.split("/").pop()}</div>
              <div className="text-[10px] text-muted/60">{fmtTs(s.captured_at)}</div>
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
            <img src={api.mediaUrl(caseId, selected.artifact_id)} alt="" className="max-h-[50vh] mx-auto rounded" />
            <div className="mt-3 space-y-1 text-sm">
              <MetaRow k="Captured at" v={fmtTs(selected.captured_at)} />
              <MetaRow k="Stored path" v={selected.stored_path} mono />
              <MetaRow k="SHA-256" v={selected.sha256} mono />
            </div>
          </div>
        </div>
      )}
    </>
  );
}

// ── WhatsApp Media, on-device (Tier 0/1, non-root, shared-storage catalogue) ─

function WhatsAppMediaSection({ data }: { data: WhatsAppMediaItem[] }) {
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<string>("all");

  const kinds = useMemo(() => Array.from(new Set(data.map((m) => m.type))).sort(), [data]);

  const filtered = useMemo(() => {
    const q = query.toLowerCase();
    return data
      .filter((m) => kind === "all" || m.type === kind)
      .filter((m) => !q || m.filename.toLowerCase().includes(q) || m.path.toLowerCase().includes(q))
      .sort((a, b) => (b.date || "").localeCompare(a.date || ""));
  }, [data, query, kind]);

  if (data.length === 0)
    return (
      <EmptyState
        title="No on-device WhatsApp media catalogued"
        detail="This requires WhatsApp's Media folder to be present and reachable in shared storage (/sdcard/Android/media/com.whatsapp/WhatsApp/Media, or the legacy /sdcard/WhatsApp/Media on upgraded devices). Distinct from the Tier-2 decrypted-backup media under WhatsApp Backup, which recovers media referenced by chat-database messages regardless of whether it's still in this folder — and may include media this folder never had, or omit media this folder still has."
      />
    );

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <div className="text-xs text-muted bg-panel border border-line rounded px-3 py-2 mb-3">
        On-device WhatsApp media, as currently stored in shared storage — a filename/size/date catalogue only. These
        entries were not individually pulled into the case manifest, so no artifact ID was assigned and no thumbnail
        preview is available here. This is distinct from the Tier-2 decrypted-backup media shown under WhatsApp
        Backup, which the tool has actually recovered and can preview.
      </div>
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <Filters query={query} onQuery={setQuery} placeholder="Search filename or path…" />
        <select className="input w-auto" value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="all">All types</option>
          {kinds.map((k) => (
            <option key={k} value={k}>{k}</option>
          ))}
        </select>
      </div>
      <div className="card overflow-auto flex-1">
        <table className="w-full text-sm">
          <thead>
            <tr>
              <th className="th">Filename</th>
              <th className="th w-24">Type</th>
              <th className="th w-24">Size</th>
              <th className="th w-28">Date</th>
              <th className="th">Path (on-device inventory)</th>
            </tr>
          </thead>
          <tbody>
            {filtered.slice(0, 2000).map((m, i) => (
              <tr key={i}>
                <td className="td font-medium truncate max-w-xs" title={m.filename}>{m.filename}</td>
                <td className="td text-xs">{m.type}</td>
                <td className="td text-xs font-mono">{bytes(m.size_bytes)}</td>
                <td className="td text-xs font-mono text-muted">{m.date || "—"}</td>
                <td className="td text-[11px] font-mono text-muted truncate max-w-md" title={m.path}>{m.path}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
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
