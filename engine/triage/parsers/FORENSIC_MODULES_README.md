# Forensic Modules Implementation

This document describes the 5 new forensic modules implemented for Android device analysis.

## Overview

All modules follow forensic best practices:
- **Never fabricate data**: Absence is recorded honestly, not filled with defaults
- **Separate time semantics**: Different timestamps are kept distinct (bond write time ≠ connection time)
- **Explicit caveats**: Every limitation is documented in the output
- **Graceful degradation**: Malformed input is skipped, not fatal
- **Tier-aware**: Each module documents its access tier (root vs non-root)

---

## MODULE 1: Bluetooth Correlation

**File**: `triage/parsers/bt_config.py`

### Purpose
Correlates root-tier Bluetooth bond records (`/data/misc/bluedroid/bt_config.conf`) with non-root dumpsys output (`adb shell dumpsys bluetooth_manager`).

### Key Function
```python
correlate_bluetooth(bond_store_dict: dict, dumpsys_list: list[dict]) -> list[dict]
```

### Critical Features
1. **Separated time semantics**: 
   - `bond_record_written_utc`: When the pairing record was written to disk
   - `dumpsys_connected_at_capture`: Live connection state from dumpsys
   
2. **MAC address correlation**:
   - Full MAC match when available
   - Fallback to last 2 octets for redacted dumpsys output (Android 8+)
   - Ambiguous matches are flagged

3. **Mandatory caveat**:
   > "The bond timestamp is when the pairing record was written to disk, NOT a connection time."

### Usage Example
```python
from triage.parsers import bt_config

# Parse bond store
bonds = bt_config.parse_bt_config("/path/to/bt_config.conf")

# Parse dumpsys output
dumpsys_devices = [
    {"mac": "XX:XX:XX:XX:EE:FF", "name": "Device1", "connected": True}
]

# Correlate
correlated = bt_config.correlate_bluetooth(bonds, dumpsys_devices)

# Access separated timestamps
for device in correlated:
    print(f"Bond written: {device['bond_record_written_utc']}")
    print(f"Connected at dump: {device['dumpsys_connected_at_dump_time']}")
```

### Test Coverage
- Full MAC matching
- Redacted suffix matching
- Caveat verification
- Timestamp separation

---

## MODULE 2: Wi-Fi Passwords (Root Tier 2)

**File**: `triage/parsers/wifi.py`

### Purpose
Extracts Wi-Fi credentials from root-only config files with robust parsing.

### Supported Formats

#### 1. `WifiConfigStore.xml` (Android ≥ 9)
XML format with `<Network>` elements:
```xml
<Network>
  <WifiConfiguration>
    <string name="SSID">"MyNetwork"</string>
    <string name="PreSharedKey">"password123"</string>
    <string name="AllowedKeyMgmt">WPA_PSK</string>
  </WifiConfiguration>
</Network>
```

#### 2. `wpa_supplicant.conf` (Android ≤ 8)
INI-style format with `network={}` blocks:
```
network={
    ssid="OldNetwork"
    psk="oldpassword"
    key_mgmt=WPA-PSK
}
```

### Key Features
1. **Quote stripping**: Handles nested quotes in both SSID and password
2. **Security classification**: Maps `AllowedKeyMgmt` to human-readable labels
   - WPA/WPA2/WPA3/WEP/OPEN
3. **Robust parsing**: Malformed entries are skipped, not fatal

### Usage Example
```python
from pathlib import Path
from triage.parsers import wifi

# Auto-detect format and parse
networks = wifi.parse_wifi_config(Path("/data/misc/wifi/WifiConfigStore.xml"))

for net in networks:
    print(f"SSID: {net.ssid}")
    print(f"Password: {net.password}")
    print(f"Security: {net.security}")
    print(f"Source: {net.source_file}")
```

### Dataclass Return
```python
@dataclass
class WifiNetwork:
    ssid: str
    password: str
    security: str  # WPA, WPA3, WEP, OPEN
    source_file: str
```

### Test Coverage
- WPA/WPA2/WPA3 networks
- WEP networks
- Open networks
- Both XML and conf formats
- Quote handling

---

## MODULE 3: Wi-Fi Traffic History (Non-root Tier 0)

**File**: `triage/parsers/wifi_live.py`

### Purpose
Parses `dumpsys netstats --full --uid` to extract Wi-Fi byte usage per SSID per hour bucket.

### Key Function
```python
parse_netstats(text: str) -> list[WifiUsageBucket]
```

### Critical Features

1. **Hour-bucket resolution**: Traffic is grouped by hour (3600s or 7200s buckets)
2. **ALWAYS approximate**: `approximate = True` is hardcoded - this proves bytes moved, NOT continuous connection
3. **ISO-8601 timestamps**: Epoch seconds converted to `YYYY-MM-DDTHH:MM:SSZ`

### Parsing Details
- Looks for `networkId="SSID"` or `wifiNetworkKey="SSID"` to identify network
- Extracts `st=` (start epoch), `rb=` (rx bytes), `tb=` (tx bytes)
- Calculates `bucket_end` from `start + duration`

### Dataclass Return
```python
@dataclass
class WifiUsageBucket:
    ssid: str
    bucket_start: str  # ISO-8601
    bucket_end: str    # ISO-8601
    rx_bytes: int
    tx_bytes: int
    approximate: bool = True  # ALWAYS True
    caveats: list[str]
```

### Mandatory Caveats
> "This proves bytes moved during this hour-bucket, NOT continuous connection."

> "Bucket resolution is {duration}s — never narrow this interval."

### Usage Example
```python
from triage.parsers import wifi_live

netstats_output = """
ident=[{networkId="MyWiFi", type=WIFI}] uid=-1 set=ALL tag=0x0
  NetworkStatsHistory: bucketDuration=3600
  st=1609459200 rb=1024000 rp=100 tb=512000 tp=50
"""

buckets = wifi_live.parse_netstats(netstats_output)

for bucket in buckets:
    print(f"SSID: {bucket.ssid}")
    print(f"Period: {bucket.bucket_start} to {bucket.bucket_end}")
    print(f"RX: {bucket.rx_bytes} bytes, TX: {bucket.tx_bytes} bytes")
    print(f"Approximate: {bucket.approximate}")  # Always True
```

### Test Coverage
- Traffic parsing with multiple buckets
- ISO-8601 timestamp format
- `approximate=True` enforcement
- Caveat presence

---

## MODULE 4: USB Connection State (Non-root Tier 0)

**File**: `triage/acquire/real.py`

### Purpose
Determines if a USB cable is physically connected using three independent probes.

### Key Function
```python
get_usb_state(adb: Adb) -> dict
```

### Three Probes

1. **Type-C data role**: `/sys/class/typec/port0/data_role`
   - If says "host", USB is active
   
2. **Battery power source**: `dumpsys battery`
   - Check if "USB" appears as power source
   
3. **ADB devices list**: `adb devices`
   - If shows "device" state (not emulator), cable present

### Verdict Logic
**`usb_connected = True` if at least 2 out of 3 probes return true**

### Return Structure
```python
{
    "usb_connected": bool,
    "probe_results": {
        "typec_data_role": str,
        "battery_power_source": str,
        "adb_device_state": str
    },
    "probe_votes": list[str],  # Which probes voted True
    "caveats": list[str]
}
```

### Usage Example
```python
from triage.acquire.real import get_usb_state
from triage.adb import Adb

adb = Adb()
usb_state = get_usb_state(adb)

if usb_state["usb_connected"]:
    print("USB cable detected")
    print(f"Probes agreed: {usb_state['probe_votes']}")
else:
    print("USB not detected or insufficient evidence")
```

### Caveats
- "USB connection state reflects the moment of capture only"
- "Does not establish how long the cable was connected"
- "Does not identify the host computer"

### Test Coverage
- 2 out of 3 agreement
- Insufficient votes handling
- Probe failure handling
- Caveat verification

---

## MODULE 5: Hotspot Indicators (Non-root Tier 0)

**File**: `triage/parsers/hotspot.py`

### Purpose
Detects whether a device HOSTED or CONNECTED to a mobile hotspot.

### Key Function
```python
analyze_hotspot_indicators(
    wifi_dumpsys: str,
    netstats: str,
    wifi_config: list[dict]
) -> dict
```

### Three Detection Methods

1. **Hosted hotspot**: Look for "SoftAp" or "hostapd" in dumpsys wifi
2. **Connected to hotspot**: Look for "AndroidAP" or "Hotspot" in saved SSIDs
3. **Traffic evidence**: Look for non-zero bytes over hotspot SSIDs in netstats

### Return Structure
```python
{
    "hosted_indicator": bool,
    "connected_indicator": bool,
    "caveats": list[str],
    "details": {
        "hosted_evidence": list[str],
        "connected_evidence": list[str],
        "traffic_evidence": list[str]
    }
}
```

### Usage Example
```python
from triage.parsers import hotspot

wifi_dumpsys = """
SoftAp state: ENABLED
"""

wifi_config = [
    {"ssid": "AndroidAP1234"},
    {"ssid": "MyHomeWifi"}
]

netstats = """
ident=[{networkId="AndroidAP1234", type=WIFI}] uid=-1
  st=1609459200 rb=5000 rp=10 tb=3000 tp=5
"""

result = hotspot.analyze_hotspot_indicators(wifi_dumpsys, netstats, wifi_config)

if result["hosted_indicator"]:
    print("Device hosted a hotspot")
    
if result["connected_indicator"]:
    print("Device connected to another device's hotspot")
    
if result["details"]["traffic_evidence"]:
    print("Traffic detected over hotspot SSID")
```

### Critical Caveats
> "This detects whether a hotspot was active at capture time, or if traffic flowed over a hotspot SSID in a past hour. It DOES NOT prove the user intended to share data, nor does it log client MAC addresses."

### Test Coverage
- Hosted hotspot detection
- Connected hotspot detection
- Traffic evidence parsing
- No indicators handling
- Caveat verification

---

## Running Tests

All modules have comprehensive unit tests in `engine/tests/test_forensic_modules.py`:

```bash
cd /Users/lakshsorathiya/Android_Forensic/engine
python -m pytest tests/test_forensic_modules.py -v
```

Expected output:
```
19 passed in 0.06s
```

### Test Categories
- MODULE 1: 3 tests (correlation, redaction, caveats)
- MODULE 2: 4 tests (XML WPA/WPA3, conf, WEP)
- MODULE 3: 3 tests (traffic, timestamps, caveats)
- MODULE 4: 3 tests (USB detection, votes, caveats)
- MODULE 5: 5 tests (hosted, connected, traffic, none, caveats)
- Dataclass: 1 test (approximate field)

---

## Design Principles

### 1. Forensic Honesty
- Absence is recorded as "not found", never as "empty"
- Uncertain data includes explicit "approximate" or "unverified" markers
- Source files are always named

### 2. Time Semantics
Different timestamps mean different things and are never conflated:
- Bond write time ≠ connection time
- Netstats bucket ≠ association duration
- Capture time ≠ historical event

### 3. Caveats as First-Class Data
Every record includes a `caveats` field documenting:
- What the data proves
- What it does NOT prove
- Limitations of the measurement
- Reliability considerations

### 4. Tier Awareness
- **Tier 0 (non-root)**: dumpsys, sysfs reads, adb devices
- **Tier 2 (root)**: `/data/misc` files requiring root

### 5. Python 3.10+ Ready
All code uses:
- Type hints
- Dataclasses
- Pathlib
- No external dependencies beyond stdlib (xml.etree, re, time)

---

## Integration Notes

### Importing
```python
# Bluetooth
from triage.parsers.bt_config import correlate_bluetooth, parse_bt_config

# Wi-Fi passwords
from triage.parsers.wifi import parse_wifi_config

# Wi-Fi traffic
from triage.parsers.wifi_live import parse_netstats

# USB state
from triage.acquire.real import get_usb_state

# Hotspot
from triage.parsers.hotspot import analyze_hotspot_indicators
```

### Dependencies
All modules depend only on:
- `triage.models` (for dataclasses like WifiNetwork, WifiUsageBucket)
- `triage.config` (for Confidence enum)
- `triage.adb` (for Adb class in MODULE 4)

No external PyPI packages required.

---

## File Locations

```
engine/
├── triage/
│   ├── parsers/
│   │   ├── bt_config.py       # MODULE 1 (enhanced)
│   │   ├── wifi.py            # MODULE 2 (enhanced)
│   │   ├── wifi_live.py       # MODULE 3 (existing, used here)
│   │   └── hotspot.py         # MODULE 5 (new)
│   └── acquire/
│       └── real.py            # MODULE 4 (enhanced)
└── tests/
    └── test_forensic_modules.py  # All unit tests
```

---

## Authors & License

Implemented as part of the Android Forensic project.

All modules follow the existing codebase license and standards.
