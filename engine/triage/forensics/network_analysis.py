"""Advanced Network Forensics — PCAP, connection logs, DNS cache, proxy/VPN analysis.

Analyses network data for forensic evidence:

  * **PCAP parsing** — packet extraction, protocol identification, conversation
    reconstruction.
  * **Connection history** — parses log files for connection records and flags unusual
    destinations.
  * **DNS cache analysis** — extracts domains and timelines of DNS queries; flags
    suspicious or known-bad domains.
  * **Proxy / VPN analysis** — identifies proxy settings, VPN usage indicators.
  * **HTML report generation** — structured dark-theme network forensics report.

All functions are defensive: missing optional dependencies (dpkt, scapy) are handled
gracefully with partial results and warnings. Streaming PCAP parsing keeps memory use low.
"""

from __future__ import annotations

import concurrent.futures
import html
import re
import socket
import struct
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Optional dependencies
# ---------------------------------------------------------------------------

try:
    import dpkt  # type: ignore
    _DPKT_AVAILABLE = True
except ImportError:
    _DPKT_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PCAP_MAGIC_LE = 0xA1B2C3D4
_PCAP_MAGIC_BE = 0xD4C3B2A1
_PCAP_MAGIC_NS_LE = 0xA1B23C4D

# Known suspicious TLDs, domains, and IP ranges
_SUSPICIOUS_DOMAINS: frozenset = frozenset([
    ".onion", ".i2p", "no-ip.com", "dyndns.org", "freedns.afraid.org",
    "duckdns.org", "hopto.org", "ddns.net",
])
_SUSPICIOUS_PORTS: set = {4444, 1337, 31337, 8888, 9999, 1234, 6667, 6668, 6669}
_PRIVATE_RANGES: list = [
    re.compile(r"^10\."),
    re.compile(r"^192\.168\."),
    re.compile(r"^172\.(1[6-9]|2[0-9]|3[01])\."),
    re.compile(r"^127\."),
    re.compile(r"^::1$"),
]

# VPN / proxy keyword patterns in config/log files
_VPN_KEYWORDS: list = [
    re.compile(r"\b(?:vpn|wireguard|openvpn|ipsec|l2tp|pptp|ikev[12])\b", re.I),
    re.compile(r"\b(?:nordvpn|expressvpn|mullvad|protonvpn|surfshark)\b", re.I),
    re.compile(r"\b(?:socks5?|http_proxy|https_proxy|all_proxy)\b", re.I),
]

_IP_PAT = re.compile(
    r"\b((?:\d{1,3}\.){3}\d{1,3}|[0-9a-f:]{7,39})\b"
)
_PORT_PAT = re.compile(r":(\d{2,5})\b")
_DOMAIN_PAT = re.compile(
    r"\b([a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z]{2,})+)\b"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_private(ip: str) -> bool:
    return any(p.match(ip) for p in _PRIVATE_RANGES)


def _flag_suspicious_domain(domain: str) -> bool:
    low = domain.lower()
    return any(low.endswith(s) or s in low for s in _SUSPICIOUS_DOMAINS)


def _try_reverse_dns(ip: str) -> str:
    try:
        return socket.gethostbyaddr(ip)[0]
    except Exception:
        return ""


def _parse_pcap_raw(data: bytes) -> Optional[Dict[str, Any]]:
    """Parse raw PCAP bytes without dpkt. Returns header dict or None."""
    if len(data) < 24:
        return None
    magic = struct.unpack("<I", data[:4])[0]
    if magic == _PCAP_MAGIC_LE:
        endian = "<"
    elif magic == _PCAP_MAGIC_BE:
        endian = ">"
    elif magic == _PCAP_MAGIC_NS_LE:
        endian = "<"
    else:
        return None
    ver_maj, ver_min, thiszone, sigfigs, snaplen, network = struct.unpack(
        f"{endian}HHIIII", data[4:24]
    )
    return {
        "version": f"{ver_maj}.{ver_min}",
        "snaplen": snaplen,
        "network": network,
        "endian": endian,
        "header_bytes": 24,
    }


def _extract_packets_raw(data: bytes, endian: str, header_size: int) -> List[Dict[str, Any]]:
    """Minimal PCAP packet extractor without dpkt."""
    packets: List[Dict[str, Any]] = []
    off = header_size
    while off + 16 <= len(data):
        ts_sec = struct.unpack_from(f"{endian}I", data, off)[0]
        ts_usec = struct.unpack_from(f"{endian}I", data, off + 4)[0]
        incl_len = struct.unpack_from(f"{endian}I", data, off + 8)[0]
        orig_len = struct.unpack_from(f"{endian}I", data, off + 12)[0]
        off += 16
        if incl_len > 65535 or off + incl_len > len(data):
            break
        payload = data[off : off + incl_len]
        ts = ts_sec + ts_usec / 1_000_000
        packets.append({
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)),
            "captured_bytes": incl_len,
            "original_bytes": orig_len,
            "payload_hex": payload[:32].hex(),
        })
        off += incl_len
    return packets


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def analyze_pcap(pcap_path: Path) -> Dict[str, Any]:
    """Parse a PCAP file, extract packets, identify protocols, reconstruct
    conversations.

    Returns a dict with:
      * ``file``            — path to PCAP
      * ``packet_count``    — total packets parsed
      * ``protocols``       — protocol occurrence counts
      * ``conversations``   — reconstructed TCP/UDP flows
      * ``ips``             — unique IPs seen
      * ``suspicious_ips``  — IPs flagged as suspicious
      * ``warnings``        — any issues
    """
    result: Dict[str, Any] = {
        "file": str(pcap_path),
        "packet_count": 0,
        "protocols": {},
        "conversations": [],
        "ips": [],
        "suspicious_ips": [],
        "warnings": [],
    }
    if not pcap_path.exists():
        result["warnings"].append("File not found")
        return result

    try:
        data = pcap_path.read_bytes()
    except OSError as exc:
        result["warnings"].append(str(exc))
        return result

    if _DPKT_AVAILABLE:
        try:
            flows: Dict[tuple, Dict[str, Any]] = {}
            protocols: Dict[str, int] = {}
            ips: set = set()
            pkt_count = 0

            f = pcap_path.open("rb")
            try:
                pcap_reader = dpkt.pcap.Reader(f)
                for ts, buf in pcap_reader:
                    pkt_count += 1
                    try:
                        eth = dpkt.ethernet.Ethernet(buf)
                        protocols["ethernet"] = protocols.get("ethernet", 0) + 1
                        if isinstance(eth.data, dpkt.ip.IP):
                            ip = eth.data
                            src = socket.inet_ntoa(ip.src)
                            dst = socket.inet_ntoa(ip.dst)
                            ips.add(src)
                            ips.add(dst)
                            proto = "tcp" if isinstance(ip.data, dpkt.tcp.TCP) else (
                                "udp" if isinstance(ip.data, dpkt.udp.UDP) else f"ip_{ip.p}"
                            )
                            protocols[proto] = protocols.get(proto, 0) + 1
                            if isinstance(ip.data, (dpkt.tcp.TCP, dpkt.udp.UDP)):
                                transport = ip.data
                                key = (src, transport.sport, dst, transport.dport, proto)
                                if key not in flows:
                                    flows[key] = {
                                        "src": src, "sport": transport.sport,
                                        "dst": dst, "dport": transport.dport,
                                        "protocol": proto, "packets": 0, "bytes": 0,
                                        "start_ts": time.strftime(
                                            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)
                                        ),
                                    }
                                flows[key]["packets"] += 1
                                flows[key]["bytes"] += len(buf)
                        elif isinstance(eth.data, dpkt.ip6.IP6):
                            protocols["ipv6"] = protocols.get("ipv6", 0) + 1
                    except Exception:
                        pass
            finally:
                f.close()

            result["packet_count"] = pkt_count
            result["protocols"] = protocols
            result["conversations"] = list(flows.values())[:200]
            result["ips"] = list(ips)[:500]
            result["suspicious_ips"] = [
                ip for ip in ips
                if not _is_private(ip) and any(
                    flows.get(k, {}).get("dport") in _SUSPICIOUS_PORTS
                    for k in flows if k[0] == ip or k[2] == ip
                )
            ][:50]
            return result
        except Exception as exc:
            result["warnings"].append(f"dpkt parsing failed: {exc}; falling back to raw parser")

    # Raw fallback
    hdr = _parse_pcap_raw(data)
    if not hdr:
        result["warnings"].append("Not a valid PCAP file")
        return result
    packets = _extract_packets_raw(data, hdr["endian"], hdr["header_bytes"])
    result["packet_count"] = len(packets)
    result["warnings"].append("dpkt not available; limited packet analysis (no protocol decode)")
    return result


def analyze_network_connections(logs_path: Path) -> List[Dict[str, Any]]:
    """Analyse a network connection log file.

    Expects any text log containing IP addresses, ports, and timestamps.
    Returns a list of connection dicts with:
      * ``src``, ``dst``, ``port``      — endpoint info
      * ``timestamp``                   — ISO timestamp (if parseable)
      * ``suspicious``                  — True if flagged
      * ``reason``                      — reason for flag
    """
    connections: List[Dict[str, Any]] = []
    if not logs_path.exists():
        return [{"warning": "Log file not found", "path": str(logs_path)}]
    try:
        text = logs_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [{"error": str(exc)}]

    # ISO timestamp pattern
    ts_pat = re.compile(
        r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)"
    )
    for line in text.splitlines():
        ips = _IP_PAT.findall(line)
        ports = [int(p) for p in _PORT_PAT.findall(line) if p.isdigit() and int(p) < 65536]
        if not ips:
            continue
        ts_match = ts_pat.search(line)
        timestamp = ts_match.group(1) if ts_match else ""
        src = ips[0] if ips else ""
        dst = ips[1] if len(ips) > 1 else ""
        port = ports[0] if ports else None
        suspicious = False
        reason = ""
        if port and port in _SUSPICIOUS_PORTS:
            suspicious = True
            reason = f"Suspicious port {port}"
        if dst and not _is_private(dst) and not suspicious:
            pass  # external — note but don't flag automatically
        connections.append({
            "src": src, "dst": dst, "port": port,
            "timestamp": timestamp,
            "line": line[:200],
            "suspicious": suspicious,
            "reason": reason,
        })
    return connections[:10000]


def analyze_dns_cache(dns_path: Path) -> List[Dict[str, Any]]:
    """Analyse a DNS cache dump or log file.

    Extracts domains, constructs a timeline, and flags suspicious domains.

    Returns list of dicts with:
      * ``domain``      — resolved domain name
      * ``ip``          — resolved IP (if available)
      * ``timestamp``   — ISO timestamp (if available)
      * ``suspicious``  — True if domain matches suspicious pattern
      * ``reason``      — reason for flag
    """
    records: List[Dict[str, Any]] = []
    if not dns_path.exists():
        return [{"warning": "DNS cache file not found", "path": str(dns_path)}]
    try:
        text = dns_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [{"error": str(exc)}]

    ts_pat = re.compile(
        r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})"
    )
    for line in text.splitlines():
        domains = _DOMAIN_PAT.findall(line)
        ips = _IP_PAT.findall(line)
        ts_match = ts_pat.search(line)
        timestamp = ts_match.group(1) if ts_match else ""
        for domain in domains:
            suspicious = _flag_suspicious_domain(domain)
            reason = ""
            if suspicious:
                matched = [s for s in _SUSPICIOUS_DOMAINS if domain.lower().endswith(s) or s in domain.lower()]
                reason = f"Matches suspicious pattern: {matched}"
            records.append({
                "domain": domain,
                "ip": ips[0] if ips else "",
                "timestamp": timestamp,
                "suspicious": suspicious,
                "reason": reason,
                "raw_line": line[:200],
            })
    # Deduplicate by domain
    seen: set = set()
    deduped: List[Dict[str, Any]] = []
    for r in records:
        key = r["domain"]
        if key not in seen:
            seen.add(key)
            deduped.append(r)
    return sorted(deduped, key=lambda x: x["timestamp"], reverse=True)[:5000]


def analyze_proxy_vpn(logs_path: Path) -> Dict[str, Any]:
    """Analyse proxy/VPN configuration and log files.

    Returns a dict with:
      * ``vpn_detected``        — True if VPN usage patterns found
      * ``proxy_detected``      — True if proxy settings found
      * ``vpn_indicators``      — list of matched VPN keyword lines
      * ``proxy_settings``      — extracted proxy server/port info
      * ``vpn_services``        — named VPN services detected
      * ``warnings``            — any issues
    """
    result: Dict[str, Any] = {
        "file": str(logs_path),
        "vpn_detected": False,
        "proxy_detected": False,
        "vpn_indicators": [],
        "proxy_settings": [],
        "vpn_services": [],
        "warnings": [],
    }
    if not logs_path.exists():
        result["warnings"].append("File not found")
        return result
    try:
        text = logs_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        result["warnings"].append(str(exc))
        return result

    proxy_pat = re.compile(
        r"(?:proxy|socks)[\s=:]+([a-zA-Z0-9.\-]+):(\d{2,5})", re.I
    )
    vpn_service_pat = re.compile(
        r"\b(nordvpn|expressvpn|mullvad|protonvpn|surfshark|pia|cyberghost|"
        r"hotspotshield|tunnelbear|windscribe|ipvanish)\b", re.I
    )

    for line in text.splitlines():
        for pat in _VPN_KEYWORDS:
            if pat.search(line):
                result["vpn_detected"] = True
                result["vpn_indicators"].append(line[:200])
                break
        m = proxy_pat.search(line)
        if m:
            result["proxy_detected"] = True
            result["proxy_settings"].append({
                "host": m.group(1), "port": int(m.group(2)), "line": line[:200]
            })
        svc = vpn_service_pat.search(line)
        if svc and svc.group(1).lower() not in result["vpn_services"]:
            result["vpn_services"].append(svc.group(1).lower())

    result["vpn_indicators"] = result["vpn_indicators"][:100]
    result["proxy_settings"] = result["proxy_settings"][:50]
    return result


def generate_network_report(network_analysis: Dict) -> str:
    """Generate a styled HTML network forensics report.

    Parameters
    ----------
    network_analysis:
        A dict with any of the following keys:
          * ``pcap``         — output of analyze_pcap()
          * ``connections``  — output of analyze_network_connections()
          * ``dns``          — output of analyze_dns_cache()
          * ``proxy_vpn``    — output of analyze_proxy_vpn()
    """
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    pcap_data: dict = network_analysis.get("pcap", {})
    conns: list = network_analysis.get("connections", [])
    dns_data: list = network_analysis.get("dns", [])
    pv_data: dict = network_analysis.get("proxy_vpn", {})

    packet_count = pcap_data.get("packet_count", 0)
    protocols: dict = pcap_data.get("protocols", {})
    conversations: list = pcap_data.get("conversations", [])
    suspicious_ips: list = pcap_data.get("suspicious_ips", [])

    suspicious_conns = [c for c in conns if c.get("suspicious")]
    suspicious_dns = [d for d in dns_data if d.get("suspicious")]

    def _badge(txt: str, col: str = "#6b7280") -> str:
        return (
            f'<span style="background:{col};color:#fff;padding:2px 7px;border-radius:9999px;'
            f'font-size:.75rem;font-weight:700;">{html.escape(str(txt))}</span>'
        )

    proto_html = "".join(
        f'<div style="display:flex;justify-content:space-between;padding:.25rem 0;'
        f'border-bottom:1px solid #374151;">'
        f'<span style="color:#93c5fd;">{html.escape(p)}</span>'
        f'<span style="font-weight:700;color:#818cf8;">{c}</span></div>'
        for p, c in sorted(protocols.items(), key=lambda x: -x[1])
    )

    conv_rows = "".join(
        f"<tr>"
        f'<td style="border:1px solid #374151;padding:5px;">{html.escape(c.get("src",""))}</td>'
        f'<td style="border:1px solid #374151;padding:5px;">{c.get("sport","?")}</td>'
        f'<td style="border:1px solid #374151;padding:5px;">{html.escape(c.get("dst",""))}</td>'
        f'<td style="border:1px solid #374151;padding:5px;">{c.get("dport","?")}</td>'
        f'<td style="border:1px solid #374151;padding:5px;">{html.escape(c.get("protocol",""))}</td>'
        f'<td style="border:1px solid #374151;padding:5px;">{c.get("packets",0)}</td>'
        f'<td style="border:1px solid #374151;padding:5px;">{c.get("bytes",0)}</td>'
        f"</tr>"
        for c in conversations[:50]
    )

    dns_rows = "".join(
        f"<tr>"
        f'<td style="border:1px solid #374151;padding:5px;">{html.escape(d.get("domain",""))}</td>'
        f'<td style="border:1px solid #374151;padding:5px;">{html.escape(d.get("ip",""))}</td>'
        f'<td style="border:1px solid #374151;padding:5px;">{html.escape(d.get("timestamp",""))}</td>'
        f'<td style="border:1px solid #374151;padding:5px;">'
        f'{_badge("SUSPICIOUS","#ef4444") if d.get("suspicious") else _badge("OK","#22c55e")}'
        f"</td></tr>"
        for d in (suspicious_dns or dns_data)[:50]
    )

    vpn_badge = (
        _badge("VPN DETECTED", "#ef4444")
        if pv_data.get("vpn_detected") else _badge("No VPN", "#22c55e")
    )
    proxy_badge = (
        _badge("PROXY DETECTED", "#f59e0b")
        if pv_data.get("proxy_detected") else _badge("No Proxy", "#22c55e")
    )

    warn_html = "".join(
        f'<div style="background:#431407;border:1px solid #b45309;border-radius:.5rem;'
        f'padding:.5rem .75rem;color:#fbbf24;font-size:.85rem;margin:.25rem 0;">⚠ {html.escape(w)}</div>'
        for w in pcap_data.get("warnings", []) + pv_data.get("warnings", [])
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<title>Network Forensics Report</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#111827;color:#e5e7eb;font-family:'Segoe UI',system-ui,sans-serif;line-height:1.6;padding:2rem}}
h1{{font-size:1.75rem;font-weight:800;background:linear-gradient(90deg,#06b6d4,#3b82f6);
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
<h1>🌐 Network Forensics Report</h1>
<p class="meta">Generated: {ts}</p>
{warn_html}
<div class="stat-grid">
  <div class="stat"><div class="stat-val">{packet_count}</div><div class="stat-lbl">Packets Parsed</div></div>
  <div class="stat"><div class="stat-val">{len(conversations)}</div><div class="stat-lbl">Conversations</div></div>
  <div class="stat"><div class="stat-val">{len(conns)}</div><div class="stat-lbl">Log Connections</div></div>
  <div class="stat"><div class="stat-val">{len(suspicious_conns)}</div><div class="stat-lbl">Suspicious Conns</div></div>
  <div class="stat"><div class="stat-val">{len(dns_data)}</div><div class="stat-lbl">DNS Records</div></div>
  <div class="stat"><div class="stat-val">{len(suspicious_dns)}</div><div class="stat-lbl">Suspicious DNS</div></div>
</div>

<h2>📦 Protocols</h2>
<div class="card">{proto_html or "<p style='color:#6b7280;font-style:italic;'>No protocols identified.</p>"}</div>

<h2>🔄 Conversations ({len(conversations)})</h2>
<div class="card">
<table>
<thead><tr>
  <th>Src IP</th><th>S.Port</th><th>Dst IP</th><th>D.Port</th><th>Proto</th><th>Pkts</th><th>Bytes</th>
</tr></thead>
<tbody>{conv_rows or "<tr><td colspan='7' style='padding:6px;color:#6b7280;text-align:center;'>No conversations.</td></tr>"}</tbody>
</table>
</div>

<h2>🌍 DNS Cache ({len(dns_data)} records, {len(suspicious_dns)} suspicious)</h2>
<div class="card">
<table>
<thead><tr>
  <th>Domain</th><th>IP</th><th>Timestamp</th><th>Status</th>
</tr></thead>
<tbody>{dns_rows or "<tr><td colspan='4' style='padding:6px;color:#6b7280;text-align:center;'>No DNS records.</td></tr>"}</tbody>
</table>
</div>

<h2>🔒 Proxy / VPN Analysis {vpn_badge} {proxy_badge}</h2>
<div class="card">
{"<p>VPN Services: " + ", ".join(pv_data.get("vpn_services", [])) + "</p>" if pv_data.get("vpn_services") else ""}
{"<p>Proxy Settings: " + ", ".join(f"{p['host']}:{p['port']}" for p in pv_data.get("proxy_settings", [])) + "</p>" if pv_data.get("proxy_settings") else ""}
{"<p style='color:#6b7280;font-style:italic;'>No proxy or VPN indicators found.</p>" if not pv_data.get("vpn_detected") and not pv_data.get("proxy_detected") else ""}
</div>
</body>
</html>"""
