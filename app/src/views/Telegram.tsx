/**
 * Telegram.tsx — Chat-style Telegram conversation view.
 *
 * Data sources
 * ------------
 * GET /api/case/<id>/telegram/conversations  → TelegramConversationsMap
 * GET /api/case/<id>/media/<artifact_id>    → served file (thumbnails)
 *
 * Layout
 * ------
 * Left panel  — conversation list sorted by last_message_ts descending.
 * Right panel — selected conversation rendered as WhatsApp-style bubbles,
 *               with a coloured confidence ring around each bubble and
 *               media thumbnails served by the existing media endpoint.
 */
import { useMemo, useState, useEffect, useRef, type ChangeEvent } from "react";
import type {
  TelegramConversation,
  TelegramConversationsMap,
  TelegramMessage,
  TelegramPresence,
} from "../lib/types";
import { ConfidenceBadge } from "../components/Badges";
import { SectionHeader, EmptyState } from "../components/common";
import { fmtTs } from "../lib/hooks";
import { api, BASE } from "../lib/api";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const CONF_BG: Record<string, string> = {
  live:      "bg-live/10 border-live/40",
  recovered: "bg-recovered/10 border-recovered/40",
  carved:    "bg-carved/10 border-carved/40",
  deletion:  "bg-deletion/10 border-deletion/40",
};

function mediaSrc(caseId: string, artifactId: string | null): string | null {
  if (!artifactId) return null;
  return `${BASE}/api/case/${caseId}/media/${artifactId}`;
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function ConvListItem({
  conv,
  selected,
  onSelect,
}: {
  conv: TelegramConversation;
  selected: boolean;
  onSelect: () => void;
}) {
  const lastMsg = conv.messages.at(-1);
  return (
    <button
      onClick={onSelect}
      className={`w-full text-left px-4 py-3 border-b border-line transition-colors ${
        selected
          ? "bg-accent/15 border-l-2 border-l-accent"
          : "hover:bg-panel-2"
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium text-sm truncate">{conv.title}</span>
        <span className="text-[10px] text-muted whitespace-nowrap">
          {fmtTs(conv.last_message_ts)}
        </span>
      </div>
      <div className="text-xs text-muted mt-0.5 flex items-center gap-2">
        <span>{conv.participants.length} participant(s)</span>
        <span>·</span>
        <span>{conv.message_count} msg(s)</span>
      </div>
      {lastMsg && (
        <div className="text-xs text-muted/70 truncate mt-0.5">
          {lastMsg.sender_name}: {(lastMsg.body || "").slice(0, 60)}
        </div>
      )}
    </button>
  );
}

function MessageBubble({
  msg,
  caseId,
}: {
  msg: TelegramMessage;
  caseId: string;
}) {
  const src = mediaSrc(caseId, msg.media_artifact_id);
  const bgClass = CONF_BG[msg.confidence] ?? CONF_BG["carved"];

  return (
    <div className={`flex flex-col max-w-[70%] rounded-xl border px-3 py-2 ${bgClass}`}>
      <div className="flex items-center gap-2 mb-1">
        <span className="text-xs font-semibold text-ink/80">{msg.sender_name}</span>
        <span className="text-[10px] text-muted">{fmtTs(msg.timestamp)}</span>
        <ConfidenceBadge c={msg.confidence} />
      </div>
      {msg.body && (
        <p className="text-sm whitespace-pre-wrap break-words">{msg.body}</p>
      )}
      {src && (
        <a href={src} target="_blank" rel="noopener noreferrer" className="mt-2">
          <img
            src={src}
            alt="Telegram media"
            className="max-h-48 rounded-lg object-contain border border-line"
            onError={(e) => {
              // If not an image, show a download link instead.
              (e.currentTarget as HTMLImageElement).style.display = "none";
              (e.currentTarget.nextSibling as HTMLElement | null)?.removeAttribute("hidden");
            }}
          />
          <span hidden className="text-xs text-accent underline">
            Download media ({msg.media_artifact_id})
          </span>
        </a>
      )}
      {msg.provenance && (
        <div className="text-[10px] text-muted/60 font-mono mt-1 truncate">
          {msg.provenance}
        </div>
      )}
    </div>
  );
}

function ConversationDetail({
  conv,
  caseId,
}: {
  conv: TelegramConversation;
  caseId: string;
}) {
  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="shrink-0 px-5 py-3 border-b border-line bg-panel-2">
        <div className="font-semibold">{conv.title}</div>
        <div className="text-xs text-muted">
          {conv.participants.map((p) => p.name).join(", ")} ·{" "}
          {conv.message_count} messages
        </div>
      </div>
      {/* Bubbles */}
      <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-3">
        {conv.messages.length === 0 && (
          <div className="text-muted text-sm">No message content recovered.</div>
        )}
        {conv.messages.map((msg, i) => (
          <div
            key={i}
            className={`flex ${
              msg.sender_id === "__self__" ? "justify-end" : "justify-start"
            }`}
          >
            <MessageBubble msg={msg} caseId={caseId} />
          </div>
        ))}
      </div>
    </div>
  );
}

// Non-root fallback: ingest a Telegram Desktop "Export Telegram data" JSON/ZIP.
function ImportControl({
  caseId,
  onImported,
  compact,
}: {
  caseId: string;
  onImported: () => void;
  compact?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  async function onFile(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setBusy(true);
    setMsg(null);
    try {
      const res = await api.importExport(caseId, "telegram", file);
      setMsg(`Imported ${res.imported} message(s).`);
      onImported();
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "Import failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={compact ? "flex items-center gap-2" : "mt-4 flex flex-col items-center gap-2"}>
      <input ref={inputRef} type="file" accept=".zip,.json" className="hidden" onChange={onFile} />
      <button
        className={compact ? "btn-ghost text-xs py-1" : "btn-accent text-sm"}
        disabled={busy}
        onClick={() => inputRef.current?.click()}
      >
        {busy ? "Importing…" : "Import Telegram Desktop data export"}
      </button>
      {msg && <span className="text-xs text-muted">{msg}</span>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main view
// ---------------------------------------------------------------------------

export function TelegramView({ caseId }: { caseId: string }) {
  const [convs, setConvs] = useState<TelegramConversationsMap | null>(null);
  const [presence, setPresence] = useState<TelegramPresence | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    setLoading(true);
    fetch(`${BASE}/api/case/${caseId}/telegram/conversations`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((data: TelegramConversationsMap) => {
        setConvs(data);
        const first = Object.keys(data)[0];
        if (first) setSelectedId(first);
      })
      .catch(() => setConvs({}))
      .finally(() => setLoading(false));
    fetch(`${BASE}/api/case/${caseId}/telegram_presence`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data: TelegramPresence | null) => setPresence(data && data.attempted ? data : null))
      .catch(() => setPresence(null));
  }, [caseId, reloadKey]);

  const sortedConvs = useMemo(() => {
    if (!convs) return [];
    return Object.values(convs).sort((a, b) => {
      if (!a.last_message_ts) return 1;
      if (!b.last_message_ts) return -1;
      return b.last_message_ts.localeCompare(a.last_message_ts);
    });
  }, [convs]);

  const filtered = useMemo(() => {
    const q = search.toLowerCase();
    if (!q) return sortedConvs;
    return sortedConvs.filter(
      (c) =>
        c.title.toLowerCase().includes(q) ||
        c.participants.some((p) => p.name.toLowerCase().includes(q)) ||
        c.messages.some((m) => (m.body || "").toLowerCase().includes(q))
    );
  }, [sortedConvs, search]);

  const selectedConv = selectedId && convs ? convs[selectedId] : null;

  if (loading)
    return <div className="p-8 text-muted">Loading Telegram conversations…</div>;

  if (!convs || Object.keys(convs).length === 0) {
    const detail = presence
      ? `Tier-2 root acquisition did not recover any Telegram content. Reason: ${
          presence.reason || "unknown"
        }. This does not mean Telegram is absent from the device.`
      : "Telegram full chat history requires Tier-2 (root) access and " +
        "tier2_telegram=true in PipelineConfig. " +
        "If acquisition was Tier-0 only, only gallery media is available.";
    return (
      <div className="flex flex-col items-center justify-center h-full text-center py-16 px-6">
        <EmptyState title="No Telegram conversations" detail={detail} />
        <ImportControl caseId={caseId} onImported={() => setReloadKey((k) => k + 1)} />
      </div>
    );
  }

  return (
    <div className="flex h-full overflow-hidden">
      {/* Left: conversation list */}
      <aside className="w-72 shrink-0 border-r border-line flex flex-col">
        <div className="flex items-center justify-between px-1">
          <SectionHeader
            title="Telegram"
            sub={`${sortedConvs.length} conversation(s)`}
          />
        </div>
        <div className="px-4 pb-2">
          <ImportControl caseId={caseId} onImported={() => setReloadKey((k) => k + 1)} compact />
        </div>
        <div className="px-3 py-2 border-b border-line">
          <input
            className="w-full bg-panel border border-line rounded px-2.5 py-1.5 text-sm outline-none focus:border-accent"
            placeholder="Search conversations…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <div className="flex-1 overflow-y-auto">
          {filtered.map((conv) => (
            <ConvListItem
              key={conv.chat_id}
              conv={conv}
              selected={selectedId === conv.chat_id}
              onSelect={() => setSelectedId(conv.chat_id)}
            />
          ))}
        </div>
      </aside>

      {/* Right: conversation detail */}
      <main className="flex-1 overflow-hidden">
        {selectedConv ? (
          <ConversationDetail conv={selectedConv} caseId={caseId} />
        ) : (
          <div className="flex items-center justify-center h-full text-muted text-sm">
            Select a conversation to view messages.
          </div>
        )}
      </main>
    </div>
  );
}
