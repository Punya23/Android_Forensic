"""Automatic Hash Verification — verify integrity on case open.

Handles smart, background verification of file hashes when an existing case
is opened, caching results to avoid redundant long-running checks.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict

from .hash_verification import verify_all_hashes

logger = logging.getLogger(__name__)


def should_auto_verify(case_dir: Path) -> bool:
    """Check if auto-verification is globally enabled in settings."""
    # In a full app, this might check a global config file or DB.
    # We default to True for this feature implementation.
    return True


def is_verification_needed(case_dir: Path) -> bool:
    """Check if verification is needed based on cache and case state.

    Returns True if no previous verification exists or if it's stale (e.g. > 24 hours old).
    """
    verify_file = case_dir / "derived" / "verification.json"
    if not verify_file.exists():
        return True

    try:
        data = json.loads(verify_file.read_text(encoding="utf-8"))
        last_verified = data.get("timestamp", 0)
        # Re-verify if older than 24 hours
        if time.time() - last_verified > 86400:
            return True

        # Re-verify if case manifest was updated AFTER last verification
        manifest_file = case_dir / "manifest.json"
        if manifest_file.exists() and manifest_file.stat().st_mtime > last_verified:
            return True

        return False
    except Exception:
        return True


def store_verification_result(case_dir: Path, result: Dict[str, Any]) -> None:
    """Store verification result in case derived data."""
    derived_dir = case_dir / "derived"
    derived_dir.mkdir(parents=True, exist_ok=True)

    verify_file = derived_dir / "verification.json"
    try:
        data = {"timestamp": time.time(), "results": result}
        verify_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.error("Failed to store verification result: %s", exc)


def get_verification_alert(verification: Dict[str, Any]) -> Dict[str, str]:
    """Generate user-facing alert based on verification results."""
    status = verification.get("integrity_status", "UNKNOWN")
    failed = verification.get("failed", 0)

    if status == "INTACT":
        return {
            "level": "info",
            "message": "All case files verified successfully against original hashes.",
            "action": "none",
        }
    elif status == "TAMPERED":
        return {
            "level": "critical",
            "message": f"CRITICAL: {failed} files failed hash verification. The evidence may be tampered with or corrupted.",
            "action": "view_report",
        }
    else:
        return {
            "level": "warning",
            "message": "Hash verification state is unknown or incomplete.",
            "action": "reverify",
        }


def auto_verify_on_open(case_dir: Path) -> Dict[str, Any]:
    """Automatically verify hashes when case is opened."""
    if not should_auto_verify(case_dir):
        return {"status": "skipped", "reason": "disabled"}

    if not is_verification_needed(case_dir):
        # Load from cache
        try:
            verify_file = case_dir / "derived" / "verification.json"
            data = json.loads(verify_file.read_text(encoding="utf-8"))
            result = data.get("results", {})
            return {
                "status": "cached",
                "verified": result.get("verified", 0),
                "failed": result.get("failed", 0),
                "alert": get_verification_alert(result),
            }
        except Exception:
            pass  # Fall back to full verification

    # Perform full verification
    logger.info("Running automatic hash verification for case: %s", case_dir.name)
    result = verify_all_hashes(case_dir)
    store_verification_result(case_dir, result)

    return {
        "status": "completed",
        "verified": result.get("verified", 0),
        "failed": result.get("failed", 0),
        "alert": get_verification_alert(result),
    }
