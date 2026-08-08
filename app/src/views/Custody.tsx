import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { AuditEvent, ManifestRecord } from "../lib/types";
import { SectionHeader } from "../components/common";
import { TierBadge } from "../components/Badges";
import { bytes } from "../components/common";

/** One collector's outcome for a single Tier-1 helper-APK run (`CollectionResult.summary()`). */
interface CollectorEntry {
  collector: string;
  file: string;
  count: number;
  status: "ok" | "denied" | "error" | "unsupported" | "empty" | string;
  error?: string;
}

/**
 * The Tier-1 helper collector's per-run audit record (`collector_manifest.json`, parsed by
 * `parse_collector_manifest`). This is what keeps an empty dataset interpretable — "0 SMS" and
 * "READ_SMS was refused" look identical without it.
 */
interface CollectorManifest {
  action: string;
  collected_at: string | null;
  sdk_int: number | null;
  manufacturer: string;
  model: string;
  collectors: CollectorEntry[];
  denied: CollectorEntry[];
  permissions_granted: string[];
  permissions_denied: string[];
  source_file: string;
}

const STATUS_STYLE: Record<string, string> = {
  ok: "text-live",
  empty: "text-muted",
  denied: "text-deletion font-semibold",
  error: "text-deletion font-semibold",
  unsupported: "text-warn",
};

/** Shorten `android.permission.READ_SMS` to `READ_SMS` for display; keep the full string as a title. */
function shortPerm(p: string): string {
  const i = p.lastIndexOf(".");
  return i === -1 ? p : p.slice(i + 1);
}

export function CustodyView({ caseId }: { caseId: string }) {
  const [tab, setTab] = useState<"audit" | "manifest" | "collector">("audit");
  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [manifest, setManifest] = useState<ManifestRecord[]>([]);
  // `read_derived` on the engine defaults a *missing* dataset to `[]` regardless of its usual
  // shape, so a Tier-1 run that never happened (or whose manifest was never pulled) comes back
  // as an empty array, not `null`/`{}`. Track that explicitly instead of guessing from falsiness.
  const [collectorManifest, setCollectorManifest] = useState<CollectorManifest | null>(null);
  const [collectorLoaded, setCollectorLoaded] = useState(false);

  useEffect(() => {
    api.audit(caseId).then(setAudit).catch(() => setAudit([]));
    api.manifest(caseId).then(setManifest).catch(() => setManifest([]));
    let alive = true;
    api
      .dataset<CollectorManifest | unknown[]>(caseId, "collector_manifest")
      .then((raw) => {
        if (!alive) return;
        const ok = raw && typeof raw === "object" && !Array.isArray(raw) && Array.isArray((raw as CollectorManifest).collectors);
        setCollectorManifest(ok ? (raw as CollectorManifest) : null);
        setCollectorLoaded(true);
      })
      .catch(() => {
        if (!alive) return;
        setCollectorManifest(null);
        setCollectorLoaded(true);
      });
    return () => {
      alive = false;
    };
  }, [caseId]);

  return (
    <div className="p-6 h-full flex flex-col">
      <SectionHeader
        title="Chain of Custody"
        sub="Append-only audit trail + per-artifact SHA-256 manifest (NIST SP 800-101r1 / SWGDE-aligned)"
        right={
          <div className="flex gap-1 bg-panel rounded-md p-1">
            <TabBtn active={tab === "audit"} onClick={() => setTab("audit")}>Audit trail ({audit.length})</TabBtn>
            <TabBtn active={tab === "manifest"} onClick={() => setTab("manifest")}>Manifest ({manifest.length})</TabBtn>
            <TabBtn active={tab === "collector"} onClick={() => setTab("collector")}>
              Collector run {collectorManifest ? `(${collectorManifest.collectors.length})` : ""}
            </TabBtn>
          </div>
        }
      />

      {tab === "collector" ? (
        <CollectorRunPanel loaded={collectorLoaded} manifest={collectorManifest} />
      ) : tab === "audit" ? (
        <div className="card overflow-auto flex-1">
          <table className="w-full text-sm">
            <thead>
              <tr>
                <th className="th w-44">Timestamp</th>
                <th className="th w-36">Action</th>
                <th className="th">Detail</th>
                <th className="th w-24">Alters device</th>
                <th className="th w-20">Result</th>
              </tr>
            </thead>
            <tbody>
              {audit.map((e, i) => (
                <tr key={i}>
                  <td className="td font-mono text-xs text-muted">{e.timestamp}</td>
                  <td className="td font-mono text-xs">{e.action}</td>
                  <td className="td">
                    {e.detail}
                    {e.command && <div className="text-[10px] text-muted/70 font-mono mt-0.5">$ {e.command}</div>}
                  </td>
                  <td className="td">
                    {e.alters_device ? <span className="text-deletion font-semibold">YES</span> : <span className="text-muted">no</span>}
                  </td>
                  <td className="td">
                    <span className={e.result === "ok" ? "text-live" : e.result === "error" ? "text-deletion" : "text-warn"}>{e.result}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="card overflow-auto flex-1">
          <table className="w-full text-sm">
            <thead>
              <tr>
                <th className="th w-20">ID</th>
                <th className="th">Source path</th>
                <th className="th w-20">Tier</th>
                <th className="th w-24">Size</th>
                <th className="th">SHA-256</th>
              </tr>
            </thead>
            <tbody>
              {manifest.map((a) => (
                <tr key={a.artifact_id}>
                  <td className="td font-mono text-xs">{a.artifact_id}</td>
                  <td className="td font-mono text-xs">{a.source_path}</td>
                  <td className="td"><TierBadge tier={a.tier} /></td>
                  <td className="td text-xs">{bytes(a.size_bytes)}</td>
                  <td className="td font-mono text-[10px] text-muted break-all">{a.sha256}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function TabBtn({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button onClick={onClick} className={`px-3 py-1 rounded text-xs ${active ? "bg-panel-2 text-ink" : "text-muted"}`}>
      {children}
    </button>
  );
}

/**
 * "What the Tier-1 helper collector actually did this run" — the collector_manifest dataset,
 * previously written by the engine but never fetched or displayed anywhere in the dashboard.
 * Without this, a helper run that was denied every permission and one that ran cleanly and
 * simply found nothing were indistinguishable from the rest of the case.
 */
function CollectorRunPanel({ loaded, manifest }: { loaded: boolean; manifest: CollectorManifest | null }) {
  if (!loaded) {
    return <div className="p-8 text-muted text-sm animate-pulse">Loading collector manifest…</div>;
  }

  if (!manifest) {
    return (
      <div className="card p-6 max-w-3xl">
        <div className="text-warn font-semibold mb-2">No collector manifest for this case</div>
        <p className="text-sm text-muted leading-relaxed">
          The Tier-1 helper APK either did not run its full <code className="text-ink">dump_all</code>{" "}
          collection in this acquisition, or <code className="text-ink">collector_manifest.json</code>{" "}
          could not be pulled from the device before teardown. This is a gap in what was collected —
          it does <em>not</em> mean every Tier-1 dataset on this case is empty, and it must not be
          read as "the collector ran and found nothing."
        </p>
      </div>
    );
  }

  const deniedByName = new Set(manifest.denied.map((d) => d.collector));

  return (
    <div className="flex-1 overflow-auto space-y-4">
      {/* Run header */}
      <div className="card p-4">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
          <div>
            <div className="text-[10px] uppercase tracking-wider text-muted mb-0.5">Action</div>
            <div className="font-mono text-ink">{manifest.action || "—"}</div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-muted mb-0.5">Collected at</div>
            <div className="font-mono text-ink">{manifest.collected_at || "unknown"}</div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-muted mb-0.5">Device</div>
            <div className="text-ink">
              {manifest.manufacturer || "—"} {manifest.model || ""}
              {manifest.sdk_int != null && <span className="text-muted"> (SDK {manifest.sdk_int})</span>}
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-muted mb-0.5">Source file</div>
            <div className="font-mono text-ink">{manifest.source_file || "—"}</div>
          </div>
        </div>
      </div>

      {manifest.denied.length > 0 && (
        <div className="card p-3 border-deletion/40 bg-deletion/5">
          <div className="text-sm font-semibold text-deletion mb-1">
            {manifest.denied.length} collector{manifest.denied.length === 1 ? "" : "s"} denied this run
          </div>
          <p className="text-xs text-deletion leading-relaxed">
            Denied means the helper asked and was refused — not that there was nothing to collect.
            Treat every dataset named below as unacquired, not as empty.
          </p>
        </div>
      )}

      {/* Per-collector status */}
      <div>
        <h2 className="text-sm font-semibold text-ink mb-2">Per-collector status</h2>
        {manifest.collectors.length === 0 ? (
          <div className="card p-4 text-xs text-muted leading-relaxed">
            The manifest was recovered but lists no collectors — nothing to show.
          </div>
        ) : (
          <div className="card overflow-auto">
            <table className="w-full text-sm">
              <thead>
                <tr>
                  <th className="th w-40">Collector</th>
                  <th className="th">File</th>
                  <th className="th w-20">Count</th>
                  <th className="th w-24">Status</th>
                  <th className="th">Reason (verbatim)</th>
                </tr>
              </thead>
              <tbody>
                {manifest.collectors.map((c, i) => (
                  <tr key={`${c.collector}-${i}`} className={deniedByName.has(c.collector) ? "bg-deletion/5" : undefined}>
                    <td className="td font-mono text-xs">{c.collector}</td>
                    <td className="td font-mono text-xs text-muted">{c.file}</td>
                    <td className="td text-xs">{c.status === "ok" ? c.count : "—"}</td>
                    <td className={`td text-xs font-mono ${STATUS_STYLE[c.status] ?? "text-ink"}`}>{c.status}</td>
                    <td className="td text-xs">
                      {c.error ? (
                        <span className="text-deletion break-all">{c.error}</span>
                      ) : (
                        <span className="text-muted italic">
                          {c.status === "empty" ? "ran clean, produced no rows" : "no detail recorded"}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Permission grant state */}
      <div>
        <h2 className="text-sm font-semibold text-ink mb-2">Permission grant state at run time</h2>
        {manifest.permissions_granted.length === 0 && manifest.permissions_denied.length === 0 ? (
          <div className="card p-4 text-xs text-muted leading-relaxed">
            No permission block was recorded in this manifest.
          </div>
        ) : (
          <div className="card overflow-auto">
            <table className="w-full text-sm">
              <thead>
                <tr>
                  <th className="th">Permission</th>
                  <th className="th w-28">Granted</th>
                </tr>
              </thead>
              <tbody>
                {manifest.permissions_granted.map((p) => (
                  <tr key={p}>
                    <td className="td font-mono text-xs" title={p}>{shortPerm(p)}</td>
                    <td className="td text-xs text-live font-semibold">granted</td>
                  </tr>
                ))}
                {manifest.permissions_denied.map((p) => (
                  <tr key={p} className="bg-deletion/5">
                    <td className="td font-mono text-xs" title={p}>{shortPerm(p)}</td>
                    <td className="td text-xs text-deletion font-semibold">refused</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
