import { ChatView } from "../components/ChatView";

export function SnapchatView({ caseId }: { caseId: string }) {
  return (
    <ChatView
      caseId={caseId}
      dataset="snapchat_conversations"
      title="Snapchat"
      importApp="snapchat"
      emptyTitle="No Snapchat messages"
      emptyDetail={
        "Snapchat chats (arroyo.db, protobuf) live in app-private storage and require Tier-2 " +
        "(root) access — enable 'Tier-2 Snapchat' on a rooted device or ingest a full-filesystem " +
        "image. Ephemeral messages are carved from WAL/freelist where present. Not reachable at Tier 0/1."
      }
    />
  );
}
