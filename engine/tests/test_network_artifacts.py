"""Wi-Fi and Bluetooth artifact tests — paths, provenance, and the time claims.

The theme of every test here is the same one the parsers are built around: what
the device actually stores, versus what a report would like it to say.  A saved
network is not a visit; a bond timestamp is not a connection; ``last_active_time``
is not a time.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from triage.config import Confidence
from triage.parsers.bt_transfer import (
    BT_TRANSFER_PATHS,
    bt_transfer_summary,
    build_transfer_timeline,
    parse_bluetooth_metadata_db,
    parse_btopp,
)
from triage.parsers.wifi import (
    WIFI_CONFIG_PATHS,
    parse_wifi_config,
    parse_wifi_config_store_xml,
    parse_wifi_softap_xml,
)


# ---------------------------------------------------------------------------
# Wi-Fi: where the store lives
# ---------------------------------------------------------------------------


def test_apex_config_store_path_is_probed_first():
    """Android 11+ moved the store into the Wi-Fi APEX; missing it reads as 'no networks'."""
    paths = [p for p, _, _ in WIFI_CONFIG_PATHS]
    apex = "/data/misc/apexdata/com.android.wifi/WifiConfigStore.xml"
    assert apex in paths
    # It must be probed before the pre-APEX path, so the current store wins the
    # dedupe against a stale copy left behind by an OS upgrade.
    assert paths.index(apex) < paths.index("/data/misc/wifi/WifiConfigStore.xml")


def test_legacy_and_softap_paths_are_all_probed():
    paths = [p for p, _, _ in WIFI_CONFIG_PATHS]
    assert "/data/misc/wifi/wpa_supplicant.conf" in paths
    assert any("SoftAp" in p for p in paths)
    # Local staging names must be unique or one pull overwrites another.
    names = [n for _, n, _ in WIFI_CONFIG_PATHS]
    assert len(names) == len(set(names))


# ---------------------------------------------------------------------------
# Wi-Fi: connection provenance
# ---------------------------------------------------------------------------

_XML_WITH_STATUS = """\
<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<WifiConfigStoreData version="3">
  <NetworkList>
    <Network>
      <WifiConfiguration>
        <string name="SSID">&quot;JoinedNet&quot;</string>
        <string name="PreSharedKey">&quot;hunter2&quot;</string>
        <string name="AllowedKeyMgmt">WPA_PSK</string>
        <string name="CreatorName">android.uid.system:1000</string>
        <string name="LastUpdateName">com.android.settings</string>
        <string name="DefaultGwMacAddress">aa:bb:cc:dd:ee:ff</string>
        <string name="RandomizedMacAddress">02:11:22:33:44:55</string>
        <boolean name="IsMostRecentlyConnected" value="true" />
        <boolean name="HiddenSSID" value="false" />
        <int name="MeteredOverride" value="1" />
        <long name="ConnectChoiceTimestamp" value="1735689600000" />
        <long name="ElapsedRealtimeSinceBootTime" value="41234" />
      </WifiConfiguration>
      <NetworkStatus>
        <boolean name="HasEverConnected" value="true" />
        <int name="SelectionStatus" value="0" />
      </NetworkStatus>
    </Network>
    <Network>
      <WifiConfiguration>
        <string name="SSID">&quot;NeverJoined&quot;</string>
        <string name="AllowedKeyMgmt">NONE</string>
      </WifiConfiguration>
      <NetworkStatus>
        <boolean name="HasEverConnected" value="false" />
      </NetworkStatus>
    </Network>
  </NetworkList>
</WifiConfigStoreData>
"""


@pytest.fixture
def config_store(tmp_path: Path) -> Path:
    p = tmp_path / "WifiConfigStore.xml"
    p.write_text(_XML_WITH_STATUS, encoding="utf-8")
    return p


def test_has_ever_connected_separates_saved_from_visited(config_store: Path):
    """Saving a network is not evidence of having been at it — the flag says which."""
    nets = parse_wifi_config_store_xml(config_store)
    assert len(nets) == 2

    joined, never = nets
    assert joined.ssid == "JoinedNet"
    assert joined.has_ever_connected is True
    assert joined.is_most_recently_connected is True

    assert never.ssid == "NeverJoined"
    assert never.has_ever_connected is False
    assert any("never successfully joined" in c for c in never.caveats)


def test_provenance_fields_are_extracted(config_store: Path):
    joined = parse_wifi_config_store_xml(config_store)[0]
    assert joined.password == "hunter2"
    assert joined.security == "WPA/WPA2"
    assert joined.creator == "android.uid.system:1000"
    assert joined.last_update_by == "com.android.settings"
    assert joined.default_gateway_mac == "aa:bb:cc:dd:ee:ff"
    assert joined.randomized_mac == "02:11:22:33:44:55"
    assert joined.metered == "metered"
    assert joined.network_status == "enabled"
    assert joined.hidden is False


def test_timestamps_keep_their_original_field_name(config_store: Path):
    """A timestamp is reported as the event Android named, never as 'last connected'."""
    joined = parse_wifi_config_store_xml(config_store)[0]
    assert joined.timestamps == {"ConnectChoiceTimestamp": "2025-01-01T00:00:00Z"}
    # The dataclass carries no field that would let a caller read this as a
    # connection time.
    assert not hasattr(joined, "last_connected")


def test_uptime_counters_are_not_rendered_as_dates(config_store: Path):
    """41234 ms since boot is not 1970-01-01 — dropping it beats a fake date."""
    joined = parse_wifi_config_store_xml(config_store)[0]
    assert "ElapsedRealtimeSinceBootTime" not in joined.timestamps
    assert all(not v.startswith("1970") for v in joined.timestamps.values())


def test_network_with_no_status_block_says_unrecorded(tmp_path: Path):
    xml = """<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<WifiConfigStoreData version="3"><NetworkList><Network><WifiConfiguration>
<string name="SSID">&quot;Bare&quot;</string>
<string name="AllowedKeyMgmt">WPA_PSK</string>
</WifiConfiguration></Network></NetworkList></WifiConfigStoreData>"""
    p = tmp_path / "WifiConfigStore.xml"
    p.write_text(xml, encoding="utf-8")
    net = parse_wifi_config_store_xml(p)[0]
    assert net.has_ever_connected is None
    assert any("unrecorded, not disproved" in c for c in net.caveats)


def test_wep_key_array_is_read_at_the_tx_index(tmp_path: Path):
    xml = """<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<WifiConfigStoreData version="3"><NetworkList><Network><WifiConfiguration>
<string name="SSID">&quot;OldWep&quot;</string>
<string-array name="WEPKeys" num="2"><item value="k0">wepkey0</item><item value="k1">wepkey1</item></string-array>
<int name="WEPTxKeyIndex" value="1" />
</WifiConfiguration></Network></NetworkList></WifiConfigStoreData>"""
    p = tmp_path / "WifiConfigStore.xml"
    p.write_text(xml, encoding="utf-8")
    net = parse_wifi_config_store_xml(p)[0]
    assert net.password == "wepkey1"
    assert net.security == "WEP"


# ---------------------------------------------------------------------------
# Wi-Fi: the device's own hotspot
# ---------------------------------------------------------------------------

_SOFTAP_XML = """\
<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<WifiConfigStoreData version="3">
  <SoftAp>
    <string name="SSID">&quot;MyPhoneAP&quot;</string>
    <string name="Passphrase">sharedpass</string>
    <int name="SecurityType" value="1" />
    <boolean name="HiddenSSID" value="false" />
  </SoftAp>
</WifiConfigStoreData>
"""


def test_softap_config_is_flagged_as_offered_not_joined(tmp_path: Path):
    p = tmp_path / "WifiConfigStoreSoftAp.xml"
    p.write_text(_SOFTAP_XML, encoding="utf-8")
    nets = parse_wifi_softap_xml(p)
    assert len(nets) == 1
    ap = nets[0]
    assert ap.ssid == "MyPhoneAP"
    assert ap.password == "sharedpass"
    assert ap.security == "WPA2"
    assert ap.is_softap is True
    assert any("OFFERS, not a network it joined" in c for c in ap.caveats)


def test_dispatcher_routes_softap_by_filename(tmp_path: Path):
    """A SoftAp file must not be parsed as a saved-network list."""
    p = tmp_path / "WifiConfigStoreSoftAp.xml"
    p.write_text(_SOFTAP_XML, encoding="utf-8")
    nets = parse_wifi_config(p)
    assert len(nets) == 1 and nets[0].is_softap is True


# ---------------------------------------------------------------------------
# Bluetooth: OPP transfers
# ---------------------------------------------------------------------------


def _make_btopp(path: Path, rows: list[tuple]) -> None:
    con = sqlite3.connect(path)
    con.execute(
        """CREATE TABLE btopp (
            _id INTEGER PRIMARY KEY AUTOINCREMENT, uri TEXT, hint TEXT, _data TEXT,
            mimetype TEXT, direction INTEGER, destination TEXT, visibility INTEGER,
            confirm INTEGER, status INTEGER, total_bytes INTEGER,
            current_bytes INTEGER, timestamp INTEGER)"""
    )
    con.executemany(
        "INSERT INTO btopp (uri, hint, _data, mimetype, direction, destination, "
        "visibility, confirm, status, total_bytes, current_bytes, timestamp) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    con.commit()
    con.close()


@pytest.fixture
def btopp_db(tmp_path: Path) -> Path:
    p = tmp_path / "btopp.db"
    _make_btopp(
        p,
        [
            (
                "content://x/1",
                "evidence.jpg",
                "/sdcard/bluetooth/evidence.jpg",
                "image/jpeg",
                0,  # outbound
                "AA:BB:CC:DD:EE:FF",
                1,
                1,
                200,  # success
                204800,
                204800,
                1735689600000,  # 2025-01-01T00:00:00Z
            ),
            (
                "content://x/2",
                "notes.pdf",
                "/sdcard/bluetooth/notes.pdf",
                "application/pdf",
                1,  # inbound
                "11:22:33:44:55:66",
                1,
                1,
                490,  # canceled
                10240,
                2048,
                1735776000000,  # 2025-01-02T00:00:00Z
            ),
        ],
    )
    return p


def test_transfer_rows_carry_peer_direction_and_wall_clock_time(btopp_db: Path):
    result = parse_btopp(btopp_db)
    assert result["table"] == "btopp"
    transfers = result["transfers"]
    assert len(transfers) == 2

    sent = transfers[0]
    assert sent.direction == "outbound"
    assert sent.peer_address == "AA:BB:CC:DD:EE:FF"
    assert sent.filename == "evidence.jpg"
    assert sent.timestamp == "2025-01-01T00:00:00Z"
    assert sent.succeeded is True
    assert sent.total_bytes == 204800
    assert sent.confidence == Confidence.LIVE


def test_failed_transfer_is_not_reported_as_delivered(btopp_db: Path):
    canceled = parse_btopp(btopp_db)["transfers"][1]
    assert canceled.status == "canceled"
    assert canceled.succeeded is False
    assert any("did not complete" in c for c in canceled.caveats)


def test_transfer_time_is_caveated_as_device_clock(btopp_db: Path):
    sent = parse_btopp(btopp_db)["transfers"][0]
    assert any("DEVICE clock" in c for c in sent.caveats)


def test_empty_transfer_log_is_not_evidence_of_no_bluetooth(tmp_path: Path):
    p = tmp_path / "btopp.db"
    _make_btopp(p, [])
    result = parse_btopp(p)
    assert result["transfers"] == []
    assert any("not evidence of no Bluetooth activity" in c for c in result["caveats"])


def test_database_without_transfer_table_says_so(tmp_path: Path):
    p = tmp_path / "btopp.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE unrelated (a INTEGER)")
    con.commit()
    con.close()
    result = parse_btopp(p)
    assert result["table"] is None
    assert any("no recognisable OPP transfer table" in c for c in result["caveats"])


def test_missing_database_returns_empty_not_error(tmp_path: Path):
    result = parse_btopp(tmp_path / "nope.db")
    assert result["transfers"] == []
    assert result["table"] is None


def test_zero_timestamp_yields_no_date(tmp_path: Path):
    """A 0 timestamp must not become 1970-01-01 in a court report."""
    p = tmp_path / "btopp.db"
    _make_btopp(
        p,
        [("u", "f.bin", "/sdcard/f.bin", "application/octet-stream", 0, "AA:BB:CC:DD:EE:FF", 1, 1, 200, 1, 1, 0)],
    )
    row = parse_btopp(p)["transfers"][0]
    assert row.timestamp is None
    assert any("No usable transfer timestamp" in c for c in row.caveats)


def test_oem_renamed_table_is_found_by_column_signature(tmp_path: Path):
    p = tmp_path / "btopp.db"
    con = sqlite3.connect(p)
    con.execute(
        "CREATE TABLE share (_id INTEGER PRIMARY KEY, hint TEXT, direction INTEGER, "
        "destination TEXT, status INTEGER, timestamp INTEGER)"
    )
    con.execute(
        "INSERT INTO share VALUES (1, 'x.txt', 0, 'AA:BB:CC:DD:EE:FF', 200, 1735689600000)"
    )
    con.commit()
    con.close()
    assert parse_btopp(p)["table"] == "share"


def test_transfer_paths_include_both_encryption_roots():
    """On a locked FBE device only the device-encrypted copy is readable at all."""
    paths = [p for p, _ in BT_TRANSFER_PATHS]
    assert any(p.startswith("/data/user_de/0/") and p.endswith("btopp.db") for p in paths)
    assert any(p.startswith("/data/data/") and p.endswith("btopp.db") for p in paths)
    # The WAL holds the newest transfers; pulling the .db alone loses them.
    assert any(p.endswith("btopp.db-wal") for p in paths)
    names = [n for _, n in BT_TRANSFER_PATHS]
    assert len(names) == len(set(names))


# ---------------------------------------------------------------------------
# Bluetooth: connection order is an ordinal, not a clock
# ---------------------------------------------------------------------------


def test_last_active_time_is_ranked_never_dated(tmp_path: Path):
    p = tmp_path / "bluetooth_db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE metadata (address TEXT PRIMARY KEY, last_active_time INTEGER)")
    con.executemany(
        "INSERT INTO metadata VALUES (?,?)",
        [("AA:BB:CC:DD:EE:FF", 7), ("11:22:33:44:55:66", 42), ("99:88:77:66:55:44", 3)],
    )
    con.commit()
    con.close()

    result = parse_bluetooth_metadata_db(p)
    devices = result["devices"]
    assert [d.address for d in devices] == [
        "11:22:33:44:55:66",
        "AA:BB:CC:DD:EE:FF",
        "99:88:77:66:55:44",
    ]
    assert devices[0].rank == 1 and devices[0].ordinal == 42
    assert any("COUNTER, not a timestamp" in c for c in result["caveats"])
    # No field on the record may hold a date.
    assert not any(hasattr(d, f) for d in devices for f in ("timestamp", "last_active"))


def test_metadata_db_without_the_column_says_unrecorded(tmp_path: Path):
    p = tmp_path / "bluetooth_db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE metadata (address TEXT PRIMARY KEY)")
    con.commit()
    con.close()
    result = parse_bluetooth_metadata_db(p)
    assert result["devices"] == []
    assert any("does not record connection order" in c for c in result["caveats"])


# ---------------------------------------------------------------------------
# Timeline + summary
# ---------------------------------------------------------------------------


def test_timeline_states_the_link_and_the_outcome(btopp_db: Path):
    events = build_transfer_timeline(parse_btopp(btopp_db)["transfers"])
    assert len(events) == 2
    assert events[0]["timestamp"] < events[1]["timestamp"]
    sent, received = events
    assert "sent to AA:BB:CC:DD:EE:FF" in sent["summary"]
    assert "requires an active Bluetooth link" in sent["summary"]
    assert "outcome: canceled" in received["summary"]


def test_undated_transfers_produce_no_timeline_event(tmp_path: Path):
    p = tmp_path / "btopp.db"
    _make_btopp(
        p,
        [("u", "f.bin", "/sdcard/f.bin", "application/octet-stream", 0, "AA:BB:CC:DD:EE:FF", 1, 1, 200, 1, 1, 0)],
    )
    assert build_transfer_timeline(parse_btopp(p)["transfers"]) == []


def test_summary_counts_and_reports_undated_rows(btopp_db: Path):
    summary = bt_transfer_summary(parse_btopp(btopp_db))
    assert summary["total"] == 2
    assert summary["inbound"] == 1 and summary["outbound"] == 1
    assert summary["succeeded"] == 1 and summary["failed"] == 1
    assert summary["distinct_peers"] == 2
    assert summary["first_transfer"] == "2025-01-01T00:00:00Z"
    assert summary["last_transfer"] == "2025-01-02T00:00:00Z"
    assert summary["undated_rows"] == 0
