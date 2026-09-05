"""Acquisition activity events — per-source progress fed to the audit log and dashboard.

Every call to :func:`emit_acq_event` does two things atomically:

1. Appends a hash-chained audit-log entry via ``case.log()``.
2. Emits an ``acq_event`` Socket.IO message so the dashboard can update in real time.

Design rules
------------
* An event is only emitted when the engine **actually reaches** the collection boundary.
  There are no synthetic frontend-only events.
* ``status`` is one of ``queued | accessing | completed | skipped | failed`` — never a
  boolean.  "Not collected", "checked but empty", "permission denied" and "skipped" are
  all distinct values.
* Artifact paths are redacted before transmission.  Secrets (passwords, tokens,
  credentials) are replaced with ``[REDACTED]``.  Only the logical *source label* is
  shown by default; the physical path is included as a separate ``artifact_path`` field
  so the UI can gate its display.
* The ``icon`` field is a string key into the frontend ``SOURCE_ICON_MAP``; the engine
  never embeds SVG or binary data.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from .models import now_iso

# ---------------------------------------------------------------------------
# Source → icon key mapping
# ---------------------------------------------------------------------------
# Keep keys lower-case and stable — the frontend ``SOURCE_ICON_MAP`` in
# AcquisitionActivityPanel.tsx depends on them.

SOURCE_ICON_MAP: dict[str, str] = {
    # Messaging apps
    "telegram": "telegram",
    "whatsapp": "whatsapp",
    "instagram": "instagram",
    "snapchat": "snapchat",
    "signal": "signal",
    "sms": "sms",
    # System / OS
    "device": "device",
    "encryption": "shield",
    "contacts": "contacts",
    "calls": "calls",
    "notifications": "bell",
    "bluetooth": "bluetooth",
    "celltower": "celltower",
    "wifi": "wifi",
    "wifi_live": "wifi",
    "browser": "browser",
    "gallery": "gallery",
    "media": "gallery",
    "filesystem": "folder",
    "screenshot": "screenshot",
    # System-level root stages
    "bt_config": "bluetooth",
    "app_presence": "apps",
    "antiforensics": "shield_alert",
    "recent_tasks": "layout",
    # Intelligence / pipeline
    "intel": "sparkles",
    "aleapp": "tool",
    "recovery": "search",
    "screentime": "monitor",
    "search": "search",
    "maps": "map_pin",
    "location": "map_pin",
    # Fallback
    "unknown": "box",
}

# ---------------------------------------------------------------------------
# Path redaction
# ---------------------------------------------------------------------------

_REDACT_PATTERNS = [
    # Wi-Fi passwords / PSK
    re.compile(r'(?i)(psk|password|passwd|key|token|secret|credential)\s*[=:]\s*\S+'),
    # Bearer / auth tokens
    re.compile(r'(?i)(bearer|auth)\s+[A-Za-z0-9+/=._-]{8,}'),
    # Base64-ish blobs longer than 32 chars (keys, hashes written inline)
    re.compile(r'[A-Za-z0-9+/]{32,}={0,2}'),
]

_SAFE_PATH_PARTS = re.compile(
    r'/data/(data|user/\d+)/([^/]+)',  # package sandbox — keep package name, drop rest
)


def _redact_path(path: str) -> str:
    """Return a privacy-safe version of a device path.

    * Full credentials / tokens are replaced with ``[REDACTED]``.
    * ``/data/data/<pkg>/...`` → ``/data/data/<pkg>/…`` (deep path elided).
    * Everything else passes through unchanged.
    """
    if not path:
        return path

    # Elide deep app-sandbox paths — keep the package name, drop sub-directories
    # so the UI shows "…/data.whatsapp.net/…" rather than full sqlite internals.
    m = _SAFE_PATH_PARTS.search(path)
    if m:
        pkg = m.group(2)
        prefix = path[: m.start()]
        return f"{prefix}/data/{m.group(1)}/{pkg}/…"

    # Redact credential-looking values
    for pat in _REDACT_PATTERNS:
        path = pat.sub(lambda mo: mo.group(0).split("=")[0].split(":")[0] + "=[REDACTED]"
                       if "=" in mo.group(0) or ":" in mo.group(0)
                       else "[REDACTED]", path)
    return path


# ---------------------------------------------------------------------------
# ActivityEvent dataclass
# ---------------------------------------------------------------------------

@dataclass
class ActivityEvent:
    """One unit of acquisition activity, emitted at a real collection boundary."""

    id: str                       # UUID, stable for deduplication
    timestamp: str                # ISO-8601 UTC
    tier: str                     # "tier0" | "tier1" | "tier2"
    source: str                   # source key, e.g. "telegram"
    icon: str                     # icon key for the frontend
    action: str                   # human-readable action sentence
    status: str                   # "queued"|"accessing"|"completed"|"skipped"|"failed"
    artifact_path: str = ""       # logical path (already redacted)
    item_count: Optional[int] = None
    skip_reason: str = ""         # populated when status is "skipped" or "failed"
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Public emit helper
# ---------------------------------------------------------------------------

def emit_acq_event(
    case: Any,
    socketio: Any,
    *,
    source: str,
    action: str,
    status: str,
    tier: str = "tier0",
    artifact_path: str = "",
    item_count: Optional[int] = None,
    skip_reason: str = "",
    extra: Optional[dict[str, Any]] = None,
) -> None:
    """Log an acquisition activity event and broadcast it over Socket.IO.

    Parameters
    ----------
    case:
        Active :class:`~triage.custody.Case`.  Used to write the audit entry.
    socketio:
        Flask-SocketIO instance, or ``None`` (no-op).
    source:
        Source key, e.g. ``"telegram"``, ``"wifi"``, ``"contacts"``.
    action:
        Human-readable action sentence, e.g. ``"Parsing messages"``.
    status:
        One of ``queued | accessing | completed | skipped | failed``.
    tier:
        ``"tier0"``, ``"tier1"``, or ``"tier2"``.
    artifact_path:
        Logical artifact path.  Will be redacted before storage/transmission.
    item_count:
        Number of items processed, when known.
    skip_reason:
        Reason for skipping or failure, when applicable.
    extra:
        Additional key-value metadata written to the audit log only.
    """
    safe_path = _redact_path(artifact_path)
    icon = SOURCE_ICON_MAP.get(source, SOURCE_ICON_MAP["unknown"])
    ev = ActivityEvent(
        id=str(uuid.uuid4()),
        timestamp=now_iso(),
        tier=tier,
        source=source,
        icon=icon,
        action=action,
        status=status,
        artifact_path=safe_path,
        item_count=item_count,
        skip_reason=skip_reason,
        extra=extra or {},
    )

    # 1. Hash-chained audit log entry — always, regardless of socketio availability.
    audit_detail = action
    if skip_reason:
        audit_detail += f" — {skip_reason}"
    if item_count is not None:
        audit_detail += f" ({item_count} item(s))"

    audit_extra: dict[str, Any] = {"acq_event_id": ev.id, "source": source}
    if safe_path:
        audit_extra["artifact_path"] = safe_path
    if extra:
        audit_extra.update(extra)

    try:
        case.log(
            f"acq.{source}.{status}",
            audit_detail,
            result=status if status in ("ok", "skipped", "error") else "ok",
            tier=tier,
            **audit_extra,
        )
    except Exception:
        pass  # audit failure must never abort an acquisition

    # 2. Socket.IO broadcast — best-effort.
    if socketio is None:
        return
    try:
        socketio.emit("acq_event", ev.to_dict())
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Mock corpus helper — emit realistic events for a synthetic acquisition
# ---------------------------------------------------------------------------

MOCK_EVENT_SEQUENCE: list[dict[str, Any]] = [
    {"source": "device",       "tier": "tier0", "action": "Reading device identifiers",             "status": "completed"},
    {"source": "encryption",   "tier": "tier0", "action": "Determining encryption state (FBE/AFU)", "status": "completed"},
    {"source": "filesystem",   "tier": "tier0", "action": "Enumerating shared storage",             "status": "accessing"},
    {"source": "gallery",      "tier": "tier0", "action": "Reading exported media metadata",        "status": "completed", "item_count": 47},
    {"source": "sms",          "tier": "tier0", "action": "Parsing SMS messages",                   "status": "completed", "item_count": 312},
    {"source": "contacts",     "tier": "tier0", "action": "Parsing contacts",                       "status": "completed", "item_count": 89},
    {"source": "calls",        "tier": "tier0", "action": "Parsing call log",                       "status": "completed", "item_count": 204},
    {"source": "whatsapp",     "tier": "tier0", "action": "Checking accessible database",           "status": "accessing", "artifact_path": "/sdcard/WhatsApp/Databases/msgstore.db"},
    {"source": "whatsapp",     "tier": "tier0", "action": "Parsing messages",                       "status": "completed", "item_count": 1834},
    {"source": "telegram",     "tier": "tier0", "action": "Checking accessible database",           "status": "accessing", "artifact_path": "/sdcard/Telegram/"},
    {"source": "telegram",     "tier": "tier0", "action": "Parsing messages",                       "status": "completed", "item_count": 561},
    {"source": "instagram",    "tier": "tier0", "action": "Checking accessible database",           "status": "skipped",   "skip_reason": "App-private storage — requires root (Tier 2)"},
    {"source": "snapchat",     "tier": "tier0", "action": "Checking accessible database",           "status": "skipped",   "skip_reason": "App-private storage — requires root (Tier 2)"},
    {"source": "browser",      "tier": "tier0", "action": "Parsing shared browser history",        "status": "completed", "item_count": 73},
    {"source": "wifi_live",    "tier": "tier0", "action": "Capturing live Wi-Fi state (volatile)",  "status": "completed", "item_count": 6},
    {"source": "notifications","tier": "tier0", "action": "Reading notification history",           "status": "completed", "item_count": 28},
    {"source": "bluetooth",    "tier": "tier0", "action": "Reading Bluetooth history",              "status": "completed", "item_count": 5},
    {"source": "celltower",    "tier": "tier0", "action": "Reading cell tower history",             "status": "completed"},
    {"source": "screentime",   "tier": "tier0", "action": "Reading screen and app-usage events",   "status": "completed", "item_count": 14},
    {"source": "maps",         "tier": "tier0", "action": "Extracting Maps / location history",    "status": "completed", "item_count": 19},
    {"source": "recovery",     "tier": "tier0", "action": "Recovering deleted records (SQLite)",   "status": "completed", "item_count": 38},
    {"source": "intel",        "tier": "tier0", "action": "Finalising analysis and report",        "status": "completed"},
]


def emit_mock_events(case: Any, socketio: Any) -> None:
    """Emit the canned event sequence for a mock/synthetic acquisition.

    Called once after mock pull completes so the dashboard shows realistic
    activity without inventing non-existent collection steps.
    """
    import time as _time

    for ev_data in MOCK_EVENT_SEQUENCE:
        emit_acq_event(
            case,
            socketio,
            source=ev_data["source"],
            tier=ev_data.get("tier", "tier0"),
            action=ev_data["action"],
            status=ev_data["status"],
            artifact_path=ev_data.get("artifact_path", ""),
            item_count=ev_data.get("item_count"),
            skip_reason=ev_data.get("skip_reason", ""),
        )
        # Small delay so the UI renders events progressively, not all at once.
        _time.sleep(0.05)
