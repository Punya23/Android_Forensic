"""Wi-Fi credential parser (Root Tier 2).

Supports every on-device Wi-Fi config format Android has shipped:

* **``wpa_supplicant.conf``** (Android ≤ 8.0)  — plaintext wpa_supplicant
  configuration; one ``network={...}`` stanza per saved network.
* **``WifiConfigStore.xml``** (Android ≥ 9.0)  — XML produced by the
  ``WifiConfigManager``; one ``<Network>`` element per saved network.
* **``WifiConfigStoreSoftAp.xml``** (Android ≥ 9.0) — the credentials of the
  device's *own* mobile hotspot, which is a different fact from any network
  it joined and is flagged ``is_softap=True``.

The dispatcher :func:`parse_wifi_config` detects the format from the file
name (or path suffix) and calls the appropriate sub-parser — no hardcoded
Android version checks.  All parsing is best-effort: a malformed entry is
skipped rather than aborting the whole file.

Where the files live changes with the OS version, and getting this wrong reads
as "device had no saved networks" rather than "we looked in the Android 9 place
on an Android 14 device" — see :data:`WIFI_CONFIG_PATHS` for the full probe list.
All of them require **root access** (Tier 2) to read.  This module only parses
locally-staged copies; pulling from the device is the pipeline's responsibility.

Connection *times* are deliberately not invented here; see
:class:`triage.models.WifiNetwork` for what Android does and does not persist.
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Optional

from ..models import WifiNetwork
from ..config import Confidence


#: Every known on-device location of a Wi-Fi config store, as
#: ``(device path, local staging name, is_softap)``.  Probed in order and all
#: hits are parsed — a device can legitimately carry both an APEX-era store and
#: a legacy one left behind by an OS upgrade, and the legacy copy is often the
#: only place a since-forgotten network still exists.
WIFI_CONFIG_PATHS: list[tuple[str, str, bool]] = [
    # Android 11+ — the Wi-Fi stack moved into the com.android.wifi APEX and
    # took its config store with it. This is THE path on any current device.
    (
        "/data/misc/apexdata/com.android.wifi/WifiConfigStore.xml",
        "WifiConfigStore.xml",
        False,
    ),
    (
        "/data/misc/apexdata/com.android.wifi/WifiConfigStoreSoftAp.xml",
        "WifiConfigStoreSoftAp.xml",
        True,
    ),
    # Android 9–10 — pre-APEX location.
    ("/data/misc/wifi/WifiConfigStore.xml", "WifiConfigStore.legacy.xml", False),
    (
        "/data/misc/wifi/WifiConfigStoreSoftAp.xml",
        "WifiConfigStoreSoftAp.legacy.xml",
        True,
    ),
    # Android ≤ 8 — wpa_supplicant era.
    ("/data/misc/wifi/wpa_supplicant.conf", "wpa_supplicant.conf", False),
]


# ---------------------------------------------------------------------------
# Shared XML helpers
# ---------------------------------------------------------------------------

#: Epoch seconds either side of which a "timestamp" field is not a wall-clock
#: time: 2008-01-01 (before Android 1.0 shipped) and 2100-01-01.
_EPOCH_MIN_S = 1_199_145_600
_EPOCH_MAX_S = 4_102_444_800


def _iso(epoch_s: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch_s))


def _collect_typed(el: ET.Element) -> dict[str, Any]:
    """Flatten one Android XML block into ``{field name: python value}``.

    Android's ``XmlUtils`` writes typed elements — ``<string name="SSID">v</string>``,
    ``<boolean name="Shared" value="true" />``, ``<int name="ApBand" value="1" />``.
    Reading them generically rather than by a hardcoded field list means an OEM
    or OS version that adds a field still gets it captured instead of dropped.
    """
    out: dict[str, Any] = {}
    for child in el.iter():
        name = (child.get("name") or "").strip()
        if not name:
            continue
        tag = child.tag.lower()
        if tag in ("string", "mac-address"):
            out[name] = _unquote((child.text or "").strip())
        elif tag == "boolean":
            out[name] = (child.get("value") or "").strip().lower() == "true"
        elif tag in ("int", "long"):
            try:
                out[name] = int((child.get("value") or "").strip())
            except ValueError:
                continue
        elif tag == "byte-array":
            out[name] = (child.get("value") or child.text or "").strip()
        elif tag == "null":
            out[name] = None
        elif tag == "string-array":
            out[name] = [
                _unquote((item.text or "").strip()) for item in child.findall("item")
            ]
    return out


def _epoch_fields(values: dict[str, Any]) -> dict[str, str]:
    """Return ``{original field name: ISO-8601}`` for every real epoch in *values*.

    Which timestamp fields exist varies by Android version (``ConnectChoiceTimestamp``
    arrived in Android 12, for instance), so rather than assert a schema we keep
    whatever the store carried **under its own name**.  A reader then knows the
    timestamp is "when the user chose this network over another", not an
    unqualified "last connected" the store never claimed.
    """
    out: dict[str, str] = {}
    for key, val in values.items():
        low = key.lower()
        if "time" not in low and "stamp" not in low:
            continue
        if not isinstance(val, int) or isinstance(val, bool) or val <= 0:
            continue
        if _EPOCH_MIN_S <= val <= _EPOCH_MAX_S:
            out[key] = _iso(val)
        elif _EPOCH_MIN_S * 1000 <= val <= _EPOCH_MAX_S * 1000:
            out[key] = _iso(val / 1000)
        # Anything else is an uptime/elapsed-realtime counter, not a date. Drop it
        # rather than render a 1970 timestamp that reads as a real finding.
    return out


def _network_status_label(values: dict[str, Any]) -> str:
    """Human-readable ``<NetworkStatus>`` selection status, if the store has one."""
    raw = values.get("SelectionStatus")
    if isinstance(raw, str) and raw:
        return raw
    if isinstance(raw, int):
        return {
            0: "enabled",
            1: "temporarily-disabled",
            2: "permanently-disabled",
        }.get(raw, f"status-{raw}")
    return ""


# ---------------------------------------------------------------------------
# wpa_supplicant.conf parser (Android ≤ 8)
# ---------------------------------------------------------------------------

_NETWORK_BLOCK_RE = re.compile(r"network\s*=\s*\{([^}]*)\}", re.DOTALL)
_KV_RE = re.compile(r"^\s*(\w+)\s*=\s*(.+?)\s*$", re.MULTILINE)


def _unquote(value: str) -> str:
    """Strip surrounding double-quotes from a wpa_supplicant value if present."""
    value = value.strip()
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    return value


def parse_wpa_supplicant_conf(path: Path) -> list[WifiNetwork]:
    """Parse ``wpa_supplicant.conf`` and return a list of :class:`WifiNetwork`.

    Regex-based: extracts every ``network={...}`` block and reads ``ssid``
    and ``psk`` (or ``wep_key0`` for WEP) key-value pairs.  Quoted and
    unquoted values are both handled.  Networks without a ``psk`` (e.g. open
    or enterprise) are still returned with an empty ``password`` field.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    networks: list[WifiNetwork] = []

    for block_match in _NETWORK_BLOCK_RE.finditer(text):
        block = block_match.group(1)
        kv: dict[str, str] = {}
        for m in _KV_RE.finditer(block):
            key = m.group(1).lower()
            val = _unquote(m.group(2))
            kv[key] = val

        ssid = kv.get("ssid", "").strip()
        if not ssid:
            continue  # malformed or non-network stanza

        password = kv.get("psk", "") or kv.get("wep_key0", "")
        key_mgmt = kv.get("key_mgmt", "NONE").upper()
        # Derive a human-readable security label.
        if "WPA" in key_mgmt:
            security = "WPA/WPA2"
        elif "WEP" in key_mgmt or kv.get("wep_key0"):
            security = "WEP"
        elif key_mgmt in ("NONE", ""):
            security = "OPEN"
        else:
            security = key_mgmt

        networks.append(
            WifiNetwork(
                ssid=ssid,
                password=password,
                security=security,
                confidence=Confidence.LIVE,
                source_file=path.name,
            )
        )

    return networks


# ---------------------------------------------------------------------------
# WifiConfigStore.xml parser (Android ≥ 9)
# ---------------------------------------------------------------------------


def parse_wifi_config_store_xml(path: Path) -> list[WifiNetwork]:
    """Parse ``WifiConfigStore.xml`` and return a list of :class:`WifiNetwork`.

    Uses ``xml.etree.ElementTree`` (stdlib only).  Structure (simplified):

    .. code-block:: xml

        <WifiConfigStoreData version="3">
          <NetworkList>
            <Network>
              <WifiConfiguration>
                <string name="SSID">&quot;MyNetwork&quot;</string>
                <string name="PreSharedKey">&quot;mypassword&quot;</string>
                <string name="AllowedKeyMgmt">WPA_PSK</string>
                …
              </WifiConfiguration>
              …
            </Network>
          </NetworkList>
        </WifiConfigStoreData>

    The ``PreSharedKey`` value may itself be surrounded by extra double-quotes
    (some OEMs store ``"\"password\""``) which are stripped.
    
    Supports:
    - SSID (strip quotes)
    - PreSharedKey (strip quotes) 
    - AllowedKeyMgmt (WPA, WEP, OPEN, WPA3)
    """
    try:
        tree = ET.parse(str(path))
    except (ET.ParseError, OSError):
        return []

    root = tree.getroot()
    networks: list[WifiNetwork] = []

    # Networks may be nested under <NetworkList> or directly under root.
    for network_el in root.iter("Network"):
        wifi_cfg = network_el.find("WifiConfiguration")
        if wifi_cfg is None:
            continue

        values = _collect_typed(wifi_cfg)
        status_el = network_el.find("NetworkStatus")
        status_values = _collect_typed(status_el) if status_el is not None else {}

        # SSID is stored as a quoted string: "\"MyNetwork\""
        ssid = str(values.get("SSID") or "").strip('"')
        if not ssid:
            # Rare OEM variant: SSID as a hex byte-array rather than a string.
            raw_hex = values.get("SSID") or values.get("ssid") or ""
            try:
                ssid = bytes.fromhex(str(raw_hex)).decode("utf-8", errors="replace")
            except ValueError:
                ssid = ""
        if not ssid:
            continue

        password = str(values.get("PreSharedKey") or "").strip('"')
        if not password:
            # WEP keys live in a separate array; index is WEPTxKeyIndex.
            wep_keys = values.get("WEPKeys")
            if isinstance(wep_keys, list):
                idx = values.get("WEPTxKeyIndex")
                idx = idx if isinstance(idx, int) and 0 <= idx < len(wep_keys) else 0
                if wep_keys:
                    password = str(wep_keys[idx] or "").strip('"')

        key_mgmt_raw = str(values.get("AllowedKeyMgmt") or "")

        # Security label from AllowedKeyMgmt bitmask name. The label vocabulary is
        # shared with parse_wpa_supplicant_conf so a network reads the same whichever
        # store it came out of — WPA_PSK/WPA2_PSK are both "WPA/WPA2".
        key_mgmt_upper = key_mgmt_raw.upper()
        if "WPA3_SAE" in key_mgmt_upper or "SAE" in key_mgmt_upper:
            security = "WPA3"
        elif "WPA_PSK" in key_mgmt_upper or "WPA2_PSK" in key_mgmt_upper:
            security = "WPA/WPA2"
        elif "WEP" in key_mgmt_upper or values.get("WEPKeys"):
            security = "WEP"
        elif "NONE" in key_mgmt_upper or not key_mgmt_raw:
            security = "OPEN"
        else:
            security = key_mgmt_raw

        merged = {**values, **status_values}
        has_ever = status_values.get("HasEverConnected")
        caveats: list[str] = []
        if has_ever is False:
            caveats.append(
                "HasEverConnected=false — the network was saved but never successfully "
                "joined. Saving a network is not evidence of being at it."
            )
        elif has_ever is True:
            caveats.append(
                "HasEverConnected=true records that the network was joined at least "
                "once. Android does not store WHEN; corroborate with the netstats "
                "hour buckets or the live dumpsys association."
            )
        else:
            caveats.append(
                "No HasEverConnected flag in this store — whether the device ever "
                "joined this network is unrecorded, not disproved."
            )

        networks.append(
            WifiNetwork(
                ssid=ssid,
                password=password,
                security=security,
                confidence=Confidence.LIVE,
                source_file=path.name,
                has_ever_connected=has_ever if isinstance(has_ever, bool) else None,
                is_most_recently_connected=_opt_bool(
                    values.get("IsMostRecentlyConnected")
                ),
                creator=str(values.get("CreatorName") or ""),
                last_update_by=str(values.get("LastUpdateName") or ""),
                default_gateway_mac=str(values.get("DefaultGwMacAddress") or ""),
                randomized_mac=str(values.get("RandomizedMacAddress") or ""),
                metered=_metered_label(values.get("MeteredOverride")),
                network_status=_network_status_label(status_values),
                hidden=_opt_bool(values.get("HiddenSSID")),
                timestamps=_epoch_fields(merged),
                caveats=caveats,
            )
        )

    return networks


def _opt_bool(value: Any) -> Optional[bool]:
    """Narrow a collected value to a bool, leaving anything else as unknown."""
    return value if isinstance(value, bool) else None


def _metered_label(raw: Any) -> str:
    """Android's ``MeteredOverride`` enum, or "" when the user never set one."""
    if not isinstance(raw, int) or isinstance(raw, bool):
        return ""
    return {0: "", 1: "metered", 2: "not-metered"}.get(raw, "")


# ---------------------------------------------------------------------------
# WifiConfigStoreSoftAp.xml parser — the device's OWN hotspot
# ---------------------------------------------------------------------------


def parse_wifi_softap_xml(path: Path) -> list[WifiNetwork]:
    """Parse ``WifiConfigStoreSoftAp.xml`` — the device's own hotspot config.

    This answers a different question from the saved-network store: not "which
    networks did this device join" but "what hotspot did this device *offer*",
    including its passphrase.  A match between this SSID and another device's
    saved-network list is a direct device-to-device link.

    The element and field names drift across versions — Android 9/10 wrote
    ``<string name="WifiSsid">``, Android 11+ writes ``<string name="SSID">``
    with a ``SecurityType`` int — so both spellings are accepted.  Returns at
    most one entry; the list shape keeps it interchangeable with the other
    parsers.
    """
    try:
        tree = ET.parse(str(path))
    except (ET.ParseError, OSError):
        return []

    root = tree.getroot()
    block = root.find(".//SoftAp")
    values = _collect_typed(block if block is not None else root)

    ssid = str(values.get("SSID") or values.get("WifiSsid") or "").strip('"')
    if not ssid:
        return []

    password = str(
        values.get("Passphrase") or values.get("PreSharedKey") or ""
    ).strip('"')

    # Android 11+ SoftApConfiguration.SECURITY_TYPE_* enum.
    security = {
        0: "OPEN",
        1: "WPA2",
        2: "WPA3-SAE-transition",
        3: "WPA3-SAE",
    }.get(values.get("SecurityType"), "WPA2" if password else "OPEN")

    return [
        WifiNetwork(
            ssid=ssid,
            password=password,
            security=security,
            confidence=Confidence.LIVE,
            source_file=path.name,
            is_softap=True,
            hidden=_opt_bool(values.get("HiddenSSID")),
            timestamps=_epoch_fields(values),
            caveats=[
                "This is the hotspot this device OFFERS, not a network it joined. "
                "The record proves the hotspot was configured — not that it was ever "
                "switched on, and not when.",
            ],
        )
    ]


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def parse_wifi_config(path: Path) -> list[WifiNetwork]:
    """Auto-detect Wi-Fi config format and parse accordingly.

    Dispatches to:
    - :func:`parse_wifi_softap_xml` for ``WifiConfigStoreSoftAp.xml``.
    - :func:`parse_wifi_config_store_xml` for other ``*.xml`` files.
    - :func:`parse_wpa_supplicant_conf` for ``*.conf`` files.

    Returns an empty list for unknown extensions or parse failures.
    """
    name_lower = path.name.lower()
    if "softap" in name_lower:
        return parse_wifi_softap_xml(path)
    if name_lower.endswith(".conf") or "wpa_supplicant" in name_lower:
        return parse_wpa_supplicant_conf(path)
    if name_lower.endswith(".xml") or "wificonfigstore" in name_lower:
        return parse_wifi_config_store_xml(path)
    # Unknown — try each parser and return whichever yields results.
    conf_result = parse_wpa_supplicant_conf(path)
    if conf_result:
        return conf_result
    xml_result = parse_wifi_config_store_xml(path)
    if xml_result:
        return xml_result
    return parse_wifi_softap_xml(path)
