"""Android Bluetooth bond store parser — ``/data/misc/bluedroid/bt_config.conf``.

Forensic purpose
----------------
``bt_config.conf`` is the persistent Bluetooth bond store written by the Android
Bluetooth stack (``system/gd/storage/config_cache.cc``).  It is owned by
``bluetooth:bluetooth`` with mode 0660, so it is **Tier-2 (root) evidence**: it
does not appear in an ADB backup or in a logical extraction.

Only *persistent* device sections reach disk — a section is written once the
device acquires a "link key property" (``LinkKey``, ``LE_KEY_PENC``,
``LE_KEY_PID``, ``LE_KEY_PCSRK``, ``LE_KEY_LENC``, ``LE_KEY_LCSRK``).  So the
presence of a device section is evidence that **a bond existed** with a peer
claiming that address.

The single biggest overstatement risk in the whole tool
-------------------------------------------------------
The per-device ``Timestamp`` key is written by ``btif_storage.cc::prop2cfg()``
as ``time(NULL)`` every time the stack persists a device-property record that
includes ``BT_PROPERTY_BDADDR`` — dominantly from the discovery-result handler
in ``btif_dm.cc``.  It is therefore the **last time a bond/device-property
record was written for that address**, which in the common case lands within
seconds of pairing and is bumped forward again by any later discovery scan.

It is *not* a connection time, *not* a last-connected time, *not* a last-seen
time, and *not* proof of co-location at any moment other than the single
instant a radio frame was recorded.  Published forensic write-ups disagree on
this ("First Connected Timestamp" in ALEAPP, "device setup" in DFIR Review) —
this module deliberately refuses every one of those labels and emits
:data:`TIMESTAMP_MEANING` on every row.  For a real connection time you must
corroborate with the sources in :data:`CORROBORATION_SOURCES`.

Other limitations honoured here
-------------------------------
* **Key material is never emitted.**  ``LinkKey`` and every ``LE_KEY_*`` blob is
  reduced to a boolean / a key *name*.  The raw hex never reaches ``to_dict()``.
* **Common Criteria (NIAP) mode** replaces secret values with the literal string
  ``encrypted``.  That still proves the bond; it only means the key value is
  unrecoverable.  Never report such a device as "not paired".
* **Malformed lines are recovered, not fatal.**  AOSP discards the whole file on
  a bad line; a forensic parser must not.  Bad lines are reported in
  ``parse_errors``.
* **Absent != inaccessible.**  A missing ``bt_config.conf`` is reported as
  "not found or not readable (root required)", never as "no Bluetooth pairings".
* **OUI lookup is refused for random addresses** (see :mod:`triage.parsers.oui`).
* The peer-supplied ``Name`` is unauthenticated; only ``Aliase`` (the AOSP
  misspelling of "alias") is set by a human on the subject device.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from ..config import Confidence
from ..models import Serialisable, TimelineEvent


# ---------------------------------------------------------------------------
# Device paths
# ---------------------------------------------------------------------------

#: Candidate on-device locations for the bond store, richest first.  These are
#: *candidates* — the pipeline probes them, nothing here is assumed to exist.
BT_CONFIG_PATHS: list[str] = [
    "/data/misc/bluedroid/bt_config.conf",
    # Previous generation of the file. Android <= 12 wrote it on every save
    # (`rename(CONFIG_FILE_PATH, CONFIG_BACKUP_PATH)` in btif_config.cc), so
    # diffing .bak against .conf can recover a bond that was since removed or a
    # Timestamp that was since overwritten.
    "/data/misc/bluedroid/bt_config.bak",
    # Crash-interrupted write: current AOSP writes bt_config.conf.new then
    # rename()s it. A stray .new means the stack died mid-save.
    "/data/misc/bluedroid/bt_config.conf.new",
    # Pre-KitKat / legacy store, only relevant when [Info] FileSource = Legacy.
    "/data/misc/bluedroid/bt_config.xml",
    # Common Criteria mode sidecars (not parseable — presence is the finding).
    "/data/misc/bluedroid/bt_config.conf.encrypted",
    "/data/misc/bluedroid/bt_config.checksum.encrypted",
    "/data/misc/bluedroid/bt_config.conf.encrypted-checksum",
    "/data/misc/bluedroid/bt_config.bak.encrypted",
    "/data/misc/bluedroid/bt_config.bak.encrypted-checksum",
    # Some vendor forks relocate the store.
    "/data/misc/bluetooth/bt_config.conf",
]

#: Where a *real* connection time can be found. Emitted in the summary so the
#: examiner is pointed at corroboration instead of over-reading this file.
CORROBORATION_SOURCES: list[str] = [
    "/data/misc/bluetooth/logs/btsnoop_hci.log (HCI capture; exact connect/disconnect times, only if the HCI snoop developer option was enabled)",
    "/data/data/com.android.bluetooth/databases/btopp.db (OPP file transfers — a transfer row proves an active link)",
    "/data/data/com.google.android.gms/databases/carservicedata.db (Android Auto — real first-connection time to a vehicle)",
    "/data/user_de/0/com.android.bluetooth/databases/bluetooth_db (metadata.last_active_time is a monotonic connection COUNTER, an ordinal — never a date)",
    "/data/misc/bluedroiddump/subBuffer.log (Samsung; call-audio routing, local time, no year)",
    "Vehicle-side infotainment module (definitive for pair/connect times)",
]

#: Attached verbatim to every bond row. Do not paraphrase this into a shorter,
#: friendlier label — the shorter labels are exactly what is wrong elsewhere.
TIMESTAMP_MEANING: str = (
    "Bond record last written (UTC). Unix epoch seconds from "
    "/data/misc/bluedroid/bt_config.conf, written by the Android Bluetooth stack "
    "when a device-property record is persisted for this address — typically at "
    "pairing, and again on any later discovery scan that finds the same address. "
    "It is NOT a connection time, NOT a last-connected or last-seen time, and it "
    "does NOT establish that the two devices were linked, in use, or co-located "
    "at any other moment. Derived from the handset's own clock, which may have "
    "been wrong or unset. Corroborate against btsnoop_hci.log, btopp.db or "
    "carservicedata.db before asserting a connection."
)

#: Verbatim wording for a Common Criteria (NIAP) mode file.
COMMON_CRITERIA_NOTE: str = (
    "Bluetooth Common Criteria (NIAP) mode is enabled on this device. Pairing "
    "secrets (LinkKey, LE_KEY_PENC, LE_KEY_PID, LE_KEY_LID, LE_KEY_PCSRK, "
    "LE_KEY_LENC, LE_KEY_LCSRK) have been replaced on disk with the literal value "
    "'encrypted' and moved to /data/misc/bluedroid/bt_config.conf.encrypted, "
    "encrypted with AES-256-GCM under a non-exportable AndroidKeyStore key (alias "
    "'bluetooth-key-encrypted'). These values CANNOT be recovered from this image. "
    "The bond itself is still proven by the presence of the device section; all "
    "non-secret metadata was read normally."
)


# ---------------------------------------------------------------------------
# Key classification
# ---------------------------------------------------------------------------

#: Values that must never leave this module. Reduced to booleans / names only.
_SECRET_KEYS: frozenset[str] = frozenset(
    {
        "LinkKey",
        "LE_KEY_PENC",
        "LE_KEY_PID",
        "LE_KEY_PCSRK",
        "LE_KEY_LENC",
        "LE_KEY_LCSRK",
        "LE_KEY_LID",
        "LE_LOCAL_KEY_IRK",
        "LE_LOCAL_KEY_IR",
        "LE_LOCAL_KEY_DHK",
        "LE_LOCAL_KEY_ER",
        "Salt256Bit",
    }
)

#: The six properties whose presence causes AOSP to persist a device section at
#: all, i.e. the on-disk definition of "a bond existed".
_LINK_KEY_PROPERTIES: tuple[str, ...] = (
    "LinkKey",
    "LE_KEY_PENC",
    "LE_KEY_PID",
    "LE_KEY_PCSRK",
    "LE_KEY_LENC",
    "LE_KEY_LCSRK",
)

_LE_KEY_ORDER: tuple[str, ...] = (
    "LE_KEY_PENC",
    "LE_KEY_PID",
    "LE_KEY_PCSRK",
    "LE_KEY_LENC",
    "LE_KEY_LCSRK",
    "LE_KEY_LID",
)

#: Expected hex length of each LE SMP key blob (sizeof(struct) * 2). Used only
#: as a validity check — a wrong length is reported, never "corrected".
_LE_KEY_HEX_LEN: dict[str, int] = {
    "LE_KEY_PENC": 56,
    "LE_KEY_PID": 46,
    "LE_KEY_PCSRK": 48,
    "LE_KEY_LENC": 40,
    "LE_KEY_LCSRK": 48,
    "LE_KEY_LID": 46,
}

#: Keys we decode into typed fields. Anything not here and not a secret ends up
#: in ``unknown_keys`` — vendor forks add their own and we never drop them.
_KNOWN_DEVICE_KEYS: frozenset[str] = frozenset(
    {
        "Name",
        "Aliase",
        "ModelName",
        "Appearance",
        "DevClass",
        "DevType",
        "AddrType",
        "Timestamp",
        "LinkKeyType",
        "PinLength",
        "SecureConnectionsSupported",
        "MaxSessionKeySize",
        "Restricted",
        "Manufacturer",
        "LmpVer",
        "LmpSubVer",
        "VendorIdSource",
        "VendorId",
        "ProductId",
        "ProductVersion",
        "Version",
        "Service",
        "ServiceLe",
    }
) | _SECRET_KEYS

_ENCRYPTED_PLACEHOLDER = "encrypted"


# ---------------------------------------------------------------------------
# Enumerations (AOSP-sourced)
# ---------------------------------------------------------------------------

_DEV_TYPE_LABELS: dict[int, str] = {
    1: "bredr_classic",
    2: "le",
    3: "dual_bredr_le",
}

_ADDR_TYPE_LABELS: dict[int, str] = {
    0: "public",
    1: "random",
    2: "public_identity",
    3: "random_identity",
    0xFF: "anonymous",
}

_SCAN_MODE_LABELS: dict[int, str] = {
    0: "none",
    1: "connectable",
    2: "connectable_discoverable",
}

# hcidefs.h. 3 (Debug Combination) is a security red flag: the well-known debug
# key means the link was trivially decryptable.
_LINK_KEY_TYPE_LABELS: dict[int, str] = {
    0: "combination_legacy",
    1: "local_unit_reserved",
    2: "remote_unit",
    3: "debug_combination",
    4: "unauthenticated_p192_ssp_justworks",
    5: "authenticated_p192_ssp_mitm",
    6: "changed_combination",
    7: "unauthenticated_p256_secure_connections",
    8: "authenticated_p256_secure_connections",
}

# Class-of-Device major device classes (Bluetooth Assigned Numbers, Baseband).
_COD_MAJOR: dict[int, str] = {
    0: "miscellaneous",
    1: "computer",
    2: "phone",
    3: "lan_network_access_point",
    4: "audio_video",
    5: "peripheral",
    6: "imaging",
    7: "wearable",
    8: "toy",
    9: "health",
    31: "uncategorized",
}

_COD_MINOR: dict[int, dict[int, str]] = {
    1: {1: "desktop", 2: "server", 3: "laptop", 4: "handheld_pda", 5: "palm_pda", 6: "wearable_computer", 7: "tablet"},
    2: {1: "cellular", 2: "cordless", 3: "smartphone", 4: "wired_modem_voice_gateway", 5: "isdn_access"},
    4: {
        1: "wearable_headset", 2: "hands_free", 4: "microphone", 5: "loudspeaker",
        6: "headphones", 7: "portable_audio", 8: "car_audio", 9: "set_top_box",
        10: "hifi_audio", 11: "vcr", 12: "video_camera", 13: "camcorder",
        14: "video_monitor", 15: "video_display_loudspeaker", 16: "video_conferencing",
        18: "gaming_toy",
    },
    7: {1: "wristwatch", 2: "pager", 3: "jacket", 4: "helmet", 5: "glasses"},
    8: {1: "robot", 2: "vehicle", 3: "doll_action_figure", 4: "controller", 5: "game"},
    9: {
        1: "blood_pressure_monitor", 2: "thermometer", 3: "weighing_scale",
        4: "glucose_meter", 5: "pulse_oximeter", 6: "heart_pulse_rate_monitor",
        7: "health_data_display", 8: "step_counter", 9: "body_composition_analyser",
        10: "peak_flow_monitor", 11: "medication_monitor", 12: "knee_prosthesis",
        13: "ankle_prosthesis", 14: "generic_health_manager", 15: "personal_mobility_device",
    },
}

# Major 5 (Peripheral): minor bits 5..4 are keyboard/pointing flags and bits
# 3..0 select the device kind, so it needs its own decoder.
_COD_PERIPHERAL_KIND: dict[int, str] = {
    0: "uncategorized",
    1: "joystick",
    2: "gamepad",
    3: "remote_control",
    4: "sensing_device",
    5: "digitiser_tablet",
    6: "card_reader",
    7: "digital_pen",
    8: "handheld_scanner",
    9: "handheld_gestural_input",
}

# bits 23..13 of the CoD, a bitfield (several may be set at once).
_COD_SERVICE_BITS: dict[int, str] = {
    13: "limited_discoverable_mode",
    14: "le_audio",
    15: "reserved_bit15",
    16: "positioning",
    17: "networking",
    18: "rendering",
    19: "capturing",
    20: "object_transfer",
    21: "audio",
    22: "telephony",
    23: "information",
}

#: Bluetooth SIG Company Identifiers — a deliberately small, well-known subset.
#: An ID not in this map yields ``None``; we do not guess assignee names.
#: NOTE: this identifies the peer's *controller silicon* vendor, not the brand.
_SIG_COMPANY_IDS: dict[int, str] = {
    0: "Ericsson Technology Licensing",
    1: "Nokia Mobile Phones",
    2: "Intel Corp.",
    3: "IBM Corp.",
    4: "Toshiba Corp.",
    5: "3Com",
    6: "Microsoft",
    7: "Lucent",
    8: "Motorola",
    9: "Infineon Technologies AG",
    10: "Cambridge Silicon Radio (CSR)",
    13: "Texas Instruments Inc.",
    15: "Broadcom Corporation",
    29: "Qualcomm",
    76: "Apple, Inc.",
    89: "Nordic Semiconductor ASA",
    93: "Realtek Semiconductor Corporation",
    117: "Samsung Electronics Co. Ltd.",
    224: "Google",
}

#: Well-known 16-bit service UUIDs, rendered from the 128-bit base form.
_SERVICE_UUID_NAMES: dict[str, str] = {
    "1105": "OBEX Object Push (OPP)",
    "1106": "OBEX File Transfer (FTP)",
    "110a": "Audio Source (A2DP SRC)",
    "110b": "Audio Sink (A2DP SNK)",
    "110c": "A/V Remote Control Target",
    "110e": "A/V Remote Control (AVRCP)",
    "1112": "Headset Audio Gateway",
    "111e": "Handsfree (HFP HF)",
    "111f": "Handsfree Audio Gateway (HFP AG)",
    "1124": "Human Interface Device (HID)",
    "112f": "Phonebook Access Server (PBAP PSE)",
    "1132": "Message Access Server (MAP MAS)",
    "1200": "PnP Information (Device ID)",
    "180a": "Device Information Service",
    "180f": "Battery Service",
}

_MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}$")
_HEX_BLOB_RE = re.compile(r"^[0-9a-fA-F]{16,}$")
_BASE64ISH_RE = re.compile(r"^[A-Za-z0-9+/]{24,}={0,2}$")
_SECRETISH_KEY_RE = re.compile(r"(key|ltk|irk|csrk|secret|salt|passkey)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _to_int(raw: Optional[str]) -> Optional[int]:
    """Parse an AOSP integer property (signed 32-bit decimal). None on failure."""
    if raw is None:
        return None
    try:
        return int(raw.strip())
    except (ValueError, AttributeError):
        return None


def _epoch_to_iso(epoch: int) -> str:
    """Epoch seconds -> ISO-8601 UTC with a trailing Z."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _is_valid_mac(section: str) -> bool:
    """``ConfigCache::IsDeviceSection()`` — a section is a device iff it is a MAC."""
    return bool(_MAC_RE.match(section.strip()))


def _sanitise_unknown_value(key: str, value: str) -> str:
    """Redact anything that could be key material before it enters output.

    Vendor forks invent their own keys, and we cannot know whether a given
    unknown key holds a secret.  Redaction is driven by the *value's shape*
    rather than by the key name alone (a name like ``UnknownVendorKey`` says
    nothing about its content, and over-redacting would destroy evidence):

    * any run of >= 16 hex characters is treated as a binary blob;
    * a base64-looking value under a key whose name smells like key material is
      treated as a wrapped secret.

    The key name and the fact of its presence are always preserved, so a
    redaction is visible in the output rather than a silent deletion.
    """
    value = value.strip()
    if not value:
        return ""
    if _HEX_BLOB_RE.match(value):
        return f"<redacted binary blob: {len(value)} hex chars>"
    if _SECRETISH_KEY_RE.search(key) and _BASE64ISH_RE.match(value):
        return f"<redacted: key-like property, {len(value)} chars>"
    return value[:256]


def _decode_services(raw: Optional[str]) -> list[str]:
    """Split a space-separated 128-bit UUID list and name the well-known ones."""
    if not raw:
        return []
    out: list[str] = []
    for uuid in raw.split():
        uuid = uuid.strip().lower()
        if not uuid:
            continue
        short = uuid[4:8] if len(uuid) == 36 and uuid.startswith("0000") else ""
        name = _SERVICE_UUID_NAMES.get(short)
        out.append(f"{uuid} ({name})" if name else uuid)
    return out


# ---------------------------------------------------------------------------
# Class of Device
# ---------------------------------------------------------------------------


def decode_device_class(raw: Any) -> dict[str, Any]:
    """Decode a 24-bit Class-of-Device value (stored as a DECIMAL int by AOSP).

    Returns ``{"raw", "hex", "major", "minor", "services"}``.

    A value of 0 or an unparseable value yields ``major = "unknown"`` — the CoD
    was never learned (common for LE-only peers).  It is NOT reported as
    "miscellaneous", which is a real CoD major class and would be a fabricated
    classification.

    Caveat for the reader: ``btif_dm.cc`` keeps the *previously stored* CoD when
    a peer reports COD_UNCLASSIFIED, so DevClass can be staler than the rest of
    the record and must not be treated as contemporaneous with ``Timestamp``.
    """
    value: Optional[int]
    if isinstance(raw, bool):  # bool is an int subclass; refuse it explicitly
        value = None
    elif isinstance(raw, int):
        value = raw
    else:
        value = _to_int(raw if isinstance(raw, str) else None)

    if value is None or value <= 0:
        return {
            "raw": value if value is not None else 0,
            "hex": "",
            "major": "unknown",
            "minor": "unknown",
            "services": [],
        }

    value &= 0xFFFFFF
    major_code = (value >> 8) & 0x1F
    minor_code = (value >> 2) & 0x3F
    major = _COD_MAJOR.get(major_code, f"reserved_{major_code}")

    if major_code == 5:  # Peripheral: split flag bits from the device kind
        flags = (minor_code >> 4) & 0x03
        kind = _COD_PERIPHERAL_KIND.get(minor_code & 0x0F, f"minor_{minor_code}")
        flag_label = {0: "", 1: "keyboard", 2: "pointing_device", 3: "keyboard_pointing_combo"}[flags]
        minor = f"{flag_label}+{kind}" if flag_label and kind != "uncategorized" else (flag_label or kind)
    elif major_code == 6:  # Imaging: the minor is a bitfield at bits 7..4
        bits = []
        if minor_code & 0x04:
            bits.append("display")
        if minor_code & 0x08:
            bits.append("camera")
        if minor_code & 0x10:
            bits.append("scanner")
        if minor_code & 0x20:
            bits.append("printer")
        minor = "+".join(bits) if bits else f"minor_{minor_code}"
    else:
        minor = _COD_MINOR.get(major_code, {}).get(minor_code, f"minor_{minor_code}")

    services = [name for bit, name in sorted(_COD_SERVICE_BITS.items()) if value & (1 << bit)]

    return {
        "raw": value,
        "hex": f"0x{value:06X}",
        "major": major,
        "minor": minor,
        "services": services,
    }


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class BluetoothBond(Serialisable):
    """One bonded-device section from ``bt_config.conf``.

    Every instance is BOND evidence: it proves a pairing record exists (or
    existed) for a peer claiming ``address``.  It is never CONTACT evidence.
    No key material is stored on this object — only whether keys were present.
    """

    address: str
    name: str = ""
    dev_class_raw: Optional[int] = None
    dev_class_label: str = "unknown"
    dev_type: Optional[int] = None
    dev_type_label: str = "unknown"
    addr_type: Optional[int] = None
    addr_type_label: str = "unknown"
    vendor: Optional[str] = None
    manufacturer_id: Optional[int] = None
    product_id: Optional[int] = None
    version: Optional[int] = None
    bond_timestamp_raw: Optional[int] = None
    bond_timestamp: Optional[str] = None
    timestamp_meaning: str = TIMESTAMP_MEANING
    has_link_key: bool = False
    link_key_type: Optional[int] = None
    pin_length: Optional[int] = None
    le_key_types: list[str] = field(default_factory=list)
    services: list[str] = field(default_factory=list)
    source_file: str = ""
    confidence: str = Confidence.LIVE.value
    caveats: list[str] = field(default_factory=list)

    # --- additional forensic context (not key material) ---
    alias: str = ""  # AOSP key "Aliase" — set by a HUMAN, high probative value
    model_name: str = ""
    appearance: Optional[int] = None
    address_is_valid: bool = True
    random_address_subtype: str = ""
    vendor_lookup_reason: str = ""
    manufacturer_label: Optional[str] = None
    link_key_type_label: str = ""
    link_key_encrypted: bool = False
    le_keys_encrypted: list[str] = field(default_factory=list)
    le_keys_empty: list[str] = field(default_factory=list)
    le_identity_address: Optional[str] = None
    le_identity_addr_type: Optional[int] = None
    services_le: list[str] = field(default_factory=list)
    secure_connections: Optional[int] = None
    restricted: bool = False
    has_bond_material: bool = False
    unknown_keys: dict[str, str] = field(default_factory=dict)
    #: Constant. This file can never on its own support a contact/co-location
    #: assertion, so the class of the evidence is fixed here.
    evidence_class: str = "bond_evidence"


@dataclass
class BluetoothAdapterInfo(Serialisable):
    """The ``[Info]`` + ``[Adapter]`` + ``[Metrics]`` blocks: the subject device."""

    address: str = ""
    vendor: Optional[str] = None
    scan_mode: Optional[int] = None
    discovery_timeout: Optional[int] = None
    file_source: str = ""
    time_created: str = ""
    le_local_irk_present: bool = False
    source_file: str = ""
    caveats: list[str] = field(default_factory=list)

    name: str = ""
    scan_mode_label: str = ""
    local_io_caps: Optional[int] = None
    local_keys_present: list[str] = field(default_factory=list)
    metrics_salt_present: bool = False
    #: TimeCreated is written with std::localtime and carries NO timezone, so it
    #: cannot be normalised to UTC. Kept as the literal string plus this note.
    time_created_note: str = (
        "[Info] TimeCreated is LOCAL time with no timezone or offset "
        "(std::put_time(std::localtime(...))). It records when the Bluetooth "
        "config file itself was created — effectively first stack init after a "
        "factory reset or Bluetooth data wipe — not device manufacture, not "
        "first boot, and not comparable to the per-device UTC epoch Timestamps "
        "without knowing the handset's timezone at that moment."
    )


# ---------------------------------------------------------------------------
# INI reader (AOSP LegacyConfigFile::Read semantics, but recovering)
# ---------------------------------------------------------------------------


def _read_ini(text: str) -> tuple[list[tuple[str, dict[str, str]]], list[dict[str, Any]], list[str]]:
    """Tokenise bt_config.conf text.

    Returns ``(sections, parse_errors, duplicate_notes)`` where ``sections`` is
    an ordered list of ``(name, {key: value})``.

    Follows ``legacy_config_file.cc``: trim the whole line, skip empty / NUL /
    ``#`` lines, split on the FIRST ``=`` only (so a device name containing
    " = " survives), last duplicate key wins.  Unlike AOSP — which discards the
    entire file on a malformed line — a bad line is recorded and skipped.
    """
    sections: list[tuple[str, dict[str, str]]] = []
    index: dict[str, dict[str, str]] = {}
    errors: list[dict[str, Any]] = []
    duplicates: list[str] = []
    current: Optional[str] = None

    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip().strip("\x00").strip()
        if not line:
            continue
        if line.startswith("#"):
            continue

        if line.startswith("["):
            if not line.endswith("]") or len(line) < 3:
                errors.append(
                    {
                        "line_no": line_no,
                        "raw": raw_line[:200],
                        "reason": "unterminated section header (AOSP would discard the whole file here)",
                    }
                )
                continue
            current = line[1:-1].strip()
            if current not in index:
                bucket: dict[str, str] = {}
                index[current] = bucket
                sections.append((current, bucket))
            continue

        if "=" not in line:
            errors.append(
                {
                    "line_no": line_no,
                    "raw": raw_line[:200],
                    "reason": "no '=' separator (AOSP would discard the whole file here)",
                }
            )
            continue

        key, _, value = line.partition("=")  # FIRST '=' only
        key = key.strip()
        value = value.strip()
        if not key:
            errors.append({"line_no": line_no, "raw": raw_line[:200], "reason": "empty key"})
            continue
        if current is None:
            errors.append(
                {"line_no": line_no, "raw": raw_line[:200], "reason": "property before any section header"}
            )
            continue

        bucket = index[current]
        if key in bucket and bucket[key] != value:
            # ConfigCache::SetProperty overwrites, so the last value is what the
            # stack used. Both are reported; the conflict is itself a finding.
            duplicates.append(
                f"[{current}] duplicate key '{key}' with differing values at line {line_no}; "
                "AOSP semantics are last-wins, so the later value is the effective one"
            )
        bucket[key] = value

    return sections, errors, duplicates


# ---------------------------------------------------------------------------
# Encryption detection
# ---------------------------------------------------------------------------

_ENCRYPTED_SIBLINGS: tuple[str, ...] = (
    "bt_config.conf.encrypted",
    "bt_config.checksum.encrypted",
    "bt_config.conf.encrypted-checksum",
    "bt_config.bak.encrypted",
    "bt_config.bak.encrypted-checksum",
)


def detect_encrypted_config(path: Any) -> bool:
    """True if this bond store is (or was) protected by Common Criteria mode.

    Three independent signals, any one of which is sufficient:

    1. a sibling ``*.encrypted`` / ``*.encrypted-checksum`` file exists;
    2. any value inside the file is the literal string ``encrypted``;
    3. the file does not parse as bt_config INI at all (no section headers) —
       encrypted, corrupt, or simply not a bond store.

    Signal 3 is reported as True on purpose: the honest conclusion for an
    unreadable bond store is "content not recoverable", never "no bonds".
    """
    try:
        p = Path(path)
    except TypeError:
        return False

    try:
        parent = p.parent
        for sibling in _ENCRYPTED_SIBLINGS:
            if (parent / sibling).exists():
                return True
        if parent.is_dir():
            for child in parent.iterdir():
                nm = child.name
                if nm.startswith("bt_config") and (".encrypted" in nm or nm.endswith("-checksum")):
                    return True
    except OSError:
        pass

    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return False

    if not text.strip():
        return False

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        _key, _, value = stripped.partition("=")
        if value.strip() == _ENCRYPTED_PLACEHOLDER:
            return True

    has_section = any(l.strip().startswith("[") and l.strip().endswith("]") for l in text.splitlines())
    return not has_section


# ---------------------------------------------------------------------------
# Section -> dataclass
# ---------------------------------------------------------------------------


def _build_adapter(
    info: dict[str, str],
    adapter: dict[str, str],
    metrics: dict[str, str],
    source_file: str,
) -> BluetoothAdapterInfo:
    caveats: list[str] = []

    address = adapter.get("Address", "").strip()
    from .oui import lookup_vendor, normalise_mac  # local import: keeps module import cheap

    vendor = None
    if address:
        # The adapter is BR/EDR-capable local hardware, so its stored address is
        # public unless it is the all-zero placeholder.
        vendor = lookup_vendor(address, addr_type=0)
        if not normalise_mac(address):
            caveats.append(f"[Adapter] Address '{address}' is not a valid 48-bit address")
        elif normalise_mac(address) == "00:00:00:00:00:00":
            caveats.append(
                "[Adapter] Address is all-zero — the local controller address had not been read "
                "when the file was written; no OUI lookup performed"
            )

    scan_mode = _to_int(adapter.get("ScanMode"))
    scan_label = _SCAN_MODE_LABELS.get(scan_mode, "") if scan_mode is not None else ""
    if scan_mode is not None and not scan_label:
        scan_label = f"unrecognised (raw {scan_mode})"
        caveats.append(
            f"[Adapter] ScanMode {scan_mode} is outside the AOSP 0/1/2 enum — reported raw, not guessed"
        )

    local_keys = [k for k in ("LE_LOCAL_KEY_IRK", "LE_LOCAL_KEY_IR", "LE_LOCAL_KEY_DHK", "LE_LOCAL_KEY_ER") if adapter.get(k)]
    for k in ("LE_LOCAL_KEY_IRK", "LE_LOCAL_KEY_IR", "LE_LOCAL_KEY_DHK", "LE_LOCAL_KEY_ER"):
        if k in adapter and not adapter[k].strip():
            caveats.append(f"[Adapter] {k} is present but empty")
        elif adapter.get(k, "").strip() == _ENCRYPTED_PLACEHOLDER:
            caveats.append(
                f"[Adapter] {k} = 'encrypted'. AOSP's own Common Criteria encrypt list does NOT include "
                "LE_LOCAL_KEY_* — this is a vendor-fork extension. The value is not recoverable offline."
            )

    file_source = info.get("FileSource", "").strip()
    if file_source == "Legacy":
        caveats.append(
            "[Info] FileSource = Legacy — this config was transcoded from the older bt_config.xml, "
            "so bond records may predate the file's own TimeCreated"
        )
    elif not file_source:
        caveats.append(
            "[Info] FileSource absent — per btif_config.cc this means an existing bt_config.conf was "
            "loaded successfully (the key is only written for 'Empty' or 'Legacy' origins)"
        )

    time_created = info.get("TimeCreated", "").strip()

    return BluetoothAdapterInfo(
        address=address,
        vendor=vendor,
        scan_mode=scan_mode,
        discovery_timeout=_to_int(adapter.get("DiscoveryTimeout")),
        file_source=file_source,
        time_created=time_created,
        le_local_irk_present=bool(adapter.get("LE_LOCAL_KEY_IRK", "").strip()),
        source_file=source_file,
        caveats=caveats,
        name=adapter.get("Name", ""),
        scan_mode_label=scan_label,
        local_io_caps=_to_int(adapter.get("LocalIOCaps")),
        local_keys_present=local_keys,
        metrics_salt_present=bool(metrics.get("Salt256Bit", "").strip()),
    )


def _decode_le_identity(pid_hex: str) -> tuple[Optional[str], Optional[int]]:
    """Extract the peer identity address from an ``LE_KEY_PID`` blob.

    Layout: ``irk[16] | identity_addr_type[1] | identity_addr[6]`` = 23 bytes.
    Only the trailing 7 bytes are used; the IRK itself is discarded immediately
    and never stored.  Returns ``(address, addr_type)`` or ``(None, None)``.
    """
    value = pid_hex.strip().lower()
    if len(value) != _LE_KEY_HEX_LEN["LE_KEY_PID"]:
        return None, None
    try:
        blob = bytes.fromhex(value)
    except ValueError:
        return None, None
    if len(blob) != 23:
        return None, None
    addr_type = blob[16]
    addr = ":".join(f"{b:02x}" for b in blob[17:23])
    return addr.upper(), addr_type


def _build_bond(section: str, props: dict[str, str], source_file: str, confidence: str) -> BluetoothBond:
    """Turn one device section into a :class:`BluetoothBond`. Never raises."""
    from .oui import lookup_reason, lookup_vendor, normalise_mac, random_address_subtype

    caveats: list[str] = []
    address_norm = normalise_mac(section)
    address = address_norm or section
    if not address_norm:
        caveats.append(f"section name '{section}' is not a valid 48-bit address")

    dev_type = _to_int(props.get("DevType"))
    addr_type = _to_int(props.get("AddrType"))

    # AddrType is routinely absent on pure BR/EDR entries; BR/EDR addresses are
    # always public, so we may treat an absent AddrType on DevType=1 as public.
    effective_addr_type = addr_type
    if addr_type is None:
        if dev_type == 1:
            effective_addr_type = 0
            caveats.append(
                "AddrType absent with DevType=1 (BR/EDR) — BR/EDR addresses are always public, "
                "so the address is treated as public for OUI purposes"
            )
        else:
            caveats.append(
                "AddrType absent and device is not BR/EDR-only — address type unknown, "
                "OUI lookup suppressed rather than guessed"
            )

    vendor = lookup_vendor(address, addr_type=effective_addr_type) if effective_addr_type is not None else None
    _applicable, reason = lookup_reason(address, addr_type=effective_addr_type)
    if effective_addr_type is None:
        reason = "address type unknown — lookup suppressed"

    subtype = ""
    if addr_type in (1, 3):
        subtype = random_address_subtype(address)
        caveats.append(
            f"AddrType={addr_type} (random) — address is a {subtype} random address generated by the "
            "peer. It carries no IEEE registry meaning, so no vendor was resolved. A rotating "
            "resolvable private address also means this value may not identify the peer at any other time."
        )

    # --- Timestamp: the whole honesty problem lives here ---
    ts_raw = _to_int(props.get("Timestamp"))
    ts_iso: Optional[str] = None
    if "Timestamp" not in props:
        caveats.append(
            "no Timestamp key — the bond-record write time was never recorded for this device "
            "(common for LE bonds that never produced a classic inquiry result). Not recorded is "
            "NOT the same as unknown-because-deleted, and the file mtime must NOT be substituted."
        )
    elif ts_raw is None:
        caveats.append(f"Timestamp value {props.get('Timestamp')!r} is not an integer — not rendered")
    elif ts_raw == 0:
        caveats.append(
            "Timestamp = 0 (epoch zero) — the handset clock was not set when the record was written. "
            "Deliberately NOT rendered as 1970-01-01."
        )
    elif ts_raw < 0:
        corrected = ts_raw + 2**32
        ts_iso = _epoch_to_iso(corrected)
        caveats.append(
            f"Timestamp {ts_raw} is negative: AOSP casts time(NULL) to a signed 32-bit int, so values "
            f"after 2038-01-19T03:14:07Z wrap. Corrected by +2^32 to {corrected} ({ts_iso}); "
            "int32_wrap = true."
        )
    else:
        ts_iso = _epoch_to_iso(ts_raw)
        if ts_raw >= 2147483647 - 86400:
            caveats.append(
                "Timestamp is at or within a day of the signed 32-bit ceiling (2038-01-19T03:14:07Z) — "
                "treat as implausible / near-wrap rather than a real date."
            )

    # --- key material: presence only, values never retained ---
    link_key_raw = props.get("LinkKey")
    link_key_encrypted = (link_key_raw or "").strip() == _ENCRYPTED_PLACEHOLDER
    has_link_key = bool(link_key_raw and link_key_raw.strip())
    if link_key_encrypted:
        caveats.append(
            "LinkKey = 'encrypted' (Common Criteria / NIAP mode). The bond IS proven; the key value "
            "is held in bt_config.conf.encrypted under a non-exportable AndroidKeyStore key and "
            "cannot be recovered from this image."
        )
    elif has_link_key and len(link_key_raw.strip()) != 32:
        caveats.append(
            f"LinkKey is {len(link_key_raw.strip())} hex chars, expected 32 (16 bytes) — record may be "
            "truncated or carved"
        )

    le_key_types: list[str] = []
    le_encrypted: list[str] = []
    le_empty: list[str] = []
    for key in _LE_KEY_ORDER:
        if key not in props:
            continue
        value = props[key].strip()
        if not value:
            # e.g. the very common `LE_KEY_LID =`. Present-but-empty is a real
            # distinction from absent and must not collapse into "missing".
            le_empty.append(key)
            caveats.append(f"{key} is present but empty — recorded as present-with-no-value, not as absent")
            continue
        if value == _ENCRYPTED_PLACEHOLDER:
            le_encrypted.append(key)
            le_key_types.append(key)
            continue
        le_key_types.append(key)
        expected = _LE_KEY_HEX_LEN.get(key)
        if expected and len(value) != expected:
            caveats.append(
                f"{key} is {len(value)} hex chars, expected {expected} (sizeof(struct)*2) — "
                "structure not validated, treat with caution"
            )

    le_identity_address: Optional[str] = None
    le_identity_addr_type: Optional[int] = None
    pid_value = (props.get("LE_KEY_PID") or "").strip()
    if pid_value and pid_value != _ENCRYPTED_PLACEHOLDER:
        le_identity_address, le_identity_addr_type = _decode_le_identity(pid_value)
        if le_identity_address:
            if address_norm and le_identity_address != address_norm:
                caveats.append(
                    f"LE_KEY_PID resolves the peer identity address to {le_identity_address} "
                    f"(type {le_identity_addr_type}), which differs from the section header "
                    f"{address_norm} — the section address is likely a rotating/resolvable address"
                )
            if le_identity_addr_type == 0:
                ident_vendor = lookup_vendor(le_identity_address, addr_type=0)
                if ident_vendor and not vendor:
                    vendor = ident_vendor
                    reason = "resolved from the public identity address in LE_KEY_PID"

    has_bond_material = any(k in props and props[k].strip() for k in _LINK_KEY_PROPERTIES)
    if not has_bond_material:
        caveats.append(
            "device record without recoverable bond material: no LinkKey and no LE_KEY_* value. "
            "Current AOSP only persists a section once a link-key property exists, so this is a "
            "vendor-fork record, a partially-removed bond, or carve/recovery residue. Reported as a "
            "device record — NOT asserted as a paired device."
        )

    link_key_type = _to_int(props.get("LinkKeyType"))
    pin_length = _to_int(props.get("PinLength"))
    if link_key_type == 3:
        caveats.append(
            "LinkKeyType = 3 (Debug Combination) — the well-known Bluetooth debug key was used; "
            "the encrypted link was trivially decryptable by any passive listener"
        )
    if link_key_type is not None and link_key_type >= 4 and pin_length:
        caveats.append(
            f"internally inconsistent: LinkKeyType {link_key_type} is an SSP/Secure-Connections bond, "
            f"which should have PinLength 0, but PinLength = {pin_length}"
        )

    if props.get("Restricted", "").strip() == "1":
        caveats.append(
            "Restricted = 1 — this bond was created inside a restricted/guest profile, so it is "
            "attributable to a secondary user of the handset, not necessarily the primary user"
        )

    if props.get("Aliase", "").strip():
        caveats.append(
            "'Aliase' (a user-assigned rename) is set. Unlike Name, which is supplied by the peer and "
            "unauthenticated, an alias is only ever set by a human on this handset."
        )

    dev_class_raw = _to_int(props.get("DevClass"))
    cod = decode_device_class(dev_class_raw)
    dev_class_label = (
        f"{cod['major']}/{cod['minor']}" if cod["major"] != "unknown" else "unknown"
    )
    if dev_class_raw is None:
        caveats.append(
            "DevClass absent — the Class of Device was never learned (usual for LE-only peers). "
            "Reported as unknown, not as 'miscellaneous'."
        )

    unknown_keys: dict[str, str] = {}
    for key, value in props.items():
        if key in _KNOWN_DEVICE_KEYS:
            continue
        unknown_keys[key] = _sanitise_unknown_value(key, value)

    manufacturer_id = _to_int(props.get("Manufacturer"))

    bond = BluetoothBond(
        address=address,
        name=props.get("Name", ""),
        dev_class_raw=dev_class_raw,
        dev_class_label=dev_class_label,
        dev_type=dev_type,
        dev_type_label=_DEV_TYPE_LABELS.get(dev_type, "unknown") if dev_type is not None else "unknown",
        addr_type=addr_type,
        addr_type_label=_ADDR_TYPE_LABELS.get(addr_type, f"unrecognised_{addr_type}")
        if addr_type is not None
        else "unknown",
        vendor=vendor,
        manufacturer_id=manufacturer_id,
        product_id=_to_int(props.get("ProductId")),
        version=_to_int(props.get("ProductVersion") or props.get("Version")),
        bond_timestamp_raw=ts_raw,
        bond_timestamp=ts_iso,
        timestamp_meaning=TIMESTAMP_MEANING,
        has_link_key=has_link_key,
        link_key_type=link_key_type,
        pin_length=pin_length,
        le_key_types=le_key_types,
        services=_decode_services(props.get("Service")),
        source_file=source_file,
        confidence=confidence,
        caveats=caveats,
        alias=props.get("Aliase", ""),
        model_name=props.get("ModelName", ""),
        appearance=_to_int(props.get("Appearance")),
        address_is_valid=bool(address_norm),
        random_address_subtype=subtype,
        vendor_lookup_reason=reason,
        manufacturer_label=_SIG_COMPANY_IDS.get(manufacturer_id) if manufacturer_id is not None else None,
        link_key_type_label=_LINK_KEY_TYPE_LABELS.get(link_key_type, "")
        if link_key_type is not None
        else "",
        link_key_encrypted=link_key_encrypted,
        le_keys_encrypted=le_encrypted,
        le_keys_empty=le_empty,
        le_identity_address=le_identity_address,
        le_identity_addr_type=le_identity_addr_type,
        services_le=_decode_services(props.get("ServiceLe")),
        secure_connections=_to_int(props.get("SecureConnectionsSupported")),
        restricted=props.get("Restricted", "").strip() == "1",
        has_bond_material=has_bond_material,
        unknown_keys=unknown_keys,
    )
    return bond


# ---------------------------------------------------------------------------
# Public parse entry points
# ---------------------------------------------------------------------------


def parse_bt_config_text(text: str, source_file: str = "") -> dict[str, Any]:
    """Parse bt_config.conf *content*. Pure function — no filesystem access.

    Returns ``{"adapter", "bonds", "unknown_keys", "encrypted", "caveats", ...}``.
    Never raises: a malformed line is recorded in ``parse_errors`` and skipped.
    """
    result: dict[str, Any] = {
        "adapter": None,
        "bonds": [],
        "unknown_keys": {},
        "encrypted": False,
        "caveats": [],
        "non_device_sections": [],
        "parse_errors": [],
        "source_file": source_file,
        "parsed": False,
        "timestamp_meaning": TIMESTAMP_MEANING,
    }

    if not text or not text.strip():
        result["caveats"].append(
            "bt_config.conf is empty. An empty bond store is NOT evidence that no device was ever "
            "paired — the file is rewritten in full on every change and is wiped by a Bluetooth data "
            "reset or a failed Common Criteria checksum."
        )
        return result

    sections, errors, duplicates = _read_ini(text)
    result["parse_errors"] = errors
    result["caveats"].extend(duplicates)

    if not sections:
        result["encrypted"] = True
        result["caveats"].append(
            "no section headers found — this content is not bt_config.conf INI format. It is "
            "encrypted, corrupt, or not a bond store. Reported as unreadable; the absence of bonds "
            "is NOT established."
        )
        return result

    result["parsed"] = True

    # bt_config.bak / .new are earlier or crash-interrupted generations of the
    # store, so their rows are recovered content rather than the live state.
    lowered = (source_file or "").lower()
    if lowered.endswith(".bak") or lowered.endswith(".conf.new"):
        confidence = Confidence.RECOVERED_VERIFIED.value
        result["caveats"].append(
            f"source is '{source_file}', a previous generation or crash-interrupted write of the bond "
            "store, not the live file. Rows are marked 'recovered': they may name bonds that were "
            "since removed, and their Timestamps may predate the live file's."
        )
    else:
        confidence = Confidence.LIVE.value

    info: dict[str, str] = {}
    adapter_props: dict[str, str] = {}
    metrics: dict[str, str] = {}
    device_sections: list[tuple[str, dict[str, str]]] = []

    for name, props in sections:
        if name == "Info":
            info = props
        elif name == "Adapter":
            adapter_props = props
        elif name == "Metrics":
            metrics = props
        elif _is_valid_mac(name):
            device_sections.append((name, props))
        else:
            # Not a MAC => ConfigCache::IsDeviceSection() is false. Keep it: a
            # vendor/legacy section (or a corrupted header) is still evidence.
            result["non_device_sections"].append(
                {
                    "section": name,
                    "keys": {k: _sanitise_unknown_value(k, v) for k, v in props.items()},
                    "note": "section name is not a valid 48-bit address, so this is not a bonded-device "
                    "record; retained verbatim rather than dropped",
                }
            )

    # --- Common Criteria detection from content ---
    encrypted_values: list[str] = []
    for name, props in sections:
        for key, value in props.items():
            if value.strip() == _ENCRYPTED_PLACEHOLDER:
                encrypted_values.append(f"[{name}] {key}")
    if encrypted_values:
        result["encrypted"] = True
        result["caveats"].append(COMMON_CRITERIA_NOTE)
        result["caveats"].append(
            "values replaced by the literal string 'encrypted': " + ", ".join(encrypted_values)
        )
    result["encrypted_values"] = encrypted_values

    if info or adapter_props or metrics:
        result["adapter"] = _build_adapter(info, adapter_props, metrics, source_file)

    bonds: list[BluetoothBond] = []
    for name, props in device_sections:
        try:
            bonds.append(_build_bond(name, props, source_file, confidence))
        except Exception as exc:  # never let one bad section kill the parse
            result["parse_errors"].append(
                {"line_no": 0, "raw": f"[{name}]", "reason": f"section skipped: {exc!r}"}
            )
    result["bonds"] = bonds

    # Aggregate unknown/vendor/Gabeldorsche keys so nothing is silently dropped.
    unknown: dict[str, dict[str, str]] = {}
    for bond in bonds:
        if bond.unknown_keys:
            unknown[bond.address] = dict(bond.unknown_keys)
    for name, props in ((("Info", info)), ("Adapter", adapter_props), ("Metrics", metrics)):
        extra = {
            k: _sanitise_unknown_value(k, v)
            for k, v in props.items()
            if k
            not in {
                "FileSource",
                "TimeCreated",
                "Address",
                "ScanMode",
                "DiscoveryTimeout",
                "Name",
                "LocalIOCaps",
                "LE_LOCAL_KEY_IRK",
                "LE_LOCAL_KEY_IR",
                "LE_LOCAL_KEY_DHK",
                "LE_LOCAL_KEY_ER",
                "Salt256Bit",
            }
        }
        if extra:
            unknown[name] = extra
    result["unknown_keys"] = unknown

    result["caveats"].append(
        "bt_config.conf establishes BOND evidence only: a pairing record exists (or existed) for a "
        "peer claiming each listed address. It does not establish that any link was active, that data "
        "was exchanged, or that the devices were co-located at any particular time."
    )
    if errors:
        result["caveats"].append(
            f"{len(errors)} malformed line(s) were recovered rather than aborting the parse; AOSP "
            "itself would have discarded the entire file. See parse_errors."
        )

    return result


def parse_bt_config(path: Any) -> dict[str, Any]:
    """Parse a locally-staged copy of ``bt_config.conf``.

    Missing/unreadable files are reported honestly — an absent bond store is
    never rendered as "no Bluetooth pairings".
    """
    p = Path(path)
    source = str(p)

    if not p.exists():
        return {
            "adapter": None,
            "bonds": [],
            "unknown_keys": {},
            "encrypted": False,
            "caveats": [
                f"{source} not found. The Bluetooth bond store is root-only (bluetooth:bluetooth, "
                "mode 0660) and is absent from ADB backups and logical extractions. Its absence "
                "means it was not collected or not accessible — it is NOT evidence that no device "
                "was ever paired."
            ],
            "non_device_sections": [],
            "parse_errors": [],
            "source_file": source,
            "parsed": False,
            "file_present": False,
            "timestamp_meaning": TIMESTAMP_MEANING,
        }

    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {
            "adapter": None,
            "bonds": [],
            "unknown_keys": {},
            "encrypted": False,
            "caveats": [
                f"{source} exists but could not be read ({exc.__class__.__name__}). Present-but-"
                "inaccessible is not the same as absent; no conclusion about bonds can be drawn."
            ],
            "non_device_sections": [],
            "parse_errors": [],
            "source_file": source,
            "parsed": False,
            "file_present": True,
            "timestamp_meaning": TIMESTAMP_MEANING,
        }

    result = parse_bt_config_text(text, source_file=source)
    result["file_present"] = True

    # Sibling .encrypted files prove Common Criteria mode even when the file we
    # were handed happens to contain no 'encrypted' placeholder.
    if detect_encrypted_config(p) and not result["encrypted"]:
        result["encrypted"] = True
        result["caveats"].append(
            "sibling Common Criteria artefacts (bt_config*.encrypted / *-checksum) exist alongside "
            "this file: pairing secrets are stored encrypted under AndroidKeyStore and are not "
            "recoverable offline, even though this file parsed normally."
        )
    return result


# ---------------------------------------------------------------------------
# Correlation with the non-root dumpsys view
# ---------------------------------------------------------------------------


def _dumpsys_suffix(mac: str) -> str:
    """Last two octets of a (possibly redacted) dumpsys MAC, e.g. ``"AB:CD"``."""
    parts = [p for p in re.split(r"[:\-]", (mac or "").strip()) if p]
    if len(parts) < 2:
        return ""
    return ":".join(parts[-2:]).upper()


def correlate_bluetooth(bond_store_dict: dict, dumpsys_list: list[dict]) -> list[dict]:
    """Correlate Bluetooth bond store with dumpsys bluetooth_manager output.
    
    This function MUST separate:
    - "bond_record_written_utc" (from the INI file) 
    - "dumpsys_connected_at_capture" (from dumpsys)
    
    These are returned as distinct fields.
    
    Caveat: The bond timestamp is when the pairing record was written to disk, 
    NOT a connection time.
    
    Args:
        bond_store_dict: Parsed bt_config.conf data (from parse_bt_config)
        dumpsys_list: List of device dicts from dumpsys bluetooth_manager
        
    Returns:
        List of correlated device records with separated timestamp semantics
    """
    bonds = bond_store_dict.get("bonds", [])
    return merge_with_dumpsys(bonds, dumpsys_list)


def merge_with_dumpsys(bonds: list, dumpsys_devices: list[dict]) -> list[dict]:
    """Correlate root bond records with the MAC-redacted ``dumpsys`` device list.

    Android 8+ redacts MACs in ``dumpsys bluetooth_manager`` output to
    ``XX:XX:XX:XX:AB:CD``, so a full-MAC match is often impossible and we fall
    back to the visible two-octet suffix.  A suffix match is weak — 16 bits of
    address — so ambiguous matches are flagged, never silently collapsed.

    The two sources say different things and the merged record keeps them
    apart:

    * ``bt_config`` contributes the persistent **bond** and its
      bond-record-write time;
    * ``dumpsys`` contributes **live** state at dump time (connected /
      last-seen).

    The bond timestamp is never copied into a connection field.
    """
    from .oui import normalise_mac

    merged: list[dict[str, Any]] = []
    used_dumpsys: set[int] = set()

    dumpsys_devices = list(dumpsys_devices or [])
    by_full: dict[str, list[int]] = {}
    by_suffix: dict[str, list[int]] = {}
    for idx, dev in enumerate(dumpsys_devices):
        mac = str(dev.get("mac", ""))
        norm = normalise_mac(mac)
        if norm:
            by_full.setdefault(norm, []).append(idx)
        suffix = _dumpsys_suffix(mac)
        if suffix:
            by_suffix.setdefault(suffix, []).append(idx)

    for bond in bonds or []:
        bond_dict = bond.to_dict() if hasattr(bond, "to_dict") else dict(bond)
        address = str(bond_dict.get("address", ""))
        norm = normalise_mac(address)
        caveats = list(bond_dict.get("caveats", []))

        match_idx: Optional[int] = None
        method = "bond_only"
        ambiguous = False

        if norm and norm in by_full:
            candidates = by_full[norm]
            match_idx = candidates[0]
            method = "full_mac"
            ambiguous = len(candidates) > 1
        else:
            suffix = _dumpsys_suffix(address)
            candidates = by_suffix.get(suffix, [])
            # Only a redaction-style match is acceptable here; a full-MAC entry
            # that merely shares a suffix is a different device.
            redacted = [i for i in candidates if "X" in str(dumpsys_devices[i].get("mac", "")).upper()]
            if redacted:
                match_idx = redacted[0]
                method = "redacted_suffix"
                ambiguous = len(redacted) > 1
                caveats.append(
                    "correlated with the dumpsys record on the last two address octets only, because "
                    "dumpsys redacts MACs on Android 8+. A 16-bit suffix match is weak corroboration."
                )
                if ambiguous:
                    caveats.append(
                        f"{len(redacted)} redacted dumpsys entries share this address suffix — the "
                        "correlation is AMBIGUOUS and must not be relied on for attribution."
                    )

        dumpsys_rec = None
        if match_idx is not None:
            used_dumpsys.add(match_idx)
            dumpsys_rec = dict(dumpsys_devices[match_idx])

        record: dict[str, Any] = {
            "address": address,
            "name": bond_dict.get("name", "") or (dumpsys_rec or {}).get("name", ""),
            "match_method": method,
            "match_ambiguous": ambiguous,
            "bond": bond_dict,
            "dumpsys": dumpsys_rec,
            # Explicitly separate the two time semantics.
            "bond_record_written_utc": bond_dict.get("bond_timestamp"),
            "bond_record_written_meaning": TIMESTAMP_MEANING,
            "dumpsys_connected_at_dump_time": (dumpsys_rec or {}).get("connected") if dumpsys_rec else None,
            "dumpsys_reported_last_seen": (dumpsys_rec or {}).get("last_seen") or "" if dumpsys_rec else "",
            "provenance": [
                f"bt_config.conf ({bond_dict.get('source_file', '')}) — tier2/root, persistent bond record",
            ],
            "confidence": bond_dict.get("confidence", Confidence.LIVE.value),
            "evidence_class": "bond_evidence",
            "caveats": caveats,
        }
        if dumpsys_rec is not None:
            record["provenance"].append(
                "dumpsys bluetooth_manager — tier0/non-root, live adapter state at dump time (MACs redacted)"
            )
        record["caveats"].append(
            "the bond-record write time and the dumpsys 'connected' flag are different measurements: "
            "the former is when a pairing record was persisted, the latter is adapter state at the "
            "instant of the dump. Neither is a connection history."
        )
        merged.append(record)

    # dumpsys-only devices: seen/paired per the live adapter but with no
    # persistent bond section. Surfacing them prevents a false "not present".
    for idx, dev in enumerate(dumpsys_devices):
        if idx in used_dumpsys:
            continue
        merged.append(
            {
                "address": str(dev.get("mac", "")),
                "name": dev.get("name", ""),
                "match_method": "dumpsys_only",
                "match_ambiguous": False,
                "bond": None,
                "dumpsys": dict(dev),
                "bond_record_written_utc": None,
                "bond_record_written_meaning": TIMESTAMP_MEANING,
                "dumpsys_connected_at_dump_time": dev.get("connected"),
                "dumpsys_reported_last_seen": dev.get("last_seen", ""),
                "provenance": [
                    "dumpsys bluetooth_manager — tier0/non-root, live adapter state at dump time (MACs redacted)"
                ],
                "confidence": Confidence.LIVE.value,
                "evidence_class": "adapter_state",
                "caveats": [
                    "no matching section in bt_config.conf. Either the address is redacted beyond "
                    "correlation, the device was discovered but never bonded, or the bond store was "
                    "not collected. No persistent bond is asserted."
                ],
            }
        )

    return merged


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------


def build_bond_timeline(bonds: list) -> list[dict[str, Any]]:
    """Build TimelineEvent-shaped dicts for bonds with a usable write time.

    Wording is constrained on purpose: every summary says "bond record", and no
    summary may state or imply a connection, a link, or co-location.  Bonds
    whose Timestamp is absent, zero, or unparseable produce no event — an event
    with a fabricated time would be worse than no event.
    """
    events: list[dict[str, Any]] = []

    for bond in bonds or []:
        data = bond.to_dict() if hasattr(bond, "to_dict") else dict(bond)
        ts = data.get("bond_timestamp")
        if not ts:
            continue

        address = data.get("address", "??:??:??:??:??:??")
        name = (data.get("alias") or data.get("name") or "").strip()
        name_part = f" '{name}'" if name else ""
        vendor = data.get("vendor")
        vendor_part = f" [{vendor}]" if vendor else ""
        cls = data.get("dev_class_label", "unknown")

        summary = (
            f"[Bluetooth] bond record last written for {address}{name_part}{vendor_part} "
            f"— device class {cls}; pairing-record write time only, not a link or proximity event"
        )

        events.append(
            TimelineEvent(
                timestamp=ts,
                kind="bluetooth_bond",
                summary=summary,
                confidence=Confidence(data.get("confidence", Confidence.LIVE.value)),
                ref=address,
            ).to_dict()
        )

    events.sort(key=lambda e: e["timestamp"])
    return events


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def bt_config_summary(result: dict) -> dict[str, Any]:
    """Aggregate statistics over a :func:`parse_bt_config` result."""
    bonds: Iterable[Any] = result.get("bonds") or []
    rows = [b.to_dict() if hasattr(b, "to_dict") else dict(b) for b in bonds]

    by_dev_type: dict[str, int] = {}
    by_major_class: dict[str, int] = {}
    timestamps: list[str] = []
    for row in rows:
        by_dev_type[row.get("dev_type_label", "unknown")] = (
            by_dev_type.get(row.get("dev_type_label", "unknown"), 0) + 1
        )
        major = str(row.get("dev_class_label", "unknown")).split("/")[0]
        by_major_class[major] = by_major_class.get(major, 0) + 1
        if row.get("bond_timestamp"):
            timestamps.append(row["bond_timestamp"])

    adapter = result.get("adapter")
    adapter_dict = adapter.to_dict() if hasattr(adapter, "to_dict") else adapter

    return {
        "source_file": result.get("source_file", ""),
        "file_present": result.get("file_present", False),
        "parsed": result.get("parsed", False),
        "encrypted": bool(result.get("encrypted")),
        "total_bonds": len(rows),
        "with_link_key": sum(1 for r in rows if r.get("has_link_key")),
        "with_le_keys": sum(1 for r in rows if r.get("le_key_types")),
        "without_bond_material": sum(1 for r in rows if not r.get("has_bond_material")),
        "restricted_profile_bonds": sum(1 for r in rows if r.get("restricted")),
        "with_user_alias": sum(1 for r in rows if r.get("alias")),
        "vendor_resolved": sum(1 for r in rows if r.get("vendor")),
        "vendor_suppressed_random_address": sum(1 for r in rows if r.get("addr_type") in (1, 3)),
        "by_dev_type": by_dev_type,
        "by_major_class": by_major_class,
        "bond_records_with_write_time": len(timestamps),
        "earliest_bond_record_written_utc": min(timestamps) if timestamps else None,
        "latest_bond_record_written_utc": max(timestamps) if timestamps else None,
        "non_device_sections": len(result.get("non_device_sections") or []),
        "parse_errors": len(result.get("parse_errors") or []),
        "unknown_key_sections": len(result.get("unknown_keys") or {}),
        "adapter_address": (adapter_dict or {}).get("address", ""),
        "evidence_class": "bond_evidence",
        "timestamp_meaning": TIMESTAMP_MEANING,
        "corroboration_sources": list(CORROBORATION_SOURCES),
        "caveats": list(result.get("caveats") or []),
    }
