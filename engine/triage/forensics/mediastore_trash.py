"""MediaStore trash recovery — the highest-yield *non-root* deleted-media technique.

When a user "deletes" a photo or video on Android 11+ the file is not erased. The
MediaProvider moves it to the trash: the row's ``is_trashed`` flag is set, ``date_expires``
is stamped to roughly 30 days in the future (``TRASH_MAX_DURATION``), and the file on disk
is renamed to ``.trashed-<date_expires_seconds>-<original_name>``. The bytes stay fully
intact and in place until that expiry, at which point MediaProvider auto-purges them.

This gives an examiner two things a raw carve never could:

  * the **actual file**, byte-for-byte, recoverable with no root and no carving; and
  * a **defensible deletion timestamp** — ``date_expires`` minus the 30-day window is when
    the user trashed it. That is a real recorded time, not an inference from a gap.

This module fuses the two independent signals eRakshak already collects:

  * the **MediaStore catalogue** (``media_inventory.json``) — the database's own record of
    which items are trashed/pending, authoritative even when the file was not pulled; and
  * the **filesystem** (the case manifest) — ``.trashed-*`` / ``.pending-*`` files actually
    pulled off shared storage, which carry the expiry epoch in their name.

Honesty rules baked in:

  * A trashed item whose file was pulled is ``RECOVERED_VERIFIED`` (we hold the bytes).
  * A trashed item known only from the MediaStore row, with no file recovered, is
    ``DELETION_DETECTED`` — we can prove a deletion happened and when, but not produce the
    content. These are never conflated.
  * The 30-day window is Android's default; some OEMs change it, so the estimated deletion
    time is labelled an estimate and the exact ``date_expires`` is always shown alongside.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from ..config import Confidence

#: Android's default trash retention (frameworks/base MediaProvider TRASH_MAX_DURATION).
#: The deletion happened this long before the stamped auto-purge (date_expires).
TRASH_WINDOW_DAYS = 30

#: ``.trashed-<expires_epoch_seconds>-<original_display_name>``
_TRASHED_RE = re.compile(r"\.trashed-(\d+)-(.+)$")
#: ``.pending-<expires_epoch_seconds>-<original_display_name>`` (interrupted/in-progress write)
_PENDING_RE = re.compile(r"\.pending-(\d+)-(.+)$")


def parse_trash_filename(name: str) -> Optional[tuple[str, int, str]]:
    """Parse a MediaProvider trash/pending filename.

    Returns ``(state, expires_epoch_seconds, original_name)`` where *state* is
    ``"trashed"`` or ``"pending"``, or ``None`` if *name* is not a trash file.
    """
    base = Path(name).name
    m = _TRASHED_RE.match(base)
    if m:
        return "trashed", int(m.group(1)), m.group(2)
    m = _PENDING_RE.match(base)
    if m:
        return "pending", int(m.group(1)), m.group(2)
    return None


def _epoch_to_iso(epoch_s: Optional[int]) -> Optional[str]:
    if not epoch_s:
        return None
    try:
        return datetime.fromtimestamp(int(epoch_s), tz=timezone.utc).isoformat()
    except (ValueError, OSError, OverflowError):
        return None


def _iso_to_epoch(iso: Optional[str]) -> Optional[int]:
    if not iso:
        return None
    try:
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())
    except (ValueError, TypeError):
        return None


def _deletion_estimate(expires_epoch: Optional[int]) -> Optional[str]:
    """When the item was trashed = expiry − the retention window."""
    if not expires_epoch:
        return None
    return _epoch_to_iso(expires_epoch - TRASH_WINDOW_DAYS * 86400)


def _days_until_purge(expires_epoch: Optional[int], now_epoch: int) -> Optional[int]:
    if not expires_epoch:
        return None
    return round((expires_epoch - now_epoch) / 86400, 1)


class _TrashRecord:
    """One trashed/pending media item, fused from DB and/or filesystem evidence."""

    __slots__ = (
        "original_name",
        "state",
        "kind",
        "owner_app",
        "size_bytes",
        "expires_epoch",
        "media_id",
        "artifact_id",
        "stored_path",
        "sha256",
        "sources",
        "gps",
    )

    def __init__(self, original_name: str, state: str) -> None:
        self.original_name = original_name
        self.state = state
        self.kind = ""
        self.owner_app: Optional[str] = None
        self.size_bytes = 0
        self.expires_epoch: Optional[int] = None
        self.media_id: Optional[int] = None
        self.artifact_id: Optional[str] = None
        self.stored_path: Optional[str] = None
        self.sha256: Optional[str] = None
        self.sources: set[str] = set()
        self.gps: Optional[dict] = None

    @property
    def file_recoverable(self) -> bool:
        return self.artifact_id is not None

    def to_dict(self, now_epoch: int) -> dict:
        # We hold the bytes → RECOVERED_VERIFIED. We only know from the DB that it was
        # deleted → DELETION_DETECTED. These are deliberately different confidence classes.
        confidence = (
            Confidence.RECOVERED_VERIFIED.value
            if self.file_recoverable
            else Confidence.DELETION_DETECTED.value
        )
        expires_iso = _epoch_to_iso(self.expires_epoch)
        deleted_iso = _deletion_estimate(self.expires_epoch)
        if self.file_recoverable:
            note = (
                "Trashed file recovered intact from shared storage — no root, no "
                "carving. Content is byte-for-byte the deleted original."
            )
        elif self.state == "pending":
            note = (
                "MediaStore row is in the 'pending' state — an interrupted or "
                "in-progress write, not a user deletion."
            )
        else:
            note = (
                "MediaStore records this item as trashed but the file was not "
                "recovered (already purged, or not in the pulled scope). Deletion is "
                "proven; content is not available."
            )
        src = (
            "both"
            if len(self.sources) > 1
            else next(iter(self.sources)) if self.sources else "unknown"
        )
        return {
            "original_name": self.original_name,
            "state": self.state,  # trashed | pending
            "kind": self.kind,
            "owner_app": self.owner_app,
            "size_bytes": self.size_bytes,
            "media_id": self.media_id,
            "file_recoverable": self.file_recoverable,
            "artifact_id": self.artifact_id,
            "stored_path": self.stored_path,
            "sha256": self.sha256,
            "expires_at": expires_iso,
            "estimated_deleted_at": deleted_iso,
            "deleted_at_is_estimate": True,
            "retention_window_days": TRASH_WINDOW_DAYS,
            "days_until_auto_purge": _days_until_purge(self.expires_epoch, now_epoch),
            "gps": self.gps,
            "confidence": confidence,
            "source": src,  # mediastore | filesystem | both
            "note": note,
            "provenance": (
                f"MediaStore trash ({src}); deletion time estimated as date_expires − "
                f"{TRASH_WINDOW_DAYS}d (Android default retention; OEM values may differ)."
            ),
            "requires_verification": True,
        }


def analyze_mediastore_trash(
    media_inventory: Iterable[Any],
    manifest: Iterable[Any],
    *,
    now: Optional[datetime] = None,
) -> dict:
    """Fuse the MediaStore catalogue and the filesystem into a trash report.

    Parameters
    ----------
    media_inventory:
        ``MediaInventoryItem`` objects (or dicts) — the DB side.
    manifest:
        ``ArtifactRecord`` objects (or dicts) — the filesystem side; entries whose device
        path is a ``.trashed-*`` / ``.pending-*`` name are the recovered files.
    now:
        Reference time for the purge countdown (defaults to UTC now); injectable for tests.
    """
    now_epoch = int((now or datetime.now(timezone.utc)).timestamp())
    # Key records by (state, original_name) so the DB row and the pulled file merge.
    records: dict[tuple[str, str], _TrashRecord] = {}

    def _get(key: tuple[str, str]) -> _TrashRecord:
        if key not in records:
            records[key] = _TrashRecord(key[1], key[0])
        return records[key]

    # --- filesystem side: actually-recovered .trashed-* / .pending-* files -----
    for rec in manifest:
        source_path = _attr(rec, "source_path", "")
        parsed = parse_trash_filename(source_path)
        if not parsed:
            # Some sources only flag it, without the tell-tale name.
            flags = _attr(rec, "flags", []) or []
            if "trashed" not in flags:
                continue
            state, expires_epoch, original = "trashed", None, Path(source_path).name
        else:
            state, expires_epoch, original = parsed
        r = _get((state, original))
        r.sources.add("filesystem")
        r.artifact_id = _attr(rec, "artifact_id", None)
        r.stored_path = _attr(rec, "stored_path", None)
        r.sha256 = _attr(rec, "sha256", None)
        r.size_bytes = r.size_bytes or int(_attr(rec, "size_bytes", 0) or 0)
        if expires_epoch:
            r.expires_epoch = expires_epoch
        if not r.owner_app:
            r.owner_app = _attr(rec, "app", None)

    # --- database side: MediaStore rows flagged trashed / pending --------------
    for item in media_inventory:
        is_trashed = bool(_attr(item, "is_trashed", False))
        is_pending = bool(_attr(item, "is_pending", False))
        if not (is_trashed or is_pending):
            continue
        state = "trashed" if is_trashed else "pending"
        name = str(_attr(item, "display_name", "") or "")
        if not name:
            # Fall back to the on-disk name if the display name is blank.
            name = Path(str(_attr(item, "data_path", "") or "")).name
        r = _get((state, name))
        r.sources.add("mediastore")
        r.media_id = _attr(item, "media_id", None)
        r.kind = r.kind or str(_attr(item, "kind", "") or "")
        r.owner_app = r.owner_app or _attr(item, "owner_app", None)
        r.size_bytes = r.size_bytes or int(_attr(item, "size_bytes", 0) or 0)
        r.gps = r.gps or _attr(item, "gps", None)
        exp = _iso_to_epoch(_attr(item, "date_expires", None))
        if exp and not r.expires_epoch:
            r.expires_epoch = exp

    items = [r.to_dict(now_epoch) for r in records.values()]
    # Recovered files first, then by soonest auto-purge (most urgent to act on).
    items.sort(
        key=lambda d: (
            not d["file_recoverable"],
            (
                d["days_until_auto_purge"]
                if d["days_until_auto_purge"] is not None
                else 1e9
            ),
        )
    )

    return {"items": items, "summary": _summarise(items)}


def _summarise(items: list[dict]) -> dict:
    trashed = [i for i in items if i["state"] == "trashed"]
    pending = [i for i in items if i["state"] == "pending"]
    recoverable = [i for i in items if i["file_recoverable"]]
    expiring = [
        i
        for i in trashed
        if i["days_until_auto_purge"] is not None
        and 0 <= i["days_until_auto_purge"] <= 3
        and i["file_recoverable"]
    ]
    return {
        "total": len(items),
        "trashed": len(trashed),
        "pending": len(pending),
        "file_recovered": len(recoverable),
        "deletion_detected_only": len(items) - len(recoverable),
        "recovered_bytes": sum(i["size_bytes"] for i in recoverable),
        "expiring_within_3_days": len(expiring),
        "note": (
            "Trashed media on Android 11+ is intact and non-root recoverable within a "
            f"~{TRASH_WINDOW_DAYS}-day window; date_expires gives a defensible deletion "
            "time. Items marked deletion-detected are proven deleted but their content "
            "was not recovered."
        ),
    }


def _attr(obj: Any, name: str, default: Any) -> Any:
    """Read *name* from a dataclass/object or a dict, uniformly."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)
