"""Heuristic chat-database parser for messaging apps (Telegram, Signal-plaintext-export,
and generic app SQLite stores).

Reading a real Telegram `cache4.db` fully requires root access AND deserialising Telegram's
TL-binary `data` BLOBs — out of scope for a non-root field triage. What IS achievable and
genuinely useful is a schema-driven heuristic reader: locate any table that looks like a
message store (a text-bearing column plus optional sender/timestamp columns) and surface its
live rows as Message objects. Combined with the SQLite carver (which recovers *deleted* rows
from the same databases regardless of app), this gives multi-app message coverage without
pretending we've defeated Telegram's on-disk format.

Every row parsed this way is labelled with the app inferred from the file path so the
dashboard can attribute it, and rows are marked LIVE (they came from the live table via the
sqlite engine).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from ..config import Confidence
from ..models import Message

# Column-name candidates (lowercased) we treat as each logical field.
_TEXT_COLS = ["body", "text", "message", "content", "msg", "data_text", "snippet"]
_SENDER_COLS = ["sender", "from", "author", "uid", "user_id", "from_id", "address", "sender_name", "handle"]
_TIME_COLS = ["timestamp", "date", "ts", "time", "date_sent", "created_at", "sent"]


def _pick(cols: list[str], candidates: list[str]) -> Optional[str]:
    lower = {c.lower(): c for c in cols}
    for cand in candidates:
        if cand in lower:
            return lower[cand]
    # substring match as a fallback (e.g. "message_text")
    for cand in candidates:
        for lc, orig in lower.items():
            if cand in lc:
                return orig
    return None


def _app_from_path(path: Path) -> str:
    p = str(path).lower()
    if "telegram" in p or "cache4" in p:
        return "telegram"
    if "signal" in p:
        return "signal"
    if "whatsapp" in p or "msgstore" in p:
        return "whatsapp"
    return "app-db"


def _to_iso(value) -> Optional[str]:
    """Coerce a variety of timestamp encodings to ISO-8601."""
    if value is None:
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        s = str(value)
        return s if "-" in s else None
    # Heuristic: seconds vs milliseconds since epoch.
    from datetime import datetime, timezone
    if n > 1e12:
        n //= 1000
    if n < 1e8 or n > 4e9:
        return None
    try:
        return datetime.fromtimestamp(n, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OSError):
        return None


def parse_app_db(path: str | Path, max_rows: int = 5000) -> list[Message]:
    """Heuristically extract live chat messages from an app SQLite database."""
    path = Path(path)
    app = _app_from_path(path)
    messages: list[Message] = []
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    except sqlite3.Error:
        return []

    for table in tables:
        try:
            cols = [c[1] for c in con.execute(f"PRAGMA table_info('{table}')")]
        except sqlite3.Error:
            continue
        text_col = _pick(cols, _TEXT_COLS)
        if not text_col:
            continue  # not a message-bearing table
        sender_col = _pick(cols, _SENDER_COLS)
        time_col = _pick(cols, _TIME_COLS)
        select_cols = [c for c in (text_col, sender_col, time_col) if c]
        col_sql = ", ".join('"' + c + '"' for c in select_cols)
        try:
            rows = con.execute(
                f"SELECT {col_sql} FROM '{table}' LIMIT {int(max_rows)}").fetchall()
        except sqlite3.Error:
            continue
        for r in rows:
            body = r[text_col]
            if body is None or (isinstance(body, (bytes, bytearray))):
                # Skip BLOB payloads (e.g. Telegram TL blobs) — not plaintext.
                continue
            body = str(body).strip()
            if not body:
                continue
            messages.append(Message(
                app=app,
                sender=str(r[sender_col]) if sender_col and r[sender_col] is not None else "(unknown)",
                body=body,
                timestamp=_to_iso(r[time_col]) if time_col else None,
                confidence=Confidence.LIVE,
                source_file=path.name,
                provenance=f"live table '{table}'",
            ))
    con.close()
    return messages
