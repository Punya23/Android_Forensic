import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import type { AIFindings, CaseProfile, CollectionPlan, Finding } from "../lib/types";
import { fmtTs } from "../lib/hooks";
import { ConfidenceBadge } from "../components/Badges";
import { SectionHeader, EmptyState } from "../components/common";

const SEV_CLS: Record<string, string> = {
  critical: "text-critical border-critical/40 bg-critical/10",
  high: "text-deletion border-deletion/40 bg-deletion/10",
  medium: "text-warn border-warn/40 bg-warn/10",
  low: "text-info border-info/40 bg-info/10",
  info: "text-muted border-line bg-panel",
};

export function CaseIntelView({ caseId }: { caseId: string }) {
  const [profile, setProfile] = useState<CaseProfile | null>(null);
  const [plan, setPlan] = useState<CollectionPlan | null>(null);
  const [findings, setFindings] = useState<AIFindings | null>(null);
  const [loading, setLoading] = useState(true);
  const [rerunDesc, setRerunDesc] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    const [p, pl, f] = await Promise.all([
      api.dataset<CaseProfile>(caseId, "case_profile").catch(() => null),
      api.dataset<CollectionPlan>(caseId, "collection_plan").catch(() => null),
      api.dataset<AIFindings>(caseId, "ai_findings").catch(() => null),
    ]);
    setProfile(p && (p as CaseProfile).crime_type ? (p as CaseProfile) : null);
    setPlan(pl && (pl as CollectionPlan).crime_type ? (pl as CollectionPlan) : null);
    setFindings(f && (f as AIFindings).findings ? (f as AIFindings) : null);
    setLoading(false);
  }, [caseId]);

  useEffect(() => {
    load();
  }, [load]);

  async function reanalyze() {
    setBusy(true);
    setError(null);
    try {
      await api.analyze(caseId, rerunDesc.trim() ? { description: rerunDesc.trim() } : undefined);
      setRerunDesc("");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <div className="p-8 text-muted">Loading case intelligence…</div>;

  const counts = findings?.counts || {};
  const hasProfile = !!profile;

  return (
    <div className="p-6 h-full overflow-auto">
      <SectionHeader
        title="Case Intelligence"
        sub="AI-surfaced investigative leads — every lead cites its source and must be verified by a human examiner."
      />

      {/* Profile + plan summary */}
      {hasProfile && (
        <div className="card p-4 mb-4">
          <div className="flex flex-wrap items-center gap-2 mb-2">
            <span className="text-lg font-semibold text-ink">{profile!.crime_label}</span>
            <span className="text-xs rounded border border-accent/40 bg-accent/10 text-accent px-1.5 py-0.5">
              {profile!.extraction_method}
            </span>
            <span className="text-xs text-muted">
              confidence {Math.round((profile!.confidence || 0) * 100)}%
            </span>
          </div>
          {profile!.summary && <p className="text-sm text-muted mb-3">{profile!.summary}</p>}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <Facet label="Suspects" items={profile!.suspects} tone="text-deletion" />
            <Facet label="Victims" items={profile!.victims} tone="text-warn" />
            <Facet label="Locations" items={profile!.locations} tone="text-accent" />
            <Facet label="Entities" items={profile!.other_entities} tone="text-ink" />
          </div>
          {plan && plan.deprioritised.length > 0 && (
            <div className="text-[11px] text-muted mt-3 border-t border-line pt-2 leading-relaxed">
              <span className="font-medium">Opt-in / not auto-collected (logged):</span>{" "}
              {plan.deprioritised.map((d) => d.label).join(", ")}. Enable in a new acquisition
              if the case needs them — evidence can only be collected once.
            </div>
          )}
        </div>
      )}

      {/* Findings header / counts */}
      {findings && findings.findings.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 mb-3">
          <CountPill label="leads" n={counts.total || 0} cls="text-ink border-line bg-panel" />
          {["critical", "high", "medium", "low"].map(
            (s) => counts[s] ? <CountPill key={s} label={s} n={counts[s]} cls={SEV_CLS[s]} /> : null
          )}
          <span className="text-xs text-muted ml-1">{findings.analysis_method}</span>
        </div>
      )}

      {/* Optional LLM narrative */}
      {findings?.narrative && (
        <div className="card p-4 mb-4 border-accent/30">
          <div className="text-xs uppercase tracking-wider text-muted mb-1">AI case summary</div>
          <p className="text-sm text-ink leading-relaxed whitespace-pre-wrap">{findings.narrative}</p>
        </div>
      )}

      {/* Findings list */}
      {findings && findings.findings.length > 0 ? (
        <div className="space-y-2">
          {findings.findings.map((f) => (
            <FindingCard key={f.id} f={f} />
          ))}
          <p className="text-[11px] text-muted mt-3 leading-relaxed">{findings.disclaimer}</p>
        </div>
      ) : (
        <EmptyState
          title={hasProfile ? "No leads matched the case profile" : "No case brief on file"}
          detail={
            hasProfile
              ? "The collected artifacts didn't match the entities or keywords for this case. Try re-running with a refined description below."
              : "Start a new acquisition with a case brief, or run analysis over this case now using the box below."
          }
        />
      )}

      {/* Re-analyze */}
      <div className="card p-4 mt-6">
        <div className="label mb-1">Re-run analysis</div>
        <p className="text-xs text-muted mb-2">
          Re-score the already-collected artifacts. Leave blank to reuse the stored profile, or
          enter a refined description to re-extract entities and keywords.
        </p>
        <textarea
          className="input min-h-[60px] resize-y"
          placeholder="(optional) refined case description…"
          value={rerunDesc}
          onChange={(e) => setRerunDesc(e.target.value)}
        />
        <div className="flex items-center gap-3 mt-2">
          <button className="btn-accent" disabled={busy} onClick={reanalyze}>
            {busy ? "Analyzing…" : "Run analysis"}
          </button>
          {error && <span className="text-xs text-deletion">{error}</span>}
        </div>
      </div>
    </div>
  );
}

function FindingCard({ f }: { f: Finding }) {
  return (
    <div className="card p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2 flex-wrap">
          <span className={`rounded border px-1.5 py-0.5 text-[10px] font-semibold uppercase ${SEV_CLS[f.severity] ?? SEV_CLS.info}`}>
            {f.severity}
          </span>
          <ConfidenceBadge c={f.confidence} />
          <span className="text-sm font-medium text-ink">{f.title}</span>
        </div>
        <span className="text-xs font-mono text-muted shrink-0">{f.score.toFixed(1)}</span>
      </div>
      {f.snippet && (
        <div className="text-sm text-ink/90 mt-1.5 bg-panel rounded px-2 py-1 font-mono text-xs break-words">
          {f.snippet}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted mt-1.5">
        <span className="uppercase tracking-wide">{f.category}</span>
        {f.source_file && <span className="font-mono">{f.source_file}</span>}
        {f.timestamp && <span>{fmtTs(f.timestamp)}</span>}
        {f.entities_matched.length > 0 && (
          <span className="text-warn">entities: {f.entities_matched.join(", ")}</span>
        )}
        {f.keywords_matched.length > 0 && (
          <span className="text-accent">terms: {f.keywords_matched.join(", ")}</span>
        )}
      </div>
      <div className="text-[11px] text-muted mt-1 italic">{f.rationale}</div>
    </div>
  );
}

function Facet({ label, items, tone }: { label: string; items: string[]; tone: string }) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wider text-muted mb-1">{label}</div>
      {items && items.length > 0 ? (
        <div className="flex flex-wrap gap-1">
          {items.map((it, i) => (
            <span key={i} className={`text-xs rounded bg-panel px-1.5 py-0.5 border border-line ${tone}`}>
              {it}
            </span>
          ))}
        </div>
      ) : (
        <span className="text-xs text-muted">—</span>
      )}
    </div>
  );
}

function CountPill({ label, n, cls }: { label: string; n: number; cls: string }) {
  return (
    <span className={`rounded border px-2 py-0.5 text-xs font-semibold ${cls}`}>
      {n} {label}
    </span>
  );
}
