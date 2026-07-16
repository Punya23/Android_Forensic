"""Instagram (`com.instagram.android`) forensic parser — Tier 2 (root) + DYI export.

Acquisition tiers
-----------------
* **Non-root**: nothing. Instagram's Direct DB lives in app-private storage and Android 11+
  scoped storage also walls off its external cache — confirmed across all commercial tools.
* **Tier 2 (root / full-filesystem image)**: pull
  ``/data/data/com.instagram.android/databases/direct.db`` (+ ``-wal``/``-journal`` for deleted
  messages) and ``shared_prefs/`` for identity. This module then recovers live + deleted DMs
  with confidence badges and resolves user ids → usernames.
* **Consent / cloud**: the user's "Download Your Data" (DYI) export is a separate JSON ingest
  path (``parse_instagram_export``) — non-root but user-initiated.

Schema honesty
--------------
Instagram's Direct schema churns heavily across app versions. Rather than hardcode column
names, we introspect the ``messages``/``threads`` tables and classify columns by name
heuristics (text / timestamp / thread / user). Direct-DB timestamps are epoch **microseconds**;
DYI-export timestamps are epoch **seconds** — both are auto-detected by magnitude.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..config import Confidence
from . import appchat

APP_LABEL = "instagram"
_NOROOT = ("Instagram Direct messages live in app-private storage and require root / a "
           "full-filesystem image. Consider the user's 'Download Your Data' export instead.")


class InstagramPaths:
    DATA_DIR: str = os.environ.get("IG_DATA_DIR", "/data/data/com.instagram.android")

    @classmethod
    def direct_db(cls) -> str:
        return f"{cls.DATA_DIR}/databases/direct.db"

    @classmethod
    def prefs_dir(cls) -> str:
        return f"{cls.DATA_DIR}/shared_prefs"


# --- timestamp -------------------------------------------------------------

def _epoch_to_iso(val: Any) -> Optional[str]:
    """Normalise an epoch value (s / ms / µs) to ISO-8601 UTC."""
    try:
        n = float(val)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    if n > 1e14:        # microseconds
        n /= 1e6
    elif n > 1e11:      # milliseconds
        n /= 1e3
    try:
        return datetime.fromtimestamp(n, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OSError, ValueError, OverflowError):
        return None


# --- schema detection ------------------------------------------------------

def _tables(con: sqlite3.Connection) -> list[str]:
    return [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]


def _find_table(con: sqlite3.Connection, *candidates: str) -> Optional[str]:
    names = _tables(con)
    lower = {n.lower(): n for n in names}
    for c in candidates:
        if c.lower() in lower:
            return lower[c.lower()]
    # substring fallback
    for n in names:
        if any(c.lower() in n.lower() for c in candidates):
            return n
    return None


def _cols(con: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in con.execute(f"PRAGMA table_info('{table}')")]


def _classify(cols: list[str]) -> dict[str, Optional[str]]:
    """Map roles → column name for a messages table (heuristic)."""
    role: dict[str, Optional[str]] = {"text": None, "message": None, "ts": None,
                                      "thread": None, "user": None}
    for c in cols:
        lc = c.lower()
        if role["text"] is None and lc == "text":
            role["text"] = c
        elif role["message"] is None and lc in ("message", "content"):
            role["message"] = c
        elif role["ts"] is None and ("timestamp" in lc or lc == "date" or lc.endswith("_time")):
            role["ts"] = c
        elif role["thread"] is None and "thread" in lc:
            role["thread"] = c
        elif role["user"] is None and (lc in ("user_id", "sender_id", "recipient_id")
                                       or lc.endswith("user_id")):
            role["user"] = c
    return role


def _body_from(text_val: Any, message_val: Any) -> str:
    if text_val and str(text_val).strip():
        return str(text_val).strip()
    if message_val:
        raw = message_val
        try:
            j = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        except Exception:
            j = None
        if isinstance(j, dict):
            if j.get("text"):
                return str(j["text"])
            for k in ("url", "media_url", "playable_url", "link"):
                if j.get(k):
                    return f"[media] {j[k]}"
    return ""


# --- identity --------------------------------------------------------------

_IG_PAIR = re.compile(r'"(?:pk|user_id|id)"\s*:\s*"?(\d{3,})"?[^}]{0,200}?"username"\s*:\s*"([^"]+)"')
_IG_PAIR_REV = re.compile(r'"username"\s*:\s*"([^"]+)"[^}]{0,200}?"(?:pk|user_id|id)"\s*:\s*"?(\d{3,})"?')


def recover_instagram_users(prefs_dir: str | Path) -> list[dict[str, Any]]:
    """Best-effort user_id → username map from Instagram shared_prefs XML blobs."""
    prefs = Path(prefs_dir)
    if not prefs.exists():
        return []
    seen: dict[str, str] = {}
    for xml in prefs.glob("*.xml"):
        try:
            text = xml.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for uid, name in _IG_PAIR.findall(text):
            seen.setdefault(uid, name)
        for name, uid in _IG_PAIR_REV.findall(text):
            seen.setdefault(uid, name)
    return [{"id": uid, "name": name, "confidence": Confidence.LIVE.value}
            for uid, name in seen.items()]


# --- main recovery ---------------------------------------------------------

def recover_instagram_messages(direct_db: str | Path,
                               prefs_dir: str | Path | None = None,
                               max_live_rows: int = 10_000) -> dict[str, Any]:
    """Recover Instagram Direct messages (live + deleted) from ``direct.db``."""
    db_path = Path(direct_db)
    if not db_path.exists():
        return appchat.unavailable(APP_LABEL, _NOROOT)

    users = recover_instagram_users(prefs_dir) if prefs_dir else []
    messages: list[dict[str, Any]] = []
    msg_table: Optional[str] = None
    role: dict[str, Optional[str]] = {}

    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        msg_table = _find_table(con, "messages", "thread_messages", "message")
        thread_title: dict[str, str] = {}
        if msg_table:
            cols = _cols(con, msg_table)
            role = _classify(cols)
            sel = ", ".join(f'"{c}"' for c in cols)
            rows = con.execute(
                f'SELECT {sel} FROM "{msg_table}" LIMIT {int(max_live_rows)}').fetchall()
            for r in rows:
                d = {c: r[c] for c in cols}
                body = _body_from(d.get(role["text"] or ""), d.get(role["message"] or ""))
                if not body:
                    continue
                messages.append(appchat.msg(
                    body=body,
                    sender=str(d.get(role["user"] or "") or "<unknown>"),
                    timestamp=_epoch_to_iso(d.get(role["ts"] or "")),
                    chat_id=str(d.get(role["thread"] or "") or "") or None,
                    confidence=Confidence.LIVE.value, source_file=db_path.name,
                    provenance=f"live row in {msg_table}"))
        # thread titles
        t_table = _find_table(con, "threads", "thread")
        if t_table:
            tcols = _cols(con, t_table)
            id_c = next((c for c in tcols if "thread" in c.lower() and "id" in c.lower()), None)
            name_c = next((c for c in tcols if c.lower() in ("thread_title", "title", "name")), None)
            if id_c:
                for tr in con.execute(f'SELECT * FROM "{t_table}"').fetchall():
                    cid = str(tr[id_c]) if tr[id_c] is not None else ""
                    if cid:
                        thread_title[cid] = str(tr[name_c]) if name_c and tr[name_c] else f"Thread {cid}"
        con.close()
    except sqlite3.Error as exc:
        return {"available": True, "error": f"sqlite error: {exc}", "app": APP_LABEL,
                "messages": [], "counts": dict(appchat.ZERO_COUNTS)}

    # Deleted-row + gap recovery on the messages table. Carved-row column alignment is
    # unreliable (INTEGER PRIMARY KEY aliases the rowid and is omitted from the record body),
    # so we pick the most message-like string rather than trust a fixed column index; sender /
    # timestamp are left unattributed for carved rows rather than risk mis-attribution.
    if msg_table:
        messages.extend(appchat.carve_and_gaps(
            db_path, msg_table, body_of=appchat.best_content, source_name=db_path.name))

    # Resolve sender names.
    uidx = {u["id"]: u["name"] for u in users}
    for m in messages:
        if m["sender"] in uidx:
            m["sender_name"] = uidx[m["sender"]]

    return {
        "available": True, "error": None, "app": APP_LABEL,
        "messages": messages, "users": users,
        "schema": {"table": msg_table, "roles": role},
        "counts": appchat.count_by_confidence(messages),
    }


# --- DYI ("Download Your Data") export ingest ------------------------------

def parse_instagram_export(path: str | Path) -> dict[str, Any]:
    """Parse an Instagram DYI export (ZIP or unpacked dir) — messages/inbox/*/message_1.json.

    Export timestamps are epoch **seconds** (or ms in ``timestamp_ms``). Returns the same
    result contract as ``recover_instagram_messages`` (all confidence = LIVE, source = export).
    """
    p = Path(path)
    messages: list[dict[str, Any]] = []

    def _handle_thread(thread_name: str, obj: dict) -> None:
        for m in obj.get("messages", []):
            if not isinstance(m, dict):
                continue
            content = m.get("content") or ""
            if not content and m.get("share"):
                content = f"[share] {m['share'].get('link', '')}"
            if not content and m.get("photos"):
                content = "[photo]"
            ts = m.get("timestamp_ms") or m.get("timestamp")
            messages.append(appchat.msg(
                body=str(content), sender=str(m.get("sender_name") or "<unknown>"),
                sender_name=str(m.get("sender_name") or "<unknown>"),
                timestamp=_epoch_to_iso(ts), chat_id=thread_name,
                confidence=Confidence.LIVE.value, source_file="instagram_export",
                provenance="Download-Your-Data export"))

    try:
        if p.is_file() and p.suffix.lower() == ".zip":
            with zipfile.ZipFile(p) as z:
                for name in z.namelist():
                    if name.endswith("message_1.json") and "inbox/" in name.replace("\\", "/"):
                        thread = name.replace("\\", "/").split("inbox/")[1].split("/")[0]
                        try:
                            _handle_thread(thread, json.loads(z.read(name)))
                        except Exception:
                            continue
        elif p.is_dir():
            for jf in p.rglob("message_1.json"):
                if "inbox" in str(jf).replace("\\", "/"):
                    thread = jf.parent.name
                    try:
                        _handle_thread(thread, json.loads(jf.read_text(encoding="utf-8", errors="replace")))
                    except Exception:
                        continue
    except Exception as exc:
        return {"available": False, "error": f"export parse error: {exc}", "app": APP_LABEL,
                "messages": [], "counts": dict(appchat.ZERO_COUNTS)}

    return {"available": True, "error": None, "app": APP_LABEL, "messages": messages,
            "users": [], "schema": {"source": "dyi_export"},
            "counts": appchat.count_by_confidence(messages)}
