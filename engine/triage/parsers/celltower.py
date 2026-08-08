"""Cell tower history parser for SNAGR.

Parses cell tower data from ``adb shell dumpsys telephony.registry``.

Even when GPS is disabled, the telephony subsystem continuously logs the
serving cell tower and surrounding cells.  This provides coarse but
actionable location data that persists independently of any app.

Forensic value:
* Location tracking with GPS disabled.
* Movement patterns: a sequence of distinct cell tower records demonstrates
  physical displacement across an operator's coverage map.
* Proves presence at (or absence from) a geographic area.
* Corroborates or contradicts alibi timelines.

Caveats:
* Cell-tower location is approximate (coverage radius 100 m – 35 km).
* MCC/MNC uniquely identify the operator; Cell-ID + LAC uniquely identify
  the tower within that operator's network.
* Signal strength is reported in ASU (Arbitrary Strength Unit) which is
  vendor-defined — the conversion to dBm varies by radio type.
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional, Tuple

from ..adb import Adb
from ..models import TimelineEvent
from ..config import Confidence

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# ASU thresholds for GSM (values differ for LTE/UMTS but GSM is baseline)
_ASU_LABELS: List[Tuple[int, str]] = [
    (20, "excellent"),
    (15, "good"),
    (10, "fair"),
    (0, "poor"),
]

# Regex patterns for field extraction
_RE_CELL_ID = re.compile(r"\bcid\b\s*[=:]\s*(-?\d+)", re.IGNORECASE)
_RE_CELL_ALT = re.compile(r"\bcellId\b\s*[=:]\s*(-?\d+)", re.IGNORECASE)
_RE_LAC = re.compile(r"\blac\b\s*[=:]\s*(-?\d+)", re.IGNORECASE)
_RE_LAC_ALT = re.compile(r"\bareaCode\b\s*[=:]\s*(-?\d+)", re.IGNORECASE)
_RE_MCC = re.compile(r"\bmcc\b\s*[=:]\s*(\d+)", re.IGNORECASE)
_RE_MNC = re.compile(r"\bmnc\b\s*[=:]\s*(\d+)", re.IGNORECASE)
_RE_SIGNAL_ASU = re.compile(r"\basu\b\s*[=:]\s*(-?\d+)", re.IGNORECASE)
_RE_SIGNAL_DB = re.compile(r"\bdBm\b\s*[=:]\s*(-?\d+)", re.IGNORECASE)
_RE_OPERATOR = re.compile(
    r"(?:operator|carrierName|networkOperatorName)\s*[=:]\s*([^\n,]+)", re.IGNORECASE
)
_RE_TIMESTAMP = re.compile(
    r"(?:timestamp|time|lastChanged|registrationTime)\s*[=:]\s*(\S+)", re.IGNORECASE
)
_RE_NETWORK_TYPE = re.compile(
    r"(?:networkType|dataNetworkType|type)\s*[=:]\s*(\w+)", re.IGNORECASE
)


# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------


def parse_celltower_timestamp(raw_time: str) -> Optional[str]:
    """Convert various telephony timestamp formats to ISO-8601 UTC.

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


def get_celltower_history(adb: Adb) -> str:
    """Execute ``adb shell dumpsys telephony.registry`` and return stdout.

    Falls back to ``dumpsys phone`` on Android releases where the primary
    service name is unavailable.  Returns an empty string on any error.
    """
    result = adb.shell("dumpsys telephony.registry")
    if result.ok and result.stdout.strip():
        return result.stdout
    # Fallback for older Android releases
    result2 = adb.shell("dumpsys phone")
    return result2.stdout if result2.ok else ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _asu_to_label(asu: int) -> str:
    """Convert an ASU value to a human-readable signal quality label."""
    for threshold, label in _ASU_LABELS:
        if asu >= threshold:
            return label
    return "poor"


def _split_tower_blocks(text: str) -> List[str]:
    """Split dumpsys telephony.registry output into per-registration blocks.

    Tries several heuristics used across AOSP versions / OEM skins.
    """
    # CellIdentity blocks are the most reliable delimiter
    cell_split = re.split(
        r"\n(?=\s*(?:CellIdentity|mCellIdentity|ServiceState|CellInfo))", text
    )
    if len(cell_split) > 2:
        return cell_split

    # Blank-line-separated stanzas
    double_nl = re.split(r"\n\s*\n", text)
    if len(double_nl) > 2:
        return double_nl

    # Numbered phone / subscription entries
    numbered = re.split(r"\n(?=\s*Phone\s*\[?\d+\]?)", text)
    if len(numbered) > 2:
        return numbered

    return [text]


# ---------------------------------------------------------------------------
# Core parser
# ---------------------------------------------------------------------------


def parse_celltower_history(adb_output: str) -> List[Dict[str, Any]]:
    """Parse raw ``dumpsys telephony.registry`` output into structured records.

    Each returned dict contains:

    * ``cell_id``     — Cell Identifier (CID) within the LAC
    * ``lac``         — Location Area Code (or Tracking Area Code for LTE)
    * ``mcc``         — Mobile Country Code
    * ``mnc``         — Mobile Network Code
    * ``signal_asu``  — signal strength in ASU (-1 if unknown)
    * ``signal_label``— ``excellent`` / ``good`` / ``fair`` / ``poor``
    * ``operator``    — operator / carrier name (or empty)
    * ``timestamp``   — ISO-8601 UTC string (or empty)
    * ``network_type``— network type string (GSM/LTE/UMTS/NR/…)

    Results are deduplicated by ``(cell_id, lac, timestamp)`` tuples.
    """
    if not adb_output:
        return []

    towers: List[Dict[str, Any]] = []
    seen: set = set()

    blocks = _split_tower_blocks(adb_output)

    for block in blocks:
        if not block.strip():
            continue

        # --- cell ID ---
        cid_match = _RE_CELL_ID.search(block) or _RE_CELL_ALT.search(block)
        if not cid_match:
            continue
        cell_id_raw = int(cid_match.group(1))
        # -1 or 0 indicates "unknown" on many OEMs — skip uninformative records
        if cell_id_raw <= 0:
            continue
        cell_id = cell_id_raw

        # --- LAC ---
        lac_match = _RE_LAC.search(block) or _RE_LAC_ALT.search(block)
        lac = int(lac_match.group(1)) if lac_match else -1

        # --- MCC / MNC ---
        mcc_match = _RE_MCC.search(block)
        mnc_match = _RE_MNC.search(block)
        mcc = int(mcc_match.group(1)) if mcc_match else -1
        mnc = int(mnc_match.group(1)) if mnc_match else -1

        # --- signal strength (prefer ASU; compute from dBm if needed) ---
        asu_match = _RE_SIGNAL_ASU.search(block)
        dbm_match = _RE_SIGNAL_DB.search(block)
        if asu_match:
            signal_asu = int(asu_match.group(1))
        elif dbm_match:
            # Rough GSM ASU approximation: ASU = (dBm + 113) / 2
            dbm = int(dbm_match.group(1))
            signal_asu = max(0, (dbm + 113) // 2)
        else:
            signal_asu = -1

        signal_label = _asu_to_label(signal_asu) if signal_asu >= 0 else "unknown"

        # --- operator ---
        op_match = _RE_OPERATOR.search(block)
        operator = op_match.group(1).strip().strip('"') if op_match else ""

        # --- timestamp ---
        ts_match = _RE_TIMESTAMP.search(block)
        raw_ts = ts_match.group(1).strip() if ts_match else ""
        timestamp = parse_celltower_timestamp(raw_ts) or ""

        # --- network type ---
        nt_match = _RE_NETWORK_TYPE.search(block)
        network_type = nt_match.group(1).upper() if nt_match else ""

        # Deduplication key
        dedup = (cell_id, lac, timestamp)
        if dedup in seen:
            continue
        seen.add(dedup)

        towers.append(
            {
                "cell_id": cell_id,
                "lac": lac,
                "mcc": mcc,
                "mnc": mnc,
                "signal_asu": signal_asu,
                "signal_label": signal_label,
                "operator": operator,
                "timestamp": timestamp,
                "network_type": network_type,
            }
        )

    return towers


# ---------------------------------------------------------------------------
# Timeline builder
# ---------------------------------------------------------------------------


def build_celltower_timeline(towers: List[Dict]) -> List[TimelineEvent]:
    """Convert parsed cell tower dicts into :class:`TimelineEvent` objects.

    Only towers with a parseable timestamp are included.  The summary line
    includes the operator name, cell ID, and signal quality label to give
    the analyst quick orientation without needing to look up tower data.
    """
    events: List[TimelineEvent] = []

    for t in towers:
        ts = t.get("timestamp", "")
        if not ts:
            continue

        cell_id = t.get("cell_id", "?")
        lac = t.get("lac", "?")
        operator = t.get("operator", "")
        sig = t.get("signal_label", "unknown")
        ntype = t.get("network_type", "")

        op_part = f" via {operator}" if operator else ""
        ntype_part = f" [{ntype}]" if ntype else ""
        summary = (
            f"[Cell Tower] CID={cell_id} LAC={lac}{op_part}"
            f" — signal {sig}{ntype_part}"
        )

        events.append(
            TimelineEvent(
                timestamp=ts,
                kind="celltower",
                summary=summary,
                confidence=Confidence.LIVE,
                ref=f"cid={cell_id},lac={lac}",
            )
        )

    return events


# ---------------------------------------------------------------------------
# Summary statistics
# ---------------------------------------------------------------------------


def get_celltower_summary(towers: List[Dict]) -> Dict[str, Any]:
    """Return aggregate statistics over a parsed cell tower list.

    Returned dict keys:

    * ``total``              — total record count
    * ``unique_towers``      — unique (cell_id, lac) combinations
    * ``by_operator``        — ``{operator: count}``
    * ``by_signal``          — ``{signal_label: count}``
    * ``by_network_type``    — ``{network_type: count}``
    """
    by_operator: Dict[str, int] = {}
    by_signal: Dict[str, int] = {}
    by_ntype: Dict[str, int] = {}
    unique_tower_ids: set = set()

    for t in towers:
        op = t.get("operator", "") or "unknown"
        sig = t.get("signal_label", "unknown")
        ntype = t.get("network_type", "") or "unknown"
        cid = t.get("cell_id", -1)
        lac = t.get("lac", -1)

        by_operator[op] = by_operator.get(op, 0) + 1
        by_signal[sig] = by_signal.get(sig, 0) + 1
        by_ntype[ntype] = by_ntype.get(ntype, 0) + 1
        unique_tower_ids.add((cid, lac))

    return {
        "total": len(towers),
        "unique_towers": len(unique_tower_ids),
        "by_operator": by_operator,
        "by_signal": by_signal,
        "by_network_type": by_ntype,
    }
