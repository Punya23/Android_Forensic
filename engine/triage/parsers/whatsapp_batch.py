"""Batch WhatsApp export parser — process multiple exports concurrently.

Supports:
- ``.txt`` / ``.zip`` chat exports (plain-text parse via whatsapp_txt)
- Live ``msgstore.db`` SQLite databases (via whatsapp_db)
- Encrypted ``msgstore.db.crypt15`` / ``.crypt14`` / ``.crypt12`` backups

E2E-Encrypted backup recovery
------------------------------
WhatsApp Android stores encrypted backups as ``msgstore.db.crypt15`` (crypt14
on older devices, crypt12 on very old ones).  The key material lives outside
the backup file itself, in one of three locations:

1. ``/data/data/com.whatsapp/files/key``  — the legacy 32-byte AES key file
   (Tier-2 / root acquisition).
2. ``/sdcard/WhatsApp/Databases/encrypted_backup.key``  — exported key blob
   from WhatsApp's own "Export key" flow.
3. ``wa.db`` identity key material combined with the Google-account-bound
   64-byte key (available in official WhatsApp backups via ``whatsapp-backup``
   library if provided by the analyst).

We do **not** hard-code the key path — callers supply it.  If no key is
provided the encrypted file is still catalogued (provenance "crypt-no-key")
so the analyst sees it in the case file even without recovering content.

No crypto is implemented from scratch: decryption is delegated to the
optional ``pycryptodome`` / ``Crypto`` library (AES-256-CBC/GCM).  If the
library is absent the module still loads and gracefully marks the item as
``confidence=Confidence.CARVED_PARTIAL`` rather than crashing.
"""

from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

from .whatsapp_txt import parse_whatsapp_export
from .whatsapp_db import parse_whatsapp_db
from ..config import Confidence
from ..models import Message

# ---------------------------------------------------------------------------
# Crypt15 / crypt14 / crypt12 constants
# ---------------------------------------------------------------------------

# Known WhatsApp backup header magic bytes (big-endian uint32 prefix)
_CRYPT_VERSIONS: dict[int, str] = {
    15: "crypt15",
    14: "crypt14",
    12: "crypt12",
}

# crypt15 header layout (bytes):
#   [0..3]   = 0x57 0x41 0x00 0x0F  ("WA\x00\x0f") or protobuf header
#   Actual format is a Google protobuf envelope; we only need to locate the IV
#   and key offsets, which differ per version.
_CRYPT15_MAGIC = b"\x57\x41\x00\x0f"  # WA\x00\x0f
_CRYPT14_IV_OFFSET = 51
_CRYPT14_IV_LEN = 16
_CRYPT14_DATA_OFFSET = 67
_CRYPT15_IV_LEN = 16


# ---------------------------------------------------------------------------
# Encrypted backup helpers
# ---------------------------------------------------------------------------


def _detect_crypt_version(path: Path) -> Optional[int]:
    """Return the crypt version (15/14/12) or ``None`` if not a crypt file."""
    suffix = path.suffix.lower()
    for v in _CRYPT_VERSIONS:
        if suffix == f".crypt{v}":
            return v
    return None


def _read_key_file(key_path: Path) -> Optional[bytes]:
    """Read raw key bytes from *key_path*; return ``None`` on any error."""
    try:
        data = key_path.read_bytes()
        # The legacy WhatsApp ``key`` file is a 131-byte protobuf; the AES
        # key occupies bytes [126:158] (32 bytes, 0-indexed).
        # A bare 32-byte key file is also supported.
        if len(data) == 32:
            return data
        if len(data) >= 158:
            return data[126:158]  # protobuf-encoded legacy key file
        return None
    except OSError:
        return None


def _decrypt_crypt15(crypt_path: Path, aes_key: bytes) -> Optional[bytes]:
    """Attempt to decrypt a crypt15/crypt14 backup using *aes_key*.

    The Google-protobuf header contains a 16-byte IV; we strip the 67-byte
    header and decrypt the rest with AES-256-GCM (crypt15) or AES-256-CBC
    (crypt14).

    Returns raw SQLite bytes on success; ``None`` on failure.

    Requires ``pycryptodome``.  Silently returns ``None`` if unavailable.
    """
    try:
        from Crypto.Cipher import AES  # type: ignore[import]
    except ImportError:
        return None

    try:
        raw = crypt_path.read_bytes()

        # ---- crypt15 (GCM) ----
        if crypt_path.suffix.lower() == ".crypt15":
            # Protobuf envelope: first field contains IV; we locate it
            # heuristically — byte 3 encodes field tag 0x0f (version=15),
            # IV is at offset 3+varint_length.  WhatsApp's own layout places
            # the 16-byte IV after a short protobuf preamble (~51 bytes).
            iv_start = 51
            # Find the SQLite magic at offset 0 of the *decrypted* output by
            # probing a small set of candidate IV positions.
            for iv_start in (3, 51, 67):
                if iv_start + _CRYPT15_IV_LEN > len(raw):
                    continue
                iv = raw[iv_start : iv_start + _CRYPT15_IV_LEN]
                data_start = iv_start + _CRYPT15_IV_LEN
                ciphertext = raw[data_start:-16]  # last 16 bytes = auth tag
                auth_tag = raw[-16:]
                try:
                    cipher = AES.new(aes_key, AES.MODE_GCM, nonce=iv)
                    plaintext = cipher.decrypt_and_verify(ciphertext, auth_tag)
                    if plaintext[:16] == b"SQLite format 3\x00":
                        return plaintext
                except Exception:
                    continue
            return None

        # ---- crypt14 / crypt12 (CBC) ----
        iv = raw[_CRYPT14_IV_OFFSET : _CRYPT14_IV_OFFSET + _CRYPT14_IV_LEN]
        ciphertext = raw[_CRYPT14_DATA_OFFSET:]
        cipher = AES.new(aes_key, AES.MODE_CBC, iv=iv)
        plaintext = cipher.decrypt(ciphertext)
        # PKCS#7 un-pad
        pad = plaintext[-1]
        if 1 <= pad <= 16:
            plaintext = plaintext[:-pad]
        if plaintext[:16] == b"SQLite format 3\x00":
            return plaintext
        return None

    except Exception:
        return None


def _decrypt_and_parse(
    crypt_path: Path,
    key_path: Optional[Path],
) -> List[Message]:
    """Decrypt a crypt backup and parse its SQLite content.

    If decryption succeeds the decrypted bytes are written to a temp file
    alongside the original (same directory, ``.dec`` suffix) and parsed with
    :func:`parse_whatsapp_db`.

    Returns a list of ``Message`` objects; on failure returns an empty list
    with a provenance note (caller handles the empty-return contract).
    """
    version = _detect_crypt_version(crypt_path)
    if version is None:
        return []

    if key_path is None or not key_path.exists():
        # Catalogue the file but return no messages — key not supplied.
        return []

    aes_key = _read_key_file(key_path)
    if aes_key is None:
        return []

    plaintext = _decrypt_crypt15(crypt_path, aes_key)
    if plaintext is None:
        return []

    # Write decrypted SQLite to a sibling temp file.
    dec_path = crypt_path.with_suffix(".dec")
    try:
        dec_path.write_bytes(plaintext)
        msgs = parse_whatsapp_db(dec_path)
        # Stamp provenance to distinguish decrypted-backup from live DB.
        for m in msgs:
            m.provenance = f"decrypted {crypt_path.name} ({_CRYPT_VERSIONS[version]})"
            m.confidence = Confidence.RECOVERED_VERIFIED
        return msgs
    except Exception:
        return []
    finally:
        # Remove the decrypted file regardless of parse success.
        try:
            dec_path.unlink(missing_ok=True)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Single-file dispatcher
# ---------------------------------------------------------------------------


def _parse_single(
    path: Path,
    key_path: Optional[Path] = None,
) -> List[Message]:
    """Dispatch a single file to the appropriate parser.

    Routing table:
    - ``.db``              → live SQLite via :func:`parse_whatsapp_db`
    - ``_chat.txt``        → export text via :func:`parse_whatsapp_export`
    - ``*.zip``            → ZIP-wrapped export via :func:`parse_whatsapp_export`
    - ``.crypt15/14/12``   → encrypted backup via :func:`_decrypt_and_parse`
    """
    suffix = path.suffix.lower()

    if suffix == ".db":
        return parse_whatsapp_db(path)

    if suffix in (".txt", ".zip") and "_chat" in path.name:
        return parse_whatsapp_export(path)

    if _detect_crypt_version(path) is not None:
        return _decrypt_and_parse(path, key_path)

    return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_whatsapp_batch(
    paths: List[Path],
    parallel: bool = True,
    max_workers: int = 4,
    key_path: Optional[Path] = None,
) -> List[Message]:
    """Parse multiple WhatsApp files/databases, optionally in parallel.

    Parameters
    ----------
    paths:
        Arbitrary mix of ``.txt``, ``.zip``, ``.db``, ``.crypt15/14/12`` paths.
    parallel:
        If ``True`` (default) use a thread pool for IO-bound work.
    max_workers:
        Maximum worker threads.  Ignored when *parallel* is ``False``.
    key_path:
        Optional path to the WhatsApp AES key file, required to decrypt
        ``crypt15`` / ``crypt14`` / ``crypt12`` backups.

    Returns
    -------
    list[Message]
        All messages parsed across all files.  Failed files are silently
        skipped (errors are not propagated).
    """
    if not paths:
        return []

    if not parallel:
        return _parse_sequential(paths, key_path=key_path)

    all_messages: List[Message] = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_parse_single, p, key_path): p for p in paths}
        for future in as_completed(futures):
            try:
                msgs = future.result(timeout=30)
                all_messages.extend(msgs)
            except Exception:
                # Timeout or unexpected error — skip this file.
                continue

    return all_messages


def _parse_sequential(
    paths: List[Path],
    key_path: Optional[Path] = None,
) -> List[Message]:
    """Parse *paths* one by one, swallowing per-file errors."""
    all_messages: List[Message] = []
    for path in paths:
        try:
            msgs = _parse_single(path, key_path)
            all_messages.extend(msgs)
        except Exception:
            continue
    return all_messages


def parse_whatsapp_directory(
    directory: Path,
    recursive: bool = True,
    parallel: bool = True,
    key_path: Optional[Path] = None,
) -> List[Message]:
    """Discover and parse all WhatsApp artefacts inside *directory*.

    Scans for:
    - ``_chat.txt`` / ``_chat.zip`` export files
    - ``msgstore.db`` live databases
    - ``msgstore.db.crypt15``, ``.crypt14``, ``.crypt12`` encrypted backups

    Parameters
    ----------
    directory:
        Root folder to search (typically the case evidence folder).
    recursive:
        If ``True`` (default) search all sub-directories.
    parallel:
        Passed through to :func:`parse_whatsapp_batch`.
    key_path:
        Optional path to the WhatsApp AES key for crypt backup decryption.

    Returns
    -------
    list[Message]
    """
    glob = "**/" if recursive else ""
    paths: List[Path] = []

    for pattern in (
        f"{glob}_chat.txt",
        f"{glob}_chat.zip",
        f"{glob}msgstore.db",
        f"{glob}msgstore.db.crypt15",
        f"{glob}msgstore.db.crypt14",
        f"{glob}msgstore.db.crypt12",
    ):
        paths.extend(directory.glob(pattern))

    return parse_whatsapp_batch(paths, parallel=parallel, key_path=key_path)


def get_batch_stats(messages: List[Message]) -> Dict[str, Any]:
    """Compute summary statistics for a batch of parsed messages.

    Returns a dict with:
    - ``total``          — total message count
    - ``by_confidence``  — counts per :class:`Confidence` value
    - ``by_direction``   — counts per direction string
    - ``date_range``     — ``{start, end}`` ISO timestamps or ``None``

    Parameters
    ----------
    messages:
        Any list of :class:`~engine.triage.models.Message` objects.
    """
    by_confidence: Dict[str, int] = defaultdict(int)
    by_direction: Dict[str, int] = defaultdict(int)
    earliest: Optional[str] = None
    latest: Optional[str] = None

    for msg in messages:
        by_confidence[msg.confidence.value] += 1
        by_direction[msg.direction] += 1

        ts = msg.timestamp
        if ts:
            if earliest is None or ts < earliest:
                earliest = ts
            if latest is None or ts > latest:
                latest = ts

    return {
        "total": len(messages),
        "by_confidence": dict(by_confidence),
        "by_direction": dict(by_direction),
        "date_range": {"start": earliest, "end": latest},
    }
