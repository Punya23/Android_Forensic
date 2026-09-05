/**
 * InstagramView — Instagram Direct Message Forensics
 *
 * Renders conversations recovered from Instagram's direct.db or a
 * "Download Your Data" ZIP export, showing confidence-badged messages
 * with carved/recovered row indicators.
 *
 * Acquisition paths:
 *   Tier 0/1 : NOT available — direct.db is in app-private storage.
 *   Tier 2   : Enable "Tier-2 Instagram" on a rooted device (su pull of
 *              /data/data/com.instagram.android/databases/direct.db).
 *   Import   : "Download Your Data" ZIP from Instagram Settings > Privacy.
 *
 * The component delegates the full conversation + message UI to the shared
 * ChatView component, and adds an Instagram-specific header with acquisition
 * tier guidance and a stats bar when data is present.
 */
import { useEffect, useState } from "react";
import { Camera } from "lucide-react";
import { ChatView } from "../components/ChatView";
import { api } from "../lib/api";
import type { ChatConversationsMap } from "../lib/types";

function InstagramHeader({ convCount, msgCount }: { convCount: number; msgCount: number }) {
  return (
    <div className="flex items-center gap-3 px-5 py-3 border-b border-line bg-panel-2 shrink-0">
      {/* Instagram gradient icon */}
      <div
        className="h-8 w-8 rounded-lg flex items-center justify-center text-white text-lg shrink-0"
        style={{
          background:
            "radial-gradient(circle at 30% 107%, #fdf497 0%, #fdf497 5%, #fd5949 45%, #d6249f 60%, #285AEB 90%)",
        }}
      >
        <Camera className="h-4 w-4" strokeWidth={1.75} aria-hidden />
      </div>
      <div>
        <p className="text-sm font-semibold text-ink leading-none">Instagram Direct</p>
        <p className="text-[11px] text-muted mt-0.5">
          {convCount} conversation{convCount !== 1 ? "s" : ""} · {msgCount} message{msgCount !== 1 ? "s" : ""}
        </p>
      </div>
      <div className="ml-auto flex items-center gap-1.5">
        <span className="text-[10px] font-mono bg-orange-500/15 text-orange-400 px-2 py-0.5 rounded-full border border-orange-400/30">
          Tier-2 / Data Export
        </span>
      </div>
    </div>
  );
}

export function InstagramView({ caseId }: { caseId: string }) {
  const [stats, setStats] = useState({ convs: 0, msgs: 0, ready: false });

  useEffect(() => {
    api
      .conversations(caseId, "instagram_conversations")
      .then((data: ChatConversationsMap) => {
        const convs = Object.values(data ?? {});
        const msgs  = convs.reduce((n, c) => n + (c.messages?.length ?? 0), 0);
        setStats({ convs: convs.length, msgs, ready: true });
      })
      .catch(() => setStats({ convs: 0, msgs: 0, ready: true }));
  }, [caseId]);

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {stats.ready && stats.convs > 0 && (
        <InstagramHeader convCount={stats.convs} msgCount={stats.msgs} />
      )}
      <div className="flex-1 min-h-0">
        <ChatView
          caseId={caseId}
          dataset="instagram_conversations"
          title="Instagram"
          importApp="instagram"
          emptyTitle="No Instagram messages found"
          emptyDetail={
            "Instagram Direct (direct.db) lives in app-private storage and requires " +
            "Tier-2 (root) access — enable 'Tier-2 Instagram' on the Acquisition screen, " +
            "or load a 'Download Your Data' export from Instagram > Settings > Privacy."
          }
        />
      </div>
    </div>
  );
}
