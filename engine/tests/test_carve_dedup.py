"""Freeblock carving must dedup by physical location, never by text alone.

In a deleted-message case the NUMBER of recovered copies and WHERE each copy sat
are both evidence: the count separates one deleted message from a message sent
repeatedly, and the page/offset is what an examiner seeks to in the image to
re-derive the carve.  Text-alone dedup destroyed both silently, upstream of every
analysis layer (design invariant 5: no dedup may discard a distinct artifact).

These tests build synthetic SQLite-shaped pages whose freeblock chains are laid
out byte-exactly, so the expected page/offset of every carve is known in advance.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import List, Sequence, Tuple

from triage.models import Message
from triage.parsers.whatsapp_e2e import (
    BTREE_FREEBLOCK_PTR_OFFSET,
    BTREE_PAGE_TYPE_OFFSET,
    FREEBLOCK_DATA_OFFSET,
    FREEBLOCK_NEXT_OFFSET,
    FREEBLOCK_SIZE_OFFSET,
    SQLITE_HEADER_MAGIC,
    SQLITE_PAGE_SIZE_HEADER_OFFSET,
    _carve_from_freeblocks,
    recover_e2e_messages,
)

PAGE_SIZE: int = 1024
TABLE_LEAF: int = 0x0D  # a page type the carver accepts (spec §3.9)

DELETED_TEXT: str = "MEET ME AT THE DOCK"
JID: str = "919876543210@s.whatsapp.net"

# (freeblock offset, freeblock size, payload offset within page, payload bytes).
# The payload offset is absolute within the page rather than relative to the
# freeblock, so a test can place bytes inside an *earlier* freeblock's span —
# which is how overlapping freeblocks genuinely occur in SQLite.
BlockSpec = Tuple[int, int, int, bytes]


def _make_freeblock_page(blocks: Sequence[BlockSpec]) -> bytearray:
    """Build one b-tree leaf page whose freeblock chain walks *blocks* in order."""
    page = bytearray(PAGE_SIZE)
    page[BTREE_PAGE_TYPE_OFFSET] = TABLE_LEAF
    struct.pack_into(">H", page, BTREE_FREEBLOCK_PTR_OFFSET, blocks[0][0])
    for idx, (fb_off, fb_size, _, _) in enumerate(blocks):
        next_off = blocks[idx + 1][0] if idx + 1 < len(blocks) else 0
        struct.pack_into(">H", page, fb_off + FREEBLOCK_NEXT_OFFSET, next_off)
        struct.pack_into(">H", page, fb_off + FREEBLOCK_SIZE_OFFSET, fb_size)
    for _, _, payload_off, payload in blocks:
        page[payload_off : payload_off + len(payload)] = payload
    return page


def _build_db(path: Path, pages: Sequence[Sequence[BlockSpec]]) -> Path:
    """Write a SQLite-shaped file whose page 1 is the header and 2..N carry *pages*.

    Page 1 is left as the bare file header because the carver reads the page type
    from byte 0 of every page and so skips it — mirroring a real database, where
    the b-tree header of page 1 sits after the 100-byte file header.
    """
    raw = bytearray(PAGE_SIZE * (len(pages) + 1))
    raw[0 : len(SQLITE_HEADER_MAGIC)] = SQLITE_HEADER_MAGIC
    struct.pack_into(">H", raw, SQLITE_PAGE_SIZE_HEADER_OFFSET, PAGE_SIZE)
    for page_idx, blocks in enumerate(pages, start=1):
        raw[page_idx * PAGE_SIZE : (page_idx + 1) * PAGE_SIZE] = _make_freeblock_page(
            blocks
        )
    path.write_bytes(bytes(raw))
    return path


def _file_offset(page_number: int, page_offset: int) -> int:
    """Absolute file offset of *page_offset* on 1-based SQLite page *page_number*."""
    return (page_number - 1) * PAGE_SIZE + page_offset


def _bodies(messages: List[Message], text: str) -> List[Message]:
    return [m for m in messages if m.body == text]


def test_identical_text_at_distinct_freeblocks_and_pages_all_survive(tmp_path: Path):
    """Three carves of one string from three locations must yield three messages."""
    payload = DELETED_TEXT.encode("utf-8")
    db = _build_db(
        tmp_path / "msgstore.db",
        pages=[
            # Page 2: two non-overlapping freeblocks holding the same text.
            [(100, 64, 104, payload), (300, 64, 304, payload)],
            # Page 3: the same text again, on a different page entirely.
            [(200, 64, 204, payload)],
        ],
    )

    carved = _bodies(_carve_from_freeblocks(db), DELETED_TEXT)

    expected_offsets = {
        _file_offset(2, 104),
        _file_offset(2, 304),
        _file_offset(3, 204),
    }
    assert len(carved) == 3, (
        "each freeblock recovery of the text is separate evidence; got "
        f"{[m.provenance for m in carved]}"
    )
    # Every expected location must be citable in the provenance an examiner reads.
    for offset in expected_offsets:
        assert any(
            f"@{offset}" in m.provenance for m in carved
        ), f"no carve cites offset {offset}: {[m.provenance for m in carved]}"

    # The cited offsets must be re-derivable: seeking there in the image finds
    # the exact bytes that were reported.
    raw = db.read_bytes()
    for offset in expected_offsets:
        assert raw[offset : offset + len(payload)] == payload

    # And the page number must match the offset it is cited with.
    for page_number, page_offset in ((2, 104), (2, 304), (3, 204)):
        cite = f"page {page_number}@{_file_offset(page_number, page_offset)}"
        assert any(cite in m.provenance for m in carved), cite


def test_same_offset_read_twice_by_overlapping_freeblocks_collapses(tmp_path: Path):
    """A re-read of one physical location is not new evidence and must collapse.

    Freeblock B starts *inside* freeblock A's span, so A's content and B's content
    both cover the payload bytes and the carver walks the same file offset twice.
    B's size is 0x0080 so its length field's low byte is not decodable text —
    otherwise it would join the payload run and shift A's carve to a different
    offset, which would be a genuinely different recovery rather than a re-read.
    """
    payload = DELETED_TEXT.encode("utf-8")
    db = _build_db(
        tmp_path / "msgstore.db",
        pages=[[(400, 200, 424, payload), (420, 0x0080, 424, payload)]],
    )

    carved = _bodies(_carve_from_freeblocks(db), DELETED_TEXT)

    assert len(carved) == 1, (
        "the same bytes re-walked at one offset are one recovery; got "
        f"{[m.provenance for m in carved]}"
    )
    assert f"page 2@{_file_offset(2, 424)}" in carved[0].provenance


def test_jid_references_at_distinct_freeblocks_all_survive(tmp_path: Path):
    """Each freeblock referencing a JID is a separate deleted row touching it."""
    payload = JID.encode("ascii")
    db = _build_db(
        tmp_path / "msgstore.db",
        pages=[[(100, 64, 104, payload), (300, 64, 304, payload)]],
    )

    jid_refs = [m for m in _carve_from_freeblocks(db) if "jid_only" in m.flags]

    assert len(jid_refs) == 2, (
        "collapsing JID references hides how many deleted rows named the contact; "
        f"got {[m.provenance for m in jid_refs]}"
    )
    assert {m.sender for m in jid_refs} == {JID.split("@")[0]}
    for page_offset in (104, 304):
        cite = f"page 2@{_file_offset(2, page_offset)}"
        assert any(cite in m.provenance for m in jid_refs), cite


def test_public_recovery_api_preserves_every_distinct_carve(tmp_path: Path):
    """The merge across techniques must not undo the location-aware dedup.

    ``recover_e2e_messages`` is what the pipeline calls, so a body+sender merge
    there would re-collapse the distinct carves before any analysis layer or
    report ever saw them.
    """
    payload = DELETED_TEXT.encode("utf-8")
    db = _build_db(
        tmp_path / "msgstore.db",
        pages=[[(100, 64, 104, payload), (300, 64, 304, payload)]],
    )

    recovered = _bodies(recover_e2e_messages(db), DELETED_TEXT)

    assert len(recovered) == 2, (
        "both carves must reach the caller; got "
        f"{[m.provenance for m in recovered]}"
    )
    assert len({m.provenance for m in recovered}) == 2
