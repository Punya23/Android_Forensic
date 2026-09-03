import { useEffect, useState } from "react";
import { api, hasAuthToken, setOnUnauthorized } from "./lib/api";
import type { Health } from "./lib/types";
import { TagProvider } from "./lib/tagStore";
import { CapabilityProvider, CapabilityBanner } from "./lib/capabilities";
import { Sidebar, isCaseIndependent, VIEW_DATASET, type ViewKey } from "./components/Sidebar";
import { GlobalSearch } from "./components/GlobalSearch";
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

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [caseId, setCaseId] = useState<string | null>(null);
  const [view, setView] = useState<ViewKey>("acquire");

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

  function onCaseReady(id: string) {
    setCaseId(id);
    setView("overview");
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
    return <OnboardingView username={username} onContinue={() => setOnboarded(true)} />;
  }

  const body = (
    <div className="flex h-screen overflow-hidden">
      <Sidebar
        view={view}
        setView={setView}
        caseId={caseId}
        health={health}
        onNewAcquisition={() => setView("acquire")}
      />
      <main className="flex-1 overflow-hidden flex flex-col">
        <TopBar health={health} caseId={caseId} setView={setView} username={username} onLogout={onLogout} />
        {/* One strip, above whichever view is routed, saying why this view's data is
            absent when it is. Renders nothing when the dataset is populated, and
            nothing for views that aren't about a single dataset. */}
        {caseId && <CapabilityBanner dataset={VIEW_DATASET[view]} />}
        <div className="flex-1 overflow-auto">
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
          {caseId && view === "media" && <MediaView caseId={caseId} />}
          {caseId && view === "mediainv" && <MediaInventoryView caseId={caseId} />}
          {caseId && view === "deletedmedia" && <DeletedMediaView caseId={caseId} />}
          {caseId && view === "apps" && <AppsView caseId={caseId} />}
          {caseId && view === "accounts" && <AccountsView caseId={caseId} />}
          {caseId && view === "calendar" && <CalendarView caseId={caseId} />}
          {caseId && view === "wifi" && <WifiView caseId={caseId} />}
          {caseId && view === "instagram" && <InstagramView caseId={caseId} />}
          {caseId && view === "snapchat" && <SnapchatView caseId={caseId} />}
          {caseId && view === "discovered" && <DiscoveredChatsView caseId={caseId} />}
          {caseId && view === "locations" && <LocationsView caseId={caseId} />}
          {caseId && view === "loctrace" && <LocationTraceView caseId={caseId} />}
          {caseId && view === "browser" && <BrowserView caseId={caseId} />}
          {caseId && view === "timeline" && <TimelineView caseId={caseId} />}
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
  health,
  caseId,
  setView,
  username,
  onLogout,
}: {
  health: Health | null;
  caseId: string | null;
  setView: (v: ViewKey) => void;
  username: string | null;
  onLogout: () => void;
}) {
  return (
    <header className="h-12 border-b border-line flex items-center justify-between px-5 bg-panel-2 shrink-0 gap-4">
      <div className="flex items-center gap-3 text-sm shrink-0">
        <span className="font-semibold text-accent">SNAGR</span>
        <span className="text-muted hidden md:inline">Android Rapid Evidence Triage</span>
        {caseId && (
          <span className="font-mono text-xs bg-panel px-2 py-0.5 rounded border border-line">{caseId}</span>
        )}
      </div>
      {caseId && <GlobalSearch caseId={caseId} setView={setView} />}
      <div className="flex items-center gap-3 text-xs text-muted shrink-0">
        <span className="flex items-center gap-1.5">
          <span className={`h-2 w-2 rounded-full ${health ? "bg-live" : "bg-deletion"}`} />
          {health ? `engine v${health.version}` : "engine offline"}
        </span>
        <span className={health?.adb ? "text-live" : "text-warn"}>
          {health?.adb ? "adb ready" : "adb not found"}
        </span>
        {username && <span className="text-muted">{username}</span>}
        <button className="btn-ghost !px-2 !py-1 text-xs" onClick={onLogout}>
          Sign out
        </button>
      </div>
    </header>
  );
}
