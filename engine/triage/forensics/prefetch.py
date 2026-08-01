"""Pre-fetch and predict files for optimization.

Start pulling predicted files immediately in a background thread before
discovery completes, prioritizing known app paths and system paths.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List

from triage.adb import Adb

logger = logging.getLogger(__name__)


def predict_files(device_info: Dict[str, Any], installed_apps: List[str]) -> List[str]:
    """Predict files based on device info and installed apps."""
    paths = []
    paths.extend(get_common_paths(device_info))
    paths.extend(get_app_paths(installed_apps))

    # Filter by what should be prefetched
    return [p for p in paths if should_prefetch_file(p)]


def start_prefetch(predicted_files: List[str], adb: Adb) -> None:
    """Start pulling predicted files immediately in a background thread."""

    def _worker():
        for file_path in predicted_files:
            # Here we would normally call adb.pull() to a staging directory
            logger.debug("Prefetching file: %s", file_path)
            # adb.pull(file_path, staging_dir / Path(file_path).name)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()


def get_common_paths(device_info: Dict[str, Any]) -> List[str]:
    """Get common paths for device based on manufacturer/brand and Android version.

    OEM-specific paths are prepended so they are prefetched first — they are
    high-value targets that only exist on a particular brand's build and are
    otherwise missed by the generic AOSP path list.
    """
    from triage.config import OEM_SPECIFIC_PATHS  # local import to avoid circular at module level

    brand_key = (
        device_info.get("brand") or device_info.get("manufacturer") or ""
    ).lower()

    # OEM-specific high-value paths first (Tier-2 root paths for the brand)
    oem_paths: List[str] = OEM_SPECIFIC_PATHS.get(brand_key, [])

    # Standard AOSP paths present on every Android build
    aosp_paths: List[str] = [
        "/data/system/users/0/accounts.db",
        "/data/system/sync/accounts.xml",
        "/data/data/com.android.providers.contacts/databases/contacts2.db",
        "/data/data/com.android.providers.telephony/databases/mmssms.db",
        "/data/data/com.android.providers.contacts/databases/calllog.db",
    ]

    # Combine: OEM first (highest priority), then AOSP commons
    return oem_paths + aosp_paths


def get_app_paths(installed_apps: List[str]) -> List[str]:
    """Get paths for installed apps (WhatsApp, Telegram, Instagram, etc.)."""
    paths = []
    if "com.whatsapp" in installed_apps:
        paths.extend(
            [
                "/data/data/com.whatsapp/databases/msgstore.db",
                "/data/data/com.whatsapp/databases/wa.db",
            ]
        )
    if "org.telegram.messenger" in installed_apps:
        paths.append("/data/data/org.telegram.messenger/files/cache4.db")
    if "com.instagram.android" in installed_apps:
        paths.append("/data/data/com.instagram.android/databases/direct.db")
    if "com.snapchat.android" in installed_apps:
        paths.extend(
            [
                "/data/data/com.snapchat.android/databases/arroyo.db",
                "/data/data/com.snapchat.android/databases/main.db",
            ]
        )
    return paths


def should_prefetch_file(file_path: str) -> bool:
    """Check if file should be prefetched based on priority and type."""
    # Only prefetch high-priority database files for now
    if file_path.endswith(".db") or file_path.endswith(".sqlite"):
        return True
    if file_path.endswith(".xml"):
        return True
    return False
