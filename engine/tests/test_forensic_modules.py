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
            self.assertEqual(networks[0].security, "WPA")
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


class TestUSBConnectionState(unittest.TestCase):
    """MODULE 4: USB Connection State (Non-root Tier 0) tests"""
    
    def test_usb_connected_2_of_3_probes(self):
        """Test USB detected when 2 out of 3 probes agree"""
        mock_adb = Mock()
        
        # Probe 1: Type-C says host
        mock_result1 = Mock()
        mock_result1.stdout = "host\n"
        
        # Probe 2: Battery says USB
        mock_result2 = Mock()
        mock_result2.stdout = "USB powered: true\nAC powered: false\n"
        
        # Probe 3: Fails
        mock_adb.shell.side_effect = [mock_result1, mock_result2]
        mock_adb._base.return_value = ["adb"]
        
        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = Exception("Failed")
            
            result = get_usb_state(mock_adb)
        
        # 2 out of 2 successful probes = connected
        self.assertTrue(result["usb_connected"])
        self.assertEqual(len(result["probe_votes"]), 2)
    
    def test_usb_not_connected_insufficient_votes(self):
        """Test USB not detected when only 1 probe agrees"""
        mock_adb = Mock()
        
        # Probe 1: Type-C says device (not host)
        mock_result1 = Mock()
        mock_result1.stdout = "device\n"
        
        # Probe 2: Battery says not USB
        mock_result2 = Mock()
        mock_result2.stdout = "AC powered: false\nWireless: false\n"
        
        mock_adb.shell.side_effect = [mock_result1, mock_result2]
        mock_adb._base.return_value = ["adb"]
        
        with patch('subprocess.run') as mock_run:
            # Probe 3: ADB devices shows device
            mock_proc = Mock()
            mock_proc.stdout = "List of devices attached\n12345\tdevice\n"
            mock_run.return_value = mock_proc
            
            result = get_usb_state(mock_adb)
        
        # Only 1 out of 3 votes for USB = not connected
        self.assertFalse(result["usb_connected"])
    
    def test_usb_caveats_present(self):
        """Test that standard caveats are included"""
        mock_adb = Mock()
        mock_adb.shell.side_effect = Exception("All failed")
        mock_adb._base.return_value = ["adb"]
        
        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = Exception("Failed")
            
            result = get_usb_state(mock_adb)
        
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
    
    def test_connected_to_hotspot_detected(self):
        """Test detection of connection to another device's hotspot"""
        wifi_config = [
            {"ssid": "AndroidAP1234"},
            {"ssid": "MyHomeWifi"}
        ]
        
        result = hotspot.analyze_hotspot_indicators("", "", wifi_config)
        
        self.assertTrue(result["connected_indicator"])
        self.assertIn("androidap", result["details"]["connected_evidence"][0].lower())
    
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
        result = hotspot.analyze_hotspot_indicators("normal wifi", "", [{"ssid": "HomeNetwork"}])
        
        self.assertFalse(result["hosted_indicator"])
        self.assertFalse(result["connected_indicator"])
        self.assertIn("no hotspot indicators detected", result["caveats"][-1].lower())
    
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
