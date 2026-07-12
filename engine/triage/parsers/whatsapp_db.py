"""Dedicated parser for WhatsApp's ``msgstore.db`` SQLite database.

This module provides a schema-aware, version-tolerant live-parse of the WhatsApp
message store.  It is **not** a heuristic guesser — it introspects the actual
table schema before building its query so it degrades gracefully when columns are
absent (which happens across WhatsApp versions and backup/restore states).

Schema overview (typical Android WhatsApp)
------------------------------------------
``message``   — one row per message::

    _id              INTEGER PK
    key_remote_jid   TEXT    — the chat partner / group JID
    sender_jid       TEXT    — sender's JID (populated for incoming msgs in groups;
                               NULL/empty for 1-on-1 outgoing and incoming msgs)
    status           INTEGER — delivery/read status bitfield
    timestamp        INTEGER — milliseconds since Unix epoch (UTC)
    data             TEXT    — message body text (NULL for pure media msgs)
    media_url        TEXT    — remote URL of media (if applicable)
    mime_type        TEXT    — MIME type of media (if applicable)

``wa_contacts``   — address-book enrichment::

    jid              TEXT    — matches sender_jid / key_remote_jid
    display_name     TEXT    — human-readable contact name
    is_self          INTEGER — 1 when this row is the device owner

``chat``          — per-conversation metadata::

    jid              TEXT    — matches key_remote_jid
    subject          TEXT    — group subject / display name (NULL for 1-on-1)

Older WhatsApp versions may use ``chat_list`` instead of ``chat``, or lack
``sender_jid``/``subject`` entirely.  All of this is handled gracefully.

Public API
----------
``parse_whatsapp_db(db_path) -> list[Message]``
    Connect read-only, JOIN the tables, return typed Message objects with
    ``confidence=Confidence.LIVE`` and full provenance strings.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..config import Confidence
from ..models import Message

# JID suffix for regular user accounts
_USER_JID_SUFFIX = "@s.whatsapp.net"
_GROUP_JID_SUFFIX = "@g.us"
_BROADCAST_JID = "status@broadcast"

# Column sets we need from each table (will be intersected with the live schema).
_MSG_WANT = {
    "_id", "key_remote_jid", "sender_jid", "timestamp",
    "data", "status", "media_url", "mime_type",
}
_CONTACT_WANT = {"jid", "display_name", "is_self"}
_CHAT_WANT     = {"jid", "subject"}

# Alternative table names seen in some WhatsApp builds.
_CHAT_ALIASES  = ("chat", "chat_list")
_MSG_ALIASES   = ("message", "messages")


# ---------------------------------------------------------------------------
# Schema introspection
# ---------------------------------------------------------------------------

def _table_columns(con: sqlite3.Connection, table: str) -> set[str]:
    """Return the column names present in *table*, or empty set if table absent."""
    try:
        rows = con.execute(f"PRAGMA table_info('{table}')").fetchall()
        return {r[1] for r in rows}
    except sqlite3.Error:
        return set()


def _find_table(con: sqlite3.Connection, aliases: tuple[str, ...]) -> Optional[str]:
    """Return the first alias that exists in the database, or None."""
    try:
        existing = {
            r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    except sqlite3.Error:
        return None
    for alias in aliases:
        if alias in existing:
            return alias
    return None


def _infer_schema(con: sqlite3.Connection) -> dict[str, Any]:
    """Discover which tables/columns actually exist and return a capabilities dict."""
    msg_table = _find_table(con, _MSG_ALIASES)
    chat_table = _find_table(con, _CHAT_ALIASES)
    contact_table = "wa_contacts" if _table_columns(con, "wa_contacts") else None

    msg_cols     = _table_columns(con, msg_table)     if msg_table     else set()
    chat_cols    = _table_columns(con, chat_table)    if chat_table    else set()
    contact_cols = _table_columns(con, contact_table) if contact_table else set()

    return {
        "msg_table":      msg_table,
        "chat_table":     chat_table,
        "contact_table":  contact_table,
        "msg_cols":       msg_cols     & _MSG_WANT,
        "chat_cols":      chat_cols    & _CHAT_WANT,
        "contact_cols":   contact_cols & _CONTACT_WANT,
    }


# ---------------------------------------------------------------------------
# Own-JID detection
# ---------------------------------------------------------------------------

def _detect_own_jid(con: sqlite3.Connection) -> Optional[str]:
    """Best-effort detection of the device owner's JID.

    Tries multiple strategies in order:
    1. ``my_jid`` table (present in some WhatsApp versions).
    2. ``wa_contacts`` row with ``is_self = 1``.
    3. Give up — direction will be guessed from context.
    """
    # Strategy 1: my_jid table
    try:
        row = con.execute("SELECT jid FROM my_jid LIMIT 1").fetchone()
        if row and row[0]:
            return row[0]
    except sqlite3.Error:
        pass

    # Strategy 2: wa_contacts is_self flag
    try:
        row = con.execute(
            "SELECT jid FROM wa_contacts WHERE is_self = 1 LIMIT 1"
        ).fetchone()
        if row and row[0]:
            return row[0]
    except sqlite3.Error:
        pass

    return None


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _jid_to_phone(jid: Optional[str]) -> Optional[str]:
    """Extract phone number from 'phone@s.whatsapp.net' or return None."""
    if not jid:
        return None
    if "@" in jid:
        return jid.split("@")[0]
    return jid


def _format_sender(display_name: Optional[str], jid: Optional[str]) -> str:
    """Format a human-readable sender string.

    If a display_name is available: ``"Name (phone)"`` or just ``"Name"`` if no JID.
    Otherwise: phone number or raw JID.
    """
    phone = _jid_to_phone(jid)
    if display_name and display_name.strip():
        if phone:
            return f"{display_name.strip()} ({phone})"
        return display_name.strip()
    return phone or jid or "<unknown>"


def _ms_to_iso(ms: Optional[int]) -> Optional[str]:
    """Convert milliseconds-since-epoch (UTC) to ISO-8601 string, or None."""
    if ms is None:
        return None
    try:
        dt = datetime.fromtimestamp(int(ms) / 1000.0, tz=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OSError, OverflowError, ValueError):
        return None


def _is_group_jid(jid: Optional[str]) -> bool:
    """Return True if the JID looks like a group chat."""
    return bool(jid and _GROUP_JID_SUFFIX in jid)


def _determine_direction(
    key_remote_jid: Optional[str],
    sender_jid: Optional[str],
    own_jid: Optional[str],
) -> str:
    """Determine message direction from JIDs.

    Rules:
    * Broadcast / status messages → ``"system"``.
    * Group chat:
        - ``sender_jid`` is None/empty → the device owner sent it → ``"outgoing"``
        - ``sender_jid == own_jid``                               → ``"outgoing"``
        - else                                                     → ``"incoming"``
    * 1-on-1 chat:
        - ``sender_jid`` is None/empty → typically the outgoing side → ``"outgoing"``
          (WhatsApp omits sender_jid for messages *you* sent in 1-on-1 chats)
        - ``sender_jid == own_jid``    → ``"outgoing"``
        - else                         → ``"incoming"``
    """
    if key_remote_jid == _BROADCAST_JID:
        return "system"

    no_sender = not sender_jid or sender_jid.strip() == ""
    is_own = own_jid and sender_jid == own_jid

    if no_sender or is_own:
        return "outgoing"
    return "incoming"


# ---------------------------------------------------------------------------
# Query builder
# ---------------------------------------------------------------------------

def _build_query(schema: dict[str, Any]) -> Optional[str]:
    """Build a SELECT … LEFT JOIN query tailored to the discovered schema.

    Returns None if there is no message table (nothing to parse).
    """
    msg_t = schema["msg_table"]
    if not msg_t:
        return None

    chat_t    = schema["chat_table"]
    contact_t = schema["contact_table"]
    msg_cols  = schema["msg_cols"]

    # Core columns — mandatory
    sel = [f"m._id"]
    sel.append("m.key_remote_jid" if "key_remote_jid" in msg_cols else "NULL AS key_remote_jid")
    sel.append("m.sender_jid"     if "sender_jid"     in msg_cols else "NULL AS sender_jid")
    sel.append("m.timestamp"      if "timestamp"      in msg_cols else "NULL AS timestamp")
    sel.append("m.data"           if "data"           in msg_cols else "NULL AS data")
    sel.append("m.status"         if "status"         in msg_cols else "NULL AS status")
    sel.append("m.media_url"      if "media_url"      in msg_cols else "NULL AS media_url")
    sel.append("m.mime_type"      if "mime_type"      in msg_cols else "NULL AS mime_type")

    # wa_contacts join
    if contact_t and "display_name" in schema["contact_cols"]:
        sel.append("wc.display_name AS display_name")
    else:
        sel.append("NULL AS display_name")

    # chat join — group subject
    if chat_t and "subject" in schema["chat_cols"]:
        sel.append("ch.subject AS chat_subject")
    else:
        sel.append("NULL AS chat_subject")

    query = f"SELECT {', '.join(sel)}\nFROM {msg_t} m"

    if contact_t and "jid" in schema["contact_cols"]:
        query += (
            f"\nLEFT JOIN {contact_t} wc"
            " ON wc.jid = COALESCE(NULLIF(m.sender_jid, ''), m.key_remote_jid)"
        )

    if chat_t and "jid" in schema["chat_cols"]:
        query += f"\nLEFT JOIN {chat_t} ch ON ch.jid = m.key_remote_jid"

    query += "\nORDER BY m.timestamp ASC"
    return query


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_whatsapp_db(db_path: str | Path) -> list[Message]:
    """Parse live messages from a WhatsApp ``msgstore.db`` file.

    Opens the database read-only, introspects its schema, and executes a
    LEFT-JOIN query to produce rich ``Message`` objects.

    Parameters
    ----------
    db_path:
        Path to the ``msgstore.db`` file.

    Returns
    -------
    list[Message]
        All live rows extracted, with:
        * ``app = "whatsapp"``
        * ``confidence = Confidence.LIVE``
        * ``provenance = "msgstore.db live table"``

    Returns an empty list on any error (corrupt file, wrong schema, etc.).
    """
    db_path = Path(db_path)
    messages: list[Message] = []

    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
    except sqlite3.Error:
        return []

    try:
        schema  = _infer_schema(con)
        own_jid = _detect_own_jid(con)
        query   = _build_query(schema)

        if not query:
            return []

        try:
            cur = con.execute(query)
        except sqlite3.Error:
            return []

        for row in cur:
            try:
                _id          = row["_id"]
                key_remote   = row["key_remote_jid"]
                sender_jid   = row["sender_jid"]
                timestamp_ms = row["timestamp"]
                body_text    = row["data"] or ""
                media_url    = row["media_url"]
                mime_type    = row["mime_type"]
                display_name = row["display_name"]
                chat_subject = row["chat_subject"]

                sender   = _format_sender(display_name, sender_jid or key_remote)
                direction = _determine_direction(key_remote, sender_jid, own_jid)
                ts        = _ms_to_iso(timestamp_ms)

                # Build body: prefer text; fall back to media description.
                if not body_text and media_url:
                    body_text = f"[Media: {mime_type or 'unknown'}] {media_url}"
                elif not body_text and mime_type:
                    body_text = f"[Media: {mime_type}]"

                flags: list[str] = []
                if media_url:
                    flags.append("has_media")
                if _is_group_jid(key_remote):
                    flags.append("group_message")
                    if chat_subject:
                        flags.append(f"group:{chat_subject}")

                provenance = "msgstore.db live table"
                if _id is not None:
                    provenance += f" (row {_id})"

                messages.append(Message(
                    app="whatsapp",
                    sender=sender,
                    body=body_text,
                    timestamp=ts,
                    direction=direction,
                    confidence=Confidence.LIVE,
                    source_file=db_path.name,
                    provenance=provenance,
                    flags=flags,
                ))
            except Exception:
                # Corrupt or unexpected row — skip, never crash.
                continue

    finally:
        try:
            con.close()
        except Exception:
            pass

    return messages
