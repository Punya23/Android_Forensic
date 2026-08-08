"""Tests for the location-acquisition sources and the unified location trace.

Coverage is deliberately weighted toward the ways a location parser fails *silently*, because
those are the failures that put a wrong coordinate in front of a court:

* a fixed-point column read as degrees (or vice versa) — off by ten million, still "valid";
* a 0,0 zero-fill reported as a position off West Africa;
* longitude/latitude transposed (Telegram's TL schema and the 3GPP `loci` box both put
  longitude first) — every coordinate lands in the wrong hemisphere without erroring;
* an incoming location share attributed to the device owner rather than the counterparty;
* a map the user merely looked at counted as a place the device was.
"""

from __future__ import annotations

import json
import sqlite3
import struct
from pathlib import Path

import pytest

from triage.forensics.location_aggregate import (
    build_location_traces,
    detect_impossible_travel,
    dedupe_traces,
    presence_track,
    summarise_traces,
    traces_to_geojson,
)
from triage.models import LocationPoint
from triage.parsers.app_location import (
    SharedLocation,
    carve_telegram_geopoints,
    coordinates_in_text,
    extract_app_locations,
    extract_sqlite_locations,
    parse_instagram_locations,
    parse_snapchat_locations,
    parse_telegram_locations,
    parse_whatsapp_locations,
    summarise_shared_locations,
)
from triage.parsers.collector import (
    location_collection_meta,
    parse_bluetooth_json,
    parse_collector_manifest,
    parse_location,
    parse_wifi_json,
)
from triage.parsers.google_maps import (
    parse_gms_network_location,
    parse_maps_app_data,
    parse_maps_destination_history,
    parse_maps_myplaces,
    parse_maps_search_history,
)
from triage.parsers.url_location import (
    extract_map_query,
    extract_url_coordinates,
    locations_from_text,
    locations_from_urls,
    summarise_url_locations,
)
from triage.parsers.video_gps import extract_video_gps, parse_iso6709


# ---------------------------------------------------------------------------
# Collector helper-APK outputs
# ---------------------------------------------------------------------------


def _write(path: Path, payload) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_collector_location_parses_each_provider(tmp_path: Path):
    p = _write(
        tmp_path / "location.json",
        [
            {
                "provider": "gps",
                "latitude": 12.9716,
                "longitude": 77.5946,
                "accuracy": 8.5,
                "time": 1751826000000,
                "is_mock": False,
            },
            {
                "provider": "network",
                "latitude": 12.97,
                "longitude": 77.59,
                "accuracy": 950.0,
                "time": 1751826100000,
                "is_mock": False,
            },
            {"provider": "_meta", "providers_seen": ["gps", "network"], "permission": "fine"},
        ],
    )
    rows = parse_location(p)
    assert len(rows) == 2, "the _meta row must not become a coordinate"
    assert {r.source for r in rows} == {"collector:gps", "collector:network"}
    assert rows[0].timestamp == "2025-07-06T18:20:00Z"
    assert "±9m" in rows[0].label or "±8m" in rows[0].label


def test_collector_location_flags_mock_provider(tmp_path: Path):
    """A spoofed fix is still evidence — of spoofing — but must never read as a real position."""
    p = _write(
        tmp_path / "location.json",
        [{"provider": "gps", "latitude": 1.5, "longitude": 2.5, "time": 1751826000000, "is_mock": True}],
    )
    rows = parse_location(p)
    assert len(rows) == 1
    assert "MOCK PROVIDER" in rows[0].label


def test_collector_location_rejects_null_island(tmp_path: Path):
    p = _write(
        tmp_path / "location.json",
        [{"provider": "gps", "latitude": 0.0, "longitude": 0.0, "time": 1751826000000}],
    )
    assert parse_location(p) == []


def test_collector_location_meta_explains_empty_result(tmp_path: Path):
    """Zero fixes with providers listed means 'no cached position', not 'never asked'."""
    p = _write(
        tmp_path / "location.json",
        [{"provider": "_meta", "providers_seen": ["gps", "fused"], "permission": "coarse"}],
    )
    assert parse_location(p) == []
    meta = location_collection_meta(p)
    assert meta["providers_seen"] == ["gps", "fused"]
    assert meta["permission"] == "coarse"


def test_collector_wifi_and_bluetooth_json(tmp_path: Path):
    wifi = _write(
        tmp_path / "wifi.json",
        [
            {"type": "current_connection", "ssid": "CafeNet", "bssid": "AA:BB:CC:DD:EE:FF", "rssi": -55},
            {"type": "saved_network", "ssid": "HomeWiFi", "bssid": "11:22:33:44:55:66"},
        ],
    )
    rows = parse_wifi_json(wifi)
    assert len(rows) == 2
    # BSSIDs key the geolocation lookup, so case must be normalised or the lookup misses.
    assert rows[0]["bssid"] == "aa:bb:cc:dd:ee:ff"
    assert rows[0]["level_dbm"] == -55

    bt = _write(
        tmp_path / "bluetooth.json",
        [
            {"type": "adapter", "enabled": True, "name": "Pixel", "address": "00:11:22:33:44:55"},
            {"type": "bonded_device", "name": "Car Audio", "address": "AA:11:BB:22:CC:33", "uuids": ["x"]},
        ],
    )
    bt_rows = parse_bluetooth_json(bt)
    assert len(bt_rows) == 2
    assert bt_rows[1]["address"] == "aa:11:bb:22:cc:33"


def test_collector_manifest_separates_denied_from_empty(tmp_path: Path):
    p = tmp_path / "collector_manifest.json"
    p.write_text(
        json.dumps(
            {
                "action": "dump_all",
                "collected_at_ms": 1751826000000,
                "collectors": [
                    {"collector": "sms", "status": "denied", "count": 0, "error": "READ_SMS refused"},
                    {"collector": "location", "status": "ok", "count": 2},
                ],
                "permissions": [
                    {"permission": "android.permission.READ_SMS", "granted": False},
                    {"permission": "android.permission.ACCESS_FINE_LOCATION", "granted": True},
                ],
            }
        ),
        encoding="utf-8",
    )
    m = parse_collector_manifest(p)
    assert len(m["denied"]) == 1
    assert m["denied"][0]["collector"] == "sms"
    assert "android.permission.ACCESS_FINE_LOCATION" in m["permissions_granted"]
    assert "android.permission.READ_SMS" in m["permissions_denied"]


# ---------------------------------------------------------------------------
# Video GPS (MP4 / ISO-6709)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("+37.7749-122.4194/", {"lat": 37.7749, "lon": -122.4194}),
        ("+37.7749-122.4194+010.500/", {"lat": 37.7749, "lon": -122.4194, "alt": 10.5}),
        ("-33.8688+151.2093/", {"lat": -33.8688, "lon": 151.2093}),
    ],
)
def test_parse_iso6709_valid(raw, expected):
    assert parse_iso6709(raw) == expected


@pytest.mark.parametrize("raw", ["+00.0000+000.0000/", "", "garbage", "37.7749,-122.4194", None])
def test_parse_iso6709_rejects_bad_input(raw):
    assert parse_iso6709(raw) is None


def _mp4_with_xyz(path: Path, iso: str) -> Path:
    """Build a minimal MP4 whose moov/udta holds a `©xyz` location box."""
    payload = b"\x00" * 2 + b"\x00" * 2 + iso.encode("utf-8")
    xyz = struct.pack(">I", 8 + len(payload)) + b"\xa9xyz" + payload
    udta = struct.pack(">I", 8 + len(xyz)) + b"udta" + xyz
    moov = struct.pack(">I", 8 + len(udta)) + b"moov" + udta
    ftyp = struct.pack(">I", 16) + b"ftyp" + b"isom" + b"\x00\x00\x02\x00"
    path.write_bytes(ftyp + moov)
    return path


def test_extract_video_gps_from_xyz_atom(tmp_path: Path):
    p = _mp4_with_xyz(tmp_path / "VID_0001.mp4", "+19.0760+072.8777/")
    gps = extract_video_gps(p)
    assert gps is not None
    assert gps["lat"] == pytest.approx(19.0760)
    assert gps["lon"] == pytest.approx(72.8777)


def test_extract_video_gps_absent_returns_none(tmp_path: Path):
    """A video shot with location off has no box. 'None' must not become 0,0."""
    p = tmp_path / "plain.mp4"
    ftyp = struct.pack(">I", 16) + b"ftyp" + b"isom" + b"\x00\x00\x02\x00"
    p.write_bytes(ftyp + struct.pack(">I", 8) + b"free")
    assert extract_video_gps(p) is None


def test_extract_video_gps_ignores_non_container(tmp_path: Path):
    p = tmp_path / "notavideo.mp4"
    p.write_bytes(b"this is not an ISO base media file at all")
    assert extract_video_gps(p) is None


# ---------------------------------------------------------------------------
# WhatsApp location shares
# ---------------------------------------------------------------------------


def _whatsapp_modern(path: Path) -> Path:
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE jid(_id INTEGER PRIMARY KEY, raw_string TEXT);
        CREATE TABLE chat(_id INTEGER PRIMARY KEY, jid_row_id INTEGER);
        CREATE TABLE message(_id INTEGER PRIMARY KEY, chat_row_id INTEGER,
                             from_me INTEGER, timestamp INTEGER);
        CREATE TABLE message_location(message_row_id INTEGER, latitude REAL, longitude REAL,
            place_name TEXT, place_address TEXT, url TEXT,
            live_location_share_duration INTEGER,
            live_location_final_latitude REAL, live_location_final_longitude REAL,
            live_location_final_timestamp INTEGER);
        INSERT INTO jid VALUES(1,'919876543210@s.whatsapp.net');
        INSERT INTO chat VALUES(1,1);
        INSERT INTO message VALUES(10,1,1,1751826000000);
        INSERT INTO message VALUES(11,1,0,1751826600000);
        INSERT INTO message_location VALUES(10,12.9716,77.5946,'MG Road','Bengaluru','u',0,NULL,NULL,NULL);
        INSERT INTO message_location VALUES(11,28.6139,77.2090,'','','',3600,28.7041,77.1025,1751830200000);
        """
    )
    con.commit()
    con.close()
    return path


def test_whatsapp_modern_schema_pin_live_and_final(tmp_path: Path):
    rows = parse_whatsapp_locations(_whatsapp_modern(tmp_path / "msgstore.db"))
    kinds = {r.kind for r in rows}
    assert kinds == {"shared", "live", "live_final"}
    pin = next(r for r in rows if r.kind == "shared")
    assert pin.direction == "outgoing"
    assert pin.place_name == "MG Road"
    assert pin.chat == "919876543210", "the JID suffix should be stripped for display"
    # The final position of an expired live share is a separate fact at a separate time.
    final = next(r for r in rows if r.kind == "live_final")
    assert final.latitude == pytest.approx(28.7041)
    assert final.timestamp != next(r for r in rows if r.kind == "live").timestamp


def test_whatsapp_legacy_schema_and_null_island(tmp_path: Path):
    p = tmp_path / "msgstore_legacy.db"
    con = sqlite3.connect(p)
    con.executescript(
        """
        CREATE TABLE messages(_id INTEGER PRIMARY KEY, key_remote_jid TEXT, key_from_me INTEGER,
            timestamp INTEGER, latitude REAL, longitude REAL, media_name TEXT,
            media_caption TEXT, media_url TEXT, media_wa_type INTEGER, media_duration INTEGER);
        INSERT INTO messages VALUES(1,'911@s.whatsapp.net',1,1751826000000,19.0760,72.8777,'Gateway','Mumbai','',5,0);
        INSERT INTO messages VALUES(2,'911@s.whatsapp.net',0,1751826600000,0,0,'','','',5,0);
        INSERT INTO messages VALUES(3,'911@s.whatsapp.net',0,1751827200000,13.0827,80.2707,'','','',16,900);
        """
    )
    con.commit()
    con.close()
    rows = parse_whatsapp_locations(p)
    assert len(rows) == 2, "the 0,0 row must be dropped, not plotted"
    assert {r.kind for r in rows} == {"shared", "live"}
    live = next(r for r in rows if r.kind == "live")
    assert "live-location" in live.flags


# ---------------------------------------------------------------------------
# Telegram TL geoPoint carving
# ---------------------------------------------------------------------------


def _geo_blob(lat: float, lon: float, legacy: bool = False) -> bytes:
    if legacy:
        return b"pad" + struct.pack("<I", 0x2049D70C) + struct.pack("<dd", lon, lat)
    return b"pad" + struct.pack("<I", 0xB2A2F663) + struct.pack("<I", 0) + struct.pack("<dd", lon, lat)


def test_telegram_geopoint_longitude_comes_first():
    """TL puts longitude before latitude. Reading them in order transposes every coordinate."""
    hits = carve_telegram_geopoints(_geo_blob(12.9716, 77.5946))
    assert hits == [(pytest.approx(12.9716), pytest.approx(77.5946))]


def test_telegram_legacy_constructor_also_carved():
    hits = carve_telegram_geopoints(_geo_blob(19.0760, 72.8777, legacy=True))
    assert hits == [(pytest.approx(19.0760), pytest.approx(72.8777))]


def test_telegram_db_geo_messages(tmp_path: Path):
    p = tmp_path / "cache4.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE messages_v2(mid INTEGER, uid INTEGER, date INTEGER, out INTEGER, data BLOB)")
    con.execute("INSERT INTO messages_v2 VALUES(1,555,1751826000,1,?)", (_geo_blob(48.8584, 2.2945),))
    con.execute("INSERT INTO messages_v2 VALUES(2,555,1751826600,0,?)", (b"no coordinate here",))
    con.commit()
    con.close()
    rows = parse_telegram_locations(p)
    assert len(rows) == 1
    assert rows[0].direction == "outgoing"
    assert rows[0].latitude == pytest.approx(48.8584)


# ---------------------------------------------------------------------------
# Instagram / Snapchat / generic scan
# ---------------------------------------------------------------------------


def test_instagram_json_location_share_microsecond_timestamps(tmp_path: Path):
    p = tmp_path / "direct.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE messages(thread_id_published TEXT, user_id TEXT, timestamp INTEGER, message TEXT)")
    con.execute(
        "INSERT INTO messages VALUES('t1','u9',1751826000000000,?)",
        (json.dumps({"location": {"lat": 48.8584, "lng": 2.2945, "name": "Eiffel Tower"}}),),
    )
    con.commit()
    con.close()
    rows = parse_instagram_locations(p)
    assert len(rows) == 1
    assert rows[0].place_name == "Eiffel Tower"
    # µs timestamps must resolve to 2025, not 57000 AD.
    assert rows[0].timestamp is not None and rows[0].timestamp.startswith("2025-")


def test_snapchat_memories_locations(tmp_path: Path):
    p = tmp_path / "memories.db"
    con = sqlite3.connect(p)
    con.execute(
        "CREATE TABLE memories_snap(id INTEGER, has_location INTEGER, latitude REAL, "
        "longitude REAL, create_time INTEGER)"
    )
    con.execute("INSERT INTO memories_snap VALUES(1,1,35.6762,139.6503,1751826000000)")
    con.commit()
    con.close()
    rows = parse_snapchat_locations(p)
    assert len(rows) == 1
    # A saved snap records where it was taken, not a location deliberately shared.
    assert rows[0].kind == "media"


def test_generic_scan_decodes_e7_fixed_point(tmp_path: Path):
    """A *_e7 column read as degrees is off by ten million yet still parses as a float."""
    p = tmp_path / "ridehail.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE trips(lat_e7 INTEGER, lon_e7 INTEGER, created_at INTEGER, title TEXT)")
    con.execute("INSERT INTO trips VALUES(407127530,-740059730,1751826000,'Pickup')")
    con.commit()
    con.close()
    rows = extract_sqlite_locations(p, app="RideHail")
    assert len(rows) == 1
    assert rows[0].latitude == pytest.approx(40.712753)
    assert rows[0].longitude == pytest.approx(-74.005973)


def test_dispatcher_does_not_double_count_the_same_table(tmp_path: Path):
    """The specific reader and the generic scan both see message_location; that is one fact."""
    p = _whatsapp_modern(tmp_path / "msgstore.db")
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE cached_venue(latitude REAL, longitude REAL, name TEXT, date INTEGER)")
    con.execute("INSERT INTO cached_venue VALUES(51.5074,-0.1278,'London office',1751800000)")
    con.commit()
    con.close()

    specific = parse_whatsapp_locations(p)
    combined = extract_app_locations(p)
    assert len(specific) == 3
    # 3 shares + the one table the specific reader does not know about.
    assert len(combined) == 4
    assert sum(1 for r in combined if r.table_name == "cached_venue") == 1


def test_coordinates_in_text_requires_precision():
    """'3.5, 4.2' in conversation is not a coordinate; three decimals is the discriminator."""
    assert coordinates_in_text("meet at 12.9716, 77.5946 tonight") == [
        (pytest.approx(12.9716), pytest.approx(77.5946))
    ]
    assert coordinates_in_text("split it 3.5, 4.2 between us") == []


def test_shared_location_summary_counts_live_shares():
    rows = [
        SharedLocation(app="WhatsApp", latitude=1.1, longitude=2.2, kind="live", timestamp="2026-01-01T00:00:00Z"),
        SharedLocation(app="WhatsApp", latitude=1.2, longitude=2.3, kind="live_final", timestamp="2026-01-01T01:00:00Z"),
        SharedLocation(app="Telegram", latitude=3.3, longitude=4.4, kind="shared"),
    ]
    s = summarise_shared_locations(rows)
    assert s["total"] == 3
    assert s["live_shares"] == 2
    assert s["undated"] == 1
    assert s["by_app"] == {"WhatsApp": 2, "Telegram": 1}


# ---------------------------------------------------------------------------
# Google Maps app-private databases
# ---------------------------------------------------------------------------


def test_maps_destination_history_emits_destination_and_origin(tmp_path: Path):
    p = tmp_path / "da_destination_history"
    con = sqlite3.connect(p)
    con.execute(
        "CREATE TABLE destination_history(time INTEGER, dest_lat INTEGER, dest_lng INTEGER, "
        "dest_title TEXT, dest_address TEXT, source_lat INTEGER, source_lng INTEGER)"
    )
    con.execute(
        "INSERT INTO destination_history VALUES(1751826000000,129716000,775946000,"
        "'MG Road','Bengaluru',128000000,774000000)"
    )
    con.commit()
    con.close()
    rows = parse_maps_destination_history(p)
    sources = {r["source"] for r in rows}
    assert sources == {"maps_destination_history", "maps_directions_origin"}
    dest = next(r for r in rows if r["source"] == "maps_destination_history")
    assert dest["latitude"] == pytest.approx(12.9716)


def test_maps_saved_place_is_labelled_as_a_bookmark(tmp_path: Path):
    p = tmp_path / "gmm_myplaces.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE favorites(name TEXT, latitude REAL, longitude REAL, address TEXT, timestamp INTEGER)")
    con.execute("INSERT INTO favorites VALUES('Home',19.0760,72.8777,'Mumbai',1751800000)")
    con.commit()
    con.close()
    rows = parse_maps_myplaces(p)
    assert len(rows) == 1
    assert rows[0]["source"] == "maps_saved_place"
    assert "not a visit" in rows[0]["provenance"]


def test_maps_search_kept_without_coordinate(tmp_path: Path):
    p = tmp_path / "search_history.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE suggestions(query TEXT, latitude REAL, longitude REAL, timestamp INTEGER)")
    con.execute("INSERT INTO suggestions VALUES('pawn shop near me',NULL,NULL,1751800000)")
    con.commit()
    con.close()
    rows = parse_maps_search_history(p)
    assert len(rows) == 1
    assert rows[0]["latitude"] is None
    assert rows[0]["query"] == "pawn shop near me"


def test_gms_network_location_cache(tmp_path: Path):
    p = tmp_path / "NetworkLocation.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE ncell(cid TEXT, latitude REAL, longitude REAL, accuracy REAL, time INTEGER)")
    con.execute("INSERT INTO ncell VALUES('404-45-1234',13.0827,80.2707,850.0,1751800000000)")
    con.commit()
    con.close()
    rows = parse_gms_network_location(p)
    assert len(rows) == 1
    assert rows[0]["accuracy"] == 850.0
    assert "cell tower" in rows[0]["place_name"]


def test_maps_app_data_walks_a_staged_tree(tmp_path: Path):
    maps = tmp_path / "com.google.android.apps.maps" / "databases"
    maps.mkdir(parents=True)
    con = sqlite3.connect(maps / "gmm_myplaces.db")
    con.execute("CREATE TABLE favorites(name TEXT, latitude REAL, longitude REAL)")
    con.execute("INSERT INTO favorites VALUES('Work',12.9,77.6)")
    con.commit()
    con.close()
    rows = parse_maps_app_data(tmp_path)
    assert any(r["source"] == "maps_saved_place" for r in rows)


def test_maps_readers_do_not_modify_evidence(tmp_path: Path):
    """Opening read-write can checkpoint a WAL and rewrite the file, changing its hash."""
    p = tmp_path / "gmm_myplaces.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE favorites(name TEXT, latitude REAL, longitude REAL)")
    con.execute("INSERT INTO favorites VALUES('Home',19.0,72.8)")
    con.commit()
    con.close()
    before = p.read_bytes()
    parse_maps_myplaces(p)
    assert p.read_bytes() == before


# ---------------------------------------------------------------------------
# URL-derived locations
# ---------------------------------------------------------------------------


def test_google_maps_url_yields_place_and_viewport_separately():
    """`@` is the camera position; `!3d!4d` is the pinned place. They are different points."""
    hits = extract_url_coordinates(
        "https://www.google.com/maps/place/MG+Road/@12.9716,77.5946,17z/"
        "data=!3m1!4b1!4m5!3m4!8m2!3d12.9750!4d77.6000"
    )
    kinds = {h["kind"]: (h["latitude"], h["longitude"]) for h in hits}
    assert kinds["place"] == (pytest.approx(12.9750), pytest.approx(77.6000))
    assert kinds["viewport"] == (pytest.approx(12.9716), pytest.approx(77.5946))


@pytest.mark.parametrize(
    "url,kind",
    [
        ("https://maps.google.com/maps?daddr=13.08,80.27", "destination"),
        ("https://maps.google.com/maps?saddr=12.90,77.50", "origin"),
        ("geo:35.6762,139.6503?q=Tokyo", "shared"),
        ("https://www.openstreetmap.org/#map=15/51.5074/-0.1278", "viewport"),
        ("https://www.bing.com/maps?cp=40.7128~-74.0060", "viewport"),
        ("https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=48.8584,2.2945", "street_view"),
    ],
)
def test_url_patterns_carry_their_meaning(url, kind):
    hits = extract_url_coordinates(url)
    assert hits, f"no coordinate extracted from {url}"
    assert any(h["kind"] == kind for h in hits)


def test_role_prefixed_deep_link_pairs_by_prefix():
    """Pairing the first latitude with the last longitude would invent a coordinate."""
    hits = extract_url_coordinates(
        "https://m.uber.com/ul/?pickup[latitude]=1.3521&pickup[longitude]=103.8198"
        "&dropoff[latitude]=1.2800&dropoff[longitude]=103.8500"
    )
    by_kind = {h["kind"]: (h["latitude"], h["longitude"]) for h in hits}
    assert by_kind["origin"] == (pytest.approx(1.3521), pytest.approx(103.8198))
    assert by_kind["destination"] == (pytest.approx(1.2800), pytest.approx(103.8500))


def test_non_map_search_is_not_a_location():
    assert extract_url_coordinates("https://www.google.com/search?q=weather") == []
    assert extract_map_query("https://www.google.com/search?q=weather") == ""


def test_map_search_without_coordinate_is_kept():
    rows = locations_from_urls(
        [{"url": "https://www.google.com/maps/search/pawn+shops+near+koramangala/", "last_visit": "2026-01-01T00:00:00Z"}]
    )
    assert len(rows) == 1
    assert rows[0].latitude is None
    assert rows[0].kind == "query"
    assert rows[0].query == "pawn shops near koramangala"


def test_locations_from_text_finds_links_in_a_message_body():
    rows = locations_from_text("check this geo:12.9716,77.5946 and https://maps.google.com/?ll=19.0760,72.8777")
    assert {r.kind for r in rows} == {"shared", "viewport"}


def test_url_summary_separates_points_from_searches():
    rows = locations_from_urls(
        [
            {"url": "https://maps.google.com/maps?daddr=13.08,80.27"},
            {"url": "https://www.google.com/maps/search/pawn+shop/"},
        ]
    )
    s = summarise_url_locations(rows)
    assert s["with_coordinates"] == 1
    assert s["searches_only"] == 1
    assert s["destinations"] == 1


# ---------------------------------------------------------------------------
# Unified trace
# ---------------------------------------------------------------------------


def _sample_trace():
    return build_location_traces(
        location_points=[
            LocationPoint(latitude=12.9716, longitude=77.5946, source="exif",
                          timestamp="2026-07-01T10:00:00Z", label="photo A"),
            # Same photo seen a second way — one fact, must collapse.
            LocationPoint(latitude=12.9716, longitude=77.5946, source="mediastore",
                          timestamp="2026-07-01T10:00:00Z", label="image A"),
            LocationPoint(latitude=12.9800, longitude=77.6000, source="collector:gps",
                          timestamp="2026-07-01T11:00:00Z", label="fix"),
        ],
        shared_locations=[
            SharedLocation(app="WhatsApp", latitude=19.0760, longitude=72.8777,
                           timestamp="2026-07-01T12:00:00Z", kind="shared", direction="outgoing"),
            SharedLocation(app="WhatsApp", latitude=28.6139, longitude=77.2090,
                           timestamp="2026-07-01T13:00:00Z", kind="shared", direction="incoming"),
        ],
        maps_rows=[
            {"latitude": 51.5074, "longitude": -0.1278, "source": "maps_saved_place",
             "place_name": "Home", "timestamp": "2026-07-01T09:00:00Z"},
        ],
    )


def test_trace_merges_duplicate_media_but_not_across_categories():
    rows = _sample_trace()
    media = [r for r in rows if r.category == "media_capture"]
    assert len(media) == 1, "EXIF and MediaStore describing one photo is one fact"
    assert len({r.category for r in rows}) >= 3


def test_incoming_share_is_not_the_devices_position():
    rows = _sample_trace()
    incoming = next(r for r in rows if r.latitude == pytest.approx(28.6139))
    assert incoming.is_presence is False
    assert "counterparty-position" in incoming.flags
    outgoing = next(r for r in rows if r.latitude == pytest.approx(19.0760))
    assert outgoing.is_presence is True


def test_saved_place_is_interest_not_presence():
    rows = _sample_trace()
    saved = next(r for r in rows if r.place_name == "Home")
    assert saved.category == "interest"
    assert saved.is_presence is False
    assert saved.tier == "tier2", "app-private Maps data needs root"


def test_summary_reports_presence_and_interest_separately():
    s = summarise_traces(_sample_trace())
    assert s["presence_points"] + s["interest_points"] == s["with_coordinates"]
    assert s["presence_points"] == 3  # photo, GPS fix, outgoing share
    assert "caveat" in s and "not interchangeable" in s["caveat"]


def test_presence_track_excludes_interest_points():
    """A route drawn through a searched-for place would be a journey that never happened."""
    track = presence_track(_sample_trace())
    assert all(r.is_presence for r in track)
    assert all(r.timestamp is not None for r in track)
    assert [r.timestamp for r in track] == sorted(r.timestamp for r in track)


def test_impossible_travel_flags_physically_impossible_pairs():
    rows = build_location_traces(
        location_points=[
            LocationPoint(latitude=12.9716, longitude=77.5946, source="dumpsys",
                          timestamp="2026-07-01T10:00:00Z", label="a"),
            LocationPoint(latitude=51.5074, longitude=-0.1278, source="dumpsys",
                          timestamp="2026-07-01T10:30:00Z", label="b"),
        ]
    )
    anomalies = detect_impossible_travel(rows)
    assert len(anomalies) == 1
    assert anomalies[0]["implied_kmh"] > 900
    assert anomalies[0]["requires_verification"] is True


def test_impossible_travel_ignores_ordinary_movement():
    rows = build_location_traces(
        location_points=[
            LocationPoint(latitude=12.9716, longitude=77.5946, source="dumpsys",
                          timestamp="2026-07-01T10:00:00Z", label="a"),
            LocationPoint(latitude=12.9900, longitude=77.6100, source="dumpsys",
                          timestamp="2026-07-01T11:00:00Z", label="b"),
        ]
    )
    assert detect_impossible_travel(rows) == []


def test_undated_rows_sort_last_not_first():
    rows = dedupe_traces(
        build_location_traces(
            location_points=[
                LocationPoint(latitude=1.5, longitude=2.5, source="exif", timestamp=None, label="undated"),
                LocationPoint(latitude=3.5, longitude=4.5, source="exif",
                              timestamp="2026-07-01T10:00:00Z", label="dated"),
            ]
        )
    )
    assert rows[0].timestamp is not None
    assert rows[-1].timestamp is None


def test_geojson_is_lon_lat_and_carries_qualifiers():
    gj = traces_to_geojson(_sample_trace())
    assert gj["type"] == "FeatureCollection"
    feature = gj["features"][0]
    lon, lat = feature["geometry"]["coordinates"]
    assert -180 <= lon <= 180 and -90 <= lat <= 90
    # An exhibit must not reduce to undifferentiated dots.
    for key in ("category", "is_presence", "tier", "provenance", "source_label"):
        assert key in feature["properties"]


def test_empty_inputs_produce_an_empty_trace_not_an_error():
    assert build_location_traces() == []
    s = summarise_traces([])
    assert s["total"] == 0
    assert s["first_seen"] is None


# ---------------------------------------------------------------------------
# The OLD Maps anomaly detector (parsers/google_maps.detect_location_anomalies).
#
# The unified trace has enforced presence-vs-interest since it was written, but this
# older detector — which still feeds the `maps_location_anomalies` dataset — ran both
# of its heuristics over the raw mixed `maps_locations` list. `maps_locations` contains
# recorded device positions (Takeout, the Play-services cache) AND places the user only
# looked at (a search, a saved place, a navigation destination). Both heuristics assert
# where the device physically WAS, so over the mixed set they fabricated findings:
# a place searched at 02:00 became "Device was at 'X' at 02:xx", and a city looked up
# once became a critical "Device moved 3000 km at 1200 km/h".
# ---------------------------------------------------------------------------

from triage.parsers.google_maps import detect_location_anomalies  # noqa: E402


def test_a_place_merely_searched_at_night_is_not_reported_as_the_device_being_there():
    anomalies = detect_location_anomalies(
        [
            {
                "latitude": 19.0760,
                "longitude": 72.8777,
                "timestamp": "2026-03-01T02:30:00Z",
                "place_name": "Mumbai",
                "source": "maps_search",
            }
        ]
    )
    late_night = [a for a in anomalies if a["pattern"] == "late_night_location"]
    assert late_night == [], (
        "a searched place must never produce a late-night PRESENCE finding — "
        "searching a place at 2am evidences interest, not that the device was there"
    )


def test_a_looked_up_distant_city_cannot_fabricate_an_impossible_journey():
    # A real fix in Delhi, then the user searches for Chennai ~1760 km away 10 minutes
    # later. Mixing these manufactures a ~10000 km/h "critical" movement finding out of
    # a map search — the exact false-inculpatory result the honesty model forbids.
    anomalies = detect_location_anomalies(
        [
            {
                "latitude": 28.6139,
                "longitude": 77.2090,
                "timestamp": "2026-03-01T10:00:00Z",
                "source": "takeout",
            },
            {
                "latitude": 13.0827,
                "longitude": 80.2707,
                "timestamp": "2026-03-01T10:10:00Z",
                "place_name": "Chennai",
                "source": "maps_search",
            },
        ]
    )
    movement = [
        a
        for a in anomalies
        if a["pattern"] in ("large_location_jump", "high_speed_movement")
    ]
    assert movement == [], (
        "movement between a recorded fix and a merely-searched place is not travel"
    )


def test_genuine_presence_rows_still_produce_their_findings():
    # The fix must not silence the detector for rows that DO place the device.
    anomalies = detect_location_anomalies(
        [
            {
                "latitude": 28.6139,
                "longitude": 77.2090,
                "timestamp": "2026-03-01T02:30:00Z",
                "place_name": "Delhi",
                "source": "takeout",
            },
            {
                "latitude": 13.0827,
                "longitude": 80.2707,
                "timestamp": "2026-03-01T02:40:00Z",
                "source": "gms_network_location",
            },
        ]
    )
    patterns = {a["pattern"] for a in anomalies}
    assert "late_night_location" in patterns
    assert "large_location_jump" in patterns


def test_excluded_interest_rows_are_reported_not_silently_dropped():
    anomalies = detect_location_anomalies(
        [
            {
                "latitude": 19.0760,
                "longitude": 72.8777,
                "timestamp": "2026-03-01T02:30:00Z",
                "source": "maps_search",
            },
            {
                "latitude": 19.0760,
                "longitude": 72.8777,
                "timestamp": "2026-03-01T03:30:00Z",
                "source": "maps_saved_place",
            },
        ]
    )
    excluded = [a for a in anomalies if a["pattern"] == "interest_rows_excluded"]
    assert len(excluded) == 1, "the exclusion must be stated, so silence is never read as 'nothing was looked up'"
    assert excluded[0]["evidence"]["excluded_rows"] == 2
    assert excluded[0]["evidence"]["analysed_rows"] == 0
    assert excluded[0]["severity"] == "info"


def test_an_unknown_source_is_treated_as_interest_never_promoted_to_presence():
    # An unmapped provenance must not be able to manufacture a movement finding.
    anomalies = detect_location_anomalies(
        [
            {
                "latitude": 28.6139,
                "longitude": 77.2090,
                "timestamp": "2026-03-01T02:30:00Z",
                "source": "some_parser_added_next_year",
            }
        ]
    )
    assert [a for a in anomalies if a["pattern"] == "late_night_location"] == []
