// API client for the local Python engine. In dev, Vite proxies /api and /socket.io to
// :5057; in the packaged Electron app we hit the engine directly on localhost.
import { io, Socket } from "socket.io-client";
import type {
  AuditEvent,
  CaseSummary,
  DeviceListing,
  Flag,
  Health,
  ManifestRecord,
  Message,
  Progress,
} from "./types";

const BASE = import.meta.env.DEV ? "" : "http://127.0.0.1:5057";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(`${path} → HTTP ${res.status}`);
  return res.json() as Promise<T>;
}

export const api = {
  health: () => get<Health>("/api/health"),
  devices: () => get<DeviceListing>("/api/devices"),
  cases: () => get<{ case_id: string; examiner: string; created_at: string; device: string }[]>("/api/cases"),
  caseOverview: (id: string) => get<CaseSummary>(`/api/case/${id}`),
  dataset: <T>(id: string, name: string) => get<T>(`/api/case/${id}/${name}`),
  manifest: (id: string) => get<ManifestRecord[]>(`/api/case/${id}/manifest`),
  audit: (id: string) => get<AuditEvent[]>(`/api/case/${id}/audit`),
  tags: (id: string) => get<import("./types").Tag[]>(`/api/case/${id}/tags`),
  reportUrl: (id: string) => `${BASE}/api/case/${id}/report`,
  mediaUrl: (id: string, artifactId: string) => `${BASE}/api/case/${id}/media/${artifactId}`,
  exportUrl: (id: string) => `${BASE}/api/case/${id}/export/download`,

  addTag: async (id: string, body: { ref: string; kind: string; label: string; note?: string }) => {
    const res = await fetch(`${BASE}/api/case/${id}/tags`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json() as Promise<import("./types").Tag>;
  },
  removeTag: async (id: string, tagId: string) => {
    await fetch(`${BASE}/api/case/${id}/tags/${tagId}`, { method: "DELETE" });
  },

  acquire: async (body: {
    mock?: string;
    serial?: string;
    case_id?: string;
    examiner: string;
    authority?: string;
    scope?: string;
    tier1_contacts?: boolean;
  }): Promise<{ case_id: string; started: boolean }> => {
    const res = await fetch(`${BASE}/api/acquire`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
      throw new Error(err.error || `HTTP ${res.status}`);
    }
    return res.json();
  },
};

let socket: Socket | null = null;
export function getSocket(): Socket {
  if (!socket) {
    socket = io(BASE || "/", { transports: ["websocket", "polling"] });
  }
  return socket;
}

export type { Progress };
