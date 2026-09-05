"""Advanced Telegram analysis features.

Provides deeper forensic analysis of Telegram data including:
- Bot detection and interaction tracking
- Group activity statistics and patterns
- Dominant user identification
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List


def detect_telegram_bots(db_path: str) -> Dict[str, Dict[str, Any]]:
    """Detect Telegram bots and analyze interactions.
    
    Args:
        db_path: Path to Telegram database
        
    Returns:
        Dict mapping bot_id to bot information:
        {
            'bot_id': {
                'name': str,
                'username': str,
                'interaction_count': int,
                'first_seen': str,
                'last_seen': str,
                'message_types': {'text': 10, 'command': 5}
            }
        }
    """
    if not Path(db_path).exists():
        return {}
    
    bots_data = {}
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Try to find bots in users table
        # Bots typically have:
        # 1. is_bot flag = 1
        # 2. username ending in 'bot'
        # 3. Negative user IDs (in some schemas)
        
        queries = [
            # Direct bot flag
            """SELECT uid, first_name, username FROM users WHERE is_bot = 1""",
            # Username pattern
            """SELECT uid, first_name, username FROM users 
               WHERE username LIKE '%bot' OR username LIKE '%Bot'""",
            # Alternative schema
            """SELECT id, name, username FROM dialogs 
               WHERE type = 'bot' OR name LIKE '%bot'""",
        ]
        
        bot_ids = set()
        
        for query in queries:
            try:
                cursor.execute(query)
                rows = cursor.fetchall()
                
                for row in rows:
                    bot_id = str(row[0])
                    name = row[1] if row[1] else "Unknown Bot"
                    username = row[2] if len(row) > 2 and row[2] else ""
                    
                    bot_ids.add(bot_id)
                    
                    if bot_id not in bots_data:
                        bots_data[bot_id] = {
                            'name': name,
                            'username': username,
                            'interaction_count': 0,
                            'first_seen': None,
                            'last_seen': None,
                            'message_types': defaultdict(int)
                        }
                
            except sqlite3.Error:
                continue
        
        # Now count interactions with bots
        if bot_ids:
            # Try to count messages from/to bots
            try:
                # Query messages table
                placeholders = ','.join('?' * len(bot_ids))
                query = f"""
                    SELECT from_id, type, date 
                    FROM messages 
                    WHERE from_id IN ({placeholders})
                    ORDER BY date
                """
                cursor.execute(query, list(bot_ids))
                rows = cursor.fetchall()
                
                for row in rows:
                    bot_id = str(row[0])
                    msg_type = row[1] if row[1] else 'text'
                    timestamp = row[2] if row[2] else None
                    
                    if bot_id in bots_data:
                        bots_data[bot_id]['interaction_count'] += 1
                        bots_data[bot_id]['message_types'][msg_type] += 1
                        
                        if timestamp:
                            if not bots_data[bot_id]['first_seen']:
                                bots_data[bot_id]['first_seen'] = timestamp
                            bots_data[bot_id]['last_seen'] = timestamp
                
            except sqlite3.Error:
                pass
        
        conn.close()
        
        # Convert defaultdicts to regular dicts
        for bot_id in bots_data:
            bots_data[bot_id]['message_types'] = dict(bots_data[bot_id]['message_types'])
        
    except Exception:
        pass
    
    return bots_data


def analyze_telegram_groups(db_path: str) -> Dict[str, Dict[str, Any]]:
    """Analyze Telegram group activity and statistics.
    
    Args:
        db_path: Path to Telegram database
        
    Returns:
        Dict mapping group_id to group statistics:
        {
            'group_id': {
                'name': str,
                'total_messages': int,
                'total_members': int,
                'active_members': int,  # >10 messages
                'top_users': [{'user_id': str, 'name': str, 'message_count': int}],
                'activity_pattern': {
                    'hourly': {0: 5, 1: 2, ...},
                    'daily': {0: 100, 1: 150, ...}
                },
                'first_message': str,
                'last_message': str
            }
        }
    """
    if not Path(db_path).exists():
        return {}
    
    groups_data = {}
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Find all groups
        # Telegram groups have negative chat IDs or specific type
        group_queries = [
            """SELECT uid, title FROM chats WHERE type IN ('channel', 'group', 'supergroup')""",
            """SELECT id, name FROM dialogs WHERE type IN ('channel', 'group')""",
            """SELECT chat_id, title FROM chats WHERE chat_id < 0""",
        ]
        
        group_ids = []
        
        for query in group_queries:
            try:
                cursor.execute(query)
                rows = cursor.fetchall()
                
                for row in rows:
                    group_id = str(row[0])
                    group_name = row[1] if row[1] else f"Group {group_id}"
                    
                    groups_data[group_id] = {
                        'name': group_name,
                        'total_messages': 0,
                        'total_members': 0,
                        'active_members': 0,
                        'top_users': [],
                        'activity_pattern': {
                            'hourly': defaultdict(int),
                            'daily': defaultdict(int)
                        },
                        'first_message': None,
                        'last_message': None,
                        'user_message_counts': defaultdict(int)
                    }
                    group_ids.append(group_id)
                
                break  # Success
                
            except sqlite3.Error:
                continue
        
        # Analyze messages per group
        for group_id in group_ids:
            try:
                # Count total messages
                cursor.execute(
                    "SELECT COUNT(*) FROM messages WHERE chat_id = ?",
                    (group_id,)
                )
                count = cursor.fetchone()
                if count:
                    groups_data[group_id]['total_messages'] = count[0]
                
                # Get message details
                cursor.execute(
                    """SELECT from_id, date FROM messages 
                       WHERE chat_id = ? AND date IS NOT NULL
                       ORDER BY date""",
                    (group_id,)
                )
                rows = cursor.fetchall()
                
                for row in rows:
                    user_id = str(row[0]) if row[0] else "unknown"
                    timestamp = row[1]
                    
                    # Count per user
                    groups_data[group_id]['user_message_counts'][user_id] += 1
                    
                    # Track first/last message
                    if not groups_data[group_id]['first_message']:
                        groups_data[group_id]['first_message'] = timestamp
                    groups_data[group_id]['last_message'] = timestamp
                    
                    # Activity pattern (if timestamp is parseable)
                    try:
                        if isinstance(timestamp, (int, float)):
                            from datetime import datetime
                            dt = datetime.fromtimestamp(timestamp)
                            groups_data[group_id]['activity_pattern']['hourly'][dt.hour] += 1
                            groups_data[group_id]['activity_pattern']['daily'][dt.weekday()] += 1
                    except Exception:
                        pass
                
                # Count active members (>10 messages)
                user_counts = groups_data[group_id]['user_message_counts']
                groups_data[group_id]['active_members'] = sum(1 for count in user_counts.values() if count > 10)
                groups_data[group_id]['total_members'] = len(user_counts)
                
                # Get top users
                sorted_users = sorted(user_counts.items(), key=lambda x: x[1], reverse=True)
                groups_data[group_id]['top_users'] = [
                    {'user_id': user_id, 'message_count': count}
                    for user_id, count in sorted_users[:10]
                ]
                
                # Convert activity patterns to regular dicts
                groups_data[group_id]['activity_pattern']['hourly'] = dict(
                    groups_data[group_id]['activity_pattern']['hourly']
                )
                groups_data[group_id]['activity_pattern']['daily'] = dict(
                    groups_data[group_id]['activity_pattern']['daily']
                )
                
                # Remove temporary data
                del groups_data[group_id]['user_message_counts']
                
            except sqlite3.Error:
                continue
        
        conn.close()
        
    except Exception:
        pass
    
    return groups_data
