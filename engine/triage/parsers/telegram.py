"""Telegram cache4.db parser.

Handles both our synthetic plaintext schema (for the demo) and real Telegram
cache4.db schemas by heuristically extracting strings from the TL-encoded `data`
BLOB in the `messages` table if a plaintext column isn't found.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from ..config import Confidence
from ..models import Message

# Minimum length for a carved string from a BLOB to be considered a message body
MIN_BLOB_STRING_LEN = 3


def _extract_strings_from_blob(blob: bytes) -> str:
    """Extract printable UTF-8 strings from a Telegram TL BLOB."""
    if not blob:
        return ""
    
    strings = []
    run = bytearray()
    
    def flush():
        if len(run) >= MIN_BLOB_STRING_LEN:
            try:
                text = run.decode("utf-8")
                if sum(1 for c in text if c.isprintable()) >= len(text) * 0.8:
                    strings.append(text.strip())
            except UnicodeDecodeError:
                pass
        run.clear()

    for b in blob:
        is_text = b in (0x09, 0x0A, 0x0D) or 0x20 <= b <= 0x7E or b >= 0x80
        if is_text:
            run.append(b)
        else:
            flush()
    flush()
    
    # Heuristic: the longest string in the blob is usually the message text,
    # or we can just join them. Joining is safer for now.
    return " ".join(strings)


def parse_telegram_db(path: str | Path, max_rows: int = 5000) -> list[Message]:
    """Parse Telegram messages from cache4.db."""
    path = Path(path)
    messages: list[Message] = []
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        
        # Check if messages table exists
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'")]
        
        if not tables:
            con.close()
            return []
            
        cols = [c[1] for c in con.execute("PRAGMA table_info('messages')")]
        has_data_blob = "data" in [c.lower() for c in cols]
        
        # Our mock DB has body, sender, date
        has_body = "body" in [c.lower() for c in cols]
        has_sender = "sender" in [c.lower() for c in cols]
        has_date = "date" in [c.lower() for c in cols]

        rows = con.execute(f"SELECT * FROM messages LIMIT {int(max_rows)}").fetchall()
        
        for r in rows:
            body = ""
            sender = "(unknown)"
            timestamp = None
            
            if has_body and r["body"]:
                body = str(r["body"]).strip()
            elif has_data_blob and r["data"]:
                blob = r["data"]
                if isinstance(blob, (bytes, bytearray)):
                    body = _extract_strings_from_blob(blob)
                else:
                    body = str(blob)
                    
            if not body:
                continue
                
            if has_sender and r["sender"]:
                sender = str(r["sender"])
                
            if has_date and r["date"]:
                # Assume epoch timestamp
                val = r["date"]
                try:
                    n = int(val)
                    if n > 1e12:
                        n //= 1000
                    from datetime import datetime, timezone
                    timestamp = datetime.fromtimestamp(n, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                except (ValueError, TypeError, OSError):
                    pass
                    
            messages.append(Message(
                app="telegram",
                sender=sender,
                body=body,
                timestamp=timestamp,
                confidence=Confidence.LIVE,
                source_file=path.name,
                provenance="live table 'messages'",
            ))
            
        con.close()
    except sqlite3.Error:
        pass
        
    return messages
