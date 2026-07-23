"""Parsers for the expanded Collector helper-APK outputs (Tier 1, non-root).

The v0.2 Collector emits, in addition to contacts/calls/SMS:

    media_inventory.json   MediaStore catalogue (metadata; trashed/favorite/owner-app/GPS)
    apps.json              installed-app inventory with investigative classification
    accounts.json          device accounts (Google / WhatsApp / Telegram / …)
    calendar.json          calendar events
    usage.json             per-app foreground-usage telemetry
    collector_manifest.json summary of what ran (parsed opportunistically, not modelled)

Each parser is tolerant of both a bare JSON array and a ``{"key": [...]}`` wrapper, and of
missing fields, so an OEM quirk degrades to fewer fields rather than an exception.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..models import Account, AppUsage, CalendarEvent, InstalledApp, MediaInventoryItem

# --- timestamp helpers ------------------------------------------------------


def _ms_to_iso(value: Any) -> Optional[str]:
    try:
        ms = int(value)
        if ms <= 0:
            return None
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
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
    "com.whatsapp": "WhatsApp", "com.whatsapp.w4b": "WhatsApp Business",
    "org.telegram.messenger": "Telegram", "org.thoughtcrime.securesms": "Signal",
    "com.instagram.android": "Instagram", "com.snapchat.android": "Snapchat",
    "com.facebook.orca": "Messenger", "com.facebook.katana": "Facebook",
    "com.viber.voip": "Viber", "com.google.android.apps.messaging": "Google Messages",
    "com.android.chrome": "Chrome", "com.zhiliaoapp.musically": "TikTok",
    "com.twitter.android": "X (Twitter)", "com.discord": "Discord",
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
    "READ_CONTACTS", "WRITE_CONTACTS", "READ_CALL_LOG", "WRITE_CALL_LOG", "READ_SMS",
    "SEND_SMS", "RECEIVE_SMS", "READ_PHONE_STATE", "CALL_PHONE", "CAMERA",
    "RECORD_AUDIO", "ACCESS_FINE_LOCATION", "ACCESS_COARSE_LOCATION",
    "ACCESS_BACKGROUND_LOCATION", "READ_EXTERNAL_STORAGE", "WRITE_EXTERNAL_STORAGE",
    "READ_MEDIA_IMAGES", "READ_MEDIA_VIDEO", "READ_MEDIA_AUDIO", "READ_CALENDAR",
    "WRITE_CALENDAR", "BODY_SENSORS", "GET_ACCOUNTS", "SYSTEM_ALERT_WINDOW",
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
        out.append(MediaInventoryItem(
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
        ))
    return out


def parse_apps(path: str | Path) -> list[InstalledApp]:
    src = Path(path).name
    out: list[InstalledApp] = []
    for r in _load(path, "apps", "data"):
        granted = r.get("granted_permissions") or []
        requested = r.get("requested_permissions") or []
        dangerous = sorted({
            _short_perm(str(p)) for p in granted
            if _short_perm(str(p)) in _DANGEROUS
        })
        fn = r.get("friendly_name")
        out.append(InstalledApp(
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
        ))
    return out


def parse_accounts(path: str | Path) -> list[Account]:
    src = Path(path).name
    out: list[Account] = []
    for r in _load(path, "accounts", "data"):
        app = r.get("app")
        out.append(Account(
            name=str(r.get("name") or ""),
            type=str(r.get("type") or ""),
            app=app if isinstance(app, str) else None,
            source_file=src,
        ))
    return out


def parse_calendar(path: str | Path) -> list[CalendarEvent]:
    src = Path(path).name
    out: list[CalendarEvent] = []
    for r in _load(path, "calendar", "events", "data"):
        out.append(CalendarEvent(
            title=str(r.get("title") or "(no title)"),
            dtstart=_ms_to_iso(r.get("dtstart")),
            dtend=_ms_to_iso(r.get("dtend")),
            location=str(r.get("location") or ""),
            description=str(r.get("description") or ""),
            organizer=str(r.get("organizer") or ""),
            calendar=str(r.get("calendar") or ""),
            all_day=bool(r.get("all_day")),
            source_file=src,
        ))
    return out


def parse_usage(path: str | Path) -> list[AppUsage]:
    src = Path(path).name
    out: list[AppUsage] = []
    for r in _load(path, "usage", "data"):
        pkg = str(r.get("package") or "")
        ms = int(r.get("total_foreground_ms") or 0)
        out.append(AppUsage(
            package=pkg,
            total_foreground_ms=ms,
            total_foreground_min=round(ms / 60000.0, 1),
            last_used=_ms_to_iso(r.get("last_used")),
            friendly_name=app_from_package(pkg),
            category="other",
            source_file=src,
        ))
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
        "total": len(items), "by_kind": by_kind, "by_app": by_app,
        "trashed": trashed, "favorite": favorite, "with_gps": with_gps,
        "total_bytes": total_bytes,
    }
