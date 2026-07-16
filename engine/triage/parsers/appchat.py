"""Shared building blocks for app-specific chat recovery (Instagram, Snapchat, …).

Factors out the boilerplate that ``telegram.py`` established so every messaging-app parser
produces the **same result contract** and reuses the same tested SQLite recovery engine:

    {"available": bool, "error": str|None, "messages": [msg,…], "counts": {...}, ...}

where each ``msg`` is a dict with the keys the pipeline/timeline/dashboard already understand:
``body, sender, sender_name, timestamp, chat_id, confidence, source_file, provenance,
carve_method, page, offset, warnings, media_artifact_id``.

All of these apps keep their real chat store in app-private storage, so the parsers only
produce data on a rooted device / full-filesystem image / synthetic corpus; otherwise they
return a standardised ``available: False`` dict (never raise).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from ..config import Confidence

MIN_STR_LEN = 3

# Words that betray a leaked SQLite schema / DDL fragment rather than real message content.
_SCHEMA_WORDS = ("create", "table", "integer", "primary key", "text", "blob", "not null",
                 "autoincrement", "index", "varchar", "references", "sqlite_")


_IDENTIFIER = __import__("re").compile(r"[A-Za-z0-9_]+")


def looks_like_message(s: Any) -> bool:
    """Heuristic gate rejecting carve artifacts (schema DDL, binary fragments, bare identifiers).

    Raw carving over a DB surfaces ``CREATE TABLE`` DDL, table/column names, and byte fragments
    with embedded control characters; none of these are message content. A real message reads
    like text: mostly-printable, no embedded control bytes, has letters, and is either a
    multi-word phrase or a long single token (not a bare table/column identifier).
    """
    if not isinstance(s, str):
        return False
    s = s.strip()
    if not (MIN_STR_LEN <= len(s) <= 2000):
        return False
    # Embedded control/binary bytes ⇒ a carved fragment, not text.
    if any(ord(ch) < 32 and ch not in "\n\t\r" for ch in s):
        return False
    low = s.lower()
    n_schema = sum(1 for w in _SCHEMA_WORDS if w in low)
    if n_schema >= 2 or (n_schema >= 1 and "(" in s):
        return False
    n_alpha = sum(1 for ch in s if ch.isalpha())
    if n_alpha < 2:
        return False
    # A bare identifier (no spaces, only word chars) is a table/column name, not a message,
    # and a short spaceless token with few letters is a byte fragment.
    if " " not in s and (_IDENTIFIER.fullmatch(s) or n_alpha < 4):
        return False
    printable = sum(1 for ch in s if ch.isprintable() or ch in " \t")
    if printable / len(s) < 0.9:
        return False
    return True


def best_content(values: list) -> str:
    """Pick the most message-like string from a carved row's values (order-robust)."""
    cands = [v.strip() for v in values if looks_like_message(v)]
    return max(cands, key=len) if cands else ""


# The confidence buckets every app recovery reports.
ZERO_COUNTS: dict[str, int] = {
    "live": 0, "recovered_verified": 0, "carved_partial": 0,
    "deletion_detected": 0, "total": 0,
}


def msg(*, body: str, sender: str = "<unknown>", sender_name: Optional[str] = None,
        timestamp: Optional[str] = None, chat_id: Optional[str] = None,
        confidence: str = Confidence.LIVE.value, source_file: str = "",
        provenance: str = "", carve_method: str = "", page: Optional[int] = None,
        offset: Optional[int] = None, warnings: Optional[list] = None,
        media_artifact_id: Optional[str] = None, **extra: Any) -> dict[str, Any]:
    """Build a normalised message dict (all standard keys present)."""
    d = {
        "body": body, "sender": sender, "sender_name": sender_name or sender,
        "timestamp": timestamp, "chat_id": chat_id, "confidence": confidence,
        "source_file": source_file, "provenance": provenance,
        "carve_method": carve_method, "page": page, "offset": offset,
        "warnings": warnings or [], "media_artifact_id": media_artifact_id,
    }
    d.update(extra)
    return d


def unavailable(app_label: str, reason: str) -> dict[str, Any]:
    """Standardised 'not reachable' result (e.g. non-root device)."""
    return {"available": False, "error": reason, "app": app_label,
            "messages": [], "counts": dict(ZERO_COUNTS)}


def count_by_confidence(messages: list[dict]) -> dict[str, int]:
    counts = dict(ZERO_COUNTS)
    for m in messages:
        c = m.get("confidence", "")
        if c == Confidence.LIVE.value:
            counts["live"] += 1
        elif c == Confidence.RECOVERED_VERIFIED.value:
            counts["recovered_verified"] += 1
        elif c == Confidence.CARVED_PARTIAL.value:
            counts["carved_partial"] += 1
        elif c == Confidence.DELETION_DETECTED.value:
            counts["deletion_detected"] += 1
    counts["total"] = len(messages)
    return counts


def _conf_value(c: Any) -> str:
    return c.value if hasattr(c, "value") else str(c)


def carve_and_gaps(
    db_path: str | Path, table: str, *,
    body_of: Callable[[list], str],
    sender_of: Optional[Callable[[list], str]] = None,
    ts_of: Optional[Callable[[list], Optional[str]]] = None,
    chat_of: Optional[Callable[[list], Optional[str]]] = None,
    source_name: Optional[str] = None,
    use_sqbrite: bool = False,
) -> list[dict[str, Any]]:
    """Recover deleted/carved rows and deletion-gaps from *table* using the shared engine.

    Reuses ``recover_deleted_rows`` (structured freelist/freeblock/WAL B-tree carve — the clean
    recovery path) and ``detect_rowid_gaps`` (deletion proof). ``sqbrite_cross_check`` (a raw
    whole-file byte scan) is opt-in via *use_sqbrite*: it catches rows the B-tree walk misses
    but is noisy on small app DBs (it re-surfaces schema/other-table bytes), so app-chat parsers
    keep it off and rely on the structured carve plus the ``looks_like_message`` gate.
    """
    from ..recovery import recover_deleted_rows, detect_rowid_gaps, sqbrite_cross_check

    src = source_name or Path(db_path).name
    out: list[dict[str, Any]] = []

    try:
        carved = recover_deleted_rows(db_path, table=table)
    except Exception:
        carved = []
    for row in carved:
        body = body_of(row.values)
        if not looks_like_message(body):
            continue
        out.append(msg(
            body=body,
            sender=(sender_of(row.values) if sender_of else "<recovered>"),
            timestamp=(ts_of(row.values) if ts_of else None),
            chat_id=(chat_of(row.values) if chat_of else None),
            confidence=_conf_value(row.confidence),
            source_file=src, provenance=row.provenance,
            carve_method="freelist/freeblock",
            page=row.page, offset=row.offset, warnings=list(row.warnings)))

    if use_sqbrite:
        try:
            extra = sqbrite_cross_check(db_path, primary_rows=carved)
        except Exception:
            extra = []
        for sq in extra:
            body = best_content(list(sq.values))
            if not looks_like_message(body):
                continue
            out.append(msg(
                body=body, confidence=Confidence.CARVED_PARTIAL.value,
                source_file=sq.source_file, provenance=sq.provenance,
                carve_method="sqbrite", offset=sq.offset, warnings=list(sq.warnings)))

    try:
        gaps = detect_rowid_gaps(db_path, table)
    except Exception:
        gaps = []
    for gap in gaps:
        out.append(msg(
            body="", sender="<gap>", confidence=Confidence.DELETION_DETECTED.value,
            source_file=src, carve_method="gap_analysis",
            provenance=(f"rowid gap after {gap['after_rowid']} — "
                        f"{gap['missing']} row(s) deleted, no content recoverable"),
            warnings=["Rowid gap proves deletion occurred; no message content recovered."],
            gap_detail=gap))
    return out


def thread_conversations(
    messages: list[dict[str, Any]],
    participants: Optional[list[dict[str, Any]]] = None,
    chat_titles: Optional[dict[str, str]] = None,
) -> dict[str, dict[str, Any]]:
    """Group messages into conversation threads keyed by ``chat_id`` (app-neutral).

    *participants* is a list of ``{"id","name"}`` (or ``{"_id","_name"}``) used to resolve
    sender ids to display names. *chat_titles* optionally maps chat_id → human title.
    """
    idx: dict[str, str] = {}
    for p in participants or []:
        pid = str(p.get("id") or p.get("_id") or "")
        name = p.get("name") or p.get("_name") or pid
        if pid:
            idx[pid] = str(name)
    titles = chat_titles or {}

    conv: dict[str, dict[str, Any]] = {}
    for m in messages:
        cid = str(m.get("chat_id") or "__ungrouped__")
        c = conv.get(cid)
        if c is None:
            c = conv[cid] = {
                "chat_id": cid, "title": titles.get(cid, f"Chat {cid}"),
                "participants": [], "_pids": set(),
                "last_message_ts": None, "message_count": 0, "messages": [],
            }
        sid = str(m.get("sender") or "<unknown>")
        sname = m.get("sender_name") or idx.get(sid, sid)
        ts = m.get("timestamp")
        if ts and (c["last_message_ts"] is None or ts > c["last_message_ts"]):
            c["last_message_ts"] = ts
        if sid and sid not in c["_pids"]:
            c["_pids"].add(sid)
            c["participants"].append({"id": sid, "name": sname})
        c["messages"].append({
            "body": m.get("body", ""), "sender_name": sname, "sender_id": sid,
            "timestamp": ts, "confidence": m.get("confidence", Confidence.LIVE.value),
            "media_artifact_id": m.get("media_artifact_id"),
            "carve_method": m.get("carve_method", ""), "provenance": m.get("provenance", ""),
        })
        c["message_count"] += 1

    for c in conv.values():
        c["messages"].sort(key=lambda x: x.get("timestamp") or "0000")
        c.pop("_pids", None)
    return conv
