"""Google Maps location history parser for SNAGR.

Extracts location history from Google Maps using three complementary methods,
each with different privilege requirements:

Non-root (Tier 0):
  * ``adb shell dumpsys location``     -- current GPS coordinates

Consent-based (no root required):
  * Google Takeout export JSON         -- full location history (user exports)

Root-enhanced (Tier 2):
  * Google Maps cache                  -- recent place lookups and routes
    (/data/data/com.google.android.apps.maps/cache/)
  * ``da_destination_history``         -- every destination entered into Directions, with the
    trip's origin. Evidences *intent* (the user chose to go there), not just presence.
  * ``gmm_myplaces.db``                -- starred/saved/labelled places; Home and Work labels
    routinely identify a suspect's address.
  * ``search_history.db``              -- map search queries (kept even when no coordinate).
  * ``NetworkLocation.db`` / herrevad  -- the Play-services cell+WiFi geolocation cache, which
    records positions even for periods when GPS was switched off.

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


def _connect_ro(db_path: Path) -> Optional[sqlite3.Connection]:
    """Open an evidence database strictly read-only.

    A plain ``sqlite3.connect(path)`` opens read-write. On a database with an unclean WAL that
    is enough for SQLite to checkpoint and *rewrite the evidence file* on connect, changing its
    hash. ``mode=ro&immutable=1`` forbids any write and tells SQLite not to attempt recovery,
    which is also what lets it read a forensic copy whose ``-wal``/``-shm`` sidecars are absent.
    """
    try:
        if not db_path.is_file():
            return None
        if db_path.open("rb").read(16) != b"SQLite format 3\x00":
            return None
        con = sqlite3.connect(
            f"file:{db_path}?mode=ro&immutable=1", uri=True, check_same_thread=False
        )
        con.text_factory = lambda b: b.decode("utf-8", "replace")
        return con
    except (OSError, sqlite3.Error):
        return None


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
            con = _connect_ro(db_path)
            if con is None:
                continue
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
# Google Maps app-private databases (Tier 2 — root or full filesystem image)
# ---------------------------------------------------------------------------
#
# `parse_maps_cache` above is a generic column sniff over whatever `.db` files happen to sit in
# the cache tree. It finds coordinates but cannot say what they *mean*. The readers below know
# the specific schemas, so a row can be reported as "the user navigated here at 19:42" rather
# than an anonymous point — which is the difference between a coordinate and evidence.
#
# Locations under /data/data/com.google.android.apps.maps/databases/:
#   da_destination_history   every destination entered into Directions, with the trip's origin
#   gmm_myplaces.db          starred / saved / labelled places (Home and Work live here)
#   search_history.db        map search queries, some carrying the viewport centre
# and under /data/data/com.google.android.gms/databases/:
#   NetworkLocation.db       the GMS cell/WiFi geolocation cache — where the device asked
#                            "where am I" and what Google answered

_MAPS_DB_DIRS = (
    "com.google.android.apps.maps/databases",
    "com.google.android.gms/databases",
)


def _table_names(con: sqlite3.Connection) -> List[str]:
    try:
        return [
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
    except sqlite3.Error:
        return []


def _cols(con: sqlite3.Connection, table: str) -> Dict[str, str]:
    """Return ``{lowercase_name: real_name}`` for *table*."""
    try:
        return {r[1].lower(): r[1] for r in con.execute(f'PRAGMA table_info("{table}")')}
    except sqlite3.Error:
        return {}


def _first(cols: Dict[str, str], *candidates: str) -> Optional[str]:
    for c in candidates:
        if c in cols:
            return cols[c]
    return None


def _coord(value: Any) -> Optional[float]:
    """Coerce a coordinate, handling Google's ``E7`` fixed-point convention."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v != v:
        return None
    if abs(v) > 180.0:
        v /= 1e7
    return v if -180.0 <= v <= 180.0 else None


def _point_ok(lat: Optional[float], lon: Optional[float]) -> bool:
    if lat is None or lon is None:
        return False
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return False
    return not (abs(lat) < 1e-9 and abs(lon) < 1e-9)


def parse_maps_destination_history(db_path: Path) -> List[Dict[str, Any]]:
    """Parse ``da_destination_history`` — every destination typed into Maps Directions.

    This is one of the highest-value Maps artifacts: unlike passive location history, a
    destination is something the user *chose*, so it evidences intent rather than presence.
    Rows carry the destination coordinate and title, and many builds also record the trip's
    origin, which is emitted as its own ``maps_directions_origin`` point.
    """
    con = _connect_ro(db_path)
    if con is None:
        return []
    out: List[Dict[str, Any]] = []
    try:
        for table in _table_names(con):
            cols = _cols(con, table)
            dest_lat = _first(cols, "dest_lat", "destination_lat", "latitude", "lat")
            dest_lon = _first(cols, "dest_lng", "dest_lon", "destination_lng", "longitude", "lng")
            if not dest_lat or not dest_lon:
                continue
            ts_col = _first(cols, "time", "timestamp", "date", "last_used")
            title_col = _first(cols, "dest_title", "title", "name")
            addr_col = _first(cols, "dest_address", "address")
            src_lat = _first(cols, "source_lat", "src_lat", "start_lat")
            src_lon = _first(cols, "source_lng", "source_lon", "src_lng", "start_lng")

            select = [f'"{dest_lat}"', f'"{dest_lon}"']
            select.append(f'"{ts_col}"' if ts_col else "NULL")
            select.append(f'"{title_col}"' if title_col else "''")
            select.append(f'"{addr_col}"' if addr_col else "''")
            select.append(f'"{src_lat}"' if src_lat else "NULL")
            select.append(f'"{src_lon}"' if src_lon else "NULL")
            try:
                rows = con.execute(
                    f'SELECT {", ".join(select)} FROM "{table}" LIMIT 5000'
                ).fetchall()
            except sqlite3.Error:
                continue
            for row in rows:
                lat, lon = _coord(row[0]), _coord(row[1])
                ts = _parse_timestamp(str(row[2])) if row[2] is not None else ""
                title = str(row[3] or "")
                address = str(row[4] or "")
                if _point_ok(lat, lon):
                    out.append(
                        {
                            "latitude": lat,
                            "longitude": lon,
                            "timestamp": ts or "",
                            "place_name": title,
                            "address": address,
                            "source": "maps_destination_history",
                            "provenance": f"{db_path.name}:{table} (navigation destination)",
                        }
                    )
                slat, slon = _coord(row[5]), _coord(row[6])
                if _point_ok(slat, slon):
                    out.append(
                        {
                            "latitude": slat,
                            "longitude": slon,
                            "timestamp": ts or "",
                            "place_name": f"origin of trip to {title}" if title else "trip origin",
                            "source": "maps_directions_origin",
                            "provenance": f"{db_path.name}:{table} (navigation origin)",
                        }
                    )
    finally:
        con.close()
    return out


def parse_maps_myplaces(db_path: Path) -> List[Dict[str, Any]]:
    """Parse ``gmm_myplaces.db`` — starred, saved and labelled places.

    Home and Work labels live here, which routinely identify a suspect's address without any
    other artifact. These are *saved* places, not positions the device occupied: they prove
    the user bookmarked a location, not that they were ever at it. The ``source`` value keeps
    that distinction explicit so the map view can style them differently.
    """
    con = _connect_ro(db_path)
    if con is None:
        return []
    out: List[Dict[str, Any]] = []
    try:
        for table in _table_names(con):
            cols = _cols(con, table)
            lat_col = _first(cols, "latitude", "lat", "lat_e7", "latitude_e7")
            lon_col = _first(cols, "longitude", "lng", "lon", "lng_e7", "longitude_e7")
            if not lat_col or not lon_col:
                continue
            name_col = _first(cols, "name", "title", "label", "alias", "display_name")
            addr_col = _first(cols, "address", "formatted_address", "vicinity")
            ts_col = _first(cols, "timestamp", "time", "date", "created", "modified")
            select = [f'"{lat_col}"', f'"{lon_col}"']
            select.append(f'"{name_col}"' if name_col else "''")
            select.append(f'"{addr_col}"' if addr_col else "''")
            select.append(f'"{ts_col}"' if ts_col else "NULL")
            try:
                rows = con.execute(
                    f'SELECT {", ".join(select)} FROM "{table}" LIMIT 5000'
                ).fetchall()
            except sqlite3.Error:
                continue
            for row in rows:
                lat, lon = _coord(row[0]), _coord(row[1])
                if not _point_ok(lat, lon):
                    continue
                out.append(
                    {
                        "latitude": lat,
                        "longitude": lon,
                        "timestamp": (_parse_timestamp(str(row[4])) or "") if row[4] else "",
                        "place_name": str(row[2] or ""),
                        "address": str(row[3] or ""),
                        "source": "maps_saved_place",
                        "provenance": f"{db_path.name}:{table} (saved place — bookmark, not a visit)",
                    }
                )
    finally:
        con.close()
    return out


def parse_maps_search_history(db_path: Path) -> List[Dict[str, Any]]:
    """Parse Maps search history, keeping queries even when they carry no coordinate.

    A search for a place name is evidence of interest in that place regardless of whether the
    row stored a viewport centre. Rows without coordinates are returned with ``latitude`` and
    ``longitude`` set to ``None`` and are filtered out by :func:`build_location_points`; the
    caller can still surface them as searches.
    """
    con = _connect_ro(db_path)
    if con is None:
        return []
    out: List[Dict[str, Any]] = []
    try:
        for table in _table_names(con):
            cols = _cols(con, table)
            query_col = _first(cols, "query", "search_query", "text", "term", "suggestion")
            if not query_col:
                continue
            lat_col = _first(cols, "latitude", "lat", "lat_e7")
            lon_col = _first(cols, "longitude", "lng", "lon", "lng_e7")
            ts_col = _first(cols, "timestamp", "time", "date", "last_used")
            select = [f'"{query_col}"']
            select.append(f'"{lat_col}"' if lat_col else "NULL")
            select.append(f'"{lon_col}"' if lon_col else "NULL")
            select.append(f'"{ts_col}"' if ts_col else "NULL")
            try:
                rows = con.execute(
                    f'SELECT {", ".join(select)} FROM "{table}" LIMIT 5000'
                ).fetchall()
            except sqlite3.Error:
                continue
            for row in rows:
                query = str(row[0] or "").strip()
                if not query:
                    continue
                lat, lon = _coord(row[1]), _coord(row[2])
                has_point = _point_ok(lat, lon)
                out.append(
                    {
                        "latitude": lat if has_point else None,
                        "longitude": lon if has_point else None,
                        "timestamp": (_parse_timestamp(str(row[3])) or "") if row[3] else "",
                        "place_name": query,
                        "query": query,
                        "source": "maps_search",
                        "provenance": f"{db_path.name}:{table} (map search query)",
                    }
                )
    finally:
        con.close()
    return out


def parse_gms_network_location(db_path: Path) -> List[Dict[str, Any]]:
    """Parse the Play-services network-location cache (``NetworkLocation.db`` / ``herrevad``).

    When an app asks for a coarse position, GMS resolves nearby cell towers and WiFi BSSIDs to
    coordinates and caches the answer. The cache is therefore a record of *where the device
    asked* — usable even when GPS was off for the whole period, which is exactly the scenario
    where every other location source comes up empty.
    """
    con = _connect_ro(db_path)
    if con is None:
        return []
    out: List[Dict[str, Any]] = []
    try:
        for table in _table_names(con):
            cols = _cols(con, table)
            lat_col = _first(cols, "latitude", "lat", "lat_e7", "latitude_e7")
            lon_col = _first(cols, "longitude", "lng", "lon", "lng_e7", "longitude_e7")
            if not lat_col or not lon_col:
                continue
            ts_col = _first(cols, "time", "timestamp", "date", "expires")
            acc_col = _first(cols, "accuracy", "radius", "accuracy_m")
            key_col = _first(cols, "cid", "mac", "bssid", "key", "_id")
            select = [f'"{lat_col}"', f'"{lon_col}"']
            select.append(f'"{ts_col}"' if ts_col else "NULL")
            select.append(f'"{acc_col}"' if acc_col else "NULL")
            select.append(f'"{key_col}"' if key_col else "''")
            try:
                rows = con.execute(
                    f'SELECT {", ".join(select)} FROM "{table}" LIMIT 5000'
                ).fetchall()
            except sqlite3.Error:
                continue
            kind = "cell tower" if table.lower().startswith(("ncell", "cell")) else "WiFi AP"
            for row in rows:
                lat, lon = _coord(row[0]), _coord(row[1])
                if not _point_ok(lat, lon):
                    continue
                try:
                    accuracy = float(row[3]) if row[3] is not None else None
                except (TypeError, ValueError):
                    accuracy = None
                out.append(
                    {
                        "latitude": lat,
                        "longitude": lon,
                        "timestamp": (_parse_timestamp(str(row[2])) or "") if row[2] else "",
                        "place_name": f"{kind} {row[4]}".strip(),
                        "accuracy": accuracy,
                        "source": "gms_network_location",
                        "provenance": f"{db_path.name}:{table} (Play-services geolocation cache)",
                    }
                )
    finally:
        con.close()
    return out


# Filename fragment → reader, checked in order. The first match wins; anything unmatched falls
# through to the generic column sniff so an unfamiliar Maps database is still read.
_MAPS_READERS: tuple[tuple[str, Any], ...] = (
    ("da_destination_history", parse_maps_destination_history),
    ("destination_history", parse_maps_destination_history),
    ("gmm_myplaces", parse_maps_myplaces),
    ("myplaces", parse_maps_myplaces),
    ("search_history", parse_maps_search_history),
    ("networklocation", parse_gms_network_location),
    ("herrevad", parse_gms_network_location),
)


def parse_maps_app_data(root: Path) -> List[Dict[str, Any]]:
    """Walk a staged tree for Google Maps / Play-services databases and parse each one.

    *root* may be an app data directory, a `databases/` folder, or the whole staging tree —
    the walk finds the files either way, so the caller does not have to know how the acquisition
    laid them out.

    Every Maps database is app-private, so reaching any of this requires root or a full
    filesystem image. On a non-root acquisition this returns an empty list, which means "not
    reachable at this tier", **not** "the user never navigated anywhere".
    """
    if not root.exists():
        return []
    out: List[Dict[str, Any]] = []
    seen: set[Path] = set()

    candidates: List[Path] = []
    for pattern in ("*.db", "da_destination_history", "*.sqlite", "*.sqlitedb"):
        candidates.extend(root.rglob(pattern))
    # Also catch extension-less files that live in a Maps `databases/` directory.
    for db_dir in _MAPS_DB_DIRS:
        target = root / db_dir
        if target.is_dir():
            candidates.extend(p for p in target.iterdir() if p.is_file())

    for db_path in candidates:
        resolved = db_path.resolve()
        if resolved in seen or not db_path.is_file():
            continue
        seen.add(resolved)
        name = db_path.name.lower()
        reader = next((fn for frag, fn in _MAPS_READERS if frag in name), None)
        try:
            if reader is not None:
                out.extend(reader(db_path))
            elif "maps" in str(db_path).lower() or "gms" in str(db_path).lower():
                # Unrecognised database inside a Maps/GMS tree: fall back to the column sniff
                # rather than skipping it, since Google renames these files between releases.
                out.extend(_sniff_single_db(db_path))
        except Exception:
            # One unreadable database must not abort the sweep over the rest.
            continue
    return _dedupe_maps(out)


def _sniff_single_db(db_path: Path) -> List[Dict[str, Any]]:
    """Generic coordinate-column sniff over one database (the `parse_maps_cache` logic)."""
    con = _connect_ro(db_path)
    if con is None:
        return []
    out: List[Dict[str, Any]] = []
    try:
        for table in _table_names(con):
            cols = _cols(con, table)
            lat_col = next((cols[c] for c in cols if c == "lat" or "latitude" in c), None)
            lon_col = next(
                (cols[c] for c in cols if c in ("lon", "lng") or "longitude" in c), None
            )
            if not lat_col or not lon_col:
                continue
            ts_col = next((cols[c] for c in cols if "time" in c or "date" in c), None)
            name_col = next(
                (cols[c] for c in cols if "name" in c or "place" in c or "title" in c), None
            )
            select = [f'"{lat_col}"', f'"{lon_col}"']
            select.append(f'"{ts_col}"' if ts_col else "NULL")
            select.append(f'"{name_col}"' if name_col else "''")
            try:
                rows = con.execute(
                    f'SELECT {", ".join(select)} FROM "{table}" LIMIT 2000'
                ).fetchall()
            except sqlite3.Error:
                continue
            for row in rows:
                lat, lon = _coord(row[0]), _coord(row[1])
                if not _point_ok(lat, lon):
                    continue
                out.append(
                    {
                        "latitude": lat,
                        "longitude": lon,
                        "timestamp": (_parse_timestamp(str(row[2])) or "") if row[2] else "",
                        "place_name": str(row[3] or ""),
                        "source": "maps_cache",
                        "provenance": f"{db_path.name}:{table} (generic schema scan)",
                    }
                )
    finally:
        con.close()
    return out


def _dedupe_maps(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop rows repeating the same point/time/source, keeping the first (most specific) one."""
    seen: set[tuple] = set()
    out: List[Dict[str, Any]] = []
    for r in rows:
        lat, lon = r.get("latitude"), r.get("longitude")
        key = (
            round(lat, 6) if isinstance(lat, float) else lat,
            round(lon, 6) if isinstance(lon, float) else lon,
            r.get("timestamp") or "",
            r.get("source") or "",
            r.get("place_name") or "",
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


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
    """Detect suspicious location patterns **in rows that place the device**.

    Each returned dict contains:

    * ``pattern``     -- label (e.g. "late_night_location", "impossible_speed")
    * ``description`` -- human-readable explanation
    * ``severity``    -- "info" | "warn" | "critical"
    * ``evidence``    -- supporting data dict

    Presence vs interest
    --------------------
    ``maps_locations`` deliberately mixes two kinds of row: positions the OS actually
    recorded for this device (Takeout history, the Play-services geolocation cache) and
    places the user merely *looked at* (a search, a saved place, a navigation destination
    they may never have travelled to). Both heuristics below make claims about where the
    device physically **was**, so they run over presence rows only.

    This is not a cosmetic distinction. Run over the mixed set, a place searched at 02:00
    is reported as "Device was at 'X' at 02:xx (late night)", and a distant city looked up
    once produces a fabricated "Device moved 3000 km at 1200 km/h" flagged **critical** —
    an assertion about physical movement manufactured out of a map search. Categories come
    from the same source-of-truth table the unified location trace uses, so a parser that
    adds a source classifies identically in both places.
    """
    # Reuse the unified trace's OWN classifier rather than a second lookup against its
    # table — a hand-rolled copy here previously missed the `:`-prefix fallback _classify()
    # applies, and drifted out of sync with _SOURCE_MAP when a source was added to one but
    # not the other (found via adversarial review: real Takeout `takeout_semantic`/
    # `takeout_path` rows and source-less `current_location` fixes were silently
    # reclassified as "interest" and dropped from this very analysis). Calling the same
    # function the trace builder calls means both code paths can never disagree again.
    from ..forensics.location_aggregate import _PRESENCE_CATEGORIES, _classify

    patterns: List[Dict] = []

    # Filter to locations with valid lat/lon and timestamp
    plotted = [
        loc
        for loc in locations
        if loc.get("latitude") is not None
        and loc.get("longitude") is not None
        and loc.get("timestamp")
    ]

    def _is_presence(loc: Dict) -> bool:
        category, _label, _tier = _classify(str(loc.get("source") or ""))
        return category in _PRESENCE_CATEGORIES

    valid = [loc for loc in plotted if _is_presence(loc)]
    excluded = len(plotted) - len(valid)
    # Sort chronologically
    valid.sort(key=lambda x: x.get("timestamp", ""))

    # Excluded rows are reported, never silently dropped: "no anomalies" must not be
    # readable as "nothing was looked up", and an examiner needs to know the movement
    # analysis deliberately ignored these rather than failing to see them.
    if excluded:
        patterns.append(
            {
                "pattern": "interest_rows_excluded",
                "description": (
                    f"{excluded} plotted Maps row(s) record interest in a place (a search, "
                    f"a saved place, or a navigation destination) rather than a recorded "
                    f"device position, and were excluded from the movement and late-night "
                    f"analysis below. They evidence that the place was looked up — not that "
                    f"the device was ever there."
                ),
                "severity": "info",
                "evidence": {
                    "excluded_rows": excluded,
                    "analysed_rows": len(valid),
                },
            }
        )

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
