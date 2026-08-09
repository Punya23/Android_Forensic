# Forensic Modules - Setup & Installation Guide

This guide covers the setup, installation, and usage of the 5 new forensic modules added to SNAGR.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Installation](#installation)
3. [Module Overview](#module-overview)
4. [Quick Start](#quick-start)
5. [Testing](#testing)
6. [Integration Guide](#integration-guide)
7. [API Reference](#api-reference)
8. [Troubleshooting](#troubleshooting)

---

## Prerequisites

### System Requirements
- **Python**: 3.10 or higher
- **Operating System**: macOS, Linux, or Windows with WSL
- **ADB**: Android Debug Bridge installed and in PATH
- **Storage**: ~100MB for module dependencies

### Python Dependencies
All modules use **stdlib only** - no external packages required:
- `xml.etree.ElementTree` (XML parsing)
- `re` (regex)
- `time` (timestamp conversion)
- `pathlib` (file operations)
- `dataclasses` (data structures)
- `subprocess` (ADB interaction)

---

## Installation

### 1. Clone the Repository
```bash
cd /Users/lakshsorathiya/Android_Forensic
```

### 2. Verify Python Version
```bash
python3 --version
# Should be 3.10 or higher
```

### 3. Install Project Dependencies
```bash
cd engine
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 4. Verify Installation
```bash
# Run verification script
python verify_modules.py

# Run test suite
python -m pytest tests/test_forensic_modules.py -v
```

Expected output:
```
======================================================================
ALL MODULES VERIFIED SUCCESSFULLY ✓
======================================================================

============================= test session starts ==============================
19 passed in 0.07s
```

---

## Module Overview

### MODULE 1: Bluetooth Correlation (bt_config.py)
**Tier**: 2 (Root required for bt_config.conf)  
**Purpose**: Correlate Bluetooth bond records with live dumpsys state  
**Key Feature**: Separates bond write time from connection state

### MODULE 2: Wi-Fi Passwords (wifi.py)
**Tier**: 2 (Root required for /data/misc/wifi/)  
**Purpose**: Extract Wi-Fi credentials from config files  
**Formats**: WifiConfigStore.xml (Android 9+), wpa_supplicant.conf (Android ≤8)

### MODULE 3: Wi-Fi Traffic History (wifi_live.py)
**Tier**: 0 (Non-root, dumpsys only)  
**Purpose**: Extract hour-bucketed traffic statistics per SSID  
**Key Feature**: Always marked as "approximate"

### MODULE 4: USB Connection State (real.py)
**Tier**: 0 (Non-root)  
**Purpose**: Detect USB cable connection via 3 independent probes  
**Logic**: Requires 2 out of 3 probes to agree

### MODULE 5: Hotspot Indicators (hotspot.py)
**Tier**: 0 (Non-root)  
**Purpose**: Detect hosted or connected hotspot activity  
**Sources**: dumpsys wifi, netstats, saved network list

---

## Quick Start

### Example 1: Bluetooth Correlation
```python
from triage.parsers import bt_config

# Parse root-tier bond store
bonds = bt_config.parse_bt_config("/path/to/bt_config.conf")

# Parse non-root dumpsys output
dumpsys_devices = [
    {"mac": "XX:XX:XX:XX:EE:FF", "name": "MyDevice", "connected": True}
]

# Correlate the two sources
correlated = bt_config.correlate_bluetooth(bonds, dumpsys_devices)

# Access separated timestamps
for device in correlated:
    print(f"Address: {device['address']}")
    print(f"Bond written (UTC): {device['bond_record_written_utc']}")
    print(f"Connected at capture: {device['dumpsys_connected_at_dump_time']}")
    print(f"Caveats: {device['caveats']}")
```

### Example 2: Wi-Fi Passwords
```python
from pathlib import Path
from triage.parsers import wifi

# Parse Android 9+ XML format
xml_path = Path("/data/misc/wifi/WifiConfigStore.xml")
networks = wifi.parse_wifi_config_store_xml(xml_path)

for net in networks:
    print(f"SSID: {net.ssid}")
    print(f"Password: {net.password}")
    print(f"Security: {net.security}")  # WPA, WPA3, WEP, OPEN
    print(f"Source: {net.source_file}")
```

### Example 3: Wi-Fi Traffic History
```python
from triage.parsers import wifi_live

# Parse netstats output
netstats_output = """
ident=[{networkId="HomeWiFi", type=WIFI}] uid=-1 set=ALL tag=0x0
  NetworkStatsHistory: bucketDuration=3600
  st=1609459200 rb=1024000 rp=100 tb=512000 tp=50
"""

buckets = wifi_live.parse_netstats(netstats_output)

for bucket in buckets:
    print(f"SSID: {bucket.ssid}")
    print(f"Period: {bucket.bucket_start} to {bucket.bucket_end}")
    print(f"RX: {bucket.rx_bytes} bytes, TX: {bucket.tx_bytes} bytes")
    print(f"Approximate: {bucket.approximate}")  # Always True
    print(f"Caveats: {bucket.caveats}")
```

### Example 4: USB Connection State
```python
from triage.acquire.real import get_usb_state
from triage.adb import Adb

# Create ADB instance
adb = Adb()

# Check USB connection state
usb_state = get_usb_state(adb)

print(f"USB Connected: {usb_state['usb_connected']}")
print(f"Probes voted: {len(usb_state['probe_votes'])}/3")
print(f"Probe results:")
for probe, result in usb_state['probe_results'].items():
    print(f"  {probe}: {result}")
print(f"Caveats: {usb_state['caveats']}")
```

### Example 5: Hotspot Indicators
```python
from triage.parsers import hotspot

# Collect data sources
wifi_dumpsys = """
SoftAp state: ENABLED
SoftApManager - current state: StartedState
"""

wifi_config = [
    {"ssid": "AndroidAP1234"},
    {"ssid": "MyHomeWifi"}
]

netstats = """
ident=[{networkId="AndroidAP1234", type=WIFI}] uid=-1
  st=1609459200 rb=5000 rp=10 tb=3000 tp=5
"""

# Analyze indicators
result = hotspot.analyze_hotspot_indicators(wifi_dumpsys, netstats, wifi_config)

print(f"Hosted hotspot: {result['hosted_indicator']}")
print(f"Connected to hotspot: {result['connected_indicator']}")
print(f"Evidence:")
print(f"  Hosted: {result['details']['hosted_evidence']}")
print(f"  Connected: {result['details']['connected_evidence']}")
print(f"  Traffic: {result['details']['traffic_evidence']}")
print(f"Caveats: {result['caveats']}")
```

---

## Testing

### Run All Tests
```bash
cd /Users/lakshsorathiya/Android_Forensic/engine
python -m pytest tests/test_forensic_modules.py -v
```

### Run Specific Module Tests
```bash
# Bluetooth Correlation
pytest tests/test_forensic_modules.py::TestBluetoothCorrelation -v

# Wi-Fi Passwords
pytest tests/test_forensic_modules.py::TestWifiPasswords -v

# Wi-Fi Traffic History
pytest tests/test_forensic_modules.py::TestWifiTrafficHistory -v

# USB Connection State
pytest tests/test_forensic_modules.py::TestUSBConnectionState -v

# Hotspot Indicators
pytest tests/test_forensic_modules.py::TestHotspotIndicators -v
```

### Quick Verification
```bash
python verify_modules.py
```

### Test Coverage Summary
- **Total Tests**: 19
- **MODULE 1**: 3 tests
- **MODULE 2**: 4 tests
- **MODULE 3**: 3 tests
- **MODULE 4**: 3 tests
- **MODULE 5**: 5 tests
- **Dataclass**: 1 test

All tests use synthetic fixtures and require no physical Android device.

---

## Integration Guide

### Adding to Existing Pipeline

#### Step 1: Import Modules
```python
# Add to your acquisition script
from triage.parsers import bt_config, wifi, wifi_live, hotspot
from triage.acquire.real import get_usb_state
```

#### Step 2: Bluetooth Correlation
```python
# After pulling bt_config.conf (root required)
bonds = bt_config.parse_bt_config("/staged/bt_config.conf")

# After dumpsys bluetooth_manager
dumpsys_bt = adb.shell("dumpsys bluetooth_manager").stdout
# Parse dumpsys_bt to extract device list (implement parser)
dumpsys_devices = parse_dumpsys_bluetooth(dumpsys_bt)

# Correlate
correlated = bt_config.correlate_bluetooth(bonds, dumpsys_devices)

# Store in case database
store_bluetooth_evidence(correlated)
```

#### Step 3: Wi-Fi Credentials
```python
# Pull WifiConfigStore.xml (root required)
wifi_xml = pull_file("/data/misc/wifi/WifiConfigStore.xml")

# Parse credentials
networks = wifi.parse_wifi_config(wifi_xml)

# Store (redact passwords in reports if required)
store_wifi_credentials(networks)
```

#### Step 4: Wi-Fi Traffic
```python
# Capture netstats (non-root)
netstats_output = adb.shell("dumpsys netstats --full --uid").stdout

# Parse traffic buckets
buckets = wifi_live.parse_netstats(netstats_output)

# Generate timeline
timeline = wifi_live.build_wifi_timeline({"usage": buckets})
```

#### Step 5: USB State
```python
# Check USB connection (non-root)
usb_state = get_usb_state(adb)

# Log in device state
device_state["usb_connected"] = usb_state["usb_connected"]
device_state["usb_probes"] = usb_state["probe_results"]
```

#### Step 6: Hotspot Detection
```python
# Gather sources
wifi_dumpsys = adb.shell("dumpsys wifi").stdout
netstats = adb.shell("dumpsys netstats --full --uid").stdout
wifi_config = networks  # From step 3

# Analyze
hotspot_result = hotspot.analyze_hotspot_indicators(
    wifi_dumpsys, netstats, wifi_config
)

# Add to findings
if hotspot_result["hosted_indicator"]:
    findings.append("Device hosted a mobile hotspot")
if hotspot_result["connected_indicator"]:
    findings.append("Device connected to a mobile hotspot")
```

### Database Schema Additions

```sql
-- Bluetooth correlated devices
CREATE TABLE bluetooth_correlated (
    id INTEGER PRIMARY KEY,
    case_id TEXT NOT NULL,
    address TEXT NOT NULL,
    name TEXT,
    bond_written_utc TEXT,
    dumpsys_connected BOOLEAN,
    match_method TEXT,
    caveats TEXT,
    FOREIGN KEY (case_id) REFERENCES cases(id)
);

-- Wi-Fi credentials
CREATE TABLE wifi_credentials (
    id INTEGER PRIMARY KEY,
    case_id TEXT NOT NULL,
    ssid TEXT NOT NULL,
    password TEXT,
    security TEXT,
    source_file TEXT,
    FOREIGN KEY (case_id) REFERENCES cases(id)
);

-- Wi-Fi traffic buckets
CREATE TABLE wifi_traffic (
    id INTEGER PRIMARY KEY,
    case_id TEXT NOT NULL,
    ssid TEXT NOT NULL,
    bucket_start TEXT NOT NULL,
    bucket_end TEXT NOT NULL,
    rx_bytes INTEGER,
    tx_bytes INTEGER,
    approximate BOOLEAN DEFAULT 1,
    caveats TEXT,
    FOREIGN KEY (case_id) REFERENCES cases(id)
);

-- USB connection state
CREATE TABLE usb_state (
    id INTEGER PRIMARY KEY,
    case_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    usb_connected BOOLEAN,
    probe_votes TEXT,
    probe_results TEXT,
    caveats TEXT,
    FOREIGN KEY (case_id) REFERENCES cases(id)
);

-- Hotspot indicators
CREATE TABLE hotspot_indicators (
    id INTEGER PRIMARY KEY,
    case_id TEXT NOT NULL,
    hosted_indicator BOOLEAN,
    connected_indicator BOOLEAN,
    hosted_evidence TEXT,
    connected_evidence TEXT,
    traffic_evidence TEXT,
    caveats TEXT,
    FOREIGN KEY (case_id) REFERENCES cases(id)
);
```

---

## API Reference

### MODULE 1: bt_config.py

#### `correlate_bluetooth(bond_store_dict, dumpsys_list) -> list[dict]`
Correlate Bluetooth bond records with dumpsys output.

**Parameters:**
- `bond_store_dict`: dict - Result from `parse_bt_config()`
- `dumpsys_list`: list[dict] - List of devices from dumpsys, each with `mac`, `name`, `connected`, `last_seen`

**Returns:**
- list[dict] - Correlated records with fields:
  - `address`: str
  - `bond_record_written_utc`: str | None
  - `dumpsys_connected_at_dump_time`: bool | None
  - `match_method`: str - "full_mac", "redacted_suffix", "bond_only", "dumpsys_only"
  - `caveats`: list[str]

### MODULE 2: wifi.py

#### `parse_wifi_config_store_xml(path: Path) -> list[WifiNetwork]`
Parse WifiConfigStore.xml (Android 9+).

**Parameters:**
- `path`: Path - Path to XML file

**Returns:**
- list[WifiNetwork] - Each with `ssid`, `password`, `security`, `source_file`

#### `parse_wpa_supplicant_conf(path: Path) -> list[WifiNetwork]`
Parse wpa_supplicant.conf (Android ≤8).

### MODULE 3: wifi_live.py

#### `parse_netstats(text: str) -> list[WifiUsageBucket]`
Parse dumpsys netstats output.

**Parameters:**
- `text`: str - Raw netstats output

**Returns:**
- list[WifiUsageBucket] - Each with:
  - `ssid`: str
  - `bucket_start`: str (ISO-8601)
  - `bucket_end`: str (ISO-8601)
  - `rx_bytes`: int
  - `tx_bytes`: int
  - `approximate`: bool (always True)
  - `caveats`: list[str]

### MODULE 4: real.py

#### `get_usb_state(adb: Adb) -> dict`
Determine USB connection state via 3 probes.

**Parameters:**
- `adb`: Adb - ADB instance

**Returns:**
- dict with:
  - `usb_connected`: bool
  - `probe_results`: dict
  - `probe_votes`: list[str]
  - `caveats`: list[str]

### MODULE 5: hotspot.py

#### `analyze_hotspot_indicators(wifi_dumpsys, netstats, wifi_config) -> dict`
Analyze hotspot usage indicators.

**Parameters:**
- `wifi_dumpsys`: str - dumpsys wifi output
- `netstats`: str - dumpsys netstats output
- `wifi_config`: list[dict] - Saved networks with `ssid` field

**Returns:**
- dict with:
  - `hosted_indicator`: bool
  - `connected_indicator`: bool
  - `details`: dict with `hosted_evidence`, `connected_evidence`, `traffic_evidence`
  - `caveats`: list[str]

---

## Troubleshooting

### Test Failures

**Issue**: `ImportError: No module named 'triage'`
```bash
# Solution: Ensure you're in the engine directory
cd /Users/lakshsorathiya/Android_Forensic/engine
export PYTHONPATH="${PYTHONPATH}:$(pwd)"
python -m pytest tests/test_forensic_modules.py -v
```

**Issue**: Tests fail with "no such file or directory"
```bash
# Solution: Verify you're running from engine directory
pwd  # Should be: /Users/lakshsorathiya/Android_Forensic/engine
```

### Module Import Issues

**Issue**: Cannot import modules in your script
```python
# Solution: Add engine to Python path
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "engine"))

from triage.parsers import bt_config, wifi, wifi_live, hotspot
```

### ADB Connection Issues

**Issue**: `get_usb_state()` fails with subprocess errors
```python
# Solution: Verify ADB is installed and device is connected
import subprocess
result = subprocess.run(["adb", "devices"], capture_output=True, text=True)
print(result.stdout)  # Should show your device
```

### XML Parsing Issues

**Issue**: WifiConfigStore.xml parse returns empty list
```python
# Check if file is valid XML
import xml.etree.ElementTree as ET
try:
    tree = ET.parse("WifiConfigStore.xml")
    print("Valid XML")
except ET.ParseError as e:
    print(f"Invalid XML: {e}")
```

---

## File Structure

```
Android_Forensic/
├── README.md                           # Main project README (unchanged)
├── FORENSIC_MODULES_SETUP.md          # This file
├── FORENSIC_MODULES_SUMMARY.md        # Implementation summary
├── engine/
│   ├── triage/
│   │   ├── parsers/
│   │   │   ├── bt_config.py           # MODULE 1 (enhanced)
│   │   │   ├── wifi.py                # MODULE 2 (enhanced)
│   │   │   ├── wifi_live.py           # MODULE 3 (existing)
│   │   │   ├── hotspot.py             # MODULE 5 (new)
│   │   │   └── FORENSIC_MODULES_README.md  # Detailed API docs
│   │   ├── acquire/
│   │   │   └── real.py                # MODULE 4 (enhanced)
│   │   └── ...
│   ├── tests/
│   │   └── test_forensic_modules.py   # Unit tests
│   └── verify_modules.py              # Quick verification
└── ...
```

---

## Best Practices

### 1. Always Read Caveats
Every module output includes a `caveats` field. Read and include these in your reports.

### 2. Never Conflate Timestamps
- Bond write time ≠ Connection time
- Netstats bucket ≠ Association duration
- Use the separate fields provided

### 3. Handle Missing Data Gracefully
```python
# Good
if device.get("bond_record_written_utc"):
    print(f"Bond written: {device['bond_record_written_utc']}")
else:
    print("Bond timestamp not available")

# Bad - don't fabricate
# print(f"Bond written: {device.get('bond_record_written_utc', 'Unknown')}")
```

### 4. Test with Synthetic Data First
Use the test fixtures in `test_forensic_modules.py` as examples before running on real devices.

### 5. Log Everything
```python
import logging

logging.info(f"Parsing Bluetooth bonds from {path}")
bonds = bt_config.parse_bt_config(path)
logging.info(f"Found {len(bonds['bonds'])} bond records")
```

---

## Additional Resources

- **Full API Documentation**: `engine/triage/parsers/FORENSIC_MODULES_README.md`
- **Implementation Summary**: `FORENSIC_MODULES_SUMMARY.md`
- **Main Project README**: `README.md`
- **Test Examples**: `engine/tests/test_forensic_modules.py`

---

## Support & Contributing

For issues or questions:
1. Check the troubleshooting section above
2. Review test cases in `test_forensic_modules.py`
3. Check caveats in module output
4. Verify Python version (3.10+)

---

**Last Updated**: August 9, 2026  
**Version**: 1.0  
**Status**: Production Ready ✅
