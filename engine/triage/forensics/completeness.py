import html
from pathlib import Path
from typing import Dict
from ..custody import Case


def check_artifact_completeness(artifact_type: str, case_dir: Path) -> Dict:
    """Check completeness for specific artifact type."""
    try:
        case = Case.open(case_dir)
        manifest = case.manifest
    except Exception:
        return {
            "status": "error",
            "count": 0,
            "reason": "Failed to load case or manifest.",
        }

    count = sum(1 for item in manifest if item.category == artifact_type)

    # Very basic heuristics for demo purposes
    if count > 0:
        return {
            "status": "complete",
            "count": count,
            "reason": f"Found {count} artifacts of type {artifact_type}.",
        }
    else:
        return {
            "status": "skipped",
            "count": count,
            "reason": f"No {artifact_type} artifacts found.",
        }


def check_tier_completeness(tier: str, case_dir: Path) -> Dict:
    """Check completeness for specific tier."""
    try:
        case = Case.open(case_dir)
        manifest = case.manifest
    except Exception:
        return {"status": "error", "reason": "Failed to load case or manifest."}

    count = sum(1 for item in manifest if item.tier == tier)
    if count > 0:
        return {
            "status": "complete",
            "reason": f"Tier {tier} extraction yielded {count} items.",
        }
    else:
        return {"status": "skipped", "reason": f"No items extracted at tier {tier}."}


def generate_completeness_checklist(case_dir: Path) -> Dict:
    """Generate acquisition completeness checklist."""
    categories = ["messages", "calls", "contacts", "media", "locations", "other"]
    tiers = ["tier0", "tier1", "tier2"]

    checklist = {"artifacts": {}, "tiers": {}}

    for cat in categories:
        checklist["artifacts"][cat] = check_artifact_completeness(cat, case_dir)

    for tier in tiers:
        checklist["tiers"][tier] = check_tier_completeness(tier, case_dir)

    return checklist


def update_completeness_status(
    case_dir: Path, artifact: str, status: str, reason: str
) -> None:
    """Update completeness status and append to audit log."""
    try:
        case = Case.open(case_dir)
        case.log(
            action="completeness.update",
            detail=f"Updated status for {artifact} to {status}",
            result="ok",
            alters_device=False,
            extra={"artifact": artifact, "status": status, "reason": reason},
        )
    except Exception:
        pass


def generate_completeness_report(checklist: Dict) -> str:
    """Generate HTML completeness report."""
    html_out = [
        "<div class='completeness-report'>",
        "<h2>Acquisition Completeness</h2>",
    ]

    html_out.append("<h3>Artifact Categories</h3>")
    html_out.append(
        "<table><tr><th>Category</th><th>Status</th><th>Count</th><th>Reason</th></tr>"
    )

    for cat, data in checklist.get("artifacts", {}).items():
        status_color = "green" if data.get("status") == "complete" else "orange"
        html_out.append(f"<tr><td>{html.escape(cat)}</td>")
        html_out.append(
            f"<td style='color: {status_color};'>{html.escape(data.get('status', ''))}</td>"
        )
        html_out.append(f"<td>{data.get('count', 0)}</td>")
        html_out.append(f"<td>{html.escape(data.get('reason', ''))}</td></tr>")
    html_out.append("</table>")

    html_out.append("<h3>Tiers</h3>")
    html_out.append("<table><tr><th>Tier</th><th>Status</th><th>Reason</th></tr>")
    for tier, data in checklist.get("tiers", {}).items():
        status_color = "green" if data.get("status") == "complete" else "orange"
        html_out.append(f"<tr><td>{html.escape(tier)}</td>")
        html_out.append(
            f"<td style='color: {status_color};'>{html.escape(data.get('status', ''))}</td>"
        )
        html_out.append(f"<td>{html.escape(data.get('reason', ''))}</td></tr>")
    html_out.append("</table>")

    html_out.append("</div>")
    return "\n".join(html_out)
