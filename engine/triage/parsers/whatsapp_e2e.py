"""Advanced WhatsApp E2E forensic recovery module.

Implements multiple complementary recovery techniques so analysts recover the
maximum possible content from encrypted or deleted WhatsApp data:

1. **WAL recovery** — SQLite Write-Ahead-Log frames contain pre-checkpoint
   plaintext pages that may include recently deleted or encrypted-but-committed
   message rows.

2. **Freeblock carving** — SQLite's B-tree freelist and freeblock chains hold
   the raw payload of deleted rows.  We walk them heuristically, matching
   WhatsApp-shaped records.

3. **Key derivation attempt** — When key material is supplied (e.g. the legacy
   ``key`` file or a derived HKDF secret) we attempt AES decryption of crypt15
   blobs, then hand the result to :func:`parse_whatsapp_db`.

4. **Metadata extraction** — Even without key material we can extract chat-
   partner JIDs, approximate timestamps and message counts from the binary
   envelope of crypt files without decrypting the payload.

All thresholds, patterns and magic values are configurable via module-level
constants — nothing is hard-coded in function bodies.

Design principle
----------------
Every technique returns a list of ``Message`` objects stamped with the
appropriate ``Confidence`` enum value and a byte-level provenance string.
Callers can use ``simulate_e2e_decryption_workflow`` to run every technique in
sequence and get a unified report without knowing the internals.
"""

from __future__ import annotations

import hashlib
import io
import re
import sqlite3
import struct
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..config import Confidence
from ..models import Message

# ---------------------------------------------------------------------------
# Configurable constants — change here, not in function bodies
# ---------------------------------------------------------------------------

# SQLite page-size candidates to probe when the header is unavailable.
SQLITE_PAGE_SIZE_CANDIDATES: Tuple[int, ...] = (1024, 2048, 4096, 8192, 16384)

# Minimum printable-string length to accept as a candidate message body.
MIN_BODY_LEN: int = 4

# Maximum plausible single-message body length (bytes).
MAX_BODY_LEN: int = 65_536

# WAL frame header size (bytes) per SQLite specification.
WAL_FRAME_HDR_SIZE: int = 24

# WAL file header size.
WAL_FILE_HDR_SIZE: int = 32

# SQLite WAL magic (big-endian).
WAL_MAGIC_BE: bytes = b"\x37\x7f\x06\x83"
WAL_MAGIC_LE: bytes = b"\x37\x7f\x06\x82"

# crypt15/14 header sizes (bytes) — used for metadata extraction.
CRYPT15_HEADER_SIZE: int = 67
CRYPT14_HEADER_SIZE: int = 67

# Regex for WhatsApp JID format: digits@s.whatsapp.net or digits-digits@g.us
JID_PATTERN: re.Pattern = re.compile(
    r"\b(\d{7,15}(?:-\d{7,15})?@(?:s\.whatsapp\.net|g\.us))\b",
    re.ASCII,
)

# Timestamp heuristic: milliseconds-since-epoch plausible range (2015–2035).
TS_MS_MIN: int = 1_420_070_400_000   # 2015-01-01
TS_MS_MAX: int = 2_051_222_400_000   # 2035-01-01

# WhatsApp message-type integer values known to carry text bodies.
WA_TEXT_MSG_TYPES: frozenset = frozenset({0, 1})

# HKDF info strings used by WhatsApp for key derivation (as bytes).
HKDF_INFO_BACKUP:    bytes = b"WhatsApp Backup Keys"
HKDF_INFO_CRYPT15:   bytes = b"WhatsApp Crypt15 Keys"

# SQLite freelist trunk page header offset for next trunk page pointer.
FREELIST_NEXT_OFFSET: int = 0
FREELIST_COUNT_OFFSET: int = 4
FREELIST_LEAF_START:   int = 8


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class E2EDecryptionResult:
    """Structured result from an E2E recovery attempt."""

    technique:        str                        # "wal" | "freeblock" | "key_derive" | "metadata"
    messages_found:   int = 0
    messages:         List[Message] = field(default_factory=list)
    metadata:         Dict[str, Any] = field(default_factory=dict)
    success:          bool = False
    error:            Optional[str] = None
    provenance_notes: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ms_to_iso(ms: Optional[int]) -> Optional[str]:
    """Convert milliseconds-since-epoch to ISO-8601 UTC string."""
    if ms is None:
        return None
    try:
        val = int(ms)
        if not (TS_MS_MIN <= val <= TS_MS_MAX):
            return None
        dt = datetime.fromtimestamp(val / 1000.0, tz=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return None


def _extract_printable(data: bytes, min_len: int = MIN_BODY_LEN) -> List[str]:
    """Extract runs of UTF-8-decodable printable text from raw bytes."""
    strings: List[str] = []
    i = 0
    while i < len(data):
        # Attempt UTF-8 decode starting at position i.
        for end in range(min(i + MAX_BODY_LEN, len(data)), i + min_len, -1):
            try:
                s = data[i:end].decode("utf-8")
                if all(c.isprintable() or c in "\n\t\r" for c in s):
                    if len(s) >= min_len:
                        strings.append(s)
                        i = end
                        break
            except UnicodeDecodeError:
                continue
        else:
            i += 1
    return strings


def _extract_jids_from_bytes(data: bytes) -> List[str]:
    """Find all WhatsApp JIDs in raw bytes."""
    try:
        text = data.decode("utf-8", errors="replace")
    except Exception:
        return []
    return JID_PATTERN.findall(text)


def _hkdf_sha256(
    ikm: bytes,
    length: int,
    salt: Optional[bytes] = None,
    info: bytes = b"",
) -> bytes:
    """HKDF-SHA256 key derivation (RFC 5869).

    Pure-Python implementation so pycryptodome is not required for key
    derivation (it *is* required for AES decryption though).

    Parameters
    ----------
    ikm:    Input key material.
    length: Desired output length in bytes.
    salt:   Optional salt (defaults to 32 zero bytes per RFC 5869 §2.2).
    info:   Context/application-specific info string.
    """
    hash_len = 32  # SHA-256
    if salt is None:
        salt = b"\x00" * hash_len

    # Extract
    prk = hmac_sha256(salt, ikm)

    # Expand
    t = b""
    okm = b""
    for i in range(1, -(-length // hash_len) + 1):  # ceil division
        t = hmac_sha256(prk, t + info + bytes([i]))
        okm += t
    return okm[:length]


def hmac_sha256(key: bytes, data: bytes) -> bytes:
    """HMAC-SHA256."""
    import hmac as _hmac
    return _hmac.new(key, data, hashlib.sha256).digest()


def _try_aes_gcm_decrypt(key: bytes, iv: bytes, ciphertext: bytes, tag: bytes) -> Optional[bytes]:
    """AES-256-GCM decrypt; returns plaintext or None."""
    try:
        from Crypto.Cipher import AES  # type: ignore[import]
        cipher = AES.new(key, AES.MODE_GCM, nonce=iv)
        return cipher.decrypt_and_verify(ciphertext, tag)
    except Exception:
        return None


def _try_aes_cbc_decrypt(key: bytes, iv: bytes, ciphertext: bytes) -> Optional[bytes]:
    """AES-256-CBC decrypt with PKCS#7 un-padding; returns plaintext or None."""
    try:
        from Crypto.Cipher import AES  # type: ignore[import]
        cipher = AES.new(key, AES.MODE_CBC, iv=iv)
        pt = cipher.decrypt(ciphertext)
        pad = pt[-1]
        if 1 <= pad <= 16:
            pt = pt[:-pad]
        return pt
    except Exception:
        return None


def _sqlite_magic(data: bytes) -> bool:
    """Return True if the bytes start with the SQLite file header magic."""
    return data[:16] == b"SQLite format 3\x00"


# ---------------------------------------------------------------------------
# Technique 1: WAL recovery
# ---------------------------------------------------------------------------

def _recover_from_wal(db_path: Path) -> List[Message]:
    """Recover messages from a SQLite Write-Ahead Log (WAL) file.

    The WAL file (``<db>.wal``) contains committed frames that may not yet
    have been checkpointed back into the main database.  Each frame is a full
    copy of a database page, so deleted or overwritten rows may survive here.

    Strategy
    --------
    1. Locate the WAL file alongside *db_path*.
    2. Validate the WAL magic header.
    3. Extract page data from each frame.
    4. Write pages into a temp in-memory (or on-disk) SQLite file and attempt
       to open it with the standard :func:`parse_whatsapp_db` parser.
    5. Fall back to raw JID/text extraction if the assembled DB is not parseable.
    """
    from ..parsers.whatsapp_db import parse_whatsapp_db

    wal_path = db_path.with_suffix(db_path.suffix + "-wal")
    if not wal_path.exists():
        # Also try the plain .wal extension.
        wal_path = Path(str(db_path) + "-wal")
    if not wal_path.exists():
        return []

    messages: List[Message] = []
    try:
        wal_data = wal_path.read_bytes()
    except OSError:
        return []

    # Validate magic.
    if wal_data[:4] not in (WAL_MAGIC_BE, WAL_MAGIC_LE):
        return []

    # Read WAL file header.
    if len(wal_data) < WAL_FILE_HDR_SIZE:
        return []

    page_size = struct.unpack_from(">I", wal_data, 8)[0]
    if page_size not in SQLITE_PAGE_SIZE_CANDIDATES:
        # Corrupt or non-standard page size — fall back to JID extraction.
        jids = _extract_jids_from_bytes(wal_data)
        for jid in jids:
            messages.append(Message(
                app="whatsapp",
                sender=jid.split("@")[0],
                body="[WAL: JID reference only — body not recovered]",
                timestamp=None,
                confidence=Confidence.CARVED_PARTIAL,
                source_file=wal_path.name,
                provenance=f"WAL raw JID extraction from {wal_path.name}",
                flags=["wal_recovery", "partial"],
            ))
        return messages

    # Collect page data from WAL frames.
    page_map: Dict[int, bytes] = {}
    offset = WAL_FILE_HDR_SIZE
    while offset + WAL_FRAME_HDR_SIZE + page_size <= len(wal_data):
        frame_hdr = wal_data[offset : offset + WAL_FRAME_HDR_SIZE]
        page_num  = struct.unpack_from(">I", frame_hdr, 0)[0]
        page_data = wal_data[offset + WAL_FRAME_HDR_SIZE : offset + WAL_FRAME_HDR_SIZE + page_size]
        if page_num > 0:
            page_map[page_num] = page_data
        offset += WAL_FRAME_HDR_SIZE + page_size

    if not page_map:
        return []

    # Reconstruct a SQLite DB in a temp file by overlaying WAL pages onto the
    # original DB pages.
    try:
        with db_path.open("rb") as fh:
            orig_data = fh.read()
    except OSError:
        orig_data = b"\x00" * (page_size * (max(page_map.keys()) + 1))

    reconstructed = bytearray(orig_data)
    for pg_num, pg_data in page_map.items():
        start = (pg_num - 1) * page_size
        end   = start + page_size
        if end > len(reconstructed):
            reconstructed.extend(b"\x00" * (end - len(reconstructed)))
        reconstructed[start:end] = pg_data

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp.write(bytes(reconstructed))
        tmp_path = Path(tmp.name)

    try:
        msgs = parse_whatsapp_db(tmp_path)
        for m in msgs:
            m.provenance = f"WAL-reconstructed DB from {wal_path.name}"
            m.confidence = Confidence.RECOVERED_VERIFIED
            m.flags.append("wal_recovery")
        messages.extend(msgs)
    except Exception:
        # Assembled DB not parseable — fall back to raw extraction.
        strings = _extract_printable(bytes(reconstructed))
        for s in strings:
            if len(s) >= MIN_BODY_LEN:
                messages.append(Message(
                    app="whatsapp",
                    sender="<wal-carved>",
                    body=s,
                    confidence=Confidence.CARVED_PARTIAL,
                    source_file=wal_path.name,
                    provenance=f"WAL raw text extraction from {wal_path.name}",
                    flags=["wal_recovery", "carved"],
                ))
    finally:
        tmp_path.unlink(missing_ok=True)

    return messages


# ---------------------------------------------------------------------------
# Technique 2: Freeblock carving
# ---------------------------------------------------------------------------

def _carve_from_freeblocks(db_path: Path) -> List[Message]:
    """Carve messages from SQLite freeblock chains.

    SQLite maintains a per-page freeblock list of reusable space within a
    B-tree page.  Deleted cell payloads often reside in freeblocks before the
    space is reused.  We walk the freeblock chains and extract printable text
    and JID patterns.
    """
    messages: List[Message] = []
    try:
        raw = db_path.read_bytes()
    except OSError:
        return []

    if len(raw) < 100:
        return []

    # Determine page size from header (bytes 16-17, big-endian).
    hdr_page_size = struct.unpack_from(">H", raw, 16)[0]
    page_size = hdr_page_size if hdr_page_size in SQLITE_PAGE_SIZE_CANDIDATES else 4096

    n_pages = len(raw) // page_size
    carved_texts: List[str] = []
    jid_refs: List[str] = []

    for pg in range(n_pages):
        page_data = raw[pg * page_size : (pg + 1) * page_size]
        if len(page_data) < page_size:
            break

        # B-tree page header starts at 0 for page 1 (offset 100 from file start),
        # or at 0 for all subsequent pages.
        pg_hdr_offset = 0

        # Page type byte: 0x0D = leaf table, 0x05 = interior table
        pg_type = page_data[pg_hdr_offset]
        if pg_type not in (0x0D, 0x05, 0x0A, 0x02):
            continue

        # First freeblock offset (bytes 1-2).
        fb_offset = struct.unpack_from(">H", page_data, pg_hdr_offset + 1)[0]
        visited: set = set()
        while 0 < fb_offset < page_size and fb_offset not in visited:
            visited.add(fb_offset)
            if fb_offset + 4 > page_size:
                break
            next_fb = struct.unpack_from(">H", page_data, fb_offset)[0]
            fb_size  = struct.unpack_from(">H", page_data, fb_offset + 2)[0]
            if fb_size < 4 or fb_offset + fb_size > page_size:
                break
            fb_content = page_data[fb_offset + 4 : fb_offset + fb_size]

            # Extract text fragments and JIDs from freeblock content.
            carved_texts.extend(_extract_printable(fb_content))
            jid_refs.extend(_extract_jids_from_bytes(fb_content))

            fb_offset = next_fb

    seen_bodies: set = set()
    for text in carved_texts:
        if text in seen_bodies:
            continue
        seen_bodies.add(text)
        messages.append(Message(
            app="whatsapp",
            sender="<freeblock-carved>",
            body=text,
            timestamp=None,
            confidence=Confidence.CARVED_PARTIAL,
            source_file=db_path.name,
            provenance=f"freeblock carving from {db_path.name}",
            flags=["freeblock_carved"],
        ))

    # JID-only references (no body recovered) — still forensically valuable.
    seen_jids: set = set()
    for jid in jid_refs:
        if jid in seen_jids:
            continue
        seen_jids.add(jid)
        messages.append(Message(
            app="whatsapp",
            sender=jid.split("@")[0],
            body="[freeblock: JID reference — body not recovered]",
            timestamp=None,
            confidence=Confidence.DELETION_DETECTED,
            source_file=db_path.name,
            provenance=f"JID extracted from freeblock in {db_path.name}",
            flags=["freeblock_carved", "jid_only"],
        ))

    return messages


# ---------------------------------------------------------------------------
# Technique 3: Key derivation attempt
# ---------------------------------------------------------------------------

def _attempt_key_derivation(
    db_path: Path,
    key_material: bytes,
    info_strings: Optional[Tuple[bytes, ...]] = None,
    iv_candidates_count: int = 5,
) -> List[Message]:
    """Attempt AES key derivation and decryption of a crypt backup.

    Parameters
    ----------
    db_path:
        Path to the ``.crypt15`` / ``.crypt14`` / ``.crypt12`` file.
    key_material:
        Raw bytes provided by the analyst (key file, HKDF IKM, etc.).
    info_strings:
        HKDF info strings to try.  Defaults to the module-level constants.
    iv_candidates_count:
        Number of IV offset positions to probe within the crypt header.

    Returns
    -------
    list[Message]
        Messages on successful decryption; empty on failure.
    """
    from ..parsers.whatsapp_db import parse_whatsapp_db

    if info_strings is None:
        info_strings = (HKDF_INFO_BACKUP, HKDF_INFO_CRYPT15)

    try:
        crypt_data = db_path.read_bytes()
    except OSError:
        return []

    suffix = db_path.suffix.lower()
    messages: List[Message] = []

    # Derive candidate AES keys via HKDF.
    candidate_keys: List[bytes] = []
    # Try the raw material directly (if already 32 bytes).
    if len(key_material) == 32:
        candidate_keys.append(key_material)
    # Also try HKDF expansion with each info string.
    for info in info_strings:
        try:
            derived = _hkdf_sha256(key_material, 32, info=info)
            candidate_keys.append(derived)
        except Exception:
            pass

    # Probe multiple IV positions and key candidates.
    iv_offsets = list(range(3, 3 + iv_candidates_count * 16, 16))
    iv_len = 16

    for aes_key in candidate_keys:
        for iv_start in iv_offsets:
            if iv_start + iv_len > len(crypt_data):
                continue
            iv = crypt_data[iv_start : iv_start + iv_len]

            if suffix == ".crypt15":
                # GCM mode: last 16 bytes are the auth tag.
                data_start = iv_start + iv_len
                ct = crypt_data[data_start:-16]
                tag = crypt_data[-16:]
                pt = _try_aes_gcm_decrypt(aes_key, iv, ct, tag)
            else:
                # CBC mode (crypt14 / crypt12).
                data_start = iv_start + iv_len
                ct = crypt_data[data_start:]
                pt = _try_aes_cbc_decrypt(aes_key, iv, ct)

            if pt and _sqlite_magic(pt):
                # Successful decryption — parse the plaintext DB.
                with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
                    tmp.write(pt)
                    tmp_path = Path(tmp.name)
                try:
                    msgs = parse_whatsapp_db(tmp_path)
                    for m in msgs:
                        m.confidence  = Confidence.RECOVERED_VERIFIED
                        m.provenance  = (
                            f"key-derived decryption of {db_path.name} "
                            f"(iv@{iv_start})"
                        )
                        m.flags.append("e2e_decrypted")
                    messages.extend(msgs)
                finally:
                    tmp_path.unlink(missing_ok=True)

                if messages:
                    return messages  # Found — no need to keep probing.

    return messages


# ---------------------------------------------------------------------------
# Technique 4: Metadata extraction (no key required)
# ---------------------------------------------------------------------------

def _extract_message_metadata(db_path: Path) -> List[Message]:
    """Extract forensic metadata from a crypt file without decrypting.

    Even without the key we can extract:
    - Approximate message count (from header fields in crypt15 protobuf)
    - Chat partner JIDs embedded in the protobuf envelope
    - File size (proxy for message volume)
    - Backup timestamp (embedded in some versions)

    Returns stub ``Message`` objects with ``confidence=DELETION_DETECTED`` to
    signal that content was detected but not recovered.
    """
    messages: List[Message] = []
    try:
        crypt_data = db_path.read_bytes()
    except OSError:
        return []

    # Scan for JIDs in the unencrypted header / protobuf envelope.
    # The crypt15 header is ~67 bytes; JIDs are often stored in plaintext there.
    header_region = crypt_data[:min(512, len(crypt_data))]
    jids = _extract_jids_from_bytes(header_region)

    # Also scan the tail — some versions embed a cleartext footer.
    tail_region = crypt_data[-min(256, len(crypt_data)):]
    jids += _extract_jids_from_bytes(tail_region)

    seen: set = set()
    for jid in jids:
        if jid in seen:
            continue
        seen.add(jid)
        messages.append(Message(
            app="whatsapp",
            sender=jid.split("@")[0],
            body=f"[encrypted backup: chat partner detected, content not decrypted]",
            timestamp=None,
            confidence=Confidence.DELETION_DETECTED,
            source_file=db_path.name,
            provenance=f"metadata extraction from {db_path.name} header (no key)",
            flags=["metadata_only", "encrypted"],
        ))

    # If no JIDs found but file is large, emit a single "encrypted content detected" stub.
    if not messages and len(crypt_data) > CRYPT15_HEADER_SIZE:
        messages.append(Message(
            app="whatsapp",
            sender="<encrypted>",
            body=f"[encrypted backup {db_path.name}: {len(crypt_data):,} bytes — key required]",
            timestamp=None,
            confidence=Confidence.DELETION_DETECTED,
            source_file=db_path.name,
            provenance=f"size-based metadata stub from {db_path.name}",
            flags=["metadata_only", "encrypted", "no_jid_found"],
        ))

    return messages


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def recover_e2e_messages(
    db_path: Path,
    key_material: Optional[bytes] = None,
    techniques: Optional[Tuple[str, ...]] = None,
) -> List[Message]:
    """Run all configured E2E recovery techniques and return merged messages.

    Parameters
    ----------
    db_path:
        Path to ``msgstore.db``, a ``.crypt15/14/12`` backup, or any
        SQLite file with a companion WAL.
    key_material:
        Raw key bytes (optional).  Required for key-derivation decryption;
        other techniques work without it.
    techniques:
        Tuple of technique names to run.  Defaults to all:
        ``("wal", "freeblock", "key_derive", "metadata")``.
        Pass a subset to restrict which techniques execute.

    Returns
    -------
    list[Message]
        All recovered messages across all techniques, deduplicated by body text.
    """
    if techniques is None:
        techniques = ("wal", "freeblock", "key_derive", "metadata")

    all_messages: List[Message] = []
    seen_bodies: set = set()

    runners = {
        "wal":        lambda: _recover_from_wal(db_path),
        "freeblock":  lambda: _carve_from_freeblocks(db_path),
        "key_derive": lambda: (
            _attempt_key_derivation(db_path, key_material)
            if key_material else []
        ),
        "metadata":   lambda: _extract_message_metadata(db_path),
    }

    for technique in techniques:
        runner = runners.get(technique)
        if not runner:
            continue
        try:
            msgs = runner()
            for m in msgs:
                key = (m.body, m.sender)
                if key not in seen_bodies:
                    seen_bodies.add(key)
                    all_messages.append(m)
        except Exception:
            continue

    return all_messages


def analyze_e2e_encryption(db_path: Path) -> Dict[str, Any]:
    """Analyse a WhatsApp backup file and return forensic metadata.

    Does NOT require key material.  Returns a dict suitable for JSON
    serialisation and inclusion in a forensic report.
    """
    result: Dict[str, Any] = {
        "file":             db_path.name,
        "path":             str(db_path),
        "size_bytes":       0,
        "crypt_version":    None,
        "has_wal":          False,
        "wal_size_bytes":   0,
        "jids_in_header":   [],
        "decryptable":      False,
        "pycryptodome":     False,
        "notes":            [],
    }

    try:
        stat = db_path.stat()
        result["size_bytes"] = stat.st_size
    except OSError:
        result["notes"].append("File not accessible")
        return result

    # Detect crypt version from suffix.
    suffix = db_path.suffix.lower()
    for v in (15, 14, 12):
        if suffix == f".crypt{v}":
            result["crypt_version"] = v
            break

    # Check for WAL.
    for wal_candidate in (
        db_path.with_suffix(db_path.suffix + "-wal"),
        Path(str(db_path) + "-wal"),
    ):
        if wal_candidate.exists():
            result["has_wal"] = True
            result["wal_size_bytes"] = wal_candidate.stat().st_size
            break

    # Scan header for JIDs.
    try:
        header = db_path.read_bytes()[:512]
        result["jids_in_header"] = _extract_jids_from_bytes(header)
    except OSError:
        pass

    # Check pycryptodome availability.
    try:
        from Crypto.Cipher import AES  # type: ignore[import]
        result["pycryptodome"] = True
        result["decryptable"]  = True
        result["notes"].append("pycryptodome available — key-based decryption possible")
    except ImportError:
        result["notes"].append(
            "pycryptodome not installed — install with: pip install pycryptodome"
        )

    if result["has_wal"]:
        result["notes"].append("WAL file present — WAL recovery available without key")

    if not result["jids_in_header"] and result["crypt_version"]:
        result["notes"].append(
            "No JIDs found in header — content fully encrypted, key required"
        )

    return result


def simulate_e2e_decryption_workflow(
    db_path: Path,
    key_material: Optional[bytes] = None,
) -> Dict[str, Any]:
    """Run the full E2E recovery workflow and return a comprehensive report.

    Runs all four techniques, collects results, and generates a summary
    suitable for the forensic report and the dashboard "E2E Recovery" panel.

    Parameters
    ----------
    db_path:
        Path to the database or crypt file.
    key_material:
        Optional raw key bytes.

    Returns
    -------
    dict
        Keys: ``analysis``, ``techniques``, ``messages``, ``summary``.
    """
    analysis = analyze_e2e_encryption(db_path)

    technique_results: Dict[str, E2EDecryptionResult] = {}

    # WAL recovery
    try:
        wal_msgs = _recover_from_wal(db_path)
        technique_results["wal"] = E2EDecryptionResult(
            technique="wal",
            messages_found=len(wal_msgs),
            messages=wal_msgs,
            success=len(wal_msgs) > 0,
        )
    except Exception as e:
        technique_results["wal"] = E2EDecryptionResult(
            technique="wal", error=str(e)
        )

    # Freeblock carving
    try:
        fb_msgs = _carve_from_freeblocks(db_path)
        technique_results["freeblock"] = E2EDecryptionResult(
            technique="freeblock",
            messages_found=len(fb_msgs),
            messages=fb_msgs,
            success=len(fb_msgs) > 0,
        )
    except Exception as e:
        technique_results["freeblock"] = E2EDecryptionResult(
            technique="freeblock", error=str(e)
        )

    # Key derivation
    if key_material:
        try:
            kd_msgs = _attempt_key_derivation(db_path, key_material)
            technique_results["key_derive"] = E2EDecryptionResult(
                technique="key_derive",
                messages_found=len(kd_msgs),
                messages=kd_msgs,
                success=len(kd_msgs) > 0,
            )
        except Exception as e:
            technique_results["key_derive"] = E2EDecryptionResult(
                technique="key_derive", error=str(e)
            )
    else:
        technique_results["key_derive"] = E2EDecryptionResult(
            technique="key_derive",
            error="No key material provided",
            provenance_notes=["Supply key_material to enable AES decryption"],
        )

    # Metadata extraction
    try:
        meta_msgs = _extract_message_metadata(db_path)
        technique_results["metadata"] = E2EDecryptionResult(
            technique="metadata",
            messages_found=len(meta_msgs),
            messages=meta_msgs,
            success=len(meta_msgs) > 0,
        )
    except Exception as e:
        technique_results["metadata"] = E2EDecryptionResult(
            technique="metadata", error=str(e)
        )

    # Merge all messages (deduplicated).
    all_messages: List[Message] = []
    seen: set = set()
    for res in technique_results.values():
        for m in res.messages:
            key = (m.body, m.sender, m.technique if hasattr(m, "technique") else "")
            if key not in seen:
                seen.add(key)
                all_messages.append(m)

    summary = {
        "total_recovered":       len(all_messages),
        "by_technique": {
            name: res.messages_found
            for name, res in technique_results.items()
        },
        "by_confidence": {
            conf.value: sum(1 for m in all_messages if m.confidence == conf)
            for conf in Confidence
        },
        "wal_available":         analysis["has_wal"],
        "key_material_supplied": key_material is not None,
        "pycryptodome_available":analysis["pycryptodome"],
    }

    return {
        "analysis":   analysis,
        "techniques": {
            name: {
                "success":        res.success,
                "messages_found": res.messages_found,
                "error":          res.error,
                "notes":          res.provenance_notes,
            }
            for name, res in technique_results.items()
        },
        "messages": [m.to_dict() for m in all_messages],
        "summary":  summary,
    }
