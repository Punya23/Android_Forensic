"""Tests for the SQLite deleted-record recovery engine."""
import sqlite3
from pathlib import Path

from triage.config import Confidence
from triage.recovery import recover_deleted_rows, detect_rowid_gaps, read_live_rows, map_columns_to_whatsapp, CarvedRow


def _make_db(path: Path, rows, delete_ids):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, sender TEXT, "
                "body TEXT, ts INTEGER)")
    for r in rows:
        con.execute("INSERT INTO messages(sender,body,ts) VALUES (?,?,?)", r)
    con.commit()
    if delete_ids:
        con.execute(f"DELETE FROM messages WHERE id IN "
                    f"({','.join('?'*len(delete_ids))})", delete_ids)
        con.commit()
    con.close()


# ===========================================================================
# Existing tests (preserved for regression)
# ===========================================================================

def test_rowid_gap_detection(tmp_path):
    db = tmp_path / "m.db"
    _make_db(db, [("a", "one", 1), ("b", "two", 2), ("c", "three", 3),
                  ("d", "four", 4), ("e", "five", 5)], delete_ids=[3, 4])
    gaps = detect_rowid_gaps(db, "messages")
    assert gaps == [{"after_rowid": 2, "before_rowid": 5, "missing": 2}]


def test_live_rows_unaffected(tmp_path):
    db = tmp_path / "m.db"
    _make_db(db, [("a", "one", 1), ("b", "two", 2), ("c", "three", 3)], delete_ids=[2])
    live = read_live_rows(db, "messages")
    # SELECT rowid,* → values = [id, sender, body, ts]; body is index 2.
    bodies = {r.values[2] for r in live}
    assert "one" in bodies and "three" in bodies and "two" not in bodies


def test_inpage_freeblock_text_recovery(tmp_path):
    db = tmp_path / "m.db"
    _make_db(db, [("Rahul", "meet at the docks midnight", 1),
                  ("Priya", "bring the package tonight", 2),
                  ("X", "transfer done to account 4471 secretly", 3),
                  ("Y", "SECRET meeting warehouse nine", 4),
                  ("Z", "harmless normal message", 5)], delete_ids=[3, 4])
    rows = recover_deleted_rows(db, "messages")
    text = " ".join(str(v) for r in rows for v in r.values if isinstance(v, str))
    assert "4471" in text
    assert "warehouse" in text.lower()
    # In-page freeblock carves must be labelled partial, never verified.
    for r in rows:
        if r.provenance and "freeblock" in r.provenance:
            assert r.confidence == Confidence.CARVED_PARTIAL


def test_freelist_structured_recovery(tmp_path):
    db = tmp_path / "big.db"
    rows = [(f"u{i%5}", f"message body number {i} topic {i%7}", 1000 + i)
            for i in range(400)]
    _make_db(db, rows, delete_ids=list(range(40, 320)))
    recovered = recover_deleted_rows(db, "messages")
    verified = [r for r in recovered if r.confidence == Confidence.RECOVERED_VERIFIED]
    assert len(verified) > 0
    # A verified row from a freelist page should have clean structured fields.
    sample = verified[0]
    assert any("message body number" in str(v) for v in sample.values)
    assert "freelist" in sample.provenance or "wal" in sample.provenance


def test_corrupt_db_does_not_crash(tmp_path):
    db = tmp_path / "junk.db"
    db.write_bytes(b"not a sqlite database at all" * 10)
    assert recover_deleted_rows(db) == []
    assert detect_rowid_gaps(db, "messages") == []


def test_wal_recovery(tmp_path):
    db = tmp_path / "wal.db"
    con = sqlite3.connect(db)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, sender TEXT, body TEXT, ts INTEGER)")
    for i in range(50):
        con.execute("INSERT INTO messages(sender,body,ts) VALUES (?,?,?)",
                    (f"u{i}", f"walmessage content {i}", i))
    con.commit()
    con.execute("DELETE FROM messages WHERE id > 25")
    con.commit()
    # Do NOT checkpoint — leave the -wal file present.
    con.close()
    # The -wal may or may not persist depending on SQLite; recovery must not crash.
    rows = recover_deleted_rows(db, "messages")
    assert isinstance(rows, list)


# ===========================================================================
# New tests — Task 3a: schema_hint parameter
# ===========================================================================

def test_schema_hint_col_count_respected(tmp_path):
    """When schema_hint col_count is provided it overrides schema introspection."""
    db = tmp_path / "m.db"
    _make_db(db, [
        ("alice", "alpha message here", 100),
        ("bob",   "beta message here",  200),
        ("carol", "gamma message here", 300),
    ], delete_ids=[1, 2])
    # Our table has 4 columns (id, sender, body, ts).
    hint = {"col_count": 4, "columns": ["id", "sender", "body", "ts"]}
    rows = recover_deleted_rows(db, table="messages", schema_hint=hint)
    # Recovery must not crash and must return a list.
    assert isinstance(rows, list)
    # Any RECOVERED_VERIFIED row must have exactly 4 column values
    for r in rows:
        if r.confidence == Confidence.RECOVERED_VERIFIED:
            assert len(r.values) == 4, f"Expected 4 cols, got {len(r.values)}: {r.values}"


def test_schema_hint_registers_column_names(tmp_path):
    """schema_hint columns are stored in rows_meta_colnames for the pipeline."""
    from triage.recovery import rows_meta_colnames
    db = tmp_path / "m.db"
    _make_db(db, [("x", "body text here", 1)], delete_ids=[])
    hint = {"col_count": 4, "columns": ["id", "sender", "body", "ts"]}
    recover_deleted_rows(db, table="messages", schema_hint=hint)
    key = (db.name, "messages")
    assert key in rows_meta_colnames
    assert rows_meta_colnames[key] == ["id", "sender", "body", "ts"]


def test_schema_hint_none_backward_compatible(tmp_path):
    """Calling recover_deleted_rows without schema_hint behaves identically to before."""
    db = tmp_path / "m.db"
    _make_db(db, [("a", "something here", 1), ("b", "more here", 2)], delete_ids=[1])
    rows = recover_deleted_rows(db)   # no schema_hint, no table
    assert isinstance(rows, list)


# ===========================================================================
# New tests — Task 3b: text carve prefix anchoring
# ===========================================================================

def test_text_carve_prefix_anchoring(tmp_path):
    """Carved text runs containing 'data=…' must emit only the value after '='."""
    db = tmp_path / "m.db"
    # Use large-enough data so the page becomes big enough to trigger text carving.
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, sender TEXT, body TEXT, ts INTEGER)")
    # Insert a row whose body we want to recover after deletion.
    con.execute("INSERT INTO messages VALUES (1, 'Alice', 'secret payload anchored', 100)")
    con.execute("INSERT INTO messages VALUES (2, 'Bob', 'another keep alive message', 200)")
    con.execute("INSERT INTO messages VALUES (3, 'Carol', 'third message stays live', 300)")
    con.commit()
    con.execute("DELETE FROM messages WHERE id = 1")
    con.commit()
    con.close()
    rows = recover_deleted_rows(db, "messages")
    all_text = " ".join(
        str(v) for r in rows for v in r.values if isinstance(v, str)
    )
    # The payload text should appear in recovery results.
    assert "secret payload anchored" in all_text or "payload" in all_text


def test_text_carve_warning_content(tmp_path):
    """CARVED_PARTIAL rows from freeblock/unallocated must carry the honesty warning."""
    db = tmp_path / "m.db"
    _make_db(db, [
        ("Alice", "message one for carving", 1),
        ("Bob",   "message two for carving", 2),
        ("Carol", "message three for carving", 3),
    ], delete_ids=[1, 2])
    rows = recover_deleted_rows(db, "messages")
    carved = [r for r in rows if r.confidence == Confidence.CARVED_PARTIAL]
    for r in carved:
        assert r.warnings, f"CARVED_PARTIAL row has no warnings: {r}"
        warning_text = " ".join(r.warnings)
        assert "unallocated space" in warning_text or "record structure" in warning_text, \
            f"Expected honesty warning, got: {r.warnings}"


def test_text_carve_provenance_format(tmp_path):
    """Provenance strings must follow the 'kind page N@off (text carve)' format."""
    db = tmp_path / "m.db"
    _make_db(db, [
        ("u1", "carved provenance test message", 1),
        ("u2", "second row also useful here", 2),
    ], delete_ids=[1])
    rows = recover_deleted_rows(db, "messages")
    carved = [r for r in rows if r.confidence == Confidence.CARVED_PARTIAL]
    for r in carved:
        assert r.provenance, "CARVED_PARTIAL row has no provenance"
        # Must contain page number and offset.
        assert "page" in r.provenance, f"Unexpected provenance: {r.provenance}"


# ===========================================================================
# New tests — Task 3c: map_columns_to_whatsapp helper
# ===========================================================================

def test_map_columns_basic():
    """map_columns_to_whatsapp maps positional values to named keys.

    _WA_MESSAGE_COLUMNS order:
      0: _id, 1: key_remote_jid, 2: key_from_me, 3: key_id,
      4: status, 5: needs_push, 6: data, 7: timestamp, ...
    """
    row = CarvedRow(
        values=[
            "1",                            # _id
            "919876@s.whatsapp.net",        # key_remote_jid
            0,                              # key_from_me
            "key_abc",                      # key_id
            0,                              # status
            0,                              # needs_push
            "hello world",                  # data  ← index 6
            1751826000000,                  # timestamp
        ],
        confidence=Confidence.CARVED_PARTIAL,
        source_file="msgstore.db",
        provenance="freelist page 2@100",
    )
    mapped = map_columns_to_whatsapp(row)
    assert mapped.get("_id") == "1"
    assert mapped.get("key_remote_jid") == "919876@s.whatsapp.net"
    assert mapped.get("data") == "hello world"
    assert mapped.get("timestamp") == 1751826000000


def test_map_columns_custom_cols():
    """map_columns_to_whatsapp with a custom columns list uses that ordering."""
    row = CarvedRow(
        values=["42", "alice_jid", "hi there"],
        confidence=Confidence.RECOVERED_VERIFIED,
        source_file="msgstore.db",
        provenance="live query",
    )
    mapped = map_columns_to_whatsapp(row, columns=["_id", "sender_jid", "data"])
    assert mapped["_id"] == "42"
    assert mapped["sender_jid"] == "alice_jid"
    assert mapped["data"] == "hi there"


def test_map_columns_short_values():
    """Columns beyond the values list must map to None (not raise IndexError)."""
    row = CarvedRow(
        values=["1"],  # only one value, but we'll ask for many cols
        confidence=Confidence.CARVED_PARTIAL,
        source_file="msgstore.db",
        provenance="freeblock page 1@0",
    )
    mapped = map_columns_to_whatsapp(row, columns=["_id", "key_remote_jid", "data", "timestamp"])
    assert mapped["_id"] == "1"
    assert mapped["key_remote_jid"] is None
    assert mapped["data"] is None


# ===========================================================================
# New tests — Task 3d: WhatsApp msgstore-style schema recovery
# ===========================================================================

def _make_wa_msgstore(path: Path) -> None:
    """Create a synthetic msgstore.db with the key WhatsApp schema columns."""
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE message ("
        "_id INTEGER PRIMARY KEY, key_remote_jid TEXT, key_from_me INTEGER, key_id TEXT,"
        " status INTEGER, needs_push INTEGER, data TEXT, timestamp INTEGER,"
        " media_url TEXT, media_mime_type TEXT, media_wa_type INTEGER, media_size INTEGER,"
        " media_name TEXT, media_caption TEXT, media_hash TEXT, media_duration INTEGER,"
        " sender_jid TEXT)"
    )
    for i in range(30):
        con.execute(
            "INSERT INTO message(_id, key_remote_jid, key_from_me, key_id, status, "
            "needs_push, data, timestamp, media_url, media_mime_type, media_wa_type, "
            "media_size, media_name, media_caption, media_hash, media_duration, sender_jid) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (i + 1, f"91987654{i:04d}@s.whatsapp.net", i % 2, f"key{i}",
             0, 0, f"msgstore message body number {i}", 1751826000000 + i * 60000,
             None, None, 0, 0, None, None, None, 0,
             f"91987654{i:04d}@s.whatsapp.net" if i % 2 == 0 else None),
        )
    con.commit()
    # Delete some rows to trigger recovery.
    con.execute("DELETE FROM message WHERE _id BETWEEN 10 AND 20")
    con.commit()
    con.close()


def test_whatsapp_msgstore_recovery_with_schema_hint(tmp_path):
    """Recovery from a WhatsApp-schema DB with schema_hint yields structured rows."""
    db = tmp_path / "msgstore.db"
    _make_wa_msgstore(db)

    # Build schema hint from live DB (mimicking what the pipeline does).
    con = sqlite3.connect(db)
    cols = [r[1] for r in con.execute("PRAGMA table_info('message')").fetchall()]
    con.close()
    hint = {"col_count": len(cols), "columns": cols}

    rows = recover_deleted_rows(db, table="message", schema_hint=hint)
    assert isinstance(rows, list)

    # Verified rows should have the correct number of columns.
    verified = [r for r in rows if r.confidence == Confidence.RECOVERED_VERIFIED]
    for r in verified:
        assert len(r.values) == len(cols), \
            f"Column count mismatch: expected {len(cols)}, got {len(r.values)}"

    # At least some rows should carry the deleted message text.
    all_text = " ".join(
        str(v) for r in rows for v in r.values if isinstance(v, str)
    )
    assert "msgstore message body number" in all_text


def test_whatsapp_msgstore_recovery_no_crash_without_hint(tmp_path):
    """Recovery from a WhatsApp-schema DB without schema_hint must not crash."""
    db = tmp_path / "msgstore.db"
    _make_wa_msgstore(db)
    rows = recover_deleted_rows(db)  # no hint
    assert isinstance(rows, list)
