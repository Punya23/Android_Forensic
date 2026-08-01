"""Encrypted-messenger triage: prove *presence* without ever claiming *content*.

Forensic purpose
----------------
Signal, Molly, Session, Threema and Wickr store their message history in
SQLCipher databases whose passphrase is sealed by the Android Keystore. A root
pull yields the ciphertext and a *hardware-wrapped* key blob — and nothing else.
The single most common failure of a triage tool is to look at such a file, fail
to open it, and report "no messages found". That is a false negative that can
change the outcome of a case.

This module exists to make the opposite claim, precisely and defensibly:

    "signal.db is PRESENT, 41.3 MB, last written 2026-07-30T18:22:07Z,
     SQLCipher-encrypted, key sealed in the Android Keystore under alias
     'SignalSecret'; content is NOT recoverable by this method."

Limitations (read before trusting any output here)
--------------------------------------------------
* **No decryption is attempted or simulated anywhere in this file.** There is no
  code path that produces plaintext message content, and ``recoverable`` is
  hard-wired ``False`` for every SQLCipher target.
* SQLCipher files carry **no magic bytes** — bytes 0..15 are a random PBKDF2
  salt and everything after is AES-CBC ciphertext, by design indistinguishable
  from random data. Classifying a headerless file as "sqlcipher" is therefore an
  *inference* from size/entropy/context, never a proof. ``detect_encryption()``
  reports that inference with ``confidence = carved``, not ``live``.
* SQLCipher KDF parameters (kdf_iter, page size, HMAC algorithm) are **not
  stored in the file**. Any tool printing "KDF iterations: 256000" from a
  heuristic is fabricating. We report library *defaults* as defaults, clearly
  labelled, and note that apps override them (Threema ships ``kdf_iter = 1``).
* A path we never pulled is not an absent file. ``encrypted_apps_summary()``
  keeps "not present on device" and "not acquired" in separate buckets.
"""

from __future__ import annotations

import hashlib
import math
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional, Union

from ..config import Confidence

PathLike = Union[str, Path]

# The only header we can assert positively. Everything else is inference.
SQLITE_MAGIC: bytes = b"SQLite format 3\x00"

# SQLCipher reserves per-page space for the random IV + page HMAC. v2/v3 = 48
# (16-byte IV + 20-byte SHA1 tag rounded to an AES block), v4 = 80 (16 + 64
# SHA512). A file with the plain SQLite magic AND one of these values in byte 20
# is a SQLCipher DB running `cipher_plaintext_header_size` — the single most
# dangerous false negative in naive detection.
SQLCIPHER_RESERVED_SIZES = (48, 80)

# Read budget for detection. We deliberately touch only the header: the point of
# this module is metadata, and a 2 GB DB must not be slurped to classify it.
_HEADER_BYTES = 64

# Above this we skip hashing rather than stall a field triage; the skip is
# recorded as a caveat, never silently.
MAX_HASH_BYTES = 2 * 1024 * 1024 * 1024

# Guard on attachment-directory walks so a pathological tree cannot hang triage.
MAX_ATTACHMENT_FILES = 200_000

_KEYSTORE_REASON = (
    "SQLCipher passphrase is sealed with an AndroidKeystore AES-GCM key; the "
    "Keystore key is non-exportable and hardware-bound (TEE/StrongBox), and "
    "under File-Based Encryption it is released only after the lockscreen "
    "credential is verified post-boot. A filesystem image therefore contains "
    "the ciphertext and the wrapped key blob but nothing that can unwrap it: "
    "root does NOT equal decryption."
)

_NO_DECRYPT_CAVEAT = (
    "This tool does not attempt, simulate or emulate decryption. Content is "
    "reported as not recoverable, not as absent."
)


# --- Target catalogue --------------------------------------------------------
# Paths are *candidates* probed against a staged acquisition tree; nothing here
# is assumed to exist. `verified` records whether the path/key-storage claim is
# corroborated by primary sources (app source code) or is second-hand.
ENCRYPTED_APP_TARGETS: dict[str, dict[str, Any]] = {
    "org.thoughtcrime.securesms": {
        "label": "Signal",
        "db_paths": [
            "/data/data/org.thoughtcrime.securesms/databases/signal.db",
            "/data/data/org.thoughtcrime.securesms/databases/signal-jobmanager.db",
            "/data/data/org.thoughtcrime.securesms/databases/signal-logs.db",
            "/data/data/org.thoughtcrime.securesms/databases/signal-megaphone.db",
            "/data/data/org.thoughtcrime.securesms/databases/signal-key-value.db",
            "/data/data/org.thoughtcrime.securesms/databases/signal-local-metrics.db",
        ],
        "attachment_paths": [
            "/data/data/org.thoughtcrime.securesms/app_parts",
        ],
        "pref_paths": [
            "/data/data/org.thoughtcrime.securesms/shared_prefs/org.thoughtcrime.securesms_preferences.xml",
        ],
        "encryption": "SQLCipher",
        "key_storage": (
            "32 random bytes (DatabaseSecret) AES-GCM-sealed under AndroidKeystore "
            "alias 'SignalSecret', stored base64 in shared prefs key "
            "'pref_database_encrypted_secret'"
        ),
        "verified": True,
    },
    "im.molly.app": {
        "label": "Molly (Signal fork)",
        "db_paths": [
            "/data/data/im.molly.app/databases/signal.db",
            "/data/data/im.molly.app/databases/signal-jobmanager.db",
            "/data/data/im.molly.app/databases/signal-logs.db",
        ],
        "attachment_paths": ["/data/data/im.molly.app/app_parts"],
        "pref_paths": [
            "/data/data/im.molly.app/shared_prefs/im.molly.app_preferences.xml",
        ],
        "encryption": "SQLCipher",
        "key_storage": (
            "as Signal, plus the shared_prefs themselves are AES-256-CBC encrypted "
            "with an Argon2id-derived passphrase key entangled with a Keystore-resident "
            "HMAC key — no offline attack path"
        ),
        "verified": True,
    },
    "network.loki.messenger": {
        "label": "Session",
        "db_paths": [
            # Session is a Signal fork and kept Signal's filename; one secondary
            # source claims session.db, so we probe both and flag the conflict.
            "/data/data/network.loki.messenger/databases/signal.db",
            "/data/data/network.loki.messenger/databases/session.db",
        ],
        "attachment_paths": ["/data/data/network.loki.messenger/app_parts"],
        "pref_paths": [
            "/data/data/network.loki.messenger/shared_prefs/network.loki.messenger_preferences.xml",
        ],
        "encryption": "SQLCipher",
        "key_storage": (
            "inherited from Signal verbatim: AndroidKeystore alias 'SignalSecret', "
            "AES-GCM-sealed secret in 'pref_database_encrypted_secret'"
        ),
        "verified": False,  # signal.db vs session.db filename unresolved
    },
    "ch.threema.app": {
        "label": "Threema",
        "db_paths": ["/data/data/ch.threema.app/databases/threema4.db"],
        "attachment_paths": ["/data/media/0/Android/data/ch.threema.app"],
        "pref_paths": [
            "/data/data/ch.threema.app/files/master_key.dat",
            "/data/data/ch.threema.app/files/key.dat",
        ],
        "encryption": "SQLCipher",
        "key_storage": (
            "master_key.dat protobuf: field 1 = plaintext intermediate key (key "
            "material on disk) OR field 2 = Argon2id-protected intermediate (user "
            "passphrase required). SQLCipher opened with kdf_iter = 1 and a raw key"
        ),
        "verified": True,
    },
    "com.mywickr.wickr2": {
        "label": "Wickr Me",
        "db_paths": ["/data/data/com.mywickr.wickr2/databases/wickr_db"],
        "attachment_paths": [
            "/data/data/com.mywickr.wickr2/files/enc",
            "/data/data/com.mywickr.wickr2/cache/dec",
        ],
        "pref_paths": [
            "/data/data/com.mywickr.wickr2/files/kcd.wic",
            "/data/data/com.mywickr.wickr2/files/kck.wic",
        ],
        "encryption": "SQLCipher",
        "key_storage": (
            "kcd.wic / kck.wic key containers chained off the per-package Android ID "
            "(ssaid) in 2019-era builds; the scheme changed after 2019 and current-build "
            "behaviour is UNVERIFIED"
        ),
        "verified": False,
    },
    "com.wickr.pro": {
        "label": "Wickr Pro",
        "db_paths": ["/data/data/com.wickr.pro/databases/wickr_db"],
        "attachment_paths": [
            "/data/data/com.wickr.pro/files/enc",
            "/data/data/com.wickr.pro/cache/dec",
        ],
        "pref_paths": [
            "/data/data/com.wickr.pro/files/kcd.wic",
            "/data/data/com.wickr.pro/files/kck.wic",
        ],
        "encryption": "SQLCipher",
        "key_storage": "as Wickr Me; Pro/Enterprise key handling UNVERIFIED",
        "verified": False,
    },
    "org.telegram.messenger": {
        # Included deliberately: Telegram's local store is NOT SQLCipher. Putting
        # it in the same catalogue lets the report say so explicitly instead of
        # leaving the examiner to assume "messenger => encrypted".
        "label": "Telegram",
        "db_paths": [
            "/data/data/org.telegram.messenger/files/cache4.db",
            "/data/data/org.telegram.messenger/files/cache4.db-wal",
        ],
        "attachment_paths": [
            "/data/media/0/Android/media/org.telegram.messenger/Telegram",
        ],
        "pref_paths": [
            "/data/data/org.telegram.messenger/shared_prefs/userconfing.xml",
        ],
        "encryption": "none",
        "key_storage": (
            "cache4.db is a plain unencrypted SQLite file; only Telegram 'Secret Chats' "
            "are E2E and those are not stored in readable form"
        ),
        "verified": True,
    },
}


# --- helpers -----------------------------------------------------------------
def _iso(epoch: Optional[float]) -> Optional[str]:
    """Epoch seconds -> ISO-8601 UTC with trailing Z. None stays None."""
    if epoch is None:
        return None
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(epoch)))
    except (OverflowError, OSError, ValueError):
        return None


def _shannon(data: bytes) -> float:
    """Shannon entropy in bits/byte. Over a 64-byte sample the theoretical max is
    6.0, so thresholds here are calibrated for the sample size, not for 8.0."""
    if not data:
        return 0.0
    counts: dict[int, int] = {}
    for b in data:
        counts[b] = counts.get(b, 0) + 1
    n = len(data)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def _printable_ratio(data: bytes) -> float:
    if not data:
        return 0.0
    return sum(1 for b in data if 0x20 <= b <= 0x7E) / len(data)


def _sha256(path: Path, size: int) -> tuple[str, Optional[str]]:
    """Chunked hash. Returns (hex, caveat-or-None) — an oversized file is skipped
    loudly, never silently."""
    if size > MAX_HASH_BYTES:
        return "", (
            f"sha256 not computed: file is {size} bytes, above the "
            f"{MAX_HASH_BYTES}-byte field-triage hashing budget"
        )
    h = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
    except OSError as exc:
        return "", f"sha256 not computed: {exc.__class__.__name__}: {exc}"
    return h.hexdigest(), None


def _path_variants(device_path: str) -> list[str]:
    """/data/data/<pkg> and /data/user/0/<pkg> are the same directory; a work
    profile lives under /data/user/<N>. Probe the common aliases."""
    out = [device_path]
    if device_path.startswith("/data/data/"):
        out.append("/data/user/0/" + device_path[len("/data/data/") :])
    elif device_path.startswith("/data/user/0/"):
        out.append("/data/data/" + device_path[len("/data/user/0/") :])
    return out


def _candidates(root: Path, device_path: str) -> list[Path]:
    """Map a device path onto a staged local tree.

    We accept three shapes because staging conventions differ between operators:
    a full mirror (``<root>/data/data/pkg/...``), an alias mirror
    (``/data/user/0`` form) and a package-rooted mirror (``<root>/pkg/...``).
    """
    seen: list[Path] = []
    for variant in _path_variants(device_path):
        rel = variant.lstrip("/")
        cand = root / rel
        if cand not in seen:
            seen.append(cand)
        for marker in ("/data/data/", "/data/user/0/", "/data/media/0/"):
            if marker in variant:
                short = root / variant.split(marker, 1)[1]
                if short not in seen:
                    seen.append(short)
    return seen


def _resolve(root: Path, device_path: str) -> Optional[Path]:
    for cand in _candidates(root, device_path):
        try:
            if cand.exists():
                return cand
        except OSError:
            continue
    return None


# --- detection ---------------------------------------------------------------
def detect_encryption(path: PathLike) -> dict[str, Any]:
    """Classify a file from its first 64 bytes plus its size on disk.

    Returns a dict with keys ``encrypted``, ``format``
    (``plain-sqlite`` | ``sqlcipher`` | ``unknown-binary`` | ``empty`` | ``missing``),
    ``evidence``, ``salt_hex`` and ``confidence``.

    ``confidence`` is ``live`` only when a byte-level magic proves the answer.
    A headerless high-entropy page-aligned file is classified ``sqlcipher`` at
    ``carved`` confidence: SQLCipher writes no magic at all, so this is a
    reasoned inference and the evidence string says so.
    """
    p = Path(path)
    out: dict[str, Any] = {
        "path": str(p),
        "encrypted": False,
        "format": "missing",
        "evidence": "",
        "salt_hex": "",
        "confidence": Confidence.LIVE.value,
        "size_bytes": 0,
        "page_size": None,
        "reserved_bytes": None,
    }

    try:
        if not p.exists():
            out["evidence"] = (
                "no file at this path in the staged acquisition. This is NOT evidence "
                "that the file is absent from the device unless the path was actually "
                "pulled — see the acquisition manifest."
            )
            return out
        if not p.is_file():
            out["evidence"] = "path exists but is not a regular file (directory or special file)"
            return out
        size = p.stat().st_size
        with p.open("rb") as fh:
            head = fh.read(_HEADER_BYTES)
    except OSError as exc:
        out["format"] = "missing"
        out["evidence"] = f"unreadable: {exc.__class__.__name__}: {exc}"
        return out

    out["size_bytes"] = size

    if size == 0 or not head:
        out["format"] = "empty"
        out["evidence"] = (
            "zero-length file. An empty file is a real observation (app initialised "
            "the path but never wrote), not a parse failure."
        )
        return out

    # --- 1. plain SQLite magic present -------------------------------------
    if head[:16] == SQLITE_MAGIC:
        page_size = int.from_bytes(head[16:18], "big") if len(head) >= 18 else 0
        if page_size == 1:
            page_size = 65536
        reserved = head[20] if len(head) >= 21 else 0
        out["page_size"] = page_size
        out["reserved_bytes"] = reserved

        if reserved in SQLCIPHER_RESERVED_SIZES:
            # cipher_plaintext_header_size (SQLCipher 4): bytes 0..31 are left in
            # the clear so the file still looks like SQLite. Everything else is
            # ciphertext, and the salt is NOT in the file.
            out["encrypted"] = True
            out["format"] = "sqlcipher"
            out["confidence"] = Confidence.CARVED_PARTIAL.value
            out["evidence"] = (
                f"SQLite magic present but reserved-bytes-per-page = {reserved} "
                f"(SQLCipher v{'4' if reserved == 80 else '2/3'} per-page IV+HMAC "
                "reservation). Consistent with PRAGMA cipher_plaintext_header_size — "
                "a SQLCipher database wearing a plaintext header. INFERENCE from one "
                "header byte, not a proof; a plain SQLite file could in principle "
                "reserve the same space. Note the PBKDF2 salt is then absent from the "
                "file and must be supplied out-of-band, so even a correct key alone "
                "will not open it."
            )
            return out

        out["format"] = "plain-sqlite"
        out["encrypted"] = False
        out["confidence"] = Confidence.LIVE.value
        out["evidence"] = (
            f"'SQLite format 3\\x00' magic at offset 0, page size {page_size}, "
            f"reserved-per-page {reserved}. Unencrypted SQLite — parse normally."
        )
        return out

    # --- 2. no magic: SQLCipher inference ----------------------------------
    entropy = _shannon(head)
    printable = _printable_ratio(head)
    distinct16 = len(set(head[:16]))
    page_aligned = size >= 512 and (size % 4096 == 0 or size % 1024 == 0)
    high_entropy = entropy >= 5.0 and distinct16 >= 12 and printable < 0.70

    if page_aligned and high_entropy:
        out["encrypted"] = True
        out["format"] = "sqlcipher"
        out["salt_hex"] = head[:16].hex()
        out["confidence"] = Confidence.CARVED_PARTIAL.value
        hint = "4096 (SQLCipher v4 default)" if size % 4096 == 0 else "1024 (SQLCipher v1-v3 default)"
        out["evidence"] = (
            "No file-format magic anywhere in the header, header entropy "
            f"{entropy:.2f} bits/byte over {len(head)} bytes ({distinct16}/16 distinct "
            f"bytes in the first 16), printable ratio {printable:.2f}, file size "
            f"{size} bytes is a multiple of {hint}. This matches SQLCipher's stated "
            "design (random 16-byte PBKDF2 salt followed by AES-256-CBC ciphertext, "
            "deliberately indistinguishable from random data). CLASSIFICATION IS AN "
            "INFERENCE, NOT A PROOF: sqleet, SEE, a custom AES wrapper or an unrelated "
            "compressed/encrypted blob would look identical. salt_hex is the candidate "
            "PBKDF2 salt on that assumption. SQLCipher version and KDF parameters are "
            "NOT recoverable from ciphertext and are not reported here."
        )
        return out

    out["format"] = "unknown-binary"
    out["encrypted"] = False
    out["confidence"] = Confidence.CARVED_PARTIAL.value
    out["evidence"] = (
        f"No recognised magic; header entropy {entropy:.2f} bits/byte, printable ratio "
        f"{printable:.2f}, size {size} bytes "
        f"({'page-aligned' if page_aligned else 'not a 1024/4096 multiple'}). "
        "Encryption status UNDETERMINED — 'encrypted: false' here means 'not shown to "
        "be encrypted', not 'shown to be plaintext'."
    )
    return out


# --- artifact ----------------------------------------------------------------
@dataclass
class EncryptedAppArtifact:
    """One file belonging to an encrypted-messenger target, described honestly.

    ``recoverable`` is False for every SQLCipher target and no code path in this
    module can set it True for one; ``status`` is written so that it can be
    pasted straight into a report without an examiner having to reinterpret it.
    """

    app: str
    package: str
    path: str
    exists: bool = False
    size_bytes: int = 0
    modified: Optional[str] = None
    encryption_format: str = "missing"
    key_storage: str = ""
    recoverable: bool = False
    status: str = ""
    attachment_count: int = 0
    attachment_bytes: int = 0
    sha256: str = ""
    confidence: str = Confidence.LIVE.value
    caveats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "app": self.app,
            "package": self.package,
            "path": self.path,
            "exists": bool(self.exists),
            "size_bytes": int(self.size_bytes),
            "modified": self.modified,
            "encryption_format": self.encryption_format,
            "key_storage": self.key_storage,
            "recoverable": bool(self.recoverable),
            "status": self.status,
            "attachment_count": int(self.attachment_count),
            "attachment_bytes": int(self.attachment_bytes),
            "sha256": self.sha256,
            "confidence": self.confidence,
            "caveats": list(self.caveats),
        }


def _dir_stats(path: Path) -> dict[str, Any]:
    """File count / byte total / mtime range for an attachment directory.

    Attachment *counts* are exact even though attachment *contents* are opaque:
    Signal's ``part<19-digit>.mms`` files map 1:1 onto rows of the encrypted
    attachment table, so the count bounds how much media was exchanged.
    """
    out: dict[str, Any] = {
        "path": str(path),
        "count": 0,
        "bytes": 0,
        "first_modified": None,
        "last_modified": None,
        "truncated": False,
    }
    first: Optional[float] = None
    last: Optional[float] = None
    try:
        for child in path.rglob("*"):
            try:
                if not child.is_file():
                    continue
                st = child.stat()
            except OSError:
                continue  # unreadable entry: skip the record, never raise
            out["count"] += 1
            out["bytes"] += st.st_size
            if first is None or st.st_mtime < first:
                first = st.st_mtime
            if last is None or st.st_mtime > last:
                last = st.st_mtime
            if out["count"] >= MAX_ATTACHMENT_FILES:
                out["truncated"] = True
                break
    except OSError:
        pass
    out["first_modified"] = _iso(first)
    out["last_modified"] = _iso(last)
    return out


def _status_for(spec: dict[str, Any], det: dict[str, Any]) -> tuple[str, bool, list[str]]:
    """Compose the report-ready status line, the recoverable flag and caveats."""
    caveats: list[str] = [_NO_DECRYPT_CAVEAT]
    encryption = str(spec.get("encryption", "unknown"))
    fmt = det["format"]

    if encryption == "SQLCipher" and fmt in ("sqlcipher", "unknown-binary", "plain-sqlite"):
        status = "present, encrypted (SQLCipher/Keystore), content not recoverable"
        caveats.append(_KEYSTORE_REASON)
        caveats.append(
            "SQLCipher KDF parameters (kdf_iter, page size, HMAC algorithm) are not "
            "stored in the file and are NOT reported; published defaults are v3 = "
            "64000/1024/SHA1 and v4 = 256000/4096/SHA512, but apps override them "
            "(Threema ships kdf_iter = 1)."
        )
        if fmt == "plain-sqlite":
            # Declared-SQLCipher target that reads as plain SQLite: either an
            # exported/decrypted copy or an unencrypted-secret legacy build.
            # Flag it loudly rather than silently trusting either reading.
            status = (
                "present, reads as PLAIN SQLite despite being a declared SQLCipher "
                "target — verify provenance before relying on it"
            )
            caveats.append(
                "Header shows unencrypted SQLite where SQLCipher was expected. Possible "
                "causes: an already-decrypted export was staged, a legacy build using "
                "pref_database_unencrypted_secret, or a mis-staged file. Do not treat "
                "as original evidence without confirming the acquisition path."
            )
        if fmt == "unknown-binary":
            caveats.append(
                "Header did not meet the SQLCipher size/entropy heuristic; the file is "
                "still treated as encrypted because the target app is known to use "
                "SQLCipher, but the byte-level classification is inconclusive."
            )
        return status, False, caveats

    if encryption == "none" and fmt == "plain-sqlite":
        return (
            "present, not encrypted (plain SQLite), content readable by the standard parser",
            True,
            caveats,
        )

    if fmt == "empty":
        return (
            "present but zero-length, no content to recover",
            False,
            caveats,
        )

    if fmt == "sqlcipher":
        return (
            "present, encrypted (SQLCipher inferred from entropy/alignment), content not recoverable",
            False,
            caveats + [_KEYSTORE_REASON],
        )

    return (
        f"present, format not classified ({fmt}), content not recoverable by this tool",
        False,
        caveats,
    )


def scan_encrypted_apps(
    root: PathLike,
    *,
    targets: Optional[dict[str, dict[str, Any]]] = None,
) -> list[EncryptedAppArtifact]:
    """Probe a staged acquisition tree for every catalogued encrypted-app file.

    ``root`` is a local directory mirroring pulled device paths. Only files that
    are actually there are returned: a path that was never pulled must not be
    reported as "absent from the device". That distinction is carried by
    :func:`encrypted_apps_summary` via ``paths_attempted``.
    """
    base = Path(root)
    catalogue = targets if targets is not None else ENCRYPTED_APP_TARGETS
    out: list[EncryptedAppArtifact] = []

    for package, spec in catalogue.items():
        label = str(spec.get("label", package))
        attach = _collect_attachments(base, spec)
        primary_assigned = False

        for device_path in spec.get("db_paths", []) or []:
            local = _resolve(base, device_path)
            if local is None:
                continue  # never claim absence for a path we may not have pulled

            det = detect_encryption(local)
            if det["format"] == "missing":
                continue

            try:
                mtime = local.stat().st_mtime
            except OSError:
                mtime = None

            status, recoverable, caveats = _status_for(spec, det)
            digest, hash_caveat = _sha256(local, det["size_bytes"])
            if hash_caveat:
                caveats.append(hash_caveat)
            caveats.insert(0, f"detection evidence: {det['evidence']}")
            if not spec.get("verified", False):
                caveats.append(
                    "This path/key-storage entry is NOT corroborated by a primary "
                    "source; treat the app attribution as provisional."
                )

            art = EncryptedAppArtifact(
                app=label,
                package=package,
                path=device_path,
                exists=True,
                size_bytes=int(det["size_bytes"]),
                modified=_iso(mtime),
                encryption_format=det["format"],
                key_storage=str(spec.get("key_storage", "")),
                recoverable=bool(recoverable),
                status=status,
                sha256=digest,
                confidence=str(det["confidence"]),
                caveats=caveats,
            )
            # Attachment totals belong to the package, so they are attached once
            # (to the first surviving DB artifact) rather than double-counted.
            if not primary_assigned and attach["count"]:
                art.attachment_count = int(attach["count"])
                art.attachment_bytes = int(attach["bytes"])
                art.caveats.append(
                    "attachment_count/attachment_bytes are package-level totals for "
                    f"{', '.join(attach['dirs'])} — file counts and sizes are exact, "
                    "file contents are individually encrypted and were not opened."
                )
                if attach.get("truncated"):
                    art.caveats.append(
                        f"attachment walk stopped at the {MAX_ATTACHMENT_FILES}-file cap; "
                        "counts are a lower bound"
                    )
                primary_assigned = True

            out.append(art)

    return out


def _collect_attachments(base: Path, spec: dict[str, Any]) -> dict[str, Any]:
    total = {"count": 0, "bytes": 0, "dirs": [], "truncated": False}
    for device_dir in spec.get("attachment_paths", []) or []:
        local = _resolve(base, device_dir)
        if local is None or not local.is_dir():
            continue
        stats = _dir_stats(local)
        total["count"] += stats["count"]
        total["bytes"] += stats["bytes"]
        total["dirs"].append(device_dir)
        total["truncated"] = total["truncated"] or stats["truncated"]
    return total


def encrypted_apps_summary(
    items: Iterable[EncryptedAppArtifact],
    *,
    paths_attempted: Optional[Iterable[str]] = None,
) -> dict[str, Any]:
    """Roll up a scan, keeping 'absent' and 'never acquired' strictly apart.

    ``paths_attempted`` is the set of device paths the acquisition layer actually
    tried (from the pull manifest). Without it we cannot say a file is absent
    from the device, only that we do not have it — and the summary says exactly
    that.
    """
    arts = list(items)
    attempted: Optional[set[str]] = None
    if paths_attempted is not None:
        attempted = set()
        for p in paths_attempted:
            attempted.update(_path_variants(str(p)))

    found_paths = {a.path for a in arts}
    by_app: dict[str, dict[str, Any]] = {}
    for a in arts:
        entry = by_app.setdefault(
            a.app,
            {
                "package": a.package,
                "files": 0,
                "bytes": 0,
                "encrypted_files": 0,
                "recoverable_files": 0,
                "attachment_count": 0,
                "attachment_bytes": 0,
                "last_modified": None,
            },
        )
        entry["files"] += 1
        entry["bytes"] += a.size_bytes
        if a.encryption_format == "sqlcipher":
            entry["encrypted_files"] += 1
        if a.recoverable:
            entry["recoverable_files"] += 1
        entry["attachment_count"] += a.attachment_count
        entry["attachment_bytes"] += a.attachment_bytes
        if a.modified and (entry["last_modified"] is None or a.modified > entry["last_modified"]):
            entry["last_modified"] = a.modified

    not_present: list[dict[str, str]] = []
    not_acquired: list[dict[str, str]] = []
    for package, spec in ENCRYPTED_APP_TARGETS.items():
        for device_path in spec.get("db_paths", []) or []:
            if device_path in found_paths:
                continue
            row = {
                "package": package,
                "app": str(spec.get("label", package)),
                "path": device_path,
            }
            if attempted is not None and device_path in attempted:
                row["basis"] = (
                    "acquisition attempted this exact path and the file was not there — "
                    "absence is an observation"
                )
                not_present.append(row)
            else:
                row["basis"] = (
                    "path was never acquired (not in the pull manifest) — absence is "
                    "UNKNOWN, not observed"
                )
                not_acquired.append(row)

    caveats = [
        _NO_DECRYPT_CAVEAT,
        "'not_acquired' entries are NOT evidence of absence: nothing was pulled from "
        "those paths, so the app may well be installed with a full message history.",
    ]
    if attempted is None:
        caveats.insert(
            0,
            "No acquisition manifest was supplied (paths_attempted=None), so EVERY "
            "path we did not find is classified 'not acquired'. Absence cannot be "
            "distinguished from non-acquisition in this run.",
        )

    return {
        "total_files": len(arts),
        "encrypted_files": sum(1 for a in arts if a.encryption_format == "sqlcipher"),
        "recoverable_files": sum(1 for a in arts if a.recoverable),
        "total_bytes": sum(a.size_bytes for a in arts),
        "attachment_count": sum(a.attachment_count for a in arts),
        "attachment_bytes": sum(a.attachment_bytes for a in arts),
        "apps_present": sorted(by_app.keys()),
        "by_app": by_app,
        "not_present_on_device": not_present,
        "not_acquired": not_acquired,
        "manifest_supplied": attempted is not None,
        "caveats": caveats,
    }


# --- Signal-specific -------------------------------------------------------
_SIGNAL_PREF_KEYS = (
    "pref_database_encrypted_secret",
    "pref_database_unencrypted_secret",
    "pref_attachment_encrypted_secret",
    "pref_encrypted_backup_passphrase",
    "pref_backup_passphrase",
    "pref_backup_enabled",
    "pref_local_number",
    "pref_profile_name",
)


def _read_prefs(path: Path) -> dict[str, Any]:
    """Signal/Session shared_prefs are plaintext XML. We report which security-
    relevant keys are PRESENT, never the secret values themselves."""
    out: dict[str, Any] = {"path": str(path), "keys_present": [], "values": {}, "error": ""}
    try:
        tree = ET.parse(str(path))
    except (ET.ParseError, OSError) as exc:
        out["error"] = f"{exc.__class__.__name__}: {exc}"
        return out
    for node in tree.getroot():
        name = node.attrib.get("name", "")
        if not name:
            continue
        if name in _SIGNAL_PREF_KEYS:
            out["keys_present"].append(name)
            # Only non-secret, non-PII-bearing markers are echoed as values.
            if name in ("pref_backup_enabled",):
                out["values"][name] = node.attrib.get("value", node.text or "")
    return out


def signal_metadata(root: PathLike) -> dict[str, Any]:
    """Everything about Signal that is legitimately readable without any key.

    Databases (existence + size + mtime + classification), attachment directory
    statistics, plaintext avatar caches, on-device ``.backup`` files, and which
    security-relevant preference keys exist. None of this is message content and
    none of it is presented as such.
    """
    base = Path(root)
    package = "org.thoughtcrime.securesms"
    spec = ENCRYPTED_APP_TARGETS[package]
    out: dict[str, Any] = {
        "package": package,
        "app": "Signal",
        "databases": [],
        "attachments": None,
        "avatars": [],
        "backups": [],
        "prefs": None,
        "key_storage": spec["key_storage"],
        "recoverable": False,
        "caveats": [
            _NO_DECRYPT_CAVEAT,
            _KEYSTORE_REASON,
            "signal.db mtime is the last message-database write, i.e. the last "
            "messaging activity to the second — that is timeline evidence even "
            "though the rows are unreadable.",
        ],
    }

    for device_path in spec["db_paths"]:
        local = _resolve(base, device_path)
        if local is None:
            continue
        det = detect_encryption(local)
        if det["format"] == "missing":
            continue
        try:
            mtime: Optional[float] = local.stat().st_mtime
        except OSError:
            mtime = None
        out["databases"].append(
            {
                "path": device_path,
                "name": Path(device_path).name,
                "size_bytes": det["size_bytes"],
                "modified": _iso(mtime),
                "format": det["format"],
                "confidence": det["confidence"],
                "evidence": det["evidence"],
                "recoverable": False,
            }
        )
        # -wal / -shm siblings quantify unflushed recent writes.
        for suffix in ("-wal", "-shm", "-journal"):
            sib = _resolve(base, device_path + suffix)
            if sib is None:
                continue
            try:
                sst = sib.stat()
            except OSError:
                continue
            out["databases"].append(
                {
                    "path": device_path + suffix,
                    "name": Path(device_path).name + suffix,
                    "size_bytes": sst.st_size,
                    "modified": _iso(sst.st_mtime),
                    "format": "sqlite-sidecar",
                    "confidence": Confidence.LIVE.value,
                    "evidence": (
                        "write-ahead-log / shared-memory sidecar; its presence and size "
                        "indicate recent write activity and how much data was unflushed "
                        "at acquisition time"
                    ),
                    "recoverable": False,
                }
            )

    attach_dir = _resolve(base, "/data/data/%s/app_parts" % package)
    if attach_dir is not None and attach_dir.is_dir():
        stats = _dir_stats(attach_dir)
        stats["note"] = (
            "part<19-digit>.mms files map 1:1 onto rows of the encrypted attachment "
            "table, so the count is exact; each file is separately AES-encrypted under "
            "the AttachmentSecret and was not opened."
        )
        out["attachments"] = stats

    for avatar_rel in ("/data/data/%s/files/avatars" % package, "/data/data/%s/cache" % package):
        adir = _resolve(base, avatar_rel)
        if adir is None or not adir.is_dir():
            continue
        for child in sorted(adir.rglob("*")):
            try:
                if not child.is_file():
                    continue
                with child.open("rb") as fh:
                    magic = fh.read(8)
                st = child.stat()
            except OSError:
                continue
            if magic.startswith(b"\xff\xd8\xff") or magic.startswith(b"\x89PNG"):
                out["avatars"].append(
                    {
                        "path": str(child),
                        "size_bytes": st.st_size,
                        "modified": _iso(st.st_mtime),
                        "kind": "jpeg" if magic.startswith(b"\xff\xd8\xff") else "png",
                        "readable": True,
                    }
                )
            if len(out["avatars"]) >= 500:
                break

    for backup_root in ("/sdcard/Signal/Backups", "/data/media/0/Signal/Backups"):
        bdir = _resolve(base, backup_root)
        if bdir is None or not bdir.is_dir():
            continue
        for child in sorted(bdir.rglob("*.backup")):
            try:
                st = child.stat()
            except OSError:
                continue
            out["backups"].append(
                {
                    "path": str(child),
                    "size_bytes": st.st_size,
                    "modified": _iso(st.st_mtime),
                    "recoverable": False,
                    "note": (
                        "Signal local backup: AES-CTR + HMAC-SHA256 framed protobuf, key "
                        "derived from a 30-digit passphrase (~10^30 keyspace, not "
                        "brute-forceable). Existence, size and mtime prove an exportable "
                        "copy of the message history was made and when."
                    ),
                }
            )

    prefs = _resolve(base, spec["pref_paths"][0])
    if prefs is not None and prefs.is_file():
        out["prefs"] = _read_prefs(prefs)
        keys = out["prefs"]["keys_present"]
        if "pref_database_unencrypted_secret" in keys:
            out["caveats"].append(
                "pref_database_unencrypted_secret is present: this legacy pre-Keystore "
                "path stores the database secret WITHOUT Keystore sealing. The secret "
                "value is deliberately not echoed here, but a lab examination should "
                "pursue it."
            )
        if "pref_encrypted_backup_passphrase" in keys:
            out["caveats"].append(
                "pref_encrypted_backup_passphrase is present: the user let the app "
                "remember the 30-digit backup passphrase, stored encrypted."
            )

    if not out["databases"]:
        out["caveats"].append(
            "No Signal database file was found in the staged tree. That is not proof "
            "Signal is absent from the device — app-private storage requires root and "
            "may simply not have been acquired."
        )

    return out
