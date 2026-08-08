"""Unified location trace — every coordinate the acquisition found, in one dataset.

The engine recovers locations from a dozen unrelated places: photo EXIF, video `udta` boxes,
the MediaStore catalogue, `dumpsys location`, the helper APK's last-known fix, Google Takeout,
Maps destination history, saved places, the Play-services geolocation cache, cell towers, WiFi
BSSIDs, WhatsApp/Telegram/Instagram/Snapchat shares, and map links in browser history. Each
parser writes its own dataset in its own shape, which leaves the examiner to reconcile a dozen
lists by hand — precisely the work a triage tool exists to remove.

This module merges them into one time-ordered trace where every row answers the same three
questions: *where*, *when*, and **how do we know**.

Design rules, each of which exists because breaking it produces a misleading exhibit:

**Never merge across evidential meaning.** A GPS fix, a photo's geotag, a navigation
destination and a viewed map link are different claims. They are given distinct ``category``
values and ``weight``s, and deduplication only ever collapses rows within the same category.
Two sources reporting the same photo are one fact; a GPS fix and a Maps search at the same
coordinate are two.

**Carry the tier.** A coordinate from `/data/data/` required root; one from `dumpsys` did not.
Every row records how it was obtained, so a defence challenge to the acquisition method can be
answered per-coordinate rather than per-case.

**Distinguish presence from interest.** ``is_presence`` marks rows that place the *device*
somewhere. A saved place, a map search and a viewed viewport are interest, not presence, and
are excluded from movement analysis — a route computed through a place the suspect merely
looked up on the map would be an invented journey.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

# --- category taxonomy ------------------------------------------------------
#
# `weight` ranks how strongly a row evidences the device being at that coordinate. It orders
# the trace for review and breaks ties in deduplication; it is NOT a probability and is never
# rendered as one.

DEVICE_FIX = "device_fix"  # the OS recorded this position for this device
MEDIA_CAPTURE = "media_capture"  # a photo/video was recorded here
SHARED_LOCATION = "shared_location"  # a location deliberately sent in a conversation
NETWORK_INFERRED = "network_inferred"  # derived from cell tower / WiFi / GMS cache
NAVIGATION = "navigation"  # a destination or route the user entered
INTEREST = "interest"  # a place looked at, searched or saved

_CATEGORY_WEIGHT = {
    DEVICE_FIX: 100,
    MEDIA_CAPTURE: 80,
    SHARED_LOCATION: 70,
    NETWORK_INFERRED: 50,
    NAVIGATION: 30,
    INTEREST: 10,
}

# Categories that place the device somewhere. SHARED_LOCATION is absent by design: only an
# *outgoing* share evidences the device's own position, and that is decided per row in
# `from_shared_locations` where the message direction is known.
_PRESENCE_CATEGORIES = {DEVICE_FIX, MEDIA_CAPTURE, NETWORK_INFERRED}

# Source string → (category, human label, default tier). The source strings are the ones the
# individual parsers already emit, so adding a parser means adding one line here.
_SOURCE_MAP: dict[str, tuple[str, str, str]] = {
    # --- direct device fixes
    "dumpsys": (DEVICE_FIX, "Last known fix (dumpsys location)", "tier0"),
    "collector": (DEVICE_FIX, "Last known fix (helper APK)", "tier1"),
    "current_location": (DEVICE_FIX, "Current GPS fix", "tier0"),
    "google_takeout": (DEVICE_FIX, "Google location history (Takeout)", "tier0"),
    "takeout": (DEVICE_FIX, "Google location history (Takeout)", "tier0"),
    # Google's current (2025+) Takeout export format — the legacy top-level `locations[]`
    # array is gone, so these two are now the ONLY Takeout sources a real export produces.
    # Missing them here silently reclassified every real Takeout position as "interest".
    "takeout_semantic": (DEVICE_FIX, "Google location history (Takeout — semantic visit)", "tier0"),
    "takeout_path": (DEVICE_FIX, "Google location history (Takeout — timeline path)", "tier0"),
    # --- media
    "exif": (MEDIA_CAPTURE, "Photo EXIF GPS", "tier0"),
    "video": (MEDIA_CAPTURE, "Video location atom", "tier0"),
    "mediastore": (MEDIA_CAPTURE, "MediaStore catalogue GPS", "tier1"),
    "media": (MEDIA_CAPTURE, "Media file GPS", "tier0"),
    # --- network inference
    "celltower": (NETWORK_INFERRED, "Cell tower", "tier0"),
    "cell_tower": (NETWORK_INFERRED, "Cell tower", "tier0"),
    "wifi": (NETWORK_INFERRED, "WiFi access point", "tier1"),
    "gms_network_location": (
        NETWORK_INFERRED,
        "Play-services geolocation cache",
        "tier2",
    ),
    # --- navigation and interest
    "maps_destination_history": (NAVIGATION, "Maps navigation destination", "tier2"),
    "maps_directions_origin": (NAVIGATION, "Maps navigation origin", "tier2"),
    "maps_saved_place": (INTEREST, "Maps saved place", "tier2"),
    "maps_search": (INTEREST, "Maps search query", "tier2"),
    "maps_cache": (INTEREST, "Maps cache", "tier2"),
    "browser_url": (INTEREST, "Map link in browser history", "tier0"),
    "text_url": (INTEREST, "Map link in message text", "tier0"),
}

# URL `kind` → category, applied on top of the source mapping. A navigation destination pasted
# into a browser is a stronger claim than a map someone merely panned across.
_URL_KIND_CATEGORY = {
    "destination": NAVIGATION,
    "origin": NAVIGATION,
    "shared": SHARED_LOCATION,
    "street_view": INTEREST,
    "viewport": INTEREST,
    "place": INTEREST,
    "query": INTEREST,
}


# --- record -----------------------------------------------------------------


@dataclass
class LocationTraceRow:
    """One coordinate, normalised across every source the engine reads."""

    latitude: Optional[float]
    longitude: Optional[float]
    timestamp: Optional[str] = None
    source: str = ""  # raw source string from the originating parser
    source_label: str = ""  # human-readable rendering of `source`
    category: str = INTEREST
    weight: int = 10
    is_presence: bool = False
    tier: str = "tier0"
    app: str = ""
    label: str = ""
    place_name: str = ""
    address: str = ""
    accuracy_m: Optional[float] = None
    confidence: str = "live"
    provenance: str = ""
    source_file: str = ""
    url: str = ""
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --- coercion helpers -------------------------------------------------------


def _as_dict(obj: Any) -> dict[str, Any]:
    """Accept a dict, a dataclass, or any object with ``to_dict``."""
    if isinstance(obj, dict):
        return obj
    to_dict = getattr(obj, "to_dict", None)
    if callable(to_dict):
        try:
            result = to_dict()
            if isinstance(result, dict):
                return result
        except Exception:
            pass
    return dict(getattr(obj, "__dict__", {}) or {})


def _float(value: Any) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def _valid(lat: Optional[float], lon: Optional[float]) -> bool:
    if lat is None or lon is None:
        return False
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        return False
    return not (abs(lat) < 1e-9 and abs(lon) < 1e-9)


def _str(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "value"):  # Enum (Confidence, Tier)
        return str(value.value)
    return str(value).strip()


def _classify(source: str, kind: str = "") -> tuple[str, str, str]:
    """Resolve a raw source string to ``(category, label, tier)``.

    Sources arrive namespaced (``collector:gps``, ``exif``, ``maps_search``), so the prefix
    before ``:`` is tried after an exact match fails. An unrecognised source degrades to
    ``INTEREST`` rather than being dropped or promoted — an unknown provenance must never be
    presented as a confirmed device position.
    """
    key = (source or "").strip().lower()
    if key in _SOURCE_MAP:
        category, label, tier = _SOURCE_MAP[key]
    elif ":" in key and key.split(":", 1)[0] in _SOURCE_MAP:
        base = key.split(":", 1)[0]
        category, label, tier = _SOURCE_MAP[base]
        label = f"{label} — {key.split(':', 1)[1]}"
    else:
        category, label, tier = INTEREST, source or "unknown source", "tier0"
    if kind and kind in _URL_KIND_CATEGORY and category == INTEREST:
        category = _URL_KIND_CATEGORY[kind]
    return category, label, tier


def _row(
    lat: Any,
    lon: Any,
    *,
    source: str,
    timestamp: Any = None,
    kind: str = "",
    app: str = "",
    label: str = "",
    place_name: str = "",
    address: str = "",
    accuracy: Any = None,
    confidence: Any = "live",
    provenance: str = "",
    source_file: Any = "",
    url: str = "",
    tier: str = "",
    flags: Optional[list[str]] = None,
) -> Optional[LocationTraceRow]:
    la, lo = _float(lat), _float(lon)
    if not _valid(la, lo):
        return None
    category, source_label, default_tier = _classify(source, kind)
    return LocationTraceRow(
        latitude=la,
        longitude=lo,
        timestamp=_str(timestamp) or None,
        source=source,
        source_label=source_label,
        category=category,
        weight=_CATEGORY_WEIGHT.get(category, 10),
        is_presence=category in _PRESENCE_CATEGORIES,
        tier=tier or default_tier,
        app=_str(app),
        label=_str(label),
        place_name=_str(place_name),
        address=_str(address),
        accuracy_m=_float(accuracy),
        confidence=_str(confidence) or "live",
        provenance=provenance,
        source_file=_str(source_file),
        url=url,
        flags=list(flags or []),
    )


# --- per-source adapters ----------------------------------------------------


def from_location_points(points: Iterable[Any]) -> list[LocationTraceRow]:
    """Adapt :class:`triage.models.LocationPoint` rows (EXIF, video, dumpsys, collector…)."""
    out = []
    for p in points:
        d = _as_dict(p)
        row = _row(
            d.get("latitude"),
            d.get("longitude"),
            source=_str(d.get("source")) or "unknown",
            timestamp=d.get("timestamp"),
            label=d.get("label"),
            source_file=d.get("source_file"),
            provenance=f"LocationPoint from {_str(d.get('source')) or 'unknown source'}",
        )
        if row:
            out.append(row)
    return out


def from_shared_locations(rows: Iterable[Any]) -> list[LocationTraceRow]:
    """Adapt :class:`triage.parsers.app_location.SharedLocation` rows.

    A share is only a *device position* when it was sent by the device owner. An incoming
    share reports where the **other party** claimed to be, so it is flagged and excluded from
    presence — attributing it to the suspect would place them somewhere they never were.
    """
    out = []
    for r in rows:
        d = _as_dict(r)
        kind = _str(d.get("kind"))
        direction = _str(d.get("direction"))
        row = _row(
            d.get("latitude"),
            d.get("longitude"),
            source="shared_location",
            timestamp=d.get("timestamp"),
            app=d.get("app"),
            label=f"{_str(d.get('app'))} {kind or 'share'}".strip(),
            place_name=d.get("place_name"),
            address=d.get("place_address"),
            confidence=d.get("confidence", "live"),
            provenance=_str(d.get("provenance")),
            source_file=d.get("source_file"),
            url=_str(d.get("url")),
            flags=list(d.get("flags") or []),
        )
        if not row:
            continue
        row.category = MEDIA_CAPTURE if kind == "media" else SHARED_LOCATION
        row.weight = _CATEGORY_WEIGHT[row.category]
        row.source_label = f"{_str(d.get('app')) or 'App'} {kind or 'share'}"
        row.tier = "tier2"  # app-private databases require root or a full image
        if kind == "saved":
            row.category, row.weight = INTEREST, _CATEGORY_WEIGHT[INTEREST]
        row.is_presence = row.category in _PRESENCE_CATEGORIES or (
            row.category == SHARED_LOCATION and direction == "outgoing"
        )
        if direction == "incoming":
            row.flags.append("counterparty-position")
        out.append(row)
    return out


def from_url_locations(rows: Iterable[Any]) -> list[LocationTraceRow]:
    """Adapt :class:`triage.parsers.url_location.UrlLocation` rows.

    Coordinate-free map searches are kept: a search names a place even without a point, and
    dropping it would lose a genuine trace. They carry ``latitude=None`` and are skipped by
    every geometric routine.
    """
    out = []
    for r in rows:
        d = _as_dict(r)
        lat, lon = _float(d.get("latitude")), _float(d.get("longitude"))
        kind = _str(d.get("kind"))
        if not _valid(lat, lon):
            if not _str(d.get("query")):
                continue
            category, label, tier = _classify(_str(d.get("source")), kind)
            out.append(
                LocationTraceRow(
                    latitude=None,
                    longitude=None,
                    timestamp=_str(d.get("timestamp")) or None,
                    source=_str(d.get("source")) or "browser_url",
                    source_label=f"{label} (search, no coordinate)",
                    category=INTEREST,
                    weight=_CATEGORY_WEIGHT[INTEREST],
                    is_presence=False,
                    tier=tier,
                    label=_str(d.get("query")),
                    place_name=_str(d.get("query")),
                    confidence=_str(d.get("confidence")) or "carved",
                    provenance=_str(d.get("provenance")),
                    source_file=_str(d.get("source_file")),
                    url=_str(d.get("url")),
                    flags=["no-coordinate"],
                )
            )
            continue
        row = _row(
            lat,
            lon,
            source=_str(d.get("source")) or "browser_url",
            kind=kind,
            timestamp=d.get("timestamp"),
            label=_str(d.get("title")) or _str(d.get("query")),
            place_name=_str(d.get("query")),
            confidence=d.get("confidence", "carved"),
            provenance=_str(d.get("provenance")),
            source_file=d.get("source_file"),
            url=_str(d.get("url")),
        )
        if row:
            row.app = _str(d.get("provider"))
            out.append(row)
    return out


def from_maps_rows(rows: Iterable[Any]) -> list[LocationTraceRow]:
    """Adapt the dict rows produced by :mod:`triage.parsers.google_maps`."""
    out = []
    for r in rows:
        d = _as_dict(r)
        lat, lon = _float(d.get("latitude")), _float(d.get("longitude"))
        source = _str(d.get("source")) or "maps_cache"
        if not _valid(lat, lon):
            # A Maps search with no coordinate is still a trace; keep it as an interest row.
            query = _str(d.get("query")) or _str(d.get("place_name"))
            if source == "maps_search" and query:
                out.append(
                    LocationTraceRow(
                        latitude=None,
                        longitude=None,
                        timestamp=_str(d.get("timestamp")) or None,
                        source=source,
                        source_label="Maps search query (no coordinate)",
                        category=INTEREST,
                        weight=_CATEGORY_WEIGHT[INTEREST],
                        is_presence=False,
                        tier="tier2",
                        label=query,
                        place_name=query,
                        provenance=_str(d.get("provenance")),
                        flags=["no-coordinate"],
                    )
                )
            continue
        row = _row(
            lat,
            lon,
            source=source,
            timestamp=d.get("timestamp"),
            label=_str(d.get("place_name")),
            place_name=_str(d.get("place_name")),
            address=_str(d.get("address")),
            accuracy=d.get("accuracy"),
            provenance=_str(d.get("provenance")),
            source_file=d.get("source_file"),
        )
        if row:
            out.append(row)
    return out


def from_cell_towers(rows: Iterable[Any]) -> list[LocationTraceRow]:
    """Adapt cell-tower observations that carry a resolved coordinate.

    Most `dumpsys telephony.registry` rows have a cell id but no position — those are dropped
    here rather than plotted at 0,0, and remain available in the `celltower` dataset.
    """
    out = []
    for r in rows:
        d = _as_dict(r)
        row = _row(
            d.get("latitude"),
            d.get("longitude"),
            source="celltower",
            timestamp=d.get("timestamp") or d.get("time"),
            label=_str(d.get("cell_id") or d.get("cid") or "cell tower"),
            accuracy=d.get("accuracy") or d.get("radius"),
            provenance="cell-tower registration",
            source_file=d.get("source_file"),
        )
        if row:
            out.append(row)
    return out


def from_media_inventory(items: Iterable[Any]) -> list[LocationTraceRow]:
    """Adapt MediaStore catalogue entries that carry a GPS pair."""
    out = []
    for it in items:
        d = _as_dict(it)
        gps = d.get("gps") or {}
        if not isinstance(gps, dict):
            continue
        row = _row(
            gps.get("lat"),
            gps.get("lon"),
            source="mediastore",
            timestamp=d.get("date_taken") or d.get("timestamp"),
            app=_str(d.get("owner_app")),
            label=f"{_str(d.get('kind'))} {_str(d.get('display_name'))}".strip(),
            provenance="MediaStore catalogue GPS (metadata only, file not pulled)",
            source_file=d.get("source_file"),
        )
        if row:
            out.append(row)
    return out


# --- aggregation ------------------------------------------------------------


def build_location_traces(
    *,
    location_points: Optional[Iterable[Any]] = None,
    shared_locations: Optional[Iterable[Any]] = None,
    url_locations: Optional[Iterable[Any]] = None,
    maps_rows: Optional[Iterable[Any]] = None,
    cell_towers: Optional[Iterable[Any]] = None,
    media_inventory: Optional[Iterable[Any]] = None,
) -> list[LocationTraceRow]:
    """Merge every location source into one deduplicated, time-ordered trace.

    Each argument is optional, so a caller can build a trace from whatever the acquisition
    actually produced without constructing empty lists for the rest.
    """
    rows: list[LocationTraceRow] = []
    rows += from_location_points(location_points or [])
    rows += from_shared_locations(shared_locations or [])
    rows += from_url_locations(url_locations or [])
    rows += from_maps_rows(maps_rows or [])
    rows += from_cell_towers(cell_towers or [])
    rows += from_media_inventory(media_inventory or [])
    return dedupe_traces(rows)


def dedupe_traces(rows: list[LocationTraceRow]) -> list[LocationTraceRow]:
    """Collapse rows describing the same fact, then sort by time.

    Deduplication is deliberately *within category only*. Two parsers reporting the same photo
    is one fact and should merge. A GPS fix and a browser map link at the same coordinate are
    two independent facts — merging them would delete corroboration, which is the opposite of
    what a corroborating source is for.

    Timestamps are part of the key at second resolution: the same place visited twice is two
    rows. Undated rows key on coordinate alone within their category.
    """
    best: dict[tuple, LocationTraceRow] = {}
    passthrough: list[LocationTraceRow] = []
    for r in rows:
        if r.latitude is None or r.longitude is None:
            passthrough.append(r)
            continue
        key = (
            r.category,
            round(r.latitude, 6),
            round(r.longitude, 6),
            (r.timestamp or "")[:19],
        )
        prior = best.get(key)
        if prior is None or _detail_score(r) > _detail_score(prior):
            best[key] = r

    merged = list(best.values()) + passthrough
    # Sort undated rows last rather than first: an empty string would otherwise sort before
    # every real timestamp and open the trace with the rows that can least be placed in time.
    return sorted(
        merged,
        key=lambda r: (
            r.timestamp is None,
            r.timestamp or "",
            -r.weight,
            r.source,
        ),
    )


def _detail_score(r: LocationTraceRow) -> int:
    """Rank rows so deduplication keeps the one carrying the most context."""
    score = r.weight
    for value in (r.place_name, r.address, r.label, r.app, r.provenance):
        if value:
            score += 1
    if r.accuracy_m is not None:
        score += 1
    return score


# --- summary and derived views ----------------------------------------------


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def summarise_traces(rows: list[LocationTraceRow]) -> dict[str, Any]:
    """Roll the unified trace up for the dashboard header and the report.

    Counts are reported per category and per source so a reader can see at a glance that, say,
    47 of 50 "locations" are map links and only 3 are actual device fixes — a distinction that
    a single total would hide.
    """
    by_category: dict[str, int] = {}
    by_source: dict[str, int] = {}
    by_tier: dict[str, int] = {}
    by_app: dict[str, int] = {}
    dated: list[LocationTraceRow] = []
    presence: list[LocationTraceRow] = []

    for r in rows:
        by_category[r.category] = by_category.get(r.category, 0) + 1
        by_source[r.source_label or r.source] = (
            by_source.get(r.source_label or r.source, 0) + 1
        )
        by_tier[r.tier] = by_tier.get(r.tier, 0) + 1
        if r.app:
            by_app[r.app] = by_app.get(r.app, 0) + 1
        if r.timestamp:
            dated.append(r)
        if r.is_presence and r.latitude is not None:
            presence.append(r)

    dated.sort(key=lambda r: r.timestamp or "")
    coords = [r for r in rows if r.latitude is not None]
    bbox = None
    if coords:
        lats = [r.latitude for r in coords if r.latitude is not None]
        lons = [r.longitude for r in coords if r.longitude is not None]
        bbox = {
            "min_lat": min(lats),
            "max_lat": max(lats),
            "min_lon": min(lons),
            "max_lon": max(lons),
        }

    return {
        "total": len(rows),
        "with_coordinates": len(coords),
        "without_coordinates": len(rows) - len(coords),
        "presence_points": len(presence),
        "interest_points": len(coords) - len(presence),
        "dated": len(dated),
        "undated": len(rows) - len(dated),
        "by_category": by_category,
        "by_source": by_source,
        "by_tier": by_tier,
        "by_app": by_app,
        "first_seen": dated[0].timestamp if dated else None,
        "last_seen": dated[-1].timestamp if dated else None,
        "bounding_box": bbox,
        "sources_present": sorted({r.source for r in rows}),
        "caveat": (
            "Categories are not interchangeable. Only rows with is_presence=true evidence the "
            "device being at that coordinate; navigation and interest rows record what the "
            "user looked up. An absent location is not evidence the device was never there — "
            "it means no reachable artifact recorded one."
        ),
    }


def presence_track(rows: list[LocationTraceRow]) -> list[LocationTraceRow]:
    """The dated, coordinate-bearing, presence-only subset, in time order.

    This is the only sequence safe to draw as a movement path. Feeding the full trace to a
    route renderer would draw lines through places the user merely searched for.
    """
    track = [
        r
        for r in rows
        if r.is_presence and r.latitude is not None and r.timestamp is not None
    ]
    return sorted(track, key=lambda r: r.timestamp or "")


def detect_impossible_travel(
    rows: list[LocationTraceRow], *, max_kmh: float = 900.0
) -> list[dict[str, Any]]:
    """Flag consecutive presence points implying travel faster than *max_kmh*.

    Two readings that cannot both be true point at something worth explaining: a spoofed GPS
    fix, a wrong timezone in a parsed timestamp, a device shared between people, or a photo
    copied onto the phone from elsewhere. The default ceiling is above airliner cruise speed,
    so a hit is genuinely anomalous rather than a fast train.

    Findings are *anomalies to investigate*, never conclusions.
    """
    track = presence_track(rows)
    out: list[dict[str, Any]] = []
    for prev, cur in zip(track, track[1:]):
        t1, t2 = _parse_iso(prev.timestamp), _parse_iso(cur.timestamp)
        if t1 is None or t2 is None:
            continue
        hours = (t2 - t1).total_seconds() / 3600.0
        if hours <= 0:
            continue
        km = _haversine_km(
            prev.latitude,  # type: ignore[arg-type]
            prev.longitude,  # type: ignore[arg-type]
            cur.latitude,  # type: ignore[arg-type]
            cur.longitude,  # type: ignore[arg-type]
        )
        speed = km / hours
        if speed <= max_kmh or km < 1.0:
            continue
        out.append(
            {
                "from": {
                    "latitude": prev.latitude,
                    "longitude": prev.longitude,
                    "timestamp": prev.timestamp,
                    "source": prev.source_label or prev.source,
                },
                "to": {
                    "latitude": cur.latitude,
                    "longitude": cur.longitude,
                    "timestamp": cur.timestamp,
                    "source": cur.source_label or cur.source,
                },
                "distance_km": round(km, 2),
                "hours": round(hours, 3),
                "implied_kmh": round(speed, 1),
                "severity": "high" if speed > max_kmh * 3 else "medium",
                "interpretation": (
                    "Two location readings imply travel faster than is physically plausible. "
                    "Possible causes include a mocked GPS fix, an incorrect timestamp, media "
                    "copied onto the device from elsewhere, or the device being used by more "
                    "than one person. Requires verification."
                ),
                "requires_verification": True,
            }
        )
    return out


def traces_to_geojson(rows: list[LocationTraceRow]) -> dict[str, Any]:
    """Render the trace as GeoJSON for mapping tools and court exhibits.

    Every property that qualifies a point — category, tier, provenance, whether it evidences
    presence — travels with the geometry, so an exhibit exported from here cannot be read as a
    set of undifferentiated dots.
    """
    features = []
    for r in rows:
        if r.latitude is None or r.longitude is None:
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [r.longitude, r.latitude],  # GeoJSON is lon, lat
                },
                "properties": {
                    "timestamp": r.timestamp,
                    "source": r.source,
                    "source_label": r.source_label,
                    "category": r.category,
                    "is_presence": r.is_presence,
                    "tier": r.tier,
                    "app": r.app,
                    "label": r.label,
                    "place_name": r.place_name,
                    "address": r.address,
                    "accuracy_m": r.accuracy_m,
                    "confidence": r.confidence,
                    "provenance": r.provenance,
                    "source_file": r.source_file,
                    "url": r.url,
                    "flags": r.flags,
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}
