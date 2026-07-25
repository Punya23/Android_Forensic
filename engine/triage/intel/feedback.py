"""Closing the loop: turn what a run actually found back into learned priors.

After :func:`~.analysis.analyze_case` produces ranked leads, this module answers the
question the knowledge graph needs: *which artifact classes actually produced evidence
in this case?* Those observations feed :meth:`~.knowledge_graph.KnowledgeGraph.observe_case`
so the next similar case is planned better.

Two grades of feedback, and the difference matters:

``provisional``
    Derived automatically from the analysis output. It says "this artifact produced
    high-scoring leads", which is a proxy for evidential value, not a finding of fact.
    Recorded at reduced weight.

``confirmed``
    Entered by the examiner once the case is worked or disposed — "the call log is what
    broke it". Full weight, and it is what :func:`promote_case_to_study` writes into the
    case bank so future retrieval can cite it.

The tool must never learn that an artifact is worthless merely because *this* run's
heuristics scored it low. That is why automatic feedback is weight-limited and why an
artifact that was never collected is recorded as *unobserved*, not as a failure.
"""

from __future__ import annotations

from typing import Optional

from .casebank import CaseStudy, ArtifactOutcome
from .knowledge_graph import KnowledgeGraph
from .planner import CaseProfile, CollectionPlan

#: Weight applied to automatically-derived observations. Deliberately well below 1.0 —
#: an unreviewed heuristic should not move the priors as much as an examiner's judgement.
PROVISIONAL_WEIGHT = 0.35

#: A finding must clear this score before it counts as the artifact "producing evidence".
_DECISIVE_SCORE = 6.0
_SUPPORTING_SCORE = 2.0

#: Maps a finding's ``source_type`` onto the planner's artifact vocabulary.
SOURCE_TO_ARTIFACT: dict[str, str] = {
    "message": "sms",
    "messages": "sms",
    "sms": "sms",
    "call": "call_logs",
    "calls": "call_logs",
    "browser": "browser",
    "recovered": "deleted",
    "carved": "deleted",
    "media": "media",
    "location": "locations",
    "locations": "locations",
    "contact": "contacts",
    "contacts": "contacts",
    "whatsapp": "whatsapp",
    "telegram": "telegram",
    "instagram": "instagram",
    "snapchat": "snapchat",
    "app": "apps",
    "apps": "apps",
    "account": "accounts",
    "accounts": "accounts",
    "calendar": "calendar",
    "usage": "usage",
    "financial": "financial",
}


def _artifact_for(finding: dict) -> Optional[str]:
    """Best-effort map from a finding to an artifact class."""
    src = str(finding.get("source_type", "")).strip().lower()
    if src in SOURCE_TO_ARTIFACT:
        return SOURCE_TO_ARTIFACT[src]
    # Fall back to the app named on the finding ("whatsapp", "telegram", …).
    app = str(finding.get("app", "")).strip().lower()
    return SOURCE_TO_ARTIFACT.get(app)


def derive_artifact_yields(
    findings_bundle: dict, plan: Optional[CollectionPlan] = None
) -> dict[str, str]:
    """Grade each collected artifact from the analysis output.

    Returns ``{artifact: "decisive" | "supporting" | "none"}``.

    An artifact the plan did not collect is **omitted entirely** rather than graded
    ``none`` — we have no evidence about it either way, and recording a non-observation
    as a failure would teach the graph to keep skipping it forever.
    """
    findings = (findings_bundle or {}).get("findings") or []
    best: dict[str, float] = {}
    for finding in findings:
        artifact = _artifact_for(finding)
        if not artifact:
            continue
        try:
            score = float(finding.get("score", 0.0))
        except (TypeError, ValueError):
            score = 0.0
        # A "critical" severity lead counts even if its numeric score is modest.
        if str(finding.get("severity", "")).lower() == "critical":
            score = max(score, _DECISIVE_SCORE)
        best[artifact] = max(best.get(artifact, 0.0), score)

    collected: Optional[set[str]] = None
    if plan is not None:
        collected = {a.artifact for a in plan.artifacts if a.collect}

    yields: dict[str, str] = {}
    universe = collected if collected is not None else set(best)
    for artifact in universe:
        score = best.get(artifact, 0.0)
        if score >= _DECISIVE_SCORE:
            yields[artifact] = "decisive"
        elif score >= _SUPPORTING_SCORE:
            yields[artifact] = "supporting"
        else:
            yields[artifact] = "none"
    # Anything that produced findings but wasn't in the plan still counts.
    for artifact, score in best.items():
        if artifact not in yields:
            yields[artifact] = (
                "decisive"
                if score >= _DECISIVE_SCORE
                else "supporting" if score >= _SUPPORTING_SCORE else "none"
            )
    return yields


def record_provisional(
    graph: KnowledgeGraph,
    profile: CaseProfile,
    findings_bundle: dict,
    plan: Optional[CollectionPlan] = None,
    *,
    case_id: str = "",
) -> dict:
    """Record automatic, unreviewed feedback from one completed run.

    Returns a summary describing exactly what was learned, so the audit log and the
    report can state it rather than the graph changing invisibly.
    """
    yields = derive_artifact_yields(findings_bundle, plan)
    case_number = profile.case_number or case_id
    if not yields or not case_number:
        return {
            "recorded": False,
            "reason": "no gradeable artifacts or no case number",
            "yields": yields,
        }
    updated = graph.observe_case(
        profile.crime_type,
        yields,
        case_number=f"provisional:{case_number}",
        weight=PROVISIONAL_WEIGHT,
    )
    return {
        "recorded": bool(updated),
        "grade": "provisional",
        "weight": PROVISIONAL_WEIGHT,
        "case_number": case_number,
        "crime_type": profile.crime_type,
        "edges_updated": updated,
        "yields": yields,
        "note": (
            "Automatically derived from lead scores, not an examiner's finding. "
            "Recorded at reduced weight and superseded by a confirmed outcome."
        ),
    }


def record_confirmed(
    graph: KnowledgeGraph,
    crime_type: str,
    artifact_yields: dict[str, str],
    *,
    case_number: str,
    examiner: str = "",
) -> dict:
    """Record an examiner-confirmed outcome at full weight."""
    cleaned = {
        str(k): str(v).lower()
        for k, v in (artifact_yields or {}).items()
        if str(v).lower() in {"decisive", "supporting", "none"}
    }
    if not cleaned or not case_number:
        return {"recorded": False, "reason": "no valid yields or no case number"}
    updated = graph.observe_case(
        crime_type, cleaned, case_number=f"confirmed:{case_number}", weight=1.0
    )
    return {
        "recorded": bool(updated),
        "grade": "confirmed",
        "weight": 1.0,
        "case_number": case_number,
        "crime_type": crime_type,
        "examiner": examiner,
        "edges_updated": updated,
        "yields": cleaned,
    }


def promote_case_to_study(
    profile: CaseProfile,
    artifact_yields: dict[str, str],
    *,
    outcome: str = "",
    lessons: Optional[list[str]] = None,
    notes: Optional[dict[str, str]] = None,
    examiner: str = "",
) -> CaseStudy:
    """Turn a worked case into a :class:`~.casebank.CaseStudy` for future retrieval.

    The resulting study's ``source`` records that it is a real worked case from this
    installation, which is how a report can distinguish it from the bundled synthetic
    exemplars.
    """
    notes = notes or {}
    roles: dict[str, list[str]] = {}
    for r in profile.roles:
        roles.setdefault(r.get("role", "third_party"), []).append(r.get("name", ""))
    return CaseStudy(
        case_number=profile.case_number or "UNNUMBERED",
        title=(profile.summary or profile.description)[:120],
        crime_type=profile.crime_type,
        description=profile.description,
        roles=roles,
        artifacts=[
            ArtifactOutcome(artifact=a, yield_=y, note=notes.get(a, ""))
            for a, y in (artifact_yields or {}).items()
        ],
        outcome=outcome,
        lessons=list(lessons or []),
        source=(
            f"worked case, confirmed by {examiner}"
            if examiner
            else "worked case (local installation)"
        ),
    )
