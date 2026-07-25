import html
import datetime
from typing import Dict, List
from ..config import Confidence


def calculate_confidence_score(confidence: Confidence) -> int:
    """Calculate confidence score."""
    if not isinstance(confidence, Confidence):
        try:
            confidence = Confidence(confidence)
        except Exception:
            return 0

    scores = {
        Confidence.LIVE: 10,
        Confidence.RECOVERED_VERIFIED: 7,
        Confidence.CARVED_PARTIAL: 3,
        Confidence.DELETION_DETECTED: 1,
    }
    return scores.get(confidence, 0)


def calculate_source_score(sources: int) -> int:
    """Calculate source diversity score (max 20)."""
    # E.g. found in SMS and WhatsApp and Call logs
    return min(20, sources * 5)


def calculate_timestamp_score(timestamp: str) -> int:
    """Calculate timestamp score (max 30). More recent = higher score."""
    if not timestamp:
        return 0

    try:
        dt = datetime.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        now = datetime.datetime.now(datetime.timezone.utc)
        delta_days = (now - dt).days

        if delta_days <= 7:
            return 30
        if delta_days <= 30:
            return 20
        if delta_days <= 180:
            return 10
        if delta_days <= 365:
            return 5
        return 2
    except Exception:
        return 0


def calculate_correlation_score(evidence: Dict) -> int:
    """Calculate correlation score (max 40)."""
    # Assuming evidence dictionary tracks its correlations
    correlations = len(evidence.get("correlations", []))
    return min(40, correlations * 10)


def score_evidence(evidence: Dict) -> int:
    """Score evidence deterministically. Returns a score between 0 and 100."""
    conf = evidence.get("confidence", Confidence.LIVE)
    c_score = calculate_confidence_score(conf)

    # Base weight 10 + Source 20 + Time 30 + Correlation 40 = 100

    s_score = calculate_source_score(evidence.get("source_count", 1))
    t_score = calculate_timestamp_score(evidence.get("timestamp"))
    cor_score = calculate_correlation_score(evidence)

    # Scale confidence to act as a multiplier or base score.
    # The requirement specifically maps: LIVE: 10, RECOVERED: 7, CARVED: 3, DELETION: 1.
    # We will use it as the base 10 points.

    total = c_score + s_score + t_score + cor_score
    return min(100, max(0, total))


def generate_scoring_report(evidence_list: List[Dict]) -> str:
    """Generate HTML scoring report."""
    html_out = ["<div class='scoring-report'>", "<h2>Evidence Scoring Report</h2>"]

    if not evidence_list:
        html_out.append("<p>No evidence items provided for scoring.</p>")
    else:
        html_out.append(
            "<table><tr><th>Item Details</th><th>Confidence</th><th>Source(s)</th><th>Timestamp</th><th>Correlations</th><th>Total Score</th></tr>"
        )

        # Sort by score descending
        scored_items = []
        for item in evidence_list:
            score = score_evidence(item)
            scored_items.append((score, item))

        scored_items.sort(key=lambda x: x[0], reverse=True)

        for score, item in scored_items:
            summary = html.escape(
                str(
                    item.get(
                        "body", item.get("name", item.get("label", "Unknown Item"))
                    )
                )[:50]
            )
            conf_str = str(item.get("confidence", "live")).upper()
            src_count = item.get("source_count", 1)
            ts = item.get("timestamp", "N/A")
            corr_count = len(item.get("correlations", []))

            # Sub-scores
            c_score = calculate_confidence_score(
                item.get("confidence", Confidence.LIVE)
            )
            s_score = calculate_source_score(src_count)
            t_score = calculate_timestamp_score(ts)
            cor_score = calculate_correlation_score(item)

            html_out.append(f"<tr><td>{summary}</td>")
            html_out.append(f"<td>{conf_str} ({c_score})</td>")
            html_out.append(f"<td>{src_count} ({s_score})</td>")
            html_out.append(f"<td>{ts} ({t_score})</td>")
            html_out.append(f"<td>{corr_count} ({cor_score})</td>")
            html_out.append(f"<td><strong>{score} / 100</strong></td></tr>")

        html_out.append("</table>")

    html_out.append("</div>")
    return "\n".join(html_out)
