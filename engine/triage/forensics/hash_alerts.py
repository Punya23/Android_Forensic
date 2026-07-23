"""Hash Alerts — immediate notifications on hash mismatch.

Generates, logs, and retrieves alerts when a hash mismatch is detected
during active extraction or background verification.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def check_hash_alert(expected: str, actual: str, file_path: str) -> Dict[str, Any]:
    """Check for hash mismatch and generate alert data if needed."""
    if not expected or not actual:
        return {}

    if expected.lower() == actual.lower():
        return {}

    return {
        "timestamp": time.time(),
        "level": "critical",
        "type": "hash_mismatch",
        "file_path": file_path,
        "expected": expected,
        "actual": actual,
        "message": f"Hash mismatch detected for {file_path}",
    }


def generate_hash_alert(alert_data: Dict[str, Any]) -> str:
    """Generate formatted string representation of a hash alert."""
    if not alert_data:
        return ""

    msg = alert_data.get("message", "Unknown hash alert")
    file_path = alert_data.get("file_path", "unknown")
    expected = alert_data.get("expected", "-")
    actual = alert_data.get("actual", "-")

    return f"🚨 [CRITICAL ALERT] {msg}\n   Path: {file_path}\n   Expected: {expected}\n   Actual: {actual}"


def log_hash_alert(alert_data: Dict[str, Any], case_dir: Path) -> None:
    """Log hash alert to case audit log and derived alerts file."""
    if not alert_data:
        return

    # Print to standard logger
    formatted = generate_hash_alert(alert_data)
    logger.critical(formatted)

    # Save to derived alerts JSON
    derived_dir = case_dir / "derived"
    derived_dir.mkdir(parents=True, exist_ok=True)
    alerts_file = derived_dir / "hash_alerts.json"

    try:
        alerts = []
        if alerts_file.exists():
            try:
                alerts = json.loads(alerts_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass

        alerts.append(alert_data)
        alerts_file.write_text(json.dumps(alerts, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.error("Failed to log hash alert: %s", exc)


def get_hash_alerts(case_dir: Path) -> List[Dict[str, Any]]:
    """Get all hash alerts for a case."""
    alerts_file = case_dir / "derived" / "hash_alerts.json"
    if not alerts_file.exists():
        return []

    try:
        return json.loads(alerts_file.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("Failed to read hash alerts: %s", exc)
        return []


def clear_hash_alerts(case_dir: Path) -> None:
    """Clear all hash alerts for a case."""
    alerts_file = case_dir / "derived" / "hash_alerts.json"
    try:
        if alerts_file.exists():
            alerts_file.unlink()
    except Exception as exc:
        logger.error("Failed to clear hash alerts: %s", exc)
