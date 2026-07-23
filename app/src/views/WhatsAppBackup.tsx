/**
 * WhatsAppBackup.tsx — WhatsApp backup recovery view.
 *
 * Data sources
 * ------------
 * GET /api/case/<id>/whatsapp_backup/messages  → WhatsAppBackupMessage[]
 * GET /api/case/<id>/whatsapp_backup/summary   → WhatsAppBackupSummary
 *
 * Layout
 * ------
 * Left panel  — list of backup files with per-file confidence breakdown badges.
 * Right panel — messages for the selected backup, grouped by chat_id, rendered
 *               as chat bubbles with per-message confidence rings.
 * Top bar     — global stats (total live / recovered / carved / gaps).
 */
import { useEffect, useMemo, useState } from "react";
import type {
  WhatsAppBackupMessage,
  WhatsAppBackupSummary,
} from "../lib/types";
import { ConfidenceBadge } from "../components/Badges";
import { SectionHeader, EmptyState } from "../components/common";
import { fmtTs } from "../lib/hooks";
import { api } from "../lib/api";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const CONF_BG: Record<string, string> = {
  live: "bg-live/10 border-live/40",
  recovered: "bg-recovered/10 border-recovered/40",
  carved: "bg-carved/10 border-carved/40",
  deletion: "bg-deletion/10 border-deletion/40",
};

const CONF_RING: Record<string, string> = {
  live: "ring-live/50",
  recovered: "ring-recovered/50",
  carved: "ring-carved/50",
  deletion: "ring-deletion/50",
};

const MEDIA_ICON: Record<string, string> = {
  image: "🖼",
  video: "🎬",
  audio: "🎵",
  document: "📄",
  sticker: "🎭",
  gif: "🎞",
  vcard: "👤",
  location: "📍",
};

function mediaIcon(mediaType: string): string {
  return MEDIA_ICON[mediaType] ?? "📎";
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function StatPill({
  label,
  count,
  color,
}: {
  label: string;
  count: number;
  color: string;
}) {
  return (
    <div className={`flex items-center gap-1.5 px-3 py-1 rounded-full border text-xs font-medium ${color}`}>
      <span className="font-mono text-sm">{count.toLocaleString()}</span>
      <span className="opacity-70">{label}</span>
    </div>
  );
}

function BackupListItem({
  filename,
  stats,
  selected,
  onSelect,
}: {
  filename: string;
  stats: WhatsAppBackupSummary["per_file"][string] | undefined;
  selected: boolean;
  onSelect: () => void;
}) {
  const cryptMatch = filename.match(/crypt(\d+)/i);
  const cryptVer = cryptMatch ? `crypt${cryptMatch[1]}` : "unknown";
  const dateMatch = filename.match(/(\d{4}-\d{2}-\d{2})/);
  const date = dateMatch ? dateMatch[1] : "current";

  return (
    <button
      onClick={onSelect}
      className={`w-full text-left px-4 py-3 border-b border-line transition-colors ${selected
        ? "bg-accent/15 border-l-2 border-l-accent"
        : "hover:bg-panel-2"
        }`}
    >
      <div className="flex items-center justify-between gap-2 mb-1">
        <span className="font-mono text-xs text-accent truncate">{cryptVer}</span>
        <span className="text-[10px] text-muted">{date}</span>
      </div>
      <div className="text-xs text-muted truncate mb-2">{filename}</div>
      {stats && (
        <div className="flex flex-wrap gap-1">
          {stats.live > 0 && (
            <span className="px-1.5 py-0.5 rounded text-[10px] bg-live/10 text-live border border-live/20">
              {stats.live} live
            </span>
          )}
          {stats.recovered > 0 && (
            <span className="px-1.5 py-0.5 rounded text-[10px] bg-recovered/10 text-recovered border border-recovered/20">
              {stats.recovered} recovered
            </span>
          )}
          {stats.carved > 0 && (
            <span className="px-1.5 py-0.5 rounded text-[10px] bg-carved/10 text-carved border border-carved/20">
              {stats.carved} carved
            </span>
          )}
          {stats.deletion > 0 && (
            <span className="px-1.5 py-0.5 rounded text-[10px] bg-deletion/10 text-deletion border border-deletion/20">
              {stats.deletion} gaps
            </span>
          )}
        </div>
      )}
    </button>
  );
}

function MessageBubble({ msg }: { msg: WhatsAppBackupMessage }) {
  const isMe = msg.sender === "me";
  const conf = msg.confidence ?? "live";
  const bg = CONF_BG[conf] ?? CONF_BG.live;
  const ring = CONF_RING[conf] ?? "";
  const hasMedia = Boolean(msg.media_type || msg.media_path);

  return (
    <div className={`flex ${isMe ? "justify-end" : "justify-start"} mb-3`}>
      <div
        className={`max-w-[72%] rounded-2xl border px-4 py-2.5 ring-1 ${bg} ${ring} transition-all`}
      >
        {/* Sender label */}
        <div className="flex items-center gap-2 mb-1">
          <span className="text-[10px] font-semibold opacity-60 truncate max-w-[120px]">
            {isMe ? "You" : msg.sender}
          </span>
          <ConfidenceBadge c={conf} />
          {msg.flags?.includes("deleted") && (
            <span className="text-[9px] px-1 rounded bg-deletion/20 text-deletion border border-deletion/30">
              deleted
            </span>
          )}
        </div>

        {/* Media indicator */}
        {hasMedia && (
          <div className="text-xs text-muted mb-1 flex items-center gap-1">
            <span>{mediaIcon(msg.media_type)}</span>
            <span className="truncate opacity-70">{msg.media_path || msg.media_type}</span>
          </div>
        )}

        {/* Body */}
        <p className="text-sm leading-relaxed whitespace-pre-wrap break-words">
          {msg.body || <span className="italic opacity-40">[no text]</span>}
        </p>

        {/* Footer */}
        <div className="flex items-center justify-between gap-2 mt-1">
          <span className="text-[10px] opacity-40 truncate max-w-[180px]">
            {msg.provenance}
          </span>
          <span className="text-[10px] opacity-50 shrink-0">
            {fmtTs(msg.timestamp)}
          </span>
        </div>
      </div>
    </div>
  );
}

function ChatGroup({
  chatId,
  messages,
}: {
  chatId: string;
  messages: WhatsAppBackupMessage[];
}) {
  const [open, setOpen] = useState(true);
  const displayId = chatId.includes("@")
    ? chatId.split("@")[0]
    : chatId;

  return (
    <div className="mb-4 border border-line rounded-xl overflow-hidden">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between px-4 py-2 bg-panel-2 hover:bg-panel transition-colors"
      >
        <span className="font-medium text-sm text-accent truncate">{displayId}</span>
        <div className="flex items-center gap-2 text-xs text-muted">
          <span>{messages.length} msg(s)</span>
          <span className={`transition-transform ${open ? "rotate-180" : ""}`}>▾</span>
        </div>
      </button>
      {open && (
        <div className="p-4 bg-panel">
          {messages.map((m, i) => (
            <MessageBubble key={i} msg={m} />
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main view
// ---------------------------------------------------------------------------

export function WhatsAppBackupView({ caseId }: { caseId: string }) {
  const [messages, setMessages] = useState<WhatsAppBackupMessage[]>([]);
  const [summary, setSummary] = useState<WhatsAppBackupSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [search, setSearch] = useState("");

  useEffect(() => {
    setLoading(true);
    Promise.all([
      fetch(`/api/case/${caseId}/whatsapp_backup/messages`).then((r) => r.json()),
      fetch(`/api/case/${caseId}/whatsapp_backup/summary`).then((r) => r.json()).catch(() => null),
    ])
      .then(([msgs, sum]) => {
        setMessages(Array.isArray(msgs) ? msgs : []);
        setSummary(sum && typeof sum === "object" ? sum : null);
        // Auto-select the first backup file.
        const files = [...new Set((msgs as WhatsAppBackupMessage[]).map((m) => m.backup_file))];
        if (files.length > 0) setSelectedFile(files[0]);
      })
      .finally(() => setLoading(false));
  }, [caseId]);

  // Derive list of backup files.
  const backupFiles = useMemo(
    () => [...new Set(messages.map((m) => m.backup_file))],
    [messages]
  );

  // Filter messages for the selected backup.
  const filteredMessages = useMemo(() => {
    let ms = messages.filter((m) => m.backup_file === selectedFile);
    if (search.trim()) {
      const q = search.toLowerCase();
      ms = ms.filter(
        (m) =>
          m.body?.toLowerCase().includes(q) ||
          m.sender?.toLowerCase().includes(q) ||
          m.chat_id?.toLowerCase().includes(q)
      );
    }
    return ms;
  }, [messages, selectedFile, search]);

  // Group by chat_id.
  const chatGroups = useMemo(() => {
    const groups: Record<string, WhatsAppBackupMessage[]> = {};
    for (const m of filteredMessages) {
      const key = m.chat_id || "<unknown>";
      (groups[key] ??= []).push(m);
    }
    return Object.entries(groups).sort(([, a], [, b]) => b.length - a.length);
  }, [filteredMessages]);

  const totals = summary?.totals;

  if (loading) {
    return (
      <div className="p-8 text-muted text-sm animate-pulse">
        Loading WhatsApp backup data…
      </div>
    );
  }

  if (messages.length === 0) {
    return (
      <div className="p-8">
        <SectionHeader title="WhatsApp Backup Recovery" />
        <EmptyState
          title="No WhatsApp backup messages recovered."
          detail="Enable Tier-2 WhatsApp Backup Recovery and re-run the acquisition on a rooted device."
        />
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Top stats bar */}
      <div className="shrink-0 px-5 py-3 border-b border-line bg-panel-2 flex flex-wrap items-center gap-2">
        <span className="font-semibold text-sm mr-2">WhatsApp Backup Recovery</span>
        {totals && (
          <>
            <StatPill label="live" count={totals.live} color="text-live border-live/30" />
            <StatPill label="recovered" count={totals.recovered} color="text-recovered border-recovered/30" />
            <StatPill label="carved" count={totals.carved} color="text-carved border-carved/30" />
            <StatPill label="gaps" count={totals.deletion} color="text-deletion border-deletion/30" />
            <span className="text-xs text-muted ml-auto">{totals.messages.toLocaleString()} total messages</span>
          </>
        )}
      </div>

      <div className="flex flex-1 overflow-hidden">
        {/* Left panel — backup file list */}
        <div className="w-64 shrink-0 border-r border-line overflow-y-auto">
          <div className="px-3 py-2 text-[10px] text-muted uppercase tracking-wider font-semibold border-b border-line">
            Backup Files ({backupFiles.length})
          </div>
          {backupFiles.map((fname) => (
            <BackupListItem
              key={fname}
              filename={fname}
              stats={summary?.per_file?.[fname]}
              selected={selectedFile === fname}
              onSelect={() => setSelectedFile(fname)}
            />
          ))}
        </div>

        {/* Right panel — messages */}
        <div className="flex-1 flex flex-col overflow-hidden">
          {/* Search bar */}
          <div className="shrink-0 px-4 py-2 border-b border-line bg-panel-2 flex items-center gap-2">
            <input
              type="text"
              placeholder="Search messages, senders, chats…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted"
            />
            {search && (
              <button onClick={() => setSearch("")} className="text-muted hover:text-fg text-xs">
                ✕
              </button>
            )}
            <span className="text-xs text-muted shrink-0">
              {filteredMessages.length.toLocaleString()} message(s)
            </span>
          </div>

          {/* Chat groups */}
          <div className="flex-1 overflow-y-auto px-4 py-4">
            {chatGroups.length === 0 ? (
              <EmptyState
                title="No messages match the current filter."
                detail="Try changing the search term."
              />
            ) : (
              chatGroups.map(([chatId, msgs]) => (
                <ChatGroup key={chatId} chatId={chatId} messages={msgs} />
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
