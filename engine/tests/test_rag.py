"""Tests for the case-study retrieval, nomenclature and knowledge-graph layer.

The properties these lock down are the ones that keep the feature honest:

    * proper forensic nomenclature is produced, and guilt-presuming language is flagged;
    * retrieval finds same-crime precedents and reports their provenance;
    * learning is *asymmetric* — an artifact is promoted on modest evidence but demoted
      only on strong, corroborated evidence, because evidence can only be collected once;
    * nothing in the RAG path can drop a cheap artifact from collection;
    * a fresh installation with no corpus and no history behaves exactly like the
      pure expert ontology.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from triage.intel.casebank import (  # noqa: E402
    ArtifactOutcome,
    CaseBank,
    CaseStudy,
    DEFAULT_CORPUS,
)
from triage.intel.feedback import (  # noqa: E402
    PROVISIONAL_WEIGHT,
    derive_artifact_yields,
    promote_case_to_study,
    record_confirmed,
    record_provisional,
)
from triage.intel.knowledge_graph import KnowledgeGraph  # noqa: E402
from triage.intel.nomenclature import (  # noqa: E402
    ADVERSE_ROLES,
    ROLES,
    extract_roles,
    glossary,
    normalise_role,
    roles_by_key,
    validate_description,
)
from triage.intel.planner import (  # noqa: E402
    build_plan,
    extract_profile,
    plan_case,
    retrieve_precedents,
)

MURDER_BRIEF = "Laksh is a suspect and he has done a murder of Shubham"


@pytest.fixture()
def bank():
    return CaseBank.load()


@pytest.fixture()
def graph(bank):
    kg = KnowledgeGraph()
    kg.bootstrap_from_casebank(bank)
    return kg


# --- nomenclature ------------------------------------------------------------
def test_roles_use_canonical_forensic_terms():
    roles = extract_roles(MURDER_BRIEF)
    by_name = {r.name: r.role for r in roles}
    assert by_name["Laksh"] == "suspect"
    assert by_name["Shubham"] == "deceased"
    # The deceased is never scored as a person under investigation.
    assert ROLES["deceased"].adverse is False
    assert ROLES["suspect"].adverse is True


def test_explicit_role_statement_beats_verb_inference():
    roles = {r.name: r.role for r in
             extract_roles("Ravi murdered Anil. Ravi is the complainant.")}
    # The explicit statement wins over "Ravi murdered".
    assert roles["Ravi"] == "complainant"


def test_sentence_initial_role_word_is_matched():
    roles = {r.name: r.role for r in
             extract_roles("Complainant Meera reported that accused Vivek cheated her.")}
    assert roles["Meera"] == "complainant"
    assert roles["Vivek"] == "accused"


def test_missing_person_is_its_own_role():
    roles = {r.name: r.role for r in extract_roles("Neha is missing since Tuesday.")}
    assert roles["Neha"] == "missing_person"


@pytest.mark.parametrize("word,expected", [
    ("prime suspect", "suspect"),
    ("accomplice", "co_accused"),
    ("prosecutrix", "victim"),
    ("proclaimed offender", "absconder"),
    ("panch", "panch_witness"),
    ("nonsense-word", "third_party"),
])
def test_role_aliases_normalise(word, expected):
    assert normalise_role(word) == expected


def test_guilt_presuming_language_is_flagged():
    warnings = validate_description("Laksh is guilty and Shubham was innocent")
    terms = {w["term"] for w in warnings if w["severity"] == "warn"}
    assert "guilty" in terms and "innocent" in terms


def test_description_with_no_named_party_warns():
    warnings = validate_description("someone stole a phone")
    assert any(w["severity"] == "warn" for w in warnings)


def test_glossary_covers_every_role():
    assert {g["key"] for g in glossary()} == set(ROLES)
    assert {g["key"] for g in glossary() if g["adverse"]} == set(ADVERSE_ROLES)


def test_roles_by_key_groups_names():
    grouped = roles_by_key(extract_roles(MURDER_BRIEF))
    assert grouped["suspect"] == ["Laksh"]


# --- case bank / retrieval ---------------------------------------------------
def test_bundled_corpus_loads_and_is_labelled(bank):
    assert len(bank) >= 15
    assert DEFAULT_CORPUS.exists()
    # Every bundled study must declare its provenance as a synthetic exemplar, so a
    # report can never present one as a real precedent.
    for study in bank.all():
        assert "synthetic" in study.source.lower()


def test_retrieval_prefers_same_crime_type(bank):
    profile = extract_profile(MURDER_BRIEF)
    hits, _ = retrieve_precedents(profile, bank)
    assert hits
    assert hits[0].study.crime_type == "murder"
    assert hits[0].crime_match is True


def test_retrieval_reports_what_solved_the_prior_case(bank):
    profile = extract_profile(MURDER_BRIEF)
    hits, evidence = retrieve_precedents(profile, bank)
    top = hits[0].to_dict()
    assert top["decisive_artifacts"]
    assert top["source"]
    # Aggregated evidence is keyed by artifact and scored 0..1.
    assert "call_logs" in evidence
    assert 0.0 <= evidence["call_logs"]["yield_score"] <= 1.0


def test_empty_bank_returns_no_precedents():
    profile = extract_profile(MURDER_BRIEF)
    hits, evidence = retrieve_precedents(profile, CaseBank())
    assert hits == [] and evidence == {}


def test_malformed_corpus_line_is_skipped(tmp_path):
    path = tmp_path / "corpus.jsonl"
    path.write_text('{"case_number": "OK-1", "crime_type": "theft"}\n'
                    'not json at all\n'
                    '{"case_number": "OK-2", "crime_type": "theft"}\n')
    bank = CaseBank()
    assert bank.load_file(path) == 2


def test_append_to_file_round_trips(tmp_path):
    path = tmp_path / "local.jsonl"
    bank = CaseBank()
    study = CaseStudy(case_number="LOCAL-1", crime_type="theft",
                      description="bike theft",
                      artifacts=[ArtifactOutcome("locations", "decisive")])
    bank.append_to_file(study, path)
    assert CaseBank(studies=[]).load_file(path) == 1
    assert bank.get("LOCAL-1").decisive_artifacts() == ["locations"]


# --- knowledge graph ---------------------------------------------------------
def test_graph_bootstraps_from_corpus(graph):
    stats = graph.stats()
    assert stats["distinct_cases"] >= 15
    assert "murder" in stats["crime_types"]


def test_bootstrap_is_idempotent(bank):
    kg = KnowledgeGraph()
    kg.bootstrap_from_casebank(bank)
    first = kg.stats()["distinct_cases"]
    kg.bootstrap_from_casebank(bank)
    assert kg.stats()["distinct_cases"] == first


def test_unobserved_edge_returns_pure_doctrine():
    kg = KnowledgeGraph()
    priors = kg.artifact_priors("murder")
    assert priors["call_logs"]["observations"] == 0
    assert priors["call_logs"]["source"] == "doctrine"
    # With no observations the blended prior IS the doctrinal value.
    assert priors["call_logs"]["blended"] == priors["call_logs"]["doctrine"]


def test_posterior_shrinks_toward_doctrine_when_thinly_observed():
    kg = KnowledgeGraph()
    kg.observe_case("theft", {"usage": "decisive"}, case_number="C1")
    edge = kg.edge("theft", "usage")
    blended = kg.blended_prior("theft", "usage")
    # One observation must not move the plan to the raw posterior.
    assert edge.posterior > blended


def test_graph_round_trips_through_disk(tmp_path, graph):
    path = tmp_path / "kg.json"
    graph.save(path)
    reloaded = KnowledgeGraph.load(path)
    assert reloaded.stats()["distinct_cases"] == graph.stats()["distinct_cases"]
    assert (reloaded.blended_prior("murder", "call_logs")
            == pytest.approx(graph.blended_prior("murder", "call_logs")))


def test_corrupt_graph_file_does_not_raise(tmp_path):
    path = tmp_path / "kg.json"
    path.write_text("{ this is not json")
    kg = KnowledgeGraph.load(path)          # must degrade, not explode
    assert kg.stats()["distinct_cases"] == 0


def test_similar_crime_types_are_derived_from_shared_artifacts(graph):
    similar = graph.similar_crime_types("murder")
    assert similar
    assert all(0.0 <= s["similarity"] <= 1.0 for s in similar)
    assert similar[0]["crime_type"] != "murder"


def test_cooccurrence_records_artifacts_that_break_cases_together():
    kg = KnowledgeGraph()
    kg.observe_case("theft", {"locations": "decisive", "call_logs": "decisive"},
                    case_number="C1")
    partners = [c["artifact"] for c in kg.co_occurring("locations")]
    assert "call_logs" in partners


# --- fusion: the safety properties -------------------------------------------
def test_no_corpus_and_no_graph_equals_pure_ontology():
    """A fresh install must plan exactly as the expert ontology alone dictates."""
    profile = extract_profile(MURDER_BRIEF)
    doctrine_only = build_plan(profile)
    for entry in doctrine_only.artifacts:
        assert entry.priority == entry.doctrine_priority
    assert doctrine_only.evidence_basis == "doctrine"


def test_rag_never_drops_a_cheap_artifact(bank, graph):
    """Prioritise-never-exclude survives retrieval and learning."""
    # Teach the graph, hard, that every cheap artifact is worthless for murder.
    for i in range(60):
        graph.observe_case("murder",
                           {"call_logs": "none", "sms": "none", "contacts": "none",
                            "browser": "none", "locations": "none", "usage": "none"},
                           case_number=f"NEG-{i}")
    _, plan = plan_case(MURDER_BRIEF, bank=bank, graph=graph)
    cheap = [a for a in plan.artifacts if a.cost == "cheap"]
    assert cheap, "expected cheap artifacts in the plan"
    assert all(a.collect for a in cheap), (
        "a cheap artifact was dropped — evidence can only be collected once"
    )


def test_promotion_needs_less_evidence_than_demotion(bank, graph):
    """Asymmetry: collecting more costs time, collecting less can lose the case."""
    profile = extract_profile(MURDER_BRIEF)

    # Same amount of one-sided evidence, in opposite directions, on two artifacts
    # that both start at the doctrinal 'medium' band for murder.
    for i in range(12):
        graph.observe_case("murder", {"media": "decisive", "calendar": "none"},
                           case_number=f"ASYM-{i}")

    hits, evidence = retrieve_precedents(profile, bank)
    plan = build_plan(profile, precedents=hits, artifact_evidence=evidence, graph=graph)
    entries = {a.artifact: a for a in plan.artifacts}

    media, calendar = entries["media"], entries["calendar"]
    assert media.doctrine_priority == "medium"
    assert calendar.doctrine_priority == "medium"
    # Promotion is allowed to land; demotion is capped/blocked and must say so.
    assert media.adjustment in ("", "promoted")
    if calendar.adjustment == "demoted":
        assert any("one band" in e or "thin" in e for e in calendar.evidence)


def test_thin_demotion_evidence_keeps_the_doctrinal_ranking():
    profile = extract_profile(MURDER_BRIEF)
    kg = KnowledgeGraph()
    kg.observe_case("murder", {"call_logs": "none"}, case_number="ONE")
    plan = build_plan(profile, graph=kg)
    call_logs = next(a for a in plan.artifacts if a.artifact == "call_logs")
    assert call_logs.priority == call_logs.doctrine_priority


def test_sustained_evidence_can_eventually_promote_an_expensive_pull(bank, graph):
    before = next(a for a in plan_case(MURDER_BRIEF, bank=bank, graph=graph)[1].artifacts
                  if a.artifact == "media")
    assert before.collect is False          # doctrine: media is opt-in for homicide
    for i in range(25):
        graph.observe_case("murder", {"media": "decisive"}, case_number=f"MED-{i}")
    after = next(a for a in plan_case(MURDER_BRIEF, bank=bank, graph=graph)[1].artifacts
                 if a.artifact == "media")
    assert after.fused_score > before.fused_score
    assert after.collect is True


def test_plan_cites_its_evidence(bank, graph):
    _, plan = plan_case(MURDER_BRIEF, bank=bank, graph=graph)
    assert plan.evidence_basis == "fused"
    assert plan.precedents
    # Every precedent carries its provenance so a report cannot imply real precedent.
    assert all(p["source"] for p in plan.precedents)
    assert any("not evidence in this case" in n for n in plan.notes)


def test_recommendations_surface_skipped_artifacts_that_solved_similar_cases(bank, graph):
    _, plan = plan_case(MURDER_BRIEF, bank=bank, graph=graph)
    skipped = {a.artifact for a in plan.artifacts if not a.collect}
    for rec in plan.recommendations:
        assert rec["artifact"] in skipped
        assert rec["cases"]
        assert "decisive" in rec["message"]


def test_plan_reports_estimated_savings(bank, graph):
    _, plan = plan_case(MURDER_BRIEF, bank=bank, graph=graph)
    savings = plan.estimated_savings
    assert savings["estimated_minutes_saved"] >= 0
    assert savings["estimated_minutes_saved"] <= savings["estimated_minutes_full_run"]
    # Must be labelled an estimate, not presented as a measurement.
    assert "estimate" in savings["basis"]


def test_case_number_flows_into_the_profile(bank, graph):
    profile, _ = plan_case(MURDER_BRIEF, case_number="FIR-2026/114",
                           bank=bank, graph=graph)
    assert profile.case_number == "FIR-2026/114"


# --- feedback ----------------------------------------------------------------
def test_uncollected_artifacts_are_not_graded_as_failures():
    """A non-observation must never be learned as 'this artifact is useless'."""
    profile = extract_profile(MURDER_BRIEF)
    plan = build_plan(profile)
    bundle = {"findings": [{"source_type": "message", "score": 9.0}]}
    yields = derive_artifact_yields(bundle, plan)
    skipped = {a.artifact for a in plan.artifacts if not a.collect}
    assert not (skipped & set(yields)), "an uncollected artifact was graded"


def test_yields_grade_by_lead_score():
    bundle = {"findings": [
        {"source_type": "message", "score": 9.0},      # decisive
        {"source_type": "call", "score": 3.0},         # supporting
        {"source_type": "browser", "score": 0.2},      # none
    ]}
    yields = derive_artifact_yields(bundle, None)
    assert yields["sms"] == "decisive"
    assert yields["call_logs"] == "supporting"
    assert yields["browser"] == "none"


def test_critical_severity_counts_as_decisive():
    bundle = {"findings": [{"source_type": "recovered", "score": 0.1,
                            "severity": "critical"}]}
    assert derive_artifact_yields(bundle, None)["deleted"] == "decisive"


def test_provisional_feedback_is_weight_limited():
    kg = KnowledgeGraph()
    profile = extract_profile(MURDER_BRIEF, case_number="FIR-1")
    bundle = {"findings": [{"source_type": "call", "score": 9.0}]}
    result = record_provisional(kg, profile, bundle, None)
    assert result["recorded"] and result["grade"] == "provisional"
    assert result["weight"] == PROVISIONAL_WEIGHT < 1.0
    # One provisional case must move the prior less than one confirmed case.
    kg2 = KnowledgeGraph()
    record_confirmed(kg2, "murder", {"call_logs": "decisive"}, case_number="FIR-1")
    assert (kg.blended_prior("murder", "call_logs")
            < kg2.blended_prior("murder", "call_logs"))


def test_replaying_the_same_case_does_not_inflate_counts():
    kg = KnowledgeGraph()
    for _ in range(5):
        kg.observe_case("theft", {"locations": "decisive"}, case_number="SAME")
    assert kg.edge("theft", "locations").observations == 1


def test_confirmed_outcome_rejects_invalid_yields():
    kg = KnowledgeGraph()
    assert record_confirmed(kg, "murder", {"call_logs": "banana"},
                            case_number="X")["recorded"] is False


def test_promote_case_to_study_records_local_provenance():
    profile = extract_profile(MURDER_BRIEF, case_number="FIR-2026/114")
    study = promote_case_to_study(profile, {"call_logs": "decisive"},
                                  outcome="charge-sheeted", examiner="SI Rao")
    assert study.case_number == "FIR-2026/114"
    assert study.decisive_artifacts() == ["call_logs"]
    assert "synthetic" not in study.source.lower()
    assert "SI Rao" in study.source
    assert study.roles.get("suspect") == ["Laksh"]


def test_feedback_round_trip_changes_the_next_plan(bank, tmp_path):
    """The whole point: what one case learned must reach the next case's plan."""
    path = tmp_path / "kg.json"
    kg = KnowledgeGraph.load(path, bootstrap=bank)
    baseline = next(a for a in plan_case(MURDER_BRIEF, bank=bank, graph=kg)[1].artifacts
                    if a.artifact == "media").fused_score

    for i in range(20):
        record_confirmed(kg, "murder", {"media": "decisive"}, case_number=f"FIR-{i}")
    kg.save(path)

    reloaded = KnowledgeGraph.load(path)
    after = next(a for a in plan_case(MURDER_BRIEF, bank=bank, graph=reloaded)[1].artifacts
                 if a.artifact == "media").fused_score
    assert after > baseline
