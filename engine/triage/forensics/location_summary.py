"""Location Summary Report — aggregate all location forensic analysis into a report.

Orchestrates the five location sub-modules (clustering, place identification,
movement analysis, anomaly detection, and timeline) to produce:

* A structured Python dict summary (``generate_location_summary``).
* A self-contained dark-themed HTML report (``generate_location_html_summary``).

The HTML report includes:
    * Statistics cards (total locations, unique places, time span, movement).
    * Movement pattern section with segment breakdown.
    * Place identification table (home / work / frequent).
    * Anomalies table.
    * An embedded Folium map (if folium is installed) or a coordinate table.

Usage::

    from engine.triage.forensics.location_summary import (
        generate_location_summary,
        generate_location_html_summary,
    )

    summary = generate_location_summary(locations)
    generate_location_html_summary(locations, output_path=Path("report/location.html"))
"""

from __future__ import annotations

import html as _html_mod
import calendar
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .gps_clustering import cluster_gps_points
from .place_identification import identify_places_from_locations
from .movement_analysis import detect_movement_pattern
from .location_anomaly import detect_location_anomalies
from .location_timeline import build_location_timeline, plot_locations_on_map


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _iso_to_epoch(ts: Optional[str]) -> Optional[float]:
    if not ts:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            t = time.strptime(ts, fmt)
            return float(calendar.timegm(t))
        except ValueError:
            continue
    return None


def _flat_lat_lon(loc: Dict[str, Any]):
    lat = loc.get("lat")
    lon = loc.get("lon")
    if lat is None or lon is None:
        gps = loc.get("gps") or {}
        lat = gps.get("lat")
        lon = gps.get("lon")
    return lat, lon


def _esc(s: Any) -> str:
    return _html_mod.escape(str(s)) if s is not None else "—"


def _sev_color(severity: str) -> str:
    return {"critical": "#ef4444", "warn": "#f59e0b", "info": "#60a5fa"}.get(
        severity, "#94a3b8"
    )


# ---------------------------------------------------------------------------
# Public API — statistics helpers
# ---------------------------------------------------------------------------


def get_location_statistics(locations: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute basic statistics for a list of GPS locations.

    Args:
        locations: List of location dicts with optional ``'timestamp'``,
                   ``'lat'``, and ``'lon'`` keys.

    Returns:
        ::

            {
                'count':          int,
                'with_gps':       int,
                'unique_places':  int,   # number of clusters (radius 0.5 km)
                'time_span':      str,   # human-readable duration string
                'time_span_days': float | None,
                'first_event':    str | None,
                'last_event':     str | None,
                'movement_pattern': str, # dominant movement type
            }
    """
    timeline = build_location_timeline(locations)
    stats = timeline.get("statistics", {})

    with_gps = sum(1 for loc in locations if _flat_lat_lon(loc)[0] is not None)
    clusters = cluster_gps_points(locations, radius_km=0.5)
    unique_places = len(clusters)

    span_days = stats.get("time_span_days")
    if span_days is None:
        time_span = "unknown"
    elif span_days < 1:
        time_span = f"{int(span_days * 24)} hours"
    else:
        time_span = f"{span_days:.1f} days"

    # Movement pattern from speed analysis.
    movement_result = detect_movement_pattern(locations)
    movement_pattern = movement_result.get("overall", "unknown")

    return {
        "count": stats.get("total", len(locations)),
        "with_gps": with_gps,
        "unique_places": unique_places,
        "time_span": time_span,
        "time_span_days": span_days,
        "first_event": stats.get("first_event"),
        "last_event": stats.get("last_event"),
        "movement_pattern": movement_pattern,
    }


def get_place_statistics(places: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Summarise a list of place/cluster dicts.

    Args:
        places: Output of :func:`identify_frequent_places` or similar.

    Returns:
        ::

            {
                'home':            <place dict or None>,
                'work':            <place dict or None>,
                'frequent_places': [<place dict>, ...],
                'total_places':    int,
            }
    """
    home = next((p for p in places if p.get("place_type") == "home"), None)
    work = next((p for p in places if p.get("place_type") == "work"), None)
    frequent = [p for p in places if p.get("place_type") == "frequent"]
    return {
        "home": home,
        "work": work,
        "frequent_places": frequent,
        "total_places": len(places),
    }


def get_anomaly_statistics(anomalies: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Summarise a list of location anomaly dicts.

    Args:
        anomalies: Output of :func:`detect_location_anomalies`.

    Returns:
        ::

            {
                'total_anomalies': int,
                'by_type':         {'late_night_visit': int, ...},
                'by_severity':     {'warn': int, 'info': int, ...},
            }
    """
    by_type: Dict[str, int] = {}
    by_sev: Dict[str, int] = {}
    for a in anomalies:
        t = a.get("type", "unknown")
        s = a.get("severity", "info")
        by_type[t] = by_type.get(t, 0) + 1
        by_sev[s] = by_sev.get(s, 0) + 1
    return {
        "total_anomalies": len(anomalies),
        "by_type": by_type,
        "by_severity": by_sev,
    }


# ---------------------------------------------------------------------------
# Public API — orchestration
# ---------------------------------------------------------------------------


def generate_location_summary(locations: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Generate a comprehensive summary of all location data.

    Orchestrates clustering, place identification, movement analysis, and
    anomaly detection into a single top-level result dict.

    Args:
        locations: List of GPS location dicts.

    Returns:
        ::

            {
                'total_locations':   int,
                'unique_places':     int,
                'time_span':         str,
                'movement_pattern':  str,
                'statistics':        {...},
                'places':            {'home': ..., 'work': ..., 'frequent_places': [...]},
                'movement':          {...},   # from detect_movement_pattern
                'anomalies':         [...],   # from detect_location_anomalies
                'anomaly_stats':     {...},
            }
    """
    loc_stats = get_location_statistics(locations)
    places = identify_places_from_locations(locations)
    movement = detect_movement_pattern(locations)
    anomalies = detect_location_anomalies(locations)
    anom_stats = get_anomaly_statistics(anomalies)

    return {
        "total_locations": loc_stats["count"],
        "unique_places": loc_stats["unique_places"],
        "time_span": loc_stats["time_span"],
        "movement_pattern": loc_stats["movement_pattern"],
        "statistics": loc_stats,
        "places": places,
        "movement": movement,
        "anomalies": anomalies,
        "anomaly_stats": anom_stats,
    }


# ---------------------------------------------------------------------------
# Public API — HTML report
# ---------------------------------------------------------------------------


def generate_location_html_summary(
    locations: List[Dict[str, Any]],
    output_path: Path,
) -> None:
    """Generate a self-contained dark-themed HTML report of all location data.

    Sections:
        1. Summary statistics cards.
        2. Movement pattern and segment table.
        3. Place identification (home / work / frequent).
        4. Anomaly table.
        5. Embedded Folium map (or coordinate table fallback).

    Args:
        locations: List of GPS location dicts.
        output_path: Path where the ``.html`` file will be written.  Parent
                     directories are created automatically.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    summary = generate_location_summary(locations)
    places = summary["places"]
    movement = summary["movement"]
    anomalies = summary["anomalies"]
    stats = summary["statistics"]
    mov_stats = movement.get("statistics", {})

    # --- Folium map (optional) ---
    map_html = ""
    map_path = output_path.parent / (output_path.stem + "_map.html")
    try:
        plot_locations_on_map(locations, map_path)
        if map_path.exists():
            map_html = map_path.read_text(encoding="utf-8")
            # Strip everything outside <body>...</body> for inline embedding.
            import re as _re

            m = _re.search(
                r"<body[^>]*>(.*?)</body>", map_html, _re.DOTALL | _re.IGNORECASE
            )
            map_html = m.group(1).strip() if m else map_html
    except Exception:
        map_html = _fallback_map_table(locations)

    # --- Render sections ---
    stats_cards = _render_stats_cards(stats, movement)
    movement_section = _render_movement_section(movement)
    places_section = _render_places_section(places)
    anomaly_section = _render_anomaly_section(anomalies, summary["anomaly_stats"])
    map_section = f'<div class="map-wrap">{map_html}</div>'

    html = _HTML_TEMPLATE.format(
        stats_cards=stats_cards,
        movement_section=movement_section,
        places_section=places_section,
        anomaly_section=anomaly_section,
        map_section=map_section,
    )
    output_path.write_text(html, encoding="utf-8")


# ---------------------------------------------------------------------------
# HTML rendering helpers
# ---------------------------------------------------------------------------


def _render_stats_cards(stats: Dict[str, Any], movement: Dict[str, Any]) -> str:
    cards = [
        ("📍 Total Locations", stats.get("count", 0), "#60a5fa"),
        ("🗺 With GPS", stats.get("with_gps", 0), "#34d399"),
        ("📌 Unique Places", stats.get("unique_places", 0), "#a78bfa"),
        ("⏱ Time Span", _esc(stats.get("time_span", "—")), "#f59e0b"),
        ("🚶 Movement", _esc(stats.get("movement_pattern", "unknown")), "#fb7185"),
        (
            "📏 Total Distance",
            f"{movement.get('statistics', {}).get('total_distance', 0):.1f} km",
            "#38bdf8",
        ),
        (
            "⚡ Max Speed",
            f"{movement.get('statistics', {}).get('max_speed', 0):.1f} km/h",
            "#e879f9",
        ),
        (
            "🛑 Stationary Periods",
            len(movement.get("stationary_periods", [])),
            "#94a3b8",
        ),
    ]
    parts = []
    for label, value, color in cards:
        parts.append(
            f"""
        <div class="stat-card" style="border-top:3px solid {color}">
            <div class="stat-value" style="color:{color}">{value}</div>
            <div class="stat-label">{label}</div>
        </div>"""
        )
    return "".join(parts)


def _render_movement_section(movement: Dict[str, Any]) -> str:
    overall = _esc(movement.get("overall", "unknown"))
    seg_rows = ""
    for seg in movement.get("segments", [])[:50]:  # cap for readability
        mt = _esc(seg.get("movement_type", ""))
        color = {
            "stationary": "#94a3b8",
            "walking": "#34d399",
            "biking": "#f59e0b",
            "driving": "#ef4444",
        }.get(seg.get("movement_type", ""), "#e2e8f0")
        seg_rows += f"""
        <tr>
            <td>{_esc(seg.get('from_timestamp', '—'))}</td>
            <td>{_esc(seg.get('to_timestamp', '—'))}</td>
            <td>{seg.get('distance_km', 0):.3f} km</td>
            <td>{seg.get('speed_kmh', 0):.1f} km/h</td>
            <td><span class="badge" style="background:{color}">{mt}</span></td>
        </tr>"""

    stat = movement.get("statistics", {})
    mt_counts = stat.get("movement_types", {})
    return f"""
    <section class="card">
        <h2>🚗 Movement Pattern: <span class="highlight">{overall}</span></h2>
        <div class="sub-stats">
            <span>Total: <b>{stat.get('total_distance', 0):.2f} km</b></span>
            <span>Avg speed: <b>{stat.get('avg_speed', 0):.1f} km/h</b></span>
            <span>Max speed: <b>{stat.get('max_speed', 0):.1f} km/h</b></span>
            <span>Stationary: <b>{mt_counts.get('stationary', 0)}</b></span>
            <span>Walking: <b>{mt_counts.get('walking', 0)}</b></span>
            <span>Biking: <b>{mt_counts.get('biking', 0)}</b></span>
            <span>Driving: <b>{mt_counts.get('driving', 0)}</b></span>
        </div>
        <div class="table-wrap">
        <table>
            <thead><tr>
                <th>From</th><th>To</th><th>Distance</th><th>Speed</th><th>Type</th>
            </tr></thead>
            <tbody>{seg_rows}</tbody>
        </table>
        </div>
    </section>"""


def _render_places_section(places: Dict[str, Any]) -> str:
    def place_row(label: str, place: Optional[Dict[str, Any]]) -> str:
        if not place:
            return f'<tr><td><b>{label}</b></td><td colspan="4" class="muted">Not identified</td></tr>'
        center = place.get("center", {})
        lat = center.get("lat", "—")
        lon = center.get("lon", "—")
        conf = place.get("confidence", 0)
        visits = place.get("count", 0)
        name = _esc(place.get("place_name", ""))
        return (
            f"<tr><td><b>{label}</b></td>"
            f"<td>{name}</td>"
            f"<td>{lat:.6f}, {lon:.6f}</td>"
            f"<td>{visits}</td>"
            f"<td>{conf:.0%}</td></tr>"
        )

    home_row = place_row("🏠 Home", places.get("home"))
    work_row = place_row("🏢 Work", places.get("work"))

    freq_rows = ""
    for i, fp in enumerate(places.get("frequent_places", [])[:10], 1):
        center = fp.get("center", {})
        lat = center.get("lat", "—")
        lon = center.get("lon", "—")
        visits = fp.get("count", 0)
        conf = fp.get("confidence", 0)
        name = _esc(fp.get("place_name", ""))
        freq_rows += (
            f"<tr><td>#{i} Frequent</td>"
            f"<td>{name}</td>"
            f"<td>{lat:.6f}, {lon:.6f}</td>"
            f"<td>{visits}</td>"
            f"<td>{conf:.0%}</td></tr>"
        )

    return f"""
    <section class="card">
        <h2>📌 Place Identification</h2>
        <div class="table-wrap">
        <table>
            <thead><tr>
                <th>Type</th><th>Name / Coordinates</th><th>Location</th>
                <th>Visits</th><th>Confidence</th>
            </tr></thead>
            <tbody>{home_row}{work_row}{freq_rows}</tbody>
        </table>
        </div>
    </section>"""


def _render_anomaly_section(
    anomalies: List[Dict[str, Any]],
    anom_stats: Dict[str, Any],
) -> str:
    if not anomalies:
        return '<section class="card"><h2>⚠ Anomalies</h2><p class="muted">No anomalies detected.</p></section>'

    rows = ""
    for a in anomalies:
        sev = a.get("severity", "info")
        color = _sev_color(sev)
        loc = a.get("location", {})
        lat = loc.get("lat")
        lon = loc.get("lon")
        coord = (
            f"{lat:.5f}, {lon:.5f}" if (lat is not None and lon is not None) else "—"
        )
        rows += f"""
        <tr>
            <td><span class="badge" style="background:{color}">{_esc(sev.upper())}</span></td>
            <td>{_esc(a.get('type', ''))}</td>
            <td>{_esc(a.get('timestamp', '—'))}</td>
            <td>{coord}</td>
            <td class="muted">{_esc(a.get('explanation', ''))}</td>
        </tr>"""

    total = anom_stats.get("total_anomalies", 0)
    return f"""
    <section class="card">
        <h2>⚠ Anomalies <span class="chip">{total} detected</span></h2>
        <div class="table-wrap">
        <table>
            <thead><tr>
                <th>Severity</th><th>Type</th><th>Timestamp</th>
                <th>Location</th><th>Explanation</th>
            </tr></thead>
            <tbody>{rows}</tbody>
        </table>
        </div>
    </section>"""


def _fallback_map_table(locations: List[Dict[str, Any]]) -> str:
    """Simple coordinate table for when Folium is not available."""
    rows = ""
    gps_locs = []
    for loc in locations:
        lat, lon = _flat_lat_lon(loc)
        if lat is not None and lon is not None:
            gps_locs.append(
                (lat, lon, loc.get("timestamp", "—"), loc.get("source", "—"))
            )
    for lat, lon, ts, src in gps_locs[:100]:
        rows += f"<tr><td>{ts}</td><td>{lat:.6f}</td><td>{lon:.6f}</td><td>{_esc(src)}</td></tr>"
    return f"""
    <p class="muted" style="margin-bottom:12px;">
        Map unavailable — install <code>folium</code> for interactive map output.
    </p>
    <div class="table-wrap">
    <table>
        <thead><tr><th>Timestamp</th><th>Latitude</th><th>Longitude</th><th>Source</th></tr></thead>
        <tbody>{rows}</tbody>
    </table>
    </div>"""


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Location Summary — eRakshak Forensic Engine</title>
<meta name="description" content="GPS location analysis summary generated by eRakshak forensic engine.">
<style>
  :root {{
    --bg: #0b0f1a;
    --surface: #131929;
    --card: #1a2235;
    --border: #243050;
    --text: #e2e8f0;
    --muted: #64748b;
    --accent: #3b82f6;
    --radius: 12px;
  }}
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: var(--bg);
    color: var(--text);
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    font-size: 14px;
    padding: 32px 20px;
    line-height: 1.6;
  }}
  .page-header {{
    margin-bottom: 40px;
    border-left: 4px solid var(--accent);
    padding-left: 20px;
  }}
  h1 {{
    font-size: 2rem;
    font-weight: 700;
    background: linear-gradient(135deg, #60a5fa 0%, #a78bfa 50%, #fb7185 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    margin-bottom: 6px;
  }}
  .subtitle {{ color: var(--muted); font-size: 0.9rem; }}
  .stats-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
    gap: 16px;
    margin-bottom: 36px;
  }}
  .stat-card {{
    background: var(--card);
    border-radius: var(--radius);
    padding: 18px 16px;
    border: 1px solid var(--border);
    transition: transform 0.15s, box-shadow 0.15s;
  }}
  .stat-card:hover {{
    transform: translateY(-3px);
    box-shadow: 0 8px 32px rgba(0,0,0,0.5);
  }}
  .stat-value {{ font-size: 1.6rem; font-weight: 700; margin-bottom: 4px; }}
  .stat-label {{ font-size: 0.75rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; }}
  .card {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 24px;
    margin-bottom: 24px;
  }}
  h2 {{
    font-size: 1.1rem;
    font-weight: 600;
    color: var(--text);
    margin-bottom: 16px;
    display: flex;
    align-items: center;
    gap: 10px;
  }}
  .highlight {{ color: #60a5fa; }}
  .chip {{
    background: #1e3a5f;
    color: #93c5fd;
    border-radius: 999px;
    padding: 2px 10px;
    font-size: 0.75rem;
    font-weight: 600;
  }}
  .sub-stats {{
    display: flex;
    flex-wrap: wrap;
    gap: 16px;
    margin-bottom: 16px;
    font-size: 0.85rem;
    color: var(--muted);
  }}
  .sub-stats b {{ color: var(--text); }}
  .table-wrap {{ overflow-x: auto; }}
  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.82rem;
  }}
  th {{
    text-align: left;
    color: var(--muted);
    font-weight: 600;
    padding: 10px 12px;
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
    text-transform: uppercase;
    font-size: 0.72rem;
    letter-spacing: 0.05em;
  }}
  td {{
    padding: 10px 12px;
    border-bottom: 1px solid #1e2d45;
    vertical-align: top;
    max-width: 360px;
    word-break: break-word;
  }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover td {{ background: rgba(59,130,246,0.05); }}
  .badge {{
    display: inline-block;
    font-size: 0.65rem;
    font-weight: 700;
    letter-spacing: 0.06em;
    color: #fff;
    border-radius: 4px;
    padding: 2px 8px;
    text-transform: uppercase;
  }}
  .muted {{ color: var(--muted); font-size: 0.82rem; }}
  .map-wrap {{
    border-radius: var(--radius);
    overflow: hidden;
    border: 1px solid var(--border);
    min-height: 400px;
    margin-bottom: 24px;
  }}
  .map-wrap iframe, .map-wrap div.folium-map {{
    border-radius: var(--radius);
  }}
  code {{
    background: #1e2d45;
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 0.85em;
  }}
</style>
</head>
<body>
<header class="page-header">
  <h1>📍 Location Analysis Report</h1>
  <p class="subtitle">GPS trace summary — generated by eRakshak forensic engine</p>
</header>

<div class="stats-grid">
{stats_cards}
</div>

{movement_section}

{places_section}

{anomaly_section}

<section class="card">
  <h2>🗺 Location Map</h2>
  {map_section}
</section>

</body>
</html>
"""
