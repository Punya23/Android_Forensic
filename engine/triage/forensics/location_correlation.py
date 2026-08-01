"""Location correlation engine — link photo-level location evidence with messages.

Given a list of photo location dicts (from ``media_location``) and a list of
``Message`` objects (from ``engine.triage.models``), this module finds
connections between images and messages using three independent signals:

1. **Time proximity** — messages sent within a configurable window (default ±5 min).
2. **Coordinate mention** — messages whose body contains GPS coordinate patterns
   that match the photo location.
3. **Filename mention** — messages whose body contains the media filename.

A weighted correlation score (0–1) is calculated from the three signals, and
a significance tier is derived from the score.

Usage::

    from triage.forensics.location_correlation import (
        correlate_locations_with_messages,
        calculate_correlation_score,
        determine_significance,
    )
"""

from __future__ import annotations

import calendar
import re
import time
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Type alias — avoid importing models at module level to keep import light
# ---------------------------------------------------------------------------
# engine.triage.models.Message is imported lazily inside functions.

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Default time window (seconds) used in find_messages_near_time.
DEFAULT_WINDOW_S = 300  # 5 minutes

# Coordinate pattern: match floating-point numbers that look like GPS coords
# in message bodies (e.g. "48.8566, 2.3522" or "48.8566° N, 2.3522° E").
_COORD_RE = re.compile(
    r"[-+]?\d{1,3}\.\d{4,}\s*[°]?\s*[NSns]?\s*,?\s*[-+]?\d{1,3}\.\d{4,}"
)

# How close two floats need to be to be considered a match (degrees ~ 100 m)
_COORD_TOLERANCE = 0.01

# Correlation score weights
_WEIGHT_NEARBY = 0.4
_WEIGHT_MENTION = 0.4
_WEIGHT_PHOTO = 0.2

# Significance tier boundaries
_SCORE_HIGH = 0.8
_SCORE_MEDIUM = 0.6
_SCORE_LOW = 0.4


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _iso_to_epoch(ts: Optional[str]) -> Optional[float]:
    """Convert an ISO-8601 string (UTC) to a Unix timestamp float.

    Accepts the subset formats used by this engine (no sub-second precision).
    Returns None if the string cannot be parsed.
    """
    if not ts:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            t = time.strptime(ts, fmt)
            return float(calendar.timegm(t))
        except ValueError:
            continue
    return None


def _coords_in_text(text: str, lat: float, lon: float) -> bool:
    """Return True if *text* contains a coordinate pair close to (lat, lon)."""
    for m in _COORD_RE.finditer(text):
        raw = m.group(0)
        nums = re.findall(r"[-+]?\d+\.\d+", raw)
        if len(nums) >= 2:
            try:
                a, b = float(nums[0]), float(nums[1])
                if abs(a - lat) < _COORD_TOLERANCE and abs(b - lon) < _COORD_TOLERANCE:
                    return True
                # Also try reversed order (lon, lat)
                if abs(b - lat) < _COORD_TOLERANCE and abs(a - lon) < _COORD_TOLERANCE:
                    return True
            except ValueError:
                continue
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def find_messages_near_time(
    messages: List[Any],
    timestamp: str,
    window_seconds: int = DEFAULT_WINDOW_S,
) -> List[Any]:
    """Return messages sent within *window_seconds* of *timestamp*.

    Args:
        messages:        List of ``Message`` objects.
        timestamp:       ISO-8601 reference timestamp (e.g. photo EXIF time).
        window_seconds:  Half-width of the time window in seconds (default 300).

    Returns:
        Filtered list of ``Message`` objects within the time window.
    """
    ref_epoch = _iso_to_epoch(timestamp)
    if ref_epoch is None:
        return []

    result: List[Any] = []
    for msg in messages:
        msg_epoch = _iso_to_epoch(getattr(msg, "timestamp", None))
        if msg_epoch is None:
            continue
        if abs(msg_epoch - ref_epoch) <= window_seconds:
            result.append(msg)
    return result


def find_messages_mentioning_location(
    messages: List[Any],
    lat: float,
    lon: float,
) -> List[Any]:
    """Return messages whose body contains a coordinate pair near (lat, lon).

    The search uses a regex to find decimal coordinate patterns and compares
    them to the given coordinates within a tolerance of ~1 km.

    Args:
        messages:  List of ``Message`` objects.
        lat:       Target latitude in decimal degrees.
        lon:       Target longitude in decimal degrees.

    Returns:
        Filtered list of ``Message`` objects mentioning the location.
    """
    result: List[Any] = []
    for msg in messages:
        body = getattr(msg, "body", "") or ""
        if body and _coords_in_text(body, lat, lon):
            result.append(msg)
    return result


def find_messages_with_media(
    messages: List[Any],
    filename: str,
) -> List[Any]:
    """Return messages whose body contains *filename* (case-insensitive).

    Args:
        messages:  List of ``Message`` objects.
        filename:  Media filename to search for (e.g. 'IMG-20240317-WA0012.jpg').

    Returns:
        Filtered list of ``Message`` objects mentioning the filename.
    """
    if not filename:
        return []
    needle = filename.lower()
    result: List[Any] = []
    for msg in messages:
        body = (getattr(msg, "body", "") or "").lower()
        if needle in body:
            result.append(msg)
    return result


def calculate_correlation_score(
    nearby_msgs: List[Any],
    mention_msgs: List[Any],
    photo_msgs: List[Any],
) -> float:
    """Calculate a correlation score (0–1) from three independent signals.

    Weights:
        nearby_msgs   0.4  -- temporal proximity
        mention_msgs  0.4  -- coordinate mention in message body
        photo_msgs    0.2  -- filename mention in message body

    Each component contributes its weighted share only if the list is non-empty.
    The score saturates at 1.0.

    Args:
        nearby_msgs:   Messages found within the time window.
        mention_msgs:  Messages mentioning the location coordinates.
        photo_msgs:    Messages mentioning the media filename.

    Returns:
        Float in [0, 1].
    """
    score = 0.0
    if nearby_msgs:
        score += _WEIGHT_NEARBY
    if mention_msgs:
        score += _WEIGHT_MENTION
    if photo_msgs:
        score += _WEIGHT_PHOTO
    return min(round(score, 4), 1.0)


def determine_significance(correlation_score: float) -> str:
    """Translate a numeric correlation score into a significance tier.

    Tiers:
        HIGH     ≥ 0.8
        MEDIUM   ≥ 0.6
        LOW      ≥ 0.4
        MINIMAL  < 0.4

    Args:
        correlation_score: Float in [0, 1] from ``calculate_correlation_score``.

    Returns:
        One of ``'HIGH'``, ``'MEDIUM'``, ``'LOW'``, ``'MINIMAL'``.
    """
    if correlation_score >= _SCORE_HIGH:
        return "HIGH"
    if correlation_score >= _SCORE_MEDIUM:
        return "MEDIUM"
    if correlation_score >= _SCORE_LOW:
        return "LOW"
    return "MINIMAL"


def correlate_locations_with_messages(
    locations: List[Dict[str, Any]],
    messages: List[Any],
    window_seconds: int = DEFAULT_WINDOW_S,
) -> List[Dict[str, Any]]:
    """Correlate photo locations with messages using three evidence signals.

    For each location dict, this function searches the message list for:

    1. Messages sent within *window_seconds* of the photo timestamp.
    2. Messages whose body mentions the photo's GPS coordinates.
    3. Messages whose body contains the photo's filename.

    A weighted score is computed and a significance tier is attached.

    Args:
        locations:       List of location dicts (from ``media_location``).
        messages:        List of ``Message`` objects (from ``engine.triage.models``).
        window_seconds:  Half-width of the time window (default 300 s / 5 min).

    Returns:
        List of enriched location dicts, each with extra keys::

            nearby_messages   -- list of nearby Message dicts
            mention_messages  -- list of coordinate-mention Message dicts
            photo_messages    -- list of filename-mention Message dicts
            correlation_score -- float 0-1
            significance      -- 'HIGH' | 'MEDIUM' | 'LOW' | 'MINIMAL'
    """
    results: List[Dict[str, Any]] = []

    for loc in locations:
        gps = loc.get("gps") or {}
        lat = gps.get("lat")
        lon = gps.get("lon")
        timestamp = loc.get("timestamp")
        filename = loc.get("filename_meta", {}).get("raw") or ""
        if not filename:
            from pathlib import Path as _P

            filename = _P(loc.get("file", "")).name

        nearby = (
            find_messages_near_time(messages, timestamp, window_seconds)
            if timestamp
            else []
        )
        mention = (
            find_messages_mentioning_location(messages, lat, lon)
            if (lat and lon)
            else []
        )
        photo = find_messages_with_media(messages, filename)

        score = calculate_correlation_score(nearby, mention, photo)
        sig = determine_significance(score)

        enriched = dict(loc)
        enriched["nearby_messages"] = [_msg_to_dict(m) for m in nearby]
        enriched["mention_messages"] = [_msg_to_dict(m) for m in mention]
        enriched["photo_messages"] = [_msg_to_dict(m) for m in photo]
        enriched["correlation_score"] = score
        enriched["significance"] = sig
        results.append(enriched)

    return results


# ---------------------------------------------------------------------------
# Private serialisation helper
# ---------------------------------------------------------------------------


def _msg_to_dict(msg: Any) -> Dict[str, Any]:
    """Convert a Message object (or plain dict) to a plain dict."""
    if isinstance(msg, dict):
        return msg
    if hasattr(msg, "to_dict"):
        return msg.to_dict()
    # Fallback: extract known Message fields
    return {
        "app": getattr(msg, "app", ""),
        "sender": getattr(msg, "sender", ""),
        "body": getattr(msg, "body", ""),
        "timestamp": getattr(msg, "timestamp", None),
        "direction": getattr(msg, "direction", "unknown"),
    }
