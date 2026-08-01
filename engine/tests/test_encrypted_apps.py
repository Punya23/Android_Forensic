"""Tests for P3-3: encrypted-messenger presence reporting + FCM queued-push mining.

Every fixture is built programmatically (sqlite3 / raw bytes) so the suite has no
binary-fixture dependency. The assertions are deliberately weighted towards the
project's honesty model: presence must never be downgraded to absence, inference
must never be dressed up as proof, and nothing must ever be presented as
decrypted content.
"""

from __future__ import annotations

import json
import os
import sqlite3
import struct
from pathlib import Path

import pytest

from triage.parsers import fcm
from triage.parsers.encrypted_apps import (
    ENCRYPTED_APP_TARGETS,
    SQLITE_MAGIC,
    EncryptedAppArtifact,
    detect_encryption,
    encrypted_apps_summary,
    scan_encrypted_apps,
    signal_metadata,
)

SIGNAL_PKG = "org.thoughtcrime.securesms"
SIGNAL_DB = f"/data/data/{SIGNAL_PKG}/databases/signal.db"


# --- fixture builders --------------------------------------------------------
def _make_sqlite(path: Path, rows: int = 3) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, body TEXT)")
    con.executemany(
        "INSERT INTO messages (body) VALUES (?)", [(f"row {i}",) for i in range(rows)]
    )
    con.commit()
    con.close()
    return path


def _make_sqlcipher_like(path: Path, pages: int = 4) -> Path:
    """Random 16-byte salt followed by ciphertext, total size a 4096 multiple —
    exactly what a real SQLCipher v4 file looks like on disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(os.urandom(4096 * pages))
    return path


def _stage_signal(root: Path, *, attachments: int = 4) -> Path:
    app = root / "data" / "data" / SIGNAL_PKG
    _make_sqlcipher_like(app / "databases" / "signal.db", pages=6)
    _make_sqlcipher_like(app / "databases" / "signal-jobmanager.db", pages=2)
    parts = app / "app_parts"
    parts.mkdir(parents=True, exist_ok=True)
    for i in range(attachments):
        (parts / f"part{1000000000000000000 + i}.mms").write_bytes(os.urandom(512))
    prefs = app / "shared_prefs"
    prefs.mkdir(parents=True, exist_ok=True)
    (prefs / f"{SIGNAL_PKG}_preferences.xml").write_text(
        "<?xml version='1.0' encoding='utf-8' standalone='yes' ?>\n"
        "<map>\n"
        '  <string name="pref_database_encrypted_secret">{"iv":"AA==","data":"BB=="}</string>\n'
        '  <string name="pref_attachment_encrypted_secret">{"iv":"CC==","data":"DD=="}</string>\n'
        '  <string name="pref_local_number">+15550100</string>\n'
        "</map>\n",
        encoding="utf-8",
    )
    avatars = app / "files" / "avatars"
    avatars.mkdir(parents=True, exist_ok=True)
    (avatars / "avatar1.jpg").write_bytes(b"\xff\xd8\xff\xe0" + os.urandom(256))
    backups = root / "sdcard" / "Signal" / "Backups"
    backups.mkdir(parents=True, exist_ok=True)
    (backups / "signal-2026-07-30.backup").write_bytes(os.urandom(2048))
    return root


# --- LevelDB fixture builders ------------------------------------------------
def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _log_record(rtype: int, data: bytes, *, bad_crc: bool = False) -> bytes:
    crc = 0xDEADBEEF if bad_crc else fcm.mask_crc(fcm.crc32c(bytes([rtype]) + data))
    return struct.pack("<IHB", crc, len(data), rtype) + data


def _blocks(*block_records: list[bytes]) -> bytes:
    """Assemble 32 KiB blocks, zero-padding each to the full block size."""
    out = bytearray()
    for recs in block_records:
        block = b"".join(recs)
        assert len(block) <= fcm.LEVELDB_BLOCK_SIZE, "test fixture overflows a block"
        out += block + b"\x00" * (fcm.LEVELDB_BLOCK_SIZE - len(block))
    return bytes(out)


def _batch(entries: list[tuple[bytes, bytes | None]], seq: int = 500) -> bytes:
    out = bytearray(struct.pack("<QI", seq, len(entries)))
    for key, value in entries:
        if value is None:  # tombstone: no value field at all
            out += b"\x00" + _varint(len(key)) + key
        else:
            out += b"\x01" + _varint(len(key)) + key + _varint(len(value)) + value
    return bytes(out)


def _pb_tag(field: int, wire: int) -> bytes:
    return _varint((field << 3) | wire)


def _pb_str(field: int, text: str) -> bytes:
    raw = text.encode("utf-8")
    return _pb_tag(field, 2) + _varint(len(raw)) + raw


def _pb_msg(field: int, msg: bytes) -> bytes:
    return _pb_tag(field, 2) + _varint(len(msg)) + msg


def _fcm_value(package: str, data: dict[str, str]) -> bytes:
    inner = _pb_str(5, package)
    for k, v in data.items():
        inner += _pb_msg(7, _pb_str(1, k) + _pb_str(2, v))
    return _pb_tag(1, 0) + _varint(1) + _pb_msg(2, inner) + _pb_tag(3, 0) + _varint(7)


def _fcm_key(micros: int, prefix: str = "fcm", suffix: str = "a1b2c3") -> bytes:
    return f"{prefix}:{micros}%{suffix}".encode("utf-8")


# =============================================================================
# detect_encryption
# =============================================================================
def test_detect_plain_sqlite(tmp_path: Path) -> None:
    db = _make_sqlite(tmp_path / "cache4.db")
    det = detect_encryption(db)
    assert det["format"] == "plain-sqlite"
    assert det["encrypted"] is False
    # Magic bytes are a proof, so this is the one case allowed 'live' confidence.
    assert det["confidence"] == "live"
    assert det["page_size"] in (512, 1024, 2048, 4096, 8192, 16384, 32768, 65536)
    assert db.read_bytes()[:16] == SQLITE_MAGIC


def test_detect_sqlcipher_inference_is_labelled_as_inference(tmp_path: Path) -> None:
    f = _make_sqlcipher_like(tmp_path / "signal.db", pages=8)
    det = detect_encryption(f)
    assert det["format"] == "sqlcipher"
    assert det["encrypted"] is True
    assert len(det["salt_hex"]) == 32  # 16-byte candidate PBKDF2 salt
    # It is an inference from entropy/alignment, NOT a proof — never 'live'.
    assert det["confidence"] != "live"
    assert "INFERENCE" in det["evidence"].upper()
    # KDF parameters are unknowable from ciphertext and must not be claimed.
    assert "256000" not in det["evidence"]


def test_detect_sqlcipher_plaintext_header(tmp_path: Path) -> None:
    """SQLite magic + reserved-per-page 80 = SQLCipher v4 with a plaintext header.
    Naively trusting the magic here is the worst false negative in detection."""
    f = tmp_path / "threema4.db"
    header = bytearray(SQLITE_MAGIC + b"\x10\x00" + b"\x02\x02\x50" + os.urandom(43))
    assert header[20] == 0x50  # reserved bytes per page = 80
    f.write_bytes(bytes(header) + os.urandom(4096 * 2 - len(header)))
    det = detect_encryption(f)
    assert det["format"] == "sqlcipher"
    assert det["encrypted"] is True
    assert det["reserved_bytes"] == 80
    assert det["confidence"] != "live"


def test_detect_empty_file(tmp_path: Path) -> None:
    f = tmp_path / "empty.db"
    f.write_bytes(b"")
    det = detect_encryption(f)
    assert det["format"] == "empty"
    assert det["encrypted"] is False
    assert det["size_bytes"] == 0


def test_detect_missing_file_does_not_claim_absence(tmp_path: Path) -> None:
    det = detect_encryption(tmp_path / "nope" / "signal.db")
    assert det["format"] == "missing"
    assert "not evidence" in det["evidence"].lower() or "not proof" in det["evidence"].lower()


def test_detect_unknown_binary_reports_undetermined(tmp_path: Path) -> None:
    f = tmp_path / "notes.txt"
    f.write_text("plain text, not a database at all, nothing to see here.\n")
    det = detect_encryption(f)
    assert det["format"] == "unknown-binary"
    assert "UNDETERMINED" in det["evidence"].upper()


# =============================================================================
# scan_encrypted_apps
# =============================================================================
def test_scan_reports_signal_present_and_encrypted(tmp_path: Path) -> None:
    _stage_signal(tmp_path, attachments=5)
    items = scan_encrypted_apps(tmp_path)
    signal = [a for a in items if a.path == SIGNAL_DB]
    assert len(signal) == 1
    art = signal[0]
    assert art.exists is True
    assert art.size_bytes == 4096 * 6
    assert art.encryption_format == "sqlcipher"
    assert art.recoverable is False
    assert art.status == "present, encrypted (SQLCipher/Keystore), content not recoverable"
    assert art.modified and art.modified.endswith("Z")
    assert len(art.sha256) == 64


def test_scan_attaches_exact_attachment_counts(tmp_path: Path) -> None:
    _stage_signal(tmp_path, attachments=7)
    items = scan_encrypted_apps(tmp_path)
    totals = sum(a.attachment_count for a in items if a.package == SIGNAL_PKG)
    byte_totals = sum(a.attachment_bytes for a in items if a.package == SIGNAL_PKG)
    assert totals == 7  # counts are exact even though contents are opaque
    assert byte_totals == 7 * 512


def test_scan_caveats_name_the_keystore_reason(tmp_path: Path) -> None:
    _stage_signal(tmp_path)
    art = [a for a in scan_encrypted_apps(tmp_path) if a.path == SIGNAL_DB][0]
    blob = " ".join(art.caveats).lower()
    assert "keystore" in blob
    assert "hardware-bound" in blob or "non-exportable" in blob
    assert "does not attempt" in blob  # no decryption is attempted or simulated


def test_every_sqlcipher_target_is_unrecoverable(tmp_path: Path) -> None:
    _stage_signal(tmp_path)
    _make_sqlcipher_like(
        tmp_path / "data" / "data" / "ch.threema.app" / "databases" / "threema4.db"
    )
    _make_sqlcipher_like(
        tmp_path / "data" / "data" / "network.loki.messenger" / "databases" / "signal.db"
    )
    for art in scan_encrypted_apps(tmp_path):
        if ENCRYPTED_APP_TARGETS[art.package]["encryption"] == "SQLCipher":
            assert art.recoverable is False, art.path
            assert "not recoverable" in art.status


def test_scan_omits_paths_that_were_never_staged(tmp_path: Path) -> None:
    """Nothing may be emitted for a file we do not have — an omission is honest,
    a fabricated 'absent' record is not."""
    _stage_signal(tmp_path)
    items = scan_encrypted_apps(tmp_path)
    packages = {a.package for a in items}
    assert packages == {SIGNAL_PKG}
    assert all(a.exists for a in items)


def test_telegram_cache4_is_classified_as_plain_not_sqlcipher(tmp_path: Path) -> None:
    _make_sqlite(
        tmp_path / "data" / "data" / "org.telegram.messenger" / "files" / "cache4.db"
    )
    items = scan_encrypted_apps(tmp_path)
    tg = [a for a in items if a.package == "org.telegram.messenger"]
    assert len(tg) == 1
    assert tg[0].encryption_format == "plain-sqlite"
    assert tg[0].recoverable is True
    assert "not encrypted" in tg[0].status


# =============================================================================
# encrypted_apps_summary
# =============================================================================
def test_summary_separates_not_present_from_not_acquired(tmp_path: Path) -> None:
    _stage_signal(tmp_path)
    items = scan_encrypted_apps(tmp_path)
    # We pulled Threema's DB path (and it was not there); we never even tried Session.
    summary = encrypted_apps_summary(
        items,
        paths_attempted=[SIGNAL_DB, "/data/data/ch.threema.app/databases/threema4.db"],
    )
    absent = {r["path"] for r in summary["not_present_on_device"]}
    unknown = {r["path"] for r in summary["not_acquired"]}
    assert "/data/data/ch.threema.app/databases/threema4.db" in absent
    assert "/data/data/network.loki.messenger/databases/signal.db" in unknown
    assert not absent & unknown
    assert summary["manifest_supplied"] is True
    assert all("observation" in r["basis"] for r in summary["not_present_on_device"])
    assert all("UNKNOWN" in r["basis"] for r in summary["not_acquired"])


def test_summary_without_manifest_claims_no_absence(tmp_path: Path) -> None:
    _stage_signal(tmp_path)
    summary = encrypted_apps_summary(scan_encrypted_apps(tmp_path))
    assert summary["manifest_supplied"] is False
    assert summary["not_present_on_device"] == []
    assert summary["not_acquired"]
    assert "cannot be distinguished" in " ".join(summary["caveats"])


def test_summary_rolls_up_counts(tmp_path: Path) -> None:
    _stage_signal(tmp_path, attachments=3)
    items = scan_encrypted_apps(tmp_path)
    summary = encrypted_apps_summary(items, paths_attempted=[SIGNAL_DB])
    assert summary["total_files"] == 2  # signal.db + signal-jobmanager.db
    assert summary["encrypted_files"] == 2
    assert summary["recoverable_files"] == 0
    assert summary["attachment_count"] == 3
    assert summary["by_app"]["Signal"]["package"] == SIGNAL_PKG


# =============================================================================
# signal_metadata
# =============================================================================
def test_signal_metadata_collects_readable_context(tmp_path: Path) -> None:
    _stage_signal(tmp_path, attachments=6)
    meta = signal_metadata(tmp_path)
    names = {d["name"] for d in meta["databases"]}
    assert "signal.db" in names and "signal-jobmanager.db" in names
    assert meta["attachments"]["count"] == 6
    assert meta["recoverable"] is False
    assert len(meta["backups"]) == 1
    assert meta["backups"][0]["recoverable"] is False
    assert "pref_database_encrypted_secret" in meta["prefs"]["keys_present"]
    # The sealed secret itself must never be echoed into the report.
    assert "pref_database_encrypted_secret" not in meta["prefs"]["values"]
    assert meta["avatars"] and meta["avatars"][0]["kind"] == "jpeg"


def test_signal_metadata_on_empty_tree_says_absence_is_unproven(tmp_path: Path) -> None:
    meta = signal_metadata(tmp_path)
    assert meta["databases"] == []
    assert any("not proof" in c.lower() for c in meta["caveats"])


# =============================================================================
# JSON round-trip
# =============================================================================
def test_json_round_trip(tmp_path: Path) -> None:
    _stage_signal(tmp_path)
    items = scan_encrypted_apps(tmp_path)
    payload = {
        "artifacts": [a.to_dict() for a in items],
        "summary": encrypted_apps_summary(items, paths_attempted=[SIGNAL_DB]),
        "signal": signal_metadata(tmp_path),
    }
    restored = json.loads(json.dumps(payload))
    assert restored["artifacts"][0]["recoverable"] is False
    assert isinstance(restored["artifacts"][0]["caveats"], list)
    assert isinstance(EncryptedAppArtifact(app="x", package="y", path="z").to_dict(), dict)


# =============================================================================
# LevelDB log reader
# =============================================================================
def test_read_leveldb_log_full_records(tmp_path: Path) -> None:
    log = tmp_path / "000003.log"
    log.write_bytes(
        _blocks([_log_record(fcm.REC_FULL, b"alpha"), _log_record(fcm.REC_FULL, b"beta")])
    )
    assert fcm.read_leveldb_log(log) == [b"alpha", b"beta"]


def test_read_leveldb_log_reassembles_fragments_across_blocks(tmp_path: Path) -> None:
    log = tmp_path / "000004.log"
    head = b"H" * 100
    mid = b"M" * 200
    tail = b"T" * 50
    log.write_bytes(
        _blocks(
            [_log_record(fcm.REC_FULL, b"first-whole"), _log_record(fcm.REC_FIRST, head)],
            [_log_record(fcm.REC_MIDDLE, mid)],
            [_log_record(fcm.REC_LAST, tail), _log_record(fcm.REC_FULL, b"last-whole")],
        )
    )
    assert fcm.read_leveldb_log(log) == [b"first-whole", head + mid + tail, b"last-whole"]


def test_corrupt_record_header_is_skipped_without_raising(tmp_path: Path) -> None:
    log = tmp_path / "000005.log"
    good = _log_record(fcm.REC_FULL, b"survivor")
    # Length claims 60000 bytes, far past the end of the block.
    corrupt = struct.pack("<IHB", 0x11223344, 60000, fcm.REC_FULL) + b"junk"
    log.write_bytes(_blocks([good, corrupt], [_log_record(fcm.REC_FULL, b"next-block")]))
    records = fcm.read_leveldb_log(log)
    assert records == [b"survivor", b"next-block"]  # resynchronised at the next block
    result = fcm.parse_fcm_store(log)
    assert any("corrupt or truncated record header" in c for c in result["caveats"])


def test_bad_crc_records_are_retained_and_reported(tmp_path: Path) -> None:
    log = tmp_path / "000006.log"
    batch = _batch([(_fcm_key(1_753_900_000_000_000), _fcm_value("com.tumblr", {"body": "hello there"}))])
    log.write_bytes(_blocks([_log_record(fcm.REC_FULL, batch, bad_crc=True)]))
    result = fcm.parse_fcm_store(log)
    assert len(result["records"]) == 1  # never dropped: WAL tails are where deletions live
    assert result["framing"]["crc_mismatch"] == 1
    assert any("failed CRC32C" in c for c in result["caveats"])


def test_max_records_cap_is_reported_not_silent(tmp_path: Path) -> None:
    log = tmp_path / "000007.log"
    recs = [_log_record(fcm.REC_FULL, _batch([(b"k%d" % i, b"v")])) for i in range(6)]
    log.write_bytes(_blocks(recs))
    assert len(fcm.read_leveldb_log(log, max_records=2)) == 2
    result = fcm.parse_fcm_store(log, max_records=2)
    assert result["truncated"] is True
    assert any("record cap of 2" in c for c in result["caveats"])
    summary = fcm.fcm_summary(result)
    assert summary["truncated"] is True
    assert any("LOWER BOUND" in c for c in summary["caveats"])


# =============================================================================
# extract_strings
# =============================================================================
def test_extract_strings_on_mixed_binary() -> None:
    blob = b"\x00\x01\x02com.instagram.android\xff\xfe\x00new message\x00\x03ab" + "héllo wörld".encode(
        "utf-8"
    )
    out = fcm.extract_strings(blob, min_len=5)
    assert "com.instagram.android" in out
    assert "new message" in out
    assert "ab" not in out  # below min_len
    assert any("héllo" in s for s in out)  # multi-byte UTF-8 pass
    assert fcm.extract_strings(b"", min_len=4) == []


# =============================================================================
# FCM record semantics
# =============================================================================
def test_parse_fcm_store_extracts_metadata_and_timestamp(tmp_path: Path) -> None:
    log = tmp_path / "000008.log"
    micros = 1_753_900_991_812_345
    value = _fcm_value(
        "com.tumblr",
        {
            "from": "103953800507",
            "google.message_id": "0:1753900991%a1b2c3",
            "collapse_key": "do_not_collapse",
            "body": "someone liked your post",
        },
    )
    log.write_bytes(_blocks([_log_record(fcm.REC_FULL, _batch([(_fcm_key(micros), value)]))]))
    result = fcm.parse_fcm_store(log)
    assert result["format"] == "leveldb-log"
    rec = result["records"][0]
    assert rec["app"] == "com.tumblr"
    assert rec["sender"] == "103953800507"
    assert rec["message_id"] == "0:1753900991%a1b2c3"
    assert rec["collapse_key"] == "do_not_collapse"
    assert rec["timestamp"] == "2025-07-30T18:43:11.812345Z"
    assert rec["content_readable"] is True
    assert "someone liked your post" in rec["raw_preview"]


def test_e2e_messenger_push_is_never_reported_as_content(tmp_path: Path) -> None:
    """Signal sends a content-free wakeup push. Even if printable bytes are
    present, the record must not be flagged as readable content."""
    log = tmp_path / "000009.log"
    value = _fcm_value(
        "org.thoughtcrime.securesms",
        {"notification": "AAAAAAAAAAAAAAAAAAAAAAAAAAAA", "from": "12345678901"},
    )
    log.write_bytes(
        _blocks([_log_record(fcm.REC_FULL, _batch([(_fcm_key(1_753_900_000_000_000), value)]))])
    )
    rec = fcm.parse_fcm_store(log)["records"][0]
    assert rec["app"] == "org.thoughtcrime.securesms"
    assert rec["content_readable"] is False
    assert rec["timestamp"]  # arrival time IS evidence
    blob = " ".join(rec["caveats"]).lower()
    assert "wakeup" in blob
    assert "arrival time" in blob and "existence" in blob


def test_every_record_carries_the_not_a_decrypted_message_caveat(tmp_path: Path) -> None:
    log = tmp_path / "000010.log"
    entries = [
        (_fcm_key(1_753_900_000_000_001), _fcm_value("com.twitter.android", {"text": "hi there friend"})),
        (_fcm_key(1_753_900_000_000_002), None),  # tombstone
    ]
    log.write_bytes(_blocks([_log_record(fcm.REC_FULL, _batch(entries))]))
    result = fcm.parse_fcm_store(log)
    assert len(result["records"]) == 2
    for rec in result["records"]:
        joined = " ".join(rec["caveats"])
        assert "NOT a decrypted message" in joined
        assert "only routing metadata" in joined and "is readable" in joined
    tomb = [r for r in result["records"] if r["entry_type"] == "deleted"][0]
    assert tomb["timestamp"]  # key + arrival time survive the tombstone
    assert any("tombstone" in c.lower() for c in tomb["caveats"])


def test_log_keys_are_not_truncated_by_the_eight_byte_trailer_bug(tmp_path: Path) -> None:
    key = _fcm_key(1_753_900_000_000_003, suffix="deadbeefcafe")
    log = tmp_path / "000011.log"
    log.write_bytes(
        _blocks([_log_record(fcm.REC_FULL, _batch([(key, _fcm_value("com.tumblr", {}))]))])
    )
    rec = fcm.parse_fcm_store(log)["records"][0]
    assert rec["key"] == key.decode()
    assert fcm.user_key(key, from_log=True) == key
    assert fcm.user_key(key + b"\x01\x00\x00\x00\x00\x00\x00\x00", from_log=False) == key


# =============================================================================
# legacy SQLite GCM store
# =============================================================================
def test_parse_legacy_sqlite_gcm_store(tmp_path: Path) -> None:
    db = tmp_path / "gcm_store"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE messages (persistent_id TEXT, sender_id TEXT, app_package TEXT, "
        "collapse_key TEXT, timestamp INTEGER, payload BLOB)"
    )
    con.execute(
        "INSERT INTO messages VALUES (?,?,?,?,?,?)",
        (
            "0:1753900991%abc",
            "103953800507",
            "com.tumblr",
            "do_not_collapse",
            1_753_900_991_000,
            b"\x00\x01someone liked your post\x00",
        ),
    )
    con.execute(
        "INSERT INTO messages VALUES (?,?,?,?,?,?)",
        ("0:1753900992%abc", "999", "org.thoughtcrime.securesms", "", 1_753_900_992_000, b"\x00"),
    )
    con.commit()
    con.close()

    result = fcm.parse_fcm_store(db)
    assert result["format"] == "sqlite"
    assert {t["name"] for t in result["tables"]} == {"messages"}  # schema discovered
    tumblr = [r for r in result["records"] if r["app"] == "com.tumblr"][0]
    assert tumblr["sender"] == "103953800507"
    assert tumblr["message_id"] == "0:1753900991%abc"
    assert tumblr["timestamp"] and tumblr["timestamp"].endswith("Z")
    assert "someone liked your post" in tumblr["raw_preview"]
    signal = [r for r in result["records"] if r["app"] == "org.thoughtcrime.securesms"][0]
    assert signal["content_readable"] is False
    assert any("provisional" in c.lower() for c in result["caveats"])


# =============================================================================
# directory walk + summary
# =============================================================================
def test_parse_fcm_dir_and_summary(tmp_path: Path) -> None:
    store = (
        tmp_path
        / "data"
        / "data"
        / "com.google.android.gms"
        / "files"
        / "fcm_queued_messages.ldb"
    )
    store.mkdir(parents=True)
    (store / "CURRENT").write_text("MANIFEST-000002\n")
    (store / "000005.ldb").write_bytes(os.urandom(1024))  # snappy: must NOT be guessed at
    entries = [
        (_fcm_key(1_753_900_000_000_010), _fcm_value("com.tumblr", {"body": "readable text here"})),
        (
            _fcm_key(1_753_900_000_000_020),
            _fcm_value("org.thoughtcrime.securesms", {"notification": "opaque"}),
        ),
    ]
    (store / "000003.log").write_bytes(_blocks([_log_record(fcm.REC_FULL, _batch(entries))]))

    result = fcm.parse_fcm_dir(tmp_path)
    assert result["format"] == "leveldb-log"
    assert len(result["records"]) == 2
    assert len(result["unparsed_tables"]) == 1
    assert any("NOT parsed" in c for c in result["caveats"])

    summary = fcm.fcm_summary(result)
    assert summary["total_records"] == 2
    assert summary["content_readable_records"] == 1
    assert summary["metadata_only_records"] == 1
    assert summary["by_app"]["org.thoughtcrime.securesms"]["wakeup_only_app"] is True
    assert summary["by_app"]["com.tumblr"]["wakeup_only_app"] is False
    assert summary["first_seen"] < summary["last_seen"]
    assert any("PUSH DELIVERY EVENTS" in c for c in summary["caveats"])


def test_parse_fcm_store_on_missing_and_garbage_never_raises(tmp_path: Path) -> None:
    missing = fcm.parse_fcm_store(tmp_path / "nope" / "000001.log")
    assert missing["format"] == "unknown"
    assert missing["records"] == []
    assert any("not evidence" in c.lower() for c in missing["caveats"])

    garbage = tmp_path / "garbage.log"
    garbage.write_bytes(b"\xff" * 5000)
    res = fcm.parse_fcm_store(garbage)
    assert res["records"] == []
    assert res["format"] in ("unknown", "leveldb-log")

    table = tmp_path / "000009.ldb"
    table.write_bytes(os.urandom(4096))
    res2 = fcm.parse_fcm_store(table)
    assert res2["format"] == "unknown"
    assert any("snappy" in c for c in res2["caveats"])


def test_fcm_paths_and_targets_are_declared() -> None:
    assert any("fcm_queued_messages.ldb" in p for p in fcm.FCM_PATHS)
    assert any("gcm_store" in p for p in fcm.FCM_PATHS)
    for pkg in (
        SIGNAL_PKG,
        "im.molly.app",
        "ch.threema.app",
        "network.loki.messenger",
        "com.mywickr.wickr2",
        "org.telegram.messenger",
    ):
        spec = ENCRYPTED_APP_TARGETS[pkg]
        assert spec["db_paths"] and isinstance(spec["verified"], bool)
        assert spec["encryption"] in ("SQLCipher", "custom", "none")
    # Telegram must not be mislabelled as an encrypted store.
    assert ENCRYPTED_APP_TARGETS["org.telegram.messenger"]["encryption"] == "none"


def test_manifest_last_sequence_is_a_triage_indicator(tmp_path: Path) -> None:
    # VersionEdit: tag 4 (LastSequence) = 4242, tag 7 (NewFile) at level 0.
    edit = _varint(4) + _varint(4242)
    edit += (
        _varint(7)
        + _varint(0)
        + _varint(5)
        + _varint(1024)
        + _varint(3)
        + b"aaa"
        + _varint(3)
        + b"zzz"
    )
    manifest = tmp_path / "MANIFEST-000002"
    manifest.write_bytes(_blocks([_log_record(fcm.REC_FULL, edit)]))
    info = fcm.parse_manifest(manifest)
    assert info["last_sequence"] == 4242
    assert info["level0_files"] == 1
    assert any("level 0" in c for c in info["caveats"])


@pytest.mark.parametrize("value", [0, 1, 127, 128, 300, 16384, 2**31])
def test_varint_round_trip(value: int) -> None:
    encoded = _varint(value)
    decoded, pos = fcm._read_varint(encoded, 0)
    assert decoded == value and pos == len(encoded)
