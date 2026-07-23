// Shared types mirroring the engine's derived JSON datasets.

export type Confidence = "live" | "recovered" | "carved" | "deletion";
export type Severity = "critical" | "warn" | "info";

export interface DeviceInfo {
  manufacturer: string;
  brand: string;
  model: string;
  android_version: string;
  sdk: string;
  build_id: string;
  serial: string;
  imei: string;
  carrier: string;
  rooted: boolean;
}

export interface Health {
  tool: string;
  version: string;
  disclaimer: string;
  adb: boolean;
  running: boolean;
}

export interface DeviceListing {
  real: { serial: string; state: string }[];
  mock: { id: string; kind: string; label: string }[];
}

export interface RiskReason {
  points: number;
  label: string;
  detail: string;
  severity: Severity;
}

export interface Risk {
  level: "red" | "amber" | "green";
  score: number;
  headline: string;
  reasons: RiskReason[];
  disclaimer: string;
}

export interface Throughput {
  pulled_bytes: number;
  pull_seconds: number;
  mb_per_min: number;
  files: number;
}

export interface GraphStats {
  participants: number;
  interactions: number;
  channels: string[];
  top_contacts: { label: string; weight: number; channels: string[] }[];
}

export interface CaseSummary {
  case: {
    case_id: string;
    examiner: string;
    legal_authority: string;
    scope_note: string;
    created_at: string;
    device: DeviceInfo;
    pre_state: Record<string, unknown>;
  };
  disclaimer: string;
  standards: string[];
  artifact_count: number;
  total_bytes: number;
  audit_event_count: number;
  device_altering_actions: number;
  counts: Record<string, number>;
  risk: Risk;
  throughput: Throughput;
  graph_stats: GraphStats;
  media_inventory_summary?: MediaInventorySummary;
  notable_apps?: InstalledApp[];
  discovered_chat_count?: number;
  tag_count: number;
  case_profile?: CaseProfile;
  ai_findings_summary?: Record<string, number>;
}

// -- Case-intelligence layer ------------------------------------------------

/** Canonical procedural role from the engine's forensic nomenclature vocabulary. */
export type RoleKey =
  | "accused" | "suspect" | "co_accused" | "absconder"
  | "victim" | "deceased" | "complainant" | "informant"
  | "witness" | "panch_witness" | "missing_person" | "third_party";

export interface RoleAssignment {
  name: string;
  role: RoleKey;
  label: string;
  /** True for accused / suspect / co-accused / absconder — people under investigation. */
  adverse: boolean;
  /** The phrase in the description that assigned this role. */
  evidence: string;
  confidence: number;
}

export interface NomenclatureWarning {
  term: string;
  severity: "warn" | "info";
  message: string;
}

export interface RoleDefinition {
  key: RoleKey;
  label: string;
  definition: string;
  adverse: boolean;
  aliases: string[];
}

export interface CaseProfile {
  description: string;
  case_number: string;
  crime_type: string;
  crime_label: string;
  suspects: string[];
  victims: string[];
  other_entities: string[];
  locations: string[];
  keywords: string[];
  timeframe: string | null;
  summary: string;
  extraction_method: string;
  confidence: number;
  roles: RoleAssignment[];
  nomenclature_warnings: NomenclatureWarning[];
}

export interface ArtifactPlan {
  artifact: string;
  label: string;
  priority: "high" | "medium" | "low";
  cost: "cheap" | "expensive";
  tier: string;
  collect: boolean;
  rationale: string;
  /** Belief breakdown — what each evidence source said before fusion. */
  doctrine_priority: "high" | "medium" | "low" | "";
  doctrine_score: number;
  precedent_score: number | null;
  learned_score: number | null;
  fused_score: number;
  evidence: string[];
  adjustment: "" | "promoted" | "demoted";
}

/** A prior case study retrieved as precedent for collection planning. */
export interface Precedent {
  case_number: string;
  title: string;
  crime_type: string;
  crime_match: boolean;
  score: number;
  lexical: number;
  matched_terms: string[];
  decisive_artifacts: string[];
  useless_artifacts: string[];
  outcome: string;
  lessons: string[];
  /** Provenance — synthetic exemplar vs a real worked case. */
  source: string;
}

export interface PlanRecommendation {
  artifact: string;
  label: string;
  current_priority: string;
  pipeline_flag: string | null;
  tier: string;
  cases: string[];
  reasons: string[];
  message: string;
  co_occurs_with?: { artifact: string; cases: number }[];
}

export interface CollectionPlan {
  crime_type: string;
  crime_label: string;
  artifacts: ArtifactPlan[];
  pipeline_overrides: Record<string, boolean>;
  extra_keywords: { term: string; severity: string; is_regex: boolean }[];
  deprioritised: { artifact: string; label: string; reason: string }[];
  notes: string[];
  rationale: string;
  precedents: Precedent[];
  similar_crime_types: {
    crime_type: string;
    label: string;
    similarity: number;
    shared_decisive_artifacts: string[];
  }[];
  knowledge_graph_stats: KnowledgeGraphStats;
  evidence_basis: "doctrine" | "doctrine+precedent" | "doctrine+observation" | "fused";
  estimated_savings: {
    deprioritised_artifacts: string[];
    estimated_minutes_saved: number;
    estimated_minutes_full_run: number;
    basis: string;
  };
  recommendations: PlanRecommendation[];
}

// -- Case bank (retrieval corpus) -------------------------------------------
export type ArtifactYield = "decisive" | "supporting" | "none";

export interface ArtifactOutcome {
  artifact: string;
  yield: ArtifactYield;
  note: string;
}

export interface CaseStudy {
  case_number: string;
  title: string;
  crime_type: string;
  description: string;
  roles: Record<string, string[]>;
  artifacts: ArtifactOutcome[];
  outcome: string;
  lessons: string[];
  source: string;
  decisive?: string[];
}

export interface CaseBankResponse {
  total: number;
  crime_type: string | null;
  studies: CaseStudy[];
  disclaimer: string;
}

export interface CaseBankSearchResponse {
  query: string;
  crime_type: string | null;
  total: number;
  results: Precedent[];
}

// -- Knowledge graph (learned artifact priors) ------------------------------
export interface KnowledgeGraphStats {
  cases_observed: number;
  distinct_cases: number;
  edges: number;
  observed_edges: number;
  crime_types: string[];
  well_observed_edges: number;
}

export interface ArtifactPrior {
  artifact: string;
  /** Raw learned posterior, or null when the link has never been observed. */
  posterior: number | null;
  /** 0..1 — how much the planner is allowed to trust the posterior. */
  strength: number;
  observations: number;
  doctrine: number;
  /** Posterior shrunk toward doctrine — what the planner actually uses. */
  blended: number;
  decisive: number;
  none: number;
  source: string;
}

export interface KnowledgeGraphView {
  crime_type: string;
  artifact_priors: Record<string, ArtifactPrior>;
  similar_crime_types: CollectionPlan["similar_crime_types"];
  stats: KnowledgeGraphStats;
  disclaimer: string;
}

export interface CaseLearning {
  recorded: boolean;
  grade?: "provisional" | "confirmed";
  weight?: number;
  case_number?: string;
  crime_type?: string;
  edges_updated?: number;
  yields?: Record<string, ArtifactYield>;
  note?: string;
  reason?: string;
}

export interface Finding {
  id: string;
  title: string;
  severity: "critical" | "high" | "medium" | "low" | "info";
  score: number;
  category: string;
  snippet: string;
  source_type: string;
  source_file: string;
  timestamp: string | null;
  confidence: Confidence;
  entities_matched: string[];
  keywords_matched: string[];
  rationale: string;
  requires_verification: boolean;
}

export interface AIFindings {
  generated_for: string;
  extraction_method: string;
  analysis_method: string;
  entities: string[];
  keyword_patterns: string[];
  counts: Record<string, number>;
  findings: Finding[];
  narrative: string;
  disclaimer: string;
}

export interface PlanResponse {
  profile: CaseProfile;
  plan: CollectionPlan;
  provider: string;
  case_bank_size: number;
}

export interface NomenclatureCheckResponse {
  roles: RoleAssignment[];
  warnings: NomenclatureWarning[];
}

export interface OutcomeResponse {
  case_id: string;
  learning: CaseLearning;
  promoted_to_case_bank: CaseStudy | null;
  corpus_size: number;
  graph_stats: KnowledgeGraphStats;
}

export interface GraphNode {
  id: string;
  label: string;
  type: string;
  weight: number;
  channels: string[];
}

export interface GraphEdge {
  source: string;
  target: string;
  weight: number;
  channels: string[];
}

export interface CommunicationGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
  stats: GraphStats;
}

export interface BrowserEntry {
  url: string;
  title: string;
  visit_count: number;
  last_visit: string | null;
  source_file: string;
}

export interface Screenshot {
  artifact_id: string;
  stored_path: string;
  sha256: string;
  captured_at: string;
}

export interface Tag {
  id: string;
  ref: string;
  kind: string;
  label: string;
  note: string;
  by: string;
  at: string;
}

export interface Message {
  app: string;
  sender: string;
  body: string;
  timestamp: string | null;
  direction: string;
  confidence: Confidence;
  source_file: string;
  provenance: string;
  flags: string[];
}

export interface Contact {
  name: string;
  number: string;
  email: string;
  confidence: Confidence;
  source_file: string;
}

export interface CallRecord {
  number: string;
  name: string;
  call_type: string;
  timestamp: string | null;
  duration_s: number | null;
  confidence: Confidence;
  source_file: string;
}

export interface LocationPoint {
  latitude: number;
  longitude: number;
  source: string;
  timestamp: string | null;
  label: string;
  source_file: string;
}

export interface MediaItem {
  artifact_id: string;
  stored_path: string;
  kind: string;
  size_bytes: number;
  app: string | null;
  trashed: boolean;
  timestamp: string | null;
  gps: { lat: number; lon: number } | null;
  sha256: string;
}

export interface RecoveredRow {
  values: (string | number | null | { __blob__: string; len: number })[];
  confidence: Confidence;
  source_file: string;
  provenance: string;
  rowid: number | null;
  page: number | null;
  offset: number | null;
  warnings: string[];
  database_artifact?: string;
}

export interface Flag {
  kind: string;
  term: string;
  context: string;
  location: string;
  severity: Severity;
}

export interface TimelineEvent {
  timestamp: string;
  kind: string;
  summary: string;
  confidence: Confidence;
  ref: string;
}

export interface ManifestRecord {
  artifact_id: string;
  source_path: string;
  stored_path: string;
  size_bytes: number;
  sha256: string;
  md5: string;
  tier: string;
  method: string;
  extracted_at: string;
  category: string;
  app: string | null;
  flags: string[];
}

export interface AuditEvent {
  timestamp: string;
  action: string;
  detail: string;
  examiner: string;
  command: string;
  result: string;
  alters_device: boolean;
  tier: string | null;
  extra: Record<string, unknown>;
}

export interface Progress {
  stage: string;
  pct: number;
  detail: string;
  case_id: string;
}

// --- Telegram deep-recovery types ------------------------------------------

export interface TelegramMessage {
  body: string;
  sender_name: string;
  sender_id: string;
  timestamp: string | null;
  confidence: Confidence;
  media_artifact_id: string | null;
  carve_method: string;
  provenance: string;
}

export interface TelegramParticipant {
  id: string;
  name: string;
  confidence: string;
}

export interface TelegramConversation {
  chat_id: string;
  title: string;
  participants: TelegramParticipant[];
  last_message_ts: string | null;
  message_count: number;
  messages: TelegramMessage[];
}

/** The full telegram_conversations.json shape: a dict keyed by chat_id. */
export type TelegramConversationsMap = Record<string, TelegramConversation>;

export interface TelegramMediaItem {
  artifact_id: string;
  source_path: string;
  rel_path: string;
  size_bytes: number;
  sha256: string;
  parent_message_ts: string | null;
  confidence: Confidence;
}

// --- Expanded Tier-1 Collector datasets ------------------------------------

export interface InstalledApp {
  package: string;
  label: string;
  version_name: string;
  version_code: number;
  first_install: string | null;
  last_update: string | null;
  installer: string;
  is_system: boolean;
  category: string;
  friendly_name: string | null;
  notable: boolean;
  dangerous_granted: string[];
  permission_count: number;
  source_file: string;
}

export interface Account {
  name: string;
  type: string;
  app: string | null;
  source_file: string;
}

export interface CalendarEvent {
  title: string;
  dtstart: string | null;
  dtend: string | null;
  location: string;
  description: string;
  organizer: string;
  calendar: string;
  all_day: boolean;
  source_file: string;
}

export interface AppUsage {
  package: string;
  total_foreground_ms: number;
  total_foreground_min: number;
  last_used: string | null;
  friendly_name: string | null;
  category: string;
  source_file: string;
}

export interface MediaInventoryItem {
  media_id: number;
  kind: string;
  display_name: string;
  mime_type: string;
  size_bytes: number;
  date_taken: string | null;
  date_added: string | null;
  date_modified: string | null;
  width: number;
  height: number;
  duration_ms: number;
  bucket: string;
  owner_package: string;
  owner_app: string | null;
  relative_path: string;
  data_path: string;
  is_trashed: boolean;
  is_favorite: boolean;
  is_pending: boolean;
  gps: { lat: number; lon: number } | null;
  source_file: string;
}

export interface MediaInventorySummary {
  total: number;
  by_kind: Record<string, number>;
  by_app: Record<string, number>;
  trashed: number;
  favorite: number;
  with_gps: number;
  total_bytes: number;
}

// --- App-chat recovery (Instagram / Snapchat / generic Dynamic App Finder) ---

export interface ChatMessage {
  body: string;
  sender_name: string;
  sender_id: string;
  timestamp: string | null;
  confidence: Confidence;
  media_artifact_id: string | null;
  carve_method: string;
  provenance: string;
}

export interface ChatConversation {
  chat_id: string;
  title: string;
  participants: { id: string; name: string }[];
  last_message_ts: string | null;
  message_count: number;
  messages: ChatMessage[];
}

export type ChatConversationsMap = Record<string, ChatConversation>;

export interface DiscoveredChatTable {
  db: string;
  table: string;
  live: number;
  recovered: number;
  roles: { text: string | null; timestamp: string | null; sender: string | null; thread: string | null };
}

export interface DiscoveredChats {
  tables: DiscoveredChatTable[];
  messages: (ChatMessage & { app?: string; source_file?: string })[];
  counts?: Record<string, number>;
}

// --- Wi-Fi credential recovery (Tier 2) ------------------------------------

export interface WifiNetwork {
  ssid: string;
  password: string;
  security: string;          // WPA/WPA2 | WPA3 | WEP | OPEN | …
  timestamp: string | null;
  confidence: Confidence;
  source_file: string;
}

// --- MediaStore trash (deleted / pending media, non-root recovery) ---------

export interface MediaStoreTrashItem {
  original_name: string;
  state: "trashed" | "pending";
  kind: string;
  owner_app: string | null;
  size_bytes: number;
  media_id: number | null;
  /** True when the actual file was pulled — we hold the bytes. */
  file_recoverable: boolean;
  artifact_id: string | null;
  stored_path: string | null;
  sha256: string | null;
  /** MediaStore auto-purge time (ISO). */
  expires_at: string | null;
  /** date_expires minus the 30-day window — when the user trashed it. */
  estimated_deleted_at: string | null;
  deleted_at_is_estimate: boolean;
  retention_window_days: number;
  days_until_auto_purge: number | null;
  gps: { lat: number; lon: number } | null;
  confidence: Confidence;
  /** Where the evidence came from. */
  source: "mediastore" | "filesystem" | "both";
  note: string;
  provenance: string;
  requires_verification: boolean;
}

export interface MediaStoreTrashSummary {
  total: number;
  trashed: number;
  pending: number;
  file_recovered: number;
  deletion_detected_only: number;
  recovered_bytes: number;
  expiring_within_3_days: number;
  note: string;
}

export interface MediaStoreTrash {
  items: MediaStoreTrashItem[];
  summary: MediaStoreTrashSummary;
}

// --- WhatsApp backup recovery (Tier 2) -------------------------------------

export interface WhatsAppBackupMessage {
  backup_file: string;       // source .crypt* filename
  chat_id: string;           // key_remote_jid / chat identifier
  sender: string;            // JID or "me"
  body: string;
  timestamp: string | null;
  media_type: string;        // image | video | audio | document | …
  media_path: string;        // relative path stored in the DB
  confidence: Confidence;
  source_file: string;       // decrypted DB filename
  provenance: string;        // e.g. "freelist page 4"
  flags: string[];
}

export interface WhatsAppBackupMedia {
  artifact_id: string;
  backup_message_id: string;
  file_name: string;
  file_path_on_device: string;
  size_bytes: number;
  sha256: string;
  recovered: boolean;        // true if pulled from a .trashed-* location
}

export interface WhatsAppBackupSummary {
  per_file: Record<string, {
    live: number;
    recovered: number;
    carved: number;
    deletion: number;
    total: number;
  }>;
  totals: {
    messages: number;
    media: number;
    live: number;
    recovered: number;
    carved: number;
    deletion: number;
  };
}
