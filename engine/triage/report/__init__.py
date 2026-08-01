"""Report generation package for eRakshak forensic triage engine.

Re-exports the core ``generate_report`` function from the HTML report module
so that ``from .report import generate_report`` continues to work after the
single-file module was refactored into a package with multiple exporters.
"""

from .html_report import generate_report, _generate_hash_verification_section  # noqa: F401

__all__ = ["generate_report", "_generate_hash_verification_section"]
