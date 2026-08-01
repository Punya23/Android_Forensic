"""Tests for the root-tier Bluetooth bond store parser (bt_config.conf).

The three fixtures below are the verbatim sample files produced during research
for this artefact:

* ``SAMPLE_A`` — Android 9 era: no [Metrics], legacy Hid* keys, P-192 SSP link
  key types, one LE-only band with the classic empty ``LE_KEY_LID``.
* ``SAMPLE_B`` — Android 12/13 era: [Metrics], MetricsId, SdpDi*/ProductVersion,
  GATT caching keys, ServiceLe, and the full LE key set.
* ``SAMPLE_C`` — every edge case that breaks a naive parser: Common Criteria
  ``encrypted`` placeholders, an int32-wrapped timestamp, epoch zero, a name
  containing " = ", a duplicate ``LinkKey``, a non-MAC section header, a device
  record with no bond material, UTF-8 + emoji, and unknown vendor keys.

They are written to ``tmp_path`` inside the tests so nothing depends on a binary
fixture that may not exist.

The most important assertions in this file are the honesty ones:
``test_link_key_material_is_never_serialised``,
``test_timestamp_meaning_refuses_connection_language`` and
``test_bond_timeline_never_claims_a_connection``.
"""

from __future__ import annotations

import json
import re

import pytest

from triage.parsers.bt_config import (
    BT_CONFIG_PATHS,
    TIMESTAMP_MEANING,
    BluetoothAdapterInfo,
    BluetoothBond,
    bt_config_summary,
    build_bond_timeline,
    decode_device_class,
    detect_encrypted_config,
    merge_with_dumpsys,
    parse_bt_config,
    parse_bt_config_text,
)
from triage.parsers.oui import (
    OUI_REGISTRY_SNAPSHOT,
    OUI_TABLE,
    is_locally_administered,
    is_multicast,
    lookup_vendor,
    normalise_mac,
    oui_prefix,
    random_address_subtype,
)


# ---------------------------------------------------------------------------
# Fixtures (verbatim sample content)
# ---------------------------------------------------------------------------

SAMPLE_A = """[Info]
FileSource = Empty
TimeCreated = 2019-03-14 09:22:41

[Adapter]
Address = a4:50:46:1c:3e:77
LE_LOCAL_KEY_IRK = 9b1c74f0a2d3e6581047bcae3f92d70c
LE_LOCAL_KEY_IR = 4d81e0aa73c25b19f6083d4e97ab1c25
LE_LOCAL_KEY_DHK = e07f3c9a52b6d81403fe7a2c96db4e18
LE_LOCAL_KEY_ER = 1a6b3fd0472e9c85ba30d71e2f48c9a6
ScanMode = 2
DiscoveryTimeout = 120

[00:1e:7c:5b:a1:04]
Timestamp = 1552555361
Name = Jabra Halo Smart
DevClass = 2360324
DevType = 1
Manufacturer = 10
LmpVer = 6
LmpSubVer = 12034
Service = 0000110b-0000-1000-8000-00805f9b34fb 0000110c-0000-1000-8000-00805f9b34fb 0000110e-0000-1000-8000-00805f9b34fb 0000111e-0000-1000-8000-00805f9b34fb 00001200-0000-1000-8000-00805f9b34fb
LinkKeyType = 5
PinLength = 0
LinkKey = 4b8f2c1de6a70953bd41ce872f0a6d39

[e8:07:bf:3a:9d:12]
Timestamp = 1553120944
Name = Nissan Connect
DevClass = 6292512
DevType = 1
AddrType = 0
Manufacturer = 15
LmpVer = 6
LmpSubVer = 24838
Service = 0000110a-0000-1000-8000-00805f9b34fb 0000110b-0000-1000-8000-00805f9b34fb 0000110e-0000-1000-8000-00805f9b34fb 0000111e-0000-1000-8000-00805f9b34fb 0000112f-0000-1000-8000-00805f9b34fb 00001132-0000-1000-8000-00805f9b34fb
LinkKeyType = 4
PinLength = 0
LinkKey = c9d0114ae5763b28f10cd47a9e35b608

[3c:2e:f5:88:60:aa]
Timestamp = 1554233087
Name = MX Master 2S
DevClass = 9600
DevType = 3
AddrType = 0
Manufacturer = 10
LmpVer = 8
LmpSubVer = 8961
Service = 00001124-0000-1000-8000-00805f9b34fb 00001200-0000-1000-8000-00805f9b34fb
HidAppId = 0
HidAttrMask = 512
HidCountryCode = 0
HidSSRMaxLatency = 65535
HidSSRMinTimeout = 65535
HidSubClass = 192
HidVendorId = 1133
HidProductId = 45091
HidVersion = 273
HidDescriptor = 05010906a1018501050719e029e715002501750195088102950175088101050719002991150025917508950681000508190129059202950175038101c0
LinkKeyType = 5
PinLength = 0
LinkKey = 60f7ba1c3e94d582076ac13be9f2d840

[74:d2:1d:04:bb:9f]
Timestamp = 1556901233
Name = Mi Band 3
DevType = 2
AddrType = 1
LE_KEY_PENC = 8fd3a1c07e64b2591cae3708df6b421a3f92c1750ea6b8c300000710
LE_KEY_PID = c04e7a1936bd82f5104ce8ab7d29365f0174d21d04bb9f
LE_KEY_LENC = 8fd3a1c07e64b2591cae3708df6b421a00001007
LE_KEY_LID =
"""

SAMPLE_B = """[Info]
FileSource = Empty
TimeCreated = 2023-05-02 18:41:07

[Metrics]
Salt256Bit = 7d1f4b90c2ea38516ba07f4d29ce8130b64af95d7e2c018a34fd6b9e750c21af

[Adapter]
Address = 3a:81:c0:4f:2b:6e
LE_LOCAL_KEY_IRK = f2c07b1943a8de65021cbf7e94d3a086
LE_LOCAL_KEY_IR = 30ab5ce17f429d68b04e912c6ad3f857
LE_LOCAL_KEY_DHK = 9e14d7b0623fa85c1d70e4b98a26cf13
LE_LOCAL_KEY_ER = 5b830ade61c7492ff10d3b8a27e6c945
ScanMode = 0
DiscoveryTimeout = 120
LocalIOCaps = 3

[38:18:4c:71:0d:5a]
Name = WH-1000XM4
DevClass = 2360344
DevType = 3
AddrType = 0
Timestamp = 1683053201
Manufacturer = 301
LmpVer = 11
LmpSubVer = 34816
Service = 0000110b-0000-1000-8000-00805f9b34fb 0000110c-0000-1000-8000-00805f9b34fb 0000110e-0000-1000-8000-00805f9b34fb 0000111e-0000-1000-8000-00805f9b34fb 00001200-0000-1000-8000-00805f9b34fb 0000112f-0000-1000-8000-00805f9b34fb
ServiceLe = 0000fe03-0000-1000-8000-00805f9b34fb
LinkKeyType = 8
PinLength = 0
LinkKey = 7c4a91e0d5b3268f014ecab97d3f6250
SdpDiManufacturer = 1447
SdpDiModel = 3392
SdpDiHardwareVersion = 256
SdpDiVendorIdSource = 1
VendorIdSource = 1
VendorId = 1447
ProductId = 3392
ProductVersion = 256
AvdtpVersion = 259
AvrcpControllerVersion = 262
AvrcpPeerFeatures = 383
HfpVersion = 263
HfpSdpFeatures = 63
SecureConnectionsSupported = 1
MaxSessionKeySize = 16
MetricsId = 12
GattClientSupportedFeatures = 3
GattClientDatabaseHash = 2b41c07de95f8a613024bcfe7719d0a8
LE_KEY_PENC = 1d94c2a760f3b85e02ca7d419b6f3801000000000000000000000410
LE_KEY_PID = 7f0a3c91e5d264b8103fac7e29d506100038184c710d5a
LE_KEY_LENC = 1d94c2a760f3b85e02ca7d419b6f380100001004

[6c:5a:b0:22:e9:31]
Name = Toyota
DevClass = 6292512
DevType = 1
AddrType = 0
Timestamp = 1685739415
Manufacturer = 29
LmpVer = 10
LmpSubVer = 8963
Service = 00001105-0000-1000-8000-00805f9b34fb 0000110a-0000-1000-8000-00805f9b34fb 0000110c-0000-1000-8000-00805f9b34fb 0000110e-0000-1000-8000-00805f9b34fb 00001112-0000-1000-8000-00805f9b34fb 0000111f-0000-1000-8000-00805f9b34fb 00001132-0000-1000-8000-00805f9b34fb 00001200-0000-1000-8000-00805f9b34fb
LinkKeyType = 8
PinLength = 0
LinkKey = e30b7c92a418d6f5027bce104a9d3f68
PbapPceVersion = 258
AvdtpVersion = 259
HfpVersion = 263
SecureConnectionsSupported = 1
MetricsId = 13

[cb:07:9f:44:2d:e0]
Name = Pixel Watch
DevType = 2
AddrType = 1
Timestamp = 1686044930
Appearance = 193
ModelName = Google Pixel Watch
ServiceLe = 0000180a-0000-1000-8000-00805f9b34fb 0000180f-0000-1000-8000-00805f9b34fb 0000fd6f-0000-1000-8000-00805f9b34fb
MetricsId = 14
GattClientSupportedFeatures = 3
GattServerSupportedFeatures = 1
GattClientDatabaseHash = 91ad3f0c7e26b854103fda6b29c71048
MaxSessionKeySize = 16
LE_KEY_PENC = 4e18b7c02a935df6108ecb247f0a3961000000000000000000000410
LE_KEY_PID = a72f04e13c8b95d6207fae1b48c3d95001cb079f442de0
LE_KEY_PCSRK = 000000008b4e07d2196a3fc5027bde148a3f6c9004000000
LE_KEY_LENC = 4e18b7c02a935df6108ecb247f0a396100001004
LE_KEY_LCSRK = 00000000d16d041a63ca185d20860b9e56d6357151f50c9a
LE_KEY_LID =
"""

SAMPLE_C = """[Info]
FileSource = Legacy
TimeCreated = 2038-01-20 03:14:07

[Metrics]
Salt256Bit = 0000000000000000000000000000000000000000000000000000000000000000

[Adapter]
Address = 00:00:00:00:00:00
LE_LOCAL_KEY_IRK = encrypted
LE_LOCAL_KEY_IR = 270820dd1a2b833a545a085d0b97f503
LE_LOCAL_KEY_DHK =
LE_LOCAL_KEY_ER = dcf4a867299fee26dbe9be0ce6f28a77
ScanMode = 21
DiscoveryTimeout = 0
Name = Galaxy S21 = mine

# the line above is a device name that itself contains " = "
[AA:BB:CC:DD:EE:FF]
Name = Bose QC35 II
Aliase = Dad's headphones
DevClass = 2360344
DevType = 3
AddrType = 0
Timestamp = 2147483647
LinkKeyType = 8
PinLength = 0
LinkKey = encrypted
LE_KEY_PENC = encrypted
LE_KEY_PID = encrypted
LE_KEY_LENC = encrypted
LE_KEY_LID =
Restricted = 1

[d9:41:6b:07:c2:38]
Name = 김민준의 AirPods \U0001f3a7
DevClass = 2360344
DevType = 3
AddrType = 0
Timestamp = -2147483648
Manufacturer = 76
LmpVer = 12
LmpSubVer = 26
LinkKeyType = 8
PinLength = 0
LinkKey = a70b34ce91d258f60c3eba17d940f28c
LinkKey = 118bfe0472a3c96d5f80e14b273ac6d9

[7f:e2:04:9a:3c:11]
Name = BLE_Tracker
DevType = 2
AddrType = 1
Timestamp = 0
LE_KEY_PENC = 3f81ca02e7495b6d10c8fa3b72e6d941000000000000000000000410
LE_KEY_PID = 55c0a3e1748bd29f06e3ca17b48d20f0017fe2049a3c11

[04:52:c7:19:88:6b]
Name = HP LaserJet
DevClass = 1050752
DevType = 1
Timestamp = 1729384012

[not:a:mac:address]
Name = corrupted section
Timestamp = 1729384099

[e4:5f:01:aa:bb:cc]
Name    =    Extra   Spaces   Device
DevClass = 5898764
DevType=1
AddrType = 0
Timestamp = 1730000000
LinkKeyType = 7
PinLength = 6
LinkKey = 00112233445566778899aabbccddeeff
UnknownVendorKey = SEC_SOMETHING_PROPRIETARY
SdpDiPrimaryRecord = 1
"""

#: Every piece of key material that appears in the fixtures. None of these may
#: ever appear in serialised output.
SECRET_HEX = [
    # sample A
    "4b8f2c1de6a70953bd41ce872f0a6d39",
    "c9d0114ae5763b28f10cd47a9e35b608",
    "60f7ba1c3e94d582076ac13be9f2d840",
    "8fd3a1c07e64b2591cae3708df6b421a3f92c1750ea6b8c300000710",
    "c04e7a1936bd82f5104ce8ab7d29365f0174d21d04bb9f",
    "9b1c74f0a2d3e6581047bcae3f92d70c",
    "4d81e0aa73c25b19f6083d4e97ab1c25",
    # sample B
    "7c4a91e0d5b3268f014ecab97d3f6250",
    "e30b7c92a418d6f5027bce104a9d3f68",
    "1d94c2a760f3b85e02ca7d419b6f3801000000000000000000000410",
    "4e18b7c02a935df6108ecb247f0a3961000000000000000000000410",
    "000000008b4e07d2196a3fc5027bde148a3f6c9004000000",
    "00000000d16d041a63ca185d20860b9e56d6357151f50c9a",
    "7d1f4b90c2ea38516ba07f4d29ce8130b64af95d7e2c018a34fd6b9e750c21af",
    "f2c07b1943a8de65021cbf7e94d3a086",
    # sample C
    "a70b34ce91d258f60c3eba17d940f28c",
    "118bfe0472a3c96d5f80e14b273ac6d9",
    "00112233445566778899aabbccddeeff",
    "3f81ca02e7495b6d10c8fa3b72e6d941000000000000000000000410",
    "270820dd1a2b833a545a085d0b97f503",
]


def _write(tmp_path, name: str, text: str):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _bond(result, address: str) -> dict:
    """Fetch one bond row from a parse result by (case-insensitive) address."""
    target = address.upper()
    for bond in result["bonds"]:
        if bond.address.upper() == target:
            return bond.to_dict()
    raise AssertionError(f"{address} not in {[b.address for b in result['bonds']]}")


def _json(result) -> str:
    """Serialise a whole parse result the way the report/API layer would."""
    return json.dumps(result, default=lambda o: o.to_dict(), ensure_ascii=False)


# ---------------------------------------------------------------------------
# 1. Adapter / [Info] block
# ---------------------------------------------------------------------------


def test_adapter_block_parsed(tmp_path):
    result = parse_bt_config(_write(tmp_path, "bt_config.conf", SAMPLE_A))
    adapter = result["adapter"]

    assert isinstance(adapter, BluetoothAdapterInfo)
    assert adapter.address == "a4:50:46:1c:3e:77"
    assert adapter.scan_mode == 2
    assert adapter.scan_mode_label == "connectable_discoverable"
    assert adapter.discovery_timeout == 120
    assert adapter.file_source == "Empty"
    # TimeCreated is LOCAL time with no zone — kept verbatim, never "normalised".
    assert adapter.time_created == "2019-03-14 09:22:41"
    assert "LOCAL time" in adapter.time_created_note
    assert adapter.le_local_irk_present is True
    assert set(adapter.local_keys_present) == {
        "LE_LOCAL_KEY_IRK",
        "LE_LOCAL_KEY_IR",
        "LE_LOCAL_KEY_DHK",
        "LE_LOCAL_KEY_ER",
    }


def test_adapter_all_zero_address_gets_no_vendor(tmp_path):
    result = parse_bt_config(_write(tmp_path, "bt_config.conf", SAMPLE_C))
    adapter = result["adapter"]

    assert adapter.address == "00:00:00:00:00:00"
    assert adapter.vendor is None
    assert any("all-zero" in c for c in adapter.caveats)
    # ScanMode 21 is outside the AOSP enum: reported raw, never guessed.
    assert adapter.scan_mode == 21
    assert "unrecognised" in adapter.scan_mode_label
    assert any("outside the AOSP 0/1/2 enum" in c for c in adapter.caveats)
    # FileSource = Legacy must warn that bonds may predate the file.
    assert any("Legacy" in c for c in adapter.caveats)


# ---------------------------------------------------------------------------
# 2. Multi-device parsing
# ---------------------------------------------------------------------------


def test_multi_device_parse(tmp_path):
    result = parse_bt_config(_write(tmp_path, "bt_config.conf", SAMPLE_A))

    assert [b.address for b in result["bonds"]] == [
        "00:1E:7C:5B:A1:04",
        "E8:07:BF:3A:9D:12",
        "3C:2E:F5:88:60:AA",
        "74:D2:1D:04:BB:9F",
    ]
    assert all(isinstance(b, BluetoothBond) for b in result["bonds"])

    jabra = _bond(result, "00:1e:7c:5b:a1:04")
    assert jabra["name"] == "Jabra Halo Smart"
    assert jabra["has_link_key"] is True
    assert jabra["link_key_type"] == 5
    assert jabra["link_key_type_label"] == "authenticated_p192_ssp_mitm"
    assert jabra["bond_timestamp"] == "2019-03-14T09:22:41Z"
    assert jabra["manufacturer_id"] == 10
    assert jabra["manufacturer_label"] == "Cambridge Silicon Radio (CSR)"
    assert any("Handsfree (HFP HF)" in s for s in jabra["services"])


def test_android13_sample_full_parse(tmp_path):
    result = parse_bt_config(_write(tmp_path, "bt_config.conf", SAMPLE_B))
    summary = bt_config_summary(result)

    assert summary["total_bonds"] == 3
    assert summary["encrypted"] is False

    sony = _bond(result, "38:18:4c:71:0d:5a")
    assert sony["dev_type_label"] == "dual_bredr_le"
    assert sony["product_id"] == 3392
    assert sony["version"] == 256
    assert sony["secure_connections"] == 1
    assert sony["link_key_type_label"] == "authenticated_p256_secure_connections"
    assert sony["le_key_types"] == ["LE_KEY_PENC", "LE_KEY_PID", "LE_KEY_LENC"]
    assert sony["bond_timestamp"] == "2023-05-02T18:46:41Z"
    # SIG company id 301 is not in our confident subset -> no invented label.
    assert sony["manufacturer_id"] == 301
    assert sony["manufacturer_label"] is None

    watch = _bond(result, "cb:07:9f:44:2d:e0")
    assert watch["model_name"] == "Google Pixel Watch"
    assert watch["appearance"] == 193
    assert "LE_KEY_PCSRK" in watch["le_key_types"]
    assert any("Battery Service" in s for s in watch["services_le"])


# ---------------------------------------------------------------------------
# 3. Class of Device decoding
# ---------------------------------------------------------------------------


def test_decode_device_class_known_values():
    smartphone = decode_device_class(5898764)
    assert smartphone["hex"] == "0x5A020C"
    assert smartphone["major"] == "phone"
    assert smartphone["minor"] == "smartphone"
    assert set(smartphone["services"]) == {
        "telephony",
        "object_transfer",
        "capturing",
        "networking",
    }

    headphones = decode_device_class(2360344)
    assert (headphones["major"], headphones["minor"]) == ("audio_video", "headphones")
    assert set(headphones["services"]) == {"audio", "rendering"}

    headset = decode_device_class(2360324)
    assert (headset["major"], headset["minor"]) == ("audio_video", "wearable_headset")

    car = decode_device_class(6292512)
    assert (car["major"], car["minor"]) == ("audio_video", "car_audio")
    assert set(car["services"]) == {"telephony", "audio"}

    laptop = decode_device_class(3670284)
    assert (laptop["major"], laptop["minor"]) == ("computer", "laptop")

    # Major 5 Peripheral encodes keyboard/pointing flags in the minor field.
    mouse = decode_device_class(9600)
    assert mouse["major"] == "peripheral"
    assert mouse["minor"] == "pointing_device"
    assert "limited_discoverable_mode" in mouse["services"]

    keyboard = decode_device_class(9536)
    assert keyboard["minor"] == "keyboard"


def test_decode_device_class_unknown_is_not_miscellaneous():
    """DevClass 0 / absent means 'never learned', not the real class 'miscellaneous'."""
    for value in (0, None, "", "not-a-number", -1):
        decoded = decode_device_class(value)
        assert decoded["major"] == "unknown"
        assert decoded["minor"] == "unknown"
        assert decoded["services"] == []


def test_device_class_decoded_honestly_not_by_product_name(tmp_path):
    """The section named 'HP LaserJet' carries a CoD whose major class is Toy.

    The research draft asserted this value decoded to Imaging/printer; the bits
    say otherwise (0x100880 -> major 8). The parser reports the bits, never the
    class the friendly name implies.
    """
    result = parse_bt_config(_write(tmp_path, "bt_config.conf", SAMPLE_C))
    printer = _bond(result, "04:52:c7:19:88:6b")

    assert printer["name"] == "HP LaserJet"
    assert printer["dev_class_raw"] == 1050752
    assert printer["dev_class_label"].startswith("toy/")
    assert decode_device_class(1050752)["services"] == ["object_transfer"]


# ---------------------------------------------------------------------------
# 4. DevType / AddrType labels
# ---------------------------------------------------------------------------


def test_dev_type_and_addr_type_labels(tmp_path):
    result_a = parse_bt_config(_write(tmp_path, "bt_config.conf", SAMPLE_A))

    classic = _bond(result_a, "e8:07:bf:3a:9d:12")
    assert (classic["dev_type"], classic["dev_type_label"]) == (1, "bredr_classic")
    assert (classic["addr_type"], classic["addr_type_label"]) == (0, "public")

    dual = _bond(result_a, "3c:2e:f5:88:60:aa")
    assert dual["dev_type_label"] == "dual_bredr_le"

    le_only = _bond(result_a, "74:d2:1d:04:bb:9f")
    assert (le_only["dev_type"], le_only["dev_type_label"]) == (2, "le")
    assert (le_only["addr_type"], le_only["addr_type_label"]) == (1, "random")

    # AddrType absent on a BR/EDR-only entry is treated as public, with a caveat
    # saying so rather than silently.
    no_addr_type = _bond(result_a, "00:1e:7c:5b:a1:04")
    assert no_addr_type["addr_type"] is None
    assert no_addr_type["addr_type_label"] == "unknown"
    assert any("AddrType absent with DevType=1" in c for c in no_addr_type["caveats"])


# ---------------------------------------------------------------------------
# 5/6. OUI resolution and its suppression
# ---------------------------------------------------------------------------


def test_oui_table_is_a_real_ieee_subset():
    assert len(OUI_TABLE) >= 120
    assert all(len(k) == 6 and k.upper() == k for k in OUI_TABLE)
    assert all(int(k, 16) >= 0 for k in OUI_TABLE)  # every key is real hex
    orgs = " | ".join(OUI_TABLE.values())
    for vendor in ("Apple", "Samsung", "Google", "Sony", "Bose", "Xiaomi", "Garmin", "Bose"):
        assert vendor in orgs
    assert OUI_REGISTRY_SNAPSHOT  # lookups must be reproducible against a date


def test_mac_normalisation_and_flag_bits():
    assert normalise_mac("04-52-c7-19-88-6b") == "04:52:C7:19:88:6B"
    assert normalise_mac("0452c719886b") == "04:52:C7:19:88:6B"
    assert normalise_mac("not-a-mac") == ""
    assert normalise_mac("") == ""
    assert oui_prefix("04:52:c7:19:88:6b") == "0452C7"
    assert oui_prefix("garbage") == ""
    assert is_locally_administered("02:00:00:00:00:01") is True
    assert is_locally_administered("04:52:c7:19:88:6b") is False
    assert is_multicast("01:00:5e:00:00:01") is True
    assert random_address_subtype("7f:e2:04:9a:3c:11") == "resolvable"
    assert random_address_subtype("c1:02:03:04:05:06") == "static"


def test_oui_resolved_for_public_address(tmp_path):
    """A public address resolves, and the assignee may contradict the peer name.

    04:52:C7 is registered to Bose while the peer calls itself 'HP LaserJet' —
    exactly why the peer-supplied Name is never treated as attribution.
    """
    assert lookup_vendor("04:52:c7:19:88:6b", addr_type=0) == "Bose Corporation"

    result = parse_bt_config(_write(tmp_path, "bt_config.conf", SAMPLE_C))
    row = _bond(result, "04:52:c7:19:88:6b")
    assert row["vendor"] == "Bose Corporation"
    assert "public" in row["vendor_lookup_reason"]


def test_oui_suppressed_for_random_address(tmp_path):
    # Direct API contract: AddrType 1 (and 3) must never yield a vendor.
    assert lookup_vendor("04:52:c7:19:88:6b", addr_type=1) is None
    assert lookup_vendor("04:52:c7:19:88:6b", addr_type=3) is None
    assert lookup_vendor("04:52:c7:19:88:6b", addr_type=0xFF) is None
    # Locally administered / multicast / malformed are refused too.
    assert lookup_vendor("02:52:c7:19:88:6b", addr_type=0) is None
    assert lookup_vendor("nonsense", addr_type=0) is None

    result = parse_bt_config(_write(tmp_path, "bt_config.conf", SAMPLE_C))
    tracker = _bond(result, "7f:e2:04:9a:3c:11")
    assert tracker["addr_type"] == 1
    assert tracker["vendor"] is None
    assert tracker["random_address_subtype"] == "resolvable"
    assert any("carries no IEEE registry meaning" in c for c in tracker["caveats"])


def test_le_identity_address_recovered_from_pid(tmp_path):
    """LE_KEY_PID's trailing 7 bytes give the peer identity address + type."""
    result = parse_bt_config(_write(tmp_path, "bt_config.conf", SAMPLE_B))

    sony = _bond(result, "38:18:4c:71:0d:5a")
    assert sony["le_identity_address"] == "38:18:4C:71:0D:5A"
    assert sony["le_identity_addr_type"] == 0

    watch = _bond(result, "cb:07:9f:44:2d:e0")
    assert watch["le_identity_address"] == "CB:07:9F:44:2D:E0"
    assert watch["le_identity_addr_type"] == 1  # random identity -> still no OUI
    assert watch["vendor"] is None


# ---------------------------------------------------------------------------
# 7. Key-material redaction — the non-negotiable one
# ---------------------------------------------------------------------------


def test_link_key_material_is_never_serialised(tmp_path):
    """No LinkKey / LE_KEY_* / local-key hex may survive into serialised output.

    Presence is recorded as booleans and key NAMES only.
    """
    for name, sample in (("a", SAMPLE_A), ("b", SAMPLE_B), ("c", SAMPLE_C)):
        result = parse_bt_config(_write(tmp_path, f"bt_config_{name}.conf", sample))
        blob = _json(result)
        for secret in SECRET_HEX:
            assert secret not in blob, f"key material {secret} leaked from sample {name}"
        # The HID report descriptor is an unknown-key blob and must be redacted
        # by length rather than dumped verbatim.
        assert "05010906a1018501" not in blob
        # ...but the *fact* of the bond is still recorded.
        assert '"has_link_key"' in blob
        assert '"le_key_types"' in blob

    result_a = parse_bt_config(_write(tmp_path, "bt_config.conf", SAMPLE_A))
    band = _bond(result_a, "74:d2:1d:04:bb:9f")
    assert band["has_link_key"] is False
    assert band["le_key_types"] == ["LE_KEY_PENC", "LE_KEY_PID", "LE_KEY_LENC"]
    assert band["has_bond_material"] is True


def test_empty_le_key_is_present_not_absent(tmp_path):
    """`LE_KEY_LID =` is present-with-no-value, which is not the same as missing."""
    result = parse_bt_config(_write(tmp_path, "bt_config.conf", SAMPLE_A))
    band = _bond(result, "74:d2:1d:04:bb:9f")

    assert "LE_KEY_LID" in band["le_keys_empty"]
    assert "LE_KEY_LID" not in band["le_key_types"]
    assert any("present but empty" in c for c in band["caveats"])


# ---------------------------------------------------------------------------
# 8. Unknown / vendor / Gabeldorsche key preservation
# ---------------------------------------------------------------------------


def test_unknown_keys_are_preserved_not_dropped(tmp_path):
    result = parse_bt_config(_write(tmp_path, "bt_config.conf", SAMPLE_C))

    row = _bond(result, "e4:5f:01:aa:bb:cc")
    assert row["unknown_keys"]["UnknownVendorKey"] == "SEC_SOMETHING_PROPRIETARY"
    # SdpDiPrimaryRecord is NOT an AOSP key — surfaced, never decoded.
    assert row["unknown_keys"]["SdpDiPrimaryRecord"] == "1"
    assert result["unknown_keys"]["E4:5F:01:AA:BB:CC"]["UnknownVendorKey"] == (
        "SEC_SOMETHING_PROPRIETARY"
    )

    result_b = parse_bt_config(_write(tmp_path, "bt_config_b.conf", SAMPLE_B))
    sony = _bond(result_b, "38:18:4c:71:0d:5a")
    for key in ("MetricsId", "GattClientSupportedFeatures", "SdpDiManufacturer", "HfpVersion"):
        assert key in sony["unknown_keys"], f"{key} was dropped"
    # A hex blob under an unknown key is kept as a length note, not raw bytes.
    assert sony["unknown_keys"]["GattClientDatabaseHash"].startswith("<redacted binary blob")


def test_non_device_section_retained_and_not_counted_as_a_bond(tmp_path):
    result = parse_bt_config(_write(tmp_path, "bt_config.conf", SAMPLE_C))

    sections = [s["section"] for s in result["non_device_sections"]]
    assert "not:a:mac:address" in sections
    assert all(b.address != "not:a:mac:address" for b in result["bonds"])
    assert bt_config_summary(result)["non_device_sections"] == 1


# ---------------------------------------------------------------------------
# 9/10. Encryption (Common Criteria / NIAP mode)
# ---------------------------------------------------------------------------


def test_encrypted_placeholder_proves_bond_and_flags_cc_mode(tmp_path):
    path = _write(tmp_path, "bt_config.conf", SAMPLE_C)
    result = parse_bt_config(path)

    assert result["encrypted"] is True
    assert detect_encrypted_config(path) is True
    assert any("Common Criteria" in c for c in result["caveats"])

    bose = _bond(result, "AA:BB:CC:DD:EE:FF")
    # Crucially: an encrypted key value must NOT read as "no bond".
    assert bose["has_link_key"] is True
    assert bose["link_key_encrypted"] is True
    assert bose["has_bond_material"] is True
    assert set(bose["le_keys_encrypted"]) == {"LE_KEY_PENC", "LE_KEY_PID", "LE_KEY_LENC"}
    # A user-assigned alias is highly probative and is captured separately.
    assert bose["alias"] == "Dad's headphones"
    assert bose["restricted"] is True
    assert any("restricted/guest profile" in c for c in bose["caveats"])


def test_encrypted_sibling_files_detected(tmp_path):
    path = _write(tmp_path, "bt_config.conf", SAMPLE_A)  # clean, no placeholders
    (tmp_path / "bt_config.conf.encrypted-checksum").write_text("bt_config-origin-QUJD\n")

    assert detect_encrypted_config(path) is True
    result = parse_bt_config(path)
    assert result["encrypted"] is True
    assert any("sibling Common Criteria artefacts" in c for c in result["caveats"])
    # The readable metadata is still parsed normally.
    assert len(result["bonds"]) == 4


# ---------------------------------------------------------------------------
# 11/12. Missing and garbage inputs
# ---------------------------------------------------------------------------


def test_missing_file_is_not_reported_as_no_bonds(tmp_path):
    result = parse_bt_config(tmp_path / "does_not_exist.conf")

    assert result["bonds"] == []
    assert result["file_present"] is False
    assert result["encrypted"] is False
    joined = " ".join(result["caveats"])
    assert "NOT evidence that no device was ever paired" in joined
    assert "root-only" in joined
    # Summary must survive a missing file too.
    assert bt_config_summary(result)["total_bonds"] == 0


def test_garbage_file_is_unreadable_not_empty(tmp_path):
    path = _write(tmp_path, "bt_config.conf", "\x00\x01\x02 not an ini file at all \xff\n" * 20)
    result = parse_bt_config(path)

    assert result["bonds"] == []
    assert result["encrypted"] is True
    assert any("absence of bonds is NOT established" in c for c in result["caveats"])
    assert result["parsed"] is False


def test_empty_and_malformed_lines_recovered_never_raise(tmp_path):
    text = (
        "[Info]\nFileSource = Empty\n"
        "this line has no separator\n"
        "[unterminated section\n"
        "[aa:bb:cc:dd:ee:01]\nName = Survivor\nLinkKey = 0011223344556677\n"
        "= valueonly\n"
    )
    result = parse_bt_config_text(text, source_file="mem")

    # AOSP would discard the entire file on either bad line; we recover.
    assert [b.address for b in result["bonds"]] == ["AA:BB:CC:DD:EE:01"]
    reasons = " ".join(e["reason"] for e in result["parse_errors"])
    assert "no '=' separator" in reasons
    assert "unterminated section header" in reasons
    assert "empty key" in reasons

    # An empty file is honestly caveated, not silently "zero bonds".
    empty = parse_bt_config_text("", source_file="mem")
    assert empty["bonds"] == []
    assert any("NOT evidence" in c for c in empty["caveats"])


# ---------------------------------------------------------------------------
# 13-17. Timestamp semantics — the biggest overstatement risk
# ---------------------------------------------------------------------------


def test_timestamp_meaning_refuses_connection_language(tmp_path):
    for name, sample in (("a", SAMPLE_A), ("b", SAMPLE_B), ("c", SAMPLE_C)):
        result = parse_bt_config(_write(tmp_path, f"bt_config_{name}.conf", sample))
        assert result["bonds"], f"sample {name} produced no bonds"
        for bond in result["bonds"]:
            meaning = bond.to_dict()["timestamp_meaning"]
            assert meaning == TIMESTAMP_MEANING
            lowered = meaning.lower()
            assert "bond record" in lowered
            assert "not a connection time" in lowered
            assert "co-located" in lowered
            assert "pairing" in lowered
            # The field must never be relabelled as any of these.
            assert "first connected timestamp" not in lowered
            assert "last seen" not in lowered.replace("last-seen", "")
            assert bond.evidence_class == "bond_evidence"


def test_timestamp_int32_wrap_corrected_and_flagged(tmp_path):
    result = parse_bt_config(_write(tmp_path, "bt_config.conf", SAMPLE_C))
    airpods = _bond(result, "d9:41:6b:07:c2:38")

    assert airpods["bond_timestamp_raw"] == -2147483648
    assert airpods["bond_timestamp"] == "2038-01-19T03:14:08Z"
    assert any("int32_wrap = true" in c for c in airpods["caveats"])
    # UTF-8 + emoji names must survive untouched.
    assert airpods["name"] == "김민준의 AirPods \U0001f3a7"
    assert airpods["manufacturer_label"] == "Apple, Inc."


def test_timestamp_epoch_zero_is_not_rendered_as_1970(tmp_path):
    result = parse_bt_config(_write(tmp_path, "bt_config.conf", SAMPLE_C))
    tracker = _bond(result, "7f:e2:04:9a:3c:11")

    assert tracker["bond_timestamp_raw"] == 0
    # The rendered value must be absent — never the misleading 1970-01-01.
    assert tracker["bond_timestamp"] is None
    assert "1970" not in str(tracker["bond_timestamp"])
    assert any("clock was not set" in c for c in tracker["caveats"])
    assert not build_bond_timeline([b for b in result["bonds"] if b.bond_timestamp_raw == 0])


def test_timestamp_near_int32_ceiling_flagged(tmp_path):
    result = parse_bt_config(_write(tmp_path, "bt_config.conf", SAMPLE_C))
    bose = _bond(result, "AA:BB:CC:DD:EE:FF")

    assert bose["bond_timestamp_raw"] == 2147483647
    assert bose["bond_timestamp"] == "2038-01-19T03:14:07Z"
    assert any("near-wrap" in c for c in bose["caveats"])


def test_absent_timestamp_is_not_invented(tmp_path):
    """An LE bond with no Timestamp key must yield None, never the file mtime."""
    text = (
        "[Info]\nFileSource = Empty\n\n"
        "[74:d2:1d:04:bb:9f]\nName = Mi Band 3\nDevType = 2\nAddrType = 1\n"
        "LE_KEY_PENC = 8fd3a1c07e64b2591cae3708df6b421a3f92c1750ea6b8c300000710\n"
        "LE_KEY_LID =\n"
    )
    path = _write(tmp_path, "bt_config.conf", text)
    result = parse_bt_config(path)
    band = _bond(result, "74:d2:1d:04:bb:9f")

    assert band["bond_timestamp_raw"] is None
    assert band["bond_timestamp"] is None
    joined = " ".join(band["caveats"])
    assert "no Timestamp key" in joined
    assert "file mtime must NOT be substituted" in joined
    # And it contributes no timeline event rather than one dated from the file.
    assert build_bond_timeline(result["bonds"]) == []


# ---------------------------------------------------------------------------
# 18. Timeline wording
# ---------------------------------------------------------------------------


def test_bond_timeline_never_claims_a_connection(tmp_path):
    events = []
    for name, sample in (("a", SAMPLE_A), ("b", SAMPLE_B), ("c", SAMPLE_C)):
        result = parse_bt_config(_write(tmp_path, f"bt_config_{name}.conf", sample))
        events.extend(build_bond_timeline(result["bonds"]))

    assert events
    for event in events:
        summary = event["summary"]
        assert "bond" in summary.lower()
        assert "connected" not in summary.lower()
        assert "co-locat" not in summary.lower()
        assert event["kind"] == "bluetooth_bond"
        assert event["timestamp"].endswith("Z")

        # The peer-supplied device name is quoted verbatim and may itself
        # contain the word "connect" (e.g. "Nissan Connect"). Strip the quoted
        # name, then assert the tool's OWN wording claims no link whatsoever.
        generated = re.sub(r"'[^']*'", "", summary)
        assert "connect" not in generated.lower()
        assert "paired with" not in generated.lower()
        assert "seen" not in generated.lower()
        assert "pairing-record write time only" in generated

    # Bonds with no usable write time produce no event rather than a fake one.
    result_c = parse_bt_config(_write(tmp_path, "bt_config_c.conf", SAMPLE_C))
    assert len(result_c["bonds"]) == 5
    assert len(build_bond_timeline(result_c["bonds"])) == 4  # epoch-zero row dropped
    assert build_bond_timeline([]) == []

    timestamps = [e["timestamp"] for e in build_bond_timeline(result_c["bonds"])]
    assert timestamps == sorted(timestamps)


# ---------------------------------------------------------------------------
# 19. dumpsys correlation
# ---------------------------------------------------------------------------


def test_merge_with_dumpsys_keeps_both_provenances(tmp_path):
    result = parse_bt_config(_write(tmp_path, "bt_config.conf", SAMPLE_B))
    dumpsys = [
        # Full MAC (older Android / privileged dump)
        {
            "mac": "38:18:4C:71:0D:5A",
            "name": "WH-1000XM4",
            "bond_state": "bonded",
            "connected": True,
            "last_seen": "2023-06-05T10:00:00Z",
            "device_class": "audio",
            "is_paired": True,
        },
        # Redacted MAC (Android 8+): only the last two octets are visible
        {
            "mac": "XX:XX:XX:XX:E9:31",
            "name": "Toyota",
            "bond_state": "bonded",
            "connected": False,
            "last_seen": "",
            "device_class": "audio",
            "is_paired": True,
        },
        # Present live but with no persistent bond section
        {
            "mac": "XX:XX:XX:XX:11:22",
            "name": "Unknown Speaker",
            "bond_state": "none",
            "connected": False,
            "last_seen": "",
            "device_class": "audio",
            "is_paired": False,
        },
    ]

    merged = merge_with_dumpsys(result["bonds"], dumpsys)
    by_addr = {m["address"]: m for m in merged}

    sony = by_addr["38:18:4C:71:0D:5A"]
    assert sony["match_method"] == "full_mac"
    assert sony["dumpsys_connected_at_dump_time"] is True
    assert sony["bond_record_written_utc"] == "2023-05-02T18:46:41Z"
    assert len(sony["provenance"]) == 2
    # The bond time must never be presented as a connection time.
    assert sony["bond_record_written_meaning"] == TIMESTAMP_MEANING
    assert "connected_at" not in sony
    assert "last_connected" not in sony
    assert any("different measurements" in c for c in sony["caveats"])

    toyota = by_addr["6C:5A:B0:22:E9:31"]
    assert toyota["match_method"] == "redacted_suffix"
    assert toyota["match_ambiguous"] is False
    assert any("last two address octets only" in c for c in toyota["caveats"])

    # Watch has no dumpsys counterpart.
    watch = by_addr["CB:07:9F:44:2D:E0"]
    assert watch["match_method"] == "bond_only"
    assert watch["dumpsys"] is None

    orphan = by_addr["XX:XX:XX:XX:11:22"]
    assert orphan["match_method"] == "dumpsys_only"
    assert orphan["bond"] is None
    assert orphan["evidence_class"] == "adapter_state"
    assert any("No persistent bond is asserted" in c for c in orphan["caveats"])


def test_merge_flags_ambiguous_suffix_matches(tmp_path):
    text = (
        "[aa:bb:cc:dd:e9:31]\nName = One\nLinkKey = 0011223344556677\nTimestamp = 1683053201\n\n"
        "[11:22:33:44:e9:31]\nName = Two\nLinkKey = 8899aabbccddeeff\nTimestamp = 1683053202\n"
    )
    result = parse_bt_config_text(text, source_file="mem")
    dumpsys = [
        {"mac": "XX:XX:XX:XX:E9:31", "name": "Ambiguous", "connected": False},
        {"mac": "XX:XX:XX:XX:E9:31", "name": "Ambiguous 2", "connected": False},
    ]

    merged = merge_with_dumpsys(result["bonds"], dumpsys)
    ambiguous = [m for m in merged if m["match_ambiguous"]]
    assert ambiguous
    assert any("AMBIGUOUS" in c for m in ambiguous for c in m["caveats"])
    # No key material may leak through the merge path either.
    blob = json.dumps(merged)
    assert "0011223344556677" not in blob
    assert "8899aabbccddeeff" not in blob


# ---------------------------------------------------------------------------
# 20. JSON round trip
# ---------------------------------------------------------------------------


def test_json_round_trip_is_plain_types(tmp_path):
    result = parse_bt_config(_write(tmp_path, "bt_config.conf", SAMPLE_B))
    payload = {
        "adapter": result["adapter"].to_dict(),
        "bonds": [b.to_dict() for b in result["bonds"]],
        "summary": bt_config_summary(result),
        "timeline": build_bond_timeline(result["bonds"]),
        "unknown_keys": result["unknown_keys"],
    }

    reloaded = json.loads(json.dumps(payload))
    assert reloaded["bonds"][0]["address"] == "38:18:4C:71:0D:5A"
    assert reloaded["summary"]["total_bonds"] == 3
    assert reloaded["adapter"]["address"] == "3a:81:c0:4f:2b:6e"
    # Enums must have been flattened to their string values.
    assert reloaded["bonds"][0]["confidence"] == "live"
    assert reloaded["timeline"][0]["confidence"] == "live"


# ---------------------------------------------------------------------------
# 21-23. INI grammar edge cases
# ---------------------------------------------------------------------------


def test_split_on_first_equals_and_inner_spaces_preserved(tmp_path):
    result = parse_bt_config(_write(tmp_path, "bt_config.conf", SAMPLE_C))

    # A device name that itself contains " = " must survive intact.
    assert result["adapter"].name == "Galaxy S21 = mine"
    # Outer whitespace trimmed, inner runs preserved; `DevType=1` with no spaces.
    spaced = _bond(result, "e4:5f:01:aa:bb:cc")
    assert spaced["name"] == "Extra   Spaces   Device"
    assert spaced["dev_type"] == 1
    # PinLength 6 with an SSP P-256 key type is internally inconsistent.
    assert any("internally inconsistent" in c for c in spaced["caveats"])


def test_duplicate_key_last_wins_and_conflict_reported(tmp_path):
    result = parse_bt_config(_write(tmp_path, "bt_config.conf", SAMPLE_C))

    assert any(
        "duplicate key 'LinkKey'" in c and "last-wins" in c for c in result["caveats"]
    )
    airpods = _bond(result, "d9:41:6b:07:c2:38")
    assert airpods["has_link_key"] is True
    blob = _json(result)
    assert "a70b34ce91d258f60c3eba17d940f28c" not in blob
    assert "118bfe0472a3c96d5f80e14b273ac6d9" not in blob


def test_uppercase_section_matches_case_insensitively(tmp_path):
    result = parse_bt_config(_write(tmp_path, "bt_config.conf", SAMPLE_C))
    # Section header was uppercase in the file; addresses are normalised.
    assert _bond(result, "aa:bb:cc:dd:ee:ff")["address"] == "AA:BB:CC:DD:EE:FF"


def test_device_record_without_bond_material_is_not_called_paired(tmp_path):
    result = parse_bt_config(_write(tmp_path, "bt_config.conf", SAMPLE_C))
    printer = _bond(result, "04:52:c7:19:88:6b")

    assert printer["has_link_key"] is False
    assert printer["le_key_types"] == []
    assert printer["has_bond_material"] is False
    joined = " ".join(printer["caveats"])
    assert "device record without recoverable bond material" in joined
    assert "NOT asserted as a paired device" in joined


# ---------------------------------------------------------------------------
# 24-26. Provenance, summary, paths
# ---------------------------------------------------------------------------


def test_bak_source_is_marked_recovered(tmp_path):
    result = parse_bt_config(_write(tmp_path, "bt_config.bak", SAMPLE_A))

    assert all(b.confidence == "recovered" for b in result["bonds"])
    assert any("previous generation" in c for c in result["caveats"])
    # And a live .conf stays "live".
    live = parse_bt_config(_write(tmp_path, "bt_config.conf", SAMPLE_A))
    assert all(b.confidence == "live" for b in live["bonds"])


def test_summary_reports_honest_aggregates(tmp_path):
    result = parse_bt_config(_write(tmp_path, "bt_config.conf", SAMPLE_C))
    summary = bt_config_summary(result)

    assert summary["total_bonds"] == 5  # the non-MAC section is excluded
    assert summary["encrypted"] is True
    assert summary["without_bond_material"] == 1
    assert summary["restricted_profile_bonds"] == 1
    assert summary["with_user_alias"] == 1
    assert summary["vendor_suppressed_random_address"] == 1
    assert summary["bond_records_with_write_time"] == 4  # epoch-zero row excluded
    assert summary["evidence_class"] == "bond_evidence"
    assert summary["timestamp_meaning"] == TIMESTAMP_MEANING
    assert any("btsnoop_hci.log" in s for s in summary["corroboration_sources"])
    assert any("counter" in s.lower() for s in summary["corroboration_sources"])


def test_candidate_paths_cover_conf_and_backup():
    assert "/data/misc/bluedroid/bt_config.conf" in BT_CONFIG_PATHS
    assert "/data/misc/bluedroid/bt_config.bak" in BT_CONFIG_PATHS
    assert "/data/misc/bluedroid/bt_config.conf.new" in BT_CONFIG_PATHS
    assert any("encrypted" in p for p in BT_CONFIG_PATHS)


@pytest.mark.parametrize("sample", [SAMPLE_A, SAMPLE_B, SAMPLE_C])
def test_parse_is_total_and_never_raises(tmp_path, sample):
    """Truncating the fixture at every 40th character must never raise."""
    for cut in range(0, len(sample), 40):
        result = parse_bt_config_text(sample[:cut], source_file="mem")
        assert isinstance(result["bonds"], list)
        assert isinstance(result["caveats"], list)
        json.dumps(result, default=lambda o: o.to_dict(), ensure_ascii=False)
