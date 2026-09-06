"""AI Evidence Summary: an entirely model-authored narrative digest of a case's own
already-computed findings, scoped down to what the case brief and the learned
knowledge graph together say actually matters.

**How this differs from the two analysis passes it sits on top of.** ``analyze_case``
(:mod:`.analysis`) produces ``ai_findings`` — a deterministic ranking; an LLM is an
optional assist there, and the ranking exists identically with or without one.
``investigate_case`` (:mod:`.investigator`) adds a fixed, deterministic hypothesis pass
on top of that; again, a model only adds narrative prose over hypotheses that were
answered by code. This module has no such deterministic fallback: turning a list of
findings into prose *is* the model's job, so when no model is configured or reachable
this dataset is honestly written as ``{"generated": False, ...}`` rather than faking a
summary with string-templating — the same "never return a fabricated success" rule that
governs every stub pull in this codebase (see the eRakshak honesty invariants).

**Scope — "only the components related to the case", combined.** Two independent
filters, both applied:

    1. *Entity/keyword relevance* — a :class:`~.analysis.Finding` only qualifies if it
       already matched a named person or keyword from the case brief
       (``Finding.entities_matched`` / ``keywords_matched``, populated by
       ``analyze_derived``). A finding nobody in the case brief is connected to is not
       what an examiner asking "what did we find on the people/terms named in this
       case" wants surfaced here — ``ai_findings`` still has it, unfiltered.
    2. *Knowledge-graph yield* — of the findings that pass (1), only those whose
       artifact class the learned :class:`~.knowledge_graph.KnowledgeGraph` rates at or
       above a floor for *this case's crime type* (falls back to the doctrinal ontology
       prior when nothing has been learned yet, so this is never empty on a fresh
       install — see ``KnowledgeGraph.blended_prior``).

**Grounding.** The LLM is handed only the matched findings' titles/snippets/rationale
and instructed, explicitly, to use no fact not present in them. Every finding used is
recorded by id in ``matched_finding_ids`` — already-visible, cross-referenceable rows in
the case's own ``ai_findings`` — so nothing in the narrative is a new, unauditable claim.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from .analysis import Finding, resolve_artifact
from .knowledge_graph import KnowledgeGraph
from .llm import LLMProvider, get_provider
from .ontology import ARTIFACTS
from .planner import CaseProfile

#: Below this blended yield prior (0..1), an artifact class is left out of the summary
#: even if a finding on it matched an entity/keyword. 0.5 is the doctrinal midpoint
#: (see ``_ontology_prior`` in knowledge_graph.py: medium priority == 0.5) — an artifact
#: rated below "medium" for this crime type is not what this *summary* highlights,
#: though it stays fully visible, unfiltered, in ai_findings.
_YIELD_FLOOR = 0.5

#: Finding.category values that are already a second-order, high-signal derived
#: finding in their own right rather than a direct artifact pull — a contradiction or
#: a scam-indicator flag — so the yield filter does not gate them: it would be a
#: category error to ask "does this crime type usually yield contradictions" the way
#: it asks "does it usually yield WhatsApp messages". Deliberately a denylist, not an
#: allowlist of gated categories: a category :mod:`.analysis` adds in the future that
#: nobody updates this module for should fail CLOSED (gated, excluded by default) —
#: silently exempting an unrecognised category from the yield filter would be the
#: opposite of this module's purpose ("only high-yield artifact classes").
_UNGATED_CATEGORIES = {"contradiction", "scam_indicator"}

_MAX_FINDINGS_TO_MODEL = 20

_DISCLAIMER = (
    "AI-generated investigative aid. Every statement above is drawn only from the "
    "findings cited by id, already listed in this case's Case Intelligence tab — this "
    "narrative adds no new evidence and is not itself a forensic conclusion. It must be "
    "independently reviewed against the cited findings before use, and this tool has "
    "never been independently validated (see Tool Self-Validation)."
)

_SUMMARY_SYSTEM = (
    "You are a forensic case-summary assistant for a police digital-forensics tool. "
    "You will be given a case description and a fixed list of findings, each with an "
    "id. Write a short, neutral evidence summary in plain prose for an investigating "
    "officer. Rules: use ONLY the findings given — never mention a name, place, or "
    "detail that is not in them; never assert guilt or draw a legal conclusion; when "
    "you refer to a finding, cite its id in parentheses, e.g. '(F-MSG-0007)'; if the "
    "findings are thin, say so plainly rather than padding. This is an investigative "
    "aid, not a certified conclusion."
)


@dataclass
class AiEvidenceSummary:
    generated: bool
    provider: str = "heuristic"
    model: str = ""
    degraded_from: str = ""
    crime_type: str = "general"
    crime_label: str = "General / Unspecified"
    relevant_entities: list[str] = field(default_factory=list)
    high_yield_artifacts: list[dict] = field(default_factory=list)
    matched_finding_ids: list[str] = field(default_factory=list)
    matched_count: int = 0
    total_findings_considered: int = 0
    narrative: str = ""
    disclaimer: str = _DISCLAIMER
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _artifact_for(f: Finding) -> Optional[str]:
    """Map a Finding onto an ontology/knowledge-graph artifact key, or ``None`` when
    the category doesn't correspond to one (see ``_UNGATED_CATEGORIES``)."""
    if f.category == "message":
        # Same alias table analysis.py itself scored this Finding's app_priority
        # with — resolve_artifact, not a second hand-rolled copy, so a future alias
        # added there (e.g. "signal") is picked up here automatically instead of
        # silently diverging.
        return resolve_artifact(f.app)
    if f.category == "call":
        return "call_logs"
    if f.category == "recovered":
        return "deleted"
    if f.category == "browser":
        return "browser"
    return None


def _high_yield_artifacts(kg: Optional[KnowledgeGraph], crime_type: str) -> list[dict]:
    """Artifact classes the (learned-or-doctrinal) knowledge graph rates at or above
    the floor for *crime_type*, richest first. Never empty in practice — every
    artifact has at least a doctrinal prior — so this is a ranking/threshold, not an
    availability check.

    A caller with no persisted graph (feature not configured, fresh install) passes
    ``None``; a bare ``KnowledgeGraph()`` has no observations at all, so every
    ``blended_prior`` falls straight through to the doctrinal ontology value — the
    same numbers the planner uses on day one before any case has been learned from.
    Treating "no graph" as "no high-yield artifacts" instead would silently empty out
    this summary for every message/call/recovered/browser finding on a fresh install,
    which is not what "no graph yet" means.
    """
    priors = (kg or KnowledgeGraph()).artifact_priors(crime_type)
    out = [
        {
            "artifact": artifact,
            "label": ARTIFACTS.get(artifact, {}).get("label", artifact),
            "blended": data["blended"],
        }
        for artifact, data in priors.items()
        if data.get("blended", 0.0) >= _YIELD_FLOOR
    ]
    out.sort(key=lambda d: -d["blended"])
    return out


def _relevant_findings(
    profile: CaseProfile, findings: list[Finding], high_yield: set[str]
) -> tuple[list[Finding], list[str]]:
    """Findings that matched a case entity/keyword AND (when their category is
    yield-gated) sit in a high-yield artifact class. Returns the matched findings,
    highest score first, plus the case entities/keywords actually hit — so the bundle
    can report which named parties the summary is even about."""
    entities = profile.entities()
    wanted = {e.lower() for e in entities} | {k.lower() for k in profile.keywords if k}
    matched: list[Finding] = []
    hit_terms: set[str] = set()
    for f in findings:
        terms = {e.lower() for e in f.entities_matched} | {
            k.lower() for k in f.keywords_matched
        }
        overlap = terms & wanted
        if not overlap:
            continue
        if f.category not in _UNGATED_CATEGORIES:
            artifact = _artifact_for(f)
            if artifact not in high_yield:
                continue
        matched.append(f)
        hit_terms |= overlap
    matched.sort(key=lambda f: -f.score)
    # Report entities/keywords in their original case-brief casing, not lower().
    original = {t.lower(): t for t in entities + profile.keywords}
    relevant_entities = [original[t] for t in hit_terms if t in original]
    return matched, sorted(relevant_entities)


def _can_narrate(provider: LLMProvider) -> bool:
    """Whether *provider* can write prose at all — the heuristic stand-in never can,
    by design (see llm.py). Checked once by the caller; :func:`_narrative` itself
    trusts that check rather than repeating it, so there's exactly one place this
    "usable for narration" rule lives in this module."""
    return bool(getattr(provider, "available", False)) and provider.name != "heuristic"


def _narrative(
    provider: LLMProvider, profile: CaseProfile, matched: list[Finding]
) -> Optional[str]:
    lines = [
        f"- ({f.id}) [{f.category}/{f.app or f.source_type}] {f.title}: "
        f"{f.snippet or f.rationale}"
        for f in matched[:_MAX_FINDINGS_TO_MODEL]
    ]
    prompt = (
        f"Case: {profile.crime_label}\nDescription: {profile.description}\n\n"
        f"Findings ({len(lines)} of {len(matched)} matched, highest-scored first):\n"
        + "\n".join(lines)
    )
    return provider.generate(_SUMMARY_SYSTEM, prompt)


def generate_ai_evidence_summary(
    case: Any,
    profile: CaseProfile,
    ai_findings: dict,
    knowledge_graph: Optional[KnowledgeGraph] = None,
    provider: Optional[LLMProvider] = None,
) -> dict:
    """Build the AI Evidence Summary from a case's already-computed ``ai_findings``,
    persist it as the ``ai_evidence_summary`` derived dataset, and return the bundle.

    Always writes the dataset (unconditional envelope — see ``capabilities.py``'s
    ``ai_evidence_summary`` entry) so an examiner can tell "no model was available"
    from "the case brief named nobody" from "nothing matched" — three different
    reasons the report can come back empty, each requiring a different fix.
    """
    provider = provider or get_provider()
    findings = [Finding(**d) for d in (ai_findings or {}).get("findings", [])]
    high_yield = _high_yield_artifacts(knowledge_graph, profile.crime_type)
    high_yield_keys = {d["artifact"] for d in high_yield}

    matched, relevant_entities = _relevant_findings(profile, findings, high_yield_keys)

    bundle = AiEvidenceSummary(
        generated=False,
        crime_type=profile.crime_type,
        crime_label=profile.crime_label,
        relevant_entities=relevant_entities,
        high_yield_artifacts=high_yield,
        matched_finding_ids=[f.id for f in matched],
        matched_count=len(matched),
        total_findings_considered=len(findings),
    )

    if not profile.entities() and not profile.keywords:
        bundle.reason = "case brief named no people or keywords to summarise against"
    elif not matched:
        bundle.reason = (
            "no collected finding matched a named case entity/keyword in a "
            "high-yield artifact class for this crime type"
        )
    elif not _can_narrate(provider):
        bundle.provider = provider.name
        bundle.degraded_from = getattr(provider, "degraded_from", "")
        bundle.reason = (
            "no local model is reachable — see /api/llm/status; the matched findings "
            "above are real, only the narrative could not be written"
        )
    else:
        text = _narrative(provider, profile, matched)
        if text:
            bundle.generated = True
            bundle.provider = provider.name
            bundle.model = getattr(provider, "model", "")
            bundle.narrative = text.strip()
        else:
            bundle.reason = "the local model did not return a usable response"

    result = bundle.to_dict()
    case.write_derived("ai_evidence_summary", result)
    return result
