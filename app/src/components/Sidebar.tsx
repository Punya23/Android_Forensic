import type { Health } from "../lib/types";
import { useCapabilities } from "../lib/capabilities";

export type ViewKey =
  | "acquire"
  | "cases"
  | "overview"
  | "intel"
  | "knowledge"
  | "messages"
  | "contacts"
  | "calls"
  | "notifications"
  | "media"
  | "mediainv"
  | "deletedmedia"
  | "telegram"
  | "whatsapp_backup"
  | "instagram"
  | "snapchat"
  | "apps"
  | "accounts"
  | "calendar"
  | "wifi"
  | "wifi_live"
  | "bluetooth"
  | "celltower"
  | "screentime"
  | "search"
  | "gaccounts"
  | "locations"
  | "loctrace"
  | "browser"
  | "timeline"
  | "recovered"
  | "discovered"
  | "graph"
  | "advanced"
  | "tagged"
  | "apppresence"
  | "antiforensics"
  | "recenttasks"
  | "encryptedapps"
  | "aleapp"
  | "encryption"
  | "devicestate"
  | "validation"
  | "custody"
  | "report";

const NAV: { key: ViewKey; label: string; icon: string; group?: string }[] = [
  { key: "cases", label: "Case History", icon: "🗄" },
  { key: "overview", label: "Overview", icon: "▤" },
  { key: "intel", label: "Case Intelligence", icon: "✦" },
  { key: "messages", label: "Messages", icon: "💬", group: "Communications" },
  { key: "telegram", label: "Telegram", icon: "✈" },
  { key: "whatsapp_backup", label: "WA Backup Recovery", icon: "🔓" },
  { key: "instagram", label: "Instagram", icon: "📷" },
  { key: "snapchat", label: "Snapchat", icon: "👻" },
  { key: "discovered", label: "Discovered Chats", icon: "🔎" },
  { key: "contacts", label: "Contacts", icon: "👤" },
  { key: "calls", label: "Calls", icon: "📞" },
  { key: "notifications", label: "Notifications", icon: "🔔" },
  { key: "media", label: "Media", icon: "🖼", group: "Device" },
  { key: "mediainv", label: "Media Inventory", icon: "🗂" },
  { key: "deletedmedia", label: "Deleted Media", icon: "🗑" },
  { key: "apps", label: "Installed Apps", icon: "📦" },
  { key: "accounts", label: "Accounts", icon: "🔑" },
  { key: "calendar", label: "Calendar", icon: "📅" },
  { key: "wifi", label: "Wi-Fi Passwords", icon: "📶" },
  { key: "screentime", label: "Screen & App Usage", icon: "⏳" },
  { key: "search", label: "Search History", icon: "🔍" },
  { key: "gaccounts", label: "Registered Accounts", icon: "👥" },
  { key: "loctrace", label: "Location Trace (all sources)", icon: "🌍" },
  { key: "locations", label: "Location Tracing (photos)", icon: "🗺" },
  { key: "browser", label: "Browser History", icon: "🌐" },
  { key: "wifi_live", label: "Wi-Fi (live, non-root)", icon: "📡", group: "Connectivity" },
  { key: "bluetooth", label: "Bluetooth", icon: "🔵" },
  { key: "celltower", label: "Cell Towers", icon: "📶" },
  { key: "timeline", label: "Timeline", icon: "⏱", group: "Analysis" },
  { key: "recovered", label: "Recovered / Deleted", icon: "♻" },
  { key: "graph", label: "Social Graph", icon: "🕸" },
  { key: "advanced", label: "Advanced Analytics", icon: "🧠" },
  { key: "apppresence", label: "App Presence", icon: "🧩" },
  { key: "antiforensics", label: "Anti-Forensics", icon: "🕵" },
  { key: "recenttasks", label: "Recent Tasks", icon: "🪟" },
  { key: "encryptedapps", label: "Encrypted Apps", icon: "🔐" },
  { key: "aleapp", label: "ALEAPP Artifacts", icon: "🧪" },
  { key: "tagged", label: "Tagged Items", icon: "★" },
  { key: "custody", label: "Chain of Custody", icon: "🔒", group: "Forensics" },
  { key: "encryption", label: "Encryption Posture", icon: "🛡" },
  { key: "devicestate", label: "Device State (pre/post)", icon: "🔁" },
  { key: "validation", label: "Tool Validation", icon: "✅" },
  { key: "knowledge", label: "Knowledge Base", icon: "📚" },
  { key: "report", label: "Report", icon: "📄" },
];

/**
 * The dataset each view is *about*. Used to look the view up in the per-case capability
 * map, so a nav item and its page can both say whether the data was collected, checked
 * and empty, gated off, unreachable, or not built yet. Views with no single backing
 * dataset (Overview, Report, Timeline over everything) are deliberately absent.
 */
export const VIEW_DATASET: Partial<Record<ViewKey, string>> = {
  messages: "messages",
  contacts: "contacts",
  calls: "calls",
  notifications: "notifications",
  media: "media",
  mediainv: "media_inventory",
  deletedmedia: "mediastore_trash",
  telegram: "telegram_conversations",
  whatsapp_backup: "whatsapp_backup_messages",
  instagram: "instagram_conversations",
  snapchat: "snapchat_conversations",
  discovered: "discovered_chats",
  apps: "apps",
  accounts: "accounts",
  calendar: "calendar",
  wifi: "wifi",
  wifi_live: "wifi_live",
  bluetooth: "bluetooth",
  celltower: "celltower",
  screentime: "screen_app_usage",
  search: "search_history",
  gaccounts: "google_accounts",
  locations: "locations",
  loctrace: "location_traces",
  browser: "browser",
  recovered: "recovered",
  graph: "graph",
  advanced: "advanced",
  apppresence: "app_presence",
  antiforensics: "antiforensic_findings",
  recenttasks: "recent_tasks",
  encryptedapps: "encrypted_apps",
  aleapp: "aleapp",
  encryption: "encryption_state",
  devicestate: "device_state",
  validation: "validation_report",
  intel: "ai_findings",
};

/** Views that work without a case loaded — they read installation-wide state. */
const CASE_INDEPENDENT: ReadonlySet<ViewKey> = new Set<ViewKey>(["acquire", "knowledge", "cases"]);

export function isCaseIndependent(view: ViewKey): boolean {
  return CASE_INDEPENDENT.has(view);
}

/**
 * A one-word tail on a nav item saying why that view has nothing in it. Populated and
 * unknown states render nothing — the badge is only there when the absence needs
 * explaining, so the sidebar stays readable.
 */
function NavState({ state }: { state?: string }) {
  if (!state || state === "populated") return null;
  const label =
    state === "planned"
      ? "soon"
      : state === "not_collected"
        ? "off"
        : state === "inaccessible"
          ? "n/a"
          : "0";
  const tone =
    state === "planned"
      ? "bg-accent/15 text-accent border-accent/30"
      : state === "not_collected"
        ? "bg-warn/15 text-warn border-warn/30"
        : state === "inaccessible"
          ? "bg-deletion/15 text-deletion border-deletion/30"
          : "bg-panel text-muted/70 border-line";
  return (
    <span
      className={`ml-auto shrink-0 text-[9px] font-mono px-1.5 py-px rounded-full border ${tone}`}
    >
      {label}
    </span>
  );
}

export function Sidebar({
  view,
  setView,
  caseId,
  health,
  onNewAcquisition,
}: {
  view: ViewKey;
  setView: (v: ViewKey) => void;
  caseId: string | null;
  health: Health | null;
  onNewAcquisition: () => void;
}) {
  let lastGroup = "";
  const caps = useCapabilities();
  return (
    <aside className="w-60 shrink-0 border-r border-line bg-panel flex flex-col">
      <div className="p-4 border-b border-line">
        <button className="btn-accent w-full" onClick={onNewAcquisition}>
          + New Acquisition
        </button>
      </div>
      <nav className="flex-1 overflow-y-auto py-2">
        {NAV.map((item) => {
          const showGroup = item.group && item.group !== lastGroup;
          if (item.group) lastGroup = item.group;
          // The Knowledge Base reads installation-wide state, so it stays reachable
          // before any case is loaded.
          const disabled = !caseId && !isCaseIndependent(item.key);
          return (
            <div key={item.key}>
              {showGroup && (
                <div className="px-4 pt-3 pb-1 text-[10px] uppercase tracking-widest text-muted/70">
                  {item.group}
                </div>
              )}
              <button
                disabled={disabled}
                onClick={() => setView(item.key)}
                className={`w-full text-left px-4 py-2 text-sm flex items-center gap-2.5 transition-colors ${
                  view === item.key
                    ? "bg-accent/15 text-accent border-r-2 border-accent"
                    : "text-ink/80 hover:bg-panel-2 disabled:opacity-30 disabled:hover:bg-transparent"
                }`}
              >
                <span className="w-4 text-center opacity-80">{item.icon}</span>
                <span className="truncate">{item.label}</span>
                <NavState state={caps?.by_dataset[VIEW_DATASET[item.key] ?? ""]?.state} />
              </button>
            </div>
          );
        })}
      </nav>
      <div className="p-3 border-t border-line text-[10px] text-muted leading-relaxed">
        <div className="text-accent/90 font-semibold mb-1">Triage preview only</div>
        Minimally-invasive, fully-logged acquisition. Not a substitute for full lab
        examination.
      </div>
    </aside>
  );
}
