"""DEPRECATED — Indian Evidence Act s.65B certificate generation.

The Indian Evidence Act, 1872 was repealed and replaced by the Bharatiya Sakshya
Adhiniyam, 2023 with effect from 1 July 2024. Electronic-evidence certification now runs
through **BSA 2023 s.63** and its Schedule (Part A / Part B, dual signatures), which is
implemented in :mod:`triage.forensics.bsa_certificate`.

This module previously emitted an s.65B certificate that, besides citing a repealed
statute, asserted:

    "The extraction process was read-only and no data on the original device was altered."

That claim is false for this tool and false for mobile acquisition generally. No
write-blocking exists for mobile devices (SWGDE 18-F-003), and SNAGR's own Tier-1 path
installs a helper APK and grants runtime permissions. Signing that sentence would have put
an untrue statement into a court document. The generator is therefore removed rather than
merely left unused, and calling it now raises.

Use instead::

    from triage.forensics.bsa_certificate import build_certificate, render_certificate_html
"""

from __future__ import annotations

from typing import Any, Dict

DEPRECATION_REASON = (
    "IEA 1872 s.65B was repealed on 2024-07-01 and replaced by BSA 2023 s.63. The former "
    "generator also certified that the acquisition was read-only and altered nothing on "
    "the device, which is not true of any mobile acquisition. Use "
    "triage.forensics.bsa_certificate.build_certificate() instead."
)


def generate_65b_certificate(
    case_meta: Dict[str, Any], examiner_name: str, designation: str
) -> str:
    """Removed. Raises :class:`NotImplementedError` pointing at the BSA s.63 generator."""
    raise NotImplementedError(DEPRECATION_REASON)
