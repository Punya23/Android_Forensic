"""Continuous Hash Verification — verify hashes on the fly.

Tracks hash verification status continuously during the acquisition process
instead of waiting until the end.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Dict, Set

logger = logging.getLogger(__name__)


class ContinuousHashVerifier:
    """Continuously verifies hashes during extraction."""

    def __init__(self):
        self._verified_files: Set[str] = set()
        self._failed_files: Set[str] = set()
        self._total_files = 0
        self._lock = None  # Optional lock if accessed from multiple threads

    def reset(self) -> None:
        """Reset verification state."""
        self._verified_files.clear()
        self._failed_files.clear()
        self._total_files = 0

    def verify_file(self, file_path: Path, expected_hash: str) -> bool:
        """Verify a single file and update status."""
        if not expected_hash:
            return False

        self._total_files += 1
        path_str = str(file_path)

        if not file_path.exists():
            self._failed_files.add(path_str)
            return False

        try:
            sha256 = hashlib.sha256()
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    sha256.update(chunk)

            actual_hash = sha256.hexdigest().lower()
            if actual_hash == expected_hash.lower():
                self._verified_files.add(path_str)
                # Remove from failed if it was there previously
                self._failed_files.discard(path_str)
                return True
            else:
                self._failed_files.add(path_str)
                logger.warning(
                    "Hash mismatch for %s (Expected: %s, Actual: %s)",
                    file_path,
                    expected_hash,
                    actual_hash,
                )
                return False
        except Exception as exc:
            self._failed_files.add(path_str)
            logger.warning("Error hashing %s: %s", file_path, exc)
            return False

    def get_status(self) -> str:
        """Get current verification status."""
        if not self._total_files:
            return "⏳ Waiting for files..."

        if self._failed_files:
            return f"⚠️ {len(self._failed_files)} files failed verification"

        if len(self._verified_files) == self._total_files:
            return "✅ All files verified"

        return f"✅ {len(self._verified_files)} verified, {self._total_files - len(self._verified_files)} pending"

    def get_stats(self) -> Dict[str, int]:
        """Get verification statistics."""
        return {
            "verified_count": len(self._verified_files),
            "failed_count": len(self._failed_files),
            "total_count": self._total_files,
        }
