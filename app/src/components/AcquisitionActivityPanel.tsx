/**
 * AcquisitionActivityPanel
 *
 * Renders a live, filterable feed of acquisition activity events received over
 * Socket.IO. Each row maps to a real engine collection boundary — events are
 * never synthesised on the frontend.
 *
 * Status semantics (matches engine acq_activity.py):
 *   completed   – source was accessed and produced data
 *   skipped     – source was checked but not accessed (with a stated reason)
 *   failed      – an error occurred accessing this source
 *   accessing   – currently in progress (pulsing highlight)
 *   queued      – scheduled but not yet started
 *
 * "Not collected", "permission denied", and "checked but empty" are all
 * surfaced as distinct skip_reason values inside a "skipped" row — they are
 * never left as a blank panel or a false-negative silence.
 */

import { useMemo, useState } from "react";
import type { AcqEvent, AcqStatus } from "../lib/types";
import {
  Bluetooth,
  Box,
  Cpu,
  FileSearch,
  Folder,
  Globe2,
  Image,
  Layout,
  MapPin,
  MessageSquare,
  Monitor,
  Puzzle,
  Search,
  Shield,
  ShieldAlert,
  Sparkles,
  Terminal,
  Wifi,
  Bell,
} from "lucide-react";

// ---------------------------------------------------------------------------
// Source → icon component
// ---------------------------------------------------------------------------

/** Inline SVG brand icons for apps that don't have a Lucide equivalent. */
function TelegramIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden>
      <path d="M12 0C5.373 0 0 5.373 0 12s5.373 12 12 12 12-5.373 12-12S18.627 0 12 0zm5.894 8.221-1.97 9.28c-.145.658-.537.818-1.084.508l-3-2.21-1.447 1.394c-.16.16-.295.295-.605.295l.213-3.053 5.56-5.023c.242-.213-.054-.333-.373-.12l-6.871 4.326-2.962-.924c-.643-.204-.657-.643.136-.953l11.57-4.461c.537-.194 1.006.131.833.941z" />
    </svg>
  );
}

function WhatsAppIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden>
      <path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z" />
    </svg>
  );
}

function InstagramIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden>
      <path d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zM12 0C8.741 0 8.333.014 7.053.072 2.695.272.273 2.69.073 7.052.014 8.333 0 8.741 0 12c0 3.259.014 3.668.072 4.948.2 4.358 2.618 6.78 6.98 6.98C8.333 23.986 8.741 24 12 24c3.259 0 3.668-.014 4.948-.072 4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98C15.668.014 15.259 0 12 0zm0 5.838a6.162 6.162 0 100 12.324 6.162 6.162 0 000-12.324zM12 16a4 4 0 110-8 4 4 0 010 8zm6.406-11.845a1.44 1.44 0 100 2.881 1.44 1.44 0 000-2.881z" />
    </svg>
  );
}

function SnapchatIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden>
      <path d="M12.166.006C9.845-.085 5.68 1.016 3.66 5.123 2.802 6.81 2.57 8.685 2.65 9.97l-.005.101c-.124.05-.266.076-.415.076-.422 0-.847-.18-1.126-.351L1 9.734c-.056-.034-.12-.051-.19-.051a.38.38 0 00-.379.38c0 .157.094.301.242.36.696.264 1.498.61 1.692 1.113.017.045.026.093.026.142 0 .068-.018.138-.052.2-.19.348-.785.57-1.319.79-.17.068-.34.138-.497.21C.1 13.084 0 13.292 0 13.5c0 .264.17.488.423.562.047.015.1.022.152.022.123 0 .244-.031.351-.089.63-.339 1.273-.5 1.913-.5.25 0 .5.03.742.09.186.046.374.07.559.07.256 0 .512-.05.76-.153.118-.05.236-.116.35-.199-.102.45-.159.94-.159 1.46 0 1.7.607 3.135 1.727 4.151 1.22 1.107 2.97 1.773 4.825 1.773h.178c.567-.005 2.175-.073 3.573-.915a5.49 5.49 0 00.688-.515c.89.574 2.026.932 3.338 1.011.116.008.23.012.345.012 1.684 0 3.27-.64 4.342-1.756.964-1.002 1.457-2.33 1.457-3.76 0-.521-.057-1.012-.16-1.461a2.75 2.75 0 00.35.199c.248.103.504.153.76.153.185 0 .373-.024.56-.07.24-.06.49-.09.741-.09.64 0 1.283.161 1.913.5.107.058.228.09.35.09.053 0 .106-.008.153-.023.253-.074.423-.298.423-.562 0-.208-.1-.416-.523-.622a12.05 12.05 0 01-.496-.21c-.534-.22-1.13-.442-1.32-.79a.431.431 0 01-.05-.2c0-.049.008-.097.025-.142.194-.503.996-.849 1.692-1.113a.38.38 0 00.242-.36.38.38 0 00-.38-.38c-.07 0-.133.017-.19.051l-.103.062c-.28.172-.705.351-1.127.351-.148 0-.29-.027-.414-.076l-.005-.1c.08-1.287-.152-3.162-1.01-4.85C18.321 1.016 14.157-.085 12.166.006z" />
    </svg>
  );
}

function SignalIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden>
      <path d="M12 0C5.373 0 0 5.373 0 12s5.373 12 12 12 12-5.373 12-12S18.627 0 12 0zm0 21a9 9 0 110-18 9 9 0 010 18zm0-16a7 7 0 100 14A7 7 0 0012 5zm0 2a5 5 0 110 10A5 5 0 0112 7zm0 2a3 3 0 100 6 3 3 0 000-6z" />
    </svg>
  );
}

type IconKey = string;
function SourceIcon({ icon, size = 14 }: { icon: IconKey; size?: number }) {
  const cls = `shrink-0`;
  const props = { size, strokeWidth: 1.75, className: cls, "aria-hidden": true };
  switch (icon) {
    case "telegram":    return <TelegramIcon size={size} />;
    case "whatsapp":    return <WhatsAppIcon size={size} />;
    case "instagram":   return <InstagramIcon size={size} />;
    case "snapchat":    return <SnapchatIcon size={size} />;
    case "signal":      return <SignalIcon size={size} />;
    case "sms":         return <MessageSquare {...props} />;
    case "contacts":    return <MessageSquare {...props} />;
    case "calls":       return <Terminal {...props} />;
    case "bluetooth":   return <Bluetooth {...props} />;
    case "wifi":
    case "wifi_live":   return <Wifi {...props} />;
    case "browser":     return <Globe2 {...props} />;
    case "gallery":
    case "media":       return <Image {...props} />;
    case "folder":
    case "filesystem":  return <Folder {...props} />;
    case "screenshot":  return <Monitor {...props} />;
    case "bell":
    case "notifications": return <Bell {...props} />;
    case "device":      return <Cpu {...props} />;
    case "shield":      return <Shield {...props} />;
    case "shield_alert": return <ShieldAlert {...props} />;
    case "apps":        return <Puzzle {...props} />;
    case "layout":      return <Layout {...props} />;
    case "sparkles":    return <Sparkles {...props} />;
    case "tool":        return <Terminal {...props} />;
    case "search":      return <Search {...props} />;
    case "map_pin":
    case "celltower":   return <MapPin {...props} />;
    case "monitor":     return <Monitor {...props} />;
    default:            return <Box {...props} />;
  }
}

// ---------------------------------------------------------------------------
// Status badge
// ---------------------------------------------------------------------------

const STATUS_STYLES: Record<AcqStatus, string> = {
  completed: "bg-green-500/15 text-green-400 border-green-500/30",
  skipped:   "bg-amber-500/15 text-amber-400 border-amber-500/30",
  failed:    "bg-red-500/15 text-red-400 border-red-500/30",
  accessing: "bg-blue-500/15 text-blue-400 border-blue-500/30 animate-pulse",
  queued:    "bg-panel text-muted border-line",
};

function StatusBadge({ status }: { status: AcqStatus }) {
  return (
    <span
      className={`rounded border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${STATUS_STYLES[status]}`}
    >
      {status}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Tier badge
// ---------------------------------------------------------------------------

const TIER_LABEL: Record<string, string> = {
  tier0: "T0",
  tier1: "T1",
  tier2: "T2",
};
const TIER_STYLE: Record<string, string> = {
  tier0: "text-muted border-line",
  tier1: "text-accent border-accent/40",
  tier2: "text-warn border-warn/40",
};

function TierBadge({ tier }: { tier: string }) {
  return (
    <span
      className={`rounded border px-1 py-0.5 text-[9px] font-bold uppercase ${TIER_STYLE[tier] ?? "text-muted border-line"}`}
      title={`Acquisition ${tier}`}
    >
      {TIER_LABEL[tier] ?? tier}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Source label map
// ---------------------------------------------------------------------------

const SOURCE_LABELS: Record<string, string> = {
  telegram: "Telegram", whatsapp: "WhatsApp", instagram: "Instagram",
  snapchat: "Snapchat", signal: "Signal", sms: "SMS",
  contacts: "Contacts", calls: "Call Log", notifications: "Notifications",
  bluetooth: "Bluetooth", celltower: "Cell Tower", wifi: "Wi-Fi Credentials",
  wifi_live: "Wi-Fi Live", browser: "Browser", gallery: "Gallery",
  media: "Media", filesystem: "File System", screenshot: "Screenshot",
  device: "Device", encryption: "Encryption", bt_config: "BT Bond Store",
  app_presence: "App Presence", antiforensics: "Containers / Privacy",
  recent_tasks: "Recent Tasks", intel: "Intelligence", aleapp: "ALEAPP",
  recovery: "Deleted Records", screentime: "Screen Time",
  search: "Search History", maps: "Maps / Location", location: "Location",
};

// ---------------------------------------------------------------------------
// Single activity row
// ---------------------------------------------------------------------------

function ActivityRow({ ev, active }: { ev: AcqEvent; active: boolean }) {
  const ts = ev.timestamp ? ev.timestamp.replace("T", " ").replace("Z", " UTC") : "";
  const label = SOURCE_LABELS[ev.source] ?? ev.source;

  return (
    <div
      className={`group flex items-start gap-2.5 px-3 py-2 border-b border-line last:border-b-0 transition-colors ${
        active ? "bg-blue-500/8 border-l-2 border-l-blue-500/50" : "hover:bg-panel/50"
      }`}
      title={ev.artifact_path ? `Path: ${ev.artifact_path}` : undefined}
    >
      {/* Icon */}
      <div className="mt-0.5 text-muted shrink-0">
        <SourceIcon icon={ev.icon} size={14} />
      </div>

      {/* Main content */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5 flex-wrap">
          <TierBadge tier={ev.tier} />
          <span className="text-xs font-semibold">{label}</span>
          <StatusBadge status={ev.status} />
          {ev.item_count != null && (
            <span className="text-[10px] text-muted tabular-nums">{ev.item_count.toLocaleString()} item{ev.item_count !== 1 ? "s" : ""}</span>
          )}
        </div>
        <div className="text-[11px] text-muted mt-0.5 leading-snug">{ev.action}</div>
        {ev.skip_reason && (
          <div className="text-[10px] text-amber-400/80 mt-0.5 leading-snug">
            ↳ {ev.skip_reason}
          </div>
        )}
        {ev.artifact_path && (
          <div
            className="text-[10px] font-mono text-muted/60 mt-0.5 truncate max-w-xs group-hover:text-muted/90 transition-colors"
            title={ev.artifact_path}
          >
            {ev.artifact_path}
          </div>
        )}
      </div>

      {/* Timestamp — right-aligned */}
      <div className="shrink-0 text-[10px] font-mono text-muted/50 mt-0.5 whitespace-nowrap">
        {ts.split(" ")[1] ?? ts}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Filter bar
// ---------------------------------------------------------------------------

const ALL_STATUSES: AcqStatus[] = ["accessing", "completed", "skipped", "failed", "queued"];
const ALL_TIERS = ["tier0", "tier1", "tier2"];

// ---------------------------------------------------------------------------
// Main panel
// ---------------------------------------------------------------------------

export interface AcquisitionActivityPanelProps {
  events: AcqEvent[];
  /** When true the panel shows "Live" indicator and auto-scrolls. */
  live?: boolean;
  maxHeight?: string;
}

export function AcquisitionActivityPanel({
  events,
  live = false,
  maxHeight = "420px",
}: AcquisitionActivityPanelProps) {
  const [statusFilter, setStatusFilter] = useState<AcqStatus | "all">("all");
  const [tierFilter, setTierFilter] = useState<string>("all");
  const [sourceFilter, setSourceFilter] = useState<string>("all");

  // Derive available source options from received events
  const availableSources = useMemo(() => {
    const keys = [...new Set(events.map((e) => e.source))];
    return keys.sort((a, b) => (SOURCE_LABELS[a] ?? a).localeCompare(SOURCE_LABELS[b] ?? b));
  }, [events]);

  // Filter
  const filtered = useMemo(() => {
    return events.filter((e) => {
      if (statusFilter !== "all" && e.status !== statusFilter) return false;
      if (tierFilter !== "all" && e.tier !== tierFilter) return false;
      if (sourceFilter !== "all" && e.source !== sourceFilter) return false;
      return true;
    });
  }, [events, statusFilter, tierFilter, sourceFilter]);

  // Counts for filter badges
  const counts = useMemo(() => {
    const acc: Record<string, number> = { all: events.length };
    for (const ev of events) acc[ev.status] = (acc[ev.status] ?? 0) + 1;
    return acc;
  }, [events]);

  const activeSource = [...events].reverse().find((e: AcqEvent) => e.status === "accessing")?.source ?? null;

  if (events.length === 0) {
    return (
      <div className="card p-4 text-sm text-muted text-center">
        <FileSearch className="mx-auto mb-2 h-6 w-6 opacity-40" strokeWidth={1.5} aria-hidden />
        <p>Waiting for acquisition to begin…</p>
        <p className="text-xs mt-1 opacity-60">
          Activity events will appear here as the engine accesses each data source.
        </p>
      </div>
    );
  }

  return (
    <div className="card overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-line bg-panel/50">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold">Acquisition Activity</span>
          {live && (
            <span className="flex items-center gap-1 text-[10px] text-green-400 font-semibold">
              <span className="h-1.5 w-1.5 rounded-full bg-green-400 animate-pulse" />
              LIVE
            </span>
          )}
          <span className="text-[10px] text-muted tabular-nums">{filtered.length} / {events.length}</span>
        </div>
        {activeSource && (
          <span className="text-[10px] text-blue-400 animate-pulse">
            Accessing: {SOURCE_LABELS[activeSource] ?? activeSource}
          </span>
        )}
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-1.5 px-3 py-2 border-b border-line bg-panel/30">
        {/* Status chips */}
        {(["all", ...ALL_STATUSES] as const).map((s) => (
          <button
            key={s}
            onClick={() => setStatusFilter(s as AcqStatus | "all")}
            className={`text-[10px] rounded border px-1.5 py-0.5 uppercase font-semibold transition-colors ${
              statusFilter === s
                ? s === "all"
                  ? "bg-accent/20 border-accent/50 text-accent"
                  : STATUS_STYLES[s as AcqStatus]
                : "border-line text-muted hover:border-muted"
            }`}
          >
            {s === "all" ? `All (${counts.all})` : `${s} (${counts[s] ?? 0})`}
          </button>
        ))}

        <span className="w-px bg-line self-stretch" />

        {/* Tier chips */}
        {(["all", ...ALL_TIERS] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTierFilter(t)}
            className={`text-[10px] rounded border px-1.5 py-0.5 uppercase font-semibold transition-colors ${
              tierFilter === t
                ? "bg-accent/20 border-accent/50 text-accent"
                : "border-line text-muted hover:border-muted"
            }`}
          >
            {t === "all" ? "All tiers" : TIER_LABEL[t]}
          </button>
        ))}

        {/* Source select */}
        {availableSources.length > 1 && (
          <select
            value={sourceFilter}
            onChange={(e) => setSourceFilter(e.target.value)}
            className="text-[10px] rounded border border-line bg-panel text-muted px-1.5 py-0.5 ml-auto"
            aria-label="Filter by source"
          >
            <option value="all">All sources</option>
            {availableSources.map((s) => (
              <option key={s} value={s}>{SOURCE_LABELS[s] ?? s}</option>
            ))}
          </select>
        )}
      </div>

      {/* Event list */}
      <div
        className="overflow-y-auto divide-y divide-line"
        style={{ maxHeight }}
        aria-live={live ? "polite" : undefined}
        aria-label="Acquisition activity feed"
      >
        {filtered.length === 0 ? (
          <div className="px-3 py-6 text-xs text-muted text-center">
            No events match the current filters.
          </div>
        ) : (
          filtered.map((ev) => (
            <ActivityRow key={ev.id} ev={ev} active={ev.status === "accessing"} />
          ))
        )}
      </div>

      {/* Footer — forensic data integrity notice */}
      <div className="px-3 py-1.5 border-t border-line bg-panel/30 text-[9px] text-muted/60 leading-snug">
        Events emitted only at real engine collection boundaries.
        "Skipped" = source checked, not accessed.
        "Failed" = access attempted, error occurred.
        Artifact paths are redacted; no credentials or message content are shown.
      </div>
    </div>
  );
}
