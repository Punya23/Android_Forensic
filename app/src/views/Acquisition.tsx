import { useEffect, useRef, useState } from "react";
import { api, getSocket } from "../lib/api";
import type { DeviceListing, LlmStatus, PlanResponse, Progress } from "../lib/types";
import {
  DeprioritisedList,
  PartialCollectionList,
  PlanNotes,
} from "../components/PlanPanels";
import {
  Sparkles,
  AlertTriangle,
  Wifi,
  Globe2,
  Unlock,
  Bluetooth,
  Puzzle,
  ShieldAlert,
  AppWindow,
} from "lucide-react";

const STAGES = [
  "init", "device", "intel", "tier1", "enumerate", "screenshot", "pull", "location", "recover",
  "flag", "timeline", "analysis", "persist", "report", "done",
];

export function AcquisitionView({
  onCaseReady,
  onOpenCase,
}: {
  onCaseReady: (id: string) => void;
  onOpenCase: (id: string) => void;
}) {
  const [devices, setDevices] = useState<DeviceListing | null>(null);
  const [cases, setCases] = useState<{ case_id: string; examiner: string; device: string }[]>([]);
  const [target, setTarget] = useState<{ kind: "mock" | "real"; id: string; label: string } | null>(null);
  const [caseId, setCaseId] = useState("");
  const [examiner, setExaminer] = useState("");
  const [authority, setAuthority] = useState("");
  const [scope, setScope] = useState("");
  const [brief, setBrief] = useState("");
  // Case-intelligence back-end. Both options keep case text on this workstation —
  // "heuristic" (offline, zero-config) or a local model over Ollama. There is no
  // cloud back-end: evidence text must never leave the machine.
  const [llmProvider, setLlmProvider] = useState<"heuristic" | "ollama">("heuristic");
  // What this workstation can actually run, asked of the engine rather than assumed.
  // Offering a back-end the machine has no model for turns a deliberate choice into a
  // silent fallback discovered only after the acquisition.
  const [llmStatus, setLlmStatus] = useState<LlmStatus | null>(null);
  // Whether the plan may switch on root-only pulls. Collection scope is the examiner's
  // decision: a case brief alone must not be able to widen it without them saying so.
  const [planAllowTier2, setPlanAllowTier2] = useState(true);
  const [caseNumber, setCaseNumber] = useState("");
  const [plan, setPlan] = useState<PlanResponse | null>(null);
  const [planning, setPlanning] = useState(false);
  const [planError, setPlanError] = useState<string | null>(null);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tier1Contacts, setTier1Contacts] = useState(false);
  const [tier1Calllog, setTier1Calllog] = useState(false);
  const [tier1Sms, setTier1Sms] = useState(false);
  const [tier1CollectAll, setTier1CollectAll] = useState(false);
  const [tier2Telegram, setTier2Telegram] = useState(false);
  const [tier2Instagram, setTier2Instagram] = useState(false);
  const [tier2Snapchat, setTier2Snapchat] = useState(false);
  const [tier2Wifi, setTier2Wifi] = useState(false);
  const [tier2BrowserHistory, setTier2BrowserHistory] = useState(false);
  const [tier2WhatsappBackup, setTier2WhatsappBackup] = useState(false);
  // Deep root-tier artifact stages. All default off: each requires root, and the
  // examiner should choose them deliberately rather than inherit them.
  const [tier2BtConfig, setTier2BtConfig] = useState(false);
  const [tier2AppPresence, setTier2AppPresence] = useState(false);
  const [tier2AntiForensics, setTier2AntiForensics] = useState(false);
  const [tier2RecentTasks, setTier2RecentTasks] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const timerRef = useRef<number | null>(null);

  useEffect(() => {
    api
      .llmStatus()
      .then(setLlmStatus)
      .catch(() => setLlmStatus(null));
    api.devices().then((d) => {
      setDevices(d);
      if (d.real[0]) setTarget({ kind: "real", id: d.real[0].serial, label: d.real[0].serial });
      else if (d.mock[0]) setTarget({ kind: "mock", id: d.mock[0].id, label: d.mock[0].label });
    });
    api.cases().then(setCases).catch(() => {});
  }, []);

  useEffect(() => {
    const s = getSocket();
    s.on("progress", (p: Progress) => setProgress(p));
    s.on("complete", (c: { case_id: string }) => {
      stopTimer();
      setRunning(false);
      onCaseReady(c.case_id);
    });
    s.on("failed", (f: { error: string }) => {
      stopTimer();
      setRunning(false);
      setError(f.error);
    });
    return () => {
      s.off("progress");
      s.off("complete");
      s.off("failed");
    };
  }, [onCaseReady]);

  function stopTimer() {
    if (timerRef.current) window.clearInterval(timerRef.current);
    timerRef.current = null;
  }

  async function previewPlan() {
    if (!brief.trim()) return;
    setPlanning(true);
    setPlanError(null);
    try {
      const p = await api.plan(brief.trim(), {
        allow_tier2: planAllowTier2,
        case_number: caseNumber.trim() || undefined,
        llm_provider: llmProvider,
      });
      setPlan(p);
      // Reflect the recommended tier flags in the checkboxes (officer can still override).
      const ov = p.plan.pipeline_overrides || {};
      setTier1Contacts(!!ov.tier1_contacts);
      setTier1Calllog(!!ov.tier1_calllog);
      setTier1Sms(!!ov.tier1_sms);
      setTier1CollectAll(!!ov.tier1_collect_all);
      setTier2Telegram(!!ov.tier2_telegram);
      setTier2Instagram(!!ov.tier2_instagram);
      setTier2Snapchat(!!ov.tier2_snapchat);
      setTier2Wifi(!!ov.tier2_wifi);
      setTier2BrowserHistory(!!ov.tier2_browser_history);
      setTier2WhatsappBackup(!!ov.tier2_whatsapp_backup);
    } catch (e) {
      setPlanError(e instanceof Error ? e.message : String(e));
    } finally {
      setPlanning(false);
    }
  }

  /** Act on a "prior cases suggest reconsidering" prompt by ticking its tier flag. */
  function enableRecommendation(flag: string) {
    const setters: Record<string, (v: boolean) => void> = {
      tier1_contacts: setTier1Contacts,
      tier1_calllog: setTier1Calllog,
      tier1_sms: setTier1Sms,
      tier1_collect_all: setTier1CollectAll,
      tier2_telegram: setTier2Telegram,
      tier2_instagram: setTier2Instagram,
      tier2_snapchat: setTier2Snapchat,
      tier2_wifi: setTier2Wifi,
      tier2_browser_history: setTier2BrowserHistory,
      tier2_whatsapp_backup: setTier2WhatsappBackup,
    };
    setters[flag]?.(true);
  }

  async function start() {
    if (!target || !examiner) return;
    setError(null);
    setRunning(true);
    setProgress({ stage: "init", pct: 0, detail: "Starting…", case_id: "" });
    setElapsed(0);
    timerRef.current = window.setInterval(() => setElapsed((e) => e + 1), 1000);
    try {
      await api.acquire({
        [target.kind === "mock" ? "mock" : "serial"]: target.id,
        case_id: caseId || undefined,
        examiner,
        authority,
        scope,
        case_description: brief.trim() || undefined,
        plan_allow_tier2: planAllowTier2,
        case_number: caseNumber.trim() || undefined,
        llm_provider: llmProvider,
        tier1_contacts: target.kind === "real" ? tier1Contacts : false,
        tier1_calllog: target.kind === "real" ? tier1Calllog : false,
        tier1_sms: target.kind === "real" ? tier1Sms : false,
        tier1_collect_all: target.kind === "real" ? tier1CollectAll : false,
        tier2_telegram: target.kind === "real" ? tier2Telegram : false,
        tier2_instagram: target.kind === "real" ? tier2Instagram : false,
        tier2_snapchat: target.kind === "real" ? tier2Snapchat : false,
        tier2_wifi: target.kind === "real" ? tier2Wifi : false,
        tier2_browser_history: target.kind === "real" ? tier2BrowserHistory : false,
        tier2_whatsapp_backup: target.kind === "real" ? tier2WhatsappBackup : false,
        tier2_bt_config: target.kind === "real" ? tier2BtConfig : false,
        tier2_app_presence: target.kind === "real" ? tier2AppPresence : false,
        tier2_antiforensics: target.kind === "real" ? tier2AntiForensics : false,
        tier2_recent_tasks: target.kind === "real" ? tier2RecentTasks : false,
      });
    } catch (e) {
      stopTimer();
      setRunning(false);
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  if (running) {
    return <ProgressScreen progress={progress} elapsed={elapsed} />;
  }

  return (
    <div className="max-w-4xl mx-auto p-8">
      <h1 className="text-2xl font-bold mb-1">New Acquisition</h1>
      <p className="text-muted text-sm mb-6">
        Connect a seized device or select a mock corpus, record the legal authority, and
        begin a minimally-invasive Tier-0 triage.
      </p>

      {error && (
        <div className="card border-deletion/50 bg-deletion/10 p-3 mb-4 text-sm text-deletion">
          {error}
        </div>
      )}

      {/* Device selection */}
      <div className="card p-4 mb-4">
        <div className="label">Target device</div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          {devices?.real.map((d) => (
            <DeviceOption
              key={d.serial}
              selected={target?.id === d.serial}
              onClick={() => setTarget({ kind: "real", id: d.serial, label: d.serial })}
              title={d.serial}
              subtitle={`Connected device · ${d.state}`}
              live
            />
          ))}
          {devices?.mock.map((d) => (
            <DeviceOption
              key={d.id}
              selected={target?.id === d.id}
              onClick={() => setTarget({ kind: "mock", id: d.id, label: d.label })}
              title={d.label}
              subtitle="Mock corpus (no phone — demo/dev)"
            />
          ))}
          {!devices?.real.length && !devices?.mock.length && (
            <div className="text-sm text-muted col-span-2 py-3">
              No devices or mock corpora found. Generate one with{" "}
              <code className="text-accent">python tools/make_corpus.py _corpus/device_A</code>.
            </div>
          )}
        </div>
      </div>

      {/* Case metadata */}
      <div className="card p-4 mb-4 grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div>
          <label className="label">Case ID</label>
          <input className="input" placeholder="auto (CASE-0001)" value={caseId} onChange={(e) => setCaseId(e.target.value)} />
        </div>
        <div>
          <label className="label">Examiner *</label>
          <input className="input" placeholder="Insp. R. Sharma" value={examiner} onChange={(e) => setExaminer(e.target.value)} />
        </div>
        <div>
          <label className="label">Legal authority</label>
          <input className="input" placeholder="Search Warrant # / consent ref" value={authority} onChange={(e) => setAuthority(e.target.value)} />
        </div>
        <div>
          <label className="label">Scope / minimisation</label>
          <input className="input" placeholder="e.g. comms + media + location only" value={scope} onChange={(e) => setScope(e.target.value)} />
        </div>
      </div>

      {/* Case brief -> AI collection plan */}
      <div className="card p-4 mb-4 border-accent/30">
        <div className="label mb-1">
          <span className="text-accent inline-flex items-center gap-1">
            <Sparkles className="inline h-3.5 w-3.5" strokeWidth={1.75} aria-hidden /> Case brief
          </span>{" "}
          — targeted, intelligent triage (optional)
        </div>
        <p className="text-xs text-muted mb-3">
          Describe the case in plain language, naming each party and their role. The tool
          extracts a case profile, searches prior case studies for what actually solved
          similar cases, and recommends which artifacts matter most here. Cheap artifacts
          are always collected; only expensive/root pulls are prioritised. Nothing is
          skipped silently.
        </p>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-2">
          <div>
            <label className="label">FIR / case number</label>
            <input
              className="input"
              placeholder="e.g. FIR-2026/0114"
              value={caseNumber}
              onChange={(e) => setCaseNumber(e.target.value)}
            />
          </div>
          <div>
            <label className="label">AI back-end</label>
            <select
              className="input"
              value={llmProvider}
              onChange={(e) => setLlmProvider(e.target.value as typeof llmProvider)}
            >
              {(llmStatus?.providers ?? []).map((p) => (
                <option key={p.name} value={p.name} disabled={!p.available}>
                  {p.label}
                  {p.available ? "" : " — unavailable"}
                </option>
              ))}
              {!llmStatus && <option value="heuristic">Heuristic (offline, default)</option>}
            </select>
          </div>
          <div className="flex items-end">
            <div className="text-[11px] text-muted leading-relaxed">
              {(() => {
                const chosen = llmStatus?.providers.find((p) => p.name === llmProvider);
                if (!chosen) {
                  return "Deterministic regex/entity extraction, zero network calls. Always available.";
                }
                return (
                  <>
                    <p>{chosen.note}</p>
                    {!chosen.available && (
                      <p className="text-warn mt-1">{chosen.reason} Runs deterministically instead.</p>
                    )}
                    {chosen.name === "ollama" && chosen.models && chosen.models.length > 0 && (
                      <p className="mt-1">
                        Installed: <span className="font-mono text-ink/80">{chosen.models.join(", ")}</span>
                        {" · using "}
                        <span className="font-mono text-ink/80">{llmStatus?.chat_model}</span>
                      </p>
                    )}
                  </>
                );
              })()}
            </div>
          </div>
        </div>

        <p className="text-[11px] text-muted leading-relaxed mb-2">
          Use forensic nomenclature: <b>accused</b> / <b>suspect</b> for the person
          under investigation, <b>victim</b> / <b>deceased</b> for the person harmed,
          plus <b>complainant</b>, <b>witness</b>, <b>panch witness</b>. Avoid
          “guilty” and “innocent” — those are trial outcomes, not investigative roles.
        </p>

        <textarea
          className="input min-h-[72px] resize-y"
          placeholder="e.g. Accused Laksh is a suspect in the murder of deceased Shubham. Complainant Kavita reported they met near the docks at midnight."
          value={brief}
          onChange={(e) => setBrief(e.target.value)}
        />
        <div className="flex items-center gap-3 mt-2 flex-wrap">
          <button className="btn" disabled={!brief.trim() || planning} onClick={previewPlan}>
            {planning ? "Planning…" : "Preview collection plan"}
          </button>
          {planError && <span className="text-xs text-deletion">{planError}</span>}
          {plan && (
            <span className="text-xs text-muted">
              Detected: <span className="text-ink font-medium">{plan.profile.crime_label}</span>{" "}
              · {plan.profile.extraction_method} · planning basis{" "}
              <span className="text-ink">{plan.plan.evidence_basis}</span>
              {plan.case_bank_size > 0 && <> · {plan.case_bank_size} case studies</>}
              {plan.retrieval_mode && plan.retrieval_mode !== "none" && (
                <>
                  {" · retrieval "}
                  <span className={plan.retrieval_mode === "hybrid" ? "text-live" : "text-ink"}>
                    {plan.retrieval_mode === "hybrid"
                      ? `hybrid (BM25 + ${plan.embedding?.model ?? "local embeddings"})`
                      : "lexical (BM25 only)"}
                  </span>
                </>
              )}
            </span>
          )}
        </div>
        {plan?.retrieval_mode === "lexical" && plan?.embedding?.reason && (
          <p className="text-[11px] text-muted mt-1.5 leading-relaxed">
            Precedent retrieval matched on words only — {plan.embedding.reason} The plan is
            still valid; it just could not use meaning-based similarity to find studies
            phrased differently from this brief.
          </p>
        )}
        {plan?.profile.llm_degraded_from && (
          <p className="text-[11px] text-warn mt-1.5 leading-relaxed">
            The '{plan.profile.llm_degraded_from}' back-end was requested but unreachable —
            this plan was extracted with the deterministic heuristic path instead. The
            acquisition below will attempt the same back-end again and degrade the same way
            if it is still unreachable.
          </p>
        )}

        {/* Nomenclature coaching — advisory, never blocking */}
        {plan && plan.profile.nomenclature_warnings.length > 0 && (
          <div className="mt-3 rounded-md border border-warn/40 bg-warn/5 p-3 space-y-1">
            {plan.profile.nomenclature_warnings.map((w, i) => (
              <div key={i} className="text-[11px] leading-relaxed">
                <span className={w.severity === "warn" ? "text-warn" : "text-muted"}>
                  {w.severity === "warn" ? (
                    <AlertTriangle className="inline h-3.5 w-3.5" strokeWidth={1.75} aria-hidden />
                  ) : (
                    "ℹ"
                  )}
                </span>{" "}
                <span className="text-muted">{w.message}</span>
              </div>
            ))}
          </div>
        )}

        {plan && (
          <div className="mt-4 grid grid-cols-1 lg:grid-cols-2 gap-4">
            {/* Parties, in canonical roles */}
            <div className="rounded-md border border-line p-3">
              <div className="text-xs uppercase tracking-wider text-muted mb-2">
                Parties (forensic nomenclature)
              </div>
              {plan.profile.roles.filter((r) => r.role !== "third_party").length > 0 ? (
                <div className="space-y-1 mb-3">
                  {plan.profile.roles
                    .filter((r) => r.role !== "third_party")
                    .map((r) => (
                      <div key={r.name} className="flex items-center justify-between text-xs">
                        <span className={r.adverse ? "text-deletion font-medium" : "text-ink"}>
                          {r.name}
                        </span>
                        <span className="flex items-center gap-2">
                          <span className="text-muted">{r.label}</span>
                          <span className="text-[10px] text-muted font-mono">
                            {Math.round(r.confidence * 100)}%
                          </span>
                        </span>
                      </div>
                    ))}
                </div>
              ) : (
                <p className="text-[11px] text-muted mb-3">
                  No party roles detected — name the parties and their roles for precise
                  scoring.
                </p>
              )}
              <ProfileChips label="Locations" items={plan.profile.locations} tone="text-accent" />
              <ProfileChips label="Keywords" items={plan.profile.keywords} tone="text-muted" />
            </div>

            {/* Scope consent: shown beside the plan it constrains. */}
            <label className="flex items-start gap-2 cursor-pointer rounded-md border border-line p-2 mb-3">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={planAllowTier2}
                onChange={(e) => setPlanAllowTier2(e.target.checked)}
              />
              <span className="text-[11px] text-muted leading-relaxed">
                Let the case brief recommend root-only (Tier-2) app-database pulls.
                Untick to keep the plan to what a non-rooted device can reach — the
                artifacts it would have recommended are still listed, with the reason,
                so nothing is hidden.
              </span>
            </label>

            {/* Priority plan */}
            <div className="rounded-md border border-line p-3">
              <div className="text-xs uppercase tracking-wider text-muted mb-2">
                Recommended focus (prioritise, never exclude)
              </div>
              <div className="space-y-1 max-h-52 overflow-auto">
                {plan.plan.artifacts.map((a) => (
                  <div key={a.artifact} className="flex items-center justify-between text-xs">
                    <span className="flex items-center gap-1.5">
                      <span className={a.collect ? "text-live" : "text-muted"}>
                        {a.collect ? "●" : "○"}
                      </span>
                      {a.label}
                      {a.adjustment && (
                        <span
                          className={
                            a.adjustment === "promoted" ? "text-live" : "text-warn"
                          }
                          title={a.evidence.join(" · ")}
                        >
                          {a.adjustment === "promoted" ? "▲" : "▼"}
                        </span>
                      )}
                    </span>
                    <span className="flex items-center gap-1.5">
                      <PriorityPill p={a.priority} />
                      <span className="text-muted">{a.cost}</span>
                    </span>
                  </div>
                ))}
              </div>
              {plan.plan.deprioritised.length > 0 && (
                <div className="text-[11px] text-muted mt-2 leading-relaxed border-t border-line pt-2">
                  Opt-in (not auto-collected, logged):{" "}
                  <DeprioritisedList items={plan.plan.deprioritised} />
                  {plan.plan.estimated_savings.estimated_minutes_saved > 0 && (
                    <>
                      {" "}
                      Deferring these saves roughly{" "}
                      {plan.plan.estimated_savings.estimated_minutes_saved} of{" "}
                      {plan.plan.estimated_savings.estimated_minutes_full_run} minutes
                      ({plan.plan.estimated_savings.basis}).
                    </>
                  )}
                </div>
              )}
              {/* Collected-in-part is the opposite risk to a deferral: the record
                  exists, and must not be read as a complete one. */}
              <PartialCollectionList items={plan.plan.partial_collection} />
              {/* Carries the synthetic-exemplar and prioritise-never-exclude
                  disclosures the plan is required to state. */}
              <div className="mt-2 border-t border-line pt-2">
                <PlanNotes notes={plan.plan.notes} />
              </div>
            </div>
          </div>
        )}

        {/* Actionable precedent: a similar case was solved on something we're skipping */}
        {plan && plan.plan.recommendations.length > 0 && (
          <div className="mt-4 rounded-md border border-warn/50 bg-warn/5 p-3">
            <div className="text-xs uppercase tracking-wider text-warn mb-2">
              Prior cases suggest reconsidering
            </div>
            <div className="space-y-2">
              {plan.plan.recommendations.map((r) => (
                <div key={r.artifact} className="text-xs">
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-ink">{r.message}</span>
                    {r.pipeline_flag && (
                      <button
                        className="btn-ghost shrink-0"
                        onClick={() => enableRecommendation(r.pipeline_flag!)}
                      >
                        Enable
                      </button>
                    )}
                  </div>
                  {r.reasons.slice(0, 1).map((reason, i) => (
                    <div key={i} className="text-[11px] text-muted mt-0.5">
                      {reason}
                    </div>
                  ))}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Retrieved precedent, with provenance */}
        {plan && plan.plan.precedents.length > 0 && (
          <div className="mt-4 rounded-md border border-line p-3">
            <div className="text-xs uppercase tracking-wider text-muted mb-2">
              Prior-case studies consulted
            </div>
            <div className="space-y-1.5">
              {plan.plan.precedents.map((p) => (
                <div key={p.case_number} className="text-xs">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-[11px] text-accent">{p.case_number}</span>
                    <span className="text-ink truncate">{p.title}</span>
                    <span className="text-[10px] text-muted font-mono ml-auto shrink-0">
                      {p.score.toFixed(2)}
                    </span>
                  </div>
                  <div className="text-[11px] text-muted">
                    Solved by: {p.decisive_artifacts.join(", ") || "—"} · {p.source}
                  </div>
                </div>
              ))}
            </div>
            <p className="text-[11px] text-warn mt-2 leading-relaxed border-t border-line pt-2">
              These studies rank artifacts for collection planning only. They are not
              evidence in this case and carry no precedential weight. Entries marked
              synthetic are expert-curated teaching exemplars, not real case records.
            </p>
          </div>
        )}
      </div>

      {/* Tier-1 options */}
      <div className="card p-4 mb-4">
        <div className="label mb-3">Tier-1 Helper (optional, real device only)</div>
        <div className="space-y-3">
          <label className="flex items-start gap-3 cursor-pointer">
            <input
              type="checkbox"
              className="mt-1"
              disabled={!target || target.kind !== "real"}
              checked={target?.kind === "real" ? tier1CollectAll : false}
              onChange={(e) => setTier1CollectAll(e.target.checked)}
            />
            <div>
              <div className="text-sm font-medium">Full collection (dump_all)</div>
              <div className="text-xs text-muted mt-1">
                Installs the Collector, grants the non-restricted permissions, and captures the
                MediaStore inventory (incl. trashed/favorite/owner-app), installed-app inventory
                (flags vault/messaging apps), accounts, calendar, and app-usage — then uninstalls.
                All steps logged in the audit trail.
              </div>
            </div>
          </label>

          <label className="flex items-start gap-3 cursor-pointer">
            <input
              type="checkbox"
              className="mt-1"
              disabled={!target || target.kind !== "real"}
              checked={target?.kind === "real" ? tier1Contacts : false}
              onChange={(e) => setTier1Contacts(e.target.checked)}
            />
            <div>
              <div className="text-sm font-medium">Tier-1 Contacts</div>
              <div className="text-xs text-muted mt-1">
                Installs Collector APK, grants READ_CONTACTS, exports contacts.json, then uninstalls.
                This is state-changing and will be logged in the audit trail.
              </div>
            </div>
          </label>

          <label className="flex items-start gap-3 cursor-pointer">
            <input
              type="checkbox"
              className="mt-1"
              disabled={!target || target.kind !== "real"}
              checked={target?.kind === "real" ? tier1Calllog : false}
              onChange={(e) => setTier1Calllog(e.target.checked)}
            />
            <div>
              <div className="text-sm font-medium">Tier-1 Call Log</div>
              <div className="text-xs text-muted mt-1">
                Installs Collector APK, grants READ_CALL_LOG, exports calllog.json, then uninstalls.
                This is state-changing and will be logged in the audit trail.
              </div>
            </div>
          </label>

          <label className="flex items-start gap-3 cursor-pointer">
            <input
              type="checkbox"
              className="mt-1"
              disabled={!target || target.kind !== "real"}
              checked={target?.kind === "real" ? tier1Sms : false}
              onChange={(e) => setTier1Sms(e.target.checked)}
            />
            <div>
              <div className="text-sm font-medium">Tier-1 SMS</div>
              <div className="text-xs text-muted mt-1">
                Installs Collector APK, grants READ_SMS, exports sms.json, then uninstalls.
                This is state-changing and will be logged in the audit trail.
              </div>
            </div>
          </label>
        </div>
      </div>

      {/* Tier-2 options (root) */}
      <div className="card p-4 mb-4">
        <div className="label mb-1">Tier-2 App Recovery (root required, real device only)</div>
        <p className="text-xs text-muted mb-3">
          These apps keep their chats in app-private storage, unreachable without root. On a
          rooted device the engine copies the databases via <code className="text-accent">su</code>,
          recovers live + deleted messages with confidence badges, and logs every step. On a
          non-rooted device the step is logged as skipped.
        </p>
        <div className="space-y-3">
          {[
            { label: "Tier-2 Telegram", db: "cache4.db", checked: tier2Telegram, set: setTier2Telegram },
            { label: "Tier-2 Instagram", db: "direct.db", checked: tier2Instagram, set: setTier2Instagram },
            { label: "Tier-2 Snapchat", db: "arroyo.db / main.db", checked: tier2Snapchat, set: setTier2Snapchat },
          ].map((t) => (
            <label key={t.label} className="flex items-start gap-3 cursor-pointer">
              <input
                type="checkbox"
                className="mt-1"
                disabled={!target || target.kind !== "real"}
                checked={target?.kind === "real" ? t.checked : false}
                onChange={(e) => t.set(e.target.checked)}
              />
              <div>
                <div className="text-sm font-medium">{t.label}</div>
                <div className="text-xs text-muted mt-1">
                  Root-pull <span className="font-mono">{t.db}</span> and run deep recovery.
                </div>
              </div>
            </label>
          ))}

          {/* Wi-Fi Credentials — separate from the messaging apps, distinct description */}
          <label className="flex items-start gap-3 cursor-pointer border-t border-line pt-3">
            <input
              type="checkbox"
              className="mt-1"
              disabled={!target || target.kind !== "real"}
              checked={target?.kind === "real" ? tier2Wifi : false}
              onChange={(e) => setTier2Wifi(e.target.checked)}
            />
            <div>
              <div className="text-sm font-medium flex items-center gap-1.5">
                <Wifi className="h-4 w-4" strokeWidth={1.75} aria-hidden /> Wi-Fi Credentials
              </div>
              <div className="text-xs text-muted mt-1">
                Root-probe{" "}
                <code className="text-accent">WifiConfigStore.xml</code>{" "}
                (Android 9+) and{" "}
                <code className="text-accent">wpa_supplicant.conf</code>{" "}
                (Android ≤8) to recover stored SSID/password pairs. No
                cracking — verbatim plaintext from OS storage. Logged as
                non-device-altering.
              </div>
            </div>
          </label>

          {/* Browser History Recovery */}
          <label className="flex items-start gap-3 cursor-pointer border-t border-line pt-3">
            <input
              type="checkbox"
              className="mt-1"
              disabled={!target || target.kind !== "real"}
              checked={target?.kind === "real" ? tier2BrowserHistory : false}
              onChange={(e) => setTier2BrowserHistory(e.target.checked)}
            />
            <div>
              <div className="text-sm font-medium flex items-center gap-1.5">
                <Globe2 className="h-4 w-4" strokeWidth={1.75} aria-hidden /> Browser History
              </div>
              <div className="text-xs text-muted mt-1">
                Root-pull the real per-browser{" "}
                <code className="text-accent">History</code> DB from Chrome,
                Brave, Samsung Internet and Edge, plus Firefox's{" "}
                <code className="text-accent">places.sqlite</code>. History
                lives in app-private storage, so this is unreachable without
                root. Runs the same deleted-row carver as everything else, so
                cleared history shows up as a deletion finding rather than
                silence.
              </div>
            </div>
          </label>

          {/* WhatsApp Backup Recovery */}
          <label className="flex items-start gap-3 cursor-pointer border-t border-line pt-3">
            <input
              type="checkbox"
              className="mt-1"
              disabled={!target || target.kind !== "real"}
              checked={target?.kind === "real" ? tier2WhatsappBackup : false}
              onChange={(e) => setTier2WhatsappBackup(e.target.checked)}
            />
            <div>
              <div className="text-sm font-medium flex items-center gap-1.5">
                <Unlock className="h-4 w-4" strokeWidth={1.75} aria-hidden /> WhatsApp Backup Recovery
              </div>
              <div className="text-xs text-muted mt-1">
                Root-pull the encryption key, then decrypt and analyse daily{" "}
                <code className="text-accent">msgstore.db.crypt*</code>{" "}
                backup files. Recovers live messages plus deleted rows (freelist,
                WAL, carving). Up to 5 most-recent backups processed by default.
                Requires{" "}
                <code className="text-accent">whatsapp-crypt14-decrypter</code>{" "}
                or <code className="text-accent">pycryptodome</code> as fallback.
              </div>
            </div>
          </label>
        </div>
      </div>

      {/* Deep system artifacts (root). Separated from app recovery because these read
          OS-level stores rather than a messaging app's database, and each carries a
          specific interpretation limit the examiner must know before enabling it. */}
      <div className="card p-4 mb-4">
        <div className="label mb-1">
          Deep System Artifacts (root required, real device only)
        </div>
        <p className="text-xs text-muted mb-3">
          OS-level stores that outlive app uninstalls and answer questions app databases
          cannot. Each is copied read-only via <code className="text-accent">su</code> and
          logged. Read the caveat on each one — they are easy to over-read.
        </p>
        <div className="space-y-3">
          {[
            {
              key: "bt",
              icon: Bluetooth,
              label: "Bluetooth bond store",
              checked: tier2BtConfig,
              set: setTier2BtConfig,
              body: (
                <>
                  Parses <code className="text-accent">bt_config.conf</code> for persistent
                  pairings, device class and vendor. <strong>Caveat:</strong> a bond
                  timestamp is when the pairing record was written — it is not a connection
                  time and does not place two devices together at any later moment. Link
                  keys are never displayed.
                </>
              ),
            },
            {
              key: "presence",
              icon: Puzzle,
              label: "App presence & execution",
              checked: tier2AppPresence,
              set: setTier2AppPresence,
              body: (
                <>
                  Reads <code className="text-accent">packages.xml</code>, the{" "}
                  <code className="text-accent">usagestats</code> tree and the Play-Protect
                  APK-digest database, which survive an uninstall.{" "}
                  <strong>Caveat:</strong> installation evidence is not execution evidence —
                  execution is only claimed where a real foreground event exists.
                </>
              ),
            },
            {
              key: "antiforensics",
              icon: ShieldAlert,
              label: "User containers & privacy apps",
              checked: tier2AntiForensics,
              set: setTier2AntiForensics,
              body: (
                <>
                  Enumerates Android users (work profiles, dual-app clones, Secure Folder),
                  privacy/vault packages and the factory-reset trace.{" "}
                  <strong>Caveat:</strong> these are structural observations, never
                  conclusions about intent — a work profile or a privacy app has ordinary,
                  lawful uses.
                </>
              ),
            },
            {
              key: "recents",
              icon: AppWindow,
              label: "Recent tasks & screen snapshots",
              checked: tier2RecentTasks,
              set: setTier2RecentTasks,
              body: (
                <>
                  Catalogues <code className="text-accent">/data/system_ce/0/recent_tasks</code>{" "}
                  and its snapshots. <strong>Requires AFU</strong> — on a device that has not
                  been unlocked since boot this is skipped with a stated reason. Highly
                  volatile: cleared by swipe-away, force-stop and reboot, so absence proves
                  nothing.
                </>
              ),
            },
          ].map((t) => {
            const Icon = t.icon;
            return (
              <label
                key={t.key}
                className="flex items-start gap-3 cursor-pointer border-t border-line pt-3 first:border-t-0 first:pt-0"
              >
                <input
                  type="checkbox"
                  className="mt-1"
                  disabled={!target || target.kind !== "real"}
                  checked={target?.kind === "real" ? t.checked : false}
                  onChange={(e) => t.set(e.target.checked)}
                />
                <div>
                  <div className="text-sm font-medium flex items-center gap-1.5">
                    <Icon className="h-4 w-4" strokeWidth={1.75} aria-hidden /> {t.label}
                  </div>
                  <div className="text-xs text-muted mt-1">{t.body}</div>
                </div>
              </label>
            );
          })}
        </div>
      </div>

      <div className="flex items-center gap-3">
        <button className="btn-accent" disabled={!target || !examiner} onClick={start}>
          Begin Acquisition
        </button>
        {!examiner && <span className="text-xs text-muted">Examiner name required.</span>}
      </div>

      {/* Recent cases */}
      {cases.length > 0 && (
        <div className="mt-8">
          <div className="label">Open an existing case</div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {cases.map((c) => (
              <button key={c.case_id} onClick={() => onOpenCase(c.case_id)} className="card p-3 text-left hover:border-accent/50 transition-colors">
                <div className="font-mono text-sm text-accent">{c.case_id}</div>
                <div className="text-xs text-muted mt-1">{c.examiner} · {c.device}</div>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function ProfileChips({ label, items, tone }: { label: string; items: string[]; tone: string }) {
  if (!items || items.length === 0) return null;
  return (
    <div className="flex items-baseline gap-2 mb-1.5">
      <span className="text-[11px] uppercase tracking-wider text-muted w-16 shrink-0">{label}</span>
      <div className="flex flex-wrap gap-1">
        {items.map((it, i) => (
          <span key={i} className={`text-xs rounded bg-panel px-1.5 py-0.5 border border-line ${tone}`}>
            {it}
          </span>
        ))}
      </div>
    </div>
  );
}

function PriorityPill({ p }: { p: "high" | "medium" | "low" }) {
  const cls = {
    high: "text-deletion border-deletion/40 bg-deletion/10",
    medium: "text-warn border-warn/40 bg-warn/10",
    low: "text-muted border-line bg-panel",
  }[p];
  return <span className={`rounded border px-1.5 py-0.5 text-[10px] font-semibold uppercase ${cls}`}>{p}</span>;
}

function DeviceOption({
  selected,
  onClick,
  title,
  subtitle,
  live,
}: {
  selected: boolean;
  onClick: () => void;
  title: string;
  subtitle: string;
  live?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      className={`text-left p-3 rounded-md border transition-colors ${
        selected ? "border-accent bg-accent/10" : "border-line hover:border-muted"
      }`}
    >
      <div className="flex items-center gap-2">
        {live && <span className="h-2 w-2 rounded-full bg-live animate-pulse" />}
        <span className="font-medium text-sm">{title}</span>
      </div>
      <div className="text-xs text-muted mt-0.5">{subtitle}</div>
    </button>
  );
}

function ProgressScreen({ progress, elapsed }: { progress: Progress | null; elapsed: number }) {
  const pct = Math.round((progress?.pct ?? 0) * 100);
  const mm = String(Math.floor(elapsed / 60)).padStart(2, "0");
  const ss = String(elapsed % 60).padStart(2, "0");
  const stageIdx = STAGES.indexOf(progress?.stage ?? "init");
  return (
    <div className="max-w-2xl mx-auto p-8 flex flex-col items-center justify-center min-h-[70vh]">
      <div className="w-full card p-8">
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-lg font-semibold">Acquisition in progress</h2>
          <span className="font-mono text-2xl tabular-nums text-accent">
            {mm}:{ss}
          </span>
        </div>
        <div className="h-3 rounded-full bg-panel overflow-hidden mb-2">
          <div
            className="h-full bg-accent transition-all duration-300"
            style={{ width: `${pct}%` }}
          />
        </div>
        <div className="flex justify-between text-sm mb-6">
          <span className="text-muted">{progress?.detail}</span>
          <span className="font-mono">{pct}%</span>
        </div>
        <div className="grid gap-1" style={{ gridTemplateColumns: `repeat(${STAGES.length}, minmax(0, 1fr))` }}>
          {STAGES.map((s, i) => (
            <div
              key={s}
              title={s}
              className={`h-1.5 rounded-full ${i <= stageIdx ? "bg-accent" : "bg-line"}`}
            />
          ))}
        </div>
        <p className="text-xs text-muted mt-6 leading-relaxed">
          Target: readable preview within 5–10 minutes. Every action is being written to the
          append-only audit log as it happens.
        </p>
      </div>
    </div>
  );
}