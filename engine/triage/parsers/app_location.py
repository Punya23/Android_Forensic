"""Shared-location extraction from messaging-app databases.

When a suspect sends "here's where I am" in a chat, the coordinate lands in that app's private
database — not in EXIF, not in `dumpsys location`, and not in Google's location history. Those
shares are often the only record placing a device somewhere at a stated time, and until now the
engine walked straight past them: the chat parsers pull message *text*, and a location share has
no text.

This module reads them. Four app-specific readers plus a generic fallback:

``parse_whatsapp_locations``
    ``msgstore.db``. Two schema eras — the pre-2021 ``messages`` table with inline
    ``latitude``/``longitude`` columns, and the current ``message`` + ``message_location``
    split. Live-location shares are distinguished from one-shot pins, and the *final* position
    of an expired live share is emitted as its own row.

``parse_telegram_locations``
    ``cache4.db``. Telegram stores messages as TL-serialised blobs, so there are no coordinate
    columns to query. The ``geoPoint`` constructors are carved out of the blob bytes instead —
    which has the useful side effect of working on WAL frames and freed pages too.

``parse_instagram_locations``
    ``direct.db``. Location and venue shares arrive as JSON in the message payload column.

``parse_snapchat_locations``
    ``memories.db`` — ``memories_snap`` carries ``has_location`` plus explicit lat/long for
    every saved snap. Also reads ``main.db`` if it holds coordinate columns.

``extract_sqlite_locations``
    The generic fallback: any SQLite file is scanned for tables holding coordinate-shaped
    columns. This is what catches the long tail — dating apps, ride-hailing apps, fitness
    trackers, food delivery — without needing a hand-written parser per app.

**Tier.** Every app-private database here lives under ``/data/data/<pkg>/`` and requires root or
a full filesystem image. That is a Tier-2 acquisition; nothing in this module runs on a non-root
pull. The one exception is a database recovered from a backup or an export, which the caller
labels through ``source_file``.

**Honesty.** A row is emitted only when a coordinate is actually present. Absence of location
rows means the app recorded no share *in the data we could read* — never that no share happened.
"""

from __future__ import annotations

import json
import re
import sqlite3
import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from ..config import Confidence
from ..models import Serialisable

# --- record -----------------------------------------------------------------


@dataclass
class SharedLocation(Serialisable):
    """One coordinate recovered from an app database."""

    app: str
    latitude: float
    longitude: float
    timestamp: Optional[str] = None
    sender: str = ""
    chat: str = ""
    place_name: str = ""
    place_address: str = ""
    # shared      — a one-shot pin sent in a conversation
    # live        — a point sampled during a live-location share
    # live_final  — the last position of a live share that has since expired
    # media       — a coordinate attached to a photo/video/memory, not sent as a location
    # saved       — a bookmarked/starred place, not a position the device occupied
    kind: str = "shared"
    direction: str = "unknown"  # incoming | outgoing | unknown
    accuracy_m: Optional[float] = None
    url: str = ""
    confidence: Confidence = Confidence.LIVE
    source_file: str = ""
    provenance: str = ""
    table_name: str = ""
    flags: list[str] = field(default_factory=list)


# --- shared helpers ---------------------------------------------------------

# Column-name spellings seen in the wild, most specific first so that a table carrying both
# `latitude` and `lat` resolves to the unambiguous one.
_LAT_NAMES = (
    "latitude",
    "lat",
    "gps_lat",
    "latitude_e7",
    "lat_e7",
    "geo_lat",
    "location_lat",
    "start_lat",
)
_LON_NAMES = (
    "longitude",
    "lon",
    "lng",
    "long",
    "gps_lon",
    "gps_lng",
    "longitude_e7",
    "lon_e7",
    "geo_lon",
    "location_lng",
    "location_lon",
    "start_lon",
)
_TIME_NAMES = (
    "timestamp",
    "date",
    "time",
    "created_at",
    "creation_timestamp",
    "date_taken",
    "sent_timestamp",
    "received_timestamp",
    "create_time",
    "modified",
)
_NAME_NAMES = ("place_name", "name", "title", "venue_name", "label", "address_name")
_ADDR_NAMES = ("place_address", "address", "vicinity", "formatted_address")

# Tables whose coordinates are configuration or cache noise rather than device positions.
_TABLE_DENYLIST = {
    "android_metadata",
    "sqlite_sequence",
    "sqlite_stat1",
    "room_master_table",
}


def _connect(path: str | Path) -> Optional[sqlite3.Connection]:
    """Open a database read-only via URI, returning ``None`` if it is not usable SQLite.

    ``immutable=1`` is deliberate: it tells SQLite the file will not change, which lets it open
    a database whose WAL/SHM sidecars are missing or whose header says "hot journal" — the
    normal state of a forensic copy. It also guarantees the engine never writes to evidence.
    """
    p = Path(path)
    if not p.is_file():
        return None
    try:
        if p.open("rb").read(16) != b"SQLite format 3\x00":
            return None
    except OSError:
        return None
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro&immutable=1", uri=True)
        con.text_factory = lambda b: b.decode("utf-8", "replace")
        return con
    except sqlite3.Error:
        return None


def _tables(con: sqlite3.Connection) -> list[str]:
    try:
        return [
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            if r[0] not in _TABLE_DENYLIST and not r[0].startswith("sqlite_")
        ]
    except sqlite3.Error:
        return []


def _columns(con: sqlite3.Connection, table: str) -> list[str]:
    try:
        return [r[1] for r in con.execute(f'PRAGMA table_info("{table}")').fetchall()]
    except sqlite3.Error:
        return []


def _pick(columns: Iterable[str], candidates: tuple[str, ...]) -> Optional[str]:
    """Return the first column whose lower-cased name matches *candidates*, preserving order."""
    lower = {c.lower(): c for c in columns}
    for want in candidates:
        if want in lower:
            return lower[want]
    return None


def _coerce_coord(value: Any, column: str = "") -> Optional[float]:
    """Coerce a stored coordinate to degrees.

    Several apps store fixed-point integers rather than floats: Google's ``*_e7`` convention
    (degrees × 1e7) and WhatsApp's older ``*_e6``-ish integer columns. Guessing wrong scales a
    coordinate by ten million, so the divisor is chosen from the column name when it says so,
    and otherwise from magnitude — a latitude can never legitimately exceed 90.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v != v:  # NaN
        return None
    name = column.lower()
    if name.endswith("_e7"):
        v /= 1e7
    elif name.endswith("_e6"):
        v /= 1e6
    elif abs(v) > 180.0:
        # Unlabelled fixed-point. Step down by decades until it lands in range rather than
        # assuming a single scale, since both 1e6 and 1e7 conventions are in use.
        for divisor in (1e7, 1e6, 1e5, 1e4):
            if abs(v / divisor) <= 180.0:
                v /= divisor
                break
        else:
            return None
    return v


def _valid_point(lat: Optional[float], lon: Optional[float]) -> bool:
    if lat is None or lon is None:
        return False
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        return False
    # 0,0 is the zero-filled "never set" value, not a position in the Gulf of Guinea.
    return not (abs(lat) < 1e-9 and abs(lon) < 1e-9)


def _to_iso(value: Any) -> Optional[str]:
    """Best-effort timestamp normalisation across the units apps actually use.

    Seconds, milliseconds and microseconds all appear — Instagram uses epoch microseconds,
    WhatsApp and Snapchat milliseconds, Telegram seconds — so the unit is inferred from
    magnitude against a plausible date window rather than assumed per app.
    """
    if value in (None, "", 0):
        return None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        if s.isdigit():
            value = int(s)
        else:
            try:
                return (
                    datetime.fromisoformat(s.replace("Z", "+00:00"))
                    .astimezone(timezone.utc)
                    .strftime("%Y-%m-%dT%H:%M:%SZ")
                )
            except ValueError:
                return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    # Window: 1990-01-01 .. 2100-01-01 in seconds.
    for divisor in (1.0, 1e3, 1e6, 1e9):
        secs = n / divisor
        if 631152000 <= secs <= 4102444800:
            try:
                return datetime.fromtimestamp(secs, tz=timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
            except (OSError, OverflowError, ValueError):
                return None
    return None


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace").strip()
    return str(value).strip()


# --- WhatsApp ---------------------------------------------------------------

_WA_MSG_ALIASES = ("message", "messages")


def parse_whatsapp_locations(db_path: str | Path) -> list[SharedLocation]:
    """Extract location shares from a WhatsApp ``msgstore.db``.

    Handles both schema eras and both share types:

    * **Legacy** (``messages`` table): ``latitude``/``longitude`` columns are populated for any
      message whose ``media_wa_type`` is 5 (location) or 16 (live location). ``media_name`` and
      ``media_caption`` carry the place name and address for a "send a place" share.
    * **Current** (``message`` + ``message_location``): coordinates moved to a side table keyed
      by ``message_row_id``. ``live_location_share_duration`` > 0 marks a live share, and
      ``live_location_final_latitude``/``…_longitude`` hold where the sharer ended up — a
      distinct, separately-timestamped fact, so it is emitted as its own ``live_final`` row.

    Sender attribution uses ``from_me`` when present; the chat name comes from the ``chat``/
    ``chat_list`` join when available, falling back to the raw JID.
    """
    con = _connect(db_path)
    if con is None:
        return []
    src = Path(db_path).name
    out: list[SharedLocation] = []
    try:
        tables = set(_tables(con))
        if "message_location" in tables:
            out.extend(_wa_modern(con, tables, src))
        # The legacy path is still worth running when both exist: a database migrated in place
        # can retain populated coordinate columns on rows the new table never got.
        msg_table = next((t for t in _WA_MSG_ALIASES if t in tables), None)
        if msg_table:
            out.extend(_wa_legacy(con, msg_table, src))
    finally:
        con.close()
    return _dedupe(out)


def _wa_jid_label(jid: str) -> str:
    """Strip the WhatsApp JID suffix so a chat reads as a phone number or group id."""
    return jid.split("@", 1)[0] if jid else ""


def _wa_modern(
    con: sqlite3.Connection, tables: set[str], src: str
) -> list[SharedLocation]:
    cols = {c.lower() for c in _columns(con, "message_location")}
    if not {"latitude", "longitude"} <= cols:
        return []
    has_msg = "message" in tables
    msg_cols = {c.lower() for c in _columns(con, "message")} if has_msg else set()
    chat_cols = {c.lower() for c in _columns(con, "chat")} if "chat" in tables else set()

    select = [
        "l.latitude AS lat",
        "l.longitude AS lon",
        _sel(cols, "place_name", "l.place_name", "''") + " AS place_name",
        _sel(cols, "place_address", "l.place_address", "''") + " AS place_address",
        _sel(cols, "url", "l.url", "''") + " AS url",
        _sel(cols, "live_location_share_duration", "l.live_location_share_duration", "0")
        + " AS live_duration",
        _sel(cols, "live_location_final_latitude", "l.live_location_final_latitude", "NULL")
        + " AS final_lat",
        _sel(cols, "live_location_final_longitude", "l.live_location_final_longitude", "NULL")
        + " AS final_lon",
        _sel(cols, "live_location_final_timestamp", "l.live_location_final_timestamp", "NULL")
        + " AS final_ts",
        _sel(cols, "accuracy", "l.accuracy", "NULL") + " AS accuracy",
    ]
    joins = ""
    if has_msg and "message_row_id" in cols:
        select += [
            _sel(msg_cols, "timestamp", "m.timestamp", "NULL") + " AS ts",
            _sel(msg_cols, "from_me", "m.from_me", "NULL") + " AS from_me",
            _sel(msg_cols, "sender_jid_row_id", "m.sender_jid_row_id", "NULL") + " AS sender_row",
            _sel(msg_cols, "chat_row_id", "m.chat_row_id", "NULL") + " AS chat_row",
        ]
        joins = " LEFT JOIN message m ON m._id = l.message_row_id"
        if chat_cols and "jid_row_id" in chat_cols and "jid" in tables:
            select.append("j.raw_string AS chat_jid")
            joins += (
                " LEFT JOIN chat c ON c._id = m.chat_row_id"
                " LEFT JOIN jid j ON j._id = c.jid_row_id"
            )
    else:
        select += ["NULL AS ts", "NULL AS from_me", "NULL AS sender_row", "NULL AS chat_row"]

    query = f"SELECT {', '.join(select)} FROM message_location l{joins}"
    try:
        cur = con.execute(query)
        names = [d[0] for d in cur.description]
        rows = cur.fetchall()
    except sqlite3.Error:
        return []

    out: list[SharedLocation] = []
    for raw in rows:
        r = dict(zip(names, raw))
        lat = _coerce_coord(r.get("lat"))
        lon = _coerce_coord(r.get("lon"))
        live = _int_or_zero(r.get("live_duration")) > 0
        ts = _to_iso(r.get("ts"))
        direction = _wa_direction(r.get("from_me"))
        chat = _wa_jid_label(_text(r.get("chat_jid")))
        if _valid_point(lat, lon):
            out.append(
                SharedLocation(
                    app="WhatsApp",
                    latitude=lat,  # type: ignore[arg-type]
                    longitude=lon,  # type: ignore[arg-type]
                    timestamp=ts,
                    chat=chat,
                    place_name=_text(r.get("place_name")),
                    place_address=_text(r.get("place_address")),
                    url=_text(r.get("url")),
                    kind="live" if live else "shared",
                    direction=direction,
                    accuracy_m=_float_or_none(r.get("accuracy")),
                    source_file=src,
                    provenance="message_location (live table)",
                    table_name="message_location",
                    flags=["live-location"] if live else [],
                )
            )
        # An expired live share keeps its last known position in separate columns. That is a
        # different point at a different time and must not be collapsed into the row above.
        flat = _coerce_coord(r.get("final_lat"))
        flon = _coerce_coord(r.get("final_lon"))
        if _valid_point(flat, flon):
            out.append(
                SharedLocation(
                    app="WhatsApp",
                    latitude=flat,  # type: ignore[arg-type]
                    longitude=flon,  # type: ignore[arg-type]
                    timestamp=_to_iso(r.get("final_ts")) or ts,
                    chat=chat,
                    kind="live_final",
                    direction=direction,
                    source_file=src,
                    provenance="message_location live-location final position",
                    table_name="message_location",
                    flags=["live-location", "final-position"],
                )
            )
    return out


def _wa_legacy(con: sqlite3.Connection, table: str, src: str) -> list[SharedLocation]:
    cols = _columns(con, table)
    lower = {c.lower() for c in cols}
    if not {"latitude", "longitude"} <= lower:
        return []
    sel = [
        "latitude AS lat",
        "longitude AS lon",
        _sel(lower, "timestamp", "timestamp", "NULL") + " AS ts",
        _sel(lower, "key_from_me", "key_from_me", "NULL") + " AS from_me",
        _sel(lower, "key_remote_jid", "key_remote_jid", "''") + " AS chat_jid",
        _sel(lower, "media_name", "media_name", "''") + " AS place_name",
        _sel(lower, "media_caption", "media_caption", "''") + " AS place_address",
        _sel(lower, "media_url", "media_url", "''") + " AS url",
        _sel(lower, "media_wa_type", "media_wa_type", "NULL") + " AS wa_type",
        _sel(lower, "media_duration", "media_duration", "NULL") + " AS duration",
    ]
    query = (
        f'SELECT {", ".join(sel)} FROM "{table}" '
        "WHERE latitude IS NOT NULL AND longitude IS NOT NULL "
        "AND NOT (latitude = 0 AND longitude = 0)"
    )
    try:
        cur = con.execute(query)
        names = [d[0] for d in cur.description]
        rows = cur.fetchall()
    except sqlite3.Error:
        return []

    out: list[SharedLocation] = []
    for raw in rows:
        r = dict(zip(names, raw))
        lat = _coerce_coord(r.get("lat"))
        lon = _coerce_coord(r.get("lon"))
        if not _valid_point(lat, lon):
            continue
        # media_wa_type 5 = location pin, 16 = live location. Any other type carrying a
        # coordinate is a media item that happened to be geotagged.
        wa_type = _int_or_zero(r.get("wa_type"))
        kind = {5: "shared", 16: "live"}.get(wa_type, "media")
        out.append(
            SharedLocation(
                app="WhatsApp",
                latitude=lat,  # type: ignore[arg-type]
                longitude=lon,  # type: ignore[arg-type]
                timestamp=_to_iso(r.get("ts")),
                chat=_wa_jid_label(_text(r.get("chat_jid"))),
                place_name=_text(r.get("place_name")),
                place_address=_text(r.get("place_address")),
                url=_text(r.get("url")),
                kind=kind,
                direction=_wa_direction(r.get("from_me")),
                source_file=src,
                provenance=f"{table}.latitude/longitude (live table)",
                table_name=table,
                flags=["live-location"] if kind == "live" else [],
            )
        )
    return out


def _wa_direction(from_me: Any) -> str:
    if from_me is None:
        return "unknown"
    try:
        return "outgoing" if int(from_me) == 1 else "incoming"
    except (TypeError, ValueError):
        return "unknown"


def _sel(available: Iterable[str], column: str, expr: str, fallback: str) -> str:
    """Return *expr* if *column* exists in the live schema, else the literal *fallback*.

    Lets one query serve every schema variant: a column absent from this build of the app
    yields a constant instead of an ``OperationalError`` that would lose the whole table.
    """
    return expr if column in {c.lower() for c in available} else fallback


def _int_or_zero(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float_or_none(value: Any) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


# --- Telegram ---------------------------------------------------------------

# TL constructors carrying a coordinate. Telegram serialises little-endian, so the 32-bit
# constructor id appears in the blob byte-reversed.
#   geoPoint#b2a2f663    flags:# long:double lat:double access_hash:long accuracy_radius:...
#   geoPoint#2049d70c    long:double lat:double access_hash:long          (legacy, no flags)
_TG_GEOPOINT_FLAGS = struct.pack("<I", 0xB2A2F663)
_TG_GEOPOINT_LEGACY = struct.pack("<I", 0x2049D70C)


def parse_telegram_locations(db_path: str | Path) -> list[SharedLocation]:
    """Carve ``geoPoint`` constructors out of Telegram's TL-serialised message blobs.

    Telegram's ``cache4.db`` has no coordinate columns — a message is one opaque
    TL-serialised blob in ``messages_v2.data`` (``messages.data`` on older builds). So instead
    of querying, this scans each blob for the ``geoPoint`` constructor id and reads the two
    doubles that follow. **Longitude precedes latitude** in the TL schema; swapping them would
    put every European coordinate in the Indian Ocean without erroring.

    Because it is a byte scan rather than a column read, the same routine works unchanged on
    blobs recovered from WAL frames and freed pages, which is where deleted shares survive.
    Carved hits are therefore reported at ``RECOVERED_VERIFIED`` only when they came from a
    live row; callers passing recovered blobs should downgrade accordingly.
    """
    con = _connect(db_path)
    if con is None:
        return []
    src = Path(db_path).name
    out: list[SharedLocation] = []
    try:
        tables = set(_tables(con))
        table = next((t for t in ("messages_v2", "messages") if t in tables), None)
        if table is None:
            return []
        cols = {c.lower() for c in _columns(con, table)}
        if "data" not in cols:
            return []
        sel = [
            "data",
            _sel(cols, "date", "date", "NULL") + " AS ts",
            _sel(cols, "uid", "uid", "NULL") + " AS uid",
            _sel(cols, "out", "out", "NULL") + " AS out",
            _sel(cols, "mid", "mid", "NULL") + " AS mid",
        ]
        try:
            cur = con.execute(
                f'SELECT {", ".join(sel)} FROM "{table}" WHERE data IS NOT NULL'
            )
            names = [d[0] for d in cur.description]
            rows = cur.fetchall()
        except sqlite3.Error:
            return []
        for raw in rows:
            r = dict(zip(names, raw))
            blob = r.get("data")
            if not isinstance(blob, (bytes, bytearray)):
                continue
            for lat, lon in _tg_geopoints(bytes(blob)):
                out.append(
                    SharedLocation(
                        app="Telegram",
                        latitude=lat,
                        longitude=lon,
                        timestamp=_to_iso(r.get("ts")),
                        chat=_text(r.get("uid")),
                        kind="shared",
                        direction=_tg_direction(r.get("out")),
                        source_file=src,
                        provenance=f"{table}.data TL geoPoint (live row)",
                        table_name=table,
                    )
                )
    finally:
        con.close()
    return _dedupe(out)


def _tg_direction(out: Any) -> str:
    if out is None:
        return "unknown"
    try:
        return "outgoing" if int(out) == 1 else "incoming"
    except (TypeError, ValueError):
        return "unknown"


def carve_telegram_geopoints(blob: bytes) -> list[tuple[float, float]]:
    """Public wrapper over the TL geoPoint carve, for use on WAL frames and freed pages."""
    return list(_tg_geopoints(blob))


def _tg_geopoints(blob: bytes) -> Iterable[tuple[float, float]]:
    """Yield ``(lat, lon)`` for every plausible geoPoint constructor in *blob*."""
    seen: set[tuple[float, float]] = set()
    for magic, offset in ((_TG_GEOPOINT_FLAGS, 8), (_TG_GEOPOINT_LEGACY, 4)):
        # offset = bytes between the constructor id and the first double (flags word for the
        # modern constructor, nothing for the legacy one).
        start = 0
        while True:
            idx = blob.find(magic, start)
            if idx < 0:
                break
            start = idx + 4
            base = idx + offset
            if base + 16 > len(blob):
                continue
            try:
                lon, lat = struct.unpack_from("<dd", blob, base)
            except struct.error:
                continue
            if not _valid_point(lat, lon):
                continue
            key = (round(lat, 6), round(lon, 6))
            if key in seen:
                continue
            seen.add(key)
            yield lat, lon


# --- Instagram --------------------------------------------------------------

# Coordinate keys seen in Instagram's message JSON, ordered so the explicit pair wins.
_IG_LAT_KEYS = ("lat", "latitude")
_IG_LON_KEYS = ("lng", "lon", "longitude")


def parse_instagram_locations(db_path: str | Path) -> list[SharedLocation]:
    """Extract location and venue shares from Instagram's ``direct.db``.

    Instagram keeps DM payloads as JSON in the message column, so a shared location is a
    ``{"location": {"lat": …, "lng": …, "name": …}}`` object rather than a column. Timestamps in
    this database are **epoch microseconds**, which :func:`_to_iso` resolves by magnitude.
    """
    con = _connect(db_path)
    if con is None:
        return []
    src = Path(db_path).name
    out: list[SharedLocation] = []
    try:
        tables = set(_tables(con))
        table = next((t for t in ("messages", "message") if t in tables), None)
        if table is None:
            return []
        cols = {c.lower() for c in _columns(con, table)}
        payload_col = next(
            (c for c in ("message", "text", "content", "payload") if c in cols), None
        )
        if payload_col is None:
            return []
        sel = [
            f"{payload_col} AS payload",
            _sel(cols, "timestamp", "timestamp", "NULL") + " AS ts",
            _sel(cols, "thread_id_published", "thread_id_published", "''") + " AS thread",
            _sel(cols, "user_id", "user_id", "''") + " AS sender",
        ]
        try:
            cur = con.execute(
                f'SELECT {", ".join(sel)} FROM "{table}" WHERE {payload_col} IS NOT NULL'
            )
            names = [d[0] for d in cur.description]
            rows = cur.fetchall()
        except sqlite3.Error:
            return []
        for raw in rows:
            r = dict(zip(names, raw))
            for lat, lon, name, addr in _json_points(r.get("payload")):
                out.append(
                    SharedLocation(
                        app="Instagram",
                        latitude=lat,
                        longitude=lon,
                        timestamp=_to_iso(r.get("ts")),
                        chat=_text(r.get("thread")),
                        sender=_text(r.get("sender")),
                        place_name=name,
                        place_address=addr,
                        kind="shared",
                        source_file=src,
                        provenance=f"{table}.{payload_col} JSON location (live row)",
                        table_name=table,
                    )
                )
    finally:
        con.close()
    return _dedupe(out)


def _json_points(payload: Any) -> list[tuple[float, float, str, str]]:
    """Recursively pull ``(lat, lon, name, address)`` tuples out of a JSON payload.

    Walks the whole object rather than matching one known path, because the shape of a share
    differs between a location pin, a venue, a story reshare and a live-video invite — and the
    schema changes between app versions faster than a fixed path could track.
    """
    text = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else payload
    if not isinstance(text, str) or "{" not in text:
        return []
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return []
    found: list[tuple[float, float, str, str]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            lat = _first_key(node, _IG_LAT_KEYS)
            lon = _first_key(node, _IG_LON_KEYS)
            la, lo = _coerce_coord(lat), _coerce_coord(lon)
            if _valid_point(la, lo):
                found.append(
                    (
                        la,  # type: ignore[arg-type]
                        lo,  # type: ignore[arg-type]
                        _text(node.get("name") or node.get("short_name") or ""),
                        _text(node.get("address") or node.get("external_source") or ""),
                    )
                )
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return found


def _first_key(node: dict, keys: tuple[str, ...]) -> Any:
    for k in keys:
        if k in node:
            return node[k]
    return None


# --- Snapchat ---------------------------------------------------------------


def parse_snapchat_locations(db_path: str | Path) -> list[SharedLocation]:
    """Extract saved-snap coordinates from Snapchat's ``memories.db`` (or ``main.db``).

    ``memories_snap`` carries ``has_location`` alongside explicit ``latitude``/``longitude`` for
    every snap saved to Memories. These are ``kind="media"``: they record where a snap was
    *taken*, which is not the same claim as a location deliberately shared in a chat.
    """
    con = _connect(db_path)
    if con is None:
        return []
    src = Path(db_path).name
    out: list[SharedLocation] = []
    try:
        for table in _tables(con):
            cols = _columns(con, table)
            lat_col = _pick(cols, _LAT_NAMES)
            lon_col = _pick(cols, _LON_NAMES)
            if not lat_col or not lon_col:
                continue
            time_col = _pick(cols, _TIME_NAMES)
            sel = [f'"{lat_col}" AS lat', f'"{lon_col}" AS lon']
            sel.append(f'"{time_col}" AS ts' if time_col else "NULL AS ts")
            try:
                cur = con.execute(
                    f'SELECT {", ".join(sel)} FROM "{table}" '
                    f'WHERE "{lat_col}" IS NOT NULL AND "{lon_col}" IS NOT NULL'
                )
                names = [d[0] for d in cur.description]
                rows = cur.fetchall()
            except sqlite3.Error:
                continue
            for raw in rows:
                r = dict(zip(names, raw))
                lat = _coerce_coord(r.get("lat"), lat_col)
                lon = _coerce_coord(r.get("lon"), lon_col)
                if not _valid_point(lat, lon):
                    continue
                out.append(
                    SharedLocation(
                        app="Snapchat",
                        latitude=lat,  # type: ignore[arg-type]
                        longitude=lon,  # type: ignore[arg-type]
                        timestamp=_to_iso(r.get("ts")),
                        kind="media",
                        source_file=src,
                        provenance=f"{table}.{lat_col}/{lon_col} (live table)",
                        table_name=table,
                    )
                )
    finally:
        con.close()
    return _dedupe(out)


# --- generic SQLite fallback ------------------------------------------------


def extract_sqlite_locations(
    db_path: str | Path, app: str = "", *, max_rows_per_table: int = 5000
) -> list[SharedLocation]:
    """Scan any SQLite database for tables holding coordinate-shaped columns.

    This is the long tail: dating apps, ride-hailing, delivery, fitness and every other app
    that quietly logs where its user was. Writing a bespoke parser for each is not feasible, so
    the schema is inspected instead — a table with a latitude-ish and a longitude-ish column is
    read, and anything that decodes to a plausible point is emitted with the table and column
    names recorded in ``provenance`` so a reviewer can trace and challenge every row.

    Rows are capped per table. Cap hits are not silent — see ``flags`` on the returned rows,
    which carry ``row-cap-reached`` when a table was truncated.
    """
    con = _connect(db_path)
    if con is None:
        return []
    src = Path(db_path).name
    label = app or Path(db_path).stem
    out: list[SharedLocation] = []
    try:
        for table in _tables(con):
            cols = _columns(con, table)
            lat_col = _pick(cols, _LAT_NAMES)
            lon_col = _pick(cols, _LON_NAMES)
            if not lat_col or not lon_col or lat_col == lon_col:
                continue
            time_col = _pick(cols, _TIME_NAMES)
            name_col = _pick(cols, _NAME_NAMES)
            addr_col = _pick(cols, _ADDR_NAMES)
            sel = [f'"{lat_col}" AS lat', f'"{lon_col}" AS lon']
            sel.append(f'"{time_col}" AS ts' if time_col else "NULL AS ts")
            sel.append(f'"{name_col}" AS name' if name_col else "'' AS name")
            sel.append(f'"{addr_col}" AS addr' if addr_col else "'' AS addr")
            try:
                cur = con.execute(
                    f'SELECT {", ".join(sel)} FROM "{table}" '
                    f'WHERE "{lat_col}" IS NOT NULL AND "{lon_col}" IS NOT NULL '
                    f"LIMIT {int(max_rows_per_table) + 1}"
                )
                names = [d[0] for d in cur.description]
                rows = cur.fetchall()
            except sqlite3.Error:
                continue
            capped = len(rows) > max_rows_per_table
            for raw in rows[:max_rows_per_table]:
                r = dict(zip(names, raw))
                lat = _coerce_coord(r.get("lat"), lat_col)
                lon = _coerce_coord(r.get("lon"), lon_col)
                if not _valid_point(lat, lon):
                    continue
                out.append(
                    SharedLocation(
                        app=label,
                        latitude=lat,  # type: ignore[arg-type]
                        longitude=lon,  # type: ignore[arg-type]
                        timestamp=_to_iso(r.get("ts")),
                        place_name=_text(r.get("name")),
                        place_address=_text(r.get("addr")),
                        kind="media",
                        source_file=src,
                        provenance=f"{table}.{lat_col}/{lon_col} (generic schema scan)",
                        table_name=table,
                        flags=["row-cap-reached"] if capped else [],
                    )
                )
    finally:
        con.close()
    return _dedupe(out)


# --- dispatcher -------------------------------------------------------------

# Filename → (app label, reader). Matched as a substring of the lower-cased file name.
_READERS: tuple[tuple[str, str, Any], ...] = (
    ("msgstore", "WhatsApp", parse_whatsapp_locations),
    ("wa.db", "WhatsApp", parse_whatsapp_locations),
    ("cache4", "Telegram", parse_telegram_locations),
    ("direct.db", "Instagram", parse_instagram_locations),
    ("memories", "Snapchat", parse_snapchat_locations),
    ("arroyo", "Snapchat", parse_snapchat_locations),
    ("main.db", "Snapchat", parse_snapchat_locations),
)


def extract_app_locations(
    db_path: str | Path, *, app_hint: str = ""
) -> list[SharedLocation]:
    """Route a database to its app-specific reader, then sweep it generically.

    The app-specific readers understand things the generic scan cannot — that WhatsApp's
    ``live_location_share_duration`` distinguishes a live share from a pin, that Telegram has no
    coordinate columns at all — so they run first and their rows win. The generic scan then runs
    over the same file anyway, because an app database routinely holds coordinates in tables its
    own parser does not know about (a cached venue list, an ad-targeting table).

    Generic rows describing a point a specific reader already reported are dropped: the same
    coordinate would otherwise appear twice, once correctly classified as a live-location share
    and once as a nondescript ``media`` row, which would inflate every count downstream.
    """
    p = Path(db_path)
    name = p.name.lower()
    label = app_hint
    specific: list[SharedLocation] = []
    for needle, app_label, reader in _READERS:
        if needle in name:
            label = label or app_label
            try:
                specific = list(reader(p))
            except Exception:
                # A malformed database must not abort the sweep over the others.
                specific = []
            break
    try:
        generic = extract_sqlite_locations(p, app=label)
    except Exception:
        generic = []

    # A generic row is redundant if the specific reader already read that table, or already
    # reported that exact point. Both tests are needed: the generic scan reads the coordinate
    # columns straight off the table and so has no timestamp, while the specific reader joins
    # out to the message row to get one — the same share therefore does not match on time.
    claimed_tables = {r.table_name for r in specific if r.table_name}
    covered_full = {
        (round(r.latitude, 6), round(r.longitude, 6), r.timestamp or "") for r in specific
    }
    covered_points = {(round(r.latitude, 6), round(r.longitude, 6)) for r in specific}

    fresh = []
    for r in generic:
        point = (round(r.latitude, 6), round(r.longitude, 6))
        if r.table_name in claimed_tables:
            continue
        if (*point, r.timestamp or "") in covered_full:
            continue
        if r.timestamp is None and point in covered_points:
            continue
        fresh.append(r)
    return _dedupe(specific + fresh)


def _dedupe(rows: list[SharedLocation]) -> list[SharedLocation]:
    """Collapse rows describing the same point, keeping the most informative one.

    Two readers legitimately surface the same coordinate — the WhatsApp reader and the generic
    scan both see ``message_location``. Keying on rounded coordinate + timestamp + app + kind
    merges those without merging two genuinely distinct shares of the same place at different
    times. ~1e-6 degrees is roughly 0.1 m, well below any consumer GPS fix.
    """
    best: dict[tuple, SharedLocation] = {}
    for r in rows:
        key = (
            r.app,
            round(r.latitude, 6),
            round(r.longitude, 6),
            r.timestamp or "",
            r.kind,
        )
        prior = best.get(key)
        if prior is None or _informativeness(r) > _informativeness(prior):
            best[key] = r
    return sorted(
        best.values(), key=lambda r: (r.timestamp or "", r.app, r.latitude, r.longitude)
    )


def _informativeness(r: SharedLocation) -> int:
    """Rank rows so the dedupe keeps the one carrying the most context."""
    score = 0
    for value in (r.place_name, r.place_address, r.chat, r.sender, r.url):
        if value:
            score += 1
    if r.direction != "unknown":
        score += 1
    if "generic schema scan" not in r.provenance:
        score += 2  # a purpose-built reader knows more than the fallback
    return score


# --- summary ----------------------------------------------------------------


def summarise_shared_locations(rows: list[SharedLocation]) -> dict[str, Any]:
    """Roll shared locations up for the dashboard header and the report."""
    by_app: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    timestamps: list[str] = []
    for r in rows:
        by_app[r.app] = by_app.get(r.app, 0) + 1
        by_kind[r.kind] = by_kind.get(r.kind, 0) + 1
        if r.timestamp:
            timestamps.append(r.timestamp)
    timestamps.sort()
    return {
        "total": len(rows),
        "by_app": by_app,
        "by_kind": by_kind,
        "live_shares": by_kind.get("live", 0) + by_kind.get("live_final", 0),
        "undated": sum(1 for r in rows if not r.timestamp),
        "first_seen": timestamps[0] if timestamps else None,
        "last_seen": timestamps[-1] if timestamps else None,
    }


# Kept for callers that want to spot a coordinate written into free text (a pasted
# "12.9716, 77.5946" in a chat message is a location claim even though no app recorded it).
_TEXT_COORD_RE = re.compile(
    r"(?<![\d.])([+-]?[0-8]?\d(?:\.\d{3,})|90(?:\.0+)?)\s*,\s*"
    r"([+-]?1?[0-7]?\d(?:\.\d{3,})|180(?:\.0+)?)(?![\d.])"
)


def coordinates_in_text(text: str) -> list[tuple[float, float]]:
    """Find decimal-degree coordinate pairs written into message text.

    Requires at least three decimal places on both values, which is what separates a real
    coordinate from an ordinary "3.5, 4.2" appearing in conversation. Callers should treat hits
    as *claims* made in text, not as recorded device positions.
    """
    out: list[tuple[float, float]] = []
    for m in _TEXT_COORD_RE.finditer(text or ""):
        try:
            lat, lon = float(m.group(1)), float(m.group(2))
        except ValueError:
            continue
        if _valid_point(lat, lon):
            out.append((lat, lon))
    return out
