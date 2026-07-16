import type { Health } from "../lib/types";

export type ViewKey =
  | "acquire"
  | "overview"
  | "messages"
  | "contacts"
  | "calls"
  | "media"
  | "mediainv"
  | "telegram"
  | "instagram"
  | "snapchat"
  | "apps"
  | "accounts"
  | "calendar"
  | "locations"
  | "browser"
  | "timeline"
  | "recovered"
  | "discovered"
  | "graph"
  | "tagged"
  | "custody"
  | "report";

const NAV: { key: ViewKey; label: string; icon: string; group?: string }[] = [
  { key: "overview", label: "Overview", icon: "▤" },
  { key: "messages", label: "Messages", icon: "💬", group: "Communications" },
  { key: "telegram", label: "Telegram", icon: "✈" },
  { key: "instagram", label: "Instagram", icon: "📷" },
  { key: "snapchat", label: "Snapchat", icon: "👻" },
  { key: "discovered", label: "Discovered Chats", icon: "🔎" },
  { key: "contacts", label: "Contacts", icon: "👤" },
  { key: "calls", label: "Calls", icon: "📞" },
  { key: "media", label: "Media", icon: "🖼", group: "Device" },
  { key: "mediainv", label: "Media Inventory", icon: "🗂" },
  { key: "apps", label: "Installed Apps", icon: "📦" },
  { key: "accounts", label: "Accounts", icon: "🔑" },
  { key: "calendar", label: "Calendar", icon: "📅" },
  { key: "locations", label: "Locations", icon: "📍" },
  { key: "browser", label: "Browser History", icon: "🌐" },
  { key: "timeline", label: "Timeline", icon: "⏱", group: "Analysis" },
  { key: "recovered", label: "Recovered / Deleted", icon: "♻" },
  { key: "graph", label: "Social Graph", icon: "🕸" },
  { key: "tagged", label: "Tagged Items", icon: "★" },
  { key: "custody", label: "Chain of Custody", icon: "🔒", group: "Forensics" },
  { key: "report", label: "Report", icon: "📄" },
];

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
          const disabled = !caseId;
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
