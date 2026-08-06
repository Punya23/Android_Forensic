import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import type {
  AIFindings,
  ArtifactYield,
  CaseLearning,
  CaseProfile,
  CollectionPlan,
  Finding,
} from "../lib/types";
import { fmtTs } from "../lib/hooks";
import { ConfidenceBadge } from "../components/Badges";
import {
  AnalysisCaveats,
  DeprioritisedList,
  PartialCollectionList,
  PlanNotes,
} from "../components/PlanPanels";
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
  const [learning, setLearning] = useState<CaseLearning | null>(null);
  const [loading, setLoading] = useState(true);
  const [rerunDesc, setRerunDesc] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    const [p, pl, f, l] = await Promise.all([
      api.dataset<CaseProfile>(caseId, "case_profile").catch(() => null),
      api.dataset<CollectionPlan>(caseId, "collection_plan").catch(() => null),
      api.dataset<AIFindings>(caseId, "ai_findings").catch(() => null),
      api.dataset<CaseLearning>(caseId, "case_learning").catch(() => null),
    ]);
    setProfile(p && (p as CaseProfile).crime_type ? (p as CaseProfile) : null);
    setPlan(pl && (pl as CollectionPlan).crime_type ? (pl as CollectionPlan) : null);
    setFindings(f && (f as AIFindings).findings ? (f as AIFindings) : null);
    setLearning(l && typeof (l as CaseLearning).recorded === "boolean" ? (l as CaseLearning) : null);
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

          {/* Parties in canonical procedural roles */}
          {profile!.roles && profile!.roles.filter((r) => r.role !== "third_party").length > 0 ? (
            <div className="flex flex-wrap gap-2 mb-3">
              {profile!.roles
                .filter((r) => r.role !== "third_party")
                .map((r) => (
                  <span
                    key={r.name}
                    title={r.evidence || undefined}
                    className={`text-xs rounded border px-2 py-0.5 ${
                      r.adverse
                        ? "text-deletion border-deletion/40 bg-deletion/10"
                        : "text-ink border-line bg-panel"
                    }`}
                  >
                    <span className="text-muted">{r.label}:</span> {r.name}
                  </span>
                ))}
            </div>
          ) : (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <Facet label="Suspects" items={profile!.suspects} tone="text-deletion" />
              <Facet label="Victims" items={profile!.victims} tone="text-warn" />
              <Facet label="Locations" items={profile!.locations} tone="text-accent" />
              <Facet label="Entities" items={profile!.other_entities} tone="text-ink" />
            </div>
          )}
          {profile!.locations.length > 0 && profile!.roles?.length > 0 && (
            <Facet label="Locations" items={profile!.locations} tone="text-accent" />
          )}

          {plan && (
            <div className="text-[11px] text-muted mt-3 border-t border-line pt-2 leading-relaxed space-y-1">
              <div>
                Planning basis: <span className="text-ink">{plan.evidence_basis}</span>
                {plan.precedents?.length > 0 && (
                  <> · {plan.precedents.length} prior-case stud(y/ies) consulted</>
                )}
                {plan.knowledge_graph_stats?.distinct_cases > 0 && (
                  <> · knowledge graph holds {plan.knowledge_graph_stats.distinct_cases} case(s)</>
                )}
              </div>
              {profile?.llm_degraded_from && (
                <div className="text-warn">
                  The '{profile.llm_degraded_from}' LLM back-end was requested for this
                  case but was unreachable — the profile below is the deterministic
                  extraction, not a model's.
                </div>
              )}
              {plan.deprioritised.length > 0 && (
                <div className="space-y-1">
                  <span className="font-medium">
                    Opt-in / not auto-collected (each reason is in the case log):
                  </span>
                  <DeprioritisedList items={plan.deprioritised} />
                  <div>
                    Enable in a new acquisition if the case needs them — evidence can
                    only be collected once.
                  </div>
                </div>
              )}
              <PartialCollectionList items={plan.partial_collection} />
              <PlanNotes notes={plan.notes} />
            </div>
          )}
        </div>
      )}

      {/* Precedent used to build the plan, with provenance */}
      {plan && plan.precedents?.length > 0 && (
        <div className="card p-4 mb-4">
          <div className="text-xs uppercase tracking-wider text-muted mb-2">
            Prior-case studies consulted for collection planning
          </div>
          <div className="space-y-2">
            {plan.precedents.map((p) => (
              <div key={p.case_number} className="text-xs border-b border-line pb-2 last:border-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-mono text-[11px] text-accent">{p.case_number}</span>
                  <span className="text-ink">{p.title}</span>
                  {p.crime_match && (
                    <span className="text-[10px] rounded border border-line px-1 text-muted">
                      same crime type
                    </span>
                  )}
                  <span className="text-[10px] font-mono text-muted ml-auto">
                    {p.score.toFixed(2)}
                  </span>
                </div>
                <div className="text-[11px] text-muted mt-0.5">
                  Solved by:{" "}
                  <span className="text-live">{p.decisive_artifacts.join(", ") || "—"}</span>
                  {p.useless_artifacts.length > 0 && (
                    <>
                      {" "}
                      · produced nothing:{" "}
                      <span className="text-muted">{p.useless_artifacts.join(", ")}</span>
                    </>
                  )}
                </div>
                <div className="text-[11px] text-muted italic mt-0.5">{p.source}</div>
              </div>
            ))}
          </div>
          <p className="text-[11px] text-warn mt-2 leading-relaxed">
            These studies were used only to rank which artifacts to collect. They are not
            evidence in this case and carry no precedential weight.
          </p>
        </div>
      )}

      {/* Artifacts whose ranking the evidence moved */}
      {plan && plan.artifacts?.some((a) => a.adjustment) && (
        <div className="card p-4 mb-4">
          <div className="text-xs uppercase tracking-wider text-muted mb-2">
            Evidence-based re-ranking
          </div>
          <div className="space-y-2">
            {plan.artifacts
              .filter((a) => a.adjustment)
              .map((a) => (
                <div key={a.artifact} className="text-xs">
                  <div className="flex items-center gap-2">
                    <span className="text-ink">{a.label}</span>
                    <span className="text-muted">
                      {a.doctrine_priority} →{" "}
                      <span className={a.adjustment === "promoted" ? "text-live" : "text-warn"}>
                        {a.priority}
                      </span>
                    </span>
                    <span className="text-[10px] font-mono text-muted ml-auto">
                      fused {a.fused_score.toFixed(2)}
                    </span>
                  </div>
                  {a.evidence.map((e, i) => (
                    <div key={i} className="text-[11px] text-muted mt-0.5">
                      {e}
                    </div>
                  ))}
                </div>
              ))}
          </div>
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
          {/* A capped, de-duplicated or partially-unreadable lead list read as the
              complete set understates the evidence held. */}
          <div className="w-full">
            <AnalysisCaveats
              truncated={findings.truncated}
              totalMatched={findings.total_matched}
              shown={findings.shown}
              deduplicated={findings.deduplicated}
              unreadableCount={findings.unreadable_count}
              degradedFrom={findings.llm_degraded_from}
            />
          </div>
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

      {/* Feedback loop */}
      {hasProfile && (
        <OutcomePanel
          caseId={caseId}
          profile={profile!}
          plan={plan}
          learning={learning}
          onSaved={load}
        />
      )}
    </div>
  );
}

/** Examiner confirmation of what actually solved the case — the full-weight feedback. */
function OutcomePanel({
  caseId,
  profile,
  plan,
  learning,
  onSaved,
}: {
  caseId: string;
  profile: CaseProfile;
  plan: CollectionPlan | null;
  learning: CaseLearning | null;
  onSaved: () => void;
}) {
  // Seed the form from the provisional grades so the examiner corrects rather than types.
  const collected = (plan?.artifacts || []).filter((a) => a.collect);
  const [yields, setYields] = useState<Record<string, ArtifactYield>>({});
  const [examiner, setExaminer] = useState("");
  const [outcome, setOutcome] = useState("");
  const [addToBank, setAddToBank] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (learning?.yields) setYields({ ...learning.yields });
  }, [learning]);

  const artifacts = collected.length
    ? collected.map((a) => ({ key: a.artifact, label: a.label }))
    : Object.keys(learning?.yields || {}).map((k) => ({ key: k, label: k }));

  async function save() {
    setSaving(true);
    setErr(null);
    try {
      const res = await api.recordOutcome(caseId, {
        artifact_yields: yields,
        case_number: profile.case_number || undefined,
        examiner: examiner.trim() || undefined,
        outcome: outcome.trim() || undefined,
        add_to_case_bank: addToBank,
      });
      setSaved(
        `Recorded at full weight. Knowledge graph now holds ${res.graph_stats.distinct_cases} case(s)` +
          (res.promoted_to_case_bank
            ? `; added to the case bank as ${res.promoted_to_case_bank.case_number} (corpus ${res.corpus_size}).`
            : ".")
      );
      onSaved();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="card p-4 mt-4">
      <div className="flex items-center justify-between">
        <div>
          <div className="label mb-1">Record case outcome</div>
          <p className="text-xs text-muted">
            Tell the system which artifacts actually broke the case. This is what future
            similar cases learn from.
          </p>
        </div>
        <button className="btn-ghost shrink-0" onClick={() => setOpen((o) => !o)}>
          {open ? "Hide" : "Open"}
        </button>
      </div>

      {learning?.recorded && (
        <div className="text-[11px] text-muted mt-2 border-t border-line pt-2 leading-relaxed">
          <span className="text-warn">Provisional</span> feedback was recorded automatically
          at weight {learning.weight} from this run's lead scores.
          {learning.graded_from === "observed collection" ? (
            <> Graded against what this run actually acquired.</>
          ) : learning.graded_from === "plan intent" ? (
            <span className="text-warn">
              {" "}
              Graded against the plan's intent rather than what was acquired, so an
              artifact that was planned but never ran may be graded as a null.
            </span>
          ) : null}{" "}
          {learning.note} Confirming below records the examiner's outcome at full
          weight alongside this one.
        </div>
      )}

      {open && (
        <div className="mt-3 space-y-3">
          {artifacts.length === 0 ? (
            <p className="text-xs text-muted">
              No collected artifacts recorded for this case.
            </p>
          ) : (
            <div className="space-y-1">
              {artifacts.map((a) => (
                <div key={a.key} className="flex items-center justify-between text-xs">
                  <span className="text-ink">{a.label}</span>
                  <div className="flex gap-1">
                    {(["decisive", "supporting", "none"] as ArtifactYield[]).map((y) => (
                      <button
                        key={y}
                        onClick={() => setYields((prev) => ({ ...prev, [a.key]: y }))}
                        className={`rounded border px-1.5 py-0.5 text-[10px] uppercase ${
                          yields[a.key] === y
                            ? y === "decisive"
                              ? "text-live border-live/50 bg-live/10"
                              : y === "supporting"
                              ? "text-warn border-warn/50 bg-warn/10"
                              : "text-muted border-line bg-panel"
                            : "text-muted border-line"
                        }`}
                      >
                        {y}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="label">Examiner</label>
              <input
                className="input"
                placeholder="e.g. SI Rao"
                value={examiner}
                onChange={(e) => setExaminer(e.target.value)}
              />
            </div>
            <div>
              <label className="label">Disposal / outcome</label>
              <input
                className="input"
                placeholder="e.g. charge-sheeted"
                value={outcome}
                onChange={(e) => setOutcome(e.target.value)}
              />
            </div>
          </div>

          <label className="flex items-center gap-2 text-xs text-muted">
            <input
              type="checkbox"
              checked={addToBank}
              onChange={(e) => setAddToBank(e.target.checked)}
            />
            Also add this case to the retrieval corpus, so future similar cases can cite it
          </label>

          <div className="flex items-center gap-3">
            <button
              className="btn-accent"
              disabled={saving || Object.keys(yields).length === 0}
              onClick={save}
            >
              {saving ? "Recording…" : "Record outcome"}
            </button>
            {saved && <span className="text-xs text-live">{saved}</span>}
            {err && <span className="text-xs text-deletion">{err}</span>}
          </div>
        </div>
      )}
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
