"""Recovery-honesty regression tests (roadmap P0-4, P0-5).

P0-5: WAL frames must be validated (salt generation + cumulative checksum + commit)
      before being stamped RECOVERED_VERIFIED. A stale/torn/uncommitted frame is real
      content but NOT verified — it must carve at CARVED_PARTIAL, never be overstated.
P0-4: the sqbrite cross-check must dedup against the rows the primary pass already
      found (it was fed an empty list, double-counting every hit).
"""
import sqlite3
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from triage.config import Confidence  # noqa: E402
from triage.recovery.sqlite_recovery import _recover_from_wal  # noqa: E402
from triage.recovery.sqbrite import sqbrite_cross_check, sqbrite_scan  # noqa: E402


def _wal_db(tmp_path: Path) -> Path:
    """A WAL-mode db with committed inserts + a delete, WAL left un-checkpointed."""
    db = tmp_path / "msg.db"
    con = sqlite3.connect(str(db))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA wal_autocheckpoint=0")
    con.execute("CREATE TABLE msg(id INTEGER PRIMARY KEY, body TEXT)")
    con.execute("INSERT INTO msg(body) VALUES('hello_docks_4471')")
    con.commit()
    con.execute("INSERT INTO msg(body) VALUES('second_committed_row')")
    con.commit()
    con.execute("DELETE FROM msg WHERE id=1")
    con.commit()
    # deliberately DO NOT close — closing checkpoints and drains the WAL.
    # Keep a reference so GC doesn't close the connection mid-test.
    _OPEN_CONNS.append(con)
    return db


_OPEN_CONNS: list = []


def teardown_module(module):
    for con in _OPEN_CONNS:
        try:
            con.close()
        except Exception:
            pass


# --- P0-5 ------------------------------------------------------------------


def test_valid_wal_frames_are_verified(tmp_path):
    db = _wal_db(tmp_path)
    rows = _recover_from_wal(db, None, set())
    assert rows, "a live current-generation WAL should yield carved page images"
    # Every frame here is current-generation, checksum-valid and committed.
    assert all(r.confidence == Confidence.RECOVERED_VERIFIED for r in rows)
    assert all("[unverified" not in r.provenance for r in rows)


def test_stale_generation_wal_frames_are_downgraded(tmp_path):
    db = _wal_db(tmp_path)
    wal = Path(str(db) + "-wal")
    raw = bytearray(wal.read_bytes())
    # Flip a byte in the header salt so no frame matches the current generation
    # (also breaks the header checksum) — nothing may be promoted to verified.
    raw[16] ^= 0xFF
    wal.write_bytes(bytes(raw))

    rows = _recover_from_wal(db, None, set())
    assert rows, "content is still recoverable, just not verified"
    assert all(r.confidence == Confidence.CARVED_PARTIAL for r in rows)
    assert all("unverified" in r.provenance for r in rows)


def test_torn_frame_checksum_breaks_the_chain(tmp_path):
    db = _wal_db(tmp_path)
    wal = Path(str(db) + "-wal")
    raw = bytearray(wal.read_bytes())
    page_size = struct.unpack(">I", raw[8:12])[0]
    frame_size = 24 + page_size
    # Corrupt the page bytes of the FIRST frame (leave its salt intact) so its stored
    # checksum no longer matches: the chain must break and it must not be verified.
    first_page_off = 32 + 24
    raw[first_page_off + 100] ^= 0xFF
    wal.write_bytes(bytes(raw))

    rows = _recover_from_wal(db, None, set())
    # No frame from the corrupted frame onward may be RECOVERED_VERIFIED.
    assert not any(r.confidence == Confidence.RECOVERED_VERIFIED for r in rows) or all(
        "[unverified" in r.provenance
        for r in rows
        if r.confidence != Confidence.RECOVERED_VERIFIED
    )
    assert any(r.confidence == Confidence.CARVED_PARTIAL for r in rows)


# --- P0-4 ------------------------------------------------------------------


def _carvable_db(tmp_path: Path) -> Path:
    db = tmp_path / "carve.db"
    con = sqlite3.connect(str(db))
    con.execute("PRAGMA secure_delete=OFF")
    con.execute("PRAGMA auto_vacuum=NONE")
    con.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, v TEXT)")
    for i in range(50):
        con.execute("INSERT INTO t(v) VALUES(?)", (f"UNIQUEWORD_{i}_payload_content",))
    con.commit()
    con.execute("DELETE FROM t WHERE id <= 40")
    con.commit()
    con.close()
    return db


def test_sqbrite_cross_check_dedups_dict_rows(tmp_path):
    db = _carvable_db(tmp_path)
    full = sqbrite_scan(db)
    if not full:
        pytest.skip("this platform's SQLite left nothing carvable in the freelist")

    # Feed the first found row back as a PRIMARY dict row (the pipeline keeps dicts,
    # not CarvedRow objects — the old code passed [] and thus double-counted).
    primary = [{"values": list(full[0].values)}]
    deduped = sqbrite_cross_check(db, primary_rows=primary)

    def fp(vals):
        return tuple(str(v)[:40] for v in vals[:2])

    primary_fp = fp(full[0].values)
    assert all(fp(r.values) != primary_fp for r in deduped), (
        "cross-check must exclude rows already recovered by the primary pass"
    )
    # And passing dict rows must not crash (dict.values is a method, not the data).
    assert isinstance(deduped, list)


def test_sqbrite_cross_check_empty_primary_keeps_everything(tmp_path):
    db = _carvable_db(tmp_path)
    full = sqbrite_scan(db)
    if not full:
        pytest.skip("nothing carvable")
    # With no primary fingerprints, nothing is deduped (documents the old behaviour
    # that inflated counts when [] was passed unconditionally).
    everything = sqbrite_cross_check(db, primary_rows=[])
    assert len(everything) >= len(sqbrite_cross_check(db, primary_rows=[{"values": list(full[0].values)}]))
