"""Place Identification — identify home, work, and frequent places from GPS data.

Analyses a stream of GPS location dicts (e.g. from ``media_location`` or
``gps_clustering``) and labels geographic clusters as *home*, *work*, or
*frequent* based on the time-of-day distribution of visits.

Algorithm outline:
    * Home  — cluster most visited between 22:00–06:00 UTC (night hours).
    * Work  — cluster most visited between 09:00–17:00 UTC on weekdays.
    * Frequent — any cluster with >= *min_visits* points (default 3).

Usage::

    from engine.triage.forensics.place_identification import identify_places_from_locations

    places = identify_places_from_locations(locations)
    # places == {'home': {...}, 'work': {...}, 'frequent_places': [...]}
"""

from __future__ import annotations

import calendar
import math
import time
from typing import Any, Dict, List, Optional

from .gps_clustering import cluster_gps_points, get_cluster_center


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_NIGHT_START_H = 22  # 10 PM UTC
_NIGHT_END_H = 6  # 6 AM UTC
_WORK_START_H = 9  # 9 AM UTC
_WORK_END_H = 17  # 5 PM UTC
_DEFAULT_MIN_VISITS = 3
_CLUSTER_RADIUS_KM = 0.5


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
    """Return UTC hour (0-23) from an ISO-8601 timestamp string."""
    ep = _iso_to_epoch(ts)
    if ep is None:
        return None
    return time.gmtime(ep).tm_hour


def _utc_weekday(ts: Optional[str]) -> Optional[int]:
    """Return weekday index (0=Monday … 6=Sunday) from an ISO-8601 timestamp."""
    ep = _iso_to_epoch(ts)
    if ep is None:
        return None
    return time.gmtime(ep).tm_wday  # 0=Mon, 6=Sun


def _is_night(ts: Optional[str]) -> bool:
    """Return True if the UTC hour falls in the night window (22:00–06:00)."""
    h = _utc_hour(ts)
    if h is None:
        return False
    return h >= _NIGHT_START_H or h < _NIGHT_END_H


def _is_work_hours(ts: Optional[str]) -> bool:
    """Return True if the timestamp is a weekday and within 09:00–17:00 UTC."""
    h = _utc_hour(ts)
    wd = _utc_weekday(ts)
    if h is None or wd is None:
        return False
    return wd <= 4 and _WORK_START_H <= h < _WORK_END_H  # Mon–Fri


def _filter_by_hour(locations: List[Dict[str, Any]], pred) -> List[Dict[str, Any]]:
    """Return locations whose timestamp satisfies *pred(timestamp)*."""
    return [loc for loc in locations if pred(loc.get("timestamp"))]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_place_name(lat: float, lon: float) -> str:
    """Get a human-readable place name from coordinates.

    Currently returns a formatted coordinate string.  In a deployment with
    network access, this could call a reverse-geocoding API (e.g. Nominatim).

    Args:
        lat: Latitude in decimal degrees.
        lon: Longitude in decimal degrees.

    Returns:
        Coordinate string in the form ``"<lat>, <lon>"``.
    """
    return f"{lat:.6f}, {lon:.6f}"


def calculate_place_confidence(place: Dict[str, Any]) -> float:
    """Calculate a confidence score (0.0–1.0) for a place identification.

    Score is based on:
        * Visit count    (40% weight) — saturates at 20 visits.
        * Hour coverage  (30% weight) — fraction of expected hours present.
        * Time span      (30% weight) — days of evidence; saturates at 30 days.

    Args:
        place: A cluster/place dict containing at least ``'count'``,
               ``'first_visit'``, ``'last_visit'``, and ``'points'``.

    Returns:
        Float in ``[0.0, 1.0]``.
    """
    count = place.get("count", 0)
    points = place.get("points", [])

    # Visit-count score (saturates at 20).
    visit_score = min(count / 20.0, 1.0)

    # Hour-coverage score: fraction of unique UTC hours seen, out of 24.
    hours = set()
    for p in points:
        h = _utc_hour(p.get("timestamp"))
        if h is not None:
            hours.add(h)
    hour_score = len(hours) / 24.0

    # Time-span score: days between first and last visit (saturates at 30).
    ep_first = _iso_to_epoch(place.get("first_visit"))
    ep_last = _iso_to_epoch(place.get("last_visit"))
    if ep_first is not None and ep_last is not None and ep_last > ep_first:
        span_days = (ep_last - ep_first) / 86400.0
        span_score = min(span_days / 30.0, 1.0)
    else:
        span_score = 0.0

    confidence = 0.4 * visit_score + 0.3 * hour_score + 0.3 * span_score
    return round(confidence, 4)


def identify_home_location(locations: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Identify the home location from GPS data.

    Filters locations visited during night hours (22:00–06:00 UTC) and
    returns the most-visited cluster in that subset.

    Args:
        locations: List of location dicts with ``'lat'``, ``'lon'``, and
                   ``'timestamp'`` keys.

    Returns:
        The densest night-time cluster dict (with an extra ``'place_type'``
        key set to ``'home'`` and a ``'confidence'`` score), or ``None`` if
        no night-time GPS data is available.
    """
    night_locs = _filter_by_hour(locations, _is_night)
    if not night_locs:
        return None
    clusters = cluster_gps_points(night_locs, radius_km=_CLUSTER_RADIUS_KM)
    if not clusters:
        return None
    best = clusters[0]  # already sorted by count descending
    center = best["center"]
    best["place_type"] = "home"
    best["place_name"] = get_place_name(center["lat"], center["lon"])
    best["confidence"] = calculate_place_confidence(best)
    return best


def identify_work_location(locations: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Identify the work location from GPS data.

    Filters locations visited during work hours (09:00–17:00 UTC, Mon–Fri)
    and returns the most-visited cluster in that subset.

    Args:
        locations: List of location dicts with ``'lat'``, ``'lon'``, and
                   ``'timestamp'`` keys.

    Returns:
        The densest work-hours cluster dict (with ``'place_type'`` set to
        ``'work'`` and a ``'confidence'`` score), or ``None`` if no
        work-hours GPS data is available.
    """
    work_locs = _filter_by_hour(locations, _is_work_hours)
    if not work_locs:
        return None
    clusters = cluster_gps_points(work_locs, radius_km=_CLUSTER_RADIUS_KM)
    if not clusters:
        return None
    best = clusters[0]
    center = best["center"]
    best["place_type"] = "work"
    best["place_name"] = get_place_name(center["lat"], center["lon"])
    best["confidence"] = calculate_place_confidence(best)
    return best


def identify_frequent_places(
    locations: List[Dict[str, Any]],
    min_visits: int = _DEFAULT_MIN_VISITS,
) -> List[Dict[str, Any]]:
    """Identify frequently visited places from GPS data.

    Clusters all GPS-bearing locations and returns clusters that meet or
    exceed the *min_visits* threshold.

    Args:
        locations: List of location dicts with ``'lat'`` and ``'lon'`` keys.
        min_visits: Minimum number of location points required for a cluster
                    to be reported.  Default ``3``.

    Returns:
        List of cluster dicts with extra keys ``'place_type'``,
        ``'place_name'``, and ``'confidence'``, sorted by descending visit
        count.
    """
    clusters = cluster_gps_points(locations, radius_km=_CLUSTER_RADIUS_KM)
    result: List[Dict[str, Any]] = []
    for cl in clusters:
        if cl["count"] >= min_visits:
            center = cl["center"]
            cl["place_type"] = "frequent"
            cl["place_name"] = get_place_name(center["lat"], center["lon"])
            cl["confidence"] = calculate_place_confidence(cl)
            result.append(cl)
    return result


def identify_places_from_locations(locations: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Identify home, work, and frequently visited places from GPS data.

    Orchestrates :func:`identify_home_location`, :func:`identify_work_location`,
    and :func:`identify_frequent_places` into a single result dict.

    Args:
        locations: List of location dicts.  Each dict must have at minimum
                   ``'lat'`` and ``'lon'`` numeric keys (or a nested ``'gps'``
                   sub-dict) and an optional ISO-8601 ``'timestamp'``.

    Returns:
        ::

            {
                'home':             <cluster dict or None>,
                'work':             <cluster dict or None>,
                'frequent_places':  [<cluster dict>, ...],
                'total_clusters':   int,
                'total_gps_points': int,
            }
    """
    # Normalise nested gps dicts to flat lat/lon.
    flat: List[Dict[str, Any]] = []
    for loc in locations:
        if loc.get("lat") is None and loc.get("gps"):
            gps = loc["gps"]
            flat_loc = dict(loc)
            flat_loc["lat"] = gps.get("lat")
            flat_loc["lon"] = gps.get("lon")
            flat.append(flat_loc)
        else:
            flat.append(loc)

    gps_locs = [
        l for l in flat if l.get("lat") is not None and l.get("lon") is not None
    ]

    home = identify_home_location(gps_locs)
    work = identify_work_location(gps_locs)
    frequent = identify_frequent_places(gps_locs)

    all_clusters = cluster_gps_points(gps_locs, radius_km=_CLUSTER_RADIUS_KM)

    return {
        "home": home,
        "work": work,
        "frequent_places": frequent,
        "total_clusters": len(all_clusters),
        "total_gps_points": len(gps_locs),
    }
