"""Unit tests for triage.intel.entity_links — deterministic, same-case name/number
cross-linking against this case's own already-collected data (see the module
docstring for why this is substring match, disclosed as such, not identity
resolution).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from triage.custody import Case, CaseMeta
from triage.intel.entity_links import build_entity_links, build_entity_links_for_case
from triage.intel.planner import CaseProfile


@pytest.fixture()
def case(tmp_path: Path) -> Case:
    return Case.create(tmp_path / "cases", CaseMeta(case_id="ELINK-1", examiner="Insp. Rao"))


def _profile(**kw) -> CaseProfile:
    base = dict(
        description="Drug trafficking case involving Rahul Sharma.",
        crime_type="drug_trafficking",
        crime_label="Drug Trafficking",
        suspects=["Rahul Sharma"],
    )
    base.update(kw)
    return CaseProfile(**base)


_DERIVED = {
    "messages": [
        {"sender": "Rahul Sharma", "body": "meet tonight", "timestamp": "2026-01-01T21:00:00"}
    ],
    "calls": [
        {"name": "Rahul Sharma", "number": "9876543210", "call_type": "outgoing"}
    ],
    "contacts": [{"name": "Rahul Sharma", "number": "9876543210"}],
    "browser": [],
    "locations": [],
    "recovered": [],
}


def test_no_entities_named_gives_honest_reason_not_a_silent_empty():
    result = build_entity_links(_DERIVED, _profile(suspects=[]))
    assert result["entities"] == []
    assert "named no people" in result["reason"]


def test_entity_matched_across_multiple_datasets():
    result = build_entity_links(_DERIVED, _profile())
    assert result["entity_count"] == 1
    link = result["entities"][0]
    assert link["entity"] == "Rahul Sharma"
    assert link["occurrence_count"] == 3
    assert set(link["datasets"]) == {"messages", "calls", "contacts"}


def test_case_insensitive_match():
    derived = {**_DERIVED, "messages": [{"sender": "rahul SHARMA", "body": "hi"}]}
    result = build_entity_links(derived, _profile())
    assert result["entities"][0]["occurrence_count"] == 3  # message + call + contact


def test_entity_not_present_in_any_dataset_yields_zero_not_omitted():
    profile = _profile(suspects=["Someone Else"])
    result = build_entity_links(_DERIVED, profile)
    assert result["entity_count"] == 1
    assert result["entities"][0]["occurrence_count"] == 0
    assert result["entities"][0]["datasets"] == []


def test_richest_linked_entity_sorts_first():
    derived = {
        **_DERIVED,
        "messages": [
            {"sender": "Rahul Sharma", "body": "a", "timestamp": "t1"},
            {"sender": "Priya Nair", "body": "b", "timestamp": "t2"},
        ],
    }
    profile = _profile(suspects=["Rahul Sharma"], victims=["Priya Nair"])
    result = build_entity_links(derived, profile)
    assert [e["entity"] for e in result["entities"]] == ["Rahul Sharma", "Priya Nair"]


def test_occurrences_truncated_but_count_stays_accurate():
    many_messages = [
        {"sender": "Rahul Sharma", "body": f"msg {i}", "timestamp": f"t{i}"}
        for i in range(80)
    ]
    derived = {**_DERIVED, "messages": many_messages, "calls": [], "contacts": []}
    result = build_entity_links(derived, _profile())
    link = result["entities"][0]
    assert link["occurrence_count"] == 80
    assert len(link["occurrences"]) == 50
    assert link["truncated"] == 30


def test_no_passages_at_all_gives_honest_reason():
    empty = {k: [] for k in _DERIVED}
    result = build_entity_links(empty, _profile())
    assert result["entities"][0]["occurrence_count"] == 0
    assert "were collected to search" in result["reason"]


def test_disclaimer_always_present():
    result = build_entity_links(_DERIVED, _profile())
    assert "not identity resolution" in result["disclaimer"]


def test_build_entity_links_for_case_reads_and_persists(case: Case):
    case.write_derived("messages", _DERIVED["messages"])
    case.write_derived("calls", _DERIVED["calls"])
    case.write_derived("contacts", _DERIVED["contacts"])

    bundle = build_entity_links_for_case(case, _profile())

    assert bundle["entity_count"] == 1
    persisted = case.read_derived("entity_links")
    assert persisted == bundle
