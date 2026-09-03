"""Deep investigation: a bounded, deterministic, multi-step pass over a case's own
evidence that :func:`~.analysis.analyze_case` structurally cannot do in one flat scoring
pass — cross-linking findings that were scored independently.

**What this is, precisely, and what it deliberately is not.** The pattern this follows
— an explicit, persisted plan (a fixed set of hypotheses, not implicit chain-of-thought),
each investigated over only the datasets it needs, with results grounded against the
cited artifact before anything is asserted — is the same shape "deep agent" scaffolding
(LangChain's ``deepagents``, Manus, and the DFIR agent literature) uses for long-horizon
work: externalised planning, scoped sub-tasks, and a verify-before-assert discipline
(the HunterAgent neuro-symbolic pattern: an LLM may *propose* a correlation, but a
deterministic check decides whether it is accepted). What it is *not* is an open-ended,
LLM-driven tool-calling loop that decides for itself what to look at next — that would
buy real capability at the cost of exactly the property this codebase will not trade
away: every claim traceable to a specific artifact, with no step where a model's own
judgment silently substitutes for one. So the plan here is a **fixed, small set of
hypothesis templates** (bounded, auditable, the same hypothesis set for the same case
profile every time), not a model deciding its own investigative steps. That is a
deliberate, documented scope limit for a v1, not an oversight — see the module's tests
for exactly what it does and does not cover.

**Two hypotheses, both grounded in already-computed, timestamped datasets:**

    * ``channel_gap`` — a case entity with a Contacts entry but no corresponding
      message/call Finding: a known relationship with no communication surfaced,
      worth checking whether the right app/tier was ever collected.
    * ``location_correlation`` — a location anomaly (already flagged, timestamped, by
      :mod:`triage.forensics.location_anomaly`) that falls within a time window of a
      message/call Finding: the device was somewhere unusual *and* communicating
      around the same time, a correlation neither dataset alone shows.

Both run deterministically and always run — matching how ``analyze_derived``'s own
scoring is deterministic and always runs. An LLM (if configured) only ever adds a
narrative synthesis on top of the same, already-cited findings, exactly like
``analyze_case``'s own optional narrative — never a hard dependency, never a path that
runs a *different*, degraded investigation with no model.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from ..forensics.contradiction import parse_iso as _parse_iso
from .analysis import Finding
from .llm import LLMProvider, get_provider
from .planner import CaseProfile, CollectionPlan

#: Findings within this many seconds of a location anomaly are considered correlated.
_CORRELATION_WINDOW_S = 1800


@dataclass
class LinkedFinding:
    """A correlation between two independently-produced pieces of evidence.

    Never a new fact on its own — ``left``/``right`` are ``Finding.id`` (or a location-
    anomaly reference) values a reader can look up in the datasets already on screen.
    The correlation itself is computed, not asserted by a model: a fixed time window,
    checked in code, is what decides whether this gets emitted at all.
    """

    id: str
    kind: str  # "location_correlation"
    rationale: str
    left_ref: str
    right_ref: str
    gap_seconds: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Hypothesis:
    """One line of inquiry, persisted with its own status — the externalised plan a
    deep-agent-style investigation keeps instead of implicit reasoning in a transcript."""

    id: str
    kind: str
    question: str
    dataset_scope: list[str]
    status: str = "pending"  # pending | answered | blocked
    finding_ids: list[str] = field(default_factory=list)
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _name_in_text(name: str, *texts: str) -> bool:
    name_l = name.strip().lower()
    if not name_l:
        return False
    return any(name_l in (t or "").lower() for t in texts)


# --- hypothesis: channel gap -------------------------------------------------
def _investigate_channel_gap(
    profile: CaseProfile,
    contacts: list[dict],
    findings: list[Finding],
    plan: Optional[CollectionPlan],
) -> Hypothesis:
    hyp = Hypothesis(
        id="H-CHANNEL-GAP",
        kind="channel_gap",
        question=(
            "Which named parties in this case have a Contacts entry but no "
            "message/call finding — a known relationship with no communication "
            "surfaced?"
        ),
        dataset_scope=["contacts", "messages", "calls"],
    )
    adverse = profile.adverse_entities() or profile.entities()
    if not adverse:
        hyp.status = "blocked"
        hyp.detail = "No named case entities to check — the case brief named nobody."
        return hyp
    if not contacts:
        hyp.status = "blocked"
        hyp.detail = (
            "No contacts dataset was collected this run (Tier 1). Cannot tell a "
            "genuine channel gap from one this acquisition never had the data to see."
        )
        return hyp

    findings_by_entity: dict[str, list[Finding]] = {}
    for f in findings:
        for e in f.entities_matched:
            findings_by_entity.setdefault(e.lower(), []).append(f)

    gaps: list[str] = []
    for name in adverse:
        has_contact = any(
            _name_in_text(name, c.get("name", "")) for c in contacts
        )
        if not has_contact:
            continue
        has_comm_finding = bool(findings_by_entity.get(name.lower()))
        if not has_comm_finding:
            gaps.append(name)

    if not gaps:
        hyp.status = "answered"
        hyp.detail = (
            "Every adverse-role party with a Contacts entry also has at least one "
            "message/call finding — no channel gap detected."
        )
        return hyp

    hyp.status = "answered"
    hyp.detail = (
        f"{', '.join(gaps)} {'has' if len(gaps) == 1 else 'have'} a Contacts entry "
        "but no communication surfaced by keyword/entity scoring. This does not mean "
        "no communication happened — it means either none was collected (check "
        + (
            ", ".join(d["artifact"] for d in (plan.deprioritised if plan else []))
            or "the collection plan"
        )
        + " for an app that was made opt-in and not enabled), or it happened on a "
        "channel that didn't match the case's keyword/entity vocabulary."
    )
    return hyp


# --- hypothesis: location correlation ----------------------------------------
def _investigate_location_correlation(
    location_anomalies: list[dict], findings: list[Finding]
) -> tuple[Hypothesis, list[LinkedFinding]]:
    hyp = Hypothesis(
        id="H-LOCATION-CORR",
        kind="location_correlation",
        question=(
            "Does any already-flagged location anomaly fall within "
            f"{_CORRELATION_WINDOW_S // 60} minutes of a message/call finding — "
            "device movement and communication activity co-occurring?"
        ),
        dataset_scope=["location_anomalies", "messages", "calls"],
    )
    if not location_anomalies:
        hyp.status = "blocked"
        hyp.detail = (
            "No location anomalies were flagged this run — either the device's "
            "location trace was too thin to cluster, or nothing unusual was found."
        )
        return hyp, []

    comm_points = [
        (dt, f)
        for f in findings
        if f.category in ("message", "call")
        for dt in [_parse_iso(f.timestamp)]
        if dt is not None
    ]
    if not comm_points:
        hyp.status = "blocked"
        hyp.detail = "No timestamped message/call findings to correlate against."
        return hyp, []

    linked: list[LinkedFinding] = []
    n = 0
    for anomaly in location_anomalies:
        a_dt = _parse_iso(anomaly.get("timestamp"))
        if a_dt is None:
            continue
        nearest = min(
            (
                (abs((dt - a_dt).total_seconds()), dt, f)
                for dt, f in comm_points
                if abs((dt - a_dt).total_seconds()) <= _CORRELATION_WINDOW_S
            ),
            default=None,
            key=lambda t: t[0],
        )
        if nearest is None:
            continue
        gap_s, f_dt, f = nearest
        n += 1
        linked.append(
            LinkedFinding(
                id=f"LNK-{n:03d}",
                kind="location_correlation",
                rationale=(
                    f"Location anomaly ({anomaly.get('type', 'unusual location')}, "
                    f"{anomaly.get('severity', 'info')}) at {anomaly.get('timestamp')} "
                    f"is {round(gap_s)}s from finding {f.id} (\"{f.title}\") at "
                    f"{f.timestamp}. {anomaly.get('explanation', '')} Two independently"
                    "-computed signals co-occurring in time — not, on its own, proof "
                    "either caused the other."
                ),
                left_ref=f"location_anomalies[{anomaly.get('type', '')}@{anomaly.get('timestamp')}]",
                right_ref=f.id,
                gap_seconds=round(gap_s),
            )
        )

    if not linked:
        hyp.status = "answered"
        hyp.detail = (
            f"{len(location_anomalies)} location anomaly(ies) flagged; none fell "
            "within the correlation window of a message/call finding."
        )
        return hyp, []

    hyp.status = "answered"
    hyp.finding_ids = [lf.right_ref for lf in linked]
    hyp.detail = f"{len(linked)} location/communication correlation(s) found."
    return hyp, linked


# --- optional narrative on top -----------------------------------------------
_NARRATIVE_SYSTEM = (
    "You are a forensic analyst assistant. Given a case profile, a set of "
    "investigative hypotheses and their already-grounded results, write a short, "
    "neutral synthesis (max 5 sentences) of what the investigation established. "
    "Reference only the provided hypotheses and linked findings — never invent a "
    "correlation, channel, or party not listed. Never assert guilt. Remind the reader "
    "that this requires human verification against the cited source artifacts."
)


def _investigation_narrative(
    provider: LLMProvider,
    profile: CaseProfile,
    hypotheses: list[Hypothesis],
    linked: list[LinkedFinding],
) -> Optional[str]:
    if not getattr(provider, "available", False) or provider.name == "heuristic":
        return None
    if not hypotheses:
        return None
    lines = [f"- [{h.status}] {h.question} → {h.detail}" for h in hypotheses]
    if linked:
        lines.append("Linked findings:")
        lines += [f"  - {lf.rationale}" for lf in linked[:10]]
    prompt = (
        f"Case: {profile.crime_label}\nDescription: {profile.description}\n\n"
        f"Investigation results:\n" + "\n".join(lines)
    )
    return provider.generate(_NARRATIVE_SYSTEM, prompt)


# --- entry point ---------------------------------------------------------------
def investigate_case(
    case: Any,
    profile: CaseProfile,
    plan: Optional[CollectionPlan] = None,
    provider: Optional[LLMProvider] = None,
) -> dict:
    """Read a live :class:`~triage.custody.Case`'s ``ai_findings`` (run
    :func:`~.analysis.analyze_case` first) plus its ``location_anomalies`` and
    ``contacts`` datasets, investigate, and persist the result as the
    ``investigation_trace`` derived dataset. Returns the bundle.

    Operates over the same top-ranked findings the examiner already sees in
    ``ai_findings`` (bounded by that call's own ``limit``, default 50) rather than
    every match before truncation — a finding that names a case entity scores highly
    by design (``_score()`` weights a named entity above almost everything else), so in
    practice a covering finding for the channel-gap check is very unlikely to fall
    outside that window, but this is a real scope limit, not a promise of exhaustive
    coverage — see the returned bundle's ``disclaimer``.
    """
    ai_findings = case.read_derived("ai_findings") or {}
    findings = [Finding(**d) for d in ai_findings.get("findings", [])]
    derived = {
        "contacts": case.read_derived("contacts") or [],
        "location_anomalies": case.read_derived("location_anomalies") or [],
    }
    bundle = investigate(derived, profile, findings, plan=plan, provider=provider)
    case.write_derived("investigation_trace", bundle)
    return bundle


def investigate(
    derived: dict[str, Any],
    profile: CaseProfile,
    findings: list[Finding],
    plan: Optional[CollectionPlan] = None,
    provider: Optional[LLMProvider] = None,
) -> dict:
    """Run every wired hypothesis over *derived* and *findings* (the same Finding list
    :func:`~.analysis.analyze_derived` already produced — this never re-scores from
    scratch) and return the ``investigation_trace`` bundle.

    Fully unit-testable with plain dicts/lists, matching ``analyze_derived``'s own
    contract, and never raises: a hypothesis that hits an unexpected shape is recorded
    as ``blocked`` with the reason, not allowed to take down the whole pass.
    """
    provider = provider or get_provider()

    hypotheses: list[Hypothesis] = []
    linked: list[LinkedFinding] = []

    try:
        hypotheses.append(
            _investigate_channel_gap(
                profile, derived.get("contacts", []) or [], findings, plan
            )
        )
    except Exception as exc:
        hypotheses.append(
            Hypothesis(
                id="H-CHANNEL-GAP",
                kind="channel_gap",
                question="Channel-gap check",
                dataset_scope=["contacts", "messages", "calls"],
                status="blocked",
                detail=f"Hypothesis errored and was not answered: {exc}",
            )
        )

    try:
        loc_hyp, loc_linked = _investigate_location_correlation(
            derived.get("location_anomalies", []) or [], findings
        )
        hypotheses.append(loc_hyp)
        linked.extend(loc_linked)
    except Exception as exc:
        hypotheses.append(
            Hypothesis(
                id="H-LOCATION-CORR",
                kind="location_correlation",
                question="Location/communication correlation check",
                dataset_scope=["location_anomalies", "messages", "calls"],
                status="blocked",
                detail=f"Hypothesis errored and was not answered: {exc}",
            )
        )

    narrative = _investigation_narrative(provider, profile, hypotheses, linked)
    analysis_method = "deterministic"
    if narrative:
        analysis_method = f"deterministic+llm:{provider.name}"

    return {
        "hypotheses": [h.to_dict() for h in hypotheses],
        "linked_findings": [lf.to_dict() for lf in linked],
        "narrative": narrative or "",
        "analysis_method": analysis_method,
        "disclaimer": (
            "A bounded, deterministic multi-hypothesis pass over this case's own "
            "already-collected, already-cited findings. A hypothesis marked 'blocked' "
            "means the data needed to answer it wasn't collected or wasn't usable — "
            "not that the answer is 'no'. A linked finding is a time correlation "
            "between two independently-grounded pieces of evidence, never a new fact "
            "on its own; both sides must be verified against their own source "
            "artifact. This is investigative lead generation, not a determination of "
            "guilt."
        ),
    }
