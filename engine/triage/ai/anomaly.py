import html
import logging
from typing import List, Dict, Any
from datetime import datetime, timezone

try:
    import numpy as np
    from sklearn.ensemble import IsolationForest
    HAS_ML = True
except ImportError:
    HAS_ML = False
    logging.warning("scikit-learn or numpy not installed. Falling back to basic anomaly detection.")

def get_anomaly_score(evidence: Dict) -> float:
    """Calculate anomaly score (0-1)."""
    score = evidence.get("ai_anomaly_score", 0.0)
    return float(score)

def find_unusual_patterns(evidence_list: List[Dict]) -> List[Dict]:
    """Find unusual patterns (Time-based, frequency-based)."""
    # Simple fallback: look for messages at odd hours (1 AM - 4 AM)
    patterns = []
    for ev in evidence_list:
        ts = ev.get("timestamp")
        if not ts: continue
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if 1 <= dt.hour <= 4:
                p_ev = dict(ev)
                p_ev["ai_anomaly_score"] = 0.8
                p_ev["anomaly_reason"] = "Activity during odd hours (1AM-4AM)"
                p_ev["anomaly_type"] = "timing"
                patterns.append(p_ev)
        except Exception:
            pass
    return patterns

def classify_anomaly(evidence: Dict) -> Dict[str, Any]:
    """Classify anomaly type."""
    a_type = evidence.get("anomaly_type", "unknown")
    reason = evidence.get("anomaly_reason", "Statistical outlier")
    return {"type": a_type, "reason": reason}

def detect_anomalies(evidence_list: List[Dict]) -> List[Dict]:
    """Detect anomalous evidence using machine learning."""
    if not evidence_list:
        return []
        
    if not HAS_ML or len(evidence_list) < 10:
        return find_unusual_patterns(evidence_list)
        
    # Prepare features for Isolation Forest
    # Feature 1: length of content
    # Feature 2: Hour of day (if available, else 12)
    features = []
    for ev in evidence_list:
        length = len(str(ev.get("body", ev.get("name", ""))))
        hour = 12
        ts = ev.get("timestamp")
        if ts:
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                hour = dt.hour
            except Exception:
                pass
        features.append([length, hour])
        
    clf = IsolationForest(random_state=42, contamination=0.05)
    preds = clf.fit_predict(features)
    # Isolation forest gives -1 for anomalies, 1 for normal
    
    anomalies = []
    for idx, pred in enumerate(preds):
        if pred == -1:
            a_ev = dict(evidence_list[idx])
            a_ev["ai_anomaly_score"] = 0.9
            a_ev["anomaly_reason"] = "Statistical outlier in feature space"
            a_ev["anomaly_type"] = "unknown"
            anomalies.append(a_ev)
            
    # Combine with hardcoded patterns
    hardcoded = find_unusual_patterns(evidence_list)
    # Deduplicate based on simple body match
    seen_bodies = {str(a.get("body", a.get("name", ""))) for a in anomalies}
    
    for hc in hardcoded:
        b = str(hc.get("body", hc.get("name", "")))
        if b not in seen_bodies:
            anomalies.append(hc)
            
    return anomalies

def generate_anomaly_report(anomalies: List[Dict]) -> str:
    """Generate HTML anomaly report."""
    html_out = ["<div class='ai-anomaly-report'>", "<h2>Anomaly Detection</h2>"]
    
    if not anomalies:
        html_out.append("<p>No significant anomalies detected.</p></div>")
        return "\n".join(html_out)
        
    html_out.append("<table><tr><th>Anomaly Type</th><th>Score</th><th>Reason</th><th>Snippet</th></tr>")
    
    for a in anomalies:
        cls_info = classify_anomaly(a)
        score = get_anomaly_score(a)
        snippet = html.escape(str(a.get("body", a.get("name", "")))[:60]) + "..."
        
        html_out.append(f"<tr><td><strong>{html.escape(cls_info['type'].upper())}</strong></td>")
        html_out.append(f"<td>{score:.2f}</td>")
        html_out.append(f"<td>{html.escape(cls_info['reason'])}</td>")
        html_out.append(f"<td>{snippet}</td></tr>")
        
    html_out.append("</table></div>")
    return "\n".join(html_out)
