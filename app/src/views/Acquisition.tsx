import { useEffect, useRef, useState } from "react";
import { api, getSocket } from "../lib/api";
import type { DeviceListing, Progress } from "../lib/types";

const STAGES = [
  "init", "device", "tier1", "enumerate", "screenshot", "pull", "location", "recover", "flag",
  "timeline", "analysis", "persist", "report", "done",
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
  const [progress, setProgress] = useState<Progress | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tier1Contacts, setTier1Contacts] = useState(false);
  const [tier1Calllog, setTier1Calllog] = useState(false);
  const [tier1Sms, setTier1Sms] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const timerRef = useRef<number | null>(null);

  useEffect(() => {
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
        tier1_contacts: target.kind === "real" ? tier1Contacts : false,
        tier1_calllog: target.kind === "real" ? tier1Calllog : false,
        tier1_sms: target.kind === "real" ? tier1Sms : false,
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

      {/* Tier-1 options */}
      <div className="card p-4 mb-4">
        <div className="label mb-3">Tier-1 Helper (optional, real device only)</div>
        <div className="space-y-3">
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