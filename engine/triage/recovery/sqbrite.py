"""sqbrite secondary SQLite recovery pass.

sqbrite (MIT) uses a different heuristic from our primary sqlite_recovery engine:
instead of walking the B-tree freelist structure, it scans the raw file bytes for
SQLite record header signatures (varint patterns), making it effective at catching
rows whose freelist chain was already zeroed but whose content page still holds
residual record bytes.

We vendored the core logic (rather than importing from an external package) to:
  1. Avoid a pip dependency that may drift or vanish.
  2. Tune the minimum-printable-ratio and minimum-string-length thresholds for
     messaging-app payloads (short messages, often <20 chars, easily discarded by
     generic carvers).

## Cross-check posture
sqbrite results are de-duplicated against the primary engine by (rowid, values[:2])
fingerprint. Only rows *not* found by the primary pass are surfaced — the goal is
cross-check coverage, not duplicate inflation of the recovered-row count. All results
are marked CARVED_PARTIAL; we never up-grade confidence here.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..config import Confidence

_HEADER_MAGIC = b"SQLite format 3\x00"
_MIN_TEXT_LEN = 3  # shorter strings are almost certainly noise
_MIN_PRINTABLE = 0.75  # fraction of chars that must be printable


@dataclass
class SqbriteRow:
    """A recovered row from the sqbrite secondary pass."""

    values: list[Any]
    offset: int  # byte offset in the file where this record was found
    source_file: str
    confidence: Confidence = Confidence.CARVED_PARTIAL
    provenance: str = "sqbrite secondary pass"
    warnings: list[str] = field(
        default_factory=lambda: [
            "sqbrite secondary-pass carve: record structure inferred from raw bytes; "
            "field boundaries and column mapping are approximate"
        ]
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "values": [_jsonable(v) for v in self.values],
            "offset": self.offset,
            "source_file": self.source_file,
            "confidence": self.confidence.value,
            "provenance": self.provenance,
            "warnings": self.warnings,
        }


def _jsonable(v: Any) -> Any:
    if isinstance(v, bytes):
        return {"__blob__": v[:64].hex(), "len": len(v)}
    return v


# ---------------------------------------------------------------------------
# Varint decoder (same as primary engine — kept here to be self-contained)
# ---------------------------------------------------------------------------
def _read_varint(buf: bytes, off: int) -> tuple[int, int]:
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


def _serial_size(s: int) -> int:
    if s <= 0:
        return 0
    if s <= 4:
        return s
    if s == 5:
        return 6
    if s in (6, 7):
        return 8
    if s in (8, 9):
        return 0
    if s >= 12:
        return (s - 12) // 2 if s % 2 == 0 else (s - 13) // 2
    return 0


def _decode_value(serial: int, data: bytes) -> Any:
    if serial == 0:
        return None
    if 1 <= serial <= 6:
        return int.from_bytes(data, "big", signed=True)
    if serial == 7:
        return struct.unpack(">d", data)[0] if len(data) == 8 else None
    if serial == 8:
        return 0
    if serial == 9:
        return 1
    if serial >= 12 and serial % 2 == 0:
        return data
    if serial >= 13:
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data.decode("utf-8", "replace")
    return None


def _has_useful_text(values: list[Any]) -> bool:
    for v in values:
        if not isinstance(v, str):
            continue
        stripped = v.strip()
        if len(stripped) < _MIN_TEXT_LEN:
            continue
        printable = sum(1 for c in stripped if c.isprintable())
        if printable >= len(stripped) * _MIN_PRINTABLE:
            return True
    return False


# ---------------------------------------------------------------------------
# Core scanner: slide a window over raw file bytes looking for record headers
# ---------------------------------------------------------------------------
_VARINT_BYTE = re.compile(rb"[\x01-\xff]")  # any non-null byte — varint probe anchor


def _try_parse_record_at(
    buf: bytes, off: int, max_cols: int = 20
) -> Optional[list[Any]]:
    """Attempt to parse a SQLite record at buf[off]. Returns column values or None."""
    if off + 2 > len(buf):
        return None
    try:
        hdr_len, c = _read_varint(buf, off)
        if hdr_len < 2 or hdr_len > 512 or off + hdr_len > len(buf):
            return None
        serials: list[int] = []
        pos = off + c
        while pos < off + hdr_len:
            s, used = _read_varint(buf, pos)
            if used == 0 or len(serials) > max_cols:
                return None
            serials.append(s)
            pos += used
        if not serials:
            return None
        values: list[Any] = []
        body = off + hdr_len
        for s in serials:
            sz = _serial_size(s)
            if body + sz > len(buf):
                return None
            values.append(_decode_value(s, buf[body : body + sz]))
            body += sz
        return values
    except Exception:
        return None


def sqbrite_scan(
    db_path: str | Path,
    primary_fingerprints: Optional[set[tuple]] = None,
    step: int = 1,
) -> list[SqbriteRow]:
    """Scan a SQLite file's raw bytes for residual record signatures.

    Args:
        db_path:              Path to the database file.
        primary_fingerprints: Set of (values[0], values[1]) tuples already found
                              by the primary engine — used to deduplicate results.
        step:                 Scan granularity in bytes. 1 = exhaustive (slow on large
                              files); increase to 4–8 for speed at cost of coverage.

    Returns:
        List of SqbriteRow objects not already in primary_fingerprints.
    """
    db_path = Path(db_path)
    if primary_fingerprints is None:
        primary_fingerprints = set()

    try:
        data = db_path.read_bytes()
    except OSError:
        return []

    if len(data) < 100 or data[:16] != _HEADER_MAGIC:
        return []

    rows: list[SqbriteRow] = []
    seen_offsets: set[int] = set()
    end = len(data)
    off = 100  # skip the 100-byte SQLite file header

    while off < end:
        # Quick pre-filter: first varint byte of a valid header is typically 2–128.
        byte = data[off]
        if 2 <= byte <= 200:
            values = _try_parse_record_at(data, off)
            if values and _has_useful_text(values) and off not in seen_offsets:
                # Dedup against primary engine.
                fp = tuple(str(v)[:40] for v in values[:2])
                if fp not in primary_fingerprints:
                    rows.append(
                        SqbriteRow(
                            values=values,
                            offset=off,
                            source_file=db_path.name,
                        )
                    )
                    primary_fingerprints.add(fp)
                seen_offsets.add(off)
        off += step

    return rows


def sqbrite_cross_check(
    db_path: str | Path,
    primary_rows: list,
    step: int = 1,
) -> list[SqbriteRow]:
    """Convenience wrapper: run sqbrite and return only rows not in primary_rows.

    Args:
        db_path:      Path to the database.
        primary_rows: Rows already recovered by the primary engine, used to build the
                      dedup fingerprint set. Accepts either CarvedRow-like objects
                      (``.values`` attribute) OR the plain dicts the pipeline keeps
                      (``{"values": [...]}``). Passing an empty list disables dedup and
                      double-counts every row the primary pass already found.
        step:         Scan step size (bytes). Default 1 = full coverage.

    Returns:
        New rows found only by sqbrite.
    """
    # Build fingerprint set from primary engine's results. Must mirror sqbrite_scan's
    # own scheme exactly: first two values, str-truncated to 40 chars.
    fps: set[tuple] = set()
    for r in primary_rows:
        if isinstance(r, dict):
            vals = r.get("values") or []
        else:
            vals = getattr(r, "values", None) or []
        if vals:
            fp = tuple(str(v)[:40] for v in vals[:2])
            fps.add(fp)

    return sqbrite_scan(db_path, primary_fingerprints=fps, step=step)
