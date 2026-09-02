/**
 * SnapchatView — Snapchat Forensics
 *
 * Renders conversations recovered from Snapchat's arroyo.db (protobuf) or a
 * "Download My Data" ZIP export.  Ephemeral messages may be present as
 * carved/recovered rows via WAL/freelist analysis.
 *
 * Acquisition paths:
 *   Tier 0/1 : NOT available — arroyo.db is in app-private storage.
 *   Tier 2   : Enable "Tier-2 Snapchat" on a rooted device (su pull of
 *              /data/data/com.snapchat.android/databases/arroyo.db).
 *   Import   : "Download My Data" ZIP from Snapchat → Settings → Privacy.
 *
 * The component delegates the full conversation + message UI to the shared
 * ChatView component, and adds a Snapchat-specific header with acquisition
 * tier guidance and stats when data is present.
 */
import { useEffect, useState } from "react";
import { ChatView } from "../components/ChatView";
import { api } from "../lib/api";
import type { ChatConversationsMap } from "../lib/types";

function SnapchatHeader({ convCount, msgCount, carvedCount }: {
  convCount: number;
  msgCount: number;
  carvedCount: number;
}) {
  return (
    <div className="flex items-center gap-3 px-5 py-3 border-b border-line bg-panel-2 shrink-0">
      {/* Snapchat yellow icon */}
      <div
        className="h-8 w-8 rounded-lg flex items-center justify-center text-white text-lg shrink-0"
        style={{ background: "#FFFC00" }}
      >
        👻
      </div>
      <div>
        <p className="text-sm font-semibold text-ink leading-none">Snapchat</p>
        <p className="text-[11px] text-muted mt-0.5">
          {convCount} conversation{convCount !== 1 ? "s" : ""} · {msgCount} message{msgCount !== 1 ? "s" : ""}
          {carvedCount > 0 && (
            <span className="ml-2 text-yellow-400">· {carvedCount} carved (ephemeral remnants)</span>
          )}
        </p>
      </div>
      <div className="ml-auto flex items-center gap-1.5">
        {carvedCount > 0 && (
          <span className="text-[10px] font-mono bg-yellow-500/15 text-yellow-400 px-2 py-0.5 rounded-full border border-yellow-400/30">
            Carved rows
          </span>
        )}
        <span className="text-[10px] font-mono bg-orange-500/15 text-orange-400 px-2 py-0.5 rounded-full border border-orange-400/30">
          Tier-2 / Data Export
        </span>
      </div>
    </div>
  );
}

export function SnapchatView({ caseId }: { caseId: string }) {
  const [stats, setStats] = useState({ convs: 0, msgs: 0, carved: 0, ready: false });

  useEffect(() => {
    api
      .conversations(caseId, "snapchat_conversations")
      .then((data: ChatConversationsMap) => {
        const convs = Object.values(data ?? {});
        const msgs  = convs.reduce((n, c) => n + (c.messages?.length ?? 0), 0);
        const carved = convs.reduce(
          (n, c) =>
            n + (c.messages ?? []).filter((m) => m.confidence === "carved" || m.confidence === "recovered").length,
          0
        );
        setStats({ convs: convs.length, msgs, carved, ready: true });
      })
      .catch(() => setStats({ convs: 0, msgs: 0, carved: 0, ready: true }));
  }, [caseId]);

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {stats.ready && stats.convs > 0 && (
        <SnapchatHeader convCount={stats.convs} msgCount={stats.msgs} carvedCount={stats.carved} />
      )}
      <div className="flex-1 min-h-0">
        <ChatView
          caseId={caseId}
          dataset="snapchat_conversations"
          title="Snapchat"
          importApp="snapchat"
          emptyTitle="No Snapchat messages found"
          emptyDetail={
            "Snapchat chats (arroyo.db / protobuf) live in app-private storage and require " +
            "Tier-2 (root) access — enable 'Tier-2 Snapchat' on the Acquisition screen, " +
            "or load a 'Download My Data' export from Snapchat → Settings → Privacy → Download My Data. " +
            "Ephemeral messages are carved from WAL/freelist where present."
          }
        />
      </div>
    </div>
  );
}
