"""Parsers for the expanded Collector helper-APK outputs (Tier 1, non-root).

The v0.2 Collector emits, in addition to contacts/calls/SMS:

    media_inventory.json   MediaStore catalogue (metadata; trashed/favorite/owner-app/GPS)
    apps.json              installed-app inventory with investigative classification
    accounts.json          device accounts (Google / WhatsApp / Telegram / …)
    calendar.json          calendar events
    usage.json             per-app foreground-usage telemetry
    location.json          last-known GPS fix per provider (the only direct coordinate a
                           non-root helper can get from Android)
    wifi.json              current association, saved networks, scan results (BSSIDs are
                           geolocatable, so this is a location trail as well as a network one)
    bluetooth.json         adapter identity + bonded devices
    collector_manifest.json per-run audit record: which collectors ran, what was denied

Each parser is tolerant of both a bare JSON array and a ``{"key": [...]}`` wrapper, and of
missing fields, so an OEM quirk degrades to fewer fields rather than an exception.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..models import (
    Account,
    AppUsage,
    CalendarEvent,
    InstalledApp,
    LocationPoint,
    MediaInventoryItem,
)

# --- timestamp helpers ------------------------------------------------------


def _ms_to_iso(value: Any) -> Optional[str]:
    try:
        ms = int(value)
        if ms <= 0:
            return None
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _s_to_iso(value: Any) -> Optional[str]:
    try:
        s = int(value)
        if s <= 0:
            return None
        return datetime.fromtimestamp(s, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _load(path: str | Path, *keys: str) -> list[dict]:
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    if isinstance(data, dict):
        for k in keys:
            if isinstance(data.get(k), list):
                return [r for r in data[k] if isinstance(r, dict)]
        # fall through to any list value
        for v in data.values():
            if isinstance(v, list):
                return [r for r in v if isinstance(r, dict)]
        return []
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    return []


# --- package → app-name mapping (mirrors the APK's KnownApps table) ----------
_PKG_APP = {
    "com.whatsapp": "WhatsApp",
    "com.whatsapp.w4b": "WhatsApp Business",
    "org.telegram.messenger": "Telegram",
    "org.thoughtcrime.securesms": "Signal",
    "com.instagram.android": "Instagram",
    "com.snapchat.android": "Snapchat",
    "com.facebook.orca": "Messenger",
    "com.facebook.katana": "Facebook",
    "com.viber.voip": "Viber",
    "com.google.android.apps.messaging": "Google Messages",
    "com.android.chrome": "Chrome",
    "com.zhiliaoapp.musically": "TikTok",
    "com.twitter.android": "X (Twitter)",
    "com.discord": "Discord",
}


def app_from_package(pkg: str | None) -> Optional[str]:
    if not pkg:
        return None
    if pkg in _PKG_APP:
        return _PKG_APP[pkg]
    # Derive a readable name from the last path component of the package id.
    tail = pkg.rsplit(".", 1)[-1]
    return tail.capitalize() if tail else None


# Dangerous permission groups, for scoring an app's granted permissions.
_DANGEROUS = {
    "READ_CONTACTS",
    "WRITE_CONTACTS",
    "READ_CALL_LOG",
    "WRITE_CALL_LOG",
    "READ_SMS",
    "SEND_SMS",
    "RECEIVE_SMS",
    "READ_PHONE_STATE",
    "CALL_PHONE",
    "CAMERA",
    "RECORD_AUDIO",
    "ACCESS_FINE_LOCATION",
    "ACCESS_COARSE_LOCATION",
    "ACCESS_BACKGROUND_LOCATION",
    "READ_EXTERNAL_STORAGE",
    "WRITE_EXTERNAL_STORAGE",
    "READ_MEDIA_IMAGES",
    "READ_MEDIA_VIDEO",
    "READ_MEDIA_AUDIO",
    "READ_CALENDAR",
    "WRITE_CALENDAR",
    "BODY_SENSORS",
    "GET_ACCOUNTS",
    "SYSTEM_ALERT_WINDOW",
}


def _short_perm(p: str) -> str:
    return p.rsplit(".", 1)[-1]


# --- parsers ----------------------------------------------------------------


def parse_media_inventory(path: str | Path) -> list[MediaInventoryItem]:
    src = Path(path).name
    out: list[MediaInventoryItem] = []
    for r in _load(path, "media", "data"):
        gps = None
        if r.get("gps_lat") is not None and r.get("gps_lon") is not None:
            try:
                gps = {"lat": float(r["gps_lat"]), "lon": float(r["gps_lon"])}
            except (TypeError, ValueError):
                gps = None
        pkg = str(r.get("owner_package") or "")
        out.append(
            MediaInventoryItem(
                media_id=int(r.get("id") or 0),
                kind=str(r.get("kind") or "other"),
                display_name=str(r.get("display_name") or ""),
                mime_type=str(r.get("mime_type") or ""),
                size_bytes=int(r.get("size") or 0),
                date_taken=_ms_to_iso(r.get("date_taken")),
                date_added=_s_to_iso(r.get("date_added")),
                date_modified=_s_to_iso(r.get("date_modified")),
                width=int(r.get("width") or 0),
                height=int(r.get("height") or 0),
                duration_ms=int(r.get("duration") or 0),
                bucket=str(r.get("bucket") or ""),
                owner_package=pkg,
                owner_app=app_from_package(pkg),
                relative_path=str(r.get("relative_path") or ""),
                data_path=str(r.get("data_path") or ""),
                is_trashed=bool(r.get("is_trashed")),
                is_favorite=bool(r.get("is_favorite")),
                is_pending=bool(r.get("is_pending")),
                date_expires=_s_to_iso(r.get("date_expires")),
                gps=gps,
                source_file=src,
            )
        )
    return out


def parse_apps(path: str | Path) -> list[InstalledApp]:
    src = Path(path).name
    out: list[InstalledApp] = []
    for r in _load(path, "apps", "data"):
        granted = r.get("granted_permissions") or []
        requested = r.get("requested_permissions") or []
        dangerous = sorted(
            {_short_perm(str(p)) for p in granted if _short_perm(str(p)) in _DANGEROUS}
        )
        fn = r.get("friendly_name")
        out.append(
            InstalledApp(
                package=str(r.get("package") or ""),
                label=str(r.get("label") or ""),
                version_name=str(r.get("version_name") or ""),
                version_code=int(r.get("version_code") or 0),
                first_install=_ms_to_iso(r.get("first_install")),
                last_update=_ms_to_iso(r.get("last_update")),
                installer=str(r.get("installer") or ""),
                is_system=bool(r.get("is_system")),
                category=str(r.get("category") or "other"),
                friendly_name=fn if isinstance(fn, str) else None,
                notable=bool(r.get("notable")),
                dangerous_granted=dangerous,
                permission_count=len(requested) if isinstance(requested, list) else 0,
                source_file=src,
            )
        )
    return out


def parse_accounts(path: str | Path) -> list[Account]:
    src = Path(path).name
    out: list[Account] = []
    for r in _load(path, "accounts", "data"):
        app = r.get("app")
        out.append(
            Account(
                name=str(r.get("name") or ""),
                type=str(r.get("type") or ""),
                app=app if isinstance(app, str) else None,
                source_file=src,
            )
        )
    return out


def parse_calendar(path: str | Path) -> list[CalendarEvent]:
    src = Path(path).name
    out: list[CalendarEvent] = []
    for r in _load(path, "calendar", "events", "data"):
        out.append(
            CalendarEvent(
                title=str(r.get("title") or "(no title)"),
                dtstart=_ms_to_iso(r.get("dtstart")),
                dtend=_ms_to_iso(r.get("dtend")),
                location=str(r.get("location") or ""),
                description=str(r.get("description") or ""),
                organizer=str(r.get("organizer") or ""),
                calendar=str(r.get("calendar") or ""),
                all_day=bool(r.get("all_day")),
                source_file=src,
            )
        )
    return out


def parse_usage(path: str | Path) -> list[AppUsage]:
    src = Path(path).name
    out: list[AppUsage] = []
    for r in _load(path, "usage", "data"):
        pkg = str(r.get("package") or "")
        ms = int(r.get("total_foreground_ms") or 0)
        out.append(
            AppUsage(
                package=pkg,
                total_foreground_ms=ms,
                total_foreground_min=round(ms / 60000.0, 1),
                last_used=_ms_to_iso(r.get("last_used")),
                friendly_name=app_from_package(pkg),
                category="other",
                source_file=src,
            )
        )
    return out


def media_inventory_summary(items: list[MediaInventoryItem]) -> dict[str, Any]:
    """Aggregate counts for the dashboard Overview / Media header."""
    by_kind: dict[str, int] = {}
    by_app: dict[str, int] = {}
    trashed = favorite = with_gps = 0
    total_bytes = 0
    for it in items:
        by_kind[it.kind] = by_kind.get(it.kind, 0) + 1
        if it.owner_app:
            by_app[it.owner_app] = by_app.get(it.owner_app, 0) + 1
        trashed += 1 if it.is_trashed else 0
        favorite += 1 if it.is_favorite else 0
        with_gps += 1 if it.gps else 0
        total_bytes += it.size_bytes
    return {
        "total": len(items),
        "by_kind": by_kind,
        "by_app": by_app,
        "trashed": trashed,
        "favorite": favorite,
        "with_gps": with_gps,
        "total_bytes": total_bytes,
    }


# --- location.json ----------------------------------------------------------


def parse_location(path: str | Path) -> list[LocationPoint]:
    """Parse the Collector's ``location.json`` into :class:`LocationPoint` rows.

    The APK records the OS's last-known fix for every location provider it can name
    (``gps`` / ``network`` / ``passive`` / ``fused``). This is the only *direct* coordinate a
    non-root, non-privileged helper can obtain from Android — everything else the engine
    reports as a location is inferred from media EXIF, cell towers, WiFi BSSIDs or app
    databases. It is therefore high-value and worth surfacing distinctly.

    A ``_meta`` row (written by the APK to record which providers were queried) carries no
    coordinate and is skipped here; :func:`location_collection_meta` reads it instead so the
    report can distinguish "no fix was cached" from "we never asked".
    """
    src = Path(path).name
    out: list[LocationPoint] = []
    for r in _load(path, "locations", "data"):
        provider = str(r.get("provider") or "")
        if provider == "_meta":
            continue
        lat, lon = _coord_pair(r.get("latitude"), r.get("longitude"))
        if lat is None or lon is None:
            continue
        label_bits = [f"last-known fix ({provider or 'unknown provider'})"]
        acc = r.get("accuracy")
        if isinstance(acc, (int, float)):
            label_bits.append(f"±{acc:.0f}m")
        if r.get("is_mock"):
            # A mocked fix means a location-spoofing app supplied this coordinate. It is
            # still evidence — of spoofing — but must never be presented as a real position.
            label_bits.append("MOCK PROVIDER")
        out.append(
            LocationPoint(
                latitude=lat,
                longitude=lon,
                source=f"collector:{provider}" if provider else "collector",
                timestamp=_ms_to_iso(r.get("time")),
                label=" ".join(label_bits),
                source_file=src,
            )
        )
    return out


def location_collection_meta(path: str | Path) -> dict[str, Any]:
    """Return the ``_meta`` row from ``location.json`` (providers queried, permission held).

    Kept separate from :func:`parse_location` so an empty coordinate list stays explainable:
    zero fixes with ``providers_seen`` populated means the device held no cached position,
    which is a different finding from the collector never having run.
    """
    for r in _load(path, "locations", "data"):
        if str(r.get("provider") or "") == "_meta":
            seen = r.get("providers_seen")
            return {
                "providers_seen": [str(p) for p in seen] if isinstance(seen, list) else [],
                "permission": str(r.get("permission") or ""),
                "collected_at": _ms_to_iso(r.get("collected_at_ms")),
            }
    return {"providers_seen": [], "permission": "", "collected_at": None}


def _coord_pair(lat: Any, lon: Any) -> tuple[Optional[float], Optional[float]]:
    """Coerce a lat/long pair, rejecting out-of-range values and the 0,0 null-island sentinel."""
    try:
        la, lo = float(lat), float(lon)
    except (TypeError, ValueError):
        return None, None
    if not (-90.0 <= la <= 90.0) or not (-180.0 <= lo <= 180.0):
        return None, None
    if la == 0.0 and lo == 0.0:
        return None, None
    return la, lo


# --- wifi.json / bluetooth.json ---------------------------------------------


def parse_wifi_json(path: str | Path) -> list[dict[str, Any]]:
    """Parse the Collector's ``wifi.json`` (current association, saved networks, scan results).

    Returned rows keep the APK's ``type`` discriminator so downstream code can tell a *saved*
    network (historic presence at that location) from a *scan result* (the device is in range
    of that AP right now). BSSIDs are normalised to lower-case colon form because the same
    radio is reported in mixed case by different Android versions, and the geolocation lookup
    keys on an exact string match.
    """
    src = Path(path).name
    out: list[dict[str, Any]] = []
    for r in _load(path, "wifi", "networks", "data"):
        row_type = str(r.get("type") or "unknown")
        bssid = _norm_mac(r.get("bssid"))
        out.append(
            {
                "type": row_type,
                "ssid": str(r.get("ssid") or ""),
                "bssid": bssid,
                "hidden": bool(r.get("hidden", False)),
                "frequency_mhz": r.get("frequency_mhz"),
                "level_dbm": r.get("level_dbm", r.get("rssi")),
                "capabilities": str(r.get("capabilities") or ""),
                "status": str(r.get("status") or ""),
                "network_id": r.get("network_id"),
                "ip_address": str(r.get("ip_address") or ""),
                "source_file": src,
            }
        )
    return out


def parse_bluetooth_json(path: str | Path) -> list[dict[str, Any]]:
    """Parse the Collector's ``bluetooth.json`` (adapter identity + bonded devices)."""
    src = Path(path).name
    out: list[dict[str, Any]] = []
    for r in _load(path, "bluetooth", "devices", "data"):
        out.append(
            {
                "type": str(r.get("type") or "unknown"),
                "name": str(r.get("name") or ""),
                "address": _norm_mac(r.get("address")),
                "bond_state": str(r.get("bond_state") or ""),
                "device_type": str(r.get("device_type") or ""),
                "device_class": r.get("device_class"),
                "enabled": r.get("enabled"),
                "state": str(r.get("state") or ""),
                "uuids": [str(u) for u in r.get("uuids", []) if u]
                if isinstance(r.get("uuids"), list)
                else [],
                "source_file": src,
            }
        )
    return out


def _norm_mac(value: Any) -> str:
    """Normalise a MAC/BSSID to lower-case colon form; pass through anything unrecognised."""
    s = str(value or "").strip()
    if not s or s.startswith("["):  # "[permission_denied]" placeholder
        return s
    return s.lower()


# --- collector_manifest.json ------------------------------------------------


def parse_collector_manifest(path: str | Path) -> dict[str, Any]:
    """Parse the per-run audit record the Collector writes alongside its data files.

    The manifest is what makes a zero-row artifact interpretable: for each collector it
    records ``ok`` / ``empty`` / ``denied`` / ``unsupported`` / ``error`` plus the reason, and
    lists the grant state of every permission the helper asked for. Without it, "0 SMS" and
    "READ_SMS was refused" would be indistinguishable in the report.
    """
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    collectors = [c for c in data.get("collectors", []) if isinstance(c, dict)]
    perms = [c for c in data.get("permissions", []) if isinstance(c, dict)]
    return {
        "action": str(data.get("action") or ""),
        "collected_at": _ms_to_iso(data.get("collected_at_ms")),
        "sdk_int": data.get("sdk_int"),
        "manufacturer": str(data.get("manufacturer") or ""),
        "model": str(data.get("model") or ""),
        "collectors": collectors,
        "denied": [c for c in collectors if c.get("status") == "denied"],
        "permissions_granted": [
            str(c.get("permission") or "") for c in perms if c.get("granted")
        ],
        "permissions_denied": [
            str(c.get("permission") or "") for c in perms if not c.get("granted")
        ],
        "source_file": p.name,
    }
