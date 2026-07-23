"""Movement Pattern Analysis — analyse movement patterns from GPS data.

Processes a chronological list of GPS location dicts to calculate:

* Speed between consecutive points (km/h) via the Haversine formula.
* Movement type classification per segment.
* Overall movement pattern label for the whole trace.
* Detection of stationary periods.
* Aggregate movement statistics.

Usage::

    from engine.triage.forensics.movement_analysis import detect_movement_pattern

    result = detect_movement_pattern(locations)
    # result == {
    #     'overall':    'walking',
    #     'segments':   [...],
    #     'statistics': {...},
    # }
"""

from __future__ import annotations

import calendar
import math
import time
from typing import Any, Dict, List, Optional

from .gps_clustering import calculate_distance


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Movement type thresholds (km/h).
_STATIONARY_MAX = 5.0
_WALKING_MAX = 15.0
_BIKING_MAX = 30.0
# > 30 km/h → driving

# Minimum stationary duration to record as a stationary period (minutes).
_MIN_STATIONARY_MINUTES = 5.0

# Minimum time delta (seconds) between points to attempt speed calculation.
_MIN_TIME_DELTA_S = 1.0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _iso_to_epoch(ts: Optional[str]) -> Optional[float]:
    """Convert an ISO-8601 UTC string to a Unix float. Returns None on failure."""
    if not ts:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            t = time.strptime(ts, fmt)
            return float(calendar.timegm(t))
        except ValueError:
            continue
    return None


def _epoch_to_iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _flat_lat_lon(point: Dict[str, Any]) -> tuple[Optional[float], Optional[float]]:
    """Extract (lat, lon) from a point dict, handling nested 'gps' sub-dicts."""
    lat = point.get("lat")
    lon = point.get("lon")
    if lat is None or lon is None:
        gps = point.get("gps") or {}
        lat = gps.get("lat")
        lon = gps.get("lon")
    return lat, lon


def _sort_by_time(locations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sort locations chronologically; items with no timestamp go last."""
    return sorted(locations, key=lambda r: r.get("timestamp") or "9999")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_time_between_points(p1: Dict[str, Any], p2: Dict[str, Any]) -> float:
    """Calculate the time elapsed between two GPS points in hours.

    Args:
        p1: Earlier point dict with an optional ``'timestamp'`` key.
        p2: Later point dict with an optional ``'timestamp'`` key.

    Returns:
        Time difference in hours.  Returns ``0.0`` if either timestamp is
        missing or unparseable.
    """
    ep1 = _iso_to_epoch(p1.get("timestamp"))
    ep2 = _iso_to_epoch(p2.get("timestamp"))
    if ep1 is None or ep2 is None:
        return 0.0
    return abs(ep2 - ep1) / 3600.0


def calculate_speed(prev: Dict[str, Any], curr: Dict[str, Any]) -> float:
    """Calculate the speed in km/h between two consecutive GPS points.

    Speed is computed as the Haversine distance divided by the elapsed time.

    Args:
        prev: Previous GPS point dict (with ``'lat'``, ``'lon'``,
              ``'timestamp'``).
        curr: Current GPS point dict.

    Returns:
        Speed in km/h.  Returns ``0.0`` if time delta is zero or if either
        point lacks valid coordinates.
    """
    lat1, lon1 = _flat_lat_lon(prev)
    lat2, lon2 = _flat_lat_lon(curr)
    if any(v is None for v in (lat1, lon1, lat2, lon2)):
        return 0.0

    ep1 = _iso_to_epoch(prev.get("timestamp"))
    ep2 = _iso_to_epoch(curr.get("timestamp"))
    if ep1 is None or ep2 is None:
        return 0.0

    time_s = abs(ep2 - ep1)
    if time_s < _MIN_TIME_DELTA_S:
        return 0.0

    dist_km = calculate_distance(float(lat1), float(lon1), float(lat2), float(lon2))
    time_h = time_s / 3600.0
    return dist_km / time_h if time_h > 0 else 0.0


def classify_movement_type(speed_kmh: float) -> str:
    """Classify movement type from a speed value.

    Thresholds:
        * stationary — speed < 5 km/h
        * walking    — 5 ≤ speed < 15 km/h
        * biking     — 15 ≤ speed < 30 km/h
        * driving    — speed ≥ 30 km/h

    Args:
        speed_kmh: Speed in kilometres per hour.

    Returns:
        One of ``'stationary'``, ``'walking'``, ``'biking'``, ``'driving'``.
    """
    if speed_kmh < _STATIONARY_MAX:
        return "stationary"
    if speed_kmh < _WALKING_MAX:
        return "walking"
    if speed_kmh < _BIKING_MAX:
        return "biking"
    return "driving"


def detect_stationary_periods(locations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Detect contiguous periods during which the subject was stationary.

    Iterates through consecutive point pairs.  Whenever the movement type
    is ``'stationary'``, the period is extended; when a non-stationary
    segment is encountered, the ongoing stationary window is closed and
    recorded if it exceeds ``_MIN_STATIONARY_MINUTES``.

    Args:
        locations: Chronologically ordered list of GPS point dicts.

    Returns:
        List of stationary period dicts::

            [
                {
                    'start':    str,   # ISO timestamp of first stationary point
                    'end':      str,   # ISO timestamp of last stationary point
                    'duration': float, # duration in hours
                    'lat':      float, # representative latitude
                    'lon':      float, # representative longitude
                },
                ...
            ]
    """
    sorted_locs = _sort_by_time(locations)
    periods: List[Dict[str, Any]] = []

    # Filter to points with valid GPS + timestamp.
    valid: List[Dict[str, Any]] = []
    for loc in sorted_locs:
        lat, lon = _flat_lat_lon(loc)
        if lat is not None and lon is not None and loc.get("timestamp"):
            valid.append(loc)

    if len(valid) < 2:
        return periods

    # Sliding window: track start of current stationary block.
    stat_start_idx: Optional[int] = None

    for i in range(1, len(valid)):
        speed = calculate_speed(valid[i - 1], valid[i])
        mtype = classify_movement_type(speed)

        if mtype == "stationary":
            if stat_start_idx is None:
                stat_start_idx = i - 1
        else:
            if stat_start_idx is not None:
                _maybe_add_period(valid, stat_start_idx, i - 1, periods)
                stat_start_idx = None

    # Close any open window at the end.
    if stat_start_idx is not None:
        _maybe_add_period(valid, stat_start_idx, len(valid) - 1, periods)

    return periods


def _maybe_add_period(
    valid: List[Dict[str, Any]],
    start_idx: int,
    end_idx: int,
    periods: List[Dict[str, Any]],
) -> None:
    """Helper: add a stationary period if it exceeds the minimum duration."""
    ep_start = _iso_to_epoch(valid[start_idx].get("timestamp"))
    ep_end = _iso_to_epoch(valid[end_idx].get("timestamp"))
    if ep_start is None or ep_end is None:
        return
    duration_h = abs(ep_end - ep_start) / 3600.0
    duration_min = duration_h * 60.0
    if duration_min < _MIN_STATIONARY_MINUTES:
        return

    lat, lon = _flat_lat_lon(valid[start_idx])
    periods.append(
        {
            "start": valid[start_idx]["timestamp"],
            "end": valid[end_idx]["timestamp"],
            "duration": round(duration_h, 4),
            "lat": lat,
            "lon": lon,
        }
    )


def calculate_movement_statistics(locations: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Calculate aggregate movement statistics across all GPS points.

    Args:
        locations: Chronologically ordered list of GPS location dicts.

    Returns:
        ::

            {
                'total_distance': float,        # km
                'avg_speed':      float,        # km/h (over moving segments)
                'max_speed':      float,        # km/h
                'movement_types': {
                    'stationary': int,
                    'walking':    int,
                    'biking':     int,
                    'driving':    int,
                },
                'segment_count':  int,
                'total_time':     float,        # hours between first and last point
            }
    """
    sorted_locs = _sort_by_time(locations)
    valid: List[Dict[str, Any]] = []
    for loc in sorted_locs:
        lat, lon = _flat_lat_lon(loc)
        if lat is not None and lon is not None:
            valid.append(loc)

    stats: Dict[str, Any] = {
        "total_distance": 0.0,
        "avg_speed": 0.0,
        "max_speed": 0.0,
        "movement_types": {"stationary": 0, "walking": 0, "biking": 0, "driving": 0},
        "segment_count": 0,
        "total_time": 0.0,
    }

    if len(valid) < 2:
        return stats

    speeds: List[float] = []
    total_dist = 0.0
    type_counts: Dict[str, int] = {
        "stationary": 0,
        "walking": 0,
        "biking": 0,
        "driving": 0,
    }

    for i in range(1, len(valid)):
        lat1, lon1 = _flat_lat_lon(valid[i - 1])
        lat2, lon2 = _flat_lat_lon(valid[i])
        if any(v is None for v in (lat1, lon1, lat2, lon2)):
            continue

        dist = calculate_distance(float(lat1), float(lon1), float(lat2), float(lon2))
        total_dist += dist
        speed = calculate_speed(valid[i - 1], valid[i])
        speeds.append(speed)
        mtype = classify_movement_type(speed)
        type_counts[mtype] = type_counts.get(mtype, 0) + 1

    # Total time between first and last valid timestamped point.
    ep_first = _iso_to_epoch(valid[0].get("timestamp"))
    ep_last = _iso_to_epoch(valid[-1].get("timestamp"))
    total_time = abs(ep_last - ep_first) / 3600.0 if (ep_first and ep_last) else 0.0

    moving_speeds = [s for s in speeds if s > 0]
    avg_speed = sum(moving_speeds) / len(moving_speeds) if moving_speeds else 0.0

    stats["total_distance"] = round(total_dist, 3)
    stats["avg_speed"] = round(avg_speed, 2)
    stats["max_speed"] = round(max(speeds), 2) if speeds else 0.0
    stats["movement_types"] = type_counts
    stats["segment_count"] = len(speeds)
    stats["total_time"] = round(total_time, 3)
    return stats


def detect_movement_pattern(locations: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Detect movement patterns across a list of GPS locations.

    Produces a comprehensive movement report including an overall pattern
    label, a per-segment breakdown, and aggregate statistics.

    Args:
        locations: List of GPS location dicts with ``'lat'``, ``'lon'``,
                   and ``'timestamp'`` keys.

    Returns:
        ::

            {
                'overall':    str,   # dominant movement type
                'segments':   [
                    {
                        'from_timestamp': str,
                        'to_timestamp':   str,
                        'distance_km':    float,
                        'speed_kmh':      float,
                        'movement_type':  str,
                    },
                    ...
                ],
                'statistics': { ... },   # output of calculate_movement_statistics
                'stationary_periods': [...],  # output of detect_stationary_periods
            }
    """
    sorted_locs = _sort_by_time(locations)
    valid: List[Dict[str, Any]] = []
    for loc in sorted_locs:
        lat, lon = _flat_lat_lon(loc)
        if lat is not None and lon is not None:
            valid.append(loc)

    segments: List[Dict[str, Any]] = []

    for i in range(1, len(valid)):
        lat1, lon1 = _flat_lat_lon(valid[i - 1])
        lat2, lon2 = _flat_lat_lon(valid[i])
        if any(v is None for v in (lat1, lon1, lat2, lon2)):
            continue

        speed = calculate_speed(valid[i - 1], valid[i])
        dist = calculate_distance(float(lat1), float(lon1), float(lat2), float(lon2))
        segments.append(
            {
                "from_timestamp": valid[i - 1].get("timestamp"),
                "to_timestamp": valid[i].get("timestamp"),
                "distance_km": round(dist, 4),
                "speed_kmh": round(speed, 2),
                "movement_type": classify_movement_type(speed),
            }
        )

    # Overall label: most common non-stationary type; fallback to 'stationary'.
    type_counts: Dict[str, int] = {}
    for seg in segments:
        mt = seg["movement_type"]
        type_counts[mt] = type_counts.get(mt, 0) + 1

    if type_counts:
        overall = max(type_counts, key=lambda k: type_counts[k])
    else:
        overall = "stationary"

    statistics = calculate_movement_statistics(locations)
    stationary_periods = detect_stationary_periods(locations)

    return {
        "overall": overall,
        "segments": segments,
        "statistics": statistics,
        "stationary_periods": stationary_periods,
    }
