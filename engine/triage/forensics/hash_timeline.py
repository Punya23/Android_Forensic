"""Hash Timeline — track and visualize file hashing performance over time.

Analyzes manifest or audit logs to determine when files were hashed and
how long the hashing took, presenting the data in an HTML timeline chart.
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def get_hash_timeline(case_dir: Path) -> List[Dict[str, Any]]:
    """Get timeline of when files were hashed.

    In a real implementation, this would parse detailed audit logs or metrics.
    For demonstration, we mock elapsed time based on file size if not available.
    """
    manifest_path = case_dir / "manifest.json"
    if not manifest_path.exists():
        return []

    timeline = []
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            artifacts = data.get("artifacts", [])

            for idx, artifact in enumerate(artifacts):
                path = artifact.get("path") or artifact.get("stored_path", "unknown")
                sha256 = artifact.get("sha256_hash", "")
                size = artifact.get("size_bytes", 0)

                # Mock a timestamp and elapsed time if they aren't in the manifest
                # Typically these would be tracked via ContinuousHashVerifier or pipeline telemetry
                ts = artifact.get("timestamp", 1700000000 + idx)
                # Estimate: 100 MB/s hashing speed = 0.01 seconds per MB
                elapsed = artifact.get(
                    "hash_duration_s", max(0.001, size / (100 * 1024 * 1024))
                )

                if sha256:
                    timeline.append(
                        {
                            "timestamp": ts,
                            "file": path,
                            "hash": sha256,
                            "size": size,
                            "time_elapsed": elapsed,
                        }
                    )
    except Exception as exc:
        logger.error("Failed to load hash timeline from %s: %s", case_dir, exc)

    # Sort chronologically
    return sorted(timeline, key=lambda x: x["timestamp"])


def get_hash_speed_stats(timeline: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Get hash speed statistics."""
    if not timeline:
        return {"avg_time": 0, "max_time": 0, "min_time": 0, "total_time": 0}

    times = [item["time_elapsed"] for item in timeline]
    return {
        "avg_time": sum(times) / len(times),
        "max_time": max(times),
        "min_time": min(times),
        "total_time": sum(times),
    }


def get_slowest_files(
    timeline: List[Dict[str, Any]], limit: int = 10
) -> List[Dict[str, Any]]:
    """Get the slowest files to hash."""
    return sorted(timeline, key=lambda x: x["time_elapsed"], reverse=True)[:limit]


def create_timeline_chart(timeline: List[Dict[str, Any]]) -> str:
    """Create an HTML/CSS bar chart of hash times."""
    if not timeline:
        return "<p class='muted'>No timeline data available.</p>"

    max_time = max(item["time_elapsed"] for item in timeline)
    if max_time == 0:
        max_time = 0.001

    bars = ""
    # Simplify chart if too many items (sample up to 50)
    sample_rate = max(1, len(timeline) // 50)
    sampled = timeline[::sample_rate][:50]

    for item in sampled:
        height_pct = min(100, (item["time_elapsed"] / max_time) * 100)
        # Ensure minimum height of 1px for visibility
        height_pct = max(1, height_pct)

        # Color red if it's very slow (> 1 second)
        color = "#ef4444" if item["time_elapsed"] > 1.0 else "#3b82f6"

        tooltip = f"{item['file']}&#10;Time: {item['time_elapsed']:.3f}s"
        bars += f'<div style="width:10px; height:{height_pct}%; background:{color}; margin:0 2px; display:inline-block; border-radius:2px 2px 0 0;" title="{tooltip}"></div>'

    html = f"""
    <div style="height: 150px; display: flex; align-items: flex-end; border-bottom: 1px solid var(--border); margin-bottom: 10px;">
        {bars}
    </div>
    <div style="display: flex; justify-content: space-between; color: var(--muted); font-size: 0.8rem;">
        <span>Start</span>
        <span>End</span>
    </div>
    """
    return html


def generate_timeline_html(case_dir: Path) -> str:
    """Generate HTML timeline visualization."""
    timeline = get_hash_timeline(case_dir)
    stats = get_hash_speed_stats(timeline)
    slowest = get_slowest_files(timeline)

    chart_html = create_timeline_chart(timeline)

    slowest_html = ""
    if slowest:
        slowest_html = "<table><thead><tr><th>Time (s)</th><th>Size</th><th>File</th></tr></thead><tbody>"
        for item in slowest:
            size_mb = item["size"] / (1024 * 1024)
            slowest_html += f"<tr><td>{item['time_elapsed']:.3f}s</td><td>{size_mb:.1f} MB</td><td style='word-break:break-all'>{item['file']}</td></tr>"
        slowest_html += "</tbody></table>"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hash Timeline & Performance</title>
<style>
  :root {{ --bg: #0b0f1a; --surface: #131929; --card: #1a2235; --border: #243050; --text: #e2e8f0; --muted: #64748b; --accent: #3b82f6; --radius: 12px; }}
  body {{ background: var(--bg); color: var(--text); font-family: 'Segoe UI', system-ui, sans-serif; padding: 32px 20px; line-height: 1.6; }}
  .card {{ background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 24px; margin-bottom: 24px; }}
  h1 {{ font-size: 2rem; background: linear-gradient(135deg, #60a5fa, #a78bfa); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
  h2 {{ font-size: 1.2rem; margin-bottom: 16px; border-bottom: 1px solid var(--border); padding-bottom: 8px; }}
  .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; margin-bottom: 24px; }}
  .stat-card {{ background: var(--surface); padding: 16px; border-radius: 8px; border: 1px solid var(--border); text-align: center; }}
  .stat-val {{ font-size: 1.5rem; font-weight: bold; color: var(--accent); }}
  .stat-label {{ font-size: 0.8rem; color: var(--muted); text-transform: uppercase; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; text-align: left; }}
  th, td {{ padding: 12px; border-bottom: 1px solid var(--border); }}
  th {{ color: var(--muted); font-weight: normal; }}
  .muted {{ color: var(--muted); }}
</style>
</head>
<body>
<h1>⏱️ Hash Timeline</h1>

<div class="stats-grid">
  <div class="stat-card">
    <div class="stat-val">{len(timeline)}</div>
    <div class="stat-label">Files Hashed</div>
  </div>
  <div class="stat-card">
    <div class="stat-val">{stats['total_time']:.1f}s</div>
    <div class="stat-label">Total Time</div>
  </div>
  <div class="stat-card">
    <div class="stat-val">{stats['avg_time']:.3f}s</div>
    <div class="stat-label">Avg Time / File</div>
  </div>
  <div class="stat-card">
    <div class="stat-val">{stats['max_time']:.1f}s</div>
    <div class="stat-label">Max Time</div>
  </div>
</div>

<div class="card">
  <h2>Hashing Speed Over Time</h2>
  {chart_html}
</div>

<div class="card">
  <h2>Slowest Files to Hash</h2>
  {slowest_html}
</div>
</body>
</html>"""

    try:
        reports_dir = case_dir / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        report_path = reports_dir / "hash_timeline.html"
        report_path.write_text(html, encoding="utf-8")
        return str(report_path)
    except Exception as exc:
        logger.error("Failed to write timeline dashboard: %s", exc)
        return ""
