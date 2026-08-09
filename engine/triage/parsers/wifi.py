"""Wi-Fi credential parser (Root Tier 2).

MODULE 2: Wi-Fi Passwords (Root Tier 2)

Supports two Android Wi-Fi config file formats:

* **``wpa_supplicant.conf``** (Android ≤ 8.0)  — plaintext wpa_supplicant
  configuration; one ``network={...}`` stanza per saved network.
* **``WifiConfigStore.xml``** (Android ≥ 9.0)  — XML produced by the
  ``WifiConfigManager``; one ``<Network>`` element per saved network.

The dispatcher :func:`parse_wifi_config` detects the format from the file
name (or path suffix) and calls the appropriate sub-parser — no hardcoded
Android version checks.  All parsing is best-effort: a malformed entry is
skipped rather than aborting the whole file.

Both files live in ``/data/misc/wifi/`` and require **root access** (Tier 2) to read.
This module only parses locally-staged copies; pulling from the device is the
pipeline's responsibility.

Returns WifiNetwork(ssid, password, security, source_file) dataclasses.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from ..models import WifiNetwork
from ..config import Confidence


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

        ssid = ""
        password = ""
        key_mgmt_raw = ""

        for string_el in wifi_cfg.iter("string"):
            name = (string_el.get("name") or "").strip()
            val = _unquote((string_el.text or "").strip())
            if name == "SSID":
                # SSID is stored as a quoted string: "\"MyNetwork\""
                ssid = val.strip('"')
            elif name == "PreSharedKey":
                password = val.strip('"')
            elif name == "AllowedKeyMgmt":
                key_mgmt_raw = val

        if not ssid:
            # Try int/byte SSID representations (rare but seen on some OEMs)
            for bytes_el in wifi_cfg.iter("byte-array"):
                if (bytes_el.get("name") or "").lower() == "ssid":
                    try:
                        hex_val = bytes_el.get("value", "")
                        ssid = bytes.fromhex(hex_val).decode("utf-8", errors="replace")
                    except Exception:
                        pass
                    break

        if not ssid:
            continue

        # Security label from AllowedKeyMgmt bitmask name.
        key_mgmt_upper = key_mgmt_raw.upper()
        if "WPA_PSK" in key_mgmt_upper or "WPA2_PSK" in key_mgmt_upper:
            security = "WPA"
        elif "WPA3_SAE" in key_mgmt_upper or "SAE" in key_mgmt_upper:
            security = "WPA3"
        elif "WEP" in key_mgmt_upper:
            security = "WEP"
        elif "NONE" in key_mgmt_upper or not key_mgmt_raw:
            security = "OPEN"
        else:
            security = key_mgmt_raw

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
# Dispatcher
# ---------------------------------------------------------------------------


def parse_wifi_config(path: Path) -> list[WifiNetwork]:
    """Auto-detect Wi-Fi config format and parse accordingly.

    Dispatches to:
    - :func:`parse_wpa_supplicant_conf` for ``*.conf`` files.
    - :func:`parse_wifi_config_store_xml` for ``*.xml`` files.

    Returns an empty list for unknown extensions or parse failures.
    """
    name_lower = path.name.lower()
    if name_lower.endswith(".conf") or "wpa_supplicant" in name_lower:
        return parse_wpa_supplicant_conf(path)
    elif name_lower.endswith(".xml") or "wificonfigstore" in name_lower:
        return parse_wifi_config_store_xml(path)
    # Unknown — try both parsers and return whichever yields results.
    conf_result = parse_wpa_supplicant_conf(path)
    if conf_result:
        return conf_result
    return parse_wifi_config_store_xml(path)
