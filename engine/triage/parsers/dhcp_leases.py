"""Hotspot client-lease parser (Root Tier 2, gated by ``tier2_wifi``).

Answers a question :mod:`triage.parsers.hotspot` explicitly declines to answer:
*which devices* connected to this device's own mobile hotspot, by MAC and IP,
rather than merely whether the hotspot was active.

Android has shipped two entirely different DHCP servers for tethering, and
which one a device runs determines whether this data exists at all:

* **Legacy dnsmasq stack** (AOSP up to roughly Android 8, and many custom ROMs
  — LineageOS included — still today): the DHCP server is ``dnsmasq``, and it
  persists every lease it hands out to a plaintext file, conventionally
  ``/data/misc/dhcp/dnsmasq.leases`` (some OEM trees use
  ``/data/misc/wifi/dnsmasq.leases`` instead). Root-owned, mode 0660 — root
  required to read it, but the format is a one-line-per-lease flat file with
  no further processing needed.
* **Mainline Tethering module** (``com.android.tethering`` APEX, stock on
  Android 9+ Pixel/Samsung/most current OEM builds): Google replaced dnsmasq
  with an in-process Java ``DhcpServer`` that keeps its client table
  (``ClientInfo`` map) in memory only. It is never written to disk, by design
  — there is no file this module or anyone else can root-pull after the fact.
  A build on this stack legitimately has **no lease file anywhere**, and that
  absence is not evidence that no client ever joined the hotspot; see
  :data:`CAVEAT_NOT_PERSISTED`.

This module only parses locally-staged copies of whichever lease file(s) were
found; pulling them off the device is the pipeline's job (see
``_run_tier2_hotspot_leases`` in ``triage.pipeline``, invoked under the same
``tier2_wifi`` root-tier opt-in as the saved-network credential recovery).
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

#: Every known on-device location of a dnsmasq lease file, as
#: ``(device path, local staging name)``. Probed in order; a device that still
#: ran dnsmasq at some point in its life can carry a stale copy at the legacy
#: path alongside (or instead of) the current one, so all hits are parsed and
#: merged rather than stopping at the first match.
LEASE_PATHS: list[tuple[str, str]] = [
    ("/data/misc/dhcp/dnsmasq.leases", "dnsmasq.leases"),
    ("/data/misc/wifi/dnsmasq.leases", "dnsmasq.leases.wifi"),
]

#: Shown when no lease file was found at any known path. Absence here is the
#: *expected* result on any Android 9+ device running the stock mainline
#: Tethering module — its DHCP server never persists leases — so this must
#: never be read as "no client ever connected to this hotspot".
CAVEAT_NOT_PERSISTED = (
    "No dnsmasq lease file found at any known path. On Android 9+ builds "
    "running the stock mainline Tethering module, the hotspot's DHCP server "
    "keeps client leases in memory only and never writes them to disk — this "
    "is the expected, unavoidable result on that stack, not evidence that no "
    "device ever joined this hotspot. A lease file existing at all means this "
    "build still runs the legacy dnsmasq-based tethering stack (pre-Android 9 "
    "AOSP lineage, or a custom ROM such as LineageOS)."
)

#: One line per lease: ``<expiry-epoch> <mac> <ip> <hostname> [client-id]``.
#: ``hostname``/``client-id`` are ``*`` when dnsmasq was not given one.
_MAC_RE = re.compile(r"^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}$")


def _iso(epoch_s: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch_s))


def parse_dnsmasq_leases(path: Path) -> list[dict[str, Any]]:
    """Parse one ``dnsmasq.leases`` file into a list of client-lease records.

    A malformed or non-lease line is skipped rather than aborting the whole
    file — dnsmasq's own lease file is line-oriented and forward-compatible,
    but OEM patches have been seen to append trailing fields.

    An ``expiry-epoch`` of ``0`` means dnsmasq recorded no expiry (a static
    lease); ``lease_expiry`` is left empty rather than rendered as an
    1970-01-01 finding.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    leases: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 4:
            continue  # not a lease line (dnsmasq writes nothing else to this file)

        expiry_raw, mac, ip, hostname = parts[0], parts[1], parts[2], parts[3]
        client_id = parts[4] if len(parts) > 4 else ""

        if not _MAC_RE.match(mac):
            continue
        try:
            expiry_epoch = int(expiry_raw)
        except ValueError:
            continue

        leases.append(
            {
                "mac": mac.lower(),
                "ip": ip,
                "hostname": "" if hostname == "*" else hostname,
                "client_id": "" if client_id == "*" else client_id,
                "lease_expiry": _iso(expiry_epoch) if expiry_epoch > 0 else "",
                "source_file": path.name,
            }
        )
    return leases


def collect_hotspot_leases(pulled: dict[str, Path]) -> dict[str, Any]:
    """Merge every pulled lease file into one honest result.

    ``pulled`` is ``{device_path: local Path}`` as returned by the pipeline's
    root-pull helper. Entries are deduped on ``(mac, ip, lease_expiry)`` so a
    lease surviving in both the current and a legacy-path copy is not
    double-counted.
    """
    leases: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for local_file in pulled.values():
        for lease in parse_dnsmasq_leases(local_file):
            key = (lease["mac"], lease["ip"], lease["lease_expiry"])
            if key in seen:
                continue
            seen.add(key)
            leases.append(lease)

    caveats: list[str] = []
    if leases:
        caveats.append(
            "Each row is a DHCP lease this device's hotspot handed to a CLIENT — "
            "the MAC and IP identify the client, not this device. The timestamp "
            "is the lease's EXPIRY, not when the client joined, and dnsmasq does "
            "not remove a stale entry until the slot is reused, so an old lease "
            "can outlive the session it belongs to. Most phones randomise their "
            "Wi-Fi MAC per network since Android 8/9, so the same physical device "
            "can appear here under a different MAC on a later visit."
        )
    return {"leases": leases, "caveats": caveats}
