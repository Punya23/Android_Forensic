"""MODULE 5: Hotspot Indicators (Non-root Tier 0)

Detects whether a device HOSTED or CONNECTED to a mobile hotspot.

This module analyzes multiple data sources to detect hotspot usage:
1. wifi_dumpsys: Check for "SoftAp" or "hostapd" to detect hosted hotspots
2. wifi_config: Check for SSIDs containing "AndroidAP" or "Hotspot" (connected to hotspot)
3. netstats: Check for traffic over hotspot SSIDs

Caveats:
- Detects whether a hotspot was active at capture time, or if traffic flowed 
  over a hotspot SSID in a past hour
- Does NOT prove the user intended to share data
- Does NOT log client MAC addresses
"""

from __future__ import annotations

from typing import Any


def analyze_hotspot_indicators(
    wifi_dumpsys: str,
    netstats: str,
    wifi_config: list[dict[str, Any]]
) -> dict[str, Any]:
    """Analyze hotspot indicators from multiple sources.
    
    Args:
        wifi_dumpsys: Raw dumpsys wifi output text
        netstats: Raw dumpsys netstats output text
        wifi_config: List of saved Wi-Fi network dicts with 'ssid' field
        
    Returns:
        Dict with fields:
        - hosted_indicator: bool - True if device hosted a hotspot
        - connected_indicator: bool - True if device connected to a hotspot
        - caveats: list[str] - Important limitations and disclaimers
        - details: dict - Additional context about detections
    """
    result: dict[str, Any] = {
        "hosted_indicator": False,
        "connected_indicator": False,
        "caveats": [],
        "details": {
            "hosted_evidence": [],
            "connected_evidence": [],
            "traffic_evidence": []
        }
    }
    
    # Standard caveats - ALWAYS include these
    result["caveats"].append(
        "This detects whether a hotspot was active at capture time, or if traffic "
        "flowed over a hotspot SSID in a past hour. It DOES NOT prove the user "
        "intended to share data, nor does it log client MAC addresses."
    )
    
    # Check if device HOSTED a hotspot (SoftAp / hostapd in dumpsys)
    if wifi_dumpsys:
        wifi_dumpsys_lower = wifi_dumpsys.lower()
        if "softap" in wifi_dumpsys_lower:
            result["hosted_indicator"] = True
            result["details"]["hosted_evidence"].append(
                "Found 'SoftAp' in dumpsys wifi output - indicates device hosted a hotspot"
            )
        if "hostapd" in wifi_dumpsys_lower:
            result["hosted_indicator"] = True
            result["details"]["hosted_evidence"].append(
                "Found 'hostapd' in dumpsys wifi output - hostapd is the AP daemon"
            )
    
    # Check if device CONNECTED to a hotspot (AndroidAP / Hotspot in SSID)
    hotspot_keywords = ["androidap", "hotspot"]
    for network in wifi_config:
        if not isinstance(network, dict):
            continue
        ssid = str(network.get("ssid", "")).lower()
        if not ssid:
            continue
        for keyword in hotspot_keywords:
            if keyword in ssid:
                result["connected_indicator"] = True
                result["details"]["connected_evidence"].append(
                    f"Found hotspot keyword '{keyword}' in saved SSID: {network.get('ssid')}"
                )
                break
    
    # Check for current consumption (traffic over hotspot SSIDs in netstats)
    if netstats:
        # Look for networkId="..." or wifiNetworkKey="..." entries
        import re
        # Match both formats: networkId="X" and wifiNetworkKey="X"
        network_id_pattern = r'(?:networkId|wifiNetworkKey)="([^"]*)"'
        matches = re.findall(network_id_pattern, netstats)
        
        for ssid in matches:
            ssid_lower = ssid.lower()
            for keyword in hotspot_keywords:
                if keyword in ssid_lower:
                    # Check if there's non-zero traffic in subsequent lines
                    # Look for pattern: st=... rb=... tb=...
                    # Find the section containing this SSID
                    ssid_idx = netstats.find(f'"{ssid}"')
                    if ssid_idx >= 0:
                        # Look ahead for traffic buckets (next 2000 chars)
                        section = netstats[ssid_idx:ssid_idx + 2000]
                        bucket_pattern = r'st=(\d+)\s+rb=(\d+)\s+.*?tb=(\d+)'
                        buckets = re.findall(bucket_pattern, section)
                        for st, rb, tb in buckets:
                            rx_bytes = int(rb)
                            tx_bytes = int(tb)
                            if rx_bytes > 0 or tx_bytes > 0:
                                result["details"]["traffic_evidence"].append(
                                    f"Non-zero traffic over hotspot SSID '{ssid}': "
                                    f"rx={rx_bytes} bytes, tx={tx_bytes} bytes"
                                )
                    break
    
    # Add contextual caveats based on findings
    if result["hosted_indicator"]:
        result["caveats"].append(
            "Hosted hotspot indicator detected. This means the device's tethering "
            "or mobile hotspot feature was active at some point. It does not identify "
            "which devices connected or what data was transferred."
        )
    
    if result["connected_indicator"]:
        result["caveats"].append(
            "Connected hotspot indicator detected. The device has saved credentials "
            "for a network with hotspot-like naming. This suggests the device connected "
            "to another device's mobile hotspot, but does not establish when or where."
        )
    
    if result["details"]["traffic_evidence"]:
        result["caveats"].append(
            "Traffic evidence over hotspot SSID detected in netstats. This proves "
            "data was transferred, but netstats uses hour-long buckets and cannot "
            "establish precise connection times or durations."
        )
    
    if not result["hosted_indicator"] and not result["connected_indicator"]:
        result["caveats"].append(
            "No hotspot indicators detected. This does not prove hotspot usage never "
            "occurred - only that the standard indicators are absent from this capture."
        )
    
    return result
