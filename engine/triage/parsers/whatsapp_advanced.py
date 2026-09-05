"""Advanced WhatsApp analysis features.

Provides deeper forensic analysis of WhatsApp data including:
- Reaction analysis from message_reactions table
- Admin detection from group_participants table
- Call pattern analysis with suspicious behavior detection
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def analyze_whatsapp_reactions(db_path: str) -> Dict[str, Dict[str, Any]]:
    """Analyze emoji reactions from WhatsApp message_reactions table.
    
    Args:
        db_path: Path to WhatsApp msgstore.db
        
    Returns:
        Dict mapping message_id to reaction data:
        {
            message_id: {
                'reactions': {emoji: count},
                'users': {emoji: [jid_list]},
                'total_reactions': int
            }
        }
    """
    if not Path(db_path).exists():
        return {}
    
    reactions_data = {}
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Try to query message_reactions table
        # Schema varies by version, try multiple approaches
        queries = [
            # Modern schema
            """SELECT message_row_id, reaction_text, sender_jid 
               FROM message_reactions WHERE reaction_text IS NOT NULL""",
            # Alternative schema
            """SELECT message_id, reaction, sender 
               FROM reactions WHERE reaction IS NOT NULL""",
        ]
        
        for query in queries:
            try:
                cursor.execute(query)
                rows = cursor.fetchall()
                
                for row in rows:
                    msg_id = str(row[0])
                    emoji = row[1]
                    sender = row[2] if len(row) > 2 else "unknown"
                    
                    if msg_id not in reactions_data:
                        reactions_data[msg_id] = {
                            'reactions': defaultdict(int),
                            'users': defaultdict(list),
                            'total_reactions': 0
                        }
                    
                    reactions_data[msg_id]['reactions'][emoji] += 1
                    reactions_data[msg_id]['users'][emoji].append(sender)
                    reactions_data[msg_id]['total_reactions'] += 1
                
                break  # Success, don't try other queries
                
            except sqlite3.Error:
                continue  # Try next query
        
        conn.close()
        
        # Convert defaultdicts to regular dicts for JSON serialization
        for msg_id in reactions_data:
            reactions_data[msg_id]['reactions'] = dict(reactions_data[msg_id]['reactions'])
            reactions_data[msg_id]['users'] = dict(reactions_data[msg_id]['users'])
        
    except Exception as e:
        # Log error but return partial data
        pass
    
    return reactions_data


def detect_whatsapp_admins(db_path: str) -> Dict[str, List[str]]:
    """Detect group admins from WhatsApp group_participants table.
    
    Args:
        db_path: Path to WhatsApp msgstore.db
        
    Returns:
        Dict mapping group_jid to list of admin JIDs:
        {
            'group_jid@g.us': ['admin1@s.whatsapp.net', 'admin2@s.whatsapp.net']
        }
    """
    if not Path(db_path).exists():
        return {}
    
    admins_data = defaultdict(list)
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Try multiple schema variations
        queries = [
            # Modern schema
            """SELECT gjid, jid, is_admin 
               FROM group_participants WHERE is_admin = 1""",
            # Alternative schema
            """SELECT group_jid, user_jid, admin 
               FROM group_participants WHERE admin = 1""",
            # Fallback: Check for admin column existence
            """SELECT gjid, jid FROM group_participants 
               WHERE admin = 1 OR is_admin = 1 OR is_super_admin = 1""",
        ]
        
        for query in queries:
            try:
                cursor.execute(query)
                rows = cursor.fetchall()
                
                for row in rows:
                    group_jid = row[0]
                    user_jid = row[1]
                    
                    if group_jid and user_jid:
                        admins_data[group_jid].append(user_jid)
                
                break  # Success
                
            except sqlite3.Error:
                continue
        
        conn.close()
        
    except Exception:
        pass
    
    return dict(admins_data)


def analyze_whatsapp_calls(call_logs: List[Dict]) -> Dict[str, Any]:
    """Analyze WhatsApp call patterns for forensic insights.
    
    Args:
        call_logs: List of call records with fields:
            - jid: Contact JID
            - timestamp: Call timestamp
            - duration: Call duration in seconds
            - call_result: 'answered', 'missed', 'rejected', etc.
            
    Returns:
        Dict with per-contact statistics and suspicious patterns:
        {
            'per_contact': {
                jid: {
                    'total_calls': int,
                    'total_duration': int,
                    'missed_calls': int,
                    'answered_calls': int,
                    'rejected_calls': int,
                    'average_duration': float,
                    'night_calls': int,  # 1-5 AM
                    'suspicious_patterns': []
                }
            },
            'suspicious_patterns': [
                {'type': 'high_frequency', 'jid': str, 'details': str},
                {'type': 'odd_hours', 'jid': str, 'details': str}
            ]
        }
    """
    per_contact = defaultdict(lambda: {
        'total_calls': 0,
        'total_duration': 0,
        'missed_calls': 0,
        'answered_calls': 0,
        'rejected_calls': 0,
        'durations': [],
        'night_calls': 0,
        'timestamps': []
    })
    
    # Analyze each call
    for call in call_logs:
        jid = call.get('jid', 'unknown')
        duration = call.get('duration', 0)
        result = call.get('call_result', '').lower()
        timestamp = call.get('timestamp')
        
        per_contact[jid]['total_calls'] += 1
        per_contact[jid]['total_duration'] += duration
        per_contact[jid]['durations'].append(duration)
        
        # Count by result
        if 'miss' in result:
            per_contact[jid]['missed_calls'] += 1
        elif 'answer' in result or 'accept' in result:
            per_contact[jid]['answered_calls'] += 1
        elif 'reject' in result or 'decline' in result:
            per_contact[jid]['rejected_calls'] += 1
        
        # Check for night calls (1-5 AM)
        if timestamp:
            try:
                dt = _parse_timestamp(timestamp)
                per_contact[jid]['timestamps'].append(dt)
                
                if dt.hour in [1, 2, 3, 4, 5]:
                    per_contact[jid]['night_calls'] += 1
            except Exception:
                pass
    
    # Calculate statistics and detect patterns
    suspicious_patterns = []
    
    for jid, stats in per_contact.items():
        # Average duration
        if stats['durations']:
            stats['average_duration'] = sum(stats['durations']) / len(stats['durations'])
        else:
            stats['average_duration'] = 0
        
        # Remove temporary list (not JSON serializable)
        del stats['durations']
        
        # Detect suspicious patterns
        
        # 1. High frequency (>20 calls)
        if stats['total_calls'] > 20:
            suspicious_patterns.append({
                'type': 'high_frequency',
                'jid': jid,
                'details': f"{stats['total_calls']} calls detected",
                'severity': 'MEDIUM'
            })
        
        # 2. Odd hours (>5 night calls)
        if stats['night_calls'] > 5:
            suspicious_patterns.append({
                'type': 'odd_hours',
                'jid': jid,
                'details': f"{stats['night_calls']} calls between 1-5 AM",
                'severity': 'HIGH'
            })
        
        # 3. High missed call ratio (>70%)
        if stats['total_calls'] > 5:
            missed_ratio = stats['missed_calls'] / stats['total_calls']
            if missed_ratio > 0.7:
                suspicious_patterns.append({
                    'type': 'high_missed_ratio',
                    'jid': jid,
                    'details': f"{int(missed_ratio * 100)}% calls missed",
                    'severity': 'MEDIUM'
                })
        
        # 4. Frequency spike detection
        if len(stats['timestamps']) > 10:
            # Check for bursts (5+ calls in 10 minutes)
            sorted_times = sorted(stats['timestamps'])
            for i in range(len(sorted_times) - 4):
                window = sorted_times[i:i+5]
                if (window[-1] - window[0]).total_seconds() < 600:  # 10 minutes
                    suspicious_patterns.append({
                        'type': 'frequency_spike',
                        'jid': jid,
                        'details': '5+ calls in 10 minutes',
                        'severity': 'HIGH'
                    })
                    break
        
        # Remove timestamps list (not serializable)
        del stats['timestamps']
    
    return {
        'per_contact': dict(per_contact),
        'suspicious_patterns': suspicious_patterns,
        'summary': {
            'total_contacts': len(per_contact),
            'total_calls': sum(s['total_calls'] for s in per_contact.values()),
            'total_duration': sum(s['total_duration'] for s in per_contact.values()),
            'suspicious_contacts': len(set(p['jid'] for p in suspicious_patterns))
        }
    }


def _parse_timestamp(ts: Any) -> datetime:
    """Parse timestamp from various formats."""
    if isinstance(ts, datetime):
        return ts
    
    if isinstance(ts, (int, float)):
        # Unix timestamp (milliseconds or seconds)
        if ts > 10**10:  # Milliseconds
            return datetime.fromtimestamp(ts / 1000)
        else:
            return datetime.fromtimestamp(ts)
    
    if isinstance(ts, str):
        # ISO format
        ts_clean = ts.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(ts_clean)
        except Exception:
            # Try parsing as timestamp
            try:
                return datetime.fromtimestamp(float(ts))
            except Exception:
                pass
    
    raise ValueError(f"Cannot parse timestamp: {ts}")
