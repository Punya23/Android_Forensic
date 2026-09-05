/**
 * EncryptedApps.tsx — encrypted messenger databases as a POSITIVE finding.
 *
 * The framing of this view is deliberate. "We found Signal's database, it is
 * 4.2 MB, it was last written on <date>, and its contents are cryptographically
 * unreachable" is a *finding*, not a gap. It establishes app presence, activity
 * volume and recency without ever claiming to read a message. This view therefore
 * renders existence, size, mtime, hash and the precise reason recovery fails —
 * and renders no message content of any kind, by construction.
 *
 * Datasets
 * --------
 *   encrypted_apps          (list)   per-database records
 *   encrypted_apps_summary  (object) roll-up counts
 *   fcm_records             (list)   raw push-payload fragments
 *   signal                  (object) Signal-specific databases
 */
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { api } from "../lib/api";
import { useDataset, fmtTs } from "../lib/hooks";
import { bytes } from "../components/common";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface EncryptedAppRecord {
  app: string;
  package: string;
  path: string;
  exists: boolean | null;
  size_bytes: number | null;
  modified: string | null;
  encryption_format: string;
  key_storage: string;
  recoverable: boolean | null;
  status: string;
  attachment_count: number | null;
  attachment_bytes: number | null;
  sha256: string;
  confidence: string;
  caveats?: string[];
}

export interface EncryptedAppsSummary {
  apps_checked?: number;
  present?: number;
  /** Checked, and the path was confirmed absent on the device. */
  not_present?: number;
  /** Never checked / not attempted at this tier — NOT the same as absent. */
  not_acquired?: number;
  encrypted?: number;
  recoverable?: number;
  total_bytes?: number;
  attachment_count?: number;
  attachment_bytes?: number;
  caveats?: string[];
  notes?: string[];
}

export interface SignalEncryptedDatabase {
  artifact_id: string;
  path: string;
  size_bytes: number | null;
  status: string;
  recoverable: boolean | null;
  reason: string;
}

export interface SignalReport {
  /**
   * Typed as unknown[] on purpose: this view counts these records and never
   * renders their fields, so there is no shape that could leak message content.
   */
  messages?: unknown[];
  encrypted_databases?: SignalEncryptedDatabase[];
}

export interface FcmRecord {
  app?: string;
  package?: string;
  from?: string;
  message_id?: string;
  collapse_key?: string;
  priority?: string | number | null;
  sent_at?: string | null;
  received_at?: string | null;
  size_bytes?: number | null;
  content_readable?: boolean | null;
  source?: string;
  caveats?: string[];
}

// ---------------------------------------------------------------------------
// Classification — "absent" and "unchecked" are different findings
// ---------------------------------------------------------------------------

type Presence = "present" | "not-present" | "not-acquired";

const NOT_ACQUIRED_RE = /not[\s_-]?(acquired|attempted|checked|collected)|skipped|unknown|no[\s_-]?access|denied|permission/i;

/**
 * Absence of evidence is not evidence of absence. A record only counts as
 * "not present on device" when the collector positively reported exists=false.
 * Anything ambiguous falls to "not acquired", never to "absent".
 */
function presenceOf(r: EncryptedAppRecord): Presence {
  if (NOT_ACQUIRED_RE.test(r.status ?? "")) return "not-acquired";
  if (r.exists === false) return "not-present";
  if (r.exists === true) return "present";
  return "not-acquired";
}

const CONF_COLORS: Record<string, string> = {
  live: "bg-live/10 text-live border-live/30",
  recovered: "bg-recovered/10 text-recovered border-recovered/30",
  carved: "bg-carved/10 text-carved border-carved/30",
  deletion: "bg-deletion/10 text-deletion border-deletion/30",
};

function Chip({ children, tone }: { children: ReactNode; tone: string }) {
  return (
    <span
      className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide border whitespace-nowrap ${tone}`}
    >
      {children}
    </span>
  );
}

function CaveatList({ items, title }: { items: string[]; title?: string }) {
  if (items.length === 0) return null;
  return (
    <div className="mt-2">
      {title && (
        <div className="text-[10px] uppercase tracking-wider text-warn font-semibold mb-1">
          {title}
        </div>
      )}
      <ul className="list-disc pl-4 space-y-0.5">
        {items.map((c, i) => (
          <li key={i} className="text-xs text-warn leading-relaxed">
            {c}
          </li>
        ))}
      </ul>
    </div>
  );
}

function Field({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-muted mb-0.5">{label}</div>
      <div className="text-sm text-ink font-mono break-all">{value}</div>
    </div>
  );
}

function StatBlock({
  n,
  label,
  detail,
  tone,
  derived,
  onClick,
  active,
}: {
  n: ReactNode;
  label: string;
  detail: string;
  tone: string;
  derived: boolean;
  onClick?: () => void;
  active?: boolean;
}) {
  const clickable = !!onClick;
  return (
    <div
      className={`card p-3 flex-1 min-w-[190px] ${
        clickable ? "cursor-pointer transition-colors hover:bg-panel-2" : ""
      } ${active ? "ring-1 ring-accent border-accent/50" : ""}`}
      onClick={onClick}
      role={clickable ? "button" : undefined}
      tabIndex={clickable ? 0 : undefined}
      onKeyDown={clickable ? (e) => (e.key === "Enter" || e.key === " ") && onClick!() : undefined}
    >
      <div className={`text-2xl font-bold ${tone}`}>{n}</div>
      <div className="text-[11px] uppercase tracking-wider text-muted mt-0.5">{label}</div>
      <p className="text-[11px] text-muted leading-relaxed mt-1.5">{detail}</p>
      <div className="text-[10px] text-muted mt-1 italic">
        {derived
          ? "derived from the per-database rows (no summary figure reported)"
          : "reported by the collector summary"}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Per-database card
// ---------------------------------------------------------------------------

function AppCard({ r }: { r: EncryptedAppRecord }) {
  const presence = presenceOf(r);
  const recoverable = r.recoverable === true;

  return (
    <div className="card p-4 mb-3">
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <span className="text-base font-semibold text-ink">{r.app || r.package || "—"}</span>
        {presence === "present" && <Chip tone="bg-live/10 text-live border-live/30">present</Chip>}
        {presence === "not-present" && (
          <Chip tone="bg-muted/10 text-muted border-line">not present on device</Chip>
        )}
        {presence === "not-acquired" && (
          <Chip tone="bg-carved/10 text-carved border-carved/30">not acquired</Chip>
        )}
        {r.encryption_format && (
          <Chip tone="bg-recovered/10 text-recovered border-recovered/30">
            {r.encryption_format}
          </Chip>
        )}
        {r.confidence && (
          <Chip tone={CONF_COLORS[r.confidence] ?? "bg-muted/10 text-muted border-line"}>
            {r.confidence}
          </Chip>
        )}
      </div>

      <div className="font-mono text-[11px] text-muted break-all mb-3">{r.path || "—"}</div>

      {presence === "present" ? (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
            <Field label="Size on disk" value={r.size_bytes != null ? bytes(r.size_bytes) : "—"} />
            <Field label="Last modified" value={fmtTs(r.modified)} />
            <Field label="Encryption format" value={r.encryption_format || "not determined"} />
            <Field label="Key storage" value={r.key_storage || "not determined"} />
            <Field
              label="Attachments"
              value={r.attachment_count != null ? String(r.attachment_count) : "—"}
            />
            <Field
              label="Attachment bytes"
              value={r.attachment_bytes != null ? bytes(r.attachment_bytes) : "—"}
            />
            <Field label="Collector status" value={r.status || "—"} />
            <Field
              label="SHA-256"
              value={
                r.sha256 ? (
                  <span title={r.sha256}>{r.sha256.slice(0, 16)}…</span>
                ) : (
                  <span className="text-muted">not hashed</span>
                )
              }
            />
          </div>

          {recoverable ? (
            <div className="rounded-md border border-carved/40 bg-carved/5 p-3">
              <div className="text-xs font-semibold text-carved mb-1">
                Marked recoverable by the collector
              </div>
              <p className="text-xs text-carved leading-relaxed">
                The collector believes this database can be opened. No content is rendered in this
                view regardless — decrypted message bodies belong in the per-app conversation views
                with their own provenance, never in an encryption-posture report.
              </p>
            </div>
          ) : (
            <div className="rounded-md border border-deletion/40 bg-deletion/5 p-3">
              <div className="text-xs font-semibold text-deletion mb-1">
                Content not recoverable
              </div>
              <p className="text-xs text-deletion leading-relaxed">
                The database file was located and its metadata recorded, but its contents cannot be
                read.{" "}
                {r.encryption_format
                  ? `The store is ${r.encryption_format}`
                  : "The store is encrypted at rest"}
                {r.key_storage ? ` and its key is held in ${r.key_storage}` : ""}. In practice this
                means one or more of: a page-level cipher (SQLCipher) that renders the file opaque
                to any SQLite reader; a hardware-backed key in the Keymaster/StrongBox TEE that is
                non-exportable by design, so it can never be copied off the device with the file;
                and a boot-bound key that is unavailable until the user credential is entered. No
                key was extracted and none was attempted.
              </p>
              <p className="text-xs text-deletion leading-relaxed mt-1.5">
                <strong>What this record does establish:</strong> the app was installed, its message
                store exists on this device, it is {r.size_bytes != null ? bytes(r.size_bytes) : "of the size shown"} in
                size, and it was last written at {fmtTs(r.modified)}. Existence, volume and recency
                are findings in their own right.
              </p>
            </div>
          )}
        </>
      ) : presence === "not-present" ? (
        <p className="text-xs text-muted leading-relaxed">
          The collector checked this path and reported it absent. That is a positive negative: the
          app&apos;s message store was not at its expected location at acquisition time. It does not
          rule out a different install location, a secondary Android user/work profile, or removal
          before seizure.
        </p>
      ) : (
        <p className="text-xs text-carved leading-relaxed">
          This path was <strong>not acquired</strong> — the collector did not, or could not, check
          it (status: <code className="font-mono">{r.status || "unreported"}</code>). This is not a
          finding of absence. Nothing may be concluded about whether this app or its database exists
          on the device.
        </p>
      )}

      <CaveatList items={r.caveats ?? []} title="Caveats" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main view
// ---------------------------------------------------------------------------

export function EncryptedAppsView({ caseId }: { caseId: string }) {
  const { data: apps, loading: appsLoading } = useDataset<EncryptedAppRecord>(
    caseId,
    "encrypted_apps",
  );
  const { data: fcm, loading: fcmLoading } = useDataset<FcmRecord>(caseId, "fcm_records");
  const [summary, setSummary] = useState<EncryptedAppsSummary>({});
  const [signal, setSignal] = useState<SignalReport>({});
  const [objLoading, setObjLoading] = useState(true);
  const [filter, setFilter] = useState("");
  // Which summary tile (if any) the "Message stores" list below is currently
  // narrowed to. Clicking the same tile again clears it — see the StatBlock
  // onClick wiring below. Declared here, unconditionally and before either
  // early return, per the Rules of Hooks.
  const [presenceFilter, setPresenceFilter] = useState<Presence | null>(null);

  useEffect(() => {
    let alive = true;
    setObjLoading(true);
    Promise.all([
      api.dataset<EncryptedAppsSummary>(caseId, "encrypted_apps_summary").catch(() => ({})),
      api.dataset<SignalReport>(caseId, "signal").catch(() => ({})),
    ])
      .then(([s, sig]) => {
        if (!alive) return;
        setSummary(s && typeof s === "object" ? s : {});
        setSignal(sig && typeof sig === "object" ? sig : {});
      })
      .finally(() => alive && setObjLoading(false));
    return () => {
      alive = false;
    };
  }, [caseId]);

  if (appsLoading || fcmLoading || objLoading) {
    return (
      <div className="p-8 text-muted text-sm animate-pulse">Loading encrypted-app posture…</div>
    );
  }

  const signalDbs = signal.encrypted_databases ?? [];
  const signalMessageCount = signal.messages?.length ?? 0;

  // Counts. Prefer the collector's own figures; fall back to deriving from rows,
  // and label which one the reader is looking at.
  const derivedPresent = apps.filter((r) => presenceOf(r) === "present").length;
  const derivedNotPresent = apps.filter((r) => presenceOf(r) === "not-present").length;
  const derivedNotAcquired = apps.filter((r) => presenceOf(r) === "not-acquired").length;

  const present = summary.present ?? derivedPresent;
  const notPresent = summary.not_present ?? derivedNotPresent;
  const notAcquired = summary.not_acquired ?? derivedNotAcquired;

  const totalBytes =
    summary.total_bytes ??
    apps.reduce((acc, r) => acc + (presenceOf(r) === "present" ? r.size_bytes ?? 0 : 0), 0);

  const q = filter.trim().toLowerCase();
  const filtered = apps
    .filter((r) => !presenceFilter || presenceOf(r) === presenceFilter)
    .filter(
      (r) =>
        !q ||
        r.app?.toLowerCase().includes(q) ||
        r.package?.toLowerCase().includes(q) ||
        r.path?.toLowerCase().includes(q) ||
        r.encryption_format?.toLowerCase().includes(q) ||
        r.status?.toLowerCase().includes(q),
    );

  const header = (
    <div className="mb-5">
      <h1 className="text-xl font-bold mb-1 flex items-center gap-2">
        <span>🔒</span> Encrypted Apps
        <span className="text-xs font-normal text-muted bg-panel-2 border border-line rounded px-2 py-0.5 ml-1">
          Tier 2 — Root
        </span>
      </h1>
      <p className="text-sm text-muted leading-relaxed">
        Encryption posture of messenger data stores. Reading the app-private{" "}
        <code className="font-mono">/data/data/</code> tree requires root; the records below report
        what was found there and, for each one, whether its contents are reachable.
      </p>
    </div>
  );

  const thesis = (
    <div className="card p-3 mb-4 border-live/40 bg-live/5 text-xs text-live leading-relaxed">
      <span className="font-semibold">
        &ldquo;Encrypted and present&rdquo; is a finding, not a failure.{" "}
      </span>
      Every row here that shows an existing database establishes four things independent of its
      contents: the app was installed on this device, it maintained a message store, that store
      reached a given size, and it was written to at a given time. Those are evidential facts.
      Reporting them as &ldquo;nothing recovered&rdquo; would understate the acquisition. This view
      states the reason each store is unreadable so that the limitation is on the record, and it
      renders no message content anywhere — not for encrypted stores, and not for readable ones.
    </div>
  );

  if (apps.length === 0 && signalDbs.length === 0 && fcm.length === 0) {
    return (
      <div className="p-6 max-w-5xl mx-auto">
        {header}
        {thesis}
        <div className="card p-8 max-w-3xl">
          <div className="text-warn font-semibold mb-2">
            No encrypted-app survey was recorded for this case
          </div>
          <p className="text-sm text-muted leading-relaxed">
            This is an empty dataset, which means the survey did not run — it does{" "}
            <strong className="text-ink">not</strong> mean the device had no encrypted messengers.
            Enumerating <code className="text-ink font-mono">/data/data/&lt;pkg&gt;/databases/</code>{" "}
            is a <span className="text-ink">Tier 2 — Root</span> operation; on a non-rooted handset
            the shell UID cannot stat those paths at all, so nothing can be reported either way.
          </p>
          <p className="text-sm text-muted leading-relaxed mt-2">
            Re-run the acquisition with root and the encrypted-app survey enabled to distinguish
            &ldquo;not installed&rdquo; from &ldquo;not looked at&rdquo;. Until then both remain
            open.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 max-w-5xl mx-auto">
      {header}
      {thesis}

      {/* ------- Summary: absence and ignorance counted separately ------- */}
      <div className="flex flex-wrap gap-3 mb-2">
        <StatBlock
          n={present}
          label="Present & encrypted"
          detail="Database located on device. Existence, size and mtime are evidence even though contents are not readable."
          tone="text-live"
          derived={summary.present === undefined}
          onClick={() => setPresenceFilter((f) => (f === "present" ? null : "present"))}
          active={presenceFilter === "present"}
        />
        <StatBlock
          n={notPresent}
          label="Not present on device"
          detail="Path was checked and positively reported absent at acquisition time."
          tone="text-muted"
          derived={summary.not_present === undefined}
          onClick={() => setPresenceFilter((f) => (f === "not-present" ? null : "not-present"))}
          active={presenceFilter === "not-present"}
        />
        <StatBlock
          n={notAcquired}
          label="Not acquired / not attempted"
          detail="Never checked — no observation was made. Nothing may be concluded about these, in either direction."
          tone="text-carved"
          derived={summary.not_acquired === undefined}
          onClick={() => setPresenceFilter((f) => (f === "not-acquired" ? null : "not-acquired"))}
          active={presenceFilter === "not-acquired"}
        />
        <StatBlock
          n={bytes(totalBytes)}
          label="Encrypted data on device"
          detail="Total size of located-but-unreadable message stores. A volume indicator, not a message count."
          tone="text-accent"
          derived={summary.total_bytes === undefined}
          onClick={() => setPresenceFilter((f) => (f === "present" ? null : "present"))}
          active={presenceFilter === "present"}
        />
      </div>
      <p className="text-xs text-muted leading-relaxed mb-4">
        <strong className="text-warn">These two zero-states are not interchangeable.</strong>{" "}
        &ldquo;Not present&rdquo; is a checked, negative observation. &ldquo;Not acquired&rdquo; is
        the absence of any observation. They are counted separately above and must never be merged
        into a single &ldquo;not found&rdquo; figure in a report.
      </p>
      <CaveatList items={summary.caveats ?? []} title="Summary caveats" />
      {(summary.notes ?? []).length > 0 && (
        <ul className="list-disc pl-4 space-y-0.5 mb-4">
          {(summary.notes ?? []).map((n, i) => (
            <li key={i} className="text-xs text-muted leading-relaxed">
              {n}
            </li>
          ))}
        </ul>
      )}

      {/* ------- Per-database records ------- */}
      <section className="mb-6">
        <div className="flex items-baseline gap-2 mb-2">
          <h2 className="text-sm font-semibold text-ink uppercase tracking-wider">
            Message stores
          </h2>
          <span className="text-xs text-muted">({apps.length})</span>
        </div>
        <input
          className="input max-w-sm mb-3"
          placeholder="Filter by app, package, path, format or status…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
        {apps.length === 0 ? (
          <div className="card p-6 text-sm text-muted leading-relaxed">
            The encrypted-app survey produced no per-database records. Other sections on this page
            may still carry findings.
          </div>
        ) : filtered.length === 0 ? (
          <div className="card p-6 text-sm text-muted text-center">
            No message stores match your filter.
          </div>
        ) : (
          filtered.map((r, i) => <AppCard key={`${r.package}-${r.path}-${i}`} r={r} />)
        )}
      </section>

      {/* ------- Signal ------- */}
      <section className="mb-6">
        <div className="flex items-baseline gap-2 mb-2">
          <h2 className="text-sm font-semibold text-ink uppercase tracking-wider">
            Signal databases
          </h2>
          <span className="text-xs text-muted">({signalDbs.length})</span>
        </div>
        {signalDbs.length === 0 ? (
          <div className="card p-6 text-sm text-muted leading-relaxed">
            No Signal database records. Signal was either not installed, or its data directory was
            not surveyed — this dataset does not distinguish the two, so neither may be asserted.
          </div>
        ) : (
          <div className="card overflow-auto">
            <table className="w-full text-sm">
              <thead>
                <tr>
                  <th className="th w-32">Artifact ID</th>
                  <th className="th">Path</th>
                  <th className="th w-24">Size</th>
                  <th className="th w-28">Status</th>
                  <th className="th w-28">Recoverable</th>
                  <th className="th">Reason</th>
                </tr>
              </thead>
              <tbody>
                {signalDbs.map((db, i) => (
                  <tr key={i}>
                    <td className="td font-mono text-[11px] text-muted break-all">
                      {db.artifact_id || "—"}
                    </td>
                    <td className="td font-mono text-[11px] break-all">{db.path || "—"}</td>
                    <td className="td font-mono text-xs">
                      {db.size_bytes != null ? bytes(db.size_bytes) : "—"}
                    </td>
                    <td className="td text-xs">{db.status || "—"}</td>
                    <td className="td">
                      {db.recoverable === true ? (
                        <Chip tone="bg-carved/10 text-carved border-carved/30">yes</Chip>
                      ) : db.recoverable === false ? (
                        <Chip tone="bg-deletion/10 text-deletion border-deletion/30">no</Chip>
                      ) : (
                        <span className="text-xs text-muted italic">not stated</span>
                      )}
                    </td>
                    <td className="td text-xs text-muted leading-relaxed">
                      {db.reason || (
                        <span className="italic">no reason recorded — treat as undetermined</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="card p-3 mt-3 border-line text-xs text-muted leading-relaxed">
          <span className="font-semibold text-ink">
            {signalMessageCount} record(s) present in the Signal message dataset.
          </span>{" "}
          Their contents are deliberately not rendered on this page. This view reports encryption
          posture only; any message body — however it was obtained — belongs in a conversation view
          alongside its own provenance and confidence marking, so that nothing here can be mistaken
          for decrypted output.
        </div>
      </section>

      {/* ------- FCM ------- */}
      <section className="mb-6">
        <div className="flex items-baseline gap-2 mb-2">
          <h2 className="text-sm font-semibold text-ink uppercase tracking-wider">
            FCM push-payload fragments
          </h2>
          <span className="text-xs text-muted">({fcm.length})</span>
        </div>
        <div className="card p-3 mb-3 border-warn/40 bg-warn/5 text-xs text-warn leading-relaxed">
          <span className="font-semibold">These are raw push-notification fragments, not messages. </span>
          Firebase Cloud Messaging payloads are transport artefacts recovered from the push
          subsystem. For an end-to-end-encrypted messenger the payload body is{" "}
          <strong>itself ciphertext</strong> — the server never holds the plaintext, so the push
          carries only a wake-up and routing envelope. What is readable is metadata: which app was
          notified, the sender ID, a message ID, and timing. A fragment therefore evidences{" "}
          <em>that a message arrived</em>, not what it said. Per-record readability is stated in the{" "}
          <em>Content readable</em> column and is never inferred.
        </div>
        {fcm.length === 0 ? (
          <div className="card p-6 text-sm text-muted leading-relaxed">
            No FCM records. The push datastore was either not collected or held nothing at
            acquisition time; this dataset cannot tell you which, and it is short-lived in any case.
          </div>
        ) : (
          <div className="card overflow-auto">
            <table className="w-full text-sm">
              <thead>
                <tr>
                  <th className="th">App / package</th>
                  <th className="th">From (sender ID)</th>
                  <th className="th">Message ID</th>
                  <th className="th w-24">Priority</th>
                  <th className="th">Sent</th>
                  <th className="th">Received</th>
                  <th className="th w-32">Content readable</th>
                </tr>
              </thead>
              <tbody>
                {fcm.map((f, i) => (
                  <tr key={i}>
                    <td className="td">
                      <div className="text-sm">{f.app || "—"}</div>
                      <div className="font-mono text-[11px] text-muted break-all">
                        {f.package || ""}
                      </div>
                      <CaveatList items={f.caveats ?? []} />
                    </td>
                    <td className="td font-mono text-[11px] break-all">{f.from || "—"}</td>
                    <td className="td font-mono text-[11px] break-all">{f.message_id || "—"}</td>
                    <td className="td text-xs">{f.priority != null ? String(f.priority) : "—"}</td>
                    <td className="td font-mono text-xs text-muted">{fmtTs(f.sent_at)}</td>
                    <td className="td font-mono text-xs text-muted">{fmtTs(f.received_at)}</td>
                    <td className="td">
                      {f.content_readable === true ? (
                        <Chip tone="bg-carved/10 text-carved border-carved/30">readable</Chip>
                      ) : f.content_readable === false ? (
                        <Chip tone="bg-deletion/10 text-deletion border-deletion/30">
                          encrypted payload
                        </Chip>
                      ) : (
                        <span className="text-xs text-muted italic">not stated</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="text-xs text-muted leading-relaxed mt-2">
          Payload bodies are not displayed here even where{" "}
          <em>content readable</em> is true, for the same reason as the Signal section: this page is
          an encryption-posture report and must not double as a message viewer.
        </p>
      </section>
    </div>
  );
}
