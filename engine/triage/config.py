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
    # OEM skin / OS version probes — absent on stock Android, populated on OEM builds
    "ro.build.version.oneui": "oneui_version",        # Samsung One UI
    "ro.miui.ui.version.name": "miui_version",        # Xiaomi MIUI / HyperOS (legacy key)
    "ro.mi.os.version.name": "hyperos_version",       # Xiaomi HyperOS (new key)
    "ro.oplus.version.os": "coloros_version",         # OPPO ColorOS / Realme UI / new OxygenOS
    "ro.oxygen.version": "oxygenos_version",          # OnePlus OxygenOS (older builds)
    "ro.nothing.version": "nothing_os_version",       # Nothing OS
    "ro.build.version.magic": "magicos_version",      # Honor MagicOS
    "ro.build.version.harmonyos": "harmonyos_version",# Huawei HarmonyOS
}

# ---------------------------------------------------------------------------
# OEM Quirks Registry
# ---------------------------------------------------------------------------
# Maps lowercase brand/manufacturer name → list of string quirk flags.
# These flags are attached to DeviceInfo.oem_quirks and surfaced in the
# forensic report so the examiner knows what friction to expect on-scene.
#
# Flag naming convention: <scope>_<issue>  e.g. "pm_grant_blocked"
# ---------------------------------------------------------------------------
OEM_QUIRKS: dict[str, list[str]] = {
    # Samsung One UI — Knox partition keeps Secure Folder data opaque.
    "samsung": [
        "knox_container",          # Knox Secure Folder; app-private DBs inside are encrypted
        "secure_folder_opaque",    # Secure Folder mount not reachable via ADB shell
        "logsprovider_db",         # Samsung-specific call-log DB at com.sec.android.provider.logsprovider
    ],
    # Xiaomi / Redmi / POCO — HyperOS/MIUI has strict USB install controls.
    "xiaomi": [
        "mi_account_usb_auth",     # USB Debugging (Security settings) requires Mi Account login + SIM
        "install_via_usb_toggle",  # 'Install via USB' must be enabled in Developer Options separately
        "aggressive_battery_kill", # MIUI battery saver may kill the collector APK mid-run
    ],
    "redmi": [
        "mi_account_usb_auth",
        "install_via_usb_toggle",
        "aggressive_battery_kill",
    ],
    "poco": [
        "mi_account_usb_auth",
        "install_via_usb_toggle",
        "aggressive_battery_kill",
    ],
    # OPPO ColorOS — password prompt on APK install, aggressive app management.
    "oppo": [
        "usb_install_password_prompt",  # OS may ask for lock screen PIN during `adb install`
        "aggressive_process_kill",      # ColorOS may terminate collector between ADB triggers
        "auto_launch_deny",             # ColorOS may block the collector from auto-starting
    ],
    # Realme UI (ColorOS derivative)
    "realme": [
        "usb_install_password_prompt",
        "aggressive_process_kill",
        "auto_launch_deny",
    ],
    # OnePlus OxygenOS — pm grant blocked on real devices (fixed in commit 6485e5e).
    "oneplus": [
        "pm_grant_blocked",        # `pm grant` raises SecurityException; must use runtime dialog
    ],
    # Google Pixel UI — closest to stock; no known forensic friction.
    "google": [],
    # Motorola Hello UI — very close to stock; no known forensic friction.
    "motorola": [],
    # Nothing OS — stock-like; no known forensic friction.
    "nothing": [],
    # Honor MagicOS — similar to OPPO/Realme lineage post-Huawei split.
    "honor": [
        "usb_debug_timeout",       # USB debugging authorization may time out quickly on MagicOS
        "magic_link_interference", # Honor's cross-device feature may interfere with ADB sessions
    ],
    # Huawei HarmonyOS — AOSP-based versions (≤3.x) partially work;
    # HarmonyOS NEXT drops AOSP entirely and is incompatible.
    "huawei": [
        "harmonyos_check_required",# Engine will detect version and warn/abort appropriately
        "adb_may_be_absent",       # HarmonyOS NEXT devices may lack standard ADB
        "google_services_absent",  # No Google Play, no GMS artifacts
    ],
}

# ---------------------------------------------------------------------------
# OEM-specific artifact paths (DB paths that only exist on that brand's build)
# ---------------------------------------------------------------------------
# These are Tier-2 (root required) paths. They are passed to the prefetch
# module so high-priority OEM databases are pulled early in the pipeline.
# ---------------------------------------------------------------------------
OEM_SPECIFIC_PATHS: dict[str, list[str]] = {
    "samsung": [
        # Samsung call log (separate from AOSP calllog.db on many builds)
        "/data/data/com.sec.android.provider.logsprovider/databases/logs.db",
        # Samsung messaging (alternative to AOSP mmssms.db)
        "/data/data/com.samsung.android.messaging/databases/message.db",
        # Samsung Gallery (may contain metadata for deleted media)
        "/data/data/com.sec.android.gallery3d/databases/picasa.db",
    ],
    "xiaomi": [
        # MIUI/HyperOS Gallery — stores metadata for all media including cloud-synced
        "/data/data/com.miui.gallery/databases/miuiGallery.db",
        # MIUI security centre (app-usage + suspicious app history)
        "/data/data/com.miui.securitycenter/databases/data.db",
    ],
    "redmi": [
        "/data/data/com.miui.gallery/databases/miuiGallery.db",
        "/data/data/com.miui.securitycenter/databases/data.db",
    ],
    "poco": [
        "/data/data/com.miui.gallery/databases/miuiGallery.db",
        "/data/data/com.miui.securitycenter/databases/data.db",
    ],
    "oppo": [
        # ColorOS messaging DB
        "/data/data/com.coloros.mms/databases/message.db",
    ],
    "realme": [
        "/data/data/com.coloros.mms/databases/message.db",
    ],
    "oneplus": [
        "/data/data/com.coloros.mms/databases/message.db",
    ],
    "google": [],
    "motorola": [],
    "nothing": [],
    "honor": [],
    "huawei": [],
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