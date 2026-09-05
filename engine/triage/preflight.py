"""Pre-acquisition device readiness: Developer Options / USB debugging.

Android will not let ANY tool — this one, Cellebrite, Oxygen, anything — talk to a device
over ADB until a human has, on the device's own screen: turned on Developer Options,
turned on USB debugging, and tapped "Allow" on the RSA-fingerprint prompt for this
specific workstation. That sequence needs a finger on the glass; nothing issued from the
computer side can substitute for it, because the entire point of the prompt is that the
computer side is not trusted yet. This module does not pretend otherwise. It:

1. Detects exactly where a connected (or not-yet-connected) device stands in that
   sequence (:func:`detect_connection_state`), and
2. Hands back the brand-specific checklist to finish it (:func:`steps_for_brand`) — the
   generic AOSP steps apply everywhere; each OEM registered in ``config.OEM_QUIRKS`` adds
   its own known extra friction on top, e.g. Xiaomi's separate "USB debugging (Security
   settings)" toggle or OPPO's lock-screen PIN prompt on install.
3. Automates the one step in the whole sequence that IS legitimately scriptable
   (:func:`reassert_developer_options`) — and only because it requires an ADB shell
   session that already exists, which means it can never be the FIRST enable on a device.

See ``apk/README.md`` and ``docs/SETUP.md`` for the same material written for a human
examiner, and ``triage.config.OEM_QUIRKS`` for the quirk flags these steps correspond to.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .adb import Adb, AdbResult


class ConnectionState(str, Enum):
    """Where the target stands between "never touched" and "ready for Tier 0/1"."""

    NO_ADB_BINARY = "no_adb_binary"  # this workstation has no adb to run
    NO_DEVICE = "no_device"  # nothing answering on the bus/network `adb devices` checks
    UNAUTHORIZED = "unauthorized"  # USB debugging is on; the RSA prompt isn't accepted yet
    OFFLINE = "offline"  # the daemon lists it but the transport isn't answering
    READY = "device"  # authorized and answering shell commands


@dataclass
class DeviceReadiness:
    state: ConnectionState
    serial: str = ""
    note: str = ""
    raw_state: str = field(default="")  # the literal `adb devices` token, for debugging


# ---------------------------------------------------------------------------
# Developer Options / USB debugging checklist
# ---------------------------------------------------------------------------
# Generic AOSP sequence — required on every brand, first. Brand entries below are
# ADDITIONAL friction that OEM is known to layer on top, not a replacement for this.
_GENERIC_STEPS: list[str] = [
    "Settings → About phone → tap 'Build number' 7 times, until 'You are now a "
    "developer!' appears",
    "Settings → System → Developer options → turn on 'USB debugging'",
    "Connect the USB cable to this workstation",
    "On the device screen, tap 'Allow' on the 'Allow USB debugging?' prompt — tick "
    "'Always allow from this computer' so the authorization survives a reboot",
]

# Keyed the same way as config.OEM_QUIRKS (lowercase brand/manufacturer).
_XIAOMI_EXTRA = [
    "Settings → Additional settings → Developer options → also enable 'USB debugging "
    "(Security settings)' — this second toggle needs a Mi Account signed in and an "
    "active SIM, not just the switch",
    "Settings → Additional settings → Developer options → enable 'Install via USB'",
    "Turn off battery saver for the acquisition — MIUI/HyperOS kills background apps "
    "aggressively, which can end the collection mid-run",
]
_OPPO_EXTRA = [
    "Have the device owner enter the lock-screen PIN if ColorOS prompts for it during "
    "`adb install`",
    "Keep the collector app in the foreground during collection — ColorOS may kill "
    "background processes between triggers",
]
_VIVO_EXTRA = [
    "Settings → More settings → Developer options → if present, turn off 'Verify apps "
    "over USB' — Funtouch OS/OriginOS can block `adb install` while it's on",
    "Keep the collector app in the foreground — Vivo's i Manager may kill it otherwise",
]

_BRAND_STEPS: dict[str, list[str]] = {
    "xiaomi": _XIAOMI_EXTRA,
    "redmi": _XIAOMI_EXTRA,
    "poco": _XIAOMI_EXTRA,
    "oppo": _OPPO_EXTRA,
    "realme": _OPPO_EXTRA,
    "oneplus": [
        "No extra Developer Options step here, but expect `pm grant` to fail on this "
        "OS — the engine falls back to on-screen permission dialogs during Tier-1 "
        "collection; tap 'Allow' as each one appears",
    ],
    "vivo": _VIVO_EXTRA,
    "iqoo": _VIVO_EXTRA,
    "honor": [
        "Re-authorize the ADB prompt promptly if the session drops — MagicOS times "
        "out the authorization faster than stock Android",
    ],
    "huawei": [
        "AOSP-based HarmonyOS (≤3.x) only. HarmonyOS NEXT has no Android layer and "
        "cannot be reached over standard ADB at all, regardless of Developer Options "
        "state — this is a platform difference, not a setting to find",
    ],
    "samsung": [
        "Data inside Secure Folder stays unreachable even after USB debugging is on "
        "— that's Knox container encryption, not a Developer Options setting",
    ],
}


def steps_for_brand(brand: str) -> list[str]:
    """Full enable-developer-mode checklist for *brand* (case-insensitive).

    Always the generic AOSP sequence first — every brand needs that — then whatever
    extra friction that OEM is known to add. An unrecognised or empty brand gets just
    the generic sequence, which is still correct: it is the fallback baseline every
    Android build follows, OEM skin or not.
    """
    extra = _BRAND_STEPS.get((brand or "").strip().lower(), [])
    return list(_GENERIC_STEPS) + list(extra)


# ---------------------------------------------------------------------------
# Connection-state detection
# ---------------------------------------------------------------------------
def detect_connection_state(adb: Adb) -> DeviceReadiness:
    """Classify what state the target is in from `adb devices`.

    Never raises — a missing adb binary or an empty device list are readiness states
    in their own right, not exceptions to propagate.
    """
    if not adb.available:
        return DeviceReadiness(
            ConnectionState.NO_ADB_BINARY,
            note="no adb binary found on this workstation (checked bundled vendor copy, "
            "$ANDROID_HOME, the default SDK path, and $PATH)",
        )

    devices = Adb.list_devices(adb.adb_path)
    if adb.serial:
        devices = [d for d in devices if d["serial"] == adb.serial]
    if not devices:
        return DeviceReadiness(
            ConnectionState.NO_DEVICE,
            note="no device visible to adb yet — plug in the cable, or Developer "
            "Options / USB debugging has not been turned on on the device",
        )

    d = devices[0]
    serial, raw_state = d["serial"], d["state"]
    if raw_state == "unauthorized":
        return DeviceReadiness(
            ConnectionState.UNAUTHORIZED,
            serial=serial,
            raw_state=raw_state,
            note="device is visible but not authorized — tap 'Allow' on the 'Allow "
            "USB debugging?' prompt on the device screen (it may be hidden behind the "
            "lock screen; unlock the device first)",
        )
    if raw_state == "offline":
        return DeviceReadiness(
            ConnectionState.OFFLINE,
            serial=serial,
            raw_state=raw_state,
            note="the adb daemon lists the device but the transport isn't answering — "
            "try reseating the cable or `adb kill-server` followed by `adb devices`",
        )
    if raw_state == "device":
        return DeviceReadiness(ConnectionState.READY, serial=serial, raw_state=raw_state)
    return DeviceReadiness(
        ConnectionState.NO_DEVICE,
        serial=serial,
        raw_state=raw_state,
        note=f"unrecognised adb state '{raw_state}' — treating as not ready",
    )


# ---------------------------------------------------------------------------
# The one legitimately-automatable step
# ---------------------------------------------------------------------------
def reassert_developer_options(adb: Adb) -> tuple[AdbResult, AdbResult]:
    """Re-enable Developer Options + USB debugging via `settings put global`.

    This is the only step in the whole sequence that can be scripted — and only
    because it requires an ADB shell session that already exists. It exists for the
    case where an OEM (MIUI in particular) silently flips Developer Options back off
    between sessions on the *same* device: the toggle disappears from the Settings UI,
    but the underlying globals can be restored without walking the examiner back
    through the tap-Build-number-7-times ritual. It CANNOT be the first enable on a
    device that has never had USB debugging on — there is no ADB session to run it
    over yet, on any brand, ever. Both commands are state-changing; the caller is
    responsible for logging them with ``alters_device=True`` like every other Tier-1
    step (see ``pipeline._log_tier1_step``).
    """
    dev_opts = adb.shell("settings put global development_settings_enabled 1")
    adb_enabled = adb.shell("settings put global adb_enabled 1")
    return dev_opts, adb_enabled
