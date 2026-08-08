"""FBE / AFU-BFU encryption-state detection — a first-class forensic finding.

Why this module exists
----------------------
Every conclusion SNAGR draws about an Android device's *credential-encrypted* (CE)
data is conditional on one fact: whether the per-user CE class key is currently loaded
into the kernel keyring. That single bit decides whether ``/data/data/com.whatsapp`` is
an inventory of readable databases or an opaque pile of ciphertext.

Android's file-based encryption (fscrypt) splits ``/data`` into two key classes:

``DE`` (Device Encrypted)
    Key is unwrapped by ``vold`` at boot from a Keystore/TEE key bound to Verified Boot,
    **not** to the user's credential. Readable from boot onward — i.e. readable in BFU.

``CE`` (Credential Encrypted)
    Key is unwrapped only after the user's PIN/pattern/password is verified through
    Gatekeeper/Weaver → synthetic password → CE class key → installed into the kernel
    keyring. Readable only *after first unlock*.

Hence the two states this module reports:

``BFU`` (Before First Unlock)
    The CE class key for that user is **not** in the kernel keyring.

``AFU`` (After First Unlock)
    The CE class key **is** in the kernel keyring — regardless of whether the screen is
    currently locked.

Limitations and deliberate refusals
-----------------------------------
* **This is a per-user property.** User 0 can be AFU while a work profile (user 10) is
  BFU-equivalent because its CE key was evicted when the profile was stopped. We record
  per-user observations and report the primary user's state as the device-level headline.
* **``ro.crypto.state`` is posture, not state.** It reads ``encrypted`` in *both* AFU and
  BFU on an FBE device. Deriving AFU/BFU from it is the single most common triage error
  and this module never does it.
* **Keyguard is not the CE key.** A locked screen on a device that has been unlocked once
  since boot is still AFU. ``screen_locked`` is recorded as a *separate* field and never
  by itself forces a BFU verdict.
* **ENOENT under a CE root is not absence.** Without the key the kernel cannot hash a
  plaintext filename, so ``stat /data/data/com.android.settings`` fails as if the path did
  not exist. :func:`gate_ce_artifact` therefore never emits "not found" for a CE path —
  it emits "present, encrypted, inaccessible (BFU)".
* **Root is not decryption.** fscrypt is not a permission check; in BFU there is simply no
  key material in the kernel to derive per-file keys from. Root converts "no filesystem
  access" into "full DE access plus complete CE *metadata* inventory" — a real evidentiary
  gain, but not decryption.
* **Read-only.** Only ``getprop``, ``ls``, ``stat``, ``cat`` and ``dumpsys`` *queries* are
  issued. This module never reboots, never runs ``vdc cryptfs checkpw/verifypw/changepw``
  (those feed the hardware-rate-limited Gatekeeper counter and can trip a wipe policy), and
  never unlinks anything — fscrypt permits deletion *without* the key.
* Every probe failure degrades into a recorded observation plus a caveat. Nothing here
  ever fabricates a value, and an undetermined state is reported as ``"unknown"``, never
  optimistically as ``"afu"``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..models import now_iso

ShellFn = Callable[[str], str]


# --- Probe inventory ---------------------------------------------------------

#: ``getprop`` keys that carry encryption *posture*. None of these is a valid AFU/BFU
#: discriminator on FBE — they describe how the device is encrypted, not whether the
#: credential key is currently loaded. Collected for the audit trail and for the
#: FDE/unencrypted branches, where a couple of them genuinely are state-bearing.
ENCRYPTION_PROBE_PROPS: list[str] = [
    "ro.crypto.type",  # "file" (FBE) | "block" (FDE) | "none"
    "ro.crypto.state",  # "encrypted" | "unencrypted" | "unsupported" — POSTURE ONLY
    "ro.crypto.fs_crypto_blkdev",  # FDE only; present => FDE volume already mounted
    "ro.crypto.volume.options",  # "::v2" => fscrypt policy v2 (A11+)
    "ro.crypto.volume.contents_mode",  # legacy (A10 and lower)
    "ro.crypto.volume.filenames_mode",  # legacy (A10 and lower)
    "ro.crypto.volume.metadata.method",  # "dm-default-key" => metadata encryption
    "ro.crypto.volume.metadata.encryption",
    "ro.crypto.dm_default_key.options_format.version",
    "ro.crypto.uses_fs_ioc_add_encryption_key",
    "ro.crypto.scrypt_params",
    "ro.crypto.sdp",  # Samsung Knox Sensitive Data Protection (extra layer)
    "vold.decrypt",  # FDE only: trigger_restart_min_framework => still locked
    "vold.post_fs_data_done",
    "ro.build.version.sdk",
    "ro.build.version.release",
    "ro.product.first_api_level",  # the API the device LAUNCHED with — decides mandates
]

#: Credential-encrypted roots. In BFU these directories *exist* and are listable, but the
#: entry names are fscrypt "no-key names" (base64 of the ciphertext filename) and the file
#: contents are unreadable (``open()`` → ``ENOKEY``).
CE_CANARY_PATHS: list[str] = [
    "/data/data",  # primary CE canary (user 0)
    "/data/user/0",  # symlink alias of the above; a mismatch is a tampering signal
    "/data/system_ce/0",  # CE system storage (accounts_ce.db, recent_tasks, snapshots)
    "/data/media/0",  # internal "SD card" — DCIM, Download, app media
]

#: Device-encrypted roots. These MUST be plaintext in both AFU and BFU. If a DE canary is
#: also unreadable, the correct verdict is UNKNOWN/ABNORMAL (booted to recovery, DE key not
#: installed, or filesystem damage) — not BFU.
DE_CANARY_PATHS: list[str] = [
    "/data/user_de/0",  # per-app Direct Boot sandboxes
    "/data/system",  # packages.xml, packages.list, users/, netstats/
    "/data/misc",  # wifi config store, bluedroid, vold, adb_keys
]

#: Path prefixes whose contents are credential-encrypted. Matched on segment boundaries so
#: that ``/data/user_de/0`` never matches the ``/data/user`` prefix.
_CE_PATH_PREFIXES: tuple[str, ...] = (
    "/data/data",
    "/data/user",
    "/data/system_ce",
    "/data/misc_ce",
    "/data/vendor_ce",
    "/data/media",
    "/storage/emulated",
    "/storage/self/primary",
    "/sdcard",
    "/mnt/user",
    "/mnt/runtime",
)

#: Explicitly device-encrypted prefixes, checked first because several of them are string
#: prefixes of a CE prefix (``/data/user_de`` vs ``/data/user``).
_DE_PATH_PREFIXES: tuple[str, ...] = (
    "/data/user_de",
    "/data/system_de",
    "/data/misc_de",
    "/data/vendor_de",
    "/data/system",
    "/data/misc",
    "/data/app",
    "/data/local",
    "/data/property",
    "/data/anr",
    "/data/tombstones",
    "/data/unencrypted",
    "/metadata",
)

#: Adopted storage mirrors /data: ``user/`` and ``media/`` under the expand mount carry the
#: SAME per-user CE class key, so they are CE even though the volume key itself lives in DE
#: storage at /data/misc/vold/expand_<PARTUUID>.key.
_ADOPTED_CE_RE = re.compile(r"^/mnt/expand/[^/]+/(user|media)(/|$)")

# fscrypt pads filenames to a multiple of 16 bytes before encrypting, so a no-key name is
# base64 of 16k bytes => ceil(64k/3) characters: 22, 43, 64, 86, 107, 128, ...
_NOKEY_LENGTHS: frozenset[int] = frozenset(-(-64 * k // 3) for k in range(1, 17))

# Two alphabets are seen in the wild: the classic Android ext4/f2fs set (A-Za-z0-9+,) and
# upstream base64url (A-Za-z0-9-_). Notably absent from both: '.', which every Android
# package name contains — that is the cheapest discriminator we have.
_NOKEY_CHARS_RE = re.compile(r"^[A-Za-z0-9+,_-]+$")

_LS_LONG_PREFIX_RE = re.compile(r"^[dlbcps-][rwxsStT-]{9}[.+]?\s")
_LS_TIME_RE = re.compile(r"\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:\s+[+-]\d{4})?\s+(.+)$")

_ERR_MISSING = ("no such file or directory", "does not exist", "cannot find")
_ERR_DENIED = (
    "permission denied",
    "operation not permitted",
    "access denied",
    "not allowed",
    "not found",  # e.g. "su: not found" — the probe could not run, so nothing was observed
    "inaccessible",
)
_ERR_ENOKEY = ("required key not available", "enokey")

#: The mandatory wording for a CE artifact observed while the CE key is absent. Deliberately
#: a module constant so the report layer and the tests reference the same string.
BFU_REPORT_AS = "present, encrypted, inaccessible (BFU)"

#: Stated verbatim in the caveats of every FBE device. Root is a UID/capability property;
#: fscrypt enforcement is not a permission check.
ROOT_IS_NOT_DECRYPTION_CAVEAT = (
    "Root is not decryption: on a file-based-encrypted device the kernel has no CE class "
    "key material before first unlock, so there is nothing for root to bypass. Root in BFU "
    "upgrades 'no filesystem access' to 'full DE access plus a complete CE metadata "
    "inventory' — it does not decrypt credential-encrypted content."
)


# --- Data model --------------------------------------------------------------
@dataclass
class EncryptionState:
    """The device's encryption posture and, separately, its CE key state.

    ``unlock_state`` is the forensically load-bearing field. It is derived from direct
    filesystem observation where possible and from the framework's own view otherwise;
    when neither is available it stays ``"unknown"`` and a caveat says so explicitly.
    """

    crypto_type: str = ""  # "file" | "block" | "none" | "" (unknown / prop absent)
    crypto_state: str = ""  # "encrypted" | "unencrypted" | "unsupported" | ""
    sdk: int = 0
    android_release: str = ""
    metadata_encryption: bool = False  # Android 11+ dm-default-key
    unlock_state: str = "unknown"  # "afu" | "bfu" | "not_encrypted" | "unknown"
    unlock_evidence: list[str] = field(default_factory=list)
    screen_locked: Optional[bool] = None  # keyguard only — NOT an AFU/BFU discriminator
    ce_accessible: Optional[bool] = None  # None => not observed, not "no"
    de_accessible: Optional[bool] = None
    fbe_mandatory: bool = False  # sdk >= 29
    caveats: list[str] = field(default_factory=list)
    probes: dict[str, str] = field(default_factory=dict)  # raw output, audit trail

    # --- supplementary context (not part of the minimum contract) ---
    posture: str = "UNKNOWN"  # FBE_V2 | FBE_V1 | FDE | UNENCRYPTED | UNKNOWN
    first_api_level: int = 0
    hw_wrapped_keys: bool = False
    policy_version: int = 0
    contents_mode: str = ""
    filenames_mode: str = ""
    strong_auth_after_boot: Optional[bool] = None
    root_available: bool = False
    confidence: str = "low"  # "high" | "medium" | "low"
    per_user: list[dict[str, Any]] = field(default_factory=list)
    collected_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        """Plain JSON-safe projection. No enums, no Paths, no None-typed surprises."""
        return {
            "crypto_type": self.crypto_type,
            "crypto_state": self.crypto_state,
            "sdk": int(self.sdk),
            "android_release": self.android_release,
            "metadata_encryption": bool(self.metadata_encryption),
            "unlock_state": self.unlock_state,
            "unlock_evidence": list(self.unlock_evidence),
            "screen_locked": self.screen_locked,
            "ce_accessible": self.ce_accessible,
            "de_accessible": self.de_accessible,
            "fbe_mandatory": bool(self.fbe_mandatory),
            "caveats": list(self.caveats),
            "probes": {str(k): str(v) for k, v in self.probes.items()},
            "posture": self.posture,
            "first_api_level": int(self.first_api_level),
            "hw_wrapped_keys": bool(self.hw_wrapped_keys),
            "policy_version": int(self.policy_version),
            "contents_mode": self.contents_mode,
            "filenames_mode": self.filenames_mode,
            "strong_auth_after_boot": self.strong_auth_after_boot,
            "root_available": bool(self.root_available),
            "confidence": self.confidence,
            "per_user": [dict(u) for u in self.per_user],
            "collected_at": self.collected_at,
        }


# --- Property parsing --------------------------------------------------------
def parse_getprop_dump(output: str) -> dict[str, str]:
    """Parse a bare ``getprop`` dump (``[key]: [value]`` lines) into a plain dict.

    Tolerant by design: a truncated or interleaved line is skipped rather than raising,
    because a partially readable property dump is still evidence.
    """
    props: dict[str, str] = {}
    if not output:
        return props
    for line in output.splitlines():
        m = re.match(r"^\s*\[([^\]]+)\]\s*:\s*\[(.*)\]\s*$", line)
        if m:
            props[m.group(1).strip()] = m.group(2).strip()
    return props


def _clean(value: Any) -> str:
    """Normalise a single getprop value. Handles bare values and ``[value]`` wrappers."""
    if value is None:
        return ""
    s = str(value).strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1].strip()
    # `getprop <missing-key>` prints an empty line; some shells echo the literal string.
    if s.lower() in {"", "null", "none-set"}:
        return ""
    return s


def _as_int(value: str) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def parse_encryption_props(props: dict[str, str]) -> dict[str, Any]:
    """Normalise raw ``getprop`` values into typed encryption facts.

    Returns posture only. Nothing in here is an AFU/BFU discriminator on FBE — see the
    module docstring. The returned ``notes`` list carries the honest qualifications
    (e.g. "``ro.crypto.type`` absent; absence is not evidence of 'none'").
    """
    props = props or {}
    get = lambda k: _clean(props.get(k, ""))  # noqa: E731 — local shorthand, read-only

    crypto_type = get("ro.crypto.type").lower()
    crypto_state = get("ro.crypto.state").lower()
    sdk = _as_int(get("ro.build.version.sdk"))
    release = get("ro.build.version.release")
    first_api = _as_int(get("ro.product.first_api_level"))
    blkdev = get("ro.crypto.fs_crypto_blkdev")
    vold_decrypt = get("vold.decrypt")
    options = get("ro.crypto.volume.options")
    contents_mode = get("ro.crypto.volume.contents_mode")
    filenames_mode = get("ro.crypto.volume.filenames_mode")
    metadata_method = get("ro.crypto.volume.metadata.method").lower()
    metadata_enc = get("ro.crypto.volume.metadata.encryption")
    dm_default_key_ver = get("ro.crypto.dm_default_key.options_format.version")

    notes: list[str] = []

    metadata_encryption = bool(
        metadata_method == "dm-default-key" or metadata_enc or dm_default_key_ver
    )

    # fscrypt policy version. "::v2" is explicit; A11+ devices are v2 in practice even when
    # the property is absent, but we only *assert* v2 when we can see it.
    policy_version = 0
    if "v2" in options:
        policy_version = 2
    elif contents_mode or filenames_mode:
        # These props only survive on the legacy (A10 and lower) path, which is v1-era.
        policy_version = 1
    elif crypto_type == "file" and sdk >= 30:
        policy_version = 2
        notes.append(
            "fscrypt policy version inferred from sdk>=30, not observed in a property."
        )

    # Hardware-wrapped keys (A14+, fstab `wrappedkey_v0`) cannot be confirmed from
    # properties alone — the authoritative source is the fstab fileencryption= flag.
    hw_wrapped_keys = "wrappedkey" in options.lower()

    if crypto_state == "unencrypted" or crypto_type == "none":
        posture = "UNENCRYPTED"
    elif crypto_state == "unsupported":
        posture = "UNENCRYPTED"
        notes.append(
            "ro.crypto.state=unsupported — no crypto support; confirm with lsattr "
            "(no 'E' flag on /data/data)."
        )
    elif crypto_type == "file":
        posture = "FBE_V2" if policy_version == 2 else "FBE_V1"
    elif crypto_type == "block":
        posture = "FDE"
        notes.append(
            "Legacy full-disk encryption (dm-crypt). There is no DE/CE split: once the "
            "volume is mounted, all of /data is readable."
        )
    else:
        posture = "UNKNOWN"
        if crypto_state == "encrypted":
            notes.append(
                "ro.crypto.type absent while ro.crypto.state=encrypted — some Android 8.x/9 "
                "FBE builds omit the property. Absence is NOT evidence of 'none'."
            )
        else:
            notes.append(
                "Neither ro.crypto.type nor ro.crypto.state was readable; encryption "
                "posture is undetermined."
            )

    # The task contract defines fbe_mandatory as sdk >= 29. first_api_level is the property
    # that actually governs the AOSP mandate, so a divergence is recorded rather than hidden.
    fbe_mandatory = sdk >= 29
    if fbe_mandatory and first_api and first_api < 29:
        notes.append(
            f"Device runs sdk={sdk} but launched with first_api_level={first_api}; an "
            "upgraded device may legitimately still be FDE (ro.crypto.type=block)."
        )
    if first_api >= 30 and not metadata_encryption:
        notes.append(
            f"first_api_level={first_api} implies metadata encryption is mandatory, but no "
            "dm-default-key property was observed — property may simply be absent."
        )

    return {
        "crypto_type": crypto_type,
        "crypto_state": crypto_state,
        "sdk": sdk,
        "android_release": release,
        "first_api_level": first_api,
        "metadata_encryption": metadata_encryption,
        "fbe_mandatory": fbe_mandatory,
        "posture": posture,
        "policy_version": policy_version,
        "contents_mode": contents_mode,
        "filenames_mode": filenames_mode,
        "hw_wrapped_keys": hw_wrapped_keys,
        "fs_crypto_blkdev": blkdev,
        "vold_decrypt": vold_decrypt,
        "notes": notes,
    }


# --- Directory-listing classification ----------------------------------------
def looks_like_nokey_name(name: str) -> bool:
    """True if ``name`` has the shape of an fscrypt no-key (ciphertext) filename.

    A no-key name is base64 of a 16-byte-padded ciphertext filename, so its length is one
    of 22, 43, 64, 86, ... and its alphabet excludes '.', which every Android package name
    contains. Lengths outside that set are accepted only with corroborating high-entropy
    evidence (mixed case *and* digits), because OEM kernels vary.
    """
    if not name or not _NOKEY_CHARS_RE.match(name):
        return False
    if len(name) in _NOKEY_LENGTHS:
        return True
    if len(name) < 22:
        return False
    has_upper = any(c.isupper() for c in name)
    has_lower = any(c.islower() for c in name)
    has_digit = any(c.isdigit() for c in name)
    return has_upper and has_lower and has_digit


def _extract_listing_names(listing: str) -> list[str]:
    """Pull entry names out of ``ls`` output, long-format or plain, skipping '.' and '..'."""
    names: list[str] = []
    for raw in listing.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        low = line.strip().lower()
        if low.startswith("total ") or low == "total":
            continue
        if _LS_LONG_PREFIX_RE.match(line):
            m = _LS_TIME_RE.search(line)
            name = m.group(1).strip() if m else line.split()[-1]
            # A symlink line ends with "name -> target"; the entry is the left-hand side.
            if " -> " in name:
                name = name.split(" -> ", 1)[0].strip()
            candidates = [name]
        else:
            # Plain `ls` output — usually one entry per line, sometimes columnised.
            candidates = line.split()
        for c in candidates:
            c = c.strip().rstrip("/")
            if c and c not in (".", ".."):
                names.append(c)
    return names


def classify_ce_listing(listing: str) -> str:
    """Classify an ``ls`` of a CE (or DE) directory.

    Returns one of ``"readable"``, ``"encrypted"``, ``"denied"``, ``"empty"``, ``"missing"``.

    ``"encrypted"`` means the directory was successfully listed but the entry names are
    fscrypt no-key names — the directory is *present and populated*, just unreadable. That
    distinction is the whole point: it must never collapse into ``"missing"``.
    """
    if listing is None:
        return "empty"
    text = str(listing)
    if not text.strip():
        return "empty"

    low = text.lower()
    # The definitive BFU proof, if a cat(1) error leaked into the captured output.
    if any(tok in low for tok in _ERR_ENOKEY):
        return "encrypted"
    if any(tok in low for tok in _ERR_MISSING):
        return "missing"
    if any(tok in low for tok in _ERR_DENIED):
        return "denied"

    names = _extract_listing_names(text)
    if not names:
        return "empty"

    nokey = sum(1 for n in names if looks_like_nokey_name(n))
    # The research threshold: >90% of entries matching the no-key shape is a no-key listing.
    return "encrypted" if (nokey / len(names)) >= 0.9 else "readable"


# --- Framework-side (dumpsys) parsing ----------------------------------------
def parse_user_unlock_states(dumpsys_output: str) -> dict[int, str]:
    """Map user id -> ``"afu"`` | ``"bfu"`` | ``"unknown"`` from ``dumpsys user`` output.

    Also understands the ``UserState{id=0, state=RUNNING_UNLOCKED}`` shape emitted by
    ``dumpsys activity users``. ``RUNNING_UNLOCKED`` (or an "Unlock time" line) means that
    user's CE key is loaded; ``RUNNING_LOCKED``/``BOOTING`` means it is not.

    This is the framework's *own view*. It can lag or go stale, so a direct ENOKEY
    observation always outranks it.
    """
    states: dict[int, str] = {}
    if not dumpsys_output:
        return states

    current: Optional[int] = None
    for line in str(dumpsys_output).splitlines():
        m_inline = re.search(
            r"UserState\{\s*id\s*=\s*(\d+)\s*,\s*state\s*=\s*([A-Z_]+)", line
        )
        if m_inline:
            states[int(m_inline.group(1))] = _user_state_to_ce(m_inline.group(2))
            continue

        m_user = re.search(r"UserInfo\{\s*(\d+)\s*:", line)
        if m_user:
            current = int(m_user.group(1))
            states.setdefault(current, "unknown")
            continue

        if current is None:
            continue

        m_state = re.search(r"\bState:\s*([A-Z_]+)", line)
        if m_state:
            states[current] = _user_state_to_ce(m_state.group(1))
            continue

        if re.search(r"\bUnlock time:", line):
            # An unlock timestamp exists => the CE key was installed at least once.
            states[current] = "afu"
    return states


def _user_state_to_ce(state: str) -> str:
    s = (state or "").upper()
    if s == "RUNNING_UNLOCKED":
        return "afu"
    if s in {"RUNNING_LOCKED", "BOOTING", "STOPPING", "SHUTDOWN", "STOPPED"}:
        return "bfu"
    if s == "RUNNING_UNLOCKING":
        # Mid-transition: the key install has been requested but is not confirmed.
        return "unknown"
    return "unknown"


def parse_keyguard(dumpsys_output: str) -> dict[str, Any]:
    """Extract keyguard/trust facts. These describe the *screen*, never the CE key."""
    result: dict[str, Any] = {"screen_locked": None, "strong_auth_after_boot": None}
    if not dumpsys_output:
        return result
    text = str(dumpsys_output)

    m = re.search(r"\bdeviceLocked\s*=\s*(true|false)", text, re.IGNORECASE)
    if m is None:
        m = re.search(r"\bshowing\s*=\s*(true|false)", text, re.IGNORECASE)
    if m is None:
        m = re.search(r"\bmShowingLockscreen\s*=\s*(true|false)", text, re.IGNORECASE)
    if m is not None:
        result["screen_locked"] = m.group(1).lower() == "true"

    if "STRONG_AUTH_REQUIRED_AFTER_BOOT" in text:
        result["strong_auth_after_boot"] = True
    elif re.search(r"strongAuthRequired\s*=", text):
        result["strong_auth_after_boot"] = False
    return result


# --- Live detection ----------------------------------------------------------
def _maybe_su(cmd: str, root_available: bool) -> str:
    """Wrap a read-only command in ``su -c`` when root is available."""
    if not root_available:
        return cmd
    return "su -c '" + cmd.replace("'", "'\\''") + "'"


def _run(
    shell: Optional[ShellFn], cmd: str, state: EncryptionState
) -> str:
    """Execute one read-only probe. A failure is recorded, never raised, never faked."""
    if shell is None:
        state.probes[cmd] = "<probe not run: no shell callable supplied>"
        return ""
    try:
        out = shell(cmd)
    except Exception as exc:  # noqa: BLE001 — a dead probe must not abort triage
        msg = f"<probe failed: {type(exc).__name__}: {exc}>"
        state.probes[cmd] = msg
        state.caveats.append(
            f"Probe '{cmd}' failed ({type(exc).__name__}); the corresponding observation "
            "is absent, not negative."
        )
        return ""
    text = "" if out is None else str(out)
    state.probes[cmd] = text
    return text


def detect_encryption_state(
    shell: Optional[ShellFn], *, root_available: bool = False
) -> EncryptionState:
    """Determine encryption posture and CE key state using read-only probes only.

    ``shell`` is a callable ``(cmd: str) -> str``. This function issues ``getprop``, ``ls``,
    ``cat`` and ``dumpsys`` queries and nothing else — no reboot, no ``vdc cryptfs``
    credential operations, no writes, no deletions.

    Never raises. Any probe that fails, returns nothing, or is unavailable becomes an
    observation plus a caveat, and an undetermined result stays ``unlock_state="unknown"``.
    """
    state = EncryptionState(root_available=bool(root_available))

    try:
        _collect_properties(state, shell)
        de_class = _probe_canaries(state, shell, DE_CANARY_PATHS, "de")
        ce_class = _probe_canaries(state, shell, CE_CANARY_PATHS, "ce")
        enokey_seen = _probe_enokey(state, shell, ce_class)
        framework = _probe_framework(state, shell)
        _decide(state, de_class, ce_class, enokey_seen, framework)
        _finalise_caveats(state)
    except Exception as exc:  # noqa: BLE001 — the contract is "must never raise"
        state.unlock_state = "unknown"
        state.confidence = "low"
        state.caveats.append(
            f"Encryption-state detection aborted internally ({type(exc).__name__}: {exc}); "
            "state was NOT determined. Treat every credential-encrypted artifact as "
            "accessibility-undetermined."
        )
    return state


def _collect_properties(state: EncryptionState, shell: Optional[ShellFn]) -> None:
    """One bulk ``getprop``, falling back to per-key reads if the dump is unusable."""
    dump = _run(shell, "getprop", state)
    props = parse_getprop_dump(dump)
    if not props:
        for key in ENCRYPTION_PROBE_PROPS:
            val = _clean(_run(shell, f"getprop {key}", state))
            if val:
                props[key] = val

    if not props:
        state.caveats.append(
            "No encryption properties could be read from the device; encryption posture is "
            "undetermined (this is 'not observed', not 'unencrypted')."
        )

    parsed = parse_encryption_props(props)
    state.crypto_type = parsed["crypto_type"]
    state.crypto_state = parsed["crypto_state"]
    state.sdk = parsed["sdk"]
    state.android_release = parsed["android_release"]
    state.first_api_level = parsed["first_api_level"]
    state.metadata_encryption = parsed["metadata_encryption"]
    state.fbe_mandatory = parsed["fbe_mandatory"]
    state.posture = parsed["posture"]
    state.policy_version = parsed["policy_version"]
    state.contents_mode = parsed["contents_mode"]
    state.filenames_mode = parsed["filenames_mode"]
    state.hw_wrapped_keys = parsed["hw_wrapped_keys"]
    state.probes["_parsed_props"] = repr(parsed)
    state.caveats.extend(parsed["notes"])


def _probe_canaries(
    state: EncryptionState, shell: Optional[ShellFn], paths: list[str], kind: str
) -> dict[str, str]:
    """List each canary path read-only and classify what came back."""
    results: dict[str, str] = {}
    for path in paths:
        cmd = _maybe_su(f"ls -la {path}/", state.root_available)
        out = _run(shell, cmd, state)
        verdict = classify_ce_listing(out)
        results[path] = verdict
        state.unlock_evidence.append(f"{kind.upper()} canary {path} -> {verdict}")
    return results


def _probe_enokey(
    state: EncryptionState, shell: Optional[ShellFn], ce_class: dict[str, str]
) -> bool:
    """Attempt the definitive BFU proof: ``open()`` on a CE regular file returning ENOKEY.

    Only attempted with root and only against a directory already classified as no-key.
    ``cat`` is read-only; nothing is created, modified or unlinked.
    """
    if not state.root_available:
        return False
    target_dir = next((p for p, v in ce_class.items() if v == "encrypted"), None)
    if target_dir is None:
        return False

    listing = state.probes.get(_maybe_su(f"ls -la {target_dir}/", True), "")
    entries = [n for n in _extract_listing_names(listing) if looks_like_nokey_name(n)]
    if not entries:
        return False

    inner_cmd = _maybe_su(f"ls -la {target_dir}/{entries[0]}/", True)
    inner = _run(shell, inner_cmd, state)
    filename = ""
    for line in inner.splitlines():
        if line.startswith("-"):  # a regular file, not a directory
            m = _LS_TIME_RE.search(line)
            candidate = (m.group(1) if m else line.split()[-1]).strip()
            if candidate not in (".", ".."):
                filename = candidate
                break
    if not filename:
        return False

    probe_cmd = _maybe_su(
        f"cat {target_dir}/{entries[0]}/{filename} 2>&1 | head -c 200",
        True,
    )
    out = _run(shell, probe_cmd, state).lower()
    if any(tok in out for tok in _ERR_ENOKEY):
        state.unlock_evidence.append(
            f"ENOKEY on open() of {target_dir}/{entries[0]}/{filename} "
            "('Required key not available') — definitive proof the CE class key is absent."
        )
        return True
    return False


def _probe_framework(state: EncryptionState, shell: Optional[ShellFn]) -> dict[str, Any]:
    """Collect the framework's own view: per-user CE state, keyguard, strong-auth."""
    users_out = _run(shell, "dumpsys user", state)
    user_states = parse_user_unlock_states(users_out)
    if not user_states:
        act_out = _run(shell, "dumpsys activity users", state)
        user_states = parse_user_unlock_states(act_out)

    trust_out = _run(shell, "dumpsys trust", state)
    keyguard = parse_keyguard(trust_out)
    if keyguard["screen_locked"] is None:
        win_out = _run(shell, "dumpsys window policy", state)
        win_kg = parse_keyguard(win_out)
        if win_kg["screen_locked"] is not None:
            keyguard["screen_locked"] = win_kg["screen_locked"]
        if keyguard["strong_auth_after_boot"] is None:
            keyguard["strong_auth_after_boot"] = win_kg["strong_auth_after_boot"]

    state.screen_locked = keyguard["screen_locked"]
    state.strong_auth_after_boot = keyguard["strong_auth_after_boot"]

    for uid in sorted(user_states):
        state.per_user.append(
            {
                "user_id": uid,
                "ce_state": user_states[uid],
                "source": "dumpsys user State",
            }
        )
        state.unlock_evidence.append(
            f"dumpsys reports user {uid} CE state = {user_states[uid]}"
        )
    if state.screen_locked is not None:
        state.unlock_evidence.append(
            f"Keyguard screen_locked={state.screen_locked} (screen state only — this is "
            "NOT an AFU/BFU discriminator)."
        )
    if state.strong_auth_after_boot:
        state.unlock_evidence.append(
            "dumpsys trust strongAuthRequired=STRONG_AUTH_REQUIRED_AFTER_BOOT — the "
            "credential has not been entered since boot (proxy evidence for BFU)."
        )
    return {"user_states": user_states, **keyguard}


def _decide(
    state: EncryptionState,
    de_class: dict[str, str],
    ce_class: dict[str, str],
    enokey_seen: bool,
    framework: dict[str, Any],
) -> None:
    """Apply the observation -> state decision table. Direct evidence outranks framework."""
    de_values = list(de_class.values())
    ce_values = list(ce_class.values())
    user_states: dict[int, str] = framework.get("user_states") or {}
    # User 0 is the headline; a work profile can differ and is preserved in per_user.
    primary = user_states.get(0) or (
        user_states[min(user_states)] if user_states else "unknown"
    )

    if "readable" in de_values:
        state.de_accessible = True
    elif de_values and all(v in {"encrypted"} for v in de_values):
        state.de_accessible = False

    if "readable" in ce_values:
        state.ce_accessible = True
    elif "encrypted" in ce_values:
        state.ce_accessible = False

    # --- Unencrypted ---------------------------------------------------------
    if state.posture == "UNENCRYPTED":
        state.unlock_state = "not_encrypted"
        state.confidence = "high" if state.crypto_state else "medium"
        state.unlock_evidence.append(
            f"ro.crypto.type={state.crypto_type or '<absent>'} / "
            f"ro.crypto.state={state.crypto_state or '<absent>'} — /data is not encrypted, "
            "so AFU/BFU is not a meaningful axis for this device."
        )
        state.caveats.append(
            "Reported as UNENCRYPTED from properties; corroborate with `lsattr -d "
            "/data/data` showing no 'E' flag before relying on this."
        )
        return

    # --- Legacy full-disk encryption -----------------------------------------
    if state.crypto_type == "block":
        blkdev = _clean(
            parse_getprop_dump(state.probes.get("getprop", "")).get(
                "ro.crypto.fs_crypto_blkdev", ""
            )
        ) or _clean(state.probes.get("getprop ro.crypto.fs_crypto_blkdev", ""))
        vold = _clean(
            parse_getprop_dump(state.probes.get("getprop", "")).get("vold.decrypt", "")
        ) or _clean(state.probes.get("getprop vold.decrypt", ""))
        if blkdev:
            state.unlock_state = "afu"
            state.confidence = "high"
            state.unlock_evidence.append(
                f"FDE: ro.crypto.fs_crypto_blkdev={blkdev} — the dm-crypt volume is mounted, "
                "so /data is decrypted. There is no DE/CE split on FDE."
            )
        elif "trigger_restart_min_framework" in vold:
            state.unlock_state = "bfu"
            state.confidence = "high"
            state.unlock_evidence.append(
                "FDE: vold.decrypt=trigger_restart_min_framework and no fs_crypto_blkdev — "
                "the encrypted volume is NOT mounted; /data is a tmpfs."
            )
            state.caveats.append(
                "FDE locked: /data is a tmpfs, so user directories are genuinely absent from "
                "the mounted view. The ciphertext still exists on the raw userdata partition "
                "— report this as 'encrypted volume not mounted', never as 'no user data'."
            )
        else:
            state.unlock_state = "unknown"
            state.confidence = "low"
            state.caveats.append(
                "FDE device but neither ro.crypto.fs_crypto_blkdev nor vold.decrypt was "
                "readable; mount state was NOT determined."
            )
        return

    # --- File-based encryption (and unknown posture) -------------------------
    # DE control probe first: if even DE is unreadable, this is abnormal, not BFU.
    if de_values and "encrypted" in de_values and "readable" not in de_values:
        state.unlock_state = "unknown"
        state.confidence = "low"
        state.caveats.append(
            "ABNORMAL: the device-encrypted control paths are themselves unreadable. DE must "
            "be plaintext in both AFU and BFU, so this indicates /data was mounted without "
            "the DE key (recovery/TWRP), the DE key failed to install, or filesystem damage. "
            "This is NOT a BFU finding."
        )
        return

    if enokey_seen:
        state.unlock_state = "bfu"
        state.confidence = "high"
    elif "encrypted" in ce_values:
        state.unlock_state = "bfu"
        state.confidence = "high"
        state.unlock_evidence.append(
            "CE roots list successfully but every entry is an fscrypt no-key name — the CE "
            "class key is not in the kernel keyring."
        )
        if primary == "afu":
            state.caveats.append(
                "ANOMALY: the framework reports user 0 as unlocked while the CE directory "
                "listing shows no-key names. The direct filesystem observation is "
                "authoritative; the framework view may be stale or refer to another user."
            )
    elif "readable" in ce_values:
        state.unlock_state = "afu"
        state.confidence = "high"
        state.unlock_evidence.append(
            "CE roots list with plaintext package names — the CE class key is loaded."
        )
    elif primary in {"afu", "bfu"}:
        state.unlock_state = primary
        state.confidence = "medium"
        state.unlock_evidence.append(
            f"No direct CE filesystem observation was possible; state taken from the "
            f"framework's own report for user 0 ({primary})."
        )
        state.caveats.append(
            "CE key state was derived from `dumpsys` rather than from a direct ENOKEY probe. "
            "The framework's view can lag or be stale; confidence is medium."
        )
        if primary == "bfu" and state.strong_auth_after_boot:
            state.confidence = "high"
            state.unlock_evidence.append(
                "Corroborated by strongAuthRequired=STRONG_AUTH_REQUIRED_AFTER_BOOT."
            )
    elif ce_values and all(v == "empty" for v in ce_values):
        state.unlock_state = "unknown"
        state.confidence = "low"
        state.caveats.append(
            "All CE roots listed as empty and no framework state was available. This may mean "
            "user storage was never prepared or that the listing was suppressed — it is NOT "
            "sufficient to conclude BFU, and certainly not AFU."
        )
    else:
        state.unlock_state = "unknown"
        state.confidence = "low"
        state.caveats.append(
            "CE key state was NOT determined: no readable CE/DE listing and no usable "
            "`dumpsys user` output. Do not assume AFU. Every credential-encrypted artifact "
            "must be reported as accessibility-undetermined until this is re-probed."
        )

    if state.unlock_state == "bfu" and primary == "afu":
        pass  # anomaly already recorded above


def _finalise_caveats(state: EncryptionState) -> None:
    """Attach the standing forensic caveats that apply regardless of outcome."""
    if state.fbe_mandatory or state.crypto_type == "file":
        state.caveats.append(ROOT_IS_NOT_DECRYPTION_CAVEAT)
    if state.fbe_mandatory:
        state.caveats.append(
            f"sdk={state.sdk} (>=29): file-based encryption is mandatory on devices launched "
            "at this API level, so credential-encrypted app data is unreadable until the "
            "user's credential has been entered at least once since boot."
        )
    if state.hw_wrapped_keys or (state.sdk >= 34 and state.crypto_type == "file"):
        state.caveats.append(
            "Android 14+ devices commonly use hardware-wrapped keys (wrappedkey_v0): the raw "
            "class key never exists in kernel-readable memory, so RAM-based key recovery does "
            "not apply even with full kernel code execution."
        )
    if state.unlock_state == "afu":
        state.caveats.append(
            "State is AFU and it is one-way: a reboot, battery exhaustion, crash, or a "
            "vendor 'auto-restart after N days locked' policy silently converts AFU to BFU "
            "and cannot be undone without the credential. Keep the device powered and RF "
            "isolated; do not reboot and do not restart system_server."
        )
    if state.screen_locked and state.unlock_state == "afu":
        state.caveats.append(
            "The screen is locked but the device is AFU. Keyguard is a UI gate, not a "
            "cryptographic one — with root, full filesystem acquisition is possible despite "
            "the lockscreen."
        )
    if state.unlock_state == "bfu":
        state.caveats.append(
            "BFU: credential-encrypted content is present on disk but cryptographically "
            "inaccessible. Device-encrypted artifacts (packages.list, accounts_de.db, "
            "WifiConfigStore.xml, bt_config.conf, telephony.db, netstats) remain available, "
            "as does full CE directory metadata (inode, size, mode, uid/gid, timestamps)."
        )
        state.caveats.append(
            "Do not delete or move anything under a CE root: fscrypt permits unlink/rmdir "
            "WITHOUT the key, so a stray write would destroy evidence that is otherwise "
            "recoverable once the credential is obtained."
        )
    if state.unlock_state == "unknown":
        state.caveats.append(
            "Encryption state NOT determined. This is explicitly not a finding of 'unlocked' "
            "and not a finding of 'no data'."
        )
    # De-duplicate while preserving order — repeated probes can append the same note twice.
    seen: set[str] = set()
    deduped: list[str] = []
    for c in state.caveats:
        if c not in seen:
            seen.add(c)
            deduped.append(c)
    state.caveats = deduped


# --- Artifact gating ---------------------------------------------------------
def is_ce_path(device_path: str) -> bool:
    """True if ``device_path`` lives under a credential-encrypted root.

    Segment-aware: ``/data/user_de/0/com.foo`` is DE and must not match the ``/data/user``
    CE prefix. Adopted-storage ``user/`` and ``media/`` subtrees are CE because they are
    protected by the same per-user CE class key as internal storage.
    """
    if not device_path:
        return False
    p = "/" + str(device_path).strip().replace("\\", "/").strip("/")
    while "//" in p:
        p = p.replace("//", "/")
    if p != "/":
        p = p.rstrip("/")

    if _ADOPTED_CE_RE.match(p):
        return True
    for de in _DE_PATH_PREFIXES:
        if p == de or p.startswith(de + "/"):
            return False
    for ce in _CE_PATH_PREFIXES:
        if p == ce or p.startswith(ce + "/"):
            return True
    return False


def gate_ce_artifact(state: EncryptionState, device_path: str) -> dict[str, Any]:
    """Decide how an artifact at ``device_path`` must be reported given the CE key state.

    The critical rule: a credential-encrypted path on a BFU device is **present, encrypted
    and inaccessible** — it is never "not found". Without the CE key the kernel cannot hash
    a plaintext filename, so a lookup fails with ENOENT even though the data is on disk.
    Reporting that as absence produces a false exculpatory finding.
    """
    ce = is_ce_path(device_path)
    unlock = state.unlock_state if state is not None else "unknown"

    if not ce:
        return {
            "accessible": True,
            "reason": (
                "Device-encrypted (or unencrypted) path — its key is unwrapped at boot from "
                "a Verified-Boot-bound key, so it is readable regardless of whether the "
                "user's credential has been entered."
            ),
            "report_as": "accessible (device-encrypted storage)",
            "confidence_note": (
                "Readability here is independent of AFU/BFU. Access still requires adequate "
                "privilege (root or an appropriately scoped shell)."
            ),
            "ce_path": False,
            "unlock_state": unlock,
        }

    if unlock == "bfu":
        user = _user_from_path(device_path)
        return {
            "accessible": False,
            "reason": (
                f"fscrypt CE class key for user {user} is not loaded into the kernel keyring "
                "(device is BFU); open() on any file beneath this path returns ENOKEY, and a "
                "lookup by plaintext name returns ENOENT because the kernel cannot hash the "
                "name without the key."
            ),
            "report_as": BFU_REPORT_AS,
            "confidence_note": (
                "Presence is established by directory metadata (no-key entry names, inode, "
                "size, mode, uid/gid, timestamps); content is unavailable. Root does not "
                "change this — there is no key material to bypass. Absence of readable "
                "content here is evidence of encryption, never of absence of data."
            ),
            "ce_path": True,
            "unlock_state": unlock,
        }

    if unlock == "afu":
        return {
            "accessible": True,
            "reason": (
                "Credential-encrypted path and the CE class key for this user is loaded "
                "(device is AFU), so contents decrypt transparently on read."
            ),
            "report_as": "accessible (CE key loaded, AFU)",
            "confidence_note": (
                "AFU is volatile: a reboot or power loss reverts the device to BFU and this "
                "path becomes unreadable again. Acquire now and re-probe the state before "
                "each subsequent acquisition phase."
            ),
            "ce_path": True,
            "unlock_state": unlock,
        }

    if unlock == "not_encrypted":
        return {
            "accessible": True,
            "reason": (
                "/data is not encrypted on this device, so the CE/DE distinction does not "
                "apply to this path."
            ),
            "report_as": "accessible (device not encrypted)",
            "confidence_note": (
                "Confirm the unencrypted posture with `lsattr -d` showing no 'E' flag before "
                "relying on it; a property-only determination is corroborating, not proof."
            ),
            "ce_path": True,
            "unlock_state": unlock,
        }

    return {
        "accessible": False,
        "reason": (
            "Credential-encrypted path, and the device's CE key state could not be "
            "determined. Accessibility is undetermined — this is an unresolved probe, not "
            "an observation about whether data exists."
        ),
        "report_as": "present or absent — accessibility undetermined (encryption state unknown)",
        "confidence_note": (
            "Re-probe the encryption state before drawing any conclusion about this path. "
            "An undetermined state must never be rendered as unlocked or as empty."
        ),
        "ce_path": True,
        "unlock_state": unlock,
    }


def _user_from_path(device_path: str) -> str:
    """Best-effort Android user id for a CE path; '0' is the default primary user."""
    m = re.search(r"^/data/(?:user|system_ce|misc_ce|vendor_ce|media)/(\d+)", device_path or "")
    if m:
        return m.group(1)
    return "0"


# --- Examiner-facing summary -------------------------------------------------
def encryption_summary(state: EncryptionState) -> dict[str, Any]:
    """Counts plus a one-paragraph, court-readable explanation of what the state means."""
    if state is None:
        state = EncryptionState()

    counts = {
        "probes_run": len(state.probes),
        "probes_failed": sum(
            1 for v in state.probes.values() if str(v).startswith("<probe ")
        ),
        "evidence_items": len(state.unlock_evidence),
        "caveats": len(state.caveats),
        "users_observed": len(state.per_user),
        "users_afu": sum(1 for u in state.per_user if u.get("ce_state") == "afu"),
        "users_bfu": sum(1 for u in state.per_user if u.get("ce_state") == "bfu"),
        "users_unknown": sum(
            1 for u in state.per_user if u.get("ce_state") not in {"afu", "bfu"}
        ),
    }

    posture_txt = {
        "FBE_V2": "file-based encryption (fscrypt policy v2)",
        "FBE_V1": "file-based encryption (fscrypt policy v1)",
        "FDE": "legacy full-disk encryption (dm-crypt)",
        "UNENCRYPTED": "no encryption on /data",
        "UNKNOWN": "an undetermined encryption posture",
    }.get(state.posture, "an undetermined encryption posture")

    android_txt = (
        f"Android {state.android_release} (API {state.sdk})"
        if state.android_release or state.sdk
        else "an Android version that could not be read"
    )

    if state.unlock_state == "bfu":
        body = (
            "The device is BEFORE FIRST UNLOCK (BFU): the credential-encrypted class key for "
            "the user is not loaded into the kernel keyring. Credential-encrypted app data — "
            "/data/data, /data/user/<N>, /data/system_ce and internal media — is physically "
            "present on the device but cryptographically inaccessible; every filename in "
            "those directories is ciphertext and every read returns ENOKEY. This is a "
            "statement about encryption, NOT about whether data exists: an artifact that "
            "cannot be read here must never be reported as absent. Root does not help, "
            "because fscrypt is not a permission check and there is no key material to "
            "bypass. Device-encrypted evidence remains fully available: the installed-app "
            "inventory, account records, Wi-Fi and Bluetooth history, SIM/subscription data, "
            "per-app network usage, and complete metadata (size, timestamps, ownership) for "
            "every encrypted directory."
        )
    elif state.unlock_state == "afu":
        body = (
            "The device is AFTER FIRST UNLOCK (AFU): the credential-encrypted class key is "
            "loaded, so credential-encrypted app data decrypts transparently on read. A "
            "locked screen does not change this — keyguard is a user-interface gate, not a "
            "cryptographic one. This state is volatile and one-way: any reboot, battery "
            "exhaustion, crash, or vendor auto-restart policy returns the device to BFU and "
            "the data becomes unreadable again without the user's credential. Acquisition "
            "should proceed immediately, with the device kept powered and RF isolated, and "
            "the state re-verified before each subsequent acquisition phase."
        )
    elif state.unlock_state == "not_encrypted":
        body = (
            "/data on this device is not encrypted, so the before/after-first-unlock "
            "distinction does not apply. All user data is readable subject only to "
            "privilege. This posture was read from device properties and should be "
            "corroborated with an fscrypt policy-flag check before it is relied upon in a "
            "report."
        )
    else:
        body = (
            "The credential-encryption key state could NOT be determined. This is an "
            "unresolved probe, not a finding. It must not be rendered as 'unlocked', and it "
            "must not be rendered as 'no data present'. Until the state is established, "
            "every credential-encrypted path should be reported as accessibility-"
            "undetermined, and the determination should be re-attempted before acquisition."
        )

    explanation = (
        f"{android_txt} with {posture_txt}"
        f"{'; metadata encryption (dm-default-key) is in use' if state.metadata_encryption else ''}. "
        f"{body}"
    )

    return {
        "unlock_state": state.unlock_state,
        "posture": state.posture,
        "confidence": state.confidence,
        "screen_locked": state.screen_locked,
        "fbe_mandatory": state.fbe_mandatory,
        "metadata_encryption": state.metadata_encryption,
        "counts": counts,
        "evidence": list(state.unlock_evidence),
        "caveats": list(state.caveats),
        "explanation": explanation,
        "generated_at": now_iso(),
    }
