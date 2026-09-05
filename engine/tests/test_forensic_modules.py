"""Unit tests for the 5 forensic modules.

Tests MODULE 1-5:
1. Bluetooth Correlation (bt_config.py)
2. Wi-Fi Passwords (wifi.py)
3. Wi-Fi Traffic History (wifi_live.py)
4. USB Connection State (acquire/real.py)
5. Hotspot Indicators (hotspot.py)
"""

import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch

# Import modules under test
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from triage.parsers import bt_config, wifi, wifi_live, hotspot
from triage.acquire.real import get_usb_state


class TestBluetoothCorrelation(unittest.TestCase):
    """MODULE 1: Bluetooth Correlation tests"""
    
    def test_correlate_bluetooth_full_mac_match(self):
        """Test correlation with full MAC address match"""
        # Mock bond store
        bond_store = {
            "bonds": [
                {
                    "address": "AA:BB:CC:DD:EE:FF",
                    "name": "Device1",
                    "bond_timestamp": "2024-01-01T12:00:00Z",
                    "to_dict": lambda: {
                        "address": "AA:BB:CC:DD:EE:FF",
                        "name": "Device1",
                        "bond_timestamp": "2024-01-01T12:00:00Z",
                        "caveats": []
                    }
                }
            ]
        }
        
        # Mock dumpsys output
        dumpsys_list = [
            {
                "mac": "AA:BB:CC:DD:EE:FF",
                "name": "Device1",
                "connected": True,
                "last_seen": "2024-01-02T10:00:00"
            }
        ]
        
        result = bt_config.correlate_bluetooth(bond_store, dumpsys_list)
        
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["address"], "AA:BB:CC:DD:EE:FF")
        # Check that timestamps are SEPARATE
        self.assertIn("bond_record_written_utc", result[0])
        self.assertIn("dumpsys_connected_at_dump_time", result[0])
        self.assertEqual(result[0]["bond_record_written_utc"], "2024-01-01T12:00:00Z")
        self.assertEqual(result[0]["dumpsys_connected_at_dump_time"], True)
    
    def test_correlate_bluetooth_redacted_suffix_match(self):
        """Test correlation with redacted MAC (last 2 octets only)"""
        bond_store = {
            "bonds": [
                {
                    "address": "AA:BB:CC:DD:EE:FF",
                    "name": "Device1",
                    "bond_timestamp": "2024-01-01T12:00:00Z",
                    "to_dict": lambda: {
                        "address": "AA:BB:CC:DD:EE:FF",
                        "name": "Device1",
                        "bond_timestamp": "2024-01-01T12:00:00Z",
                        "caveats": []
                    }
                }
            ]
        }
        
        # Redacted MAC in dumpsys (Android 8+)
        dumpsys_list = [
            {
                "mac": "XX:XX:XX:XX:EE:FF",
                "name": "Device1",
                "connected": False,
                "last_seen": ""
            }
        ]
        
        result = bt_config.correlate_bluetooth(bond_store, dumpsys_list)
        
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["match_method"], "redacted_suffix")
        self.assertIn("16-bit suffix match", str(result[0]["caveats"]))
    
    def test_bond_timestamp_caveat(self):
        """Verify the caveat about bond timestamp meaning"""
        bond_store = {
            "bonds": [
                {
                    "address": "AA:BB:CC:DD:EE:FF",
                    "bond_timestamp": "2024-01-01T12:00:00Z",
                    "to_dict": lambda: {
                        "address": "AA:BB:CC:DD:EE:FF",
                        "bond_timestamp": "2024-01-01T12:00:00Z",
                        "caveats": []
                    }
                }
            ]
        }
        
        result = bt_config.correlate_bluetooth(bond_store, [])
        
        # Check for the critical caveat
        caveats_str = str(result[0]["caveats"])
        self.assertIn("bond-record", caveats_str.lower())
        self.assertIn("neither is a connection", caveats_str.lower())


class TestWifiPasswords(unittest.TestCase):
    """MODULE 2: Wi-Fi Passwords (Root Tier 2) tests"""
    
    def test_parse_wificonfigstore_xml_wpa(self):
        """Test parsing WifiConfigStore.xml with WPA network"""
        xml_content = '''<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<WifiConfigStoreData version="3">
  <NetworkList>
    <Network>
      <WifiConfiguration>
        <string name="SSID">&quot;TestNetwork&quot;</string>
        <string name="PreSharedKey">&quot;password123&quot;</string>
        <string name="AllowedKeyMgmt">WPA_PSK</string>
      </WifiConfiguration>
    </Network>
  </NetworkList>
</WifiConfigStoreData>'''
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.xml', delete=False) as f:
            f.write(xml_content)
            temp_path = Path(f.name)
        
        try:
            networks = wifi.parse_wifi_config_store_xml(temp_path)
            
            self.assertEqual(len(networks), 1)
            self.assertEqual(networks[0].ssid, "TestNetwork")
            self.assertEqual(networks[0].password, "password123")
            self.assertEqual(networks[0].security, "WPA/WPA2")
        finally:
            temp_path.unlink()
    
    def test_parse_wificonfigstore_xml_wpa3(self):
        """Test parsing WifiConfigStore.xml with WPA3 network"""
        xml_content = '''<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<WifiConfigStoreData version="3">
  <NetworkList>
    <Network>
      <WifiConfiguration>
        <string name="SSID">&quot;SecureNet&quot;</string>
        <string name="PreSharedKey">&quot;strongpass&quot;</string>
        <string name="AllowedKeyMgmt">WPA3_SAE</string>
      </WifiConfiguration>
    </Network>
  </NetworkList>
</WifiConfigStoreData>'''
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.xml', delete=False) as f:
            f.write(xml_content)
            temp_path = Path(f.name)
        
        try:
            networks = wifi.parse_wifi_config_store_xml(temp_path)
            
            self.assertEqual(len(networks), 1)
            self.assertEqual(networks[0].security, "WPA3")
        finally:
            temp_path.unlink()
    
    def test_parse_wpa_supplicant_conf(self):
        """Test parsing legacy wpa_supplicant.conf"""
        conf_content = '''
network={
    ssid="OldNetwork"
    psk="oldpassword"
    key_mgmt=WPA-PSK
}

network={
    ssid="OpenNet"
    key_mgmt=NONE
}
'''
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
            f.write(conf_content)
            temp_path = Path(f.name)
        
        try:
            networks = wifi.parse_wpa_supplicant_conf(temp_path)
            
            self.assertEqual(len(networks), 2)
            self.assertEqual(networks[0].ssid, "OldNetwork")
            self.assertEqual(networks[0].password, "oldpassword")
            self.assertEqual(networks[1].security, "OPEN")
        finally:
            temp_path.unlink()
    
    def test_parse_wep_network(self):
        """Test WEP network parsing"""
        conf_content = '''
network={
    ssid="WEPNetwork"
    wep_key0="1234567890"
    key_mgmt=WEP
}
'''
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
            f.write(conf_content)
            temp_path = Path(f.name)
        
        try:
            networks = wifi.parse_wpa_supplicant_conf(temp_path)
            
            self.assertEqual(len(networks), 1)
            self.assertEqual(networks[0].security, "WEP")
            self.assertEqual(networks[0].password, "1234567890")
        finally:
            temp_path.unlink()


class TestWifiTrafficHistory(unittest.TestCase):
    """MODULE 3: Wi-Fi Traffic History (Non-root Tier 0) tests"""
    
    def test_parse_netstats_with_traffic(self):
        """Test parsing netstats with actual traffic"""
        netstats_output = '''ident=[{networkId="TestSSID", type=WIFI}] uid=-1 set=ALL tag=0x0
  NetworkStatsHistory: bucketDuration=3600
  st=1609459200 rb=1024000 rp=100 tb=512000 tp=50
  st=1609462800 rb=2048000 rp=200 tb=1024000 tp=100
'''
        
        buckets = wifi_live.parse_netstats(netstats_output)
        
        self.assertEqual(len(buckets), 2)
        self.assertEqual(buckets[0].ssid, "TestSSID")
        self.assertEqual(buckets[0].rx_bytes, 1024000)
        self.assertEqual(buckets[0].tx_bytes, 512000)
        # Check that approximate field is ALWAYS True
        self.assertTrue(buckets[0].approximate)
        self.assertTrue(buckets[1].approximate)
        # Check caveat
        self.assertIn("approximate", buckets[0].caveats[0].lower())
    
    def test_netstats_bucket_times(self):
        """Test that bucket times are correctly converted to ISO-8601"""
        netstats_output = '''ident=[{networkId="SSID", type=WIFI}] uid=-1 set=ALL tag=0x0
  NetworkStatsHistory: bucketDuration=3600
  st=1609459200 rb=1000 rp=10 tb=2000 tp=20
'''
        
        buckets = wifi_live.parse_netstats(netstats_output)
        
        self.assertEqual(len(buckets), 1)
        # Verify ISO-8601 format with Z suffix
        self.assertRegex(buckets[0].bucket_start, r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z')
        self.assertRegex(buckets[0].bucket_end, r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z')
    
    def test_netstats_caveat_present(self):
        """Test that critical caveats are included"""
        netstats_output = '''ident=[{networkId="Net", type=WIFI}] uid=-1 set=ALL tag=0x0
  NetworkStatsHistory: bucketDuration=7200
  st=1609459200 rb=100 rp=1 tb=200 tp=2
'''
        
        buckets = wifi_live.parse_netstats(netstats_output)
        
        self.assertGreater(len(buckets), 0)
        caveat_text = " ".join(buckets[0].caveats).lower()
        self.assertIn("approximate", caveat_text)
        self.assertIn("not", caveat_text)  # "not a connection" or similar
        self.assertIn("hour", caveat_text)


def _adb_returning(responses: dict, serial: str = "ABC123"):
    """Mock Adb whose shell() answers by command substring; unknown commands fail."""
    mock_adb = Mock()
    mock_adb.serial = serial

    def _shell(cmd, *args, **kwargs):
        for needle, stdout in responses.items():
            if needle in cmd:
                res = Mock()
                res.ok = True
                res.stdout = stdout
                res.stderr = ""
                return res
        res = Mock()
        res.ok = False
        res.stdout = ""
        res.stderr = "no such file or directory"
        return res

    mock_adb.shell.side_effect = _shell
    return mock_adb


class TestUSBConnectionState(unittest.TestCase):
    """USB connection state (Non-root Tier 0) tests"""

    def test_any_positive_probe_reports_connected(self):
        adb = _adb_returning({"dumpsys battery": "USB powered: true\nAC powered: false\n"})

        result = get_usb_state(adb)

        self.assertIs(result["usb_connected"], True)
        self.assertIn("battery:usb-powered", result["probe_votes"])

    def test_device_role_is_the_connected_role_not_host(self):
        """A phone plugged into a workstation is the DEVICE side of the link.

        Treating 'host' as the connected role inverts the test and returns False
        on exactly the setup a forensic capture runs on.
        """
        adb = _adb_returning({"typec/port0/data_role": "[device] host\n"})

        result = get_usb_state(adb)

        self.assertIs(result["usb_connected"], True)
        self.assertIn("typec:device", result["probe_votes"])

    def test_host_role_is_flagged_as_otg_not_the_workstation_link(self):
        adb = _adb_returning({"typec/port0/data_role": "host\n"})

        result = get_usb_state(adb)

        self.assertIn("OTG", " ".join(result["caveats"]))
        self.assertNotIn("typec:host", result["probe_votes"])

    def test_all_probes_negative_reports_disconnected(self):
        adb = _adb_returning(
            {
                "dumpsys battery": "USB powered: false\nAC powered: false\n",
                "android_usb/android0/state": "DISCONNECTED\n",
            }
        )

        result = get_usb_state(adb)

        self.assertIs(result["usb_connected"], False)

    def test_no_legible_probe_is_unknown_not_disconnected(self):
        """Absent != disconnected — the whole point of the tri-state."""
        result = get_usb_state(_adb_returning({}))

        self.assertIsNone(result["usb_connected"])
        self.assertIn("UNKNOWN", " ".join(result["caveats"]))

    def test_tcp_transport_is_recorded_separately(self):
        adb = _adb_returning({"dumpsys battery": "USB powered: false\n"}, serial="192.168.1.5:5555")

        result = get_usb_state(adb)

        self.assertEqual(result["transport"], "tcp")
        self.assertIn("over TCP/IP", " ".join(result["caveats"]))

    def test_adb_reachability_is_not_used_as_a_probe(self):
        """We are talking over ADB, so 'adb devices lists it' always passes.

        It proves nothing about a cable and must not be able to carry a verdict.
        """
        adb = _adb_returning({})

        get_usb_state(adb)

        issued = [call.args[0] for call in adb.shell.call_args_list]
        self.assertFalse(any("devices" in cmd for cmd in issued))
        # Nor may it reach around the Adb API to run `adb devices` itself.
        adb._base.assert_not_called()

    def test_usb_caveats_present(self):
        """Test that standard caveats are included"""
        result = get_usb_state(_adb_returning({}))

        caveat_text = " ".join(result["caveats"]).lower()
        self.assertIn("moment of capture", caveat_text)
        self.assertIn("does not establish", caveat_text)


class TestHotspotIndicators(unittest.TestCase):
    """MODULE 5: Hotspot Indicators (Non-root Tier 0) tests"""
    
    def test_hosted_hotspot_detected(self):
        """Test detection of hosted hotspot"""
        wifi_dumpsys = '''
mWifiInfo SSID: <unknown>, BSSID: <none>
SoftAp state: ENABLED
SoftApManager - current state: StartedState
'''

        result = hotspot.analyze_hotspot_indicators(wifi_dumpsys, "", [])

        self.assertTrue(result["hosted_indicator"])
        self.assertIn("SoftAp", result["details"]["hosted_evidence"][0])

    def test_idle_softap_state_machine_is_not_a_hosted_hotspot(self):
        """dumpsys wifi prints SoftApManager on EVERY device, hotspot or not.

        Matching the word "SoftAp" therefore flags every phone ever seized. Only
        an explicit started/enabled state may set hosted_indicator.
        """
        wifi_dumpsys = '''
mWifiInfo SSID: "HomeNetwork", BSSID: aa:bb:cc:dd:ee:ff
SoftApManager - current state: IdleState
mWifiApState: 11
'''

        result = hotspot.analyze_hotspot_indicators(wifi_dumpsys, "", [])

        self.assertIs(result["hosted_indicator"], False)

    def test_absent_softap_block_is_unknown_not_false(self):
        """A build that reports no AP state at all must not read as "hotspot off"."""
        result = hotspot.analyze_hotspot_indicators("mWifiInfo SSID: <unknown>", "", [])

        self.assertIsNone(result["hosted_indicator"])
        self.assertIn(
            "unknown", " ".join(result["caveats"]).lower()
        )

    def test_tethered_interface_corroborates_hosting(self):
        connectivity = "Tethering:\n  Tethered ifaces: [wlan1]\n"

        result = hotspot.analyze_hotspot_indicators(
            "", "", [], connectivity=connectivity
        )

        self.assertTrue(result["hosted_indicator"])
        self.assertTrue(
            any("wlan1" in e for e in result["details"]["hosted_evidence"])
        )

    def test_softap_config_records_configured_not_active(self):
        result = hotspot.analyze_hotspot_indicators(
            "", "", [], softap_config={"ssid": "MyPhoneAP"}
        )

        self.assertTrue(result["hosted_configured"])
        self.assertIn(
            "not that it was ever switched on", " ".join(result["caveats"])
        )

    def test_connected_to_hotspot_detected(self):
        """Test detection of connection to another device's hotspot"""
        wifi_config = [
            {"ssid": "AndroidAP1234"},
            {"ssid": "MyHomeWifi"}
        ]

        result = hotspot.analyze_hotspot_indicators("", "", wifi_config)

        self.assertTrue(result["connected_indicator"])
        self.assertIn("androidap", result["details"]["connected_evidence"][0].lower())

    def test_ssid_name_match_is_labelled_a_heuristic(self):
        """An SSID is freely chosen, so a name match is a lead, never a finding."""
        result = hotspot.analyze_hotspot_indicators(
            "", "", [{"ssid": "AndroidAP1234"}]
        )

        self.assertIn(
            "not a determination", result["details"]["connected_evidence"][0]
        )
        self.assertIn("lead, not a conclusion", " ".join(result["caveats"]))

    def test_no_saved_network_list_says_the_check_could_not_run(self):
        """Android 10+ hides the saved list from non-root; that is not "no hotspot"."""
        result = hotspot.analyze_hotspot_indicators("", "", [])

        self.assertIsNone(result["connected_indicator"])
        self.assertIn("unreadable without root", " ".join(result["caveats"]))

    def test_hotspot_with_traffic(self):
        """Test detection of traffic over hotspot SSID"""
        netstats = '''
ident=[{networkId="AndroidAP5678", type=WIFI}] uid=-1 set=ALL tag=0x0
  NetworkStatsHistory: bucketDuration=3600
    st=1609459200 rb=5000 rp=10 tb=3000 tp=5
'''
        
        result = hotspot.analyze_hotspot_indicators("", netstats, [])
        
        self.assertGreater(len(result["details"]["traffic_evidence"]), 0)
        self.assertIn("rx=5000", result["details"]["traffic_evidence"][0])
    
    def test_no_hotspot_indicators(self):
        """Test when no hotspot indicators are present"""
        wifi_dumpsys = "SoftApManager - current state: IdleState"
        result = hotspot.analyze_hotspot_indicators(
            wifi_dumpsys, "", [{"ssid": "HomeNetwork"}]
        )

        self.assertIs(result["hosted_indicator"], False)
        self.assertIs(result["connected_indicator"], False)
        caveats = " ".join(result["caveats"]).lower()
        # An absence of indicators is never allowed to read as an absence of use.
        self.assertIn("does not exclude hotspot use", caveats)
        self.assertIn("neither shown nor excluded", caveats)
    
    def test_critical_caveats_always_present(self):
        """Test that critical caveats are always included"""
        result = hotspot.analyze_hotspot_indicators("", "", [])
        
        caveat_text = " ".join(result["caveats"]).lower()
        self.assertIn("does not prove the user intended", caveat_text)
        self.assertIn("nor does it log client mac", caveat_text)
        self.assertIn("active at capture time", caveat_text)


class TestDataclassStructures(unittest.TestCase):
    """Test that dataclasses have required fields"""
    
    def test_wifi_usage_bucket_has_approximate_field(self):
        """WifiUsageBucket MUST have approximate=True hardcoded"""
        bucket = wifi_live.WifiUsageBucket(
            ssid="Test",
            bucket_start="2024-01-01T00:00:00Z",
            bucket_end="2024-01-01T01:00:00Z"
        )
        
        # Check default is True
        self.assertTrue(bucket.approximate)
        
        # Check it's in the dict representation
        bucket_dict = bucket.to_dict()
        self.assertTrue(bucket_dict["approximate"])


if __name__ == '__main__':
    unittest.main()
