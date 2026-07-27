"""Google Maps location history parser for eRakshak.

Extracts location history from Google Maps using three complementary methods,
each with different privilege requirements:

Non-root (Tier 0):
  * ``adb shell dumpsys location``     -- current GPS coordinates

Consent-based (no root required):
  * Google Takeout export JSON         -- full location history (user exports)

Root-enhanced (Tier 2):
  * Google Maps cache                  -- recent place lookups and routes
    (/data/data/com.google.android.apps.maps/cache/)

Forensic value:
* Proves physical location at specific times.
* Identifies frequently visited places (home, workplace, associates).
* Detects late-night or suspicious location patterns.
* Corroborates or contradicts witness accounts and alibi timelines.
* Large Google Takeout exports contain years of timestamped GPS tracks.

Graceful degradation: all functions return empty results on failure rather
than raising exceptions.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..adb import Adb
from ..models import TimelineEvent, LocationPoint
from ..config import Confidence

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Speed threshold in km/h above which movement is flagged as "fast" (vehicle)
_FAST_MOVEMENT_KMH = 120

# Distance in km between consecutive points that triggers an anomaly
_ANOMALY_DISTANCE_KM = 500

# Hours considered "late night" for pattern detection
_LATE_NIGHT_START = 22  # 10 PM
_LATE_NIGHT_END = 5  # 5 AM

# Google Maps app data path (root required)
_MAPS_DATA_PATH = "/data/data/com.google.android.apps.maps"

# Minimum GPS accuracy (metres) to be considered a confident fix
_MIN_ACCURACY_M = 500


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_timestamp(raw: str) -> Optional[str]:
    """Convert various timestamp formats to ISO-8601 UTC."""
    if not raw:
        return None
    raw = str(raw).strip()
    if re.fullmatch(r"\d{10,13}", raw):
        epoch_val = int(raw)
        if epoch_val > 9_999_999_999:
            epoch_val //= 1000
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch_val))
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})[T\s](\d{2}):(\d{2}):(\d{2})", raw)
    if m:
        yr, mo, dy, hr, mn, sc = m.groups()
        return f"{yr}-{mo}-{dy}T{hr}:{mn}:{sc}Z"
    # ISO 8601 with Z or offset
    m2 = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", raw)
    if m2:
        return m2.group(1) + "Z"
    return None


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great-circle distance in km between two GPS points."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    )
    return 2 * R * math.asin(math.sqrt(a))


def _ts_to_epoch(iso_ts: str) -> float:
    """Convert ISO-8601 UTC string to Unix timestamp float."""
    import calendar

    t = time.strptime(iso_ts, "%Y-%m-%dT%H:%M:%SZ")
    return float(calendar.timegm(t))


def _get_hour(iso_ts: str) -> Optional[int]:
    """Extract the UTC hour (0-23) from an ISO-8601 timestamp string."""
    try:
        return time.gmtime(int(_ts_to_epoch(iso_ts))).tm_hour
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Current GPS location -- dumpsys location
# ---------------------------------------------------------------------------

_RE_LAT = re.compile(r"(?:latitude|lat)\s*[=:]\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE)
_RE_LON = re.compile(r"(?:longitude|lon|lng)\s*[=:]\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE)
_RE_ACCUR = re.compile(r"accuracy\s*[=:]\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE)
_RE_LOC_TS = re.compile(r"(?:time|timestamp|mTime)\s*[=:]\s*(\d+)", re.IGNORECASE)
_RE_PROVIDER = re.compile(r"provider\s*[=:]\s*(\w+)", re.IGNORECASE)


def parse_current_location(dumpsys_output: str) -> Dict[str, Any]:
    """Parse current GPS location from ``dumpsys location`` output.

    Returns a dict with keys:

    * ``latitude``   -- float latitude (or None)
    * ``longitude``  -- float longitude (or None)
    * ``accuracy_m`` -- GPS accuracy in metres (or -1)
    * ``timestamp``  -- ISO-8601 UTC string (or empty)
    * ``provider``   -- location provider (gps/network/fused/etc.)
    * ``valid``      -- True if both lat and lon were extracted
    """
    result: Dict[str, Any] = {
        "latitude": None,
        "longitude": None,
        "accuracy_m": -1,
        "timestamp": "",
        "provider": "",
        "valid": False,
    }

    if not dumpsys_output:
        return result

    lat_m = _RE_LAT.search(dumpsys_output)
    lon_m = _RE_LON.search(dumpsys_output)
    acc_m = _RE_ACCUR.search(dumpsys_output)
    ts_m = _RE_LOC_TS.search(dumpsys_output)
    prov_m = _RE_PROVIDER.search(dumpsys_output)

    if lat_m:
        result["latitude"] = float(lat_m.group(1))
    if lon_m:
        result["longitude"] = float(lon_m.group(1))
    if acc_m:
        result["accuracy_m"] = float(acc_m.group(1))
    if ts_m:
        result["timestamp"] = _parse_timestamp(ts_m.group(1)) or ""
    if prov_m:
        result["provider"] = prov_m.group(1).lower()

    if result["latitude"] is not None and result["longitude"] is not None:
        # Sanity check: valid lat/lon ranges
        if -90 <= result["latitude"] <= 90 and -180 <= result["longitude"] <= 180:
            result["valid"] = True

    return result


def get_current_location(adb: Adb) -> Dict[str, Any]:
    """Execute ``adb shell dumpsys location`` and parse the GPS fix.

    Returns the dict produced by :func:`parse_current_location`.
    Returns an invalid location dict on ADB failure.
    """
    result = adb.shell("dumpsys location")
    if not result.ok or not result.stdout.strip():
        return parse_current_location("")
    return parse_current_location(result.stdout)


# ---------------------------------------------------------------------------
# Google Takeout location history
# ---------------------------------------------------------------------------


def parse_google_takeout_location(export_path: Path) -> List[Dict[str, Any]]:
    """Parse a Google Takeout Location History JSON export.

    Handles both formats:
    * Old format: {"locations": [...]}
    * New semantic format: {"semanticSegments": [...]}

    Each returned dict contains:

    * ``latitude``    -- float
    * ``longitude``   -- float
    * ``timestamp``   -- ISO-8601 UTC string
    * ``accuracy_m``  -- metres (or -1)
    * ``activity``    -- detected activity type (or empty)
    * ``source``      -- "takeout"
    """
    if not export_path.exists():
        return []

    results: List[Dict[str, Any]] = []

    try:
        with open(export_path, "r", encoding="utf-8", errors="replace") as f:
            data = json.load(f)
    except Exception:
        return []

    # --- Old format: top-level "locations" array ---
    locations = data.get("locations", [])
    for loc in locations:
        try:
            lat = loc.get("latitudeE7", 0) / 1e7
            lon = loc.get("longitudeE7", 0) / 1e7
            raw_ts = loc.get("timestampMs") or loc.get("timestamp") or ""
            ts = _parse_timestamp(str(raw_ts)) or ""
            acc = int(loc.get("accuracy", -1))
            activity = ""
            # Extract most probable activity if present
            activities = loc.get("activity", [])
            if activities:
                acts = activities[0].get("activity", [])
                if acts:
                    activity = acts[0].get("type", "")

            if -90 <= lat <= 90 and -180 <= lon <= 180:
                results.append(
                    {
                        "latitude": lat,
                        "longitude": lon,
                        "timestamp": ts,
                        "accuracy_m": acc,
                        "activity": activity,
                        "source": "takeout",
                    }
                )
        except Exception:
            continue

    # --- New semantic format: "semanticSegments" ---
    segments = data.get("semanticSegments", [])
    for seg in segments:
        try:
            # Each segment may have a "visit" or "timelinePath"
            visit = seg.get("visit", {})
            start_ts = _parse_timestamp(seg.get("startTime", "")) or ""
            if visit:
                tp = visit.get("topCandidate", {}).get("placeLocation", {})
                lat_str = tp.get("latLng", "")
                # Format: "12.345678, 76.543210"
                if lat_str and "," in lat_str:
                    parts = lat_str.split(",")
                    lat = float(parts[0].strip())
                    lon = float(parts[1].strip())
                    results.append(
                        {
                            "latitude": lat,
                            "longitude": lon,
                            "timestamp": start_ts,
                            "accuracy_m": -1,
                            "activity": "visit",
                            "source": "takeout_semantic",
                        }
                    )

            # "timelinePath" is a list of {point, time} objects
            for path_pt in seg.get("timelinePath", []):
                pt = path_pt.get("point", "")
                pt_ts = _parse_timestamp(path_pt.get("time", "")) or ""
                if pt and "," in pt:
                    parts = pt.split(",")
                    lat = float(parts[0].strip())
                    lon = float(parts[1].strip())
                    results.append(
                        {
                            "latitude": lat,
                            "longitude": lon,
                            "timestamp": pt_ts,
                            "accuracy_m": -1,
                            "activity": seg.get("activity", {})
                            .get("topCandidate", {})
                            .get("type", ""),
                            "source": "takeout_path",
                        }
                    )
        except Exception:
            continue

    # Sort by timestamp
    results.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return results


# ---------------------------------------------------------------------------
# Google Maps cache (root enhanced)
# ---------------------------------------------------------------------------


def parse_maps_cache(cache_dir: Path) -> List[Dict[str, Any]]:
    """Parse Google Maps cache for location data (root access required).

    Gracefully returns an empty list if the cache is missing or inaccessible.

    Each returned dict contains:

    * ``latitude``    -- float
    * ``longitude``   -- float
    * ``timestamp``   -- ISO-8601 UTC (or empty)
    * ``place_name``  -- place name if available (or empty)
    * ``source``      -- "maps_cache"
    """
    if not cache_dir.exists():
        return []

    results: List[Dict[str, Any]] = []

    # Scan SQLite databases in the cache tree
    for db_path in cache_dir.rglob("*.db"):
        try:
            con = sqlite3.connect(str(db_path), check_same_thread=False)
            cur = con.cursor()
            # Enumerate all tables and look for lat/lon columns
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cur.fetchall()]
            for table in tables:
                try:
                    cur.execute(f"PRAGMA table_info({table})")
                    cols = {row[1].lower(): row[1] for row in cur.fetchall()}
                    lat_col = next((cols[c] for c in cols if "lat" in c), None)
                    lon_col = next(
                        (cols[c] for c in cols if "lon" in c or "lng" in c), None
                    )
                    if not lat_col or not lon_col:
                        continue
                    ts_col = next(
                        (cols[c] for c in cols if "time" in c or "date" in c),
                        None,
                    )
                    name_col = next(
                        (
                            cols[c]
                            for c in cols
                            if "name" in c or "place" in c or "title" in c
                        ),
                        None,
                    )
                    select_cols = [lat_col, lon_col]
                    if ts_col:
                        select_cols.append(ts_col)
                    if name_col:
                        select_cols.append(name_col)
                    cur.execute(
                        f"SELECT {', '.join(select_cols)} FROM {table} LIMIT 1000"
                    )
                    for row in cur.fetchall():
                        try:
                            lat = float(row[0])
                            lon = float(row[1])
                            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                                continue
                            ts = _parse_timestamp(str(row[2])) if len(row) > 2 else ""
                            place = str(row[3]) if len(row) > 3 else ""
                            results.append(
                                {
                                    "latitude": lat,
                                    "longitude": lon,
                                    "timestamp": ts or "",
                                    "place_name": place,
                                    "source": "maps_cache",
                                }
                            )
                        except (TypeError, ValueError):
                            continue
                except sqlite3.OperationalError:
                    continue
            con.close()
        except Exception:
            continue

    return results


# ---------------------------------------------------------------------------
# Timeline builder
# ---------------------------------------------------------------------------


def build_location_timeline(locations: List[Dict]) -> List[TimelineEvent]:
    """Convert location dicts into :class:`TimelineEvent` objects.

    Only locations with a parseable timestamp are included.
    """
    events: List[TimelineEvent] = []

    for loc in locations:
        ts = loc.get("timestamp", "")
        if not ts:
            continue
        lat = loc.get("latitude", 0.0)
        lon = loc.get("longitude", 0.0)
        place = loc.get("place_name", "") or loc.get("activity", "")
        acc = loc.get("accuracy_m", -1)
        source = loc.get("source", "unknown")
        place_part = f" ({place})" if place else ""
        acc_part = f" ±{int(acc)}m" if acc > 0 else ""
        summary = f"[Location] {lat:.5f}, {lon:.5f}{place_part}{acc_part} [{source}]"
        events.append(
            TimelineEvent(
                timestamp=ts,
                kind="location",
                summary=summary,
                confidence=Confidence.LIVE,
                ref=f"{lat:.5f},{lon:.5f}",
            )
        )

    events.sort(key=lambda e: e.timestamp, reverse=True)
    return events


# ---------------------------------------------------------------------------
# LocationPoint builder
# ---------------------------------------------------------------------------


def build_location_points(locations: List[Dict]) -> List[LocationPoint]:
    """Convert location dicts to :class:`LocationPoint` model objects.

    Used for map visualisation in the dashboard.
    """
    points: List[LocationPoint] = []

    for loc in locations:
        lat = loc.get("latitude")
        lon = loc.get("longitude")
        if lat is None or lon is None:
            continue
        try:
            lat = float(lat)
            lon = float(lon)
        except (TypeError, ValueError):
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue

        points.append(
            LocationPoint(
                latitude=lat,
                longitude=lon,
                source=loc.get("source", "unknown"),
                timestamp=loc.get("timestamp") or None,
                label=loc.get("place_name", ""),
                source_file=loc.get("source", ""),
            )
        )

    return points


# ---------------------------------------------------------------------------
# Summary statistics
# ---------------------------------------------------------------------------


def get_location_summary(locations: List[Dict]) -> Dict[str, Any]:
    """Compute aggregate statistics over a parsed location list.

    Returned dict keys:

    * ``total``               -- total location records
    * ``with_timestamp``      -- records that have a parseable timestamp
    * ``unique_places``       -- distinct place names
    * ``date_range``          -- {"first": ISO, "last": ISO} or empty
    * ``sources``             -- {source_name: count}
    * ``bounding_box``        -- {"min_lat", "max_lat", "min_lon", "max_lon"}
    """
    with_ts = 0
    places: set = set()
    sources: Dict[str, int] = {}
    timestamps: List[str] = []
    lats: List[float] = []
    lons: List[float] = []

    for loc in locations:
        ts = loc.get("timestamp", "")
        place = loc.get("place_name", "")
        src = loc.get("source", "unknown")
        lat = loc.get("latitude")
        lon = loc.get("longitude")

        if ts:
            with_ts += 1
            timestamps.append(ts)
        if place:
            places.add(place)
        sources[src] = sources.get(src, 0) + 1
        if lat is not None:
            lats.append(float(lat))
        if lon is not None:
            lons.append(float(lon))

    timestamps.sort()
    date_range = {"first": timestamps[0], "last": timestamps[-1]} if timestamps else {}
    bounding_box = (
        {
            "min_lat": min(lats),
            "max_lat": max(lats),
            "min_lon": min(lons),
            "max_lon": max(lons),
        }
        if lats and lons
        else {}
    )

    return {
        "total": len(locations),
        "with_timestamp": with_ts,
        "unique_places": len(places),
        "date_range": date_range,
        "sources": sources,
        "bounding_box": bounding_box,
    }


# ---------------------------------------------------------------------------
# Anomaly / pattern detection
# ---------------------------------------------------------------------------


def detect_location_anomalies(locations: List[Dict]) -> List[Dict]:
    """Detect suspicious location patterns.

    Each returned dict contains:

    * ``pattern``     -- label (e.g. "late_night_location", "impossible_speed")
    * ``description`` -- human-readable explanation
    * ``severity``    -- "info" | "warn" | "critical"
    * ``evidence``    -- supporting data dict
    """
    patterns: List[Dict] = []

    # Filter to locations with valid lat/lon and timestamp
    valid = [
        loc
        for loc in locations
        if loc.get("latitude") is not None
        and loc.get("longitude") is not None
        and loc.get("timestamp")
    ]
    # Sort chronologically
    valid.sort(key=lambda x: x.get("timestamp", ""))

    # --- Late-night visits ---
    for loc in valid:
        ts = loc.get("timestamp", "")
        hour = _get_hour(ts)
        if hour is None:
            continue
        if hour >= _LATE_NIGHT_START or hour < _LATE_NIGHT_END:
            place = (
                loc.get("place_name", "")
                or f"{loc['latitude']:.4f},{loc['longitude']:.4f}"
            )
            patterns.append(
                {
                    "pattern": "late_night_location",
                    "description": f"Device was at '{place}' at {hour:02d}:xx UTC (late night).",
                    "severity": "warn",
                    "evidence": {
                        "timestamp": ts,
                        "latitude": loc["latitude"],
                        "longitude": loc["longitude"],
                        "hour_utc": hour,
                    },
                }
            )

    # --- Impossible speed / large jumps between consecutive points ---
    for i in range(1, len(valid)):
        prev = valid[i - 1]
        curr = valid[i]
        try:
            dist_km = _haversine_km(
                float(prev["latitude"]),
                float(prev["longitude"]),
                float(curr["latitude"]),
                float(curr["longitude"]),
            )
            dt_s = _ts_to_epoch(curr["timestamp"]) - _ts_to_epoch(prev["timestamp"])
            if dt_s <= 0:
                continue
            speed_kmh = (dist_km / dt_s) * 3600

            if dist_km >= _ANOMALY_DISTANCE_KM:
                patterns.append(
                    {
                        "pattern": "large_location_jump",
                        "description": (
                            f"Device moved {dist_km:.0f} km in {dt_s / 3600:.1f} h "
                            f"({speed_kmh:.0f} km/h) -- may indicate travel or data anomaly."
                        ),
                        "severity": (
                            "warn" if speed_kmh < _FAST_MOVEMENT_KMH * 3 else "critical"
                        ),
                        "evidence": {
                            "from_ts": prev["timestamp"],
                            "to_ts": curr["timestamp"],
                            "distance_km": round(dist_km, 1),
                            "speed_kmh": round(speed_kmh, 1),
                        },
                    }
                )
            elif speed_kmh > _FAST_MOVEMENT_KMH:
                patterns.append(
                    {
                        "pattern": "high_speed_movement",
                        "description": (
                            f"Device moved at ~{speed_kmh:.0f} km/h "
                            f"({dist_km:.1f} km in {dt_s:.0f} s) -- likely vehicle or data error."
                        ),
                        "severity": "info",
                        "evidence": {
                            "from_ts": prev["timestamp"],
                            "to_ts": curr["timestamp"],
                            "distance_km": round(dist_km, 2),
                            "speed_kmh": round(speed_kmh, 1),
                        },
                    }
                )
        except Exception:
            continue

    return patterns
