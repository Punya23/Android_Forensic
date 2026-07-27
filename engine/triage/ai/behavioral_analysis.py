"""AI-Powered Behavioral Analysis Engine

Analyzes behavioral patterns from user activities:
1. Behavioral Profiling
2. Pattern Recognition
3. Deep Anomaly Detection
4. Predictive Analysis

Uses ML models (IsolationForest) for anomaly detection, with rule-based fallbacks.
"""

from __future__ import annotations

import collections
import datetime
import math
import re
import time
from typing import Any, Dict, List, Optional, Tuple

try:
    from sklearn.ensemble import IsolationForest # type: ignore
    HAS_ML = True
except ImportError:
    HAS_ML = False


# ---------------------------------------------------------------------------
# Behavioral Profiling
# ---------------------------------------------------------------------------

def build_behavioral_profile(activities: List[Dict]) -> Dict[str, Any]:
    """Build a comprehensive behavioral profile from user activities."""
    if not activities:
        return {"status": "no_data"}
        
    profile: Dict[str, Any] = {
        "version": "1.0",
        "last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "activity_level": "unknown",
        "temporal_patterns": {},
        "interest_clusters": {},
        "social_connections": {},
        "risk_indicators": [],
        "traits": {},
        "confidence_score": 0.0,
    }
    
    # 1. Temporal Patterns (Hours of activity)
    hours = []
    days = []
    for a in activities:
        ts = a.get("timestamp")
        if ts:
            try:
                # Basic ISO format handling
                dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                hours.append(dt.hour)
                days.append(dt.weekday())
            except Exception:
                pass
                
    if hours:
        h_count = collections.Counter(hours)
        peak_hour = h_count.most_common(1)[0][0]
        profile["temporal_patterns"] = {
            "peak_hour": peak_hour,
            "most_active_hours": [h for h, _ in h_count.most_common(3)],
            "night_activity": sum(1 for h in hours if 0 <= h < 5) / len(hours),
        }
        
    if days:
        d_count = collections.Counter(days)
        profile["temporal_patterns"]["peak_weekday"] = d_count.most_common(1)[0][0]
        
    # 2. Activity Level
    if len(activities) > 100:
        profile["activity_level"] = "high"
    elif len(activities) > 20:
        profile["activity_level"] = "medium"
    else:
        profile["activity_level"] = "low"
        
    # 3. Social Connections (Based on 'recipient' or 'sender' fields)
    contacts = []
    for a in activities:
        if "recipient" in a:
            contacts.append(a["recipient"])
        if "sender" in a:
            contacts.append(a["sender"])
            
    if contacts:
        c_count = collections.Counter(contacts)
        profile["social_connections"] = {
            "top_contacts": c_count.most_common(5),
            "unique_contacts": len(c_count),
        }
        
    # 4. Interest Clusters (Based on keywords in 'body' or 'url')
    keywords = []
    for a in activities:
        text = str(a.get("body", a.get("url", ""))).lower()
        words = re.findall(r"\b[a-z]{5,}\b", text)
        keywords.extend(words)
        
    if keywords:
        k_count = collections.Counter(keywords)
        stop_words = {"https", "http", "www", "com", "net", "org"}
        top_k = [k for k, _ in k_count.most_common(20) if k not in stop_words][:5]
        profile["interest_clusters"] = {"top_keywords": top_k}
        
    # 5. Risk Indicators
    risk_words = {"hack", "exploit", "leak", "darkweb", "vpn", "proxy", "crypto"}
    for k in keywords:
        if k in risk_words:
            profile["risk_indicators"].append(f"Suspicious keyword: {k}")
            
    profile["risk_indicators"] = list(set(profile["risk_indicators"]))
    
    # 6. Traits
    profile["traits"] = {
        "consistency": "high" if len(set(hours)) < 8 else "low", # If active in few specific hours
        "social_engagement": "high" if len(contacts) > 10 else "low",
        "risk_tolerance": "high" if len(profile["risk_indicators"]) > 0 else "low",
    }
    
    profile["confidence_score"] = min(len(activities) / 100.0, 1.0)
    
    return profile


# ---------------------------------------------------------------------------
# Pattern Recognition
# ---------------------------------------------------------------------------

def recognize_patterns(activities: List[Dict]) -> Dict[str, Any]:
    """Identify behavioral patterns automatically (chains, frequencies)."""
    patterns: List[Dict[str, Any]] = []
    
    # Sort by time
    sorted_act = []
    for a in activities:
        ts = a.get("timestamp")
        if ts:
            try:
                dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                sorted_act.append((dt, a))
            except Exception:
                pass
    sorted_act.sort(key=lambda x: x[0])
    
    # 1. Action Sequences
    sequences = []
    for i in range(len(sorted_act) - 1):
        dt1, a1 = sorted_act[i]
        dt2, a2 = sorted_act[i+1]
        
        # If actions happen within 5 minutes of each other
        if (dt2 - dt1).total_seconds() < 300:
            type1 = a1.get("type", "unknown")
            type2 = a2.get("type", "unknown")
            sequences.append(f"{type1} -> {type2}")
            
    if sequences:
        s_count = collections.Counter(sequences)
        for seq, count in s_count.most_common(3):
            if count > 2: # At least 3 occurrences to be a pattern
                patterns.append({
                    "pattern_type": "sequence",
                    "description": f"Frequent sequence: {seq}",
                    "occurrences": count,
                    "confidence": min(count / 10.0, 1.0)
                })
                
    # 2. Daily Frequency
    days = [dt.date() for dt, _ in sorted_act]
    if days:
        d_count = collections.Counter(days)
        avg_per_day = sum(d_count.values()) / len(d_count)
        patterns.append({
            "pattern_type": "frequency",
            "description": f"Average {avg_per_day:.1f} activities per active day",
            "occurrences": len(d_count),
            "confidence": 0.9
        })
        
    return {
        "patterns": patterns,
        "total_patterns": len(patterns)
    }


# ---------------------------------------------------------------------------
# Anomaly Detection (Deep)
# ---------------------------------------------------------------------------

def detect_anomalies_deep(activities: List[Dict], profile: Dict) -> Dict[str, Any]:
    """Deep anomaly detection using ML (IsolationForest) and rules."""
    anomalies: List[Dict[str, Any]] = []
    
    # Prepare data
    features = []
    valid_activities = []
    for a in activities:
        ts = a.get("timestamp")
        if ts:
            try:
                dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                hour = dt.hour
                length = len(str(a.get("body", a.get("url", ""))))
                features.append([hour, length])
                valid_activities.append((dt, a))
            except Exception:
                pass
                
    # ML Anomaly Detection
    if HAS_ML and len(features) > 10:
        try:
            clf = IsolationForest(contamination=0.05, random_state=42)
            preds = clf.fit_predict(features)
            scores = clf.decision_function(features)
            
            for i, pred in enumerate(preds):
                if pred == -1: # Anomaly
                    score = float(abs(scores[i]))
                    a = valid_activities[i][1]
                    anomalies.append({
                        "activity": a,
                        "severity": score,
                        "type": "statistical_outlier",
                        "reason": f"Activity features (hour={features[i][0]}, len={features[i][1]}) deviate from norm."
                    })
        except Exception:
            pass
            
    # Rule-based Anomaly Detection (Deviations from profile)
    if "temporal_patterns" in profile:
        peak_hour = profile["temporal_patterns"].get("peak_hour")
        if peak_hour is not None:
            for dt, a in valid_activities:
                # If activity is 12 hours shifted from peak hour (e.g., active at 3AM instead of 3PM)
                if abs(dt.hour - peak_hour) in (11, 12, 13):
                    # Check if already added
                    if not any(x["activity"] == a for x in anomalies):
                        anomalies.append({
                            "activity": a,
                            "severity": 0.7,
                            "type": "timing_anomaly",
                            "reason": f"Activity at {dt.hour}:00 is highly unusual compared to peak hour {peak_hour}:00."
                        })
                        
    # Sort by severity
    anomalies.sort(key=lambda x: x["severity"], reverse=True)
    
    return {
        "anomalies": anomalies,
        "count": len(anomalies),
        "model_used": "IsolationForest+Rules" if HAS_ML else "Rules"
    }


# ---------------------------------------------------------------------------
# Predictive Analysis
# ---------------------------------------------------------------------------

def predict_future_behavior(activities: List[Dict], timeframe_days: int = 7) -> Dict[str, Any]:
    """Predict future behavior patterns (activity times, likely actions)."""
    predictions: List[Dict[str, Any]] = []
    
    hours = []
    types = []
    for a in activities:
        ts = a.get("timestamp")
        if ts:
            try:
                dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                hours.append(dt.hour)
                if "type" in a:
                    types.append(a["type"])
            except Exception:
                pass
                
    if not hours:
        return {"predictions": [], "status": "insufficient_data"}
        
    # 1. Predict Next Active Hours
    h_count = collections.Counter(hours)
    top_hours = [h for h, _ in h_count.most_common(3)]
    
    predictions.append({
        "target": "activity_time",
        "prediction": f"User is highly likely to be active during hours: {top_hours}",
        "confidence": 0.85,
        "timeframe": f"Next {timeframe_days} days"
    })
    
    # 2. Predict Likely Action Type
    if types:
        t_count = collections.Counter(types)
        top_type = t_count.most_common(1)[0][0]
        prob = t_count.most_common(1)[0][1] / len(types)
        
        predictions.append({
            "target": "action_type",
            "prediction": f"Next primary action type is likely to be: {top_type}",
            "confidence": round(prob, 2),
            "timeframe": f"Next {timeframe_days} days"
        })
        
    return {
        "predictions": predictions,
        "status": "success"
    }
