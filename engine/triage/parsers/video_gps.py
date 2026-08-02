"""GPS extraction from MP4/MOV/3GP video containers.

Photos carry GPS in EXIF, which :mod:`triage.parsers.exif` already reads. Videos do not have
EXIF — a camera app writes the recording position into the ISO base-media-format `udta`
(user-data) box instead, as one of:

``©xyz``
    The QuickTime/MP4 convention Android's camera and every mainstream Android OEM use. The
    payload is a 16-bit length, a 16-bit language code, then an ISO-6709 string
    (``+37.7749-122.4194/``). This is the case that matters in practice.

``loci``
    The 3GPP location box. A version/flags word, a language word, a NUL-terminated UTF-8 or
    UTF-16 place name, a one-byte role, then **fixed-point 16.16** longitude, latitude and
    altitude — note longitude comes *first*, the opposite order to ``©xyz``.

``GPS ``/``gps ``
    Seen on some action cameras and dashcams; carries the same ISO-6709 payload.

Everything here is parsed with the standard library only: no ffmpeg, no exiftool, no external
binary that might not exist on a field laptop. The scan is bounded — it walks the real box tree
rather than regex-ing the whole file, so a multi-gigabyte video costs a handful of seeks.

A video shot with location services off has no such box; the honest result is ``None``, which
callers must render as "no recorded location", never as "the device was not there".
"""

from __future__ import annotations

import re
import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

# Container boxes worth descending into while hunting for `udta`. Anything else is skipped by
# its declared size, which is what keeps this cheap on large files.
_CONTAINERS = {b"moov", b"udta", b"trak", b"meta", b"ilst", b"mdia", b"minf", b"stbl"}

# Boxes whose payload is an ISO-6709 string.
_ISO6709_BOXES = {b"\xa9xyz", b"xyz ", b"GPS ", b"gps "}

# 1904-01-01 epoch used by the MP4 `mvhd` box, vs the Unix 1970 epoch.
_MP4_EPOCH_OFFSET = 2082844800

# ISO-6709: signed latitude, signed longitude, optional signed altitude, optional CRS suffix.
_ISO6709_RE = re.compile(
    r"^([+-]\d{1,3}(?:\.\d+)?)([+-]\d{1,3}(?:\.\d+)?)(?:([+-]\d+(?:\.\d+)?))?(?:CRS[\w:.]*)?/?$"
)

_VIDEO_SUFFIXES = {".mp4", ".m4v", ".mov", ".3gp", ".3g2", ".qt", ".m4a"}

# Cap on how much of the box tree to walk. A GPS box lives in `moov/udta`, which sits within the
# first or last few megabytes of any real file; anything beyond this is a malformed or hostile
# container and is not worth unbounded work.
_MAX_BOXES = 20_000


def is_video(path: str | Path) -> bool:
    """True if *path* has a container suffix this module knows how to walk."""
    return Path(path).suffix.lower() in _VIDEO_SUFFIXES


def parse_iso6709(raw: str | None) -> Optional[dict[str, float]]:
    """Parse an ISO-6709 point string into ``{"lat", "lon"}`` (plus ``alt`` when present).

    Returns ``None`` for anything unparseable, out of range, or equal to the 0,0 "null island"
    sentinel that zero-filled location boxes decode to — a fixed-point field that was never
    written reads as exactly 0,0, and reporting that as a position off West Africa would be a
    fabricated coordinate.
    """
    if not raw:
        return None
    s = raw.strip().strip("\x00").strip()
    m = _ISO6709_RE.match(s)
    if not m:
        return None
    try:
        lat = float(m.group(1))
        lon = float(m.group(2))
    except ValueError:
        return None
    if not _plausible(lat, lon):
        return None
    out: dict[str, float] = {"lat": lat, "lon": lon}
    if m.group(3):
        try:
            out["alt"] = float(m.group(3))
        except ValueError:
            pass
    return out


def _plausible(lat: float, lon: float) -> bool:
    if lat != lat or lon != lon:  # NaN
        return False
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        return False
    return not (lat == 0.0 and lon == 0.0)


def _fixed_16_16(raw: bytes) -> float:
    """Decode a signed 16.16 fixed-point word (the `loci` coordinate format)."""
    return struct.unpack(">i", raw)[0] / 65536.0


def _parse_loci(payload: bytes) -> Optional[dict[str, Any]]:
    """Decode a 3GPP ``loci`` box.

    Layout: 1-byte version, 3-byte flags, 2-byte language, NUL-terminated name, 1-byte role,
    then longitude, latitude and altitude as 16.16 fixed-point. Longitude first is not a typo —
    it is what the 3GPP spec specifies, and getting it backwards silently transposes every
    coordinate.
    """
    if len(payload) < 4 + 2 + 1 + 1 + 12:
        return None
    body = payload[6:]  # skip version+flags (4) and language (2)
    nul = body.find(b"\x00")
    if nul < 0:
        return None
    name = body[:nul].decode("utf-8", "replace").strip()
    rest = body[nul + 1 :]
    if len(rest) < 1 + 12:
        return None
    rest = rest[1:]  # role byte
    lon = _fixed_16_16(rest[0:4])
    lat = _fixed_16_16(rest[4:8])
    alt = _fixed_16_16(rest[8:12])
    if not _plausible(lat, lon):
        return None
    out: dict[str, Any] = {"lat": lat, "lon": lon, "box": "loci"}
    if alt:
        out["alt"] = alt
    if name:
        out["place_name"] = name
    return out


def _parse_iso6709_box(payload: bytes) -> Optional[dict[str, Any]]:
    """Decode a ``©xyz``-style box: 2-byte length, 2-byte language, ISO-6709 text."""
    if len(payload) < 4:
        return None
    text = payload[4:].decode("utf-8", "replace")
    if not text.strip():
        # Some writers omit the length/language prefix entirely.
        text = payload.decode("utf-8", "replace")
    point = parse_iso6709(text)
    if point is None:
        # Retry treating the whole payload as text (prefix-less writers).
        point = parse_iso6709(payload.decode("utf-8", "replace"))
    if point is None:
        return None
    return {**point, "box": "©xyz"}


def _walk(fh, end: int, depth: int, found: dict, budget: list[int]) -> None:
    """Walk the box tree from the current offset to *end*, filling *found* in place."""
    while fh.tell() + 8 <= end and budget[0] > 0:
        budget[0] -= 1
        header_start = fh.tell()
        header = fh.read(8)
        if len(header) < 8:
            return
        size = struct.unpack(">I", header[:4])[0]
        box_type = header[4:8]
        header_len = 8
        if size == 1:  # 64-bit extended size
            ext = fh.read(8)
            if len(ext) < 8:
                return
            size = struct.unpack(">Q", ext)[0]
            header_len = 16
        elif size == 0:  # box runs to end of file
            size = end - header_start
        if size < header_len or header_start + size > end:
            return  # malformed or truncated — stop rather than seek wildly

        payload_len = size - header_len
        if box_type in _ISO6709_BOXES and "lat" not in found:
            hit = _parse_iso6709_box(fh.read(min(payload_len, 512)))
            if hit:
                found.update(hit)
        elif box_type == b"loci" and "lat" not in found:
            hit = _parse_loci(fh.read(min(payload_len, 1024)))
            if hit:
                found.update(hit)
        elif box_type == b"mvhd" and "created" not in found:
            found.update(_parse_mvhd(fh.read(min(payload_len, 120))))
        elif box_type in _CONTAINERS:
            # `meta` carries a 4-byte version/flags word before its children in MP4 (but not in
            # QuickTime). Detect by peeking: if the next 4 bytes are zero, skip them.
            child_start = header_start + header_len
            if box_type == b"meta":
                peek = fh.read(4)
                child_start += 4 if peek[:4] == b"\x00\x00\x00\x00" else 0
            fh.seek(child_start)
            _walk(fh, header_start + size, depth + 1, found, budget)

        fh.seek(header_start + size)
        if "lat" in found and "created" in found:
            return


def _parse_mvhd(payload: bytes) -> dict[str, Any]:
    """Read the creation time from a ``mvhd`` box (MP4 epoch is 1904-01-01, not 1970)."""
    if len(payload) < 5:
        return {}
    version = payload[0]
    try:
        if version == 1 and len(payload) >= 20:
            secs = struct.unpack(">Q", payload[4:12])[0]
        elif len(payload) >= 8:
            secs = struct.unpack(">I", payload[4:8])[0]
        else:
            return {}
    except struct.error:
        return {}
    if secs <= _MP4_EPOCH_OFFSET:
        return {}
    try:
        dt = datetime(1904, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=secs)
    except (OverflowError, OSError, ValueError):
        return {}
    return {"created": dt.strftime("%Y-%m-%dT%H:%M:%SZ")}


def extract_video_gps(path: str | Path) -> Optional[dict[str, float]]:
    """Return ``{"lat": …, "lon": …}`` for a video, or ``None`` if it records no position.

    Mirrors :func:`triage.parsers.exif.extract_gps` so the two are interchangeable at call
    sites that only need a coordinate.
    """
    full = extract_video_location(path)
    if not full or "lat" not in full:
        return None
    return {"lat": full["lat"], "lon": full["lon"]}


def extract_video_location(path: str | Path) -> Optional[dict[str, Any]]:
    """Full location record for a video: coordinate, which box it came from, and creation time.

    Returns ``None`` when the file is unreadable or is not a parseable container. A readable
    container with no location box returns a dict without ``lat``/``lon`` only if a creation
    time was found; otherwise ``None``.
    """
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError:
        return None
    if size < 16:
        return None
    found: dict[str, Any] = {}
    try:
        with p.open("rb") as fh:
            # Confirm this really is an ISO base-media container before walking it: a mislabelled
            # file would otherwise send the walker chasing garbage box sizes.
            head = fh.read(12)
            if len(head) < 12 or head[4:8] not in (b"ftyp", b"moov", b"free", b"mdat", b"skip"):
                return None
            fh.seek(0)
            _walk(fh, size, 0, found, [_MAX_BOXES])
    except (OSError, struct.error, ValueError):
        return None
    if not found:
        return None
    found["source_file"] = p.name
    return found


def extract_datetime(path: str | Path) -> Optional[str]:
    """Container creation time as an ISO-8601 UTC string, or ``None``."""
    rec = extract_video_location(path)
    return rec.get("created") if rec else None
