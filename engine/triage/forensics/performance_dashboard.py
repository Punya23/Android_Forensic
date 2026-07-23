"""Performance Dashboard — real-time acquisition metrics.

Generates a dark-themed HTML report displaying acquisition performance,
including stage timings, throughput, bottleneck detection, and heatmaps.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List

from engine.triage.metrics import get_performance_report

logger = logging.getLogger(__name__)


def get_real_time_metrics() -> Dict[str, Any]:
    """Get current performance metrics."""
    return get_performance_report()


def get_stage_times() -> Dict[str, float]:
    """Get time per stage."""
    report = get_real_time_metrics()
    stages = report.get("stages", {})
    return {name: data.get("total_s", 0.0) for name, data in stages.items()}


def get_bottlenecks() -> List[Dict[str, Any]]:
    """Detect bottlenecks based on stage times."""
    stage_times = get_stage_times()
    if not stage_times:
        return []
        
    total_time = sum(stage_times.values())
    if total_time == 0:
        return []
        
    avg_time = total_time / len(stage_times)
    bottlenecks = []
    
    for stage, time_s in stage_times.items():
        if time_s > avg_time * 2 and time_s > 5.0:
            percentage = (time_s / total_time) * 100
            
            suggestion = "Optimize parsing"
            if stage == "pull":
                suggestion = "Use batch transfer or pre-fetch"
            elif stage == "hash":
                suggestion = "Use memory mapping for large files"
                
            bottlenecks.append({
                "stage": stage,
                "time_s": round(time_s, 2),
                "percentage": round(percentage, 1),
                "suggestion": suggestion
            })
            
    # Sort by time descending
    return sorted(bottlenecks, key=lambda x: x["time_s"], reverse=True)


def generate_performance_heatmap() -> str:
    """Generate a CSS/HTML based performance heatmap."""
    stage_times = get_stage_times()
    if not stage_times:
        return "<div class='muted'>No stage data available yet.</div>"
        
    total_time = sum(stage_times.values())
    if total_time == 0:
        return "<div class='muted'>Total time is zero.</div>"
        
    html = ['<div style="display: flex; height: 30px; width: 100%; border-radius: 6px; overflow: hidden; margin-top: 10px;">']
    
    colors = ["#3b82f6", "#ef4444", "#10b981", "#f59e0b", "#8b5cf6", "#ec4899", "#14b8a6"]
    
    legend = ['<div style="display: flex; flex-wrap: wrap; gap: 15px; margin-top: 10px; font-size: 0.8rem;">']
    
    for i, (stage, time_s) in enumerate(sorted(stage_times.items(), key=lambda x: x[1], reverse=True)):
        pct = (time_s / total_time) * 100
        if pct < 1.0:
            continue
            
        color = colors[i % len(colors)]
        html.append(f'<div style="width: {pct}%; background-color: {color};" title="{stage}: {time_s:.1f}s ({pct:.1f}%)"></div>')
        legend.append(f'<div style="display: flex; align-items: center; gap: 5px;"><div style="width: 10px; height: 10px; background-color: {color}; border-radius: 2px;"></div>{stage} ({pct:.1f}%)</div>')
        
    html.append('</div>')
    legend.append('</div>')
    
    return "".join(html) + "".join(legend)


def generate_performance_dashboard(case_dir: Path) -> str:
    """Generate HTML performance dashboard in the case directory."""
    metrics = get_real_time_metrics()
    total_s = metrics.get("total_elapsed_s", 0)
    mb_min = metrics.get("mb_per_min", 0)
    bytes_proc = metrics.get("bytes_processed", 0)
    mb_proc = bytes_proc / (1024 * 1024)
    
    heatmap = generate_performance_heatmap()
    
    bottlenecks = get_bottlenecks()
    bottleneck_html = ""
    if bottlenecks:
        bottleneck_html = "<table><thead><tr><th>Stage</th><th>Time (s)</th><th>% of Total</th><th>Suggestion</th></tr></thead><tbody>"
        for b in bottlenecks:
            bottleneck_html += f"<tr><td>{b['stage']}</td><td>{b['time_s']}</td><td>{b['percentage']}%</td><td>{b['suggestion']}</td></tr>"
        bottleneck_html += "</tbody></table>"
    else:
        bottleneck_html = "<p class='muted'>No significant bottlenecks detected.</p>"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Performance Dashboard</title>
<style>
  :root {{ --bg: #0b0f1a; --surface: #131929; --card: #1a2235; --border: #243050; --text: #e2e8f0; --muted: #64748b; --accent: #3b82f6; --radius: 12px; }}
  body {{ background: var(--bg); color: var(--text); font-family: 'Segoe UI', system-ui, sans-serif; padding: 32px 20px; line-height: 1.6; }}
  .card {{ background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 24px; margin-bottom: 24px; }}
  h1 {{ font-size: 2rem; background: linear-gradient(135deg, #60a5fa, #a78bfa); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
  h2 {{ font-size: 1.2rem; margin-bottom: 16px; border-bottom: 1px solid var(--border); padding-bottom: 8px; }}
  .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }}
  .stat-card {{ background: var(--surface); padding: 16px; border-radius: 8px; border: 1px solid var(--border); }}
  .stat-val {{ font-size: 1.8rem; font-weight: bold; color: var(--accent); }}
  .stat-label {{ font-size: 0.8rem; color: var(--muted); text-transform: uppercase; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
  th {{ text-align: left; padding: 10px; border-bottom: 2px solid var(--border); color: var(--muted); }}
  td {{ padding: 10px; border-bottom: 1px solid var(--border); }}
  .muted {{ color: var(--muted); }}
</style>
</head>
<body>
<h1>⚡ Performance Dashboard</h1>

<div class="stats-grid">
  <div class="stat-card"><div class="stat-val">{total_s:.1f}s</div><div class="stat-label">Total Time</div></div>
  <div class="stat-card"><div class="stat-val">{mb_min:.1f}</div><div class="stat-label">MB / Minute</div></div>
  <div class="stat-card"><div class="stat-val">{mb_proc:.1f}</div><div class="stat-label">MB Processed</div></div>
</div>

<div class="card">
  <h2>Time Distribution</h2>
  {heatmap}
</div>

<div class="card">
  <h2>Bottleneck Detection</h2>
  {bottleneck_html}
</div>
</body>
</html>"""
    
    try:
        reports_dir = Path(case_dir) / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        report_path = reports_dir / "performance.html"
        report_path.write_text(html, encoding="utf-8")
        return str(report_path)
    except Exception as exc:
        logger.error("Failed to write performance dashboard: %s", exc)
        return ""
