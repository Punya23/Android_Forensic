"""Static configuration: acquisition targets, confidence tiers, standards references.

These lists are intentionally data (not hard-coded logic) so they can be tuned per
target OEM/Android version without touching the acquisition code. Paths are treated as
*candidates* and probed dynamically — nothing here is assumed to exist.
"""

from __future__ import annotations

from enum import Enum


# --- Acquisition tiers -------------------------------------------------------
class Tier(str, Enum):
    """How invasive the acquisition action was. Every artifact carries its tier so the
    chain-of-custody report can answer 'how did you get this'."""

    TIER0 = "tier0"  # zero device-state change: adb pull of shared storage, dumpsys
    TIER1 = "tier1"  # shell-level but state-changing: helper APK + pm grant
    TIER2 = "tier2"  # root required: raw app-private DBs (advanced / lab mode)


# --- Battery-aware acquisition -----------------------------------------------
# How often the live BatteryMonitor re-polls the device during a run (seconds).
# Kept here, not in pipeline.py, so it can be tuned per-deployment like every
# other acquisition constant in this file.
BATTERY_POLL_INTERVAL_S: float = 20.0


# --- Recovery confidence tiers ----------------------------------------------
class Confidence(str, Enum):
    """Provenance/confidence of a data row. Never render carved data with the same
    weight as live data — this enum drives the UI badge colour and the report."""

    LIVE = "live"  # normal query result
    RECOVERED_VERIFIED = (
        "recovered"  # intact freelist page or un-checkpointed WAL frame
    )
    CARVED_PARTIAL = (
        "carved"  # signature-matched over unallocated space; may be corrupt
    )
    DELETION_DETECTED = (
        "deletion"  # a rowid gap proves a deletion; no content recovered
    )


# --- Tier 0 shared-storage pull targets (probed; missing paths are skipped) ---
# These are candidate roots on /sdcard reachable by the shell UID without root. We
# enumerate the *whole* app shared-storage subtree (…/WhatsApp, not just …/WhatsApp/Media)
# because msgstore.db.crypt15 lives under a sibling Databases/ folder on many builds — the
# research flagged that its exact location has moved across versions, so probe broadly.
TIER0_PULL_ROOTS: list[str] = [
    "/sdcard/DCIM",
    "/sdcard/Pictures",
    "/sdcard/Download",
    "/sdcard/Movies",
    "/sdcard/Music",
    "/sdcard/Documents",
    "/sdcard/WhatsApp",  # legacy pre-scoped-storage layout, still present on upgraded devices
    "/sdcard/Android/media/com.whatsapp/WhatsApp",  # Media + Databases + Backups
    "/sdcard/Android/media/org.telegram.messenger/Telegram",
]

# App-media roots we categorise specially in the dashboard.
APP_MEDIA_ROOTS: dict[str, str] = {
    "whatsapp": "/sdcard/Android/media/com.whatsapp/WhatsApp/Media",
    "telegram": "/sdcard/Android/media/org.telegram.messenger/Telegram",
}

# Image extensions we run EXIF/GPS extraction over.
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".webp", ".tiff"}
VIDEO_EXTS = {".mp4", ".3gp", ".mkv", ".mov", ".avi", ".webm"}
AUDIO_EXTS = {".opus", ".m4a", ".aac", ".mp3", ".ogg", ".amr", ".wav"}

# `getprop` keys collected for the device intake block.
DEVICE_PROPS: dict[str, str] = {
    "ro.product.manufacturer": "manufacturer",
    "ro.product.model": "model",
    "ro.product.brand": "brand",
    "ro.product.name": "product",
    "ro.build.version.release": "android_version",
    "ro.build.version.sdk": "sdk",
    "ro.build.display.id": "build_id",
    "ro.serialno": "serial",
    "ro.boot.serialno": "boot_serial",
    "gsm.sim.operator.alpha": "carrier",
}

# --- Standards references quoted verbatim in the report footer ---------------
STANDARDS_REFS: list[str] = [
    "NIST SP 800-101 Rev.1 — Guidelines on Mobile Device Forensics",
    "SWGDE 12-F-002 v2.0 — Best Practices for Mobile Phone Forensics",
    "SWGDE 18-F-003 v2.0 — Mobile Device Evidence Collection, Preservation, Handling & Acquisition",
    "SWGDE Position on the Use of MD5 and SHA1 Hash Algorithms v1.0",
]

# The phrase we use everywhere instead of the (unsupportable) 'read-only'.
ACQUISITION_DISCLAIMER = (
    "Minimally-invasive, fully-logged logical acquisition. No write-blocking exists for "
    "mobile devices (SWGDE 18-F-003); every device interaction is timestamped and logged "
    "in the audit trail. This is a field-triage preview, NOT a substitute for full "
    "laboratory examination (NIST SP 800-101r1 §4.5)."
)

PRIMARY_HASH = "sha256"