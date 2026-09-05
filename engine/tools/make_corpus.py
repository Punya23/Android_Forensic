"""Synthetic corpus generator — builds a realistic mock Android device tree.

Run::

    python tools/make_corpus.py _corpus/device_A

Produces a fixtures directory the MockDeviceSource can drive, containing:
  * a WhatsApp 'Export Chat' .txt with a plausible conversation
  * SQLite databases with deliberately DELETED rows (for recovery testing) — both an
    in-page-freeblock case and a freelist case
  * GPS-EXIF-tagged JPEG photos + a '.trashed-*' (recycle-bin) photo
  * Tier-1 helper output: contacts.json and calllog.json
  * canned `dumpsys location` output
  * a _device.json intake/pre-state block

Everything is synthetic and self-contained — no real personal data. This is what lets the
team build and demo the full pipeline with no phone, and it doubles as the ground-truth
corpus for measuring recovery/false-positive rates.
"""

from __future__ import annotations

import json
import sqlite3
import struct
import sys
from pathlib import Path

# tools/ is not a package; keep sibling helpers importable however this file is run.
sys.path.insert(0, str(Path(__file__).resolve().parent))

# --- a tiny valid JPEG with a GPS EXIF block (built by hand, no deps) --------
# We construct a minimal baseline JPEG and inject an EXIF APP1 segment with GPS.


def _exif_app1(lat: float, lon: float, dt: str = "2026:07:06 21:45:12") -> bytes:
    """Build a minimal EXIF APP1 segment containing GPSLatitude/Longitude + DateTime."""

    def rational(num: int, den: int) -> bytes:
        return struct.pack(">II", num, den)

    def dms(value: float) -> bytes:
        value = abs(value)
        d = int(value)
        m = int((value - d) * 60)
        s = int(round((value - d - m / 60) * 3600 * 100))
        return rational(d, 1) + rational(m, 1) + rational(s, 100)

    lat_ref = b"N" if lat >= 0 else b"S"
    lon_ref = b"E" if lon >= 0 else b"W"

    # TIFF header (big-endian)
    tiff = b"MM\x00\x2a" + struct.pack(">I", 8)

    # We build two IFDs: IFD0 (with a GPS IFD pointer + DateTime) and the GPS IFD.
    # Offsets are relative to the start of the TIFF header.
    dt_bytes = dt.encode() + b"\x00"

    # Layout plan:
    #   IFD0 at offset 8
    #   IFD0 entries: DateTime(0x0132 ASCII), GPSIFDPointer(0x8825 LONG)
    #   then GPS IFD, then data area (DateTime string, GPS rationals)
    # To keep it simple and valid, place data after both IFDs.

    # Compute sizes
    ifd0_count = 2
    ifd0_size = 2 + ifd0_count * 12 + 4  # count + entries + next-offset
    ifd0_offset = 8
    gps_ifd_offset = ifd0_offset + ifd0_size

    gps_count = 6
    gps_ifd_size = 2 + gps_count * 12 + 4
    data_offset = gps_ifd_offset + gps_ifd_size

    # Data area contents
    data = bytearray()
    dt_off = data_offset + len(data)
    data += dt_bytes
    latref_off = data_offset + len(data)
    data += lat_ref + b"\x00"
    lat_off = data_offset + len(data)
    data += dms(lat)
    lonref_off = data_offset + len(data)
    data += lon_ref + b"\x00"
    lon_off = data_offset + len(data)
    data += dms(lon)

    def entry(tag: int, typ: int, count: int, value_or_offset: int) -> bytes:
        return struct.pack(">HHI", tag, typ, count) + struct.pack(">I", value_or_offset)

    def entry_inline(tag: int, typ: int, count: int, raw: bytes) -> bytes:
        raw = (raw + b"\x00\x00\x00\x00")[:4]
        return struct.pack(">HHI", tag, typ, count) + raw

    # IFD0
    ifd0 = struct.pack(">H", ifd0_count)
    ifd0 += entry(0x0132, 2, len(dt_bytes), dt_off)  # DateTime ASCII
    ifd0 += entry(0x8825, 4, 1, gps_ifd_offset)  # GPS IFD pointer
    ifd0 += struct.pack(">I", 0)  # next IFD = 0

    # GPS IFD
    gps = struct.pack(">H", gps_count)
    gps += entry_inline(0x0001, 2, 2, lat_ref)  # GPSLatitudeRef
    gps += entry(0x0002, 5, 3, lat_off)  # GPSLatitude (3 rationals)
    gps += entry_inline(0x0003, 2, 2, lon_ref)  # GPSLongitudeRef
    gps += entry(0x0004, 5, 3, lon_off)  # GPSLongitude
    gps += entry_inline(0x0005, 1, 1, b"\x00")  # GPSAltitudeRef
    gps += entry(0x0007, 5, 3, lat_off)  # GPSTimeStamp (reuse; filler)
    gps += struct.pack(">I", 0)

    payload = tiff + ifd0 + gps + bytes(data)
    exif = b"Exif\x00\x00" + payload
    app1 = b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif
    return app1


def _minimal_jpeg_with_gps(lat: float, lon: float) -> bytes:
    """A minimal but structurally valid JPEG: SOI + APP1(EXIF/GPS) + a tiny scan.

    Pillow reads the EXIF from APP1 without needing a decodable image body for _getexif,
    but we include minimal SOF/SOS/EOI so the file is a well-formed JPEG."""
    soi = b"\xff\xd8"
    app1 = _exif_app1(lat, lon)
    # Minimal grayscale 1x1: DQT, SOF0, DHT, SOS with a couple of entropy bytes, EOI.
    dqt = b"\xff\xdb\x00\x43\x00" + bytes([16] * 64)
    sof = b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x11\x00"
    dht = b"\xff\xc4\x00\x14\x00" + bytes([1] + [0] * 15) + b"\x00"
    sos = b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00" + b"\xd2\xcf\x20"
    eoi = b"\xff\xd9"
    return soi + app1 + dqt + sof + dht + sos + eoi


# --- WhatsApp export ---------------------------------------------------------
_WA_CHAT = """\
[06/07/2026, 20:59:11] Messages and calls are end-to-end encrypted.
[06/07/2026, 21:00:04] Rahul Verma: Are we still on for tonight?
[06/07/2026, 21:00:39] Imran K: Yes. Bring the package to the docks at midnight.
[06/07/2026, 21:01:12] Rahul Verma: The cash transfer to account 4471 is done.
[06/07/2026, 21:02:50] Imran K: Good. Delete this chat after you read it.
[06/07/2026, 21:03:31] Rahul Verma: Understood. Meeting location changed to warehouse 9.
[06/07/2026, 21:04:07] Imran K: I have the weapon ready if needed.
[06/07/2026, 21:05:20] Rahul Verma: No violence. Just the delivery.
"""


def _build_messages_db(path: Path, deleted_ids: list[int], bulk: bool = False) -> None:
    """Create a msgstore-like SQLite DB and delete some rows (leaving them recoverable)."""
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE messages (_id INTEGER PRIMARY KEY, chat TEXT, "
        "sender TEXT, body TEXT, timestamp INTEGER)"
    )
    rows = [
        ("docks-crew", "Rahul", "Are we still on for tonight?", 1751826004000),
        (
            "docks-crew",
            "Imran",
            "Bring the package to the docks at midnight",
            1751826039000,
        ),
        (
            "docks-crew",
            "Rahul",
            "The cash transfer to account 4471 is done",
            1751826072000,
        ),
        ("docks-crew", "Imran", "Delete this chat after you read it", 1751826170000),
        (
            "docks-crew",
            "Rahul",
            "Meeting location changed to warehouse 9",
            1751826211000,
        ),
        ("docks-crew", "Imran", "I have the weapon ready if needed", 1751826247000),
        ("docks-crew", "Rahul", "No violence just the delivery", 1751826320000),
    ]
    if bulk:
        for i in range(400):
            rows.append(
                (
                    f"grp{i%5}",
                    f"user{i%9}",
                    f"routine message {i} regarding shipment {i%17}",
                    1751800000000 + i * 1000,
                )
            )
    for r in rows:
        con.execute(
            "INSERT INTO messages(chat,sender,body,timestamp) VALUES (?,?,?,?)", r
        )
    con.commit()
    if deleted_ids:
        con.execute(
            f"DELETE FROM messages WHERE _id IN "
            f"({','.join('?' * len(deleted_ids))})",
            deleted_ids,
        )
        con.commit()
    _add_whatsapp_location_shares(con)
    con.close()


def _add_whatsapp_location_shares(con: sqlite3.Connection) -> None:
    """Add the modern WhatsApp location schema (`message` + `message_location`).

    Location shares carry no message text, so they are invisible to a parser that reads message
    bodies — which is exactly why the corpus needs them: without these rows the shared-location
    path is never exercised end to end.

    Three cases are represented, because they mean different things: an outgoing pin (the
    device's own position), an incoming pin (where the *other party* claimed to be), and an
    expired live share, which also records the final position the sharer reached.
    """
    con.execute(
        "CREATE TABLE IF NOT EXISTS jid (_id INTEGER PRIMARY KEY, raw_string TEXT)"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS chat (_id INTEGER PRIMARY KEY, jid_row_id INTEGER)"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS message (_id INTEGER PRIMARY KEY, chat_row_id INTEGER, "
        "from_me INTEGER, timestamp INTEGER)"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS message_location ("
        "message_row_id INTEGER, latitude REAL, longitude REAL, place_name TEXT, "
        "place_address TEXT, url TEXT, live_location_share_duration INTEGER, "
        "live_location_final_latitude REAL, live_location_final_longitude REAL, "
        "live_location_final_timestamp INTEGER)"
    )
    con.execute("INSERT INTO jid(_id, raw_string) VALUES (1, '919820011223@s.whatsapp.net')")
    con.execute("INSERT INTO chat(_id, jid_row_id) VALUES (1, 1)")
    con.executemany(
        "INSERT INTO message(_id, chat_row_id, from_me, timestamp) VALUES (?,?,?,?)",
        [
            (101, 1, 1, 1751826100000),  # outgoing pin
            (102, 1, 0, 1751826400000),  # incoming pin — counterparty's position
            (103, 1, 1, 1751826700000),  # outgoing live share, since expired
        ],
    )
    con.executemany(
        "INSERT INTO message_location VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            (
                101, 18.9220, 72.8347, "Mumbai Docks", "Dockyard Road, Mumbai",
                "https://maps.google.com/?q=18.9220,72.8347", 0, None, None, None,
            ),
            (
                102, 19.0330, 72.8570, "Warehouse 9", "Dharavi, Mumbai", "", 0,
                None, None, None,
            ),
            (
                103, 19.0176, 72.8562, "", "", "", 3600,
                18.9500, 72.8400, 1751830300000,
            ),
        ],
    )
    con.commit()


def _contacts_json() -> list[dict]:
    return [
        {"name": "Imran K", "number": "+91 98200 44711", "email": ""},
        {"name": "Rahul Verma", "number": "+91 99300 55822", "email": "rv@example.in"},
        {"name": "Warehouse 9", "number": "+91 90000 09090", "email": ""},
        {"name": "Auntie", "number": "+91 90011 22334", "email": ""},
    ]


def _calllog_json() -> list[dict]:
    base = 1751826000000
    return [
        {
            "number": "+91 98200 44711",
            "name": "Imran K",
            "type": 2,
            "date": base + 100000,
            "duration": 154,
        },
        {
            "number": "+91 99300 55822",
            "name": "Rahul Verma",
            "type": 1,
            "date": base + 500000,
            "duration": 42,
        },
        {
            "number": "+91 90000 09090",
            "name": "",
            "type": 3,
            "date": base + 900000,
            "duration": 0,
        },
    ]


def _build_telegram_db(path: Path) -> None:
    """A Telegram-style plaintext chat store (messages table) with deleted rows.

    Real Telegram cache4.db stores message text inside TL-serialized BLOBs and needs root;
    this synthetic store uses a readable schema so the heuristic app-DB parser can surface
    live messages and the SQLite carver can recover the deleted ones — demonstrating the
    multi-app capability honestly (see parsers/appdb.py notes)."""
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE messages (mid INTEGER PRIMARY KEY, dialog TEXT, "
        "sender TEXT, body TEXT, date INTEGER)"
    )
    rows = [
        (
            "secret-9",
            "Imran",
            "Switch to Telegram, WhatsApp is compromised",
            1751826400,
        ),
        ("secret-9", "Rahul", "Ok. New drop point is pier 4 warehouse", 1751826460),
        ("secret-9", "Imran", "Payment of 5 lakh moved via hawala today", 1751826520),
        ("secret-9", "Rahul", "Delete everything before the raid", 1751826580),
        ("secret-9", "Imran", "Passport and fake id ready for pickup", 1751826640),
    ]
    for r in rows:
        con.execute("INSERT INTO messages(dialog,sender,body,date) VALUES (?,?,?,?)", r)
    con.commit()
    con.execute("DELETE FROM messages WHERE mid IN (3, 4, 5)")  # deleted secret chats
    con.commit()
    _add_telegram_geo_messages(con)
    con.close()


def _add_telegram_geo_messages(con: sqlite3.Connection) -> None:
    """Add a `messages_v2` table holding real TL-serialised ``geoPoint`` blobs.

    Telegram has no coordinate columns — a shared location is bytes inside the message blob, so
    the only way to recover it is to carve the ``geoPoint`` constructor. The blobs here are
    byte-accurate (little-endian constructor id, then **longitude before latitude** as two
    doubles), so the carver is exercised against the real wire format rather than a stand-in
    that would let a byte-order bug pass unnoticed.
    """
    con.execute(
        "CREATE TABLE IF NOT EXISTS messages_v2 (mid INTEGER PRIMARY KEY, uid INTEGER, "
        "date INTEGER, out INTEGER, data BLOB)"
    )

    def geo_blob(lat: float, lon: float, legacy: bool = False) -> bytes:
        if legacy:
            # geoPoint#2049d70c long:double lat:double access_hash:long
            return (
                b"\x0f\x00\x00\x00"
                + struct.pack("<I", 0x2049D70C)
                + struct.pack("<dd", lon, lat)
                + struct.pack("<q", 0)
            )
        # geoPoint#b2a2f663 flags:# long:double lat:double access_hash:long
        return (
            b"\x0f\x00\x00\x00"
            + struct.pack("<I", 0xB2A2F663)
            + struct.pack("<I", 0)
            + struct.pack("<dd", lon, lat)
            + struct.pack("<q", 0)
        )

    con.executemany(
        "INSERT INTO messages_v2(mid, uid, date, out, data) VALUES (?,?,?,?,?)",
        [
            (901, 77001, 1751826800, 1, geo_blob(18.9500, 72.8400)),
            (902, 77001, 1751826900, 0, geo_blob(19.0176, 72.8562, legacy=True)),
        ],
    )
    con.commit()


def _build_instagram_db(path: Path) -> None:
    """A synthetic Instagram direct.db (threads + messages, µs timestamps) with deleted rows.

    Mirrors the real schema shape (messages.thread_id_published ↔ threads.thread_id, text +
    epoch-microsecond timestamps) closely enough that the Tier-2 Instagram parser surfaces live
    messages and the SQLite carver recovers the deleted ones — demonstrating the capability
    honestly on a rooted-device / image stand-in.
    """
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE threads (thread_id TEXT, thread_title TEXT)")
    con.execute(
        "CREATE TABLE messages (_id INTEGER PRIMARY KEY, thread_id_published TEXT, "
        "user_id TEXT, text TEXT, timestamp INTEGER)"
    )
    con.execute("INSERT INTO threads VALUES ('t_9931', 'imran_k')")
    base = 1751826000000000  # epoch microseconds
    rows = [
        ("t_9931", "778812", "moving the goods through the new route", base),
        ("t_9931", "119020", "understood, keep it off whatsapp", base + 60_000_000),
        (
            "t_9931",
            "778812",
            "cash is in account 4471, delete this",
            base + 120_000_000,
        ),
        ("t_9931", "119020", "the passport and fake id are ready", base + 180_000_000),
    ]
    for r in rows:
        con.execute(
            "INSERT INTO messages(thread_id_published,user_id,text,timestamp) "
            "VALUES (?,?,?,?)",
            r,
        )
    con.commit()
    con.execute("DELETE FROM messages WHERE _id IN (3, 4)")  # deleted incriminating DMs
    con.commit()
    con.close()


def _pb_text(s: str) -> bytes:
    """Encode a string as protobuf field 2, wire type 2 (length-delimited) — like Snapchat."""
    b = s.encode("utf-8")
    return bytes([0x12, len(b)]) + b


def _build_snapchat_dbs(arroyo_path: Path, main_path: Path) -> None:
    """Synthetic Snapchat arroyo.db (conversation_message protobuf blobs) + main.db (Friend)."""
    if arroyo_path.exists():
        arroyo_path.unlink()
    con = sqlite3.connect(arroyo_path)
    con.execute(
        "CREATE TABLE conversation_message (_id INTEGER PRIMARY KEY, "
        "client_conversation_id TEXT, server_message_id INTEGER, message_content BLOB, "
        "creation_timestamp INTEGER, content_type INTEGER, sender_id TEXT)"
    )
    base = 1751826000000  # epoch ms
    msgs = [
        ("conv_a", 1, _pb_text("the drop is at pier 4 tonight"), base, 1, "u_100"),
        (
            "conv_a",
            2,
            _pb_text("payment moved via hawala, no trace"),
            base + 60000,
            1,
            "u_200",
        ),
        (
            "conv_a",
            3,
            _pb_text("burn the sim and delete everything"),
            base + 120000,
            1,
            "u_100",
        ),
    ]
    for m in msgs:
        con.execute(
            "INSERT INTO conversation_message(client_conversation_id,server_message_id,"
            "message_content,creation_timestamp,content_type,sender_id) "
            "VALUES (?,?,?,?,?,?)",
            m,
        )
    con.commit()
    con.execute("DELETE FROM conversation_message WHERE _id = 3")  # ephemeral, deleted
    con.commit()
    con.close()

    if main_path.exists():
        main_path.unlink()
    con = sqlite3.connect(main_path)
    con.execute("CREATE TABLE Friend (userId TEXT, username TEXT, displayName TEXT)")
    con.execute("INSERT INTO Friend VALUES ('u_100', 'imran_k99', 'Imran')")
    con.execute("INSERT INTO Friend VALUES ('u_200', 'rahul_v', 'Rahul Verma')")
    con.commit()
    con.close()


def _media_inventory_json() -> list[dict]:
    """A synthetic MediaStore catalogue: mix of camera, WhatsApp, a trashed and a geotagged item."""
    base_s = 1751800000  # epoch seconds
    base_ms = base_s * 1000
    return [
        {
            "kind": "image",
            "id": 1001,
            "display_name": "IMG_20260706_2145.jpg",
            "mime_type": "image/jpeg",
            "size": 2_845_112,
            "date_added": base_s,
            "date_modified": base_s,
            "date_taken": base_ms,
            "width": 4032,
            "height": 3024,
            "duration": 0,
            "bucket": "Camera",
            "owner_package": "com.android.camera",
            "relative_path": "DCIM/Camera/",
            "data_path": "/storage/emulated/0/DCIM/Camera/IMG_20260706_2145.jpg",
            "is_trashed": False,
            "is_favorite": True,
            "is_pending": False,
            "gps_lat": 19.0759,
            "gps_lon": 72.8776,
        },
        {
            "kind": "image",
            "id": 1002,
            "display_name": "IMG-20260706-WA0007.jpg",
            "mime_type": "image/jpeg",
            "size": 128_442,
            "date_added": base_s + 3600,
            "date_modified": base_s + 3600,
            "date_taken": base_ms + 3600000,
            "width": 1600,
            "height": 1200,
            "duration": 0,
            "bucket": "WhatsApp Images",
            "owner_package": "com.whatsapp",
            "relative_path": "Pictures/WhatsApp/",
            "data_path": "/storage/emulated/0/Pictures/WhatsApp/IMG-20260706-WA0007.jpg",
            "is_trashed": False,
            "is_favorite": False,
            "is_pending": False,
        },
        {
            "kind": "image",
            "id": 1003,
            "display_name": "evidence_photo.jpg",
            "mime_type": "image/jpeg",
            "size": 3_112_004,
            "date_added": base_s + 7200,
            "date_modified": base_s + 7200,
            "date_taken": base_ms + 7200000,
            "width": 4032,
            "height": 3024,
            "duration": 0,
            "bucket": "Camera",
            "owner_package": "com.android.camera",
            "relative_path": "DCIM/Camera/",
            "data_path": "/storage/emulated/0/DCIM/Camera/evidence_photo.jpg",
            "is_trashed": True,
            "is_favorite": False,
            "is_pending": False,
            "gps_lat": 18.9220,
            "gps_lon": 72.8347,
        },
        {
            "kind": "video",
            "id": 2001,
            "display_name": "VID_20260706_2200.mp4",
            "mime_type": "video/mp4",
            "size": 41_882_100,
            "date_added": base_s + 9000,
            "date_modified": base_s + 9000,
            "date_taken": base_ms + 9000000,
            "width": 1920,
            "height": 1080,
            "duration": 34000,
            "bucket": "Camera",
            "owner_package": "com.android.camera",
            "relative_path": "DCIM/Camera/",
            "data_path": "/storage/emulated/0/DCIM/Camera/VID_20260706_2200.mp4",
            "is_trashed": False,
            "is_favorite": False,
            "is_pending": False,
        },
        {
            "kind": "audio",
            "id": 3001,
            "display_name": "PTT-20260706-WA0002.opus",
            "mime_type": "audio/ogg",
            "size": 88_210,
            "date_added": base_s + 12000,
            "date_modified": base_s + 12000,
            "date_taken": 0,
            "width": 0,
            "height": 0,
            "duration": 12000,
            "bucket": "WhatsApp Voice Notes",
            "owner_package": "com.whatsapp",
            "relative_path": "Pictures/WhatsApp/Media/WhatsApp Voice Notes/",
            "data_path": "/storage/emulated/0/.../PTT-20260706-WA0002.opus",
            "is_trashed": False,
            "is_favorite": False,
            "is_pending": False,
        },
    ]


def _apps_json() -> list[dict]:
    """Synthetic installed-app inventory: messaging apps, a crypto wallet, and a vault app."""
    base = 1740000000000  # ms

    def app(pkg, label, cat, notable, ver="1.0", danger=None, system=False):
        return {
            "package": pkg,
            "label": label,
            "version_name": ver,
            "version_code": 100,
            "first_install": base,
            "last_update": base + 5_000_000,
            "installer": "com.android.vending",
            "is_system": system,
            "category": cat,
            "friendly_name": label if notable else None,
            "notable": notable,
            "requested_permissions": (danger or []),
            "granted_permissions": (danger or []),
        }

    return [
        app(
            "com.whatsapp",
            "WhatsApp",
            "messaging",
            True,
            "2.24.1",
            [
                "android.permission.READ_CONTACTS",
                "android.permission.CAMERA",
                "android.permission.RECORD_AUDIO",
            ],
        ),
        app(
            "org.telegram.messenger",
            "Telegram",
            "messaging",
            True,
            "10.9.0",
            [
                "android.permission.READ_CONTACTS",
                "android.permission.ACCESS_FINE_LOCATION",
            ],
        ),
        app(
            "com.instagram.android",
            "Instagram",
            "messaging",
            True,
            "312.0",
            ["android.permission.CAMERA", "android.permission.READ_MEDIA_IMAGES"],
        ),
        app(
            "com.snapchat.android",
            "Snapchat",
            "messaging",
            True,
            "12.60",
            ["android.permission.CAMERA", "android.permission.ACCESS_FINE_LOCATION"],
        ),
        app(
            "com.calculator.vault.hider",
            "Calculator Vault",
            "anti_forensic",
            True,
            "3.2",
            ["android.permission.READ_EXTERNAL_STORAGE", "android.permission.CAMERA"],
        ),
        app("io.metamask", "MetaMask", "crypto", True, "7.10", []),
        app("com.android.chrome", "Chrome", "browser", True, "126.0", []),
        app("com.android.settings", "Settings", "other", False, "14", [], system=True),
        app(
            "com.google.android.gms",
            "Google Play services",
            "other",
            False,
            "24.0",
            [],
            system=True,
        ),
    ]


def _accounts_json() -> list[dict]:
    return [
        {"name": "subject.device@gmail.com", "type": "com.google", "app": "Google"},
        {"name": "WhatsApp", "type": "com.whatsapp", "app": "WhatsApp"},
        {"name": "Telegram", "type": "org.telegram.messenger", "app": "Telegram"},
        {"name": "imran_k99", "type": "com.snapchat.android", "app": "Snapchat"},
    ]


def _calendar_json() -> list[dict]:
    base = 1751900000000  # ms
    return [
        {
            "title": "Meet at warehouse 9",
            "dtstart": base,
            "dtend": base + 3600000,
            "location": "Warehouse 9, Dockyard Rd",
            "description": "bring the package",
            "organizer": "imran",
            "calendar": "Personal",
            "all_day": False,
        },
        {
            "title": "Pickup — pier 4",
            "dtstart": base + 86400000,
            "dtend": base + 86400000 + 1800000,
            "location": "Pier 4",
            "description": "",
            "organizer": "",
            "calendar": "Personal",
            "all_day": False,
        },
    ]


def _usage_json() -> list[dict]:
    now = 1751999000000  # ms
    return [
        {"package": "com.whatsapp", "total_foreground_ms": 5_400_000, "last_used": now},
        {
            "package": "org.telegram.messenger",
            "total_foreground_ms": 3_600_000,
            "last_used": now - 100000,
        },
        {
            "package": "com.snapchat.android",
            "total_foreground_ms": 1_200_000,
            "last_used": now - 500000,
        },
        {
            "package": "com.calculator.vault.hider",
            "total_foreground_ms": 900_000,
            "last_used": now - 200000,
        },
        {
            "package": "io.metamask",
            "total_foreground_ms": 600_000,
            "last_used": now - 900000,
        },
    ]


def _sms_json() -> list[dict]:
    base = 1751826000000
    return [
        {
            "address": "+91 98200 44711",
            "body": "Bank OTP is 448192 do not share",
            "type": 1,
            "date": base + 10000,
        },
        {
            "address": "VM-HDFCBK",
            "body": "Rs 500000 debited from a/c XX4471 via NEFT",
            "type": 1,
            "date": base + 60000,
        },
        {
            "address": "+91 99300 55822",
            "body": "reached the docks waiting",
            "type": 1,
            "date": base + 120000,
        },
        {
            "address": "+91 99300 55822",
            "body": "coming in 5",
            "type": 2,
            "date": base + 130000,
        },
    ]


def _build_browser_history(path: Path) -> None:
    """A minimal Chrome-style History DB (urls table) with WebKit-epoch timestamps."""
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT, "
        "visit_count INTEGER, last_visit_time INTEGER)"
    )
    # WebKit epoch: microseconds since 1601-01-01. 13800000000000000 ~ 2038; use realistic 2026.
    base = 13_360_000_000_000_000
    urls = [
        (
            "https://www.google.com/search?q=how+to+wipe+android+phone",
            "how to wipe android - Google",
            4,
            base + 1_000_000_000,
        ),
        (
            "https://coinmarketcap.com/currencies/monero/",
            "Monero price",
            7,
            base + 2_000_000_000,
        ),
        ("https://protonmail.com/login", "Proton Mail login", 12, base + 3_000_000_000),
        (
            "https://www.google.com/search?q=untraceable+sim+card",
            "untraceable sim - Google",
            3,
            base + 4_000_000_000,
        ),
        (
            "https://en.wikipedia.org/wiki/Hawala",
            "Hawala - Wikipedia",
            2,
            base + 5_000_000_000,
        ),
        # Map links. Each exercises a different URL pattern and, more importantly, a different
        # evidential meaning — a navigation destination is intent to travel, a viewport is only
        # a place that was looked at, and a Street View is reconnaissance of an address.
        (
            "https://www.google.com/maps/dir/?api=1&destination=18.9220,72.8347",
            "Directions to Mumbai Docks - Google Maps",
            6,
            base + 6_000_000_000,
        ),
        (
            "https://www.google.com/maps/place/Warehouse+9/@19.0330,72.8570,17z/"
            "data=!3m1!4b1!4m5!3m4!8m2!3d19.0335!4d72.8575",
            "Warehouse 9 - Google Maps",
            9,
            base + 7_000_000_000,
        ),
        (
            "https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=19.0176,72.8562",
            "Street View - Google Maps",
            2,
            base + 8_000_000_000,
        ),
        (
            "https://www.openstreetmap.org/#map=16/18.9500/72.8400",
            "OpenStreetMap",
            1,
            base + 9_000_000_000,
        ),
        (
            "https://www.google.com/maps/search/scrap+dealers+near+dockyard+road/",
            "scrap dealers near dockyard road - Google Maps",
            3,
            base + 10_000_000_000,
        ),
        # Surviving search queries. Without at least one live search row the Search
        # History view is empty for a reason that reads as "nothing was searched"
        # rather than "the searches that existed were deleted" — and the two deleted
        # rows below are the whole point of the narrative. Keeping both states in the
        # same table is what makes the deletion legible.
        (
            "https://www.google.com/search?q=customs+clearance+pier+4+timings",
            "customs clearance pier 4 timings - Google",
            2,
            base + 11_000_000_000,
        ),
        (
            "https://www.google.com/search?q=monero+to+inr",
            "monero to inr - Google",
            5,
            base + 12_000_000_000,
        ),
        (
            "https://www.bing.com/search?q=burner+phone+shops+mumbai",
            "burner phone shops mumbai - Bing",
            1,
            base + 13_000_000_000,
        ),
    ]
    for u in urls:
        con.execute(
            "INSERT INTO urls(url,title,visit_count,last_visit_time) VALUES (?,?,?,?)",
            u,
        )
    con.commit()
    con.execute("DELETE FROM urls WHERE id IN (1, 4)")  # cleared search history
    con.commit()
    con.close()


def _screenshot_png() -> bytes:
    """A small synthetic 'lock screen' style PNG for the manual-capture demo."""
    # A tiny solid-colour PNG built by hand (64x64, dark). Valid minimal PNG.
    import struct as _s
    import zlib as _z

    w = h = 64
    raw = bytearray()
    for y in range(h):
        raw.append(0)  # filter type 0
        for x in range(w):
            raw += bytes((22, 26, 31))  # RGB matching the app panel colour

    def chunk(typ: bytes, data: bytes) -> bytes:
        return (
            _s.pack(">I", len(data))
            + typ
            + data
            + _s.pack(">I", _z.crc32(typ + data) & 0xFFFFFFFF)
        )

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = _s.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    idat = _z.compress(bytes(raw))
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def _dumpsys_location() -> str:
    return (
        "LocationManagerService:\n"
        "  last location for provider fused:\n"
        "    Location[fused 19.075983,72.877655 hAcc=12 et=+1d2h34m ...]\n"
        "  last location for provider gps:\n"
        "    Location[gps 19.076100,72.878001 hAcc=8 et=+1d2h30m ...]\n"
    )


def build(dest: Path) -> None:
    sdcard = dest / "sdcard"
    dcim = sdcard / "DCIM" / "Camera"
    pictures = sdcard / "Pictures"
    wa_media = sdcard / "Android/media/com.whatsapp/WhatsApp/Media/WhatsApp Images"
    wa_db_dir = sdcard / "Android/media/com.whatsapp/WhatsApp/Databases"
    tg_media = sdcard / "Android/media/org.telegram.messenger/Telegram/Telegram Images"
    downloads = sdcard / "Download"
    shell = dest / "_shell"
    for d in (dcim, pictures, wa_media, wa_db_dir, tg_media, downloads, shell):
        d.mkdir(parents=True, exist_ok=True)

    # GPS-tagged photos (Mumbai dockside coordinates for the narrative)
    (dcim / "IMG_20260706_2145.jpg").write_bytes(
        _minimal_jpeg_with_gps(19.0759, 72.8776)
    )
    (dcim / "IMG_20260706_2150.jpg").write_bytes(
        _minimal_jpeg_with_gps(19.0654, 72.8412)
    )
    # A recently-deleted photo in the MediaStore trash (.trashed-<epoch>-name)
    (dcim / ".trashed-1751900000-IMG_evidence.jpg").write_bytes(
        _minimal_jpeg_with_gps(18.9220, 72.8347)
    )
    # WhatsApp media (already-decrypted, non-root reachable)
    (wa_media / "IMG-20260706-WA0007.jpg").write_bytes(
        _minimal_jpeg_with_gps(19.0760, 72.8779)
    )
    # Telegram cached media (only present because 'Save to Gallery' was on)
    (tg_media / "photo_2026-07-06_21-10-33.jpg").write_bytes(
        _minimal_jpeg_with_gps(19.07, 72.88)
    )

    # WhatsApp export text
    (downloads / "WhatsApp Chat with docks-crew.txt").write_text(
        _WA_CHAT, encoding="utf-8"
    )

    # SQLite DBs with deleted rows
    _build_messages_db(
        wa_db_dir / "msgstore.db", deleted_ids=[3, 4, 6]
    )  # in-page freeblock
    _build_messages_db(
        sdcard / "Download" / "chatcache.db",
        deleted_ids=list(range(20, 260)),
        bulk=True,
    )  # freelist case

    # Telegram-style store (multi-app deleted recovery) — placed in shared storage for demo
    tg_db_dir = sdcard / "Android/media/org.telegram.messenger/Telegram"
    _build_telegram_db(tg_db_dir / "cache4.db")

    # Chrome-style browser history with cleared (deleted) searches
    _build_browser_history(downloads / "History")

    # Tier-1 helper outputs (simulate a sideloaded Collector run)
    (downloads / "contacts.json").write_text(json.dumps(_contacts_json(), indent=2))
    (downloads / "calllog.json").write_text(json.dumps(_calllog_json(), indent=2))
    (downloads / "sms.json").write_text(json.dumps(_sms_json(), indent=2))

    # Expanded Tier-1 Collector outputs (media inventory, apps, accounts, calendar, usage)
    (downloads / "media_inventory.json").write_text(
        json.dumps(_media_inventory_json(), indent=2)
    )
    (downloads / "apps.json").write_text(json.dumps(_apps_json(), indent=2))
    (downloads / "accounts.json").write_text(json.dumps(_accounts_json(), indent=2))
    (downloads / "calendar.json").write_text(json.dumps(_calendar_json(), indent=2))
    (downloads / "usage.json").write_text(json.dumps(_usage_json(), indent=2))

    # Tier-2 app-chat stand-ins (represent DBs obtained from a rooted device / image). The
    # engine's app-chat recovery keys on these filenames: direct.db → Instagram, arroyo.db →
    # Snapchat (with its sibling main.db for identity).
    _build_instagram_db(downloads / "direct.db")
    _build_snapchat_dbs(downloads / "arroyo.db", downloads / "main.db")

    # Canned shell output + manual-capture screenshot for the mock source.
    # `dumpsys location` was for a long time the *only* canned reply the corpus
    # shipped, so every other shell-derived stage (notifications, Bluetooth, cell
    # towers, live Wi-Fi, screen/app usage, accounts) parsed an empty string and
    # wrote an empty dataset. corpus_shell supplies the rest.
    (shell / "dumpsys_location.txt").write_text(_dumpsys_location())
    (shell / "screenshot.png").write_bytes(_screenshot_png())
    from corpus_shell import build_shell_fixtures  # noqa: E402  (tools/ is on sys.path)

    _shell_files = build_shell_fixtures(shell)

    # Device intake + pre-state
    (dest / "_device.json").write_text(
        json.dumps(
            {
                "device": {
                    "manufacturer": "Samsung",
                    "brand": "samsung",
                    "model": "SM-G991B (Galaxy S21)",
                    "product": "o1sxxx",
                    "android_version": "14",
                    "sdk": "34",
                    "build_id": "UP1A.231005.007",
                    "serial": "R58N90ABCDE",
                    "imei": "35-901234-567890-1",
                    "carrier": "Airtel",
                    "rooted": False,
                    "os_skin": "One UI 6.1",
                    "oem_quirks": ["knox_container", "secure_folder_opaque", "logsprovider_db"],
                },
                "pre_state": {
                    "screen_locked": False,
                    "battery_level": 76,
                    "device_time": "2026-07-06T22:03:44+0530",
                    "time_skew_seconds": 3,
                    "root_available": False,
                    "note": "MOCK DEVICE — synthetic fixtures for development/demo, not a real seizure",
                },
            },
            indent=2,
        )
    )

    print(f"Corpus written to {dest}")
    print("  - WhatsApp export + 4 SQLite DBs with deleted rows (msgstore, chatcache,")
    print("    Telegram cache4, Chrome History), 5 photos (1 trashed), a screenshot,")
    print("    contacts.json, calllog.json, sms.json, dumpsys location, device intake")
    print(f"  - {len(_shell_files)} canned shell replies (dumpsys notification / "
          "bluetooth_manager /")
    print("    telephony.registry / power / batterystats / usagestats / account /")
    print("    wifi / wifiscanner / connectivity / netstats, plus getprops)")


if __name__ == "__main__":
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("_corpus/device_A")
    dest.mkdir(parents=True, exist_ok=True)
    build(dest)
