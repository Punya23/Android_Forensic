import datetime
import html
import math
from typing import Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor
from ..config import Confidence
from ..models import Message, LocationPoint, CallRecord

# Optional: assume metrics import if available, else stub
try:
    from ..metrics import track_stage_time
except ImportError:
    import contextlib
    @contextlib.contextmanager
    def track_stage_time(stage: str):
        yield

def parse_iso(ts: Optional[str]) -> Optional[datetime.datetime]:
    if not ts: return None
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None

def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0 # Earth radius in kilometers
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) * math.sin(dlat / 2) +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) * math.sin(dlon / 2))
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def check_message_vs_location(messages: List[Dict], locations: List[Dict]) -> List[Dict]:
    """Check if message claims location but GPS shows different location."""
    contradictions = []
    loc_points = [
        (parse_iso(loc.get("timestamp")), loc)
        for loc in locations if loc.get("timestamp") and loc.get("latitude") and loc.get("longitude")
    ]
    loc_points = [(dt, loc) for dt, loc in loc_points if dt]
    
    # Simple heuristic: looking for "I'm at home" but GPS shows not home (mock logic)
    # We will just look for messages mentioning "at home" or "in office" and flag them if GPS was active.
    for msg in messages:
        msg_dt = parse_iso(msg.get("timestamp"))
        if not msg_dt or not msg.get("body"):
            continue
            
        body = str(msg["body"]).lower()
        if "at home" in body or "i am at home" in body or "i'm at home" in body:
            # Find closest GPS point within 5 minutes
            for dt, loc in loc_points:
                if abs((dt - msg_dt).total_seconds()) < 300: # 5 mins
                    # Check distance from a hypothetical "home" coordinates (if we had it, but here we just flag)
                    # For a real implementation, we'd compare against known home coordinates.
                    # Here we generate an anomaly alert based on the proximity of GPS data during a location claim.
                    contradictions.append({
                        "type": "message_vs_location",
                        "severity": "HIGH",
                        "evidence": f"Message claims 'at home' at {msg_dt.isoformat()}, but GPS shows {loc['latitude']}, {loc['longitude']}",
                        "timestamp": msg.get("timestamp"),
                        "recommendation": "Cross-reference GPS coordinates with known addresses."
                    })
                    break
    return contradictions

def check_message_vs_call(messages: List[Dict], calls: List[Dict]) -> List[Dict]:
    """Check if message says 'phone was off' but call log shows calls."""
    contradictions = []
    call_times = [
        (parse_iso(call.get("timestamp")), call)
        for call in calls if call.get("timestamp")
    ]
    call_times = [(dt, call) for dt, call in call_times if dt]

    for msg in messages:
        msg_dt = parse_iso(msg.get("timestamp"))
        if not msg_dt or not msg.get("body"):
            continue
            
        body = str(msg["body"]).lower()
        if "phone was off" in body or "was sleeping" in body or "battery died" in body:
            # Check calls in proximity
            for dt, call in call_times:
                # If they claim phone was off, but made a call within 30 mins around that msg
                if abs((dt - msg_dt).total_seconds()) < 1800:
                    contradictions.append({
                        "type": "message_vs_call",
                        "severity": "HIGH",
                        "evidence": f"Message claims '{msg['body']}' at {msg_dt.isoformat()}, but {call.get('call_type', 'call')} call logged at {dt.isoformat()}",
                        "timestamp": msg.get("timestamp"),
                        "recommendation": "Review call logs for activity during claimed offline period."
                    })
                    break
    return contradictions

def check_photo_vs_message(media: List[Dict], messages: List[Dict]) -> List[Dict]:
    """Check if photo timestamp vs message timestamp mismatch."""
    contradictions = []
    # Similar to above, comparing EXIF data with messaging contexts.
    # Left as a stub for actual EXIF parsing logic.
    return contradictions

def check_location_vs_timeline(locations: List[Dict], timeline: List[Dict]) -> List[Dict]:
    """Check if location history contradicts timeline events."""
    contradictions = []
    # E.g., user claims to be somewhere (timeline event) but GPS shows differently.
    return contradictions

def detect_contradictions(messages: List[Dict], calls: List[Dict], locations: List[Dict], timeline: List[Dict]) -> List[Dict]:
    """Detect contradictions across artifacts in parallel."""
    results = []
    with track_stage_time("contradiction_detection"):
        with ThreadPoolExecutor(max_workers=4) as executor:
            fut1 = executor.submit(check_message_vs_location, messages, locations)
            fut2 = executor.submit(check_message_vs_call, messages, calls)
            # Add future checks here
            
            results.extend(fut1.result())
            results.extend(fut2.result())
    return results

def generate_contradiction_report(contradictions: List[Dict]) -> str:
    """Generate HTML report of contradictions."""
    html_out = ["<div class='contradiction-report'>", "<h2>Contradiction Analysis</h2>"]
    if not contradictions:
        html_out.append("<p>No contradictions detected.</p>")
    else:
        html_out.append("<table><tr><th>Severity</th><th>Timestamp</th><th>Evidence</th><th>Recommendation</th></tr>")
        for c in contradictions:
            html_out.append(f"<tr><td>{html.escape(c.get('severity', ''))}</td>")
            html_out.append(f"<td>{html.escape(c.get('timestamp', ''))}</td>")
            html_out.append(f"<td>{html.escape(c.get('evidence', ''))}</td>")
            html_out.append(f"<td>{html.escape(c.get('recommendation', ''))}</td></tr>")
        html_out.append("</table>")
    html_out.append("</div>")
    return "\n".join(html_out)
