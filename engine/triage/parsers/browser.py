"""Parser for Chromium/Chrome browser history (the `urls` table of a `History` SQLite DB).

Browser history is a high-value triage artifact (search terms, visited sites, timing). On a
non-rooted device the History DB sits in app-private storage, so this is realistically a
Tier-2/root or extracted-image artifact — but when the file is available (root, backup, or
our synthetic corpus) it parses cleanly, and its deleted rows are recoverable by the same
SQLite carver as everything else.

Chrome stores visit times as microseconds since 1601-01-01 (the Windows/WebKit epoch); we
convert to ISO-8601.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

# 1601-01-01 .. 1970-01-01 in microseconds
_WEBKIT_EPOCH_OFFSET_US = 11644473600 * 1_000_000


def _webkit_to_iso(micros) -> Optional[str]:
    try:
        us = int(micros)
        if us <= 0:
            return None
        dt = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(
            microseconds=us - _WEBKIT_EPOCH_OFFSET_US)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OverflowError):
        return None


def parse_browser_history(path: str | Path, max_rows: int = 5000) -> list[dict[str, Any]]:
    """Return browser-history rows: [{url, title, visit_count, last_visit}]."""
    path = Path(path)
    out: list[dict[str, Any]] = []
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        rows = con.execute(
            "SELECT url, title, visit_count, last_visit_time FROM urls "
            f"ORDER BY last_visit_time DESC LIMIT {int(max_rows)}").fetchall()
        con.close()
    except sqlite3.Error:
        return []
    for url, title, visits, last in rows:
        out.append({
            "url": url or "",
            "title": title or "",
            "visit_count": visits or 0,
            "last_visit": _webkit_to_iso(last),
            "source_file": path.name,
        })
    return out
