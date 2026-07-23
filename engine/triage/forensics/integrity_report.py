"""Hash Integrity Report — comprehensive hash reporting.

Generates a detailed HTML hash integrity report including a full manifest,
verification summaries, and forensic recommendations based on integrity checks.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List

from engine.triage.forensics.hash_verification import verify_all_hashes

logger = logging.getLogger(__name__)


def get_integrity_summary(case_dir: Path) -> Dict[str, Any]:
    """Get integrity summary by running verification."""
    try:
        return verify_all_hashes(case_dir)
    except Exception as exc:
        logger.error("Failed to get integrity summary: %s", exc)
        return {
            "total_files": 0,
            "verified": 0,
            "failed": 0,
            "integrity_status": "ERROR",
            "failed_files": [],
        }


def get_recommendations(case_dir: Path) -> List[str]:
    """Get recommendations based on integrity check."""
    summary = get_integrity_summary(case_dir)
    recs = []

    if summary["failed"] > 0:
        recs.append(
            "⚠️ CRITICAL: Hash mismatches detected. Evidence may have been tampered with or corrupted."
        )
        recs.append(
            "Recommendation: Re-acquire the device or restore from a trusted backup immediately."
        )
        recs.append(
            f"Review the {summary['failed']} failed files closely to determine if they are critical to the case."
        )
    else:
        recs.append("✅ All files verified successfully. Evidence integrity is intact.")
        recs.append("Recommendation: Proceed with analysis using standard procedures.")

    if summary["total_files"] == 0:
        recs.append(
            "ℹ️ No files found with hashes in the manifest. Ensure hashing was enabled during acquisition."
        )

    return recs


def generate_detailed_manifest(case_dir: Path) -> str:
    """Generate detailed manifest HTML table."""
    try:
        import json

        manifest_path = case_dir / "manifest.json"
        if not manifest_path.exists():
            return "<p class='muted'>No manifest found.</p>"

        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            artifacts = data.get("artifacts", [])

        if not artifacts:
            return "<p class='muted'>Manifest is empty.</p>"

        html = [
            "<table><thead><tr><th>File Path</th><th>SHA-256</th><th>MD5</th><th>Size</th></tr></thead><tbody>"
        ]

        for art in artifacts:
            path = art.get("path") or art.get("stored_path", "unknown")
            sha256 = art.get("sha256_hash", "-")
            md5 = art.get("md5_hash", "-")
            size = art.get("size_bytes", 0)

            html.append(
                f"<tr><td style='word-break:break-all'>{path}</td>"
                f"<td style='font-family:monospace;font-size:0.85em'>{sha256}</td>"
                f"<td style='font-family:monospace;font-size:0.85em'>{md5}</td>"
                f"<td>{size} B</td></tr>"
            )

        html.append("</tbody></table>")
        return "".join(html)

    except Exception as exc:
        logger.error("Failed to generate detailed manifest: %s", exc)
        return f"<p class='muted'>Error loading manifest: {exc}</p>"


def generate_integrity_report(case_dir: Path) -> str:
    """Generate detailed integrity report HTML."""
    summary = get_integrity_summary(case_dir)
    status = summary.get("integrity_status", "UNKNOWN")
    status_color = (
        "#10b981"
        if status == "INTACT"
        else "#ef4444" if status == "TAMPERED" else "#64748b"
    )

    recs_html = "".join(f"<li>{r}</li>" for r in get_recommendations(case_dir))
    manifest_html = generate_detailed_manifest(case_dir)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Detailed Hash Integrity Report</title>
<style>
  :root {{ --bg: #f8fafc; --surface: #ffffff; --border: #e2e8f0; --text: #1e293b; --muted: #64748b; --accent: #3b82f6; }}
  body {{ background: var(--bg); color: var(--text); font-family: 'Segoe UI', system-ui, sans-serif; padding: 32px 20px; line-height: 1.6; max-width: 1200px; margin: 0 auto; }}
  .card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 24px; margin-bottom: 24px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
  h1 {{ font-size: 2rem; color: #0f172a; border-bottom: 2px solid var(--border); padding-bottom: 10px; margin-bottom: 20px; }}
  h2 {{ font-size: 1.3rem; margin-top: 0; color: #334155; border-bottom: 1px solid var(--border); padding-bottom: 8px; }}
  .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }}
  .stat-card {{ background: var(--bg); padding: 16px; border-radius: 6px; border: 1px solid var(--border); text-align: center; }}
  .stat-val {{ font-size: 1.8rem; font-weight: bold; }}
  .stat-label {{ font-size: 0.8rem; color: var(--muted); text-transform: uppercase; font-weight: 600; }}
  .status-badge {{ background-color: {status_color}; color: white; padding: 6px 16px; border-radius: 20px; font-weight: bold; display: inline-block; margin-bottom: 24px; font-size: 1.1rem; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; text-align: left; margin-top: 10px; }}
  th, td {{ padding: 12px 8px; border-bottom: 1px solid var(--border); }}
  th {{ background: var(--bg); color: #475569; font-weight: 600; }}
  .recs {{ background: #fefce8; border: 1px solid #fef08a; padding: 16px 24px; border-radius: 6px; }}
  .recs li {{ margin-bottom: 8px; color: #854d0e; }}
</style>
</head>
<body>
<h1>🔍 Detailed Hash Integrity Report</h1>
<div class="status-badge">INTEGRITY STATUS: {status}</div>

<div class="stats-grid">
  <div class="stat-card">
    <div class="stat-val">{summary.get('total_files', 0)}</div>
    <div class="stat-label">Total Files</div>
  </div>
  <div class="stat-card">
    <div class="stat-val" style="color: #10b981;">{summary.get('verified', 0)}</div>
    <div class="stat-label">Verified Intact</div>
  </div>
  <div class="stat-card">
    <div class="stat-val" style="color: #ef4444;">{summary.get('failed', 0)}</div>
    <div class="stat-label">Failed / Tampered</div>
  </div>
</div>

<div class="card recs">
  <h2 style="border-bottom-color: #fde047; color: #854d0e;">Forensic Recommendations</h2>
  <ul>{recs_html}</ul>
</div>

<div class="card">
  <h2>Detailed Hash Manifest</h2>
  <div style="overflow-x: auto;">
    {manifest_html}
  </div>
</div>

<footer style="text-align: center; color: var(--muted); font-size: 0.85rem; margin-top: 40px;">
  Generated automatically by the Triage Forensic Engine
</footer>
</body>
</html>"""
    return html


def export_integrity_report(case_dir: Path, output_path: Path) -> None:
    """Export integrity report to file."""
    try:
        html = generate_integrity_report(case_dir)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(html, encoding="utf-8")
        logger.info("Exported integrity report to %s", output_path)
    except Exception as exc:
        logger.error("Failed to export integrity report: %s", exc)
