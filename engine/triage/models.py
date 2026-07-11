"""Typed data models shared across the engine.

Everything the acquisition/recovery/parsing layers produce is one of these dataclasses,
and everything serialises to plain JSON for the case folder and the dashboard API.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from .config import Confidence, Tier


def now_iso() -> str:
    """Current UTC timestamp in ISO-8601 with a trailing Z (no ambiguous local time)."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _clean(d: dict[str, Any]) -> dict[str, Any]:
    """Recursively convert enums to their value for JSON serialisation."""
    out: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, (Tier, Confidence)):
            out[k] = v.value
        elif isinstance(v, dict):
            out[k] = _clean(v)
        elif isinstance(v, list):
            out[k] = [_clean(i) if isinstance(i, dict) else i for i in v]
        else:
            out[k] = v
    return out


@dataclass
class Serialisable:
    def to_dict(self) -> dict[str, Any]:
        return _clean(asdict(self))


# --- Audit / custody --------------------------------------------------------
@dataclass
class AuditEvent(Serialisable):
    """One line in the append-only audit log."""

    timestamp: str
    action: str                      # e.g. "adb.pull", "pm.grant", "hash.compute"
    detail: str                      # human-readable summary
    examiner: str = ""
    command: str = ""                # exact shell command issued, if any
    result: str = "ok"               # "ok" | "error" | "skipped"
    alters_device: bool = False      # True if this action changed device state
    tier: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ArtifactRecord(Serialisable):
    """A single pulled file recorded in the hash manifest."""

    artifact_id: str
    source_path: str                 # path on the device
    stored_path: str                 # relative path inside the case folder
    size_bytes: int
    sha256: str
    md5: str
    tier: Tier
    method: str                      # "adb pull", "helper-apk", "mock", ...
    extracted_at: str
    category: str = "other"          # media/document/database/app-export/...
    app: Optional[str] = None        # whatsapp/telegram/... if attributable
    flags: list[str] = field(default_factory=list)  # e.g. ["trashed", "carved-source"]


# --- Parsed artifact rows ---------------------------------------------------
@dataclass
class Message(Serialisable):
    app: str
    sender: str
    body: str
    timestamp: Optional[str] = None      # ISO-8601 if parseable
    direction: str = "unknown"           # incoming/outgoing/unknown
    confidence: Confidence = Confidence.LIVE
    source_file: str = ""
    provenance: str = ""                 # e.g. "wal frame 12", "freelist page 4"
    flags: list[str] = field(default_factory=list)


@dataclass
class Contact(Serialisable):
    name: str
    number: str = ""
    email: str = ""
    confidence: Confidence = Confidence.LIVE
    source_file: str = ""


@dataclass
class CallRecord(Serialisable):
    number: str
    name: str = ""
    call_type: str = "unknown"           # incoming/outgoing/missed
    timestamp: Optional[str] = None
    duration_s: Optional[int] = None
    confidence: Confidence = Confidence.LIVE
    source_file: str = ""


@dataclass
class LocationPoint(Serialisable):
    latitude: float
    longitude: float
    source: str                          # "exif" | "dumpsys" | app name
    timestamp: Optional[str] = None
    label: str = ""
    source_file: str = ""


@dataclass
class MediaItem(Serialisable):
    artifact_id: str
    stored_path: str
    kind: str                            # image/video/audio/document
    size_bytes: int
    app: Optional[str] = None
    trashed: bool = False
    timestamp: Optional[str] = None
    gps: Optional[dict[str, float]] = None
    sha256: str = ""


@dataclass
class Flag(Serialisable):
    """A keyword / known-hash hit surfaced for analyst awareness."""

    kind: str                            # "keyword" | "known-hash"
    term: str
    context: str
    location: str                        # where it was found (file / message id)
    severity: str = "info"               # info/warn/critical


@dataclass
class TimelineEvent(Serialisable):
    timestamp: str
    kind: str                            # message/call/media/location
    summary: str
    confidence: Confidence = Confidence.LIVE
    ref: str = ""
