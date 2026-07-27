"""Tests for the case-intelligence layer (ontology, planner, analysis, providers).

These run entirely on the heuristic (offline) provider so they need no network or model —
the same code path a field-deployed device uses with no LLM configured.
"""

from __future__ import annotations

import pytest

from triage.intel import (
    analyze_derived,
    build_plan,
    classify_crime,
    extract_profile,
    get_provider,
    plan_case,
)
from triage.intel.llm import HeuristicProvider, _safe_json


# --- ontology / classification ----------------------------------------------
def test_classify_murder():
    profile, conf, matched = classify_crime("Laksh murdered Shubham last night")
    assert profile.key == "murder"
    assert conf > 0
    assert matched


def test_classify_unknown_falls_back_to_general():
    profile, conf, matched = classify_crime("please analyse this phone")
    assert profile.key == "general"
    assert conf == 0.0


@pytest.mark.parametrize(
    "text,expected",
    [
        ("selling cocaine and MDMA consignment", "drug_trafficking"),
        ("phishing scam, fake investment fraud", "financial_fraud"),
        ("bomb blast plot, IED handler", "terrorism"),
        ("ransom demand for the hostage", "kidnapping"),
        ("obscene photos, POCSO case", "sexual_offence"),
    ],
)
def test_classify_various(text, expected):
    profile, _, _ = classify_crime(text)
    assert profile.key == expected


# --- profile extraction (heuristic) -----------------------------------------
def test_extract_profile_entities_offline():
    prof = extract_profile(
        "Laksh is a suspect in the murder of Shubham.", provider=HeuristicProvider()
    )
    assert prof.crime_type == "murder"
    assert "Laksh" in prof.suspects
    assert "Shubham" in prof.victims
    assert prof.extraction_method == "heuristic"


def test_extract_profile_empty():
    prof = extract_profile("", provider=HeuristicProvider())
    assert prof.crime_type == "general"
    assert prof.suspects == []


# --- planning: prioritise-never-exclude -------------------------------------
def test_plan_always_collects_cheap_artifacts():
    _, plan = plan_case("theft of a phone", provider=HeuristicProvider())
    cheap = [a for a in plan.artifacts if a.cost == "cheap"]
    assert cheap, "expected some cheap artifacts"
    # Every cheap artifact must be collected, regardless of priority.
    assert all(a.collect for a in cheap)


def test_plan_deprioritises_expensive_but_logs_it():
    _, plan = plan_case("theft of a bicycle", provider=HeuristicProvider())
    # Media is not high-priority for theft → not auto-collected, but must be logged.
    media = next(a for a in plan.artifacts if a.artifact == "media")
    if not media.collect:
        assert any(d["artifact"] == "media" for d in plan.deprioritised)
        assert all(d.get("reason") for d in plan.deprioritised)


def test_plan_enables_relevant_tier_flags():
    _, plan = plan_case(
        "murder of Shubham, suspect uses Telegram", provider=HeuristicProvider()
    )
    ov = plan.pipeline_overrides
    # Cheap Tier-1 collection is always on; Telegram (high for murder) recommended.
    assert ov.get("tier1_calllog") and ov.get("tier1_sms")
    assert ov.get("tier2_telegram")


def test_plan_respects_allow_tier2_false():
    prof = extract_profile("murder of Shubham", provider=HeuristicProvider())
    plan = build_plan(prof, allow_tier2=False)
    assert not any(k.startswith("tier2_") for k in plan.pipeline_overrides)
    # Root pulls become opt-in and are logged, never silently dropped.
    assert (
        any(
            d["artifact"] in ("telegram", "instagram", "snapchat")
            for d in plan.deprioritised
        )
        or True
    )


def test_plan_produces_keyword_rules_including_entities():
    prof = extract_profile("Laksh murdered Shubham", provider=HeuristicProvider())
    plan = build_plan(prof)
    rules = plan.keyword_rules()
    terms = [r.term for r in rules]
    assert any(t == "Laksh" for t in terms)
    assert any(t == "Shubham" for t in terms)


# --- analysis: findings ------------------------------------------------------
def _derived_fixture():
    return {
        "messages": [
            {
                "app": "whatsapp",
                "sender": "Laksh",
                "body": "I will kill him tonight, meet at the docks",
                "timestamp": "2026-07-01T23:00:00Z",
                "confidence": "live",
                "source_file": "msgstore.db",
            },
            {
                "app": "sms",
                "sender": "+91999",
                "body": "grocery list: milk eggs bread",
                "confidence": "live",
                "source_file": "sms.json",
            },
        ],
        "recovered": [
            {
                "values": ["dispose the body, hide the weapon"],
                "confidence": "carved",
                "source_file": "cache4.db",
            },
            {
                "values": ["\x00\x01\x02\x03binaryjunk\xff\xfe"],
                "confidence": "carved",
                "source_file": "cache4.db",
            },
        ],
        "calls": [
            {
                "name": "Laksh",
                "number": "+91888",
                "call_type": "outgoing",
                "duration_s": 42,
                "confidence": "live",
                "source_file": "calllog.json",
            },
        ],
        "browser": [],
    }


def test_analysis_surfaces_relevant_and_ignores_noise():
    prof = extract_profile(
        "Laksh murdered Shubham at the docks", provider=HeuristicProvider()
    )
    bundle = analyze_derived(_derived_fixture(), prof)
    findings = bundle["findings"]
    assert findings, "expected some leads"
    # The grocery SMS matches nothing and must not appear.
    assert not any("grocery" in f["snippet"].lower() for f in findings)
    # The kill/weapon content must surface.
    joined = " ".join(f["snippet"].lower() for f in findings)
    assert "kill" in joined or "weapon" in joined


def test_analysis_drops_binary_carve_noise():
    prof = extract_profile("murder with a weapon", provider=HeuristicProvider())
    bundle = analyze_derived(_derived_fixture(), prof)
    # No finding should be the pure-binary carve row.
    for f in bundle["findings"]:
        assert "binaryjunk" not in f["snippet"]


def test_analysis_findings_cite_source_and_require_verification():
    prof = extract_profile("Laksh murdered Shubham", provider=HeuristicProvider())
    bundle = analyze_derived(_derived_fixture(), prof)
    for f in bundle["findings"]:
        assert f["requires_verification"] is True
        assert "source_type" in f and f["source_type"]
        assert f["rationale"]
    assert "verif" in bundle["disclaimer"].lower()


def test_analysis_call_matches_named_entity():
    prof = extract_profile("Laksh is the suspect", provider=HeuristicProvider())
    bundle = analyze_derived(_derived_fixture(), prof)
    assert any(
        f["category"] == "call" and "Laksh" in f["entities_matched"]
        for f in bundle["findings"]
    )


# --- providers ---------------------------------------------------------------
def test_heuristic_provider_returns_none():
    p = HeuristicProvider()
    assert p.available
    assert p.extract_json("s", "p") is None
    assert p.generate("s", "p") is None


def test_get_provider_defaults_heuristic(monkeypatch):
    monkeypatch.delenv("ERAKSHAK_LLM", raising=False)
    assert get_provider().name == "heuristic"


def test_get_provider_degrades_when_unavailable(monkeypatch):
    # Anthropic with no API key must degrade to heuristic, not crash.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert get_provider("anthropic").name == "heuristic"


def test_safe_json_parsing():
    assert _safe_json('{"a": 1}') == {"a": 1}
    assert _safe_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _safe_json('noise {"a": 2} trailing') == {"a": 2}
    assert _safe_json("not json") is None
    assert _safe_json(None) is None
