"""Parsers for browser history: Chromium's ``urls`` table and Firefox's Places DB.

Browser history is a high-value triage artifact (search terms, visited sites, timing). On a
non-rooted device the History/places.sqlite DB sits in app-private storage, so this is
realistically a Tier-2/root or extracted-image artifact — but when the file is available
(root, backup, or our synthetic corpus) it parses cleanly, and its deleted rows are
recoverable by the same SQLite carver as everything else. See ``pipeline._run_tier2_browser_history``
for the root pull that reaches the real per-browser DB on a live device.

Chrome/Brave/Samsung Internet/Edge (Chromium-family) store visit times as microseconds
since 1601-01-01 (the Windows/WebKit epoch). Firefox stores them as microseconds since
1970-01-01 (Unix epoch). Both are converted to ISO-8601 here.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

# 1601-01-01 .. 1970-01-01 in microseconds
_WEBKIT_EPOCH_OFFSET_US = 11644473600 * 1_000_000

# Chromium-family browser packages known to store history in the same ``urls``-table schema,
# keyed by package so callers can label rows with a human-readable browser name.
CHROMIUM_BROWSER_PACKAGES: dict[str, str] = {
    "com.android.chrome": "Chrome",
    "com.brave.browser": "Brave",
    "com.sec.android.app.sbrowser": "Samsung Internet",
    "com.microsoft.emmx": "Edge",
    "com.vivaldi.browser": "Vivaldi",
}


def _webkit_to_iso(micros) -> Optional[str]:
    try:
        us = int(micros)
        if us <= 0:
            return None
        dt = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(
            microseconds=us - _WEBKIT_EPOCH_OFFSET_US
        )
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OverflowError):
        return None


def _unix_micros_to_iso(micros) -> Optional[str]:
    try:
        us = int(micros)
        if us <= 0:
            return None
        return datetime.fromtimestamp(us / 1_000_000, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def parse_browser_history(
    path: str | Path, max_rows: int = 5000, browser_app: Optional[str] = None
) -> list[dict[str, Any]]:
    """Return Chromium-family browser-history rows: [{url, title, visit_count, last_visit}].

    ``browser_app`` labels every row (e.g. "Chrome", "Brave") so a device with more than one
    Chromium-based browser installed doesn't blend their histories into one anonymous list.
    """
    path = Path(path)
    out: list[dict[str, Any]] = []
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        rows = con.execute(
            "SELECT url, title, visit_count, last_visit_time FROM urls "
            f"ORDER BY last_visit_time DESC LIMIT {int(max_rows)}"
        ).fetchall()
        con.close()
    except sqlite3.Error:
        return []
    for url, title, visits, last in rows:
        out.append(
            {
                "url": url or "",
                "title": title or "",
                "visit_count": visits or 0,
                "last_visit": _webkit_to_iso(last),
                "browser_app": browser_app or "",
                "source_file": path.name,
            }
        )
    return out


def parse_firefox_places(
    path: str | Path, max_rows: int = 5000, browser_app: Optional[str] = None
) -> list[dict[str, Any]]:
    """Return Firefox history rows from a ``places.sqlite`` DB, same shape as
    :func:`parse_browser_history` so both feed one ``browser`` dataset.

    Firefox splits history across ``moz_places`` (one row per URL) and
    ``moz_historyvisits`` (one row per visit); the join and per-URL ``MAX(visit_date)``
    reproduce Chromium's "last_visit_time" semantics from a different schema.
    """
    path = Path(path)
    out: list[dict[str, Any]] = []
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        rows = con.execute(
            "SELECT p.url, p.title, p.visit_count, MAX(h.visit_date) AS last_visit "
            "FROM moz_places p JOIN moz_historyvisits h ON h.place_id = p.id "
            "GROUP BY p.id ORDER BY last_visit DESC "
            f"LIMIT {int(max_rows)}"
        ).fetchall()
        con.close()
    except sqlite3.Error:
        return []
    for url, title, visits, last in rows:
        out.append(
            {
                "url": url or "",
                "title": title or "",
                "visit_count": visits or 0,
                "last_visit": _unix_micros_to_iso(last),
                "browser_app": browser_app or "Firefox",
                "source_file": path.name,
            }
        )
    return out
