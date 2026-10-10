"""Android-version and OEM compatibility rules for the non-root flow.

The non-root flow is two things: the ADB shell UID (``adb pull``, ``dumpsys``, ``content``) and
the sideloaded Collector app (installed with ``adb install``, given runtime permissions with
``pm grant``). Both behave differently by Android release and by OEM skin. This module holds
those rules as pure functions so they can be tested without a phone and the pipeline stays free
of version checks.

Sources are AOSP (``android1x-release`` branches), developer.android.com and vendor
documentation, read in the 2026-10 compatibility audit. Anything the audit could not confirm is
marked UNVERIFIED and is never turned into a claim in a report. Nothing here has been run on a
physical Android 10–15 handset by this project yet; it was exercised on an Android 16 emulator,
so the version rules are "AOSP says", not "your phone does".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Collector ``minSdk`` (apk/app/build.gradle): older releases cannot install it at all.
MIN_COLLECTOR_SDK = 26
#: The Android releases the compatibility audit covers (10 … 15).
TESTED_SDK_RANGE = (29, 35)


def sdk_int(value: str | int | None) -> int:
    """API level from ``ro.build.version.sdk`` (``"34"``); 0 when unknown or unparseable."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# Runtime permissions the Collector needs, by the API level they exist at
# ---------------------------------------------------------------------------
# (permission, first API level, last API level or None). ``pm grant`` of a permission that does
# not exist at this level — or that the manifest does not request there — fails, and the failure
# used to be logged as an error on every run. Boundaries from the platform documentation:
# READ_EXTERNAL_STORAGE is replaced by READ_MEDIA_* in 33, ACCESS_MEDIA_LOCATION arrives in 29,
# BLUETOOTH_CONNECT/SCAN in 31. POST_NOTIFICATIONS (33) is deliberately absent: the Collector
# posts no notifications and its manifest does not request it, so granting it can never take.
_COLLECTOR_GRANTS: tuple[tuple[str, int, int | None], ...] = (
    ("android.permission.READ_CONTACTS", 1, None),
    ("android.permission.READ_CALL_LOG", 16, None),
    ("android.permission.READ_SMS", 1, None),
    ("android.permission.READ_EXTERNAL_STORAGE", 1, 32),
    ("android.permission.READ_MEDIA_IMAGES", 33, None),
    ("android.permission.READ_MEDIA_VIDEO", 33, None),
    ("android.permission.READ_MEDIA_AUDIO", 33, None),
    ("android.permission.ACCESS_MEDIA_LOCATION", 29, None),
    ("android.permission.READ_CALENDAR", 1, None),
    ("android.permission.GET_ACCOUNTS", 1, None),
    ("android.permission.ACCESS_FINE_LOCATION", 1, None),
    ("android.permission.ACCESS_COARSE_LOCATION", 1, None),
    ("android.permission.BLUETOOTH_CONNECT", 31, None),
    ("android.permission.BLUETOOTH_SCAN", 31, None),
)


def collector_grants(sdk: int) -> list[str]:
    """Permissions worth ``pm grant``-ing to the Collector on this API level.

    An unknown level (0) returns every permission: a harmless superset, which is what the engine
    did before it looked at the version at all."""
    return [p for p, lo, hi in _COLLECTOR_GRANTS if not sdk or (lo <= sdk and (hi is None or sdk <= hi))]


# ---------------------------------------------------------------------------
# `adb install` failures
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class InstallFailure:
    """Why ``adb install`` failed and what to do about it.

    ``action``: ``reinstall`` — a copy signed with another key is on the phone; remove it and
    retry. ``ask_examiner`` — a person has to do something on the phone's screen; retry once
    afterwards. ``none`` — not recoverable from here; the hint says what to change."""

    code: str
    hint: str
    action: str


_REDMI = {"mi_account_usb_auth", "install_via_usb_toggle"}
_COLOROS = {"usb_install_password_prompt", "pm_grant_blocked"}
_VIVO = {"usb_install_verify_prompt"}


def _screen_hint(quirks: set[str]) -> str:
    if quirks & _REDMI:
        return (
            "Xiaomi/Redmi/POCO: turn on 'Install via USB' and 'USB debugging (Security settings)' in "
            "Developer options (the second needs a SIM and a Mi Account), then tap Install on the phone's prompt"
        )
    if quirks & _COLOROS:
        return (
            "OnePlus/OPPO/Realme: tap Install (and enter the lock-screen PIN) on the phone's prompt; if "
            "installs or permission grants keep being refused, turn on 'Disable permission monitoring' in "
            "Developer options"
        )
    if quirks & _VIVO:
        return "Vivo/iQOO: turn off 'Verify apps over USB' if shown, and tap Install on the phone's prompt"
    if "auto_blocker_usb" in quirks:
        return (
            "Samsung: turn off Auto Blocker (Settings > Security and privacy), and tap 'Install anyway' / "
            "'Install without scanning' if Google Play Protect asks to scan the app"
        )
    return "unlock the phone and tap Install / Allow on any prompt it shows; keep the screen on"


_USER_ACTION_CODES = frozenset(
    {
        "INSTALL_FAILED_USER_RESTRICTED",
        "INSTALL_FAILED_ABORTED",
        "INSTALL_CANCELED_BY_USER",
        "INSTALL_FAILED_VERIFICATION_FAILURE",
        "INSTALL_FAILED_VERIFICATION_TIMEOUT",
    }
)


def classify_install_failure(output: str, quirks: set[str] | list[str] | None = None) -> InstallFailure | None:
    """Turn ``adb install`` output into an :class:`InstallFailure`, or ``None`` if it succeeded.

    Error names are PackageManager's own (``INSTALL_FAILED_*``); OEM wording of a refusal varies,
    so the skin only picks which hint to give, never whether the install counts as failed."""
    text = output or ""
    q = set(quirks or [])
    m = re.search(r"INSTALL_[A-Z_]+", text)
    code = m.group(0) if m else ""
    if not code:
        if re.search(r"error: (device|no devices)|device (offline|unauthorized)|protocol fault", text):
            return InstallFailure(
                "ADB_UNAVAILABLE",
                "adb lost the phone: unlock it, accept the 'Allow USB debugging?' prompt and reseat the cable",
                "none",
            )
        if "canceled by user" in text.lower():
            return InstallFailure("INSTALL_CANCELED_BY_USER", _screen_hint(q), "ask_examiner")
        if "Success" in text or not re.search(r"(?i)failure|error|exception", text):
            return None
        return InstallFailure("INSTALL_FAILED", f"adb install failed: {text.strip()[-160:]}", "none")
    if code in ("INSTALL_FAILED_UPDATE_INCOMPATIBLE", "INSTALL_FAILED_SHARED_USER_INCOMPATIBLE"):
        return InstallFailure(
            code,
            "a Collector signed with a different key is already installed (another laptop's build); "
            "it is removed and installed again",
            "reinstall",
        )
    if code in _USER_ACTION_CODES:
        scan = " (Play Protect / 'Verify apps over USB' may be scanning it)" if "VERIFICATION" in code else ""
        return InstallFailure(code, _screen_hint(q) + scan, "ask_examiner")
    if code == "INSTALL_FAILED_INSUFFICIENT_STORAGE":
        return InstallFailure(code, "the phone is out of storage; free some space for the Collector", "none")
    if code == "INSTALL_FAILED_OLDER_SDK":
        return InstallFailure(
            code, f"Android older than 8.0 (API {MIN_COLLECTOR_SDK}) cannot run the Collector; Tier-0 only", "none"
        )
    if code.startswith("INSTALL_PARSE_FAILED") or code == "INSTALL_FAILED_INVALID_APK":
        return InstallFailure(code, "the Collector APK is corrupt or unsigned; rebuild it (apk/: ./gradlew assembleDebug)", "none")
    return InstallFailure(code, f"adb install failed ({code}): {text.strip()[-160:]}", "none")


# ---------------------------------------------------------------------------
# What the examiner is told about this phone
# ---------------------------------------------------------------------------
def describe(sdk: int, release: str = "", quirks: list[str] | None = None) -> dict:
    """The compatibility picture for one phone: whether the Collector can run, whether this
    Android release is inside the audited range, and the version-specific facts that change how
    the engine behaves. Shown in the device check and recorded in the case audit."""
    notes: list[str] = []
    if not sdk:
        notes.append("Android version unknown — the engine tries every permission and falls back where one fails.")
    elif sdk < MIN_COLLECTOR_SDK:
        notes.append(f"API {sdk} is older than the Collector's minimum (API {MIN_COLLECTOR_SDK}): Tier-0 shell collection only.")
    elif sdk < TESTED_SDK_RANGE[0]:
        notes.append("Android 8–9 is supported by the Collector but outside the audited range (Android 10–15).")
    elif sdk > TESTED_SDK_RANGE[1]:
        notes.append(f"Android API {sdk} is newer than the audited range (Android 10–15); check the result against the manifest.")
    if sdk == 29:
        notes.append("Android 10: the shell cannot read the call log through content providers; the Collector supplies it.")
    if sdk >= 30:
        notes.append("Android 11+: storage goes through FUSE, so many small files are slow one by one; the engine streams them as tar.")
    if sdk >= 33:
        notes.append("Android 13+: media access uses READ_MEDIA_* instead of READ_EXTERNAL_STORAGE; those are granted.")
    if "auto_blocker_usb" in (quirks or []):
        notes.append(
            "Samsung Auto Blocker (One UI 6.0+) is on by default and blocks USB commands; if the phone never appears "
            "in adb, turn it off first."
        )
    return {
        "sdk": sdk,
        "android": release,
        "collector_supported": sdk == 0 or sdk >= MIN_COLLECTOR_SDK,
        "audited": TESTED_SDK_RANGE[0] <= sdk <= TESTED_SDK_RANGE[1],
        "notes": notes,
    }
