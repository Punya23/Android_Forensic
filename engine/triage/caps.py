"""Size caps for a demo-sized acquisition.

A phone holding 128 GB cannot be pulled in a ten-minute demonstration, so a run can be capped
overall (``total_bytes``) and per category (``bucket_bytes``). Selection is deterministic: within
a category the newest files are taken first, and the overall budget is shared between categories
round-robin so photos cannot starve WhatsApp. A file that does not fit what is left is skipped,
never truncated. The returned report states exactly what was left behind, because a capped
acquisition is a partial one and the case must say so.
"""

from __future__ import annotations

import re
from typing import Callable, Iterable

Entry = tuple[str, int, int]  # (device path, size in bytes — 0 if unknown, mtime — 0 if unknown)


#: Categories a presentation run leaves out entirely (music is bulky and not evidence).
EXCLUDED_BUCKETS = frozenset({"music"})
#: Largest single protected file that is still taken whole; above this it is reported as skipped.
PROTECTED_MAX_BYTES = 2 * 1024**3
_RECORDING_DIRS = ("/recordings/", "/recording/", "/voice", "/call recording", "/callrecord")
_SCREEN_REC = ("/screenshots/", "screenrecord", "screen record", "screen_record")


def _name(device_path: str) -> str:
    return device_path.rsplit("/", 1)[-1].lower()


def is_protected(device_path: str) -> bool:
    """Files that cannot be usefully split and matter too much to drop for want of budget: the
    trash (deleted-but-recoverable media), chat databases and backups. They are taken whole, above
    the caps if need be, and the report says by how much."""
    from .pipeline import _categorise

    name, lower = _name(device_path), device_path.lower()
    return (
        name.startswith((".trashed-", ".pending-"))
        or _categorise(device_path)[0] == "database"
        or "/whatsapp/databases/" in lower
        or bucket_of(device_path) in ("recording", "screen-recording")  # one file each: cannot be split
    )


def _is_chat_backup(device_path: str) -> bool:
    """A daily encrypted chat backup (msgstore-YYYY-MM-DD.N.db.cryptNN). Each is a near-duplicate
    of the previous day's, so only the newest one is protected."""
    return re.match(r"^msgstore.*\.crypt\d+$", _name(device_path)) is not None


def bucket_of(device_path: str) -> str:
    """The category a file is budgeted under: trash, its app (WhatsApp, Telegram), voice or
    screen recording, else its file kind. Audio that is not a recording is ``music``."""
    from .pipeline import _categorise  # late import: pipeline imports this module

    if _name(device_path).startswith((".trashed-", ".pending-")):
        return "trashed"
    category, app = _categorise(device_path)
    lower = device_path.lower()
    if app:
        return app
    if category == "audio":
        return "recording" if any(k in lower for k in _RECORDING_DIRS) else "music"
    if category == "video" and any(k in lower for k in _SCREEN_REC):
        return "screen-recording"
    return category


def apply_caps(
    entries: Iterable[Entry],
    bucket_bytes: int = 0,
    total_bytes: int = 0,
    key: Callable[[str], str] = bucket_of,
) -> tuple[list[str], dict]:
    """Return (selected paths, report). ``0`` for a cap means no cap.

    Protected files (:func:`is_protected`) are taken first and whole, above the caps if need be
    (``overcap_bytes`` says by how much); ordinary files then fill their usual budgets. Excluded
    buckets (music) are never taken."""
    queues: dict[str, list[Entry]] = {}
    for e in entries:
        queues.setdefault(key(e[0]), []).append(e)
    for q in queues.values():
        q.sort(key=lambda e: (-e[2], e[0]))  # newest first, path as the tiebreak

    stats = {
        b: {
            "available_files": len(q),
            "available_bytes": sum(e[1] for e in q),
            "selected_files": 0,
            "selected_bytes": 0,
            "protected_files": 0,
            "protected_bytes": 0,
            "excluded": b in EXCLUDED_BUCKETS,
        }
        for b, q in queues.items()
    }
    bucket_left = {b: bucket_bytes or float("inf") for b in queues}
    total_left = total_bytes or float("inf")
    chosen: list[str] = []

    # 1. protected files: whole, regardless of the caps (up to PROTECTED_MAX_BYTES each)
    newest_backup: dict[str, str] = {}
    for q in queues.values():
        for e in q:  # q is newest-first, so the first chat backup seen in a folder is its newest
            if _is_chat_backup(e[0]):
                newest_backup.setdefault(e[0].rsplit("/", 1)[0], e[0])
    for b in sorted(queues):
        if b in EXCLUDED_BUCKETS:
            continue
        keep: list[Entry] = []
        for e in queues[b]:
            older_backup = _is_chat_backup(e[0]) and newest_backup.get(e[0].rsplit("/", 1)[0]) != e[0]
            if is_protected(e[0]) and not older_backup and e[1] <= PROTECTED_MAX_BYTES:
                chosen.append(e[0])
                stats[b]["selected_files"] += 1
                stats[b]["selected_bytes"] += e[1]
                stats[b]["protected_files"] += 1
                stats[b]["protected_bytes"] += e[1]
                # Deliberately not charged to the budgets: a protected file is the exception to the
                # cap, so the category's ordinary files still get their full allowance.
            else:
                keep.append(e)
        queues[b] = keep

    # 2. everything else: newest first, round-robin between categories, never over a budget
    progressed = True
    while progressed:
        progressed = False
        for b in sorted(queues):
            if b in EXCLUDED_BUCKETS:
                continue
            q = queues[b]
            while q:
                path, size, _ = q[0]
                q.pop(0)
                # Budgets only shrink, so a file that does not fit now never will: drop it for good.
                if size <= bucket_left[b] and size <= total_left:
                    chosen.append(path)
                    bucket_left[b] -= size
                    total_left -= size
                    stats[b]["selected_files"] += 1
                    stats[b]["selected_bytes"] += size
                    progressed = True
                    break

    for s in stats.values():
        s["skipped_files"] = s["available_files"] - s["selected_files"]
        s["skipped_bytes"] = s["available_bytes"] - s["selected_bytes"]
    selected_total = sum(s["selected_bytes"] for s in stats.values())
    bucket_over = sum(max(0, s["selected_bytes"] - bucket_bytes) for s in stats.values()) if bucket_bytes else 0
    total_over = max(0, selected_total - total_bytes) if total_bytes else 0
    report = {
        "total_cap_bytes": total_bytes,
        "bucket_cap_bytes": bucket_bytes,
        "buckets": dict(sorted(stats.items())),
        "overcap_bytes": max(bucket_over, total_over),
        **{
            f"{k}_{u}": sum(s[f"{k}_{u}"] for s in stats.values())
            for k in ("available", "selected", "skipped", "protected")
            for u in ("files", "bytes")
        },
    }
    return chosen, report


#: A capped run looks at recently changed files first and only widens the window while there is
#: still room to fill, so it never has to size every file on a large phone.
WINDOWS: tuple[int | None, ...] = (7, 30, 90, 365, None)
_MIN_STOP_DAYS = 30  # do not stop before this: a quiet week must not hide a category's recent files


def _full(report: dict, bucket_bytes: int, total_bytes: int) -> bool:
    if total_bytes and report["selected_bytes"] >= 0.98 * total_bytes:
        return True
    buckets = report["buckets"]
    # A bucket is full once it has turned a file away (nothing more fits its budget) or is within
    # 10% of the cap.
    return bool(
        bucket_bytes
        and buckets
        and all(b["skipped_files"] > 0 or b["selected_bytes"] >= 0.9 * bucket_bytes for b in buckets.values())
    )


def _add_trash(source, entries: list, seen: set) -> None:
    """The media index hides trashed items, so the deleted-but-recoverable files are listed
    separately (by name) and added; they are protected, so they are always taken."""
    for e in getattr(source, "list_trashed_files", lambda: [])():
        if e[0] not in seen:
            seen.add(e[0])
            entries.append(e)


def select_files(
    source,
    roots: Iterable[str],
    *,
    max_files: int,
    bucket_bytes: int = 0,
    total_bytes: int = 0,
    on_root: Callable[[str, int], None] | None = None,
    now: float | None = None,  # unused here; kept so callers/tests can pin a clock if a source needs one
) -> tuple[list[str], dict | None]:
    """Enumerate ``roots`` on ``source`` and choose what to pull.

    Uncapped, this is the plain listing (de-duplicated, first ``max_files``) and the report is
    ``None``. Capped, it lists recently modified files with sizes, widening the window
    (7, 30, 90, 365 days, then everything) until the caps are filled, applies :func:`apply_caps`,
    and returns its report with ``window_days`` — how far back it had to look (``None`` = all) —
    so the case states that "available" means "scanned", not "on the phone".
    """
    capped = bool(bucket_bytes or total_bytes)
    seen: set[str] = set()
    entries: list[Entry] = []
    if not capped:
        for root in roots:
            found = [(p, 0, 0) for p in source.list_files(root)]
            if found and on_root:
                on_root(root, len(found))
            for e in found:
                if e[0] not in seen:
                    seen.add(e[0])
                    entries.append(e)
        return [e[0] for e in entries][:max_files], None

    roots = list(roots)
    # Preferred: the device's own file index — seconds, not a walk of the whole storage.
    indexed = getattr(source, "list_indexed_files", lambda: None)()
    if indexed:
        prefixes = tuple(r.rstrip("/") + "/" for r in roots)
        for e in indexed:
            if e[0].startswith(prefixes) and e[0] not in seen:
                seen.add(e[0])
                entries.append(e)
        if on_root:
            on_root("media index", len(entries))
        _add_trash(source, entries, seen)
        chosen, report = apply_caps(entries, bucket_bytes, total_bytes)
        report["window_days"] = None
        report["listing"] = "media index"
        return chosen[:max_files], report

    chosen: list[str] = []
    report = {}
    for days in WINDOWS:
        for root in roots:
            found = source.list_files_detailed(root, days)
            if found and on_root:
                on_root(root, len(found))
            for e in found:
                if e[0] not in seen:
                    seen.add(e[0])
                    entries.append(e)
        if days is None:
            _add_trash(source, entries, seen)
        chosen, report = apply_caps(entries, bucket_bytes, total_bytes)
        if days is None or (_full(report, bucket_bytes, total_bytes) and days >= _MIN_STOP_DAYS) or (
            total_bytes and report["selected_bytes"] >= 0.98 * total_bytes
        ):
            report["window_days"] = days
            break
    return chosen[:max_files], report
