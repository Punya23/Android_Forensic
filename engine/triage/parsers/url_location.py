"""Location traces recovered from URLs in browser history, chat messages and app links.

A map link is a location record. Someone who looks up a route, drops a pin, shares a Street
View, or pastes a `geo:` link into a chat leaves a coordinate in plain text — and that
coordinate survives in browser history long after the map app's own cache is gone. Browser
history is also reachable on acquisitions where app-private Maps databases are not.

What this reads, and what each pattern actually means:

``/maps/@lat,lon,17z``
    The *viewport* — where the map was centred. It says the user was looking at that area, not
    that the device was there.

``/maps/place/<name>/@lat,lon`` and ``!3dlat!4dlon``
    A specific place the user opened. The ``!3d``/``!4d`` pair inside Google's ``data=``
    parameter is the authoritative coordinate of the pinned place; the ``@`` coordinate on the
    same URL is only the camera position, and the two differ.

``?daddr=`` / ``/dir/…``
    A navigation *destination* — evidence of intent to travel there.

``?cbll=`` / ``?panoid=``
    Street View. Users check what a location looks like before going there.

``geo:lat,lon``
    An RFC 5870 geo URI, produced when a location is shared out of a maps app into a chat.

Also handled: OpenStreetMap, Apple Maps, Bing Maps, Waze, Yandex, HERE, and the generic
``lat=``/``lng=`` query-parameter convention that ride-hailing and delivery deep links use.

**Confidence.** Every row here is a *claim made by a link*, not a device fix. A user can open a
map of anywhere on earth from their sofa. Rows are emitted at ``CARVED_PARTIAL`` and labelled
with their ``kind`` so a report can never present "searched for" as "was at".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import parse_qs, unquote, urlsplit

from ..config import Confidence
from ..models import Serialisable

# --- record -----------------------------------------------------------------


@dataclass
class UrlLocation(Serialisable):
    """A coordinate (or map search) recovered from a URL."""

    latitude: Optional[float]
    longitude: Optional[float]
    url: str
    provider: str = ""  # google_maps | openstreetmap | apple_maps | geo_uri | generic | …
    # viewport      — the map was centred here (a view, not a visit)
    # place         — a specific place was opened
    # destination   — a navigation target: intent to travel
    # origin        — a navigation start point
    # street_view   — imagery of this spot was viewed
    # shared        — a geo: URI, i.e. a location handed to another app or person
    # query         — a place searched by name; no coordinate
    kind: str = "viewport"
    query: str = ""
    title: str = ""
    timestamp: Optional[str] = None
    visit_count: Optional[int] = None
    confidence: Confidence = Confidence.CARVED_PARTIAL
    source: str = "browser_url"
    source_file: str = ""
    provenance: str = ""
    flags: list[str] = field(default_factory=list)


# --- coordinate helpers -----------------------------------------------------

_COORD = r"[-+]?\d{1,3}(?:\.\d+)?"


def _valid(lat: Optional[float], lon: Optional[float]) -> bool:
    if lat is None or lon is None:
        return False
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        return False
    # Reject 0,0 and integer-only pairs like "1,2": a real map link always carries decimals,
    # and accepting bare integers turns every "?q=1,2" pagination link into a coordinate.
    return not (abs(lat) < 1e-9 and abs(lon) < 1e-9)


def _pair(lat_s: str, lon_s: str, *, require_decimal: bool = True) -> Optional[tuple[float, float]]:
    if require_decimal and ("." not in lat_s or "." not in lon_s):
        return None
    try:
        lat, lon = float(lat_s), float(lon_s)
    except (TypeError, ValueError):
        return None
    return (lat, lon) if _valid(lat, lon) else None


# --- URL patterns -----------------------------------------------------------
# Each entry: (compiled regex, provider, kind). Group 1 = latitude, group 2 = longitude.
# Order matters — the more specific meaning is matched first so a Google Maps place URL is
# reported as a place rather than as a viewport.

_PATTERNS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    # Google's data= parameter: !3d<lat>!4d<lon> is the pinned place, not the camera.
    (re.compile(rf"!3d({_COORD})!4d({_COORD})"), "google_maps", "place"),
    # Street View camera location.
    (re.compile(rf"[?&]cbll=({_COORD}),({_COORD})"), "google_maps", "street_view"),
    (re.compile(rf"[?&]viewpoint=({_COORD}),({_COORD})"), "google_maps", "street_view"),
    # Navigation destination / origin.
    (re.compile(rf"[?&]daddr=({_COORD}),({_COORD})"), "google_maps", "destination"),
    (re.compile(rf"[?&]destination=({_COORD}),({_COORD})"), "google_maps", "destination"),
    (re.compile(rf"[?&]saddr=({_COORD}),({_COORD})"), "google_maps", "origin"),
    (re.compile(rf"[?&]origin=({_COORD}),({_COORD})"), "google_maps", "origin"),
    # Map viewport: /maps/@lat,lon,zoom  or  ?ll= / ?q= / ?center=
    (re.compile(rf"/maps/@({_COORD}),({_COORD})"), "google_maps", "viewport"),
    (re.compile(rf"/@({_COORD}),({_COORD})"), "google_maps", "viewport"),
    (re.compile(rf"[?&]ll=({_COORD}),({_COORD})"), "google_maps", "viewport"),
    (re.compile(rf"[?&]center=({_COORD}),({_COORD})"), "google_maps", "viewport"),
    (re.compile(rf"[?&]sll=({_COORD}),({_COORD})"), "google_maps", "viewport"),
    (re.compile(rf"[?&]q=(?:loc:)?({_COORD}),({_COORD})"), "google_maps", "place"),
    # RFC 5870 geo URI — a location shared out of an app.
    (re.compile(rf"geo:({_COORD}),({_COORD})"), "geo_uri", "shared"),
    # OpenStreetMap.
    (re.compile(rf"#map=\d+/({_COORD})/({_COORD})"), "openstreetmap", "viewport"),
    (re.compile(rf"[?&]mlat=({_COORD})[^&]*&mlon=({_COORD})"), "openstreetmap", "place"),
    # Bing Maps uses a tilde separator.
    (re.compile(rf"[?&]cp=({_COORD})~({_COORD})"), "bing_maps", "viewport"),
    # Yandex and HERE put longitude first, so the groups are swapped by _PROVIDER_LON_FIRST.
    (re.compile(rf"[?&]ll=({_COORD})%2C({_COORD})"), "yandex_maps", "viewport"),
    # Generic lat/lng query pair — ride-hailing and delivery deep links.
    (
        re.compile(rf"[?&](?:lat|latitude)=({_COORD})(?:[^&]*&)*?(?:lng|lon|longitude)=({_COORD})"),
        "generic",
        "viewport",
    ),
)

# Hosts whose ``ll=`` parameter is longitude-first. Getting this backwards silently transposes
# every coordinate instead of erroring, so it is handled explicitly rather than by heuristic.
_LON_FIRST_HOSTS = ("yandex.", "wego.here.com", "here.com")

# Map hosts, used to decide whether a `?q=` with no coordinate is a *map search* worth keeping.
_MAP_HOSTS = (
    "google.com/maps",
    "maps.google.",
    "goo.gl/maps",
    "maps.app.goo.gl",
    "openstreetmap.org",
    "maps.apple.com",
    "bing.com/maps",
    "waze.com",
    "yandex.",
    "here.com",
    "mapquest.com",
    "wikimapia.org",
)

_PROVIDER_BY_HOST = (
    ("maps.google.", "google_maps"),
    ("google.com/maps", "google_maps"),
    ("goo.gl/maps", "google_maps"),
    ("maps.app.goo.gl", "google_maps"),
    ("openstreetmap.org", "openstreetmap"),
    ("maps.apple.com", "apple_maps"),
    ("bing.com/maps", "bing_maps"),
    ("waze.com", "waze"),
    ("yandex.", "yandex_maps"),
    ("here.com", "here_maps"),
    ("mapquest.com", "mapquest"),
)


def extract_url_coordinates(url: str) -> list[dict[str, Any]]:
    """Return every coordinate encoded in *url*, most meaningful interpretation first.

    A single Maps URL commonly carries two different coordinates — the camera position after
    ``@`` and the pinned place inside ``data=`` — which are not the same location. Both are
    returned, distinguished by ``kind``, rather than silently picking one.
    """
    if not url:
        return []
    decoded = unquote(url)
    lowered = decoded.lower()
    lon_first = any(h in lowered for h in _LON_FIRST_HOSTS)

    out: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for pattern, provider, kind in _PATTERNS:
        for m in pattern.finditer(decoded):
            a, b = m.group(1), m.group(2)
            # A provider-specific swap, not a guess: these hosts document longitude first.
            lat_s, lon_s = (b, a) if (lon_first and provider != "geo_uri") else (a, b)
            point = _pair(lat_s, lon_s)
            if point is None:
                continue
            key = (round(point[0], 6), round(point[1], 6), kind)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "latitude": point[0],
                    "longitude": point[1],
                    "provider": provider,
                    "kind": kind,
                    "pattern": pattern.pattern,
                }
            )
    for hit in _bracketed_params(decoded):
        key = (round(hit["latitude"], 6), round(hit["longitude"], 6), hit["kind"])
        if key in seen:
            continue
        seen.add(key)
        out.append(hit)
    return out


# Ride-hailing and delivery deep links group coordinates by role:
#   ?pickup[latitude]=…&pickup[longitude]=…&dropoff[latitude]=…&dropoff[longitude]=…
# Pairing must respect the prefix — matching the first latitude against the last longitude
# would invent a coordinate halfway between two real places.
_BRACKET_PARAM = re.compile(
    r"([A-Za-z_]*)(?:\[|%5B)?\b(lat|latitude|lng|lon|longitude)\b(?:\]|%5D)?=([-+]?\d{1,3}\.\d+)",
    re.IGNORECASE,
)

# Prefix → what that coordinate means in the journey.
_ROLE_KIND = {
    "pickup": "origin",
    "source": "origin",
    "start": "origin",
    "from": "origin",
    "origin": "origin",
    "dropoff": "destination",
    "drop": "destination",
    "dest": "destination",
    "destination": "destination",
    "to": "destination",
    "end": "destination",
}


def _bracketed_params(decoded: str) -> list[dict[str, Any]]:
    """Extract role-prefixed coordinate pairs (``pickup[latitude]`` / ``dropoff[longitude]``)."""
    lats: dict[str, float] = {}
    lons: dict[str, float] = {}
    for m in _BRACKET_PARAM.finditer(decoded):
        prefix = (m.group(1) or "").strip("_&?").lower()
        field_name = m.group(2).lower()
        try:
            value = float(m.group(3))
        except ValueError:
            continue
        target = lats if field_name in ("lat", "latitude") else lons
        target.setdefault(prefix, value)

    out: list[dict[str, Any]] = []
    for prefix, lat in lats.items():
        lon = lons.get(prefix)
        if lon is None or not _valid(lat, lon):
            continue
        out.append(
            {
                "latitude": lat,
                "longitude": lon,
                "provider": "deep_link",
                "kind": _ROLE_KIND.get(prefix, "viewport"),
                "pattern": "role-prefixed lat/lon parameters",
            }
        )
    return out


def extract_map_query(url: str) -> str:
    """Return the place name searched on a map host, or ``""``.

    A search for "pawn shops near Koramangala" has no coordinate but is still a location trace:
    it evidences interest in an area. Only map hosts are considered, so an ordinary web search
    is not misread as a location.
    """
    if not url:
        return ""
    decoded = unquote(url)
    lowered = decoded.lower()
    if not any(h in lowered for h in _MAP_HOSTS):
        return ""
    try:
        parts = urlsplit(decoded)
    except ValueError:
        return ""
    params = parse_qs(parts.query)
    for key in ("q", "query", "search", "daddr", "destination"):
        for value in params.get(key, []):
            text = value.strip()
            # Skip values that are themselves coordinates — those are handled as points.
            if text and not re.fullmatch(rf"{_COORD},{_COORD}", text):
                return text
    # Google encodes the place name in the path: /maps/place/<name>/@…
    m = re.search(r"/maps/(?:place|search|dir)/([^/@?]+)", decoded)
    if m:
        text = m.group(1).replace("+", " ").strip()
        if text and not re.fullmatch(rf"{_COORD},{_COORD}", text):
            return text
    return ""


def _provider_for(url: str) -> str:
    lowered = (url or "").lower()
    for host, provider in _PROVIDER_BY_HOST:
        if host in lowered:
            return provider
    return "generic"


# --- record builders --------------------------------------------------------


def locations_from_urls(
    rows: Iterable[Any],
    *,
    source_file: str = "",
    source: str = "browser_url",
    url_key: str = "url",
    time_key: str = "last_visit",
    title_key: str = "title",
) -> list[UrlLocation]:
    """Turn browser-history rows (or any ``{url, title, timestamp}`` dicts) into locations.

    Works on the output of :func:`triage.parsers.browser.parse_browser_history` unchanged, and
    on chat messages by passing ``url_key="body"`` — a pasted map link in a WhatsApp export is
    the same evidence as one in browser history.

    Coordinate-free map searches are kept as ``kind="query"`` rows with ``latitude`` /
    ``longitude`` set to ``None``. Dropping them would discard a real location trace just
    because it is not a point.
    """
    out: list[UrlLocation] = []
    for row in rows:
        data = row if isinstance(row, dict) else getattr(row, "__dict__", {})
        url = str(data.get(url_key) or "")
        if not url:
            continue
        title = str(data.get(title_key) or "")
        ts = data.get(time_key) or data.get("timestamp") or None
        visits = data.get("visit_count")
        try:
            visits = int(visits) if visits is not None else None
        except (TypeError, ValueError):
            visits = None

        hits = extract_url_coordinates(url)
        for hit in hits:
            out.append(
                UrlLocation(
                    latitude=hit["latitude"],
                    longitude=hit["longitude"],
                    url=url,
                    provider=hit["provider"],
                    kind=hit["kind"],
                    title=title,
                    timestamp=str(ts) if ts else None,
                    visit_count=visits,
                    source=source,
                    source_file=source_file or str(data.get("source_file") or ""),
                    provenance=f"URL {hit['kind']} coordinate ({hit['provider']})",
                )
            )
        if not hits:
            query = extract_map_query(url)
            if query:
                out.append(
                    UrlLocation(
                        latitude=None,
                        longitude=None,
                        url=url,
                        provider=_provider_for(url),
                        kind="query",
                        query=query,
                        title=title,
                        timestamp=str(ts) if ts else None,
                        visit_count=visits,
                        source=source,
                        source_file=source_file or str(data.get("source_file") or ""),
                        provenance="map search query (no coordinate in URL)",
                    )
                )
    return _dedupe(out)


# Bare URLs embedded in free text (chat bodies, notes, clipboard dumps).
_URL_IN_TEXT = re.compile(r"(?:https?://|geo:)[^\s<>\"'\]\)]+", re.IGNORECASE)


def locations_from_text(
    text: str, *, source_file: str = "", timestamp: Optional[str] = None
) -> list[UrlLocation]:
    """Pull map links out of free text — a chat body, a note, a clipboard capture."""
    rows = [
        {"url": m.group(0), "last_visit": timestamp, "source_file": source_file}
        for m in _URL_IN_TEXT.finditer(text or "")
    ]
    return locations_from_urls(rows, source_file=source_file, source="text_url")


def _dedupe(rows: list[UrlLocation]) -> list[UrlLocation]:
    """Collapse repeat visits to the same coordinate at the same time, keeping visit counts.

    Browser history genuinely contains the same map URL many times. Merging on
    coordinate+kind+timestamp keeps each distinct visit while removing the duplication that
    comes from one URL matching several patterns.
    """
    best: dict[tuple, UrlLocation] = {}
    for r in rows:
        key = (
            round(r.latitude, 6) if r.latitude is not None else None,
            round(r.longitude, 6) if r.longitude is not None else None,
            r.kind,
            r.query,
            r.timestamp or "",
        )
        prior = best.get(key)
        if prior is None or (r.visit_count or 0) > (prior.visit_count or 0):
            best[key] = r
    return sorted(
        best.values(),
        key=lambda r: (r.timestamp or "", r.provider, r.kind, r.url),
    )


def summarise_url_locations(rows: list[UrlLocation]) -> dict[str, Any]:
    """Roll URL-derived locations up for the report and dashboard."""
    by_provider: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    for r in rows:
        by_provider[r.provider] = by_provider.get(r.provider, 0) + 1
        by_kind[r.kind] = by_kind.get(r.kind, 0) + 1
    points = [r for r in rows if r.latitude is not None]
    return {
        "total": len(rows),
        "with_coordinates": len(points),
        "searches_only": len(rows) - len(points),
        "by_provider": by_provider,
        "by_kind": by_kind,
        "destinations": by_kind.get("destination", 0),
        "note": (
            "A map link records what the user looked at, not where the device was. "
            "Treat these as interest in a location, not as a position fix."
        ),
    }


def source_label(path: str | Path) -> str:
    """Short label for the file a URL row came from (used in ``source_file``)."""
    return Path(path).name
