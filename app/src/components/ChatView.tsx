/**
 * ChatView — a reusable, app-neutral conversation view (list + chat bubbles).
 *
 * Renders any `ChatConversationsMap` (the shape produced by the engine's
 * `thread_conversations`), so Instagram, Snapchat, and the generic app-finder all share one
 * consistent, confidence-badged chat UI. Deleted/carved messages are ringed by their
 * confidence colour exactly like the Telegram view.
 */
import { useEffect, useMemo, useRef, useState, type ChangeEvent } from "react";
import type { ChatConversationsMap, ChatConversation, ChatMessage } from "../lib/types";
import { ConfidenceBadge } from "./Badges";
import { SectionHeader, EmptyState } from "./common";
import { fmtTs } from "../lib/hooks";
import { api } from "../lib/api";

const CONF_BG: Record<string, string> = {
  live: "bg-live/10 border-live/40",
  recovered: "bg-recovered/10 border-recovered/40",
  carved: "bg-carved/10 border-carved/40",
  deletion: "bg-deletion/10 border-deletion/40",
};

function ConvListItem({
  conv,
  selected,
  onSelect,
}: {
  conv: ChatConversation;
  selected: boolean;
  onSelect: () => void;
}) {
  const lastMsg = conv.messages.at(-1);
  return (
    <button
      onClick={onSelect}
      className={`w-full text-left px-4 py-3 border-b border-line transition-colors ${
        selected ? "bg-accent/15 border-l-2 border-l-accent" : "hover:bg-panel-2"
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium text-sm truncate">{conv.title}</span>
        <span className="text-[10px] text-muted whitespace-nowrap">{fmtTs(conv.last_message_ts)}</span>
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

function MessageBubble({ msg, caseId }: { msg: ChatMessage; caseId: string }) {
  const src = msg.media_artifact_id ? `${api.base}/case/${caseId}/media/${msg.media_artifact_id}` : null;
  const bgClass = CONF_BG[msg.confidence] ?? CONF_BG.carved;
  return (
    <div className={`flex flex-col max-w-[70%] rounded-xl border px-3 py-2 ${bgClass}`}>
      <div className="flex items-center gap-2 mb-1">
        <span className="text-xs font-semibold text-ink/80">{msg.sender_name}</span>
        <span className="text-[10px] text-muted">{fmtTs(msg.timestamp)}</span>
        <ConfidenceBadge c={msg.confidence} />
      </div>
      {msg.body && <p className="text-sm whitespace-pre-wrap break-words">{msg.body}</p>}
      {src && (
        <a href={src} target="_blank" rel="noopener noreferrer" className="mt-2">
          <img
            src={src}
            alt="media"
            className="max-h-48 rounded-lg object-contain border border-line"
            onError={(e) => {
              (e.currentTarget as HTMLImageElement).style.display = "none";
            }}
          />
        </a>
      )}
      {msg.provenance && (
        <div className="text-[10px] text-muted/60 font-mono mt-1 truncate">{msg.provenance}</div>
      )}
    </div>
  );
}

function ConversationDetail({ conv, caseId }: { conv: ChatConversation; caseId: string }) {
  return (
    <div className="flex flex-col h-full">
      <div className="shrink-0 px-5 py-3 border-b border-line bg-panel-2">
        <div className="font-semibold">{conv.title}</div>
        <div className="text-xs text-muted">
          {conv.participants.map((p) => p.name).join(", ") || "—"} · {conv.message_count} messages
        </div>
      </div>
      <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-3">
        {conv.messages.length === 0 && (
          <div className="text-muted text-sm">No message content recovered.</div>
        )}
        {conv.messages.map((msg, i) => (
          <div key={i} className="flex justify-start">
            <MessageBubble msg={msg} caseId={caseId} />
          </div>
        ))}
      </div>
    </div>
  );
}

function ImportControl({
  app,
  caseId,
  onImported,
  compact,
}: {
  app: "instagram" | "snapchat";
  caseId: string;
  onImported: () => void;
  compact?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  async function onFile(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow re-selecting the same file
    if (!file) return;
    setBusy(true);
    setMsg(null);
    try {
      const res = await api.importExport(caseId, app, file);
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
      <input
        ref={inputRef}
        type="file"
        accept=".zip,.json"
        className="hidden"
        onChange={onFile}
      />
      <button
        className={compact ? "btn-ghost text-xs py-1" : "btn-accent text-sm"}
        disabled={busy}
        onClick={() => inputRef.current?.click()}
      >
        {busy ? "Importing…" : `Import ${app === "instagram" ? "Instagram" : "Snapchat"} data export`}
      </button>
      {msg && <span className="text-xs text-muted">{msg}</span>}
    </div>
  );
}

export function ChatView({
  caseId,
  dataset,
  title,
  emptyTitle,
  emptyDetail,
  importApp,
}: {
  caseId: string;
  dataset: string; // derived dataset name serving a ChatConversationsMap (e.g. "instagram_conversations")
  title: string;
  emptyTitle: string;
  emptyDetail: string;
  importApp?: "instagram" | "snapchat"; // enables "Import data export" (non-root path)
}) {
  const [convs, setConvs] = useState<ChatConversationsMap | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    setLoading(true);
    setSelectedId(null);
    api
      .conversations(caseId, dataset)
      .then((data: ChatConversationsMap) => {
        setConvs(data && typeof data === "object" ? data : {});
        const first = Object.keys(data || {})[0];
        if (first) setSelectedId(first);
      })
      .catch(() => setConvs({}))
      .finally(() => setLoading(false));
  }, [caseId, dataset, reloadKey]);

  const sorted = useMemo(() => {
    if (!convs) return [];
    return Object.values(convs).sort((a, b) => {
      if (!a.last_message_ts) return 1;
      if (!b.last_message_ts) return -1;
      return b.last_message_ts.localeCompare(a.last_message_ts);
    });
  }, [convs]);

  const filtered = useMemo(() => {
    const q = search.toLowerCase();
    if (!q) return sorted;
    return sorted.filter(
      (c) =>
        c.title.toLowerCase().includes(q) ||
        c.participants.some((p) => p.name.toLowerCase().includes(q)) ||
        c.messages.some((m) => (m.body || "").toLowerCase().includes(q))
    );
  }, [sorted, search]);

  const selected = selectedId && convs ? convs[selectedId] : null;

  if (loading) return <div className="p-8 text-muted">Loading {title} conversations…</div>;
  if (!convs || Object.keys(convs).length === 0) {
    if (importApp)
      return (
        <div className="flex flex-col items-center justify-center h-full text-center py-16 px-6">
          <div className="text-muted text-sm max-w-md">
            <div className="text-ink font-medium mb-1">{emptyTitle}</div>
            <p className="leading-relaxed">{emptyDetail}</p>
          </div>
          <ImportControl app={importApp} caseId={caseId} onImported={() => setReloadKey((k) => k + 1)} />
        </div>
      );
    return <EmptyState title={emptyTitle} detail={emptyDetail} />;
  }

  return (
    <div className="flex h-full overflow-hidden">
      <aside className="w-72 shrink-0 border-r border-line flex flex-col">
        <div className="p-4 pb-2">
          <SectionHeader
            title={title}
            sub={`${sorted.length} conversation(s)`}
            right={
              importApp ? (
                <ImportControl
                  app={importApp}
                  caseId={caseId}
                  onImported={() => setReloadKey((k) => k + 1)}
                  compact
                />
              ) : undefined
            }
          />
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
      <main className="flex-1 overflow-hidden">
        {selected ? (
          <ConversationDetail conv={selected} caseId={caseId} />
        ) : (
          <div className="flex items-center justify-center h-full text-muted text-sm">
            Select a conversation to view messages.
          </div>
        )}
      </main>
    </div>
  );
}
