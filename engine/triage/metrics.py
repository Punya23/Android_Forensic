"""Performance metrics tracking for triage acquisitions.

Provides lightweight, thread-safe timing utilities and throughput calculations so the
examiner (and the dashboard) can see at a glance how fast the acquisition is running and
how long it is likely to take.

All state is module-level but guarded by a lock, so multiple pipeline calls from the same
process don't interfere with each other.  Call :func:`reset` between runs.

Usage example
-------------
::

    from triage.metrics import start_timer, stop_timer, track_stage_time
    from triage.metrics import get_performance_report, display_speed_metrics

    t0 = start_timer()
    # … do work …
    elapsed = stop_timer(t0)
    track_stage_time("system", elapsed)

    print(display_speed_metrics())
    report = get_performance_report()
"""

from __future__ import annotations

import threading
import time
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Module-level state (reset between runs)
# ---------------------------------------------------------------------------

_lock = threading.Lock()

_stage_times: Dict[str, List[float]] = {}  # stage → list of elapsed seconds
_bytes_total: int = 0  # cumulative bytes processed
_run_start: Optional[float] = None  # monotonic timestamp of run start


# ---------------------------------------------------------------------------
# Public API — timers
# ---------------------------------------------------------------------------


def start_timer() -> float:
    """Return the current monotonic clock value to start a timer.

    Returns
    -------
    float
        ``time.monotonic()`` value.
    """
    return time.monotonic()


def stop_timer(start_time: float) -> float:
    """Return elapsed seconds since *start_time*.

    Parameters
    ----------
    start_time:
        Value previously returned by :func:`start_timer`.

    Returns
    -------
    float
        Elapsed seconds (always ≥ 0).
    """
    return max(time.monotonic() - start_time, 0.0)


# ---------------------------------------------------------------------------
# Public API — tracking
# ---------------------------------------------------------------------------


def reset() -> None:
    """Clear all accumulated metrics and start a fresh run timer.

    Call this at the beginning of each acquisition run to avoid stale data
    from a previous run appearing in the report.
    """
    global _bytes_total, _run_start
    with _lock:
        _stage_times.clear()
        _bytes_total = 0
        _run_start = time.monotonic()


def track_stage_time(stage: str, elapsed: float) -> None:
    """Record *elapsed* seconds for *stage*.

    A stage may be tracked multiple times (e.g. per-file timings); all values
    are accumulated and the report shows the sum and average.

    Parameters
    ----------
    stage:
        Human-readable stage name, e.g. ``"pull"``, ``"system"``, ``"parse"``.
    elapsed:
        Seconds spent in this stage call.
    """
    with _lock:
        _stage_times.setdefault(stage, []).append(max(elapsed, 0.0))


def add_bytes(n: int) -> None:
    """Add *n* bytes to the cumulative bytes-processed counter.

    Parameters
    ----------
    n:
        Number of bytes processed (e.g. a pulled file's size).
    """
    global _bytes_total
    with _lock:
        _bytes_total += max(n, 0)


# ---------------------------------------------------------------------------
# Public API — reporting
# ---------------------------------------------------------------------------


def get_performance_report() -> Dict:
    """Generate a performance summary for the current (or last) run.

    Returns
    -------
    Dict
        Keys:

        * ``total_elapsed_s`` — wall-clock seconds since :func:`reset` was called.
        * ``stages`` — dict mapping stage name → ``{count, total_s, avg_s}``.
        * ``bytes_processed`` — cumulative bytes.
        * ``mb_per_min`` — throughput in megabytes per minute.
        * ``eta_s`` — estimated seconds remaining (``None`` if unknown).
    """
    with _lock:
        stages_snapshot = {k: list(v) for k, v in _stage_times.items()}
        bytes_snap = _bytes_total
        run_start = _run_start

    total_elapsed = time.monotonic() - run_start if run_start is not None else 0.0
    mb = bytes_snap / (1024 * 1024)
    mb_per_min = (mb / total_elapsed) * 60.0 if total_elapsed > 0 else 0.0

    stages_report: Dict[str, Dict] = {}
    for name, samples in stages_snapshot.items():
        total_s = sum(samples)
        stages_report[name] = {
            "count": len(samples),
            "total_s": round(total_s, 3),
            "avg_s": round(total_s / len(samples), 3) if samples else 0.0,
        }

    return {
        "total_elapsed_s": round(total_elapsed, 2),
        "stages": stages_report,
        "bytes_processed": bytes_snap,
        "mb_per_min": round(mb_per_min, 1),
        "eta_s": None,  # populated by display_speed_metrics when we know total files
    }


def display_speed_metrics(
    files_done: int = 0,
    files_total: int = 0,
) -> str:
    """Return a human-readable one-line speed summary.

    Parameters
    ----------
    files_done:
        Number of files pulled so far.
    files_total:
        Total number of files to pull (0 = unknown).

    Returns
    -------
    str
        E.g. ``"Throughput: 12.3 MB/min | Files: 45/200 | ETA: ~1m 22s"``.
    """
    report = get_performance_report()
    elapsed = report["total_elapsed_s"]
    mb_min = report["mb_per_min"]

    parts: List[str] = [f"Throughput: {mb_min:.1f} MB/min"]

    if files_total > 0:
        parts.append(f"Files: {files_done}/{files_total}")
        remaining = files_total - files_done
        # Estimate ETA from average per-file time in the 'pull' stage.
        pull_stats = report["stages"].get("pull", {})
        avg_s = pull_stats.get("avg_s", 0.0)
        if avg_s > 0 and remaining > 0:
            eta_s = int(avg_s * remaining)
            mins, secs = divmod(eta_s, 60)
            eta_str = f"{mins}m {secs:02d}s" if mins else f"{secs}s"
            parts.append(f"ETA: ~{eta_str}")
    elif elapsed > 0:
        parts.append(f"Elapsed: {elapsed:.0f}s")

    return " | ".join(parts)
