import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "./api";
import type { Tag } from "./types";

interface TagStore {
  tags: Tag[];
  isTagged: (ref: string) => Tag | undefined;
  toggle: (ref: string, kind: string, label: string) => Promise<void>;
  remove: (id: string) => Promise<void>;
}

const Ctx = createContext<TagStore | null>(null);

export function TagProvider({ caseId, children }: { caseId: string; children: ReactNode }) {
  const [tags, setTags] = useState<Tag[]>([]);

  useEffect(() => {
    api.tags(caseId).then(setTags).catch(() => setTags([]));
  }, [caseId]);

  const isTagged = (ref: string) => tags.find((t) => t.ref === ref);

  async function toggle(ref: string, kind: string, label: string) {
    const existing = isTagged(ref);
    if (existing) {
      await api.removeTag(caseId, existing.id);
      setTags((ts) => ts.filter((t) => t.id !== existing.id));
    } else {
      const tag = await api.addTag(caseId, { ref, kind, label });
      setTags((ts) => [...ts, tag]);
    }
  }

  async function remove(id: string) {
    await api.removeTag(caseId, id);
    setTags((ts) => ts.filter((t) => t.id !== id));
  }

  return <Ctx.Provider value={{ tags, isTagged, toggle, remove }}>{children}</Ctx.Provider>;
}

export function useTags(): TagStore {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useTags must be used within TagProvider");
  return ctx;
}

export function TagButton({ refId, kind, label }: { refId: string; kind: string; label: string }) {
  const { isTagged, toggle } = useTags();
  const tagged = !!isTagged(refId);
  return (
    <button
      onClick={(e) => {
        e.stopPropagation();
        toggle(refId, kind, label);
      }}
      title={tagged ? "Remove bookmark" : "Bookmark this item"}
      className={`text-sm leading-none transition-colors ${tagged ? "text-accent" : "text-muted/40 hover:text-muted"}`}
    >
      {tagged ? "★" : "☆"}
    </button>
  );
}
