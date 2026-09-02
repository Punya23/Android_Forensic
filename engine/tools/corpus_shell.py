"""Canned ``dumpsys`` / ``getprop`` output for the synthetic mock corpus.

The mock acquisition source (:class:`triage.acquire.mock.MockDeviceSource`) answers
``shell_readonly(cmd)`` by looking for ``_shell/<cmd with spaces→_ and /→_>.txt``.
Until this module existed the corpus shipped exactly one such file
(``dumpsys_location.txt``), so every shell-derived stage — notifications, Bluetooth,
cell towers, live Wi-Fi, screen/app usage, signed-in accounts — parsed an empty string
and wrote an empty dataset. The pipeline was fine; the demo just had nothing to read,
and roughly twenty dashboard views rendered blank for a reason that had nothing to do
with the device.

Every block below is shaped the way the framework actually prints it — the same shapes
the parser unit tests assert against — so the corpus exercises the real parsers rather
than a convenient simplification. Content follows the corpus narrative (Samsung S21 on
Airtel, Mumbai dockyard, contacts Imran K / Rahul Verma / Warehouse 9).

Nothing here is evidence. `_device.json` already stamps the corpus as synthetic and the
mock source labels every audit entry ``mock``.
"""

from __future__ import annotations

# Narrative epoch, in ms. Matches the base used by the call log and notification
# fixtures elsewhere in the corpus so the timeline lines up across datasets.
BASE_MS = 1751826000000


def _ms(offset_seconds: int) -> int:
    return BASE_MS + offset_seconds * 1000


# ---------------------------------------------------------------------------
# dumpsys notification --history
# ---------------------------------------------------------------------------
def notification_history() -> str:
    """Android 11+ numbered notification-history format.

    Notification history is the highest-value Tier-0 artifact on a non-rooted phone:
    it carries message previews from apps whose databases are unreadable without root.
    Two of the entries below name a contact whose chat rows were deleted, which is the
    point — the notification ring buffer outlived the message.
    """
    rows = [
        ("com.whatsapp", _ms(0), "Imran K", "consignment lands 2140, pier 4", "high"),
        ("com.whatsapp", _ms(180), "Imran K", "come alone", "high"),
        (
            "org.telegram.messenger",
            _ms(420),
            "docks-crew",
            "Rahul: warehouse 9 is clear",
            "default",
        ),
        (
            "com.android.dialer",
            _ms(900),
            "Missed call",
            "+91 90000 09090",
            "max",
        ),
        (
            "com.snapchat.android",
            _ms(1500),
            "imran_k99",
            "Sent you a snap",
            "default",
        ),
        (
            "com.instagram.android",
            _ms(2100),
            "rahul.v",
            "Rahul sent you a message",
            "default",
        ),
        (
            "com.google.android.apps.messaging",
            _ms(2700),
            "AX-HDFCBK",
            "UPI Rs.48,000 debited a/c XX4471",
            "default",
        ),
        (
            "io.metamask",
            _ms(3300),
            "MetaMask",
            "Transaction confirmed",
            "low",
        ),
        (
            "com.calculator.vault.hider",
            _ms(3900),
            "Calculator Vault",
            "Backup complete",
            "min",
        ),
        (
            "com.whatsapp",
            _ms(5400),
            "Auntie",
            "beta khana kha liya?",
            "default",
        ),
    ]
    out = []
    for i, (pkg, post, title, text, prio) in enumerate(rows):
        out.append(
            f"  {i}: pkg={pkg} postTime={post} key=0|{pkg}|{i + 1}|null|1014{i}\n"
            f"     Title: {title}\n"
            f"     Text: {text}\n"
            f"     Priority: {prio}\n"
        )
    return "Notification History:\n\n" + "\n".join(out)


# ---------------------------------------------------------------------------
# dumpsys bluetooth_manager
# ---------------------------------------------------------------------------
def bluetooth_manager() -> str:
    """Bonded-device list in the shape ``dumpsys bluetooth_manager`` prints it.

    ``lastSeen`` is what the framework reports; the parser converts it, and the
    Bluetooth module elsewhere is careful never to present ``last_active_time`` (a
    counter) as a wall-clock date. These records carry real epochs, so they are safe
    to render as times.
    """
    devices = [
        ("A4:83:E7:2B:19:04", "Imran iPhone", 12, "false", _ms(-300), "0x0200"),
        ("48:D6:D5:9C:1A:77", "Swift Dzire — Bluetooth", 12, "false", _ms(-3600), "0x0400"),
        ("F0:5C:77:1E:64:B2", "boAt Airdopes 141", 12, "true", _ms(60), "0x0400"),
        ("3C:2E:F5:88:0D:19", "Galaxy Watch4", 12, "false", _ms(-86400), "0x0700"),
        ("DC:A6:32:41:7F:AA", "warehouse-pi", 11, "false", _ms(-7200), "0x0100"),
    ]
    blocks = ["Bluetooth Status\n  enabled: true\n  state: ON\n\nBonded devices:"]
    for mac, name, bond, connected, last_seen, cls in devices:
        blocks.append(
            f"{mac}\n"
            f"name = {name}\n"
            f"bondState = {bond}\n"
            f"connected = {connected}\n"
            f"lastSeen = {last_seen}\n"
            f"btClass = {cls}"
        )
    return "\n\n".join(blocks) + "\n"


# ---------------------------------------------------------------------------
# dumpsys telephony.registry
# ---------------------------------------------------------------------------
def telephony_registry() -> str:
    """Serving-cell registrations across the evening, as CellIdentity stanzas.

    Five distinct (cid, lac) pairs in sequence: the handset moved. Cell-tower location
    is coarse by nature — the parser records the identifiers and lets the location
    layer decide what, if anything, they justify claiming.
    """
    towers = [
        (24175, 4102, 22, "Airtel", "LTE", 21, _ms(-5400)),
        (24188, 4102, 22, "Airtel", "LTE", 17, _ms(-1800)),
        (31904, 4117, 22, "Airtel", "LTE", 12, _ms(0)),
        (31911, 4117, 22, "Airtel", "NR", 24, _ms(1800)),
        (24175, 4102, 22, "Airtel", "LTE", 19, _ms(9000)),
    ]
    head = (
        "TelephonyRegistry:\n"
        "  Phone Id=0\n"
        "  mNumPhones=1\n"
        "  mDefaultSubId=1\n"
    )
    blocks = [head]
    for cid, lac, mnc, op, net, asu, ts in towers:
        blocks.append(
            f"CellIdentity: mcc=404 mnc={mnc} lac={lac} cid={cid}\n"
            f"    operator={op}\n"
            f"    networkType={net}\n"
            f"    asu={asu}\n"
            f"    timestamp={ts}\n"
        )
    return "\n".join(blocks)


# ---------------------------------------------------------------------------
# dumpsys power  /  batterystats  /  usagestats
# ---------------------------------------------------------------------------
def power() -> str:
    """``dumpsys power`` — wakefulness plus the sleep/wake timestamps the parser reads."""
    return (
        "POWER MANAGER (dumpsys power)\n\n"
        "Power Manager State:\n"
        "  mWakefulness=Awake\n"
        "  mWakefulnessChanging=false\n"
        "  mIsPowered=true\n"
        "  mPlugType=2\n"
        "  mBatteryLevel=76\n"
        f"  mLastWakeTime={_ms(-7200)} (elapsed)\n"
        f"  mLastSleepTime={_ms(-10800)} (elapsed)\n"
        f"  mLastUserActivityTime={_ms(120)}\n"
        "  mDisplayReady=true\n"
        "  mHoldingDisplaySuspendBlocker=true\n"
    )


def batterystats() -> str:
    """``dumpsys batterystats`` foreground-time rows, one per notable package."""
    rows = [
        ("com.whatsapp", 4_620_000),
        ("org.telegram.messenger", 2_880_000),
        ("com.snapchat.android", 1_140_000),
        ("com.instagram.android", 3_300_000),
        ("io.metamask", 420_000),
        ("com.calculator.vault.hider", 780_000),
        ("com.android.chrome", 5_100_000),
    ]
    out = [
        "Battery History (...):\n",
        "Statistics since last charge:\n",
        "  Start level=98% Current level=76%\n",
    ]
    for pkg, fg in rows:
        out.append(f"  Proc {pkg}:\n    fg time={fg} ms\n")
    return "".join(out)


def usagestats() -> str:
    """``dumpsys usagestats`` per-package rows with ``lastTimeUsed``."""
    rows = [
        ("com.whatsapp", 4_620_000, _ms(300)),
        ("org.telegram.messenger", 2_880_000, _ms(480)),
        ("com.snapchat.android", 1_140_000, _ms(1500)),
        ("com.instagram.android", 3_300_000, _ms(2100)),
        ("io.metamask", 420_000, _ms(3300)),
        ("com.calculator.vault.hider", 780_000, _ms(3900)),
        ("com.android.chrome", 5_100_000, _ms(-600)),
    ]
    out = ["In-memory daily stats\n  timeRange=\"last 24h\"\n"]
    for pkg, fg, last in rows:
        out.append(
            f'  package={pkg} totalTimeInForeground={fg} lastTimeUsed={last} '
            f"appLaunchCount=6\n"
        )
    return "".join(out)


# ---------------------------------------------------------------------------
# dumpsys account
# ---------------------------------------------------------------------------
def account() -> str:
    """``dumpsys account`` — the identities registered with AccountManager.

    Deliberately includes a non-Google account: "which identities exist on this
    handset" is a broader and more useful question than "which Gmail is signed in".
    """
    entries = [
        ("subject.device@gmail.com", "com.google", _ms(-1200)),
        ("imran.k.trader@gmail.com", "com.google", _ms(-172800)),
        ("+919820044711", "com.whatsapp", _ms(-900)),
        ("imran_k99", "com.snapchat.android", _ms(-4500)),
        ("rahul.v", "com.instagram.android", _ms(-2400)),
    ]
    out = ["Accounts: %d\n" % len(entries)]
    for name, typ, sync in entries:
        out.append(
            f"  Account {{name={name}, type={typ}}}\n"
            f"    lastSyncTime={sync}\n"
            f"    authTokenType=null\n"
        )
    return "".join(out)


# ---------------------------------------------------------------------------
# Live Wi-Fi surface (Tier 0, volatile) — dumpsys wifi / wifiscanner /
# connectivity / netstats, plus the two getprops and the MAC read.
# ---------------------------------------------------------------------------
def wifi() -> str:
    """``dumpsys wifi``: current association + the saved-network configuration map.

    The ``mWifiInfo`` line reproduces Android 14's ``WifiInfo.toString()`` field order
    exactly, including the run-together network-key append that a naive split gets
    wrong. ``hasEverConnected`` is what separates "saved because someone typed the
    password" from "actually joined this network".
    """
    saved = [
        ("Warehouse9-2G", "true", "SAE", "07-06 21:58:11", 14),
        ("JioFiber-4471", "true", "WPA_PSK", "07-06 19:12:04", 61),
        ("Airtel_dock_guest", "false", "WPA_PSK", "<none>", 0),
        ("MTNL-FreeWiFi", "true", "NONE", "07-04 13:40:55", 3),
    ]
    head = (
        "Wi-Fi is enabled\n"
        "Verbose logging is disabled\n"
        "mLinkProperties {InterfaceName: wlan0 LinkAddresses: [ 192.168.29.117/24 ] "
        "DnsAddresses: [ /192.168.29.1 ] Domains: lan MTU: 0}\n"
        'mWifiInfo SSID: "Warehouse9-2G", BSSID: dc:a6:32:41:7f:ab, '
        "MAC: 8e:44:1c:d2:03:9f, IP: /192.168.29.117, Security type: 4, "
        "Supplicant state: COMPLETED, Wi-Fi standard: 6, RSSI: -57, "
        "Link speed: 286Mbps, Tx Link speed: 286Mbps, "
        "Max Supported Tx Link speed: 573Mbps, Rx Link speed: 286Mbps, "
        "Max Supported Rx Link speed: 573Mbps, Frequency: 2437MHz, Net ID: 1, "
        "Metered hint: false, score: 60, isUsable: true, CarrierMerged: false, "
        "SubscriptionId: -1, IsPrimary: 1, Trusted: true, Restricted: false, "
        "Ephemeral: false, OEM paid: false, OEM private: false, OSU AP: false, "
        "FQDN: <none>, Provider friendly name: <none>, "
        'Requesting package name: <none>"Warehouse9-2G"SAEMLO Information: , '
        "Is TID-To-Link negotiation supported by the AP: false, "
        "AP MLD Address: <none>, AP MLO Link Id: <none>, AP MLO Affiliated links: []\n"
        "mLastBssid dc:a6:32:41:7f:ab\n"
        "mLastNetworkId 1\n"
        "mLastSignalLevel 3\n"
        "\n"
        "Dump of WifiConfigManager\n"
        "WifiConfigManager - Configured networks Begin ----\n"
    )
    body = []
    for idx, (ssid, ever, keymgmt, last_conn, num_assoc) in enumerate(saved):
        body.append(
            f'ID: {idx} SSID: "{ssid}" PROVIDER-NAME: null BSSID: null FQDN: null '
            f"HOME-PROVIDER-NETWORK: false PRIO: 0 HIDDEN: false PMF: true "
            f"CarrierId: -1 SubscriptionId: -1 SubscriptionGroup: null "
            f"Currently Connected: {'true' if idx == 1 else 'false'} "
            f"User Selected: {'true' if ever == 'true' else 'false'}\n"
            f" NetworkSelectionStatus NETWORK_SELECTION_ENABLED\n"
            f" hasEverConnected: {ever}\n"
            f" hasNeverDetectedCaptivePortal: true\n"
            f" mCandidateSecurityParams: null\n"
            f" KeyMgmt: {keymgmt} Protocols: RSN\n"
            f" numAssociation {num_assoc}\n"
            f" macRandomizationSetting: 3\n"
            f" mRandomizedMacAddress: 8e:44:1c:d2:03:9f\n"
            f" deletionPriority: 0\n"
            f"IP config:\n"
            f"IP assignment: DHCP\n"
            f"Proxy settings: NONE\n"
            f"lastConnected: {last_conn}\n"
        )
    return head + "\n".join(body) + "WifiConfigManager - Configured networks End ----\n"


def wifiscanner() -> str:
    """``dumpsys wifiscanner`` — the last scan table.

    The column layout (``BSSID  Frequency  RSSI  Age(sec)  SSID  Flags``) is what
    ``ScanResultUtil.dumpScanResults`` emits, including the locale-dependent comma
    decimal separator in the age column that a naive float parse trips over.

    A scan result proves the *access point* was in radio range at scan time. It does
    not prove the handset ever joined it, and the Wi-Fi view keeps those apart.
    """
    return """WifiScanningService:
Latest scan results:
    BSSID              Frequency      RSSI           Age(sec)     SSID                                 Flags
  dc:a6:32:41:7f:ab       2437        -57              4.021    Warehouse9-2G                     [RSN-SAE-CCMP-128][ESS]
  4c:ed:fb:11:9a:20       5180        -74              4.021    JioFiber-4471                     [WPA2-PSK-CCMP-128][RSN-PSK-CCMP-128][ESS]
  90:9a:4a:d0:5c:31       2412        -81              4.021    Airtel_dock_guest                 [WPA2-PSK-CCMP-128][ESS]
  74:83:c2:6f:11:08       2462        -88             12,340    Pier4-Ops                         [WPA2-PSK-CCMP-128][ESS]
  b8:27:eb:5d:42:c1       2412        -66              4.021    <unknown ssid>                    [ESS]

Latest native scan results:
  90:9a:4a:d0:5c:31       2412        -83              9.114    Airtel_dock_guest                 [ESS]
Latest native pno scan results:
WificondScannerImpl - Log Begin ----
WificondScannerImpl - Log End ----
"""


def connectivity() -> str:
    """``dumpsys connectivity`` — the active default network and its validation state."""
    return """NetworkProviders for:
Active default network: 602

Current Networks:
  NetworkAgentInfo{ ni{[type: WIFI[], state: CONNECTED/CONNECTED, reason: (unspecified), extra: , failover: false, available: true, roaming: false]}  network{602}  nethandle{2588976009229}  lp{{InterfaceName: wlan0 LinkAddresses: [ 192.168.29.117/24 ] DnsAddresses: [ /192.168.29.1 ] Domains: lan MTU: 0 ServerAddress: /192.168.29.1}}  nc{[ Transports: WIFI Capabilities: NOT_METERED&INTERNET&NOT_RESTRICTED&TRUSTED&NOT_VPN&VALIDATED&NOT_ROAMING&FOREGROUND&NOT_CONGESTED&NOT_SUSPENDED LinkUpBandwidth>=261146Kbps LinkDnBandwidth>=5648Kbps SignalStrength: -57 OwnerUid: 1000 SSID: "Warehouse9-2G" RequestorUid: -1 RequestorPackageName: null]}  Score{60}  everValidated{true}  lastValidated{true}  created{true} lingering{false} explicitlySelected{true} acceptUnvalidated{false} }

Network Requests:
"""


def netstats() -> str:
    """``dumpsys netstats --full --uid`` — hour-bucketed Wi-Fi byte counters.

    Shape follows the Android 12 schema: ``ident=[{... networkId="X" ...}]`` headers,
    ``bucketDuration`` in *seconds*, and ``st=`` bucket starts in epoch seconds.

    ``bucketDuration`` is carried through deliberately. The Wi-Fi module refuses to
    present a bucket as a join time: traffic inside an hour bucket means the handset
    used that network somewhere in that hour, and nothing more precise. A cellular
    identity and an all-zero bucket are both included so the parser has something it
    must correctly ignore and something it must not read as a disconnection.
    """
    base_s = BASE_MS // 1000
    hour = 3600
    return f"""Configs:
  netstats_combine_subtype_enabled=false
Active interfaces:
  iface=wlan0 ident=[{{type=WIFI, subType=0, networkId="Warehouse9-2G", metered=false, defaultNetwork=true}}]
Active UID interfaces:
  iface=wlan0 ident=[{{type=WIFI, subType=0, networkId="Warehouse9-2G", metered=false, defaultNetwork=true}}]

Dev stats:
  Pending bytes: 0
  Complete history:
  ident=[{{type=MOBILE, subType=13, subscriberId=404450..., metered=true, defaultNetwork=false}}] uid=-1 set=ALL tag=0x0
    NetworkStatsHistory: bucketDuration=3600
      st={base_s - 4 * hour} rb=8891233 rp=7412 tb=1207442 tp=3901 op=0
  ident=[{{type=WIFI, subType=0, networkId="Warehouse9-2G", metered=false, defaultNetwork=true}}] uid=-1 set=ALL tag=0x0
    NetworkStatsHistory: bucketDuration=3600
      st={base_s - 2 * hour} rb=48211904 rp=41022 tb=6120448 tp=15330 op=0
      st={base_s - hour} rb=91334656 rp=77410 tb=12220416 tp=28884 op=0
      st={base_s} rb=15220736 rp=13004 tb=2201600 tp=5512 op=0
  ident=[{{type=WIFI, subType=0, networkId="JioFiber-4471", metered=false, defaultNetwork=false}}] uid=-1 set=ALL tag=0x0
    NetworkStatsHistory: bucketDuration=3600
      st={base_s - 24 * hour} rb=310445056 rp=254331 tb=28884992 tp=61220 op=0
      st={base_s - 23 * hour} rb=0 rp=0 tb=0 tp=0 op=0
  ident=[{{type=WIFI, subType=0, networkId="MTNL-FreeWiFi", metered=false, defaultNetwork=false}}] uid=-1 set=ALL tag=0x0
    NetworkStatsHistory: bucketDuration=3600
      st={base_s - 50 * hour} rb=1044480 rp=982 tb=204800 tp=511 op=0
UID stats:
  Pending bytes: 0
  Complete history:
  ident=[{{type=WIFI, subType=0, networkId="Warehouse9-2G", metered=false, defaultNetwork=true}}] uid=10143 set=FOREGROUND tag=0x0
    NetworkStatsHistory: bucketDuration=7200
      st={base_s - 2 * hour} rb=363197 rp=427 tb=20962 tp=231 op=0
"""


def wlan0_address() -> str:
    """The interface MAC. Randomised per-SSID on modern Android — recorded as such."""
    return "8e:44:1c:d2:03:9f\n"


def sdk_version() -> str:
    return "34\n"


def timezone() -> str:
    return "Asia/Kolkata\n"


# ---------------------------------------------------------------------------
# Registry: shell command -> canned output
# ---------------------------------------------------------------------------
#: Every command the pipeline issues through ``shell_readonly``, mapped to its canned
#: reply. ``build_shell_fixtures`` turns the keys into the filenames the mock source
#: looks for, so adding a stage here is a one-line change.
SHELL_FIXTURES: dict[str, "callable"] = {
    "dumpsys notification --history": notification_history,
    "dumpsys bluetooth_manager": bluetooth_manager,
    "dumpsys telephony.registry": telephony_registry,
    "dumpsys power": power,
    "dumpsys batterystats": batterystats,
    "dumpsys usagestats": usagestats,
    "dumpsys account": account,
    "dumpsys wifi": wifi,
    "dumpsys wifiscanner": wifiscanner,
    "dumpsys connectivity": connectivity,
    "dumpsys netstats --full --uid": netstats,
    "cat /sys/class/net/wlan0/address": wlan0_address,
    "getprop ro.build.version.sdk": sdk_version,
    "getprop persist.sys.timezone": timezone,
}


def fixture_filename(cmd: str) -> str:
    """Mirror :meth:`MockDeviceSource.shell_readonly`'s command→filename mapping."""
    return cmd.strip().replace(" ", "_").replace("/", "_") + ".txt"


def build_shell_fixtures(shell_dir) -> list[str]:
    """Write every canned shell reply into ``shell_dir``. Returns the filenames."""
    written = []
    for cmd, producer in SHELL_FIXTURES.items():
        name = fixture_filename(cmd)
        (shell_dir / name).write_text(producer(), encoding="utf-8")
        written.append(name)
    return written
