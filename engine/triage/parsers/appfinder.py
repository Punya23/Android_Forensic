"""Dynamic App Finder — generic SQLite chat discovery for *unknown* apps.

Cellebrite (App Genie) and Magnet (Dynamic App Finder) both ship a feature that scans any
SQLite database for tables that *look like* a chat store and auto-classifies their columns —
so an investigator gets messages out of an app nobody wrote a dedicated parser for. This is the
open-source equivalent.

For every table it inspects, it scores column names against role heuristics (text / timestamp /
sender / thread). Tables that have both a text-ish and a timestamp-ish column are treated as
message tables: live rows are read and mapped, then the shared recovery engine carves deleted
rows and detects deletion gaps. Results carry ``app = "<db>:<table>"`` provenance so they're
clearly distinguished from dedicated-parser output.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Optional

from ..config import Confidence
from . import appchat

# Column-name substrings that hint at each role (first match wins per column).
_TEXT_HINTS = ("text", "body", "message", "content", "caption", "msg", "comment")
_TS_HINTS = ("timestamp", "date", "_time", "time_", "sent_at", "created", "_ts")
_SENDER_HINTS = ("sender", "from_id", "from_jid", "author", "user_id", "account")
_THREAD_HINTS = ("thread", "conversation", "chat_id", "dialog", "room", "peer")

# Tables that are never chats (avoid noise from framework/bookkeeping tables).
_SKIP_TABLES = {"android_metadata", "sqlite_sequence", "room_master_table"}

MAX_TABLES = 40
MAX_LIVE_ROWS = 5_000


def _pick(cols: list[str], hints: tuple[str, ...], taken: set[str]) -> Optional[str]:
    # Prefer an exact-ish match, then any substring match, skipping already-assigned columns.
    for c in cols:
        if c in taken:
            continue
        lc = c.lower()
        if lc in hints or any(lc == h for h in hints):
            taken.add(c)
            return c
    for c in cols:
        if c in taken:
            continue
        lc = c.lower()
        if any(h in lc for h in hints):
            taken.add(c)
            return c
    return None


def _iso(val: Any) -> Optional[str]:
    """Normalise a numeric epoch (s/ms/µs) or leave an ISO-ish string as-is."""
    from datetime import datetime, timezone

    if val is None:
        return None
    if isinstance(val, str):
        # already looks like a date?
        return val if ("-" in val and ":" in val) else None
    try:
        n = float(val)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    if n > 1e14:
        n /= 1e6
    elif n > 1e11:
        n /= 1e3
    try:
        return datetime.fromtimestamp(n, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OSError, ValueError, OverflowError):
        return None


def scan_sqlite_for_chats(
    db_path: str | Path, *, app_label: Optional[str] = None
) -> dict[str, Any]:
    """Discover chat-like tables in one SQLite DB and extract their messages (live + deleted)."""
    p = Path(db_path)
    label = app_label or p.stem
    result = {"available": False, "db": p.name, "tables": [], "messages": []}
    if not p.exists():
        return result

    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        tables = [
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ][:MAX_TABLES]
    except sqlite3.Error:
        return result

    messages: list[dict[str, Any]] = []
    table_reports: list[dict[str, Any]] = []

    for table in tables:
        if table.lower() in _SKIP_TABLES:
            continue
        try:
            cols = [r[1] for r in con.execute(f"PRAGMA table_info('{table}')")]
        except sqlite3.Error:
            continue
        taken: set[str] = set()
        text_c = _pick(cols, _TEXT_HINTS, taken)
        ts_c = _pick(cols, _TS_HINTS, taken)
        if not text_c or not ts_c:
            continue  # needs at least text + time to be a plausible chat table
        sender_c = _pick(cols, _SENDER_HINTS, taken)
        thread_c = _pick(cols, _THREAD_HINTS, taken)

        live_count = 0
        try:
            sel = ", ".join(f'"{c}"' for c in cols)
            for r in con.execute(
                f'SELECT {sel} FROM "{table}" LIMIT {MAX_LIVE_ROWS}'
            ).fetchall():
                body = r[text_c]
                if not isinstance(body, str) or len(body.strip()) < appchat.MIN_STR_LEN:
                    continue
                messages.append(
                    appchat.msg(
                        body=body.strip(),
                        sender=(
                            str(r[sender_c])
                            if sender_c and r[sender_c] is not None
                            else "<unknown>"
                        ),
                        timestamp=_iso(r[ts_c]),
                        chat_id=(
                            str(r[thread_c])
                            if thread_c and r[thread_c] is not None
                            else None
                        ),
                        confidence=Confidence.LIVE.value,
                        source_file=p.name,
                        provenance=f"live row in {label}:{table}",
                        app=f"{label}:{table}",
                    )
                )
                live_count += 1
        except sqlite3.Error:
            pass

        # Deleted rows + gaps. Carved-row column alignment is unreliable, so pick the most
        # message-like string in the row rather than trust a fixed column index.
        carved = appchat.carve_and_gaps(
            p, table, body_of=appchat.best_content, source_name=p.name
        )
        for m in carved:
            m["app"] = f"{label}:{table}"
        messages.extend(carved)

        if live_count or carved:
            table_reports.append(
                {
                    "table": table,
                    "live": live_count,
                    "recovered": len(carved),
                    "roles": {
                        "text": text_c,
                        "timestamp": ts_c,
                        "sender": sender_c,
                        "thread": thread_c,
                    },
                }
            )

    con.close()
    result["available"] = bool(table_reports)
    result["tables"] = table_reports
    result["messages"] = messages
    result["counts"] = appchat.count_by_confidence(messages)
    return result
