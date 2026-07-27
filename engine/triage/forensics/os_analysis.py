"""Advanced Android OS Forensics — system properties, services, intents, and broadcast
receiver analysis.

Analyses Android OS-level artifacts for forensic evidence:

  * **System properties** — full ``getprop`` output analysed for suspicious values,
    unusual build fingerprints, rooting indicators, and security patch levels.
  * **Android services** — running/registered services listed via ADB and analysed for
    rogue, persistence, or overlay services.
  * **Intent data** — intent dump files parsed to extract component targets, URIs,
    and extras; suspicious intents flagged.
  * **Broadcast receivers** — registered receivers catalogued and checked against known
    suspicious action filters (BOOT_COMPLETED, SEND_SMS, accessibility, etc.).
  * **HTML report** — structured dark-theme OS forensics report.

All functions are defensive: ADB failures return partial results with warnings rather
than raising exceptions.
"""

from __future__ import annotations

import html
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..adb import Adb

# ---------------------------------------------------------------------------
# Constants — suspicious patterns
# ---------------------------------------------------------------------------

_SUSPICIOUS_PROPS: Dict[str, str] = {
    "ro.debuggable": "1",
    "ro.secure": "0",
    "service.adb.root": "1",
    "persist.sys.usb.config": "adb",
}

_ROOTING_INDICATORS: list = [
    re.compile(r"\bmagisk\b", re.I),
    re.compile(r"\bsupersu\b", re.I),
    re.compile(r"\bxposed\b", re.I),
    re.compile(r"\bbusy\s*box\b", re.I),
    re.compile(r"\b(?:UNOFFICIAL|test-keys)\b"),
    re.compile(r"\beng\b"),          # engineering build
]

_SUSPICIOUS_SERVICE_PATTERNS: list = [
    re.compile(r"\b(?:frida|inject|hook|overlay|spyware|stalker|monitor)\b", re.I),
    re.compile(r"\b(?:accessibility|admin|device_admin)\b", re.I),
    re.compile(r"\b(?:keylogger|screen_capture|record)\b", re.I),
]

_SUSPICIOUS_INTENT_ACTIONS: frozenset = frozenset([
    "android.intent.action.SEND_SMS",
    "android.intent.action.CALL",
    "android.intent.action.VIEW",
    "android.provider.Telephony.SMS_RECEIVED",
    "android.intent.action.BOOT_COMPLETED",
    "android.intent.action.PACKAGE_ADDED",
    "com.android.phone.intent.CALL_OBSERVER",
])

_SUSPICIOUS_RECEIVER_ACTIONS: frozenset = frozenset([
    "android.intent.action.BOOT_COMPLETED",
    "android.intent.action.RECEIVE_BOOT_COMPLETED",
    "android.provider.Telephony.SMS_RECEIVED",
    "android.telephony.action.CARRIER_CONFIG_CHANGED",
    "android.intent.action.SEND_SMS",
    "android.intent.action.SEND_MULTIPLE",
    "android.intent.action.CALL",
    "android.accessibilityservice.AccessibilityService",
])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_getprop_output(text: str) -> Dict[str, str]:
    """Parse ``getprop`` output into a {key: value} dict."""
    props: Dict[str, str] = {}
    # Format: [key]: [value]
    pat = re.compile(r"^\[([^\]]+)\]:\s*\[([^\]]*)\]$")
    for line in text.splitlines():
        m = pat.match(line.strip())
        if m:
            props[m.group(1)] = m.group(2)
    return props


def _is_rooted(props: Dict[str, str]) -> tuple:
    """Check if the system properties indicate a rooted device.
    Returns (is_rooted: bool, indicators: list[str]).
    """
    indicators: List[str] = []
    for key, suspicious_val in _SUSPICIOUS_PROPS.items():
        actual = props.get(key, "")
        if actual == suspicious_val:
            indicators.append(f"{key}={actual}")
    all_vals = " ".join(props.values())
    for pat in _ROOTING_INDICATORS:
        m = pat.search(all_vals)
        if m:
            indicators.append(f"Rooting keyword: '{m.group()}' in property values")
    return bool(indicators), indicators


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def analyze_system_properties(device_info: Dict) -> Dict[str, Any]:
    """Analyse system properties from device_info dict.

    The ``device_info`` dict is expected to contain a ``raw_props`` key with the
    full output of ``getprop``, or individual property keys like ``ro.product.model``.

    Returns a dict with:
      * ``properties``       — parsed {key: value} dict
      * ``rooted``           — True if rooting indicators found
      * ``rooting_reasons``  — list of matching indicators
      * ``security_patch``   — security patch date string
      * ``build_type``       — "user" / "userdebug" / "eng"
      * ``suspicious``       — list of suspicious property findings
      * ``warnings``         — any issues
    """
    result: Dict[str, Any] = {
        "properties": {},
        "rooted": False,
        "rooting_reasons": [],
        "security_patch": "",
        "build_type": "",
        "suspicious": [],
        "warnings": [],
    }

    raw = device_info.get("raw_props", "")
    if raw:
        props = _parse_getprop_output(raw)
    else:
        # device_info itself may contain individual props
        props = {k: v for k, v in device_info.items() if isinstance(v, str)}

    result["properties"] = props
    rooted, reasons = _is_rooted(props)
    result["rooted"] = rooted
    result["rooting_reasons"] = reasons
    result["security_patch"] = props.get("ro.build.version.security_patch", "")
    result["build_type"] = props.get("ro.build.type", "")

    # Flag suspicious individual props
    for key, val in props.items():
        if key in _SUSPICIOUS_PROPS and val == _SUSPICIOUS_PROPS[key]:
            result["suspicious"].append({
                "property": key,
                "value": val,
                "reason": f"Property {key}={val} is a known security risk",
            })
        if "test-keys" in val or "UNOFFICIAL" in val:
            result["suspicious"].append({
                "property": key,
                "value": val,
                "reason": "Non-production build key detected",
            })

    return result


def analyze_android_services(adb: Adb) -> List[Dict[str, Any]]:
    """Analyse running Android services via ADB.

    Issues ``adb shell dumpsys activity services`` and parses the output.

    Returns a list of service dicts with:
      * ``name``        — service component name
      * ``pid``         — process ID (if available)
      * ``package``     — owning package
      * ``suspicious``  — True if flagged
      * ``reasons``     — list of flag reasons
    """
    services: List[Dict[str, Any]] = []
    try:
        res = adb.shell("dumpsys activity services")
        if not res.ok:
            return [{"warning": f"ADB command failed: {res.stderr}"}]
        text = res.stdout
    except Exception as exc:
        return [{"warning": f"ADB exception: {exc}"}]

    # Parse: ServiceRecord{…} in com.package/.ServiceClass
    svc_pat = re.compile(
        r"ServiceRecord\{[0-9a-f]+ u\d+ ([^\}]+)\}"
    )
    pid_pat = re.compile(r"app=ProcessRecord\{[0-9a-f]+ (\d+):[^\}]+\}")

    for line in text.splitlines():
        m = svc_pat.search(line)
        if not m:
            continue
        comp = m.group(1).strip()
        pid_m = pid_pat.search(line)
        pid = pid_m.group(1) if pid_m else ""
        package = comp.split("/")[0] if "/" in comp else comp
        suspicious = False
        reasons: List[str] = []
        for pat in _SUSPICIOUS_SERVICE_PATTERNS:
            if pat.search(comp):
                suspicious = True
                reasons.append(f"Service name matches pattern: {pat.pattern}")
        services.append({
            "name": comp,
            "pid": pid,
            "package": package,
            "suspicious": suspicious,
            "reasons": reasons,
        })

    return services or [{"info": "No services parsed from dumpsys output"}]


def analyze_intent_data(intent_path: Path) -> List[Dict[str, Any]]:
    """Analyse an intent dump file (text output of ``dumpsys`` or logcat with intent info).

    Returns a list of intent dicts with:
      * ``action``      — intent action string
      * ``component``   — target component
      * ``data_uri``    — data URI (if present)
      * ``extras``      — key/value pairs from intent extras
      * ``suspicious``  — True if action is flagged
    """
    intents: List[Dict[str, Any]] = []
    if not intent_path.exists():
        return [{"warning": "Intent dump file not found", "path": str(intent_path)}]
    try:
        text = intent_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [{"error": str(exc)}]

    action_pat = re.compile(r"(?:action|act)=([^\s,\}]+)", re.I)
    comp_pat = re.compile(r"(?:component|cmp)=([^\s,\}]+)", re.I)
    data_pat = re.compile(r"(?:data|dat|uri)=([^\s,\}]+)", re.I)
    extra_pat = re.compile(r"(?:extras|ext)=\{([^\}]*)\}", re.I)

    for line in text.splitlines():
        action_m = action_pat.search(line)
        if not action_m:
            continue
        action = action_m.group(1)
        comp_m = comp_pat.search(line)
        data_m = data_pat.search(line)
        extra_m = extra_pat.search(line)
        extras: Dict[str, str] = {}
        if extra_m:
            for kv in extra_m.group(1).split(","):
                parts = kv.strip().split("=", 1)
                if len(parts) == 2:
                    extras[parts[0].strip()] = parts[1].strip()
        suspicious = action in _SUSPICIOUS_INTENT_ACTIONS
        intents.append({
            "action": action,
            "component": comp_m.group(1) if comp_m else "",
            "data_uri": data_m.group(1) if data_m else "",
            "extras": extras,
            "suspicious": suspicious,
            "line": line[:300],
        })

    return intents[:5000]


def analyze_broadcast_receivers(adb: Adb) -> List[Dict[str, Any]]:
    """Analyse registered broadcast receivers via ADB.

    Issues ``adb shell dumpsys package`` and parses Receiver sections.

    Returns a list of receiver dicts with:
      * ``package``     — owning package
      * ``receiver``    — receiver class name
      * ``actions``     — list of registered intent-filter actions
      * ``suspicious``  — True if any action is flagged
      * ``reasons``     — list of flag reasons
    """
    receivers: List[Dict[str, Any]] = []
    try:
        res = adb.shell("dumpsys package")
        if not res.ok:
            return [{"warning": f"ADB command failed: {res.stderr}"}]
        text = res.stdout
    except Exception as exc:
        return [{"warning": f"ADB exception: {exc}"}]

    # Simple block parser
    current_pkg = ""
    pkg_pat = re.compile(r"^Package \[([^\]]+)\]")
    rcv_pat = re.compile(r"Receiver \{([^\}]+)\}")
    action_pat = re.compile(r"Action: \"([^\"]+)\"")

    current_receiver: Optional[Dict[str, Any]] = None

    for line in text.splitlines():
        pkg_m = pkg_pat.match(line.strip())
        if pkg_m:
            current_pkg = pkg_m.group(1)
        rcv_m = rcv_pat.search(line)
        if rcv_m:
            if current_receiver:
                receivers.append(current_receiver)
            current_receiver = {
                "package": current_pkg,
                "receiver": rcv_m.group(1).strip(),
                "actions": [],
                "suspicious": False,
                "reasons": [],
            }
        if current_receiver:
            act_m = action_pat.search(line)
            if act_m:
                action = act_m.group(1)
                current_receiver["actions"].append(action)
                if action in _SUSPICIOUS_RECEIVER_ACTIONS:
                    current_receiver["suspicious"] = True
                    current_receiver["reasons"].append(
                        f"Registered for sensitive action: {action}"
                    )

    if current_receiver:
        receivers.append(current_receiver)

    return receivers[:5000] or [{"info": "No receivers parsed from dumpsys output"}]


def generate_os_report(os_analysis: Dict) -> str:
    """Generate a styled HTML Android OS forensics report.

    Parameters
    ----------
    os_analysis:
        A dict with any of the following keys:
          * ``properties``  — output of analyze_system_properties()
          * ``services``    — output of analyze_android_services()
          * ``intents``     — output of analyze_intent_data()
          * ``receivers``   — output of analyze_broadcast_receivers()
    """
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    props_data: dict = os_analysis.get("properties", {})
    services: list = os_analysis.get("services", [])
    intents: list = os_analysis.get("intents", [])
    receivers: list = os_analysis.get("receivers", [])

    all_props: dict = props_data.get("properties", {})
    rooted: bool = props_data.get("rooted", False)
    rooting_reasons: list = props_data.get("rooting_reasons", [])
    suspicious_props: list = props_data.get("suspicious", [])
    security_patch: str = props_data.get("security_patch", "")
    build_type: str = props_data.get("build_type", "")

    suspicious_svc = [s for s in services if s.get("suspicious")]
    suspicious_int = [i for i in intents if i.get("suspicious")]
    suspicious_rcv = [r for r in receivers if r.get("suspicious")]

    def _badge(txt: str, col: str = "#6b7280") -> str:
        return (
            f'<span style="background:{col};color:#fff;padding:2px 7px;border-radius:9999px;'
            f'font-size:.75rem;font-weight:700;">{html.escape(str(txt))}</span>'
        )

    root_badge = _badge("ROOTED", "#ef4444") if rooted else _badge("NOT ROOTED", "#22c55e")
    build_col = {"user": "#22c55e", "userdebug": "#f59e0b", "eng": "#ef4444"}.get(build_type, "#6b7280")

    props_html = "".join(
        f'<div style="display:flex;justify-content:space-between;padding:.2rem 0;'
        f'border-bottom:1px solid #374151;">'
        f'<span style="color:#93c5fd;font-family:monospace;font-size:.82rem;">{html.escape(k)}</span>'
        f'<span style="color:#e5e7eb;font-size:.82rem;">{html.escape(v[:80])}</span>'
        f"</div>"
        for k, v in list(all_props.items())[:80]
    )

    svc_rows = "".join(
        f"<tr>"
        f'<td style="border:1px solid #374151;padding:5px;word-break:break-all;">{html.escape(s.get("name",""))}</td>'
        f'<td style="border:1px solid #374151;padding:5px;">{html.escape(s.get("package",""))}</td>'
        f'<td style="border:1px solid #374151;padding:5px;">{s.get("pid","")}</td>'
        f'<td style="border:1px solid #374151;padding:5px;">'
        f'{_badge("SUSPICIOUS","#ef4444") if s.get("suspicious") else _badge("OK","#22c55e")}'
        f"</td></tr>"
        for s in (suspicious_svc or services)[:50]
    )

    int_rows = "".join(
        f"<tr>"
        f'<td style="border:1px solid #374151;padding:5px;word-break:break-all;">{html.escape(i.get("action",""))}</td>'
        f'<td style="border:1px solid #374151;padding:5px;word-break:break-all;">{html.escape(i.get("component",""))}</td>'
        f'<td style="border:1px solid #374151;padding:5px;">'
        f'{_badge("SUSPICIOUS","#ef4444") if i.get("suspicious") else _badge("OK","#22c55e")}'
        f"</td></tr>"
        for i in (suspicious_int or intents)[:50]
    )

    rcv_rows = "".join(
        f"<tr>"
        f'<td style="border:1px solid #374151;padding:5px;">{html.escape(r.get("package",""))}</td>'
        f'<td style="border:1px solid #374151;padding:5px;word-break:break-all;">{html.escape(r.get("receiver",""))}</td>'
        f'<td style="border:1px solid #374151;padding:5px;font-size:.78rem;">{html.escape(", ".join(r.get("actions",[])[:5]))}</td>'
        f'<td style="border:1px solid #374151;padding:5px;">'
        f'{_badge("SUSPICIOUS","#ef4444") if r.get("suspicious") else _badge("OK","#22c55e")}'
        f"</td></tr>"
        for r in (suspicious_rcv or receivers)[:50]
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<title>Android OS Forensics Report</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#111827;color:#e5e7eb;font-family:'Segoe UI',system-ui,sans-serif;line-height:1.6;padding:2rem}}
h1{{font-size:1.75rem;font-weight:800;background:linear-gradient(90deg,#22c55e,#06b6d4);
    -webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-bottom:.5rem}}
h2{{font-size:1.15rem;font-weight:700;color:#c7d2fe;margin:1.5rem 0 .5rem;
    border-bottom:1px solid #374151;padding-bottom:.25rem}}
.meta{{color:#6b7280;font-size:.85rem;margin-bottom:1.5rem}}
.card{{background:#1f2937;border:1px solid #374151;border-radius:.75rem;padding:1rem 1.25rem;margin-bottom:1rem}}
.stat-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.75rem;margin-bottom:1.5rem}}
.stat{{background:#1f2937;border:1px solid #374151;border-radius:.5rem;padding:.75rem 1rem;text-align:center}}
.stat-val{{font-size:2rem;font-weight:800;color:#818cf8}}
.stat-lbl{{font-size:.8rem;color:#6b7280;text-transform:uppercase;letter-spacing:.05em}}
table{{width:100%;border-collapse:collapse;font-size:.82rem}}
th{{border:1px solid #374151;padding:5px;background:#1f2937;color:#9ca3af;text-align:left}}
</style>
</head>
<body>
<h1>🤖 Android OS Forensics Report</h1>
<p class="meta">Generated: {ts}</p>
<div class="stat-grid">
  <div class="stat"><div class="stat-val">{len(all_props)}</div><div class="stat-lbl">System Props</div></div>
  <div class="stat"><div class="stat-val">{len(services)}</div><div class="stat-lbl">Services</div></div>
  <div class="stat"><div class="stat-val">{len(suspicious_svc)}</div><div class="stat-lbl">Suspicious Services</div></div>
  <div class="stat"><div class="stat-val">{len(intents)}</div><div class="stat-lbl">Intents</div></div>
  <div class="stat"><div class="stat-val">{len(suspicious_int)}</div><div class="stat-lbl">Suspicious Intents</div></div>
  <div class="stat"><div class="stat-val">{len(receivers)}</div><div class="stat-lbl">Receivers</div></div>
  <div class="stat"><div class="stat-val">{len(suspicious_rcv)}</div><div class="stat-lbl">Suspicious Receivers</div></div>
</div>

<h2>⚙ System Properties {root_badge}</h2>
<div class="card">
<p style="margin-bottom:.5rem;">Build Type: {_badge(build_type or "unknown", build_col)} | Security Patch: <strong>{html.escape(security_patch or "unknown")}</strong></p>
{"<p style='color:#ef4444;margin-bottom:.5rem;'>Rooting indicators: " + "; ".join(html.escape(r) for r in rooting_reasons) + "</p>" if rooting_reasons else ""}
{"".join(f'<div style="background:#431407;border:1px solid #b45309;border-radius:.5rem;padding:.4rem .6rem;color:#fbbf24;font-size:.82rem;margin:.2rem 0;">⚠ {html.escape(sp["property"])}={html.escape(sp["value"])}: {html.escape(sp["reason"])}</div>' for sp in suspicious_props)}
<details><summary style="cursor:pointer;color:#93c5fd;font-weight:600;margin-top:.5rem;">All Properties ({len(all_props)})</summary>
<div style="margin-top:.5rem;">{props_html}</div>
</details>
</div>

<h2>⚡ Services ({len(services)} total, {len(suspicious_svc)} suspicious)</h2>
<div class="card">
<table>
<thead><tr>
  <th>Service Name</th><th>Package</th><th>PID</th><th>Status</th>
</tr></thead>
<tbody>{svc_rows or "<tr><td colspan='4' style='padding:6px;color:#6b7280;text-align:center;'>No services found.</td></tr>"}</tbody>
</table>
</div>

<h2>📨 Intents ({len(intents)} total, {len(suspicious_int)} suspicious)</h2>
<div class="card">
<table>
<thead><tr>
  <th>Action</th><th>Component</th><th>Status</th>
</tr></thead>
<tbody>{int_rows or "<tr><td colspan='3' style='padding:6px;color:#6b7280;text-align:center;'>No intents found.</td></tr>"}</tbody>
</table>
</div>

<h2>📡 Broadcast Receivers ({len(receivers)} total, {len(suspicious_rcv)} suspicious)</h2>
<div class="card">
<table>
<thead><tr>
  <th>Package</th><th>Receiver</th><th>Actions</th><th>Status</th>
</tr></thead>
<tbody>{rcv_rows or "<tr><td colspan='4' style='padding:6px;color:#6b7280;text-align:center;'>No receivers found.</td></tr>"}</tbody>
</table>
</div>
</body>
</html>"""
