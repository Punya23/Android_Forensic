"""Tests for the cross-case identifier index added to triage/registry.py
(``case_identifiers`` table, ``upsert_case_identifiers``, ``find_linked_cases``).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from triage import registry  # noqa: E402
from triage.forensics.case_reference import extract_case_identifiers  # noqa: E402


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    return tmp_path


def test_upsert_indexes_every_identifier(root: Path):
    ids = extract_case_identifiers(
        contacts=[{"name": "Imran K", "number": "+91 98200 44711"}],
    )
    n = registry.upsert_case_identifiers(root, "CASE-A", ids)
    assert n == 1


def test_find_linked_cases_matches_normalized_phone_across_formats(root: Path):
    ids_a = extract_case_identifiers(contacts=[{"name": "Imran K", "number": "+91 98200 44711"}])
    ids_b = extract_case_identifiers(calls=[{"number": "+919820044711", "source_file": "c.json", "timestamp": "t"}])
    registry.upsert_case_identifiers(root, "CASE-A", ids_a)
    registry.upsert_case_identifiers(root, "CASE-B", ids_b)

    linked = registry.find_linked_cases(root, "CASE-A")
    assert len(linked) == 1
    assert linked[0]["case_id"] == "CASE-B"
    assert linked[0]["shared"][0]["category"] == "phone_numbers"
    # Each side's ORIGINAL (non-normalised) value is preserved for citation.
    assert linked[0]["shared"][0]["this_value"] == "+91 98200 44711"
    assert linked[0]["shared"][0]["other_value"] == "+919820044711"


def test_find_linked_cases_is_symmetric(root: Path):
    ids_a = extract_case_identifiers(contacts=[{"name": "X", "number": "+919820044711"}])
    ids_b = extract_case_identifiers(contacts=[{"name": "Y", "number": "+919820044711"}])
    registry.upsert_case_identifiers(root, "CASE-A", ids_a)
    registry.upsert_case_identifiers(root, "CASE-B", ids_b)

    assert {e["case_id"] for e in registry.find_linked_cases(root, "CASE-A")} == {"CASE-B"}
    assert {e["case_id"] for e in registry.find_linked_cases(root, "CASE-B")} == {"CASE-A"}


def test_no_self_matches(root: Path):
    ids = extract_case_identifiers(contacts=[{"name": "X", "number": "+919820044711"}])
    registry.upsert_case_identifiers(root, "CASE-A", ids)
    assert registry.find_linked_cases(root, "CASE-A") == []


def test_unrelated_cases_produce_no_link(root: Path):
    ids_a = extract_case_identifiers(contacts=[{"name": "X", "number": "+919820044711"}])
    ids_b = extract_case_identifiers(contacts=[{"name": "Y", "number": "+911111111111"}])
    registry.upsert_case_identifiers(root, "CASE-A", ids_a)
    registry.upsert_case_identifiers(root, "CASE-B", ids_b)
    assert registry.find_linked_cases(root, "CASE-A") == []


def test_reupsert_replaces_not_accumulates(root: Path):
    """A re-run after new artifacts are collected must not leave stale rows from an
    earlier, smaller extraction alongside the new ones."""
    ids_first = extract_case_identifiers(contacts=[{"name": "X", "number": "+911111111111"}])
    registry.upsert_case_identifiers(root, "CASE-A", ids_first)
    ids_second = extract_case_identifiers(contacts=[{"name": "Y", "number": "+922222222222"}])
    n = registry.upsert_case_identifiers(root, "CASE-A", ids_second)
    assert n == 1

    ids_b = extract_case_identifiers(contacts=[{"name": "Z", "number": "+911111111111"}])
    registry.upsert_case_identifiers(root, "CASE-B", ids_b)
    # The stale first-run number must no longer link CASE-A to CASE-B.
    assert registry.find_linked_cases(root, "CASE-A") == []


def test_delete_case_row_removes_identifiers_too(root: Path):
    ids = extract_case_identifiers(contacts=[{"name": "X", "number": "+919820044711"}])
    registry.upsert_case_identifiers(root, "CASE-A", ids)
    registry.delete_case_row(root, "CASE-A")
    conn = registry._connect(root)
    try:
        rows = conn.execute("SELECT COUNT(*) FROM case_identifiers WHERE case_id = ?", ("CASE-A",)).fetchone()
        assert rows[0] == 0
    finally:
        conn.close()


def test_grouped_by_other_case_with_multiple_shared_identifiers(root: Path):
    ids_a = extract_case_identifiers(
        contacts=[{"name": "X", "number": "+919820044711", "email": "x@example.com"}]
    )
    ids_b = extract_case_identifiers(
        contacts=[{"name": "Y", "number": "+919820044711"}],
        messages=[{"body": "reach x@example.com", "source_file": "m.db", "timestamp": "t"}],
    )
    registry.upsert_case_identifiers(root, "CASE-A", ids_a)
    registry.upsert_case_identifiers(root, "CASE-B", ids_b)
    linked = registry.find_linked_cases(root, "CASE-A")
    assert len(linked) == 1
    assert len(linked[0]["shared"]) == 2
