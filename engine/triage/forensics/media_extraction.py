"""Selective Media Extraction — priority-based media pulling.

Pulls media files in phases:
1. Thumbnails (<100KB)
2. Small images (<5MB)
3. Large images (background)
4. Videos (on-demand / skipped)
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List

from engine.triage.adb import Adb

logger = logging.getLogger(__name__)

# Constants
PHASE_1_LIMIT_S = 30.0
PHASE_2_LIMIT_S = 60.0


def extract_media_with_priority(media_root: Path, adb: Adb) -> Dict[str, Any]:
    """Extract media files with priority phases."""
    start_time = time.monotonic()
    results = {
        "thumbnails": [],
        "small_images": [],
        "large_images_started": False,
    }

    # Phase 1: Thumbnails
    results["thumbnails"] = extract_thumbnails(media_root, adb)

    # Phase 2: Small images
    elapsed = time.monotonic() - start_time
    if elapsed < PHASE_2_LIMIT_S:
        results["small_images"] = extract_small_images(media_root, adb, max_size_mb=5)

    return results


def extract_thumbnails(media_root: Path, adb: Adb) -> List[Dict]:
    """Extract thumbnail files only."""
    thumbs: List[Dict] = []
    # Probe for .thumbnails directory
    remote_thumb_dir = f"{media_root}/.thumbnails"
    files = adb.list_files(remote_thumb_dir, timeout=10)
    for f in files:
        size = get_media_file_size(f, adb)
        if size > 0 and size < 100 * 1024:
            thumbs.append({"path": f, "size": size})
    return thumbs


def extract_small_images(media_root: Path, adb: Adb, max_size_mb: int = 5) -> List[Dict]:
    """Extract images under max_size_mb."""
    small_imgs: List[Dict] = []
    files = adb.list_files(str(media_root), timeout=30)
    max_bytes = max_size_mb * 1024 * 1024
    for f in files:
        if "/.thumbnails/" in f:
            continue
        if any(f.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp")):
            size = get_media_file_size(f, adb)
            if 0 < size <= max_bytes:
                small_imgs.append({"path": f, "size": size})
    return small_imgs


def extract_large_images_background(media_root: Path, adb: Adb, callback: Callable) -> None:
    """Extract large images in background thread."""
    def _worker():
        files = adb.list_files(str(media_root), timeout=30)
        for f in files:
            if any(f.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp")):
                size = get_media_file_size(f, adb)
                if size > 5 * 1024 * 1024:
                    callback(f, size)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()


def get_media_file_size(device_path: str, adb: Adb) -> int:
    """Get file size without pulling using ls -la."""
    res = adb.shell(f"ls -la '{device_path}'", timeout=5)
    if not res.ok:
        return 0
    # Expected output: -rw-rw---- 1 root everybody 12345 2024-01-01 12:00 /path
    try:
        parts = res.stdout.strip().split()
        if len(parts) >= 5:
            return int(parts[4])
    except (ValueError, IndexError):
        pass
    return 0


def should_extract_media(device_path: str, elapsed_time: float) -> bool:
    """Decision based on time elapsed."""
    if elapsed_time < PHASE_1_LIMIT_S:
        return "/.thumbnails/" in device_path
    if elapsed_time < PHASE_2_LIMIT_S:
        return True # allow small images (size check done elsewhere)
    return True # allow all
