"""Tests for the SQLite deleted-record recovery engine."""
import sqlite3
from pathlib import Path

from triage.config import Confidence
from triage.recovery import recover_deleted_rows, detect_rowid_gaps, read_live_rows


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
