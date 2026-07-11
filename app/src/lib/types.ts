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
  tag_count: number;
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
