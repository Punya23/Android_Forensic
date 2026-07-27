"""Bluetooth history parser for eRakshak.

Parses Bluetooth device history from ``adb shell dumpsys bluetooth_manager``.

This identifies devices that were paired with, connected to, or seen by the
target Android device.  Even after a factory reset, Bluetooth bond records can
persist in flash if the OEM does not wipe them.

Forensic value:
* Proves physical proximity to another device (paired/connected = same room).
* Identifies co-conspirators via MAC addresses of their personal devices.
* Correlates with cell-tower and notification timelines to confirm meetings.
* MAC manufacturer prefix (OUI) can identify the device vendor / type.
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

# Bond state integers -> canonical label (mirrors BluetoothDevice constants)
_BOND_STATE_MAP: Dict[str, str] = {
    "10": "none",
    "11": "bonding",
    "12": "bonded",
    "none": "none",
    "bonding": "bonding",
    "bonded": "bonded",
    "not_bonded": "none",
}

# Major device class codes -> friendly label
_DEVICE_CLASS_MAP: Dict[int, str] = {
    0x0100: "computer",
    0x0200: "phone",
    0x0300: "network",
    0x0400: "audio",
    0x0500: "peripheral",
    0x0600: "imaging",
    0x0700: "wearable",
    0x0800: "toy",
    0x0900: "health",
    0x1F00: "uncategorised",
}

# Regex patterns for field extraction
_RE_NAME = re.compile(r"name\s*[=:]\s*(.+)", re.IGNORECASE)
_RE_ALIAS = re.compile(r"alias\s*[=:]\s*(.+)", re.IGNORECASE)
_RE_MAC = re.compile(r"\b([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})\b")
_RE_BOND = re.compile(r"bondState\s*[=:]\s*(\w+)", re.IGNORECASE)
_RE_BOND_ALT = re.compile(r"bond(?:ed)?\s*[=:]\s*(\w+)", re.IGNORECASE)
_RE_CONNECTED = re.compile(r"connected\s*[=:]\s*(true|false)", re.IGNORECASE)
_RE_LAST_SEEN = re.compile(r"lastSeen\s*[=:]\s*(.+)", re.IGNORECASE)
_RE_LAST_SEEN_ALT = re.compile(
    r"last(?:_| )(?:seen|connected|active)\s*[=:]\s*(.+)", re.IGNORECASE
)
_RE_CLASS = re.compile(
    r"(?:btClass|deviceClass|class)\s*[=:]\s*(0x[0-9a-fA-F]+|\d+)", re.IGNORECASE
)


# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------


def parse_bluetooth_timestamp(raw_time: str) -> Optional[str]:
    """Convert various Bluetooth timestamp formats to ISO-8601 UTC.

    Handles:
    * Milliseconds since epoch  (e.g. ``1751826000000``)
    * Seconds since epoch       (e.g. ``1751826000``)
    * Date string               (e.g. ``2025-07-06 14:23:01``)
    * Android log format        (e.g. ``07-06 14:23:01.456``)
    """
    if not raw_time:
        return None

    raw = raw_time.strip()

    # --- numeric epoch (ms or s) ---
    if re.fullmatch(r"\d{10,13}", raw):
        epoch_val = int(raw)
        if epoch_val > 9_999_999_999:
            epoch_val //= 1000
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch_val))

    # --- ISO-like: YYYY-MM-DD HH:MM:SS ---
    m = re.match(
        r"(\d{4})-(\d{2})-(\d{2})[T\s](\d{2}):(\d{2}):(\d{2})",
        raw,
    )
    if m:
        yr, mo, dy, hr, mn, sc = m.groups()
        return f"{yr}-{mo}-{dy}T{hr}:{mn}:{sc}Z"

    # --- Android log format: MM-DD HH:MM:SS.mmm ---
    m2 = re.match(
        r"(\d{1,2})-(\d{1,2})\s+(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?",
        raw,
    )
    if m2:
        month, day, hour, minute, sec = m2.groups()
        year = time.gmtime().tm_year
        return f"{year}-{int(month):02d}-{int(day):02d}" f"T{hour}:{minute}:{sec}Z"

    return None


# ---------------------------------------------------------------------------
# ADB acquisition
# ---------------------------------------------------------------------------


def get_bluetooth_history(adb: Adb) -> str:
    """Execute ``adb shell dumpsys bluetooth_manager`` and return stdout.

    Falls back to ``dumpsys bluetooth`` if the primary command fails (Android
    versions prior to 6.0 used a different service name).  Returns an empty
    string on any error.
    """
    result = adb.shell("dumpsys bluetooth_manager")
    if result.ok and result.stdout.strip():
        return result.stdout
    # Fallback for older Android releases
    result2 = adb.shell("dumpsys bluetooth")
    return result2.stdout if result2.ok else ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_device_class(raw: str) -> str:
    """Convert a raw device class integer / hex string to a friendly label."""
    try:
        value = (
            int(raw, 16) if raw.startswith("0x") or raw.startswith("0X") else int(raw)
        )
        # Major device class is bits 8-12
        major = value & 0x1F00
        return _DEVICE_CLASS_MAP.get(major, "other")
    except (ValueError, TypeError):
        return "other"


def _normalise_bond(raw: str) -> str:
    """Map raw bondState value to a canonical label."""
    cleaned = raw.strip().lower()
    return _BOND_STATE_MAP.get(cleaned, "unknown")


def _split_device_blocks(text: str) -> List[str]:
    """Split dumpsys bluetooth_manager output into per-device blocks.

    Attempts several common delimiters used across AOSP versions.
    """
    # Pattern: lines starting with a MAC address introduce a new device record
    mac_split = re.split(r"\n(?=\s*[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})", text)
    if len(mac_split) > 2:
        return mac_split

    # Blank-line-separated stanzas
    double_nl = re.split(r"\n\s*\n", text)
    if len(double_nl) > 2:
        return double_nl

    # Numbered device entries
    numbered = re.split(r"\n(?=\s*Device\s+\d+)", text)
    if len(numbered) > 2:
        return numbered

    return [text]


# ---------------------------------------------------------------------------
# Core parser
# ---------------------------------------------------------------------------


def parse_bluetooth_history(adb_output: str) -> List[Dict[str, Any]]:
    """Parse raw ``dumpsys bluetooth_manager`` output into structured device rows.

    Each returned dict contains:

    * ``mac``          — MAC address (unique device identifier)
    * ``name``         — device display name (or empty)
    * ``bond_state``   — ``none`` / ``bonding`` / ``bonded``
    * ``connected``    — True if the device was connected at dump time
    * ``last_seen``    — ISO-8601 UTC timestamp (or empty)
    * ``device_class`` — ``computer`` / ``phone`` / ``audio`` / etc.
    * ``is_paired``    — True if bond_state == "bonded"

    Results are deduplicated by MAC address; the richest record wins (most
    fields populated).
    """
    if not adb_output:
        return []

    # MAC address -> best record dict
    devices_by_mac: Dict[str, Dict[str, Any]] = {}

    blocks = _split_device_blocks(adb_output)

    for block in blocks:
        if not block.strip():
            continue

        # Each block must contain a MAC address to be useful
        mac_match = _RE_MAC.search(block)
        if not mac_match:
            continue
        mac = mac_match.group(1).upper()

        # --- name (prefer alias over internal name) ---
        alias_match = _RE_ALIAS.search(block)
        name_match = _RE_NAME.search(block)
        name = ""
        if alias_match:
            name = alias_match.group(1).strip().strip('"')
        elif name_match:
            name = name_match.group(1).strip().strip('"')

        # --- bond state ---
        bond_match = _RE_BOND.search(block) or _RE_BOND_ALT.search(block)
        bond_state = _normalise_bond(bond_match.group(1)) if bond_match else "none"

        # --- connected flag ---
        conn_match = _RE_CONNECTED.search(block)
        connected = conn_match.group(1).lower() == "true" if conn_match else False

        # --- last seen timestamp ---
        seen_match = _RE_LAST_SEEN.search(block) or _RE_LAST_SEEN_ALT.search(block)
        raw_ts = seen_match.group(1).strip() if seen_match else ""
        last_seen = parse_bluetooth_timestamp(raw_ts) or ""

        # --- device class ---
        class_match = _RE_CLASS.search(block)
        device_class = (
            _parse_device_class(class_match.group(1)) if class_match else "other"
        )

        record: Dict[str, Any] = {
            "mac": mac,
            "name": name,
            "bond_state": bond_state,
            "connected": connected,
            "last_seen": last_seen,
            "device_class": device_class,
            "is_paired": bond_state == "bonded",
        }

        # Deduplication: keep richer record (more non-empty fields)
        if mac not in devices_by_mac:
            devices_by_mac[mac] = record
        else:
            existing = devices_by_mac[mac]
            existing_score = sum(1 for v in existing.values() if v)
            new_score = sum(1 for v in record.values() if v)
            if new_score > existing_score:
                devices_by_mac[mac] = record

    return list(devices_by_mac.values())


# ---------------------------------------------------------------------------
# Timeline builder
# ---------------------------------------------------------------------------


def build_bluetooth_timeline(devices: List[Dict]) -> List[TimelineEvent]:
    """Convert parsed Bluetooth device dicts into :class:`TimelineEvent` objects.

    Only devices with a ``last_seen`` timestamp are included; the summary
    includes MAC address, device name, and bond state.
    """
    events: List[TimelineEvent] = []

    for d in devices:
        ts = d.get("last_seen", "")
        if not ts:
            continue

        mac = d.get("mac", "??:??:??:??:??:??")
        name = d.get("name", "")
        bond_state = d.get("bond_state", "none")
        connected = d.get("connected", False)
        dev_class = d.get("device_class", "other")

        name_part = f" ({name})" if name else ""
        state_tag = "CONNECTED" if connected else bond_state.upper()
        summary = f"[Bluetooth] {mac}{name_part} — {state_tag} [{dev_class}]"

        events.append(
            TimelineEvent(
                timestamp=ts,
                kind="bluetooth",
                summary=summary,
                confidence=Confidence.LIVE,
                ref=mac,
            )
        )

    return events


# ---------------------------------------------------------------------------
# Summary statistics
# ---------------------------------------------------------------------------


def get_bluetooth_summary(devices: List[Dict]) -> Dict[str, Any]:
    """Return aggregate statistics over a parsed Bluetooth device list.

    Returned dict keys:

    * ``total``         — total unique device count
    * ``by_bond_state`` — ``{bond_state: count}``
    * ``by_class``      — ``{device_class: count}``
    * ``connected``     — count of devices connected at dump time
    * ``paired``        — count of bonded devices
    * ``with_name``     — count of devices with a non-empty name
    """
    by_bond: Dict[str, int] = {}
    by_class: Dict[str, int] = {}
    connected = 0
    paired = 0
    with_name = 0

    for d in devices:
        bond = d.get("bond_state", "none")
        cls = d.get("device_class", "other")

        by_bond[bond] = by_bond.get(bond, 0) + 1
        by_class[cls] = by_class.get(cls, 0) + 1

        if d.get("connected"):
            connected += 1
        if d.get("is_paired"):
            paired += 1
        if d.get("name"):
            with_name += 1

    return {
        "total": len(devices),
        "by_bond_state": by_bond,
        "by_class": by_class,
        "connected": connected,
        "paired": paired,
        "with_name": with_name,
    }
