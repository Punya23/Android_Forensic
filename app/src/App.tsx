import { useEffect, useState } from "react";
import { ShieldCheck, FolderOpen, Cpu, Usb, CircleUserRound, LogOut, Menu } from "lucide-react";
import { api, hasAuthToken, setOnUnauthorized } from "./lib/api";
import type { Health } from "./lib/types";
import { TagProvider } from "./lib/tagStore";
import { CapabilityProvider, CapabilityBanner } from "./lib/capabilities";
import { Sidebar, isCaseIndependent, VIEW_DATASET, sectionOf, viewLabel, type ViewKey } from "./components/Sidebar";
import { GlobalSearch } from "./components/GlobalSearch";
import { FetchErrorBanner } from "./components/FetchErrorBanner";
import { ThemeToggle } from "./components/ThemeToggle";
import { LoginView } from "./views/Login";
import { OnboardingView } from "./views/Onboarding";
import { AcquisitionView } from "./views/Acquisition";
import { CasesView } from "./views/Cases";
import { OverviewView } from "./views/Overview";
import { CaseIntelView } from "./views/CaseIntel";
import { AskTheCaseView } from "./views/AskTheCase";
import { KnowledgeBaseView } from "./views/KnowledgeBase";
import { MessagesView } from "./views/Messages";
import { ContactsView } from "./views/Contacts";
import { CallsView } from "./views/Calls";
import { MediaView } from "./views/Media";
import { LocationsView } from "./views/Locations";
import { LocationTraceView } from "./views/LocationTrace";
import { BrowserView } from "./views/Browser";
import { TimelineView } from "./views/Timeline";
import { RecoveredView } from "./views/Recovered";
import { GraphView } from "./views/Graph";
import { TaggedView } from "./views/Tagged";
import { CustodyView } from "./views/Custody";
import { ReportView } from "./views/Report";
import { TelegramView } from "./views/Telegram";
import { WhatsAppBackupView } from "./views/WhatsAppBackup";
import { InstagramView } from "./views/Instagram";
import { SnapchatView } from "./views/Snapchat";
import { DiscoveredChatsView } from "./views/DiscoveredChats";
import { AppsView } from "./views/Apps";
import { AccountsView } from "./views/Accounts";
import { CalendarView } from "./views/Calendar";
import { MediaInventoryView } from "./views/MediaInventory";
import { DeletedMediaView } from "./views/DeletedMedia";
import { WifiView } from "./views/WiFi";
// Views for datasets the engine collects but previously never rendered (P1-1/3/4/7,
// P2-3/4, P3-1..4). Grouped here so the set is obvious at a glance.
import { WifiLiveView } from "./views/WifiLive";
import { BluetoothView } from "./views/Bluetooth";
import { CellTowerView } from "./views/CellTower";
import { ScreenTimeView } from "./views/ScreenTime";
import { SearchHistoryView } from "./views/SearchHistory";
import { GoogleAccountsView } from "./views/GoogleAccounts";
import { EncryptionView } from "./views/Encryption";
import { DeviceStateView } from "./views/DeviceState";
import { AppPresenceView } from "./views/AppPresence";
import { AntiForensicsView } from "./views/AntiForensics";
import { RecentTasksView } from "./views/RecentTasks";
import { EncryptedAppsView } from "./views/EncryptedApps";
import { AleappView } from "./views/Aleapp";
import { ValidationView } from "./views/Validation";
import { NotificationsView } from "./views/Notifications";
import { AdvancedAnalyticsView } from "./views/AdvancedAnalytics";
import { HomeView } from "./views/Home";

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [caseId, setCaseId] = useState<string | null>(null);
  const [view, setView] = useState<ViewKey>("home");
  // Set by MediaView's "View on map" button; consumed by LocationsView to fly
  // the map to that exact photo's point on arrival, then cleared so revisiting
  // Locations later doesn't re-trigger the same fly-to.
  // Narrow screens show the sidebar as a slide-over drawer instead of a fixed rail.
  const [navOpen, setNavOpen] = useState(false);
  const [mediaMapFocus, setMediaMapFocus] = useState<string | null>(null);

  // --- auth / onboarding gate ---------------------------------------------
  // authed starts true if a token survived a page reload; api.me() below confirms
  // it's still accepted (the engine drops all tokens on restart). onboarded is
  // deliberately NOT persisted — the welcome screen is a per-session thing, shown
  // again every time someone signs back in.
  const [authed, setAuthed] = useState(hasAuthToken());
  const [onboarded, setOnboarded] = useState(false);
  const [username, setUsername] = useState<string | null>(null);

  useEffect(() => {
    setOnUnauthorized(() => {
      setAuthed(false);
      setOnboarded(false);
      setUsername(null);
    });
  }, []);

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null));
  }, []);

  // A stored token might be stale (engine restarted since last visit). Confirm it
  // still works; if not, api.ts's 401 handler already flips authed back to false.
  useEffect(() => {
    if (authed) api.me().then((r) => setUsername(r.username)).catch(() => {});
  }, [authed]);

  function onCaseReady(id: string, landOn: ViewKey = "overview") {
    setCaseId(id);
    setView(landOn);
  }

  /** Sign-in lands on Home, which shows installation-wide stats and the way into a case. */
  function enterDashboard() {
    setOnboarded(true);
    setView("home");
  }

  function onLogout() {
    api.logout().finally(() => {
      setAuthed(false);
      setOnboarded(false);
      setUsername(null);
      setCaseId(null);
    });
  }

  if (!authed) {
    return (
      <LoginView
        health={health}
        onSuccess={(name) => {
          setUsername(name);
          setAuthed(true);
        }}
      />
    );
  }

  if (!onboarded) {
    return <OnboardingView username={username} onContinue={enterDashboard} />;
  }

  const sec = sectionOf(view);
  const crumb = [sec, viewLabel(view)].filter((x, i, arr) => x && arr.indexOf(x) === i).join("  /  ");

  const body = (
    <div className="flex h-screen overflow-hidden">
      <div className={`${navOpen ? "fixed inset-0 z-40 flex" : "hidden"} md:static md:z-auto md:flex`}>
        <Sidebar
          view={view}
          setView={(v) => {
            setView(v);
            setNavOpen(false);
          }}
          caseId={caseId}
          health={health}
          onNewAcquisition={() => {
            setView("acquire");
            setNavOpen(false);
          }}
        />
        <button
          aria-label="Close menu"
          className="flex-1 bg-black/50 md:hidden"
          onClick={() => setNavOpen(false)}
        />
      </div>
      <main className="flex-1 overflow-hidden flex flex-col">
        <TopBar crumb={crumb} health={health} caseId={caseId} setView={setView} username={username} onLogout={onLogout} onMenu={() => setNavOpen(true)} />
        {/* One strip, above whichever view is routed, saying why this view's data is
            absent when it is. Renders nothing when the dataset is populated, and
            nothing for views that aren't about a single dataset. */}
        {caseId && <CapabilityBanner dataset={VIEW_DATASET[view]} />}
        <FetchErrorBanner />
        <div className="flex-1 overflow-auto">
          {view === "home" && (
            <HomeView username={username} health={health} setView={setView} onOpenCase={onCaseReady} />
          )}
          {view === "acquire" && <AcquisitionView onCaseReady={onCaseReady} onOpenCase={onCaseReady} />}
          {view === "cases" && <CasesView onOpenCase={onCaseReady} />}
          {view === "knowledge" && <KnowledgeBaseView />}
          {!isCaseIndependent(view) && !caseId && (
            <div className="p-8 text-muted">No case loaded. Start an acquisition first.</div>
          )}
          {caseId && view === "overview" && <OverviewView caseId={caseId} setView={setView} />}
          {caseId && view === "intel" && <CaseIntelView caseId={caseId} />}
          {caseId && view === "ask" && <AskTheCaseView caseId={caseId} />}
          {caseId && view === "messages" && <MessagesView caseId={caseId} />}
          {caseId && view === "contacts" && <ContactsView caseId={caseId} />}
          {caseId && view === "calls" && <CallsView caseId={caseId} />}
          {caseId && view === "notifications" && <NotificationsView caseId={caseId} />}
          {caseId && view === "media" && (
            <MediaView
              caseId={caseId}
              onViewOnMap={(storedPath) => {
                setMediaMapFocus(storedPath);
                setView("locations");
              }}
            />
          )}
          {caseId && view === "mediainv" && <MediaInventoryView caseId={caseId} />}
          {caseId && view === "deletedmedia" && <DeletedMediaView caseId={caseId} />}
          {caseId && view === "apps" && <AppsView caseId={caseId} />}
          {caseId && view === "accounts" && <AccountsView caseId={caseId} />}
          {caseId && view === "calendar" && <CalendarView caseId={caseId} />}
          {caseId && view === "wifi" && <WifiView caseId={caseId} />}
          {caseId && view === "instagram" && <InstagramView caseId={caseId} />}
          {caseId && view === "snapchat" && <SnapchatView caseId={caseId} />}
          {caseId && view === "discovered" && <DiscoveredChatsView caseId={caseId} />}
          {caseId && view === "locations" && (
            <LocationsView
              caseId={caseId}
              focusStoredPath={mediaMapFocus}
              onFocusHandled={() => setMediaMapFocus(null)}
            />
          )}
          {caseId && view === "loctrace" && <LocationTraceView caseId={caseId} />}
          {caseId && view === "browser" && <BrowserView caseId={caseId} />}
          {caseId && view === "timeline" && <TimelineView caseId={caseId} setView={setView} />}
          {caseId && view === "recovered" && <RecoveredView caseId={caseId} />}
          {caseId && view === "graph" && <GraphView caseId={caseId} />}
          {caseId && view === "advanced" && <AdvancedAnalyticsView caseId={caseId} setView={setView} />}
          {caseId && view === "tagged" && <TaggedView caseId={caseId} setView={setView} />}
          {caseId && view === "custody" && <CustodyView caseId={caseId} />}
          {caseId && view === "report" && <ReportView caseId={caseId} />}
          {caseId && view === "telegram" && <TelegramView caseId={caseId} />}
          {caseId && view === "whatsapp_backup" && <WhatsAppBackupView caseId={caseId} />}
          {caseId && view === "wifi_live" && <WifiLiveView caseId={caseId} />}
          {caseId && view === "bluetooth" && <BluetoothView caseId={caseId} />}
          {caseId && view === "celltower" && <CellTowerView caseId={caseId} />}
          {caseId && view === "screentime" && <ScreenTimeView caseId={caseId} />}
          {caseId && view === "search" && <SearchHistoryView caseId={caseId} />}
          {caseId && view === "gaccounts" && <GoogleAccountsView caseId={caseId} />}
          {caseId && view === "encryption" && <EncryptionView caseId={caseId} />}
          {caseId && view === "devicestate" && <DeviceStateView caseId={caseId} />}
          {caseId && view === "apppresence" && <AppPresenceView caseId={caseId} />}
          {caseId && view === "antiforensics" && <AntiForensicsView caseId={caseId} />}
          {caseId && view === "recenttasks" && <RecentTasksView caseId={caseId} />}
          {caseId && view === "encryptedapps" && <EncryptedAppsView caseId={caseId} />}
          {caseId && view === "aleapp" && <AleappView caseId={caseId} />}
          {caseId && view === "validation" && <ValidationView caseId={caseId} />}
        </div>
      </main>
    </div>
  );

  // The tag store and the capability map are both per-case; only mount them once a
  // case is loaded. Capabilities wrap the tag store so every view can ask why its
  // dataset is empty without each one re-fetching the same answer.
  return caseId ? (
    <CapabilityProvider caseId={caseId} key={caseId}>
      <TagProvider caseId={caseId}>{body}</TagProvider>
    </CapabilityProvider>
  ) : (
    body
  );
}

function TopBar({
  crumb,
  health,
  caseId,
  setView,
  username,
  onLogout,
  onMenu,
}: {
  crumb: string;
  health: Health | null;
  caseId: string | null;
  setView: (v: ViewKey) => void;
  username: string | null;
  onLogout: () => void;
  onMenu: () => void;
}) {
  return (
    <header className="h-14 border-b border-line flex items-center justify-between px-3 md:px-5 bg-panel/70 backdrop-blur shrink-0 gap-3 md:gap-4">
      <div className="flex items-center gap-3 text-sm shrink-0">
        <button className="md:hidden btn-ghost !px-2 !py-1.5" aria-label="Open menu" onClick={onMenu}>
          <Menu className="h-4 w-4" aria-hidden />
        </button>
        <div className="md:hidden flex items-center gap-1.5 text-accent font-semibold">
          <ShieldCheck className="h-[18px] w-[18px]" strokeWidth={2.25} aria-hidden />
          SNAGR
        </div>
        <span className="text-muted hidden md:inline">{crumb}</span>
        {caseId && (
          <span className="flex items-center gap-1.5 font-mono text-xs bg-panel px-2 py-1 rounded-md border border-line">
            <FolderOpen className="h-3.5 w-3.5 text-muted" strokeWidth={1.75} aria-hidden />
            {caseId}
          </span>
        )}
      </div>
      {caseId && <GlobalSearch caseId={caseId} setView={setView} />}
      <div className="flex items-center gap-3 text-xs text-muted shrink-0">
        <span className="hidden sm:flex items-center gap-1.5">
          <Cpu className={`h-3.5 w-3.5 ${health ? "text-live" : "text-deletion"}`} strokeWidth={1.75} aria-hidden />
          <span className="hidden xl:inline">{health ? `engine v${health.version}` : "engine offline"}</span>
        </span>
        <span className={`hidden sm:flex items-center gap-1.5 ${health?.adb ? "text-live" : "text-warn"}`}>
          <Usb className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden />
          <span className="hidden xl:inline">{health?.adb ? "adb ready" : "adb not found"}</span>
        </span>
        {username && (
          <span className="hidden lg:flex items-center gap-1.5 text-muted">
            <CircleUserRound className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden />
            {username}
          </span>
        )}
        <ThemeToggle />
        <button className="btn-ghost !px-2.5 !py-1.5 text-xs flex items-center gap-1.5" onClick={onLogout}>
          <LogOut className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden />
          <span className="hidden sm:inline">Sign out</span>
        </button>
      </div>
    </header>
  );
}
