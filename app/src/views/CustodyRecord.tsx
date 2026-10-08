import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import type { CustodyPhase, CustodyRecord, Handover } from "../lib/types";
import { bytes } from "../components/common";

/** `2026-08-05T16:46:12Z` → `2026-08-05 16:46:12 UTC`. Shown as recorded; nothing is shifted to local time. */
function stamp(iso?: string | null): string {
  return iso ? iso.replace("T", " ").replace(/Z$/, " UTC") : "—";
}

const VERDICT: Record<string, { label: string; tone: string; note: string }> = {
  clean: { label: "Returned to found state", tone: "text-live border-live/40 bg-live/5", note: "Every change made to the device was reversed and re-checked." },
  retained: { label: "Helper app left on the device", tone: "text-accent border-accent/40 bg-accent/5", note: "Left installed by the examiner's choice; disclosed here and in the report." },
  residual: { label: "Changes remain on the device", tone: "text-deletion border-deletion/40 bg-deletion/5", note: "Something made by this acquisition was still present afterwards." },
  unverified: { label: "Not verified", tone: "text-warn border-warn/40 bg-warn/5", note: "The device could not be re-checked, so its state is unknown — not clean." },
};

const ACTION_LABEL: Record<string, string> = {
  received: "Received",
  released: "Released",
  transferred: "Transferred",
  examined: "Examined",
  returned: "Returned",
  sealed: "Sealed",
};

function Field({ k, v, mono }: { k: string; v?: string | null; mono?: boolean }) {
  return (
    <div className="grid grid-cols-[7.5rem_1fr] gap-2 py-1 text-xs">
      <div className="text-muted">{k}</div>
      <div className={`${mono ? "font-mono" : ""} break-all ${v ? "text-ink" : "text-warn"}`}>{v || "not recorded"}</div>
    </div>
  );
}

export function CustodyRecordPanel({ caseId }: { caseId: string }) {
  const [rec, setRec] = useState<CustodyRecord | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .custody(caseId)
      .then((r) => {
        setRec(r);
        setError(null);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [caseId]);
  useEffect(load, [load]);

  if (error) return <div className="card p-4 text-sm text-deletion">Could not load the custody record: {error}</div>;
  if (!rec) return <div className="p-8 text-muted text-sm animate-pulse">Loading custody record…</div>;

  const chain = rec.integrity.audit_chain;
  const verdict = VERDICT[rec.device_state.verdict] ?? VERDICT.unverified;

  const download = () => {
    const blob = new Blob([JSON.stringify(rec, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `custody-${rec.case.case_id}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <div className="flex-1 overflow-auto space-y-4 pb-6">
      {rec.condition_on_receipt.synthetic && (
        <div className="card p-3 border-warn/40 bg-warn/5 text-xs text-warn">
          This case holds synthetic demonstration data, not a real device. Nothing here evidences a real acquisition.
        </div>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <div className="card p-4">
          <div className="label mb-2">Case &amp; authority</div>
          <Field k="Case ID" v={rec.case.case_id} mono />
          <Field k="Examiner" v={rec.case.examiner} />
          <Field k="Legal authority" v={rec.case.legal_authority} />
          <Field k="Scope" v={rec.case.scope_note} />
          <Field k="Opened" v={stamp(rec.case.opened_at)} mono />
          <Field k="Tool" v={rec.case.tool} />
        </div>

        <div className="card p-4">
          <div className="label mb-2">Device received</div>
          <Field k="Device" v={[rec.device.manufacturer, rec.device.model].filter(Boolean).join(" ")} />
          <Field k="Serial" v={rec.device.serial} mono />
          <Field k="IMEI" v={rec.device.imei} mono />
          <Field k="Software" v={[rec.device.android_version && `Android ${rec.device.android_version}`, rec.device.os_skin].filter(Boolean).join(" · ")} />
          <Field k="Rooted" v={rec.device.rooted ? "yes" : "no"} />
          <div className="mt-2 pt-2 border-t border-line text-[11px] uppercase tracking-wider text-muted">Condition on receipt</div>
          <Field k="Screen" v={rec.condition_on_receipt.screen_locked == null ? "" : rec.condition_on_receipt.screen_locked ? "locked" : "unlocked"} />
          <Field k="Battery" v={rec.condition_on_receipt.battery_level == null ? "" : `${rec.condition_on_receipt.battery_level}%`} />
          <Field k="Device clock" v={rec.condition_on_receipt.device_time} mono />
        </div>

        <div className="card p-4">
          <div className="label mb-2">Record integrity</div>
          <div className={`rounded-md border p-2 text-xs mb-2 ${chain.valid ? "text-live border-live/40 bg-live/5" : "text-deletion border-deletion/40 bg-deletion/5"}`}>
            <div className="font-semibold">
              {chain.valid
                ? `Audit log intact — ${chain.verified} of ${chain.total} entries re-checked`
                : chain.first_bad_line != null
                  ? `Audit log BROKEN at line ${chain.first_bad_line}`
                  : "Audit log could not be verified"}
            </div>
            <div className="mt-0.5 opacity-90">Re-checked just now from the start of the log.</div>
          </div>
          <Field k="Log head" v={chain.head} mono />
          <div className="mt-2 pt-2 border-t border-line" />
          <Field k="Evidence files" v={`${rec.integrity.evidence.files.toLocaleString()} · ${bytes(rec.integrity.evidence.bytes)}`} />
          <Field k="Evidence set hash" v={rec.integrity.evidence.set_sha256} mono />
          <details className="mt-2 text-[11px] text-muted leading-relaxed">
            <summary className="cursor-pointer">What this proves, and what it does not</summary>
            <p className="mt-1">
              The log head and the evidence-set hash are fingerprints. Write them on the custody form or send them to
              someone else now; if either differs later, the log or the evidence changed. {rec.integrity.seal_note}
            </p>
          </details>
        </div>
      </div>

      <div className={`card p-3 border text-sm ${verdict.tone}`}>
        <div className="flex flex-wrap items-baseline gap-x-3">
          <span className="font-semibold">Device after acquisition: {verdict.label}</span>
          <span className="text-xs opacity-90">
            {rec.device_state.device_altering_actions} action{rec.device_state.device_altering_actions === 1 ? "" : "s"} changed the device during this run
          </span>
        </div>
        <p className="text-xs mt-1 opacity-90">{rec.device_state.statement || verdict.note}</p>
      </div>

      <div>
        <h2 className="text-sm font-semibold text-ink mb-2">What happened, in order</h2>
        <div className="card p-4">
          <ol className="relative border-l border-line ml-2 space-y-5">
            {rec.timeline.map((p) => (
              <PhaseRow key={p.key} phase={p} />
            ))}
          </ol>
        </div>
      </div>

      <HandoverLog caseId={caseId} rec={rec} onSaved={load} />

      <div className="flex justify-end">
        <button className="btn-ghost text-xs" onClick={download}>
          Download this record (JSON)
        </button>
      </div>
    </div>
  );
}

function PhaseRow({ phase }: { phase: CustodyPhase }) {
  const [open, setOpen] = useState(false);
  const tone = phase.errors ? "bg-deletion" : phase.device_altering ? "bg-warn" : "bg-accent";
  return (
    <li className="ml-4">
      <span className={`absolute -left-[5px] mt-1.5 h-2.5 w-2.5 rounded-full ${tone}`} aria-hidden />
      <div className="flex flex-wrap items-baseline gap-x-3">
        <span className="text-sm font-semibold text-ink">{phase.title}</span>
        <span className="font-mono text-[11px] text-muted">
          {stamp(phase.started)}
          {phase.ended !== phase.started && ` → ${stamp(phase.ended).slice(11)}`}
        </span>
      </div>
      <div className="text-xs text-muted mt-0.5">
        {phase.actors.length ? `By ${phase.actors.join(", ")}` : "Actor not recorded"} · {phase.event_count} recorded step{phase.event_count === 1 ? "" : "s"}
        {phase.device_altering > 0 && <span className="text-warn font-semibold"> · {phase.device_altering} changed the device</span>}
        {phase.errors > 0 && <span className="text-deletion font-semibold"> · {phase.errors} failed</span>}
      </div>
      {phase.summary && <div className="text-xs text-ink mt-0.5">{phase.summary}</div>}
      {phase.events.length > 0 && (
        <button className="text-[11px] text-accent mt-1" onClick={() => setOpen((o) => !o)}>
          {open ? "Hide steps" : `Show ${phase.events.length} step${phase.events.length === 1 ? "" : "s"}`}
        </button>
      )}
      {open && (
        <table className="w-full text-xs mt-2">
          <tbody>
            {phase.events.map((e, i) => (
              <tr key={i} className="align-top">
                <td className="py-1 pr-3 font-mono text-[10px] text-muted whitespace-nowrap">{stamp(e.timestamp).slice(11)}</td>
                <td className="py-1 pr-3 font-mono text-[10px] text-muted whitespace-nowrap">{e.action}</td>
                <td className="py-1">
                  {e.detail}
                  {e.alters_device && <span className="ml-2 text-warn font-semibold">CHANGED DEVICE</span>}
                  {e.result === "error" && <span className="ml-2 text-deletion font-semibold">FAILED</span>}
                  {e.command && <div className="font-mono text-[10px] text-muted/70 break-all">$ {e.command}</div>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </li>
  );
}

function HandoverLog({ caseId, rec, onSaved }: { caseId: string; rec: CustodyRecord; onSaved: () => void }) {
  const blank = { action: "received", from_person: "", to_person: "", purpose: "", location: "", notes: "" };
  const [form, setForm] = useState(blank);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = (k: keyof typeof blank, v: string) => setForm((f) => ({ ...f, [k]: v }));

  const submit = async () => {
    setSaving(true);
    setError(null);
    try {
      await api.addHandover(caseId, form);
      setForm(blank);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <h2 className="text-sm font-semibold text-ink mb-2">Handovers — who held the evidence</h2>
      <div className="card overflow-auto mb-3">
        {rec.transfers.length === 0 ? (
          <div className="p-4 text-xs text-warn leading-relaxed">
            No handover has been recorded. The log above shows what the tool did; a chain of custody also needs to say
            who handed the device or evidence to whom, when and why. Record each handover below.
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr>
                <th className="th w-40">When</th>
                <th className="th w-24">Action</th>
                <th className="th">From</th>
                <th className="th">To</th>
                <th className="th">Purpose / place</th>
                <th className="th w-28">Recorded by</th>
              </tr>
            </thead>
            <tbody>
              {rec.transfers.map((t: Handover) => (
                <tr key={t.id}>
                  <td className="td font-mono text-xs text-muted">{stamp(t.at)}</td>
                  <td className="td text-xs font-semibold">{ACTION_LABEL[t.action] ?? t.action}</td>
                  <td className="td text-xs">{t.from_person || "—"}</td>
                  <td className="td text-xs">{t.to_person || "—"}</td>
                  <td className="td text-xs">
                    {t.purpose || "—"}
                    {t.location && <span className="text-muted"> · {t.location}</span>}
                    {t.notes && <div className="text-muted">{t.notes}</div>}
                    <div className="font-mono text-[10px] text-muted/70" title="Hash of the audit-log line that pins this entry">
                      log {t.audit_entry_hash.slice(0, 16)}…
                    </div>
                  </td>
                  <td className="td text-xs">{t.recorded_by}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="card p-4">
        <div className="label mb-2">Record a handover</div>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
          <label className="text-xs text-muted">
            What happened
            <select className="input mt-1" value={form.action} onChange={(e) => set("action", e.target.value)}>
              {rec.transfer_actions.map((a) => (
                <option key={a} value={a}>
                  {ACTION_LABEL[a] ?? a}
                </option>
              ))}
            </select>
          </label>
          <label className="text-xs text-muted">
            Handed over by (name, role)
            <input className="input mt-1" value={form.from_person} onChange={(e) => set("from_person", e.target.value)} placeholder="e.g. Insp. R. Sharma, IO" />
          </label>
          <label className="text-xs text-muted">
            Received by (name, role)
            <input className="input mt-1" value={form.to_person} onChange={(e) => set("to_person", e.target.value)} placeholder="e.g. Punya Surana, examiner" />
          </label>
          <label className="text-xs text-muted">
            Place
            <input className="input mt-1" value={form.location} onChange={(e) => set("location", e.target.value)} placeholder="e.g. Cyber lab, Pune" />
          </label>
          <label className="text-xs text-muted md:col-span-2">
            Purpose
            <input className="input mt-1" value={form.purpose} onChange={(e) => set("purpose", e.target.value)} placeholder="e.g. Forensic triage under warrant" />
          </label>
          <label className="text-xs text-muted md:col-span-2">
            Notes (seal numbers, condition)
            <input className="input mt-1" value={form.notes} onChange={(e) => set("notes", e.target.value)} />
          </label>
        </div>
        <div className="flex flex-wrap items-center gap-3 mt-3">
          <button className="btn" disabled={saving} onClick={submit}>
            {saving ? "Saving…" : "Add to chain of custody"}
          </button>
          {error && <span className="text-xs text-deletion">{error}</span>}
          <span className="text-[11px] text-muted">Written into the tamper-evident log; it cannot be edited or removed afterwards without the check above failing.</span>
        </div>
      </div>
    </div>
  );
}
