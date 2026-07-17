"""Tests for the artifact parsers."""
import json
import sqlite3
from pathlib import Path

from triage.parsers import (
    parse_whatsapp_export,
    stream_whatsapp_export,
    parse_whatsapp_db,
    parse_contacts_json,
    parse_calllog_json,
)
from triage.config import Confidence


# ===========================================================================
# Existing tests (preserved for regression)
# ===========================================================================

def test_whatsapp_bracket_format(tmp_path):
    p = tmp_path / "chat.txt"
    p.write_text(
        "[06/07/2026, 21:00:04] Rahul: Are we still on?\n"
        "[06/07/2026, 21:00:39] Imran: Yes, bring it.\n"
        "continuation line of the same message\n"
        "[06/07/2026, 21:01:12] Rahul: Done.\n", encoding="utf-8")
    msgs = parse_whatsapp_export(p)
    assert len(msgs) == 3
    assert msgs[0].sender == "Rahul"
    assert msgs[0].timestamp == "2026-07-06T21:00:04"
    assert "continuation line" in msgs[1].body


def test_whatsapp_dash_format(tmp_path):
    p = tmp_path / "chat.txt"
    p.write_text(
        "06/07/2026, 21:00 - Rahul: Hello there\n"
        "06/07/2026, 21:01 - Imran: Reply here\n", encoding="utf-8")
    msgs = parse_whatsapp_export(p)
    assert len(msgs) == 2
    assert msgs[1].sender == "Imran"


def test_whatsapp_system_line(tmp_path):
    p = tmp_path / "chat.txt"
    p.write_text("[06/07/2026, 20:59:11] Messages are end-to-end encrypted.\n"
                 "[06/07/2026, 21:00:04] Rahul: hi\n", encoding="utf-8")
    msgs = parse_whatsapp_export(p)
    assert msgs[0].sender == "<system>"
    assert msgs[1].sender == "Rahul"


def test_contacts_parser(tmp_path):
    p = tmp_path / "contacts.json"
    p.write_text(json.dumps([
        {"name": "A", "number": "111"},
        {"display_name": "B", "phone": "222", "email": "b@x.com"},
        {"garbage": True},
    ]))
    contacts = parse_contacts_json(p)
    assert len(contacts) == 2
    assert contacts[1].email == "b@x.com"


def test_calllog_parser(tmp_path):
    p = tmp_path / "calllog.json"
    p.write_text(json.dumps([
        {"number": "111", "type": 2, "date": 1751826000000, "duration": 30},
        {"number": "222", "type": 3, "date": 1751826100000, "duration": 0},
    ]))
    calls = parse_calllog_json(p)
    assert calls[0].call_type == "outgoing"
    assert calls[1].call_type == "missed"
    assert calls[0].timestamp is not None


# ===========================================================================
# New tests — Task 1: Streaming parser & edge-case robustness
# ===========================================================================

def test_whatsapp_streaming_returns_iterator(tmp_path):
    """stream_whatsapp_export must be a lazy generator, not a list."""
    import types
    p = tmp_path / "chat.txt"
    p.write_text("[06/07/2026, 10:00:00] Alice: hello\n", encoding="utf-8")
    gen = stream_whatsapp_export(p)
    assert isinstance(gen, types.GeneratorType)


def test_whatsapp_streaming_large_file(tmp_path):
    """1000-message synthetic export: streaming yields exactly 1000 messages."""
    p = tmp_path / "big_chat.txt"
    lines = []
    for i in range(1000):
        h, m, s = i // 3600 % 24, i // 60 % 60, i % 60
        lines.append(f"[06/07/2026, {h:02d}:{m:02d}:{s:02d}] User{i % 5}: Message number {i}\n")
    p.write_text("".join(lines), encoding="utf-8")
    # Use the streaming API — nothing should accumulate in memory beyond one message.
    count = sum(1 for _ in stream_whatsapp_export(p))
    assert count == 1000


def test_whatsapp_sender_with_parens(tmp_path):
    """Sender names containing phone numbers in parens must be kept intact."""
    p = tmp_path / "chat.txt"
    p.write_text("[06/07/2026, 10:00:00] Rahul (9876543210): hello\n", encoding="utf-8")
    msgs = parse_whatsapp_export(p)
    assert len(msgs) == 1
    assert msgs[0].sender == "Rahul (9876543210)"
    assert msgs[0].body == "hello"


def test_whatsapp_colon_in_body(tmp_path):
    """A colon inside the message body must not split the body incorrectly."""
    p = tmp_path / "chat.txt"
    p.write_text("[06/07/2026, 10:00:00] Rahul: a: b: c\n", encoding="utf-8")
    msgs = parse_whatsapp_export(p)
    assert msgs[0].sender == "Rahul"
    assert msgs[0].body == "a: b: c"


def test_whatsapp_continuation_line(tmp_path):
    """A line without a timestamp is appended to the previous message body."""
    p = tmp_path / "chat.txt"
    p.write_text(
        "[06/07/2026, 10:00:00] Alice: first line\n"
        "second line\n"
        "third line\n"
        "[06/07/2026, 10:01:00] Bob: next message\n",
        encoding="utf-8",
    )
    msgs = parse_whatsapp_export(p)
    assert len(msgs) == 2
    assert "second line" in msgs[0].body
    assert "third line" in msgs[0].body


def test_whatsapp_malformed_line_no_crash(tmp_path):
    """A line with embedded NUL bytes or other garbage must not crash the parser."""
    p = tmp_path / "chat.txt"
    # Write a file with a good line, a NUL-containing line, then another good line.
    content = (
        b"[06/07/2026, 10:00:00] Alice: good message\n"
        b"\x00\x01\x02\x03 garbage \xff\xfe\n"
        b"[06/07/2026, 10:01:00] Bob: after garbage\n"
    )
    p.write_bytes(content)
    # Must not raise.
    msgs = parse_whatsapp_export(p)
    # The two good messages must be present.
    senders = [m.sender for m in msgs]
    assert "Alice" in senders
    assert "Bob" in senders


def test_whatsapp_owner_hint_direction(tmp_path):
    """owner_hint correctly labels outgoing messages."""
    p = tmp_path / "chat.txt"
    p.write_text(
        "[06/07/2026, 10:00:00] Alice: hi\n"
        "[06/07/2026, 10:01:00] Bob: hello back\n",
        encoding="utf-8",
    )
    msgs = parse_whatsapp_export(p, owner_hint="Alice")
    assert msgs[0].direction == "outgoing"
    assert msgs[1].direction == "incoming"


def test_whatsapp_all_messages_have_provenance(tmp_path):
    """Every message yielded by the parser must carry a provenance string."""
    p = tmp_path / "chat.txt"
    p.write_text(
        "[06/07/2026, 10:00:00] Alice: msg1\n"
        "[06/07/2026, 10:01:00] Bob: msg2\n",
        encoding="utf-8",
    )
    msgs = parse_whatsapp_export(p)
    for msg in msgs:
        assert msg.provenance, f"Missing provenance on {msg}"
        assert msg.confidence == Confidence.LIVE


# ===========================================================================
# New tests — Task 2: WhatsApp DB parser
# ===========================================================================

def _make_msgstore(path: Path, *, include_contacts=True, include_chat=True,
                    group_jid=False, own_jid="910000000001@s.whatsapp.net") -> None:
    """Build a minimal synthetic msgstore.db for testing."""
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE message ("
        "_id INTEGER PRIMARY KEY, key_remote_jid TEXT, sender_jid TEXT, "
        "status INTEGER, timestamp INTEGER, data TEXT, "
        "media_url TEXT, mime_type TEXT)"
    )
    if include_contacts:
        con.execute(
            "CREATE TABLE wa_contacts (jid TEXT PRIMARY KEY, display_name TEXT, is_self INTEGER DEFAULT 0)"
        )
        con.execute("INSERT INTO wa_contacts VALUES (?, ?, 1)", (own_jid, "Me"))
        con.execute("INSERT INTO wa_contacts VALUES (?, ?, 0)",
                    ("919876543210@s.whatsapp.net", "Rahul"))
    if include_chat:
        con.execute("CREATE TABLE chat (jid TEXT PRIMARY KEY, subject TEXT)")
        if group_jid:
            con.execute("INSERT INTO chat VALUES ('123456789@g.us', 'The Crew')")

    remote = "123456789@g.us" if group_jid else "919876543210@s.whatsapp.net"
    # Message 1: incoming text
    con.execute(
        "INSERT INTO message(_id, key_remote_jid, sender_jid, status, timestamp, data) "
        "VALUES (1, ?, '919876543210@s.whatsapp.net', 0, 1751826000000, 'Meet at docks')",
        (remote,)
    )
    # Message 2: outgoing text
    con.execute(
        "INSERT INTO message(_id, key_remote_jid, sender_jid, status, timestamp, data) "
        "VALUES (2, ?, ?, 1, 1751826060000, 'On my way')",
        (remote, own_jid)
    )
    # Message 3: media message (no text body)
    con.execute(
        "INSERT INTO message(_id, key_remote_jid, sender_jid, status, timestamp, "
        "data, media_url, mime_type) "
        "VALUES (3, ?, '919876543210@s.whatsapp.net', 0, 1751826120000, NULL, "
        "'https://example.com/img.jpg', 'image/jpeg')",
        (remote,)
    )
    con.commit()
    con.close()


def test_whatsapp_db_live_parse_basic(tmp_path):
    """parse_whatsapp_db returns well-formed Message objects from a synthetic msgstore.db."""
    db = tmp_path / "msgstore.db"
    _make_msgstore(db)
    msgs = parse_whatsapp_db(db)
    assert len(msgs) == 3
    bodies = [m.body for m in msgs]
    assert "Meet at docks" in bodies
    assert "On my way" in bodies
    # Media message gets a descriptive body.
    assert any("Media" in b or "image" in b for b in bodies)


def test_whatsapp_db_confidence_live(tmp_path):
    """All messages from parse_whatsapp_db carry LIVE confidence."""
    db = tmp_path / "msgstore.db"
    _make_msgstore(db)
    msgs = parse_whatsapp_db(db)
    for msg in msgs:
        assert msg.confidence == Confidence.LIVE
        assert msg.app == "whatsapp"
        assert "msgstore.db live table" in msg.provenance


def test_whatsapp_db_direction_detection(tmp_path):
    """Outgoing messages (sender_jid == own_jid) must be labelled 'outgoing'."""
    db = tmp_path / "msgstore.db"
    _make_msgstore(db, own_jid="910000000001@s.whatsapp.net")
    msgs = parse_whatsapp_db(db)
    # Message 2 has sender_jid == own_jid → outgoing.
    outgoing = [m for m in msgs if m.direction == "outgoing"]
    assert len(outgoing) >= 1
    assert any("On my way" in m.body for m in outgoing)


def test_whatsapp_db_display_name_in_sender(tmp_path):
    """Sender field should include the display name when wa_contacts is present."""
    db = tmp_path / "msgstore.db"
    _make_msgstore(db)
    msgs = parse_whatsapp_db(db)
    # Incoming messages should resolve to "Rahul (919876543210)"
    incoming = [m for m in msgs if m.direction == "incoming"]
    assert any("Rahul" in m.sender for m in incoming)


def test_whatsapp_db_group_flag(tmp_path):
    """Messages in a group chat must have 'group_message' in flags."""
    db = tmp_path / "msgstore.db"
    _make_msgstore(db, group_jid=True)
    msgs = parse_whatsapp_db(db)
    assert any("group_message" in m.flags for m in msgs)


def test_whatsapp_db_missing_columns_graceful(tmp_path):
    """A minimal msgstore.db with only _id, key_remote_jid, timestamp, data must parse."""
    db = tmp_path / "msgstore.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE message (_id INTEGER PRIMARY KEY, key_remote_jid TEXT, "
        "timestamp INTEGER, data TEXT)"
    )
    con.execute("INSERT INTO message VALUES (1, '919876543210@s.whatsapp.net', 1751826000000, 'hello')")
    con.commit()
    con.close()
    msgs = parse_whatsapp_db(db)
    assert len(msgs) == 1
    assert msgs[0].body == "hello"
    assert msgs[0].confidence == Confidence.LIVE


def test_whatsapp_db_nonexistent_file(tmp_path):
    """Calling parse_whatsapp_db on a missing file must return [] without raising."""
    result = parse_whatsapp_db(tmp_path / "nonexistent.db")
    assert result == []


def test_whatsapp_db_wrong_schema_graceful(tmp_path):
    """A SQLite file that isn't a msgstore.db must return [] without raising."""
    db = tmp_path / "random.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE foo (bar TEXT)")
    con.commit()
    con.close()
    result = parse_whatsapp_db(db)
    assert result == []


def test_whatsapp_db_timestamp_iso_format(tmp_path):
    """Timestamps must be converted from epoch-ms to ISO-8601 strings."""
    db = tmp_path / "msgstore.db"
    _make_msgstore(db)
    msgs = parse_whatsapp_db(db)
    for msg in msgs:
        if msg.timestamp:
            assert "T" in msg.timestamp, f"Not ISO-8601: {msg.timestamp}"


# ===========================================================================
# New tests — Task 4: Telegram cache4.db recovery
# ===========================================================================

from triage.parsers import (
    parse_telegram_db,
    recover_telegram_messages,
    export_recovered_messages_json,
    detect_telegram_schema,
)
from triage.config import Confidence


def _make_telegram_mock_db(path: Path, rows=None, delete_ids=None) -> None:
    """Synthetic cache4.db using the project's mock schema (body/sender/date)."""
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY, sender TEXT, body TEXT, date INTEGER)"
    )
    if rows:
        for r in rows:
            con.execute("INSERT INTO messages(sender, body, date) VALUES (?,?,?)", r)
    con.commit()
    if delete_ids:
        con.execute(
            f"DELETE FROM messages WHERE id IN ({','.join('?' * len(delete_ids))})",
            delete_ids,
        )
        con.commit()
    con.close()


def _make_telegram_real_db(path: Path, rows=None, delete_ids=None) -> None:
    """Synthetic cache4.db using real Telegram v2 schema (mid/from_id/peer_id/date/message)."""
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE messages ("
        "mid INTEGER PRIMARY KEY, from_id INTEGER, peer_id INTEGER, "
        "date INTEGER, message TEXT, out INTEGER)"
    )
    if rows:
        for r in rows:
            con.execute(
                "INSERT INTO messages(mid, from_id, peer_id, date, message, out) "
                "VALUES (?,?,?,?,?,?)",
                r,
            )
    con.commit()
    if delete_ids:
        con.execute(
            f"DELETE FROM messages WHERE mid IN ({','.join('?' * len(delete_ids))})",
            delete_ids,
        )
        con.commit()
    con.close()


# --- Task 4a: schema detection -----------------------------------------------

def test_telegram_schema_detection_mock_schema(tmp_path):
    """detect_telegram_schema correctly identifies the mock (body/sender) layout."""
    db = tmp_path / "cache4.db"
    _make_telegram_mock_db(db, rows=[("Alice", "hello world", 1000)])
    schema = detect_telegram_schema(db)
    assert schema.usable, "Schema should be usable"
    assert schema.mapping.get("body") == "body"
    assert schema.mapping.get("date") == "date"
    assert "synthetic" in schema.version_label or "mock" in schema.version_label \
        or "dynamic" in schema.version_label


def test_telegram_schema_detection_real_v2_schema(tmp_path):
    """detect_telegram_schema correctly identifies real Telegram v2 (from_id/message) layout."""
    db = tmp_path / "cache4.db"
    _make_telegram_real_db(db, rows=[(1, 12345, 67890, 1_700_000_000, "test message", 0)])
    schema = detect_telegram_schema(db)
    assert schema.usable, "Schema should be usable"
    assert schema.mapping.get("body") == "message"
    assert schema.mapping.get("from_id") == "from_id"
    assert "v2" in schema.version_label or "from_id" in schema.version_label \
        or "dynamic" in schema.version_label


def test_telegram_schema_detection_missing_file(tmp_path):
    """detect_telegram_schema on a missing path returns an unusable schema without raising."""
    schema = detect_telegram_schema(tmp_path / "nonexistent.db")
    assert not schema.usable
    assert schema.col_count == 0


# --- Task 4b: live parse ------------------------------------------------------

def test_telegram_live_parse_synthetic_schema(tmp_path):
    """parse_telegram_db correctly parses live rows with the mock schema."""
    db = tmp_path / "cache4.db"
    _make_telegram_mock_db(db, rows=[
        ("Alice", "meet at the docks", 1_700_000_000),
        ("Bob",   "bring the package", 1_700_000_060),
    ])
    msgs = parse_telegram_db(db)
    assert len(msgs) == 2
    bodies = {m.body for m in msgs}
    assert "meet at the docks" in bodies
    assert "bring the package" in bodies
    for msg in msgs:
        assert msg.app == "telegram"
        assert msg.confidence == Confidence.LIVE
        assert msg.timestamp is not None and "T" in msg.timestamp


def test_telegram_live_parse_real_schema(tmp_path):
    """parse_telegram_db correctly parses live rows with the real Telegram v2 schema."""
    db = tmp_path / "cache4.db"
    _make_telegram_real_db(db, rows=[
        (1, 11111, 22222, 1_700_000_000, "secret payload real schema", 0),
        (2, 33333, 22222, 1_700_000_060, "another real message", 1),
    ])
    msgs = parse_telegram_db(db)
    assert len(msgs) >= 2
    bodies_joined = " ".join(m.body for m in msgs)
    assert "secret payload real schema" in bodies_joined
    assert "another real message" in bodies_joined


# --- Task 4c: deleted-row recovery -------------------------------------------

def test_telegram_recovery_deleted_rows(tmp_path):
    """recover_telegram_messages recovers deleted Telegram messages (mock schema)."""
    db = tmp_path / "cache4.db"
    _make_telegram_mock_db(db, rows=[
        ("Rahul", "transfer done account 4471 secretly", 1_700_000_100),
        ("Priya", "warehouse nine tonight", 1_700_000_200),
        ("Ali",   "this one stays live", 1_700_000_300),
        ("Rani",  "also stays live", 1_700_000_400),
    ], delete_ids=[1, 2])

    result = recover_telegram_messages(db)

    assert result["available"] is True
    assert result["error"] is None

    all_messages = result["messages"]
    # Must have found at least the 2 live rows.
    live_bodies = {m["body"] for m in all_messages if m["confidence"] == "live"}
    assert "this one stays live" in live_bodies
    assert "also stays live" in live_bodies

    # Deletion was detected: either carved text or DELETION_DETECTED gap entries
    # must appear (SQLite page layout determines which content survives in freeblocks).
    non_live = [m for m in all_messages if m["confidence"] != "live"]
    assert len(non_live) > 0, (
        "Expected at least one non-live entry (carved or deletion-detected) "
        "from deleting rows 1 and 2"
    )


def test_telegram_recovery_contains_live_rows(tmp_path):
    """recover_telegram_messages includes live rows with LIVE confidence."""
    db = tmp_path / "cache4.db"
    _make_telegram_mock_db(db, rows=[
        ("Alice", "live message alpha", 1_700_000_000),
        ("Bob",   "live message beta",  1_700_000_060),
    ])
    result = recover_telegram_messages(db)
    live_msgs = [m for m in result["messages"] if m["confidence"] == Confidence.LIVE.value]
    assert len(live_msgs) == 2
    bodies = {m["body"] for m in live_msgs}
    assert "live message alpha" in bodies


# --- Task 4d: no-root fallback -----------------------------------------------

def test_telegram_no_root_fallback(tmp_path):
    """recover_telegram_messages on a missing file returns the standard error dict."""
    result = recover_telegram_messages(tmp_path / "nonexistent_cache4.db")
    assert result["available"] is False
    assert result["error"] is not None
    assert "root" in result["error"].lower()
    assert result["messages"] == []
    assert result["counts"]["total"] == 0


# --- Task 4e: JSON export ----------------------------------------------------

def test_telegram_export_json(tmp_path):
    """export_recovered_messages_json writes valid JSON with required provenance keys."""
    db = tmp_path / "cache4.db"
    _make_telegram_mock_db(db, rows=[
        ("Alice", "exportable message one", 1_700_000_000),
        ("Bob",   "exportable message two", 1_700_000_060),
    ])
    result = recover_telegram_messages(db)
    out_path = tmp_path / "tg_export.json"
    returned = export_recovered_messages_json(result, out_path)

    # File exists and path matches.
    assert returned == out_path
    assert out_path.exists()

    # Valid JSON.
    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)

    # Top-level provenance keys must be present.
    assert "tool" in data
    assert "schema_version" in data
    assert "counts" in data
    assert "messages" in data
    assert isinstance(data["messages"], list)

    # Each message must carry the required provenance fields.
    required_keys = {"body", "sender", "confidence", "source_file",
                     "carve_method", "provenance"}
    for msg in data["messages"]:
        missing = required_keys - msg.keys()
        assert not missing, f"Message missing keys {missing}: {msg}"


# --- Task 4f: confidence badges ----------------------------------------------

def test_telegram_confidence_badges(tmp_path):
    """Recovered rows carry correct confidence values; no carve has LIVE confidence."""
    db = tmp_path / "cache4.db"
    # Use a large enough dataset to force freelist pages.
    rows = [(f"user{i}", f"message payload number {i} keyword{i}", 1_700_000_000 + i)
            for i in range(200)]
    _make_telegram_mock_db(db, rows=rows, delete_ids=list(range(10, 160)))

    result = recover_telegram_messages(db)
    msgs = result["messages"]
    assert len(msgs) > 0

    valid_confidences = {c.value for c in Confidence}
    for msg in msgs:
        assert msg["confidence"] in valid_confidences, \
            f"Invalid confidence: {msg['confidence']}"

    # Carved rows must NOT have LIVE confidence.
    carved = [m for m in msgs if "freeblock" in m.get("carve_method", "")
              or "unallocated" in m.get("carve_method", "")
              or m["confidence"] == Confidence.CARVED_PARTIAL.value]
    for m in carved:
        assert m["confidence"] != Confidence.LIVE.value, \
            f"Carved row incorrectly labelled LIVE: {m}"


# --- Task 4g: rowid gap detection -------------------------------------------

def test_telegram_rowid_gap_detection(tmp_path):
    """DELETION_DETECTED entries are emitted for rowid gaps in the messages table."""
    db = tmp_path / "cache4.db"
    _make_telegram_mock_db(db, rows=[
        ("a", "first",  1),
        ("b", "second", 2),
        ("c", "third",  3),
        ("d", "fourth", 4),
        ("e", "fifth",  5),
    ], delete_ids=[2, 3, 4])  # creates a gap: rowids 1 → 5

    result = recover_telegram_messages(db)
    gaps = [m for m in result["messages"]
            if m["confidence"] == Confidence.DELETION_DETECTED.value]
    assert len(gaps) >= 1, "Expected at least one DELETION_DETECTED entry for the rowid gap"
    # The gap entry must carry an honest provenance string.
    for gap in gaps:
        assert "rowid" in gap["provenance"].lower() or "gap" in gap["provenance"].lower()


# --- Task 4h: recover_telegram_messages counts dict --------------------------

def test_telegram_counts_dict_structure(tmp_path):
    """recover_telegram_messages returns a counts dict with all expected keys."""
    db = tmp_path / "cache4.db"
    _make_telegram_mock_db(db, rows=[
        ("Alice", "message one stays", 1_700_000_000),
        ("Bob",   "message two stays", 1_700_000_060),
    ])
    result = recover_telegram_messages(db)
    counts = result["counts"]
    for key in ("live", "recovered_verified", "carved_partial", "deletion_detected", "total"):
        assert key in counts, f"counts dict missing key: {key}"
    assert counts["live"] == 2
    assert counts["total"] == counts["live"] + counts["recovered_verified"] + \
                               counts["carved_partial"] + counts["deletion_detected"]


# ===========================================================================
# Task 5 — Telegram deep recovery (schema, users/chats, media, conversations,
#           timeline)
# ===========================================================================

from triage.parsers import (
    detect_table_schema,
    recover_users_and_chats,
    extract_media_paths_from_blob,
    build_conversations,
)
from triage.timeline import build_timeline


def _make_users_db(path: Path, columns: list[str], rows: list[tuple]) -> None:
    """Create a minimal SQLite DB with a 'users' table for schema tests."""
    con = sqlite3.connect(path)
    col_defs = ", ".join(f'"{c}" TEXT' for c in columns)
    con.execute(f"CREATE TABLE users ({col_defs})")
    placeholders = ", ".join("?" * len(columns))
    con.executemany(f"INSERT INTO users VALUES ({placeholders})", rows)
    con.commit()
    con.close()


def _make_chats_db(path: Path) -> None:
    """Create a minimal DB with both users and chats tables."""
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE users (uid TEXT, first_name TEXT, last_name TEXT)")
    con.execute("CREATE TABLE chats (cid TEXT, title TEXT, peer_type TEXT)")
    con.execute("INSERT INTO users VALUES ('1','Alice','Smith')")
    con.execute("INSERT INTO users VALUES ('2','Bob','Jones')")
    con.execute("INSERT INTO chats VALUES ('100','My Group','group')")
    con.execute("INSERT INTO chats VALUES ('200','Private','user')")
    con.commit()
    con.close()


# --- 5a: generic schema detection, standard column order ---

def test_detect_table_schema_users_default_order(tmp_path):
    """detect_table_schema correctly classifies users table with typical columns."""
    db = tmp_path / "u.db"
    _make_users_db(db, ["uid", "first_name", "last_name", "phone"], [
        ("1", "Alice", "Smith", "+91999"),
    ])
    schema = detect_table_schema(db, "users")
    assert schema.usable, "Schema should be usable"
    assert schema.col_count == 4
    # 'first_name' or 'last_name' should match name_col.
    assert schema.mapping.get("name_col") in ("first_name", "last_name"), \
        f"name_col not found: {schema.mapping}"
    # 'phone' should match phone_col.
    assert schema.mapping.get("phone_col") == "phone"


# --- 5b: shuffled column order, same heuristics apply ---

def test_detect_table_schema_users_shuffled_order(tmp_path):
    """detect_table_schema works regardless of column declaration order."""
    db = tmp_path / "u2.db"
    # Intentionally weird order.
    _make_users_db(db, ["phone", "last_name", "uid", "first_name"], [
        ("+91999", "Smith", "1", "Alice"),
    ])
    schema = detect_table_schema(db, "users")
    assert schema.usable
    # The id role should pick up 'uid'.
    assert schema.mapping.get("id_col") == "uid"
    assert schema.mapping.get("phone_col") == "phone"


# --- 5c: chats table dynamic detection ---

def test_detect_table_schema_chats_dynamic(tmp_path):
    """detect_table_schema correctly classifies a chats table."""
    db = tmp_path / "c.db"
    _make_chats_db(db)
    schema = detect_table_schema(db, "chats")
    assert schema.usable
    # 'title' should be name_col or text_col.
    got = schema.mapping.get("name_col") or schema.mapping.get("text_col")
    assert got == "title", f"Expected title as name/text col, got: {schema.mapping}"


# --- 5d: unknown table returns unusable schema ---

def test_detect_table_schema_unknown_table(tmp_path):
    """detect_table_schema returns usable=False for a table that doesn't exist."""
    db = tmp_path / "x.db"
    _make_chats_db(db)
    schema = detect_table_schema(db, "nonexistent_table_xyz")
    assert not schema.usable, "Unknown table should be unusable"
    assert schema.col_count == 0


# --- 5e: recover_users_and_chats live rows ---

def test_recover_users_and_chats_live(tmp_path):
    """recover_users_and_chats returns live rows from both tables."""
    db = tmp_path / "tg.db"
    _make_chats_db(db)
    result = recover_users_and_chats(db)
    assert result["available"]
    assert len(result["users"]) >= 2, f"Expected >=2 users, got {result['users']}"
    assert len(result["chats"]) >= 2, f"Expected >=2 chats, got {result['chats']}"
    # All live rows should have live confidence.
    for u in result["users"]:
        assert u["confidence"] == Confidence.LIVE.value


# --- 5f: recover_users_and_chats no-file fallback ---

def test_recover_users_and_chats_deleted(tmp_path):
    """recover_users_and_chats returns error dict when file is missing (no-root path)."""
    result = recover_users_and_chats(tmp_path / "missing.db")
    assert not result["available"]
    assert "root" in result["error"].lower()
    assert result["users"] == []
    assert result["chats"] == []


# --- 5g: media blob parsing — path extraction ---

def _make_tl_blob(path: str) -> bytes:
    """Encode a single TL-style length-prefixed string (no padding for simplicity)."""
    encoded = path.encode("utf-8")
    length = len(encoded)
    # Simple: length byte + string bytes (no 4-byte alignment for test purposes).
    return bytes([length]) + encoded


def test_extract_media_paths_from_blob_real_pattern(tmp_path):
    """extract_media_paths_from_blob finds a relative path in a TL blob."""
    blob = _make_tl_blob("4/1.jpg")
    paths = extract_media_paths_from_blob(blob)
    assert "4/1.jpg" in paths, f"Expected '4/1.jpg' in {paths}"


def test_extract_media_paths_from_blob_multiple(tmp_path):
    """extract_media_paths_from_blob finds multiple paths."""
    # Build a blob with two paths concatenated.
    blob = _make_tl_blob("cache/thumb_12345.jpg") + b"\x00\x00" + _make_tl_blob("3/2.mp4")
    paths = extract_media_paths_from_blob(blob)
    # At least one of the two paths should be found.
    assert any("jpg" in p or "mp4" in p for p in paths), \
        f"Expected at least one media path, got: {paths}"


def test_extract_media_paths_from_blob_empty():
    """extract_media_paths_from_blob returns empty list for empty or None blob."""
    assert extract_media_paths_from_blob(b"") == []
    assert extract_media_paths_from_blob(None) == []  # type: ignore[arg-type]


# --- 5h: conversation threading ---

def test_build_conversations_groups_by_chat():
    """build_conversations groups messages by chat_id into separate conversations."""
    messages = [
        {"body": "hi",  "sender": "1", "chat_id": "100", "timestamp": "2024-01-01T00:00:00Z",
         "confidence": "live", "carve_method": "", "provenance": "", "media_artifact_id": None},
        {"body": "bye", "sender": "2", "chat_id": "200", "timestamp": "2024-01-01T00:01:00Z",
         "confidence": "live", "carve_method": "", "provenance": "", "media_artifact_id": None},
        {"body": "ok",  "sender": "1", "chat_id": "100", "timestamp": "2024-01-01T00:02:00Z",
         "confidence": "live", "carve_method": "", "provenance": "", "media_artifact_id": None},
    ]
    convs = build_conversations(messages, users=[], chats=[])
    assert "100" in convs
    assert "200" in convs
    assert convs["100"]["message_count"] == 2
    assert convs["200"]["message_count"] == 1


def test_build_conversations_resolves_sender_name():
    """build_conversations resolves sender IDs to display names from the users list."""
    messages = [
        {"body": "hello", "sender": "42", "chat_id": "99", "timestamp": None,
         "confidence": "live", "carve_method": "", "provenance": "", "media_artifact_id": None},
    ]
    users = [{"_id": "42", "_name": "Alice Smith", "confidence": "live"}]
    convs = build_conversations(messages, users=users, chats=[])
    msg = convs["99"]["messages"][0]
    assert msg["sender_name"] == "Alice Smith", \
        f"Expected 'Alice Smith', got '{msg['sender_name']}'"


def test_build_conversations_title_from_chat():
    """build_conversations uses the chats list to set the conversation title."""
    messages = [
        {"body": "test", "sender": "1", "chat_id": "77", "timestamp": None,
         "confidence": "live", "carve_method": "", "provenance": "", "media_artifact_id": None},
    ]
    chats = [{"_id": "77", "_name": "Forensic Team", "confidence": "live"}]
    convs = build_conversations(messages, users=[], chats=chats)
    assert convs["77"]["title"] == "Forensic Team", \
        f"Expected 'Forensic Team', got '{convs['77']['title']}'"


# --- 5i: timeline includes telegram events ---

def test_timeline_includes_telegram_events():
    """build_timeline emits telegram_message events when telegram_messages are passed."""
    tg_msgs = [
        {
            "body":       "deleted secret message",
            "sender":     "123",
            "timestamp":  "2024-06-01T10:00:00Z",
            "confidence": Confidence.CARVED_PARTIAL.value,
            "source_file": "cache4.db",
        },
        {
            "body":       "live message",
            "sender":     "456",
            "timestamp":  "2024-06-01T11:00:00Z",
            "confidence": Confidence.LIVE.value,
            "source_file": "cache4.db",
        },
    ]
    tg_media = [
        {
            "rel_path":          "4/1.jpg",
            "artifact_id":       "ART-001",
            "parent_message_ts": "2024-06-01T10:30:00Z",
            "confidence":        Confidence.CARVED_PARTIAL.value,
        },
    ]
    timeline = build_timeline(telegram_messages=tg_msgs, telegram_media=tg_media)

    kinds = {ev["kind"] for ev in timeline}
    assert "telegram_message" in kinds, \
        f"Expected 'telegram_message' in timeline kinds: {kinds}"
    assert "telegram_media" in kinds, \
        f"Expected 'telegram_media' in timeline kinds: {kinds}"

    # Timeline must be sorted by timestamp.
    ts_list = [ev["timestamp"] for ev in timeline if ev["timestamp"]]
    assert ts_list == sorted(ts_list), "Timeline is not sorted"

    # Confidence must be preserved.
    carved_ev = next(
        ev for ev in timeline
        if ev["kind"] == "telegram_message" and "deleted" in ev["summary"]
    )
    assert carved_ev["confidence"] == Confidence.CARVED_PARTIAL.value


# ===========================================================================
# Wi-Fi parser tests
# ===========================================================================

from triage.parsers.wifi import (
    parse_wpa_supplicant_conf,
    parse_wifi_config_store_xml,
    parse_wifi_config,
)


_WPA_CONF_TYPICAL = """\
ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=wifi
update_config=1

network={
    ssid="HomeNetwork"
    psk="mysecretpassword"
    key_mgmt=WPA-PSK
    priority=1
}

network={
    ssid="OfficeWifi"
    psk=unquotedpass123
    key_mgmt=WPA-PSK
}

network={
    ssid="OpenCafe"
    key_mgmt=NONE
}

network={
    ssid="OldWep"
    wep_key0="weppass"
    key_mgmt=NONE
}
"""

_WPA_CONF_SPECIAL_CHARS = """\
network={
    ssid="My Network With Spaces & Special!"
    psk="p@ssw0rd#2024"
    key_mgmt=WPA-PSK
}
"""


def test_wpa_conf_basic(tmp_path):
    """Parse a typical wpa_supplicant.conf with multiple network blocks."""
    p = tmp_path / "wpa_supplicant.conf"
    p.write_text(_WPA_CONF_TYPICAL, encoding="utf-8")
    nets = parse_wpa_supplicant_conf(p)
    assert len(nets) == 4

    home = nets[0]
    assert home.ssid == "HomeNetwork"
    assert home.password == "mysecretpassword"
    assert home.security == "WPA/WPA2"

    office = nets[1]
    assert office.ssid == "OfficeWifi"
    assert office.password == "unquotedpass123"
    assert office.security == "WPA/WPA2"

    open_net = nets[2]
    assert open_net.ssid == "OpenCafe"
    assert open_net.password == ""
    assert open_net.security == "OPEN"

    wep_net = nets[3]
    assert wep_net.ssid == "OldWep"
    assert wep_net.password == "weppass"
    assert wep_net.security == "WEP"


def test_wpa_conf_special_chars(tmp_path):
    """SSIDs and PSKs with spaces, ampersands, and special characters are parsed correctly."""
    p = tmp_path / "wpa_supplicant.conf"
    p.write_text(_WPA_CONF_SPECIAL_CHARS, encoding="utf-8")
    nets = parse_wpa_supplicant_conf(p)
    assert len(nets) == 1
    assert nets[0].ssid == "My Network With Spaces & Special!"
    assert nets[0].password == "p@ssw0rd#2024"


def test_wpa_conf_empty_file(tmp_path):
    """Empty config file returns an empty list, not an exception."""
    p = tmp_path / "wpa_supplicant.conf"
    p.write_text("", encoding="utf-8")
    assert parse_wpa_supplicant_conf(p) == []


def test_wpa_conf_missing_file(tmp_path):
    """Non-existent file returns empty list gracefully."""
    assert parse_wpa_supplicant_conf(tmp_path / "does_not_exist.conf") == []


_XML_TYPICAL = """\
<?xml version="1.0" encoding="utf-8"?>
<WifiConfigStoreData version="3">
  <NetworkList>
    <Network>
      <WifiConfiguration>
        <string name="SSID">&quot;HomeNetwork&quot;</string>
        <string name="PreSharedKey">&quot;secretpass&quot;</string>
        <string name="AllowedKeyMgmt">WPA_PSK</string>
      </WifiConfiguration>
    </Network>
    <Network>
      <WifiConfiguration>
        <string name="SSID">&quot;CorpWifi&quot;</string>
        <string name="PreSharedKey">&quot;corp_pass_2024&quot;</string>
        <string name="AllowedKeyMgmt">WPA2_PSK</string>
      </WifiConfiguration>
    </Network>
    <Network>
      <WifiConfiguration>
        <string name="SSID">&quot;OpenHotspot&quot;</string>
        <string name="AllowedKeyMgmt">NONE</string>
      </WifiConfiguration>
    </Network>
    <Network>
      <WifiConfiguration>
        <string name="SSID">&quot;Wpa3Net&quot;</string>
        <string name="PreSharedKey">&quot;wpa3pass&quot;</string>
        <string name="AllowedKeyMgmt">WPA3_SAE</string>
      </WifiConfiguration>
    </Network>
  </NetworkList>
</WifiConfigStoreData>
"""

_XML_NO_SSID = """\
<?xml version="1.0" encoding="utf-8"?>
<WifiConfigStoreData version="3">
  <NetworkList>
    <Network>
      <WifiConfiguration>
        <string name="PreSharedKey">&quot;pass&quot;</string>
      </WifiConfiguration>
    </Network>
  </NetworkList>
</WifiConfigStoreData>
"""


def test_xml_basic(tmp_path):
    """Parse a typical WifiConfigStore.xml with multiple Network elements."""
    p = tmp_path / "WifiConfigStore.xml"
    p.write_text(_XML_TYPICAL, encoding="utf-8")
    nets = parse_wifi_config_store_xml(p)
    assert len(nets) == 4

    home = nets[0]
    assert home.ssid == "HomeNetwork"
    assert home.password == "secretpass"
    assert home.security == "WPA/WPA2"

    corp = nets[1]
    assert corp.ssid == "CorpWifi"
    assert corp.password == "corp_pass_2024"
    assert corp.security == "WPA/WPA2"

    open_net = nets[2]
    assert open_net.ssid == "OpenHotspot"
    assert open_net.password == ""
    assert open_net.security == "OPEN"

    wpa3_net = nets[3]
    assert wpa3_net.ssid == "Wpa3Net"
    assert wpa3_net.security == "WPA3"


def test_xml_skips_network_without_ssid(tmp_path):
    """Networks without an SSID are silently skipped."""
    p = tmp_path / "WifiConfigStore.xml"
    p.write_text(_XML_NO_SSID, encoding="utf-8")
    nets = parse_wifi_config_store_xml(p)
    assert nets == []


def test_xml_malformed_graceful(tmp_path):
    """Malformed XML returns empty list instead of raising."""
    p = tmp_path / "WifiConfigStore.xml"
    p.write_text("<unclosed>", encoding="utf-8")
    assert parse_wifi_config_store_xml(p) == []


def test_xml_missing_file(tmp_path):
    """Non-existent file returns empty list gracefully."""
    assert parse_wifi_config_store_xml(tmp_path / "does_not_exist.xml") == []


def test_dispatch_conf(tmp_path):
    """parse_wifi_config() dispatches to the .conf parser for .conf files."""
    p = tmp_path / "wpa_supplicant.conf"
    p.write_text(_WPA_CONF_TYPICAL, encoding="utf-8")
    nets = parse_wifi_config(p)
    assert len(nets) == 4
    assert all(hasattr(n, "ssid") for n in nets)


def test_dispatch_xml(tmp_path):
    """parse_wifi_config() dispatches to the XML parser for .xml files."""
    p = tmp_path / "WifiConfigStore.xml"
    p.write_text(_XML_TYPICAL, encoding="utf-8")
    nets = parse_wifi_config(p)
    assert len(nets) == 4


def test_source_file_set(tmp_path):
    """source_file field is set to the basename of the parsed file."""
    p = tmp_path / "WifiConfigStore.xml"
    p.write_text(_XML_TYPICAL, encoding="utf-8")
    nets = parse_wifi_config(p)
    assert all(n.source_file == "WifiConfigStore.xml" for n in nets)


def test_confidence_live(tmp_path):
    """All parsed networks carry LIVE confidence (read from OS storage, not carved)."""
    from triage.config import Confidence
    p = tmp_path / "WifiConfigStore.xml"
    p.write_text(_XML_TYPICAL, encoding="utf-8")
    nets = parse_wifi_config(p)
    assert all(n.confidence == Confidence.LIVE for n in nets)


def test_serialisable(tmp_path):
    """WifiNetwork.to_dict() produces a JSON-serialisable dict with expected keys."""
    import json as _json
    p = tmp_path / "WifiConfigStore.xml"
    p.write_text(_XML_TYPICAL, encoding="utf-8")
    net = parse_wifi_config(p)[0]
    d = net.to_dict()
    # Must be JSON-serialisable.
    raw = _json.dumps(d)
    assert "HomeNetwork" in raw
    # Must contain all expected keys.
    assert {"ssid", "password", "security", "confidence", "source_file"} <= set(d.keys())
