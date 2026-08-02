"""A from-scratch SQLite forensic carver for deleted-record recovery.

This implements the recovery vectors that matter for messaging-app databases without
depending on an external tool (though `sqlite-dissect` can be layered on top later):

  1. **Freelist pages** — pages freed by DELETE are pushed onto the freelist; their old
     cell content survives until the page is reused. We walk the freelist and carve.
  2. **Freeblocks** — within a still-live leaf page, deleted cells leave freeblocks
     (a linked list of unallocated gaps). We carve those gaps.
  3. **Unallocated area** — the gap between the cell-pointer array and the cell content
     region on every leaf page can hold remnants of deleted cells.
  4. **WAL frames** — the -wal file holds full page images not yet checkpointed; an old
     version of a page (pre-deletion) may still be there. We parse every frame.
  5. **Rowid-gap detection** — even when content can't be carved, a gap in the
     AUTOINCREMENT/rowid sequence proves a deletion occurred (DFIR "gap analysis").
     See :func:`detect_deletion_evidence` for the full, confidence-tagged treatment of
     structural deletion evidence (rowid gaps, max-rowid shortfall, freelist pages,
     live-vs-recovered mismatch, sqlite_sequence shortfall).
  6. **Overflow-page chains** — a record whose payload does not fit on its b-tree page
     spills onto a linked list of overflow pages. Reassembling that chain is what makes
     long messages and large TEXT/BLOB columns come back *whole*; see
     :func:`read_overflow_chain`. When the chain cannot be completed (page reused,
     out of range, cyclic) the value is kept up to what was actually recovered, an
     explicit "overflow chain incomplete" warning is attached, and the row's confidence
     is downgraded to CARVED_PARTIAL — a silently truncated value must never be
     presented as verified.

Limitations
-----------
  * Overflow pages are resolved against the *current* page images. If an overflow page
    has already been reused by a later write, the tail of that value is gone for good;
    we report the truncation rather than guessing.
  * For WAL frames the overflow chain is resolved against a page map built from the WAL
    (latest frame per page) with the main database as a fallback. Page versions may
    therefore come from a different transaction than the frame being carved; rows whose
    value required that map carry an explicit warning saying so.

Confidence is assigned honestly:
  * LIVE                — read via the sqlite3 engine (accurate).
  * RECOVERED_VERIFIED  — carved from a freelist page or WAL frame, header fully intact,
                          column count matches the schema, no ambiguity.
  * CARVED_PARTIAL      — carved from a freeblock / unallocated gap, or with a column
                          count / serial-type mismatch: may be corrupt or overlapping.
  * DELETION_DETECTED   — a rowid gap proves deletion but no content was recovered.

The parser is defensive: any malformed structure is skipped, never raised, so a hostile
or corrupt database can't crash the acquisition.
"""

from __future__ import annotations

import sqlite3
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..config import Confidence

# SQLite b-tree page types (first byte of each page's b-tree header).
_LEAF_TABLE = 0x0D
_INTERIOR_TABLE = 0x05
_HEADER_MAGIC = b"SQLite format 3\x00"
# Rollback-journal header magic (SQLite file-format §rollback journal).
_JOURNAL_MAGIC = b"\xd9\xd5\x05\xf9\x20\xa1\x63\xd7"


# --- varint -----------------------------------------------------------------
def _read_varint(buf: bytes, off: int) -> tuple[int, int]:
    """Decode a SQLite varint at buf[off]. Return (value, bytes_consumed).

    SQLite varints are big-endian, up to 9 bytes; the first 8 bytes contribute 7 bits
    each (high bit = continuation), and a 9th byte contributes all 8 bits.
    """
    result = 0
    for i in range(9):
        if off + i >= len(buf):
            return result, i
        byte = buf[off + i]
        if i == 8:
            result = (result << 8) | byte
            return result, 9
        result = (result << 7) | (byte & 0x7F)
        if not (byte & 0x80):
            return result, i + 1
    return result, 9


# --- serial-type value decoding --------------------------------------------
def _serial_size(serial: int) -> int:
    if serial < 0:
        return 0
    if serial <= 4:
        return serial
    if serial == 5:
        return 6
    if serial == 6 or serial == 7:
        return 8
    if serial in (8, 9):
        return 0
    if serial >= 12:
        return (serial - 12) // 2 if serial % 2 == 0 else (serial - 13) // 2
    return 0


def _decode_value(serial: int, data: bytes) -> Any:
    if serial == 0:
        return None
    if serial == 1:
        return int.from_bytes(data, "big", signed=True)
    if serial == 2:
        return int.from_bytes(data, "big", signed=True)
    if serial == 3:
        return int.from_bytes(data, "big", signed=True)
    if serial == 4:
        return int.from_bytes(data, "big", signed=True)
    if serial == 5:
        return int.from_bytes(data, "big", signed=True)
    if serial == 6:
        return int.from_bytes(data, "big", signed=True)
    if serial == 7:
        return struct.unpack(">d", data)[0] if len(data) == 8 else None
    if serial == 8:
        return 0
    if serial == 9:
        return 1
    if serial >= 12 and serial % 2 == 0:  # BLOB
        return data
    if serial >= 13:  # TEXT (UTF-8)
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data.decode("utf-8", "replace")
    return None


# --- overflow-page chains (P1-6) --------------------------------------------
# A table-leaf cell stores only part of a large payload on its own page; the rest lives
# on a singly-linked list of "overflow" pages. Without following that list every long
# message and every large TEXT/BLOB column comes back silently truncated — which is both
# an evidence-loss bug and an honesty-model violation (a truncated value presented as a
# complete one). The spill maths below is SQLite's, verbatim (file-format §1.6).

# Hard ceiling on chain hops. A 2 GiB payload at a 512-byte page size is ~4M pages; this
# ceiling is far above any real record and exists purely so a hostile/corrupt file can
# never make us walk forever.
_MAX_OVERFLOW_HOPS = 200_000

# SQLite's own default SQLITE_MAX_LENGTH. A declared payload above this is not a record.
_MAX_SPILLED_PAYLOAD = 1_000_000_000


def overflow_thresholds(page_size: int, reserved: int = 0) -> dict[str, int]:
    """Return the SQLite table-b-tree-leaf payload spill thresholds for this geometry.

    ``usable`` = page_size - reserved (the reserved-space byte lives at file-header
    offset 20). ``max_local`` (X) is the largest payload that stays entirely on the
    page; ``min_local`` (M) is the smallest amount SQLite will ever keep on-page for a
    spilled record. Exposed because examiners (and tests) need to reason about *why* a
    given record did or did not spill.
    """
    usable = int(page_size) - int(reserved)
    return {
        "usable": usable,
        "max_local": usable - 35,  # X
        "min_local": ((usable - 12) * 32 // 255) - 23,  # M
    }


def local_payload_size(payload_len: int, page_size: int, reserved: int = 0) -> int:
    """How many payload bytes of a table-leaf cell live on the b-tree page itself.

    If the return value equals ``payload_len`` the record is entirely on-page. Otherwise
    the on-page bytes are followed by a 4-byte big-endian overflow page number and the
    remaining ``payload_len - local`` bytes follow the overflow chain.
    """
    usable = int(page_size) - int(reserved)
    if usable < 48 or usable - 4 <= 0:
        # Degenerate geometry (corrupt header). Don't invent a spill point.
        return payload_len
    x = usable - 35
    if payload_len <= x:
        return payload_len
    m = ((usable - 12) * 32 // 255) - 23
    if m <= 0:
        return payload_len
    k = m + ((payload_len - m) % (usable - 4))
    return k if k <= x else m


def _follow_overflow(
    get_page: Any,
    page_size: int,
    first_page: int,
    needed: int,
    reserved: int = 0,
) -> tuple[bytes, dict[str, Any]]:
    """Walk an overflow chain using ``get_page(n) -> bytes | None``.

    Never raises and never loops forever: it stops on next==0, on an unavailable /
    out-of-range page, on a page already visited (cycle guard) and on a hop ceiling.
    """
    status: dict[str, Any] = {
        "complete": False,
        "pages_read": 0,
        "reason": "",
        "truncated_bytes": 0,
    }
    out = bytearray()
    if needed <= 0:
        status["complete"] = True
        status["reason"] = "no overflow required"
        return bytes(out), status

    usable = int(page_size) - int(reserved)
    cap = usable - 4  # content bytes per overflow page
    if cap <= 0:
        status["reason"] = "invalid page geometry (usable size <= 4)"
        status["truncated_bytes"] = needed
        return bytes(out), status

    # Generous but bounded: the number of pages the payload could legitimately need,
    # plus slack, capped by the global ceiling.
    max_hops = min((needed // cap) + 8, _MAX_OVERFLOW_HOPS)

    seen: set[int] = set()
    page_no = int(first_page)
    reason = ""
    while True:
        if len(out) >= needed:
            break
        if page_no == 0:
            reason = "chain terminated early (next page number 0)"
            break
        if page_no < 0:
            reason = f"invalid overflow page number {page_no}"
            break
        if page_no in seen:
            reason = f"cyclic overflow chain detected at page {page_no}"
            break
        if len(seen) >= max_hops:
            reason = f"overflow hop ceiling reached after {len(seen)} pages"
            break
        try:
            page = get_page(page_no)
        except Exception:  # a hostile page source must not break acquisition
            page = None
        if page is None or len(page) < 8:
            reason = (
                f"overflow page {page_no} unavailable "
                "(out of range, truncated file, or reused by a later write)"
            )
            break
        seen.add(page_no)
        try:
            nxt = int.from_bytes(page[0:4], "big")
        except Exception:
            reason = f"unreadable overflow pointer on page {page_no}"
            break
        chunk = page[4 : 4 + cap]
        if not chunk:
            reason = f"overflow page {page_no} carried no content"
            break
        out += chunk[: needed - len(out)]
        status["pages_read"] = len(seen)
        page_no = nxt

    if len(out) >= needed:
        status["complete"] = True
        status["reason"] = "chain fully reassembled"
        status["truncated_bytes"] = 0
    else:
        status["complete"] = False
        status["reason"] = reason or "chain ended before the payload was complete"
        status["truncated_bytes"] = needed - len(out)
    return bytes(out), status


def read_overflow_chain(
    data: bytes,
    page_size: int,
    first_page: int,
    needed: int,
    reserved: int = 0,
) -> tuple[bytes, dict[str, Any]]:
    """Reassemble ``needed`` payload bytes from the overflow chain starting at
    ``first_page`` inside the page image ``data``.

    Returns ``(payload_bytes, status)`` where status is
    ``{"complete": bool, "pages_read": int, "reason": str, "truncated_bytes": int}``.
    ``complete`` False means the caller MUST treat the value as truncated: keep what was
    recovered, say so, and downgrade the row's confidence.
    """
    ps = int(page_size)

    def _get(n: int) -> Optional[bytes]:
        if n < 1 or ps <= 0:
            return None
        start = (n - 1) * ps
        if start < 0 or start + ps > len(data):
            return None  # out of range: honestly unavailable, not silently zero-filled
        return data[start : start + ps]

    return _follow_overflow(_get, ps, first_page, needed, reserved)


@dataclass
class OverflowContext:
    """Where a carver may look for the overflow pages of a record it is parsing.

    ``page_map`` (e.g. WAL frames or rollback-journal pre-images) takes priority over the
    flat ``data`` image; ``note`` is surfaced as a row warning whenever the map actually
    had to be used, because a page pulled from a different transaction is a real caveat.
    """

    page_size: int
    reserved: int = 0
    data: Optional[bytes] = None
    page_map: Optional[dict[int, bytes]] = None
    note: str = ""

    def page(self, n: int) -> Optional[bytes]:
        if n < 1 or self.page_size <= 0:
            return None
        if self.page_map:
            p = self.page_map.get(n)
            if p is not None and len(p) >= self.page_size:
                return p[: self.page_size]
        if self.data is not None:
            start = (n - 1) * self.page_size
            if 0 <= start and start + self.page_size <= len(self.data):
                return self.data[start : start + self.page_size]
        return None


def _db_geometry(data: bytes) -> tuple[int, int]:
    """(page_size, reserved_space) from a SQLite file header. (0, 0) if not a database."""
    if len(data) < 100 or data[:16] != _HEADER_MAGIC:
        return (0, 0)
    page_size = struct.unpack(">H", data[16:18])[0]
    if page_size == 1:
        page_size = 65536
    reserved = data[20]
    if reserved >= page_size:
        reserved = 0  # nonsense header value; don't propagate it into the spill maths
    return (page_size, reserved)


@dataclass
class CarvedRow:
    """One recovered/live record with full provenance for the analyst."""

    values: list[Any]
    confidence: Confidence
    source_file: str
    provenance: str  # e.g. "freelist page 7", "wal frame 3", "freeblock p4@1832"
    rowid: Optional[int] = None
    page: Optional[int] = None
    offset: Optional[int] = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "values": [_jsonable(v) for v in self.values],
            "confidence": self.confidence.value,
            "source_file": self.source_file,
            "provenance": self.provenance,
            "rowid": self.rowid,
            "page": self.page,
            "offset": self.offset,
            "warnings": self.warnings,
        }


def _jsonable(v: Any) -> Any:
    if isinstance(v, bytes):
        # Show short blobs as hex; truncate long ones.
        return {"__blob__": v[:64].hex(), "len": len(v)}
    return v


# --- record parsing ---------------------------------------------------------
def _parse_record(payload: bytes, allow_partial_tail: bool = False) -> Optional[list[Any]]:
    """Parse a SQLite record (the payload after payload-len/rowid). Return column values
    or None if the header is inconsistent with the body length.

    ``allow_partial_tail`` is set only when we already KNOW the payload was cut short by
    an incomplete overflow chain. In that case the last column is decoded from whatever
    bytes survived instead of being thrown away as None — the caller is responsible for
    attaching the truncation warning and downgrading the row's confidence. Default False
    keeps the historic behaviour for every other call site.
    """
    if not payload:
        return None
    header_len, consumed = _read_varint(payload, 0)
    if header_len <= 0 or header_len > len(payload):
        return None
    serials: list[int] = []
    pos = consumed
    while pos < header_len:
        serial, used = _read_varint(payload, pos)
        if used == 0:
            return None
        serials.append(serial)
        pos += used
    values: list[Any] = []
    body = header_len
    for serial in serials:
        size = _serial_size(serial)
        if body + size > len(payload):
            # Body shorter than header claims — truncated/overwritten record.
            frag = payload[body:] if body < len(payload) else b""
            if allow_partial_tail and frag and serial >= 12:
                # Keep the bytes we actually have for the truncated TEXT/BLOB column.
                values.append(_decode_value(serial, frag))
            else:
                values.append(None)
            body += size
            continue
        values.append(_decode_value(serial, payload[body : body + size]))
        body += size
    return values


def _record_declared_size(payload: bytes) -> Optional[int]:
    """Total size a record's own header claims: header_len + sum(serial sizes).

    Used as the acceptance test for a SPILLED cell. For a genuine record this equals the
    cell's payload-length varint exactly, so requiring the match rejects essentially all
    of the random byte sequences a sliding carver walks over — without it, allowing
    payload_len to exceed the page (which a spilled cell must) would flood the output with
    invented "truncated overflow" rows.
    """
    header_len, consumed = _read_varint(payload, 0)
    if header_len <= 0 or header_len > len(payload):
        return None
    total = header_len
    pos = consumed
    while pos < header_len:
        serial, used = _read_varint(payload, pos)
        if used == 0:
            return None
        total += _serial_size(serial)
        pos += used
    return total


def _has_content(values: list[Any]) -> bool:
    """A carve is worth surfacing only if it holds at least one non-trivial value —
    a decodable string of length >= 2 or a non-zero number. Filters all-None noise."""
    for v in values:
        if isinstance(v, str) and len(v.strip()) >= 2:
            return True
        if isinstance(v, (int, float)) and v not in (0, 1):
            return True
        if isinstance(v, bytes) and len(v) >= 2:
            return True
    return False


@dataclass
class _CellCarve:
    """Internal result of parsing one table-leaf cell (with its overflow chain)."""

    rowid: int
    payload_len: int
    values: list[Any]
    clean: bool
    consumed: int  # on-page bytes this cell occupies (varints + local payload [+ ptr])
    advance: int  # how far the sliding carver should jump after a clean hit
    warnings: list[str] = field(default_factory=list)
    overflow: Optional[dict[str, Any]] = None


def _try_carve_cell(
    buf: bytes,
    off: int,
    expected_cols: Optional[int],
    ovf: Optional[OverflowContext] = None,
) -> Optional[_CellCarve]:
    """Attempt to parse a table-leaf cell starting at buf[off].

    Returns a :class:`_CellCarve` or None. ``clean`` is True only when the payload was
    fully reassembled (on-page, or on-page + a COMPLETE overflow chain) and — when known
    — the column count matches the schema. A record whose overflow chain could not be
    completed comes back with ``clean=False``, an explicit truncation warning and the
    surviving prefix of the affected value, so the caller downgrades it to
    CARVED_PARTIAL instead of publishing a silently short value as verified.
    """
    try:
        payload_len, c1 = _read_varint(buf, off)
        # A cell that SPILLS legitimately declares more payload than fits on its page, so
        # the historic "must fit in the buffer" guard is only correct when we have no
        # overflow context. With one, allow up to SQLite's own record-length ceiling and
        # rely on the exact header/size cross-check below to reject noise.
        max_payload = len(buf) if ovf is None else _MAX_SPILLED_PAYLOAD
        if payload_len <= 0 or payload_len > max_payload:
            return None
        rowid, c2 = _read_varint(buf, off + c1)
        rec_start = off + c1 + c2
        if rec_start >= len(buf):
            return None
        avail = len(buf) - rec_start

        warnings: list[str] = []
        ovf_status: Optional[dict[str, Any]] = None
        allow_partial = False

        local = payload_len
        if ovf is not None and ovf.page_size > 0:
            local = local_payload_size(payload_len, ovf.page_size, ovf.reserved)

        if local >= payload_len:
            # Everything is on this page (the historic path).
            take = min(payload_len, avail)
            record = buf[rec_start : rec_start + take]
            consumed = c1 + c2 + take
            # Historic (deliberately conservative) advance: payload_len, not the true
            # cell size. Keeping it means the sliding carver still re-examines the few
            # bytes of the following cell header, which is where several existing
            # freeblock recoveries come from. Do not "fix" this without re-baselining.
            advance = payload_len
            payload_complete = take == payload_len
            if payload_complete and _record_declared_size(record) != payload_len:
                # The record header's own serial-type sum doesn't add up to the
                # declared payload length: this offset is not a genuine cell
                # boundary, just bytes that happen to parse as a plausible-looking
                # header (common right after a freeblock header clobbers the real
                # one). Reject rather than emit a garbled/misaligned row.
                return None
        else:
            # The record spilled: [local payload][4-byte big-endian overflow page no].
            take = min(local, avail)
            head = buf[rec_start : rec_start + take]
            consumed = c1 + c2 + local + 4
            # A spilled cell only occupies `consumed` bytes on this page — advancing by
            # the (much larger) declared payload_len would jump clean off the page.
            advance = consumed
            ptr_off = rec_start + local
            if take < local or ptr_off + 4 > len(buf):
                # A real spilled cell always carries its full local payload AND its
                # 4-byte overflow pointer on this page. If they don't fit, this offset is
                # not a cell — reject it rather than invent a truncated record.
                return None
            # Exact structural cross-check: the record header must account for exactly
            # payload_len bytes. This is what makes it safe to trust a >page-size
            # payload_len found by a byte-sliding carver.
            if _record_declared_size(head) != payload_len:
                return None
            first_page = int.from_bytes(buf[ptr_off : ptr_off + 4], "big")
            tail, ovf_status = _follow_overflow(
                ovf.page, ovf.page_size, first_page, payload_len - local, ovf.reserved
            )
            record = head + tail
            if ovf.note and ovf_status.get("pages_read"):
                warnings.append(ovf.note)
            payload_complete = bool(ovf_status.get("complete"))
            if not payload_complete:
                allow_partial = True
                warnings.append(
                    f"overflow chain incomplete — value truncated at {len(record)} bytes "
                    f"of {payload_len} declared "
                    f"({ovf_status.get('reason') or 'unknown reason'}); the affected "
                    "column holds only the bytes actually recovered"
                )

        values = _parse_record(record, allow_partial_tail=allow_partial)
        if values is None or not values:
            return None
        # Reject noise: a real record has content, not a wall of NULLs.
        if not _has_content(values):
            return None
        if sum(1 for v in values if v is None) > len(values) * 0.6:
            return None
        clean = payload_complete
        if expected_cols is not None and len(values) != expected_cols:
            clean = False
        if rowid < 0 or rowid > (1 << 48):
            return None
        return _CellCarve(
            rowid=rowid,
            payload_len=payload_len,
            values=values,
            clean=clean,
            consumed=max(consumed, 1),
            advance=max(advance, 1),
            warnings=warnings,
            overflow=ovf_status,
        )
    except Exception:
        return None


# Known field-name prefixes that may survive in the raw byte stream before a
# message-text value.  When we find one of these, we anchor the recovered text
# to start *after* the '=' so we emit only the payload, not the column name.
_TEXT_FIELD_PREFIXES: tuple[bytes, ...] = (
    b"data=",
    b"body=",
    b"msg=",
    b"text=",
    b"content=",
    b"message=",
)


def _strip_field_prefix(raw: str) -> str:
    """If a text run starts with a known SQLite column-name prefix (e.g. 'data=VALUE'),
    return only the value portion.  Only fires when the prefix appears at the very
    beginning of the run (case-insensitive), so legitimate body text that happens to
    contain the word 'message' or 'text' is never truncated."""
    lower = raw.lower()
    for prefix in _TEXT_FIELD_PREFIXES:
        p = prefix.decode()
        if lower.startswith(p):
            return raw[len(p) :]
    return raw


def _carve_text_runs(
    page: bytes,
    page_abs_base: int,
    region_off: int,
    region_len: int,
    source_file: str,
    page_num: int,
    kind: str,
    min_len: int = 4,
) -> list[CarvedRow]:
    """Extract printable UTF-8 text runs from a deleted region (freeblock / unallocated).

    When a cell is freed in-page, the 4-byte freeblock header clobbers the payload-length
    and rowid, so the record can't be structurally reparsed — but the message *text* after
    those bytes survives intact. Recovering it as a labelled text fragment is honest,
    reliable, and exactly the evidentiary content a triage officer cares about. Always
    CARVED_PARTIAL: the surrounding record structure is gone, so field boundaries and any
    adjacent record are unverified.

    Prefix anchoring
    ~~~~~~~~~~~~~~~~
    If a run contains a known column-name prefix (``data=``, ``body=``, ``msg=``, etc.),
    only the value after the ``=`` is captured as the recovered message text.  This
    significantly reduces noise (column-name bleed-through is a common artefact when the
    record header and payload bytes span a freed region).
    """
    rows: list[CarvedRow] = []
    end = min(region_off + region_len, len(page))
    i = region_off
    run_start = -1
    run = bytearray()
    # Start offset (within `run`) of a multi-byte UTF-8 sequence still being
    # assembled, and how many continuation bytes it still needs. A raw byte-slide
    # carver walks straight through a freed cell's trailing binary columns (e.g. a
    # 4-byte big-endian timestamp) right after the text column ends; naively
    # accepting "any byte >= 0x80" as text lets a few of those numeric bytes leak
    # onto the end of an otherwise-correct value. Requiring each multi-byte
    # sequence to be STRUCTURALLY valid UTF-8 (a lead byte followed by exactly the
    # right count of 0x80-0xBF continuation bytes) rejects that noise without any
    # risk of cutting real text short — genuine UTF-8 content always validates.
    pending_start = -1
    pending_needed = 0

    def _flush(rstart: int) -> None:
        if len(run) < min_len:
            return
        try:
            text = run.decode("utf-8")
        except UnicodeDecodeError:
            text = run.decode("utf-8", "ignore")
        # Require the run to be mostly real printable characters, not control soup.
        printable = sum(1 for ch in text if ch.isprintable())
        if not (len(text) >= min_len and printable >= len(text) * 0.8 and text.strip()):
            return
        # Prefix-anchor: strip leading field names so only the value is captured.
        anchored = _strip_field_prefix(text).strip()
        if len(anchored) < min_len:
            return
        rows.append(
            CarvedRow(
                values=[anchored],
                confidence=Confidence.CARVED_PARTIAL,
                source_file=source_file,
                page=page_num,
                offset=page_abs_base + rstart,
                provenance=f"{kind} page {page_num}@{rstart} (text carve)",
                warnings=[
                    "Recovered text fragment from unallocated space; record structure may be "
                    "incomplete or span multiple records. Do not treat as a standalone message "
                    "without corroboration."
                ],
            )
        )

    while i < end:
        b = page[i]
        if pending_needed:
            if 0x80 <= b <= 0xBF:
                run.append(b)
                pending_needed -= 1
                if pending_needed == 0:
                    pending_start = -1
                i += 1
                continue
            # Broken multi-byte sequence: the lead byte (and any partial
            # continuations already appended) were never real text. Drop them,
            # end the run there, and reprocess this byte fresh below.
            del run[pending_start:]
            pending_needed = 0
            pending_start = -1
            _flush(run_start)
            run = bytearray()
            run_start = -1
            continue
        if b in (0x09, 0x0A, 0x0D) or 0x20 <= b <= 0x7E:
            if run_start < 0:
                run_start = i
            run.append(b)
        elif 0xC2 <= b <= 0xF4:
            # Valid UTF-8 lead byte: 0xC2-0xDF (2-byte), 0xE0-0xEF (3-byte),
            # 0xF0-0xF4 (4-byte). Provisionally accepted; verified by the
            # continuation-byte check above before it's kept.
            if run_start < 0:
                run_start = i
            pending_start = len(run)
            pending_needed = 1 if b <= 0xDF else (2 if b <= 0xEF else 3)
            run.append(b)
        else:
            # Control byte, or 0x80-0xC1/0xF5-0xFF — never valid outside a
            # continuation. Ends the run.
            _flush(run_start)
            run = bytearray()
            run_start = -1
        i += 1
    if pending_needed:
        # Region ended mid-sequence: incomplete, so drop it rather than emit a
        # truncated multi-byte character.
        del run[pending_start:]
    _flush(run_start)
    return rows


# --- page-level helpers -----------------------------------------------------
def _page_bytes(data: bytes, page_size: int, page_num: int) -> bytes:
    start = (page_num - 1) * page_size
    return data[start : start + page_size]


def _btree_header_offset(page_num: int) -> int:
    # Page 1 has the 100-byte file header before its b-tree header.
    return 100 if page_num == 1 else 0


def _live_cell_offsets(page: bytes, hdr_off: int) -> list[int]:
    """Return the cell-content offsets from a leaf page's cell-pointer array."""
    if hdr_off + 8 > len(page):
        return []
    ncells = struct.unpack(">H", page[hdr_off + 3 : hdr_off + 5])[0]
    ptr_start = hdr_off + 8  # table-leaf header is 8 bytes
    offsets = []
    for i in range(ncells):
        p = ptr_start + i * 2
        if p + 2 > len(page):
            break
        offsets.append(struct.unpack(">H", page[p : p + 2])[0])
    return offsets


def _freeblock_regions(page: bytes, hdr_off: int) -> list[tuple[int, int]]:
    """Walk the freeblock linked list; return [(offset, size)] gaps of deleted cells."""
    regions = []
    if hdr_off + 2 > len(page):
        return regions
    fb = struct.unpack(">H", page[hdr_off + 1 : hdr_off + 3])[0]
    seen = set()
    while fb and fb not in seen and fb + 4 <= len(page):
        seen.add(fb)
        nxt = struct.unpack(">H", page[fb : fb + 2])[0]
        size = struct.unpack(">H", page[fb + 2 : fb + 4])[0]
        regions.append((fb, max(size, 4)))
        fb = nxt
    return regions


def _unallocated_region(page: bytes, hdr_off: int) -> tuple[int, int]:
    """The gap between the end of the cell-pointer array and the cell content area."""
    if hdr_off + 8 > len(page):
        return (0, 0)
    ncells = struct.unpack(">H", page[hdr_off + 3 : hdr_off + 5])[0]
    content_start = struct.unpack(">H", page[hdr_off + 5 : hdr_off + 7])[0]
    if content_start == 0:
        content_start = len(page)
    ptr_end = hdr_off + 8 + ncells * 2
    if content_start > ptr_end:
        return (ptr_end, content_start - ptr_end)
    return (0, 0)


def _carve_region(
    page: bytes,
    page_abs_base: int,
    region_off: int,
    region_len: int,
    expected_cols: Optional[int],
    source_file: str,
    page_num: int,
    provenance_kind: str,
    confidence: Confidence,
    seen_records: set[tuple[int, tuple[Any, ...]]],
    ovf: Optional[OverflowContext] = None,
) -> list[CarvedRow]:
    """Slide through a byte region attempting to carve table-leaf cells."""
    rows: list[CarvedRow] = []
    end = min(region_off + region_len, len(page))
    off = region_off
    while off < end:
        carved = _try_carve_cell(page, off, expected_cols, ovf)
        if carved:
            rec_key = (carved.rowid, tuple(carved.values))
            if rec_key not in seen_records or provenance_kind == "freeblock":
                conf = confidence if carved.clean else Confidence.CARVED_PARTIAL
                warnings = list(carved.warnings)
                if not carved.clean and not warnings:
                    warnings = ["header/column mismatch or truncated payload"]
                rows.append(
                    CarvedRow(
                        values=carved.values,
                        confidence=conf,
                        source_file=source_file,
                        provenance=f"{provenance_kind} page {page_num}@{off}",
                        rowid=carved.rowid,
                        page=page_num,
                        offset=page_abs_base + off,
                        warnings=warnings,
                    )
                )
                seen_records.add(rec_key)
                # Advance past this record's declared payload only if the parse was clean.
                # If unclean, the payload_len might be garbage, so advance by 1 to avoid skipping valid data.
                off += max(carved.advance, 1) if carved.clean else 1
                continue
        off += 1
    return rows


# --- freelist ---------------------------------------------------------------
def _freelist_pages(data: bytes, page_size: int) -> list[int]:
    """Return the page numbers on the freelist (trunk pages + their leaf entries)."""
    if len(data) < 40:
        return []
    first_trunk = struct.unpack(">I", data[32:36])[0]
    pages: list[int] = []
    trunk = first_trunk
    guard = 0
    while trunk and guard < 100000:
        guard += 1
        page = _page_bytes(data, page_size, trunk)
        if len(page) < 8:
            break
        next_trunk = struct.unpack(">I", page[0:4])[0]
        nleaf = struct.unpack(">I", page[4:8])[0]
        for i in range(min(nleaf, (page_size - 8) // 4)):
            p = 8 + i * 4
            leaf = struct.unpack(">I", page[p : p + 4])[0]
            if leaf:
                pages.append(leaf)
        pages.append(trunk)
        trunk = next_trunk
    return pages


# --- public API -------------------------------------------------------------
def _schema_tables(db_path: Path) -> dict[str, dict[str, Any]]:
    """Return {table_name: {"columns": [...], "col_count": n}} via the sqlite3 engine."""
    out: dict[str, dict[str, Any]] = {}
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'"
        )
        for (name,) in cur.fetchall():
            cols = con.execute(f"PRAGMA table_info('{name}')").fetchall()
            out[name] = {
                "columns": [c["name"] for c in cols],
                "col_count": len(cols),
            }
        con.close()
    except sqlite3.Error:
        pass
    return out


# Column names recovered during the most recent read_live_rows() call, keyed by
# (db filename, table) so a caller can label the positional value lists.
rows_meta_colnames: dict[tuple[str, str], list[str]] = {}


def read_live_rows(
    db_path: str | Path, table: str, limit: int = 100000
) -> list[CarvedRow]:
    """Read live rows via the sqlite3 engine (authoritative)."""
    db_path = Path(db_path)
    rows: list[CarvedRow] = []
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cur = con.execute(f"SELECT rowid, * FROM '{table}' LIMIT {int(limit)}")
        colnames = [d[0] for d in cur.description]
        for r in cur.fetchall():
            rows.append(
                CarvedRow(
                    values=list(r[1:]),
                    confidence=Confidence.LIVE,
                    source_file=db_path.name,
                    provenance="live query",
                    rowid=r[0],
                )
            )
        con.close()
        rows_meta_colnames[(db_path.name, table)] = colnames[1:]
    except sqlite3.Error:
        pass
    return rows


def read_page_cells(
    db_path: str | Path,
    page_num: int,
    *,
    expected_cols: Optional[int] = None,
    confidence: Confidence = Confidence.RECOVERED_VERIFIED,
) -> list[CarvedRow]:
    """Parse the *allocated* cells of one table-leaf page straight from the raw bytes,
    following every overflow chain.

    This is the ground-truth path used to prove the overflow reassembly is byte-correct:
    a value that spans several overflow pages must come back identical to what the
    sqlite3 engine returns. It is deliberately NOT labelled ``LIVE`` — LIVE is reserved
    for rows read through the SQLite engine itself; these are structurally reassembled
    from disk, so a record whose chain could not be completed downgrades to
    CARVED_PARTIAL and says why.
    """
    db_path = Path(db_path)
    try:
        data = db_path.read_bytes()
    except OSError:
        return []
    page_size, reserved = _db_geometry(data)
    if page_size <= 0:
        return []
    page = _page_bytes(data, page_size, int(page_num))
    hdr_off = _btree_header_offset(int(page_num))
    if len(page) < page_size or hdr_off >= len(page) or page[hdr_off] != _LEAF_TABLE:
        return []
    ovf = OverflowContext(page_size=page_size, reserved=reserved, data=data)
    rows: list[CarvedRow] = []
    for off in _live_cell_offsets(page, hdr_off):
        carved = _try_carve_cell(page, off, expected_cols, ovf)
        if not carved:
            continue
        conf = confidence if carved.clean else Confidence.CARVED_PARTIAL
        warnings = list(carved.warnings)
        if not carved.clean and not warnings:
            warnings = ["header/column mismatch or truncated payload"]
        rows.append(
            CarvedRow(
                values=carved.values,
                confidence=conf,
                source_file=db_path.name,
                provenance=f"allocated cell page {page_num}@{off} (raw parse)",
                rowid=carved.rowid,
                page=int(page_num),
                offset=(int(page_num) - 1) * page_size + off,
                warnings=warnings,
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Column-mapping helper for WhatsApp msgstore.db
# ---------------------------------------------------------------------------

# Canonical column order for the WhatsApp ``message`` table (typical Android build).
# This is used to map positional CarvedRow values to named fields.
_WA_MESSAGE_COLUMNS: list[str] = [
    "_id",
    "key_remote_jid",
    "key_from_me",
    "key_id",
    "status",
    "needs_push",
    "data",
    "timestamp",
    "media_url",
    "media_mime_type",
    "media_wa_type",
    "media_size",
    "media_name",
    "media_caption",
    "media_hash",
    "media_duration",
    "origin",
    "latitude",
    "longitude",
    "thumb_image",
    "remote_resource",
    "received_timestamp",
    "send_timestamp",
    "receipt_server_timestamp",
    "receipt_device_timestamp",
    "read_device_timestamp",
    "played_device_timestamp",
    "raw_data",
    "starred",
    "quoted_row_id",
    "mentioned_jids",
    "multicast_id",
    "edit_version",
    "media_enc_hash",
    "payment_transaction_id",
    "forwarded",
    "sender_jid",
]


def map_columns_to_whatsapp(
    row: "CarvedRow",
    columns: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Map the positional values in a ``CarvedRow`` to named WhatsApp ``message`` fields.

    Parameters
    ----------
    row:
        A carved (or live) row whose ``values`` list is positionally ordered.
    columns:
        The ordered column names for this row (from ``rows_meta_colnames`` or a
        ``schema_hint``).  Defaults to ``_WA_MESSAGE_COLUMNS`` if None.

    Returns
    -------
    dict mapping column name → value for every column that is present.
    The dict always includes ``data`` (message body text) and ``timestamp``
    (epoch ms), even if they map to ``None``.
    """
    cols = columns or _WA_MESSAGE_COLUMNS
    out: dict[str, Any] = {"data": None, "timestamp": None}
    for i, col in enumerate(cols):
        if i < len(row.values):
            out[col] = row.values[i]
        else:
            out[col] = None
    return out


def recover_deleted_rows(
    db_path: str | Path,
    table: Optional[str] = None,
    schema_hint: Optional[dict[str, Any]] = None,
) -> list[CarvedRow]:
    """Carve deleted/old rows from freelist pages, freeblocks, unallocated space, and
    the -wal file.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file.
    table:
        If given, the table whose column count is used to validate carves.
        Also populates ``rows_meta_colnames`` so callers can map columns by name.
    schema_hint:
        Optional dict with database-specific hints.  For WhatsApp, pass::

            {
                "col_count": 16,            # exact column count of the target table
                "columns": ["_id", ...],   # ordered column names
            }

        When provided, ``col_count`` overrides the schema-introspection result for
        ``expected_cols``, and ``columns`` is stored in ``rows_meta_colnames`` so
        the pipeline can use ``map_columns_to_whatsapp`` to build rich Message objects.
    """
    db_path = Path(db_path)
    data = db_path.read_bytes()
    if len(data) < 100 or data[:16] != _HEADER_MAGIC:
        return []
    page_size, reserved = _db_geometry(data)
    if page_size <= 0:
        return []
    # Overflow pages for records carved out of THIS file image are resolved against the
    # same image. If a chain page has since been reused the row is reported truncated.
    ovf = OverflowContext(page_size=page_size, reserved=reserved, data=data)

    schema = _schema_tables(db_path)

    # schema_hint takes priority over live introspection for expected_cols.
    if schema_hint and "col_count" in schema_hint:
        expected_cols: Optional[int] = int(schema_hint["col_count"])
        # Register the hinted column names so the pipeline can map by name.
        if table and "columns" in schema_hint:
            rows_meta_colnames[(db_path.name, table)] = list(schema_hint["columns"])
    else:
        expected_cols = schema.get(table, {}).get("col_count") if table else None
        # If no specific table, use the most common column count as a soft hint.
        if expected_cols is None and schema:
            counts = [t["col_count"] for t in schema.values()]
            expected_cols = max(set(counts), key=counts.count) if counts else None

    n_pages = len(data) // page_size
    seen_records: set[tuple[int, tuple[Any, ...]]] = set()
    results: list[CarvedRow] = []

    # 1) Freelist pages — highest-confidence deleted content.
    freelist = set(_freelist_pages(data, page_size))
    for pnum in sorted(freelist):
        page = _page_bytes(data, page_size, pnum)
        if len(page) < 8:
            continue
        results += _carve_region(
            page,
            (pnum - 1) * page_size,
            0,
            len(page),
            expected_cols,
            db_path.name,
            pnum,
            "freelist",
            Confidence.RECOVERED_VERIFIED,
            seen_records,
            ovf,
        )

    # 2) Live leaf pages: freeblocks + unallocated gaps hold deleted cells whose header
    #    bytes were clobbered by the freeblock header. First try a structured full-cell
    #    carve (catches cells whose header happened to survive); then always run a text
    #    carve to recover the message content the freeblock header destroyed the frame of.
    for pnum in range(1, n_pages + 1):
        if pnum in freelist:
            continue
        page = _page_bytes(data, page_size, pnum)
        hdr_off = _btree_header_offset(pnum)
        if hdr_off + 1 > len(page) or page[hdr_off] != _LEAF_TABLE:
            continue
        regions = list(_freeblock_regions(page, hdr_off))
        u_off, u_len = _unallocated_region(page, hdr_off)
        if u_len > 4:
            regions.append((u_off, u_len))
        # A freed region's leading bytes are clobbered by the freeblock header, but a
        # deleted cell whose OWN header survived further in is fully re-parseable (this
        # is what FQLite recovers). So first slide a STRUCTURED carve to recover those
        # intact-header cells with rowid + typed columns (CARVED_PARTIAL — the region is
        # not authoritative), THEN always run a text carve to salvage the raw content of
        # cells whose header the freeblock destroyed. Text-only carving alone dropped the
        # structure of every recoverable freeblock cell.
        base = (pnum - 1) * page_size
        for r_off, r_size in regions:
            kind = "unallocated" if (r_off, r_size) == (u_off, u_len) else "freeblock"
            results += _carve_region(
                page, base, r_off, r_size, expected_cols,
                db_path.name, pnum, kind, Confidence.CARVED_PARTIAL, seen_records, ovf)
            results += _carve_text_runs(
                page, base, r_off, r_size, db_path.name, pnum, kind)

    # 3) WAL frames — un-checkpointed page versions.
    results += _recover_from_wal(db_path, expected_cols, seen_records)

    # 4) Rollback journal — pre-deletion page images for non-WAL databases.
    results += _recover_from_journal(db_path, expected_cols, seen_records)

    return results


def _wal_cksum(s0: int, s1: int, buf: bytes, content_le: bool) -> tuple[int, int]:
    """SQLite WAL cumulative checksum over ``buf`` (must be a multiple of 8 bytes).

    Mirrors ``walChecksumBytes``: interpret the byte stream as pairs of 32-bit
    integers (endianness selected by the WAL magic) and accumulate the Fibonacci-
    weighted running sums s0/s1.
    """
    fmt = "<I" if content_le else ">I"
    end = len(buf) - (len(buf) % 8)
    for i in range(0, end, 8):
        x0 = struct.unpack_from(fmt, buf, i)[0]
        x1 = struct.unpack_from(fmt, buf, i + 4)[0]
        s0 = (s0 + x0 + s1) & 0xFFFFFFFF
        s1 = (s1 + x1 + s0) & 0xFFFFFFFF
    return s0, s1


def _recover_from_wal(db_path: Path, expected_cols: Optional[int],
                      seen_records: set[tuple[int, tuple[Any, ...]]]) -> list[CarvedRow]:
    """Recover deleted-content page images from a ``-wal`` sidecar, with per-frame
    validation.

    A WAL file can hold frames from MORE than one generation: after a checkpoint,
    SQLite resets the header salt and rewrites from frame 1, but any tail frames not
    yet overwritten still carry the OLD salt. Blindly carving every frame and stamping
    it ``RECOVERED_VERIFIED`` is an honesty-model violation — stale, torn, or
    uncommitted frames are not verified evidence. We therefore validate each frame:

      * salt match     — frame belongs to the current WAL generation (header salt-1/2);
      * checksum chain  — cumulative Fibonacci checksum matches the stored frame checksum;
      * commit tracking — a frame with a non-zero db-size field commits its transaction.

    Only current-generation, checksum-valid, committed frames are ``RECOVERED_VERIFIED``.
    Everything else (stale generation, failed checksum, uncommitted tail) is carved at
    ``CARVED_PARTIAL`` with the reason in its label, so the content is still surfaced but
    never overstated.
    """
    wal = db_path.with_name(db_path.name + "-wal")
    if not wal.exists():
        return []
    data = wal.read_bytes()
    if len(data) < 32:
        return []
    magic = struct.unpack(">I", data[0:4])[0]
    if magic not in (0x377F0682, 0x377F0683):
        return []
    # magic ...0682 → checksum content read little-endian (native on LE hosts);
    # magic ...0683 → big-endian. Stored checksum words are always big-endian.
    content_le = magic == 0x377F0682
    page_size = struct.unpack(">I", data[8:12])[0]
    if page_size == 1:
        page_size = 65536
    if page_size <= 0:
        return []
    hdr_salt = data[16:24]  # salt-1 + salt-2

    # Seed the checksum chain from the 24-byte WAL header and confirm the header's own
    # checksum. If the header checksum does not verify we cannot trust the chain, so no
    # frame can be promoted to RECOVERED_VERIFIED.
    s0, s1 = _wal_cksum(0, 0, data[0:24], content_le)
    header_ok = data[24:32] == struct.pack(">II", s0, s1)

    frame_size = 24 + page_size
    results: list[CarvedRow] = []

    # Pass 1: classify every frame; track the last valid committed frame.
    frames: list[tuple[int, int, bytes, int, bool, int]] = []  # idx,page,page_bytes,off,current_valid,db_size
    chain_ok = header_ok
    last_commit_idx = 0
    off = 32
    frame_idx = 0
    while off + frame_size <= len(data):
        frame_idx += 1
        fh = data[off:off + 24]
        page_num = struct.unpack(">I", fh[0:4])[0]
        db_size = struct.unpack(">I", fh[4:8])[0]
        f_salt = fh[8:16]
        stored_cksum = fh[16:24]
        page = data[off + 24:off + 24 + page_size]

        current_valid = False
        if chain_ok and f_salt == hdr_salt:
            n0, n1 = _wal_cksum(s0, s1, fh[0:8], content_le)
            n0, n1 = _wal_cksum(n0, n1, page, content_le)
            if struct.pack(">II", n0, n1) == stored_cksum:
                current_valid = True
                s0, s1 = n0, n1
                if db_size != 0:
                    last_commit_idx = frame_idx
            else:
                chain_ok = False  # torn write — nothing after this is trustworthy
        frames.append((frame_idx, page_num, page, off, current_valid, db_size))
        off += frame_size

    # Overflow chains for a WAL frame may run through pages that also live in the WAL.
    # Build a page map (latest frame wins) with the main database as the fallback. A page
    # taken from a different transaction than the frame being carved is a genuine caveat,
    # so `note` is attached as a row warning whenever the map is actually consulted.
    wal_page_map: dict[int, bytes] = {}
    for _idx, pnum, pbytes, _o, _v, _d in frames:
        if pnum > 0 and len(pbytes) == page_size:
            wal_page_map[pnum] = pbytes
    try:
        base_data = db_path.read_bytes()
    except OSError:
        base_data = b""
    _, reserved = _db_geometry(base_data)
    ovf = OverflowContext(
        page_size=page_size,
        reserved=reserved,
        data=base_data or None,
        page_map=wal_page_map,
        note=(
            "overflow pages for this value were reassembled from the WAL page map "
            "(latest frame per page) and/or the main database; the reassembled tail may "
            "originate from a different transaction than the frame it was carved from"
        ),
    )

    # Pass 2: carve, assigning confidence from the classification.
    for frame_idx, page_num, page, foff, current_valid, _db_size in frames:
        hdr_off = _btree_header_offset(page_num)
        if not (hdr_off < len(page) and page[hdr_off] == _LEAF_TABLE):
            continue
        committed = current_valid and frame_idx <= last_commit_idx
        if committed:
            conf = Confidence.RECOVERED_VERIFIED
            reason = ""
        elif current_valid:
            conf = Confidence.CARVED_PARTIAL
            reason = " [uncommitted tail]"
        elif not header_ok:
            conf = Confidence.CARVED_PARTIAL
            reason = " [unverified: WAL header checksum failed]"
        else:
            conf = Confidence.CARVED_PARTIAL
            reason = " [unverified: stale generation or failed checksum]"
        label = f"wal frame {frame_idx} (db page {page_num}){reason}"
        results += _carve_region(
            page, foff + 24, 0, len(page), expected_cols,
            wal.name, page_num, label, conf, seen_records, ovf)
    return results


def _recover_from_journal(db_path: Path, expected_cols: Optional[int],
                          seen_records: set[tuple[int, tuple[Any, ...]]]) -> list[CarvedRow]:
    """Recover pre-deletion page images from a ``-journal`` rollback sidecar.

    For a rollback-mode (non-WAL) database, the ``-journal`` holds the ORIGINAL page
    content before the current transaction modified it — i.e. for a DELETE, the page
    exactly as it was WITH the row still present. This sidecar is pulled during
    acquisition but was never parsed, so that entire deleted-content surface was
    collected and then silently ignored.

    A journal pre-image page also contains rows that are still live, so these carves are
    labeled ``CARVED_PARTIAL`` (pre-transaction snapshot — verify against the live db),
    never ``RECOVERED_VERIFIED``: they are real page bytes but a mix of deleted and live
    content. Dedups against the freelist/WAL pass via the shared ``seen_records``.
    """
    jr = db_path.with_name(db_path.name + "-journal")
    if not jr.exists():
        return []
    data = jr.read_bytes()
    if len(data) < 28:
        return []

    sector_size = struct.unpack(">I", data[20:24])[0]
    page_size = struct.unpack(">I", data[24:28])[0]
    valid_page_sizes = (512, 1024, 2048, 4096, 8192, 16384, 32768, 65536)
    has_magic = data[0:8] == _JOURNAL_MAGIC
    zeroed_magic = data[0:8] == b"\x00" * 8
    # SQLite writes the header magic LAST, at commit-sync — so a hot journal from a
    # crash carries the magic, but an in-flight/uncommitted one (which still holds the
    # pre-deletion page images we want) has a ZEROED magic with the rest of the header
    # intact. Accept either, provided the page/sector fields are self-consistent, and
    # reject a committed-then-cleared journal (garbage header, no plausible page size).
    plausible = page_size in valid_page_sizes and sector_size in valid_page_sizes
    if not ((has_magic or zeroed_magic) and plausible):
        return []
    if not (0 < sector_size <= (1 << 20)):
        sector_size = 512

    rec_size = 4 + page_size + 4
    filelen = len(data)
    results: list[CarvedRow] = []
    sync_note = "" if has_magic else " (unsynced)"
    # Collected in the walk, carved afterwards: a pre-image record's overflow chain may
    # run through pages that are themselves pre-images in this same journal, so we need
    # the full page map before any carving starts.
    preimages: list[tuple[int, bytes, int]] = []  # (pgno, page bytes, absolute offset)

    seg_off = 0
    guard = 0
    first_segment = True
    while seg_off + 28 <= filelen and guard < 10000:
        guard += 1
        # Every segment after the first is announced by a header magic at a sector
        # boundary; the first segment may have a zeroed (unsynced) magic.
        if not first_segment and data[seg_off:seg_off + 8] != _JOURNAL_MAGIC:
            break
        first_segment = False

        nrec = struct.unpack(">I", data[seg_off + 8:seg_off + 12])[0]
        seg_sector = struct.unpack(">I", data[seg_off + 20:seg_off + 24])[0] or sector_size
        # Page records begin at the first sector boundary of this segment.
        rec_off = seg_off + seg_sector
        if nrec in (0, 0xFFFFFFFF):
            max_recs = (filelen - rec_off) // rec_size if rec_size else 0
        else:
            max_recs = nrec

        parsed = 0
        while parsed < max_recs and rec_off + rec_size <= filelen:
            # A sector-aligned magic marks the next segment header, not a record.
            if (data[rec_off:rec_off + 8] == _JOURNAL_MAGIC
                    and (rec_off - seg_off) % seg_sector == 0):
                break
            pgno = struct.unpack(">I", data[rec_off:rec_off + 4])[0]
            page = data[rec_off + 4:rec_off + 4 + page_size]
            if pgno > 0 and len(page) == page_size:
                preimages.append((pgno, page, rec_off + 4))
            rec_off += rec_size
            parsed += 1

        # Advance to the next sector boundary; look for a further segment header.
        consumed = rec_off - seg_off
        aligned = seg_off + ((consumed + seg_sector - 1) // seg_sector) * seg_sector
        if aligned <= seg_off:
            break
        seg_off = aligned

    # Overflow pages: prefer the journal's own pre-images, fall back to the live database.
    journal_map: dict[int, bytes] = {}
    for pgno, page, _o in preimages:
        journal_map.setdefault(pgno, page)  # first pre-image = oldest = pre-transaction
    try:
        base_data = db_path.read_bytes()
    except OSError:
        base_data = b""
    _, reserved = _db_geometry(base_data)
    ovf = OverflowContext(
        page_size=page_size,
        reserved=reserved,
        data=base_data or None,
        page_map=journal_map,
        note=(
            "overflow pages for this value came from the rollback-journal pre-image map "
            "and/or the live database; the reassembled tail is not guaranteed to be the "
            "pre-transaction version of those pages"
        ),
    )

    for pgno, page, abs_off in preimages:
        hdr_off = _btree_header_offset(pgno)
        if hdr_off < len(page) and page[hdr_off] == _LEAF_TABLE:
            results += _carve_region(
                page, abs_off, 0, len(page), expected_cols,
                jr.name, pgno,
                f"rollback journal pre-image (db page {pgno}) "
                f"[pre-transaction{sync_note}, verify]",
                Confidence.CARVED_PARTIAL, seen_records, ovf)

    return results


def detect_rowid_gaps(db_path: str | Path, table: str) -> list[dict[str, Any]]:
    """Detect gaps in the rowid sequence of a table — proof a deletion occurred even when
    no content is recoverable (DFIR gap analysis)."""
    db_path = Path(db_path)
    gaps: list[dict[str, Any]] = []
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        rowids = [
            r[0] for r in con.execute(f"SELECT rowid FROM '{table}' ORDER BY rowid")
        ]
        con.close()
    except sqlite3.Error:
        return []
    for a, b in zip(rowids, rowids[1:]):
        if b - a > 1:
            gaps.append({"after_rowid": a, "before_rowid": b, "missing": b - a - 1})
    return gaps


# ===========================================================================
# P1-5: DELETION_DETECTED as a first-class evidence class
# ===========================================================================
#
# Structural deletion evidence is a fundamentally different kind of finding from carved
# content. It says "rows WERE removed from this table" with real, often strong, structural
# support — and it recovers NOTHING of what those rows said. Emitting it as an untyped
# dict alongside recovered rows invites exactly the conflation the honesty model exists to
# prevent, so it gets its own confidence-tagged class, its own renderer path, and — most
# importantly — a mandatory list of the innocent explanations that produce the same
# signature.

# Innocent explanations that produce a rowid gap with no deletion at all. Every mechanism
# starts from these and adds its own.
_COMMON_FP_CAUSES: list[str] = [
    "rolled-back or aborted transactions consume rowids that are never reused",
    "explicit INSERT with a caller-supplied rowid can skip values arbitrarily",
    "a table rebuild / schema migration (INSERT..SELECT into a new table) renumbers rows",
    "VACUUM rewrites the file and can change the apparent layout",
    "rows may have been moved to an archive/history table rather than deleted",
]


@dataclass
class DeletionEvidence:
    """One piece of STRUCTURAL evidence that rows were removed — never content.

    ``confidence`` is ``Confidence.DELETION_DETECTED.value`` ("deletion") for every real
    finding. The single exception is a record emitted to say a table was *skipped*
    (WITHOUT ROWID / virtual table): those carry ``"not-applicable"`` because claiming
    "deletion detected" for a table we could not analyse would be a fabricated finding.
    """

    db_file: str
    table: str
    mechanism: str
    first_missing_rowid: Optional[int] = None
    last_missing_rowid: Optional[int] = None
    missing_count: int = 0
    confidence: str = Confidence.DELETION_DETECTED.value
    description: str = ""
    false_positive_causes: list[str] = field(default_factory=list)
    provenance: str = ""
    caveats: list[str] = field(default_factory=list)
    # Mechanism-specific numbers (freelist page count, sequence values, rowid samples…).
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "db_file": self.db_file,
            "table": self.table,
            "mechanism": self.mechanism,
            "first_missing_rowid": self.first_missing_rowid,
            "last_missing_rowid": self.last_missing_rowid,
            "missing_count": self.missing_count,
            "confidence": self.confidence,
            "description": self.description,
            "false_positive_causes": list(self.false_positive_causes),
            "provenance": self.provenance,
            "caveats": list(self.caveats),
            "details": {k: _jsonable(v) for k, v in self.details.items()},
            # Stated on every record so no downstream renderer can lose it.
            "recovers_content": False,
        }


# Marker confidence for a table we could not legitimately analyse. Deliberately NOT a
# Confidence enum member: it is the absence of a finding, not a weaker finding.
NOT_APPLICABLE = "not-applicable"


def _table_sql(con: sqlite3.Connection, table: str) -> str:
    try:
        row = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
    except sqlite3.Error:
        return ""
    return (row[0] or "") if row else ""


def _is_without_rowid(sql: str) -> bool:
    """True if the CREATE TABLE statement declares WITHOUT ROWID.

    Such a table has no rowid at all, so "missing rowids" is a category error — any gap
    analysis over it would be pure fabrication.
    """
    return "WITHOUTROWID" in "".join(sql.upper().split())


def _is_virtual(sql: str) -> bool:
    return "CREATEVIRTUALTABLE" in "".join(sql.upper().split())[:64]


def _freelist_page_count(db_path: Path) -> int:
    """Header bytes 36:40 — the total number of pages on the freelist. -1 if unreadable."""
    try:
        with open(db_path, "rb") as fh:
            head = fh.read(100)
    except OSError:
        return -1
    if len(head) < 40 or head[:16] != _HEADER_MAGIC:
        return -1
    return struct.unpack(">I", head[36:40])[0]


def _recovered_rowids(recovered_rows: Any, table: Optional[str]) -> list[int]:
    """Pull rowids out of a heterogeneous list of CarvedRow objects and/or plain dicts.

    Rows that declare a table (attribute or key) are filtered to ``table``; rows that do
    not declare one are kept, and the ambiguity is recorded as a false-positive cause by
    the caller.
    """
    out: list[int] = []
    if not recovered_rows:
        return out
    for r in recovered_rows:
        try:
            if isinstance(r, dict):
                rowid = r.get("rowid")
                rtable = r.get("table")
            else:
                rowid = getattr(r, "rowid", None)
                rtable = getattr(r, "table", None)
            if rowid is None:
                continue
            if rtable and table and str(rtable) != str(table):
                continue
            out.append(int(rowid))
        except Exception:
            continue  # a malformed row must never break the analysis
    return sorted(set(out))


def detect_deletion_evidence(
    db_path: str | Path,
    tables: Optional[list[str]] = None,
    *,
    recovered_rows: Optional[list[Any]] = None,
) -> list[DeletionEvidence]:
    """Collect every available piece of STRUCTURAL deletion evidence for a database.

    Mechanisms, each tagged in ``DeletionEvidence.mechanism``:

    ``rowid-gap``
        Contiguous missing rowid ranges inside a rowid table.
    ``max-rowid-shortfall``
        ``max(rowid)`` exceeds the live ``COUNT(*)`` — rows are missing from the middle
        or the head of the sequence even if no single gap is visible.
    ``freelist-pages``
        The file header records pages on the freelist: whole pages were released, which
        only happens when content was removed (or the file was rebuilt).
    ``live-vs-recovered``
        A carved/recovered row carries a rowid that is not present in the live table.
        This is the strongest form: it pairs the structural gap with a page image.
    ``sequence-shortfall``
        ``sqlite_sequence`` (AUTOINCREMENT high-water mark) exceeds ``max(rowid)``, so
        rows once existed above the current maximum.

    Returns ``[]`` — never raises — for a missing, locked, corrupt or non-SQLite file.
    """
    db_path = Path(db_path)
    items: list[DeletionEvidence] = []
    if not db_path.exists() or not db_path.is_file():
        return items

    # --- database-wide: freelist pages (works even if no table can be queried) ---
    fl_pages = _freelist_page_count(db_path)
    if fl_pages > 0:
        items.append(
            DeletionEvidence(
                db_file=db_path.name,
                table="(database-wide)",
                mechanism="freelist-pages",
                missing_count=0,
                description=(
                    f"The database header records {fl_pages} page(s) on the freelist. "
                    "Pages reach the freelist when b-tree content is released — i.e. rows "
                    "or whole tables were removed. The number of ROWS this represents is "
                    "unknown and no content is asserted."
                ),
                false_positive_causes=_COMMON_FP_CAUSES
                + [
                    "DROP TABLE / DROP INDEX frees pages without any row-level deletion",
                    "auto_vacuum=INCREMENTAL parks pages on the freelist as normal upkeep",
                    "a large UPDATE that shrinks a b-tree also frees pages",
                ],
                provenance=f"{db_path.name} file header bytes 36:40 (freelist page count)",
                caveats=[
                    "Freelist page count is a whole-database signal: it cannot be "
                    "attributed to a specific table without carving the pages themselves.",
                    "Pages on the freelist may still hold recoverable content — see the "
                    "carved-rows output, which is a separate and independent finding.",
                ],
                details={"freelist_pages": fl_pages},
            )
        )

    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return items

    try:
        if tables is None:
            try:
                tables = [
                    r[0]
                    for r in con.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' "
                        "AND name NOT LIKE 'sqlite_%'"
                    ).fetchall()
                ]
            except sqlite3.Error:
                tables = []

        # AUTOINCREMENT high-water marks, if the table exists at all.
        seq_map: dict[str, int] = {}
        try:
            for name, seq in con.execute("SELECT name, seq FROM sqlite_sequence"):
                seq_map[str(name)] = int(seq)
        except sqlite3.Error:
            pass

        for table in tables or []:
            try:
                items += _deletion_evidence_for_table(
                    con, db_path, str(table), seq_map, recovered_rows
                )
            except Exception:
                # A single hostile table must not cost us the rest of the analysis.
                continue
    finally:
        try:
            con.close()
        except sqlite3.Error:
            pass
    return items


def _deletion_evidence_for_table(
    con: sqlite3.Connection,
    db_path: Path,
    table: str,
    seq_map: dict[str, int],
    recovered_rows: Optional[list[Any]],
) -> list[DeletionEvidence]:
    items: list[DeletionEvidence] = []
    sql = _table_sql(con, table)

    if _is_without_rowid(sql) or _is_virtual(sql):
        why = (
            "declared WITHOUT ROWID — the table has no rowid sequence at all"
            if _is_without_rowid(sql)
            else "a virtual table — its rows are synthesised by a module, not stored in a "
            "rowid b-tree"
        )
        items.append(
            DeletionEvidence(
                db_file=db_path.name,
                table=table,
                mechanism="rowid-analysis-skipped",
                missing_count=0,
                confidence=NOT_APPLICABLE,
                description=(
                    f"Rowid-based deletion analysis was NOT performed on '{table}': it is "
                    f"{why}. No deletion is claimed for this table by this analysis."
                ),
                false_positive_causes=[
                    "None applicable: no finding is asserted for this table. Reporting a "
                    "rowid gap here would be a fabricated result, because the primary key "
                    "is a user key with no monotonic allocation guarantee."
                ],
                provenance=f"{db_path.name} sqlite_master.sql for '{table}'",
                caveats=[
                    "Absence of a finding here is NOT evidence that nothing was deleted "
                    "from this table — it means this particular technique does not apply.",
                    "Deletions from such a table may still be recoverable by page carving "
                    "(freelist / WAL / journal), which is unaffected by the rowid model.",
                ],
                details={"skipped": True, "reason": why},
            )
        )
        return items

    # --- mechanism: rowid-gap ------------------------------------------------
    try:
        rowids = [
            int(r[0])
            for r in con.execute(f"SELECT rowid FROM '{table}' ORDER BY rowid")
        ]
    except sqlite3.Error:
        return items

    gap_causes = _COMMON_FP_CAUSES + [
        "AUTOINCREMENT never reuses a rowid, so any historic rollback leaves a permanent "
        "gap that looks identical to a deletion",
        "some apps allocate ids from a per-thread or per-chat counter, not a global one",
    ]
    for a, b in zip(rowids, rowids[1:]):
        if b - a > 1:
            items.append(
                DeletionEvidence(
                    db_file=db_path.name,
                    table=table,
                    mechanism="rowid-gap",
                    first_missing_rowid=a + 1,
                    last_missing_rowid=b - 1,
                    missing_count=b - a - 1,
                    description=(
                        f"{b - a - 1} consecutive rowid(s) ({a + 1}..{b - 1}) are absent "
                        f"from live table '{table}', which still holds rowid {a} and "
                        f"rowid {b}. Rows once occupied that range. NO content from those "
                        "rows is recovered by this finding."
                    ),
                    false_positive_causes=gap_causes,
                    provenance=(
                        f"{db_path.name}: SELECT rowid FROM '{table}' ORDER BY rowid "
                        "(live sequence scan)"
                    ),
                    caveats=[
                        "A rowid gap proves the SEQUENCE skipped values; it does not prove "
                        "the skipped rows were ever committed and visible to the user.",
                        "The gap says nothing about when the deletion happened.",
                    ],
                    details={"after_rowid": a, "before_rowid": b},
                )
            )

    # --- mechanism: max-rowid-shortfall -------------------------------------
    if rowids:
        max_rowid = rowids[-1]
        live_count = len(rowids)
        shortfall = max_rowid - live_count
        if shortfall > 0:
            items.append(
                DeletionEvidence(
                    db_file=db_path.name,
                    table=table,
                    mechanism="max-rowid-shortfall",
                    first_missing_rowid=None,
                    last_missing_rowid=None,
                    missing_count=shortfall,
                    description=(
                        f"Table '{table}' has max(rowid)={max_rowid} but only "
                        f"{live_count} live row(s): {shortfall} rowid value(s) in the "
                        "allocated range are unaccounted for. NO content is recovered by "
                        "this finding."
                    ),
                    false_positive_causes=_COMMON_FP_CAUSES
                    + [
                        "the table may simply not start at rowid 1 (imported or merged data)",
                        "AUTOINCREMENT gaps from failed inserts inflate the shortfall",
                    ],
                    provenance=(
                        f"{db_path.name}: max(rowid) vs COUNT(*) on '{table}' (live query)"
                    ),
                    caveats=[
                        "This counts unaccounted-for rowid VALUES, not confirmed rows.",
                        "It overlaps with the rowid-gap findings above; do not add the two "
                        "counts together.",
                    ],
                    details={"max_rowid": max_rowid, "live_count": live_count},
                )
            )

    # --- mechanism: sequence-shortfall --------------------------------------
    seq = seq_map.get(table)
    if seq is not None:
        max_rowid = rowids[-1] if rowids else 0
        if seq > max_rowid:
            items.append(
                DeletionEvidence(
                    db_file=db_path.name,
                    table=table,
                    mechanism="sequence-shortfall",
                    first_missing_rowid=(max_rowid + 1) if rowids else None,
                    last_missing_rowid=seq,
                    missing_count=seq - max_rowid,
                    description=(
                        f"sqlite_sequence records a high-water mark of {seq} for '{table}' "
                        f"but the highest live rowid is {max_rowid}: {seq - max_rowid} "
                        "row(s) were allocated above the current maximum and are gone. NO "
                        "content is recovered by this finding."
                    ),
                    false_positive_causes=_COMMON_FP_CAUSES
                    + [
                        "the AUTOINCREMENT counter also advances for INSERTs that were "
                        "later rolled back, so the shortfall can exceed real deletions",
                        "sqlite_sequence can be written directly by an application",
                    ],
                    provenance=f"{db_path.name}: sqlite_sequence.seq vs max(rowid) for '{table}'",
                    caveats=[
                        "Only AUTOINCREMENT tables appear in sqlite_sequence; absence of a "
                        "row here is not evidence of anything.",
                        "The counter is monotonic by design — it never comes back down.",
                    ],
                    details={"sequence_value": seq, "max_rowid": max_rowid},
                )
            )

    # --- mechanism: live-vs-recovered ---------------------------------------
    carved_ids = _recovered_rowids(recovered_rows, table)
    if carved_ids:
        live = set(rowids)
        missing = [rid for rid in carved_ids if rid not in live]
        if missing:
            items.append(
                DeletionEvidence(
                    db_file=db_path.name,
                    table=table,
                    mechanism="live-vs-recovered",
                    first_missing_rowid=min(missing),
                    last_missing_rowid=max(missing),
                    missing_count=len(missing),
                    description=(
                        f"{len(missing)} carved record(s) carry rowid(s) that are not "
                        f"present in live table '{table}' (e.g. "
                        f"{', '.join(str(m) for m in missing[:8])}"
                        f"{'…' if len(missing) > 8 else ''}). The row existed on disk and "
                        "is gone from the live table. This record asserts the DELETION "
                        "only — the content of those carved rows is reported separately, "
                        "with its own confidence."
                    ),
                    false_positive_causes=_COMMON_FP_CAUSES
                    + [
                        "a carved rowid may belong to a DIFFERENT table whose records "
                        "share the same page or byte pattern",
                        "a carved cell may be a pre-commit image of a row that was never "
                        "committed",
                        "byte-pattern carving can synthesise a plausible-looking rowid "
                        "from unrelated bytes",
                    ],
                    provenance=(
                        f"{db_path.name}: carved rowids cross-checked against live "
                        f"'{table}' rowids"
                    ),
                    caveats=[
                        "Confidence in this finding is bounded by the confidence of the "
                        "carve it came from — a CARVED_PARTIAL source makes the rowid "
                        "itself uncertain.",
                        "Carved rows without a table attribution are attributed to every "
                        "analysed table; check the carve's provenance before relying on it.",
                    ],
                    details={"missing_rowids": missing[:200]},
                )
            )

    return items


def deletion_evidence_summary(items: list) -> dict[str, Any]:
    """Aggregate :class:`DeletionEvidence` records (or their dicts) for the report.

    The examiner-facing paragraph is deliberately blunt about what this class of evidence
    is and is not: it proves data WAS deleted, and it recovers no content whatsoever.
    """

    def _get(item: Any, key: str, default: Any = None) -> Any:
        if isinstance(item, dict):
            return item.get(key, default)
        return getattr(item, key, default)

    by_mechanism: dict[str, int] = {}
    by_table: dict[str, int] = {}
    findings: list[Any] = []
    skipped: list[str] = []
    total_missing = 0

    for it in items or []:
        conf = _get(it, "confidence", "")
        mech = str(_get(it, "mechanism", "unknown"))
        table = str(_get(it, "table", ""))
        if conf != Confidence.DELETION_DETECTED.value:
            # Skipped/not-applicable records are NOT deletion findings and must never be
            # counted as such.
            skipped.append(f"{table} ({mech})")
            continue
        findings.append(it)
        by_mechanism[mech] = by_mechanism.get(mech, 0) + 1
        by_table[table] = by_table.get(table, 0) + 1
        try:
            total_missing += int(_get(it, "missing_count", 0) or 0)
        except (TypeError, ValueError):
            pass

    tables = sorted(t for t in by_table if t and t != "(database-wide)")
    if findings:
        narrative = (
            f"Structural deletion evidence: {len(findings)} finding(s) across "
            f"{len(tables)} table(s) ({', '.join(tables) if tables else 'database-wide'}) "
            f"using {len(by_mechanism)} independent mechanism(s) "
            f"({', '.join(sorted(by_mechanism))}). This evidence proves that data WAS "
            "DELETED from this database. It recovers no content: no message text, no "
            "attachments, no participants, no timestamps of the deleted rows themselves. "
            "It is a distinct and structurally strong finding in its own right — an "
            "established deletion event — and it must never be presented, counted or "
            "summarised as recovered data. Each record lists the innocent explanations "
            "that produce the same signature; those must be excluded before the finding "
            "is relied upon."
        )
    else:
        narrative = (
            "No structural deletion evidence was found. This is NOT proof that nothing "
            "was deleted — a rebuilt, vacuumed or WITHOUT ROWID table leaves no rowid "
            "signature at all. This class of evidence recovers no content in either "
            "direction; absence here says nothing about carved content."
        )

    return {
        "confidence": Confidence.DELETION_DETECTED.value,
        "total_findings": len(findings),
        "by_mechanism": by_mechanism,
        "by_table": by_table,
        "tables_affected": tables,
        "total_missing_rowids": total_missing,
        "skipped_tables": skipped,
        "recovers_content": False,
        "summary": narrative,
        "caveats": [
            "Deletion evidence and recovered content are separate findings; never merge "
            "their counts.",
            "missing_count values from different mechanisms overlap — they are not "
            "additive.",
        ],
    }


def recover_all(db_path: str | Path) -> dict[str, Any]:
    """Full recovery summary for one database: schema, live counts, carved rows, gaps."""
    db_path = Path(db_path)
    schema = _schema_tables(db_path)
    carved = recover_deleted_rows(db_path)
    gaps: dict[str, list[dict[str, Any]]] = {}
    live_counts: dict[str, int] = {}
    for tname in schema:
        gaps[tname] = detect_rowid_gaps(db_path, tname)
        try:
            con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            live_counts[tname] = con.execute(
                f"SELECT COUNT(*) FROM '{tname}'"
            ).fetchone()[0]
            con.close()
        except sqlite3.Error:
            live_counts[tname] = -1
    # Structural deletion evidence is reported ALONGSIDE the carved content, never
    # folded into it: it proves rows were removed but recovers nothing of what they said.
    try:
        evidence = detect_deletion_evidence(
            db_path, list(schema.keys()) or None, recovered_rows=carved
        )
    except Exception:
        evidence = []
    return {
        "database": db_path.name,
        "schema": schema,
        "live_counts": live_counts,
        "carved": [c.to_dict() for c in carved],
        "rowid_gaps": gaps,
        "carved_count": len(carved),
        "deletion_evidence": [e.to_dict() for e in evidence],
        "deletion_summary": deletion_evidence_summary(evidence),
    }
