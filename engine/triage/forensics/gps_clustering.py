"""GPS Clustering — group nearby GPS points into geographic clusters.

Uses a greedy nearest-centroid algorithm with configurable radius (km).
Distances are calculated using the Haversine formula.

All functions consume and return plain JSON-serialisable dicts so that they
compose cleanly with the rest of the forensics location-tracing subsystem.

Typical usage::

    from engine.triage.forensics.gps_clustering import cluster_gps_points

    locations = [{"lat": 28.61, "lon": 77.20, "timestamp": "2024-03-01T09:00:00Z"}, ...]
    clusters  = cluster_gps_points(locations, radius_km=0.5)
"""
from __future__ import annotations

import calendar
import math
import time
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EARTH_RADIUS_KM = 6371.0


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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great-circle distance in kilometres between two GPS points.

    Uses the Haversine formula, which gives geodesic distance accurate to
    within ~0.5% for the distances typically encountered in forensic work.

    Args:
        lat1: Latitude of the first point in decimal degrees.
        lon1: Longitude of the first point in decimal degrees.
        lat2: Latitude of the second point in decimal degrees.
        lon2: Longitude of the second point in decimal degrees.

    Returns:
        Distance in kilometres as a float.
    """
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lam = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lam / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def get_cluster_center(points: List[Dict[str, Any]]) -> Dict[str, float]:
    """Calculate the arithmetic centroid of a list of GPS points.

    The centroid is computed as the mean latitude and mean longitude.  This is
    geometrically exact for small clusters (< ~100 km diameter) and is
    consistent with the cluster-building algorithm's assumptions.

    Args:
        points: List of dicts, each containing ``'lat'`` and ``'lon'`` keys.

    Returns:
        ``{'lat': float, 'lon': float}``; ``{'lat': 0.0, 'lon': 0.0}`` if
        *points* is empty or contains no valid coordinates.
    """
    lats, lons = [], []
    for p in points:
        lat = p.get("lat")
        lon = p.get("lon")
        if lat is not None and lon is not None:
            lats.append(float(lat))
            lons.append(float(lon))
    if not lats:
        return {"lat": 0.0, "lon": 0.0}
    return {"lat": sum(lats) / len(lats), "lon": sum(lons) / len(lons)}


def get_cluster_bounds(points: List[Dict[str, Any]]) -> Dict[str, float]:
    """Get the bounding box of a cluster's points.

    Args:
        points: List of dicts with ``'lat'`` and ``'lon'`` keys.

    Returns:
        ``{'min_lat': x, 'max_lat': y, 'min_lon': x, 'max_lon': y}``
    """
    lats, lons = [], []
    for p in points:
        lat = p.get("lat")
        lon = p.get("lon")
        if lat is not None and lon is not None:
            lats.append(float(lat))
            lons.append(float(lon))
    if not lats:
        return {"min_lat": 0.0, "max_lat": 0.0, "min_lon": 0.0, "max_lon": 0.0}
    return {
        "min_lat": min(lats),
        "max_lat": max(lats),
        "min_lon": min(lons),
        "max_lon": max(lons),
    }


def get_cluster_visit_pattern(cluster: Dict[str, Any]) -> Dict[str, Any]:
    """Analyse the visit pattern of a cluster.

    Parses the timestamps of all points in the cluster to derive:

    * ``first_visit`` — ISO timestamp of the earliest point.
    * ``last_visit``  — ISO timestamp of the most recent point.
    * ``visit_count`` — number of distinct points.
    * ``average_duration`` — mean inter-visit gap in hours (0.0 if < 2 points).

    Args:
        cluster: A cluster dict as returned by :func:`cluster_gps_points`.

    Returns:
        Dict with keys ``first_visit``, ``last_visit``, ``visit_count``,
        and ``average_duration`` (hours).
    """
    points: List[Dict[str, Any]] = cluster.get("points", [])
    epochs: List[float] = []
    for p in points:
        ts = p.get("timestamp")
        ep = _iso_to_epoch(ts)
        if ep is not None:
            epochs.append(ep)

    if not epochs:
        return {
            "first_visit": cluster.get("first_visit"),
            "last_visit": cluster.get("last_visit"),
            "visit_count": cluster.get("count", len(points)),
            "average_duration": 0.0,
        }

    epochs.sort()
    first_visit = _epoch_to_iso(epochs[0])
    last_visit  = _epoch_to_iso(epochs[-1])
    visit_count = len(epochs)

    if visit_count >= 2:
        gaps_hours = [
            (epochs[i] - epochs[i - 1]) / 3600.0
            for i in range(1, len(epochs))
        ]
        average_duration = sum(gaps_hours) / len(gaps_hours)
    else:
        average_duration = 0.0

    return {
        "first_visit": first_visit,
        "last_visit": last_visit,
        "visit_count": visit_count,
        "average_duration": round(average_duration, 3),
    }


def cluster_gps_points(
    locations: List[Dict[str, Any]],
    radius_km: float = 0.5,
) -> List[Dict[str, Any]]:
    """Group nearby GPS points into geographic clusters.

    Uses a greedy nearest-centroid algorithm:

    1. For each incoming location, compute its distance to every existing
       cluster centroid.
    2. If the nearest centroid is within *radius_km*, assign the location to
       that cluster and update the centroid (running mean).
    3. Otherwise, start a new cluster.

    The algorithm runs in O(N x K) where K is the number of clusters — fast
    enough for typical forensic media collections (< 50 000 points).

    Args:
        locations: List of location dicts.  Each dict must have ``'lat'``
            (float) and ``'lon'`` (float) keys at the top level, or nested
            under a ``'gps'`` sub-dict (as produced by ``media_location``).
            An optional ``'timestamp'`` (ISO-8601 str) is used for visit
            pattern calculation.  All other keys are preserved as-is.

        radius_km: Maximum distance (km) from the cluster centroid for a
            point to be assigned to that cluster.  Default ``0.5`` (500 m).

    Returns:
        List of cluster dicts, ordered by descending point count::

            [
                {
                    'center':      {'lat': float, 'lon': float},
                    'points':      [<location dict>, ...],
                    'count':       int,
                    'first_visit': str | None,
                    'last_visit':  str | None,
                    'bounds':      {'min_lat': x, 'max_lat': y,
                                    'min_lon': x, 'max_lon': y},
                },
                ...
            ]
    """
    # Separate points that have valid GPS coordinates.
    gps_points: List[Dict[str, Any]] = []
    for loc in locations:
        lat = loc.get("lat")
        lon = loc.get("lon")
        # Also handle nested gps dict (from media_location output).
        if lat is None or lon is None:
            gps = loc.get("gps") or {}
            lat = gps.get("lat")
            lon = gps.get("lon")
        if lat is not None and lon is not None:
            # Normalise to a flat dict with top-level lat/lon.
            norm = dict(loc)
            norm["lat"] = float(lat)
            norm["lon"] = float(lon)
            gps_points.append(norm)

    if not gps_points:
        return []

    # Internal cluster representation: centroid floats + points list.
    clusters: List[Dict[str, Any]] = []

    for point in gps_points:
        p_lat = point["lat"]
        p_lon = point["lon"]

        # Find the closest existing cluster centroid.
        best_idx: Optional[int] = None
        best_dist = float("inf")
        for idx, cl in enumerate(clusters):
            dist = calculate_distance(p_lat, p_lon, cl["center_lat"], cl["center_lon"])
            if dist < best_dist:
                best_dist = dist
                best_idx = idx

        if best_idx is not None and best_dist <= radius_km:
            # Assign to existing cluster and update centroid (online mean).
            cl = clusters[best_idx]
            cl["points"].append(point)
            n = len(cl["points"])
            cl["center_lat"] = (cl["center_lat"] * (n - 1) + p_lat) / n
            cl["center_lon"] = (cl["center_lon"] * (n - 1) + p_lon) / n
        else:
            # Start a new single-point cluster.
            clusters.append({
                "center_lat": p_lat,
                "center_lon": p_lon,
                "points": [point],
            })

    # Build final output dicts.
    result: List[Dict[str, Any]] = []
    for cl in clusters:
        pts = cl["points"]
        timestamps = sorted(
            filter(None, (_iso_to_epoch(p.get("timestamp")) for p in pts))
        )
        first_visit = _epoch_to_iso(timestamps[0])  if timestamps else None
        last_visit  = _epoch_to_iso(timestamps[-1]) if timestamps else None

        center = {
            "lat": round(cl["center_lat"], 8),
            "lon": round(cl["center_lon"], 8),
        }
        result.append({
            "center":      center,
            "points":      pts,
            "count":       len(pts),
            "first_visit": first_visit,
            "last_visit":  last_visit,
            "bounds":      get_cluster_bounds(pts),
        })

    # Sort by descending point count (most-visited place first).
    result.sort(key=lambda c: c["count"], reverse=True)
    return result
