"""FCM/GCM queued-push mining — a second-chance source for *metadata*, rarely content.

Forensic purpose
----------------
``/data/data/com.google.android.gms/files/fcm_queued_messages.ldb/`` is a LevelDB
store (a directory, despite the ``.ldb`` name) owned by Google Play Services, not
by the receiving app. Play Services writes a record when a push arrives and
deletes it once the target app takes delivery, so the steady state is "almost
everything is a tombstone" — and the forensic value lies precisely in the
superseded and deleted records still sitting in the write-ahead log.

Because the queue belongs to GMS, records survive:
  * the user deleting the message inside the app,
  * the server deleting the message,
  * the app being uninstalled afterwards.

What this actually yields — and what it does not
------------------------------------------------
Protocol-level metadata is present regardless of app: target package, message
id, sender (FCM project number), collapse key, and the arrival timestamp encoded
in the LevelDB key. That is genuine evidence: "package X received a push at time
T from sender S."

The 4 KB data payload is at the app developer's discretion. Some apps
(Twitter/X, Instagram, Tumblr, Facebook, TikTok…) put readable notification text
in it. End-to-end messengers deliberately do not: Signal, Molly, Session,
Threema, WhatsApp and Wickr send content-free wakeup pushes, or their own
ciphertext. For those packages this module reports **timing and existence only**,
and says so in every record's caveats. Nothing here is ever a decrypted message.

Implementation limitations
--------------------------
* Only the ``.log`` write-ahead file is parsed properly. It is uncompressed, flat
  and resynchronises at each 32 KiB block, so a corrupt tail costs one record.
  ``.ldb``/``.sst`` table files are usually snappy-compressed; we do not ship a
  snappy decoder, so those are reported as unparsed rather than half-guessed.
* Record CRC32C is checked where cheap, but a mismatch never drops a record — a
  damaged tail is exactly where deleted data lives. Mismatch counts are reported.
* The protobuf field map is transcribed from ALEAPP's FCM structure. Field
  numbers are stable enough to be useful but are not documented by Google; every
  positionally-derived field is caveated as inferred.
* Key layout is ``<prefix>:<unix_micros>%<suffix>``. Records read from a ``.log``
  carry the bare user key; only records read out of a ``.ldb`` table carry the
  8-byte internal sequence/type trailer. Stripping 8 bytes off a log key (a known
  bug in tooling that reuses ALEAPP's iterator) silently corrupts every key.
"""

from __future__ import annotations

import datetime as _dt
import re
import sqlite3
import struct
from pathlib import Path
from typing import Any, Iterable, Optional, Union

PathLike = Union[str, Path]

FCM_PATHS: list[str] = [
    # The LevelDB queue itself (a directory).
    "/data/data/com.google.android.gms/files/fcm_queued_messages.ldb",
    "/data/user/0/com.google.android.gms/files/fcm_queued_messages.ldb",
    # Legacy GCM Reliable-Message-Queue SQLite stores (pre-2019 Play Services).
    # Filenames and schema are UNVERIFIED — we probe and discover, never assume.
    "/data/data/com.google.android.gms/databases/gcm_store",
    "/data/data/com.google.android.gms/databases/gcm_rmq2",
    "/data/data/com.google.android.gms/databases/app_gcm_store",
    # Identity/context, useful for correlating a push to a device+account.
    "/data/data/com.google.android.gms/databases/gservices.db",
    "/data/data/com.google.android.gms/databases/phenotype.db",
    "/data/data/com.google.android.gms/databases/gms.notifications.db",
]

# Per-app push registration artefacts. Cross-referencing these against the
# package field inside the queue proves "this installation of this app received
# these pushes", and survives the app being uninstalled afterwards.
PER_APP_FCM_PATHS: list[str] = [
    "shared_prefs/com.google.android.gms.appid.xml",
    "files/PersistedInstallation.json",
    "no_backup/PersistedInstallation.json",
]

LEVELDB_BLOCK_SIZE = 32768
LEVELDB_HEADER_SIZE = 7

REC_ZERO, REC_FULL, REC_FIRST, REC_MIDDLE, REC_LAST = 0, 1, 2, 3, 4

# Above this we skip CRC verification rather than stall a field triage on a pure
# Python CRC loop. The skip is reported, never silent.
MAX_CRC_VERIFY_BYTES = 8 * 1024 * 1024

# Packages whose FCM payload is content-free by design (wakeup push) or is the
# app's own ciphertext. For these, the store yields timing and existence only.
WAKEUP_ONLY_PACKAGES: frozenset[str] = frozenset(
    {
        "org.thoughtcrime.securesms",  # Signal
        "im.molly.app",
        "im.molly.foss",
        "network.loki.messenger",  # Session
        "ch.threema.app",
        "ch.threema.app.work",
        "com.whatsapp",
        "com.whatsapp.w4b",
        "com.mywickr.wickr2",
        "com.wickr.pro",
        "org.telegram.messenger",  # push body is a notification stub, not the message
    }
)

# Apps observed by published research to place readable notification text in the
# payload. Presence here raises expectations, never conclusions — we still only
# report what the bytes actually contain.
READABLE_CONTENT_PACKAGES: frozenset[str] = frozenset(
    {
        "com.twitter.android",
        "com.instagram.android",
        "com.zhiliaoapp.musically",
        "com.tumblr",
        "com.facebook.katana",
        "com.facebook.orca",
        "kik.android",
        "com.skype.raider",
        "com.microsoft.office.outlook",
        "com.microsoft.xboxone.smartglass",
    }
)

_PAYLOAD_CAVEAT = (
    "Raw push-payload fragment recovered from the Google Play Services delivery "
    "queue. This is NOT a decrypted message. For end-to-end-encrypted messengers "
    "the payload body is itself encrypted or absent by design, and only routing "
    "metadata (target package, sender id, arrival time) is readable."
)

_QUEUE_CAVEAT = (
    "fcm_queued_messages.ldb is a delivery queue owned by Google Play Services, "
    "not by the receiving app: records can survive in-app deletion, server-side "
    "deletion and app uninstall. Conversely, delivered records are tombstoned, so "
    "absence of a record is not evidence that no push arrived."
)


# --- CRC32C ------------------------------------------------------------------
_CRC32C_POLY = 0x82F63B78
_CRC32C_TABLE: list[int] = []
for _i in range(256):
    _c = _i
    for _ in range(8):
        _c = (_c >> 1) ^ (_CRC32C_POLY if _c & 1 else 0)
    _CRC32C_TABLE.append(_c)
del _i, _c


def crc32c(data: bytes, crc: int = 0) -> int:
    """CRC-32C (Castagnoli) — NOT zlib's CRC-32. Used by LevelDB record framing."""
    crc ^= 0xFFFFFFFF
    for b in data:
        crc = _CRC32C_TABLE[(crc ^ b) & 0xFF] ^ (crc >> 8)
    return crc ^ 0xFFFFFFFF


def mask_crc(crc: int) -> int:
    """LevelDB stores CRCs rotated and offset so a CRC never sits over its data."""
    return (((crc >> 15) | (crc << 17)) + 0xA282EAD8) & 0xFFFFFFFF


def unmask_crc(masked: int) -> int:
    rot = (masked - 0xA282EAD8) & 0xFFFFFFFF
    return ((rot >> 17) | (rot << 15)) & 0xFFFFFFFF


# --- varint ------------------------------------------------------------------
def _read_varint(buf: bytes, pos: int, max_bytes: int = 10) -> tuple[Optional[int], int]:
    """LEB128 decode. Returns (None, pos) on a truncated/oversized varint so every
    caller can degrade gracefully instead of raising."""
    result = 0
    shift = 0
    n = len(buf)
    for i in range(max_bytes):
        if pos + i >= n:
            return None, pos
        b = buf[pos + i]
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos + i + 1
        shift += 7
    return None, pos


# --- LevelDB write-ahead log -------------------------------------------------
def _read_leveldb_log_verbose(
    path: PathLike, *, max_records: int = 20000
) -> dict[str, Any]:
    """Full-detail log read. :func:`read_leveldb_log` is the plain-list wrapper.

    Returns records plus the framing statistics a report needs: how many blocks
    were walked, how many headers were rejected, how many CRCs failed, and
    whether the record cap truncated the result.
    """
    out: dict[str, Any] = {
        "records": [],
        "truncated": False,
        "blocks": 0,
        "bad_headers": 0,
        "crc_checked": 0,
        "crc_mismatch": 0,
        "crc_verified": True,
        "dropped_fragments": 0,
        "error": "",
    }
    p = Path(path)
    try:
        raw = p.read_bytes()
    except OSError as exc:
        out["error"] = f"{exc.__class__.__name__}: {exc}"
        return out

    verify = len(raw) <= MAX_CRC_VERIFY_BYTES
    out["crc_verified"] = verify

    records: list[bytes] = []
    pending: Optional[bytearray] = None

    for block_start in range(0, len(raw), LEVELDB_BLOCK_SIZE):
        if len(records) >= max_records:
            out["truncated"] = True
            break
        block = raw[block_start : block_start + LEVELDB_BLOCK_SIZE]
        out["blocks"] += 1
        offset = 0
        blen = len(block)
        # A record never starts within the last 6 bytes of a block; the leftover
        # is zero padding and must be skipped.
        while offset + LEVELDB_HEADER_SIZE <= blen and offset <= LEVELDB_BLOCK_SIZE - LEVELDB_HEADER_SIZE:
            if len(records) >= max_records:
                out["truncated"] = True
                break
            header = block[offset : offset + LEVELDB_HEADER_SIZE]
            stored_crc = struct.unpack("<I", header[0:4])[0]
            length = struct.unpack("<H", header[4:6])[0]
            rtype = header[6]

            if rtype == REC_ZERO and length == 0:
                break  # preallocated / padding tail: nothing further in this block

            data_start = offset + LEVELDB_HEADER_SIZE
            data_end = data_start + length
            if rtype > REC_LAST or data_end > blen:
                # Corrupt or truncated framing. Resynchronise at the next block
                # rather than guessing byte offsets inside garbage.
                out["bad_headers"] += 1
                break

            data = block[data_start:data_end]
            offset = data_end

            if verify:
                out["crc_checked"] += 1
                if mask_crc(crc32c(bytes([rtype]) + data)) != stored_crc:
                    out["crc_mismatch"] += 1
                    # Deliberately NOT dropped: a bad CRC in a WAL tail is the
                    # normal signature of exactly the residual data we want.

            if rtype == REC_FULL:
                if pending is not None:
                    out["dropped_fragments"] += 1
                    pending = None
                records.append(data)
            elif rtype == REC_FIRST:
                if pending is not None:
                    out["dropped_fragments"] += 1
                pending = bytearray(data)
            elif rtype == REC_MIDDLE:
                if pending is None:
                    out["dropped_fragments"] += 1
                else:
                    pending.extend(data)
            elif rtype == REC_LAST:
                if pending is None:
                    out["dropped_fragments"] += 1
                else:
                    pending.extend(data)
                    records.append(bytes(pending))
                    pending = None

    if pending is not None:
        # A FIRST/MIDDLE run with no LAST: the file was truncated mid-record.
        out["dropped_fragments"] += 1

    if len(records) >= max_records:
        out["truncated"] = True
    out["records"] = records[:max_records]
    return out


def read_leveldb_log(path: PathLike, *, max_records: int = 20000) -> list[bytes]:
    """Dependency-free LevelDB write-ahead-log reader.

    32 KiB blocks; each record is a 7-byte header (4-byte masked CRC32C, 2-byte
    little-endian length, 1-byte type) followed by ``length`` payload bytes.
    FIRST/MIDDLE/LAST fragments are reassembled across block boundaries. A record
    whose length would run past the end of its block is skipped and the reader
    resynchronises at the next block. Never raises.

    Returns the reassembled *user records* (write batches). Use
    :func:`parse_fcm_store` when you also need the framing statistics and the
    truncation flag.
    """
    return list(_read_leveldb_log_verbose(path, max_records=max_records)["records"])


def parse_write_batch(record: bytes) -> list[dict[str, Any]]:
    """Decode one reassembled log record (a LevelDB WriteBatch).

    Layout: 8-byte LE sequence, 4-byte LE entry count, then per entry a 1-byte
    value type (0 = deletion/tombstone, 1 = value), a varint32-prefixed key and,
    for values only, a varint32-prefixed value. Malformed entries end the batch
    rather than raising.
    """
    out: list[dict[str, Any]] = []
    if len(record) < 12:
        return out
    seq = struct.unpack("<Q", record[0:8])[0]
    count = struct.unpack("<I", record[8:12])[0]
    pos = 12
    n = len(record)
    # Guard against a corrupt count claiming millions of entries.
    count = min(count, max(0, (n - 12) // 2 + 1))
    for i in range(count):
        if pos >= n:
            break
        vtype = record[pos]
        pos += 1
        if vtype not in (0, 1):
            break
        klen, pos = _read_varint(record, pos)
        if klen is None or klen < 0 or pos + klen > n:
            break
        key = record[pos : pos + klen]
        pos += klen
        value = b""
        if vtype == 1:
            vlen, pos = _read_varint(record, pos)
            if vlen is None or vlen < 0 or pos + vlen > n:
                break
            value = record[pos : pos + vlen]
            pos += vlen
        out.append(
            {
                "seq": seq + i,
                "type": "deleted" if vtype == 0 else "value",
                "key": key,
                "value": value,
            }
        )
    return out


def user_key(raw_key: bytes, *, from_log: bool) -> bytes:
    """Strip the 8-byte internal trailer only for keys read from a table file.

    Log records carry the bare user key. Blindly slicing ``[:-8]`` (as some
    tooling does) removes eight real characters from every log-sourced key.
    """
    if from_log or len(raw_key) <= 8:
        return raw_key
    return raw_key[:-8]


# --- strings -----------------------------------------------------------------
def extract_strings(blob: bytes, *, min_len: int = 4) -> list[str]:
    """Printable ASCII runs plus non-ASCII UTF-8 runs, in order of appearance.

    Used only to produce clearly-labelled *raw fragments*; nothing here is
    interpreted as message content.
    """
    if not blob or min_len < 1:
        return []
    out: list[str] = []
    seen: set[str] = set()
    try:
        pattern = re.compile(rb"[\x20-\x7e]{%d,}" % int(min_len))
        for m in pattern.finditer(blob):
            s = m.group().decode("ascii", errors="ignore")
            if s and s not in seen:
                seen.add(s)
                out.append(s)
        # Second pass for multi-byte UTF-8 text (emoji, non-Latin scripts) that
        # the ASCII pass cannot see. Only runs containing a non-ASCII character
        # are added, so this complements rather than duplicates.
        text = blob.decode("utf-8", errors="ignore")
        for m in re.finditer(r"[^\x00-\x1f\x7f]{%d,}" % int(min_len), text):
            s = m.group()
            if any(ord(ch) > 127 for ch in s) and s not in seen:
                seen.add(s)
                out.append(s)
    except (re.error, ValueError, UnicodeDecodeError):
        return out
    return out


# --- protobuf ----------------------------------------------------------------
def _iter_protobuf(buf: bytes) -> Iterable[tuple[int, int, Any]]:
    """Minimal wire-format walker. Stops at the first thing it cannot decode
    rather than guessing — a partially-decoded record is honest, a fabricated one
    is not."""
    i, n = 0, len(buf)
    while i < n:
        tag, i = _read_varint(buf, i)
        if tag is None:
            return
        fnum, wt = tag >> 3, tag & 7
        if fnum == 0:
            return
        if wt == 0:
            v, i = _read_varint(buf, i)
            if v is None:
                return
            yield fnum, wt, v
        elif wt == 1:
            if i + 8 > n:
                return
            yield fnum, wt, buf[i : i + 8]
            i += 8
        elif wt == 2:
            ln, i = _read_varint(buf, i)
            if ln is None or ln < 0 or i + ln > n:
                return
            yield fnum, wt, buf[i : i + ln]
            i += ln
        elif wt == 5:
            if i + 4 > n:
                return
            yield fnum, wt, buf[i : i + 4]
            i += 4
        else:
            return  # groups / unknown wire type: stop, do not guess


def _as_text(raw: Any) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw)


def parse_fcm_value(value: bytes) -> dict[str, Any]:
    """Decode an FCM queue protobuf value into package + data key/value pairs.

    Field map (ALEAPP FCM_PROTOBUFF_STRUCTURE): top-level field 2 is the
    container; inside it field 5 is the target package, field 7 is a repeated
    key/value pair of the FCM data payload (sub-fields 1 and 2), field 9 echoes
    the LevelDB key, and fields 2/3/6 are strings whose meaning Google does not
    document. Everything positional is flagged as inferred.
    """
    out: dict[str, Any] = {
        "package": "",
        "data": {},
        "container_fields": {},
        "key_echo": "",
        "decoded": False,
    }
    try:
        for fnum, wt, val in _iter_protobuf(value):
            if fnum != 2 or wt != 2 or not isinstance(val, bytes):
                continue
            out["decoded"] = True
            for cf, cwt, cval in _iter_protobuf(val):
                if cwt != 2 or not isinstance(cval, bytes):
                    continue
                if cf == 5:
                    out["package"] = _as_text(cval)
                elif cf == 9:
                    out["key_echo"] = _as_text(cval)
                elif cf == 7:
                    k = v = None
                    for kf, kwt, kval in _iter_protobuf(cval):
                        if kwt != 2 or not isinstance(kval, bytes):
                            continue
                        if kf == 1:
                            k = _as_text(kval)
                        elif kf == 2:
                            v = _as_text(kval)
                    if k is not None:
                        out["data"][k] = v if v is not None else ""
                elif cf in (2, 3, 6):
                    out["container_fields"][str(cf)] = _as_text(cval)
            break
    except (ValueError, IndexError, struct.error):
        # Degrade to whatever was decoded before the malformation.
        pass
    return out


# --- key / timestamp ---------------------------------------------------------
def parse_fcm_key(key_text: str) -> dict[str, Any]:
    """Split ``<prefix>:<unix_micros>%<suffix>`` and render the arrival time.

    Returns ``timestamp = None`` when the key does not carry a plausible
    microsecond epoch — an unparsable key is reported as unparsable, never
    back-filled with 'now'.
    """
    out: dict[str, Any] = {"key": key_text, "timestamp": None, "micros": None, "suffix": ""}
    if ":" not in key_text:
        return out
    _, rest = key_text.split(":", 1)
    digits = rest.split("%", 1)
    if len(digits) == 2:
        out["suffix"] = digits[1]
    raw = digits[0].strip()
    if not raw.isdigit():
        return out
    micros = int(raw)
    # Sanity window: 2001-09-09 .. 2286-11-20 in microseconds.
    if not (1_000_000_000_000_000 <= micros <= 10_000_000_000_000_000):
        return out
    out["micros"] = micros
    try:
        ts = _dt.datetime(1970, 1, 1, tzinfo=_dt.timezone.utc) + _dt.timedelta(
            microseconds=micros
        )
        out["timestamp"] = ts.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    except (OverflowError, ValueError):
        out["timestamp"] = None
    return out


_ID_KEYS = ("google.message_id", "gcm.message_id", "message_id", "google.c.a.c_id")
_SENDER_KEYS = ("from", "google.c.sender.id", "sender", "gcm.n.from")
_COLLAPSE_KEYS = ("collapse_key", "google.c.a.c_l", "gcm.n.collapse_key")


def _first(data: dict[str, Any], keys: Iterable[str]) -> str:
    for k in keys:
        v = data.get(k)
        if v:
            return str(v)
    return ""


def _build_record(
    key_bytes: bytes,
    value: bytes,
    *,
    entry_type: str,
    seq: int,
    from_log: bool,
) -> dict[str, Any]:
    key_text = user_key(key_bytes, from_log=from_log).decode("utf-8", errors="replace")
    keyinfo = parse_fcm_key(key_text)
    decoded = parse_fcm_value(value)
    data = decoded["data"]

    caveats: list[str] = [_PAYLOAD_CAVEAT]
    package = decoded["package"]

    message_id = _first(data, _ID_KEYS)
    sender = _first(data, _SENDER_KEYS)
    collapse_key = _first(data, _COLLAPSE_KEYS)

    inferred: list[str] = []
    cf = decoded["container_fields"]
    if not message_id and cf.get("3"):
        message_id = cf["3"]
        inferred.append("message_id from protobuf container field 3")
    if not sender and cf.get("2"):
        sender = cf["2"]
        inferred.append("sender from protobuf container field 2")
    if not collapse_key and cf.get("6"):
        collapse_key = cf["6"]
        inferred.append("collapse_key from protobuf container field 6")
    if inferred:
        caveats.append(
            "Field mapping inferred from protobuf field numbers (ALEAPP "
            "FCM_PROTOBUFF_STRUCTURE), not from a documented schema: "
            + "; ".join(inferred)
            + ". Treat these values as provisional."
        )

    fragments = extract_strings(value, min_len=5)
    raw_preview = " | ".join(fragments)[:400]

    # Honesty gate: a payload from an E2E messenger is never 'readable content',
    # however many printable bytes happen to be in it.
    if package in WAKEUP_ONLY_PACKAGES:
        content_readable = False
        caveats.append(
            f"{package} sends content-free wakeup pushes (or its own ciphertext) by "
            "design: the client fetches the actual message over its own authenticated "
            "channel. This record is evidence of ARRIVAL TIME and EXISTENCE only — no "
            "message content is available here or anywhere in this store."
        )
    else:
        content_readable = bool(data) and any(
            isinstance(v, str) and len(v) >= 8 and any(ch.isalpha() for ch in v)
            for v in data.values()
        )
        if content_readable:
            caveats.append(
                "Data-payload key/value pairs contain readable text. This is the "
                "notification payload the app's server chose to send, not the message "
                "store: it may be a truncated preview, a placeholder, or stale relative "
                "to what the user finally saw."
            )
        elif not package:
            caveats.append(
                "Target package could not be decoded from the protobuf; app attribution "
                "for this record is unknown."
            )

    if entry_type == "deleted":
        caveats.append(
            "LevelDB tombstone: this key was DELETED from the queue (normal after "
            "delivery). The key and its arrival timestamp survive; the value does not."
        )

    return {
        "sender": sender,
        "message_id": message_id,
        "collapse_key": collapse_key,
        "app": package,
        "raw_preview": raw_preview,
        "timestamp": keyinfo["timestamp"],
        "content_readable": bool(content_readable),
        "caveats": caveats,
        # Supporting detail, kept out of the headline fields above.
        "key": keyinfo["key"],
        "entry_type": entry_type,
        "sequence": seq,
        "data_keys": sorted(data.keys()),
        "data": data,
        "value_bytes": len(value),
    }


# --- legacy SQLite GCM store -------------------------------------------------
_SQLITE_MAGIC = b"SQLite format 3\x00"

_COL_HINTS = {
    "app": ("app", "package", "pkg", "category"),
    "sender": ("sender", "from", "sender_id"),
    "message_id": ("persistent_id", "message_id", "msg_id", "id"),
    "collapse_key": ("collapse", "collapse_key"),
    "timestamp": ("timestamp", "ts", "time", "date"),
    "payload": ("payload", "data", "body", "message"),
}


def _classify_columns(cols: list[str]) -> dict[str, str]:
    """Map discovered columns onto our record fields. The legacy gcm_store schema
    is UNVERIFIED across GMS versions, so nothing is hardcoded — we probe
    sqlite_master and classify by name."""
    mapping: dict[str, str] = {}
    lowered = {c: c.lower() for c in cols}
    for role, hints in _COL_HINTS.items():
        for c, lc in lowered.items():
            if c in mapping.values():
                continue
            if lc in hints:
                mapping[role] = c
                break
        if role in mapping:
            continue
        for c, lc in lowered.items():
            if c in mapping.values():
                continue
            if any(h in lc for h in hints):
                mapping[role] = c
                break
    return mapping


def _parse_legacy_sqlite(path: Path, *, max_records: int) -> dict[str, Any]:
    out: dict[str, Any] = {
        "records": [],
        "format": "sqlite",
        "truncated": False,
        "tables": [],
        "caveats": [
            _QUEUE_CAVEAT,
            "Legacy GCM SQLite store: the table/column schema of gcm_store / gcm_rmq2 "
            "is not documented and varies by Play Services version, so the schema was "
            "discovered from sqlite_master and columns were mapped by name heuristics. "
            "Column attribution is provisional.",
        ],
    }
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        out["format"] = "unknown"
        out["caveats"].append(f"sqlite open failed: {exc}")
        return out

    try:
        con.row_factory = sqlite3.Row
        try:
            tables = con.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='table'"
            ).fetchall()
        except sqlite3.Error as exc:
            out["caveats"].append(f"schema discovery failed: {exc}")
            return out

        for trow in tables:
            name = trow["name"]
            if name in ("android_metadata", "sqlite_sequence"):
                continue
            out["tables"].append({"name": name, "sql": trow["sql"] or ""})
            try:
                cur = con.execute(f'SELECT * FROM "{name}" LIMIT {int(max_records) + 1}')
                cols = [d[0] for d in cur.description or []]
                rows = cur.fetchall()
            except sqlite3.Error as exc:
                out["caveats"].append(f"table {name!r} unreadable: {exc}")
                continue

            if len(rows) > max_records:
                out["truncated"] = True
                rows = rows[:max_records]

            mapping = _classify_columns(cols)
            for row in rows:
                try:
                    d = {c: row[c] for c in cols}
                except (IndexError, KeyError):
                    continue  # skip the record, never raise
                blob_parts: list[str] = []
                for v in d.values():
                    if isinstance(v, bytes):
                        blob_parts.extend(extract_strings(v, min_len=5))
                    elif isinstance(v, str) and len(v) >= 5:
                        blob_parts.append(v)
                package = str(d.get(mapping.get("app", ""), "") or "")
                payload_col = mapping.get("payload", "")
                payload_val = d.get(payload_col) if payload_col else None
                readable = bool(payload_val) and package not in WAKEUP_ONLY_PACKAGES
                caveats = [_PAYLOAD_CAVEAT, f"source table: {name}"]
                if package in WAKEUP_ONLY_PACKAGES:
                    caveats.append(
                        f"{package} sends content-free wakeup pushes: arrival time and "
                        "existence only, never content."
                    )
                out["records"].append(
                    {
                        "sender": str(d.get(mapping.get("sender", ""), "") or ""),
                        "message_id": str(d.get(mapping.get("message_id", ""), "") or ""),
                        "collapse_key": str(d.get(mapping.get("collapse_key", ""), "") or ""),
                        "app": package,
                        "raw_preview": " | ".join(blob_parts)[:400],
                        "timestamp": _normalise_ts(d.get(mapping.get("timestamp", ""))),
                        "content_readable": bool(readable),
                        "caveats": caveats,
                        "key": "",
                        "entry_type": "value",
                        "sequence": -1,
                        "data_keys": sorted(str(c) for c in cols),
                        "data": {},
                        "value_bytes": 0,
                        "table": name,
                        "column_mapping": mapping,
                    }
                )
    finally:
        try:
            con.close()
        except sqlite3.Error:
            pass
    return out


def _normalise_ts(value: Any) -> Optional[str]:
    """Best-effort epoch -> ISO-8601 Z. Returns None rather than inventing a time."""
    if value in (None, ""):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        s = str(value)
        return s if s.endswith("Z") else None
    # Accept seconds / milliseconds / microseconds within a plausible window.
    for divisor in (1, 1000, 1_000_000):
        secs = n / divisor
        if 1_000_000_000 <= secs <= 4_102_444_800:  # 2001-09-09 .. 2100-01-01
            try:
                return (
                    _dt.datetime.fromtimestamp(secs, tz=_dt.timezone.utc)
                    .strftime("%Y-%m-%dT%H:%M:%S.%f")
                    + "Z"
                )
            except (OverflowError, OSError, ValueError):
                return None
    return None


# --- public entry points -----------------------------------------------------
def parse_fcm_store(path: PathLike, *, max_records: int = 20000) -> dict[str, Any]:
    """Parse one FCM store file (a LevelDB ``.log`` or a legacy GCM SQLite DB).

    Returns ``{"records": [...], "format": "leveldb-log"|"sqlite"|"unknown",
    "truncated": bool, "caveats": [...]}``. Passing the ``.ldb`` *directory* is
    accepted and delegates to :func:`parse_fcm_dir`.
    """
    p = Path(path)
    base: dict[str, Any] = {
        "path": str(p),
        "records": [],
        "format": "unknown",
        "truncated": False,
        "caveats": [_QUEUE_CAVEAT],
    }

    try:
        if not p.exists():
            base["caveats"].append(
                "No file at this path in the staged acquisition. Absence here is not "
                "evidence that the store is absent from the device unless the path was "
                "actually pulled."
            )
            return base
        if p.is_dir():
            return parse_fcm_dir(p, max_records=max_records)
        with p.open("rb") as fh:
            head = fh.read(16)
    except OSError as exc:
        base["caveats"].append(f"unreadable: {exc.__class__.__name__}: {exc}")
        return base

    if head == _SQLITE_MAGIC:
        res = _parse_legacy_sqlite(p, max_records=max_records)
        res["path"] = str(p)
        res.setdefault("truncated", False)
        if res["truncated"]:
            res["caveats"].append(
                f"row cap of {max_records} reached: the record list is TRUNCATED and "
                "counts are a lower bound"
            )
        return res

    if p.suffix.lower() in (".ldb", ".sst"):
        base["format"] = "unknown"
        base["caveats"].append(
            "LevelDB table file (.ldb/.sst): data blocks are normally snappy-compressed "
            "and this reader ships no snappy decoder, so the file was NOT parsed. Its "
            "contents are unknown, not empty. The .log write-ahead file in the same "
            "directory is the recoverable target."
        )
        return base

    log = _read_leveldb_log_verbose(p, max_records=max_records)
    if log["error"]:
        base["caveats"].append(f"read failed: {log['error']}")
        return base
    if not log["records"] and log["blocks"] and log["bad_headers"] >= log["blocks"]:
        base["caveats"].append(
            "No valid LevelDB record framing found; the file is not a LevelDB "
            "write-ahead log or is damaged beyond resynchronisation."
        )
        return base

    records: list[dict[str, Any]] = []
    for rec in log["records"]:
        for entry in parse_write_batch(rec):
            records.append(
                _build_record(
                    entry["key"],
                    entry["value"],
                    entry_type=entry["type"],
                    seq=entry["seq"],
                    from_log=True,
                )
            )
            if len(records) >= max_records:
                break
        if len(records) >= max_records:
            log["truncated"] = True
            break

    base["format"] = "leveldb-log"
    base["records"] = records
    base["truncated"] = bool(log["truncated"])
    base["framing"] = {
        "blocks": log["blocks"],
        "user_records": len(log["records"]),
        "bad_headers": log["bad_headers"],
        "dropped_fragments": log["dropped_fragments"],
        "crc_checked": log["crc_checked"],
        "crc_mismatch": log["crc_mismatch"],
        "crc_verified": log["crc_verified"],
    }
    if log["truncated"]:
        base["caveats"].append(
            f"record cap of {max_records} reached: the record list is TRUNCATED and all "
            "counts derived from it are a lower bound"
        )
    if log["bad_headers"]:
        base["caveats"].append(
            f"{log['bad_headers']} block(s) contained a corrupt or truncated record "
            "header; the reader resynchronised at the next 32 KiB block boundary and "
            "those bytes were not parsed"
        )
    if log["dropped_fragments"]:
        base["caveats"].append(
            f"{log['dropped_fragments']} incomplete FIRST/MIDDLE/LAST fragment run(s) "
            "were discarded (record spans a damaged or truncated region)"
        )
    if not log["crc_verified"]:
        base["caveats"].append(
            "CRC32C verification skipped: file exceeds the "
            f"{MAX_CRC_VERIFY_BYTES}-byte verification budget. Record integrity is "
            "unverified."
        )
    elif log["crc_mismatch"]:
        base["caveats"].append(
            f"{log['crc_mismatch']} of {log['crc_checked']} records failed CRC32C. They "
            "were RETAINED, not dropped — a bad CRC in a write-ahead-log tail is the "
            "normal signature of residual/partially-overwritten data — but their "
            "contents may be corrupt."
        )
    return base


def parse_manifest(path: PathLike) -> dict[str, Any]:
    """Pull the cheap triage indicators out of a LevelDB MANIFEST.

    ``last_sequence`` high with files still at level 0 means heavy churn and
    little compaction, i.e. a high probability that deleted records are still
    recoverable from the log. VersionEdit tags: 1 comparator, 2 log number,
    3 next file, 4 last sequence, 9 prev log number.
    """
    out: dict[str, Any] = {
        "path": str(path),
        "comparator": "",
        "log_number": None,
        "prev_log_number": None,
        "next_file_number": None,
        "last_sequence": None,
        "level0_files": 0,
        "caveats": [],
    }
    for rec in read_leveldb_log(path, max_records=4096):
        pos, n = 0, len(rec)
        while pos < n:
            tag, pos = _read_varint(rec, pos)
            if tag is None:
                break
            if tag == 1:  # comparator name
                ln, pos = _read_varint(rec, pos)
                if ln is None or pos + ln > n:
                    break
                out["comparator"] = rec[pos : pos + ln].decode("utf-8", errors="replace")
                pos += ln
            elif tag in (2, 3, 4, 9):
                v, pos = _read_varint(rec, pos)
                if v is None:
                    break
                if tag == 2:
                    out["log_number"] = v
                elif tag == 3:
                    out["next_file_number"] = v
                elif tag == 4:
                    out["last_sequence"] = v
                else:
                    out["prev_log_number"] = v
            elif tag == 7:  # NewFile: level, number, size, smallest, largest
                level, pos = _read_varint(rec, pos)
                if level is None:
                    break
                if level == 0:
                    out["level0_files"] += 1
                for _ in range(2):  # file number, file size
                    _v, pos = _read_varint(rec, pos)
                    if _v is None:
                        break
                for _ in range(2):  # smallest / largest internal keys
                    ln, pos = _read_varint(rec, pos)
                    if ln is None or pos + ln > n:
                        pos = n
                        break
                    pos += ln
            else:
                # Unknown/compound tag: stop parsing this edit rather than guess.
                break
    if out["last_sequence"] is not None and out["level0_files"]:
        out["caveats"].append(
            f"last_sequence={out['last_sequence']} with {out['level0_files']} file(s) "
            "still at level 0: high write churn with little compaction, so superseded "
            "and deleted records are more likely to still be present in the log."
        )
    return out


def parse_fcm_dir(root: PathLike, *, max_records: int = 20000) -> dict[str, Any]:
    """Walk a staged tree (or a single ``fcm_queued_messages.ldb`` directory) and
    parse every FCM/GCM store found beneath it."""
    base = Path(root)
    out: dict[str, Any] = {
        "root": str(base),
        "stores": [],
        "records": [],
        "format": "unknown",
        "truncated": False,
        "manifests": [],
        "unparsed_tables": [],
        "caveats": [_QUEUE_CAVEAT],
    }
    if not base.exists():
        out["caveats"].append(
            "staged path does not exist; nothing was examined. This is not evidence "
            "that the device has no FCM queue."
        )
        return out

    candidates: list[Path] = []
    try:
        if base.is_dir():
            for child in sorted(base.rglob("*")):
                try:
                    if not child.is_file():
                        continue
                except OSError:
                    continue
                name = child.name
                if name.endswith(".log") or name.startswith("MANIFEST-"):
                    candidates.append(child)
                elif name.endswith(".ldb") or name.endswith(".sst"):
                    out["unparsed_tables"].append(str(child))
                elif name in ("gcm_store", "gcm_rmq2", "app_gcm_store") or (
                    name.endswith(".db") and "gcm" in name.lower()
                ):
                    candidates.append(child)
        else:
            candidates.append(base)
    except OSError as exc:
        out["caveats"].append(f"directory walk failed: {exc}")
        return out

    formats: set[str] = set()
    remaining = max_records
    for cand in candidates:
        if cand.name.startswith("MANIFEST-"):
            out["manifests"].append(parse_manifest(cand))
            continue
        res = parse_fcm_store(cand, max_records=max(remaining, 0))
        out["stores"].append(
            {
                "path": str(cand),
                "format": res["format"],
                "records": len(res["records"]),
                "truncated": res["truncated"],
                "caveats": res["caveats"],
            }
        )
        formats.add(res["format"])
        out["records"].extend(res["records"])
        out["truncated"] = out["truncated"] or bool(res["truncated"])
        remaining = max_records - len(out["records"])
        if remaining <= 0:
            out["truncated"] = True
            out["caveats"].append(
                f"aggregate record cap of {max_records} reached across stores; the "
                "record list is TRUNCATED"
            )
            break

    if len(formats - {"unknown"}) == 1:
        out["format"] = (formats - {"unknown"}).pop()
    elif formats:
        out["format"] = "unknown" if formats == {"unknown"} else "mixed"

    if out["unparsed_tables"]:
        out["caveats"].append(
            f"{len(out['unparsed_tables'])} LevelDB table file(s) (.ldb/.sst) were found "
            "but NOT parsed (snappy-compressed; no decoder shipped). Their contents are "
            "unknown, not empty — records deleted long enough ago to have been compacted "
            "into them are outside this tool's reach."
        )
    return out


def fcm_summary(result: dict[str, Any]) -> dict[str, Any]:
    """Roll up a :func:`parse_fcm_store` / :func:`parse_fcm_dir` result."""
    records = list(result.get("records", []) or [])
    by_app: dict[str, dict[str, Any]] = {}
    timestamps = [r["timestamp"] for r in records if r.get("timestamp")]

    for r in records:
        app = r.get("app") or "(package not decoded)"
        entry = by_app.setdefault(
            app,
            {
                "count": 0,
                "content_readable": 0,
                "metadata_only": 0,
                "tombstones": 0,
                "first_seen": None,
                "last_seen": None,
                "wakeup_only_app": app in WAKEUP_ONLY_PACKAGES,
            },
        )
        entry["count"] += 1
        if r.get("content_readable"):
            entry["content_readable"] += 1
        else:
            entry["metadata_only"] += 1
        if r.get("entry_type") == "deleted":
            entry["tombstones"] += 1
        ts = r.get("timestamp")
        if ts:
            if entry["first_seen"] is None or ts < entry["first_seen"]:
                entry["first_seen"] = ts
            if entry["last_seen"] is None or ts > entry["last_seen"]:
                entry["last_seen"] = ts

    readable = sum(1 for r in records if r.get("content_readable"))
    caveats = list(result.get("caveats", []) or [])
    caveats.append(
        "Counts describe PUSH DELIVERY EVENTS, not messages. One message can produce "
        "several pushes and one push can cover several messages."
    )
    caveats.append(_PAYLOAD_CAVEAT)
    if result.get("truncated"):
        caveats.append(
            "Result was truncated by a record cap: every count below is a LOWER BOUND."
        )

    return {
        "format": result.get("format", "unknown"),
        "total_records": len(records),
        "content_readable_records": readable,
        "metadata_only_records": len(records) - readable,
        "tombstones": sum(1 for r in records if r.get("entry_type") == "deleted"),
        "apps": sorted(by_app.keys()),
        "by_app": by_app,
        "first_seen": min(timestamps) if timestamps else None,
        "last_seen": max(timestamps) if timestamps else None,
        "unparsed_tables": len(result.get("unparsed_tables", []) or []),
        "truncated": bool(result.get("truncated")),
        "caveats": caveats,
    }
