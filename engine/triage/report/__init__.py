"""triage.report package — forensic HTML report generation and AI-driven exporters.

The primary ``generate_report`` function lives in :mod:`triage.report.html_report`
and is re-exported here so that ``from .report import generate_report`` in
``pipeline.py`` (and any other consumers) continues to work unchanged.
"""

from __future__ import annotations

# Primary HTML report generator (NIST SP 800-101r1 / SWGDE-aligned).
from .html_report import generate_report, _generate_hash_verification_section  # noqa: F401

# AI-driven report engine (automated writing, summarisation, translation).
from .report_engine import (  # noqa: F401
    generate_forensic_report,
    summarize_report,
    translate_report,
    personalize_report,
)

__all__ = [
    "generate_report",
    "_generate_hash_verification_section",
    "generate_forensic_report",
    "summarize_report",
    "translate_report",
    "personalize_report",
]
