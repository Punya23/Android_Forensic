"""Location timeline builder and map/HTML visualisation.

Produces two output artefacts from a list of location dicts:

1. **Folium HTML map** (``plot_locations_on_map``) — a static, self-contained
   HTML file with timestamped markers, coloured by source app, and a polyline
   connecting locations in chronological order.

2. **HTML timeline strip** (``generate_timeline_visualization``) — a horizontal
   timeline showing location events on a time axis with coordinate labels,
   photo filenames, and source-app badges.

Folium is an optional dependency. If not installed, map generation degrades
gracefully with a warning; the rest of the module remains fully functional.

Usage::

    from triage.forensics.location_timeline import (
        build_location_timeline,
        plot_locations_on_map,
        generate_timeline_visualization,
    )

    locations = extract_all_media_locations(media_root)
    timeline  = build_location_timeline(locations)
    plot_locations_on_map(locations, Path("output/map.html"))
    generate_timeline_visualization(timeline["events"], Path("output/timeline.html"))
"""

from __future__ import annotations

import calendar
import html
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Optional Folium import
# ---------------------------------------------------------------------------

try:
    import folium  # type: ignore

    _HAVE_FOLIUM = True
except ImportError:
    _HAVE_FOLIUM = False


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Source-app colour palette for the map markers and timeline badges.
_APP_COLORS: Dict[str, str] = {
    "whatsapp": "#25D366",
    "telegram": "#2AABEE",
    "sms": "#FF6B35",
    "instagram": "#C13584",
    "unknown": "#888888",
}

# Folium icon colours (limited set supported by Folium/Font Awesome)
_APP_FOLIUM_COLORS: Dict[str, str] = {
    "whatsapp": "green",
    "telegram": "blue",
    "sms": "orange",
    "instagram": "purple",
    "unknown": "gray",
}

# Default map tile layer
_DEFAULT_TILES = "OpenStreetMap"


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


def _days_between(ts1: Optional[str], ts2: Optional[str]) -> Optional[float]:
    e1 = _iso_to_epoch(ts1)
    e2 = _iso_to_epoch(ts2)
    if e1 is None or e2 is None:
        return None
    return abs(e2 - e1) / 86400.0


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    import math

    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    )
    return 2 * R * math.asin(math.sqrt(a))


def _sort_locations(locations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sort location dicts chronologically (None timestamps go last)."""
    return sorted(locations, key=lambda r: r.get("timestamp") or "9999")


def _center_of_locations(locations: List[Dict[str, Any]]) -> tuple[float, float]:
    """Return the arithmetic centroid of all gps-bearing locations."""
    lats, lons = [], []
    for loc in locations:
        gps = loc.get("gps") or {}
        if gps.get("lat") and gps.get("lon"):
            lats.append(gps["lat"])
            lons.append(gps["lon"])
    if lats:
        return sum(lats) / len(lats), sum(lons) / len(lons)
    return 0.0, 0.0


# ---------------------------------------------------------------------------
# Public API — Timeline building
# ---------------------------------------------------------------------------


def create_timeline_events(locations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Create structured event dicts from a list of location dicts.

    Each event contains:
        timestamp    -- ISO-8601 string or None
        lat          -- float or None
        lon          -- float or None
        file         -- full file path string
        filename     -- basename
        source_app   -- 'whatsapp' | 'telegram' | ... | 'unknown'
        device_make  -- str or None
        device_model -- str or None
        altitude     -- float or None
        software     -- str or None
    """
    events: List[Dict[str, Any]] = []
    for loc in _sort_locations(locations):
        gps = loc.get("gps") or {}
        path = loc.get("file", "")
        events.append(
            {
                "timestamp": loc.get("timestamp"),
                "lat": gps.get("lat"),
                "lon": gps.get("lon"),
                "altitude": loc.get("altitude"),
                "file": path,
                "filename": Path(path).name if path else "",
                "source_app": loc.get("source", "unknown"),
                "device_make": loc.get("device_make"),
                "device_model": loc.get("device_model"),
                "software": loc.get("software"),
            }
        )
    return events


def build_location_timeline(locations: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build a chronological location timeline from a list of location dicts.

    Args:
        locations: Output of ``extract_all_media_locations`` or similar.

    Returns::

        {
            'events':     [...],   # sorted list of event dicts
            'statistics': {
                'total':          int,
                'with_gps':       int,
                'sources':        {'whatsapp': int, ...},
                'first_event':    ISO timestamp or None,
                'last_event':     ISO timestamp or None,
                'time_span_days': float or None,
            }
        }
    """
    events = create_timeline_events(locations)

    with_gps = sum(1 for e in events if e.get("lat") and e.get("lon"))
    sources: Dict[str, int] = {}
    for e in events:
        app = e.get("source_app", "unknown")
        sources[app] = sources.get(app, 0) + 1

    timestamped = [e["timestamp"] for e in events if e.get("timestamp")]
    first_ts = min(timestamped) if timestamped else None
    last_ts = max(timestamped) if timestamped else None
    span_days = _days_between(first_ts, last_ts)

    return {
        "events": events,
        "statistics": {
            "total": len(events),
            "with_gps": with_gps,
            "sources": sources,
            "first_event": first_ts,
            "last_event": last_ts,
            "time_span_days": round(span_days, 2) if span_days is not None else None,
        },
    }


# ---------------------------------------------------------------------------
# Public API — Folium map
# ---------------------------------------------------------------------------


def add_markers_with_timestamps(
    locations: List[Dict[str, Any]],
    fmap: Any = None,
) -> str:
    """Add timestamped markers to a Folium map object.

    If *fmap* is None, a new Folium map is created centred on the locations.

    Args:
        locations: Sorted list of location dicts with 'gps' and 'timestamp'.
        fmap:      An existing ``folium.Map`` object (optional).

    Returns:
        The rendered HTML string of the map.  Returns empty string if folium
        is not available.
    """
    if not _HAVE_FOLIUM:
        return ""

    center_lat, center_lon = _center_of_locations(locations)
    if fmap is None:
        fmap = folium.Map(
            location=[center_lat or 0.0, center_lon or 0.0],
            zoom_start=12,
            tiles=_DEFAULT_TILES,
        )

    for i, loc in enumerate(locations, 1):
        gps = loc.get("gps") or {}
        lat = gps.get("lat")
        lon = gps.get("lon")
        if lat is None or lon is None:
            continue

        ts = loc.get("timestamp") or "Unknown time"
        source = loc.get("source", "unknown")
        fname = Path(loc.get("file", "")).name
        device = loc.get("device_model") or loc.get("device_make") or "Unknown device"
        altitude = loc.get("altitude")
        alt_str = f"Alt: {altitude:.1f} m" if altitude is not None else "Alt: N/A"

        popup_html = (
            f"<b>#{i} — {source.title()}</b><br>"
            f"<small>{ts}</small><br>"
            f"📍 {lat:.6f}, {lon:.6f}<br>"
            f"📁 {fname}<br>"
            f"📱 {device}<br>"
            f"🏔 {alt_str}"
        )

        folium.Marker(
            location=[lat, lon],
            popup=folium.Popup(popup_html, max_width=280),
            tooltip=f"#{i} {source.title()} — {ts}",
            icon=folium.Icon(
                color=_APP_FOLIUM_COLORS.get(source, "gray"),
                icon="camera",
                prefix="fa",
            ),
        ).add_to(fmap)

    return fmap._repr_html_() if hasattr(fmap, "_repr_html_") else ""


def add_paths_between_locations(
    locations: List[Dict[str, Any]],
    fmap: Any = None,
) -> str:
    """Add a polyline connecting locations in chronological order.

    Args:
        locations: Chronologically sorted location dicts.
        fmap:      Existing ``folium.Map`` object to add the path to.

    Returns:
        The rendered HTML string; empty string if folium is not available.
    """
    if not _HAVE_FOLIUM:
        return ""

    coords: List[List[float]] = []
    for loc in locations:
        gps = loc.get("gps") or {}
        lat = gps.get("lat")
        lon = gps.get("lon")
        if lat is not None and lon is not None:
            coords.append([lat, lon])

    if len(coords) >= 2 and fmap is not None:
        folium.PolyLine(
            locations=coords,
            color="#FF4500",
            weight=2.5,
            opacity=0.7,
            tooltip="Movement path",
        ).add_to(fmap)

    if fmap is not None and hasattr(fmap, "_repr_html_"):
        return fmap._repr_html_()
    return ""


def plot_locations_on_map(
    locations: List[Dict[str, Any]],
    output_path: Path,
) -> None:
    """Generate a static Folium HTML map with location markers and paths.

    Creates a self-contained HTML file at *output_path* that can be opened in
    any browser without a server. If folium is not installed, a plain-text
    warning file is written instead.

    Args:
        locations:    List of location dicts from ``media_location``.
        output_path:  Path where the ``.html`` file will be written.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not _HAVE_FOLIUM:
        output_path.write_text(
            "# Map not generated\n\nInstall folium (`pip install folium`) to enable map output.\n",
            encoding="utf-8",
        )
        return

    sorted_locs = _sort_locations(locations)
    gps_locs = [l for l in sorted_locs if (l.get("gps") or {}).get("lat")]

    center_lat, center_lon = _center_of_locations(gps_locs) if gps_locs else (0.0, 0.0)
    fmap = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=13 if gps_locs else 2,
        tiles=_DEFAULT_TILES,
    )

    # Add markers
    add_markers_with_timestamps(gps_locs, fmap=fmap)

    # Add movement path
    if len(gps_locs) >= 2:
        add_paths_between_locations(gps_locs, fmap=fmap)

    fmap.save(str(output_path))


# ---------------------------------------------------------------------------
# Public API — HTML timeline visualisation
# ---------------------------------------------------------------------------


def generate_timeline_visualization(
    events: List[Dict[str, Any]],
    output_path: Path,
) -> None:
    """Generate a self-contained HTML timeline visualisation.

    The timeline is rendered as a horizontal scrollable strip with:
    * Time on the x-axis (ISO timestamps as labels)
    * Colour-coded dots per source app
    * Coordinate labels (lat/lon)
    * Photo filename
    * Source-app badge

    Args:
        events:       Output of ``create_timeline_events`` (or the 'events' key
                      from ``build_location_timeline``).
        output_path:  Path where the ``.html`` file will be written.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    html_parts: List[str] = [_TIMELINE_HTML_HEAD]

    for i, ev in enumerate(events):
        ts = ev.get("timestamp") or "Unknown"
        lat = ev.get("lat")
        lon = ev.get("lon")
        source = ev.get("source_app", "unknown")
        filename = html.escape(ev.get("filename") or "")
        device = html.escape(
            " ".join(filter(None, [ev.get("device_make"), ev.get("device_model")]))
            or "Unknown device"
        )
        altitude = ev.get("altitude")
        color = _APP_COLORS.get(source, "#888888")

        coord_str = (
            f"{lat:.6f}, {lon:.6f}" if lat is not None and lon is not None else "No GPS"
        )
        alt_str = f"{altitude:.1f} m" if altitude is not None else "—"

        html_parts.append(
            f"""
        <div class="event" style="border-top: 4px solid {color}">
            <div class="badge" style="background:{color}">{html.escape(source.upper())}</div>
            <div class="ts">{html.escape(ts)}</div>
            <div class="coord">📍 {coord_str}</div>
            <div class="detail">🏔 Alt: {alt_str}</div>
            <div class="detail">📁 {filename}</div>
            <div class="detail">📱 {device}</div>
        </div>"""
        )

    html_parts.append(_TIMELINE_HTML_FOOT)
    output_path.write_text("\n".join(html_parts), encoding="utf-8")


# ---------------------------------------------------------------------------
# HTML template strings
# ---------------------------------------------------------------------------

_TIMELINE_HTML_HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Location Timeline</title>
<style>
  :root {
    --bg: #0f1117;
    --card: #1a1d27;
    --text: #e2e8f0;
    --muted: #8892a4;
    --radius: 10px;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'Segoe UI', system-ui, sans-serif;
    padding: 32px 16px;
  }
  h1 {
    font-size: 1.6rem;
    margin-bottom: 8px;
    background: linear-gradient(90deg, #60a5fa, #a78bfa);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
  }
  .subtitle { color: var(--muted); margin-bottom: 32px; font-size: 0.9rem; }
  .timeline {
    display: flex;
    overflow-x: auto;
    gap: 16px;
    padding: 8px 0 24px;
    scrollbar-color: #4a5568 var(--bg);
  }
  .event {
    background: var(--card);
    border-radius: var(--radius);
    min-width: 220px;
    max-width: 240px;
    padding: 16px 14px 14px;
    flex-shrink: 0;
    position: relative;
    transition: transform 0.15s, box-shadow 0.15s;
  }
  .event:hover {
    transform: translateY(-4px);
    box-shadow: 0 8px 24px rgba(0,0,0,0.4);
  }
  .badge {
    display: inline-block;
    font-size: 0.65rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    color: #fff;
    border-radius: 4px;
    padding: 2px 7px;
    margin-bottom: 10px;
  }
  .ts {
    font-size: 0.78rem;
    color: var(--muted);
    margin-bottom: 8px;
    word-break: break-all;
  }
  .coord {
    font-size: 0.82rem;
    font-weight: 600;
    margin-bottom: 6px;
    color: #93c5fd;
  }
  .detail {
    font-size: 0.78rem;
    color: var(--muted);
    margin-bottom: 4px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .connector {
    display: flex;
    align-items: center;
    flex-shrink: 0;
    color: #4a5568;
    font-size: 1.4rem;
    padding-top: 40px;
  }
</style>
</head>
<body>
<h1>📍 Location Timeline</h1>
<p class="subtitle">Chronological media location trace — generated by eRakshak forensic engine</p>
<div class="timeline">
"""

_TIMELINE_HTML_FOOT = """
</div>
</body>
</html>"""
