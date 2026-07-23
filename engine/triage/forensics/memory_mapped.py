"""Memory-Mapped Processing — efficient large file processing.

Implements mmap-backed file reading for extremely large files, avoiding
loading them entirely into RAM.
"""

from __future__ import annotations

import logging
import mmap
import os
import struct
from pathlib import Path
from typing import Any, Callable, Dict, Iterator

logger = logging.getLogger(__name__)

# Constants
MMAP_THRESHOLD_BYTES = 10 * 1024 * 1024  # 10 MB


def process_memory_mapped(file_path: Path, callback: Callable[[mmap.mmap], Any]) -> Any:
    """Process file using mmap."""
    if not file_path.exists() or file_path.stat().st_size == 0:
        return None

    try:
        with open(file_path, "rb") as f:
            # mmap.mmap(fileno, length, access)
            # length=0 means whole file
            with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                return callback(mm)
    except Exception as exc:
        logger.error("Failed to mmap %s: %s", file_path, exc)
        return None


def mmap_sqlite(db_path: Path) -> Dict[str, Any]:
    """Memory-map SQLite database and extract basic info without full load."""

    def _parse_header(mm: mmap.mmap) -> Dict[str, Any]:
        if len(mm) < 100:
            return {"error": "File too small"}

        header = mm[:100]
        if header[:16] != b"SQLite format 3\000":
            return {"error": "Not a SQLite 3 database"}

        page_size = struct.unpack(">H", header[16:18])[0]
        if page_size == 1:
            page_size = 65536

        return {
            "page_size": page_size,
            "write_version": header[18],
            "read_version": header[19],
            "pages": struct.unpack(">I", header[28:32])[0],
        }

    result = process_memory_mapped(db_path, _parse_header)
    return result or {"error": "Failed to map file"}


def mmap_large_file(file_path: Path, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
    """Iterate through large file with mmap in streaming chunks."""
    if not file_path.exists() or file_path.stat().st_size == 0:
        return

    try:
        with open(file_path, "rb") as f:
            with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                file_size = len(mm)
                offset = 0
                while offset < file_size:
                    size = min(chunk_size, file_size - offset)
                    yield mm[offset : offset + size]
                    offset += size
    except Exception as exc:
        logger.error("Failed to yield from mmap %s: %s", file_path, exc)


def get_mmap_stats(file_path: Path) -> Dict[str, Any]:
    """Get mmap statistics for a file."""
    if not file_path.exists():
        return {"error": "File not found"}

    size_bytes = file_path.stat().st_size
    page_size = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096

    return {
        "size_bytes": size_bytes,
        "page_size": page_size,
        "pages": (size_bytes + page_size - 1) // page_size,
        "is_mmap_beneficial": should_use_mmap(file_path),
    }


def should_use_mmap(file_path: Path) -> bool:
    """Check if mmap is beneficial based on file size."""
    if not file_path.exists():
        return False
    return file_path.stat().st_size >= MMAP_THRESHOLD_BYTES
