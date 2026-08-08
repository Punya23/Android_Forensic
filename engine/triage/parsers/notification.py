"""Notification history parser for SNAGR.

Parses Android notification history from ``adb shell dumpsys notification --history``.

This captures notifications from ALL apps (WhatsApp, Telegram, SMS, Signal,
Instagram, etc.) even if the app's private data is encrypted or deleted.
The notification service maintains a circular ring buffer (typically the last
50-100 notifications) that survives across app clears and reinstalls.

Forensic value:
* Proves app activity and communication even with encrypted app databases.
* Surfaces message previews, sender names, and timestamps that the app may
  have since deleted.
* Correlates with cell-tower / Bluetooth timelines to establish presence.
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

_MAX_NOTIFICATIONS = 500

# Map package name fragments -> friendly display name.
_COMM_APPS: Dict[str, str] = {
    "com.whatsapp": "WhatsApp",
    "com.whatsapp.w4b": "WhatsApp Business",
    "org.telegram.messenger": "Telegram",
    "org.thoughtcrime.securesms": "Signal",
    "com.instagram.android": "Instagram",
    "com.facebook.katana": "Facebook",
    "com.facebook.orca": "Messenger",
    "com.twitter.android": "Twitter/X",
    "com.snapchat.android": "Snapchat",
    "com.discord": "Discord",
    "com.viber.voip": "Viber",
    "com.skype.raider": "Skype",
    "com.google.android.apps.messaging": "Google Messages",
    "com.samsung.android.messaging": "Samsung Messages",
    "com.android.mms": "MMS",
    "com.google.android.gm": "Gmail",
    "com.microsoft.teams": "Microsoft Teams",
    "com.slack": "Slack",
    "jp.naver.line.android": "LINE",
    "com.tencent.mm": "WeChat",
    "com.kakao.talk": "KakaoTalk",
    "com.truecaller": "Truecaller",
}

# Importance-level -> human-readable priority mapping (Android O+)
_IMPORTANCE_MAP: Dict[str, str] = {
    "0": "none",
    "1": "min",
    "2": "low",
    "3": "default",
    "4": "high",
    "5": "max",
    "none": "none",
    "min": "min",
    "low": "low",
    "default": "default",
    "high": "high",
    "max": "max",
    "urgent": "max",
}

# ---------------------------------------------------------------------------
# Regex patterns for field extraction
# ---------------------------------------------------------------------------

_RE_PACKAGE = re.compile(r"(?:^|\s)pkg\s*=\s*(\S+)", re.IGNORECASE)
_RE_PACKAGE_ALT = re.compile(r"Package\s*:\s*(\S+)", re.IGNORECASE)
_RE_KEY = re.compile(r"NotificationKey\s*:\s*(.+)", re.IGNORECASE)
_RE_KEY_ALT = re.compile(r"\bkey\s*=\s*(.+)", re.IGNORECASE)
_RE_POST_TIME = re.compile(r"PostTime\s*:\s*(\S+)", re.IGNORECASE)
_RE_POST_ALT = re.compile(r"postTime\s*=\s*(\S+)", re.IGNORECASE)
_RE_TITLE = re.compile(r"Title\s*:\s*(.+)", re.IGNORECASE)
_RE_TITLE_ALT = re.compile(r"android\.title\s*=\s*(.+)", re.IGNORECASE)
_RE_TEXT = re.compile(r"(?:Text|Body)\s*:\s*(.+)", re.IGNORECASE)
_RE_TEXT_ALT = re.compile(r"android\.text\s*=\s*(.+)", re.IGNORECASE)
_RE_PRIORITY = re.compile(r"Priority\s*:\s*(\S+)", re.IGNORECASE)
_RE_PRIORITY_ALT = re.compile(r"importance\s*=\s*(\w+)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _friendly_name(package: str) -> str:
    """Return a human-readable app name for a given package, or the package itself."""
    for prefix, name in _COMM_APPS.items():
        if package.startswith(prefix):
            return name
    # Generic: capitalise the last segment of the package name.
    parts = package.split(".")
    return parts[-1].replace("_", " ").title() if parts else package


def _is_comm_app(package: str) -> bool:
    """Return True if the package belongs to a known communication app."""
    return any(package.startswith(p) for p in _COMM_APPS)


def _normalise_priority(raw: str) -> str:
    """Map raw priority / importance value to a canonical label."""
    cleaned = raw.strip().lower()
    return _IMPORTANCE_MAP.get(cleaned, cleaned)


def _split_notification_blocks(text: str) -> List[str]:
    """Split the raw dumpsys output into per-notification blocks.

    Different Android versions use different delimiters; we try several
    heuristics in priority order:

    1. Android 11+ numbered entries (``  0: pkg=…``)
    2. Blank-line-separated stanzas (Android 9/10)
    3. Lines starting with ``Package:`` (Android 9 legacy)
    4. ``pkg=`` token fallback
    """
    # Android 11+ uses numbered entries like "  0: pkg=..." or "  1: pkg=..."
    numbered = re.split(r"\n(?=\s{0,4}\d+:\s)", text)
    if len(numbered) > 1:
        return [b for b in numbered if b.strip()]

    # Android 9 legacy: "Package:" at line start is the block boundary.
    # Try this BEFORE blank-line split because blank lines appear inside
    # multi-notification legacy output as well.
    pkg_header = re.split(r"(?=^\s*Package\s*:)", text, flags=re.MULTILINE)
    if len(pkg_header) > 1:
        return [b for b in pkg_header if b.strip()]

    # Blank-line-separated blocks (some intermediate Android versions)
    double_newline = re.split(r"\n\s*\n", text)
    if len(double_newline) > 1:
        return [b for b in double_newline if b.strip()]

    # Last resort: split on any "pkg=" occurrence
    pkg_split = re.split(r"(?=pkg=)", text)
    return [b for b in pkg_split if b.strip()] if pkg_split else [text]


# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------


def parse_notification_timestamp(raw_time: str) -> Optional[str]:
    """Convert various Android timestamp formats to ISO-8601 UTC.

    Handles:
    * Milliseconds since epoch  (e.g. ``1751826000000``)
    * Seconds since epoch       (e.g. ``1751826000``)
    * Android log format        (e.g. ``07-06 14:23:01.456``)
    * ISO-like strings          (e.g. ``2025-07-06 14:23:01``)
    """
    if not raw_time:
        return None

    raw = raw_time.strip()

    # --- numeric epoch (ms or s) ---
    if re.fullmatch(r"\d{10,13}", raw):
        epoch_val = int(raw)
        # 13-digit -> milliseconds; 10-digit -> seconds
        if epoch_val > 9_999_999_999:
            epoch_val //= 1000
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch_val))

    # --- Android log format: MM-DD HH:MM:SS.mmm ---
    m = re.match(
        r"(\d{1,2})-(\d{1,2})\s+(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?",
        raw,
    )
    if m:
        month, day, hour, minute, sec = m.groups()
        year = time.gmtime().tm_year
        return f"{year}-{int(month):02d}-{int(day):02d}" f"T{hour}:{minute}:{sec}Z"

    # --- ISO-like: YYYY-MM-DD HH:MM:SS ---
    m2 = re.match(
        r"(\d{4})-(\d{2})-(\d{2})[T\s](\d{2}):(\d{2}):(\d{2})",
        raw,
    )
    if m2:
        yr, mo, dy, hr, mn, sc = m2.groups()
        return f"{yr}-{mo}-{dy}T{hr}:{mn}:{sc}Z"

    return None


# ---------------------------------------------------------------------------
# ADB acquisition
# ---------------------------------------------------------------------------


def get_notification_history(adb: Adb) -> str:
    """Execute ``adb shell dumpsys notification --history`` and return stdout.

    Returns an empty string on any error (missing adb, timeout, no device).
    Falls back to plain ``dumpsys notification`` on older AOSP builds that
    do not support the ``--history`` flag.
    """
    result = adb.shell("dumpsys notification --history")
    if result.ok and result.stdout.strip():
        return result.stdout
    # Fallback: try without --history flag
    result2 = adb.shell("dumpsys notification")
    return result2.stdout if result2.ok else ""


# ---------------------------------------------------------------------------
# Core parser
# ---------------------------------------------------------------------------


def parse_notification_history(adb_output: str) -> List[Dict[str, Any]]:
    """Parse raw ``dumpsys notification --history`` output into structured rows.

    Each returned dict contains:

    * ``package``   — app package name (e.g. ``com.whatsapp``)
    * ``app_name``  — friendly display name (e.g. ``WhatsApp``)
    * ``key``       — notification key (unique identifier within the OS)
    * ``timestamp`` — ISO-8601 UTC string (empty if unparseable)
    * ``title``     — notification title text
    * ``text``      — notification body text
    * ``priority``  — canonical priority label (none/min/low/default/high/max)
    * ``is_comm``   — True if the source is a known communication app

    Results are deduplicated by notification key and capped at
    ``_MAX_NOTIFICATIONS`` entries.
    """
    if not adb_output:
        return []

    notifications: List[Dict[str, Any]] = []
    seen_keys: set = set()

    blocks = _split_notification_blocks(adb_output)

    for block in blocks[:_MAX_NOTIFICATIONS]:
        if not block.strip():
            continue

        # --- package ---
        pkg_match = _RE_PACKAGE.search(block) or _RE_PACKAGE_ALT.search(block)
        if not pkg_match:
            continue
        package = pkg_match.group(1).strip().rstrip(",")
        if not package:
            continue

        # --- notification key ---
        key_match = _RE_KEY.search(block) or _RE_KEY_ALT.search(block)
        key = key_match.group(1).strip() if key_match else ""

        # Deduplicate by key; synthesise one when absent.
        dedup = key if key else f"{package}_{len(notifications)}"
        if dedup in seen_keys:
            continue
        seen_keys.add(dedup)

        # --- timestamp ---
        time_match = _RE_POST_TIME.search(block) or _RE_POST_ALT.search(block)
        raw_time = time_match.group(1).strip() if time_match else ""
        timestamp = parse_notification_timestamp(raw_time) or ""

        # --- title ---
        title_match = _RE_TITLE.search(block) or _RE_TITLE_ALT.search(block)
        title = title_match.group(1).strip() if title_match else ""

        # --- text ---
        text_match = _RE_TEXT.search(block) or _RE_TEXT_ALT.search(block)
        text = text_match.group(1).strip() if text_match else ""

        # --- priority ---
        pri_match = _RE_PRIORITY.search(block) or _RE_PRIORITY_ALT.search(block)
        priority = _normalise_priority(pri_match.group(1)) if pri_match else "default"

        notifications.append(
            {
                "package": package,
                "app_name": _friendly_name(package),
                "key": key,
                "timestamp": timestamp,
                "title": title,
                "text": text,
                "priority": priority,
                "is_comm": _is_comm_app(package),
            }
        )

    return notifications


# ---------------------------------------------------------------------------
# Timeline builder
# ---------------------------------------------------------------------------


def build_notification_timeline(notifications: List[Dict]) -> List[TimelineEvent]:
    """Convert parsed notification dicts into :class:`TimelineEvent` objects.

    Notifications without a parseable timestamp are skipped (they cannot be
    anchored on the timeline).  Events are returned in the same order as the
    input list (typically most-recent-first from the circular buffer).
    """
    events: List[TimelineEvent] = []

    for n in notifications:
        ts = n.get("timestamp", "")
        if not ts:
            continue

        app = n.get("app_name") or n.get("package", "Unknown")
        title = n.get("title", "")
        text = n.get("text", "")
        prio = n.get("priority", "default")

        # Build a concise summary line for the timeline view.
        parts = [f"[{app}]"]
        if title:
            parts.append(title)
        if text:
            parts.append(f"— {text[:120]}")  # cap preview length
        summary = " ".join(parts)

        events.append(
            TimelineEvent(
                timestamp=ts,
                kind="notification",
                summary=summary,
                confidence=Confidence.LIVE,
                ref=n.get("key", ""),
            )
        )

    return events


# ---------------------------------------------------------------------------
# Summary statistics
# ---------------------------------------------------------------------------


def get_notification_summary(notifications: List[Dict]) -> Dict[str, Any]:
    """Return aggregate statistics over a parsed notification list.

    Returned dict keys:

    * ``total``              — total notification count
    * ``by_app``             — ``{app_name: count}``
    * ``by_package``         — ``{package: count}``
    * ``by_priority``        — ``{priority: count}``
    * ``with_title``         — count of entries with a non-empty title
    * ``with_text``          — count of entries with a non-empty body
    * ``high_priority``      — count with priority in {``high``, ``max``}
    * ``communication_apps`` — count from known communication apps
    """
    by_app: Dict[str, int] = {}
    by_pkg: Dict[str, int] = {}
    by_prio: Dict[str, int] = {}
    with_title = 0
    with_text = 0
    high_prio = 0
    comm_count = 0

    for n in notifications:
        app = n.get("app_name", "Unknown")
        pkg = n.get("package", "unknown")
        prio = n.get("priority", "default")

        by_app[app] = by_app.get(app, 0) + 1
        by_pkg[pkg] = by_pkg.get(pkg, 0) + 1
        by_prio[prio] = by_prio.get(prio, 0) + 1

        if n.get("title"):
            with_title += 1
        if n.get("text"):
            with_text += 1
        if prio in ("high", "max"):
            high_prio += 1
        if n.get("is_comm"):
            comm_count += 1

    return {
        "total": len(notifications),
        "by_app": by_app,
        "by_package": by_pkg,
        "by_priority": by_prio,
        "with_title": with_title,
        "with_text": with_text,
        "high_priority": high_prio,
        "communication_apps": comm_count,
    }
