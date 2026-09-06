// API client for the local Python engine. In dev, Vite proxies /api and /socket.io to
// :5057; in the packaged Electron app we hit the engine directly on localhost.
import { io, Socket } from "socket.io-client";
import type {
  AcqEvent,
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

// --- auth ---------------------------------------------------------------
// One examiner session at a time, held as a bearer token. Token lives in
// localStorage so a page reload doesn't force a re-login; the engine drops it
// on restart, so a stale token still gets rejected and onUnauthorized fires.
const TOKEN_KEY = "snagr_token";
let authToken: string | null = localStorage.getItem(TOKEN_KEY);

export function setAuthToken(token: string | null) {
  authToken = token;
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}
export function hasAuthToken(): boolean {
  return !!authToken;
}

// App registers this once, on mount, to fall back to the login screen the moment
// any request comes back 401 (expired session, engine restart, wrong token, ...).
let onUnauthorized: (() => void) | null = null;
export function setOnUnauthorized(fn: () => void) {
  onUnauthorized = fn;
}

function authHeaders(extra?: Record<string, string>): Record<string, string> {
  return authToken ? { ...extra, Authorization: `Bearer ${authToken}` } : { ...extra };
}

/** Shared fetch wrapper: attaches the bearer token, unwraps JSON, and routes 401s
 * to the login screen instead of letting every call site handle it separately. */
async function request<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...opts,
    headers: { ...authHeaders(opts.headers as Record<string, string> | undefined) },
  });
  if (res.status === 401) {
    setAuthToken(null);
    onUnauthorized?.();
    throw new Error("unauthorized");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error((data as { error?: string }).error || `${path} -> HTTP ${res.status}`);
  return data as T;
}

async function get<T>(path: string): Promise<T> {
  return request<T>(path);
}

export const api = {
  // API root — used by views that fetch directly (e.g. media thumbnails, conversation maps).
  base: `${BASE}/api`,

  // --- auth ---------------------------------------------------------------
  login: async (username: string, password: string) => {
    const res = await fetch(`${BASE}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error((data as { error?: string }).error || "invalid credentials");
    const token = (data as { token: string }).token;
    setAuthToken(token);
    return data as { token: string; expires_in: number; username: string };
  },
  logout: async () => {
    try {
      await request("/api/auth/logout", { method: "POST" });
    } finally {
      setAuthToken(null);
    }
  },
  /** Confirms a stored token is still accepted by the (possibly restarted) engine. */
  me: () => get<{ username: string }>("/api/auth/me"),

  /** Which case-intelligence back-ends this workstation can actually use, live. */
  llmStatus: (refresh = false) =>
    get<import("./types").LlmStatus>(`/api/llm/status${refresh ? "?refresh=1" : ""}`),
  health: () => get<Health>("/api/health"),
  devices: () => get<DeviceListing>("/api/devices"),
  /** Connection state + Developer-Options/USB-debugging checklist for one device —
   * the dashboard's only entry point for this; there is no CLI step an examiner needs
   * to remember. `brand` is optional when a device is already answering (the engine
   * reads its own brand); pass it to get a checklist before a device is even visible. */
  checkDevice: (opts?: { serial?: string; brand?: string }) => {
    const params = new URLSearchParams();
    if (opts?.serial) params.set("serial", opts.serial);
    if (opts?.brand) params.set("brand", opts.brand);
    const qs = params.toString();
    return get<import("./types").DeviceCheckResponse>(`/api/devices/check${qs ? `?${qs}` : ""}`);
  },
  /** STATE-CHANGING: re-enable Developer Options + USB debugging via `settings put
   * global`. Only works once adb shell access already exists — see
   * triage.preflight — so this button only ever appears once a device reads "ready". */
  reassertDevOptions: (serial?: string) =>
    request<import("./types").ReassertDevOptionsResponse>("/api/devices/reassert-dev-options", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ serial }),
    }),
  cases: () => get<{ case_id: string; examiner: string; created_at: string; device: string }[]>("/api/cases"),
  caseOverview: (id: string) => get<CaseSummary>(`/api/case/${id}`),
  dataset: <T>(id: string, name: string) => get<T>(`/api/case/${id}/${name}`),
  /** Authed conversation-map fetch. Every chat view goes through here — a raw
   * `fetch` skips the bearer token and the engine answers 401, which used to render
   * as a silently-empty Snapchat/Instagram/Telegram tab. */
  conversations: (id: string, dataset: string) =>
    get<import("./types").ChatConversationsMap>(`/api/case/${id}/${dataset}`),
  telegramConversations: (id: string) =>
    get<import("./types").TelegramConversationsMap>(`/api/case/${id}/telegram/conversations`),
  telegramPresence: (id: string) =>
    get<import("./types").TelegramPresence>(`/api/case/${id}/telegram_presence`),
  whatsappBackupMessages: (id: string) =>
    get<import("./types").WhatsAppBackupMessage[]>(`/api/case/${id}/whatsapp_backup/messages`),
  whatsappBackupSummary: (id: string) =>
    get<import("./types").WhatsAppBackupSummary>(`/api/case/${id}/whatsapp_backup/summary`),
  whatsappBackupMedia: (id: string) =>
    get<import("./types").WhatsAppBackupMedia[]>(`/api/case/${id}/whatsapp_backup/media`),
  discoveredChats: (id: string) =>
    get<import("./types").DiscoveredChats>(`/api/case/${id}/discovered_chats`),
  /** Per-dataset collection state for a case — see lib/capabilities.tsx. */
  capabilities: (id: string) =>
    get<import("./types").CaseCapabilities>(`/api/case/${id}/capabilities`),
  manifest: (id: string) => get<ManifestRecord[]>(`/api/case/${id}/manifest`),
  audit: (id: string) => get<AuditEvent[]>(`/api/case/${id}/audit`),
  tags: (id: string) => get<import("./types").Tag[]>(`/api/case/${id}/tags`),
  reportUrl: (id: string) => `${BASE}/api/case/${id}/report`,
  regenerateReport: (id: string) =>
    request<{ ok: boolean; error?: string }>(`/api/case/${id}/report/regenerate`, { method: "POST" }),
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
  deleteCase: (id: string) => request<{ deleted: string }>(`/api/case/${id}`, { method: "DELETE" }),

  addTag: (id: string, body: { ref: string; kind: string; label: string; note?: string }) =>
    request<import("./types").Tag>(`/api/case/${id}/tags`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  removeTag: (id: string, tagId: string) =>
    request(`/api/case/${id}/tags/${tagId}`, { method: "DELETE" }),

  // Ingest an Instagram/Snapchat "Download Your Data" export or a Telegram Desktop
  // "Export Telegram data" (ZIP/JSON) into a case — the non-root acquisition path.
  importExport: (id: string, app: "instagram" | "snapchat" | "telegram", file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<{ imported: number; total: number; counts?: Record<string, number> }>(
      `/api/case/${id}/import/${app}`,
      { method: "POST", body: form }
    );
  },

  // Case-intelligence: preview a targeted collection plan from a plain-language brief.
  plan: (
    description: string,
    opts?: {
      llm_provider?: string;
      allow_tier2?: boolean;
      case_number?: string;
      /** Set false to preview the pure-ontology plan with no retrieval or learning. */
      use_case_bank?: boolean;
    }
  ) =>
    request<import("./types").PlanResponse>("/api/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ description, ...opts }),
    }),

  /** Validate a draft description's forensic nomenclature before acquiring. */
  checkNomenclature: (description: string) =>
    request<import("./types").NomenclatureCheckResponse>("/api/nomenclature/check", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ description }),
    }),

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

  addCaseStudy: (study: Partial<import("./types").CaseStudy>) =>
    request<{ added: string; corpus_size: number; graph_edges_updated: number }>("/api/casebank", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(study),
    }),

  // --- knowledge graph (learned artifact priors) ----------------------------
  knowledgeGraph: (crimeType: string) =>
    get<import("./types").KnowledgeGraphView>(
      `/api/knowledge-graph?crime_type=${encodeURIComponent(crimeType)}`
    ),

  /** Record the examiner's confirmed outcome — what actually solved the case. */
  recordOutcome: (
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
  ) =>
    request<import("./types").OutcomeResponse>(`/api/case/${id}/outcome`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  // Case-intelligence: (re-)run the AI findings analysis over a collected case.
  analyze: (id: string, body?: { description?: string; llm_provider?: string }) =>
    request<import("./types").AIFindings>(`/api/case/${id}/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    }),

  // Deep investigation: bounded, deterministic hypothesis pass cross-linking findings
  // (run automatically during acquisition; this re-runs it on demand).
  investigate: (id: string, body?: { llm_provider?: string }) =>
    request<import("./types").InvestigationTrace>(`/api/case/${id}/investigate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    }),

  // AI Evidence Summary: (re-)generate the entirely model-authored narrative digest
  // scoped to entity+yield-matched findings only — see triage/intel/ai_summary.py.
  // Requires a case profile and ai_findings (run analyze() first); the persisted
  // bundle is otherwise read like any sibling dataset via `api.dataset(id, "ai_evidence_summary")`.
  summarizeCase: (id: string, body?: { llm_provider?: string }) =>
    request<import("./types").AiEvidenceSummary>(`/api/case/${id}/summarize`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    }),

  // "Ask this case" — free-text Q&A over the case's own already-collected evidence.
  askCase: (
    id: string,
    question: string,
    opts?: { llm_provider?: string; top_k?: number; use_embeddings?: boolean }
  ) =>
    request<import("./types").AskCaseResponse>(`/api/case/${id}/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, ...opts }),
    }),

  // Other cases on this installation sharing a phone number / UPI ID / email.
  linkedCases: (id: string) =>
    get<import("./types").LinkedCasesResponse>(`/api/case/${id}/linked-cases`),

  acquire: (body: {
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
    /** Opt-in: generate the AI Evidence Summary after analysis. Needs a reachable
     * local model — see /api/llm/status — or this stays honestly empty. */
    run_ai_summary?: boolean;
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
  }) =>
    request<{ case_id: string; started: boolean }>("/api/acquire", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  caseActivity: (caseId: string) =>
    get<{ events: AcqEvent[] }>(`/api/cases/${caseId}/activity`),
};

let socket: Socket | null = null;
export function getSocket(): Socket {
  if (!socket) {
    socket = io(BASE || "/", { transports: ["websocket", "polling"] });
  }
  return socket;
}
