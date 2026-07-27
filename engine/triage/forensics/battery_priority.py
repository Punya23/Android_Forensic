import html
from typing import Dict, List

# Define categories
CRITICAL_ARTIFACTS = ["messages", "contacts", "calls"]
HIGH_PRIORITY_ARTIFACTS = ["locations", "browser_history", "wifi"]
MEDIUM_PRIORITY_ARTIFACTS = ["media_metadata", "app_usage"]
LOW_PRIORITY_ARTIFACTS = ["media", "other"]


def get_critical_artifacts() -> List[str]:
    """Get list of critical artifacts. Always extract regardless of battery."""
    return CRITICAL_ARTIFACTS


def get_high_priority_artifacts() -> List[str]:
    """Get list of high-priority artifacts. Extract when battery > 15%."""
    return HIGH_PRIORITY_ARTIFACTS


def get_artifact_priority(artifact: Dict) -> str:
    """Get priority of artifact based on its category."""
    category = artifact.get("category", "other").lower()

    if category in CRITICAL_ARTIFACTS:
        return "critical"
    elif category in HIGH_PRIORITY_ARTIFACTS:
        return "high"
    elif category in MEDIUM_PRIORITY_ARTIFACTS:
        return "medium"
    else:
        return "low"


def prioritize_artifacts(battery_level: int, artifacts: List[Dict]) -> List[Dict]:
    """Prioritize artifacts based on battery level."""
    extract_queue = []

    for artifact in artifacts:
        priority = get_artifact_priority(artifact)

        should_extract = False
        if priority == "critical":
            should_extract = True  # Always
        elif priority == "high" and battery_level > 15:
            should_extract = True
        elif priority == "medium" and battery_level > 30:
            should_extract = True
        elif priority == "low" and battery_level > 50:
            should_extract = True

        if should_extract:
            extract_queue.append(artifact)

    return extract_queue


def should_pull_category(file_category: str, battery_level: int) -> bool:
    """Decide whether a Tier-0 file should be pulled at the given battery level.

    ``file_category`` is whatever ``pipeline._categorise()`` returns for a device
    path ("database", "image", "video", "audio", "app-export", "document",
    "other") -- a different, file-type-level taxonomy than the artifact-level
    categories above ("messages", "locations", "media", ...). This maps the two
    onto the same CRITICAL/HIGH/MEDIUM/LOW battery bands so pre-pull gating and
    the post-hoc ``generate_battery_report`` stay consistent with each other:

        database, app-export  -> CRITICAL (holds messages/contacts/calls)  -> always
        document               -> MEDIUM                                   -> >30%
        image, video, audio    -> LOW (bulk of storage, most expendable)   -> >50%
        other (unrecognised)   -> LOW                                      -> >50%
    """
    if file_category in ("database", "app-export"):
        return True
    if file_category == "document":
        return battery_level > 30
    if file_category in ("image", "video", "audio"):
        return battery_level > 50
    return battery_level > 50


def generate_battery_report(battery_level: int, all_artifacts: List[Dict]) -> str:
    """Generate HTML battery report."""
    extracted = prioritize_artifacts(battery_level, all_artifacts)
    extracted_ids = {a.get("id") or a.get("artifact_id") for a in extracted}

    skipped = [
        a
        for a in all_artifacts
        if (a.get("id") or a.get("artifact_id")) not in extracted_ids
    ]

    html_out = [
        "<div class='battery-report'>",
        "<h2>Battery-Aware Prioritization Report</h2>",
    ]
    html_out.append(f"<p><strong>Current Battery Level:</strong> {battery_level}%</p>")

    html_out.append(
        f"<p><strong>Artifacts Queued for Extraction:</strong> {len(extracted)}</p>"
    )
    html_out.append(
        f"<p><strong>Artifacts Skipped (Low Battery):</strong> {len(skipped)}</p>"
    )

    if skipped:
        html_out.append("<h3>Skipped Artifact Categories</h3><ul>")
        skipped_cats = {}
        for a in skipped:
            cat = a.get("category", "other")
            skipped_cats[cat] = skipped_cats.get(cat, 0) + 1

        for cat, count in skipped_cats.items():
            html_out.append(f"<li>{html.escape(cat)}: {count} items</li>")
        html_out.append("</ul>")

    html_out.append("</div>")
    return "\n".join(html_out)