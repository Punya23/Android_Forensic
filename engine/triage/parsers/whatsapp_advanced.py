"""Advanced WhatsApp analysis features.

Provides deeper forensic analysis of WhatsApp data including:
- Reaction analysis from message_reactions table
- Admin detection from group_participants table

WhatsApp *call* analytics are deliberately absent: the pipeline's call log is the Android
call-log provider (number / type / duration), which carries no WhatsApp calls, and nothing
parses msgstore.db's ``call_log`` table yet. An analyser with no producer would only ever
report an empty result that reads as "no WhatsApp calls".
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List


def _open_readonly(db_path: str) -> sqlite3.Connection:
    """Open a stored artifact without any possibility of writing to it.

    A plain ``sqlite3.connect`` on a WAL-mode database checkpoints the WAL into the main
    file and deletes the sidecar on close — silently changing the evidence after its hash
    was recorded. ``mode=ro&immutable=1`` forbids that (same rule as parsers/google_maps).
    """
    return sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)


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
        conn = _open_readonly(db_path)
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
        conn = _open_readonly(db_path)
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
