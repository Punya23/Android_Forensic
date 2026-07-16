import { ChatView } from "../components/ChatView";

export function InstagramView({ caseId }: { caseId: string }) {
  return (
    <ChatView
      caseId={caseId}
      dataset="instagram_conversations"
      title="Instagram"
      importApp="instagram"
      emptyTitle="No Instagram messages"
      emptyDetail={
        "Instagram Direct (direct.db) lives in app-private storage and requires Tier-2 (root) " +
        "access — enable 'Tier-2 Instagram' on a rooted device, ingest a full-filesystem image, " +
        "or load a 'Download Your Data' export. Not reachable at Tier 0/1."
      }
    />
  );
}
