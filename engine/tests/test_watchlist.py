"""Tests for triage/forensics/watchlist.py.

Covers exact-match watchlisting (persons/identifiers of interest, examiner-curated
ground truth) and the two hardening fixes: atomic save (no truncated file on an
interrupted write) and surfaced load errors (a corrupt watchlist must not silently
look identical to "no matches, nothing on the watchlist").
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from triage.forensics.watchlist import WatchlistMatcher, get_watchlist_alerts  # noqa: E402


@pytest.fixture()
def wp(tmp_path: Path) -> Path:
    return tmp_path / "watchlist.json"


def test_missing_file_is_empty_watchlist_no_error(wp: Path):
    m = WatchlistMatcher(wp)
    assert all(len(v) == 0 for v in m.watchlist.values())
    assert m.load_error == ""


def test_add_persists_and_reloads(wp: Path):
    m = WatchlistMatcher(wp)
    m.add_to_watchlist("phone_numbers", "+919820044711")
    assert wp.exists()
    reloaded = WatchlistMatcher(wp)
    assert "+919820044711" in reloaded.watchlist["phone_numbers"]


def test_remove_persists(wp: Path):
    m = WatchlistMatcher(wp)
    m.add_to_watchlist("emails", "x@example.com")
    m.remove_from_watchlist("emails", "x@example.com")
    reloaded = WatchlistMatcher(wp)
    assert "x@example.com" not in reloaded.watchlist["emails"]


def test_unknown_category_is_a_no_op(wp: Path):
    m = WatchlistMatcher(wp)
    m.add_to_watchlist("not_a_real_category", "value")
    assert not wp.exists()  # nothing to persist


def test_corrupt_file_reports_load_error_not_silent_empty(wp: Path):
    wp.write_text("{not valid json")
    m = WatchlistMatcher(wp)
    assert m.load_error != ""
    assert all(len(v) == 0 for v in m.watchlist.values())


def test_save_is_atomic_no_tmp_file_left_behind(wp: Path):
    m = WatchlistMatcher(wp)
    m.add_to_watchlist("phone_numbers", "123")
    assert not wp.with_suffix(".tmp").exists()
    assert json.loads(wp.read_text())["phone_numbers"] == ["123"]


def test_contact_match_by_number_name_email(wp: Path):
    m = WatchlistMatcher(wp)
    m.add_to_watchlist("phone_numbers", "+919820044711")
    m.add_to_watchlist("names", "Imran K")
    matches = m.match_data(
        [{"number": "+919820044711", "name": "Imran K", "email": "irrelevant@example.com"}],
        "contact",
    )
    categories = {mm["category"] for mm in matches}
    assert "phone_numbers" in categories
    assert "names" in categories
    assert "emails" not in categories


def test_message_match_by_sender_and_upi(wp: Path):
    m = WatchlistMatcher(wp)
    m.add_to_watchlist("phone_numbers", "+919820044711")
    m.add_to_watchlist("upi_ids", "imran99@okhdfcbank")
    matches = m.match_data(
        [{"body": "pay imran99@okhdfcbank now", "sender": "+919820044711", "source_file": "m.db"}],
        "message",
    )
    categories = {mm["category"] for mm in matches}
    assert categories == {"phone_numbers", "upi_ids"}


def test_call_match_by_number(wp: Path):
    m = WatchlistMatcher(wp)
    m.add_to_watchlist("phone_numbers", "+919820044711")
    matches = m.match_data([{"number": "+919820044711"}], "call")
    assert len(matches) == 1


def test_no_match_returns_empty(wp: Path):
    m = WatchlistMatcher(wp)
    m.add_to_watchlist("phone_numbers", "+919820044711")
    assert m.match_data([{"number": "+911111111111"}], "call") == []


def test_alerts_are_critical_and_cite_source(wp: Path):
    m = WatchlistMatcher(wp)
    matches = [{"category": "phone_numbers", "value": "123", "source": {"source_file": "calllog.json"}}]
    alerts = m.get_watchlist_alerts(matches)
    assert alerts[0]["severity"] == "CRITICAL"
    assert alerts[0]["source_context"] == "calllog.json"
    assert alerts[0]["timestamp"].endswith("Z")


def test_module_level_get_watchlist_alerts_wrapper():
    matches = [{"category": "emails", "value": "x@example.com", "source": {"source_file": "m.db"}}]
    alerts = get_watchlist_alerts(matches)
    assert alerts[0]["value"] == "x@example.com"
