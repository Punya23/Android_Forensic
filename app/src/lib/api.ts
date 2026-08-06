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
  RegistryCasesResponse,
  ReportVersion,
} from "./types";

export const BASE = import.meta.env.DEV ? "" : "http://127.0.0.1:5057";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(`${path} → HTTP ${res.status}`);
  return res.json() as Promise<T>;
}

export const api = {
  // API root — used by views that fetch directly (e.g. media thumbnails, conversation maps).
  base: `${BASE}/api`,
  health: () => get<Health>("/api/health"),
  devices: () => get<DeviceListing>("/api/devices"),
  cases: () => get<{ case_id: string; examiner: string; created_at: string; device: string }[]>("/api/cases"),
  caseOverview: (id: string) => get<CaseSummary>(`/api/case/${id}`),
  dataset: <T>(id: string, name: string) => get<T>(`/api/case/${id}/${name}`),
  manifest: (id: string) => get<ManifestRecord[]>(`/api/case/${id}/manifest`),
  audit: (id: string) => get<AuditEvent[]>(`/api/case/${id}/audit`),
  tags: (id: string) => get<import("./types").Tag[]>(`/api/case/${id}/tags`),
  reportUrl: (id: string) => `${BASE}/api/case/${id}/report`,
  regenerateReport: async (id: string): Promise<{ ok: boolean; error?: string }> => {
    const res = await fetch(`${BASE}/api/case/${id}/report/regenerate`, { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error((data as { error?: string }).error || `HTTP ${res.status}`);
    return data as { ok: boolean };
  },
  mediaUrl: (id: string, artifactId: string) => `${BASE}/api/case/${id}/media/${artifactId}`,
  exportUrl: (id: string) => `${BASE}/api/case/${id}/export/download`,

  // --- case registry (cross-case history, SQLite-backed) --------------------
  registryCases: (opts?: { q?: string; sort?: string; limit?: number }) => {
    const params = new URLSearchParams();
    if (opts?.q) params.set("q", opts.q);
    if (opts?.sort) params.set("sort", opts.sort);
    if (opts?.limit) params.set("limit", String(opts.limit));
    const qs = params.toString();
    return get<RegistryCasesResponse>(`/api/registry/cases${qs ? `?${qs}` : ""}`);
  },
  caseReports: (id: string) => get<ReportVersion[]>(`/api/case/${id}/reports`),
  reportSnapshotUrl: (id: string, path: string) =>
    `${BASE}/api/case/${id}/reports/${path.split("/").pop()}`,
  deleteCase: async (id: string): Promise<{ deleted: string }> => {
    const res = await fetch(`${BASE}/api/case/${id}`, { method: "DELETE" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error((data as { error?: string }).error || `HTTP ${res.status}`);
    return data as { deleted: string };
  },

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

  // Ingest an Instagram/Snapchat "Download Your Data" export or a Telegram Desktop
  // "Export Telegram data" (ZIP/JSON) into a case — the non-root acquisition path.
  importExport: async (
    id: string,
    app: "instagram" | "snapchat" | "telegram",
    file: File
  ): Promise<{ imported: number; total: number; counts?: Record<string, number> }> => {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`${BASE}/api/case/${id}/import/${app}`, { method: "POST", body: form });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error((data as { error?: string }).error || `HTTP ${res.status}`);
    return data as { imported: number; total: number; counts?: Record<string, number> };
  },

  // Case-intelligence: preview a targeted collection plan from a plain-language brief.
  plan: async (
    description: string,
    opts?: {
      llm_provider?: string;
      allow_tier2?: boolean;
      case_number?: string;
      /** Set false to preview the pure-ontology plan with no retrieval or learning. */
      use_case_bank?: boolean;
    }
  ): Promise<import("./types").PlanResponse> => {
    const res = await fetch(`${BASE}/api/plan`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ description, ...opts }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error((data as { error?: string }).error || `HTTP ${res.status}`);
    return data as import("./types").PlanResponse;
  },

  /** Validate a draft description's forensic nomenclature before acquiring. */
  checkNomenclature: async (
    description: string
  ): Promise<import("./types").NomenclatureCheckResponse> => {
    const res = await fetch(`${BASE}/api/nomenclature/check`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ description }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error((data as { error?: string }).error || `HTTP ${res.status}`);
    return data as import("./types").NomenclatureCheckResponse;
  },

  nomenclature: () =>
    get<{ roles: import("./types").RoleDefinition[]; note: string }>("/api/nomenclature"),

  // --- case bank (retrieval corpus) -----------------------------------------
  caseBank: (crimeType?: string) =>
    get<import("./types").CaseBankResponse>(
      `/api/casebank${crimeType ? `?crime_type=${encodeURIComponent(crimeType)}` : ""}`
    ),

  searchCaseBank: (q: string, crimeType?: string) =>
    get<import("./types").CaseBankSearchResponse>(
      `/api/casebank?q=${encodeURIComponent(q)}` +
        (crimeType ? `&crime_type=${encodeURIComponent(crimeType)}` : "")
    ),

  addCaseStudy: async (
    study: Partial<import("./types").CaseStudy>
  ): Promise<{ added: string; corpus_size: number; graph_edges_updated: number }> => {
    const res = await fetch(`${BASE}/api/casebank`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(study),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error((data as { error?: string }).error || `HTTP ${res.status}`);
    return data as { added: string; corpus_size: number; graph_edges_updated: number };
  },

  // --- knowledge graph (learned artifact priors) ----------------------------
  knowledgeGraph: (crimeType: string) =>
    get<import("./types").KnowledgeGraphView>(
      `/api/knowledge-graph?crime_type=${encodeURIComponent(crimeType)}`
    ),

  /** Record the examiner's confirmed outcome — what actually solved the case. */
  recordOutcome: async (
    id: string,
    body: {
      artifact_yields: Record<string, import("./types").ArtifactYield>;
      case_number?: string;
      examiner?: string;
      outcome?: string;
      lessons?: string[];
      notes?: Record<string, string>;
      add_to_case_bank?: boolean;
    }
  ): Promise<import("./types").OutcomeResponse> => {
    const res = await fetch(`${BASE}/api/case/${id}/outcome`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error((data as { error?: string }).error || `HTTP ${res.status}`);
    return data as import("./types").OutcomeResponse;
  },

  // Case-intelligence: (re-)run the AI findings analysis over a collected case.
  analyze: async (
    id: string,
    body?: { description?: string; llm_provider?: string }
  ): Promise<import("./types").AIFindings> => {
    const res = await fetch(`${BASE}/api/case/${id}/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error((data as { error?: string }).error || `HTTP ${res.status}`);
    return data as import("./types").AIFindings;
  },

  acquire: async (body: {
    mock?: string;
    serial?: string;
    case_id?: string;
    examiner: string;
    authority?: string;
    scope?: string;
    case_description?: string;
    case_number?: string;
    llm_provider?: string;
    use_case_bank?: boolean;
    /**
     * Whether the plan may switch on root-only (Tier-2) pulls. Collection scope is the
     * examiner's decision, so a case brief alone must not be able to widen it.
     */
    plan_allow_tier2?: boolean;
    /** Load this installation's own promoted cases as retrieval precedent. */
    use_local_corpus?: boolean;
    run_ai_analysis?: boolean;
    learn_from_case?: boolean;
    tier1_contacts?: boolean;
    tier1_calllog?: boolean;
    tier1_sms?: boolean;
    tier1_collect_all?: boolean;
    tier2_telegram?: boolean;
    tier2_instagram?: boolean;
    tier2_snapchat?: boolean;
    tier2_wifi?: boolean;
    tier2_browser_history?: boolean;
    tier2_whatsapp_backup?: boolean;
    // Deep system-artifact stages (root). Omitted => the engine's default (all off).
    tier2_bt_config?: boolean;
    tier2_app_presence?: boolean;
    tier2_antiforensics?: boolean;
    tier2_recent_tasks?: boolean;
    // Tier-0 stages, on by default in the engine; send false to opt out.
    wifi_live?: boolean;
    scan_encrypted_apps?: boolean;
    run_self_validation?: boolean;
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
