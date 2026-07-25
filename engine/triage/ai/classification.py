import re
import html
from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor


try:
    from ..metrics import track_stage_time
except ImportError:
    import contextlib

    @contextlib.contextmanager
    def track_stage_time(stage: str):
        yield


# Simple heuristic patterns if heavy ML models aren't loaded
CATEGORIES = {
    "threat": re.compile(
        r"\b(kill|attack|bomb|weapon|shoot|destroy|beat)\b", re.IGNORECASE
    ),
    "fraud": re.compile(
        r"\b(otp|upi|transfer|account|scam|urgent|money|kyc)\b", re.IGNORECASE
    ),
    "harassment": re.compile(r"\b(bitch|whore|slut|abuse|harass)\b", re.IGNORECASE),
    "financial": re.compile(
        r"\b(bank|rupees|rs|inr|salary|invest|tax)\b", re.IGNORECASE
    ),
    "location": re.compile(
        r"\b(meet|address|street|road|city|gps|here)\b", re.IGNORECASE
    ),
    "media": re.compile(
        r"\b(photo|video|image|picture|selfie|recording)\b", re.IGNORECASE
    ),
}


def classify_evidence(text: str) -> Dict[str, Any]:
    """Classify evidence text into categories."""
    if not text:
        return {"category": "unknown", "confidence": 0.0, "sub_category": None}

    text_str = str(text)

    # Ideally, this would use a proper ML classifier (e.g., zero-shot or trained model).
    # Using regex heuristics as a robust, fast fallback.
    best_category = "unknown"
    highest_matches = 0

    for category, pattern in CATEGORIES.items():
        matches = len(pattern.findall(text_str))
        if matches > highest_matches:
            highest_matches = matches
            best_category = category

    confidence = min(1.0, highest_matches * 0.3) if highest_matches > 0 else 0.0

    return {
        "category": best_category if highest_matches > 0 else "communication",
        "confidence": confidence,
        "sub_category": None,  # Could be expanded
    }


def classify_evidence_batch(texts: List[str]) -> List[Dict[str, Any]]:
    """Batch classification for performance."""
    results = []
    with track_stage_time("ai_classification"):
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(classify_evidence, texts))
    return results


def get_evidence_importance_score(evidence: Dict) -> int:
    """Calculate importance score (0-100)."""
    score = 0

    # Category weight (max 40)
    cat = evidence.get("ai_category", "unknown")
    if cat in ["threat", "fraud"]:
        score += 40
    elif cat in ["harassment", "financial"]:
        score += 30
    elif cat in ["location", "media"]:
        score += 20
    else:
        score += 10

    # Confidence weight (max 30)
    conf_str = str(evidence.get("confidence", "live")).lower()
    if conf_str == "live":
        score += 30
    elif conf_str == "recovered":
        score += 20
    elif conf_str == "carved":
        score += 10

    # Source weight (max 20)
    source = str(evidence.get("source_file", "")).lower()
    if "whatsapp" in source or "telegram" in source:
        score += 20
    elif "sms" in source or "mms" in source:
        score += 15
    else:
        score += 10

    # Timestamp weight (max 10)
    if evidence.get("timestamp"):
        score += 10  # Simplifying age calculation for this heuristic

    return min(100, score)


def prioritize_evidence(evidence_list: List[Dict]) -> List[Dict]:
    """Rank evidence by importance."""
    prioritized = []
    for ev in evidence_list:
        new_ev = dict(ev)

        # Ensure we have classification
        if "ai_category" not in new_ev:
            text = str(new_ev.get("body", new_ev.get("name", "")))
            cls_res = classify_evidence(text)
            new_ev["ai_category"] = cls_res["category"]

        score = get_evidence_importance_score(new_ev)
        new_ev["importance_score"] = score

        if score >= 80:
            new_ev["priority_tier"] = "critical"
        elif score >= 60:
            new_ev["priority_tier"] = "high"
        elif score >= 40:
            new_ev["priority_tier"] = "medium"
        elif score >= 20:
            new_ev["priority_tier"] = "low"
        else:
            new_ev["priority_tier"] = "info"

        prioritized.append(new_ev)

    prioritized.sort(key=lambda x: x["importance_score"], reverse=True)
    return prioritized


def generate_classification_report(classified: List[Dict]) -> str:
    """Generate HTML classification report."""
    html_out = [
        "<div class='ai-classification-report'>",
        "<h2>AI Evidence Classification</h2>",
    ]

    if not classified:
        html_out.append("<p>No data classified.</p></div>")
        return "\n".join(html_out)

    counts = {}
    for ev in classified:
        cat = ev.get("ai_category", "unknown")
        counts[cat] = counts.get(cat, 0) + 1

    html_out.append("<h3>Category Breakdown</h3><ul>")
    for cat, count in sorted(counts.items(), key=lambda x: x[1], reverse=True):
        html_out.append(f"<li><strong>{cat.title()}</strong>: {count} items</li>")
    html_out.append("</ul>")

    html_out.append("<h3>High Priority Samples</h3>")
    html_out.append(
        "<table><tr><th>Tier</th><th>Category</th><th>Score</th><th>Snippet</th></tr>"
    )

    # Show top 10
    for ev in classified[:10]:
        tier = ev.get("priority_tier", "info").upper()
        cat = ev.get("ai_category", "unknown").title()
        score = ev.get("importance_score", 0)
        snippet = html.escape(str(ev.get("body", ev.get("name", "")))[:60]) + "..."

        color = "black"
        if tier == "CRITICAL":
            color = "red"
        elif tier == "HIGH":
            color = "orange"

        html_out.append(f"<tr><td style='color:{color}'><strong>{tier}</strong></td>")
        html_out.append(f"<td>{cat}</td><td>{score}</td><td>{snippet}</td></tr>")

    html_out.append("</table></div>")
    return "\n".join(html_out)
