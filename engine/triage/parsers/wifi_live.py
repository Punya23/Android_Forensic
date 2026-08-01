"""Non-root (Tier 0) live Wi-Fi acquisition via ``dumpsys``.

Forensic purpose
----------------
``triage.parsers.wifi`` recovers *saved credentials* from
``wpa_supplicant.conf`` / ``WifiConfigStore.xml`` — both of which live under
``/data/misc`` and require **root**.  This module is the complementary
**non-root** surface: everything obtainable from a plain ``adb shell dumpsys``
on a stock, non-rooted device.

What that surface actually gives us:

* the **live association** at capture time (SSID / BSSID / in-use MAC / RSSI /
  frequency / supplicant state)  — ``dumpsys wifi``
* the **saved-network list** as *metadata only*, never the PSK — ``dumpsys wifi``
* the **in-memory scan cache** (APs seen recently) — ``dumpsys wifiscanner``
* the **connectivity view** (default network, validation, DHCP address)
  — ``dumpsys connectivity``
* an **hour-bucketed byte-counter history per SSID** — ``dumpsys netstats``

Limitations (these are the whole point of this module — read them)
------------------------------------------------------------------
1. **There is no per-join epoch timestamp anywhere in the non-root dumpsys
   surface.**  ``WifiConfiguration.lastConnected`` is in-memory only (AOSP
   resets it to 0 on reboot), year-less on Android 9–14, and carries no UTC
   offset.  ``lastUpdated`` is explicitly *not* persisted "for privacy
   reasons".  Therefore this module NEVER emits a "device joined SSID X at
   HH:MM" claim.  It emits "traffic attributable to SSID X was recorded in the
   hour beginning HH:00", and says so in the record's own ``caveats`` list.
2. **netstats is a byte counter, not an association log.**  Resolution is the
   bucket duration printed by the dump (3600 s for the Dev/Xt interface
   aggregate, 7200 s per-UID).  A non-zero bucket proves bytes moved; it does
   not prove continuous association, and a *missing* bucket does not prove
   disconnection (an idle-but-associated link produces no bucket at all).
   Every :class:`WifiUsageBucket` is therefore ``approximate=True``.
3. **The hardware/factory MAC is not obtainable without root on Android 10+.**
   MAC randomisation is on by default, so the MAC on the live association line
   is the *per-SSID randomised* address.  That address is still the single most
   valuable pivot in the whole capture — it is exactly what the AP wrote into
   its DHCP lease table — but it is not the device's hardware identifier and is
   never reported as one.  The literal ``02:00:00:00:00:00`` is
   ``WifiInfo.DEFAULT_MAC_ADDRESS``, a permission placeholder: it is recorded
   as *unavailable*, never as a MAC address.
4. **Scan-result ages are monotonic since boot**, not wall clock, so
   :class:`WifiScanResult.seen_at` is ``None`` unless a wall-clock anchor is
   independently established.
5. Only ``adb shell dumpsys`` is used — never ``cmd wifi``, whose output goes
   through the redacting public API path and returns ``<unknown ssid>`` /
   ``02:00:00:00:00:00`` whenever the device Location toggle is off.

Every parser degrades gracefully: a malformed record is skipped, never raised.
"""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Optional

from ..config import Confidence
from ..models import TimelineEvent, now_iso

# ---------------------------------------------------------------------------
# Command surface
# ---------------------------------------------------------------------------

# Read-only commands, in the order the pipeline should capture them: most
# volatile first.  NOTE the deliberate omission of ``dumpsys netstats --poll``:
# it flushes pending in-memory buckets to the on-disk netstats database and is
# therefore a device-STATE CHANGE, not a Tier-0 read.  We accept the resulting
# under-report of the most recent bucket and say so in the caveats rather than
# quietly writing to the evidence device.
WIFI_DUMPSYS_COMMANDS: list[str] = [
    "dumpsys wifi",
    "dumpsys wifiscanner",
    "dumpsys connectivity",
    "dumpsys netstats --full --uid",
    "cat /sys/class/net/wlan0/address",
    "getprop ro.build.version.sdk",
    "getprop persist.sys.timezone",
]

# ``WifiInfo.DEFAULT_MAC_ADDRESS`` — a permission placeholder returned to
# callers lacking LOCAL_MAC_ADDRESS.  Never record this as a MAC address.
DEFAULT_MAC_PLACEHOLDER = "02:00:00:00:00:00"

# Values the framework prints for "no value".
_NULLISH = {"<none>", "null", "none", "<unknown ssid>", "", "<no ssid>"}

# ---------------------------------------------------------------------------
# Standing caveat text (verbatim, examiner-facing)
# ---------------------------------------------------------------------------

CAVEAT_NO_JOIN_TIME = (
    "Join time is NOT AUTHORITATIVE. Android exposes no per-connection epoch "
    "timestamp to a non-root examiner: WifiConfiguration.lastConnected is "
    "in-memory only and is cleared on reboot, lastUpdated is deliberately not "
    "persisted, and numAssociation is a count with no times attached."
)

CAVEAT_RANDOMISED_MAC = (
    "This is the per-SSID RANDOMISED MAC address (locally-administered bit set) "
    "that the access point and its DHCP lease table observed — it is NOT the "
    "device's hardware/factory MAC. Under persistent randomisation it is stable "
    "for this SSID and is directly comparable against seized router/ISP logs. "
    "The factory MAC is not obtainable without root on Android 10+."
)

CAVEAT_MAC_PLACEHOLDER = (
    f"MAC reported as {DEFAULT_MAC_PLACEHOLDER} (WifiInfo.DEFAULT_MAC_ADDRESS). "
    "This is a permission placeholder, not an address: recorded as UNAVAILABLE."
)

CAVEAT_SAVED_ORDERING = (
    "Recency ORDERING only. Android does not persist a connection timestamp for "
    "saved Wi-Fi networks; lastConnected is in-memory and is cleared on reboot. "
    "Ordering derived from mostRecentlyConnected / numRebootsSinceLastUse."
)

CAVEAT_SCAN_MONOTONIC = (
    "Scan-result age is measured from the device's monotonic clock (elapsed "
    "time since boot), not wall clock. No absolute observation time can be "
    "derived without the capture epoch AND the boot offset; seen_at is null."
)

CAVEAT_NETSTATS_TEMPLATE = (
    "APPROXIMATE — NON-AUTHORITATIVE. Derived from dumpsys netstats "
    "hour-bucketed byte counters (bucketDuration={sec}s). Indicates that "
    "network traffic was carried over SSID '{ssid}' at some point within "
    "{start}–{end}. It does not establish the time of association, the "
    "duration of the connection, or the device's location. Android provides no "
    "per-join timestamp accessible without root."
)

CAVEAT_NETSTATS_ZERO = (
    "A bucket with zero bytes does not prove disconnection: an idle but "
    "associated link produces no bucket at all."
)

CAVEAT_NO_POLL = (
    "`dumpsys netstats --poll` was deliberately NOT executed because it flushes "
    "pending counters to the device's on-disk netstats database (a device-state "
    "change). The most recent bucket may therefore under-report."
)

CAVEAT_TIER0 = (
    "Tier 0 acquisition: read-only dumpsys queries over adb shell. No device "
    "state was modified, no helper APK installed, no root used."
)

CAVEAT_OEM = (
    "Stock AOSP does not redact SSIDs, BSSIDs or MACs on the dumpsys path, but "
    "OEM builds ship modified Wi-Fi services and may. Absence of a field is not "
    "evidence the network was absent — it may be inaccessible on this build."
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso_from_epoch(epoch_s: int) -> str:
    """Epoch seconds -> ISO-8601 UTC with a trailing Z."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch_s))


def _unquote(value: str) -> str:
    v = (value or "").strip()
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        return v[1:-1]
    return v


def _nullish(value: Optional[str]) -> bool:
    return value is None or value.strip().lower() in _NULLISH


def _int_or_none(value: Optional[str]) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _is_locally_administered(mac: Optional[str]) -> bool:
    """True if the MAC has the locally-administered bit set (i.e. randomised).

    Randomised Android MACs always have bit 1 of the first octet set, so the
    second hex nibble is 2, 6, A or E.  A first octet carrying a real OUI means
    randomisation was off for that network (or the device predates it).
    """
    if not mac:
        return False
    try:
        first = int(mac.split(":")[0], 16)
    except (ValueError, IndexError):
        return False
    return bool(first & 0x02)


def _as_dict(obj: Any) -> dict[str, Any]:
    """Accept either one of our dataclasses or an already-JSON-decoded dict."""
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    to_dict = getattr(obj, "to_dict", None)
    if callable(to_dict):
        try:
            return to_dict()
        except Exception:  # pragma: no cover - defensive
            return {}
    return {}


# ---------------------------------------------------------------------------
# Dataclasses (defined locally — this module owns its own record types)
# ---------------------------------------------------------------------------


@dataclass
class WifiConnectionState:
    """The live association reported by ``mWifiInfo`` in ``dumpsys wifi``.

    Wholly volatile: every field here is destroyed by a reboot or by turning
    the radio off.  ``captured_at`` is the examiner-side capture time, which is
    the ONLY trustworthy timestamp on this record — it says "this was true when
    we looked", not "the device joined then".
    """

    ssid: Optional[str] = None
    bssid: Optional[str] = None
    mac_address: Optional[str] = None
    rssi: Optional[int] = None
    link_speed_mbps: Optional[int] = None
    frequency_mhz: Optional[int] = None
    supplicant_state: str = ""
    network_id: Optional[int] = None
    is_connected: bool = False
    randomized_mac: bool = False
    captured_at: str = field(default_factory=now_iso)
    caveats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dict(asdict(self))


@dataclass
class WifiSavedNetwork:
    """One entry from the ``dumpsys wifi`` configured-network list.

    METADATA ONLY. The dumpsys path prints ``PSK/SAE: *`` — the credential is
    masked by the framework and is *not* available without root. This class has
    no password field by design; use ``triage.parsers.wifi`` on a root-tier
    ``WifiConfigStore.xml`` if credentials are in scope.
    """

    ssid: str = ""
    network_id: Optional[int] = None
    key_mgmt: str = ""
    has_ever_connected: Optional[bool] = None
    num_association: Optional[int] = None
    randomized_mac: Optional[str] = None
    is_most_recently_connected: Optional[bool] = None
    last_seen: Optional[str] = None
    source: str = "dumpsys wifi: WifiConfigManager configured networks"
    caveats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dict(asdict(self))


@dataclass
class WifiScanResult:
    """One row of the in-memory scan cache from ``dumpsys wifiscanner``.

    Proximity evidence: the device's radio heard this AP.  It does NOT prove
    the device associated with it, and the age is monotonic-since-boot.
    """

    ssid: str = ""
    bssid: str = ""
    frequency_mhz: Optional[int] = None
    level_dbm: Optional[int] = None
    capabilities: str = ""
    seen_at: Optional[str] = None
    age_ms: Optional[int] = None
    caveats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dict(asdict(self))


@dataclass
class WifiUsageBucket:
    """One ``st=/rb=/tb=`` row of a ``dumpsys netstats`` history series.

    ``approximate`` is hard-defaulted to True and is never set False anywhere in
    this module: the bucket boundaries are real, but the *inference* that the
    device was associated with the SSID during them is not.
    """

    ssid: str = ""
    iface: str = ""
    uid: Optional[int] = None
    bucket_start: str = ""
    bucket_end: str = ""
    duration_ms: int = 0
    rx_bytes: int = 0
    tx_bytes: int = 0
    approximate: bool = True
    caveats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dict(asdict(self))


# ---------------------------------------------------------------------------
# `dumpsys wifi` — live association (mWifiInfo)
# ---------------------------------------------------------------------------

_RE_SSID = re.compile(r"SSID:\s*(?P<ssid>.*?),\s*BSSID:")
_RE_BSSID = re.compile(r"BSSID:\s*(?P<bssid>(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}|<none>)")
_RE_MAC = re.compile(r",\s*MAC:\s*(?P<mac>(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}|<none>)")
_RE_SUPP = re.compile(r"Supplicant state:\s*(?P<supp>[A-Z_]+)")
_RE_RSSI = re.compile(r"RSSI:\s*(?P<rssi>-?\d+)")
# The leading comma keeps this off ", Tx Link speed:" / ", Max Supported Rx Link speed:".
_RE_LINKSPEED = re.compile(r"(?:^|,)\s*Link speed:\s*(?P<link>-?\d+)Mbps")
_RE_FREQ = re.compile(r"Frequency:\s*(?P<freq>-?\d+)MHz")
_RE_NETID = re.compile(r"Net ID:\s*(?P<netid>-?\d+)")


def _parse_current_connection(text: str) -> tuple[Optional[WifiConnectionState], list[str]]:
    """Parse the single ``mWifiInfo ...`` line. Returns (state|None, caveats)."""
    line = ""
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("mWifiInfo "):
            line = stripped
            break
    if not line:
        return None, [
            "No `mWifiInfo` line found in the supplied dumpsys output. The live "
            "association state is ABSENT FROM THIS CAPTURE — this is not evidence "
            "that the device was not connected."
        ]

    caveats: list[str] = []

    m = _RE_SSID.search(line)
    ssid_raw = m.group("ssid").strip() if m else ""
    ssid = _unquote(ssid_raw)
    if _nullish(ssid):
        ssid = None
        caveats.append(
            "SSID reported as '<unknown ssid>' — the device was not associated "
            "at capture time, or the value was withheld by the framework."
        )

    m = _RE_BSSID.search(line)
    bssid = m.group("bssid") if m else None
    if _nullish(bssid):
        bssid = None

    m = _RE_MAC.search(line)
    mac = m.group("mac") if m else None
    if _nullish(mac):
        mac = None
    randomized = False
    if mac and mac.lower() == DEFAULT_MAC_PLACEHOLDER:
        # Placeholder, NOT an address. Never record it as one.
        mac = None
        caveats.append(CAVEAT_MAC_PLACEHOLDER)
    elif mac:
        randomized = _is_locally_administered(mac)
        if randomized:
            caveats.append(CAVEAT_RANDOMISED_MAC)
        else:
            caveats.append(
                "MAC carries a real OUI (globally-administered): MAC randomisation "
                "was disabled for this association, or the device predates Android 10. "
                "This may be the factory hardware address."
            )

    m = _RE_SUPP.search(line)
    supplicant_state = m.group("supp") if m else ""

    m = _RE_RSSI.search(line)
    rssi = _int_or_none(m.group("rssi")) if m else None

    m = _RE_LINKSPEED.search(line)
    link = _int_or_none(m.group("link")) if m else None

    m = _RE_FREQ.search(line)
    freq = _int_or_none(m.group("freq")) if m else None

    m = _RE_NETID.search(line)
    netid = _int_or_none(m.group("netid")) if m else None

    # COMPLETED == associated and the 4-way handshake finished.
    is_connected = supplicant_state == "COMPLETED" and bool(ssid)

    caveats.append(
        "Live association state observed at capture time only. It records what "
        "was true when dumpsys ran, not when the association began. " + CAVEAT_NO_JOIN_TIME
    )

    state = WifiConnectionState(
        ssid=ssid,
        bssid=bssid,
        mac_address=mac,
        rssi=rssi,
        link_speed_mbps=link,
        frequency_mhz=freq,
        supplicant_state=supplicant_state,
        network_id=netid,
        is_connected=is_connected,
        randomized_mac=randomized,
        captured_at=now_iso(),
        caveats=caveats,
    )
    return state, []


# ---------------------------------------------------------------------------
# `dumpsys wifi` — saved / configured networks
# ---------------------------------------------------------------------------

_CFG_BEGIN = "WifiConfigManager - Configured networks Begin ----"
_CFG_END = "WifiConfigManager - Configured networks End ----"

_CFG_HDR = re.compile(
    r"^(?:\*\s|-\sDSBLE\s)?ID:\s*(?P<id>\d+)\s+SSID:\s*(?P<ssid>.+?)\s+"
    r"PROVIDER-NAME:\s*(?P<provider>\S+)\s+BSSID:\s*(?P<bssid>\S+)\s+"
    r"FQDN:\s*(?P<fqdn>\S+)",
    re.M,
)
_RE_KEYMGMT_LINE = re.compile(r"^\s*KeyMgmt:\s*(?P<km>[^\n]*?)\s+Protocols:", re.M)
_RE_KEYMGMT_ANY = re.compile(r"^\s*KeyMgmt:\s*(?P<km>[^\n]+)$", re.M)
_RE_EVER = re.compile(r"^\s*hasEverConnected:\s*(?P<b>true|false)", re.M)
_RE_NUMASSN = re.compile(r"^\s*numAssociation\s+(?P<n>\d+)", re.M)
_RE_RANDMAC = re.compile(
    r"^\s*mRandomizedMacAddress:\s*(?P<mac>(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2})", re.M
)
_RE_MRC = re.compile(r"mostRecentlyConnected=(?P<b>true|false)")
_RE_LASTCON = re.compile(
    r"^\s*lastConnected:\s*(?P<ts>\d{4}-\d{2}-\d{2}T[\d:.]+|\d{1,2}-\d{1,2}\s[\d:.]+)", re.M
)
_RE_REBOOTS = re.compile(r"^\s*numRebootsSinceLastUse:\s*(?P<n>\d+)", re.M)


def _parse_saved_networks(text: str) -> tuple[list[WifiSavedNetwork], list[str]]:
    """Parse the fenced configured-network list.

    Only the region between the Begin/End fences is considered: every network is
    printed a SECOND time inside ``ConfigurationMap`` (``mPerId={0=ID: 0 SSID:``)
    and parsing the whole dump would double-count.
    """
    start = text.find(_CFG_BEGIN)
    if start < 0:
        return [], [
            "No 'WifiConfigManager - Configured networks Begin ----' fence in the "
            "supplied output: the saved-network list is ABSENT FROM THIS CAPTURE. "
            "That is not evidence the device had no saved networks."
        ]
    start += len(_CFG_BEGIN)
    end = text.find(_CFG_END, start)
    region = text[start:end] if end > start else text[start:]

    headers = list(_CFG_HDR.finditer(region))
    if not headers:
        return [], [
            "Configured-networks fence present but no parseable 'ID: n SSID: ...' "
            "header inside it (empty list, or an OEM-modified dump format)."
        ]

    out: list[WifiSavedNetwork] = []
    for idx, hdr in enumerate(headers):
        try:
            block_end = headers[idx + 1].start() if idx + 1 < len(headers) else len(region)
            block = region[hdr.start() : block_end]

            ssid = _unquote(hdr.group("ssid"))
            if not ssid or ssid.lower() in _NULLISH:
                # A network with no readable SSID is not usable evidence; skip it
                # rather than emitting a blank record.
                continue

            caveats: list[str] = [CAVEAT_SAVED_ORDERING, CAVEAT_NO_JOIN_TIME]

            m = _RE_KEYMGMT_LINE.search(block) or _RE_KEYMGMT_ANY.search(block)
            key_mgmt = m.group("km").strip() if m else ""

            m = _RE_EVER.search(block)
            ever = (m.group("b") == "true") if m else None

            m = _RE_NUMASSN.search(block)
            num_assn = _int_or_none(m.group("n")) if m else None
            if num_assn is not None:
                caveats.append(
                    f"numAssociation={num_assn} is an in-memory counter with no XML "
                    "backing: it is reset to 0 on reboot and carries no timestamps, "
                    "so it cannot be mapped to specific occasions."
                )

            m = _RE_RANDMAC.search(block)
            rand_mac = m.group("mac") if m else None
            if rand_mac:
                if _is_locally_administered(rand_mac):
                    caveats.append(CAVEAT_RANDOMISED_MAC)
                else:
                    caveats.append(
                        "mRandomizedMacAddress does not have the locally-administered "
                        "bit set — unexpected; treat with suspicion and verify."
                    )

            m = _RE_MRC.search(block)
            mrc = (m.group("b") == "true") if m else None

            m = _RE_REBOOTS.search(block)
            reboots = _int_or_none(m.group("n")) if m else None
            if reboots is not None:
                caveats.append(
                    f"numRebootsSinceLastUse={reboots} is an ORDINAL over boots, not a "
                    "duration: it cannot be converted to elapsed time."
                )

            last_seen: Optional[str] = None
            m = _RE_LASTCON.search(block)
            if m:
                raw_ts = m.group("ts")
                if re.match(r"^\d{4}-\d{2}-\d{2}T", raw_ts):
                    # Android 15+ prints LocalDateTime.toString(): ISO-8601 but still
                    # DEVICE-LOCAL with no offset. We normalise the shape for sorting
                    # and say loudly that the Z is an artefact, not evidence of UTC.
                    last_seen = raw_ts.split(".")[0][:19] + "Z"
                    caveats.append(
                        f"last_seen derived from in-memory lastConnected='{raw_ts}'. The "
                        "device printed DEVICE-LOCAL time with NO UTC offset; the trailing "
                        "'Z' is a normalisation artefact for sorting, NOT an assertion that "
                        "the value is UTC. The value is cleared on reboot and reflects only "
                        "the most recent connection — there is no history."
                    )
                else:
                    caveats.append(
                        f"lastConnected='{raw_ts}' is year-less and offset-less "
                        "(Android <=14 logTimeOfDay format). It cannot be resolved to an "
                        "absolute time from the dump alone, so last_seen is null; the raw "
                        "value is preserved here verbatim."
                    )

            out.append(
                WifiSavedNetwork(
                    ssid=ssid,
                    network_id=_int_or_none(hdr.group("id")),
                    key_mgmt=key_mgmt,
                    has_ever_connected=ever,
                    num_association=num_assn,
                    randomized_mac=rand_mac,
                    is_most_recently_connected=mrc,
                    last_seen=last_seen,
                    caveats=caveats,
                )
            )
        except Exception:
            # Never let one malformed block abort the whole list.
            continue

    return out, []


# ---------------------------------------------------------------------------
# `dumpsys wifiscanner` — scan cache
# ---------------------------------------------------------------------------

_SCAN_SECTIONS = (
    "Latest scan results:",
    "Latest native scan results:",
    "Latest native pno scan results:",
)

_SCAN_ROW = re.compile(
    r"^\s{1,6}(?P<bssid>[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})\s+"
    r"(?P<freq>\d+)\s+"
    r"(?P<rssi>-?\d+)(?:\((?P<chains>[^)]*)\))?\s+"
    # `%3.3f` is formatted with the DEFAULT LOCALE: on fr/de/vi devices the age
    # prints with a comma decimal separator. Accept both.
    r"(?P<age>\d+[.,]\d{3}|___\?___|>1000\.0)\s+"
    r"(?P<rest>.*?)\s*$"
)


def _parse_age_ms(raw: str) -> tuple[Optional[int], list[str]]:
    if raw == "___?___":
        return None, [
            "Scan-result age printed as '___?___' (ScanResult.timestamp was <= 0): "
            "the framework had no valid monotonic timestamp for this row."
        ]
    if raw == ">1000.0":
        return None, [
            "Scan-result age printed as '>1000.0': the framework only records that the "
            "result is older than 1000 s. No upper bound is available."
        ]
    try:
        return int(round(float(raw.replace(",", ".")) * 1000.0)), []
    except ValueError:
        return None, ["Scan-result age was unparseable; recorded as null."]


def _parse_scan_results(text: str) -> tuple[list[WifiScanResult], list[str]]:
    """Parse the ``ScanResultUtil.dumpScanResults`` tables."""
    out: list[WifiScanResult] = []
    section = ""
    seen_any_section = False

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if stripped in _SCAN_SECTIONS:
            section = stripped.rstrip(":")
            seen_any_section = True
            continue
        if not section:
            continue
        # Leaving the table: a non-indented, non-row line ends the section.
        if stripped and not raw_line.startswith(" "):
            section = ""
            continue
        if "BSSID" in stripped and "Frequency" in stripped:
            continue  # column header

        m = _SCAN_ROW.match(raw_line)
        if not m:
            continue
        try:
            rest = m.group("rest")
            # SSID is %-32s padded, so >=2 spaces separates it from the flags.
            parts = re.split(r"\s{2,}", rest.strip(), maxsplit=1)
            ssid = parts[0].strip() if parts else ""
            caps = parts[1].strip() if len(parts) > 1 else ""
            if not caps and "[" in ssid:
                idx = ssid.index("[")
                ssid, caps = ssid[:idx].strip(), ssid[idx:].strip()

            age_ms, age_caveats = _parse_age_ms(m.group("age"))

            caveats = [
                f"Observed in the '{section}' section of `dumpsys wifiscanner` "
                "(in-memory scan cache — destroyed on reboot).",
                CAVEAT_SCAN_MONOTONIC,
                "A scan result proves the device's radio HEARD this access point. It "
                "does not prove the device associated with it, nor that the operator "
                "was aware of it.",
            ] + age_caveats

            chains = m.group("chains")
            if chains:
                caveats.append(f"Per-radio-chain levels reported: {chains}.")

            out.append(
                WifiScanResult(
                    ssid=_unquote(ssid),
                    bssid=m.group("bssid"),
                    frequency_mhz=_int_or_none(m.group("freq")),
                    level_dbm=_int_or_none(m.group("rssi")),
                    capabilities=caps,
                    seen_at=None,  # monotonic only — see CAVEAT_SCAN_MONOTONIC
                    age_ms=age_ms,
                    caveats=caveats,
                )
            )
        except Exception:
            continue

    caveats: list[str] = []
    if not seen_any_section:
        caveats.append(
            "No 'Latest scan results:' table found. Note that the scan cache is "
            "printed by `dumpsys wifiscanner`, NOT `dumpsys wifi` — if only the "
            "latter was captured, the scan cache is missing, not empty."
        )
    return out, caveats


# ---------------------------------------------------------------------------
# Public: dumpsys wifi / wifiscanner
# ---------------------------------------------------------------------------


def parse_wifi_dumpsys(text: str) -> dict[str, Any]:
    """Parse ``dumpsys wifi`` (and optionally ``dumpsys wifiscanner``) output.

    The two dumps may be concatenated into a single string; each sub-parser
    anchors on its own section fences.

    Returns ``{"current": WifiConnectionState|None, "saved": [...],
    "scan_results": [...], "caveats": [...]}``.  Never raises: unparseable or
    empty input yields empty collections plus an explanatory caveat.
    """
    result: dict[str, Any] = {
        "current": None,
        "saved": [],
        "scan_results": [],
        "caveats": [],
    }
    if not text or not text.strip():
        result["caveats"].append(
            "Empty dumpsys output supplied — nothing was parsed. This records the "
            "ABSENCE OF A CAPTURE, not the absence of Wi-Fi activity on the device."
        )
        return result

    try:
        current, cur_caveats = _parse_current_connection(text)
        result["current"] = current
        result["caveats"].extend(cur_caveats)
    except Exception as exc:  # pragma: no cover - defensive
        result["caveats"].append(f"Live-association parse failed: {exc!r}")

    try:
        saved, saved_caveats = _parse_saved_networks(text)
        result["saved"] = saved
        result["caveats"].extend(saved_caveats)
    except Exception as exc:  # pragma: no cover - defensive
        result["caveats"].append(f"Saved-network parse failed: {exc!r}")

    try:
        scans, scan_caveats = _parse_scan_results(text)
        result["scan_results"] = scans
        result["caveats"].extend(scan_caveats)
    except Exception as exc:  # pragma: no cover - defensive
        result["caveats"].append(f"Scan-result parse failed: {exc!r}")

    if not result["current"] and not result["saved"] and not result["scan_results"]:
        result["caveats"].append(
            "No Wi-Fi records could be parsed from the supplied text. Either the "
            "capture is not dumpsys output, or the build's dump format is not "
            "recognised — treat as INACCESSIBLE, not as 'no Wi-Fi data'."
        )

    result["caveats"].append(CAVEAT_OEM)
    return result


# ---------------------------------------------------------------------------
# `dumpsys netstats`
# ---------------------------------------------------------------------------

_NS_IDENT = re.compile(
    r"^\s*ident=\[\{(?P<body>[^}]*)\}\]\s+uid=(?P<uid>-?\d+)\s+set=(?P<set>\w+)"
    r"\s+tag=(?P<tag>0x[0-9a-fA-F]+)"
)
_NS_ACTIVE_IFACE = re.compile(r"^\s*iface=(?P<iface>\S+)\s+ident=\[\{(?P<body>[^}]*)\}\]")
# ``networkId="X"`` on Android <=12, ``wifiNetworkKey="X"<suffix>`` on 13+.
_NS_SSID = re.compile(r'(?:networkId|wifiNetworkKey)="(?P<ssid>[^"]*)"')
_NS_DUR = re.compile(r"^\s*NetworkStatsHistory:\s*bucketDuration=(?P<sec>\d+)")
_NS_ROW = re.compile(
    r"^\s*st=(?P<st>\d+)\s+rb=(?P<rb>\d+)\s+rp=(?P<rp>\d+)\s+"
    r"tb=(?P<tb>\d+)\s+tp=(?P<tp>\d+)(?:\s+op=(?P<op>\d+))?"
)
_NS_OMIT = re.compile(r"\(omitting (?P<n>\d+) buckets\)")


def _ident_is_wifi(body: str) -> bool:
    """True when the NetworkIdentity describes a Wi-Fi network.

    Android <=12 prints ``type=WIFI``; Android 13+ prints the raw int
    (``type=1`` == TYPE_WIFI).  Presence of an SSID key is also sufficient.
    """
    if "networkId=" in body or "wifiNetworkKey=" in body:
        return True
    return bool(re.search(r"\btype=(?:WIFI|1)\b", body))


def parse_netstats(text: str) -> list[WifiUsageBucket]:
    """Parse ``dumpsys netstats --full --uid`` into per-SSID hour buckets.

    Only Wi-Fi identities are returned; cellular identities are ignored (their
    ``subscriberId`` is IMSI, is framework-scrubbed anyway, and is out of scope
    here).  Every returned bucket is ``approximate=True`` and carries a caveat
    naming the bucket duration, because netstats records BYTES MOVED, never
    association state.

    Never raises — a malformed line is skipped.
    """
    buckets: list[WifiUsageBucket] = []
    if not text or not text.strip():
        return buckets

    truncated = bool(_NS_OMIT.search(text))

    # Map SSID -> interface using the Active interfaces / Active UID interfaces
    # blocks; the history sections themselves do not name the interface.
    iface_by_ssid: dict[str, str] = {}
    for line in text.splitlines():
        m = _NS_ACTIVE_IFACE.match(line)
        if not m:
            continue
        sm = _NS_SSID.search(m.group("body"))
        if sm:
            iface_by_ssid.setdefault(sm.group("ssid"), m.group("iface"))

    cur_ssid: Optional[str] = None
    cur_uid: Optional[int] = None
    cur_dur_s: int = 0

    for line in text.splitlines():
        try:
            m = _NS_IDENT.match(line)
            if m:
                body = m.group("body")
                if not _ident_is_wifi(body):
                    cur_ssid = None
                    continue
                sm = _NS_SSID.search(body)
                # wifiNetworkKey is the WifiConfiguration key: the quoted SSID is
                # immediately followed by a security suffix (e.g. "Fraise"wpa2-psk).
                # Taking the text between the quotes strips the suffix.
                cur_ssid = sm.group("ssid") if sm else ""
                cur_uid = _int_or_none(m.group("uid"))
                cur_dur_s = 0
                continue

            m = _NS_DUR.match(line)
            if m:
                cur_dur_s = _int_or_none(m.group("sec")) or 0
                continue

            m = _NS_ROW.match(line)
            if m and cur_ssid is not None:
                if not cur_dur_s:
                    # A history row with no preceding bucketDuration cannot be given
                    # honest bounds. Skip rather than guess an hour.
                    continue
                st = int(m.group("st"))
                start_iso = _iso_from_epoch(st)
                end_iso = _iso_from_epoch(st + cur_dur_s)
                rx = int(m.group("rb"))
                tx = int(m.group("tb"))

                caveats = [
                    CAVEAT_NETSTATS_TEMPLATE.format(
                        sec=cur_dur_s,
                        ssid=cur_ssid,
                        start=start_iso,
                        end=end_iso,
                    ),
                    f"Bucket resolution is {cur_dur_s} seconds "
                    f"({cur_dur_s / 3600.0:g} h) — never narrow this interval.",
                    CAVEAT_NO_JOIN_TIME,
                ]
                if rx == 0 and tx == 0:
                    caveats.append(CAVEAT_NETSTATS_ZERO)
                if cur_uid is not None and cur_uid >= 0:
                    caveats.append(
                        f"Per-UID series (uid={cur_uid}): attribution is to the UID that "
                        "owned the socket, which may be a shared or system UID rather "
                        "than a single user-facing app."
                    )
                if truncated:
                    caveats.append(
                        "The supplied dump contains '(omitting N buckets)': it was NOT "
                        "captured with `--full`, so only the most recent 32 buckets per "
                        "series are present. Older history exists on the device."
                    )

                buckets.append(
                    WifiUsageBucket(
                        ssid=cur_ssid,
                        iface=iface_by_ssid.get(cur_ssid, ""),
                        uid=cur_uid,
                        bucket_start=start_iso,
                        bucket_end=end_iso,
                        duration_ms=cur_dur_s * 1000,
                        rx_bytes=rx,
                        tx_bytes=tx,
                        approximate=True,  # never False: see class docstring
                        caveats=caveats,
                    )
                )
        except Exception:
            continue

    return buckets


# ---------------------------------------------------------------------------
# `dumpsys connectivity`
# ---------------------------------------------------------------------------

_CONN_DEF = re.compile(r"^Active default network:\s*(?P<netid>\d+)", re.M)
_CONN_NAI = re.compile(r"NetworkAgentInfo\{(?P<body>.*)$", re.M)
_CONN_NETID = re.compile(r"\bnetwork\{(?P<netid>\d+)\}")
_CONN_SSID = re.compile(r'\bSSID:\s*"(?P<ssid>[^"]*)"')
_CONN_TR = re.compile(r"Transports:\s*(?P<t>[A-Z|&_]+)")
_CONN_CAP = re.compile(r"Capabilities:\s*(?P<c>[A-Z_&]+)")
_CONN_VAL = re.compile(r"everValidated\{(?P<ever>true|false)\}\s+lastValidated\{(?P<last>true|false)\}")
_CONN_IF = re.compile(r"InterfaceName:\s*(?P<iface>\S+)")
_CONN_LINKADDR = re.compile(r"LinkAddresses:\s*\[(?P<addrs>[^\]]*)\]")
_CONN_DNS = re.compile(r"DnsAddresses:\s*\[(?P<dns>[^\]]*)\]")
_CONN_STATE = re.compile(r"state:\s*(?P<state>\w+)/")
_CONN_NI_SHORT = re.compile(r"ni\{(?P<type>\w+)\s+(?P<state>[A-Z]+)")
_CONN_TYPE = re.compile(r"type:\s*(?P<type>\w+)")


def parse_connectivity(text: str) -> dict[str, Any]:
    """Parse ``dumpsys connectivity`` into an active-network summary.

    Returns ``{"active_default_netid", "wifi_connected", "networks": [...],
    "caveats": [...]}``.  Field order inside ``NetworkAgentInfo{}`` varies by
    Android version (and ``handle{}`` vs ``nethandle{}`` differ across builds),
    so everything is parsed by key and never by position.  Never raises.
    """
    out: dict[str, Any] = {
        "active_default_netid": None,
        "wifi_connected": False,
        "networks": [],
        "caveats": [],
    }
    if not text or not text.strip():
        out["caveats"].append(
            "Empty `dumpsys connectivity` output — the connectivity view is ABSENT "
            "FROM THIS CAPTURE, not empty on the device."
        )
        return out

    try:
        m = _CONN_DEF.search(text)
        if m:
            out["active_default_netid"] = m.group("netid")

        for nai in _CONN_NAI.finditer(text):
            try:
                body = nai.group("body")
                nm = _CONN_NETID.search(body)
                net_id = nm.group("netid") if nm else None

                sm = _CONN_SSID.search(body)
                ssid = sm.group("ssid") if sm else None

                tm = _CONN_TR.search(body)
                transports = tm.group("t") if tm else ""

                cm = _CONN_CAP.search(body)
                capabilities = cm.group("c") if cm else ""

                vm = _CONN_VAL.search(body)
                ever_validated = (vm.group("ever") == "true") if vm else None
                last_validated = (vm.group("last") == "true") if vm else None

                im = _CONN_IF.search(body)
                iface = im.group("iface") if im else ""

                lm = _CONN_LINKADDR.search(body)
                link_addresses = (
                    [a.strip() for a in lm.group("addrs").split(",") if a.strip()]
                    if lm
                    else []
                )

                dm = _CONN_DNS.search(body)
                dns = (
                    [a.strip() for a in dm.group("dns").split(",") if a.strip()]
                    if dm
                    else []
                )

                stm = _CONN_STATE.search(body)
                if stm:
                    state = stm.group("state")
                else:
                    short = _CONN_NI_SHORT.search(body)
                    state = short.group("state") if short else ""

                tym = _CONN_TYPE.search(body)
                if tym:
                    net_type = tym.group("type")
                else:
                    short = _CONN_NI_SHORT.search(body)
                    net_type = short.group("type") if short else ""

                is_wifi = "WIFI" in transports.upper() or net_type.upper() == "WIFI"
                is_default = (
                    net_id is not None and net_id == out["active_default_netid"]
                )

                entry = {
                    "net_id": net_id,
                    "type": net_type,
                    "state": state,
                    "ssid": ssid,
                    "transports": transports,
                    "capabilities": capabilities,
                    "validated": "VALIDATED" in capabilities,
                    "ever_validated": ever_validated,
                    "last_validated": last_validated,
                    "interface": iface,
                    "link_addresses": link_addresses,
                    "dns_addresses": dns,
                    "is_wifi": is_wifi,
                    "is_default": is_default,
                }
                out["networks"].append(entry)

                if is_wifi and state.upper() == "CONNECTED":
                    out["wifi_connected"] = True
            except Exception:
                continue

        if out["networks"]:
            out["caveats"].append(
                "Connectivity state is wholly volatile: netIds, NetworkAgentInfo "
                "entries and the default-network selection are destroyed on reboot "
                "and reflect capture time only."
            )
            if any(n["is_wifi"] and n["link_addresses"] for n in out["networks"]):
                out["caveats"].append(
                    "The DHCP-assigned IP in LinkAddresses pivots to the access "
                    "point's lease table alongside the randomised MAC, but leases are "
                    "reassigned over time — correlate on MAC first, IP second."
                )
        else:
            out["caveats"].append(
                "No NetworkAgentInfo entries parsed from the connectivity dump."
            )
    except Exception as exc:  # pragma: no cover - defensive
        out["caveats"].append(f"Connectivity parse failed: {exc!r}")

    return out


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------


def collect_wifi_live(shell: Callable[[str], str]) -> dict[str, Any]:
    """Run the Tier-0 read-only Wi-Fi commands and parse everything.

    ``shell`` is a callable ``(cmd: str) -> str`` supplied by the pipeline (this
    module never shells out itself).  Every call is individually guarded: a
    command that raises or returns nothing is recorded as failed and the rest of
    the collection continues.  This function NEVER raises and NEVER fabricates a
    success result.
    """
    result: dict[str, Any] = {
        "current": None,
        "saved": [],
        "scan_results": [],
        "usage": [],
        "connectivity": {},
        "device": {},
        "commands": [],
        "caveats": [CAVEAT_TIER0, CAVEAT_NO_POLL, CAVEAT_NO_JOIN_TIME, CAVEAT_OEM],
    }

    outputs: dict[str, str] = {}
    for cmd in WIFI_DUMPSYS_COMMANDS:
        entry: dict[str, Any] = {"command": cmd, "ok": False, "bytes": 0, "error": ""}
        try:
            raw = shell(cmd)
            text = raw if isinstance(raw, str) else ""
            outputs[cmd] = text
            entry["ok"] = bool(text.strip())
            entry["bytes"] = len(text)
            if not entry["ok"]:
                entry["error"] = "empty output"
                result["caveats"].append(
                    f"`{cmd}` returned no output — that data source is INACCESSIBLE "
                    "on this device/build, which is not the same as absent."
                )
        except Exception as exc:
            outputs[cmd] = ""
            entry["error"] = repr(exc)
            result["caveats"].append(
                f"`{cmd}` failed: {exc!r}. Recorded as a collection failure; no data "
                "has been inferred in its place."
            )
        result["commands"].append(entry)

    wifi_text = outputs.get("dumpsys wifi", "")
    scanner_text = outputs.get("dumpsys wifiscanner", "")

    try:
        parsed = parse_wifi_dumpsys("\n".join(t for t in (wifi_text, scanner_text) if t))
        result["current"] = parsed.get("current")
        result["saved"] = parsed.get("saved", [])
        result["scan_results"] = parsed.get("scan_results", [])
        result["caveats"].extend(parsed.get("caveats", []))
    except Exception as exc:  # pragma: no cover - defensive
        result["caveats"].append(f"dumpsys wifi parse failed: {exc!r}")

    try:
        result["usage"] = parse_netstats(outputs.get("dumpsys netstats --full --uid", ""))
    except Exception as exc:  # pragma: no cover - defensive
        result["caveats"].append(f"netstats parse failed: {exc!r}")

    try:
        result["connectivity"] = parse_connectivity(outputs.get("dumpsys connectivity", ""))
    except Exception as exc:  # pragma: no cover - defensive
        result["connectivity"] = {}
        result["caveats"].append(f"connectivity parse failed: {exc!r}")

    # Capture context: needed to reason about every year-less, offset-less
    # timestamp in the dump. Recorded, never used to silently "fix" a value.
    iface_mac = (outputs.get("cat /sys/class/net/wlan0/address", "") or "").strip()
    if iface_mac and iface_mac.lower() == DEFAULT_MAC_PLACEHOLDER:
        iface_mac = ""
        result["caveats"].append(CAVEAT_MAC_PLACEHOLDER)
    result["device"] = {
        "sdk": (outputs.get("getprop ro.build.version.sdk", "") or "").strip(),
        "timezone": (outputs.get("getprop persist.sys.timezone", "") or "").strip(),
        "wlan0_mac": iface_mac,
        "wlan0_mac_is_randomized": _is_locally_administered(iface_mac or None),
        "captured_at": now_iso(),
    }
    if iface_mac and _is_locally_administered(iface_mac):
        result["caveats"].append(
            "wlan0's in-use MAC is locally-administered (randomised). " + CAVEAT_RANDOMISED_MAC
        )

    return result


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------


def build_wifi_timeline(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Build TimelineEvent-shaped dicts from a :func:`collect_wifi_live` result.

    Accepts either the dataclass-bearing dict returned by the collector or an
    already-JSON-decoded equivalent.

    Confidence policy:

    * the **live association** is ``Confidence.LIVE`` — it was directly observed
      at capture time;
    * **netstats-derived** events are ``Confidence.RECOVERED_VERIFIED`` — the
      byte counters are intact persisted records, but the association they
      *imply* is an inference, so they must never carry the same weight as a
      live observation. Their summaries state "approximate" and name the
      hour-bucket granularity explicitly;
    * **saved-network** last-seen events are ``Confidence.RECOVERED_VERIFIED``
      with an explicit non-authoritative marker.

    Scan results produce no timeline events at all: their age is monotonic
    since boot, so placing them on a wall-clock axis would be a fabrication.
    """
    events: list[TimelineEvent] = []
    if not isinstance(result, dict):
        return []

    # --- live association ---------------------------------------------------
    cur = _as_dict(result.get("current"))
    if cur and cur.get("captured_at"):
        ssid = cur.get("ssid") or "<unknown ssid>"
        bssid = cur.get("bssid") or "<none>"
        mac = cur.get("mac_address")
        mac_part = ""
        if mac:
            mac_part = (
                f", in-use MAC {mac}"
                + (" (randomised, per-SSID)" if cur.get("randomized_mac") else " (globally-administered OUI)")
            )
        if cur.get("is_connected"):
            summary = (
                f"Wi-Fi ASSOCIATED at capture time: SSID '{ssid}', BSSID {bssid}"
                f"{mac_part}, {cur.get('frequency_mhz')} MHz, RSSI {cur.get('rssi')} dBm, "
                f"supplicant state {cur.get('supplicant_state')}. Observed live; this is "
                "the state when dumpsys ran, NOT the time the association began."
            )
        else:
            summary = (
                f"Wi-Fi NOT associated at capture time (supplicant state "
                f"{cur.get('supplicant_state') or 'unknown'}). Observed live; this does "
                "not imply the device was never connected."
            )
        events.append(
            TimelineEvent(
                timestamp=cur["captured_at"],
                kind="wifi",
                summary=summary,
                confidence=Confidence.LIVE,
                ref=f"dumpsys wifi:mWifiInfo:{ssid}",
            )
        )

    # --- netstats buckets ---------------------------------------------------
    for bucket in result.get("usage") or []:
        b = _as_dict(bucket)
        if not b.get("bucket_start"):
            continue
        rx = int(b.get("rx_bytes") or 0)
        tx = int(b.get("tx_bytes") or 0)
        if rx + tx <= 0:
            # A zero bucket proves nothing; do not put it on the timeline.
            continue
        dur_ms = int(b.get("duration_ms") or 0)
        dur_s = dur_ms // 1000
        hours = dur_s / 3600.0 if dur_s else 0.0
        summary = (
            f"approximate — non-authoritative: traffic attributable to SSID "
            f"'{b.get('ssid') or '<unknown>'}' was recorded in the netstats bucket "
            f"beginning {b.get('bucket_start')} (ends {b.get('bucket_end')}). "
            f"Hour-bucket granularity: bucketDuration={dur_s}s ({hours:g} h) — the "
            "interval must not be narrowed. Byte counters prove bytes moved "
            f"(rx={rx}, tx={tx}); they do not establish association time or duration."
        )
        events.append(
            TimelineEvent(
                timestamp=b["bucket_start"],
                kind="wifi",
                summary=summary,
                # Derived from an intact persisted record, but the association is
                # an inference — deliberately NOT Confidence.LIVE.
                confidence=Confidence.RECOVERED_VERIFIED,
                ref=f"netstats:{b.get('ssid', '')}:{b.get('bucket_start', '')}",
            )
        )

    # --- saved-network recency ---------------------------------------------
    for net in result.get("saved") or []:
        n = _as_dict(net)
        last_seen = n.get("last_seen")
        if not last_seen:
            continue
        events.append(
            TimelineEvent(
                timestamp=last_seen,
                kind="wifi",
                summary=(
                    f"NOT AUTHORITATIVE: saved network '{n.get('ssid', '')}' reports an "
                    f"in-memory lastConnected of {last_seen}. The device printed "
                    "device-local time with no UTC offset and the value is cleared on "
                    "reboot; treat as a recency ordering hint only, never as a join time."
                ),
                confidence=Confidence.RECOVERED_VERIFIED,
                ref=f"dumpsys wifi:saved:{n.get('ssid', '')}",
            )
        )

    events.sort(key=lambda e: e.timestamp)
    return [e.to_dict() for e in events]


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def wifi_live_summary(result: dict[str, Any]) -> dict[str, Any]:
    """Counts plus the examiner-facing caveat paragraph for the report."""
    if not isinstance(result, dict):
        result = {}

    cur = _as_dict(result.get("current"))
    saved = [_as_dict(s) for s in (result.get("saved") or [])]
    scans = [_as_dict(s) for s in (result.get("scan_results") or [])]
    usage = [_as_dict(u) for u in (result.get("usage") or [])]
    conn = result.get("connectivity") or {}

    ssids_with_traffic = sorted({u.get("ssid", "") for u in usage if (u.get("rx_bytes") or 0) + (u.get("tx_bytes") or 0) > 0})
    bucket_durations = sorted({int(u.get("duration_ms") or 0) // 1000 for u in usage if u.get("duration_ms")})

    # Recency ORDERING (never times): most-recently-connected first, then the
    # networks used most recently in boot-ordinal terms.
    ordered = sorted(
        saved,
        key=lambda n: (
            0 if n.get("is_most_recently_connected") else 1,
            0 if n.get("has_ever_connected") else 1,
            -(n.get("num_association") or 0),
        ),
    )

    caveat_paragraph = " ".join(
        [
            CAVEAT_TIER0,
            CAVEAT_NO_JOIN_TIME,
            "Any interval shown for an SSID is derived from dumpsys netstats "
            "hour-bucketed byte counters and is APPROXIMATE: it shows that traffic "
            "was carried over that SSID at some point inside the bucket, not that "
            "the device joined or left it at those times, and not where the device "
            "was.",
            CAVEAT_SAVED_ORDERING,
            CAVEAT_RANDOMISED_MAC,
            CAVEAT_SCAN_MONOTONIC,
            CAVEAT_NO_POLL,
            CAVEAT_OEM,
        ]
    )

    return {
        "tier": "tier0",
        "connected_at_capture": bool(cur.get("is_connected")),
        "current_ssid": cur.get("ssid"),
        "current_bssid": cur.get("bssid"),
        "current_mac": cur.get("mac_address"),
        "current_mac_randomized": bool(cur.get("randomized_mac")),
        "hardware_mac_available": False,
        "saved_count": len(saved),
        "saved_with_randomized_mac": sum(1 for n in saved if n.get("randomized_mac")),
        "scan_result_count": len(scans),
        "unique_scanned_bssids": len({s.get("bssid") for s in scans if s.get("bssid")}),
        "usage_bucket_count": len(usage),
        "usage_all_approximate": all(u.get("approximate", False) for u in usage),
        "ssids_with_traffic": ssids_with_traffic,
        "bucket_durations_s": bucket_durations,
        "active_default_netid": conn.get("active_default_netid"),
        "wifi_connected_per_connectivity": bool(conn.get("wifi_connected")),
        "saved_recency_ordering": [n.get("ssid", "") for n in ordered],
        "commands": [c.get("command") for c in (result.get("commands") or []) if isinstance(c, dict)],
        "caveats": list(result.get("caveats") or []),
        "caveat_paragraph": caveat_paragraph,
    }
