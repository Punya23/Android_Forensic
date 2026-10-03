"""Size caps for a demo-sized acquisition.

A phone holding 128 GB cannot be pulled in a ten-minute demonstration, so a run can be capped
overall (``total_bytes``) and per category (``bucket_bytes``). Selection is deterministic: within
a category the newest files are taken first, and the overall budget is shared between categories
round-robin so photos cannot starve WhatsApp. A file that does not fit what is left is skipped,
never truncated. The returned report states exactly what was left behind, because a capped
acquisition is a partial one and the case must say so.
"""

from __future__ import annotations

from typing import Callable, Iterable

Entry = tuple[str, int, int]  # (device path, size in bytes — 0 if unknown, mtime — 0 if unknown)


def bucket_of(device_path: str) -> str:
    """The category a file is budgeted under: its app (WhatsApp, Telegram) else its file kind."""
    from .pipeline import _categorise  # late import: pipeline imports this module

    category, app = _categorise(device_path)
    return app or category


def apply_caps(
    entries: Iterable[Entry],
    bucket_bytes: int = 0,
    total_bytes: int = 0,
    key: Callable[[str], str] = bucket_of,
) -> tuple[list[str], dict]:
    """Return (selected paths, report). ``0`` for a cap means no cap."""
    queues: dict[str, list[Entry]] = {}
    for e in entries:
        queues.setdefault(key(e[0]), []).append(e)
    for q in queues.values():
        q.sort(key=lambda e: (-e[2], e[0]))  # newest first, path as the tiebreak

    stats = {
        b: {"available_files": len(q), "available_bytes": sum(e[1] for e in q), "selected_files": 0, "selected_bytes": 0}
        for b, q in queues.items()
    }
    bucket_left = {b: bucket_bytes or float("inf") for b in queues}
    total_left = total_bytes or float("inf")
    chosen: list[str] = []

    progressed = True
    while progressed:
        progressed = False
        for b in sorted(queues):
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
    report = {
        "total_cap_bytes": total_bytes,
        "bucket_cap_bytes": bucket_bytes,
        "buckets": dict(sorted(stats.items())),
        **{
            f"{k}_{u}": sum(s[f"{k}_{u}"] for s in stats.values())
            for k in ("available", "selected", "skipped")
            for u in ("files", "bytes")
        },
    }
    return chosen, report


def select_files(
    source,
    roots: Iterable[str],
    *,
    max_files: int,
    bucket_bytes: int = 0,
    total_bytes: int = 0,
    on_root: Callable[[str, int], None] | None = None,
) -> tuple[list[str], dict | None]:
    """Enumerate ``roots`` on ``source`` and choose what to pull.

    Uncapped, this is the plain listing (de-duplicated, first ``max_files``) and the report is
    ``None``. With a cap it lists sizes too, applies :func:`apply_caps`, and returns its report.
    """
    capped = bool(bucket_bytes or total_bytes)
    seen: set[str] = set()
    entries: list[Entry] = []
    for root in roots:
        found = source.list_files_detailed(root) if capped else [(p, 0, 0) for p in source.list_files(root)]
        if found and on_root:
            on_root(root, len(found))
        for e in found:
            if e[0] not in seen:
                seen.add(e[0])
                entries.append(e)
    if not capped:
        return [e[0] for e in entries][:max_files], None
    chosen, report = apply_caps(entries, bucket_bytes, total_bytes)
    return chosen[:max_files], report
