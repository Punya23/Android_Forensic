"""Hash Verification Dashboard — verify hashes against manifest.

Validates the integrity of extracted files by comparing their current
hashes against the original hashes recorded in the manifest.json.
Generates an HTML dashboard reporting the results.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def load_manifest(case_dir: Path) -> List[Dict]:
    """Load the hash manifest as a list of artifact records.

    ``custody.Case`` writes ``manifest.json`` as a top-level JSON *list* — one
    dict per artifact whose keys mirror ``models.ArtifactRecord`` (``stored_path``,
    ``sha256``, ``md5``, ``extracted_at``, ...). Some defensive callers may wrap it
    as ``{"artifacts": [...]}``; accept both so verification never silently reads
    zero files. Returning the wrong shape here means the integrity check becomes a
    no-op, so this loader is deliberately tolerant.
    """
    manifest_path = case_dir / "manifest.json"
    if not manifest_path.exists():
        return []

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        logger.error("Failed to load manifest %s: %s", manifest_path, exc)
        return []

    if isinstance(data, dict):
        data = data.get("artifacts", [])
    return data if isinstance(data, list) else []


def artifact_sha256(artifact: Dict) -> str:
    """SHA-256 of a manifest record, tolerating the legacy ``sha256_hash`` key."""
    return artifact.get("sha256") or artifact.get("sha256_hash") or ""


def verify_single_file(file_path: Path, expected_hash: str) -> bool:
    """Verify a single file's hash matches the expected hash."""
    if not file_path.exists() or not expected_hash:
        return False

    try:
        # We assume sha256 for expected_hash
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                sha256.update(chunk)

        actual_hash = sha256.hexdigest()
        return actual_hash.lower() == expected_hash.lower()
    except Exception as exc:
        logger.warning("Error hashing %s: %s", file_path, exc)
        return False


def verify_all_hashes(case_dir: Path) -> Dict[str, Any]:
    """Verify all hashes in manifest."""
    start_time = time.monotonic()
    artifacts = load_manifest(case_dir)

    total_files = 0
    verified = 0
    failed = 0
    failed_files = []

    for artifact in artifacts:
        rel_path = artifact.get("stored_path")
        expected_hash = artifact_sha256(artifact)

        if not rel_path or not expected_hash:
            continue

        file_path = case_dir / rel_path
        total_files += 1

        if verify_single_file(file_path, expected_hash):
            verified += 1
        else:
            failed += 1
            failed_files.append(
                {
                    "path": rel_path,
                    "expected": expected_hash,
                }
            )

    elapsed = time.monotonic() - start_time
    status = (
        "INTACT"
        if failed == 0 and total_files > 0
        else "TAMPERED" if failed > 0 else "UNKNOWN"
    )

    return {
        "total_files": total_files,
        "verified": verified,
        "failed": failed,
        "integrity_status": status,
        "verification_time": elapsed,
        "failed_files": failed_files,
    }


def get_verification_summary(case_dir: Path) -> Dict[str, Any]:
    """Get verification summary (returns same structure as verify_all_hashes but cached)."""
    # For now, it just calculates on the fly, but in a real app this would read from a cached result
    return verify_all_hashes(case_dir)


def generate_verification_dashboard(case_dir: Path) -> str:
    """Generate HTML verification dashboard."""
    verification = verify_all_hashes(case_dir)

    total = verification["total_files"]
    verified = verification["verified"]
    failed = verification["failed"]
    status = verification["integrity_status"]
    elapsed = verification["verification_time"]
    failed_files = verification["failed_files"]

    status_color = (
        "#10b981"
        if status == "INTACT"
        else "#ef4444" if status == "TAMPERED" else "#64748b"
    )

    failed_html = ""
    if failed_files:
        failed_html = (
            "<table><thead><tr><th>File</th><th>Expected Hash</th></tr></thead><tbody>"
        )
        for f in failed_files:
            failed_html += f"<tr><td>{f['path']}</td><td>{f['expected']}</td></tr>"
        failed_html += "</tbody></table>"
    else:
        failed_html = "<p class='muted'>All files verified successfully.</p>"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hash Verification Dashboard</title>
<style>
  :root {{ --bg: #0b0f1a; --surface: #131929; --card: #1a2235; --border: #243050; --text: #e2e8f0; --muted: #64748b; --accent: #3b82f6; --radius: 12px; }}
  body {{ background: var(--bg); color: var(--text); font-family: 'Segoe UI', system-ui, sans-serif; padding: 32px 20px; line-height: 1.6; }}
  .card {{ background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 24px; margin-bottom: 24px; }}
  h1 {{ font-size: 2rem; background: linear-gradient(135deg, #60a5fa, #a78bfa); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
  h2 {{ font-size: 1.2rem; margin-bottom: 16px; border-bottom: 1px solid var(--border); padding-bottom: 8px; }}
  .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }}
  .stat-card {{ background: var(--surface); padding: 16px; border-radius: 8px; border: 1px solid var(--border); }}
  .stat-val {{ font-size: 1.8rem; font-weight: bold; }}
  .stat-label {{ font-size: 0.8rem; color: var(--muted); text-transform: uppercase; }}
  .status-badge {{ background-color: {status_color}; color: white; padding: 4px 12px; border-radius: 16px; font-weight: bold; font-size: 0.9rem; display: inline-block; margin-bottom: 20px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; text-align: left; }}
  th, td {{ padding: 12px; border-bottom: 1px solid var(--border); }}
  th {{ color: var(--muted); font-weight: normal; }}
  .muted {{ color: var(--muted); }}
</style>
</head>
<body>
<h1>🛡️ Hash Verification</h1>
<div class="status-badge">STATUS: {status}</div>

<div class="stats-grid">
  <div class="stat-card">
    <div class="stat-val">{total}</div>
    <div class="stat-label">Total Files</div>
  </div>
  <div class="stat-card">
    <div class="stat-val" style="color: #10b981;">{verified}</div>
    <div class="stat-label">Verified</div>
  </div>
  <div class="stat-card">
    <div class="stat-val" style="color: #ef4444;">{failed}</div>
    <div class="stat-label">Failed</div>
  </div>
  <div class="stat-card">
    <div class="stat-val">{elapsed:.1f}s</div>
    <div class="stat-label">Verification Time</div>
  </div>
</div>

<div class="card">
  <h2>Failed Files</h2>
  {failed_html}
</div>
</body>
</html>"""

    try:
        reports_dir = case_dir / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        report_path = reports_dir / "hash_verification.html"
        report_path.write_text(html, encoding="utf-8")
        return str(report_path)
    except Exception as exc:
        logger.error("Failed to write verification dashboard: %s", exc)
        return ""
