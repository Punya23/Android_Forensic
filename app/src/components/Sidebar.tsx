import type { Health } from "../lib/types";

export type ViewKey =
  | "acquire"
  | "cases"
  | "overview"
  | "intel"
  | "knowledge"
  | "messages"
  | "contacts"
  | "calls"
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
  | "tagged"
  | "apppresence"
  | "antiforensics"
  | "recenttasks"
  | "encryptedapps"
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
  { key: "apppresence", label: "App Presence", icon: "🧩" },
  { key: "antiforensics", label: "Anti-Forensics", icon: "🕵" },
  { key: "recenttasks", label: "Recent Tasks", icon: "🪟" },
  { key: "encryptedapps", label: "Encrypted Apps", icon: "🔐" },
  { key: "tagged", label: "Tagged Items", icon: "★" },
  { key: "custody", label: "Chain of Custody", icon: "🔒", group: "Forensics" },
  { key: "encryption", label: "Encryption Posture", icon: "🛡" },
  { key: "devicestate", label: "Device State (pre/post)", icon: "🔁" },
  { key: "validation", label: "Tool Validation", icon: "✅" },
  { key: "knowledge", label: "Knowledge Base", icon: "📚" },
  { key: "report", label: "Report", icon: "📄" },
];

/** Views that work without a case loaded — they read installation-wide state. */
const CASE_INDEPENDENT: ReadonlySet<ViewKey> = new Set<ViewKey>(["acquire", "knowledge", "cases"]);

export function isCaseIndependent(view: ViewKey): boolean {
  return CASE_INDEPENDENT.has(view);
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
                {item.label}
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
