import { useEffect, useState } from "react";
import { ArrowRight } from "lucide-react";
import { api } from "../lib/api";
import type { CaseSummary, Flag } from "../lib/types";
import type { ViewKey } from "../components/Sidebar";
import { StatCard, bytes } from "../components/common";
import { SeverityBadge } from "../components/Badges";
import { RiskCard } from "../components/RiskCard";

// Flagged-for-review panel is capped like Aleapp.tsx's table / Messages.tsx's list, with the
// same "N of M — click to show all" control so the cap is disclosed rather than silently
// hiding flags past this count.
const FLAGS_CAP = 40;

export function OverviewView({ caseId, setView }: { caseId: string; setView: (v: ViewKey) => void }) {
  const [summary, setSummary] = useState<CaseSummary | null>(null);
  const [flags, setFlags] = useState<Flag[]>([]);
  const [exporting, setExporting] = useState(false);
  const [showAllFlags, setShowAllFlags] = useState(false);

  useEffect(() => {
    api.caseOverview(caseId).then(setSummary).catch(() => setSummary(null));
    api.dataset<Flag[]>(caseId, "flags").then(setFlags).catch(() => setFlags([]));
    setShowAllFlags(false);
  }, [caseId]);

  if (!summary) return <div className="p-8 text-muted">Loading case…</div>;
  const c = summary.case;
  const d = c.device;
  const counts = summary.counts;
  const tp = summary.throughput;

  // Base tiles always shown; app-chat and Tier-1 tiles appear only when they have data.
  const baseTiles: { key: ViewKey; label: string; n: number }[] = [
    { key: "messages", label: "Messages", n: counts.messages },
    { key: "recovered", label: "Recovered", n: counts.recovered },
    { key: "contacts", label: "Contacts", n: counts.contacts },
    { key: "calls", label: "Calls", n: counts.calls },
    { key: "media", label: "Media", n: counts.media },
    { key: "locations", label: "Locations", n: counts.locations },
    { key: "browser", label: "Browser", n: counts.browser ?? 0 },
    { key: "timeline", label: "Timeline", n: counts.timeline },
  ];
  const extraTilesAll: { key: ViewKey; label: string; n: number }[] = [
    { key: "mediainv", label: "Media Inv.", n: counts.media_inventory ?? 0 },
    { key: "apps", label: "Apps", n: counts.apps ?? 0 },
    { key: "accounts", label: "Accounts", n: counts.accounts ?? 0 },
    { key: "calendar", label: "Calendar", n: counts.calendar ?? 0 },
    { key: "instagram", label: "Instagram", n: counts.instagram ?? 0 },
    { key: "snapchat", label: "Snapchat", n: counts.snapchat ?? 0 },
    { key: "discovered", label: "Discovered", n: summary.discovered_chat_count ?? 0 },
  ];
  const tiles = [...baseTiles, ...extraTilesAll.filter((t) => t.n > 0)];
  const notableApps = summary.notable_apps ?? [];
  const mediaSum = summary.media_inventory_summary;

  return (
    <div className="p-6 max-w-6xl mx-auto">
      {/* Disclaimer banner */}
      <div className="card border-accent/40 bg-accent/5 p-3 mb-4 text-sm flex gap-2">
        <span className="text-accent font-semibold shrink-0">TRIAGE PREVIEW</span>
        <span className="text-muted">{summary.disclaimer}</span>
      </div>

      {/* Traffic-light risk verdict */}
      {summary.risk && <RiskCard risk={summary.risk} />}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-5">
        <InfoCard title="Case">
          <KV k="Case ID" v={c.case_id} mono />
          <KV k="Examiner" v={c.examiner} />
          <KV k="Legal authority" v={c.legal_authority || "— (record before use)"} />
          <KV k="Scope" v={c.scope_note || "—"} />
          <KV k="Opened" v={c.created_at} mono />
        </InfoCard>
        <InfoCard title="Device (intake)">
          <KV k="Model" v={`${d.manufacturer} ${d.model}`} />
          <KV k="Android" v={`${d.android_version} (SDK ${d.sdk})`} />
          <KV k="Serial" v={d.serial} mono />
          <KV k="IMEI" v={d.imei || "—"} mono />
          <KV k="Carrier" v={d.carrier || "—"} />
          <KV k="Root" v={d.rooted ? "available (Tier 2 possible)" : "no"} tone={d.rooted ? "text-warn" : undefined} />
        </InfoCard>
        <InfoCard title="Integrity & speed">
          <KV k="Artifacts" v={String(summary.artifact_count)} />
          <KV k="Total size" v={bytes(summary.total_bytes)} />
          <KV k="Throughput" v={tp ? `${tp.mb_per_min} MB/min` : "—"} tone="text-recovered" />
          <KV k="Audit events" v={String(summary.audit_event_count)} />
          <KV k="Device-altering actions" v={String(summary.device_altering_actions)} tone={summary.device_altering_actions ? "text-warn" : "text-live"} />
          <div className="pt-2 mt-1 border-t border-line flex flex-wrap gap-2">
            <button className="btn-ghost text-xs py-1" onClick={() => setView("custody")}>Audit trail</button>
            <button className="btn-ghost text-xs py-1" onClick={() => setView("report")}>Report</button>
            <a
              className="btn-accent text-xs py-1"
              href={api.exportUrl(caseId)}
              onClick={() => setExporting(true)}
            >
              {exporting ? "Packaging…" : "Export evidence"}
            </a>
          </div>
        </InfoCard>
      </div>

      {/* Stat tiles */}
      <div className="grid grid-cols-3 sm:grid-cols-4 lg:grid-cols-7 gap-3 mb-6">
        {tiles.map((t) => (
          <button key={t.key} onClick={() => setView(t.key)} className="text-left">
            <StatCard n={t.n} label={t.label} tone={t.key === "recovered" ? "text-recovered" : undefined} />
          </button>
        ))}
      </div>

      {/* Apps of interest (Tier-1 inventory insight) */}
      {notableApps.length > 0 && (
        <div className="card p-4 mb-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold">Apps of interest ({notableApps.length})</h3>
            <button className="text-xs text-accent hover:underline" onClick={() => setView("apps")}>
              <span className="inline-flex items-center gap-1">
                View all apps <ArrowRight className="inline h-3.5 w-3.5" strokeWidth={1.75} aria-hidden />
              </span>
            </button>
          </div>
          <div className="flex flex-wrap gap-2">
            {notableApps.slice(0, 30).map((a, i) => (
              <span
                key={i}
                title={a.package}
                className={`text-xs px-2 py-1 rounded border ${
                  a.category === "anti_forensic"
                    ? "border-deletion/50 bg-deletion/10 text-deletion"
                    : "border-line bg-panel-2 text-ink/80"
                }`}
              >
                {a.friendly_name || a.label || a.package}
                <span className="text-muted/70 ml-1">· {a.category === "anti_forensic" ? "vault" : a.category}</span>
              </span>
            ))}
          </div>
          {mediaSum && (mediaSum.trashed > 0 || mediaSum.with_gps > 0) && (
            <div className="text-xs text-muted mt-3 pt-3 border-t border-line/50">
              MediaStore: {mediaSum.total} items · <span className="text-deletion">{mediaSum.trashed} trashed</span> ·{" "}
              {mediaSum.favorite} favorited · {mediaSum.with_gps} geotagged
            </div>
          )}
        </div>
      )}

      {/* Priority flags */}
      <div className="card p-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold">Flagged for review ({flags.length})</h3>
          <span className="text-xs text-muted">keyword & known-hash hits — for analyst awareness, not conclusions</span>
        </div>
        {flags.length === 0 ? (
          <p className="text-sm text-muted">No flags raised.</p>
        ) : (
          <>
            <div className="space-y-1.5 max-h-72 overflow-auto">
              {[...flags]
                .sort((a, b) => sev(a.severity) - sev(b.severity))
                .slice(0, showAllFlags ? flags.length : FLAGS_CAP)
                .map((f, i) => (
                  <div key={i} className="flex items-start gap-3 text-sm py-1 border-b border-line/50 last:border-0">
                    <SeverityBadge s={f.severity} />
                    <span className="font-mono text-accent shrink-0">{f.term}</span>
                    <span className="text-muted flex-1 truncate">{f.context}</span>
                    <span className="text-xs text-muted/70 shrink-0 hidden sm:block">{f.location}</span>
                  </div>
                ))}
            </div>
            {!showAllFlags && flags.length > FLAGS_CAP && (
              <div className="text-center pt-3">
                <button className="btn-ghost text-xs" onClick={() => setShowAllFlags(true)}>
                  Showing first {FLAGS_CAP} of {flags.length.toLocaleString()} flags — click to show all
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function sev(s: string) {
  return { critical: 0, warn: 1, info: 2 }[s] ?? 3;
}

function InfoCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="card p-4">
      <h3 className="text-xs uppercase tracking-wider text-muted mb-3">{title}</h3>
      <div className="space-y-1.5">{children}</div>
    </div>
  );
}

function KV({ k, v, mono, tone }: { k: string; v: string; mono?: boolean; tone?: string }) {
  return (
    <div className="flex justify-between gap-3 text-sm">
      <span className="text-muted">{k}</span>
      <span className={`text-right ${mono ? "font-mono text-xs" : ""} ${tone ?? "text-ink"}`}>{v}</span>
    </div>
  );
}
