"""Hotspot / tethering indicators (Non-root Tier 0).

Answers two separate questions from the volatile Tier-0 dumps:

* did this device **host** a hotspot (tethering, SoftAp), and
* is there evidence it **joined** somebody else's hotspot?

The two are not symmetrical.  The first is a state Android reports directly and
can be read as fact.  The second has no reliable on-device marker at Tier 0 — a
network called ``AndroidAP1234`` is *probably* a phone hotspot, but naming is a
convention, not a protocol, and a home router can be called anything.  This
module therefore reports the second as a **naming heuristic**, never as a
determination.

Every result field is tri-state.  ``None`` means "the dump did not say", which
is a different finding from ``False`` ("the dump said no") and is the honest
answer on a build whose ``dumpsys wifi`` omits the SoftAp block entirely.

Why not a substring search: ``dumpsys wifi`` prints its ``SoftApManager`` state
machine on essentially every modern device, hotspot or not.  Testing for the
*word* "SoftAp" therefore returns True for every phone ever seized.  Only an
explicit state line counts here.
"""

from __future__ import annotations

import re
from typing import Any, Optional


#: Lines that positively state the AP is up. ``WIFI_AP_STATE_ENABLED`` is 13 in
#: ``WifiManager``; the state machine reports ``TetheredState``/``StartedState``.
_HOSTED_ACTIVE_RE = [
    re.compile(r"SoftApManager[^\n]*current state:\s*(?:Started|Tethered)", re.I),
    re.compile(r"WIFI_AP_STATE_ENABLED", re.I),
    re.compile(r"\bmWifiApState\s*[:=]\s*13\b"),
    re.compile(r"SoftAp\s*state\s*[:=]\s*ENABLED", re.I),
    re.compile(r"ap_interface_name\s*[:=]\s*\S+", re.I),
]

#: Lines that positively state the AP is down.
_HOSTED_INACTIVE_RE = [
    re.compile(r"SoftApManager[^\n]*current state:\s*Idle", re.I),
    re.compile(r"WIFI_AP_STATE_DISABLED", re.I),
    re.compile(r"\bmWifiApState\s*[:=]\s*11\b"),
    re.compile(r"SoftAp\s*state\s*[:=]\s*DISABLED", re.I),
]

#: ``dumpsys connectivity`` tethering block — a non-empty tethered-interface list
#: is independent corroboration that something was being shared.
_TETHER_ACTIVE_RE = [
    re.compile(r"Tethered\s+ifaces?\s*[:=]\s*\[\s*(\w[^\]]*)\]", re.I),
    re.compile(r"mCurrentUpstreamIface\s*[:=]\s*(\w+)", re.I),
]

#: SSID naming conventions used by phone hotspots by default.
_HOTSPOT_SSID_HINTS = ("androidap", "hotspot", "iphone", "galaxy", "tether", "mifi")

_NETWORK_ID_RE = re.compile(r'(?:networkId|wifiNetworkKey)="([^"]*)"')
_BUCKET_RE = re.compile(r"st=(\d+)\s+rb=(\d+)\s+.*?tb=(\d+)")

CAVEAT_SCOPE = (
    "This detects whether a hotspot was active at capture time, or if traffic "
    "flowed over a hotspot SSID in a past hour. It DOES NOT prove the user "
    "intended to share data, nor does it log client MAC addresses."
)


def _tri_state_hosted(text: str) -> tuple[Optional[bool], list[str]]:
    """Read the SoftAp state out of ``dumpsys wifi``, or return unknown.

    Active evidence wins over inactive: a dump can carry a stale ``IdleState``
    line from an earlier state-machine transition alongside a current
    ``TetheredState``.  Where nothing matches at all the answer is ``None`` —
    "this build did not report an AP state" — and never ``False``.
    """
    if not text:
        return None, []
    evidence: list[str] = []
    for pattern in _HOSTED_ACTIVE_RE:
        match = pattern.search(text)
        if match:
            evidence.append(f"dumpsys wifi: {match.group(0).strip()}")
    if evidence:
        return True, evidence
    for pattern in _HOSTED_INACTIVE_RE:
        match = pattern.search(text)
        if match:
            evidence.append(f"dumpsys wifi: {match.group(0).strip()}")
    if evidence:
        return False, evidence
    return None, []


def _tether_evidence(text: str) -> list[str]:
    """Active tethered interfaces from ``dumpsys connectivity``, if any."""
    out: list[str] = []
    if not text:
        return out
    for pattern in _TETHER_ACTIVE_RE:
        for match in pattern.finditer(text):
            value = (match.group(1) or "").strip()
            if value and value.lower() not in ("null", "none", "[]"):
                out.append(f"dumpsys connectivity: {match.group(0).strip()}")
    return out


def analyze_hotspot_indicators(
    wifi_dumpsys: str,
    netstats: str,
    wifi_config: list[dict[str, Any]],
    connectivity: str = "",
    softap_config: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Analyse hotspot indicators across the Tier-0 dumps.

    Args:
        wifi_dumpsys: Raw ``dumpsys wifi`` output.
        netstats: Raw ``dumpsys netstats --full --uid`` output.
        wifi_config: Saved/known networks, each a dict with an ``ssid`` key.
        connectivity: Raw ``dumpsys connectivity`` output (tethering block).
        softap_config: Parsed ``WifiConfigStoreSoftAp.xml``, when a root pull
            produced one. Its presence proves the hotspot was *configured*,
            which is weaker than active but stronger than a guess at the name.

    Returns:
        Dict with:
        - ``hosted_indicator``: True / False / None (unknown — not reported)
        - ``connected_indicator``: True / False / None. True only ever means
          "a known network is *named* like a hotspot", never a determination.
        - ``hosted_configured``: bool — a SoftAp config exists on the device.
        - ``details``: evidence lists behind each verdict.
        - ``caveats``: limitations, always populated.
    """
    result: dict[str, Any] = {
        "hosted_indicator": None,
        "connected_indicator": None,
        "hosted_configured": False,
        "caveats": [CAVEAT_SCOPE],
        "details": {
            "hosted_evidence": [],
            "connected_evidence": [],
            "traffic_evidence": [],
        },
    }

    # --- hosted: an explicit state line, not the mere word "SoftAp" ---------
    hosted, hosted_evidence = _tri_state_hosted(wifi_dumpsys)
    result["hosted_indicator"] = hosted
    result["details"]["hosted_evidence"].extend(hosted_evidence)

    tether_evidence = _tether_evidence(connectivity)
    if tether_evidence:
        result["details"]["hosted_evidence"].extend(tether_evidence)
        result["hosted_indicator"] = True

    if softap_config:
        result["hosted_configured"] = True
        ssid = softap_config.get("ssid") or softap_config.get("SSID") or ""
        result["details"]["hosted_evidence"].append(
            f"SoftAp configuration present on device"
            + (f" (SSID '{ssid}')" if ssid else "")
        )

    # --- connected: a naming heuristic, and labelled as one -----------------
    named: list[str] = []
    for network in wifi_config or []:
        if not isinstance(network, dict):
            continue
        ssid = str(network.get("ssid", ""))
        low = ssid.lower()
        if not low:
            continue
        for hint in _HOTSPOT_SSID_HINTS:
            if hint in low:
                named.append(ssid)
                result["details"]["connected_evidence"].append(
                    f"Known network '{ssid}' matches the hotspot naming convention "
                    f"'{hint}'. This is a NAME match, not a determination that the "
                    f"network was a phone hotspot."
                )
                break
    if wifi_config:
        result["connected_indicator"] = bool(named)

    # --- traffic over hotspot-named SSIDs ----------------------------------
    if netstats:
        for ssid in _NETWORK_ID_RE.findall(netstats):
            low = ssid.lower()
            if not any(hint in low for hint in _HOTSPOT_SSID_HINTS):
                continue
            idx = netstats.find(f'"{ssid}"')
            if idx < 0:
                continue
            section = netstats[idx : idx + 2000]
            for _st, rb, tb in _BUCKET_RE.findall(section):
                rx, tx = int(rb), int(tb)
                if rx > 0 or tx > 0:
                    result["details"]["traffic_evidence"].append(
                        f"Non-zero traffic over hotspot-named SSID '{ssid}': "
                        f"rx={rx} bytes, tx={tx} bytes"
                    )

    # --- caveats, keyed to what was actually found -------------------------
    if result["hosted_indicator"] is True:
        result["caveats"].append(
            "The device's tethering / mobile hotspot was active at capture time. "
            "This does not identify which devices connected or what data moved."
        )
    elif result["hosted_indicator"] is False:
        result["caveats"].append(
            "The device reported its hotspot as OFF at capture time. This is a "
            "reading of the CURRENT state only — Android keeps no hotspot history, "
            "so earlier hotspot use is neither shown nor excluded."
        )
    else:
        result["caveats"].append(
            "No SoftAp state was reported by this build's dumpsys output. Hotspot "
            "state is UNKNOWN — this is not a finding that the hotspot was off."
        )

    if result["hosted_configured"]:
        result["caveats"].append(
            "A hotspot configuration (SSID and passphrase) exists on the device. "
            "That proves it was set up, not that it was ever switched on, and "
            "carries no date."
        )

    if named:
        result["caveats"].append(
            "One or more known networks are NAMED like a phone hotspot "
            f"({', '.join(sorted(set(named)))}). SSIDs are freely chosen: a home "
            "router can carry these names and a hotspot can be renamed to avoid "
            "them. Treat as a lead, not a conclusion."
        )
    elif wifi_config:
        result["caveats"].append(
            "No known network is named like a hotspot. Since the check is only a "
            "naming convention, this does not exclude hotspot use."
        )
    else:
        result["caveats"].append(
            "No saved-network list was available, so the naming check could not "
            "run at all. On Android 10+ this list is unreadable without root."
        )

    if result["details"]["traffic_evidence"]:
        result["caveats"].append(
            "Traffic over a hotspot-named SSID appears in netstats. This proves data "
            "moved, but netstats uses hour-long buckets and cannot establish precise "
            "connection times or durations."
        )

    return result
