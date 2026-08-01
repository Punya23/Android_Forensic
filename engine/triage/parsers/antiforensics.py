"""Structural anti-forensics detection: multi-user containers, vault apps, wipe traces, magic-byte carving.

Forensic purpose
----------------
Four *structural* concealment signals that live outside any single app's database:

1. **Multi-user / cloned containers.** Android's multi-user framework is the single most
   effective consumer concealment mechanism: a work profile, an OEM "dual app" clone, a
   Xiaomi Second Space or a Samsung Secure Folder each gets its own user id and its own
   ``/data/user/<id>`` tree. A triage tool that only reads user 0 silently misses them, and
   — worse — reports "nothing found" for a container it never looked at.
2. **Vault / secure-delete / anonymity packages.** Package *presence* is a capability signal.
   A vault app that is no longer installed but is still referenced by usage stats or leaves
   residue is a stronger observation than one sitting on the home screen.
3. **Factory-reset timing.** ``/data/misc/bootstat/factory_reset`` and the oldest
   ``firstInstallTime`` in ``packages.xml`` bracket the device's last clean boot.
4. **Renamed media.** The classic class-A vault trick is to move a JPEG and rename it
   ``.dat``. The bytes are untouched, so content identification defeats it completely.

Limitations — read these before quoting any output
--------------------------------------------------
* **Nothing here establishes intent.** Every container kind listed below has a mundane,
  supported, extremely common use (corporate MDM, a shared family tablet, two WhatsApp
  accounts, selling a phone). Every finding this module emits therefore carries at least one
  innocent explanation in ``caveats``, and no function ever emits the words "concealment
  proven", "guilty" or "deliberate".
* **A locked container is not an empty container.** Samsung Secure Folder and a locked
  Android 15 Private Space are protected by keys this tool cannot reach on a running
  device. They are reported ``extractable="present-locked"``, never as absent or empty.
* **OEM user-id conventions are conventions, not constants.** 95 (Samsung Dual Messenger),
  150+ (Secure Folder) and 999 (Xiaomi/OnePlus clone) are hard-coded vendor picks inside the
  ordinary secondary-user range. Attributions derived from an id alone are marked UNVERIFIED
  and that marker propagates into every finding they produce.
* **mtimes are approximations and are trivially alterable.** ``factory_reset_time`` dates
  "the device came up clean", which is a wipe *or* a re-flash *or* an OTA *or* a repair.
* **``type=`` only exists from Android 11.** On Android 7–10 there is no user-type attribute
  at all and container class must be inferred from ``flags`` alone; that limitation is
  written into the record's caveats, not just this docstring.
* Package identifiers that could not be confirmed against a primary source are present with
  ``verified: False``. Apps whose canonical package id could not be resolved at all
  (marketing names such as "HideX" / "Shreddit") are deliberately **absent** rather than
  guessed — a wrong package id in a report is worse than a gap.

Everything here is a pure function over a local path or an already-captured listing. Nothing
in this module shells out to adb or touches the device.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional
from xml.etree import ElementTree as ET

from ..config import Confidence

# ---------------------------------------------------------------------------
# Filesystem layout
# ---------------------------------------------------------------------------
# Path *templates* — "{id}" is substituted with a user id. These are candidates: the
# pipeline probes them and skips what does not exist. Never assume any of them are present.
ANDROID_USER_PATHS: list[str] = [
    "/data/system/users/userlist.xml",  # master index of every user id that exists
    "/data/system/users/{id}.xml",  # per-user record: flags, type, timestamps
    "/data/system/users/{id}/package-restrictions.xml",  # WHICH packages live in the container
    "/data/system/users/{id}/runtime-permissions.xml",
    "/data/system/users/{id}/settings_secure.xml",
    "/data/user/{id}",  # CE app data (user 0 == /data/data)
    "/data/user_de/{id}",  # DE app data, readable before first unlock
    "/data/system_ce/{id}",  # usagestats, recent_images, accounts_ce.db
    "/data/system_de/{id}",
    "/data/media/{id}",  # the container's "internal storage"
    "/data/misc_ce/{id}",
    "/data/misc_de/{id}",
]

# package-restrictions.xml is the highest-value LOCKED-container artifact: it lives on the
# DE (device-encrypted) system partition, so it enumerates which packages are installed
# inside a container whose CE data cannot be read. Marked unverified for Samsung, which may
# relocate it for Knox containers.
LOCKED_CONTAINER_READABLE_HINT = (
    "/data/system/users/{id}/package-restrictions.xml may enumerate the packages installed "
    "inside a locked container without decrypting any of its data [UNVERIFIED for Samsung "
    "Knox containers — confirm on the handset]"
)


def user_data_dirs(user_id: int) -> list[str]:
    """Concrete per-user directories for ``user_id`` (existence NOT implied or checked)."""
    return [p.format(id=user_id) for p in ANDROID_USER_PATHS if "{id}" in p]


# ---------------------------------------------------------------------------
# android.content.pm.UserInfo flag bits (values taken from AOSP UserInfo.java)
# ---------------------------------------------------------------------------
FLAG_PRIMARY = 0x00000001
FLAG_ADMIN = 0x00000002
FLAG_GUEST = 0x00000004
FLAG_RESTRICTED = 0x00000008
FLAG_INITIALIZED = 0x00000010
FLAG_MANAGED_PROFILE = 0x00000020
FLAG_DISABLED = 0x00000040
FLAG_QUIET_MODE = 0x00000080
FLAG_EPHEMERAL = 0x00000100
FLAG_DEMO = 0x00000200
FLAG_FULL = 0x00000400
FLAG_SYSTEM = 0x00000800
FLAG_PROFILE = 0x00001000
FLAG_EPHEMERAL_ON_CREATE = 0x00002000
FLAG_MAIN = 0x00004000
FLAG_FOR_TESTING = 0x00008000

_FLAG_NAMES: list[tuple[int, str]] = [
    (FLAG_PRIMARY, "PRIMARY"),
    (FLAG_ADMIN, "ADMIN"),
    (FLAG_GUEST, "GUEST"),
    (FLAG_RESTRICTED, "RESTRICTED"),
    (FLAG_INITIALIZED, "INITIALIZED"),
    (FLAG_MANAGED_PROFILE, "MANAGED_PROFILE"),
    (FLAG_DISABLED, "DISABLED"),
    (FLAG_QUIET_MODE, "QUIET_MODE"),
    (FLAG_EPHEMERAL, "EPHEMERAL"),
    (FLAG_DEMO, "DEMO"),
    (FLAG_FULL, "FULL"),
    (FLAG_SYSTEM, "SYSTEM"),
    (FLAG_PROFILE, "PROFILE"),
    (FLAG_EPHEMERAL_ON_CREATE, "EPHEMERAL_ON_CREATE"),
    (FLAG_MAIN, "MAIN"),
    (FLAG_FOR_TESTING, "FOR_TESTING"),
]

# OEM user-id conventions. NOT AOSP constants — every one of these is a vendor's hard-coded
# pick inside the ordinary secondary-user range, so an id alone never proves a feature.
SECURE_FOLDER_ID_RANGE = range(150, 160)  # 150; 151/152 after delete + re-create
OEM_CLONE_IDS = {
    95: "Samsung Dual Messenger (second app instance)",
    999: "OEM clone/dual-app profile (Xiaomi Dual Apps / OnePlus Parallel Apps)",
}

_UNVERIFIED_ID_CAVEAT = (
    "UNVERIFIED ATTRIBUTION: this OEM feature was inferred from the user id alone. "
    "User ids 95 / 150-159 / 999 are vendor conventions reported by community and vendor "
    "sources, not AOSP constants, and any of them is a legal ordinary secondary-user id. "
    "Confirm the feature against the handset before relying on the attribution."
)

_CONTAINER_INNOCENT_CAVEAT = (
    "INNOCENT EXPLANATION: a secondary user, work profile or app-clone container is a "
    "normal, vendor-supported Android feature — corporate device management, a shared "
    "family device, a child account, or simply running two accounts of one app. Its "
    "presence is not by itself evidence of concealment."
)

_CLOCK_CAVEAT = (
    "Timestamps are device-clock derived (epoch milliseconds recorded by the OS) and are "
    "wrong if the device clock was wrong, unset at first boot, or rolled back."
)

_NO_TYPE_ATTR_CAVEAT = (
    "No 'type' attribute in this record: user types were introduced in Android 11, so on "
    "Android 7-10 the container class is inferred from the flags bitmask alone and cannot "
    "distinguish a clone profile from a private-space profile."
)

_LOCKED_NOT_EMPTY_CAVEAT = (
    "Container present but its credential-encrypted store was NOT acquired. The absence of "
    "extracted content is NOT evidence that the container is empty — nothing inside it was "
    "examined."
)


# ---------------------------------------------------------------------------
# Packages of investigative interest
# ---------------------------------------------------------------------------
# category vocabulary (kept deliberately narrow so downstream scoring can key off it):
#   "vault" | "secure-delete" | "anonymity" | "cloned-container" | "encrypted-messaging"
#   | "root-hiding" | "vpn"
# verified=False means the package id itself was NOT confirmed against a primary source
# (Play listing, vendor site, F-Droid, or peer-reviewed forensic literature). Findings
# produced from a verified=False entry carry an UNVERIFIED caveat, always.
VAULT_PACKAGES: dict[str, dict] = {
    # --- photo / media vaults and calculator disguises (all Play-listed) -----------
    "com.hld.anzenbokusu": {
        "label": "Calculator - Photo Vault (HLD)",
        "category": "vault",
        "verified": True,
    },
    "com.hld.anzenbokusufake": {
        "label": "Calculator Photo Vault (HLD, disguised build)",
        "category": "vault",
        "verified": True,
        "note": "vendor markets this build for 'higher privacy security demands'",
    },
    "com.hld.anzenbokusufakelite": {
        "label": "Calculator Photo Vault Lite (HLD, disguised build)",
        "category": "vault",
        "verified": True,
    },
    "com.hld.anzenbokusucal": {
        "label": "Calculator Photo Vault (HLD, calculator front-end)",
        "category": "vault",
        "verified": True,
    },
    "me.lam.calculatorvault": {
        "label": "Calculator Vault",
        "category": "vault",
        "verified": True,
    },
    "it.ideasolutions.kyms": {
        "label": "KYMS (calculator front-end vault)",
        "category": "vault",
        "verified": True,
    },
    "com.netqin.ps": {
        "label": "Vault (NetQin / NQ Vault)",
        "category": "vault",
        "verified": True,
        "note": "historically XORs only the first 128 bytes with a single-byte key; the "
        "body of a stored image is plaintext and carves out intact",
    },
    "com.kii.safe": {
        "label": "Keepsafe Private Photo Vault",
        "category": "vault",
        "verified": True,
    },
    "com.thinkyeah.galleryvault": {
        "label": "GalleryVault (ThinkYeah)",
        "category": "vault",
        "verified": True,
    },
    "com.theronrogers.vaultyfree": {
        "label": "Vaulty",
        "category": "vault",
        "verified": True,
    },
    "com.hideitpro": {
        "label": "Hide It Pro (presents itself as 'Audio Manager')",
        "category": "vault",
        "verified": True,
    },
    "com.colure.app.privacygallery": {
        "label": "Hide Something (privacy gallery)",
        "category": "vault",
        "verified": True,
    },
    "com.sp.smartgallery.free": {
        "label": "Secure Gallery",
        "category": "vault",
        "verified": True,
    },
    "com.enchantedcloud.photovault": {
        "label": "Private Photo Vault",
        "category": "vault",
        "verified": True,
    },
    "com.morrison.gallerylocklite": {
        "label": "Gallery Lock Lite",
        "category": "vault",
        "verified": True,
    },
    "com.vdg.lockphotos": {
        "label": "Lock Photos",
        "category": "vault",
        "verified": True,
    },
    "com.passwordphonephotofolder.photolocker": {
        "label": "Photo Locker",
        "category": "vault",
        "verified": True,
    },
    "com.domobile.hidephoto": {
        "label": "HidePhoto (DoMobile)",
        "category": "vault",
        "verified": True,
    },
    "com.domobile.applock": {
        "label": "AppLock (DoMobile)",
        "category": "vault",
        "verified": True,
        "note": "at least four unrelated products are marketed as 'AppLock'; only the "
        "DoMobile package id is asserted here",
    },
    # --- secure delete / shredders ------------------------------------------------
    "com.projectstar.ishredder.android.standard": {
        "label": "iShredder Standard (ProtectStar)",
        "category": "secure-delete",
        "verified": True,
        "note": "vendor is protectstar.com but the published package uses 'projectstar'",
    },
    "com.projectstar.ishredder.android.enterprise": {
        "label": "iShredder Enterprise (ProtectStar)",
        "category": "secure-delete",
        "verified": False,
        "note": "sibling edition package id inferred from the Standard edition, not "
        "confirmed against a listing",
    },
    "com.projectstar.ishredder.android.government": {
        "label": "iShredder Government (ProtectStar)",
        "category": "secure-delete",
        "verified": False,
        "note": "sibling edition package id inferred, not confirmed against a listing",
    },
    # --- anonymity ----------------------------------------------------------------
    "org.torproject.android": {
        "label": "Orbot (Tor proxy)",
        "category": "anonymity",
        "verified": True,
    },
    "org.torproject.torbrowser": {
        "label": "Tor Browser for Android",
        "category": "anonymity",
        "verified": True,
    },
    "info.guardianproject.orfox": {
        "label": "Orfox (end-of-life 2019 — presence indicates a historic install)",
        "category": "anonymity",
        "verified": True,
    },
    # --- container / work-profile tooling ----------------------------------------
    "net.typeblog.shelter": {
        "label": "Shelter (provisions a real Android work profile)",
        "category": "cloned-container",
        "verified": True,
    },
    "com.oasisfeng.island": {
        "label": "Island (provisions a real Android work profile)",
        "category": "cloned-container",
        "verified": True,
    },
    "com.samsung.knox.securefolder": {
        "label": "Samsung Secure Folder",
        "category": "cloned-container",
        "verified": False,
        "note": "widely cited but not confirmed against a primary source this session",
    },
    "com.samsung.android.knox.containeragent": {
        "label": "Samsung Knox container agent",
        "category": "cloned-container",
        "verified": False,
        "note": "widely cited but not confirmed against a primary source this session",
    },
    # --- end-to-end encrypted messengers ------------------------------------------
    "org.thoughtcrime.securesms": {
        "label": "Signal",
        "category": "encrypted-messaging",
        "verified": True,
    },
    "network.loki.messenger": {
        "label": "Session (legacy Loki package id, still current)",
        "category": "encrypted-messaging",
        "verified": True,
    },
    "ch.threema.app": {
        "label": "Threema",
        "category": "encrypted-messaging",
        "verified": True,
    },
    "com.mywickr.wickr2": {
        "label": "Wickr Me (service shut down 2023 — presence indicates a historic install)",
        "category": "encrypted-messaging",
        "verified": True,
    },
    "org.telegram.messenger": {
        "label": "Telegram",
        "category": "encrypted-messaging",
        "verified": True,
    },
    "org.telegram.messenger.web": {
        "label": "Telegram (direct-download build)",
        "category": "encrypted-messaging",
        "verified": True,
    },
    "org.telegram.plus": {
        "label": "Plus Messenger (Telegram fork)",
        "category": "encrypted-messaging",
        "verified": True,
    },
    "nekox.messenger": {
        "label": "NekoX (Telegram fork)",
        "category": "encrypted-messaging",
        "verified": False,
        "note": "fork exists but the package id was not confirmed against a listing",
    },
    # --- root / integrity-evasion -------------------------------------------------
    "com.topjohnwu.magisk": {
        "label": "Magisk",
        "category": "root-hiding",
        "verified": True,
        "note": "Magisk's 'hide the app' feature repackages it under a RANDOM package id, "
        "so the ABSENCE of this package alongside a populated /data/adb/magisk is itself "
        "the observation",
    },
    "me.weishu.kernelsu": {
        "label": "KernelSU manager",
        "category": "root-hiding",
        "verified": False,
        "note": "package id not confirmed against a listing this session",
    },
    "me.bmax.apatch": {
        "label": "APatch manager",
        "category": "root-hiding",
        "verified": False,
        "note": "package id not confirmed against a listing this session",
    },
    "org.lsposed.manager": {
        "label": "LSPosed manager",
        "category": "root-hiding",
        "verified": False,
        "note": "package id not confirmed against a listing this session",
    },
    # --- VPNs ---------------------------------------------------------------------
    # A VPN on a phone in 2026 is ordinary. These are recorded as "network obfuscation
    # capability present" ONLY. None of these package ids was confirmed this session.
    "com.nordvpn.android": {
        "label": "NordVPN",
        "category": "vpn",
        "verified": False,
        "note": "package id not confirmed against a listing this session",
    },
    "com.expressvpn.vpn": {
        "label": "ExpressVPN",
        "category": "vpn",
        "verified": False,
        "note": "package id not confirmed against a listing this session",
    },
    "com.protonvpn.android": {
        "label": "Proton VPN",
        "category": "vpn",
        "verified": False,
        "note": "package id not confirmed against a listing this session",
    },
    "com.wireguard.android": {
        "label": "WireGuard",
        "category": "vpn",
        "verified": False,
        "note": "package id not confirmed against a listing this session",
    },
}

VALID_CATEGORIES: set[str] = {
    "vault",
    "secure-delete",
    "anonymity",
    "cloned-container",
    "encrypted-messaging",
    "root-hiding",
    "vpn",
}

# Package *families* matched by prefix. Anything matched this way is by definition an
# inference about an unlisted sibling build, so it is always verified=False.
VAULT_PACKAGE_PREFIXES: dict[str, dict] = {
    "com.projectstar.ishredder.": {
        "label": "iShredder (ProtectStar) — unlisted edition",
        "category": "secure-delete",
        "verified": False,
    },
    "com.hld.anzenbokusu": {
        "label": "HLD calculator photo-vault family — unlisted build",
        "category": "vault",
        "verified": False,
    },
    "org.telegram.": {
        "label": "Telegram fork / unlisted build",
        "category": "encrypted-messaging",
        "verified": False,
    },
}

# Innocent explanations, per category. At least one of these is attached to every finding.
_CATEGORY_INNOCENT: dict[str, str] = {
    "vault": "INNOCENT EXPLANATION: photo-vault and app-locker apps are mainstream Play "
    "Store products used for ordinary personal privacy — shared or family devices, "
    "protecting personal photos if the phone is lost, or keeping work and personal "
    "content apart. Installation is a capability, not an act.",
    "secure-delete": "INNOCENT EXPLANATION: secure-delete tools are marketed for selling, "
    "recycling or returning a device and for routine privacy hygiene; presence does not "
    "show that anything was erased, still less what.",
    "anonymity": "INNOCENT EXPLANATION: Tor and anonymity tools are used by journalists, "
    "researchers, activists and privacy-conscious users, and are a normal way to reach "
    "the internet from a censored network.",
    "cloned-container": "INNOCENT EXPLANATION: work-profile and app-cloning tools are a "
    "standard corporate device-management configuration and are also the ordinary way to "
    "run two accounts of a single app on one handset.",
    "encrypted-messaging": "INNOCENT EXPLANATION: end-to-end encrypted messengers are "
    "mainstream consumer apps with hundreds of millions of ordinary users; installing one "
    "is normal and lawful.",
    "root-hiding": "INNOCENT EXPLANATION: root and root-hiding are overwhelmingly used to "
    "make banking, payment and streaming apps work on a modified but personally-owned "
    "device, and to pass integrity checks for entirely mundane reasons.",
    "vpn": "INNOCENT EXPLANATION: a VPN on a 2026 handset is unremarkable — many are "
    "installed by an employer, bundled with antivirus, or used on public Wi-Fi. This is "
    "recorded as network-obfuscation capability only, never as evasion.",
}

_GENERIC_INNOCENT = (
    "INNOCENT EXPLANATION: this is an observation about the device's structure, not about "
    "any person's intent. Ordinary, lawful use of the device produces the same artifact."
)

_UNINSTALLED_INNOCENT = (
    "INNOCENT EXPLANATION for the removal: apps are uninstalled to free storage, before "
    "selling or returning a handset, by an OS or vendor cleanup, or simply because they "
    "were no longer wanted. Neither the fact nor the time of removal is established by "
    "this artifact."
)


def _unverified_pkg_caveat(package: str) -> str:
    return (
        f"UNVERIFIED ATTRIBUTION: the package -> application mapping for '{package}' was "
        "not confirmed against a primary source (Play listing, vendor site, or forensic "
        "literature). Confirm the package identity on the device before relying on this "
        "finding."
    )


# ---------------------------------------------------------------------------
# Content signatures
# ---------------------------------------------------------------------------
# (magic, offset, canonical extension, mime). Order matters: the first entry whose bytes
# match at its offset wins, so specific brands precede generic fallbacks.
MAGIC_SIGNATURES: list[tuple[bytes, int, str, str]] = [
    # ISO base media file format: "ftyp" at offset 4, brand at offset 8.
    (b"ftypheic", 4, "heic", "image/heic"),
    (b"ftypheix", 4, "heic", "image/heic"),
    (b"ftyphevc", 4, "heic", "image/heic-sequence"),
    (b"ftyphevx", 4, "heic", "image/heic-sequence"),
    (b"ftypmif1", 4, "heif", "image/heif"),
    (b"ftypmsf1", 4, "heif", "image/heif"),
    (b"ftypavif", 4, "avif", "image/avif"),
    (b"ftypavis", 4, "avif", "image/avif-sequence"),
    (b"ftypqt  ", 4, "mov", "video/quicktime"),
    (b"ftyp3gp", 4, "3gp", "video/3gpp"),
    (b"ftyp3g2", 4, "3g2", "video/3gpp2"),
    (b"ftypM4V", 4, "m4v", "video/x-m4v"),
    (b"ftypM4A", 4, "m4a", "audio/mp4"),
    (b"ftypisom", 4, "mp4", "video/mp4"),
    (b"ftypiso2", 4, "mp4", "video/mp4"),
    (b"ftypmp41", 4, "mp4", "video/mp4"),
    (b"ftypmp42", 4, "mp4", "video/mp4"),
    (b"ftypavc1", 4, "mp4", "video/mp4"),
    (b"ftyp", 4, "mp4", "video/mp4"),  # unrecognised brand: still ISO-BMFF
    # RIFF containers: the concrete type sits at offset 8.
    (b"WEBP", 8, "webp", "image/webp"),
    (b"AVI ", 8, "avi", "video/x-msvideo"),
    (b"WAVE", 8, "wav", "audio/x-wav"),
    (b"RIFF", 0, "riff", "application/octet-stream"),  # unknown RIFF subtype
    # Still images.
    (b"\x89PNG\r\n\x1a\n", 0, "png", "image/png"),
    (b"\xff\xd8\xff", 0, "jpg", "image/jpeg"),
    (b"GIF87a", 0, "gif", "image/gif"),
    (b"GIF89a", 0, "gif", "image/gif"),
    (b"II*\x00", 0, "tiff", "image/tiff"),
    (b"MM\x00*", 0, "tiff", "image/tiff"),
    # Documents / archives.
    (b"%PDF-", 0, "pdf", "application/pdf"),
    (b"PK\x03\x04", 0, "zip", "application/zip"),
    (b"PK\x05\x06", 0, "zip", "application/zip"),
    (b"PK\x07\x08", 0, "zip", "application/zip"),
    (b"Rar!\x1a\x07", 0, "rar", "application/vnd.rar"),
    (b"7z\xbc\xaf\x27\x1c", 0, "7z", "application/x-7z-compressed"),
    (b"\x1f\x8b\x08", 0, "gz", "application/gzip"),
    # Other media.
    (b"\x1aE\xdf\xa3", 0, "mkv", "video/x-matroska"),
    (b"ID3", 0, "mp3", "audio/mpeg"),
    (b"\xff\xfb", 0, "mp3", "audio/mpeg"),
    (b"\xff\xf3", 0, "mp3", "audio/mpeg"),
    (b"\xff\xf2", 0, "mp3", "audio/mpeg"),
    # Databases (vault indexes and copied app DBs).
    (b"SQLite format 3\x00", 0, "sqlite", "application/vnd.sqlite3"),
    # Two-byte signatures last: they are the most collision-prone.
    (b"BM", 0, "bmp", "image/bmp"),
]

# RIFF subtypes must also carry "RIFF" at offset 0 — guards against a chance 4-byte hit.
_RIFF_SUBTYPES = {"webp", "avi", "wav"}

# Declared extensions that are legitimately the same content as the detected type. An
# extension outside its group is what we call a mismatch.
_EXT_ALIASES: dict[str, set[str]] = {
    "jpg": {"jpg", "jpeg", "jpe", "jfif"},
    "png": {"png"},
    "gif": {"gif"},
    "webp": {"webp"},
    "bmp": {"bmp", "dib"},
    "tiff": {"tif", "tiff", "dng", "nef", "cr2", "arw", "orf", "rw2"},
    "heic": {"heic", "heif", "hif"},
    "heif": {"heic", "heif", "hif"},
    "avif": {"avif"},
    "mp4": {"mp4", "m4v", "mpeg4", "m4a", "mov"},
    "m4v": {"m4v", "mp4"},
    "m4a": {"m4a", "mp4", "aac"},
    "mov": {"mov", "qt", "mp4"},
    "3gp": {"3gp", "3gpp"},
    "3g2": {"3g2", "3gpp2"},
    "avi": {"avi"},
    "wav": {"wav", "wave"},
    "mkv": {"mkv", "webm", "mka"},
    "mp3": {"mp3"},
    "pdf": {"pdf"},
    "zip": {
        "zip",
        "apk",
        "apks",
        "aab",
        "xapk",
        "jar",
        "docx",
        "xlsx",
        "pptx",
        "odt",
        "ods",
        "odp",
        "epub",
        "kmz",
        "ipa",
        "war",
    },
    "rar": {"rar"},
    "7z": {"7z"},
    "gz": {"gz", "tgz", "gzip"},
    "sqlite": {"sqlite", "sqlite3", "db", "db3", "sqlitedb"},
    "riff": {"riff", "avi", "wav", "webp"},
}

# Extensions that carry no claim about content. A media file wearing one of these is not a
# *contradiction*, but at scale inside one tree it is the fingerprint of a rename-only vault.
OPAQUE_EXTENSIONS: set[str] = {
    "",
    "dat",
    "bin",
    "tmp",
    "temp",
    "cache",
    "enc",
    "vlt",
    "kys",
    "hld",
    "nomedia",
    "0",
    "1",
    "old",
    "bak",
}

# Content types that are worth reporting when found under an opaque name.
_CARVABLE_MEDIA = {
    "jpg",
    "png",
    "gif",
    "webp",
    "bmp",
    "tiff",
    "heic",
    "heif",
    "avif",
    "mp4",
    "m4v",
    "mov",
    "3gp",
    "3g2",
    "avi",
    "mkv",
    "pdf",
    "sqlite",
}

_MAX_HEADER_BYTES = 64


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _iso_from_epoch_s(secs: float) -> Optional[str]:
    """Epoch seconds -> ISO-8601 UTC with trailing Z. Implausible values return None."""
    try:
        s = float(secs)
    except (TypeError, ValueError):
        return None
    # Reject anything outside 1990-01-01 .. 2100-01-01: a value that far out is a parse
    # error or a wildly wrong clock, and dating evidence from it would be dishonest.
    if not (631152000.0 <= s <= 4102444800.0):
        return None
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(s))
    except (ValueError, OSError, OverflowError):
        return None


def _iso_from_epoch_ms(value: Any) -> Optional[str]:
    try:
        ms = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    if ms <= 0:
        return None
    return _iso_from_epoch_s(ms / 1000.0)


def _int_attr(el: Any, name: str, default: Optional[int] = None) -> Optional[int]:
    raw = el.get(name) if el is not None else None
    if raw is None:
        return default
    raw = str(raw).strip()
    try:
        return int(raw, 0) if raw.lower().startswith("0x") else int(raw)
    except (TypeError, ValueError):
        return default


def _flag_labels(flags: int) -> list[str]:
    """Decode a UserInfo flags bitmask into the AOSP constant names it sets."""
    out = [name for bit, name in _FLAG_NAMES if flags & bit]
    known = 0
    for bit, _ in _FLAG_NAMES:
        known |= bit
    residual = flags & ~known
    if residual:
        # Never silently drop bits we do not recognise — an OEM may define its own.
        out.append(f"UNKNOWN_BITS(0x{residual:x})")
    return out


def _dedupe(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for i in items:
        if i and i not in seen:
            seen.add(i)
            out.append(i)
    return out


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------
@dataclass
class AndroidUser:
    """One Android user / profile / container as recorded by the OS.

    ``extractable`` is the honesty-critical field. "present-locked" means the container
    exists and was NOT read — it must never be rendered as "no data found".
    """

    user_id: int
    name: str = ""
    user_type: str = ""  # AOSP `type` attribute; "" on Android <= 10 (attribute absent)
    flags: int = 0
    flag_labels: list[str] = field(default_factory=list)
    serial_number: Optional[int] = None
    created: Optional[str] = None  # ISO-8601 Z
    last_logged_in: Optional[str] = None  # ISO-8601 Z
    container_kind: str = "unknown"  # primary|secondary|work-profile|clone|secure-folder|unknown
    likely_feature: str = ""
    extractable: str = "unknown"  # extractable|present-locked|unknown
    data_dirs: list[str] = field(default_factory=list)
    source_file: str = ""
    caveats: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # The clock caveat applies to every record, so it is guaranteed non-empty and a
        # consumer can never render a user without at least one limitation attached.
        if _CLOCK_CAVEAT not in self.caveats:
            self.caveats.append(_CLOCK_CAVEAT)
        self.caveats = _dedupe(self.caveats)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AntiForensicFinding:
    """An OBSERVATION about the device's structure. Never a statement about a person.

    ``caveats`` is guaranteed non-empty and always contains at least one innocent
    explanation — that invariant is enforced in ``__post_init__`` rather than left to the
    call sites, so no code path can emit a bare accusation.
    """

    kind: str
    subject: str
    detail: str
    severity: str = "info"  # info|warn|critical
    evidence: list[str] = field(default_factory=list)
    confidence: str = Confidence.LIVE.value
    caveats: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.severity not in ("info", "warn", "critical"):
            self.severity = "info"
        if not any("INNOCENT EXPLANATION" in c for c in self.caveats):
            self.caveats.append(_GENERIC_INNOCENT)
        self.caveats = _dedupe(self.caveats)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# 1. Multi-user / cloned-container enumeration
# ---------------------------------------------------------------------------
def parse_userlist_xml(path: Any) -> list[int]:
    """Return the user ids listed in ``/data/system/users/userlist.xml``.

    Missing, unreadable or malformed input returns an empty list — never raises. An empty
    result means "could not read", which is NOT the same as "the device has one user";
    callers must not treat it as a negative finding.
    """
    ids: list[int] = []
    try:
        p = Path(path)
        if not p.is_file():
            return []
        root = ET.parse(str(p)).getroot()
    except (OSError, ET.ParseError, ValueError, TypeError):
        return []
    for el in root.iter("user"):
        uid = _int_attr(el, "id")
        if uid is not None and uid >= 0 and uid not in ids:
            ids.append(uid)
    return sorted(ids)


def _parse_userlist_meta(path: Any) -> tuple[list[int], Optional[int]]:
    """(ids, nextSerialNumber). nextSerialNumber higher than every live serial implies
    users were created and later removed."""
    ids = parse_userlist_xml(path)
    next_serial: Optional[int] = None
    try:
        p = Path(path)
        if p.is_file():
            root = ET.parse(str(p)).getroot()
            next_serial = _int_attr(root, "nextSerialNumber")
    except (OSError, ET.ParseError, ValueError, TypeError):
        next_serial = None
    return ids, next_serial


def _classify_container(
    user_id: int, flags: int, user_type: str
) -> tuple[str, str, list[str]]:
    """-> (container_kind, likely_feature, caveats). Pure inference over id/flags/type."""
    t = (user_type or "").lower()
    caveats: list[str] = []

    if user_id == 0:
        return (
            "primary",
            "primary / system user (the handset's main account)",
            caveats,
        )

    # OEM-convention ids are checked before the flags because Samsung implements Secure
    # Folder on top of the managed-profile mechanism: the flags say "work profile" while
    # the forensically decisive fact is that the container is Knox-locked.
    if user_id in SECURE_FOLDER_ID_RANGE:
        caveats.append(_UNVERIFIED_ID_CAVEAT)
        feature = "Samsung Secure Folder (Knox container) [UNVERIFIED — id convention]"
        if user_id != 150:
            caveats.append(
                f"User id {user_id} rather than 150 is consistent with a Secure Folder "
                "that was deleted and re-created (Samsung is reported to increment the id "
                "rather than reuse it). This is an INFERENCE from a single source, not an "
                "established fact, and an ordinary secondary user can hold this id."
            )
        if flags & FLAG_MANAGED_PROFILE or "profile.managed" in t:
            caveats.append(
                "Record also carries MANAGED_PROFILE — consistent with a Knox container, "
                "which is built on the managed-profile mechanism, but also with an "
                "ordinary corporate work profile that happens to hold this id."
            )
        return "secure-folder", feature, caveats

    if user_id in OEM_CLONE_IDS:
        caveats.append(_UNVERIFIED_ID_CAVEAT)
        return (
            "clone",
            f"{OEM_CLONE_IDS[user_id]} [UNVERIFIED — id convention]",
            caveats,
        )

    if "profile.managed" in t or (flags & FLAG_MANAGED_PROFILE):
        return (
            "work-profile",
            "managed (work) profile — corporate MDM, Shelter or Island are "
            "indistinguishable from this record alone; the profile-owner package in "
            "/data/system/device_policies.xml is what separates them",
            caveats,
        )

    if "profile.clone" in t:
        return "clone", "AOSP clone profile (a second instance of an app)", caveats

    if "profile.private" in t:
        caveats.append(
            "Android Private Space: the container auto-locks and its credential-encrypted "
            "key is evicted on lock, so its contents are unreadable while locked."
        )
        return (
            "unknown",
            "Android 15+ Private Space (profile type PRIVATE) — a locked, launcher-hidden "
            "profile; the container class vocabulary has no dedicated value for it",
            caveats,
        )

    if flags & FLAG_PROFILE:
        caveats.append(
            "Non-managed profile with no 'type' attribute to disambiguate: this may be a "
            "clone profile or a private-space profile."
        )
        return "clone", "non-managed profile (clone or private space)", caveats

    if flags & FLAG_GUEST:
        return "secondary", "guest user", caveats

    if flags & FLAG_RESTRICTED:
        return "secondary", "restricted profile (child / kiosk account)", caveats

    if flags & FLAG_FULL:
        return (
            "secondary",
            "full secondary user (a second account, or an OEM 'Second Space'-class "
            "feature — the two are indistinguishable from this record)",
            caveats,
        )

    return "unknown", "unclassified user record", caveats


def _decide_extractable(
    container_kind: str, flags: int, user_id: int, user_type: str = ""
) -> tuple[str, list[str]]:
    caveats: list[str] = []

    if container_kind == "primary":
        return "extractable", caveats

    if "profile.private" in (user_type or "").lower():
        caveats.append(
            "Android Private Space: the credential-encrypted key is evicted when the space "
            "locks, so its contents are not readable on a running device while locked."
        )
        caveats.append(_LOCKED_NOT_EMPTY_CAVEAT)
        return "present-locked", caveats

    if flags & FLAG_QUIET_MODE:
        caveats.append(
            "QUIET_MODE is set: the profile is currently paused/switched off by the user, "
            "so its credential-encrypted store is not unlocked. 'Switched off' is a "
            "different observation from 'absent' — and pausing a work profile outside "
            "working hours is exactly what the feature is for."
        )
        caveats.append(_LOCKED_NOT_EMPTY_CAVEAT)
        return "present-locked", caveats

    if flags & FLAG_DISABLED:
        caveats.append("Record carries DISABLED: the container is not currently usable.")
        caveats.append(_LOCKED_NOT_EMPTY_CAVEAT)
        return "present-locked", caveats

    if container_kind == "secure-folder":
        caveats.append(
            "Samsung Secure Folder is protected by a Knox/TrustZone container key that is "
            "not derived from the normal lockscreen credential; root on a running device "
            "does not grant read access while the container is locked. Vendor acquisition "
            "of Secure Folder relies on device-specific bootloader exploit chains that "
            "this tool does not implement."
        )
        caveats.append(_LOCKED_NOT_EMPTY_CAVEAT)
        caveats.append(LOCKED_CONTAINER_READABLE_HINT.format(id=user_id))
        return "present-locked", caveats

    if container_kind == "clone":
        caveats.append(
            "Clone/dual containers belong to the parent user's profile group and normally "
            "read like ordinary user-0 data once user 0 is unlocked — but this was not "
            "confirmed by reading the directory; 'extractable' here is an expectation, "
            "not a completed acquisition."
        )
        return "extractable", caveats

    if container_kind in ("work-profile", "secondary"):
        caveats.append(
            "Extractability is undetermined from this record alone: a separate credential "
            "(work challenge, or a second user's own lock) means the container's "
            "credential-encrypted key is only in the kernel keyring if that user has been "
            "unlocked since boot. Report as 'CE store not unlocked in this session' unless "
            "the directory was actually read."
        )
        caveats.append(_LOCKED_NOT_EMPTY_CAVEAT)
        return "unknown", caveats

    caveats.append(_LOCKED_NOT_EMPTY_CAVEAT)
    return "unknown", caveats


def parse_user_xml(path: Any) -> Optional[AndroidUser]:
    """Parse ``/data/system/users/<id>.xml`` into an :class:`AndroidUser`.

    Returns None for missing/unparseable input — never raises. Note two AOSP details that
    naive parsers get wrong: the user's name is a child ELEMENT (``<name>…</name>``), not
    an attribute, and the type attribute is literally ``type`` (not ``userType``) and only
    exists from Android 11.
    """
    try:
        p = Path(path)
        if not p.is_file():
            return None
        root = ET.parse(str(p)).getroot()
    except (OSError, ET.ParseError, ValueError, TypeError):
        return None

    # NB: `Element` has a deprecated falsy-when-childless truthiness, so the fallback here
    # must be an explicit `is None` test, not `or`.
    if root.tag == "user":
        el = root
    else:
        nested = root.find("user")
        el = nested if nested is not None else root

    uid = _int_attr(el, "id")
    if uid is None:
        # Fall back to the filename ("10.xml") — but say so, because the record itself
        # failed to identify the user.
        try:
            uid = int(Path(path).stem)
        except (TypeError, ValueError):
            return None
        filename_uid = True
    else:
        filename_uid = False

    flags = _int_attr(el, "flags", 0) or 0
    user_type = (el.get("type") or "").strip()

    name_el = el.find("name")
    name = (name_el.text or "").strip() if name_el is not None and name_el.text else ""

    kind, feature, caveats = _classify_container(uid, flags, user_type)
    extractable, extract_caveats = _decide_extractable(kind, flags, uid, user_type)
    caveats.extend(extract_caveats)

    if not user_type:
        caveats.append(_NO_TYPE_ATTR_CAVEAT)
    if filename_uid:
        caveats.append(
            "The record carried no 'id' attribute; the user id was taken from the "
            "filename and is therefore an assumption about the file's naming."
        )
    if uid != 0:
        caveats.append(_CONTAINER_INNOCENT_CAVEAT)

    fingerprint = (el.get("lastLoggedInFingerprint") or "").strip()
    if fingerprint:
        caveats.append(f"Build fingerprint at last login: {fingerprint}")

    return AndroidUser(
        user_id=uid,
        name=name,
        user_type=user_type,
        flags=flags,
        flag_labels=_flag_labels(flags),
        serial_number=_int_attr(el, "serialNumber"),
        created=_iso_from_epoch_ms(el.get("created")),
        last_logged_in=_iso_from_epoch_ms(el.get("lastLoggedIn")),
        container_kind=kind,
        likely_feature=feature,
        extractable=extractable,
        data_dirs=user_data_dirs(uid),
        source_file=str(path),
        caveats=caveats,
    )


def _coerce_id_listing(listing: Any) -> list[int]:
    """Accept a list of ids/names, or raw `ls` output, and pull integer user ids out."""
    if listing is None:
        return []
    items: list[str]
    if isinstance(listing, str):
        items = re.split(r"[\s,]+", listing.strip())
    elif isinstance(listing, (list, tuple, set)):
        items = [str(i) for i in listing]
    else:
        return []
    out: list[int] = []
    for raw in items:
        token = os.path.basename(str(raw).strip().rstrip("/"))
        if token.isdigit():
            uid = int(token)
            if uid not in out:
                out.append(uid)
    return sorted(out)


def enumerate_users(
    system_users_dir: Any, *, data_user_listing: Any = None
) -> list[AndroidUser]:
    """Enumerate every user/container evidenced under a staged ``/data/system/users``.

    The union of three sources is taken — ``userlist.xml``, the ``<id>.xml`` files present,
    and (optionally) the directory names observed under ``/data/user`` — because divergence
    between them is itself a finding: an id with a directory but no record is residue of a
    container that was removed.

    Missing or unreadable input returns an empty list; it never raises, and an empty list
    means "nothing could be read", not "the device has a single user".
    """
    users: dict[int, AndroidUser] = {}

    try:
        base = Path(system_users_dir)
        base_ok = base.is_dir()
    except (OSError, TypeError, ValueError):
        return []
    if not base_ok:
        return []

    listed_ids, next_serial = _parse_userlist_meta(base / "userlist.xml")

    # Per-user records.
    try:
        xml_files = sorted(base.glob("*.xml"))
    except OSError:
        xml_files = []
    for f in xml_files:
        if f.name == "userlist.xml" or not f.stem.isdigit():
            continue
        u = parse_user_xml(f)
        if u is not None:
            users[u.user_id] = u

    # Ids in userlist.xml with no readable record.
    for uid in listed_ids:
        if uid in users:
            continue
        kind, feature, caveats = _classify_container(uid, 0, "")
        extractable, extra = _decide_extractable(kind, 0, uid)
        caveats.extend(extra)
        caveats.append(
            "Listed in userlist.xml but no readable /data/system/users/<id>.xml record was "
            "staged: the container's existence is evidenced, its properties are not. This "
            "is commonly just an incomplete acquisition, not concealment."
        )
        if uid != 0:
            caveats.append(_CONTAINER_INNOCENT_CAVEAT)
        users[uid] = AndroidUser(
            user_id=uid,
            container_kind=kind,
            likely_feature=feature,
            extractable=extractable,
            data_dirs=user_data_dirs(uid),
            source_file=str(base / "userlist.xml"),
            caveats=caveats,
        )

    # Directory residue with no record at all.
    for uid in _coerce_id_listing(data_user_listing):
        if uid in users:
            continue
        kind, feature, caveats = _classify_container(uid, 0, "")
        caveats.append(
            "ORPHAN CONTAINER RESIDUE: a data directory for this user id was observed but "
            "the user appears in neither userlist.xml nor a per-user record. That pattern "
            "is consistent with a container that was removed and whose directories were "
            "left behind — and equally with a partial acquisition, an OEM-managed "
            "directory, or a container created after the listing was captured."
        )
        caveats.append(_CONTAINER_INNOCENT_CAVEAT)
        caveats.append(_LOCKED_NOT_EMPTY_CAVEAT)
        users[uid] = AndroidUser(
            user_id=uid,
            container_kind=kind,
            likely_feature=(feature + " (directory residue only)").strip(),
            extractable="unknown",
            data_dirs=user_data_dirs(uid),
            source_file="",
            caveats=caveats,
        )

    # nextSerialNumber above every live serial implies users were created and removed.
    if next_serial is not None:
        live = [u.serial_number for u in users.values() if u.serial_number is not None]
        if live and next_serial > max(live) + 1:
            note = (
                f"userlist.xml nextSerialNumber={next_serial} exceeds the highest live "
                f"serialNumber ({max(live)}) by more than one, which is consistent with "
                "one or more users having been created and later removed. Serial numbers "
                "are also consumed by pre-created and ephemeral users, so this is an "
                "indication, not proof, and removing a user is an ordinary action."
            )
            for u in users.values():
                u.caveats = _dedupe(u.caveats + [note])

    return [users[k] for k in sorted(users)]


def detect_removed_users(
    system_users_dir: Any, *, data_user_listing: Any = None
) -> list[AntiForensicFinding]:
    """Findings for containers that appear to have existed and been removed.

    Separated from :func:`enumerate_users` because "a user is gone" is an inference about
    an absence, and absences deserve their own, clearly-labelled findings.
    """
    findings: list[AntiForensicFinding] = []
    try:
        base = Path(system_users_dir)
        if not base.is_dir():
            return []
    except (OSError, TypeError, ValueError):
        return []

    users = enumerate_users(base, data_user_listing=data_user_listing)
    known = {u.user_id for u in users}
    _, next_serial = _parse_userlist_meta(base / "userlist.xml")

    for u in users:
        if u.source_file == "" and u.user_id not in (0,):
            findings.append(
                AntiForensicFinding(
                    kind="removed-container-residue",
                    subject=f"user {u.user_id}",
                    detail=(
                        f"A data directory for user {u.user_id} was observed with no "
                        "matching user record. Consistent with a container that was "
                        "removed, leaving its directories behind."
                    ),
                    severity="warn",
                    evidence=[d for d in u.data_dirs if not d.endswith(".xml")],
                    confidence=Confidence.DELETION_DETECTED.value,
                    caveats=[
                        _CONTAINER_INNOCENT_CAVEAT,
                        "The observation is a divergence between two listings; it does not "
                        "establish when, how, or by whom the container was removed, and a "
                        "partial acquisition produces exactly the same divergence.",
                    ],
                )
            )

    live_serials = [u.serial_number for u in users if u.serial_number is not None]
    if next_serial is not None and live_serials and next_serial > max(live_serials) + 1:
        findings.append(
            AntiForensicFinding(
                kind="user-serial-gap",
                subject="userlist.xml",
                detail=(
                    f"nextSerialNumber={next_serial} is higher than the highest live "
                    f"serialNumber ({max(live_serials)}), consistent with users having "
                    "been created and later removed."
                ),
                severity="info",
                evidence=[
                    f"nextSerialNumber={next_serial}",
                    f"live serialNumbers={sorted(live_serials)}",
                    f"live user ids={sorted(known)}",
                ],
                confidence=Confidence.DELETION_DETECTED.value,
                caveats=[
                    "INNOCENT EXPLANATION: serial numbers are also consumed by guest, "
                    "ephemeral and pre-created users that the OS itself creates and "
                    "destroys; deleting a second user or a guest session is an entirely "
                    "ordinary action.",
                    "This counts allocations, not deletions: it cannot say how many users "
                    "were removed, when, or what they contained.",
                ],
            )
        )
    return findings


# ---------------------------------------------------------------------------
# 2. Vault / anti-forensic package detection
# ---------------------------------------------------------------------------
def _lookup_package(package: str) -> Optional[dict]:
    entry = VAULT_PACKAGES.get(package)
    if entry is not None:
        return dict(entry)
    for prefix, tmpl in VAULT_PACKAGE_PREFIXES.items():
        if package.startswith(prefix):
            out = dict(tmpl)
            out["note"] = (
                f"matched by package-family prefix '{prefix}*', not by an exact known id"
            )
            return out
    return None


def _usage_packages(usage_events: Any) -> set[str]:
    out: set[str] = set()
    if not usage_events:
        return out
    if isinstance(usage_events, (str, bytes)):
        return out
    try:
        iterator = iter(usage_events)
    except TypeError:
        return out
    for ev in iterator:
        if isinstance(ev, dict):
            pkg = ev.get("package") or ev.get("pkg") or ev.get("package_name")
            if pkg:
                out.add(str(pkg))
        elif isinstance(ev, str):
            out.add(ev)
    return out


_SEVERITY_BY_CATEGORY = {
    "vault": "warn",
    "secure-delete": "warn",
    "root-hiding": "warn",
    "cloned-container": "info",
    "anonymity": "info",
    "encrypted-messaging": "info",
    "vpn": "info",
}


def detect_vault_apps(
    packages: list[dict], *, usage_events: Any = None
) -> list[AntiForensicFinding]:
    """Match an app inventory against :data:`VAULT_PACKAGES`.

    ``packages`` entries need only ``{"package": ...}``; ``currently_installed`` (default
    True), ``ever_executed``, ``residue_paths``, ``label``, ``first_install`` and
    ``last_used`` are used when present.

    A package that is NOT currently installed but still has presence evidence (usage stats,
    launcher/appops residue, leftover directories) is reported as the stronger observation,
    because the inventory and the residue disagree — but the finding still says only that,
    and still carries innocent explanations for both the install and the removal.
    """
    findings: list[AntiForensicFinding] = []
    if not packages:
        return findings
    if isinstance(packages, (str, bytes, dict)):
        return findings

    usage_pkgs = _usage_packages(usage_events)

    try:
        iterator = list(packages)
    except TypeError:
        return findings

    for entry in iterator:
        if not isinstance(entry, dict):
            continue  # degrade gracefully: skip the record, never raise
        package = entry.get("package") or entry.get("package_name") or entry.get("pkg")
        if not package or not isinstance(package, str):
            continue
        known = _lookup_package(package)
        if known is None:
            continue

        category = known.get("category", "vault")
        if category not in VALID_CATEGORIES:
            category = "vault"
        verified = bool(known.get("verified"))
        label = known.get("label") or package

        installed = entry.get("currently_installed")
        installed = True if installed is None else bool(installed)

        evidence: list[str] = [f"package={package}"]
        for key, tag in (
            ("label", "device label"),
            ("version_name", "version"),
            ("first_install", "firstInstallTime"),
            ("last_update", "lastUpdateTime"),
            ("last_used", "last used"),
            ("installer", "installer"),
        ):
            val = entry.get(key)
            if val:
                evidence.append(f"{tag}={val}")

        presence: list[str] = []
        if entry.get("ever_executed"):
            presence.append("inventory records the package as having been executed")
        if package in usage_pkgs:
            presence.append("package appears in captured usage events / usagestats")
        for path in entry.get("residue_paths") or []:
            presence.append(f"residual path present on device: {path}")

        caveats: list[str] = [_CATEGORY_INNOCENT.get(category, _GENERIC_INNOCENT)]
        if not verified:
            caveats.append(_unverified_pkg_caveat(package))
        if known.get("note"):
            caveats.append(f"Note on this identification: {known['note']}")
        caveats.append(
            "Capability only: this records that software able to hide, shred or anonymise "
            "content is (or was) on the device. It says nothing about whether it was used, "
            "on what, or by whom."
        )

        severity = _SEVERITY_BY_CATEGORY.get(category, "info")

        if installed:
            kind = "vault-app-installed"
            detail = (
                f"{label} ({category}) is present in the app inventory."
            )
            confidence = Confidence.LIVE.value
        elif presence:
            kind = "vault-app-uninstalled-with-residue"
            detail = (
                f"{label} ({category}) is NOT in the current app inventory, yet other "
                "artifacts still reference it. STRONGER OBSERVATION: the inventory and the "
                "residue disagree, so the package was present on this device at some point "
                "and is not now. "
                + "; ".join(presence)
            )
            confidence = Confidence.DELETION_DETECTED.value
            # An app that was removed but left traces is the more notable of the two
            # states, so a "warn" category is raised one step. It is still an observation.
            severity = "critical" if severity == "warn" else "warn"
            caveats.append(_UNINSTALLED_INNOCENT)
            caveats.append(
                "The residue proves prior presence, not the removal time: usage records "
                "and leftover directories are not timestamps of an uninstall."
            )
            evidence.extend(presence)
        else:
            kind = "vault-app-not-installed"
            detail = (
                f"{label} ({category}) is flagged as not currently installed and no "
                "corroborating presence evidence was supplied. Recorded for completeness "
                "only."
            )
            confidence = Confidence.LIVE.value
            severity = "info"
            caveats.append(_UNINSTALLED_INNOCENT)

        findings.append(
            AntiForensicFinding(
                kind=kind,
                subject=package,
                detail=detail,
                severity=severity,
                evidence=evidence,
                confidence=confidence,
                caveats=caveats,
            )
        )

    return findings


# ---------------------------------------------------------------------------
# 3. Factory-reset timing
# ---------------------------------------------------------------------------
_BOOTSTAT_RESET_FILES = (
    "factory_reset",
    "factory_reset_current_time",
    "factory_reset_record_value",
)

_RESET_CAVEATS = [
    "An mtime is an APPROXIMATION, not a logged event: it records when the filesystem last "
    "wrote the file, which is only a proxy for when the metric was recorded.",
    "An mtime is TRIVIALLY ALTERABLE — any process with write access, and any tool that "
    "copies or re-stages the file without preserving timestamps, changes it. Confirm "
    "against the original acquisition and its hashes before relying on this time.",
    "This dates 'the device came up clean'. That is a user-initiated factory reset OR an "
    "OTA/firmware re-flash OR a warranty repair OR a fresh out-of-box first boot. Report it "
    "as 'device was wiped or re-flashed at T', never as 'the user reset the phone at T'.",
    "The device clock drives this value. At first boot after a wipe there is often no "
    "network time yet, so the recorded time can be wrong by an arbitrary amount.",
    "INNOCENT EXPLANATION: factory-resetting a phone is an entirely normal act — selling "
    "it, passing it on, fixing a fault, or upgrading. Timing relative to an incident is a "
    "judgement for the investigator, not a conclusion this tool draws.",
    "Absence of these artifacts is not evidence that no reset occurred: /data/misc/bootstat "
    "is only present from roughly Android 10/11 onward and requires a full-filesystem read.",
]

_IT_ATTR_RE = re.compile(rb'\bit="([0-9a-fA-F]{6,16})"')


def _min_first_install_ms(packages_xml: Any) -> Optional[int]:
    """Smallest ``it=`` (firstInstallTime, hex ms) in packages.xml.

    Every preloaded system package is stamped during the post-wipe first boot, so the
    minimum is a robust first-boot estimate that needs no special syscall.
    """
    try:
        p = Path(packages_xml)
        if not p.is_file():
            return None
        raw = p.read_bytes()
    except (OSError, TypeError, ValueError):
        return None
    # Regex rather than a DOM parse: packages.xml is large, and on modern builds it may be
    # ABX (binary XML), where a DOM parse fails outright but the attribute bytes may not be
    # present either — in which case we simply return None instead of guessing.
    values: list[int] = []
    for m in _IT_ATTR_RE.finditer(raw):
        try:
            ms = int(m.group(1), 16)
        except ValueError:
            continue
        if ms > 0:
            values.append(ms)
    return min(values) if values else None


def factory_reset_time(
    bootstat_dir: Any, *, packages_xml: Any = None
) -> Optional[dict]:
    """Estimate when the device last came up clean, from bootstat mtimes and packages.xml.

    Returns None when neither source yields anything — an honest "no estimate", never a
    fabricated one. The returned ``caveats`` are mandatory reading and are duplicated into
    any report that quotes ``estimated_at``.
    """
    evidence: list[str] = []
    caveats: list[str] = list(_RESET_CAVEATS)
    estimated_at: Optional[str] = None
    method: Optional[str] = None
    bootstat_ms: Optional[float] = None

    base: Optional[Path]
    try:
        base = Path(bootstat_dir) if bootstat_dir is not None else None
    except (TypeError, ValueError):
        base = None

    if base is not None:
        try:
            base_is_dir = base.is_dir()
        except OSError:
            base_is_dir = False
        if base_is_dir:
            for name in _BOOTSTAT_RESET_FILES:
                f = base / name
                try:
                    if not f.is_file():
                        continue
                    mtime = f.stat().st_mtime
                except OSError:
                    continue
                iso = _iso_from_epoch_s(mtime)
                if iso is None:
                    caveats.append(
                        f"bootstat/{name} has an implausible mtime ({mtime}) and was "
                        "ignored rather than reported as a date."
                    )
                    continue
                evidence.append(f"{f} mtime={iso}")
                if estimated_at is None:
                    estimated_at = iso
                    bootstat_ms = mtime * 1000.0
                    method = f"bootstat/{name} file mtime"
        else:
            caveats.append(
                f"No bootstat directory was readable at {base}: absent because it was not "
                "staged, or because the device predates /data/misc/bootstat (Android 9 and "
                "earlier). Absence is not evidence that no reset occurred."
            )

    pkg_ms = _min_first_install_ms(packages_xml) if packages_xml is not None else None
    pkg_iso = _iso_from_epoch_ms(pkg_ms) if pkg_ms is not None else None
    if pkg_iso:
        evidence.append(f"packages.xml minimum firstInstallTime (it=) = {pkg_iso}")

    if estimated_at is None and pkg_iso is not None:
        estimated_at = pkg_iso
        method = "packages.xml minimum firstInstallTime (it=)"
        caveats.append(
            "No bootstat factory_reset artifact was available; the estimate comes from the "
            "oldest system-package firstInstallTime, which dates the first boot after the "
            "device came up clean rather than the wipe itself."
        )

    if estimated_at is None:
        return None

    corroboration: Optional[dict[str, Any]] = None
    if bootstat_ms is not None and pkg_ms is not None:
        delta_s = abs(bootstat_ms - pkg_ms) / 1000.0
        agrees = delta_s <= 3600.0
        corroboration = {
            "bootstat": _iso_from_epoch_s(bootstat_ms / 1000.0),
            "packages_xml_first_install": pkg_iso,
            "delta_seconds": round(delta_s, 3),
            "agrees_within_1h": agrees,
        }
        caveats.append(
            (
                "Two independent artifacts agree to within "
                f"{round(delta_s)}s (bootstat mtime and the oldest packages.xml "
                "firstInstallTime), which strengthens the estimate."
            )
            if agrees
            else (
                "The two artifacts DISAGREE by "
                f"{round(delta_s)}s (bootstat mtime vs the oldest packages.xml "
                "firstInstallTime). Treat the estimate as a range bounded by both values "
                "and prefer neither without further corroboration."
            )
        )

    confidence = (
        Confidence.LIVE.value
        if method and method.startswith("bootstat")
        else Confidence.CARVED_PARTIAL.value
    )

    out: dict[str, Any] = {
        "estimated_at": estimated_at,
        "method": method or "unknown",
        "confidence": confidence,
        "caveats": _dedupe(caveats),
        "evidence": evidence,
        "statement": (
            f"Device was wiped or re-flashed at approximately {estimated_at} "
            f"(source: {method}). This is an approximation, not a logged event."
        ),
    }
    if corroboration is not None:
        out["corroboration"] = corroboration
    return out


# ---------------------------------------------------------------------------
# 4. Magic-byte identification and renamed-media scanning
# ---------------------------------------------------------------------------
def identify_by_magic(path: Any) -> Optional[dict]:
    """Identify a file by its leading bytes, ignoring its name entirely.

    Returns None when the file is missing, unreadable, or matches no known signature —
    "unrecognised" is reported as absence of a result, never as a guess.
    """
    try:
        p = Path(path)
        if not p.is_file():
            return None
        with open(p, "rb") as fh:
            head = fh.read(_MAX_HEADER_BYTES)
    except (OSError, TypeError, ValueError):
        return None

    if not head:
        return None

    for magic, offset, ext, mime in MAGIC_SIGNATURES:
        end = offset + len(magic)
        if len(head) < end:
            continue
        if head[offset:end] != magic:
            continue
        # A RIFF subtype must actually sit inside a RIFF container.
        if ext in _RIFF_SUBTYPES and offset == 8 and head[0:4] != b"RIFF":
            continue
        declared = p.suffix.lower().lstrip(".")
        allowed = _EXT_ALIASES.get(ext, {ext})
        mismatch = bool(declared) and declared not in allowed
        return {
            "extension": ext,
            "mime": mime,
            "matched_offset": offset,
            "declared_extension": declared,
            "mismatch": mismatch,
            "matched_magic_hex": magic.hex(),
            "path": str(p),
        }
    return None


_MISMATCH_INNOCENT = (
    "INNOCENT EXPLANATION: extension/content mismatches are produced routinely by ordinary "
    "software — download managers and messaging apps save media under generated names, "
    "caches store images without extensions, and users rename files by accident. A single "
    "mismatch is close to meaningless; a directory full of them is what is worth a look."
)


def scan_renamed_media(root: Any, *, max_files: int = 5000) -> list[AntiForensicFinding]:
    """Walk a staged directory and report files whose content contradicts their name.

    This defeats the class-A vault behaviour (move + rename, bytes untouched) without any
    key material. Two finding kinds are produced: ``extension-content-mismatch`` for a file
    that claims one type and is another, and ``opaque-extension-media`` for recognisable
    media wearing a meaningless extension or none at all.

    If ``max_files`` is reached the walk stops and a ``scan-truncated`` finding is appended.
    Silent truncation is forbidden — a partial scan that looks complete is a lie about
    coverage.
    """
    findings: list[AntiForensicFinding] = []
    try:
        base = Path(root)
        if not base.is_dir():
            return findings
    except (OSError, TypeError, ValueError):
        return findings

    try:
        cap = int(max_files)
    except (TypeError, ValueError):
        cap = 5000
    if cap <= 0:
        cap = 0

    scanned = 0
    identified = 0
    truncated = False
    per_dir_mismatch: dict[str, int] = {}

    for dirpath, dirnames, filenames in os.walk(str(base), onerror=lambda _e: None):
        dirnames.sort()
        for fname in sorted(filenames):
            if scanned >= cap:
                truncated = True
                break
            fpath = os.path.join(dirpath, fname)
            try:
                if not os.path.isfile(fpath) or os.path.islink(fpath):
                    continue
            except OSError:
                continue
            scanned += 1
            info = identify_by_magic(fpath)
            if info is None:
                continue
            identified += 1
            declared = info["declared_extension"]

            if info["mismatch"]:
                per_dir_mismatch[dirpath] = per_dir_mismatch.get(dirpath, 0) + 1
                findings.append(
                    AntiForensicFinding(
                        kind="extension-content-mismatch",
                        subject=fpath,
                        detail=(
                            f"File is named '.{declared}' but its leading bytes are "
                            f"{info['mime']} ({info['extension']}), matched at offset "
                            f"{info['matched_offset']}. The content was identified from "
                            "the bytes; the name was ignored."
                        ),
                        severity="warn",
                        evidence=[
                            f"path={fpath}",
                            f"declared_extension={declared}",
                            f"content_type={info['mime']}",
                            f"magic={info['matched_magic_hex']} @ offset {info['matched_offset']}",
                        ],
                        confidence=Confidence.LIVE.value,
                        caveats=[_MISMATCH_INNOCENT],
                    )
                )
            elif declared in OPAQUE_EXTENSIONS and info["extension"] in _CARVABLE_MEDIA:
                findings.append(
                    AntiForensicFinding(
                        kind="opaque-extension-media",
                        subject=fpath,
                        detail=(
                            f"File carries a meaningless extension ('.{declared}')"
                            if declared
                            else "File carries no extension"
                        )
                        + f" but its content is {info['mime']} ({info['extension']}).",
                        severity="info",
                        evidence=[
                            f"path={fpath}",
                            f"declared_extension={declared or '(none)'}",
                            f"content_type={info['mime']}",
                        ],
                        confidence=Confidence.LIVE.value,
                        caveats=[
                            "INNOCENT EXPLANATION: app caches, thumbnail stores and "
                            "messaging apps routinely write media with no extension or a "
                            "generic one; this is normal application behaviour and is not "
                            "in itself concealment.",
                        ],
                    )
                )
        if truncated:
            break

    if truncated:
        findings.append(
            AntiForensicFinding(
                kind="scan-truncated",
                subject=str(base),
                detail=(
                    f"Scan stopped after the {cap}-file cap was reached; the tree was NOT "
                    "fully examined and these results are incomplete. Files beyond the cap "
                    "were never opened, so their absence from the findings means nothing."
                ),
                severity="info",
                evidence=[f"files_scanned={scanned}", f"max_files={cap}", f"root={base}"],
                confidence=Confidence.LIVE.value,
                caveats=[
                    "INNOCENT EXPLANATION: the cap is a performance limit of this tool, "
                    "not a property of the device.",
                    "Coverage is partial: re-run with a higher max_files, or scan "
                    "sub-trees individually, before drawing any conclusion from an "
                    "absence of findings.",
                ],
            )
        )

    # A directory dense with mismatches is the actual class-A vault fingerprint; single
    # mismatches are noise. Surfaced as its own low-severity summary observation.
    for dirpath, count in sorted(per_dir_mismatch.items()):
        if count >= 5:
            findings.append(
                AntiForensicFinding(
                    kind="mismatch-cluster",
                    subject=dirpath,
                    detail=(
                        f"{count} files in this single directory have content that "
                        "contradicts their extension. Clustering, rather than any one "
                        "file, is what distinguishes a rename-based vault store from "
                        "ordinary cache noise."
                    ),
                    severity="warn",
                    evidence=[f"directory={dirpath}", f"mismatched_files={count}"],
                    confidence=Confidence.LIVE.value,
                    caveats=[
                        "INNOCENT EXPLANATION: app cache and thumbnail directories "
                        "legitimately contain hundreds of extension-less or generically "
                        "named media files; identify the owning app before treating a "
                        "cluster as a vault.",
                    ],
                )
            )

    return findings


# ---------------------------------------------------------------------------
# 5. Summary
# ---------------------------------------------------------------------------
_INNOCENT_PARAGRAPH = (
    "INNOCENT EXPLANATIONS. Every item in this section is an observation about how the "
    "device is structured, and every one of them has ordinary explanations that are far "
    "more common than concealment. Work profiles are created by employers and by mainstream "
    "privacy apps; app-clone containers exist so a person can run two accounts of one app; "
    "Secure Folder and Private Space are advertised handset features used by people who "
    "share a device or simply want their photos private. Photo vaults, app lockers and "
    "secure-delete tools are sold on the Play Store to the general public and are commonly "
    "installed before selling or lending a phone. Encrypted messengers and VPNs are "
    "mainstream consumer software. A factory reset is a normal act when a device is sold, "
    "repaired or upgraded. Files whose extension does not match their content are produced "
    "constantly by caches, download managers and messaging apps. None of these observations "
    "shows intent, and this tool does not and cannot determine intent: it reports what is "
    "on the device so that a human examiner can decide what, if anything, it means."
)

_SUMMARY_DISCLAIMER = (
    "Prioritisation aid only. This section lists structural observations and their "
    "limitations. It is not a determination of concealment, intent or guilt, and it does "
    "not replace full forensic examination."
)


def _as_dicts(items: Any) -> list[dict]:
    out: list[dict] = []
    if not items:
        return out
    if isinstance(items, (str, bytes, dict)):
        return out
    try:
        iterator = list(items)
    except TypeError:
        return out
    for i in iterator:
        if isinstance(i, dict):
            out.append(i)
        elif hasattr(i, "to_dict"):
            try:
                d = i.to_dict()
            except Exception:  # pragma: no cover - defensive, to_dict is asdict
                continue
            if isinstance(d, dict):
                out.append(d)
    return out


def antiforensics_summary(
    users: Any, findings: Any, reset_info: Any
) -> dict[str, Any]:
    """Roll enumerated users, findings and the reset estimate into one report section.

    The output deliberately contains no verdict field. It counts what was observed, states
    what was NOT examined, and carries the innocent-explanations paragraph verbatim.
    """
    user_dicts = _as_dicts(users)
    finding_dicts = _as_dicts(findings)
    reset = reset_info if isinstance(reset_info, dict) else None

    by_kind: dict[str, int] = {}
    locked: list[dict[str, Any]] = []
    for u in user_dicts:
        kind = str(u.get("container_kind") or "unknown")
        by_kind[kind] = by_kind.get(kind, 0) + 1
        if u.get("extractable") == "present-locked":
            locked.append(
                {
                    "user_id": u.get("user_id"),
                    "container_kind": kind,
                    "likely_feature": u.get("likely_feature", ""),
                    "not_examined": True,
                }
            )

    by_severity: dict[str, int] = {"info": 0, "warn": 0, "critical": 0}
    finding_kinds: dict[str, int] = {}
    unverified = 0
    for f in finding_dicts:
        sev = str(f.get("severity") or "info")
        by_severity[sev] = by_severity.get(sev, 0) + 1
        k = str(f.get("kind") or "unknown")
        finding_kinds[k] = finding_kinds.get(k, 0) + 1
        if any("UNVERIFIED" in str(c) for c in (f.get("caveats") or [])):
            unverified += 1

    truncated = [f for f in finding_dicts if f.get("kind") == "scan-truncated"]

    limitations: list[str] = [
        "Containers reported as 'present-locked' were NOT opened. No statement is made, or "
        "can be made, about what they contain.",
        "Package presence is a capability observation only; it does not show use.",
    ]
    if unverified:
        limitations.append(
            f"{unverified} finding(s) rest on an UNVERIFIED attribution (an OEM user-id "
            "convention or an unconfirmed package identifier). Confirm these against the "
            "handset before quoting them."
        )
    if truncated:
        limitations.append(
            "A content scan hit its file cap and did not examine the whole tree; absence of "
            "findings in that tree means nothing."
        )
    if reset is None:
        limitations.append(
            "No factory-reset estimate was produced. That is an absence of artifacts, not "
            "evidence that no reset occurred."
        )
    else:
        limitations.append(
            "The factory-reset time is an mtime-derived approximation, is trivially "
            "alterable, and cannot distinguish a user wipe from an OTA, a re-flash or a "
            "repair."
        )
    for u in user_dicts:
        if not u.get("user_type"):
            limitations.append(
                "At least one user record had no 'type' attribute (Android 10 or earlier), "
                "so its container class was inferred from the flags bitmask alone."
            )
            break

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "users_total": len(user_dicts),
        "containers_by_kind": by_kind,
        "non_primary_containers": sum(v for k, v in by_kind.items() if k != "primary"),
        "locked_containers_not_examined": locked,
        "findings_total": len(finding_dicts),
        "findings_by_severity": by_severity,
        "findings_by_kind": finding_kinds,
        "unverified_attribution_findings": unverified,
        "scan_truncated": bool(truncated),
        "factory_reset": reset,
        "innocent_explanations": _INNOCENT_PARAGRAPH,
        "limitations": _dedupe(limitations),
        "disclaimer": _SUMMARY_DISCLAIMER,
    }


def summary_to_json(summary: dict[str, Any]) -> str:
    """Convenience: the summary is plain JSON-safe types by construction."""
    return json.dumps(summary, ensure_ascii=False, sort_keys=True)
