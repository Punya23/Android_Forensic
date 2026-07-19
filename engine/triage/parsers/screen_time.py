"""Screen time / device usage history parser for eRakshak.

Parses screen on/off events and app foreground usage from:
  * ``adb shell dumpsys power``        -- screen wakefulness state transitions
  * ``adb shell dumpsys batterystats`` -- per-app foreground time
  * ``adb shell dumpsys usagestats``   -- per-app usage statistics

Forensic value:
* Proves the device was actively being used at specific times.
* Shows usage patterns (late-night, unusual bursts) that contradict alibis.
* Per-app foreground time identifies the most used apps.
* Correlates with notification, Bluetooth, and cell-tower timelines to
  confirm who was using the device and when.

All functions operate entirely without root -- dumpsys is accessible to the
shell UID via ADB.
"""
from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

from ..adb import Adb
from ..models import TimelineEvent
from ..config import Confidence

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Hours considered "late night" for pattern detection
_LATE_NIGHT_START = 23   # 11 PM
_LATE_NIGHT_END   = 5    # 5 AM

# A "spike" session is one longer than this many minutes
_SPIKE_SESSION_MINUTES = 90

# Known suspicious app categories (fragments matched against package name)
_SUSPICIOUS_PKG_FRAGMENTS: List[str] = [
    "vpn", "proxy", "tor", "anon", "hide", "cleaner", "eraser",
    "vault", "secret", "encrypt", "ghost", "incognito",
]

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_epoch(raw: str) -> Optional[int]:
    """Parse an epoch value from a raw string (ms or s).  Returns seconds."""
    raw = raw.strip()
    if re.fullmatch(r"\d{10,13}", raw):
        epoch_val = int(raw)
        if epoch_val > 9_999_999_999:
            epoch_val //= 1000
        return epoch_val
    return None


def _epoch_to_iso(epoch_s: int) -> str:
    """Convert a Unix timestamp (seconds) to ISO-8601 UTC."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch_s))


def _parse_timestamp(raw: str) -> Optional[str]:
    """Convert various timestamp formats to ISO-8601 UTC.

    Handles:
    * Milliseconds since epoch  (e.g. 1751826000000)
    * Seconds since epoch       (e.g. 1751826000)
    * ISO-like date string      (e.g. 2025-07-06 14:23:01)
    * Android log format        (e.g. 07-06 14:23:01.456)
    """
    if not raw:
        return None
    raw = raw.strip()

    # Numeric epoch (ms or s)
    epoch = _parse_epoch(raw)
    if epoch is not None:
        return _epoch_to_iso(epoch)

    # ISO-like: YYYY-MM-DD HH:MM:SS
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})[T\s](\d{2}):(\d{2}):(\d{2})", raw)
    if m:
        yr, mo, dy, hr, mn, sc = m.groups()
        return f"{yr}-{mo}-{dy}T{hr}:{mn}:{sc}Z"

    # Android log format: MM-DD HH:MM:SS.mmm
    m2 = re.match(r"(\d{1,2})-(\d{1,2})\s+(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?", raw)
    if m2:
        month, day, hour, minute, sec = m2.groups()
        year = time.gmtime().tm_year
        return (
            f"{year}-{int(month):02d}-{int(day):02d}"
            f"T{hour}:{minute}:{sec}Z"
        )

    return None


def _is_suspicious_app(package: str) -> bool:
    """Return True if the package name contains a suspicious keyword fragment."""
    lower = package.lower()
    return any(frag in lower for frag in _SUSPICIOUS_PKG_FRAGMENTS)


def _ts_to_epoch(iso_ts: str) -> float:
    """Convert ISO-8601 UTC string to a float Unix timestamp."""
    import calendar
    t = time.strptime(iso_ts, "%Y-%m-%dT%H:%M:%SZ")
    return float(calendar.timegm(t))


# ---------------------------------------------------------------------------
# Screen on/off parsing -- dumpsys power
# ---------------------------------------------------------------------------

_RE_SCREEN_STATE = re.compile(
    r"(?:mWakefulness|wakefulness)\s*[=:]\s*(Awake|Asleep|Dozing|Dreaming)",
    re.IGNORECASE,
)
_RE_SCREEN_TIMESTAMP = re.compile(
    r"(?:lastSleepTime|lastWakeTime|lastUserActivityTime|mLastPowerOn)\s*[=:]\s*(\d+)",
    re.IGNORECASE,
)


def parse_screen_time(dumpsys_output: str) -> List[Dict[str, Any]]:
    """Parse screen on/off timestamps from ``dumpsys power`` output.

    Handles multiple dumpsys output formats across AOSP versions.

    Each returned dict contains:

    * ``event_type``   -- "ON" | "OFF" | "DOZING"
    * ``timestamp``    -- ISO-8601 UTC string (or empty if unparseable)
    * ``raw_epoch_ms`` -- raw millisecond epoch from the dump (or -1)
    * ``wakefulness``  -- raw wakefulness state string
    * ``source``       -- which field produced this event
    """
    if not dumpsys_output:
        return []

    events: List[Dict[str, Any]] = []

    # Extract wakefulness state
    for match in _RE_SCREEN_STATE.finditer(dumpsys_output):
        state = match.group(1).capitalize()
        if state == "Awake":
            event_type = "ON"
        elif state == "Dozing":
            event_type = "DOZING"
        else:
            event_type = "OFF"
        events.append({
            "event_type":   event_type,
            "timestamp":    "",
            "raw_epoch_ms": -1,
            "wakefulness":  state,
            "source":       "wakefulness_state",
        })

    # Extract timestamped last-sleep / last-wake fields
    for match in _RE_SCREEN_TIMESTAMP.finditer(dumpsys_output):
        ctx = dumpsys_output[max(0, match.start() - 40): match.start()].strip()
        raw_ms = int(match.group(1))
        ts = _parse_timestamp(str(raw_ms)) or ""
        is_wake = any(k in ctx.lower() for k in ("wake", "poweron", "useractivity"))
        event_type = "ON" if is_wake else "OFF"
        events.append({
            "event_type":   event_type,
            "timestamp":    ts,
            "raw_epoch_ms": raw_ms,
            "wakefulness":  "",
            "source":       "power_timestamp",
        })

    # Parse history lines for screen-state change markers
    _RE_HIST = re.compile(
        r"([+-])\s*\w+\s+(?:screen-on|power-on|wake-lock|SCREEN_ON|SCREEN_OFF)",
        re.IGNORECASE,
    )
    for match in _RE_HIST.finditer(dumpsys_output):
        sign = match.group(1)
        event_type = "ON" if sign == "+" else "OFF"
        line_start = dumpsys_output.rfind("\n", 0, match.start()) + 1
        line = dumpsys_output[line_start: dumpsys_output.find("\n", match.end())]
        ts_m = re.search(r"(\d{10,13})", line)
        raw_ms = int(ts_m.group(1)) if ts_m else -1
        ts = _parse_timestamp(str(raw_ms)) if raw_ms > 0 else ""
        events.append({
            "event_type":   event_type,
            "timestamp":    ts,
            "raw_epoch_ms": raw_ms,
            "wakefulness":  "",
            "source":       "history_line",
        })

    return events


def get_screen_time(adb: Adb) -> List[Dict[str, Any]]:
    """Execute ``adb shell dumpsys power`` and parse screen on/off events.

    Returns a list of screen-event dicts as produced by :func:`parse_screen_time`.
    Returns an empty list on ADB failure.
    """
    result = adb.shell("dumpsys power")
    if not result.ok or not result.stdout.strip():
        return []
    return parse_screen_time(result.stdout)


# ---------------------------------------------------------------------------
# Battery stats / app usage -- dumpsys batterystats + usagestats
# ---------------------------------------------------------------------------

_RE_FGTIME     = re.compile(r"fg\s*(?:time|Time)\s*[=:]\s*(\d+)", re.IGNORECASE)
_RE_FGTIME_ALT = re.compile(r"foreground(?:Time)?\s*[=:]\s*(\d+)", re.IGNORECASE)
_RE_BATT_PCT   = re.compile(r"(?:start|begin)\s+level\s*[=:]\s*(\d+)%?", re.IGNORECASE)
_RE_USAGE_PKG  = re.compile(
    r"package\s*[=:]\s*\"?([a-z][a-zA-Z0-9_.]+)\"?", re.IGNORECASE
)
_RE_USAGE_FG   = re.compile(
    r"(?:totalTimeInForeground|foreground(?:Time)?)\s*[=:]\s*(\d+)", re.IGNORECASE
)
_RE_USAGE_LAST = re.compile(r"lastTimeUsed\s*[=:]\s*(\d+)", re.IGNORECASE)


def parse_battery_stats(batterystats_output: str) -> List[Dict[str, Any]]:
    """Parse app usage from ``dumpsys batterystats`` output.

    Each returned dict contains:

    * ``package``          -- app package name
    * ``foreground_ms``    -- foreground time in milliseconds
    * ``foreground_min``   -- foreground time in minutes (rounded to 2 dp)
    * ``battery_pct_used`` -- estimated battery percentage consumed (or -1)
    * ``is_suspicious``    -- True if package contains a suspicious keyword
    """
    if not batterystats_output:
        return []

    apps: Dict[str, Dict[str, Any]] = {}

    for m in re.finditer(
        r"\b([a-z][a-zA-Z0-9_]*(?:\.[a-zA-Z0-9_]+){1,})\b",
        batterystats_output,
    ):
        pkg = m.group(1)
        if "." not in pkg or len(pkg) <= 5:
            continue
        window = batterystats_output[m.start(): m.start() + 500]
        fg_m = _RE_FGTIME.search(window) or _RE_FGTIME_ALT.search(window)
        if not fg_m:
            continue
        fg_ms = int(fg_m.group(1))
        if fg_ms <= 0:
            continue

        batt_window = batterystats_output[max(0, m.start() - 200): m.start() + 200]
        batt_m = _RE_BATT_PCT.search(batt_window)
        batt_pct = int(batt_m.group(1)) if batt_m else -1

        if pkg not in apps or fg_ms > apps[pkg]["foreground_ms"]:
            apps[pkg] = {
                "package":          pkg,
                "foreground_ms":    fg_ms,
                "foreground_min":   round(fg_ms / 60_000, 2),
                "battery_pct_used": batt_pct,
                "is_suspicious":    _is_suspicious_app(pkg),
            }

    return sorted(apps.values(), key=lambda x: x["foreground_ms"], reverse=True)


def _parse_usagestats(usagestats_output: str) -> List[Dict[str, Any]]:
    """Parse ``dumpsys usagestats`` output into per-app usage dicts (internal)."""
    apps: Dict[str, Dict[str, Any]] = {}
    current_pkg: Optional[str] = None

    for line in usagestats_output.splitlines():
        pkg_m = _RE_USAGE_PKG.search(line)
        if pkg_m:
            current_pkg = pkg_m.group(1)

        if current_pkg is None:
            continue

        fg_m = _RE_USAGE_FG.search(line)
        if fg_m:
            fg_ms = int(fg_m.group(1))
            lu_m = _RE_USAGE_LAST.search(line)
            raw_lu = lu_m.group(1) if lu_m else ""
            last_used = _parse_timestamp(raw_lu) or ""

            entry = apps.get(current_pkg, {
                "package":          current_pkg,
                "foreground_ms":    0,
                "foreground_min":   0.0,
                "battery_pct_used": -1,
                "is_suspicious":    _is_suspicious_app(current_pkg),
                "last_used":        "",
            })
            if fg_ms > entry["foreground_ms"]:
                entry["foreground_ms"]  = fg_ms
                entry["foreground_min"] = round(fg_ms / 60_000, 2)
            if last_used:
                entry["last_used"] = last_used
            apps[current_pkg] = entry

    return list(apps.values())


def get_app_usage(adb: Adb) -> List[Dict[str, Any]]:
    """Collect app usage from ``dumpsys batterystats`` and ``dumpsys usagestats``.

    Merges both sources for a richer view of per-app foreground time.
    Returns an empty list on ADB failure.
    """
    combined: Dict[str, Dict[str, Any]] = {}

    bs_result = adb.shell("dumpsys batterystats")
    if bs_result.ok and bs_result.stdout.strip():
        for entry in parse_battery_stats(bs_result.stdout):
            combined[entry["package"]] = entry

    us_result = adb.shell("dumpsys usagestats")
    if us_result.ok and us_result.stdout.strip():
        for entry in _parse_usagestats(us_result.stdout):
            pkg = entry["package"]
            if pkg in combined:
                if entry["foreground_ms"] > combined[pkg]["foreground_ms"]:
                    combined[pkg]["foreground_ms"]  = entry["foreground_ms"]
                    combined[pkg]["foreground_min"] = entry["foreground_min"]
                if entry.get("last_used"):
                    combined[pkg]["last_used"] = entry["last_used"]
            else:
                combined[pkg] = entry

    return sorted(combined.values(), key=lambda x: x["foreground_ms"], reverse=True)


# ---------------------------------------------------------------------------
# Timeline builder
# ---------------------------------------------------------------------------

def build_screen_timeline(screen_events: List[Dict]) -> List[TimelineEvent]:
    """Convert screen on/off events into :class:`TimelineEvent` objects.

    Pairs ON/OFF events to annotate session durations.
    Events without a parseable timestamp are skipped.
    """
    events: List[TimelineEvent] = []

    for ev in screen_events:
        ts = ev.get("timestamp", "")
        if not ts:
            continue
        etype      = ev.get("event_type", "?")
        wakefulness = ev.get("wakefulness", "")
        w_part     = f" [{wakefulness}]" if wakefulness else ""
        summary    = f"[Screen] {etype}{w_part}"
        events.append(TimelineEvent(
            timestamp  = ts,
            kind       = "screen",
            summary    = summary,
            confidence = Confidence.LIVE,
            ref        = etype,
        ))

    events.sort(key=lambda e: e.timestamp)

    # Annotate ON events with session duration
    on_stack: List[TimelineEvent] = []
    for ev in events:
        if ev.ref == "ON":
            on_stack.append(ev)
        elif ev.ref == "OFF" and on_stack:
            on_ev = on_stack.pop()
            try:
                dur_s = _ts_to_epoch(ev.timestamp) - _ts_to_epoch(on_ev.timestamp)
                if 0 < dur_s < 86_400:
                    on_ev.summary += f" -- session {round(dur_s / 60, 1)} min"
            except Exception:
                pass

    return events


# ---------------------------------------------------------------------------
# Summary statistics
# ---------------------------------------------------------------------------

def get_screen_time_summary(
    screen_events: List[Dict],
    app_usage: List[Dict],
) -> Dict[str, Any]:
    """Calculate aggregate screen-time statistics.

    Returned dict keys:

    * ``total_sessions``          -- number of screen-on sessions detected
    * ``total_screen_time_min``   -- estimated total screen-on minutes
    * ``most_used_apps``          -- top-5 apps by foreground time
    * ``most_active_hours``       -- hours of day sorted by session count (top 5)
    * ``daily_average_min``       -- rough average screen-on minutes per session
    * ``suspicious_apps``         -- packages flagged as suspicious
    """
    sessions: List[float] = []
    hour_counts: Dict[int, int] = {}
    on_epoch: Optional[float] = None

    for ev in sorted(screen_events, key=lambda e: e.get("timestamp", "")):
        ts    = ev.get("timestamp", "")
        etype = ev.get("event_type", "")
        if not ts:
            continue
        try:
            epoch = _ts_to_epoch(ts)
        except Exception:
            continue

        if etype == "ON":
            on_epoch = epoch
            hour = int(time.gmtime(int(epoch)).tm_hour)
            hour_counts[hour] = hour_counts.get(hour, 0) + 1
        elif etype == "OFF" and on_epoch is not None:
            dur_s = epoch - on_epoch
            if 0 < dur_s < 86_400:
                sessions.append(dur_s / 60)
            on_epoch = None

    total_min   = round(sum(sessions), 1)
    most_active = sorted(hour_counts, key=lambda h: hour_counts[h], reverse=True)
    daily_avg   = round(total_min / max(len(sessions), 1), 1)

    top_apps   = sorted(app_usage, key=lambda a: a.get("foreground_ms", 0), reverse=True)[:5]
    suspicious = [a for a in app_usage if a.get("is_suspicious")]

    return {
        "total_sessions":        len(sessions),
        "total_screen_time_min": total_min,
        "most_used_apps":        [a["package"] for a in top_apps],
        "most_active_hours":     most_active[:5],
        "daily_average_min":     daily_avg,
        "suspicious_apps":       [a["package"] for a in suspicious],
    }


# ---------------------------------------------------------------------------
# Pattern detection
# ---------------------------------------------------------------------------

def detect_usage_patterns(usage: List[Dict]) -> List[Dict]:
    """Detect anomalous or forensically significant usage patterns.

    Each returned dict contains:

    * ``pattern``     -- pattern label (e.g. "late_night_usage")
    * ``description`` -- human-readable explanation
    * ``severity``    -- "info" | "warn" | "critical"
    * ``evidence``    -- supporting data (package name, foreground time, etc.)
    """
    patterns: List[Dict] = []

    for app in usage:
        pkg = app.get("package", "")
        fg_min = app.get("foreground_min", 0.0)

        # Flag suspicious app names with meaningful usage
        if app.get("is_suspicious") and fg_min > 1:
            patterns.append({
                "pattern":     "suspicious_app_usage",
                "description": (
                    f"App '{pkg}' contains forensically relevant keywords "
                    f"(VPN/proxy/vault/etc.) and has {fg_min} min foreground time."
                ),
                "severity":    "warn",
                "evidence":    {"package": pkg, "foreground_min": fg_min},
            })

        # Flag unusually long sessions
        if fg_min > _SPIKE_SESSION_MINUTES:
            patterns.append({
                "pattern":     "high_usage_spike",
                "description": (
                    f"App '{pkg}' had an unusually long foreground session "
                    f"of {fg_min} min -- may indicate intensive or automated use."
                ),
                "severity":    "info",
                "evidence":    {"package": pkg, "foreground_min": fg_min},
            })

    return patterns
