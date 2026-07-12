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
