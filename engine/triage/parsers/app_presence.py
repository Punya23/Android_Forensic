"""Persistent app-presence / app-execution evidence (P3-1).

Answers the single question an examiner is most often asked about a seized Android
device: *"was application X ever on this phone, and was it ever actually run?"*

Three independent, root-only (Tier 2) artefact families are parsed and then
cross-correlated, because no single one of them answers the question honestly:

  1. ``/data/system/packages.xml``   -- PackageManagerService `Settings`. Ground truth
     for what is installed **right now**, plus install source, signer certificate and
     update timestamps. Proves INSTALLED, never EXECUTED.
  2. ``/data/system/usagestats/`` (and ``system_ce`` / ``system_de`` variants)
     -- the only artefact here that can prove EXECUTION, via ``event_log`` records.
  3. ``gass.db`` (Play Protect APK scan ledger) -- records the SHA-256 of APKs the
     scanner has seen, *including sideloads*, and is **not** cleared on uninstall.
     Proves an APK was SCANNED. Scanning is not installation and is not execution.

The forensic payoff is the set difference: a package with usage events or a gass.db
digest that is *absent* from the live package list is a "present but since removed"
finding — which is exactly the finding an investigator cares about and exactly the
one that is easiest to overstate.

LIMITATIONS — these are surfaced in the ``caveats`` of every record, not just here:

* **Android 12+ writes packages.xml as ABX (Android Binary XML), not text.** This
  module sniffs the ``ABX\\x00`` magic and refuses to guess; convert on-device with
  ``abx2xml`` first. A refusal is recorded as a caveat, never as "no packages found".
* **usagestats is actively purged on uninstall (Android 11+/schema v5).** The
  ``mappings`` token->package-name entry is deleted synchronously and a JobScheduler
  job later rewrites the interval files. Surviving records may therefore carry
  integer tokens that no longer resolve; those are emitted as ``UNRESOLVED_TOKEN_<n>``
  because an unresolvable token is itself evidence that *some* package was removed.
  Consequence: **presence of usagestats evidence is strong, absence is weak.**
* **usagestats protobuf field numbers differ between schema v4 and v5**, and proto2
  silently tolerates unknown fields, so decoding with the wrong descriptor yields
  plausible garbage rather than an error. We gate strictly on the ``version`` file
  and emit *nothing* (plus a caveat) when the version cannot be established.
* **Every timestamp here derives from ``System.currentTimeMillis()``**, which the user
  can set and NTP can jump. No timestamp in this module is monotonic.
* **Multi-user**: the ``<userId>`` path component is an Android user id (0 = owner,
  10+/150 = work profile, Secure Folder, app clones). The same package legitimately
  has completely independent state per user. Records are never merged across users.

No shelling out is performed here: every function takes a local file path that the
pipeline has already pulled.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Optional

from ..config import Confidence, Tier

# ---------------------------------------------------------------------------
# Candidate device paths — ALL Tier 2 (root). Probed, never assumed to exist.
# ---------------------------------------------------------------------------
APP_PRESENCE_PATHS: dict[str, list[str]] = {
    # PackageManagerService settings + its rollback copies. On a corrupt primary the
    # reserve/backup copy is the higher-yield artefact: it holds the *pre-last-write*
    # state and can therefore still name packages that have since been uninstalled.
    "packages_xml": [
        "/data/system/packages.xml",
        "/data/system/packages.xml.reservecopy",
        "/data/system/packages-backup.xml",
        "/data/system/packages-stopped.xml",
        "/data/system/users/0/package-restrictions.xml",
    ],
    # Plain text on every Android version — the cheapest cross-check against
    # packages.xml. Written for USER_SYSTEM only; it cannot enumerate a work profile.
    "packages_list": [
        "/data/system/packages.list",
    ],
    # Android 10+ moved the primary store to CE storage; legacy and DE roots persist
    # on upgraded devices, so all three are collected.
    "usagestats": [
        "/data/system_ce/0/usagestats",
        "/data/system_de/0/usagestats",
        "/data/system/usagestats",
    ],
    # Owning package is contested in the literature (com.google.android.gms vs
    # com.android.vending). Both are probed; the path actually used is recorded in
    # every emitted record so the examiner can attest to it.
    "gass_db": [
        "/data/data/com.google.android.gms/databases/gass.db",
        "/data/user/0/com.google.android.gms/databases/gass.db",
        "/data/user_de/0/com.google.android.gms/databases/gass.db",
        "/data/data/com.android.vending/databases/gass.db",
        "/data/user/0/com.android.vending/databases/gass.db",
        "/data/data/com.android.vending/databases/localappstate.db",
        "/data/user/0/com.android.vending/databases/localappstate.db",
    ],
}

# Everything above requires root. Kept as a single constant so callers cannot
# accidentally downgrade the tier of one path.
PRESENCE_TIER: str = Tier.TIER2.value


# ---------------------------------------------------------------------------
# Universal caveats — attached to EVERY record this module emits.
# ---------------------------------------------------------------------------
CLOCK_CAVEAT = (
    "All timestamps derive from the device wall clock (System.currentTimeMillis), "
    "which the user can set and NTP can jump; none of them are monotonic. "
    "Corroborate against DEVICE_STARTUP/DEVICE_SHUTDOWN events and filesystem mtimes."
)

MULTIUSER_CAVEAT = (
    "Multi-user: the <userId> component of /data/system/usagestats/<userId>/ is an "
    "Android user id (0=owner, 10+/150=work profile, Secure Folder, app clone). The "
    "same package has independent state per user and is NEVER merged across users. "
    "Locked CE storage for a user that was not unlocked at acquisition yields absence "
    "that is an artefact of the acquisition, not of the case."
)

INSTALL_NOT_EXECUTION_CAVEAT = (
    "Installation is NOT execution: packages.xml/packages.list/gass.db prove only that "
    "an APK was present or scanned. Only a usagestats foreground/interaction event, a "
    "dex-usage record, a crash report or an appops access time proves the app ran."
)

ABSENCE_CAVEAT = (
    "Absence of evidence is not evidence of absence: usagestats is actively purged on "
    "uninstall (Android 11+), retention is capped (~10 daily / ~4 weekly / ~6 monthly "
    "/ ~2 yearly files), and CE storage may be locked. Only positive findings carry weight."
)

_BASE_CAVEATS: list[str] = [CLOCK_CAVEAT, MULTIUSER_CAVEAT]


# ---------------------------------------------------------------------------
# UsageEvents.Event type constants (AOSP android.app.usage.UsageEvents)
# ---------------------------------------------------------------------------
EVENT_TYPE_LABELS: dict[int, str] = {
    0: "NONE",
    1: "ACTIVITY_RESUMED",  # == MOVE_TO_FOREGROUND
    2: "ACTIVITY_PAUSED",  # == MOVE_TO_BACKGROUND
    3: "END_OF_DAY",
    4: "CONTINUE_PREVIOUS_DAY",
    5: "CONFIGURATION_CHANGE",
    6: "SYSTEM_INTERACTION",
    7: "USER_INTERACTION",
    8: "SHORTCUT_INVOCATION",
    9: "CHOOSER_ACTION",
    10: "NOTIFICATION_SEEN",
    11: "STANDBY_BUCKET_CHANGED",
    12: "NOTIFICATION_INTERRUPTION",
    13: "SLICE_PINNED_PRIV",
    14: "SLICE_PINNED",
    15: "SCREEN_INTERACTIVE",
    16: "SCREEN_NON_INTERACTIVE",
    17: "KEYGUARD_SHOWN",
    18: "KEYGUARD_HIDDEN",
    19: "FOREGROUND_SERVICE_START",
    20: "FOREGROUND_SERVICE_STOP",
    21: "CONTINUING_FOREGROUND_SERVICE",
    22: "ROLLOVER_FOREGROUND_SERVICE",
    23: "ACTIVITY_STOPPED",
    24: "ACTIVITY_DESTROYED",
    25: "FLUSH_TO_DISK",
    26: "DEVICE_SHUTDOWN",
    27: "DEVICE_STARTUP",
    28: "USER_UNLOCKED",
    29: "USER_STOPPED",
    30: "LOCUS_ID_SET",
    31: "APP_COMPONENT_USED",
}

# The ONLY event types that let us say "this application executed". Notification
# events (10/12), standby-bucket bookkeeping (11) and synthetic rollover records
# (3/4/25) are deliberately excluded — none of them prove the app's code ran.
EXECUTION_EVENT_TYPES: frozenset[int] = frozenset(
    {
        1,  # ACTIVITY_RESUMED  — an Activity became foreground
        7,  # USER_INTERACTION  — explicit human interaction
        8,  # SHORTCUT_INVOCATION — deliberate launch
        19,  # FOREGROUND_SERVICE_START
        21,  # CONTINUING_FOREGROUND_SERVICE
        22,  # ROLLOVER_FOREGROUND_SERVICE
        23,  # ACTIVITY_STOPPED  — implies it was resumed first
        24,  # ACTIVITY_DESTROYED
    }
)

# Stronger subset: a user-visible Activity lifecycle transition.
FOREGROUND_EVENT_TYPES: frozenset[int] = frozenset({1, 23, 24})

# Device-level events are not attributable to a user application.
DEVICE_EVENT_TYPES: frozenset[int] = frozenset({15, 16, 17, 18, 26, 27, 28, 29})

# Synthetic bookkeeping — must be filtered out of any human-activity timeline.
SYNTHETIC_EVENT_TYPES: frozenset[int] = frozenset({3, 4, 25})

_BUCKETS: tuple[str, ...] = ("daily", "weekly", "monthly", "yearly")

# Control files inside a usagestats/<userId>/ directory that are not interval files.
_USAGESTATS_CONTROL_FILES: frozenset[str] = frozenset(
    {"version", "mappings", "migrated", "breadcrumb"}
)

# Plausible epoch-ms window used to sanity-check decoded timestamps. Anything outside
# this is reported as unresolved rather than rendered as a date.
_MIN_PLAUSIBLE_MS = 1_199_145_600_000  # 2008-01-01, pre-dates Android 1.0
_MAX_PLAUSIBLE_MS = 4_102_444_800_000  # 2100-01-01

# ApplicationInfo.FLAG_SYSTEM
_FLAG_SYSTEM = 1 << 0
_SYSTEM_PATH_PREFIXES = ("/system", "/product", "/vendor", "/apex", "/system_ext", "/odm")

# ABX (Android Binary XML) magic — Android 12+ writes packages.xml/appops.xml in it.
_ABX_MAGIC = b"ABX\x00"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _sink(caveats_out: Optional[list[str]], msg: str) -> None:
    """Append a caveat to the caller's sink, de-duplicated.

    Parsers return records; a *file-level* problem ("this file could not be decoded")
    has no record to hang off, so callers pass a list to collect them. Losing that
    information would turn "inaccessible" into "not found", which is the one mistake
    this project must never make.
    """
    if caveats_out is None:
        return
    if msg not in caveats_out:
        caveats_out.append(msg)


def _epoch_ms_to_iso(ms: Optional[int]) -> Optional[str]:
    """Epoch milliseconds -> ISO-8601 UTC with trailing Z, or None if implausible."""
    if ms is None:
        return None
    try:
        ms_int = int(ms)
    except (TypeError, ValueError):
        return None
    if not (_MIN_PLAUSIBLE_MS <= ms_int <= _MAX_PLAUSIBLE_MS):
        return None
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ms_int / 1000.0))
    except (OverflowError, OSError, ValueError):
        return None


def _parse_time_attr(raw: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Decode a packages.xml ``ft``/``it``/``ut`` attribute.

    AOSP writes these with ``attributeLongHex`` -> lowercase hex, no ``0x`` prefix.
    A value made only of decimal digits is therefore *ambiguous* (it is valid hex and
    valid decimal). We prefer the AOSP-correct hex reading and only fall back to
    decimal when hex yields an implausible date and decimal does not — recording the
    fallback as a caveat rather than silently picking one.

    Returns ``(iso_or_none, caveat_or_none)``.
    """
    if raw is None:
        return None, None
    s = str(raw).strip().lower()
    if not s:
        return None, None
    if s.startswith("0x"):
        s = s[2:]
    hex_ms: Optional[int] = None
    dec_ms: Optional[int] = None
    try:
        hex_ms = int(s, 16)
    except ValueError:
        hex_ms = None
    if s.isdigit():
        try:
            dec_ms = int(s, 10)
        except ValueError:
            dec_ms = None

    hex_iso = _epoch_ms_to_iso(hex_ms)
    if hex_iso is not None:
        return hex_iso, None
    dec_iso = _epoch_ms_to_iso(dec_ms)
    if dec_iso is not None:
        return dec_iso, (
            f"timestamp attribute {raw!r} is not a plausible epoch when read as hex "
            "(the AOSP attributeLongHex encoding); decoded as decimal milliseconds instead"
        )
    return None, (
        f"timestamp attribute {raw!r} did not decode to a plausible epoch-ms value "
        "as either hex or decimal; left unset rather than guessed"
    )


def _int_or_none(raw: Optional[str]) -> Optional[int]:
    if raw is None:
        return None
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _read_head(path: str, n: int = 8) -> bytes:
    try:
        with open(path, "rb") as fh:
            return fh.read(n)
    except OSError:
        return b""


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------
@dataclass
class PackageRecord:
    """One ``<package>`` element from packages.xml.

    Proves the package was known to PackageManager at the moment the file was
    written. It does NOT prove the app was ever launched.
    """

    package: str
    code_path: Optional[str] = None
    version_code: Optional[int] = None
    version_name: Optional[str] = None
    first_install: Optional[str] = None  # ISO-Z
    last_update: Optional[str] = None  # ISO-Z
    installer: Optional[str] = None
    install_initiator: Optional[str] = None
    uid: Optional[int] = None
    is_system: bool = False
    shared_user: Optional[str] = None
    cert_digest: Optional[str] = None  # SHA-256 of the DER signing certificate
    granted_permissions: list[str] = field(default_factory=list)
    source_file: str = ""
    tier: str = PRESENCE_TIER
    caveats: list[str] = field(default_factory=list)
    # ``ft`` is the APK file mtime, NOT an install time — kept separate so nothing
    # downstream can mistake it for one.
    apk_mtime: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "package": self.package,
            "code_path": self.code_path,
            "version_code": self.version_code,
            "version_name": self.version_name,
            "first_install": self.first_install,
            "last_update": self.last_update,
            "apk_mtime": self.apk_mtime,
            "installer": self.installer,
            "install_initiator": self.install_initiator,
            "uid": self.uid,
            "is_system": bool(self.is_system),
            "shared_user": self.shared_user,
            "cert_digest": self.cert_digest,
            "granted_permissions": list(self.granted_permissions),
            "source_file": self.source_file,
            "tier": self.tier,
            "confidence": Confidence.LIVE.value,
            "caveats": list(self.caveats),
        }


@dataclass
class UsageEvent:
    """One ``event_log`` record from a usagestats interval file."""

    package: str
    event_type: int
    event_label: str
    timestamp: Optional[str] = None  # ISO-Z
    class_name: Optional[str] = None
    source_file: str = ""
    bucket: str = ""  # daily | weekly | monthly | yearly
    caveats: list[str] = field(default_factory=list)
    user_id: Optional[str] = None  # Android user id from the path; never merged

    @property
    def is_execution(self) -> bool:
        """True only for event types that prove the app's code actually ran."""
        return self.event_type in EXECUTION_EVENT_TYPES

    def to_dict(self) -> dict[str, Any]:
        return {
            "package": self.package,
            "event_type": self.event_type,
            "event_label": self.event_label,
            "timestamp": self.timestamp,
            "class_name": self.class_name,
            "source_file": self.source_file,
            "bucket": self.bucket,
            "user_id": self.user_id,
            "is_execution": self.is_execution,
            "tier": PRESENCE_TIER,
            "confidence": Confidence.LIVE.value,
            "caveats": list(self.caveats),
        }


@dataclass
class ApkDigestRecord:
    """One APK SHA-256 recorded by the Play Protect scan ledger (gass.db).

    Key forensic property: the ledger is NOT cleared when the application is
    uninstalled, and it covers sideloaded APKs that never touched the Play Store.
    It proves an APK was *scanned* — not that it was installed, and certainly not
    that it was run.
    """

    package: str
    sha256: Optional[str] = None
    first_seen: Optional[str] = None  # ISO-Z
    source_file: str = ""
    survives_uninstall: bool = True
    caveats: list[str] = field(default_factory=list)
    version_code: Optional[int] = None
    tier: str = PRESENCE_TIER

    def to_dict(self) -> dict[str, Any]:
        return {
            "package": self.package,
            "sha256": self.sha256,
            "first_seen": self.first_seen,
            "version_code": self.version_code,
            "source_file": self.source_file,
            "survives_uninstall": bool(self.survives_uninstall),
            "tier": self.tier,
            "confidence": Confidence.RECOVERED_VERIFIED.value,
            "caveats": list(self.caveats),
        }


# ---------------------------------------------------------------------------
# packages.xml
# ---------------------------------------------------------------------------
def _cert_digest_from_sigs(pkg_el: ET.Element) -> tuple[Optional[str], Optional[str]]:
    """SHA-256 the DER signing certificate carried in ``<sigs><cert key="HEX"/>``.

    Signatures are de-duplicated across the whole file: only the first occurrence
    carries ``key=``; later packages signed by the same cert emit ``<cert index="N"/>``
    as a back-reference. A back-reference alone is not a digest, and we say so rather
    than reporting the package as unsigned.
    """
    sigs = pkg_el.find("sigs")
    if sigs is None:
        return None, None
    for cert in sigs.findall("cert"):
        key = cert.get("key")
        if key:
            try:
                der = bytes.fromhex(key.strip())
            except ValueError:
                return None, "signing certificate hex in <sigs>/<cert> is malformed"
            if not der:
                return None, "signing certificate hex in <sigs>/<cert> is empty"
            return hashlib.sha256(der).hexdigest(), None
    # Every <cert> was an index-only back-reference.
    idx = sigs.find("cert")
    if idx is not None and idx.get("index") is not None:
        return None, (
            f"signing certificate is a de-duplication back-reference (index="
            f"{idx.get('index')}); the DER bytes live on the first package in the file "
            "that used this signer — resolve across the whole file to obtain the digest"
        )
    return None, None


def _permissions_from(pkg_el: ET.Element) -> list[str]:
    """Install-time permissions only — runtime grants live in runtime-permissions.xml."""
    out: list[str] = []
    perms = pkg_el.find("perms")
    if perms is None:
        return out
    for item in perms.findall("item"):
        name = item.get("name")
        if not name:
            continue
        if str(item.get("granted", "true")).lower() == "false":
            continue
        out.append(name)
    return out


def _package_record_from_element(
    el: ET.Element, source_file: str, *, is_updated_system: bool = False
) -> Optional[PackageRecord]:
    name = el.get("name")
    if not name:
        return None

    caveats: list[str] = list(_BASE_CAVEATS)
    caveats.append(INSTALL_NOT_EXECUTION_CAVEAT)

    # ft = getLastModifiedTime() (APK file mtime), ut = getLastUpdateTime(),
    # it = firstInstallTime and is LEGACY: from Android 12 it is no longer written
    # here, having moved to per-user state in package-restrictions.xml.
    ft_iso, ft_cav = _parse_time_attr(el.get("ft"))
    ut_iso, ut_cav = _parse_time_attr(el.get("ut"))
    it_iso, it_cav = _parse_time_attr(el.get("it"))
    for c in (ft_cav, ut_cav, it_cav):
        if c:
            caveats.append(c)

    if ft_iso:
        caveats.append(
            "'ft' is the APK file last-modified time (getLastModifiedTime), NOT an "
            "install time: it is reset by updates and can be inherited from the build "
            "server or altered with root. It is reported separately as apk_mtime."
        )
    if it_iso is None:
        caveats.append(
            "firstInstallTime ('it') is absent: from Android 12 it is no longer written "
            "to packages.xml and lives per-user in "
            "/data/system/users/<userId>/package-restrictions.xml. Absence here does not "
            "mean the install time is unknown — pull that file."
        )
    else:
        caveats.append(
            "firstInstallTime is reset by uninstall+reinstall, so it is a lower bound on "
            "how long the app has been used; localappstate.db.first_download_ms is tied "
            "to the Google account and is the better source for Play installs."
        )

    public_flags = _int_or_none(el.get("publicFlags")) or 0
    code_path = el.get("codePath")
    is_system = bool(public_flags & _FLAG_SYSTEM) or bool(
        code_path and code_path.startswith(_SYSTEM_PATH_PREFIXES)
    )

    shared_user = el.get("sharedUserId")
    uid = _int_or_none(el.get("userId"))
    if shared_user is not None and uid is not None:
        # AOSP writes these mutually exclusively; both present means the file was
        # edited or produced by a third-party tool.
        caveats.append(
            "both 'userId' and 'sharedUserId' are present on this <package>; AOSP writes "
            "them mutually exclusively — treat this entry as possibly modified"
        )
    if shared_user is not None and uid is None:
        caveats.append(
            "package runs in a shared UID; any uid-keyed artefact (appops, batterystats, "
            "netstats) cannot be attributed to this package alone"
        )

    cert_digest, cert_cav = _cert_digest_from_sigs(el)
    if cert_cav:
        caveats.append(cert_cav)
    if cert_digest:
        caveats.append(
            "cert_digest is SHA-256 over the DER signing certificate as stored; compare "
            "against the known Play-signed certificate to detect a repackaged/sideloaded APK"
        )

    installer = el.get("installer")
    initiator = el.get("installInitiator")
    if el.get("installInitiatorUninstalled") == "true":
        caveats.append(
            "installInitiatorUninstalled=true: the app that initiated this install has "
            "itself since been removed"
        )
    if installer is None and initiator is None:
        caveats.append(
            "no installer or install initiator recorded; consistent with a preloaded "
            "system app or an ADB/sideload install, which cannot be distinguished here alone"
        )

    if is_updated_system:
        caveats.append(
            "recovered from an <updated-package> element: this is the FACTORY version of a "
            "preinstalled system app that has since been replaced by an update"
        )

    caveats.append(
        "versionName is not stored in packages.xml (only the numeric versionCode); it must "
        "be read from the APK manifest"
    )
    caveats.append(ABSENCE_CAVEAT)

    return PackageRecord(
        package=name,
        code_path=code_path,
        version_code=_int_or_none(el.get("version")),
        version_name=None,
        first_install=it_iso,
        last_update=ut_iso,
        installer=installer,
        install_initiator=initiator,
        uid=uid,
        is_system=is_system,
        shared_user=shared_user,
        cert_digest=cert_digest,
        granted_permissions=_permissions_from(el),
        source_file=source_file,
        tier=PRESENCE_TIER,
        caveats=caveats,
        apk_mtime=ft_iso,
    )


def parse_packages_xml(
    path: str, *, caveats_out: Optional[list[str]] = None
) -> list[PackageRecord]:
    """Parse ``/data/system/packages.xml`` (or a backup/reservecopy) into records.

    Never raises: a malformed or unreadable file yields ``[]`` plus a caveat in
    ``caveats_out``. A per-element failure skips only that element.

    Android 12+ serialises this file as ABX binary XML; we detect the magic and
    refuse rather than emit nothing silently.
    """
    records: list[PackageRecord] = []
    if not path or not os.path.isfile(path):
        _sink(caveats_out, f"packages.xml not present at {path!r} (path probed, not found)")
        return records

    head = _read_head(path, 4)
    if head.startswith(_ABX_MAGIC):
        _sink(
            caveats_out,
            f"{path!r} is ABX (Android Binary XML, magic 'ABX\\x00') as written by "
            "Android 12+; this parser does not decode ABX and will not guess. Convert on "
            "device with `abx2xml` (or `/system/framework/abx.jar`) and re-run. "
            "PRESENT BUT NOT DECODED — this is not 'no packages found'.",
        )
        return records

    try:
        tree = ET.parse(path)
        root = tree.getroot()
    except (ET.ParseError, OSError, ValueError) as exc:
        _sink(
            caveats_out,
            f"{path!r} could not be parsed as text XML ({type(exc).__name__}: {exc}); "
            "no package records were emitted. If the primary file is truncated, try "
            "packages.xml.reservecopy or packages-backup.xml.",
        )
        return records

    if root is None:
        _sink(caveats_out, f"{path!r} parsed to an empty document")
        return records

    for tag, is_updated in (("package", False), ("updated-package", True)):
        for el in root.iter(tag):
            try:
                rec = _package_record_from_element(
                    el, source_file=path, is_updated_system=is_updated
                )
            except Exception as exc:  # per-record degradation, never abort the file
                _sink(
                    caveats_out,
                    f"one <{tag}> element in {path!r} was skipped "
                    f"({type(exc).__name__}: {exc})",
                )
                continue
            if rec is not None:
                records.append(rec)

    if not records:
        _sink(
            caveats_out,
            f"{path!r} parsed successfully but contained no <package> elements",
        )
    return records


# ---------------------------------------------------------------------------
# packages.list
# ---------------------------------------------------------------------------
# Field order is fixed by AOSP writePackageListLPrInternal and consumed natively by
# libpackagelistparser; the header there says DO NOT MODIFY THIS FORMAT.
_PACKAGES_LIST_FIELDS: tuple[str, ...] = (
    "package",
    "uid",
    "debuggable",
    "data_path",
    "seinfo",
    "gids",
    "profileable_from_shell",
    "version_code",
    "profileable",
    "installer",
)


def parse_packages_list(
    path: str, *, caveats_out: Optional[list[str]] = None
) -> list[dict]:
    """Parse ``/data/system/packages.list`` (plain text on every Android version).

    Returns one dict per line. Malformed lines are skipped, never fatal.
    """
    out: list[dict] = []
    if not path or not os.path.isfile(path):
        _sink(caveats_out, f"packages.list not present at {path!r}")
        return out

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            raw_lines = fh.read().splitlines()
    except OSError as exc:
        _sink(caveats_out, f"{path!r} could not be read ({type(exc).__name__}: {exc})")
        return out

    skipped = 0
    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        parts = line.split(" ")
        if len(parts) < 2 or not parts[0]:
            skipped += 1
            continue
        rec: dict[str, Any] = {
            "source_file": path,
            "tier": PRESENCE_TIER,
            "caveats": list(_BASE_CAVEATS)
            + [
                "packages.list is written for USER_SYSTEM only (AOSP TODO b/135203078); "
                "it cannot enumerate a work profile or app clone.",
                "packages with a null parsed package (e.g. uninstalled with "
                "DELETE_KEEP_DATA) and APEX packages are omitted by the writer, so an "
                "app can have live /data/data while being invisible here.",
                INSTALL_NOT_EXECUTION_CAVEAT,
            ],
        }
        for idx, field_name in enumerate(_PACKAGES_LIST_FIELDS):
            rec[field_name] = parts[idx] if idx < len(parts) else None

        rec["uid"] = _int_or_none(rec.get("uid"))
        rec["version_code"] = _int_or_none(rec.get("version_code"))
        rec["debuggable"] = rec.get("debuggable") == "1"
        rec["profileable_from_shell"] = rec.get("profileable_from_shell") == "1"
        rec["profileable"] = rec.get("profileable") == "1"
        if rec.get("data_path") == "null":
            rec["data_path"] = None
        gids = rec.get("gids")
        rec["gids"] = (
            []
            if gids in (None, "none", "")
            else [g for g in str(gids).split(",") if g.strip()]
        )
        if len(parts) < len(_PACKAGES_LIST_FIELDS):
            rec["caveats"].append(
                f"line had {len(parts)} fields, fewer than the {len(_PACKAGES_LIST_FIELDS)} "
                "written by current AOSP; missing trailing fields are None (older platform "
                "version or truncated file)"
            )
        out.append(rec)

    if skipped:
        _sink(caveats_out, f"{skipped} malformed line(s) in {path!r} were skipped")
    return out


# ---------------------------------------------------------------------------
# Minimal, dependency-free protobuf wire-format reader
# ---------------------------------------------------------------------------
class _ProtoError(ValueError):
    """Raised internally when a byte string is not valid protobuf wire format."""


def _read_varint(buf: bytes, off: int) -> tuple[int, int]:
    """Return (value, new_offset). Raises _ProtoError on truncation/overlong."""
    result = 0
    shift = 0
    for i in range(10):  # a 64-bit varint is at most 10 bytes
        if off + i >= len(buf):
            raise _ProtoError("truncated varint")
        byte = buf[off + i]
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return result, off + i + 1
        shift += 7
    raise _ProtoError("varint longer than 10 bytes")


def _as_signed64(v: int) -> int:
    """proto int64 negatives are stored as the 10-byte two's-complement varint.

    v5 usagestats offsets are legitimately negative (down to -3600000) because of the
    one-hour rollover grace period, so this must be applied — a negative offset is
    normal and is NOT evidence of tampering.
    """
    return v - (1 << 64) if v >= (1 << 63) else v


def _decode_message(buf: bytes) -> dict[int, list[tuple[int, Any]]]:
    """Walk a protobuf message into ``{field_number: [(wire_type, value), ...]}``.

    Values: varint -> int (unsigned as stored), 64-bit -> bytes[8],
    length-delimited -> bytes, 32-bit -> bytes[4]. Groups are rejected.
    Deliberately semantics-free: this reader knows nothing about usagestats.
    """
    out: dict[int, list[tuple[int, Any]]] = {}
    off = 0
    n = len(buf)
    while off < n:
        tag, off = _read_varint(buf, off)
        field_no = tag >> 3
        wire = tag & 0x07
        if field_no == 0:
            raise _ProtoError("field number 0 is invalid")
        if wire == 0:
            val, off = _read_varint(buf, off)
        elif wire == 1:
            if off + 8 > n:
                raise _ProtoError("truncated fixed64")
            val = buf[off : off + 8]
            off += 8
        elif wire == 2:
            length, off = _read_varint(buf, off)
            if length < 0 or off + length > n:
                raise _ProtoError("truncated length-delimited field")
            val = buf[off : off + length]
            off += length
        elif wire == 5:
            if off + 4 > n:
                raise _ProtoError("truncated fixed32")
            val = buf[off : off + 4]
            off += 4
        else:
            # 3/4 are deprecated groups; 6/7 do not exist.
            raise _ProtoError(f"unsupported/invalid wire type {wire}")
        out.setdefault(field_no, []).append((wire, val))
    return out


def _pb_varint(msg: dict[int, list[tuple[int, Any]]], field_no: int) -> Optional[int]:
    for wire, val in msg.get(field_no, []):
        if wire == 0:
            return int(val)
    return None


def _pb_bytes(msg: dict[int, list[tuple[int, Any]]], field_no: int) -> Optional[bytes]:
    for wire, val in msg.get(field_no, []):
        if wire == 2:
            return val
    return None


def _pb_str(msg: dict[int, list[tuple[int, Any]]], field_no: int) -> Optional[str]:
    raw = _pb_bytes(msg, field_no)
    if raw is None:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _pb_submessages(
    msg: dict[int, list[tuple[int, Any]]], field_no: int
) -> Iterator[bytes]:
    for wire, val in msg.get(field_no, []):
        if wire == 2:
            yield val


# ---------------------------------------------------------------------------
# usagestats
# ---------------------------------------------------------------------------
def _load_mappings(user_dir: str) -> tuple[dict[int, list[str]], Optional[str]]:
    """Load the v5 ``mappings`` file (ObfuscatedPackagesProto).

    ``{package_token: [package_name, other, strings, ...]}``. Returns an empty dict
    plus a caveat when the file is missing or undecodable — the events are still
    worth emitting with unresolved tokens.
    """
    path = os.path.join(user_dir, "mappings")
    if not os.path.isfile(path):
        return {}, (
            "usagestats schema v5 'mappings' token file is absent; package tokens in the "
            "interval files cannot be resolved to names. Note that AOSP rewrites this file "
            "WITHOUT the package the moment an app is uninstalled, so a missing/short "
            "mappings file is itself consistent with recent uninstall activity."
        )
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        msg = _decode_message(raw)
    except (OSError, _ProtoError) as exc:
        return {}, (
            f"usagestats 'mappings' file at {path!r} could not be decoded "
            f"({type(exc).__name__}: {exc}); tokens are reported unresolved"
        )

    table: dict[int, list[str]] = {}
    for sub in _pb_submessages(msg, 2):  # repeated PackagesMap packages_map = 2
        try:
            pm = _decode_message(sub)
        except _ProtoError:
            continue
        token = _pb_varint(pm, 1)
        if token is None:
            continue
        strings: list[str] = []
        for _wire, val in pm.get(2, []):
            if _wire != 2:
                continue
            try:
                strings.append(val.decode("utf-8"))
            except UnicodeDecodeError:
                strings.append("")
        if strings:
            table[int(token)] = strings
    return table, None


def _resolve_token(
    mappings: dict[int, list[str]], package_token: Optional[int], sub_token: Optional[int]
) -> tuple[Optional[str], Optional[str]]:
    """Resolve a v5 (package_token, sub_token) pair against the mappings table.

    PackagesTokenData keeps the package name at index 0 of its entry; every other
    string is addressed by ``sub_token - 1`` because tokens are written +1 on disk.
    An unresolvable token is returned as ``UNRESOLVED_TOKEN_<n>`` — dropping it would
    destroy the only surviving trace of a removed package.
    """
    if package_token is None:
        return None, None
    strings = mappings.get(int(package_token))
    if not strings:
        return (
            f"UNRESOLVED_TOKEN_{int(package_token)}",
            "package token has no entry in the 'mappings' file. AOSP deletes the "
            "token->name entry synchronously when a package is uninstalled, so an "
            "orphan token is positive evidence that SOME package was removed after "
            "this event was written — the identity, however, is unknown.",
        )
    if sub_token in (None, 0):
        return strings[0], None
    idx = int(sub_token) - 1
    if 0 <= idx < len(strings):
        return strings[idx], None
    return None, (
        f"sub-token {sub_token} is out of range for package token {package_token} "
        f"({len(strings)} string(s) present); the referenced string is not recoverable"
    )


def _resolve_xml_timestamp(
    raw: Optional[int], begin_ms: Optional[int]
) -> tuple[Optional[str], Optional[str]]:
    """Legacy XML usagestats timestamps: absolute or offset-from-begin, per version.

    The exact convention per XML schema version is UNVERIFIED, so we decide on
    magnitude (a value below ~1973 as epoch-ms cannot be a real wall-clock time) and
    say which reading we used.
    """
    if raw is None:
        return None, None
    if raw >= _MIN_PLAUSIBLE_MS:
        return _epoch_ms_to_iso(raw), None
    if begin_ms is None:
        return None, (
            f"timestamp {raw} looks like an offset but the interval begin time is "
            "unknown (file name is not an epoch); left unresolved rather than guessed"
        )
    iso = _epoch_ms_to_iso(begin_ms + raw)
    return iso, (
        "legacy-XML usagestats timestamp was small and has been read as an offset from "
        "the interval begin time taken from the file name; the per-version XML "
        "convention is UNVERIFIED against AOSP source"
    )


def _usage_event(
    *,
    package: Optional[str],
    event_type: Optional[int],
    timestamp: Optional[str],
    class_name: Optional[str],
    source_file: str,
    bucket: str,
    user_id: Optional[str],
    extra_caveats: Iterable[Optional[str]] = (),
) -> Optional[UsageEvent]:
    """Build a UsageEvent, or None when the record is too incomplete to be honest."""
    if not package or event_type is None:
        return None
    if event_type not in EVENT_TYPE_LABELS:
        return None
    caveats: list[str] = list(_BASE_CAVEATS)
    for c in extra_caveats:
        if c and c not in caveats:
            caveats.append(c)
    if event_type in DEVICE_EVENT_TYPES:
        caveats.append(
            "device-level event: it is emitted against a synthetic package name and must "
            "NOT be attributed to a user application"
        )
    if event_type in SYNTHETIC_EVENT_TYPES:
        caveats.append(
            "synthetic bookkeeping event (rollover/flush), not user activity — exclude "
            "from any human-activity timeline"
        )
    if event_type in EXECUTION_EVENT_TYPES:
        caveats.append(
            "this event proves a process ran; it does not prove a human was present — "
            "accessibility services, test harnesses and automation produce the same "
            "records. Cross-check SCREEN_INTERACTIVE(15)/KEYGUARD_HIDDEN(18) nearby."
        )
    else:
        caveats.append(
            f"event type {event_type} "
            f"({EVENT_TYPE_LABELS.get(event_type, 'UNKNOWN')}) is NOT execution evidence"
        )
    if timestamp is None:
        caveats.append(
            "the raw timestamp did not resolve to a plausible epoch and has been left "
            "unset rather than rendered as a date"
        )
    caveats.append(ABSENCE_CAVEAT)
    return UsageEvent(
        package=package,
        event_type=int(event_type),
        event_label=EVENT_TYPE_LABELS.get(int(event_type), f"UNKNOWN_{event_type}"),
        timestamp=timestamp,
        class_name=class_name,
        source_file=source_file,
        bucket=bucket,
        caveats=caveats,
        user_id=user_id,
    )


def _parse_usagestats_xml(
    path: str,
    raw: bytes,
    *,
    begin_ms: Optional[int],
    bucket: str,
    user_id: Optional[str],
    caveats_out: Optional[list[str]],
) -> Optional[list[UsageEvent]]:
    """Legacy (schema v1-v3, Android 9 and earlier) text-XML interval file.

    Returns None if the bytes are not XML at all, so the caller can fall through to
    the protobuf path.
    """
    try:
        root = ET.fromstring(raw.decode("utf-8", errors="replace"))
    except (ET.ParseError, ValueError):
        return None

    events: list[UsageEvent] = []
    skipped = 0
    for el in root.iter("event"):
        pkg = el.get("package") or el.get("pkg")
        etype = _int_or_none(el.get("type"))
        raw_time = _int_or_none(el.get("time")) or _int_or_none(el.get("timeStamp"))
        iso, time_cav = _resolve_xml_timestamp(raw_time, begin_ms)
        extra: list[Optional[str]] = [
            time_cav,
            "legacy XML usagestats schema (v1-v3, Android 9 and earlier); the exact "
            "abbreviated attribute spellings per schema version are UNVERIFIED against "
            "AOSP UsageStatsXmlV1.java",
        ]
        flags = _int_or_none(el.get("flags")) or 0
        if flags & 1:  # FLAG_IS_PACKAGE_INSTANT_APP
            extra.append(
                "FLAG_IS_PACKAGE_INSTANT_APP is set: this was an instant app that was "
                "never truly installed on the device"
            )
        ev = _usage_event(
            package=pkg,
            event_type=etype,
            timestamp=iso,
            class_name=el.get("class"),
            source_file=path,
            bucket=bucket,
            user_id=user_id,
            extra_caveats=extra,
        )
        if ev is None:
            skipped += 1
            continue
        events.append(ev)

    if skipped:
        _sink(
            caveats_out,
            f"{skipped} <event> element(s) in {path!r} lacked a usable package name or a "
            "recognised event type and were skipped rather than guessed",
        )
    return events


def _parse_usagestats_proto(
    path: str,
    raw: bytes,
    *,
    version: Optional[int],
    mappings: dict[int, list[str]],
    mappings_caveat: Optional[str],
    begin_ms: Optional[int],
    bucket: str,
    user_id: Optional[str],
    caveats_out: Optional[list[str]],
) -> list[UsageEvent]:
    """Protobuf interval file, decoded with a hand-rolled wire-format walker.

    Strictly gated on the schema ``version`` file: v4 and v5 use DIFFERENT field
    numbers for the same logical fields (Event.time_ms is 5 in v4 but 3 in v5), and
    proto2 tolerates unknown fields, so decoding with the wrong descriptor produces
    plausible-looking garbage instead of an error. Without a version we emit nothing.
    """
    events: list[UsageEvent] = []

    if version is None:
        _sink(
            caveats_out,
            f"{path!r} is protobuf-encoded usagestats but the sibling 'version' file is "
            "missing or unreadable. v4 and v5 assign DIFFERENT field numbers to the same "
            "fields and proto2 ignores unknown fields, so decoding without the version "
            "would yield plausible garbage. NO events were emitted for this file.",
        )
        return events
    if version not in (4, 5):
        _sink(
            caveats_out,
            f"{path!r} declares usagestats schema version {version}, which this parser "
            "does not implement (only v4 and v5 protobuf and v1-v3 text XML are "
            "supported). NO events were emitted for this file.",
        )
        return events

    try:
        root = _decode_message(raw)
    except _ProtoError as exc:
        _sink(
            caveats_out,
            f"{path!r} is not decodable as protobuf wire format ({exc}); NO events were "
            "emitted for this file. The file is present but unreadable — this is not "
            "evidence that no usage occurred.",
        )
        return events

    # Structural sanity: an IntervalStats root must carry at least one of the fields
    # we know. Bytes that walk cleanly but contain none of them are not usagestats.
    known_roots = {2, 20, 22, 23} if version == 4 else {20, 22, 23}
    if not (set(root) & known_roots):
        _sink(
            caveats_out,
            f"{path!r} walked as protobuf but contains none of the expected IntervalStats "
            f"fields (event_log=22, packages=20, pending_events=23); it does not appear to "
            "be a usagestats interval file. NO events were emitted.",
        )
        return events

    if mappings_caveat:
        _sink(caveats_out, mappings_caveat)

    skipped = 0

    if version == 4:
        # v4: in-file StringPool (field 2), 1-based indices, ABSOLUTE timestamps.
        pool: list[str] = []
        pool_raw = _pb_bytes(root, 2)
        if pool_raw is not None:
            try:
                pool_msg = _decode_message(pool_raw)
                for _wire, val in pool_msg.get(2, []):
                    if _wire == 2:
                        try:
                            pool.append(val.decode("utf-8"))
                        except UnicodeDecodeError:
                            pool.append("")
            except _ProtoError:
                _sink(caveats_out, f"{path!r}: v4 StringPool could not be decoded")

        def _pool(idx: Optional[int]) -> Optional[str]:
            if not idx:  # index 0 means 'absent'
                return None
            i = int(idx) - 1  # 1-based into the pool
            return pool[i] if 0 <= i < len(pool) else None

        for sub in _pb_submessages(root, 22):  # repeated Event event_log = 22
            try:
                ev_msg = _decode_message(sub)
            except _ProtoError:
                skipped += 1
                continue
            pkg = _pb_str(ev_msg, 1) or _pool(_pb_varint(ev_msg, 2))
            cls = _pb_str(ev_msg, 3) or _pool(_pb_varint(ev_msg, 4))
            t_ms = _pb_varint(ev_msg, 5)
            etype = _pb_varint(ev_msg, 7)
            extra: list[Optional[str]] = [
                "usagestats schema v4: timestamps are absolute epoch-ms and strings come "
                "from the in-file StringPool"
            ]
            flags = _pb_varint(ev_msg, 6) or 0
            if flags & 1:
                extra.append(
                    "FLAG_IS_PACKAGE_INSTANT_APP is set: instant app, never truly installed"
                )
            ev = _usage_event(
                package=pkg,
                event_type=etype,
                timestamp=_epoch_ms_to_iso(t_ms),
                class_name=cls,
                source_file=path,
                bucket=bucket,
                user_id=user_id,
                extra_caveats=extra,
            )
            if ev is None:
                skipped += 1
                continue
            events.append(ev)

    else:  # version == 5
        offset_caveat = (
            "usagestats schema v5: every timestamp is stored as a signed offset from the "
            "interval begin time, which is the FILE NAME. A value of exactly 1 means "
            "'equal to the begin time' (proto2 suppresses zero); negative offsets down to "
            "-3600000 are normal (one-hour rollover grace) and are NOT tampering. Events "
            "more than an hour before the begin time are dropped at write time and are "
            "simply absent."
        )
        if begin_ms is None:
            _sink(
                caveats_out,
                f"{path!r}: v5 timestamps are offsets from the interval begin time carried "
                "in the file name, but the file name is not a decimal epoch. Timestamps "
                "are reported unset rather than fabricated.",
            )

        for sub in _pb_submessages(root, 22):  # repeated EventObfuscatedProto = 22
            try:
                ev_msg = _decode_message(sub)
            except _ProtoError:
                skipped += 1
                continue
            pkg_token = _pb_varint(ev_msg, 1)
            cls_token = _pb_varint(ev_msg, 2)
            raw_off = _pb_varint(ev_msg, 3)
            etype = _pb_varint(ev_msg, 5)

            pkg, tok_cav = _resolve_token(mappings, pkg_token, None)
            cls, cls_cav = (
                _resolve_token(mappings, pkg_token, cls_token)
                if cls_token
                else (None, None)
            )
            iso = None
            if raw_off is not None and begin_ms is not None:
                iso = _epoch_ms_to_iso(begin_ms + _as_signed64(raw_off))
            extra = [offset_caveat, tok_cav, cls_cav]
            flags = _pb_varint(ev_msg, 4) or 0
            if flags & 1:
                extra.append(
                    "FLAG_IS_PACKAGE_INSTANT_APP is set: instant app, never truly installed"
                )
            ev = _usage_event(
                package=pkg,
                event_type=etype,
                timestamp=iso,
                class_name=cls,
                source_file=path,
                bucket=bucket,
                user_id=user_id,
                extra_caveats=extra,
            )
            if ev is None:
                skipped += 1
                continue
            events.append(ev)

        # PendingEventProto (field 23) holds pre-unlock events whose strings were never
        # tokenised — high value because they survive the mappings rewrite.
        for sub in _pb_submessages(root, 23):
            try:
                pe = _decode_message(sub)
            except _ProtoError:
                skipped += 1
                continue
            pkg = _pb_str(pe, 1)
            cls = _pb_str(pe, 2)
            raw_off = _pb_varint(pe, 3)
            etype = _pb_varint(pe, 5)
            iso = None
            if raw_off is not None and begin_ms is not None:
                iso = _epoch_ms_to_iso(begin_ms + _as_signed64(raw_off))
            ev = _usage_event(
                package=pkg,
                event_type=etype,
                timestamp=iso,
                class_name=cls,
                source_file=path,
                bucket=bucket,
                user_id=user_id,
                extra_caveats=[
                    offset_caveat,
                    "recovered from pending_events (recorded before the user unlocked the "
                    "device, strings not tokenised). Whether pending-event timestamps use "
                    "the same offset encoding as event_log is UNVERIFIED.",
                ],
            )
            if ev is None:
                skipped += 1
                continue
            events.append(ev)

    if skipped:
        _sink(
            caveats_out,
            f"{skipped} record(s) in {path!r} were structurally incomplete (no resolvable "
            "package or no recognised event type) and were skipped, not guessed",
        )
    return events


def _read_version_file(user_dir: str) -> Optional[int]:
    path = os.path.join(user_dir, "version")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


def parse_usagestats_file(
    path: str,
    *,
    version: Optional[int] = None,
    mappings: Optional[dict[int, list[str]]] = None,
    mappings_caveat: Optional[str] = None,
    bucket: Optional[str] = None,
    user_id: Optional[str] = None,
    caveats_out: Optional[list[str]] = None,
) -> list[UsageEvent]:
    """Parse a single usagestats interval file (``<bucket>/<beginTimeEpochMs>``).

    The legacy text-XML format is parsed in full. The protobuf formats are decoded
    with the in-module wire-format walker and are gated on the schema version; when
    the version cannot be established, or the bytes do not decode, ZERO events are
    emitted and a caveat is recorded. Never raises.
    """
    events: list[UsageEvent] = []
    if not path or not os.path.isfile(path):
        _sink(caveats_out, f"usagestats interval file not present at {path!r}")
        return events

    base = os.path.basename(path)
    user_dir = os.path.dirname(os.path.dirname(path))
    if bucket is None:
        parent = os.path.basename(os.path.dirname(path))
        bucket = parent if parent in _BUCKETS else ""
    if user_id is None:
        user_id = os.path.basename(user_dir) or None
    if version is None:
        version = _read_version_file(user_dir)

    # The file NAME is the interval begin time — v5 offsets are meaningless without it.
    begin_ms = int(base) if base.isdigit() else None
    if begin_ms is not None and not (_MIN_PLAUSIBLE_MS <= begin_ms <= _MAX_PLAUSIBLE_MS):
        _sink(
            caveats_out,
            f"interval file name {base!r} is numeric but not a plausible epoch-ms begin "
            "time; offset-based timestamps in this file cannot be trusted",
        )
        begin_ms = None

    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        _sink(caveats_out, f"{path!r} could not be read ({type(exc).__name__}: {exc})")
        return events

    if not raw:
        _sink(caveats_out, f"{path!r} is empty (0 bytes)")
        return events

    # v1-v3 are text XML; v4/v5 are protobuf. Try XML only when it plausibly is XML.
    if raw.lstrip()[:1] == b"<":
        xml_events = _parse_usagestats_xml(
            path,
            raw,
            begin_ms=begin_ms,
            bucket=bucket or "",
            user_id=user_id,
            caveats_out=caveats_out,
        )
        if xml_events is not None:
            return xml_events

    if mappings is None:
        mappings, auto_cav = _load_mappings(user_dir) if version == 5 else ({}, None)
        mappings_caveat = mappings_caveat or auto_cav

    return _parse_usagestats_proto(
        path,
        raw,
        version=version,
        mappings=mappings,
        mappings_caveat=mappings_caveat,
        begin_ms=begin_ms,
        bucket=bucket or "",
        user_id=user_id,
        caveats_out=caveats_out,
    )


def parse_usagestats_dir(
    root: str, *, caveats_out: Optional[list[str]] = None
) -> list[UsageEvent]:
    """Walk a usagestats tree and parse every daily/weekly/monthly/yearly interval file.

    ``root`` may be the usagestats root (containing ``<userId>/`` directories), a single
    ``<userId>/`` directory, or any ancestor of them. Per-user ``version``/``mappings``
    are read once per user directory. Records from different user ids are returned
    side by side and are never merged.
    """
    events: list[UsageEvent] = []
    if not root or not os.path.isdir(root):
        _sink(caveats_out, f"usagestats directory not present at {root!r}")
        return events

    version_cache: dict[str, Optional[int]] = {}
    mapping_cache: dict[str, tuple[dict[int, list[str]], Optional[str]]] = {}
    files_seen = 0

    for dirpath, _dirnames, filenames in os.walk(root):
        bucket = os.path.basename(dirpath)
        if bucket not in _BUCKETS:
            continue
        user_dir = os.path.dirname(dirpath)
        user_id = os.path.basename(user_dir) or None

        if user_dir not in version_cache:
            version_cache[user_dir] = _read_version_file(user_dir)
        version = version_cache[user_dir]

        if user_dir not in mapping_cache:
            mapping_cache[user_dir] = (
                _load_mappings(user_dir) if version == 5 else ({}, None)
            )
        mappings, mappings_caveat = mapping_cache[user_dir]

        if "backups" in os.path.normpath(dirpath).split(os.sep):
            _sink(
                caveats_out,
                f"{dirpath!r} lies under a 'backups/' directory: it is a pre-upgrade copy "
                "and may contain records that were subsequently pruned from the live tree",
            )

        for name in sorted(filenames):
            if name in _USAGESTATS_CONTROL_FILES or name.endswith(".bak"):
                continue
            if not name.isdigit():
                _sink(
                    caveats_out,
                    f"skipped {os.path.join(dirpath, name)!r}: an interval file name must "
                    "be the decimal epoch-ms begin time",
                )
                continue
            files_seen += 1
            events.extend(
                parse_usagestats_file(
                    os.path.join(dirpath, name),
                    version=version,
                    mappings=mappings,
                    mappings_caveat=mappings_caveat,
                    bucket=bucket,
                    user_id=user_id,
                    caveats_out=caveats_out,
                )
            )

    if files_seen == 0:
        _sink(
            caveats_out,
            f"no daily/weekly/monthly/yearly interval files were found under {root!r}. "
            "Retention is capped (~10 daily files in AOSP) and uninstall triggers an "
            "active purge, so this is NOT evidence that no application was ever used.",
        )
    return events


# ---------------------------------------------------------------------------
# gass.db (Play Protect APK scan ledger)
# ---------------------------------------------------------------------------
_DIGEST_NAME_FRAGMENTS = ("sha256", "sha_256", "digest", "hash")
_PACKAGE_NAME_FRAGMENTS = ("package", "pkg", "apk_name")
_TIME_NAME_FRAGMENTS = (
    "first_download",
    "first_seen",
    "install_request",
    "timestamp",
    "_ms",
    "time",
    "date",
)
_VERSION_NAME_FRAGMENTS = ("version_code", "versioncode", "version")


def _pick_column(columns: list[str], fragments: tuple[str, ...]) -> Optional[str]:
    """First column whose lower-cased name contains one of the fragments, in order."""
    lowered = [(c, c.lower()) for c in columns]
    for frag in fragments:
        for original, low in lowered:
            if frag in low:
                return original
    return None


def _blob_to_hex(value: Any) -> Optional[str]:
    """Digests are stored as a BLOB on some builds and as hex/base64 text on others."""
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    text = str(value).strip()
    return text or None


def parse_gass_db(
    path: str, *, caveats_out: Optional[list[str]] = None
) -> list[ApkDigestRecord]:
    """Parse the Play Protect APK scan ledger (``gass.db``) or ``localappstate.db``.

    The schema varies across GMS versions and is only partially documented, so the
    table and columns are DISCOVERED via ``sqlite_master`` / ``PRAGMA table_info``
    rather than hard-coded: we look for a table carrying both a package-ish and a
    digest-ish column. If none exists, ``[]`` is returned with a caveat — never a
    fabricated result.
    """
    out: list[ApkDigestRecord] = []
    if not path or not os.path.isfile(path):
        _sink(caveats_out, f"gass.db not present at {path!r} (path probed, not found)")
        return out

    con: Optional[sqlite3.Connection] = None
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        # Undecodable text must not abort the read of an otherwise good table.
        con.text_factory = lambda b: b.decode("utf-8", errors="replace")
        cur = con.cursor()
        try:
            tables = [
                r[0]
                for r in cur.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
                if r and r[0] and not str(r[0]).startswith("sqlite_")
            ]
        except sqlite3.DatabaseError as exc:
            _sink(
                caveats_out,
                f"{path!r} is not a readable SQLite database "
                f"({type(exc).__name__}: {exc})",
            )
            return out

        matched: list[tuple[str, str, str, Optional[str], Optional[str]]] = []
        for table in tables:
            try:
                cols = [
                    str(r[1])
                    for r in cur.execute(f'PRAGMA table_info("{table}")').fetchall()
                ]
            except sqlite3.DatabaseError:
                continue
            if not cols:
                continue
            pkg_col = _pick_column(cols, _PACKAGE_NAME_FRAGMENTS)
            dig_col = _pick_column(cols, _DIGEST_NAME_FRAGMENTS)
            if not pkg_col or not dig_col:
                continue
            time_col = _pick_column(cols, _TIME_NAME_FRAGMENTS)
            ver_col = _pick_column(cols, _VERSION_NAME_FRAGMENTS)
            matched.append((table, pkg_col, dig_col, time_col, ver_col))

        if not matched:
            _sink(
                caveats_out,
                f"{path!r} contains no table carrying both a package-like and a "
                f"digest-like column (tables present: {sorted(tables)!r}). Returning no "
                "records — the file is present but its schema is not one this parser "
                "recognises; this is NOT evidence that no APK was ever scanned.",
            )
            return out

        for table, pkg_col, dig_col, time_col, ver_col in matched:
            select_cols = [pkg_col, dig_col] + [c for c in (time_col, ver_col) if c]
            quoted = ", ".join(f'"{c}"' for c in select_cols)
            try:
                rows = cur.execute(f'SELECT {quoted} FROM "{table}"').fetchall()
            except sqlite3.DatabaseError as exc:
                _sink(
                    caveats_out,
                    f"table {table!r} in {path!r} could not be read "
                    f"({type(exc).__name__}: {exc}); it was skipped",
                )
                continue

            for row in rows:
                try:
                    pkg = row[0]
                    if pkg is None:
                        continue
                    if isinstance(pkg, (bytes, bytearray)):
                        pkg = bytes(pkg).decode("utf-8", errors="replace")
                    pkg = str(pkg).strip()
                    if not pkg:
                        continue
                    digest = _blob_to_hex(row[1])
                    idx = 2
                    ts_raw: Any = None
                    ver_raw: Any = None
                    if time_col:
                        ts_raw = row[idx]
                        idx += 1
                    if ver_col:
                        ver_raw = row[idx]

                    caveats: list[str] = list(_BASE_CAVEATS)
                    caveats.append(
                        "gass.db records that Play Protect SCANNED this APK. Scanning is "
                        "not installation and is not execution — on some configurations "
                        "APKs are scanned that were never installed."
                    )
                    caveats.append(
                        "the ledger is NOT cleared on uninstall, which is exactly why it "
                        "is useful: an entry here with no matching live package is "
                        "positive evidence the APK was once on the device."
                    )
                    caveats.append(
                        "the owning package of gass.db is contested in the literature "
                        "(com.google.android.gms vs com.android.vending) — the path "
                        "actually read is recorded in source_file. UNVERIFIED."
                    )
                    caveats.append(
                        f"schema discovered at runtime: table {table!r}, package column "
                        f"{pkg_col!r}, digest column {dig_col!r}"
                        + (f", timestamp column {time_col!r}" if time_col else "")
                        + ". Column semantics beyond the name are UNVERIFIED."
                    )

                    first_seen: Optional[str] = None
                    if ts_raw is not None:
                        ts_int = _int_or_none(ts_raw)
                        if ts_int is not None:
                            # Epoch seconds vs milliseconds is not distinguishable from
                            # the column name alone; decide on magnitude and say so.
                            if _MIN_PLAUSIBLE_MS <= ts_int <= _MAX_PLAUSIBLE_MS:
                                first_seen = _epoch_ms_to_iso(ts_int)
                            elif (
                                _MIN_PLAUSIBLE_MS // 1000
                                <= ts_int
                                <= _MAX_PLAUSIBLE_MS // 1000
                            ):
                                first_seen = _epoch_ms_to_iso(ts_int * 1000)
                                caveats.append(
                                    f"column {time_col!r} was read as epoch SECONDS "
                                    "(milliseconds gave an implausible date); the unit is "
                                    "not declared in the schema — UNVERIFIED"
                                )
                            else:
                                caveats.append(
                                    f"column {time_col!r} value {ts_raw!r} is not a "
                                    "plausible epoch in seconds or milliseconds; "
                                    "first_seen left unset"
                                )
                        else:
                            caveats.append(
                                f"column {time_col!r} value {ts_raw!r} is not numeric; "
                                "first_seen left unset"
                            )
                    else:
                        caveats.append(
                            "no timestamp column was found: gass.db is not confirmed to "
                            "carry an install or scan time, so NO install time is claimed"
                        )

                    if digest is None:
                        caveats.append("digest column present but NULL for this row")

                    out.append(
                        ApkDigestRecord(
                            package=pkg,
                            sha256=digest,
                            first_seen=first_seen,
                            source_file=path,
                            survives_uninstall=True,
                            caveats=caveats,
                            version_code=_int_or_none(ver_raw),
                        )
                    )
                except Exception:
                    # One bad row must never lose the rest of the table.
                    continue
    except sqlite3.Error as exc:
        _sink(
            caveats_out,
            f"{path!r} could not be opened read-only ({type(exc).__name__}: {exc})",
        )
    except OSError as exc:
        _sink(caveats_out, f"{path!r} could not be opened ({type(exc).__name__}: {exc})")
    finally:
        if con is not None:
            try:
                con.close()
            except sqlite3.Error:
                pass
    return out


# ---------------------------------------------------------------------------
# Correlation
# ---------------------------------------------------------------------------
def _normalise_installed_now(installed_now: Any) -> Optional[set[str]]:
    """Accept a set/list of names, or the dicts returned by parse_packages_list."""
    if installed_now is None:
        return None
    names: set[str] = set()
    for item in installed_now:
        if isinstance(item, str):
            names.add(item)
        elif isinstance(item, dict):
            n = item.get("package") or item.get("name")
            if n:
                names.add(str(n))
        elif isinstance(item, PackageRecord):
            names.add(item.package)
    return names


def _min_iso(values: Iterable[Optional[str]]) -> Optional[str]:
    vals = sorted(v for v in values if v)
    return vals[0] if vals else None


def _max_iso(values: Iterable[Optional[str]]) -> Optional[str]:
    vals = sorted(v for v in values if v)
    return vals[-1] if vals else None


def correlate_app_presence(
    packages: Iterable[PackageRecord],
    events: Iterable[UsageEvent],
    digests: Iterable[ApkDigestRecord],
    *,
    installed_now: Any = None,
) -> list[dict]:
    """Cross-correlate the three artefact families, per package.

    The finding that matters is the *set difference*: a package with usage events or a
    gass.db digest that is absent from the live package list was present and has since
    been removed. That is reported with ``Confidence.DELETION_DETECTED`` and the
    mechanism is named explicitly in the caveats.

    ``ever_executed`` is True ONLY when an actual execution-class usage event exists.
    It is never inferred from installation, from a scan record, or from a notification.
    """
    pkgs = list(packages)
    evs = list(events)
    digs = list(digests)

    live_names = _normalise_installed_now(installed_now)
    if live_names is None:
        # Without an explicit live list, packages.xml IS the live package list: it is
        # PackageManager's own record of what was installed at acquisition time.
        live_names = {p.package for p in pkgs}

    by_pkg_records: dict[str, list[PackageRecord]] = {}
    for p in pkgs:
        by_pkg_records.setdefault(p.package, []).append(p)
    by_pkg_events: dict[str, list[UsageEvent]] = {}
    for e in evs:
        by_pkg_events.setdefault(e.package, []).append(e)
    by_pkg_digests: dict[str, list[ApkDigestRecord]] = {}
    for d in digs:
        by_pkg_digests.setdefault(d.package, []).append(d)

    all_names = sorted(
        set(by_pkg_records) | set(by_pkg_events) | set(by_pkg_digests) | set(live_names)
    )

    out: list[dict] = []
    for name in all_names:
        precs = by_pkg_records.get(name, [])
        pevs = by_pkg_events.get(name, [])
        pdigs = by_pkg_digests.get(name, [])

        currently_installed = name in live_names
        exec_events = [e for e in pevs if e.event_type in EXECUTION_EVENT_TYPES]
        ever_executed = bool(exec_events)

        evidence_sources: list[str] = []
        if precs:
            evidence_sources.append("packages.xml")
        if pevs:
            evidence_sources.append("usagestats")
        if pdigs:
            evidence_sources.append("gass.db")
        if currently_installed and not precs:
            evidence_sources.append("live_package_list")

        ever_installed = bool(precs) or currently_installed or bool(pevs)

        caveats: list[str] = list(_BASE_CAVEATS)
        caveats.append(INSTALL_NOT_EXECUTION_CAVEAT)
        caveats.append(ABSENCE_CAVEAT)

        if name.startswith("UNRESOLVED_TOKEN_"):
            caveats.append(
                "this entry is an ORPHAN usagestats token, not a package name: the "
                "token->name mapping was deleted (AOSP does this synchronously on "
                "uninstall). It proves some package was removed; the identity is unknown."
            )

        if ever_executed:
            labels = sorted({e.event_label for e in exec_events})
            caveats.append(
                "ever_executed=True is based on actual usagestats event(s) "
                f"{labels}; it was NOT inferred from installation."
            )
        else:
            if pevs:
                seen = sorted({e.event_label for e in pevs})
                caveats.append(
                    f"usagestats records exist for this package ({seen}) but NONE of them "
                    "are execution-class events, so ever_executed is False. Notification, "
                    "standby-bucket and rollover records do not prove the app ran."
                )
            else:
                caveats.append(
                    "ever_executed=False: no execution-class usagestats event was found. "
                    "Because usagestats is purged on uninstall and retention is capped, "
                    "this does NOT establish that the app was never run."
                )

        if pdigs and not precs:
            caveats.append(
                "known only from the Play Protect scan ledger (gass.db): the ledger "
                "survives uninstall and covers sideloads, but a scan record alone does "
                "not prove the APK was ever installed."
            )

        if not currently_installed and (pevs or pdigs or precs):
            mechanism_bits: list[str] = []
            if pevs:
                mechanism_bits.append(
                    f"{len(pevs)} usagestats event(s) reference the package"
                )
            if pdigs:
                mechanism_bits.append("a Play Protect (gass.db) APK digest is retained")
            if precs and installed_now is not None:
                mechanism_bits.append(
                    "a packages.xml entry exists (possibly from a backup/reservecopy copy)"
                )
            confidence = Confidence.DELETION_DETECTED.value
            caveats.append(
                "DELETION DETECTED — package is absent from the live package list while "
                + " and ".join(mechanism_bits)
                + ". Mechanism: these artefacts outlive PackageManager's record; the "
                "package was present on this device and is not installed now. Removal by "
                "the user, by an app update rollback, by an MDM policy or by a factory "
                "operation cannot be distinguished from these artefacts alone."
            )
        elif currently_installed:
            confidence = Confidence.LIVE.value
        else:
            confidence = Confidence.CARVED_PARTIAL.value
            caveats.append(
                "package is not currently installed and no corroborating artefact was "
                "found; this entry carries no independent support."
            )

        first_seen = _min_iso(
            [p.first_install for p in precs]
            + [d.first_seen for d in pdigs]
            + [e.timestamp for e in pevs]
        )
        last_seen = _max_iso(
            [p.last_update for p in precs]
            + [p.apk_mtime for p in precs]
            + [e.timestamp for e in pevs]
            + [d.first_seen for d in pdigs]
        )

        user_ids = sorted({e.user_id for e in pevs if e.user_id})
        if len(user_ids) > 1:
            caveats.append(
                f"usage events for this package come from multiple Android user ids "
                f"{user_ids}; they are counted together here but the underlying records "
                "remain separate and must be reported per user."
            )

        out.append(
            {
                "package": name,
                "currently_installed": currently_installed,
                "ever_installed": bool(ever_installed),
                "ever_executed": ever_executed,
                "first_seen": first_seen,
                "last_seen": last_seen,
                "event_count": len(pevs),
                "execution_event_count": len(exec_events),
                "evidence_sources": evidence_sources,
                "user_ids": user_ids,
                "confidence": confidence,
                "tier": PRESENCE_TIER,
                "caveats": caveats,
            }
        )
    return out


def app_presence_summary(correlated: Iterable[dict]) -> dict:
    """Aggregate the correlation into a report-ready block."""
    rows = list(correlated)
    removed = [
        r
        for r in rows
        if r.get("confidence") == Confidence.DELETION_DETECTED.value
    ]
    executed = [r for r in rows if r.get("ever_executed")]
    installed = [r for r in rows if r.get("currently_installed")]
    orphan_tokens = [
        r for r in rows if str(r.get("package", "")).startswith("UNRESOLVED_TOKEN_")
    ]
    return {
        "total_packages": len(rows),
        "currently_installed": len(installed),
        "ever_installed": len([r for r in rows if r.get("ever_installed")]),
        "ever_executed": len(executed),
        "install_only": len([r for r in rows if not r.get("ever_executed")]),
        "uninstalled_with_evidence": len(removed),
        "removed_packages": sorted(r["package"] for r in removed),
        "executed_packages": sorted(r["package"] for r in executed),
        "orphan_usagestats_tokens": len(orphan_tokens),
        "total_usage_events": sum(int(r.get("event_count") or 0) for r in rows),
        "total_execution_events": sum(
            int(r.get("execution_event_count") or 0) for r in rows
        ),
        "tier": PRESENCE_TIER,
        "caveats": [
            INSTALL_NOT_EXECUTION_CAVEAT,
            CLOCK_CAVEAT,
            MULTIUSER_CAVEAT,
            ABSENCE_CAVEAT,
        ],
    }
