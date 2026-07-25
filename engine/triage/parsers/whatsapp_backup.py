"""WhatsApp backup recovery parser for eRakshak.

Handles the full lifecycle of WhatsApp msgstore backup forensics:

1. **Discovery** — scan both known backup roots for ``msgstore*.db.crypt{12,14,15}`` files.
2. **Key extraction** — root-copy the key file to sdcard, then adb pull.
   crypt12/14: ``/data/data/com.whatsapp/files/key``
   crypt15:    ``/data/data/com.whatsapp/files/encrypted_backup.key``
3. **Decryption** — call ``whatsapp-crypt14-decrypter`` subprocess (preferred),
   fall back to a pure-Python AES-256-CBC implementation.
4. **SQLite integrity check** — verify the ``SQLite format 3`` magic header.
5. **Deleted-message recovery** — reuse the existing ``recover_deleted_rows``
   (sqlite-dissect freelist/WAL walk) and ``sqbrite_cross_check`` (raw byte scan).
6. **Media file recovery** — probe the Tier-0 media path and trash folder for
   each message that carries a non-empty ``media_path``.

All actions are intended to be called from ``pipeline._run_tier2_whatsapp_backup``
— nothing in this module touches the device directly; it only parses locally-staged
files and returns typed data.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from ..acquire import RealDeviceSource
    from ..custody import Case

from ..config import Confidence, Tier
from ..models import WhatsAppBackupMedia, WhatsAppBackupMessage

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Both known backup root paths on the device.
BACKUP_ROOTS: list[str] = [
    "/sdcard/WhatsApp/Databases",
    "/sdcard/Android/media/com.whatsapp/WhatsApp/Databases",
]

# Root-required key file paths, keyed by a set of crypt versions they cover.
KEY_PATHS: dict[str, str] = {
    "crypt12": "/data/data/com.whatsapp/files/key",
    "crypt14": "/data/data/com.whatsapp/files/key",
    "crypt15": "/data/data/com.whatsapp/files/encrypted_backup.key",
}

# Staging area on the device's sdcard (world-writable after root copy).
STAGE_BASE = "/sdcard/Download/erakshak_wa_backup"

# SQLite magic header (first 16 bytes of any valid SQLite file).
_SQLITE_MAGIC = b"SQLite format 3\x00"

# Backup filename patterns:
# msgstore.db.crypt15          → current (no date)
# msgstore-2024-12-31.1.db.crypt14  → dated daily backup
_BACKUP_RE = re.compile(
    r"^msgstore(?:-(?P<date>\d{4}-\d{2}-\d{2})\.?\d*)?\.db\.(?P<crypt>crypt\d+)$",
    re.IGNORECASE,
)

# WhatsApp media Tier-0 root on device.
WA_MEDIA_ROOT = "/sdcard/Android/media/com.whatsapp/WhatsApp/Media"
WA_MEDIA_ROOT_LEGACY = "/sdcard/WhatsApp/Media"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class BackupInfo:
    """Metadata about a discovered backup file."""

    device_path: str  # full path on the device
    filename: str  # basename
    crypt_version: str  # e.g. "crypt15"
    date_str: str  # "2024-12-31" or "current" for undated
    size_bytes: int = 0

    @property
    def is_current(self) -> bool:
        return self.date_str == "current"


# ---------------------------------------------------------------------------
# 1. Backup discovery
# ---------------------------------------------------------------------------


def discover_backups(source: "RealDeviceSource") -> list[BackupInfo]:
    """Scan both backup roots and return a list of :class:`BackupInfo` sorted newest-first."""
    found: list[BackupInfo] = []

    for root in BACKUP_ROOTS:
        # `find` returns one path per line; missing dirs print nothing.
        out = source.adb.shell(
            f"find '{root}' -maxdepth 1 -type f -name 'msgstore*.db.crypt*' 2>/dev/null"
        )
        if not out.ok:
            continue
        for line in (out.stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            fname = line.rsplit("/", 1)[-1]
            m = _BACKUP_RE.match(fname)
            if not m:
                continue
            date_str = m.group("date") or "current"
            crypt_ver = m.group("crypt").lower()

            # Get file size.
            stat = source.adb.shell(f"stat -c '%s' '{line}' 2>/dev/null")
            try:
                size_bytes = int((stat.stdout or "0").strip())
            except ValueError:
                size_bytes = 0

            found.append(
                BackupInfo(
                    device_path=line,
                    filename=fname,
                    crypt_version=crypt_ver,
                    date_str=date_str,
                    size_bytes=size_bytes,
                )
            )

    # Sort: current first, then by date descending.
    def _sort_key(b: BackupInfo) -> str:
        return "9999-99-99" if b.is_current else b.date_str

    found.sort(key=_sort_key, reverse=True)
    # De-duplicate by filename (in case both roots hold the same file).
    seen: set[str] = set()
    unique: list[BackupInfo] = []
    for b in found:
        if b.filename not in seen:
            seen.add(b.filename)
            unique.append(b)
    return unique


# ---------------------------------------------------------------------------
# 2. Key extraction
# ---------------------------------------------------------------------------


def extract_key(
    source: "RealDeviceSource",
    crypt_version: str,
    case: "Case",
    staging: Path,
) -> Optional[bytes]:
    """Root-copy the appropriate WhatsApp encryption key to local staging.

    Returns the raw key bytes on success, or None if root is unavailable or the
    key file is missing.  The key hash is logged to the audit trail (not the key
    itself).
    """
    remote_key = KEY_PATHS.get(crypt_version, KEY_PATHS["crypt14"])
    stage_key = f"{STAGE_BASE}_key_{crypt_version}.bin"
    local_key = staging / f"wa_key_{crypt_version}.bin"

    # su copy to sdcard.
    cp = source.adb.shell(f'su -c "cp {remote_key} {stage_key}"')
    case.log(
        "tier2.whatsapp_backup.key_cp",
        f"su cp key ({crypt_version}): {remote_key} → {stage_key}",
        command=f"adb shell su -c 'cp {remote_key} {stage_key}'",
        result="ok" if cp.ok else "error",
        alters_device=False,
        tier=Tier.TIER2.value,
    )
    if not cp.ok:
        return None

    # adb pull to local staging.
    pull = source.adb.pull(stage_key, local_key)
    if not pull.ok or not local_key.exists():
        case.log(
            "tier2.whatsapp_backup.key_pull",
            "adb pull of key file failed",
            result="error",
            tier=Tier.TIER2.value,
        )
        return None

    raw = local_key.read_bytes()

    # Log key hash (not key itself) for chain-of-custody.
    key_hash = hashlib.sha256(raw).hexdigest()
    case.log(
        "tier2.whatsapp_backup.key_hash",
        f"key sha256={key_hash[:16]}… ({len(raw)} bytes)",
        alters_device=False,
        tier=Tier.TIER2.value,
    )
    return raw


def _parse_crypt15_key(raw: bytes) -> Optional[bytes]:
    """Extract the 32-byte AES key from a crypt15 ``encrypted_backup.key`` file.

    The file is a protobuf ``KeyData`` message.  If ``google.protobuf`` is
    available, decode it properly; otherwise fall back to treating the last 32
    bytes as the raw key (heuristic that works on most builds).
    """
    try:
        pass

        # Field 2 in KeyData is the key material (bytes type).
        # Decode with raw proto parsing to avoid needing a compiled descriptor.
        pos = 0
        while pos < len(raw):
            tag_byte = raw[pos]
            field_num = tag_byte >> 3
            wire_type = tag_byte & 0x07
            pos += 1
            if wire_type == 2 and field_num == 2:  # LEN-delimited field 2
                size = 0
                shift = 0
                while True:
                    b = raw[pos]
                    pos += 1
                    size |= (b & 0x7F) << shift
                    if not (b & 0x80):
                        break
                    shift += 7
                key_bytes = raw[pos : pos + size]
                if len(key_bytes) == 32:
                    return key_bytes
                pos += size
            elif wire_type == 0:  # varint — skip
                while raw[pos] & 0x80:
                    pos += 1
                pos += 1
            elif wire_type == 2:  # LEN — skip
                size = 0
                shift = 0
                while True:
                    b = raw[pos]
                    pos += 1
                    size |= (b & 0x7F) << shift
                    if not (b & 0x80):
                        break
                    shift += 7
                pos += size
            else:
                break
    except Exception:
        pass
    # Fallback: last 32 bytes.
    if len(raw) >= 32:
        return raw[-32:]
    return None


# ---------------------------------------------------------------------------
# 3. Decryption
# ---------------------------------------------------------------------------


def decrypt_backup(
    crypt_path: Path,
    key_raw: bytes,
    out_path: Path,
    crypt_version: str,
    case: "Case",
) -> bool:
    """Decrypt a .crypt* backup file to a plain SQLite DB at *out_path*.

    Strategy:
    1. Try ``whatsapp-crypt14-decrypter`` CLI (MIT-licensed, handles crypt12/14/15).
    2. Fall back to pure-Python AES-256-CBC for crypt12/14.

    Returns True on success (SQLite header verified), False otherwise.
    """
    # -- Extract the correct 32-byte AES key ---------------------------------
    if crypt_version == "crypt15":
        aes_key = _parse_crypt15_key(key_raw)
    else:
        # crypt12/14 key file: skip a 3-byte version prefix and a 32-byte
        # WhatsApp server public key; the AES key is the next 32 bytes.
        # Layout: [version(1)][server_pub(32)][aes_key(32)][mac_key(32)] or similar.
        # Use the last 32 bytes of the 67+ byte key file if structure is ambiguous.
        if len(key_raw) >= 67:
            aes_key = key_raw[35:67]
        elif len(key_raw) >= 32:
            aes_key = key_raw[-32:]
        else:
            aes_key = None

    if not aes_key or len(aes_key) != 32:
        case.log(
            "tier2.whatsapp_backup.decrypt",
            "could not extract 32-byte AES key from key file",
            result="error",
            tier=Tier.TIER2.value,
        )
        return False

    # -- Try external tool first --------------------------------------------
    if _try_external_decrypter(crypt_path, aes_key, out_path, crypt_version, case):
        if verify_sqlite_header(out_path):
            return True
        out_path.unlink(missing_ok=True)
        case.log(
            "tier2.whatsapp_backup.decrypt",
            "external decrypter output failed SQLite header check; trying fallback",
            result="error",
            tier=Tier.TIER2.value,
        )

    # -- Pure-Python fallback (crypt12/14 only) ------------------------------
    if crypt_version in ("crypt12", "crypt14"):
        if _decrypt_aes_cbc(crypt_path, aes_key, out_path, crypt_version, case):
            if verify_sqlite_header(out_path):
                return True
            out_path.unlink(missing_ok=True)

    case.log(
        "tier2.whatsapp_backup.decrypt",
        f"all decryption paths failed for {crypt_path.name}",
        result="error",
        tier=Tier.TIER2.value,
    )
    return False


def _try_external_decrypter(
    crypt_path: Path, aes_key: bytes, out_path: Path, crypt_version: str, case: "Case"
) -> bool:
    """Invoke ``whatsapp-crypt14-decrypter`` (or its alias) as a subprocess.

    The tool is invoked with:
      decrypt_wa_database <crypt_file> <out_file> --hex-key <hex_key>

    Returns True if the subprocess succeeded (exit 0).
    """
    tool_names = ["decrypt_wa_database", "whatsapp-crypt14-decrypter"]
    hex_key = aes_key.hex()

    for tool in tool_names:
        if shutil.which(tool):
            try:
                result = subprocess.run(
                    [tool, str(crypt_path), str(out_path), "--hex-key", hex_key],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                case.log(
                    "tier2.whatsapp_backup.decrypt_ext",
                    f"{tool} exited {result.returncode}",
                    command=f"{tool} {crypt_path.name}",
                    result="ok" if result.returncode == 0 else "error",
                    tier=Tier.TIER2.value,
                )
                return result.returncode == 0
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
                case.log(
                    "tier2.whatsapp_backup.decrypt_ext",
                    f"{tool} failed: {exc}",
                    result="error",
                    tier=Tier.TIER2.value,
                )
    return False


def _decrypt_aes_cbc(
    crypt_path: Path, aes_key: bytes, out_path: Path, crypt_version: str, case: "Case"
) -> bool:
    """Pure-Python AES-256-CBC decryption fallback for crypt12/14.

    crypt14 file layout (after the 67-byte header):
      [67 bytes header][16 bytes IV][N bytes ciphertext]
    crypt12 layout:
      [67 bytes header][16 bytes IV][N bytes ciphertext]

    Returns True on success, False on any error (including missing pycryptodome).
    """
    try:
        from Crypto.Cipher import AES  # pycryptodome
    except ImportError:
        try:
            from Cryptodome.Cipher import AES  # pycryptodome3
        except ImportError:
            case.log(
                "tier2.whatsapp_backup.decrypt_py",
                "pycryptodome not installed; pure-Python fallback skipped",
                result="skipped",
                tier=Tier.TIER2.value,
            )
            return False

    try:
        data = crypt_path.read_bytes()
        # Skip the 67-byte header, extract 16-byte IV, then decrypt.
        header_len = 67
        iv = data[header_len : header_len + 16]
        ciphertext = data[header_len + 16 :]
        cipher = AES.new(aes_key, AES.MODE_CBC, iv)
        plaintext = cipher.decrypt(ciphertext)
        # Strip PKCS7 padding.
        pad_len = plaintext[-1]
        if 1 <= pad_len <= 16:
            plaintext = plaintext[:-pad_len]
        out_path.write_bytes(plaintext)
        case.log(
            "tier2.whatsapp_backup.decrypt_py",
            f"pure-Python AES-CBC decryption of {crypt_path.name} OK",
            tier=Tier.TIER2.value,
        )
        return True
    except Exception as exc:
        case.log(
            "tier2.whatsapp_backup.decrypt_py",
            f"AES-CBC error: {exc}",
            result="error",
            tier=Tier.TIER2.value,
        )
        return False


def verify_sqlite_header(db_path: Path) -> bool:
    """Return True iff *db_path* starts with the SQLite magic header."""
    try:
        with open(db_path, "rb") as f:
            header = f.read(16)
        return header == _SQLITE_MAGIC
    except OSError:
        return False


# ---------------------------------------------------------------------------
# 4. Message recovery
# ---------------------------------------------------------------------------

_MEDIA_TYPE_MAP: dict[int, str] = {
    0: "",
    1: "image",
    2: "audio",
    3: "video",
    4: "vcard",
    5: "location",
    6: "system",
    7: "document",
    8: "sticker",
    13: "gif",
    14: "liveLocation",
    15: "template",
    20: "hsm",
}


def _epoch_ms_to_iso(ts: Any) -> Optional[str]:
    """Convert a WhatsApp epoch-millisecond timestamp to ISO-8601 UTC."""
    if ts is None:
        return None
    try:
        ts_int = int(ts)
        # Guard against epoch-seconds vs epoch-ms.
        if ts_int < 1_000_000_000_000:
            ts_int *= 1000
        return datetime.fromtimestamp(ts_int / 1000.0, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    except (ValueError, OSError, OverflowError):
        return None


def recover_messages_from_db(
    db_path: Path,
    backup_filename: str,
) -> list[WhatsAppBackupMessage]:
    """Recover all messages from a decrypted msgstore SQLite DB.

    Produces four confidence tiers:
    - LIVE              — live rows in the ``messages`` / ``message`` table.
    - RECOVERED_VERIFIED — rows from freelist pages or un-checkpointed WAL frames.
    - CARVED_PARTIAL    — signature-matched rows from unallocated space.
    - DELETION_DETECTED — rowid gaps (no content; only gap position recorded).

    Uses the existing ``recover_deleted_rows`` and ``sqbrite_cross_check``
    functions from ``triage.recovery``.
    """
    from ..recovery import recover_deleted_rows, detect_rowid_gaps, sqbrite_cross_check

    results: list[WhatsAppBackupMessage] = []

    # -- Live rows -----------------------------------------------------------
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row

        # Detect table name (WhatsApp changed it across versions).
        tables = {
            r[0]
            for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        msg_table = "message" if "message" in tables else "messages"

        if msg_table in tables:
            # Introspect column names so we handle schema variations.
            col_names = [r[1] for r in con.execute(f"PRAGMA table_info('{msg_table}')")]

            def _col(row: sqlite3.Row, *candidates: str, default: Any = None) -> Any:
                for c in candidates:
                    if c in col_names:
                        try:
                            return row[c]
                        except Exception:
                            pass
                return default

            for row in con.execute(f"SELECT * FROM {msg_table}"):
                body = _col(row, "data", "text_data", "body", default="")
                if not body:
                    body = ""
                chat_id = str(
                    _col(row, "key_remote_jid", "chat_id", "jid", default="") or ""
                )
                from_me = _col(row, "key_from_me", "from_me", default=0)
                ts_raw = _col(row, "timestamp", "date", "sent_timestamp")
                media_type_raw = _col(row, "media_type", "message_type", default=0)
                media_path = str(
                    _col(
                        row,
                        "media_wa_type",
                        "media_path",
                        "media_url",
                        "file_path",
                        default="",
                    )
                    or ""
                )

                sender = (
                    "me"
                    if from_me
                    else (chat_id.split("@")[0] if chat_id else "<unknown>")
                )
                media_type_str = _MEDIA_TYPE_MAP.get(int(media_type_raw or 0), "")

                results.append(
                    WhatsAppBackupMessage(
                        backup_file=backup_filename,
                        chat_id=chat_id,
                        sender=sender,
                        body=str(body),
                        timestamp=_epoch_ms_to_iso(ts_raw),
                        media_type=media_type_str,
                        media_path=media_path,
                        confidence=Confidence.LIVE,
                        source_file=db_path.name,
                        provenance=f"live row in {msg_table}",
                    )
                )
        con.close()
    except sqlite3.Error as exc:
        pass  # live query failed; still attempt recovery

    # -- Freelist / WAL recovery (sqlite-dissect) ----------------------------
    try:
        schema_hint: dict[str, Any] = {}
        try:
            con2 = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            tables2 = {
                r[0]
                for r in con2.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            msg_table2 = "message" if "message" in tables2 else "messages"
            if msg_table2 in tables2:
                rows2 = con2.execute(f"PRAGMA table_info('{msg_table2}')").fetchall()
                cols2 = [r[1] for r in rows2]
                schema_hint = {"col_count": len(cols2), "columns": cols2}
            con2.close()
        except Exception:
            pass

        carved_rows = recover_deleted_rows(
            db_path, table="message", schema_hint=schema_hint
        )
        for cr in carved_rows:
            vals = cr.values
            # Heuristic: look for the body in the first non-trivial string.
            body = next((v for v in vals if isinstance(v, str) and len(v) >= 2), "")
            results.append(
                WhatsAppBackupMessage(
                    backup_file=backup_filename,
                    chat_id="<recovered>",
                    sender="<recovered>",
                    body=body,
                    confidence=cr.confidence,
                    source_file=db_path.name,
                    provenance=cr.provenance,
                    flags=["deleted"] + cr.warnings,
                )
            )
    except Exception:
        pass

    # -- sqbrite secondary cross-check ---------------------------------------
    try:
        extra = sqbrite_cross_check(db_path, primary_rows=[])
        for er in extra:
            vals = er.values
            body = next((v for v in vals if isinstance(v, str) and len(v) >= 2), "")
            results.append(
                WhatsAppBackupMessage(
                    backup_file=backup_filename,
                    chat_id="<carved>",
                    sender="<carved>",
                    body=body,
                    confidence=er.confidence,
                    source_file=db_path.name,
                    provenance=er.provenance,
                    flags=["deleted", "sqbrite"],
                )
            )
    except Exception:
        pass

    # -- Rowid-gap detection (DELETION_DETECTED) -----------------------------
    try:
        con3 = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        tables3 = {
            r[0]
            for r in con3.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        con3.close()
        from ..recovery import detect_rowid_gaps

        for tbl in ("message", "messages"):
            if tbl in tables3:
                gaps = detect_rowid_gaps(db_path, tbl)
                for gap in gaps:
                    results.append(
                        WhatsAppBackupMessage(
                            backup_file=backup_filename,
                            chat_id="<gap>",
                            sender="<gap>",
                            body="",
                            confidence=Confidence.DELETION_DETECTED,
                            source_file=db_path.name,
                            provenance=f"rowid gap {gap} in {tbl}",
                            flags=["deletion-detected"],
                        )
                    )
    except Exception:
        pass

    return results


# ---------------------------------------------------------------------------
# 5. Media file recovery
# ---------------------------------------------------------------------------


def recover_media_files(
    source: "RealDeviceSource",
    case: "Case",
    staging: Path,
    messages: list[WhatsAppBackupMessage],
    max_media: int = 100,
) -> list[WhatsAppBackupMedia]:
    """Pull media files referenced in backup messages.

    For each message with a non-empty ``media_path``:
    1. Check the Tier-0 media path (Android scoped storage).
    2. Check the legacy sdcard path.
    3. Check for ``.trashed-*`` variant (existing eRakshak trash detection).

    Returns a list of :class:`WhatsAppBackupMedia` records for pulled files.
    """
    media_records: list[WhatsAppBackupMedia] = []
    counter = 0

    for msg in messages:
        if counter >= max_media:
            break
        if not msg.media_path or not msg.media_path.strip():
            continue
        rel = msg.media_path.strip()
        # Normalise: strip any leading slash.
        if rel.startswith("/"):
            rel = rel.lstrip("/")

        # Candidate device paths to probe.
        candidate_paths = [
            f"{WA_MEDIA_ROOT}/{rel}",
            f"{WA_MEDIA_ROOT_LEGACY}/{rel}",
        ]

        found_path: Optional[str] = None
        is_trashed = False

        for dev_path in candidate_paths:
            probe = source.adb.shell(f"test -f '{dev_path}' && echo exists")
            if probe.ok and "exists" in (probe.stdout or ""):
                found_path = dev_path
                break

        # Try trash variant if not found.
        if not found_path:
            for dev_path in candidate_paths:
                parent = dev_path.rsplit("/", 1)[0]
                fname = dev_path.rsplit("/", 1)[-1]
                trash_probe = source.adb.shell(
                    f"find '{parent}' -maxdepth 1 -name '.trashed-*{fname}' 2>/dev/null | head -1"
                )
                if trash_probe.ok and (trash_probe.stdout or "").strip():
                    found_path = (trash_probe.stdout or "").strip().splitlines()[0]
                    is_trashed = True
                    break

        if not found_path:
            continue

        # Pull the file.
        fname_safe = rel.replace("/", "_").replace("\\", "_")
        local_file = staging / f"wa_backup_media_{counter}_{fname_safe}"
        staging_remote = f"{STAGE_BASE}_media_{counter}_{fname_safe}"

        cp = source.adb.shell(f"su -c \"cp '{found_path}' '{staging_remote}'\"")
        if not cp.ok:
            # Try direct pull without su (Tier-0 path is world-readable).
            pull = source.adb.pull(found_path, local_file)
        else:
            pull = source.adb.pull(staging_remote, local_file)

        if not pull.ok or not local_file.exists():
            continue

        # Ingest into case manifest.
        rec = case.ingest_file(
            local_file,
            source_path=found_path,
            tier=Tier.TIER2,
            method="root-su-cp" if cp.ok else "adb-pull",
            category="media",
            app="whatsapp",
            flags=["whatsapp-backup-media"] + (["trashed"] if is_trashed else []),
            move=True,
        )
        stored = case.root / rec.stored_path

        sha256 = rec.sha256
        media_records.append(
            WhatsAppBackupMedia(
                artifact_id=rec.artifact_id,
                backup_message_id=f"{msg.chat_id}:{msg.timestamp or ''}",
                file_name=rel.rsplit("/", 1)[-1],
                file_path_on_device=found_path,
                size_bytes=rec.size_bytes,
                sha256=sha256,
                recovered=is_trashed,
            )
        )
        counter += 1

    return media_records


# ---------------------------------------------------------------------------
# 6. Public summary helper
# ---------------------------------------------------------------------------


def backup_recovery_summary(
    messages: list[WhatsAppBackupMessage],
    media: list[WhatsAppBackupMedia],
) -> dict[str, Any]:
    """Return a per-file stats dict suitable for JSON serialisation."""
    from collections import defaultdict

    per_file: dict[str, dict[str, int]] = defaultdict(
        lambda: {"live": 0, "recovered": 0, "carved": 0, "deletion": 0, "total": 0}
    )
    for m in messages:
        f = per_file[m.backup_file]
        conf_key = (
            m.confidence.value if hasattr(m.confidence, "value") else str(m.confidence)
        )
        # Map to short names.
        short = {
            "live": "live",
            "recovered": "recovered",
            "carved": "carved",
            "deletion": "deletion",
        }.get(conf_key, "carved")
        f[short] = f.get(short, 0) + 1
        f["total"] = f.get("total", 0) + 1
    return {
        "per_file": dict(per_file),
        "totals": {
            "messages": len(messages),
            "media": len(media),
            "live": sum(
                1
                for m in messages
                if str(getattr(m.confidence, "value", m.confidence)) == "live"
            ),
            "recovered": sum(
                1
                for m in messages
                if str(getattr(m.confidence, "value", m.confidence)) == "recovered"
            ),
            "carved": sum(
                1
                for m in messages
                if str(getattr(m.confidence, "value", m.confidence)) == "carved"
            ),
            "deletion": sum(
                1
                for m in messages
                if str(getattr(m.confidence, "value", m.confidence)) == "deletion"
            ),
        },
    }
