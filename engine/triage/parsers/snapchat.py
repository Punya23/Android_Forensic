"""Snapchat (`com.snapchat.android`) forensic parser — Tier 2 (root).

Acquisition tiers
-----------------
* **Non-root**: nothing evidentiary — the chat DBs are app-private, ``adb backup`` is disabled.
* **Tier 2 (root / full-filesystem image)**: pull the ``databases/`` folder. This module reads:
  - ``arroyo.db`` ``conversation_message`` — the modern (late-2020→present) chat store. Message
    text lives inside a **protobuf BLOB** (``message_content``); ``content_type == 1`` = text.
  - ``main.db`` ``Friend`` — identity (``userId`` → ``username`` / ``displayName``); ``Feed`` —
    last interaction per conversation.
  - legacy ``main.db`` ``Message`` (2017–2020) as a fallback era.
  Always carve WAL / freelist too — "Clear Conversation" leaves records recoverable for weeks.

Protobuf without a schema
-------------------------
No official ``.proto`` is published and field numbers drift between builds, so message text is
extracted with a **schema-less** walker (:func:`decode_protobuf_strings`) that keeps
length-delimited (wire-type-2) fields that are valid, mostly-printable UTF-8 — the approach the
open-source Snapchat parsers use. Sender id and timestamp are taken from the SQLite columns
(reliable), not the blob.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
import json
import zipfile
from typing import Any, Optional

from ..config import Confidence
from . import appchat

APP_LABEL = "snapchat"
_NOROOT = ("Snapchat chat databases (arroyo.db / main.db) live in app-private storage and "
           "require root / a full-filesystem image; adb backup is disabled for Snapchat.")

# arroyo.db conversation_message: content_type == 1 is a text message; other ints are media/snap/status.
CONTENT_TYPE_TEXT = 1
MIN_TEXT_LEN = 2


class SnapchatPaths:
    DATA_DIR: str = os.environ.get("SNAP_DATA_DIR", "/data/data/com.snapchat.android")

    @classmethod
    def arroyo_db(cls) -> str:
        return f"{cls.DATA_DIR}/databases/arroyo.db"

    @classmethod
    def main_db(cls) -> str:
        return f"{cls.DATA_DIR}/databases/main.db"


# --- schema-less protobuf string extraction --------------------------------

def _read_varint(b: bytes, i: int) -> tuple[Optional[int], int]:
    shift = 0
    result = 0
    while i < len(b):
        byte = b[i]
        i += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return result, i
        shift += 7
        if shift > 63:
            return None, i
    return None, i


def _mostly_printable(s: str) -> bool:
    if not s:
        return False
    printable = sum(1 for c in s if c == "\n" or c == "\t" or 32 <= ord(c) < 0x110000 and c.isprintable())
    return printable / len(s) >= 0.8


def decode_protobuf_strings(blob: bytes | bytearray | None, depth: int = 0,
                            max_depth: int = 6) -> list[str]:
    """Extract candidate UTF-8 strings from a protobuf blob without a schema.

    Walks wire-type-2 (length-delimited) fields: if a chunk is valid, mostly-printable UTF-8 it
    is kept as text; otherwise it is recursively treated as a nested message. Returns the list
    of extracted strings (longest/most-meaningful first is the caller's concern).
    """
    if not blob or depth > max_depth:
        return []
    b = bytes(blob)
    out: list[str] = []
    i, n = 0, len(b)
    while i < n:
        tag, i = _read_varint(b, i)
        if tag is None:
            break
        wt = tag & 0x07
        if wt == 0:            # varint
            _, i = _read_varint(b, i)
        elif wt == 1:          # 64-bit
            i += 8
        elif wt == 5:          # 32-bit
            i += 4
        elif wt == 2:          # length-delimited
            ln, i = _read_varint(b, i)
            if ln is None or ln < 0 or i + ln > n:
                break
            chunk = b[i:i + ln]
            i += ln
            decoded = None
            try:
                s = chunk.decode("utf-8")
                if _mostly_printable(s):
                    decoded = s
            except UnicodeDecodeError:
                decoded = None
            if decoded is not None:
                out.append(decoded)
            else:
                out.extend(decode_protobuf_strings(chunk, depth + 1, max_depth))
        else:                  # unknown / group — bail
            break
    return out


def _text_from_content(blob: Any) -> str:
    """Best text guess from an arroyo message_content protobuf blob."""
    if isinstance(blob, (bytes, bytearray)):
        strings = decode_protobuf_strings(blob)
        # Prefer the longest plausible text run.
        cands = [s.strip() for s in strings if len(s.strip()) >= MIN_TEXT_LEN]
        cands.sort(key=len, reverse=True)
        return cands[0] if cands else ""
    if isinstance(blob, str) and len(blob.strip()) >= MIN_TEXT_LEN:
        return blob.strip()
    return ""


# --- timestamps ------------------------------------------------------------

def _ms_to_iso(val: Any) -> Optional[str]:
    try:
        n = float(val)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    if n > 1e11:       # milliseconds
        n /= 1e3
    try:
        return datetime.fromtimestamp(n, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OSError, ValueError, OverflowError):
        return None


# --- identity (main.db Friend) ---------------------------------------------

def recover_snapchat_friends(main_db: str | Path) -> list[dict[str, Any]]:
    """user_id → username/displayName from main.db Friend (best-effort)."""
    p = Path(main_db)
    if not p.exists():
        return []
    users: list[dict[str, Any]] = []
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        tbl = next((r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND lower(name)='friend'")), None)
        if tbl:
            cols = [r[1] for r in con.execute(f"PRAGMA table_info('{tbl}')")]
            id_c = next((c for c in cols if c.lower() in ("userid", "user_id", "id")), None)
            un_c = next((c for c in cols if c.lower() == "username"), None)
            dn_c = next((c for c in cols if c.lower() in ("displayname", "display_name")), None)
            if id_c:
                for r in con.execute(f'SELECT * FROM "{tbl}"').fetchall():
                    uid = str(r[id_c]) if r[id_c] is not None else ""
                    if not uid:
                        continue
                    name = (r[un_c] if un_c and r[un_c] else None) or \
                           (r[dn_c] if dn_c and r[dn_c] else None) or uid
                    users.append({"id": uid, "name": str(name), "confidence": Confidence.LIVE.value})
        con.close()
    except sqlite3.Error:
        return users
    return users


# --- main recovery ---------------------------------------------------------

def recover_snapchat_messages(arroyo_db: str | Path,
                              main_db: str | Path | None = None,
                              max_live_rows: int = 10_000) -> dict[str, Any]:
    """Recover Snapchat chat messages (live + deleted) from arroyo.db (or legacy main.db)."""
    a_path = Path(arroyo_db)
    if not a_path.exists():
        # Legacy fallback: some images only have main.db with a Message table.
        if main_db and Path(main_db).exists():
            return _recover_legacy_main(Path(main_db), max_live_rows)
        return appchat.unavailable(APP_LABEL, _NOROOT)

    users = recover_snapchat_friends(main_db) if main_db else []
    uidx = {u["id"]: u["name"] for u in users}
    messages: list[dict[str, Any]] = []
    table = "conversation_message"

    try:
        con = sqlite3.connect(f"file:{a_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        have = next((r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND "
            "lower(name)='conversation_message'")), None)
        if not have:
            con.close()
            return {"available": True, "error": "no conversation_message table (unknown Snapchat era)",
                    "app": APP_LABEL, "messages": [], "users": users,
                    "counts": dict(appchat.ZERO_COUNTS)}
        cols = [r[1] for r in con.execute(f"PRAGMA table_info('{table}')")]

        def col(*names: str) -> Optional[str]:
            low = {c.lower(): c for c in cols}
            for nm in names:
                if nm in low:
                    return low[nm]
            return None

        c_content = col("message_content", "content")
        c_ts = col("creation_timestamp", "created_timestamp", "timestamp")
        c_type = col("content_type", "type")
        c_sender = col("sender_id", "senderid")
        c_conv = col("client_conversation_id", "conversation_id")
        sel = ", ".join(f'"{c}"' for c in cols)
        for r in con.execute(f'SELECT {sel} FROM "{table}" LIMIT {int(max_live_rows)}').fetchall():
            d = {c: r[c] for c in cols}
            ctype = d.get(c_type) if c_type else None
            content = d.get(c_content) if c_content else None
            if ctype == CONTENT_TYPE_TEXT or ctype is None:
                body = _text_from_content(content)
            else:
                body = f"[media/snap type={ctype}]"
            if not body:
                continue
            sid = str(d.get(c_sender) or "") if c_sender else ""
            messages.append(appchat.msg(
                body=body, sender=sid or "<unknown>",
                sender_name=uidx.get(sid, sid or "<unknown>"),
                timestamp=_ms_to_iso(d.get(c_ts)) if c_ts else None,
                chat_id=str(d.get(c_conv) or "") or None if c_conv else None,
                confidence=Confidence.LIVE.value, source_file=a_path.name,
                provenance=f"live row in {table} (content_type={ctype})"))
        con.close()
    except sqlite3.Error as exc:
        return {"available": True, "error": f"sqlite error: {exc}", "app": APP_LABEL,
                "messages": [], "users": users, "counts": dict(appchat.ZERO_COUNTS)}

    # Deleted-row + gap recovery. Carved blobs may arrive as raw bytes → protobuf-decode them;
    # otherwise fall back to the most message-like string in the row.
    def body_of(vals: list) -> str:
        for v in vals:
            if isinstance(v, (bytes, bytearray)):
                t = _text_from_content(v)
                if t and appchat.looks_like_message(t):
                    return t
        return appchat.best_content(vals)

    messages.extend(appchat.carve_and_gaps(
        a_path, table, body_of=body_of, source_name=a_path.name))

    return {
        "available": True, "error": None, "app": APP_LABEL,
        "messages": messages, "users": users,
        "schema": {"table": table, "columns": cols},
        "counts": appchat.count_by_confidence(messages),
    }


def _recover_legacy_main(main_db: Path, max_live_rows: int) -> dict[str, Any]:
    """Legacy (2017–2020) Snapchat: main.db Message table (content blob, byte-offset text)."""
    users = recover_snapchat_friends(main_db)
    uidx = {u["id"]: u["name"] for u in users}
    messages: list[dict[str, Any]] = []
    table = "Message"
    try:
        con = sqlite3.connect(f"file:{main_db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        have = next((r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND lower(name)='message'")), None)
        if not have:
            con.close()
            return appchat.unavailable(APP_LABEL, _NOROOT)
        table = have
        cols = [r[1] for r in con.execute(f"PRAGMA table_info('{table}')")]
        low = {c.lower(): c for c in cols}
        c_content = low.get("content")
        c_ts = low.get("timestamp")
        c_sender = low.get("senderid") or low.get("sender_id")
        for r in con.execute(f'SELECT * FROM "{table}" LIMIT {int(max_live_rows)}').fetchall():
            body = _text_from_content(r[c_content]) if c_content else ""
            if not body:
                continue
            sid = str(r[c_sender]) if c_sender and r[c_sender] is not None else ""
            messages.append(appchat.msg(
                body=body, sender=sid or "<unknown>", sender_name=uidx.get(sid, sid or "<unknown>"),
                timestamp=_ms_to_iso(r[c_ts]) if c_ts else None,
                confidence=Confidence.LIVE.value, source_file=main_db.name,
                provenance=f"live row in legacy {table}"))
        con.close()
    except sqlite3.Error as exc:
        return {"available": True, "error": f"sqlite error: {exc}", "app": APP_LABEL,
                "messages": [], "users": users, "counts": dict(appchat.ZERO_COUNTS)}

    messages.extend(appchat.carve_and_gaps(
        main_db, table, body_of=_legacy_body, source_name=main_db.name))
    return {"available": True, "error": None, "app": APP_LABEL, "messages": messages,
            "users": users, "schema": {"table": table, "era": "legacy_main"},
            "counts": appchat.count_by_confidence(messages)}


def _legacy_body(vals: list) -> str:
    for v in vals:
        if isinstance(v, (bytes, bytearray)):
            t = _text_from_content(v)
            if t and appchat.looks_like_message(t):
                return t
    return appchat.best_content(vals)


# --- "My Data" export ingest (non-root, user-initiated) --------------------

def _export_ts(val: Any) -> Optional[str]:
    """Parse a Snapchat export timestamp (e.g. '2021-01-01 12:00:00 UTC') to ISO-8601."""
    if not val:
        return None
    raw = str(val).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S %Z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
    return _ms_to_iso(raw) if raw.isdigit() else None


def parse_snapchat_export(path: str | Path) -> dict[str, Any]:
    """Parse a Snapchat "My Data" export (ZIP / dir / json) — json/chat_history.json.

    Snapchat's export retains only the conversations the user *chose to keep*. Structure varies
    across export versions, so this walks every list in ``chat_history.json`` tolerantly,
    extracting ``From`` / ``Text`` (or media type) / ``Created`` per message. Returns the same
    result contract as ``recover_snapchat_messages`` (all confidence = LIVE, source = export).
    """
    p = Path(path)
    messages: list[dict[str, Any]] = []

    def _add(section: str, m: dict) -> None:
        body = m.get("Text") or m.get("Content") or m.get("text") or ""
        mtype = str(m.get("Media Type") or m.get("media_type") or "")
        if not body and mtype and mtype.upper() != "TEXT":
            body = f"[{mtype.lower()}]"
        if not body:
            return
        sender = str(m.get("From") or m.get("from") or "<unknown>")
        conv = str(m.get("Conversation Title") or section or sender)
        messages.append(appchat.msg(
            body=str(body), sender=sender, sender_name=sender,
            timestamp=_export_ts(m.get("Created") or m.get("created")),
            chat_id=conv, confidence=Confidence.LIVE.value,
            source_file="snapchat_export", provenance="My Data export"))

    def _handle(obj: Any) -> None:
        if isinstance(obj, dict):
            for section, items in obj.items():
                if isinstance(items, list):
                    for m in items:
                        if isinstance(m, dict):
                            _add(str(section), m)
                elif isinstance(items, dict):
                    _handle(items)
        elif isinstance(obj, list):
            for m in obj:
                if isinstance(m, dict):
                    _add("chat", m)

    try:
        if p.is_file() and p.suffix.lower() == ".zip":
            with zipfile.ZipFile(p) as z:
                for name in z.namelist():
                    if name.lower().endswith("chat_history.json"):
                        _handle(json.loads(z.read(name)))
        elif p.is_dir():
            for jf in p.rglob("chat_history.json"):
                _handle(json.loads(jf.read_text(encoding="utf-8", errors="replace")))
        elif p.is_file() and p.suffix.lower() == ".json":
            _handle(json.loads(p.read_text(encoding="utf-8", errors="replace")))
    except Exception as exc:
        return {"available": False, "error": f"export parse error: {exc}", "app": APP_LABEL,
                "messages": [], "counts": dict(appchat.ZERO_COUNTS)}

    return {"available": True, "error": None, "app": APP_LABEL, "messages": messages,
            "users": [], "schema": {"source": "mydata_export"},
            "counts": appchat.count_by_confidence(messages)}
