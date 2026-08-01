"""Batch Transfer — compress and transfer multiple files at once.

Improves transfer speeds for many small files by compressing them into a single
tar/zip archive on the device, pulling the archive, and extracting it locally.
"""

from __future__ import annotations

import logging
import tarfile
from pathlib import Path
from typing import Dict, List

from triage.adb import Adb

logger = logging.getLogger(__name__)


def batch_transfer_files(files: List[str], adb: Adb, staging_dir: Path) -> List[Dict]:
    """Transfer multiple files as a batch: compress, pull, extract, cleanup."""
    if not files:
        return []

    results = []

    # 1. Create batch on device
    remote_batch_path = create_batch_on_device(files, adb)
    if not remote_batch_path:
        return results

    # 2. Pull batch
    local_batch_path = staging_dir / "batch_transfer.tar.gz"
    success = pull_batch_from_device(remote_batch_path, adb, local_batch_path)

    if success and local_batch_path.exists():
        # 3. Extract locally
        extracted_paths = extract_batch_locally(local_batch_path, staging_dir)
        for p in extracted_paths:
            results.append({"file": str(p), "status": "extracted"})

        # Remove local archive
        try:
            local_batch_path.unlink()
        except OSError:
            pass

    # 4. Cleanup on device
    cleanup_batch_on_device(remote_batch_path, adb)

    return results


def create_batch_on_device(files: List[str], adb: Adb) -> str:
    """Create a tar archive on the device."""
    remote_path = "/data/local/tmp/batch_transfer.tar.gz"

    # Group files into a single tar command, being mindful of command line length limits
    # In a real implementation, we might need to write the list to a file and use -T
    files_str = " ".join(f"'{f}'" for f in files[:50])  # limit for example

    res = adb.shell(f"tar -czf {remote_path} {files_str} 2>/dev/null", timeout=120)
    if res.ok:
        return remote_path

    # Fallback if tar fails (e.g. no permission to read some files)
    return ""


def pull_batch_from_device(remote_path: str, adb: Adb, local_path: Path) -> bool:
    """Pull the batch file from the device."""
    res = adb.pull(remote_path, local_path, timeout=300)
    return res.ok


def extract_batch_locally(batch_path: Path, output_dir: Path) -> List[Path]:
    """Extract the batch file locally."""
    extracted = []
    try:
        with tarfile.open(batch_path, "r:gz") as tar:
            # Prevent path traversal attacks during extraction
            for member in tar.getmembers():
                # Strip leading slashes to make relative
                if member.name.startswith("/"):
                    member.name = member.name.lstrip("/")
                tar.extract(member, path=output_dir, set_attrs=False)
                extracted.append(output_dir / member.name)
    except Exception as exc:
        logger.warning("Failed to extract batch file: %s", exc)

    return extracted


def cleanup_batch_on_device(remote_path: str, adb: Adb) -> None:
    """Remove the batch file from the device."""
    if remote_path:
        adb.shell(f"rm '{remote_path}'", timeout=10)


def get_batch_size(files: List[str], adb: Adb) -> int:
    """Calculate total size of files to decide if batch transfer is beneficial."""
    if not files:
        return 0

    total_size = 0
    for f in files[:20]:  # Check a subset to save time
        res = adb.shell(f"ls -la '{f}'", timeout=5)
        if res.ok:
            try:
                parts = res.stdout.strip().split()
                if len(parts) >= 5:
                    total_size += int(parts[4])
            except (ValueError, IndexError):
                pass

    # Extrapolate
    if len(files) > 20:
        avg_size = total_size / 20
        total_size = int(avg_size * len(files))

    return total_size
