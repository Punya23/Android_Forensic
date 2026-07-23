"""Incremental Parsing — parse large files as they arrive.

Implements streaming parsers for SQLite, JSON, and CSV to yield records
immediately without loading the entire dataset into memory.
"""
from __future__ import annotations

import csv
import json
import logging
import sqlite3
from pathlib import Path
from typing import Dict, Iterator

logger = logging.getLogger(__name__)

# Constants
INCREMENTAL_THRESHOLD_BYTES = 50 * 1024 * 1024  # 50 MB


def parse_incrementally(data_stream: Iterator[bytes]) -> Iterator[Dict]:
    """Parse data stream incrementally (generic fallback).
    
    Yields chunks wrapped in a dictionary.
    """
    for chunk in data_stream:
        yield {"chunk_size": len(chunk), "data": chunk}


def incremental_sqlite_parse(db_path: Path, table: str = None) -> Iterator[Dict]:
    """Parse SQLite incrementally, yielding rows as they're read."""
    if not db_path.exists():
        return
        
    try:
        # uri=True allows opening immutable/read-only
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # If no table specified, pick the first user table
        if not table:
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            row = cursor.fetchone()
            if not row:
                return
            table = row[0]
            
        # Yield rows one by one
        cursor.execute(f"SELECT * FROM {table}")
        while True:
            row = cursor.fetchone()
            if not row:
                break
            yield dict(row)
            
    except Exception as exc:
        logger.error("Incremental SQLite parse failed for %s: %s", db_path, exc)
    finally:
        if 'conn' in locals():
            conn.close()


def incremental_json_parse(file_path: Path) -> Iterator[Dict]:
    """Parse JSON incrementally using ijson if available, or line-by-line fallback."""
    if not file_path.exists():
        return
        
    try:
        import ijson
        with open(file_path, "rb") as f:
            # Assuming array of objects at the root
            for item in ijson.items(f, "item"):
                if isinstance(item, dict):
                    yield item
                else:
                    yield {"value": item}
    except ImportError:
        # Fallback for JSON Lines (NDJSON) or simple formats
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line in ("[", "]", ","):
                        continue
                    if line.endswith(","):
                        line = line[:-1]
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue
        except Exception as exc:
            logger.error("Fallback JSON parse failed for %s: %s", file_path, exc)
    except Exception as exc:
        logger.error("Incremental JSON parse failed for %s: %s", file_path, exc)


def incremental_csv_parse(file_path: Path) -> Iterator[Dict]:
    """Parse CSV incrementally, yielding rows as they're read."""
    if not file_path.exists():
        return
        
    try:
        with open(file_path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                yield dict(row)
    except Exception as exc:
        logger.error("Incremental CSV parse failed for %s: %s", file_path, exc)


def should_parse_incrementally(file_path: Path) -> bool:
    """Check if incremental parsing is beneficial based on file size and type."""
    if not file_path.exists():
        return False
        
    size = file_path.stat().st_size
    if size < INCREMENTAL_THRESHOLD_BYTES:
        return False
        
    ext = file_path.suffix.lower()
    return ext in (".db", ".sqlite", ".json", ".csv")
