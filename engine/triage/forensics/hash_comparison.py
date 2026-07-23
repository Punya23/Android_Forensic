"""Hash Comparison Tool — compare hashes between two cases.

Provides functionality to compare hashes of two different acquisitions,
identifying files that are new, missing, identical, or modified (different hash).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def load_case_hashes(case_dir: Path) -> Dict[str, str]:
    """Load hashes from a case's manifest.json.
    
    Returns:
        Dict mapping sha256 to original file path (or stored path if preferred,
        but typically we map by path to compare hashes, or by hash to find dupes).
        For comparison, we'll map path -> sha256 to detect modifications.
    """
    manifest_path = case_dir / "manifest.json"
    if not manifest_path.exists():
        return {}
        
    hash_map = {}
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            artifacts = data.get("artifacts", [])
            for artifact in artifacts:
                # We use the original path as the key to detect modifications to the same file
                path = artifact.get("path") or artifact.get("stored_path")
                sha256 = artifact.get("sha256_hash")
                if path and sha256:
                    hash_map[path] = sha256
    except Exception as exc:
        logger.error("Failed to load hashes from %s: %s", case_dir, exc)
        
    return hash_map


def get_hash_differences(hash_map1: Dict[str, str], hash_map2: Dict[str, str]) -> Dict[str, List[str]]:
    """Get differences between two hash maps (paths -> hashes).
    
    Returns:
        Dict with keys: 'different', 'missing', 'new', 'same'
    """
    same = []
    different = []
    missing = []
    new = []
    
    # Check what's in map1 vs map2
    for path, hash1 in hash_map1.items():
        if path in hash_map2:
            hash2 = hash_map2[path]
            if hash1.lower() == hash2.lower():
                same.append(path)
            else:
                different.append(path)
        else:
            missing.append(path)
            
    # Check what's new in map2
    for path in hash_map2:
        if path not in hash_map1:
            new.append(path)
            
    return {
        "same": same,
        "different": different,
        "missing": missing,
        "new": new
    }


def get_similarity_score(case1: Path, case2: Path) -> float:
    """Calculate similarity score between cases based on identical hashes (0-100)."""
    hash_map1 = load_case_hashes(case1)
    hash_map2 = load_case_hashes(case2)
    
    if not hash_map1 and not hash_map2:
        return 100.0
    if not hash_map1 or not hash_map2:
        return 0.0
        
    differences = get_hash_differences(hash_map1, hash_map2)
    total_unique_paths = len(hash_map1) + len(differences["new"])
    
    if total_unique_paths == 0:
        return 100.0
        
    same_count = len(differences["same"])
    return (same_count / total_unique_paths) * 100.0


def compare_hashes(case1: Path, case2: Path) -> Dict[str, Any]:
    """Compare hashes between two cases and return a summary dictionary."""
    hash_map1 = load_case_hashes(case1)
    hash_map2 = load_case_hashes(case2)
    
    differences = get_hash_differences(hash_map1, hash_map2)
    
    total1 = len(hash_map1)
    total2 = len(hash_map2)
    
    total_unique = total1 + len(differences["new"])
    same_count = len(differences["same"])
    score = (same_count / total_unique * 100.0) if total_unique > 0 else 100.0
    
    return {
        "same": differences["same"],
        "different": differences["different"],
        "missing": differences["missing"],
        "new": differences["new"],
        "summary": {
            "case1_files": total1,
            "case2_files": total2,
            "identical": same_count,
            "modified": len(differences["different"]),
            "removed": len(differences["missing"]),
            "added": len(differences["new"]),
            "similarity_score": score
        }
    }


def generate_comparison_report(case1: Path, case2: Path) -> str:
    """Generate HTML comparison report."""
    results = compare_hashes(case1, case2)
    summary = results["summary"]
    
    c1_name = case1.name
    c2_name = case2.name
    
    score = summary['similarity_score']
    score_color = "#10b981" if score > 90 else "#f59e0b" if score > 70 else "#ef4444"
    
    def _create_list_html(items, title):
        if not items:
            return ""
        items_html = "".join(f"<li>{path}</li>" for path in items[:100])
        more = f"<li>... and {len(items)-100} more</li>" if len(items) > 100 else ""
        return f"<h3>{title} ({len(items)})</h3><ul class='file-list'>{items_html}{more}</ul>"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hash Comparison Report</title>
<style>
  :root {{ --bg: #0b0f1a; --surface: #131929; --card: #1a2235; --border: #243050; --text: #e2e8f0; --muted: #64748b; }}
  body {{ background: var(--bg); color: var(--text); font-family: 'Segoe UI', system-ui, sans-serif; padding: 32px 20px; line-height: 1.6; }}
  .card {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 24px; margin-bottom: 24px; }}
  h1 {{ font-size: 2rem; background: linear-gradient(135deg, #60a5fa, #a78bfa); -webkit-background-clip: text; -webkit-text-fill-color: transparent; margin-bottom: 10px; }}
  h2, h3 {{ border-bottom: 1px solid var(--border); padding-bottom: 8px; }}
  .score {{ font-size: 2.5rem; font-weight: bold; color: {score_color}; }}
  .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 16px; margin: 20px 0; }}
  .stat-box {{ background: var(--surface); padding: 15px; border-radius: 8px; text-align: center; border: 1px solid var(--border); }}
  .stat-num {{ font-size: 1.5rem; font-weight: bold; margin-bottom: 5px; }}
  .stat-label {{ font-size: 0.8rem; color: var(--muted); text-transform: uppercase; }}
  .file-list {{ max-height: 300px; overflow-y: auto; background: var(--surface); padding: 15px 15px 15px 35px; border-radius: 6px; font-family: monospace; font-size: 0.85rem; border: 1px solid var(--border); }}
  .diff {{ color: #f59e0b; }} .miss {{ color: #ef4444; }} .new {{ color: #10b981; }}
</style>
</head>
<body>
<h1>Hash Comparison Report</h1>
<p style="color:var(--muted)">Comparing <b>{c1_name}</b> (Baseline) vs <b>{c2_name}</b></p>

<div class="card" style="text-align:center">
  <div class="stat-label">Similarity Score</div>
  <div class="score">{score:.1f}%</div>
</div>

<div class="stats">
  <div class="stat-box"><div class="stat-num">{summary['case1_files']}</div><div class="stat-label">Files in {c1_name}</div></div>
  <div class="stat-box"><div class="stat-num">{summary['case2_files']}</div><div class="stat-label">Files in {c2_name}</div></div>
  <div class="stat-box"><div class="stat-num">{summary['identical']}</div><div class="stat-label">Identical</div></div>
  <div class="stat-box diff"><div class="stat-num">{summary['modified']}</div><div class="stat-label">Modified</div></div>
  <div class="stat-box miss"><div class="stat-num">{summary['removed']}</div><div class="stat-label">Removed</div></div>
  <div class="stat-box new"><div class="stat-num">{summary['added']}</div><div class="stat-label">Added</div></div>
</div>

<div class="card">
  <h2>Differences</h2>
  {_create_list_html(results['different'], "Modified Files (Hash Mismatch)")}
  {_create_list_html(results['missing'], "Missing Files (Removed)")}
  {_create_list_html(results['new'], "New Files (Added)")}
</div>
</body>
</html>"""

    # We save the report in case2's directory
    try:
        reports_dir = case2 / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        report_path = reports_dir / f"comparison_{c1_name}.html"
        report_path.write_text(html, encoding="utf-8")
        return str(report_path)
    except Exception as exc:
        logger.error("Failed to write comparison report: %s", exc)
        return ""
