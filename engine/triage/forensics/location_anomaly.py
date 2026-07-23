"""Location Anomaly Detection — detect unusual patterns in GPS location data.

Analyses a chronological list of GPS location dicts and flags events that
deviate from the subject's typical pattern:

* **Late-night visits** — locations recorded between 23:00 and 05:00 UTC.
* **Unusual locations** — points that are more than 2 standard deviations
  from the subject's geographic centroid.
* **New locations**    — points in the most-recent portion of the timeline
  that are far from any cluster established in the earlier portion.

Each anomaly is returned as a plain dict so it can be serialised to JSON and
stored via ``case.write_derived``.

Usage::

    from engine.triage.forensics.location_anomaly import detect_location_anomalies

    anomalies = detect_location_anomalies(locations)
"""
from __future__ import annotations

import calendar
import math
import time
from typing import Any, Dict, List, Optional

from .gps_clustering import calculate_distance, cluster_gps_points


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_LATE_NIGHT_START_H = 23   # 11 PM UTC
_LATE_NIGHT_END_H   = 5    # 5 AM UTC

# Z-score threshold for unusual-location detection.
_UNUSUAL_SIGMA = 2.0

# Radius used to check whether a new-location point is near a prior cluster.
_NEW_LOCATION_RADIUS_KM = 1.0

# Fraction of the timeline used to establish the "historical" baseline.
_HISTORY_FRACTION = 0.70

# Anomaly score weights.
_W_NIGHT    = 0.35
_W_UNUSUAL  = 0.45
_W_NEW      = 0.20


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


def _utc_hour(ts: Optional[str]) -> Optional[int]:
    ep = _iso_to_epoch(ts)
    return time.gmtime(ep).tm_hour if ep is not None else None


def _is_late_night(ts: Optional[str]) -> bool:
    h = _utc_hour(ts)
    if h is None:
        return False
    return h >= _LATE_NIGHT_START_H or h < _LATE_NIGHT_END_H


def _flat_lat_lon(loc: Dict[str, Any]) -> tuple[Optional[float], Optional[float]]:
    lat = loc.get("lat")
    lon = loc.get("lon")
    if lat is None or lon is None:
        gps = loc.get("gps") or {}
        lat = gps.get("lat")
        lon = gps.get("lon")
    return lat, lon


def _centroid(locations: List[Dict[str, Any]]) -> tuple[float, float]:
    """Return arithmetic centroid of all valid GPS points."""
    lats, lons = [], []
    for loc in locations:
        lat, lon = _flat_lat_lon(loc)
        if lat is not None and lon is not None:
            lats.append(float(lat))
            lons.append(float(lon))
    if not lats:
        return 0.0, 0.0
    return sum(lats) / len(lats), sum(lons) / len(lons)


def _std_dev(values: List[float]) -> float:
    """Population standard deviation."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return math.sqrt(variance)


def _sort_by_time(locations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(locations, key=lambda r: r.get("timestamp") or "9999")


def _anomaly_dict(
    anomaly_type: str,
    loc: Dict[str, Any],
    severity: str,
    explanation: str,
) -> Dict[str, Any]:
    """Build a standard anomaly output dict."""
    lat, lon = _flat_lat_lon(loc)
    return {
        "type":        anomaly_type,
        "location":    {"lat": lat, "lon": lon},
        "timestamp":   loc.get("timestamp"),
        "severity":    severity,
        "explanation": explanation,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def calculate_anomaly_score(location: Dict[str, Any], history: List[Dict[str, Any]]) -> float:
    """Calculate an anomaly score (0.0–1.0) for a single location.

    Combines three binary signals with fixed weights:

    * Night-time visit    (weight 0.35)
    * Unusual distance    (weight 0.45) — > 2σ from the history centroid
    * New location        (weight 0.20) — > 1 km from any historical cluster

    A higher score indicates a more anomalous observation.

    Args:
        location: A single GPS location dict.
        history:  All prior GPS locations that form the baseline.

    Returns:
        Float in ``[0.0, 1.0]``.
    """
    score = 0.0

    # Night signal.
    if _is_late_night(location.get("timestamp")):
        score += _W_NIGHT

    lat, lon = _flat_lat_lon(location)
    if lat is None or lon is None or not history:
        return round(score, 4)

    # Distance-from-centroid signal.
    c_lat, c_lon = _centroid(history)
    distances = []
    for h in history:
        h_lat, h_lon = _flat_lat_lon(h)
        if h_lat is not None and h_lon is not None:
            distances.append(calculate_distance(float(h_lat), float(h_lon), c_lat, c_lon))

    if distances:
        mean_d  = sum(distances) / len(distances)
        sigma   = _std_dev(distances)
        loc_d   = calculate_distance(float(lat), float(lon), c_lat, c_lon)
        z_score = (loc_d - mean_d) / sigma if sigma > 0 else 0.0
        if z_score > _UNUSUAL_SIGMA:
            score += _W_UNUSUAL

    # New-location signal (far from any historical cluster centroid).
    clusters = cluster_gps_points(history, radius_km=_NEW_LOCATION_RADIUS_KM)
    is_new = True
    for cl in clusters:
        c = cl["center"]
        if calculate_distance(float(lat), float(lon), c["lat"], c["lon"]) <= _NEW_LOCATION_RADIUS_KM:
            is_new = False
            break
    if is_new:
        score += _W_NEW

    return round(min(score, 1.0), 4)


def detect_late_night_visits(locations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Detect location records timestamped between 23:00 and 05:00 UTC.

    Args:
        locations: List of GPS location dicts with ``'timestamp'`` keys.

    Returns:
        List of anomaly dicts with ``type='late_night_visit'`` and
        ``severity='warn'``.
    """
    anomalies: List[Dict[str, Any]] = []
    for loc in locations:
        ts = loc.get("timestamp")
        if _is_late_night(ts):
            h = _utc_hour(ts)
            anomalies.append(_anomaly_dict(
                anomaly_type="late_night_visit",
                loc=loc,
                severity="warn",
                explanation=(
                    f"Location recorded at {h:02d}:xx UTC — within the late-night "
                    f"window (23:00–05:00 UTC)."
                ),
            ))
    return anomalies


def detect_unusual_locations(locations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Detect locations that are statistically far from the subject's centroid.

    Computes the mean and standard deviation of distances from the geographic
    centroid of all valid GPS points.  Locations more than
    ``_UNUSUAL_SIGMA`` (2) standard deviations from the mean are flagged.

    Args:
        locations: List of GPS location dicts.

    Returns:
        List of anomaly dicts with ``type='unusual_location'`` and
        ``severity='warn'``.
    """
    # Collect valid points.
    valid: List[tuple[Dict[str, Any], float, float]] = []
    for loc in locations:
        lat, lon = _flat_lat_lon(loc)
        if lat is not None and lon is not None:
            valid.append((loc, float(lat), float(lon)))

    if len(valid) < 3:
        return []

    c_lat, c_lon = _centroid(locations)
    distances = [
        calculate_distance(lat, lon, c_lat, c_lon) for _, lat, lon in valid
    ]
    mean_d = sum(distances) / len(distances)
    sigma  = _std_dev(distances)

    if sigma == 0:
        return []

    anomalies: List[Dict[str, Any]] = []
    for (loc, lat, lon), dist in zip(valid, distances):
        z = (dist - mean_d) / sigma
        if z > _UNUSUAL_SIGMA:
            anomalies.append(_anomaly_dict(
                anomaly_type="unusual_location",
                loc=loc,
                severity="warn",
                explanation=(
                    f"Location is {dist:.1f} km from the centroid — "
                    f"{z:.1f}σ above mean ({mean_d:.1f} km)."
                ),
            ))
    return anomalies


def detect_new_locations(locations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Detect locations that have never been visited in the historical baseline.

    Splits the (chronologically sorted) location list into a *history* portion
    (first 70 %) and a *recent* portion (last 30 %).  Points in the recent
    portion that lie more than ``_NEW_LOCATION_RADIUS_KM`` from every cluster
    in the history are flagged as new locations.

    Args:
        locations: Chronologically ordered list of GPS location dicts.

    Returns:
        List of anomaly dicts with ``type='new_location'`` and
        ``severity='info'``.
    """
    sorted_locs = _sort_by_time(locations)
    n = len(sorted_locs)
    if n < 5:
        return []

    split = int(n * _HISTORY_FRACTION)
    history = sorted_locs[:split]
    recent  = sorted_locs[split:]

    # Build historical clusters.
    hist_clusters = cluster_gps_points(history, radius_km=_NEW_LOCATION_RADIUS_KM)

    anomalies: List[Dict[str, Any]] = []
    for loc in recent:
        lat, lon = _flat_lat_lon(loc)
        if lat is None or lon is None:
            continue

        is_new = True
        for cl in hist_clusters:
            c = cl["center"]
            if calculate_distance(float(lat), float(lon), c["lat"], c["lon"]) <= _NEW_LOCATION_RADIUS_KM:
                is_new = False
                break

        if is_new:
            anomalies.append(_anomaly_dict(
                anomaly_type="new_location",
                loc=loc,
                severity="info",
                explanation=(
                    f"Location ({lat:.5f}, {lon:.5f}) has not been visited "
                    f"in the historical baseline (first {int(_HISTORY_FRACTION * 100)}% "
                    f"of the timeline)."
                ),
            ))
    return anomalies


def detect_location_anomalies(locations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Detect all anomalous location patterns in a GPS location stream.

    Combines :func:`detect_late_night_visits`, :func:`detect_unusual_locations`,
    and :func:`detect_new_locations` into a single deduplicated and
    severity-sorted result.

    Severity ordering (descending):  ``'critical'`` > ``'warn'`` > ``'info'``.

    Args:
        locations: List of GPS location dicts with ``'lat'``, ``'lon'``, and
                   ``'timestamp'`` keys.

    Returns:
        List of anomaly dicts::

            [
                {
                    'type':        str,          # anomaly type identifier
                    'location':    {'lat', 'lon'},
                    'timestamp':   str | None,
                    'severity':    'warn' | 'info' | 'critical',
                    'explanation': str,
                },
                ...
            ]

        Sorted by severity (warn first), then by timestamp.
    """
    if not locations:
        return []

    all_anomalies: List[Dict[str, Any]] = []
    all_anomalies.extend(detect_late_night_visits(locations))
    all_anomalies.extend(detect_unusual_locations(locations))
    all_anomalies.extend(detect_new_locations(locations))

    # Deduplicate by (type, timestamp, lat, lon).
    seen: set = set()
    unique: List[Dict[str, Any]] = []
    for a in all_anomalies:
        loc = a.get("location") or {}
        key = (a["type"], a.get("timestamp"), loc.get("lat"), loc.get("lon"))
        if key not in seen:
            seen.add(key)
            unique.append(a)

    # Sort: severity descending, then timestamp ascending.
    _sev_order = {"critical": 0, "warn": 1, "info": 2}
    unique.sort(key=lambda a: (
        _sev_order.get(a["severity"], 99),
        a.get("timestamp") or "",
    ))
    return unique
