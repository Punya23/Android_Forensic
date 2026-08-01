"""Tool-validation package: SWGDE 18-Q-001 reporting and NIST CFTT coverage.

Forensic purpose
----------------
Two deliverables, both machine-readable:

  * :mod:`triage.validation.swgde` — a validation report structured per
    SWGDE 18-Q-001-2.1 (v2.1, 2024-03-07) §6 "Documentation of Testing Results",
    which is the document that actually carries the field list. Tool-added fields
    (conclusion, review, approval) are labelled as such rather than passed off as
    18-Q-001 requirements; those belong to SWGDE 18-Q-002 (2018-11-20).
  * :mod:`triage.validation.cftt` — an honest coverage matrix against the NIST CFTT
    Mobile Device Forensic Tool Specification v3.3 (2025-01) assertions, in which every
    status is justified by naming the module that implements it.

:mod:`triage.validation.harness` runs the offline known-answer self-test that populates
a real report — no device, no network, no downloads, and no fabricated passes.

Limitations
-----------
Producing a validation report is not the same as being validated. 18-Q-001-2.1 §5.6
covers in-house developed tools and recommends "that the tester be independent of the
developer"; a report this package generates about its own engine cannot evidence that.
Read :func:`known_limitations` before quoting any output from here.
"""

from __future__ import annotations

from .cftt import (
    ASSERTION_SCHEME,
    COVERAGE,
    MDT_ASSERTIONS,
    STATUSES,
    UNVERIFIED_PREFIX,
    coverage_matrix,
    coverage_summary,
    render_coverage_html,
)
from .harness import (
    NEGATIVE_CONTROL_CASE_ID,
    run_self_validation,
    self_validation_summary,
)
from .swgde import (
    ERROR_TYPES,
    STANDARDS_CITED,
    SWGDE_REPORT_FIELDS,
    TESTING_TYPES,
    ValidationCase,
    ValidationReport,
    build_report,
    known_limitations,
    render_report_html,
    render_report_json,
    validate_report,
)

__all__ = [
    # --- SWGDE 18-Q-001 report ---
    "SWGDE_REPORT_FIELDS",
    "STANDARDS_CITED",
    "ERROR_TYPES",
    "TESTING_TYPES",
    "ValidationCase",
    "ValidationReport",
    "build_report",
    "validate_report",
    "render_report_html",
    "render_report_json",
    "known_limitations",
    # --- NIST CFTT coverage ---
    "MDT_ASSERTIONS",
    "COVERAGE",
    "STATUSES",
    "ASSERTION_SCHEME",
    "UNVERIFIED_PREFIX",
    "coverage_matrix",
    "coverage_summary",
    "render_coverage_html",
    # --- self-validation harness ---
    "run_self_validation",
    "self_validation_summary",
    "NEGATIVE_CONTROL_CASE_ID",
]
