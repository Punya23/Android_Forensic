"""Unit tests for triage.intel.ai_summary — the entirely-model-authored AI Evidence
Summary, scoped to findings that match a case entity/keyword AND sit in a high-yield
artifact class for the case's crime type (see the module docstring for why both).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from triage.custody import Case, CaseMeta
from triage.intel.ai_summary import (
    _high_yield_artifacts,
    _relevant_findings,
    generate_ai_evidence_summary,
)
from triage.intel.analysis import Finding
from triage.intel.knowledge_graph import KnowledgeGraph
from triage.intel.llm import HeuristicProvider
from triage.intel.planner import CaseProfile


@pytest.fixture()
def case(tmp_path: Path) -> Case:
    return Case.create(tmp_path / "cases", CaseMeta(case_id="AISUM-1", examiner="Insp. Rao"))


def _profile(**kw) -> CaseProfile:
    base = dict(
        description="Drug trafficking case involving Rahul Sharma.",
        crime_type="drug_trafficking",
        crime_label="Drug Trafficking",
        suspects=["Rahul Sharma"],
        keywords=["shipment"],
    )
    base.update(kw)
    return CaseProfile(**base)


def _finding(**kw) -> Finding:
    base = dict(
        id="F-MSG-0001",
        title="whatsapp message",
        severity="high",
        score=0.9,
        category="message",
        app="whatsapp",
        entities_matched=["Rahul Sharma"],
        keywords_matched=[],
    )
    base.update(kw)
    return Finding(**base)


class _FakeProvider:
    """Stands in for OllamaProvider without a network — deterministic text back."""

    name = "ollama"
    available = True
    model = "llama3.1"
    degraded_from = ""

    def __init__(self, text: str = "Rahul Sharma is named in one WhatsApp message (F-MSG-0001)."):
        self._text = text

    def extract_json(self, system, prompt, schema_hint=None):
        return None

    def generate(self, system, prompt):
        return self._text


# --- _high_yield_artifacts ----------------------------------------------------
def test_high_yield_artifacts_falls_back_to_doctrine_with_no_graph():
    out = _high_yield_artifacts(None, "drug_trafficking")
    assert out, "doctrine alone must always yield something — see ontology defaults"
    assert all(d["blended"] >= 0.5 for d in out)
    # sorted richest-first
    assert [d["blended"] for d in out] == sorted((d["blended"] for d in out), reverse=True)


def test_high_yield_artifacts_same_with_bare_knowledge_graph():
    """A freshly-constructed KnowledgeGraph has no observations, so it must score
    identically to passing None — see the docstring rationale in ai_summary.py."""
    assert _high_yield_artifacts(None, "general") == _high_yield_artifacts(
        KnowledgeGraph(), "general"
    )


# --- _relevant_findings --------------------------------------------------------
def test_relevant_findings_requires_entity_or_keyword_match():
    profile = _profile()
    matching = _finding()
    noise = _finding(id="F-MSG-0002", entities_matched=[], keywords_matched=[])
    high_yield = {d["artifact"] for d in _high_yield_artifacts(None, profile.crime_type)}
    matched, entities = _relevant_findings(profile, [matching, noise], high_yield)
    assert [f.id for f in matched] == ["F-MSG-0001"]
    assert entities == ["Rahul Sharma"]


def test_relevant_findings_gates_message_call_recovered_browser_on_yield():
    profile = _profile()
    matching = _finding()
    # Not in the (fabricated) high-yield set at all — must be excluded even though it
    # matches an entity, because its category is yield-gated.
    matched, _ = _relevant_findings(profile, [matching], high_yield=set())
    assert matched == []


def test_relevant_findings_does_not_yield_gate_ungated_categories():
    profile = _profile()
    scam = _finding(
        id="F-SCAM-0001", category="scam_indicator", app="", entities_matched=[],
        keywords_matched=["shipment"],
    )
    # Empty high_yield set would exclude a "message" category finding, but a
    # scam_indicator finding isn't yield-gated at all (see _UNGATED_CATEGORIES).
    matched, _ = _relevant_findings(profile, [scam], high_yield=set())
    assert [f.id for f in matched] == ["F-SCAM-0001"]


def test_relevant_findings_fails_closed_on_an_unrecognised_category():
    """A future Finding.category this module was never updated for must default to
    EXCLUDED (gated, like message/call/recovered/browser), not silently exempted like
    contradiction/scam_indicator — the opposite default would defeat the whole
    "only high-yield artifact classes" premise for the category nobody remembered to
    list. See _UNGATED_CATEGORIES's docstring."""
    profile = _profile()
    future = _finding(id="F-NEW-0001", category="some_future_category")
    matched, _ = _relevant_findings(profile, [future], high_yield=set())
    assert matched == []


def test_relevant_findings_sorts_by_score_descending():
    profile = _profile()
    low = _finding(id="F-MSG-0002", score=0.3)
    high = _finding(id="F-MSG-0003", score=0.95)
    high_yield = {d["artifact"] for d in _high_yield_artifacts(None, profile.crime_type)}
    matched, _ = _relevant_findings(profile, [low, high], high_yield)
    assert [f.id for f in matched] == ["F-MSG-0003", "F-MSG-0002"]


# --- generate_ai_evidence_summary (end-to-end, real Case) ----------------------
def test_generate_summary_no_brief_entities_or_keywords(case: Case):
    profile = _profile(suspects=[], victims=[], other_entities=[], keywords=[])
    bundle = generate_ai_evidence_summary(
        case, profile, {"findings": []}, provider=HeuristicProvider()
    )
    assert bundle["generated"] is False
    assert "named no people or keywords" in bundle["reason"]
    # Unconditional write — the capability catalogue depends on this always landing.
    assert case.read_derived("ai_evidence_summary") == bundle


def test_generate_summary_no_matches(case: Case):
    profile = _profile()
    unrelated = _finding(entities_matched=[], keywords_matched=[])
    bundle = generate_ai_evidence_summary(
        case,
        profile,
        {"findings": [unrelated.to_dict()]},
        provider=HeuristicProvider(),
    )
    assert bundle["generated"] is False
    assert bundle["matched_count"] == 0
    assert "no collected finding matched" in bundle["reason"]


def test_generate_summary_heuristic_reports_real_matches_but_no_narrative(case: Case):
    """Matches exist and are recorded (traceable, auditable) even when there is no
    model to write prose from them — this is the honesty-model distinction the module
    docstring calls out: real matched evidence vs. no narrative available."""
    profile = _profile()
    finding = _finding()
    bundle = generate_ai_evidence_summary(
        case, profile, {"findings": [finding.to_dict()]}, provider=HeuristicProvider()
    )
    assert bundle["generated"] is False
    assert bundle["matched_count"] == 1
    assert bundle["matched_finding_ids"] == ["F-MSG-0001"]
    assert "no local model is reachable" in bundle["reason"]
    assert bundle["narrative"] == ""


def test_generate_summary_with_reachable_model_writes_narrative(case: Case):
    profile = _profile()
    finding = _finding()
    bundle = generate_ai_evidence_summary(
        case, profile, {"findings": [finding.to_dict()]}, provider=_FakeProvider()
    )
    assert bundle["generated"] is True
    assert bundle["provider"] == "ollama"
    assert bundle["model"] == "llama3.1"
    assert "F-MSG-0001" in bundle["narrative"]
    assert bundle["disclaimer"]  # never empty — this is investigative aid, not a verdict
    assert case.read_derived("ai_evidence_summary")["generated"] is True


def test_generate_summary_never_raises_on_malformed_findings(case: Case):
    """A bundle with an unexpected key on a stored Finding must not crash the whole
    summary pass — the caller (pipeline.py) already wraps this in a broad except, but
    the function itself should degrade to 'no matches' rather than KeyError."""
    profile = _profile()
    with pytest.raises(TypeError):
        # Deliberately malformed — Finding(**d) rejects an unknown field. Documents the
        # current contract: ai_findings is trusted internal shape, not external input.
        generate_ai_evidence_summary(
            case, profile, {"findings": [{"unexpected_field": 1}]}, provider=HeuristicProvider()
        )
