"""Telegram cache4.db parser — live query + full forensic recovery.

Acquisition tiers
-----------------
* **Tier 0 (non-root, no recovery)**: pull media files from
  ``/sdcard/Android/media/org.telegram.messenger/Telegram/`` only.
  ``cache4.db`` is in app-private storage (``allowBackup=false``) and is
  *not* reachable without root.

* **Tier 2 (root)**: the pipeline copies ``cache4.db`` out of
  ``/data/data/org.telegram.messenger/files/`` via ``adb shell su`` and
  pulls it. This module then provides:

  - ``detect_table_schema()``   — generic dynamic schema probe (any table).
  - ``detect_telegram_schema()``— alias for messages table (backward compat).
  - ``recover_telegram_messages()`` — full message recovery pipeline.
  - ``recover_users_and_chats()``   — live + deleted users/chats.
  - ``extract_media_paths_from_blob()`` — heuristic path scanner from TL BLOBs.
  - ``build_conversations()``   — thread messages → conversations dict.
  - ``export_recovered_messages_json()`` — full-provenance JSON export.

No-root fallback
----------------
All ``recover_*`` functions return a standardised error dict when the DB
path does not exist, surfacing:
  "Telegram full chat history requires root. Only media from gallery is available."

Schema detection — NO HARDCODING
---------------------------------
``detect_table_schema()`` probes column names dynamically via
``PRAGMA table_info`` and classifies each column by a heuristic that
examines the lower-cased column name for semantic substrings (e.g. "id",
"name", "date", "text", "msg", "path", "type").  No column name is
assumed to exist; unrecognised columns are stored under ``extra_cols``.

Media blob parsing — NO HARDCODED TL TAG NUMBERS
-------------------------------------------------
Telegram TL-encodes media metadata.  Rather than hardcode tag numbers
(which change between builds), ``extract_media_paths_from_blob()`` uses a
length-prefixed string scanner that finds any UTF-8 string in the BLOB
that looks like a relative file path (contains "/" and ".", length 4-128).
This is provenance-tagged "media_blob_heuristic".

Conversation threading
----------------------
``build_conversations()`` takes the lists produced by
``recover_telegram_messages()`` and ``recover_users_and_chats()`` and
groups messages by chat_id, resolves sender names from the users list,
and produces a ``dict[str, ConversationDict]`` keyed by chat_id string.

Encryption
----------
``cache4.db`` is NOT encrypted by default — it is a plain SQLite file.
No decryption step is needed.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import zipfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..config import Confidence
from ..models import Message
from ..recovery.sqlite_recovery import (
    recover_deleted_rows,
    detect_rowid_gaps,
    CarvedRow,
)
from ..recovery.sqbrite import sqbrite_cross_check

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configurable paths (overridable via env-var for testing)
# ---------------------------------------------------------------------------


class TelegramPaths:
    """Configurable device-side path constants."""

    FILES_DIR: str = os.environ.get(
        "TGRAM_FILES_DIR",
        "/data/data/org.telegram.messenger/files",
    )

    @classmethod
    def db_path(cls) -> str:
        return f"{cls.FILES_DIR}/cache4.db"

    @classmethod
    def media_path(cls, relative: str) -> str:
        """Resolve a relative media path to an absolute device path."""
        return f"{cls.FILES_DIR}/{relative.lstrip('/')}"


# ---------------------------------------------------------------------------
# Minimum useful string length when extracting text from BLOBs.
# ---------------------------------------------------------------------------
MIN_BLOB_STRING_LEN = 3
_MEDIA_PATH_RE = re.compile(
    r"[a-zA-Z0-9_\-\.]+/[a-zA-Z0-9_\-\.]+(?:/[a-zA-Z0-9_\-\.]+)*"
)


# ---------------------------------------------------------------------------
# Generic table schema detection (NO HARDCODED COLUMN NAMES)
# ---------------------------------------------------------------------------

# Heuristic: classify a column by substrings in its lower-cased name.
# Each entry is (canonical_role, [substrings_that_trigger_this_role]).
# First match wins.  A column can trigger at most one role.
_ROLE_HINTS: list[tuple[str, list[str]]] = [
    (
        "id_col",
        [
            "_id",
            "rowid",
            "^id$",
            "uid",
            "peer_id",
            "from_id",
            "chat_id",
            "dialog_id",
            "user_id",
        ],
    ),
    ("name_col", ["name", "title", "first_name", "last_name", "display"]),
    ("text_col", ["message", "body", "text", "content", "msg", "caption"]),
    ("date_col", ["date", "time", "ts", "timestamp", "sent", "recv"]),
    ("path_col", ["path", "file", "media", "thumb", "url", "uri"]),
    ("type_col", ["type", "kind", "subtype", "media_type"]),
    ("out_col", ["^out$", "is_out", "outgoing", "sent_by_me"]),
    ("blob_col", ["data", "blob", "tl_data", "raw", "payload"]),
    ("phone_col", ["phone", "number", "tel"]),
    ("username_col", ["username", "handle", "login"]),
]


@dataclass
class TableSchema:
    """Result of dynamic schema detection for any SQLite table."""

    table_name: str = ""
    raw_columns: list[str] = field(default_factory=list)
    col_count: int = 0
    # canonical role → actual column name (first match per role)
    mapping: dict[str, Optional[str]] = field(default_factory=dict)
    # columns that didn't match any role
    extra_cols: list[str] = field(default_factory=list)
    usable: bool = False
    version_label: str = "unknown"

    # Convenience accessors
    def get(self, role: str) -> Optional[str]:
        return self.mapping.get(role)

    def to_schema_hint(self) -> dict[str, Any]:
        return {"col_count": self.col_count, "columns": self.raw_columns}

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def detect_table_schema(db_path: str | Path, table_name: str) -> TableSchema:
    """Dynamically detect and classify the schema of any SQLite table.

    No column names are hardcoded — classification is driven entirely by
    substring heuristics applied to the lower-cased column name.

    Parameters
    ----------
    db_path:
        Path to the SQLite database.
    table_name:
        Name of the table to inspect (e.g. ``"messages"``, ``"users"``,
        ``"chats"``).  A case-insensitive match is attempted against the
        actual table list if the exact name is not found.

    Returns
    -------
    A :class:`TableSchema` with ``usable=False`` if the table is absent or
    the file doesn't exist.
    """
    db_path = Path(db_path)
    schema = TableSchema(table_name=table_name)

    if not db_path.exists():
        return schema

    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

        # Resolve actual table name (case-insensitive).
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        existing = {r[0].lower(): r[0] for r in rows}
        real_name = existing.get(table_name.lower())
        if real_name is None:
            con.close()
            return schema
        schema.table_name = real_name

        cols_info = con.execute(f"PRAGMA table_info('{real_name}')").fetchall()
        con.close()

        raw_cols = [r[1] for r in cols_info]
        schema.raw_columns = raw_cols
        schema.col_count = len(raw_cols)

        mapping: dict[str, Optional[str]] = {role: None for role, _ in _ROLE_HINTS}
        claimed: set[str] = set()
        extra: list[str] = []

        for col in raw_cols:
            col_lower = col.lower()
            matched = False
            for role, hints in _ROLE_HINTS:
                if mapping[role] is not None:
                    continue  # role already filled
                for hint in hints:
                    # Support anchored patterns like "^id$"
                    if hint.startswith("^") and hint.endswith("$"):
                        if col_lower == hint[1:-1]:
                            mapping[role] = col
                            claimed.add(col)
                            matched = True
                            break
                    elif hint in col_lower:
                        mapping[role] = col
                        claimed.add(col)
                        matched = True
                        break
                if matched:
                    break
            if not matched:
                extra.append(col)

        schema.mapping = mapping
        schema.extra_cols = extra

        # Mark usable if we have at least an id or name/text column.
        schema.usable = bool(
            mapping.get("id_col")
            or mapping.get("name_col")
            or mapping.get("text_col")
            or mapping.get("path_col")
        )

        # Label for provenance.
        schema.version_label = (
            f"dynamic/{real_name}({','.join(raw_cols[:4])}…)"
            if len(raw_cols) > 4
            else f"dynamic/{real_name}({','.join(raw_cols)})"
        )

    except sqlite3.Error as exc:
        log.warning("detect_table_schema(%s, %s): %s", db_path.name, table_name, exc)

    return schema


# ---------------------------------------------------------------------------
# Backward-compatible alias — detect_telegram_schema now delegates here
# ---------------------------------------------------------------------------


@dataclass
class TelegramSchema:
    """Thin wrapper keeping the old API alive for callers that use it directly."""

    raw_columns: list[str] = field(default_factory=list)
    mapping: dict[str, Optional[str]] = field(default_factory=dict)
    col_count: int = 0
    usable: bool = False
    version_label: str = "unknown"

    def body_col(self) -> Optional[str]:
        return self.mapping.get("body") or self.mapping.get("text_col")

    def date_col(self) -> Optional[str]:
        return self.mapping.get("date") or self.mapping.get("date_col")

    def from_id_col(self) -> Optional[str]:
        return self.mapping.get("from_id") or self.mapping.get("id_col")

    def data_blob_col(self) -> Optional[str]:
        return self.mapping.get("data_blob") or self.mapping.get("blob_col")

    def to_schema_hint(self) -> dict[str, Any]:
        return {"col_count": self.col_count, "columns": self.raw_columns}


def detect_telegram_schema(path: str | Path) -> TelegramSchema:
    """Backward-compatible wrapper — delegates to detect_table_schema."""
    path = Path(path)
    ts = detect_table_schema(path, "messages")

    # Map generic roles back onto the legacy TelegramSchema field names.
    old_map: dict[str, Optional[str]] = {}
    m = ts.mapping
    old_map["body"] = m.get("text_col")
    old_map["date"] = m.get("date_col")
    old_map["from_id"] = m.get("id_col")  # best guess for sender ID
    old_map["data_blob"] = m.get("blob_col")

    # Also preserve any raw column names the old code expected.
    for col in ts.raw_columns:
        cl = col.lower()
        if "message" == cl or "body" == cl:
            old_map["body"] = col
        if "date" == cl:
            old_map["date"] = col
        if "from_id" == cl:
            old_map["from_id"] = col
        if "data" == cl:
            old_map["data_blob"] = col

    s = TelegramSchema(
        raw_columns=ts.raw_columns,
        mapping=old_map,
        col_count=ts.col_count,
        usable=ts.usable,
        version_label=ts.version_label,
    )
    return s


# ---------------------------------------------------------------------------
# User & Chat recovery
# ---------------------------------------------------------------------------


def _recover_table_rows(
    db_path: Path,
    table_name: str,
    schema: TableSchema,
) -> list[dict[str, Any]]:
    """Generic live + deleted row recovery for a given table."""
    results: list[dict[str, Any]] = []
    real_table = schema.table_name

    if not real_table or not schema.usable:
        return results

    # --- Live rows ---
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        rows = con.execute(f"SELECT * FROM '{real_table}' LIMIT 10000").fetchall()
        con.close()

        for row in rows:
            rec = _row_to_generic_record(
                row, schema, Confidence.LIVE, db_path.name, "live_query"
            )
            if rec:
                results.append(rec)
    except sqlite3.Error as exc:
        log.warning("_recover_table_rows live(%s): %s", real_table, exc)

    # --- Deleted rows (recovery engine) ---
    try:
        carved: list[CarvedRow] = recover_deleted_rows(
            db_path,
            table=real_table,
            schema_hint=schema.to_schema_hint(),
        )
        for cr in carved:
            rec = _carved_to_generic_record(cr, schema, db_path.name)
            if rec:
                results.append(rec)
    except Exception as exc:
        log.warning("_recover_table_rows carved(%s): %s", real_table, exc)

    return results


def _row_to_generic_record(
    row: sqlite3.Row,
    schema: TableSchema,
    confidence: Confidence,
    source_file: str,
    carve_method: str,
) -> Optional[dict[str, Any]]:
    """Convert a live sqlite3.Row to a generic provenance dict."""
    rec: dict[str, Any] = {
        "confidence": confidence.value,
        "source_file": source_file,
        "carve_method": carve_method,
        "provenance": f"live table '{schema.table_name}'",
        "warnings": [],
    }

    for col in schema.raw_columns:
        try:
            val = row[col]
            if isinstance(val, (bytes, bytearray)):
                val = {"__blob__": True, "len": len(val)}
            rec[col] = val
        except (KeyError, IndexError):
            pass

    # Synthesise a human-friendly "name" field.
    name_col = schema.get("name_col")
    if name_col and rec.get(name_col):
        rec["_name"] = str(rec[name_col])
    else:
        # Try to join first_name + last_name if both exist.
        parts = []
        for col in schema.raw_columns:
            if "first_name" in col.lower() or "last_name" in col.lower():
                v = rec.get(col)
                if v and not isinstance(v, dict):
                    parts.append(str(v))
        if parts:
            rec["_name"] = " ".join(parts)

    id_col = schema.get("id_col")
    if id_col and rec.get(id_col) is not None:
        rec["_id"] = str(rec[id_col])

    return rec if rec.get("_id") or rec.get("_name") else None


def _carved_to_generic_record(
    cr: CarvedRow,
    schema: TableSchema,
    source_file: str,
) -> Optional[dict[str, Any]]:
    """Convert a CarvedRow to a generic provenance dict."""
    if not cr.values:
        return None

    rec: dict[str, Any] = {
        "confidence": cr.confidence.value,
        "source_file": cr.source_file or source_file,
        "page": cr.page,
        "offset": cr.offset,
        "carve_method": "freelist" if "freelist" in (cr.provenance or "") else "carve",
        "provenance": cr.provenance or "",
        "warnings": list(cr.warnings),
    }

    for i, col in enumerate(schema.raw_columns):
        if i < len(cr.values):
            val = cr.values[i]
            if isinstance(val, (bytes, bytearray)):
                val = {"__blob__": True, "len": len(val)}
            rec[col] = val

    # Synthesise display fields.
    name_col = schema.get("name_col")
    if name_col and rec.get(name_col) and not isinstance(rec.get(name_col), dict):
        rec["_name"] = str(rec[name_col])
    id_col = schema.get("id_col")
    if id_col and rec.get(id_col) is not None:
        rec["_id"] = str(rec[id_col])

    return rec


def recover_users_and_chats(db_path: str | Path) -> dict[str, Any]:
    """Recover live + deleted rows from the ``users`` and ``chats`` tables.

    Uses the same confidence framework as :func:`recover_telegram_messages`.

    Parameters
    ----------
    db_path:
        Local path to ``cache4.db``.

    Returns
    -------
    ``{"users": [...], "chats": [...], "schema_users": {...}, "schema_chats": {...}}``
    or a standardised error dict if the file is absent.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        return {
            "available": False,
            "error": "Telegram full chat history requires root. Only media from gallery is available.",
            "users": [],
            "chats": [],
        }

    users_schema = detect_table_schema(db_path, "users")
    chats_schema = detect_table_schema(db_path, "chats")

    users = (
        _recover_table_rows(db_path, "users", users_schema)
        if users_schema.usable
        else []
    )
    chats = (
        _recover_table_rows(db_path, "chats", chats_schema)
        if chats_schema.usable
        else []
    )

    log.info(
        "recover_users_and_chats: users=%d chats=%d",
        len(users),
        len(chats),
    )

    return {
        "available": True,
        "error": None,
        "users": users,
        "chats": chats,
        "schema_users": users_schema.to_dict(),
        "schema_chats": chats_schema.to_dict(),
        "counts": {
            "users_live": sum(
                1 for u in users if u.get("confidence") == Confidence.LIVE.value
            ),
            "users_recovered": sum(
                1
                for u in users
                if u.get("confidence") == Confidence.RECOVERED_VERIFIED.value
            ),
            "users_carved": sum(
                1
                for u in users
                if u.get("confidence") == Confidence.CARVED_PARTIAL.value
            ),
            "chats_live": sum(
                1 for c in chats if c.get("confidence") == Confidence.LIVE.value
            ),
            "chats_recovered": sum(
                1
                for c in chats
                if c.get("confidence") == Confidence.RECOVERED_VERIFIED.value
            ),
            "chats_carved": sum(
                1
                for c in chats
                if c.get("confidence") == Confidence.CARVED_PARTIAL.value
            ),
        },
    }


# ---------------------------------------------------------------------------
# Media blob path extractor (heuristic — no hardcoded TL tag numbers)
# ---------------------------------------------------------------------------


def extract_media_paths_from_blob(blob: bytes | bytearray | None) -> list[str]:
    """Extract candidate local file paths from a Telegram TL-encoded BLOB.

    Strategy
    --------
    Telegram serialises media metadata using TLite encoding.  Rather than
    hardcoding TL constructor IDs (which change across builds), we scan the
    BLOB for length-prefixed UTF-8 strings (using the TL ``string`` type
    encoding: first byte = length if < 254, else 3-byte LE length) and then
    filter for strings that look like relative file paths.

    A candidate path must:
    * be 4–200 characters long
    * contain at least one "/" and one "."
    * consist only of characters valid in Android file paths
    * NOT start with "http" (those are remote URLs, not local paths)

    Returns
    -------
    List of candidate relative paths (e.g. ``["4/1.jpg", "cache/1234.thumb"]``).
    Tagged ``"media_blob_heuristic"`` in provenance — not guaranteed correct.
    """
    if not blob:
        return []

    data = bytes(blob)
    n = len(data)
    paths: list[str] = []
    i = 0

    while i < n:
        b = data[i]

        # TL string encoding: length byte < 254 means inline length.
        if b < 254 and b >= 4:
            length = b
            i += 1
            if i + length > n:
                i += 1
                continue
            candidate = data[i : i + length]
            i += length
            # Align to 4-byte boundary (TL padding).
            pad = (4 - ((length + 1) % 4)) % 4
            i += pad
        elif b == 254:
            # 3-byte LE length follows.
            if i + 4 > n:
                break
            length = int.from_bytes(data[i + 1 : i + 4], "little")
            i += 4
            if length < 4 or i + length > n:
                i += 1
                continue
            candidate = data[i : i + length]
            i += length
            pad = (4 - (length % 4)) % 4
            i += pad
        else:
            i += 1
            continue

        # Decode and validate.
        try:
            s = candidate.decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            continue

        # Must look like a relative path.
        if (
            4 <= len(s) <= 200
            and "/" in s
            and "." in s
            and not s.startswith("http")
            and _MEDIA_PATH_RE.match(s)
        ):
            paths.append(s)

    # Fallback: raw regex scan over the whole BLOB interpreted as latin-1.
    if not paths:
        try:
            text = data.decode("latin-1", errors="replace")
            for m in _MEDIA_PATH_RE.finditer(text):
                s = m.group(0)
                if 4 <= len(s) <= 200 and "." in s and not s.startswith("http"):
                    paths.append(s)
        except Exception:
            pass

    # Deduplicate while preserving order.
    seen: set[str] = set()
    unique: list[str] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


# ---------------------------------------------------------------------------
# Conversation threading
# ---------------------------------------------------------------------------

# Type aliases (plain dicts — no dataclass overhead needed here).
ConversationDict = dict[str, Any]
ConversationsMap = dict[str, ConversationDict]


def build_conversations(
    messages: list[dict[str, Any]],
    users: list[dict[str, Any]],
    chats: list[dict[str, Any]],
) -> ConversationsMap:
    """Group messages into conversation threads.

    Parameters
    ----------
    messages:
        List of message dicts from :func:`recover_telegram_messages` (the
        ``"messages"`` key of its return value).
    users:
        List of user dicts from :func:`recover_users_and_chats`.
    chats:
        List of chat dicts from :func:`recover_users_and_chats`.

    Returns
    -------
    A ``dict`` keyed by ``chat_id`` (as string).  Each value has::

        {
            "chat_id": str,
            "title": str,                    # from chats, or "<unknown chat>"
            "participants": [...],           # users referenced in this chat
            "last_message_ts": str | None,
            "message_count": int,
            "messages": [
                {
                    "body": str,
                    "sender_name": str,      # resolved from users
                    "sender_id": str,
                    "timestamp": str | None,
                    "confidence": str,
                    "media_artifact_id": str | None,
                    "carve_method": str,
                    "provenance": str,
                }
            ]
        }

    Messages with no ``chat_id`` are grouped under ``"__ungrouped__"``.

    Design note
    -----------
    Sender name resolution is done by building an id→name index from the
    users list.  The column used as the user ID is determined dynamically
    from the schema (the ``_id`` synthesised field).  This avoids any
    hardcoded column name assumption.
    """
    # Build user id → display name index.
    user_index: dict[str, str] = {}
    for u in users:
        uid = str(u.get("_id") or u.get("id") or "")
        name = u.get("_name") or uid or "<unknown user>"
        if uid:
            user_index[uid] = str(name)

    # Build chat id → title index.
    chat_index: dict[str, str] = {}
    for c in chats:
        cid = str(c.get("_id") or c.get("id") or "")
        title = c.get("_name") or c.get("title") or cid or "<unknown chat>"
        if cid:
            chat_index[cid] = str(title)

    conversations: ConversationsMap = {}

    for msg in messages:
        # Determine chat_id — stored as "chat_id" or inferred from sender.
        chat_id = str(msg.get("chat_id") or msg.get("peer_id") or "__ungrouped__")

        if chat_id not in conversations:
            conversations[chat_id] = {
                "chat_id": chat_id,
                "title": chat_index.get(chat_id, f"Chat {chat_id}"),
                "participants": [],  # filled below
                "_participant_ids": set(),  # temporary, removed later
                "last_message_ts": None,
                "message_count": 0,
                "messages": [],
            }

        conv = conversations[chat_id]

        sender_id = str(msg.get("sender") or msg.get("from_id") or "<unknown>")
        sender_name = user_index.get(sender_id, sender_id)

        ts = msg.get("timestamp")
        if ts and (conv["last_message_ts"] is None or ts > conv["last_message_ts"]):
            conv["last_message_ts"] = ts

        # Track unique participants.
        if sender_id and sender_id not in conv["_participant_ids"]:
            conv["_participant_ids"].add(sender_id)
            conv["participants"].append(
                {
                    "id": sender_id,
                    "name": sender_name,
                    "confidence": user_index.get(sender_id + "__conf", "unknown"),
                }
            )

        conv["messages"].append(
            {
                "body": msg.get("body", ""),
                "sender_name": sender_name,
                "sender_id": sender_id,
                "timestamp": ts,
                "confidence": msg.get("confidence", Confidence.LIVE.value),
                "media_artifact_id": msg.get("media_artifact_id"),
                "carve_method": msg.get("carve_method", ""),
                "provenance": msg.get("provenance", ""),
            }
        )
        conv["message_count"] += 1

    # Sort messages within each conversation by timestamp.
    for conv in conversations.values():
        conv["messages"].sort(key=lambda m: m.get("timestamp") or "0000")
        conv.pop("_participant_ids", None)  # remove internal tracking set

    return conversations


# ---------------------------------------------------------------------------
# BLOB string extractor (TL-encoded content fallback for message bodies)
# ---------------------------------------------------------------------------


def _extract_strings_from_blob(blob: bytes) -> str:
    """Extract printable UTF-8 strings from a Telegram TL-encoded BLOB (body fallback)."""
    if not blob:
        return ""
    strings: list[str] = []
    run = bytearray()

    def _flush() -> None:
        if len(run) < MIN_BLOB_STRING_LEN:
            return
        try:
            text = run.decode("utf-8")
            if sum(1 for c in text if c.isprintable()) >= len(text) * 0.8:
                strings.append(text.strip())
        except UnicodeDecodeError:
            pass
        run.clear()

    for b in blob:
        is_text = b in (0x09, 0x0A, 0x0D) or 0x20 <= b <= 0x7E or b >= 0x80
        if is_text:
            run.append(b)
        else:
            _flush()
    _flush()
    return " ".join(strings)


# ---------------------------------------------------------------------------
# Row-to-message mapping helpers (for recover_telegram_messages)
# ---------------------------------------------------------------------------


def _epoch_to_iso(val: Any) -> Optional[str]:
    if val is None:
        return None
    try:
        n = int(val)
        if n <= 0:
            return None
        if n > 1e12:
            n //= 1000
        return datetime.fromtimestamp(n, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, TypeError, OSError):
        return None


def _extract_body_from_row(row: sqlite3.Row, schema: TelegramSchema) -> str:
    body_col = schema.body_col()
    if body_col:
        try:
            val = row[body_col]
            if val:
                return str(val).strip()
        except (KeyError, IndexError):
            pass
    blob_col = schema.data_blob_col()
    if blob_col:
        try:
            blob = row[blob_col]
            if blob and isinstance(blob, (bytes, bytearray)):
                text = _extract_strings_from_blob(bytes(blob))
                if text:
                    return text
        except (KeyError, IndexError):
            pass
    return ""


def _map_carved_row_to_body(values: list[Any], schema: TelegramSchema) -> str:
    raw_cols = schema.raw_columns
    for col_name in (schema.body_col(), schema.data_blob_col()):
        if col_name is None:
            continue
        try:
            idx = raw_cols.index(col_name)
            if idx < len(values):
                val = values[idx]
                if isinstance(val, str) and val.strip():
                    return val.strip()
                if isinstance(val, (bytes, bytearray)):
                    text = _extract_strings_from_blob(bytes(val))
                    if text:
                        return text
        except ValueError:
            pass
    parts = [
        str(v).strip()
        for v in values
        if isinstance(v, str) and len(str(v).strip()) >= MIN_BLOB_STRING_LEN
    ]
    return " ".join(parts)


def _map_carved_row_to_timestamp(
    values: list[Any], schema: TelegramSchema
) -> Optional[str]:
    date_col = schema.date_col()
    if date_col and date_col in schema.raw_columns:
        try:
            idx = schema.raw_columns.index(date_col)
            if idx < len(values):
                return _epoch_to_iso(values[idx])
        except ValueError:
            pass
    for v in values:
        if isinstance(v, int) and 1_000_000_000 <= v <= 9_999_999_999:
            return _epoch_to_iso(v)
        if isinstance(v, int) and 1_000_000_000_000 <= v <= 9_999_999_999_999:
            return _epoch_to_iso(v)
    return None


def _map_carved_row_to_sender(values: list[Any], schema: TelegramSchema) -> str:
    from_col = schema.from_id_col()
    if from_col and from_col in schema.raw_columns:
        try:
            idx = schema.raw_columns.index(from_col)
            if idx < len(values) and values[idx] is not None:
                return str(values[idx])
        except ValueError:
            pass
    return "<recovered>"


def _find_messages_table(db_path: Path) -> Optional[str]:
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        con.close()
        names = {r[0].lower(): r[0] for r in rows}
        for candidate in ("messages", "messages_v2", "message", "msgs"):
            if candidate in names:
                return names[candidate]
    except sqlite3.Error:
        pass
    return None


def _read_live_telegram_rows(
    db_path: Path,
    schema: TelegramSchema,
    max_rows: int,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    msg_table = _find_messages_table(db_path)
    if not msg_table:
        return results
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            f"SELECT * FROM '{msg_table}' LIMIT {int(max_rows)}"
        ).fetchall()
        con.close()
        for row in rows:
            body = _extract_body_from_row(row, schema)
            if not body:
                continue
            ts = None
            date_col = schema.date_col()
            if date_col:
                try:
                    ts = _epoch_to_iso(row[date_col])
                except (KeyError, IndexError):
                    pass
            sender = "<unknown>"
            from_col = schema.from_id_col()
            if from_col:
                try:
                    v = row[from_col]
                    if v is not None:
                        sender = str(v)
                except (KeyError, IndexError):
                    pass
            # Also capture chat_id for conversation threading.
            chat_id = None
            for candidate in ("peer_id", "chat_id", "dialog_id"):
                try:
                    chat_id = row[candidate]
                    if chat_id is not None:
                        chat_id = str(chat_id)
                        break
                except (KeyError, IndexError):
                    pass
            results.append(
                {
                    "body": body,
                    "sender": sender,
                    "timestamp": ts,
                    "chat_id": chat_id,
                    "confidence": Confidence.LIVE.value,
                    "source_file": db_path.name,
                    "page": None,
                    "offset": None,
                    "carve_method": "live_query",
                    "provenance": f"live table '{msg_table}'",
                    "warnings": [],
                    "media_artifact_id": None,
                }
            )
    except sqlite3.Error as exc:
        log.warning("_read_live_telegram_rows: %s", exc)
    return results


def _carved_row_to_dict(
    row: CarvedRow,
    body: str,
    schema: TelegramSchema,
    source_name: str,
) -> dict[str, Any]:
    ts = _map_carved_row_to_timestamp(row.values, schema)
    sender = _map_carved_row_to_sender(row.values, schema)
    prov = row.provenance or ""
    if "freelist" in prov:
        carve_method = "freelist"
    elif "wal" in prov:
        carve_method = "wal"
    elif "freeblock" in prov:
        carve_method = "freeblock"
    elif "unallocated" in prov:
        carve_method = "unallocated"
    else:
        carve_method = "carve"
    return {
        "body": body,
        "sender": sender,
        "timestamp": ts,
        "chat_id": None,
        "confidence": row.confidence.value,
        "source_file": row.source_file or source_name,
        "page": row.page,
        "offset": row.offset,
        "carve_method": carve_method,
        "provenance": prov,
        "warnings": list(row.warnings),
        "media_artifact_id": None,
    }


# ---------------------------------------------------------------------------
# Main public recovery API
# ---------------------------------------------------------------------------


def recover_telegram_messages(
    db_path: str | Path,
    max_live_rows: int = 10_000,
) -> dict[str, Any]:
    """Recover all Telegram messages from a locally-obtained ``cache4.db``.

    This is the primary entry point for **Tier-2 (root) recovery**.

    Returns
    -------
    A result dict with keys: ``available``, ``error``, ``schema``,
    ``messages``, ``counts``.  See module docstring for full shape.
    """
    db_path = Path(db_path)
    _zero_counts: dict[str, int] = {
        "live": 0,
        "recovered_verified": 0,
        "carved_partial": 0,
        "deletion_detected": 0,
        "total": 0,
    }

    if not db_path.exists():
        log.info("cache4.db not found at %s — root required", db_path)
        return {
            "available": False,
            "error": (
                "Telegram full chat history requires root. "
                "Only media from gallery is available."
            ),
            "schema": {},
            "messages": [],
            "counts": dict(_zero_counts),
        }

    schema = detect_telegram_schema(db_path)
    log.info(
        "Telegram schema: %s, %d cols, usable=%s",
        schema.version_label,
        schema.col_count,
        schema.usable,
    )

    messages: list[dict[str, Any]] = []

    # Live rows.
    live_rows = _read_live_telegram_rows(db_path, schema, max_live_rows)
    messages.extend(live_rows)

    # Recovery engine.
    msg_table = _find_messages_table(db_path)
    schema_hint = schema.to_schema_hint() if schema.usable else {}
    try:
        carved = recover_deleted_rows(
            db_path,
            table=msg_table,
            schema_hint=schema_hint if schema_hint else None,
        )
    except Exception as exc:
        log.warning("recover_deleted_rows: %s", exc)
        carved = []

    for row in carved:
        body = _map_carved_row_to_body(row.values, schema)
        if not body:
            continue
        messages.append(_carved_row_to_dict(row, body, schema, db_path.name))

    # sqbrite secondary pass.
    try:
        extra = sqbrite_cross_check(db_path, primary_rows=carved)
    except Exception as exc:
        log.warning("sqbrite: %s", exc)
        extra = []

    for sq_row in extra:
        body = " ".join(
            str(v).strip()
            for v in sq_row.values
            if isinstance(v, str) and len(str(v).strip()) >= MIN_BLOB_STRING_LEN
        )
        if not body:
            continue
        messages.append(
            {
                "body": body,
                "sender": "<recovered>",
                "timestamp": None,
                "chat_id": None,
                "confidence": Confidence.CARVED_PARTIAL.value,
                "source_file": sq_row.source_file,
                "page": None,
                "offset": sq_row.offset,
                "carve_method": "sqbrite",
                "provenance": sq_row.provenance,
                "warnings": list(sq_row.warnings),
                "media_artifact_id": None,
            }
        )

    # Rowid gap detection.
    if msg_table:
        try:
            gaps = detect_rowid_gaps(db_path, msg_table)
        except Exception as exc:
            log.warning("detect_rowid_gaps: %s", exc)
            gaps = []
        for gap in gaps:
            messages.append(
                {
                    "body": "",
                    "sender": "<gap>",
                    "timestamp": None,
                    "chat_id": None,
                    "confidence": Confidence.DELETION_DETECTED.value,
                    "source_file": db_path.name,
                    "page": None,
                    "offset": None,
                    "carve_method": "gap_analysis",
                    "provenance": (
                        f"rowid gap after {gap['after_rowid']} — "
                        f"{gap['missing']} row(s) deleted, no content recoverable"
                    ),
                    "warnings": [
                        "Rowid gap proves deletion occurred; no message content "
                        "was recovered. Content may have been overwritten."
                    ],
                    "gap_detail": gap,
                    "media_artifact_id": None,
                }
            )

    # Counts.
    counts: dict[str, int] = dict(_zero_counts)
    for msg in messages:
        conf = msg.get("confidence", "")
        if conf == Confidence.LIVE.value:
            counts["live"] += 1
        elif conf == Confidence.RECOVERED_VERIFIED.value:
            counts["recovered_verified"] += 1
        elif conf == Confidence.CARVED_PARTIAL.value:
            counts["carved_partial"] += 1
        elif conf == Confidence.DELETION_DETECTED.value:
            counts["deletion_detected"] += 1
    counts["total"] = len(messages)

    return {
        "available": True,
        "error": None,
        "schema": {
            "raw_columns": schema.raw_columns,
            "mapping": schema.mapping,
            "col_count": schema.col_count,
            "version_label": schema.version_label,
            "usable": schema.usable,
        },
        "messages": messages,
        "counts": counts,
    }


def export_recovered_messages_json(
    result: dict[str, Any],
    output_path: str | Path,
) -> Path:
    """Serialise a :func:`recover_telegram_messages` result dict to JSON."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    export = {
        "tool": "eRakshak Android Triage — Telegram Recovery",
        "schema_version": result.get("schema", {}).get("version_label", "unknown"),
        "available": result.get("available", False),
        "error": result.get("error"),
        "counts": result.get("counts", {}),
        "schema": result.get("schema", {}),
        "messages": result.get("messages", []),
    }
    output_path.write_text(
        json.dumps(export, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    log.info("Telegram recovery exported to %s", output_path)
    return output_path


# ---------------------------------------------------------------------------
# Non-root acquisition path: Telegram Desktop "Export Telegram data" (JSON)
# ---------------------------------------------------------------------------
# This is data the examiner *obtained from the user/a synced PC*, not a device pull —
# useful when root is unavailable and the suspect (or a court order) has produced an
# export. Supports both the full-account export (top-level "chats": {"list": [...]})
# and a single-chat "Export chat history" (a JSON that IS one chat object). Media
# referenced by the export is not ingested here — only text, with a placeholder marking
# where media was — so this stays honest about what it actually recovered.

_EXPORT_ZERO_COUNTS: dict[str, int] = {
    "live": 0,
    "recovered_verified": 0,
    "carved_partial": 0,
    "deletion_detected": 0,
    "total": 0,
}


def _export_unavailable(reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "error": reason,
        "app": "telegram",
        "messages": [],
        "users": [],
        "chats": [],
        "counts": dict(_EXPORT_ZERO_COUNTS),
    }


def _export_message_text(text: Any) -> str:
    """Flatten a Desktop-export ``text`` field (plain string or entity-run array)."""
    if isinstance(text, str):
        return text
    if isinstance(text, list):
        parts: list[str] = []
        for piece in text:
            if isinstance(piece, str):
                parts.append(piece)
            elif isinstance(piece, dict):
                parts.append(str(piece.get("text", "")))
        return "".join(parts)
    return ""


def _export_clean_sender_id(raw: str) -> str:
    """Strip the Desktop export's ``user``/``channel`` prefix from a from_id."""
    m = re.match(r"^(?:user|channel)(\d+)$", raw)
    return m.group(1) if m else raw


def _export_timestamp(m: dict[str, Any]) -> Optional[str]:
    ts = _epoch_to_iso(m.get("date_unixtime"))
    if ts:
        return ts
    raw = m.get("date")
    if isinstance(raw, str) and raw:
        try:
            datetime.fromisoformat(raw)
            return raw
        except ValueError:
            return None
    return None


def _export_find_document(path: Path) -> Optional[dict[str, Any]]:
    """Locate and parse the export's ``result.json`` from a file, dir, or zip."""

    def _load(data: bytes) -> dict[str, Any]:
        return json.loads(data.decode("utf-8-sig"))

    if path.is_file() and path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.lower().endswith("result.json")]
            if not names:
                names = [n for n in z.namelist() if n.lower().endswith(".json")]
            if not names:
                return None
            names.sort(key=lambda n: n.count("/"))
            return _load(z.read(names[0]))
    if path.is_file():
        return _load(path.read_bytes())
    if path.is_dir():
        hits = list(path.rglob("result.json")) or list(path.rglob("*.json"))
        if not hits:
            return None
        hits.sort(key=lambda h: len(h.parts))
        return _load(hits[0].read_bytes())
    return None


def parse_telegram_export(path: str | Path) -> dict[str, Any]:
    """Parse a Telegram Desktop "Export Telegram data" JSON (or a ZIP/dir containing it).

    Non-root acquisition path — mirrors :func:`parsers.instagram.parse_instagram_export`
    and :func:`parsers.snapchat.parse_snapchat_export`. All messages are reported as
    ``Confidence.LIVE``: this is Telegram's own client view of the conversation at export
    time, not a forensic recovery pass over ``cache4.db``.

    Returns
    -------
    ``{"available", "error", "app", "messages", "users", "chats", "counts"}`` — the
    ``messages``/``users``/``chats`` triple feeds :func:`build_conversations` directly,
    the same as the Tier-2 root-recovery path.
    """
    p = Path(path)
    try:
        doc = _export_find_document(p)
    except Exception as exc:
        return _export_unavailable(f"export parse error: {exc}")
    if not doc:
        return _export_unavailable("no result.json found in export")

    if isinstance(doc.get("chats"), dict) and isinstance(doc["chats"].get("list"), list):
        chat_list = doc["chats"]["list"]
    elif isinstance(doc.get("messages"), list):
        chat_list = [doc]  # single-chat "Export chat history"
    else:
        return _export_unavailable("export JSON has neither chats.list nor messages")

    messages: list[dict[str, Any]] = []
    chats_out: list[dict[str, Any]] = []
    users_idx: dict[str, str] = {}

    for i, chat in enumerate(chat_list):
        if not isinstance(chat, dict):
            continue
        chat_id = str(chat.get("id") if chat.get("id") is not None else f"export_chat_{i}")
        chat_name = str(chat.get("name") or f"Chat {chat_id}")
        chats_out.append(
            {"_id": chat_id, "_name": chat_name, "confidence": Confidence.LIVE.value}
        )
        for m in chat.get("messages", []):
            if not isinstance(m, dict) or m.get("type") == "service":
                continue  # service messages (joins/pins/calls) carry no chat content
            body = _export_message_text(m.get("text"))
            if not body:
                if m.get("photo"):
                    body = "[photo]"
                elif m.get("file"):
                    body = f"[file] {Path(str(m['file'])).name}"
                elif m.get("media_type"):
                    body = f"[{m['media_type']}]"
            sender_raw = str(m.get("from_id") or m.get("from") or "<unknown>")
            sender_id = _export_clean_sender_id(sender_raw)
            sender_name = str(m.get("from") or sender_id)
            users_idx.setdefault(sender_id, sender_name)
            messages.append(
                {
                    "body": body,
                    "sender": sender_id,
                    "timestamp": _export_timestamp(m),
                    "chat_id": chat_id,
                    "confidence": Confidence.LIVE.value,
                    "source_file": "telegram_desktop_export",
                    "page": None,
                    "offset": None,
                    "carve_method": "",
                    "provenance": "Telegram Desktop data export (user-supplied, not a device pull)",
                    "warnings": [],
                    "media_artifact_id": None,
                }
            )

    if not messages and not chats_out:
        return _export_unavailable("export contained no chats or messages")

    users_out = [
        {"_id": uid, "_name": name, "confidence": Confidence.LIVE.value}
        for uid, name in users_idx.items()
    ]
    counts = dict(_EXPORT_ZERO_COUNTS)
    counts["live"] = len(messages)
    counts["total"] = len(messages)

    return {
        "available": True,
        "error": None,
        "app": "telegram",
        "messages": messages,
        "users": users_out,
        "chats": chats_out,
        "counts": counts,
    }


# ---------------------------------------------------------------------------
# Legacy live-only parser (kept for Tier-0 pipeline compatibility)
# ---------------------------------------------------------------------------


def parse_telegram_db(path: str | Path, max_rows: int = 5000) -> list[Message]:
    """Parse live Telegram messages from cache4.db.

    This is the Tier-0 / mock-corpus-compatible entry point used by the
    pipeline for basic live-row extraction. For full forensic recovery use
    :func:`recover_telegram_messages`.
    """
    path = Path(path)
    messages: list[Message] = []
    if not path.exists():
        return messages

    schema = detect_telegram_schema(path)
    msg_table = _find_messages_table(path)
    if not msg_table:
        return messages

    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            f"SELECT * FROM '{msg_table}' LIMIT {int(max_rows)}"
        ).fetchall()
        con.close()

        for row in rows:
            body = _extract_body_from_row(row, schema)
            if not body:
                continue
            sender = "<unknown>"
            from_col = schema.from_id_col()
            if from_col:
                try:
                    v = row[from_col]
                    if v is not None:
                        sender = str(v)
                except (KeyError, IndexError):
                    pass
            try:
                mock_sender = row["sender"]
                if mock_sender:
                    sender = str(mock_sender)
            except (KeyError, IndexError):
                pass
            ts = None
            date_col = schema.date_col()
            if date_col:
                try:
                    ts = _epoch_to_iso(row[date_col])
                except (KeyError, IndexError):
                    pass
            messages.append(
                Message(
                    app="telegram",
                    sender=sender,
                    body=body,
                    timestamp=ts,
                    confidence=Confidence.LIVE,
                    source_file=path.name,
                    provenance=f"live table '{msg_table}'",
                )
            )
    except sqlite3.Error as exc:
        log.warning("parse_telegram_db: %s", exc)

    return messages
