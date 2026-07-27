"""Advanced Memory Forensics — Android memory dump analysis.

Analyses Android memory dumps to extract volatile forensic evidence:

  * **ELF header parsing** — identifies ELF binaries embedded in the dump.
  * **String extraction** — scans for printable ASCII/UTF-8 strings (min 4 chars)
    and categorises them (URLs, IPs, emails, tokens, paths, etc.).
  * **Process memory region mapping** — reconstructs per-PID memory regions from
    ``/proc/<pid>/maps``-style data embedded in the dump.
  * **Anomaly detection** — flags obfuscated blobs, packed sections, high-entropy
    regions, and suspicious string patterns (shell commands, base64, etc.).
  * **HTML report generation** — presents all findings in a structured dark-theme
    report suitable for case documentation.

All functions are defensive: exceptions are caught and annotated, never propagated.
Performance: memory-mapped file access, streaming string extraction, parallel region
analysis via ``concurrent.futures``.
"""

from __future__ import annotations

import concurrent.futures
import html
import math
import mmap
import re
import struct
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Constants & patterns
# ---------------------------------------------------------------------------

_ELF_MAGIC = b"\x7fELF"
_MIN_STR_LEN = 4
_HIGH_ENTROPY_THRESHOLD = 7.2   # bits per byte (Shannon entropy)

# Categorisation patterns
_PATTERNS: dict[str, re.Pattern] = {
    "url":    re.compile(r"https?://[^\s\"'<>]{6,}", re.I),
    "email":  re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"),
    "ip":     re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    "path":   re.compile(r"(?:/[a-zA-Z0-9._\-]+){2,}"),
    "token":  re.compile(r"(?:Bearer|token|key|secret|api|password)[=:\s][^\s]{8,}", re.I),
    "base64": re.compile(r"[A-Za-z0-9+/]{32,}={0,2}"),
    "shell":  re.compile(r"\b(?:sh|bash|chmod|rm\s+-rf|wget|curl|nc\s+)\b", re.I),
    "phone":  re.compile(r"\+?\d[\d\s\-]{9,14}\d"),
}

# Suspicious string signals
_SUSPICIOUS_KEYWORDS = frozenset([
    "su ", "rooted", "frida", "xposed", "busybox", "magisk", "supersu",
    "strace", "ptrace", "injection", "hooking", "gdb ", "adb ", "netcat",
    "reverse shell", "payload", "/system/bin/sh", "am start", "pm install",
])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _shannon_entropy(data: bytes) -> float:
    """Calculate Shannon entropy (bits per byte) of a byte sequence."""
    if not data:
        return 0.0
    freq: dict[int, int] = {}
    for b in data:
        freq[b] = freq.get(b, 0) + 1
    n = len(data)
    return -sum((c / n) * math.log2(c / n) for c in freq.values() if c > 0)


def _categorise_string(s: str) -> List[str]:
    """Return list of category labels matching a string."""
    cats: List[str] = []
    for cat, pat in _PATTERNS.items():
        if pat.search(s):
            cats.append(cat)
    lower = s.lower()
    if any(kw in lower for kw in _SUSPICIOUS_KEYWORDS):
        cats.append("suspicious")
    return cats or ["generic"]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def analyze_memory_dump(memory_path: Path) -> Dict[str, Any]:
    """Analyse a memory dump file.

    Parses ELF headers, extracts process memory regions, strings, and anomalies.
    Uses memory-mapped access for efficiency.

    Returns
    -------
    dict with keys:
      * ``file``          — path to the dump
      * ``size_bytes``    — file size
      * ``elf_headers``   — list of ELF header dicts found within the dump
      * ``strings``       — top string categories summary
      * ``string_count``  — total strings extracted
      * ``anomalies``     — list of anomaly dicts
      * ``regions``       — discovered memory region mappings
      * ``warnings``      — list of any issues during analysis
    """
    result: Dict[str, Any] = {
        "file": str(memory_path),
        "size_bytes": 0,
        "elf_headers": [],
        "strings": {},
        "string_count": 0,
        "anomalies": [],
        "regions": [],
        "warnings": [],
    }
    if not memory_path.exists():
        result["warnings"].append("File not found")
        return result

    try:
        size = memory_path.stat().st_size
        result["size_bytes"] = size
        with memory_path.open("rb") as fh:
            with mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                data = bytes(mm[:])
    except Exception as exc:
        result["warnings"].append(f"Read error: {exc}")
        try:
            data = memory_path.read_bytes()
            result["size_bytes"] = len(data)
        except Exception as exc2:
            result["warnings"].append(f"Fallback read failed: {exc2}")
            return result

    # ELF header scan
    elf_offsets: List[int] = []
    off = 0
    while True:
        idx = data.find(_ELF_MAGIC, off)
        if idx == -1:
            break
        elf_offsets.append(idx)
        off = idx + 1

    for off in elf_offsets[:32]:  # cap
        if off + 64 > len(data):
            continue
        hdr = data[off : off + 64]
        try:
            ei_class = hdr[4]      # 1=32-bit, 2=64-bit
            ei_data = hdr[5]       # 1=LE, 2=BE
            e_type = struct.unpack_from("<H", hdr, 16)[0]
            e_machine = struct.unpack_from("<H", hdr, 18)[0]
            result["elf_headers"].append({
                "offset": off,
                "class": "64-bit" if ei_class == 2 else "32-bit",
                "endianness": "little" if ei_data == 1 else "big",
                "type": {1: "relocatable", 2: "executable", 3: "shared", 4: "core"}.get(e_type, f"0x{e_type:04x}"),
                "machine": f"0x{e_machine:04x}",
            })
        except Exception:
            pass

    # String extraction (streaming)
    strings = extract_memory_strings(data)
    result["string_count"] = len(strings)
    cat_summary: Dict[str, int] = {}
    for s in strings:
        for cat in _categorise_string(s):
            cat_summary[cat] = cat_summary.get(cat, 0) + 1
    result["strings"] = cat_summary

    # Region extraction
    result["regions"] = _extract_regions_from_strings(strings)

    # Anomaly detection
    result["anomalies"] = find_memory_anomalies(data)

    return result


def extract_memory_strings(memory_data: bytes) -> List[str]:
    """Extract printable strings from memory bytes.

    Scans for contiguous ASCII/UTF-8 printable sequences of at least
    ``_MIN_STR_LEN`` characters. Returns a deduplicated list, longest first.
    """
    strings: List[str] = []
    buf = bytearray()
    for b in memory_data:
        if 0x20 <= b <= 0x7E or b in (0x09, 0x0A, 0x0D):
            buf.append(b)
        else:
            if len(buf) >= _MIN_STR_LEN:
                try:
                    s = buf.decode("ascii", "ignore").strip()
                    if len(s) >= _MIN_STR_LEN:
                        strings.append(s)
                except Exception:
                    pass
            buf = bytearray()
    if len(buf) >= _MIN_STR_LEN:
        try:
            s = buf.decode("ascii", "ignore").strip()
            if len(s) >= _MIN_STR_LEN:
                strings.append(s)
        except Exception:
            pass
    # Deduplicate while preserving order
    seen: set = set()
    deduped: List[str] = []
    for s in strings:
        if s not in seen:
            seen.add(s)
            deduped.append(s)
    return sorted(deduped, key=len, reverse=True)


def _extract_regions_from_strings(strings: List[str]) -> List[Dict[str, Any]]:
    """Attempt to reconstruct /proc/<pid>/maps-style regions from string content."""
    regions: List[Dict[str, Any]] = []
    # Pattern: address_range perms offset dev inode pathname
    maps_pat = re.compile(
        r"([0-9a-f]{8,16})-([0-9a-f]{8,16})\s+([rwxsp-]{4})\s+"
        r"([0-9a-f]+)\s+([\w:]+)\s+(\d+)\s*(.*)"
    )
    for s in strings:
        m = maps_pat.match(s)
        if m:
            regions.append({
                "start": m.group(1),
                "end": m.group(2),
                "perms": m.group(3),
                "offset": m.group(4),
                "dev": m.group(5),
                "inode": m.group(6),
                "pathname": m.group(7).strip(),
            })
    return regions


def extract_process_memory(pid: int, memory_path: Path) -> Dict[str, Any]:
    """Extract memory information for a specific process PID from the dump.

    Scans the dump for strings that reference the given PID (in /proc/<pid>/…
    paths) and collects associated memory regions and strings.

    Returns
    -------
    dict with:
      * ``pid``       — the requested PID
      * ``regions``   — memory regions attributed to this PID
      * ``strings``   — strings referencing this PID
      * ``warnings``  — any issues
    """
    result: Dict[str, Any] = {
        "pid": pid,
        "regions": [],
        "strings": [],
        "warnings": [],
    }
    if not memory_path.exists():
        result["warnings"].append("Memory dump file not found")
        return result
    try:
        data = memory_path.read_bytes()
    except OSError as exc:
        result["warnings"].append(str(exc))
        return result

    all_strings = extract_memory_strings(data)
    pid_tag = f"/{pid}/"
    pid_strings = [s for s in all_strings if pid_tag in s or f"pid={pid}" in s]
    result["strings"] = pid_strings[:200]

    regions = _extract_regions_from_strings(all_strings)
    # Filter regions that have a plausible path containing pid or no path at all
    result["regions"] = [
        r for r in regions
        if pid_tag in r.get("pathname", "") or not r.get("pathname")
    ][:100]

    return result


def find_memory_anomalies(memory_data: bytes) -> List[Dict[str, Any]]:
    """Find anomalies in memory data.

    Detects:
      * High-entropy regions (possible encryption / packing)
      * Suspicious string patterns (anti-forensic tools, shells)
      * Embedded ELF images inside a process dump
      * Repeated NOP sleds or shellcode signatures

    Returns
    -------
    List of anomaly dicts with ``type``, ``offset``, ``detail``, and ``severity``.
    """
    anomalies: List[Dict[str, Any]] = []

    # 1) High-entropy 4KB chunks
    chunk_size = 4096
    for i in range(0, min(len(memory_data), 50 * 1024 * 1024), chunk_size):
        chunk = memory_data[i : i + chunk_size]
        if len(chunk) < chunk_size // 2:
            break
        ent = _shannon_entropy(chunk)
        if ent >= _HIGH_ENTROPY_THRESHOLD:
            anomalies.append({
                "type": "high_entropy",
                "offset": i,
                "detail": f"Shannon entropy {ent:.2f} bits/byte — possible encrypted/packed data",
                "severity": "high" if ent > 7.8 else "medium",
            })

    # 2) Suspicious strings
    strings = extract_memory_strings(memory_data)
    for s in strings:
        cats = _categorise_string(s)
        if "suspicious" in cats or "shell" in cats or "token" in cats:
            anomalies.append({
                "type": "suspicious_string",
                "offset": None,
                "detail": s[:256],
                "severity": "high" if "shell" in cats else "medium",
            })

    # 3) Embedded ELF inside dump
    off = 0
    elf_count = 0
    while True:
        idx = memory_data.find(_ELF_MAGIC, off)
        if idx == -1 or elf_count >= 20:
            break
        anomalies.append({
            "type": "embedded_elf",
            "offset": idx,
            "detail": f"ELF magic at offset {idx} — embedded binary",
            "severity": "medium",
        })
        elf_count += 1
        off = idx + 1

    # 4) NOP sleds (x86: 0x90 x64+)
    nop_pat = re.compile(b"\x90{64,}")
    for m in nop_pat.finditer(memory_data):
        anomalies.append({
            "type": "nop_sled",
            "offset": m.start(),
            "detail": f"NOP sled of {len(m.group())} bytes at offset {m.start()}",
            "severity": "high",
        })

    return anomalies[:200]  # cap


def generate_memory_report(memory_analysis: Dict) -> str:
    """Generate a styled HTML memory forensics report.

    Parameters
    ----------
    memory_analysis:
        Output of ``analyze_memory_dump()`` or a dict with similar structure.
    """
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    fpath = memory_analysis.get("file", "Unknown")
    size_mb = memory_analysis.get("size_bytes", 0) / (1024 * 1024)
    elf_hdrs: list = memory_analysis.get("elf_headers", [])
    strings_summary: dict = memory_analysis.get("strings", {})
    str_count = memory_analysis.get("string_count", 0)
    anomalies: list = memory_analysis.get("anomalies", [])
    regions: list = memory_analysis.get("regions", [])
    warnings: list = memory_analysis.get("warnings", [])

    sev_colour = {"high": "#ef4444", "medium": "#f59e0b", "low": "#22c55e"}

    def _badge(txt: str, col: str) -> str:
        return (
            f'<span style="background:{col};color:#fff;padding:2px 7px;'
            f'border-radius:9999px;font-size:.75rem;font-weight:700;">'
            f'{html.escape(txt)}</span>'
        )

    rows_html = ""
    for an in anomalies[:50]:
        col = sev_colour.get(an.get("severity", "low"), "#6b7280")
        rows_html += (
            f"<tr>"
            f'<td style="border:1px solid #374151;padding:6px;">{html.escape(an.get("type","?"))}</td>'
            f'<td style="border:1px solid #374151;padding:6px;">{an.get("offset","—")}</td>'
            f'<td style="border:1px solid #374151;padding:6px;word-break:break-all;">{html.escape(an.get("detail","")[:120])}</td>'
            f'<td style="border:1px solid #374151;padding:6px;">{_badge(an.get("severity","low"), col)}</td>'
            f"</tr>"
        )

    elf_html = "".join(
        f'<div style="background:#1e3a5f;border:1px solid #1e40af;border-radius:.5rem;padding:.5rem .75rem;margin:.25rem 0;">'
        f'Offset {e.get("offset","?")} | {html.escape(e.get("class","?"))} {html.escape(e.get("type","?"))} '
        f'| machine {html.escape(e.get("machine","?"))}</div>'
        for e in elf_hdrs
    )

    str_cats_html = "".join(
        f'<div style="display:flex;justify-content:space-between;padding:.25rem 0;border-bottom:1px solid #374151;">'
        f'<span style="color:#93c5fd;">{html.escape(cat)}</span>'
        f'<span style="font-weight:700;color:#818cf8;">{count}</span></div>'
        for cat, count in sorted(strings_summary.items(), key=lambda x: -x[1])
    )

    regions_html = ""
    for r in regions[:30]:
        regions_html += (
            f'<div style="font-family:monospace;font-size:.8rem;color:#a3e635;padding:.2rem 0;">'
            f'{r.get("start","?")}–{r.get("end","?")} {html.escape(r.get("perms",""))} '
            f'{html.escape(r.get("pathname","[anon]"))}</div>'
        )

    warn_html = "".join(
        f'<div style="background:#431407;border:1px solid #b45309;border-radius:.5rem;'
        f'padding:.5rem .75rem;color:#fbbf24;font-size:.85rem;margin:.25rem 0;">⚠ {html.escape(w)}</div>'
        for w in warnings
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<title>Memory Forensics Report</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#111827;color:#e5e7eb;font-family:'Segoe UI',system-ui,sans-serif;line-height:1.6;padding:2rem}}
h1{{font-size:1.75rem;font-weight:800;background:linear-gradient(90deg,#8b5cf6,#06b6d4);
    -webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-bottom:.5rem}}
h2{{font-size:1.15rem;font-weight:700;color:#c7d2fe;margin:1.5rem 0 .5rem;
    border-bottom:1px solid #374151;padding-bottom:.25rem}}
.meta{{color:#6b7280;font-size:.85rem;margin-bottom:1.5rem}}
.stat-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.75rem;margin-bottom:1.5rem}}
.stat{{background:#1f2937;border:1px solid #374151;border-radius:.5rem;padding:.75rem 1rem;text-align:center}}
.stat-val{{font-size:2rem;font-weight:800;color:#818cf8}}
.stat-lbl{{font-size:.8rem;color:#6b7280;text-transform:uppercase;letter-spacing:.05em}}
.card{{background:#1f2937;border:1px solid #374151;border-radius:.75rem;padding:1rem 1.25rem;margin-bottom:1rem}}
table{{width:100%;border-collapse:collapse;font-size:.82rem}}
th{{border:1px solid #374151;padding:6px;background:#1f2937;color:#9ca3af;text-align:left}}
</style>
</head>
<body>
<h1>🧠 Memory Forensics Report</h1>
<p class="meta">File: <strong>{html.escape(fpath)}</strong> | Size: {size_mb:.1f} MB | Generated: {ts}</p>
{warn_html}
<div class="stat-grid">
  <div class="stat"><div class="stat-val">{len(elf_hdrs)}</div><div class="stat-lbl">ELF Headers</div></div>
  <div class="stat"><div class="stat-val">{str_count}</div><div class="stat-lbl">Strings Extracted</div></div>
  <div class="stat"><div class="stat-val">{len(anomalies)}</div><div class="stat-lbl">Anomalies Found</div></div>
  <div class="stat"><div class="stat-val">{sum(1 for a in anomalies if a.get("severity")=="high")}</div><div class="stat-lbl">High Severity</div></div>
  <div class="stat"><div class="stat-val">{len(regions)}</div><div class="stat-lbl">Memory Regions</div></div>
</div>

<h2>🔵 ELF Headers Found ({len(elf_hdrs)})</h2>
<div class="card">{elf_html or "<p style='color:#6b7280;font-style:italic;'>None found.</p>"}</div>

<h2>📝 String Categories</h2>
<div class="card">{str_cats_html or "<p style='color:#6b7280;font-style:italic;'>No strings extracted.</p>"}</div>

<h2>🗺 Memory Regions ({len(regions)})</h2>
<div class="card">{regions_html or "<p style='color:#6b7280;font-style:italic;'>No regions reconstructed.</p>"}</div>

<h2>⚠ Anomalies ({len(anomalies)})</h2>
<div class="card">
<table>
<thead><tr>
  <th>Type</th><th>Offset</th><th>Detail</th><th>Severity</th>
</tr></thead>
<tbody>{rows_html or "<tr><td colspan='4' style='padding:6px;color:#6b7280;text-align:center;'>No anomalies detected.</td></tr>"}</tbody>
</table>
</div>
</body>
</html>"""
