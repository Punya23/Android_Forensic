import {
  Archive,
  Home,
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
  ChevronRight,
  type LucideIcon,
} from "lucide-react";
import { useState } from "react";
import type { CapabilityState, Health } from "../lib/types";
import { useCapabilities } from "../lib/capabilities";
import { ROOT_ACQUISITION } from "../lib/features";

export type ViewKey =
  | "home"
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

/**
 * Sidebar order follows an investigator's flow: open the case → ask it questions →
 * work the evidence by kind → analyse → check risk flags → verify integrity & report.
 * The first item of each group carries `group`; groups are collapsible in the render.
 */
const NAV: { key: ViewKey; label: string; icon: LucideIcon; group?: string; root?: true }[] = [
  { key: "home", label: "Home", icon: Home, group: "Case" },
  { key: "cases", label: "Case History", icon: Archive },
  { key: "overview", label: "Overview", icon: LayoutDashboard },
  { key: "ask", label: "Ask This Case", icon: MessageSquareText },
  { key: "intel", label: "Case Intelligence", icon: Sparkles },
  { key: "report", label: "Report", icon: FileText },

  { key: "messages", label: "Messages (SMS)", icon: MessageSquare, group: "Communications" },
  { key: "calls", label: "Calls", icon: Phone },
  { key: "contacts", label: "Contacts", icon: User },
  { key: "notifications", label: "Notifications", icon: Bell },
  { key: "telegram", label: "Telegram", icon: Send },
  { key: "instagram", label: "Instagram", icon: Camera },
  { key: "snapchat", label: "Snapchat", icon: Ghost },
  { key: "whatsapp_backup", label: "WhatsApp Backup", icon: Unlock, root: true },
  { key: "discovered", label: "Other Chat Apps", icon: ScanSearch, root: true },

  { key: "media", label: "Photos & Videos", icon: Image, group: "Media" },
  { key: "mediainv", label: "Media Inventory", icon: FolderOpen },
  { key: "deletedmedia", label: "Deleted Media", icon: Trash2 },

  { key: "loctrace", label: "Location Trace", icon: Globe, group: "Location & Network" },
  { key: "locations", label: "Photo Locations", icon: Globe2 },
  { key: "celltower", label: "Cell Towers", icon: RadioTower },
  { key: "wifi_live", label: "Wi-Fi Networks (live)", icon: RadioTower },
  { key: "wifi", label: "Saved Wi-Fi", icon: Wifi, root: true },
  { key: "bluetooth", label: "Bluetooth", icon: Bluetooth },

  { key: "browser", label: "Browser History", icon: Globe2, group: "Activity & Accounts" },
  { key: "search", label: "Search History", icon: Search },
  { key: "apps", label: "Installed Apps", icon: Package },
  { key: "apppresence", label: "App Presence", icon: Puzzle, root: true },
  { key: "screentime", label: "Screen & App Usage", icon: Hourglass },
  { key: "recenttasks", label: "Recent Tasks", icon: AppWindow, root: true },
  { key: "accounts", label: "Device Accounts", icon: KeyRound },
  { key: "gaccounts", label: "Registered Accounts", icon: Users },
  { key: "calendar", label: "Calendar", icon: Calendar },

  { key: "timeline", label: "Timeline", icon: Clock, group: "Analysis" },
  { key: "graph", label: "Communication Network", icon: Network },
  { key: "recovered", label: "Recovered / Deleted", icon: Recycle },
  { key: "advanced", label: "Advanced Analytics", icon: Brain },
  { key: "tagged", label: "Tagged Items", icon: Star },

  { key: "antiforensics", label: "Anti-Forensics", icon: ShieldAlert, group: "Risk Flags", root: true },
  { key: "encryptedapps", label: "Encrypted Apps", icon: Lock, root: true },
  { key: "encryption", label: "Encryption Posture", icon: ShieldCheck },

  { key: "custody", label: "Chain of Custody", icon: ShieldCheck, group: "Integrity & Tools" },
  { key: "devicestate", label: "Device State (pre/post)", icon: RefreshCw },
  { key: "validation", label: "Tool Validation", icon: CircleCheck },
  { key: "aleapp", label: "ALEAPP Artifacts", icon: FlaskConical },
  { key: "knowledge", label: "Knowledge Base", icon: BookOpen },
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
const CASE_INDEPENDENT: ReadonlySet<ViewKey> = new Set<ViewKey>(["home", "acquire", "knowledge", "cases"]);

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

type NavItem = (typeof NAV)[number];
const SECTIONS: { name: string; items: NavItem[] }[] = [];
for (const item of NAV) {
  if (item.group) SECTIONS.push({ name: item.group, items: [] });
  // Root-only views stay hidden while root acquisition is unvalidated (see lib/features.ts).
  if (item.root && !ROOT_ACQUISITION) continue;
  SECTIONS[SECTIONS.length - 1].items.push(item);
}

export function viewLabel(view: ViewKey): string | undefined {
  return NAV.find((i) => i.key === view)?.label;
}

/** Which section a view belongs to (for the page's colour band). */
export function sectionOf(view: ViewKey): string | undefined {
  return SECTIONS.find((s) => s.items.some((i) => i.key === view))?.name;
}

/** Sections open on first visit; the rest start collapsed so the rail stays short. */
const DEFAULT_OPEN = new Set(["Case", "Communications"]);
const OPEN_KEY = "snagr.sidebar.open";

function loadOpen(): Set<string> {
  try {
    const raw = localStorage.getItem(OPEN_KEY);
    if (raw) return new Set(JSON.parse(raw) as string[]);
  } catch {
    /* storage blocked or corrupt — fall back to defaults */
  }
  return new Set(DEFAULT_OPEN);
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
  const caps = useCapabilities();
  const [open, setOpen] = useState<Set<string>>(loadOpen);

  function toggle(name: string) {
    const next = new Set(open);
    if (!next.delete(name)) next.add(name);
    setOpen(next);
    try {
      localStorage.setItem(OPEN_KEY, JSON.stringify([...next]));
    } catch {
      /* per-viewer convenience only */
    }
  }

  return (
    <aside className="w-64 shrink-0 border-r border-line bg-panel/80 backdrop-blur flex flex-col">
      {/* Same height and rule as the top bar, so the two line up across every page. */}
      <div className="h-14 shrink-0 px-4 border-b border-line flex items-center gap-2.5 text-[15px] font-semibold tracking-wide">
        <ShieldCheck className="h-5 w-5 text-accent" strokeWidth={2.25} aria-hidden />
        SNAGR
      </div>
      <div className="px-3 pt-3 pb-1">
        <button
          className="btn-accent w-full flex items-center justify-center gap-1.5"
          onClick={onNewAcquisition}
        >
          <Plus className="h-4 w-4" strokeWidth={2.5} />
          New Acquisition
        </button>
      </div>
      <nav className="flex-1 overflow-y-auto py-2 px-2">
        {SECTIONS.map((sec) => {
          const hasActive = sec.items.some((i) => i.key === view);
          // The section holding the current view is always open, so the examiner can
          // see where they are even if they collapsed it earlier.
          const expanded = open.has(sec.name) || hasActive;
          return (
            <div key={sec.name} className="mb-1">
              <button
                onClick={() => toggle(sec.name)}
                aria-expanded={expanded}
                className="w-full flex items-center gap-1.5 px-2 py-1.5 text-[10px] font-semibold uppercase tracking-widest text-muted/80 hover:text-ink"
              >
                <ChevronRight
                  className={`h-3 w-3 transition-transform ${expanded ? "rotate-90" : ""}`}
                  aria-hidden
                />
                {sec.name}
                <span className="ml-auto font-mono normal-case tracking-normal text-muted/60">
                  {sec.items.length}
                </span>
              </button>
              {expanded &&
                sec.items.map((item) => {
                  // The Knowledge Base reads installation-wide state, so it stays
                  // reachable before any case is loaded.
                  const disabled = !caseId && !isCaseIndependent(item.key);
                  const active = view === item.key;
                  const Icon = item.icon;
                  // Resolved once per row and shared by the badge and its tooltip, so the
                  // two can never disagree about which state they are describing.
                  const cap = caps?.by_dataset[VIEW_DATASET[item.key] ?? ""];
                  return (
                    <button
                      key={item.key}
                      disabled={disabled}
                      title={navTitle(cap)}
                      onClick={() => setView(item.key)}
                      className={`group w-full text-left mb-0.5 px-3 py-2 rounded-xl text-[13px] font-medium flex items-center gap-3 transition-all duration-200 ease-out active:scale-[0.96] ${
                        active
                          ? "neon"
                          : "text-ink/70 hover:bg-panel-2 hover:text-ink hover:translate-x-1 disabled:opacity-30 disabled:hover:bg-transparent disabled:hover:translate-x-0"
                      }`}
                    >
                      <Icon
                        className="h-[16px] w-[16px] shrink-0 transition-transform duration-200 ease-out group-hover:scale-125 group-hover:-rotate-6 group-active:scale-90"
                        strokeWidth={active ? 2.25 : 1.75}
                        aria-hidden
                      />
                      <span className="truncate">{item.label}</span>
                      <NavState cap={cap} />
                    </button>
                  );
                })}
            </div>
          );
        })}
      </nav>
    </aside>
  );
}
