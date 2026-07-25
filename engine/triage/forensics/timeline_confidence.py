"""Evidence Timeline with Confidence Markers."""

from typing import List, Dict
import html


def generate_confidence_timeline(timeline_events: List[Dict]) -> str:
    """Generate HTML timeline incorporating confidence badges."""
    html_out = ["<div class='timeline'>", "<h2>Confidence Timeline</h2><ul>"]

    for event in sorted(timeline_events, key=lambda x: x.get("timestamp", "")):
        conf = event.get("confidence", "live").upper()

        # Color code based on confidence
        color = "green"
        if conf == "RECOVERED_VERIFIED":
            color = "blue"
        elif conf == "CARVED_PARTIAL":
            color = "orange"
        elif conf == "DELETION_DETECTED":
            color = "red"

        html_out.append(f"<li><span style='color: {color}'>[{conf}]</span> ")
        html_out.append(f"<strong>{html.escape(event.get('timestamp', ''))}</strong>: ")
        html_out.append(f"{html.escape(event.get('summary', ''))}</li>")

    html_out.append("</ul></div>")
    return "\n".join(html_out)
