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
def _parse_record(payload: bytes) -> Optional[list[Any]]:
    """Parse a SQLite record (the payload after payload-len/rowid). Return column values
    or None if the header is inconsistent with the body length."""
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
            values.append(None)
            body += size
            continue
        values.append(_decode_value(serial, payload[body : body + size]))
        body += size
    return values


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


def _try_carve_cell(
    buf: bytes, off: int, expected_cols: Optional[int]
) -> Optional[tuple[int, int, list[Any], bool]]:
    """Attempt to parse a table-leaf cell starting at buf[off].

    Returns (rowid, payload_len, values, clean) or None. `clean` is True when the header
    parsed with no truncation and (if known) the column count matches the schema.
    """
    try:
        payload_len, c1 = _read_varint(buf, off)
        if payload_len <= 0 or payload_len > len(buf):
            return None
        rowid, c2 = _read_varint(buf, off + c1)
        rec_start = off + c1 + c2
        if rec_start >= len(buf):
            return None
        avail = len(buf) - rec_start
        take = min(payload_len, avail)
        values = _parse_record(buf[rec_start : rec_start + take])
        if values is None or not values:
            return None
        # Reject noise: a real record has content, not a wall of NULLs.
        if not _has_content(values):
            return None
        if sum(1 for v in values if v is None) > len(values) * 0.6:
            return None
        clean = take == payload_len
        if expected_cols is not None and len(values) != expected_cols:
            clean = False
        if rowid < 0 or rowid > (1 << 48):
            return None
        return rowid, payload_len, values, clean
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
        is_text = b in (0x09, 0x0A, 0x0D) or 0x20 <= b <= 0x7E or b >= 0x80
        if is_text:
            if run_start < 0:
                run_start = i
            run.append(b)
        else:
            _flush(run_start)
            run = bytearray()
            run_start = -1
        i += 1
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
) -> list[CarvedRow]:
    """Slide through a byte region attempting to carve table-leaf cells."""
    rows: list[CarvedRow] = []
    end = min(region_off + region_len, len(page))
    off = region_off
    while off < end:
        carved = _try_carve_cell(page, off, expected_cols)
        if carved:
            rowid, payload_len, values, clean = carved
            rec_key = (rowid, tuple(values))
            if rec_key not in seen_records or provenance_kind == "freeblock":
                conf = confidence if clean else Confidence.CARVED_PARTIAL
                warnings = (
                    [] if clean else ["header/column mismatch or truncated payload"]
                )
                rows.append(
                    CarvedRow(
                        values=values,
                        confidence=conf,
                        source_file=source_file,
                        provenance=f"{provenance_kind} page {page_num}@{off}",
                        rowid=rowid,
                        page=page_num,
                        offset=page_abs_base + off,
                        warnings=warnings,
                    )
                )
                seen_records.add(rec_key)
                # Advance past this record's declared payload only if the parse was clean.
                # If unclean, the payload_len might be garbage, so advance by 1 to avoid skipping valid data.
                off += max(payload_len, 1) if clean else 1
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
    page_size = struct.unpack(">H", data[16:18])[0]
    if page_size == 1:
        page_size = 65536
    if page_size <= 0 or (len(data) % page_size) not in (0, len(data) % page_size):
        pass  # tolerate trailing bytes

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
        # In-page freed regions have clobbered cell headers, so structured re-parsing is
        # unreliable here — text carving is the honest recovery method. (Structured
        # carving is reserved for freelist pages / WAL frames, where cells stay intact.)
        for r_off, r_size in regions:
            kind = "unallocated" if (r_off, r_size) == (u_off, u_len) else "freeblock"
            results += _carve_text_runs(
                page, (pnum - 1) * page_size, r_off, r_size, db_path.name, pnum, kind
            )

    # 3) WAL frames — un-checkpointed page versions.
    results += _recover_from_wal(db_path, expected_cols, seen_records)

    return results


def _recover_from_wal(
    db_path: Path,
    expected_cols: Optional[int],
    seen_records: set[tuple[int, tuple[Any, ...]]],
) -> list[CarvedRow]:
    wal = db_path.with_name(db_path.name + "-wal")
    if not wal.exists():
        return []
    data = wal.read_bytes()
    if len(data) < 32:
        return []
    magic = struct.unpack(">I", data[0:4])[0]
    if magic not in (0x377F0682, 0x377F0683):
        return []
    page_size = struct.unpack(">I", data[8:12])[0]
    if page_size == 1:
        page_size = 65536
    if page_size <= 0:
        return []
    frame_size = 24 + page_size
    results: list[CarvedRow] = []
    off = 32
    frame_idx = 0
    while off + frame_size <= len(data):
        frame_idx += 1
        page_num = struct.unpack(">I", data[off : off + 4])[0]
        page = data[off + 24 : off + 24 + page_size]
        hdr_off = _btree_header_offset(page_num)
        if hdr_off < len(page) and page[hdr_off] == _LEAF_TABLE:
            # Carve the whole page image; WAL frames often hold pre-deletion versions.
            results += _carve_region(
                page,
                off + 24,
                0,
                len(page),
                expected_cols,
                wal.name,
                page_num,
                f"wal frame {frame_idx} (db page {page_num})",
                Confidence.RECOVERED_VERIFIED,
                seen_records,
            )
        off += frame_size
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
    return {
        "database": db_path.name,
        "schema": schema,
        "live_counts": live_counts,
        "carved": [c.to_dict() for c in carved],
        "rowid_gaps": gaps,
        "carved_count": len(carved),
    }
