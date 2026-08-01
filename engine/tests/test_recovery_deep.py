"""Deep-recovery regression tests (roadmap P1-5, P1-6).

P1-6 — SQLite overflow-page chains
    A record whose payload does not fit on its b-tree page spills onto a linked list of
    overflow pages. Before this work the carver truncated every such payload at the page
    boundary, so every long message and every large TEXT/BLOB column came back silently
    short. These tests prove (a) the X/M/K spill maths matches SQLite's, (b) a multi-page
    value round-trips byte-for-byte, and (c) when the chain CANNOT be completed the value
    is kept up to what was recovered, an explicit warning is attached and the row is
    downgraded to CARVED_PARTIAL — never left claiming RECOVERED_VERIFIED.

P1-5 — DELETION_DETECTED as a first-class evidence class
    Structural deletion evidence proves rows were removed and recovers NO content. These
    tests pin that distinction: every record is confidence-tagged, carries honest
    false-positive causes, and the summary paragraph says out loud that no content is
    recovered. A WITHOUT ROWID table must be detected and skipped, not fabricated over.

Every fixture is built programmatically with the stdlib ``sqlite3`` module in ``tmp_path``.
"""

from __future__ import annotations

import json
import sqlite3
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from triage.config import Confidence  # noqa: E402
from triage.recovery.sqlite_recovery import (  # noqa: E402
    NOT_APPLICABLE,
    DeletionEvidence,
    _btree_header_offset,
    _db_geometry,
    _live_cell_offsets,
    _page_bytes,
    _read_varint,
    _record_declared_size,
    detect_deletion_evidence,
    detect_rowid_gaps,
    deletion_evidence_summary,
    local_payload_size,
    overflow_thresholds,
    read_overflow_chain,
    read_page_cells,
    recover_deleted_rows,
)

# ===========================================================================
# helpers — all fixtures are built from bytes/sqlite3 at test time
# ===========================================================================


def _make_db(path: Path, page_size: int, rows: list[tuple], *, blob: bool = False) -> None:
    """One table ``t(id INTEGER PRIMARY KEY, body TEXT|BLOB)`` with the given rows."""
    con = sqlite3.connect(str(path))
    con.execute(f"PRAGMA page_size={page_size}")
    con.execute("PRAGMA auto_vacuum=NONE")
    con.execute(f"CREATE TABLE t(id INTEGER PRIMARY KEY, body {'BLOB' if blob else 'TEXT'})")
    for r in rows:
        con.execute("INSERT INTO t(body) VALUES(?)", r)
    con.commit()
    con.close()


def _leaf_pages(data: bytes, page_size: int) -> list[int]:
    """Page numbers that are table-b-tree leaves, excluding page 1 (sqlite_master)."""
    out = []
    for p in range(2, (len(data) // page_size) + 1):
        page = _page_bytes(data, page_size, p)
        if len(page) == page_size and page[_btree_header_offset(p)] == 0x0D:
            out.append(p)
    return out


def _first_cell_geometry(data: bytes, page_size: int, reserved: int, page_num: int):
    """(cell_offset, payload_len, local, absolute overflow-pointer offset) for cell 0."""
    page = _page_bytes(data, page_size, page_num)
    hdr_off = _btree_header_offset(page_num)
    offs = _live_cell_offsets(page, hdr_off)
    assert offs, "expected at least one allocated cell on this page"
    off = offs[0]
    payload_len, c1 = _read_varint(page, off)
    _rowid, c2 = _read_varint(page, off + c1)
    rec_start = off + c1 + c2
    local = local_payload_size(payload_len, page_size, reserved)
    ptr_abs = (page_num - 1) * page_size + rec_start + local
    return off, payload_len, local, ptr_abs


def _patch(path: Path, offset: int, blob: bytes) -> None:
    raw = bytearray(path.read_bytes())
    raw[offset : offset + len(blob)] = blob
    path.write_bytes(bytes(raw))


def _push_page_onto_freelist(path: Path, page_num: int) -> None:
    """Append a synthetic freelist trunk page listing ``page_num`` and point the header
    at it. This puts a page that still holds intact cells (and whose overflow chain is
    still present in the file) into the exact position the carver treats as recoverable
    deleted content — a deterministic stand-in for a real DELETE."""
    raw = bytearray(path.read_bytes())
    page_size, _ = _db_geometry(bytes(raw))
    n_pages = len(raw) // page_size
    trunk_no = n_pages + 1
    trunk = bytearray(page_size)
    trunk[0:4] = struct.pack(">I", 0)  # no next trunk
    trunk[4:8] = struct.pack(">I", 1)  # one leaf entry
    trunk[8:12] = struct.pack(">I", page_num)
    raw += trunk
    raw[32:36] = struct.pack(">I", trunk_no)  # first freelist trunk page
    raw[36:40] = struct.pack(">I", 2)  # total freelist pages
    path.write_bytes(bytes(raw))


def _synthetic_chain(page_size: int, pages: list[tuple[int, int, bytes]]) -> bytes:
    """Build a raw page image. ``pages`` is [(page_no, next_page_no, content)]."""
    max_page = max(p for p, _n, _c in pages)
    buf = bytearray(page_size * max_page)
    for pno, nxt, content in pages:
        base = (pno - 1) * page_size
        buf[base : base + 4] = struct.pack(">I", nxt)
        buf[base + 4 : base + 4 + len(content)] = content
    return bytes(buf)


# ===========================================================================
# P1-6 — spill threshold maths
# ===========================================================================


def test_overflow_thresholds_match_sqlite_formula():
    """usable/X/M are SQLite's own definitions, not an approximation."""
    for page_size in (512, 1024, 4096, 65536):
        th = overflow_thresholds(page_size, 0)
        assert th["usable"] == page_size
        assert th["max_local"] == page_size - 35
        assert th["min_local"] == ((page_size - 12) * 32 // 255) - 23
    # The reserved-space byte (file header offset 20) shrinks the usable page.
    th = overflow_thresholds(4096, 32)
    assert th["usable"] == 4064
    assert th["max_local"] == 4064 - 35


def test_payload_just_under_threshold_stays_on_page():
    """X bytes is the largest payload that never spills."""
    x = overflow_thresholds(1024)["max_local"]
    assert local_payload_size(x, 1024) == x
    assert local_payload_size(x - 1, 1024) == x - 1


def test_payload_just_over_threshold_spills_with_k_or_m():
    """X+1 spills, and the on-page amount is exactly K (if K<=X) else M."""
    page_size = 1024
    th = overflow_thresholds(page_size)
    x, m, usable = th["max_local"], th["min_local"], th["usable"]
    payload = x + 1
    local = local_payload_size(payload, page_size)
    assert local < payload, "a payload above X must spill"
    k = m + ((payload - m) % (usable - 4))
    assert local == (k if k <= x else m)
    assert m <= local <= x


def test_spill_amount_never_exceeds_x_for_any_payload():
    """Fuzz the maths: the on-page amount is always in [M, X] once spilling starts."""
    page_size = 4096
    th = overflow_thresholds(page_size)
    for payload in range(th["max_local"] + 1, th["max_local"] + 4000, 37):
        local = local_payload_size(payload, page_size)
        assert th["min_local"] <= local <= th["max_local"]
        assert local < payload


def test_degenerate_geometry_does_not_invent_a_spill_point():
    """A corrupt header must not make us split a payload at a nonsense boundary."""
    assert local_payload_size(5000, 0) == 5000
    assert local_payload_size(5000, 8) == 5000
    assert local_payload_size(5000, -4096) == 5000


# ===========================================================================
# P1-6 — read_overflow_chain
# ===========================================================================


def test_read_overflow_chain_reassembles_three_pages():
    ps = 512
    cap = ps - 4
    a, b, c = b"A" * cap, b"B" * cap, b"C" * 40
    data = _synthetic_chain(ps, [(1, 0, b""), (2, 3, a), (3, 4, b), (4, 0, c)])
    payload, status = read_overflow_chain(data, ps, 2, cap * 2 + 40)
    assert status["complete"] is True
    assert status["pages_read"] == 3
    assert status["truncated_bytes"] == 0
    assert payload == a + b + c


def test_read_overflow_chain_zero_needed_reads_nothing():
    data = _synthetic_chain(512, [(1, 0, b""), (2, 0, b"x")])
    payload, status = read_overflow_chain(data, 512, 2, 0)
    assert payload == b""
    assert status["complete"] is True
    assert status["pages_read"] == 0


def test_read_overflow_chain_out_of_range_page_is_truncated_not_raised():
    data = _synthetic_chain(512, [(1, 0, b""), (2, 0, b"x" * 100)])
    payload, status = read_overflow_chain(data, 512, 999, 1000)
    assert payload == b""
    assert status["complete"] is False
    assert status["truncated_bytes"] == 1000
    assert "unavailable" in status["reason"]


def test_read_overflow_chain_zero_first_page_is_truncated():
    data = _synthetic_chain(512, [(1, 0, b""), (2, 0, b"x" * 100)])
    payload, status = read_overflow_chain(data, 512, 0, 100)
    assert status["complete"] is False
    assert payload == b""
    assert "0" in status["reason"]


def test_read_overflow_chain_cycle_terminates():
    """A self-referential chain must stop immediately, not spin forever."""
    ps = 512
    data = _synthetic_chain(ps, [(1, 0, b""), (2, 2, b"Z" * (ps - 4))])
    payload, status = read_overflow_chain(data, ps, 2, 100_000)
    assert status["complete"] is False
    assert status["pages_read"] == 1
    assert "cyclic" in status["reason"]
    assert len(payload) == ps - 4


def test_read_overflow_chain_longer_cycle_terminates():
    ps = 512
    cap = ps - 4
    data = _synthetic_chain(
        ps, [(1, 0, b""), (2, 3, b"A" * cap), (3, 4, b"B" * cap), (4, 2, b"C" * cap)]
    )
    payload, status = read_overflow_chain(data, ps, 2, 10_000_000)
    assert status["complete"] is False
    assert status["pages_read"] == 3
    assert "cyclic" in status["reason"]
    assert len(payload) == cap * 3


def test_read_overflow_chain_respects_reserved_space():
    """Reserved bytes at the end of each page are NOT payload."""
    ps, reserved = 512, 20
    cap = ps - reserved - 4
    data = _synthetic_chain(ps, [(1, 0, b""), (2, 0, b"P" * (ps - 4))])
    payload, status = read_overflow_chain(data, ps, 2, cap, reserved)
    assert status["complete"] is True
    assert len(payload) == cap


def test_read_overflow_chain_never_raises_on_garbage():
    """Hostile input degrades to a status dict; it never raises and never lies."""
    for bad in (b"", b"\x00" * 7, b"\xff" * 3000, b"\x01" * 200):
        payload, status = read_overflow_chain(bad, 512, 2, 500)
        assert isinstance(payload, bytes)
        assert set(status) == {"complete", "pages_read", "reason", "truncated_bytes"}
        assert isinstance(status["complete"], bool)
        # complete==True is only ever claimed when the bytes really were assembled.
        assert (len(payload) >= 500) == status["complete"]
    # A page size that cannot hold a 4-byte pointer must degrade, not divide by zero.
    _p, st = read_overflow_chain(b"\x00" * 100, 4, 2, 10)
    assert st["complete"] is False
    assert st["truncated_bytes"] == 10


# ===========================================================================
# P1-6 — end-to-end: real databases
# ===========================================================================


def test_value_at_threshold_has_no_overflow_page(tmp_path):
    """A payload of exactly X fits on the page; X+1 forces an overflow page.

    Record layout for ``t(id INTEGER PRIMARY KEY, body TEXT)`` with an aliased rowid:
    header = [header_len:1][id serial 0x00:1][body serial:2] = 4 bytes, so
    payload_len = 4 + len(body).
    """
    ps = 1024
    x = overflow_thresholds(ps)["max_local"]  # 989

    under = tmp_path / "under.db"
    _make_db(under, ps, [("u" * (x - 4),)])
    data = under.read_bytes()
    assert len(data) // ps == 2, "no overflow page should have been allocated"
    rows = read_page_cells(under, _leaf_pages(data, ps)[0])
    assert len(rows) == 1 and rows[0].values[1] == "u" * (x - 4)
    assert rows[0].confidence == Confidence.RECOVERED_VERIFIED

    over = tmp_path / "over.db"
    _make_db(over, ps, [("o" * (x - 3),)])
    data = over.read_bytes()
    assert len(data) // ps == 3, "one overflow page should have been allocated"
    rows = read_page_cells(over, _leaf_pages(data, ps)[0])
    assert len(rows) == 1 and rows[0].values[1] == "o" * (x - 3)
    assert rows[0].confidence == Confidence.RECOVERED_VERIFIED
    assert rows[0].warnings == []


def test_multipage_text_round_trips_from_a_live_page(tmp_path):
    """A TEXT value spanning many overflow pages comes back byte-identical."""
    db = tmp_path / "big.db"
    big = "OVERFLOW_TEXT_" * 700  # ~9.8 KB across ~10 pages at page_size 1024
    _make_db(db, 1024, [(big,)])
    data = db.read_bytes()
    ps, _res = _db_geometry(data)
    rows = read_page_cells(db, _leaf_pages(data, ps)[0])
    assert len(rows) == 1
    assert rows[0].values[1] == big
    assert rows[0].confidence == Confidence.RECOVERED_VERIFIED
    assert rows[0].warnings == []
    # Sanity: the sqlite3 engine agrees.
    con = sqlite3.connect(str(db))
    assert con.execute("SELECT body FROM t").fetchone()[0] == big
    con.close()


def test_blob_spanning_three_or_more_overflow_pages(tmp_path):
    """A BLOB crossing 3+ overflow pages reassembles, and the chain reports its hops."""
    db = tmp_path / "blob.db"
    payload = bytes(range(256)) * 12  # 3072 bytes of non-text bytes
    _make_db(db, 512, [(sqlite3.Binary(payload),)], blob=True)
    data = db.read_bytes()
    ps, reserved = _db_geometry(data)
    page_num = _leaf_pages(data, ps)[0]

    _off, payload_len, local, ptr_abs = _first_cell_geometry(data, ps, reserved, page_num)
    assert local < payload_len, "this record must have spilled"
    first_page = struct.unpack(">I", data[ptr_abs : ptr_abs + 4])[0]
    tail, status = read_overflow_chain(data, ps, first_page, payload_len - local, reserved)
    assert status["complete"] is True
    assert status["pages_read"] >= 3, f"expected 3+ overflow pages, got {status}"
    assert len(tail) == payload_len - local

    rows = read_page_cells(db, page_num)
    assert len(rows) == 1
    assert rows[0].values[1] == payload
    assert rows[0].confidence == Confidence.RECOVERED_VERIFIED


def test_overflow_round_trips_from_a_freelist_carve(tmp_path):
    """The same multi-page value must reassemble when the page is carved as deleted.

    The leaf page is pushed onto the freelist (the exact state a DELETE leaves behind),
    so ``recover_deleted_rows`` reaches it through its highest-confidence vector.
    """
    db = tmp_path / "freelist.db"
    big = "CARVED_OVERFLOW_" * 400
    _make_db(db, 1024, [(big,), ("short row that stays put",)])
    data = db.read_bytes()
    ps, _res = _db_geometry(data)
    leaf = _leaf_pages(data, ps)[0]
    _push_page_onto_freelist(db, leaf)

    rows = recover_deleted_rows(db, "t")
    hits = [r for r in rows if any(v == big for v in r.values)]
    assert hits, (
        "the carved record must carry the FULL overflow-reassembled value, not a "
        f"page-truncated prefix (got {len(rows)} rows)"
    )
    assert any(r.confidence == Confidence.RECOVERED_VERIFIED for r in hits)
    assert all("freelist" in r.provenance for r in hits)


def test_zeroed_overflow_pointer_downgrades_to_carved_and_warns(tmp_path):
    """A destroyed overflow pointer must never yield a silently-short 'verified' value."""
    db = tmp_path / "zeroed.db"
    big = "TRUNCATE_ME_" * 400
    _make_db(db, 1024, [(big,)])
    data = db.read_bytes()
    ps, reserved = _db_geometry(data)
    page_num = _leaf_pages(data, ps)[0]
    _off, payload_len, local, ptr_abs = _first_cell_geometry(data, ps, reserved, page_num)
    _patch(db, ptr_abs, b"\x00\x00\x00\x00")  # chain pointer destroyed

    rows = read_page_cells(db, page_num)  # must not raise
    assert len(rows) == 1
    row = rows[0]
    assert row.confidence == Confidence.CARVED_PARTIAL, (
        "a truncated overflow value must be downgraded, never left RECOVERED_VERIFIED"
    )
    joined = " ".join(row.warnings)
    assert "overflow chain incomplete" in joined
    assert "value truncated at" in joined
    # The bytes we DID recover are kept, not thrown away or padded.
    recovered = row.values[1]
    assert isinstance(recovered, str) and recovered
    assert big.startswith(recovered)
    assert len(recovered) < len(big)


def test_out_of_range_overflow_pointer_downgrades(tmp_path):
    db = tmp_path / "oor.db"
    big = "OUT_OF_RANGE_" * 400
    _make_db(db, 1024, [(big,)])
    data = db.read_bytes()
    ps, reserved = _db_geometry(data)
    page_num = _leaf_pages(data, ps)[0]
    _o, _pl, _lo, ptr_abs = _first_cell_geometry(data, ps, reserved, page_num)
    _patch(db, ptr_abs, struct.pack(">I", 0xFFFFFF))  # page far past EOF

    rows = read_page_cells(db, page_num)
    assert len(rows) == 1
    assert rows[0].confidence == Confidence.CARVED_PARTIAL
    assert any("overflow chain incomplete" in w for w in rows[0].warnings)


def test_cyclic_overflow_pointer_in_a_real_db_terminates(tmp_path):
    """A self-referential chain in a real file must degrade, not hang or raise."""
    db = tmp_path / "cycle.db"
    big = "CYCLE_" * 900
    _make_db(db, 1024, [(big,)])
    data = db.read_bytes()
    ps, reserved = _db_geometry(data)
    page_num = _leaf_pages(data, ps)[0]
    _o, _pl, _lo, ptr_abs = _first_cell_geometry(data, ps, reserved, page_num)
    first_page = struct.unpack(">I", data[ptr_abs : ptr_abs + 4])[0]
    # Make the first overflow page point at itself.
    _patch(db, (first_page - 1) * ps, struct.pack(">I", first_page))

    rows = read_page_cells(db, page_num)
    assert len(rows) == 1
    assert rows[0].confidence == Confidence.CARVED_PARTIAL
    assert any("overflow chain incomplete" in w for w in rows[0].warnings)


def test_truncated_file_loses_the_chain_tail_honestly(tmp_path):
    """Chopping the overflow pages off the end of the file reports truncation."""
    db = tmp_path / "chopped.db"
    big = "CHOPPED_" * 700
    _make_db(db, 1024, [(big,)])
    data = db.read_bytes()
    ps, _res = _db_geometry(data)
    page_num = _leaf_pages(data, ps)[0]
    db.write_bytes(data[: (page_num + 1) * ps])  # keep the leaf, drop most of the chain

    rows = read_page_cells(db, page_num)
    assert len(rows) == 1
    assert rows[0].confidence == Confidence.CARVED_PARTIAL
    assert any("overflow chain incomplete" in w for w in rows[0].warnings)
    assert big.startswith(rows[0].values[1])


def test_record_declared_size_matches_payload_len_for_real_cells(tmp_path):
    """The structural cross-check that lets us trust a >page-size payload_len."""
    db = tmp_path / "declared.db"
    _make_db(db, 1024, [("D" * 4000,)])
    data = db.read_bytes()
    ps, reserved = _db_geometry(data)
    page_num = _leaf_pages(data, ps)[0]
    off, payload_len, local, _ptr = _first_cell_geometry(data, ps, reserved, page_num)
    page = _page_bytes(data, ps, page_num)
    _pl, c1 = _read_varint(page, off)
    _rid, c2 = _read_varint(page, off + c1)
    head = page[off + c1 + c2 : off + c1 + c2 + local]
    assert _record_declared_size(head) == payload_len
    assert _record_declared_size(b"\x00") is None


def test_recover_deleted_rows_still_works_on_a_db_with_overflow(tmp_path):
    """The overflow wiring must not break ordinary carving or crash on any page."""
    db = tmp_path / "mixed.db"
    con = sqlite3.connect(str(db))
    con.execute("PRAGMA page_size=1024")
    con.execute("PRAGMA auto_vacuum=NONE")
    con.execute("PRAGMA secure_delete=OFF")
    con.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, body TEXT)")
    for i in range(60):
        con.execute("INSERT INTO t(body) VALUES(?)", (f"SHORTMSG_{i}_content", ))
    con.execute("INSERT INTO t(body) VALUES(?)", ("LONGMSG_" * 500,))
    con.commit()
    con.execute("DELETE FROM t WHERE id % 2 = 0")
    con.commit()
    con.close()
    rows = recover_deleted_rows(db, "t")
    assert isinstance(rows, list)
    for r in rows:
        # Any row that is not fully reassembled must say so and must not claim verified.
        if any("overflow chain incomplete" in w for w in r.warnings):
            assert r.confidence == Confidence.CARVED_PARTIAL


# ===========================================================================
# P1-5 — DELETION_DETECTED as a first-class evidence class
# ===========================================================================


def _deleted_db(path: Path) -> None:
    """A rowid table with a hole in the middle AND rows removed from the top."""
    con = sqlite3.connect(str(path))
    con.execute("PRAGMA auto_vacuum=NONE")
    con.execute("CREATE TABLE msg(id INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT)")
    # Bodies are deliberately fat so whole pages are released by the DELETE below —
    # that is what makes the freelist-page mechanism observable.
    for i in range(200):
        con.execute("INSERT INTO msg(body) VALUES(?)", (f"body_{i}_" + "x" * 400,))
    con.commit()
    con.execute("DELETE FROM msg WHERE id BETWEEN 50 AND 90")   # a contiguous gap
    con.execute("DELETE FROM msg WHERE id > 180")               # above the high-water mark
    con.commit()
    con.close()


def _mechanisms(items) -> set[str]:
    return {i.mechanism for i in items}


def test_rowid_gap_is_a_confidence_tagged_evidence_record(tmp_path):
    db = tmp_path / "gap.db"
    _deleted_db(db)
    items = detect_deletion_evidence(db, ["msg"])
    gaps = [i for i in items if i.mechanism == "rowid-gap"]
    assert gaps, "the 50..90 hole must be reported"
    g = gaps[0]
    assert g.confidence == Confidence.DELETION_DETECTED.value == "deletion"
    assert g.first_missing_rowid == 50
    assert g.last_missing_rowid == 90
    assert g.missing_count == 41
    assert g.table == "msg"
    assert g.db_file == "gap.db"
    assert g.provenance


def test_max_rowid_shortfall_mechanism(tmp_path):
    db = tmp_path / "short.db"
    _deleted_db(db)
    items = detect_deletion_evidence(db, ["msg"])
    sf = [i for i in items if i.mechanism == "max-rowid-shortfall"]
    assert sf, "max(rowid) exceeds COUNT(*) and must be reported"
    assert sf[0].missing_count == 41  # max rowid 180, 139 live rows
    assert sf[0].details["max_rowid"] == 180
    assert sf[0].confidence == Confidence.DELETION_DETECTED.value


def test_sequence_shortfall_mechanism(tmp_path):
    """AUTOINCREMENT high-water mark above max(rowid) proves rows above it are gone."""
    db = tmp_path / "seq.db"
    _deleted_db(db)
    items = detect_deletion_evidence(db, ["msg"])
    seq = [i for i in items if i.mechanism == "sequence-shortfall"]
    assert seq, "sqlite_sequence still records 200 while max(rowid) is 180"
    s = seq[0]
    assert s.details["sequence_value"] == 200
    assert s.details["max_rowid"] == 180
    assert s.missing_count == 20
    assert s.confidence == Confidence.DELETION_DETECTED.value


def test_freelist_pages_mechanism(tmp_path):
    db = tmp_path / "fl.db"
    _deleted_db(db)
    items = detect_deletion_evidence(db)
    fl = [i for i in items if i.mechanism == "freelist-pages"]
    assert fl, "deleting 60 rows must have released pages onto the freelist"
    assert fl[0].details["freelist_pages"] > 0
    assert fl[0].table == "(database-wide)"
    assert fl[0].confidence == Confidence.DELETION_DETECTED.value
    assert "36:40" in fl[0].provenance


def test_no_freelist_pages_means_no_freelist_record(tmp_path):
    db = tmp_path / "clean.db"
    _make_db(db, 1024, [("nothing was ever deleted here",)])
    items = detect_deletion_evidence(db)
    assert not [i for i in items if i.mechanism == "freelist-pages"]


def test_live_vs_recovered_mechanism_with_carved_rows(tmp_path):
    """A carved rowid absent from the live table is the strongest structural form."""
    db = tmp_path / "lvr.db"
    _deleted_db(db)
    carved = [{"rowid": 55, "table": "msg"}, {"rowid": 60, "table": "msg"},
              {"rowid": 1, "table": "msg"}]  # rowid 1 IS live -> must not be counted
    items = detect_deletion_evidence(db, ["msg"], recovered_rows=carved)
    lvr = [i for i in items if i.mechanism == "live-vs-recovered"]
    assert lvr, "carved rowids 55 and 60 are absent from the live table"
    r = lvr[0]
    assert r.missing_count == 2
    assert r.first_missing_rowid == 55 and r.last_missing_rowid == 60
    assert r.details["missing_rowids"] == [55, 60]
    assert r.confidence == Confidence.DELETION_DETECTED.value


def test_live_vs_recovered_accepts_carvedrow_objects(tmp_path):
    """The pipeline passes CarvedRow objects as well as dicts; both must work."""
    db = tmp_path / "lvr2.db"
    _deleted_db(db)
    rows = recover_deleted_rows(db, "msg")
    items = detect_deletion_evidence(db, ["msg"], recovered_rows=rows)
    assert isinstance(items, list)
    # And a list of junk must not raise.
    assert isinstance(
        detect_deletion_evidence(db, ["msg"], recovered_rows=[None, 7, {"nope": 1}]),
        list,
    )


def test_without_rowid_table_is_detected_and_skipped_with_a_caveat(tmp_path):
    """A WITHOUT ROWID table has no rowid sequence; gap analysis would be fabrication."""
    db = tmp_path / "wor.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE kv(k TEXT PRIMARY KEY, v TEXT) WITHOUT ROWID")
    con.execute("INSERT INTO kv VALUES('a','1')")
    con.execute("INSERT INTO kv VALUES('z','2')")
    con.commit()
    con.close()

    items = detect_deletion_evidence(db)
    kv = [i for i in items if i.table == "kv"]
    assert kv, "the skipped table must still be reported, not silently dropped"
    rec = kv[0]
    assert rec.mechanism == "rowid-analysis-skipped"
    assert rec.confidence == NOT_APPLICABLE != Confidence.DELETION_DETECTED.value
    assert rec.details["skipped"] is True
    assert "WITHOUT ROWID" in rec.description
    assert rec.caveats and any("NOT evidence" in c for c in rec.caveats)
    # No deletion mechanism may be asserted for it.
    assert not [i for i in items if i.table == "kv" and i.confidence == "deletion"]


def test_every_record_carries_false_positive_causes(tmp_path):
    db = tmp_path / "fp.db"
    _deleted_db(db)
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE kv(k TEXT PRIMARY KEY, v TEXT) WITHOUT ROWID")
    con.commit()
    con.close()
    items = detect_deletion_evidence(db, recovered_rows=[{"rowid": 55, "table": "msg"}])
    assert len(items) >= 5
    for i in items:
        assert i.false_positive_causes, f"{i.mechanism} has no false-positive causes"
        assert i.caveats, f"{i.mechanism} has no caveats"
        assert i.description
        assert i.provenance
    # The named innocent explanations the honesty model requires are actually present.
    joined = " ".join(c for i in items for c in i.false_positive_causes).lower()
    for phrase in ("rolled-back", "vacuum", "migration", "autoincrement", "explicit insert"):
        assert phrase in joined, f"missing false-positive cause: {phrase}"


def test_summary_states_that_no_content_is_recovered(tmp_path):
    db = tmp_path / "sum.db"
    _deleted_db(db)
    items = detect_deletion_evidence(db, ["msg"])
    summary = deletion_evidence_summary(items)
    assert summary["total_findings"] >= 3
    assert summary["recovers_content"] is False
    assert summary["confidence"] == "deletion"
    text = summary["summary"].lower()
    assert "no content" in text
    assert "deleted" in text
    assert summary["by_mechanism"]["rowid-gap"] >= 1
    assert "msg" in summary["by_table"]
    assert summary["tables_affected"] == ["msg"]


def test_summary_excludes_skipped_tables_from_the_findings_count(tmp_path):
    db = tmp_path / "sum2.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE kv(k TEXT PRIMARY KEY, v TEXT) WITHOUT ROWID")
    con.execute("INSERT INTO kv VALUES('a','1')")
    con.commit()
    con.close()
    items = detect_deletion_evidence(db)
    summary = deletion_evidence_summary(items)
    assert summary["total_findings"] == 0
    assert summary["skipped_tables"]
    assert "no content" in summary["summary"].lower()


def test_summary_accepts_dicts_as_well_as_objects(tmp_path):
    db = tmp_path / "sum3.db"
    _deleted_db(db)
    items = detect_deletion_evidence(db, ["msg"])
    as_objects = deletion_evidence_summary(items)
    as_dicts = deletion_evidence_summary([i.to_dict() for i in items])
    assert as_objects["total_findings"] == as_dicts["total_findings"]
    assert as_objects["by_mechanism"] == as_dicts["by_mechanism"]
    assert deletion_evidence_summary([])["total_findings"] == 0


def test_evidence_json_round_trips(tmp_path):
    """to_dict() must be plain JSON-safe types for the report/dashboard."""
    db = tmp_path / "json.db"
    _deleted_db(db)
    items = detect_deletion_evidence(db, recovered_rows=[{"rowid": 55, "table": "msg"}])
    payload = {
        "evidence": [i.to_dict() for i in items],
        "summary": deletion_evidence_summary(items),
    }
    text = json.dumps(payload)
    back = json.loads(text)
    assert len(back["evidence"]) == len(items)
    for rec in back["evidence"]:
        assert rec["recovers_content"] is False
        assert isinstance(rec["false_positive_causes"], list)
        assert rec["confidence"] in ("deletion", NOT_APPLICABLE)


def test_missing_database_returns_empty_list(tmp_path):
    assert detect_deletion_evidence(tmp_path / "does_not_exist.db") == []
    assert detect_deletion_evidence(tmp_path) == []  # a directory, not a file


def test_corrupt_database_never_raises(tmp_path):
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"this is definitely not a sqlite file" * 40)
    assert detect_deletion_evidence(junk) == []

    truncated = tmp_path / "trunc.db"
    _deleted_db(truncated)
    raw = truncated.read_bytes()
    truncated.write_bytes(raw[: len(raw) // 3])
    assert isinstance(detect_deletion_evidence(truncated), list)


def test_table_with_no_gaps_yields_no_gap_records(tmp_path):
    db = tmp_path / "nogaps.db"
    _make_db(db, 1024, [("row one here",), ("row two here",), ("row three here",)])
    items = detect_deletion_evidence(db, ["t"])
    assert "rowid-gap" not in _mechanisms(items)
    assert "max-rowid-shortfall" not in _mechanisms(items)


def test_deletion_evidence_dataclass_defaults():
    ev = DeletionEvidence(db_file="a.db", table="t", mechanism="rowid-gap")
    assert ev.confidence == Confidence.DELETION_DETECTED.value == "deletion"
    d = ev.to_dict()
    assert d["recovers_content"] is False
    assert d["missing_count"] == 0
    json.dumps(d)


def test_detect_rowid_gaps_behaviour_is_unchanged(tmp_path):
    """The legacy dict-returning helper must keep its exact contract."""
    db = tmp_path / "legacy.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE messages(id INTEGER PRIMARY KEY, body TEXT)")
    for i in range(1, 6):
        con.execute("INSERT INTO messages(id, body) VALUES(?,?)", (i, f"b{i}"))
    con.commit()
    con.execute("DELETE FROM messages WHERE id IN (3,4)")
    con.commit()
    con.close()
    assert detect_rowid_gaps(db, "messages") == [
        {"after_rowid": 2, "before_rowid": 5, "missing": 2}
    ]
    assert detect_rowid_gaps(db, "no_such_table") == []


def test_deletion_evidence_and_carved_content_stay_separate(tmp_path):
    """recover_all must report the two classes side by side, never merged."""
    from triage.recovery.sqlite_recovery import recover_all

    db = tmp_path / "all.db"
    _deleted_db(db)
    out = recover_all(db)
    assert "carved" in out and "deletion_evidence" in out
    assert out["deletion_summary"]["recovers_content"] is False
    for rec in out["deletion_evidence"]:
        assert "values" not in rec, "deletion evidence must never carry row content"
    json.dumps(out)
