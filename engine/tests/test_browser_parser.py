"""Unit tests for the Chromium and Firefox browser-history parsers."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from triage.parsers.browser import parse_browser_history, parse_firefox_places

_WEBKIT_BASE = 13_360_000_000_000_000  # ~2026, WebKit epoch (microseconds since 1601-01-01)


def _build_chrome_history(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT, "
        "visit_count INTEGER, last_visit_time INTEGER)"
    )
    con.execute(
        "INSERT INTO urls(url,title,visit_count,last_visit_time) VALUES (?,?,?,?)",
        ("https://example.com/search?q=test", "test - Example", 4, _WEBKIT_BASE),
    )
    con.commit()
    con.close()


def _build_firefox_places(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE moz_places (id INTEGER PRIMARY KEY, url TEXT, title TEXT, "
        "visit_count INTEGER)"
    )
    con.execute(
        "CREATE TABLE moz_historyvisits (id INTEGER PRIMARY KEY, place_id INTEGER, "
        "visit_date INTEGER)"
    )
    con.execute(
        "INSERT INTO moz_places(url,title,visit_count) VALUES (?,?,?)",
        ("https://mozilla.org/", "Mozilla", 2),
    )
    con.execute(
        "INSERT INTO moz_historyvisits(place_id,visit_date) VALUES (1, ?)",
        (1_770_000_000_000_000,),
    )
    con.commit()
    con.close()


def test_parse_browser_history_returns_rows(tmp_path: Path):
    db = tmp_path / "History"
    _build_chrome_history(db)
    rows = parse_browser_history(db)
    assert len(rows) == 1
    assert rows[0]["url"] == "https://example.com/search?q=test"
    assert rows[0]["visit_count"] == 4
    assert rows[0]["last_visit"] is not None
    assert rows[0]["source_file"] == "History"


def test_parse_browser_history_tags_browser_app(tmp_path: Path):
    db = tmp_path / "History"
    _build_chrome_history(db)
    rows = parse_browser_history(db, browser_app="Brave")
    assert rows[0]["browser_app"] == "Brave"


def test_parse_browser_history_defaults_browser_app_to_empty_string(tmp_path: Path):
    db = tmp_path / "History"
    _build_chrome_history(db)
    rows = parse_browser_history(db)
    assert rows[0]["browser_app"] == ""


def test_parse_browser_history_missing_file_returns_empty(tmp_path: Path):
    assert parse_browser_history(tmp_path / "nope.db") == []


def test_parse_firefox_places_returns_rows(tmp_path: Path):
    db = tmp_path / "places.sqlite"
    _build_firefox_places(db)
    rows = parse_firefox_places(db)
    assert len(rows) == 1
    assert rows[0]["url"] == "https://mozilla.org/"
    assert rows[0]["title"] == "Mozilla"
    assert rows[0]["visit_count"] == 2
    assert rows[0]["last_visit"] is not None
    assert rows[0]["browser_app"] == "Firefox"


def test_parse_firefox_places_row_without_visits_is_excluded(tmp_path: Path):
    """A ``moz_places`` row with no matching ``moz_historyvisits`` join yields nothing —
    the INNER JOIN is deliberate: a place with zero visits has no "last visit" to report."""
    db = tmp_path / "places.sqlite"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE moz_places (id INTEGER PRIMARY KEY, url TEXT, title TEXT, "
        "visit_count INTEGER)"
    )
    con.execute(
        "CREATE TABLE moz_historyvisits (id INTEGER PRIMARY KEY, place_id INTEGER, "
        "visit_date INTEGER)"
    )
    con.execute(
        "INSERT INTO moz_places(url,title,visit_count) VALUES (?,?,?)",
        ("https://orphan.example/", "Orphan", 0),
    )
    con.commit()
    con.close()
    assert parse_firefox_places(db) == []


def test_parse_firefox_places_missing_file_returns_empty(tmp_path: Path):
    assert parse_firefox_places(tmp_path / "nope.sqlite") == []
