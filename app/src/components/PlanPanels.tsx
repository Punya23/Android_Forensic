// Rendering for the parts of a CollectionPlan that make it reviewable: the logged
// reason behind every skip, the planner's own notes (which carry the provenance and
// synthetic-exemplar disclosures), the retrieved precedent with its match terms, and
// the doctrine/precedent/learned breakdown behind a re-ranking.
//
// Shared by the acquisition preview and the case-intelligence view so the two can
// never drift apart on wording that is load-bearing for honesty.
import { ArrowRight } from "lucide-react";
import type { ArtifactPlan, DeprioritisedArtifact, Precedent } from "../lib/types";

/** The corpus records provenance in free text; "synthetic" marks a teaching exemplar. */
function isSynthetic(source: string): boolean {
  return (source || "").toLowerCase().includes("synthetic");
}

// Notes that state a limit on what the plan may be read to mean, rather than a
// planning fact. Rendered in warn colour because a reader skimming the panel must not
// be able to miss them.
const DISCLOSURE = /synthetic|precedential weight|verified by a human|collected once|never (?:drop|remove)/i;

/** Every deferred artifact with the reason the planner logged for deferring it. */
export function DeprioritisedList({ items }: { items: DeprioritisedArtifact[] }) {
  if (!items || items.length === 0) return null;
  return (
    <div className="space-y-1.5">
      {items.map((d) => (
        <div key={d.artifact} className="text-[11px] leading-relaxed">
          <span className="text-ink">{d.label}</span>
          <span className="text-muted"> — {d.reason}</span>
        </div>
      ))}
    </div>
  );
}

/** plan.notes verbatim — provenance, disclosures and the gating explanation. */
export function PlanNotes({ notes, title }: { notes: string[]; title?: string }) {
  if (!notes || notes.length === 0) return null;
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wider text-muted mb-1">
        {title ?? "Planning notes (recorded in the case log)"}
      </div>
      <ul className="space-y-1">
        {notes.map((n, i) => (
          <li
            key={i}
            className={`flex gap-1.5 text-[11px] leading-relaxed ${
              DISCLOSURE.test(n) ? "text-warn" : "text-muted"
            }`}
          >
            <span className="shrink-0">·</span>
            <span>{n}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** A retrieved prior case, with enough of it exposed to interrogate the ranking. */
export function PrecedentList({ precedents }: { precedents: Precedent[] }) {
  if (!precedents || precedents.length === 0) return null;
  return (
    <div className="space-y-2.5">
      {precedents.map((p) => (
        <div key={p.case_number} className="text-xs border-b border-line pb-2 last:border-0 last:pb-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-mono text-[11px] text-accent">{p.case_number}</span>
            <span className="text-ink">{p.title}</span>
            {p.crime_match && (
              <span className="text-[10px] rounded border border-line px-1 text-muted">
                same crime type
              </span>
            )}
            <span
              className="text-[10px] font-mono text-muted ml-auto shrink-0"
              title={`retrieval score ${p.score.toFixed(3)} · lexical ${p.lexical.toFixed(3)}`}
            >
              {p.score.toFixed(2)}
            </span>
          </div>

          {p.matched_terms.length > 0 && (
            <div className="flex flex-wrap items-baseline gap-1 mt-1">
              <span className="text-[11px] text-muted">Matched on:</span>
              {p.matched_terms.map((t, i) => (
                <span
                  key={i}
                  className="text-[10px] font-mono rounded bg-panel border border-line px-1 text-accent"
                >
                  {t}
                </span>
              ))}
            </div>
          )}

          <div className="text-[11px] text-muted mt-1">
            Solved by:{" "}
            <span className="text-live">{p.decisive_artifacts.join(", ") || "—"}</span>
            {p.useless_artifacts.length > 0 && (
              <> · produced nothing: <span className="text-muted">{p.useless_artifacts.join(", ")}</span></>
            )}
          </div>

          {/* Kept distinct from "produced nothing": an artifact that could not be read
              is a reason to prepare for the obstacle, not to skip the artifact. */}
          {(p.inaccessible_artifacts?.length ?? 0) > 0 && (
            <div className="text-[11px] text-warn mt-0.5">
              Could not be accessed in this prior case:{" "}
              <span className="font-mono">{p.inaccessible_artifacts!.join(", ")}</span>
              {" — plan for root/keys rather than treating it as a dead end."}
            </div>
          )}

          {p.outcome && (
            <div className="text-[11px] text-muted mt-0.5">
              Disposal: <span className="text-ink">{p.outcome}</span>
            </div>
          )}

          {p.lessons.length > 0 && (
            <ul className="mt-0.5 space-y-0.5">
              {p.lessons.map((l, i) => (
                <li key={i} className="text-[11px] text-muted leading-relaxed">
                  Lesson: <span className="text-ink/90">{l}</span>
                </li>
              ))}
            </ul>
          )}

          <div className="text-[11px] mt-1 italic">
            <span className={isSynthetic(p.source) ? "text-warn" : "text-muted"}>{p.source}</span>
            {isSynthetic(p.source) && (
              <span className="text-warn"> — expert-curated exemplar, not a real case record</span>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

/** What each belief source said before fusion, so a ranking can be argued with. */
export function BeliefBreakdown({ a }: { a: ArtifactPlan }) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[10px] font-mono text-muted mt-1">
      <span title="The expert ontology's own priority for this crime type">
        doctrine {a.doctrine_score.toFixed(2)}
        {a.doctrine_priority && ` (${a.doctrine_priority})`}
      </span>
      <span title="How often this artifact was decisive in the retrieved prior cases">
        precedent {a.precedent_score === null ? "n/a" : a.precedent_score.toFixed(2)}
      </span>
      <span title="Learned prior from cases this installation has actually observed">
        learned {a.learned_score === null ? "n/a" : a.learned_score.toFixed(2)}
      </span>
      <span className="text-ink" title="The fused score the priority band was computed from">
        fused {a.fused_score.toFixed(2)}
      </span>
    </div>
  );
}

/** Artifacts the evidence moved off their doctrinal ranking, with the full breakdown. */
export function RerankedArtifacts({ artifacts }: { artifacts: ArtifactPlan[] }) {
  const moved = (artifacts || []).filter((a) => a.adjustment);
  if (moved.length === 0) return null;
  return (
    <div className="space-y-2.5">
      {moved.map((a) => (
        <div key={a.artifact} className="text-xs border-b border-line pb-2 last:border-0 last:pb-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-ink">{a.label}</span>
            <span className="text-muted">
              {a.doctrine_priority || "—"} <ArrowRight className="inline h-3 w-3" strokeWidth={1.75} aria-hidden />{" "}
              <span className={a.adjustment === "promoted" ? "text-live" : "text-warn"}>
                {a.priority}
              </span>
            </span>
            <span
              className={`rounded border px-1 text-[10px] uppercase ${
                a.adjustment === "promoted"
                  ? "text-live border-live/40 bg-live/10"
                  : "text-warn border-warn/40 bg-warn/10"
              }`}
            >
              {a.adjustment}
            </span>
          </div>
          <BeliefBreakdown a={a} />
          {a.rationale && <div className="text-[11px] text-muted mt-1">{a.rationale}</div>}
          {a.evidence.map((e, i) => (
            <div key={i} className="text-[11px] text-muted mt-0.5 italic">
              {e}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

/**
 * Artifacts collected only in part because the rest needs a root stage.
 *
 * Kept visually distinct from a deferral: these ARE being collected, and the risk is
 * the opposite one — a partial browser or location record being read as complete.
 */
export function PartialCollectionList({
  items,
}: {
  items?: {
    artifact: string;
    label: string;
    pipeline_flag: string | null;
    root_stage_enabled: boolean;
    reason: string;
  }[];
}) {
  if (!items || items.length === 0) return null;
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wider text-muted mb-1">
        Collected in part only
      </div>
      <div className="space-y-1.5">
        {items.map((p) => (
          <div key={p.artifact} className="text-[11px] leading-relaxed">
            <span className="text-ink">{p.label}</span>
            {!p.root_stage_enabled && p.pipeline_flag && (
              <span className="text-warn"> (enable {p.pipeline_flag} for the rest)</span>
            )}
            <span className="text-muted"> — {p.reason}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/**
 * What the analyser could not read, and what it left out of the listed leads.
 *
 * "Examined and found irrelevant" and "could not be decoded" are different statements
 * about the evidence, and a capped lead list read as complete understates it.
 */
export function AnalysisCaveats({
  truncated,
  totalMatched,
  shown,
  deduplicated,
  unreadableCount,
  degradedFrom,
}: {
  truncated?: number;
  totalMatched?: number;
  shown?: number;
  deduplicated?: number;
  unreadableCount?: number;
  degradedFrom?: string;
}) {
  const lines: string[] = [];
  if (truncated) {
    lines.push(
      `Showing the top ${shown} of ${totalMatched} matching leads — ${truncated} more are not listed and remain part of the case.`
    );
  }
  if (deduplicated) {
    lines.push(
      `${deduplicated} near-duplicate lead(s) were collapsed into the entries above.`
    );
  }
  if (unreadableCount) {
    lines.push(
      `${unreadableCount} row(s) could not be decoded and were not examined — that is not a finding that they held nothing.`
    );
  }
  if (degradedFrom) {
    lines.push(
      `The '${degradedFrom}' LLM back-end was requested but was unreachable; this analysis is the deterministic one.`
    );
  }
  if (lines.length === 0) return null;
  return (
    <ul className="space-y-1 mt-1">
      {lines.map((l, i) => (
        <li key={i} className="flex gap-1.5 text-[11px] leading-relaxed text-warn">
          <span className="shrink-0">·</span>
          <span>{l}</span>
        </li>
      ))}
    </ul>
  );
}
