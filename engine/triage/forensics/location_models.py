"""Location data models for the forensics location-tracing subsystem.

All models are JSON-serialisable dataclasses with ``to_dict()`` methods.
GPS coordinate validation is performed in ``__post_init__`` where applicable.

Usage::

    from engine.triage.forensics.location_models import (
        MediaLocation, LocationCluster, LocationAnomaly, LocationTrace
    )
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean_dict(d: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively make a dict JSON-safe (convert sets → lists, etc.)."""
    out: Dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out[k] = _clean_dict(v)
        elif isinstance(v, (list, tuple)):
            out[k] = [_clean_dict(i) if isinstance(i, dict) else i for i in v]
        elif isinstance(v, set):
            out[k] = sorted(v)
        else:
            out[k] = v
    return out


def _validate_lat(lat: Optional[float]) -> None:
    if lat is not None and not (-90.0 <= lat <= 90.0):
        raise ValueError(f"Invalid latitude: {lat!r} (must be -90 to 90)")


def _validate_lon(lon: Optional[float]) -> None:
    if lon is not None and not (-180.0 <= lon <= 180.0):
        raise ValueError(f"Invalid longitude: {lon!r} (must be -180 to 180)")


# ---------------------------------------------------------------------------
# MediaLocation
# ---------------------------------------------------------------------------

@dataclass
class MediaLocation:
    """A single media file with an associated physical location.

    Attributes:
        file_path     -- Absolute path to the media file
        file_name     -- Basename of the media file
        latitude      -- GPS latitude in decimal degrees (None if absent)
        longitude     -- GPS longitude in decimal degrees (None if absent)
        altitude      -- GPS altitude in metres (None if absent)
        timestamp     -- ISO-8601 timestamp (from EXIF or filename)
        device_make   -- Camera/phone manufacturer (e.g. 'Samsung')
        device_model  -- Camera/phone model (e.g. 'SM-G991B')
        software      -- Software tag (e.g. 'WhatsApp', 'Telegram')
        source_app    -- Which messaging app the file came from
        media_type    -- 'image' | 'video' | 'audio' | 'unknown'
        confidence    -- 'high' | 'medium' | 'low' (based on source)
    """

    file_path: str
    file_name: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    altitude: Optional[float] = None
    timestamp: Optional[str] = None
    device_make: Optional[str] = None
    device_model: Optional[str] = None
    software: Optional[str] = None
    source_app: str = "unknown"          # whatsapp / telegram / sms / instagram / unknown
    media_type: str = "unknown"          # image / video / audio / unknown
    confidence: str = "medium"           # high / medium / low

    def __post_init__(self) -> None:
        _validate_lat(self.latitude)
        _validate_lon(self.longitude)

    @property
    def has_gps(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    @property
    def gps(self) -> Optional[Dict[str, float]]:
        if self.has_gps:
            return {"lat": self.latitude, "lon": self.longitude}   # type: ignore[return-value]
        return None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["has_gps"] = self.has_gps
        d["gps"] = self.gps
        return _clean_dict(d)


# ---------------------------------------------------------------------------
# LocationCluster
# ---------------------------------------------------------------------------

@dataclass
class LocationCluster:
    """A geographic cluster of media locations (frequent/notable place).

    Attributes:
        center_lat      -- Cluster centroid latitude
        center_lon      -- Cluster centroid longitude
        locations       -- List of MediaLocation dicts belonging to this cluster
        count           -- Number of media items in the cluster
        first_visit     -- ISO-8601 timestamp of the earliest media item
        last_visit      -- ISO-8601 timestamp of the most recent media item
        place_type      -- 'home' | 'work' | 'frequent' | 'unknown'
    """

    center_lat: float
    center_lon: float
    locations: List[Dict[str, Any]] = field(default_factory=list)
    count: int = 0
    first_visit: Optional[str] = None
    last_visit: Optional[str] = None
    place_type: str = "unknown"          # home / work / frequent / unknown

    def __post_init__(self) -> None:
        _validate_lat(self.center_lat)
        _validate_lon(self.center_lon)
        if self.count == 0 and self.locations:
            self.count = len(self.locations)

    def to_dict(self) -> Dict[str, Any]:
        return _clean_dict(asdict(self))


# ---------------------------------------------------------------------------
# LocationAnomaly
# ---------------------------------------------------------------------------

@dataclass
class LocationAnomaly:
    """A detected anomaly in a subject's location pattern.

    Attributes:
        anomaly_type  -- 'late_night' | 'new_location' | 'unusual_pattern'
        latitude      -- Location where the anomaly was detected (or None)
        longitude     -- Location where the anomaly was detected (or None)
        timestamp     -- ISO-8601 timestamp of the anomalous event
        severity      -- 'info' | 'warn' | 'critical'
        explanation   -- Human-readable description of the anomaly
    """

    anomaly_type: str                    # late_night / new_location / unusual_pattern
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    timestamp: Optional[str] = None
    severity: str = "info"               # info / warn / critical
    explanation: str = ""

    def __post_init__(self) -> None:
        _validate_lat(self.latitude)
        _validate_lon(self.longitude)
        valid_types = {"late_night", "new_location", "unusual_pattern"}
        if self.anomaly_type not in valid_types:
            # Allow extension without raising; just normalise to 'unusual_pattern'
            self.anomaly_type = "unusual_pattern"
        valid_severities = {"info", "warn", "critical"}
        if self.severity not in valid_severities:
            self.severity = "info"

    def to_dict(self) -> Dict[str, Any]:
        return _clean_dict(asdict(self))


# ---------------------------------------------------------------------------
# LocationTrace
# ---------------------------------------------------------------------------

@dataclass
class LocationTrace:
    """The complete location trace for a subject derived from media evidence.

    Attributes:
        locations         -- Chronological list of MediaLocation dicts
        total_locations   -- Total number of location data points
        unique_places     -- Approximate number of distinct geographic clusters
        time_span_days    -- Calendar days between first and last location
        movement_pattern  -- Narrative summary (e.g. 'stationary' / 'mobile')
        anomalies         -- List of LocationAnomaly dicts detected in the trace
    """

    locations: List[Dict[str, Any]] = field(default_factory=list)
    total_locations: int = 0
    unique_places: int = 0
    time_span_days: Optional[float] = None
    movement_pattern: str = "unknown"    # stationary / local / mobile / unknown
    anomalies: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.total_locations == 0 and self.locations:
            self.total_locations = len(self.locations)

    @property
    def statistics(self) -> Dict[str, Any]:
        return {
            "total": self.total_locations,
            "unique_places": self.unique_places,
            "time_span_days": self.time_span_days,
            "movement_pattern": self.movement_pattern,
        }

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["statistics"] = self.statistics
        return _clean_dict(d)
