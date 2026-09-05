"""Behavioral Pattern Detection for forensic analysis.

Detects anomalies and patterns in user behavior including timing anomalies,
frequency bursts, contact switching, sudden silence, and night activity.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from ..models import CallRecord, Message


class BehavioralAnomalyDetector:
    """Detects behavioral patterns and anomalies in user communication."""
    
    # Thresholds
    NIGHT_HOURS = [1, 2, 3, 4, 5]  # 1-5 AM
    BURST_MULTIPLIER = 3.0  # 3x normal activity
    BURST_WINDOW_MINUTES = 10
    SILENCE_THRESHOLD_DAYS = 3
    NEW_CONTACT_WINDOW_DAYS = 7
    NEW_CONTACT_THRESHOLD = 5
    
    def __init__(self):
        """Initialize detector."""
        pass
    
    def detect_patterns(
        self, messages: List[Message], calls: List[CallRecord]
    ) -> List[dict]:
        """Detect all behavioral patterns and anomalies.
        
        Args:
            messages: List of Message objects
            calls: List of CallRecord objects
            
        Returns:
            List of pattern/anomaly dicts with type, severity, description, evidence
        """
        patterns = []
        
        # Analyze messages
        if messages:
            patterns.extend(self.analyze_timing_patterns(messages)["anomalies"])
            patterns.extend(self.detect_burst_activity(messages))
            patterns.extend(self.identify_contact_switches(messages))
            patterns.extend(self._detect_sudden_silence(messages))
            patterns.extend(self._detect_new_contact_surge(messages))
        
        # Analyze calls
        if calls:
            patterns.extend(self._analyze_call_patterns(calls))
        
        # Sort by score
        patterns.sort(key=lambda x: x.get("score", 0), reverse=True)
        
        return patterns
    
    def analyze_timing_patterns(self, messages: List[Message]) -> dict:
        """Analyze timing patterns and detect anomalies.
        
        Returns:
            dict with hourly distribution, anomalies, stats
        """
        hourly_counts = defaultdict(int)
        night_messages = []
        
        for msg in messages:
            if not msg.timestamp:
                continue
            
            try:
                dt = self._parse_timestamp(msg.timestamp)
                hour = dt.hour
                hourly_counts[hour] += 1
                
                # Track night messages
                if hour in self.NIGHT_HOURS:
                    night_messages.append(msg)
            except Exception:
                continue
        
        # Calculate statistics
        if not hourly_counts:
            return {"hourly": {}, "anomalies": [], "stats": {}}
        
        counts = list(hourly_counts.values())
        avg = statistics.mean(counts)
        std = statistics.stdev(counts) if len(counts) > 1 else 0
        
        # Detect anomalies
        anomalies = []
        
        # Night activity anomaly
        night_count = len(night_messages)
        if night_count > 0:
            night_avg = sum(hourly_counts[h] for h in self.NIGHT_HOURS) / len(self.NIGHT_HOURS)
            
            if night_avg > avg + 2 * std or night_count > 20:
                # Group night messages by participants
                participants = set()
                sample_texts = []
                
                for msg in night_messages[:5]:
                    if msg.sender:
                        participants.add(msg.sender)
                    if msg.text:
                        sample_texts.append(msg.text[:100])
                
                anomalies.append({
                    "type": "timing_anomaly",
                    "subtype": "night_activity",
                    "severity": "HIGH" if night_count > 30 else "MEDIUM",
                    "description": f"{night_count} messages sent between 1-5 AM",
                    "evidence": {
                        "time_range": ["01:00", "05:00"],
                        "message_count": night_count,
                        "participants": list(participants),
                        "sample": sample_texts[0] if sample_texts else "",
                    },
                    "score": min(50 + night_count, 100),
                    "requires_verification": True,
                })
        
        return {
            "hourly": dict(hourly_counts),
            "anomalies": anomalies,
            "stats": {
                "avg_per_hour": avg,
                "std_per_hour": std,
                "night_messages": night_count,
            },
        }
    
    def detect_burst_activity(self, messages: List[Message]) -> List[dict]:
        """Detect sudden spikes in activity (>3x normal).
        
        Returns:
            List of burst activity anomalies
        """
        if not messages:
            return []
        
        # Sort by timestamp
        sorted_msgs = sorted(
            [m for m in messages if m.timestamp],
            key=lambda m: m.timestamp
        )
        
        if len(sorted_msgs) < 10:
            return []
        
        bursts = []
        
        # Slide window to detect bursts
        window = timedelta(minutes=self.BURST_WINDOW_MINUTES)
        
        for i, msg in enumerate(sorted_msgs):
            try:
                start_time = self._parse_timestamp(msg.timestamp)
                end_time = start_time + window
                
                # Count messages in window
                window_msgs = []
                for other in sorted_msgs[i:]:
                    other_time = self._parse_timestamp(other.timestamp)
                    if other_time <= end_time:
                        window_msgs.append(other)
                    else:
                        break
                
                # Check if burst
                if len(window_msgs) >= 15:  # 15+ messages in 10 minutes
                    # Extract participants and content
                    participants = set()
                    sample_texts = []
                    
                    for m in window_msgs[:5]:
                        if m.sender:
                            participants.add(m.sender)
                        if m.text:
                            sample_texts.append(m.text[:80])
                    
                    bursts.append({
                        "type": "frequency_burst",
                        "severity": "HIGH" if len(window_msgs) > 30 else "MEDIUM",
                        "description": f"{len(window_msgs)} messages in {self.BURST_WINDOW_MINUTES} minutes",
                        "evidence": {
                            "time_start": msg.timestamp,
                            "time_end": sorted_msgs[i + len(window_msgs) - 1].timestamp,
                            "message_count": len(window_msgs),
                            "participants": list(participants),
                            "sample": sample_texts[0] if sample_texts else "",
                        },
                        "score": min(60 + len(window_msgs), 100),
                        "requires_verification": True,
                    })
                    
                    # Skip ahead to avoid duplicate detections
                    i += len(window_msgs)
            
            except Exception:
                continue
        
        # Deduplicate overlapping bursts
        return self._deduplicate_bursts(bursts)
    
    def identify_contact_switches(self, messages: List[Message]) -> List[dict]:
        """Identify rapid switching between contacts.
        
        Returns:
            List of contact switching patterns
        """
        if not messages:
            return []
        
        # Sort by timestamp
        sorted_msgs = sorted(
            [m for m in messages if m.timestamp and m.recipient],
            key=lambda m: m.timestamp
        )
        
        if len(sorted_msgs) < 5:
            return []
        
        switches = []
        switch_count = 0
        last_contact = None
        switch_window = []
        
        for i, msg in enumerate(sorted_msgs):
            current_contact = msg.recipient
            
            if last_contact and current_contact != last_contact:
                switch_count += 1
                switch_window.append({
                    "time": msg.timestamp,
                    "from": last_contact,
                    "to": current_contact,
                })
                
                # Check if rapid switching (5+ switches in 30 minutes)
                if len(switch_window) >= 5:
                    try:
                        first_time = self._parse_timestamp(switch_window[0]["time"])
                        last_time = self._parse_timestamp(switch_window[-1]["time"])
                        duration = (last_time - first_time).total_seconds() / 60
                        
                        if duration <= 30:
                            # Rapid switching detected
                            contacts = list(set(sw["to"] for sw in switch_window))
                            
                            switches.append({
                                "type": "contact_switching",
                                "severity": "MEDIUM",
                                "description": f"Rapidly switched between {len(contacts)} contacts",
                                "evidence": {
                                    "switch_count": len(switch_window),
                                    "duration_minutes": int(duration),
                                    "contacts": contacts[:5],
                                    "timeline": switch_window[:10],
                                },
                                "score": min(50 + len(switch_window) * 5, 90),
                                "requires_verification": False,
                            })
                            
                            switch_window = []
                    except Exception:
                        pass
            
            last_contact = current_contact
        
        return switches
    
    def _detect_sudden_silence(self, messages: List[Message]) -> List[dict]:
        """Detect unusual drops in activity."""
        if len(messages) < 20:
            return []
        
        # Sort by timestamp
        sorted_msgs = sorted(
            [m for m in messages if m.timestamp],
            key=lambda m: m.timestamp
        )
        
        silences = []
        
        # Look for gaps > 3 days after regular activity
        for i in range(len(sorted_msgs) - 1):
            try:
                current_time = self._parse_timestamp(sorted_msgs[i].timestamp)
                next_time = self._parse_timestamp(sorted_msgs[i + 1].timestamp)
                
                gap_days = (next_time - current_time).days
                
                if gap_days >= self.SILENCE_THRESHOLD_DAYS:
                    # Check activity before silence
                    recent_before = [
                        m for m in sorted_msgs[max(0, i-20):i]
                        if (current_time - self._parse_timestamp(m.timestamp)).days <= 7
                    ]
                    
                    # Check activity after silence
                    recent_after = [
                        m for m in sorted_msgs[i+1:min(len(sorted_msgs), i+21)]
                        if (self._parse_timestamp(m.timestamp) - next_time).days <= 7
                    ]
                    
                    if len(recent_before) >= 5 and len(recent_after) >= 5:
                        silences.append({
                            "type": "sudden_silence",
                            "severity": "MEDIUM" if gap_days < 7 else "HIGH",
                            "description": f"Unusual {gap_days}-day silence in communication",
                            "evidence": {
                                "silence_start": sorted_msgs[i].timestamp,
                                "silence_end": sorted_msgs[i + 1].timestamp,
                                "duration_days": gap_days,
                                "activity_before": len(recent_before),
                                "activity_after": len(recent_after),
                            },
                            "score": min(40 + gap_days * 5, 85),
                            "requires_verification": True,
                        })
            
            except Exception:
                continue
        
        return silences[:3]  # Top 3 silences
    
    def _detect_new_contact_surge(self, messages: List[Message]) -> List[dict]:
        """Detect many new contacts in short period."""
        if len(messages) < 10:
            return []
        
        # Group by contact and time
        contact_first_seen: Dict[str, datetime] = {}
        
        for msg in messages:
            if not msg.recipient or not msg.timestamp:
                continue
            
            try:
                msg_time = self._parse_timestamp(msg.timestamp)
                
                if msg.recipient not in contact_first_seen:
                    contact_first_seen[msg.recipient] = msg_time
            except Exception:
                continue
        
        # Check for surges
        surges = []
        sorted_contacts = sorted(contact_first_seen.items(), key=lambda x: x[1])
        
        for i in range(len(sorted_contacts)):
            window_start = sorted_contacts[i][1]
            window_end = window_start + timedelta(days=self.NEW_CONTACT_WINDOW_DAYS)
            
            new_contacts_in_window = [
                contact for contact, first_seen in sorted_contacts[i:]
                if first_seen <= window_end
            ]
            
            if len(new_contacts_in_window) >= self.NEW_CONTACT_THRESHOLD:
                surges.append({
                    "type": "new_contact_surge",
                    "severity": "MEDIUM",
                    "description": f"{len(new_contacts_in_window)} new contacts in {self.NEW_CONTACT_WINDOW_DAYS} days",
                    "evidence": {
                        "window_start": window_start.isoformat(),
                        "window_end": window_end.isoformat(),
                        "new_contact_count": len(new_contacts_in_window),
                        "contacts": new_contacts_in_window[:10],
                    },
                    "score": min(45 + len(new_contacts_in_window) * 3, 80),
                    "requires_verification": False,
                })
                break  # Only report first surge
        
        return surges
    
    def _analyze_call_patterns(self, calls: List[CallRecord]) -> List[dict]:
        """Analyze call patterns for anomalies."""
        patterns = []
        
        if not calls:
            return patterns
        
        # Night calls
        night_calls = []
        for call in calls:
            if call.timestamp:
                try:
                    dt = self._parse_timestamp(call.timestamp)
                    if dt.hour in self.NIGHT_HOURS:
                        night_calls.append(call)
                except Exception:
                    continue
        
        if len(night_calls) > 5:
            patterns.append({
                "type": "timing_anomaly",
                "subtype": "night_calls",
                "severity": "MEDIUM",
                "description": f"{len(night_calls)} calls between 1-5 AM",
                "evidence": {
                    "call_count": len(night_calls),
                    "time_range": ["01:00", "05:00"],
                },
                "score": min(50 + len(night_calls) * 2, 85),
                "requires_verification": True,
            })
        
        return patterns
    
    def _parse_timestamp(self, ts: str) -> datetime:
        """Parse ISO-8601 timestamp."""
        # Handle various formats
        ts_clean = ts.replace("Z", "+00:00")
        
        try:
            return datetime.fromisoformat(ts_clean)
        except Exception:
            # Try parsing without timezone
            return datetime.fromisoformat(ts_clean.split("+")[0].split("-")[0])
    
    def _deduplicate_bursts(self, bursts: List[dict]) -> List[dict]:
        """Remove overlapping burst detections."""
        if not bursts:
            return []
        
        # Sort by time
        sorted_bursts = sorted(bursts, key=lambda x: x["evidence"]["time_start"])
        
        deduplicated = [sorted_bursts[0]]
        
        for burst in sorted_bursts[1:]:
            last_burst = deduplicated[-1]
            
            # Check for overlap
            try:
                last_end = self._parse_timestamp(last_burst["evidence"]["time_end"])
                current_start = self._parse_timestamp(burst["evidence"]["time_start"])
                
                # If no overlap, add
                if current_start > last_end:
                    deduplicated.append(burst)
            except Exception:
                deduplicated.append(burst)
        
        return deduplicated
