import { useEffect, useState } from "react";
import { AlertTriangle, Stethoscope } from "lucide-react";
import { api } from "../lib/api";
import { fmtTs } from "../lib/hooks";
import { StatCard } from "../components/common";

// ---------------------------------------------------------------------------
// Types — mirror engine/triage/device_state.py (capture_device_state,
// diff_device_state, verify_teardown, device_state_summary). Everything is
// optional: a run that aborted mid-way still writes a partial record, and a
// missing field must render as "not recorded" rather than as a pass.
// ---------------------------------------------------------------------------
export type TeardownVerdict = "clean" | "residual" | "unverified";

export interface ProbeDiff {
  probe: string;
  before: string;
  after: string;
}

export interface ResidueItem {
  kind: string;
  subject: string;
  detail: string;
}

export interface TeardownLedger {
  package?: string;
  installed?: boolean;
  granted_permissions?: string[];
  appops_set?: string[];
  activities_launched?: string[];
  files_written_to_device?: string[];
}

export interface DeviceStateDiff {
  expected_drift?: ProbeDiff[];
  unexpected_changes?: ProbeDiff[];
  unavailable_probes?: string[];
  permissions_added?: string[];
  permissions_removed?: string[];
  appops_added?: string[];
  appops_removed?: string[];
  helper_present_before?: boolean | null;
  helper_present_after?: boolean | null;
  returned_to_found_state?: boolean;
  caveats?: string[];
  error?: string;
}

export interface DeviceStateTeardown {
  verdict?: TeardownVerdict;
  residue?: ResidueItem[];
  unverified?: string[];
  detail?: string;
  ledger?: TeardownLedger;
  checked_at?: string;
}

export interface DeviceStateSnapshot {
  phase?: string;
  captured_at?: string;
  probes?: Record<string, string>;
  helper_package_present?: boolean | null;
  granted_permissions?: string[];
  appops_allowed?: string[];
  caveats?: string[];
  not_captured?: boolean;
  reason?: string;
}

export interface DeviceStateSummary {
  teardown_verdict?: string;
  residual_changes?: number;
  unexpected_differences?: number;
  expected_drift?: number;
  unavailable_probes?: number;
  returned_to_found_state?: boolean;
  statement?: string;
}

export interface DeviceStateRecord {
  pre?: DeviceStateSnapshot;
  post?: DeviceStateSnapshot;
  diff?: DeviceStateDiff;
  teardown?: DeviceStateTeardown;
  summary?: DeviceStateSummary;
}

// Each Counts stat card jumps to the matching section further down the same page rather
// than filtering — the four counts back four independently-rendered sections (residue is
// only ever one of several diff tables, not rows sliced out of one shared table), so a
// scroll anchor fits this data shape better than a table filter.
function scrollToSection(id: string) {
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

const TONE = {
  live: { bg: "#e4f4ea", text: "#1c7d3f", border: "#1c7d3f" },
  carved: { bg: "#f6ecd4", text: "#a6741a", border: "#a6741a" },
  deletion: { bg: "#f6dedd", text: "#a5322f", border: "#a5322f" },
} as const;

type ToneName = keyof typeof TONE;

interface VerdictCopy {
  tone: ToneName;
  label: string;
  headline: string;
  body: string;
}

// "unverified" is deliberately amber and worded as a failure to check. It must never
// share the green of "clean" — "we could not look" is not "we looked and it was gone".
const VERDICTS: Record<TeardownVerdict, VerdictCopy> = {
  clean: {
    tone: "live",
    label: "CLEAN",
    headline: "Every state-changing action was re-checked on the device and confirmed reversed",
    body:
      "The device was re-queried after teardown. Nothing this acquisition installed, granted or " +
      "set was still present. This covers the actions recorded in the ledger below and what the " +
      "read-only probes can observe — it is not a proof that nothing else on the device changed.",
  },
  residual: {
    tone: "deletion",
    label: "RESIDUAL",
    headline: "Changes made by this acquisition were still present on the device",
    body:
      "At least one modification survived teardown and is itemised below. The device was NOT " +
      "returned to its found state. These changes must be disclosed and the device re-checked; " +
      "do not describe the handback as clean.",
  },
  unverified: {
    tone: "carved",
    label: "UNVERIFIED — NOT A PASS",
    headline: "Reversal could not be confirmed — the device did not answer the verification queries",
    body:
      "This acquisition made state-changing actions, and the check that they were undone could " +
      "not be completed. The device state at handback is UNKNOWN, not clean. Treat reversal as " +
      "unconfirmed and re-check the device before returning it.",
  },
};

function Banner({ verdict }: { verdict: TeardownVerdict }) {
  const v = VERDICTS[verdict];
  const c = TONE[v.tone];
  return (
    <div
      className="rounded-lg p-5 mb-4"
      style={{ background: c.bg, border: `2px solid ${c.border}`, color: c.text }}
    >
      <div className="flex items-center gap-3 mb-2">
        <span
          className="text-[11px] font-bold uppercase tracking-wider rounded px-2 py-0.5"
          style={{ background: c.border, color: c.bg }}
        >
          {v.label}
        </span>
        <span className="text-[11px] uppercase tracking-wider opacity-70">
          teardown.verdict = {verdict}
        </span>
      </div>
      <div className="text-lg font-bold leading-snug mb-2">{v.headline}</div>
      <p className="text-sm leading-relaxed">{v.body}</p>
    </div>
  );
}

function Chip({ text, tone, note }: { text: string; tone: "bad" | "good"; note: string }) {
  const c = tone === "bad" ? TONE.deletion : TONE.live;
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded px-2 py-1 text-xs font-mono"
      style={{ background: c.bg, color: c.text, border: `1px solid ${c.border}` }}
      title={note}
    >
      <span className="font-sans font-semibold uppercase text-[10px] tracking-wider">{note}</span>
      {text}
    </span>
  );
}

function DiffTable({ rows }: { rows: ProbeDiff[] }) {
  return (
    <div className="card overflow-auto">
      <table className="w-full text-sm">
        <thead>
          <tr>
            <th className="th w-48">Probe</th>
            <th className="th">Before (found state)</th>
            <th className="th">After (handback state)</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={`${r.probe}-${i}`}>
              <td className="td font-mono text-xs text-muted break-all">{r.probe}</td>
              <td className="td font-mono text-xs whitespace-pre-wrap break-all">{r.before}</td>
              <td className="td font-mono text-xs whitespace-pre-wrap break-all">{r.after}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function LedgerRow({ label, items, note }: { label: string; items: string[]; note: string }) {
  return (
    <div className="border-t border-line py-2.5 first:border-t-0 first:pt-0">
      <div className="text-[11px] uppercase tracking-wider text-muted mb-1">{label}</div>
      {items.length === 0 ? (
        <div className="text-sm text-muted">None — this acquisition performed no such action.</div>
      ) : (
        <ul className="space-y-1">
          {items.map((it, i) => (
            <li key={i} className="font-mono text-xs text-ink break-all">
              {it}
            </li>
          ))}
        </ul>
      )}
      <div className="text-xs text-muted mt-1 leading-relaxed">{note}</div>
    </div>
  );
}

export function DeviceStateView({ caseId }: { caseId: string }) {
  const [record, setRecord] = useState<DeviceStateRecord | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    api
      .dataset<DeviceStateRecord>(caseId, "device_state")
      .then((d) => alive && setRecord(d ?? {}))
      .catch(() => alive && setRecord({}))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [caseId]);

  if (loading) return <div className="p-8 text-muted text-sm">Loading device state…</div>;

  const rec: DeviceStateRecord = record ?? {};
  const captured = Object.keys(rec).length > 0;

  const header = (
    <div className="mb-5">
      <h1 className="text-xl font-bold mb-1 flex items-center gap-2">
        <Stethoscope className="h-4 w-4" strokeWidth={1.75} aria-hidden /> Device State &amp; Reversal
        <span className="text-xs font-normal text-muted bg-panel-2 border border-line rounded px-2 py-0.5 ml-1">
          Tier 0 — Read-only probes
        </span>
      </h1>
      <p className="text-sm text-muted">
        Was the device handed back as it was found? Pre- and post-acquisition snapshots taken with
        query-only commands, diffed, plus verification that every Tier-1 state change was undone.
      </p>
    </div>
  );

  // Honest empty state: no snapshot means reversal is unverified, not successful.
  if (!captured) {
    return (
      <div className="p-6 max-w-5xl mx-auto">
        {header}
        <div className="card p-6 max-w-3xl">
          <div className="text-warn font-semibold mb-2">
            No post-acquisition state snapshot was recorded for this case
          </div>
          <p className="text-sm text-muted leading-relaxed">
            No <code className="text-ink font-mono">device_state</code> record exists, so there is
            no before/after comparison and no confirmation that anything this acquisition changed
            on the device was reversed. Reversal is therefore{" "}
            <strong className="text-ink">unverified</strong> — which is not the same as clean.
          </p>
          <p className="text-sm text-muted leading-relaxed mt-2">
            If a Tier-1 helper was installed or a permission was granted during this run, it may
            still be in place. Do not state on the record that the device was returned to its found
            state; re-query the device directly before handback.
          </p>
        </div>
      </div>
    );
  }

  const teardown = rec.teardown ?? {};
  const diff = rec.diff ?? {};
  const summary = rec.summary ?? {};
  const ledger = teardown.ledger ?? {};

  const rawVerdict = teardown.verdict ?? summary.teardown_verdict;
  const verdict: TeardownVerdict =
    rawVerdict === "clean" || rawVerdict === "residual" ? rawVerdict : "unverified";

  const residue = teardown.residue ?? [];
  const unverifiedItems = teardown.unverified ?? [];
  const expected = diff.expected_drift ?? [];
  const unexpected = diff.unexpected_changes ?? [];
  const unavailable = diff.unavailable_probes ?? [];
  const permsAdded = diff.permissions_added ?? [];
  const permsRemoved = diff.permissions_removed ?? [];
  const opsAdded = diff.appops_added ?? [];
  const opsRemoved = diff.appops_removed ?? [];
  const caveats = diff.caveats ?? [];

  const returned = diff.returned_to_found_state ?? summary.returned_to_found_state;

  return (
    <div className="p-6 max-w-5xl mx-auto">
      {header}

      <Banner verdict={verdict} />

      {/* The examiner-facing sentence, front and centre. */}
      <div className="card p-4 mb-5">
        <div className="text-[11px] uppercase tracking-wider text-muted mb-1.5">
          Statement for the record
        </div>
        <p className="text-base text-ink leading-relaxed">
          {summary.statement ??
            "No summary statement was written for this case. The verdict above is derived from the raw teardown record; treat the handback state as unconfirmed until it is re-checked."}
        </p>
        {teardown.detail && (
          <p className="text-sm text-muted leading-relaxed mt-3 pt-3 border-t border-line">
            {teardown.detail}
          </p>
        )}
        <div className="flex flex-wrap gap-4 mt-3 pt-3 border-t border-line text-xs text-muted">
          <span>
            Pre-snapshot:{" "}
            <span className="font-mono text-ink">{fmtTs(rec.pre?.captured_at)}</span>
          </span>
          <span>
            Post-snapshot:{" "}
            <span className="font-mono text-ink">{fmtTs(rec.post?.captured_at)}</span>
            {rec.post?.not_captured && (
              <span className="text-warn"> — not captured: {rec.post.reason ?? "reason not recorded"}</span>
            )}
          </span>
          <span>
            Verified at: <span className="font-mono text-ink">{fmtTs(teardown.checked_at)}</span>
          </span>
          <span>
            Returned to found state:{" "}
            {returned === undefined ? (
              <span className="text-warn font-semibold">not recorded</span>
            ) : returned ? (
              <span className="text-live font-semibold">yes (observed surface only)</span>
            ) : (
              <span className="text-deletion font-semibold">no</span>
            )}
          </span>
        </div>
      </div>

      {/* Counts. Each card jumps to its section below — residual changes only when there is
          a residue table to jump to (it's the one section that doesn't always render). */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-5">
        <StatCard
          n={residue.length}
          label="Residual changes"
          tone={residue.length ? "text-deletion" : "text-ink"}
          onClick={residue.length > 0 ? () => scrollToSection("residual-section") : undefined}
        />
        <StatCard
          n={unexpected.length}
          label="Unexpected changes"
          tone={unexpected.length ? "text-deletion" : "text-ink"}
          onClick={() => scrollToSection("unexpected-section")}
        />
        <StatCard
          n={expected.length}
          label="Expected drift"
          tone="text-muted"
          onClick={() => scrollToSection("expected-section")}
        />
        <StatCard
          n={unavailable.length}
          label="Unavailable probes"
          tone={unavailable.length ? "text-warn" : "text-ink"}
          onClick={() => scrollToSection("unavailable-section")}
        />
      </div>

      {/* Residue — only meaningful when something actually survived. */}
      {residue.length > 0 && (
        <>
          <h2 id="residual-section" className="text-sm font-semibold text-deletion mb-2 scroll-mt-4">
            Residual modifications still on the device ({residue.length})
          </h2>
          <div className="card overflow-auto mb-5">
            <table className="w-full text-sm">
              <thead>
                <tr>
                  <th className="th w-32">Kind</th>
                  <th className="th w-64">Subject</th>
                  <th className="th">Detail</th>
                </tr>
              </thead>
              <tbody>
                {residue.map((r, i) => (
                  <tr key={i}>
                    <td className="td">
                      <span
                        className="inline-block rounded px-2 py-0.5 text-[11px] font-semibold"
                        style={{
                          background: TONE.deletion.bg,
                          color: TONE.deletion.text,
                          border: `1px solid ${TONE.deletion.border}`,
                        }}
                      >
                        {r.kind}
                      </span>
                    </td>
                    <td className="td font-mono text-xs break-all">{r.subject}</td>
                    <td className="td text-xs text-muted leading-relaxed">{r.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {/* Checks that could not be completed. */}
      {unverifiedItems.length > 0 && (
        <div className="card p-4 mb-5 border-warn/40 bg-warn/5">
          <div className="text-warn font-semibold text-sm mb-2">
            Checks that could not be completed ({unverifiedItems.length})
          </div>
          <ul className="space-y-1 text-xs text-warn">
            {unverifiedItems.map((u, i) => (
              <li key={i} className="font-mono break-all">
                {u}
              </li>
            ))}
          </ul>
          <p className="text-xs text-muted mt-2 leading-relaxed">
            Each line is a verification the device did not answer. The underlying state is unknown —
            it is not evidence that the change was reversed.
          </p>
        </div>
      )}

      {/* Permission / appop deltas. */}
      <h2 className="text-sm font-semibold text-ink mb-2">Permission and appop deltas</h2>
      <div className="card p-4 mb-5 space-y-3">
        <div>
          <div className="text-[11px] uppercase tracking-wider text-muted mb-1.5">
            Still held after acquisition
          </div>
          {permsAdded.length === 0 && opsAdded.length === 0 ? (
            <div className="text-sm text-muted">
              None — no permission or appop present at handback was absent when the device was
              received.
            </div>
          ) : (
            <div className="flex flex-wrap gap-2">
              {permsAdded.map((p) => (
                <Chip key={`pa-${p}`} text={p} tone="bad" note="permission added" />
              ))}
              {opsAdded.map((o) => (
                <Chip key={`oa-${o}`} text={o} tone="bad" note="appop added" />
              ))}
            </div>
          )}
        </div>
        <div className="border-t border-line pt-3">
          <div className="text-[11px] uppercase tracking-wider text-muted mb-1.5">
            Reverted during teardown
          </div>
          {permsRemoved.length === 0 && opsRemoved.length === 0 ? (
            <div className="text-sm text-muted">
              None recorded as reverted. On a run that granted nothing, this is expected.
            </div>
          ) : (
            <div className="flex flex-wrap gap-2">
              {permsRemoved.map((p) => (
                <Chip key={`pr-${p}`} text={p} tone="good" note="reverted" />
              ))}
              {opsRemoved.map((o) => (
                <Chip key={`or-${o}`} text={o} tone="good" note="reverted" />
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Two clearly separated diff tables. */}
      <h2 id="unexpected-section" className="text-sm font-semibold text-deletion mb-1 scroll-mt-4">
        Unexpected changes ({unexpected.length})
      </h2>
      <p className="text-xs text-muted mb-2 leading-relaxed">
        Differences the acquisition does not inherently cause. Each one needs an explanation before
        the device is described as returned to its found state.
      </p>
      {unexpected.length === 0 ? (
        <div className="card p-4 mb-5 text-sm text-muted">
          No unexpected difference was observed between the pre- and post-acquisition snapshots.
          This covers only what the read-only probes can see.
        </div>
      ) : (
        <div className="mb-5">
          <DiffTable rows={unexpected} />
        </div>
      )}

      <h2 id="expected-section" className="text-sm font-semibold text-ink mb-1 scroll-mt-4">
        Expected drift ({expected.length}){" "}
        <span className="text-muted font-normal">— not examiner modifications</span>
      </h2>
      <p className="text-xs text-muted mb-2 leading-relaxed">
        Clock, uptime, battery and screen state move during <em>any</em> acquisition, including one
        that touches nothing. These rows are listed separately precisely so they cannot be mistaken
        for something the examiner changed.
      </p>
      {expected.length === 0 ? (
        <div className="card p-4 mb-5 text-sm text-muted">
          No drift recorded in the clock, uptime, battery or screen probes.
        </div>
      ) : (
        <div className="mb-5">
          <DiffTable rows={expected} />
        </div>
      )}

      {/* Probes that could not be compared. */}
      <h2 id="unavailable-section" className="text-sm font-semibold text-ink mb-2 scroll-mt-4">
        Probes that could not be compared ({unavailable.length})
      </h2>
      <div className="card p-4 mb-5">
        {unavailable.length === 0 ? (
          <p className="text-sm text-muted">
            Every probe returned a legible value in both snapshots.
          </p>
        ) : (
          <>
            <div className="flex flex-wrap gap-2">
              {unavailable.map((p) => (
                <span
                  key={p}
                  className="rounded px-2 py-1 text-xs font-mono"
                  style={{
                    background: TONE.carved.bg,
                    color: TONE.carved.text,
                    border: `1px solid ${TONE.carved.border}`,
                  }}
                >
                  {p}
                </span>
              ))}
            </div>
            <p className="text-xs text-muted mt-2 leading-relaxed">
              These probes failed in one or both snapshots, so no before/after comparison exists for
              them. An unavailable probe is not evidence that the underlying state was unchanged.
            </p>
          </>
        )}
      </div>

      {/* The teardown ledger: exactly what this run did to the device. */}
      <h2 className="text-sm font-semibold text-ink mb-1">Teardown ledger</h2>
      <p className="text-xs text-muted mb-2 leading-relaxed">
        Every device-altering action this run recorded, so reversal targets exactly what was done.
        Helper package:{" "}
        <code className="font-mono text-ink">{ledger.package ?? "not recorded"}</code>
      </p>
      <div className="card p-4 mb-5">
        <LedgerRow
          label="Installed"
          items={ledger.installed ? [ledger.package ?? "collector helper APK"] : []}
          note="An APK installed on the device by this acquisition. Its removal is verified above, not assumed."
        />
        <LedgerRow
          label="Permissions granted"
          items={ledger.granted_permissions ?? []}
          note="Granted with pm grant to the helper package. Only grants that actually succeeded are listed — revoking a grant that never happened would itself be a device change."
        />
        <LedgerRow
          label="Appops set"
          items={ledger.appops_set ?? []}
          note="Special-access operations set for the helper (e.g. GET_USAGE_STATS). These survive an uninstall on some builds, which is why they are verified separately."
        />
        <LedgerRow
          label="Activities launched"
          items={ledger.activities_launched ?? []}
          note="UI components started on the device. Launching an activity leaves usage-stats and recent-task traces that cannot be undone; they are disclosed rather than reversed."
        />
        <LedgerRow
          label="Files written to the device"
          items={ledger.files_written_to_device ?? []}
          note="Files this acquisition placed in shared storage. Any of these still present at handback appears in the residue table above."
        />
      </div>

      {/* Every diff caveat. */}
      <h2 className="text-sm font-semibold text-ink mb-2">
        Caveats <span className="text-muted font-normal">({caveats.length})</span>
      </h2>
      <div className="card p-4">
        {diff.error && (
          <p className="text-sm text-deletion leading-relaxed mb-3">
            The pre/post diff failed to compute: <span className="font-mono">{diff.error}</span>. The
            comparison above is incomplete and must not be read as agreement between the snapshots.
          </p>
        )}
        {caveats.length === 0 ? (
          <p className="text-sm text-muted">
            No caveats were recorded against this diff. That is the absence of a recorded caveat,
            not an assurance that the snapshots capture everything that changed.
          </p>
        ) : (
          <ul className="space-y-2">
            {caveats.map((c, i) => (
              <li key={i} className="flex gap-2 text-sm text-warn leading-relaxed">
                <AlertTriangle className="h-4 w-4 shrink-0" strokeWidth={1.75} aria-hidden />
                <span>{c}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
