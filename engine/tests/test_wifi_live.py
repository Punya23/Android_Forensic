"""Tests for the non-root (Tier 0) live Wi-Fi dumpsys parser.

Every fixture below is a realistic ``dumpsys`` block: the ``mWifiInfo`` lines,
the ``WifiConfigManager`` fences, the ``ScanResultUtil.dumpScanResults`` table,
the ``NetworkStatsHistory`` rows and the ``NetworkAgentInfo`` lines are all
reproduced in the exact shape the framework emits them, including the known
parser traps (the Android 13+ run-together ``<none><none>MLO Information:``
token, the locale-dependent comma decimal separator in the scan-result age,
``bucketDuration`` 3600 vs 7200, ``networkId=`` vs ``wifiNetworkKey="X"suffix``,
and the duplicate ConfigurationMap listing).

The honesty assertions are as important as the parsing assertions: no record
may claim a join time, netstats records must be flagged approximate and must
name their bucket duration, and a randomised MAC must never be presented as a
hardware address.
"""

from __future__ import annotations

import json

import pytest

from triage.config import Confidence
from triage.parsers.wifi_live import (
    DEFAULT_MAC_PLACEHOLDER,
    WIFI_DUMPSYS_COMMANDS,
    WifiConnectionState,
    WifiSavedNetwork,
    WifiScanResult,
    WifiUsageBucket,
    build_wifi_timeline,
    collect_wifi_live,
    parse_connectivity,
    parse_netstats,
    parse_wifi_dumpsys,
    wifi_live_summary,
)

# ===========================================================================
# Fixtures — verbatim-shaped dumpsys output
# ===========================================================================

# Android 14, associated. mWifiInfo line built strictly from the
# android-14.0.0_r1 WifiInfo.toString() field order, including the unseparated
# mNetworkKey append that produces `<none>"HomeNet_5G"WPA_PSKMLO Information: `.
WIFI_A14_CONNECTED = """Wi-Fi is enabled
Verbose logging is disabled
mLinkProperties {InterfaceName: wlan0 LinkAddresses: [ 192.168.1.234/24 ] DnsAddresses: [ /192.168.1.1 ] Domains: lan MTU: 0}
mWifiInfo SSID: "HomeNet_5G", BSSID: 8a:de:4b:f0:1f:15, MAC: 0e:e6:6a:3f:06:b0, IP: /192.168.1.234, Security type: 2, Supplicant state: COMPLETED, Wi-Fi standard: 6, RSSI: -48, Link speed: 1200Mbps, Tx Link speed: 1200Mbps, Max Supported Tx Link speed: 2401Mbps, Rx Link speed: 866Mbps, Max Supported Rx Link speed: 2401Mbps, Frequency: 5220MHz, Net ID: 0, Metered hint: false, score: 60, isUsable: true, CarrierMerged: false, SubscriptionId: -1, IsPrimary: 1, Trusted: true, Restricted: false, Ephemeral: false, OEM paid: false, OEM private: false, OSU AP: false, FQDN: <none>, Provider friendly name: <none>, Requesting package name: <none>"HomeNet_5G"WPA_PSKMLO Information: , Is TID-To-Link negotiation supported by the AP: false, AP MLD Address: <none>, AP MLO Link Id: <none>, AP MLO Affiliated links: []
mLastBssid 8a:de:4b:f0:1f:15
mLastNetworkId 0
mLastSignalLevel 4

Dump of WifiConfigManager
WifiConfigManager - Log Begin ----
2026-02-11T16:23:41.505 - updateNetworkSelectionStatus: 0 setting status to NETWORK_SELECTION_ENABLED
WifiConfigManager - Log End ----
WifiConfigManager - Configured networks Begin ----
ID: 0 SSID: "TP-Link_1F18_6G" PROVIDER-NAME: null BSSID: null FQDN: null HOME-PROVIDER-NETWORK: false PRIO: 0 HIDDEN: false PMF: true CarrierId: -1 SubscriptionId: -1 SubscriptionGroup: null Currently Connected: false User Selected: false
 NetworkSelectionStatus NETWORK_SELECTION_ENABLED
 hasEverConnected: true
 hasNeverDetectedCaptivePortal: true
 mCandidateSecurityParams: null mLastUsedSecurityParams: Security Parameters:
 Type: 4
 Enabled: true
 KeyMgmt: SAE
 Protocols: RSN
 AuthAlgorithms:
 PairwiseCiphers: CCMP GCMP_256 GCMP_128
 GroupCiphers: CCMP GCMP_256 GCMP_128
 RequirePmf: true
 numAssociation 1
 validatedInternetAccess shared trusted
 macRandomizationSetting: 3
 mRandomizedMacAddress: 0e:e6:6a:3f:06:b0
 randomizedMacExpirationTimeMs: 02-11 20:23:43.415
 randomizedMacLastModifiedTimeMs: <none>
 deletionPriority: 0
 KeyMgmt: SAE Protocols: RSN
 AuthAlgorithms:
 PairwiseCiphers: CCMP GCMP_256 GCMP_128
 GroupCiphers: CCMP GCMP_256 GCMP_128
 PSK/SAE: *
Enterprise config:
IP config:
IP assignment: DHCP
Proxy settings: NONE
 cuid=10068 cname=com.oculus.panelapp.settings luid=10068 lname=com.oculus.panelapp.settings lcuid=10068 allowAutojoin=true noInternetAccessExpected=false mostRecentlyConnected=false
lastConnected: 02-11 16:23:41.505

numRebootsSinceLastUse: 2
recentFailure: Association Rejection code: 0, last update time: 0
bssidAllowlist unset
IsDppConfigurator: true
HasEncryptedPreSharedKey: false
ID: 1 SSID: "HomeNet_5G" PROVIDER-NAME: null BSSID: null FQDN: null HOME-PROVIDER-NETWORK: false PRIO: 0 HIDDEN: false PMF: false CarrierId: -1 SubscriptionId: -1 SubscriptionGroup: null Currently Connected: true User Selected: true
 NetworkSelectionStatus NETWORK_SELECTION_ENABLED
 hasEverConnected: true
 numAssociation 55
 validatedInternetAccess shared trusted
 macRandomizationSetting: 3
 mRandomizedMacAddress: 7e:6b:50:e3:a0:76
 deletionPriority: 0
 KeyMgmt: WPA_PSK Protocols: WPA RSN
 AuthAlgorithms: OPEN
 PairwiseCiphers: CCMP
 GroupCiphers: TKIP CCMP
 PSK/SAE: *
Enterprise config:
IP config:
IP assignment: DHCP
Proxy settings: NONE
 cuid=1000 cname=android.uid.system:1000 luid=1000 lname=android lcuid=1000 allowAutojoin=true noInternetAccessExpected=false mostRecentlyConnected=true
lastConnected: 02-11 18:02:07.113

numRebootsSinceLastUse: 0
recentFailure: Association Rejection code: 0, last update time: 0
bssidAllowlist unset
IsDppConfigurator: true
HasEncryptedPreSharedKey: false

WifiConfigManager - Configured networks End ----
WifiConfigManager - ConfigurationMap Begin ----
mPerId={0=ID: 0 SSID: "TP-Link_1F18_6G" PROVIDER-NAME: null BSSID: null FQDN: null HOME-PROVIDER-NETWORK: false PRIO: 0 HIDDEN: false, 1=ID: 1 SSID: "HomeNet_5G" PROVIDER-NAME: null BSSID: null FQDN: null HOME-PROVIDER-NETWORK: false PRIO: 0 HIDDEN: false}
WifiConfigManager - ConfigurationMap End ----
WifiConfigManager - Next network ID to be allocated 2
WifiConfigManager - Last selected network ID 1
"""

# Android 14, DISCONNECTED. Note MAC: 02:00:00:00:00:00 — the framework
# placeholder, not an address — and the run-together MLO token.
WIFI_A14_DISCONNECTED = """mWifiInfo SSID: <unknown ssid>, BSSID: <none>, MAC: 02:00:00:00:00:00, IP: null, Security type: -1, Supplicant state: DISCONNECTED, Wi-Fi standard: 4, RSSI: -127, Link speed: -1Mbps, Tx Link speed: -1Mbps, Max Supported Tx Link speed: -1Mbps, Rx Link speed: -1Mbps, Max Supported Rx Link speed: -1Mbps, Frequency: -1MHz, Net ID: -1, Metered hint: false, score: 0, isUsable: true, CarrierMerged: false, SubscriptionId: -1, IsPrimary: 0, Trusted: false, Restricted: false, Ephemeral: false, OEM paid: false, OEM private: false, OSU AP: false, FQDN: <none>, Provider friendly name: <none>, Requesting package name: <none><none>MLO Information: , Is TID-To-Link negotiation supported by the AP: false, AP MLD Address: <none>, AP MLO Link Id: <none>, AP MLO Affiliated links: <none>
"""

# Android 8/9-era device: pre-randomisation, so the factory OUI MAC leaks, and
# the saved-network schema is the older one (creation time=, PSK: *, no
# mRandomizedMacAddress / numRebootsSinceLastUse / lastConnected).
WIFI_A9_LEGACY = """mLinkProperties {LinkAddresses: []  Routes: [] DnsAddresses: [] Domains: null MTU: 0}
mWifiInfo SSID: <unknown ssid>, BSSID: <none>, MAC: 50:5b:c2:74:5a:9b, Supplicant state: DISCONNECTED, RSSI: -127, Link speed: -1Mbps, Frequency: -1MHz, Net ID: -1, Metered hint: false, score: 0
mDhcpResults null
mNetworkInfo [type: WIFI[], state: DISCONNECTED/DISCONNECTED, reason: (unspecified), extra: <unknown ssid>, failover: false, available: true, roaming: false]
mLastSignalLevel -1
mLastBssid null
mLastNetworkId -1
mOperationalMode 1

Dump of WifiConfigManager
WifiConfigManager - Log Begin ----
2019-09-17T09:00:01.897 - clearInternalData: Clearing all internal data
WifiConfigManager - Log End ----
WifiConfigManager - Configured networks Begin ----
ID: 0 SSID: "EFSociety" PROVIDER-NAME: null BSSID: null FQDN: null PRIO: 0 HIDDEN: false
 NetworkSelectionStatus NETWORK_SELECTION_ENABLED
 hasEverConnected: true
 numAssociation 13
 creation time=09-17 09:02:33.422
 validatedInternetAccess
 KeyMgmt: WPA_PSK Protocols: WPA RSN
 AuthAlgorithms: OPEN
 PairwiseCiphers: TKIP CCMP
 GroupCiphers: WEP40 WEP104 TKIP CCMP
 PSK: *
 sim_num
Enterprise config:
IP config:
IP assignment: DHCP
Proxy settings: NONE
 cuid=1000 cname=android.uid.system:1000 luid=1000 lname=android.uid.system:1000 lcuid=1000 userApproved=USER_UNSPECIFIED noInternetAccessExpected=false roamingFailureBlackListTimeMilli: 1000
recentFailure: Association Rejection code: 0
ShareThisAp: false

WifiConfigManager - Configured networks End ----
WifiConfigManager - Next network ID to be allocated 1
WifiConfigManager - Last selected network ID -1
"""

# Android 15/16: logTimeOfDay() switched to LocalDateTime.toString(), so the
# timestamps are ISO-8601 — but still device-local with no UTC offset.
WIFI_A15_SAVED = """mWifiInfo SSID: "OfficeGuest", BSSID: ec:a9:40:6b:bf:8f, MAC: de:13:aa:01:9c:2f, IP: /10.0.0.55, Security type: 2, Supplicant state: COMPLETED, Wi-Fi standard: 11ax, RSSI: -61, Link speed: 433Mbps, Tx Link speed: 433Mbps, Frequency: 5180MHz, Net ID: 3, Metered hint: false, score: 60
WifiConfigManager - Configured networks Begin ----
ID: 3 SSID: "OfficeGuest" PROVIDER-NAME: null BSSID: null FQDN: null HOME-PROVIDER-NETWORK: false PRIO: 0 HIDDEN: false PMF: false CarrierId: -1 SubscriptionId: -1 SubscriptionGroup: null Currently Connected: true User Selected: false
 NetworkSelectionStatus NETWORK_SELECTION_ENABLED
 hasEverConnected: true
 numAssociation 55
 validatedInternetAccess shared trusted
 macRandomizationSetting: 3
 mRandomizedMacAddress: 7e:6b:50:e3:a0:76
 randomizedMacExpirationTimeMs: 2026-01-04T14:18:20.438
 randomizedMacLastModifiedTimeMs: <none>
 persistentMacRandomizationSeed: 0
 deletionPriority: 0
 KeyMgmt: WPA_PSK Protocols: WPA RSN
 PSK/SAE: *
IP config:
IP assignment: DHCP
Proxy settings: NONE
 cuid=1000 cname=android.uid.system:1000 luid=1000 lname=android lcuid=1000 allowAutojoin=true noInternetAccessExpected=false mostRecentlyConnected=true
lastConnected: 2026-01-03T14:18:20.457

lastUpdated: 2026-01-01T00:12:32.191
numRebootsSinceLastUse: 0
bssidAllowlist unset
WifiConfigManager - Configured networks End ----
"""

# `dumpsys wifiscanner` — ScanResultUtil.dumpScanResults table. The third row
# exercises the dual-radio-chain rssi form AND the comma decimal separator that
# a non-en-locale device produces via the default-locale %3.3f.
WIFISCANNER_DUMP = """WifiScanningService:
Latest scan results:
    BSSID              Frequency      RSSI           Age(sec)     SSID                                 Flags
  00:10:94:11:11:00       2422        -32              5.166    AP1                               [ESS]
  00:10:94:22:22:00       2422        -46              5.166    AP2                               [ESS]
  da:fa:50:8a:16:31       5240      -17(0:-19/1:-21)    442,265    Fraise                            [WPA2-PSK-CCMP-128][RSN-PSK-CCMP-128][ESS]

Latest native scan results:
  aa:bb:cc:dd:ee:ff       5745        -71              12.004   Cafe Free WiFi                    [ESS]
Latest native pno scan results:
WificondScannerImpl - Log Begin ----
2019-07-11T13:48:43.994 - processPendingScans: freqs = null, hNetworkSSIDSet = []
WificondScannerImpl - Log End ----
"""

# `dumpsys netstats --full --uid`, Android <=12 schema (`networkId=`, type=WIFI).
NETSTATS_A12 = """Configs:
  netstats_combine_subtype_enabled=false
Active interfaces:
  iface=wlan0 ident=[{type=WIFI, subType=0, networkId="Dung Pham", metered=false, defaultNetwork=true}]
Active UID interfaces:
  iface=wlan0 ident=[{type=WIFI, subType=0, networkId="Dung Pham", metered=false, defaultNetwork=true}]
Top openSession callers (uid=count):
  10094=3698

Stats Providers:
  BpfCoordinator Xt:
    NetworkStats: elapsedRealtime=0
Dev stats:
  Pending bytes: 0
  Complete history:
  ident=[{type=WIFI, subType=0, networkId="ATTT-LAB", metered=false, defaultNetwork=false}] uid=-1 set=ALL tag=0x0
    NetworkStatsHistory: bucketDuration=3600
      st=1760770800 rb=0 rp=0 tb=135 tp=2 op=0
      st=1760774400 rb=702 rp=8 tb=772 tp=12 op=0
  ident=[{type=WIFI, subType=0, networkId="Dung Pham", metered=false, defaultNetwork=true}] uid=-1 set=ALL tag=0x0
    NetworkStatsHistory: bucketDuration=3600
      st=1682478000 rb=4147 rp=18 tb=2572 tp=27 op=0
      st=1760634000 rb=539110800 rp=382246 tb=6817399 tp=91650 op=0
      st=1760637600 rb=358310242 rp=269779 tb=6631147 tp=61480 op=0
UID stats:
  Pending bytes: 1968689
  Complete history:
  ident=[{type=WIFI, subType=0, networkId="ATTT-LAB", metered=false, defaultNetwork=true}] uid=1000 set=FOREGROUND tag=0x0
    NetworkStatsHistory: bucketDuration=7200
      st=1760767200 rb=363197 rp=427 tb=20962 tp=231 op=0
      st=1760774400 rb=687001 rp=693 tb=20910 tp=330 op=0
"""

# `dumpsys netstats --full --uid`, Android 13+ schema: raw int `type=`,
# `wifiNetworkKey="Fraise"wpa2-psk`, plus a cellular identity that must be
# ignored (its subscriberId is framework-scrubbed IMSI, not Wi-Fi).
NETSTATS_A13 = """Xt stats:
  Complete history:
  ident=[{type=0, ratType=-2, subscriberId=208202..., metered=false, defaultNetwork=false, oemManaged=OEM_NONE, subId=1}] uid=-1 set=ALL tag=0x0
    NetworkStatsHistory: bucketDuration=3600
      st=1756983600 rb=2526 rp=8 tb=3681 tp=7 op=0
      st=1756987200 rb=1638 rp=3 tb=2535 tp=3 op=0
Uid stats:
  ident=[{type=1, ratType=COMBINED, wifiNetworkKey="Fraise"wpa2-psk, metered=false, defaultNetwork=true, oemManaged=OEM_NONE, subId=-1}] uid=1005009 set=FOREGROUND tag=0x0
    NetworkStatsHistory: bucketDuration=7200
      st=1758002400 rb=7183 rp=15 tb=3671 tp=16 op=0
      st=1758016800 rb=7297 rp=16 tb=3631 tp=15 op=0
"""

# A history series whose only bucket carries no bytes at all. A zero bucket is
# NOT evidence of disconnection — an idle-but-associated link emits no bucket.
NETSTATS_ZERO_ONLY = """Dev stats:
  Complete history:
  ident=[{type=WIFI, subType=0, networkId="QuietNet", metered=false, defaultNetwork=true}] uid=-1 set=ALL tag=0x0
    NetworkStatsHistory: bucketDuration=3600
      st=1760770800 rb=0 rp=0 tb=0 tp=0 op=0
"""

# Truncated netstats — the examiner forgot `--full`.
NETSTATS_TRUNCATED = """Dev stats:
  Complete history:
  ident=[{type=WIFI, subType=0, networkId="HomeNet_5G", metered=false, defaultNetwork=true}] uid=-1 set=ALL tag=0x0
    NetworkStatsHistory: bucketDuration=3600
      (omitting 214 buckets)
      st=1760770800 rb=8891 rp=12 tb=1207 tp=9 op=0
"""

# `dumpsys connectivity`, Wi-Fi connected + validated.
CONNECTIVITY_WIFI = """NetworkProviders for:
Active default network: 602

Current Networks:
  NetworkAgentInfo{ ni{[type: WIFI[], state: CONNECTED/CONNECTED, reason: (unspecified), extra: , failover: false, available: true, roaming: false]}  network{602}  nethandle{2588976009229}  lp{{InterfaceName: wlan0 LinkAddresses: [ fe80::a009:f6ff:fefb:cb9f/64,192.168.1.234/24 ] DnsAddresses: [ /fd00:db80::1,/192.168.1.1 ] Domains: lan MTU: 0 ServerAddress: /192.168.1.1 Routes: [ 192.168.1.0/24 -> 0.0.0.0 wlan0 mtu 0,0.0.0.0/0 -> 192.168.1.1 wlan0 mtu 0 ]}}  nc{[ Transports: WIFI Capabilities: NOT_METERED&INTERNET&NOT_RESTRICTED&TRUSTED&NOT_VPN&VALIDATED&NOT_ROAMING&FOREGROUND&NOT_CONGESTED&NOT_SUSPENDED LinkUpBandwidth>=261146Kbps LinkDnBandwidth>=5648Kbps SignalStrength: -53 OwnerUid: 1000 SSID: "Dung Pham" RequestorUid: -1 RequestorPackageName: null]}  Score{60}  everValidated{true}  lastValidated{true}  created{true} lingering{false} explicitlySelected{false} acceptUnvalidated{false} everCaptivePortalDetected{false} lastCaptivePortalDetected{false} partialConnectivity{false} acceptPartialConnectivity{false} clat{mBaseIface: null, mIface: null, mState: IDLE} }

Network Requests:
  uid/pid:1000/4569 NetworkRequest [ LISTEN id=5, [ Transports: WIFI Capabilities: NOT_RESTRICTED&TRUSTED&NOT_VPN&FOREGROUND Uid: 1000 AdministratorUids: [] RequestorUid: 1000 RequestorPackageName: android] ]
"""

# `dumpsys connectivity`, Android 14 non-Wi-Fi default. Key order inside
# NetworkAgentInfo{} differs and it prints handle{} rather than nethandle{}.
CONNECTIVITY_ETHERNET = """Active default network: 100

Current Networks:
  NetworkAgentInfo{network{100}  handle{432902426637}  ni{Ethernet CONNECTED extra: } Score(50 ; KeepConnected : 0 ; Policies : IS_UNMETERED)   lp{{InterfaceName: vlan5 LinkAddresses: [ 192.168.1.100/24 ] DnsAddresses: [ /192.168.1.102 ] Domains: null MTU: 1500 Routes: [ 192.168.1.0/24 -> 0.0.0.0 vlan5 mtu 0 ]}}  nc{[ Transports: CELLULAR Capabilities: NOT_METERED&INTERNET&NOT_RESTRICTED&TRUSTED&NOT_VPN&NOT_ROAMING&FOREGROUND&NOT_CONGESTED&NOT_SUSPENDED LinkUpBandwidth>=50000Kbps LinkDnBandwidth>=450000Kbps]}}
"""

GARBAGE = "\x00\x01 not a dumpsys output at all\n{{{ ]]] ID: SSID:\nst=notanumber rb=\n"


def _fake_shell(mapping: dict[str, str]):
    """Build a ``(cmd) -> str`` callable backed by a dict, recording calls."""
    calls: list[str] = []

    def shell(cmd: str) -> str:
        calls.append(cmd)
        return mapping.get(cmd, "")

    shell.calls = calls  # type: ignore[attr-defined]
    return shell


# ===========================================================================
# 1. Current association
# ===========================================================================


def test_current_connection_android14_fields():
    """Every mWifiInfo field is extracted from the A14 one-liner."""
    parsed = parse_wifi_dumpsys(WIFI_A14_CONNECTED)
    cur = parsed["current"]
    assert isinstance(cur, WifiConnectionState)
    assert cur.ssid == "HomeNet_5G"  # quotes stripped
    assert cur.bssid == "8a:de:4b:f0:1f:15"
    assert cur.mac_address == "0e:e6:6a:3f:06:b0"
    assert cur.rssi == -48
    assert cur.frequency_mhz == 5220
    assert cur.network_id == 0
    assert cur.supplicant_state == "COMPLETED"
    assert cur.is_connected is True
    # `Link speed:` must not be confused with `Tx/Rx/Max Supported ... Link speed:`
    assert cur.link_speed_mbps == 1200
    assert cur.captured_at.endswith("Z")


def test_current_connection_never_claims_a_join_time():
    """The live record must state that it is capture-time, not join-time."""
    cur = parse_wifi_dumpsys(WIFI_A14_CONNECTED)["current"]
    blob = " ".join(cur.caveats).lower()
    assert "not authoritative" in blob
    assert "not when the association began" in blob
    # No field on the record even *could* be read as a join epoch.
    assert "joined_at" not in cur.to_dict()
    assert "connected_at" not in cur.to_dict()


def test_randomised_mac_is_flagged_and_caveated():
    """0e:… has the locally-administered bit set -> randomised, with the pivot caveat."""
    cur = parse_wifi_dumpsys(WIFI_A14_CONNECTED)["current"]
    assert cur.randomized_mac is True
    blob = " ".join(cur.caveats)
    assert "RANDOMISED MAC" in blob
    assert "NOT the device's hardware" in blob
    assert "router" in blob.lower()


def test_factory_oui_mac_is_not_flagged_as_randomised():
    """A real OUI (50:5b:c2) on a pre-A10 device is not a randomised address."""
    cur = parse_wifi_dumpsys(WIFI_A9_LEGACY)["current"]
    assert cur.mac_address == "50:5b:c2:74:5a:9b"
    assert cur.randomized_mac is False
    assert any("OUI" in c for c in cur.caveats)
    assert cur.is_connected is False
    assert cur.supplicant_state == "DISCONNECTED"


def test_default_mac_placeholder_is_recorded_as_unavailable():
    """02:00:00:00:00:00 is a permission placeholder — never a MAC value."""
    cur = parse_wifi_dumpsys(WIFI_A14_DISCONNECTED)["current"]
    assert cur.mac_address is None
    assert cur.ssid is None  # '<unknown ssid>' is not an SSID
    assert cur.bssid is None
    assert cur.is_connected is False
    assert any(DEFAULT_MAC_PLACEHOLDER in c and "UNAVAILABLE" in c for c in cur.caveats)


# ===========================================================================
# 2. Saved networks
# ===========================================================================


def test_saved_networks_parsed_once_not_double_counted():
    """Only the fenced Configured-networks region is parsed, not ConfigurationMap."""
    saved = parse_wifi_dumpsys(WIFI_A14_CONNECTED)["saved"]
    assert [n.ssid for n in saved] == ["TP-Link_1F18_6G", "HomeNet_5G"]
    assert all(isinstance(n, WifiSavedNetwork) for n in saved)
    assert [n.network_id for n in saved] == [0, 1]
    assert [n.key_mgmt for n in saved] == ["SAE", "WPA_PSK"]


def test_saved_network_metadata_and_recency_flags():
    saved = {n.ssid: n for n in parse_wifi_dumpsys(WIFI_A14_CONNECTED)["saved"]}
    home = saved["HomeNet_5G"]
    tplink = saved["TP-Link_1F18_6G"]
    assert home.has_ever_connected is True
    assert home.num_association == 55
    assert home.is_most_recently_connected is True
    assert tplink.is_most_recently_connected is False
    assert home.randomized_mac == "7e:6b:50:e3:a0:76"
    # numRebootsSinceLastUse is an ordinal, and we must say so.
    assert any("ORDINAL over boots" in c for c in tplink.caveats)


def test_saved_networks_never_carry_a_credential():
    """The dumpsys path prints `PSK/SAE: *` — no password may ever be emitted."""
    for net in parse_wifi_dumpsys(WIFI_A14_CONNECTED)["saved"]:
        d = net.to_dict()
        assert "password" not in d
        assert "psk" not in d
        assert not any("*" == str(v) for v in d.values())


def test_yearless_lastconnected_is_not_resolved_to_a_timestamp():
    """`02-11 16:23:41.505` has no year and no offset -> last_seen stays None."""
    saved = {n.ssid: n for n in parse_wifi_dumpsys(WIFI_A14_CONNECTED)["saved"]}
    home = saved["HomeNet_5G"]
    assert home.last_seen is None
    assert any("year-less" in c and "02-11 18:02:07.113" in c for c in home.caveats)


def test_iso_lastconnected_is_normalised_but_flagged_as_local_time():
    """A15 prints ISO-8601 — still device-local, so the Z must be declared an artefact."""
    saved = parse_wifi_dumpsys(WIFI_A15_SAVED)["saved"]
    assert len(saved) == 1
    net = saved[0]
    assert net.last_seen == "2026-01-03T14:18:20Z"
    assert any("normalisation artefact" in c for c in net.caveats)
    assert any("NO UTC offset" in c for c in net.caveats)


def test_legacy_android9_saved_schema_still_parses():
    """Pre-A10 blocks have no randomised MAC / lastConnected — degrade, don't fail."""
    saved = parse_wifi_dumpsys(WIFI_A9_LEGACY)["saved"]
    assert len(saved) == 1
    net = saved[0]
    assert net.ssid == "EFSociety"
    assert net.key_mgmt == "WPA_PSK"
    assert net.has_ever_connected is True
    assert net.num_association == 13
    assert net.randomized_mac is None
    assert net.last_seen is None
    assert any("Recency ORDERING only" in c for c in net.caveats)


# ===========================================================================
# 3. Scan results
# ===========================================================================


def test_scan_results_parsed_from_wifiscanner_table():
    scans = parse_wifi_dumpsys(WIFISCANNER_DUMP)["scan_results"]
    assert len(scans) == 4
    assert all(isinstance(s, WifiScanResult) for s in scans)
    first = scans[0]
    assert first.bssid == "00:10:94:11:11:00"
    assert first.ssid == "AP1"
    assert first.frequency_mhz == 2422
    assert first.level_dbm == -32
    assert first.capabilities == "[ESS]"
    assert first.age_ms == 5166
    # An SSID containing a space must survive the %-32s column split.
    assert scans[3].ssid == "Cafe Free WiFi"


def test_scan_row_with_radio_chains_and_comma_decimal_age():
    """Dual-chain rssi form + default-locale comma decimal separator."""
    scans = {s.bssid: s for s in parse_wifi_dumpsys(WIFISCANNER_DUMP)["scan_results"]}
    row = scans["da:fa:50:8a:16:31"]
    assert row.level_dbm == -17
    assert row.ssid == "Fraise"
    assert row.age_ms == 442265  # 442,265 s -> ms
    assert row.capabilities.startswith("[WPA2-PSK-CCMP-128]")
    assert any("radio-chain" in c for c in row.caveats)


def test_scan_results_have_no_wallclock_time():
    """Age is monotonic-since-boot: seen_at must stay None and say why."""
    for scan in parse_wifi_dumpsys(WIFISCANNER_DUMP)["scan_results"]:
        assert scan.seen_at is None
        assert any("monotonic clock" in c for c in scan.caveats)
        assert any("does not prove the device associated" in c for c in scan.caveats)


# ===========================================================================
# 4. netstats
# ===========================================================================


def test_netstats_buckets_and_durations():
    buckets = parse_netstats(NETSTATS_A12)
    assert len(buckets) == 7  # 2 + 3 Dev-series rows, 2 UID-series rows
    assert all(isinstance(b, WifiUsageBucket) for b in buckets)
    durations = {b.duration_ms for b in buckets}
    assert durations == {3600 * 1000, 7200 * 1000}  # Dev = 1 h, UID = 2 h
    assert {b.duration_ms for b in buckets if b.uid == -1} == {3600 * 1000}
    assert {b.duration_ms for b in buckets if b.uid == 1000} == {7200 * 1000}

    dev = [b for b in buckets if b.uid == -1 and b.ssid == "Dung Pham"]
    assert len(dev) == 3
    first = dev[0]
    assert first.bucket_start == "2023-04-26T03:00:00Z"  # the stale outlier bucket
    assert first.bucket_end == "2023-04-26T04:00:00Z"
    assert first.rx_bytes == 4147 and first.tx_bytes == 2572
    # Interface comes from the Active interfaces map.
    assert first.iface == "wlan0"
    assert [b.iface for b in buckets if b.ssid == "ATTT-LAB"] == ["", "", "", ""]


def test_every_netstats_bucket_is_approximate_and_names_its_duration():
    """Mandatory honesty behaviour for all netstats-derived records."""
    for buckets, expected in (
        (parse_netstats(NETSTATS_A12), {"bucketDuration=3600s", "bucketDuration=7200s"}),
        (parse_netstats(NETSTATS_A13), {"bucketDuration=7200s"}),
    ):
        assert buckets
        for b in buckets:
            assert b.approximate is True
            blob = " ".join(b.caveats)
            assert "APPROXIMATE — NON-AUTHORITATIVE" in blob
            assert any(tag in blob for tag in expected)
            assert "no per-join timestamp" in blob.lower()


def test_netstats_android13_schema_strips_security_suffix_and_skips_cellular():
    """`wifiNetworkKey="Fraise"wpa2-psk` -> SSID 'Fraise'; the cellular ident is ignored."""
    buckets = parse_netstats(NETSTATS_A13)
    assert {b.ssid for b in buckets} == {"Fraise"}
    assert len(buckets) == 2  # only the Wi-Fi identity's two rows
    assert buckets[0].uid == 1005009
    assert buckets[0].bucket_start == "2025-09-16T06:00:00Z"
    assert any("Per-UID series" in c for c in buckets[0].caveats)


def test_netstats_truncation_is_reported_not_hidden():
    """'(omitting N buckets)' means --full was missing; the record must say so."""
    buckets = parse_netstats(NETSTATS_TRUNCATED)
    assert len(buckets) == 1
    assert any("omitting N buckets" in c and "--full" in c for c in buckets[0].caveats)


def test_netstats_zero_bucket_carries_the_no_disconnection_caveat():
    zero = parse_netstats(NETSTATS_ZERO_ONLY)
    assert len(zero) == 1
    assert zero[0].rx_bytes == 0 and zero[0].tx_bytes == 0
    assert any("does not prove disconnection" in c for c in zero[0].caveats)
    # …and a bucket that proves nothing must not be placed on the timeline.
    assert build_wifi_timeline({"usage": zero}) == []


# ===========================================================================
# 5. connectivity
# ===========================================================================


def test_connectivity_wifi_connected():
    conn = parse_connectivity(CONNECTIVITY_WIFI)
    assert conn["active_default_netid"] == "602"
    assert conn["wifi_connected"] is True
    assert len(conn["networks"]) == 1
    net = conn["networks"][0]
    assert net["net_id"] == "602"
    assert net["ssid"] == "Dung Pham"
    assert net["state"] == "CONNECTED"
    assert net["interface"] == "wlan0"
    assert net["validated"] is True
    assert net["ever_validated"] is True and net["last_validated"] is True
    assert "192.168.1.234/24" in net["link_addresses"]
    assert net["is_default"] is True
    assert any("DHCP-assigned IP" in c for c in conn["caveats"])


def test_connectivity_parses_by_key_not_position():
    """A14 puts network{}/handle{} before ni{} and shortens the NetworkInfo string."""
    conn = parse_connectivity(CONNECTIVITY_ETHERNET)
    assert conn["active_default_netid"] == "100"
    assert conn["wifi_connected"] is False
    net = conn["networks"][0]
    assert net["net_id"] == "100"
    assert net["type"] == "Ethernet"
    assert net["state"] == "CONNECTED"
    assert net["interface"] == "vlan5"
    assert net["is_wifi"] is False


# ===========================================================================
# 6. Timeline
# ===========================================================================


def _full_result() -> dict:
    parsed = parse_wifi_dumpsys(WIFI_A14_CONNECTED + "\n" + WIFISCANNER_DUMP)
    return {
        "current": parsed["current"],
        "saved": parsed["saved"],
        "scan_results": parsed["scan_results"],
        "usage": parse_netstats(NETSTATS_A12),
        "connectivity": parse_connectivity(CONNECTIVITY_WIFI),
        "commands": [{"command": c, "ok": True} for c in WIFI_DUMPSYS_COMMANDS],
        "caveats": parsed["caveats"],
    }


def test_timeline_shape_and_live_confidence_for_current_association():
    events = build_wifi_timeline(_full_result())
    assert events
    for ev in events:
        assert set(ev) == {"timestamp", "kind", "summary", "confidence", "ref"}
        assert ev["kind"] == "wifi"
    live = [e for e in events if e["confidence"] == Confidence.LIVE.value]
    assert len(live) == 1
    assert "ASSOCIATED at capture time" in live[0]["summary"]
    assert "HomeNet_5G" in live[0]["summary"]


def test_timeline_netstats_events_are_labelled_approximate_with_bucket_granularity():
    events = build_wifi_timeline(_full_result())
    derived = [e for e in events if e["ref"].startswith("netstats:")]
    assert derived
    for ev in derived:
        assert "approximate" in ev["summary"]
        assert "Hour-bucket granularity" in ev["summary"]
        assert "bucketDuration=" in ev["summary"]
        # An inference from a byte counter is never a live observation.
        assert ev["confidence"] != Confidence.LIVE.value
    assert len(derived) == 7


def test_timeline_is_sorted_and_never_states_a_join_time():
    events = build_wifi_timeline(_full_result())
    assert [e["timestamp"] for e in events] == sorted(e["timestamp"] for e in events)
    for ev in events:
        low = ev["summary"].lower()
        assert "joined at" not in low
        assert "connected at " not in low.replace("associated at capture time", "")


def test_timeline_accepts_json_decoded_dicts_too():
    """The orchestrator may hand back a JSON round-tripped result."""
    result = _full_result()
    serialisable = {
        "current": result["current"].to_dict(),
        "saved": [n.to_dict() for n in result["saved"]],
        "scan_results": [s.to_dict() for s in result["scan_results"]],
        "usage": [b.to_dict() for b in result["usage"]],
        "connectivity": result["connectivity"],
        "caveats": result["caveats"],
    }
    decoded = json.loads(json.dumps(serialisable))
    assert build_wifi_timeline(decoded) == build_wifi_timeline(result)


# ===========================================================================
# 7. Collector
# ===========================================================================


def test_collect_wifi_live_runs_only_readonly_commands():
    shell = _fake_shell(
        {
            "dumpsys wifi": WIFI_A14_CONNECTED,
            "dumpsys wifiscanner": WIFISCANNER_DUMP,
            "dumpsys connectivity": CONNECTIVITY_WIFI,
            "dumpsys netstats --full --uid": NETSTATS_A12,
            "cat /sys/class/net/wlan0/address": "0e:e6:6a:3f:06:b0\n",
            "getprop ro.build.version.sdk": "34\n",
            "getprop persist.sys.timezone": "Asia/Kolkata\n",
        }
    )
    result = collect_wifi_live(shell)

    assert shell.calls == WIFI_DUMPSYS_COMMANDS
    # Nothing state-changing: no --poll, no `cmd wifi`, no svc/settings writes.
    joined = " ".join(shell.calls)
    for forbidden in ("--poll", "cmd wifi", "svc wifi", "settings put", "pm grant"):
        assert forbidden not in joined

    assert result["current"].ssid == "HomeNet_5G"
    assert len(result["saved"]) == 2
    assert len(result["scan_results"]) == 4
    assert len(result["usage"]) == 7
    assert result["connectivity"]["wifi_connected"] is True
    assert result["device"]["sdk"] == "34"
    assert result["device"]["timezone"] == "Asia/Kolkata"
    assert result["device"]["wlan0_mac_is_randomized"] is True
    assert any("--poll" in c and "NOT executed" in c for c in result["caveats"])


def test_collect_wifi_live_never_raises_on_a_broken_shell():
    def exploding_shell(cmd: str) -> str:
        raise RuntimeError(f"adb: device offline ({cmd})")

    result = collect_wifi_live(exploding_shell)
    assert result["current"] is None
    assert result["saved"] == []
    assert result["scan_results"] == []
    assert result["usage"] == []
    assert all(c["ok"] is False for c in result["commands"])
    assert any("device offline" in c for c in result["caveats"])
    # Never fabricate a success.
    assert not any(c["ok"] for c in result["commands"])


def test_collect_wifi_live_marks_missing_sources_inaccessible_not_absent():
    shell = _fake_shell({"dumpsys wifi": WIFI_A14_CONNECTED})
    result = collect_wifi_live(shell)
    assert result["current"] is not None
    assert result["usage"] == []
    assert any(
        "INACCESSIBLE" in c and "not the same as absent" in c for c in result["caveats"]
    )


# ===========================================================================
# 8. Empty / garbage input, summary, serialisation
# ===========================================================================


@pytest.mark.parametrize("text", ["", "   \n\n ", GARBAGE])
def test_empty_and_garbage_input_returns_empty_collections_with_caveats(text):
    parsed = parse_wifi_dumpsys(text)
    assert parsed["current"] is None
    assert parsed["saved"] == []
    assert parsed["scan_results"] == []
    assert parsed["caveats"]
    assert parse_netstats(text) == []
    conn = parse_connectivity(text)
    assert conn["networks"] == []
    assert conn["caveats"]
    assert build_wifi_timeline({"current": None, "saved": [], "usage": []}) == []


def test_garbage_input_is_not_mistaken_for_evidence():
    parsed = parse_wifi_dumpsys(GARBAGE)
    blob = " ".join(parsed["caveats"])
    assert "INACCESSIBLE" in blob
    assert "no Wi-Fi data" in blob


def test_wifi_live_summary_counts_and_caveat_paragraph():
    result = _full_result()
    summary = wifi_live_summary(result)
    assert summary["tier"] == "tier0"
    assert summary["connected_at_capture"] is True
    assert summary["current_ssid"] == "HomeNet_5G"
    assert summary["current_mac_randomized"] is True
    assert summary["hardware_mac_available"] is False
    assert summary["saved_count"] == 2
    assert summary["saved_with_randomized_mac"] == 2
    assert summary["scan_result_count"] == 4
    assert summary["usage_bucket_count"] == 7
    assert summary["usage_all_approximate"] is True
    assert summary["bucket_durations_s"] == [3600, 7200]
    assert summary["ssids_with_traffic"] == ["ATTT-LAB", "Dung Pham"]
    # Recency is an ORDERING, never a time: mostRecentlyConnected first.
    assert summary["saved_recency_ordering"][0] == "HomeNet_5G"

    para = summary["caveat_paragraph"]
    for phrase in (
        "Tier 0",
        "NOT AUTHORITATIVE",
        "APPROXIMATE",
        "Recency ORDERING only",
        "RANDOMISED MAC",
        "monotonic clock",
    ):
        assert phrase in para


def test_full_result_json_round_trips():
    result = _full_result()
    payload = {
        "current": result["current"].to_dict(),
        "saved": [n.to_dict() for n in result["saved"]],
        "scan_results": [s.to_dict() for s in result["scan_results"]],
        "usage": [b.to_dict() for b in result["usage"]],
        "connectivity": result["connectivity"],
        "timeline": build_wifi_timeline(result),
        "summary": wifi_live_summary(result),
    }
    text = json.dumps(payload)
    back = json.loads(text)
    assert back["current"]["ssid"] == "HomeNet_5G"
    assert back["usage"][0]["approximate"] is True
    assert back["timeline"][0]["kind"] == "wifi"
    assert isinstance(back["summary"]["caveats"], list)
