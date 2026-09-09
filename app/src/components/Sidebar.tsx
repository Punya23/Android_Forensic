import {
  Archive,
  LayoutDashboard,
  Sparkles,
  MessageSquareText,
  MessageSquare,
  Send,
  Unlock,
  Camera,
  Ghost,
  ScanSearch,
  User,
  Phone,
  Bell,
  Image,
  FolderOpen,
  Trash2,
  Package,
  KeyRound,
  Calendar,
  Wifi,
  Hourglass,
  Search,
  Users,
  Globe,
  Globe2,
  RadioTower,
  Bluetooth,
  Clock,
  Recycle,
  Network,
  Brain,
  Puzzle,
  ShieldAlert,
  AppWindow,
  Lock,
  FlaskConical,
  Star,
  ShieldCheck,
  RefreshCw,
  CircleCheck,
  BookOpen,
  FileText,
  Plus,
  type LucideIcon,
} from "lucide-react";
import type { CapabilityState, Health } from "../lib/types";
import { useCapabilities } from "../lib/capabilities";

export type ViewKey =
  | "acquire"
  | "cases"
  | "overview"
  | "intel"
  | "ask"
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

const NAV: { key: ViewKey; label: string; icon: LucideIcon; group?: string }[] = [
  { key: "cases", label: "Case History", icon: Archive },
  { key: "overview", label: "Overview", icon: LayoutDashboard },
  { key: "intel", label: "Case Intelligence", icon: Sparkles },
  { key: "ask", label: "Ask This Case", icon: MessageSquareText },
  { key: "messages", label: "Messages", icon: MessageSquare, group: "Communications" },
  { key: "telegram", label: "Telegram", icon: Send },
  { key: "whatsapp_backup", label: "WA Backup Recovery", icon: Unlock },
  { key: "instagram", label: "Instagram", icon: Camera },
  { key: "snapchat", label: "Snapchat", icon: Ghost },
  { key: "discovered", label: "Discovered Chats", icon: ScanSearch },
  { key: "contacts", label: "Contacts", icon: User },
  { key: "calls", label: "Calls", icon: Phone },
  { key: "notifications", label: "Notifications", icon: Bell },
  { key: "media", label: "Media", icon: Image, group: "Device" },
  { key: "mediainv", label: "Media Inventory", icon: FolderOpen },
  { key: "deletedmedia", label: "Deleted Media", icon: Trash2 },
  { key: "apps", label: "Installed Apps", icon: Package },
  { key: "accounts", label: "Accounts", icon: KeyRound },
  { key: "calendar", label: "Calendar", icon: Calendar },
  { key: "wifi", label: "Wi-Fi Passwords", icon: Wifi },
  { key: "screentime", label: "Screen & App Usage", icon: Hourglass },
  { key: "search", label: "Search History", icon: Search },
  { key: "gaccounts", label: "Registered Accounts", icon: Users },
  { key: "loctrace", label: "Location Trace (all sources)", icon: Globe },
  { key: "locations", label: "Location Tracing (photos)", icon: Globe2 },
  { key: "browser", label: "Browser History", icon: Globe2 },
  { key: "wifi_live", label: "Wi-Fi (live, non-root)", icon: RadioTower, group: "Connectivity" },
  { key: "bluetooth", label: "Bluetooth", icon: Bluetooth },
  { key: "celltower", label: "Cell Towers", icon: RadioTower },
  { key: "timeline", label: "Timeline", icon: Clock, group: "Analysis" },
  { key: "recovered", label: "Recovered / Deleted", icon: Recycle },
  { key: "graph", label: "Social Graph", icon: Network },
  { key: "advanced", label: "Advanced Analytics", icon: Brain },
  { key: "apppresence", label: "App Presence", icon: Puzzle },
  { key: "antiforensics", label: "Anti-Forensics", icon: ShieldAlert },
  { key: "recenttasks", label: "Recent Tasks", icon: AppWindow },
  { key: "encryptedapps", label: "Encrypted Apps", icon: Lock },
  { key: "aleapp", label: "ALEAPP Artifacts", icon: FlaskConical },
  { key: "tagged", label: "Tagged Items", icon: Star },
  { key: "custody", label: "Chain of Custody", icon: ShieldCheck, group: "Forensics" },
  { key: "encryption", label: "Encryption Posture", icon: ShieldCheck },
  { key: "devicestate", label: "Device State (pre/post)", icon: RefreshCw },
  { key: "validation", label: "Tool Validation", icon: CircleCheck },
  { key: "knowledge", label: "Knowledge Base", icon: BookOpen },
  { key: "report", label: "Report", icon: FileText },
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
 *
 * The words are chosen to answer "what do I do about this?", because the badge is the
 * only thing an examiner sees without opening the view. "opt-in" is a stage that was
 * left un-ticked and *will* run if re-enabled on this handset — the engine says so in
 * `flag_actionable`, and where it says no ("not run") the fix is something else the
 * reason names, such as importing an account-data export. "n/a" is a dataset this
 * handset could never have produced, so re-running changes nothing (the engine decides
 * which of the three a Tier-2 stage on an unrooted phone is — see `triage/capabilities.py`);
 * "soon" is not built yet, with no date attached to it anywhere; "0" is the device
 * finding: the stage looked and the source was empty. The badge sits in a 256px rail
 * beside a truncated label, so none of these may grow past a few characters — the full
 * sentence lives in the row's `title` tooltip instead.
 *
 * Every state is matched by name and an unrecognised one renders nothing. It must not
 * fall through to "0": that badge asserts a finding about the device, and a build that
 * does not recognise the state the engine sent has established no such thing.
 */
function NavState({ cap }: { cap?: CapabilityState }) {
  const state = cap?.state;
  if (!state || state === "populated") return null;
  const label =
    state === "planned"
      ? "soon"
      : state === "not_collected"
        ? cap?.flag_actionable
          ? "opt-in"
          : "not run"
        : state === "inaccessible"
          ? "n/a"
          : state === "empty"
            ? "0"
            : null;
  if (label === null) return null;
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

/**
 * Hover text for a nav row, so a bare "opt-in" / "n/a" / "soon" explains itself without
 * the examiner having to open the view to find out.
 *
 * Every word of it comes from the engine's own capability record — `reason` is written
 * by `resolve()` in `triage/capabilities.py` and `requires` is the catalogue's stated
 * precondition. The sidebar deliberately writes none of its own prose here: a tooltip
 * that claimed more than the acquisition established would be the same overstatement
 * this whole layer exists to prevent.
 *
 * Only the gap states get one. `empty` is excluded on purpose: its reason is the
 * engine's affirmative device finding ("the stage ran and the source held nothing"),
 * and hanging that sentence off a hover in a nav rail puts an evidential claim where
 * nobody asked a question. The place to read a finding is the view, with its tier badge,
 * its requires line and its caveats — not a tooltip. Returns undefined for those and for
 * a populated or unknown dataset, so React drops the attribute entirely.
 */
const TOOLTIP_STATES: ReadonlySet<string> = new Set([
  "not_collected",
  "inaccessible",
  "planned",
]);

function navTitle(cap?: CapabilityState): string | undefined {
  if (!cap || !cap.reason || !TOOLTIP_STATES.has(cap.state)) return undefined;
  return cap.requires ? `${cap.reason}\n\nRequires: ${cap.requires}` : cap.reason;
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
    <aside className="w-64 shrink-0 border-r border-line bg-panel-2 flex flex-col">
      <div className="p-3 border-b border-line">
        <button
          className="btn-accent w-full flex items-center justify-center gap-1.5"
          onClick={onNewAcquisition}
        >
          <Plus className="h-4 w-4" strokeWidth={2.5} />
          New Acquisition
        </button>
      </div>
      <nav className="flex-1 overflow-y-auto py-2 px-2">
        {NAV.map((item) => {
          const showGroup = item.group && item.group !== lastGroup;
          if (item.group) lastGroup = item.group;
          // The Knowledge Base reads installation-wide state, so it stays reachable
          // before any case is loaded.
          const disabled = !caseId && !isCaseIndependent(item.key);
          const active = view === item.key;
          const Icon = item.icon;
          // Resolved once per row and shared by the badge and its tooltip, so the two
          // can never disagree about which state they are describing.
          const cap = caps?.by_dataset[VIEW_DATASET[item.key] ?? ""];
          return (
            <div key={item.key}>
              {showGroup && (
                <div className="mt-4 pt-3 pb-1 px-2.5 border-t border-line text-[10px] font-semibold uppercase tracking-widest text-muted/70">
                  {item.group}
                </div>
              )}
              <button
                disabled={disabled}
                title={navTitle(cap)}
                onClick={() => setView(item.key)}
                className={`w-full text-left mb-0.5 px-2.5 py-[7px] rounded-md text-[13px] font-medium flex items-center gap-2.5 transition-colors ${
                  active
                    ? "bg-accent/12 text-accent"
                    : "text-ink/75 hover:bg-panel disabled:opacity-30 disabled:hover:bg-transparent"
                }`}
              >
                <Icon
                  className="h-[15px] w-[15px] shrink-0"
                  strokeWidth={active ? 2.25 : 1.75}
                  aria-hidden
                />
                <span className="truncate">{item.label}</span>
                <NavState cap={cap} />
              </button>
            </div>
          );
        })}
      </nav>
      <div className="p-3 border-t border-line text-[10px] text-muted leading-relaxed">
        <div className="flex items-center gap-1.5 text-accent/90 font-semibold mb-1">
          <ShieldAlert className="h-3 w-3" strokeWidth={2.25} />
          Triage preview only
        </div>
        Minimally-invasive, fully-logged acquisition. Not a substitute for full lab
        examination.
      </div>
    </aside>
  );
}
