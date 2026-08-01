"""Tests for the tool-validation package (SWGDE 18-Q-001 report + CFTT coverage matrix).

These tests are as much about HONESTY as about correctness. The coverage matrix is the
one place in this codebase where it is easiest to quietly over-claim, so several tests
below exist specifically to make over-claiming fail the build:

  * every assertion must carry an assessment, and every assessment must name the module
    that justifies it;
  * nothing may be marked "met" on the strength of a capability the tool does not have
    (physical/raw acquisition, unallocated-space recovery);
  * assertion wording that was not verified verbatim must be visibly flagged as a
    paraphrase;
  * the self-validation harness must record a failure as a failure — the negative
    control proves it does not swallow them.

All fixtures are built programmatically; nothing here depends on a binary fixture file,
a device, or a network.
"""

from __future__ import annotations

import json
import re

import pytest

from triage.validation import (
    COVERAGE,
    MDT_ASSERTIONS,
    NEGATIVE_CONTROL_CASE_ID,
    STATUSES,
    SWGDE_REPORT_FIELDS,
    UNVERIFIED_PREFIX,
    ValidationCase,
    build_report,
    coverage_matrix,
    coverage_summary,
    known_limitations,
    render_coverage_html,
    render_report_html,
    render_report_json,
    run_self_validation,
    self_validation_summary,
    validate_report,
)

# Capabilities eRakshak does not have. An assertion may not be claimed "met" on the
# strength of any of them. Word-bounded so "drawn" does not trip the "raw" check.
_OVERCLAIM_PATTERN = re.compile(r"\b(physical|raw|unallocated)\b", re.IGNORECASE)


# --- shared, expensive fixture -------------------------------------------------
@pytest.fixture(scope="module")
def report():
    """Run the offline self-validation once for the whole module.

    ``case_dir=None`` exercises the temp-directory path, which is what a caller with
    no arguments gets; the fixtures are cleaned up afterwards.
    """
    return run_self_validation(tester="pytest (automated regression run)")


def _case(report, case_id):
    """Fetch one case by id, failing loudly if the harness did not run it at all."""
    match = [c for c in report.cases if c.case_id == case_id]
    assert match, f"case {case_id} was not run at all; cases={[c.case_id for c in report.cases]}"
    return match[0]


# ===========================================================================
# CFTT coverage matrix — structure
# ===========================================================================
def test_every_assertion_has_a_coverage_entry():
    """No assertion may go unassessed — silence must not read as a pass."""
    missing = [a["id"] for a in MDT_ASSERTIONS if a["id"] not in COVERAGE]
    assert missing == [], f"assertions with no COVERAGE entry: {missing}"


def test_no_orphan_coverage_entries():
    """A COVERAGE entry for an assertion that does not exist is a stale claim."""
    ids = {a["id"] for a in MDT_ASSERTIONS}
    orphans = sorted(set(COVERAGE) - ids)
    assert orphans == [], f"COVERAGE entries with no matching assertion: {orphans}"


def test_every_coverage_entry_has_non_empty_evidence():
    """An assessment with no evidence is an assertion, not a finding."""
    for row in coverage_matrix():
        assert row["evidence"].strip(), f"{row['id']} has an empty evidence string"
        assert len(row["evidence"]) > 40, (
            f"{row['id']} evidence is too short to name an implementing module: "
            f"{row['evidence']!r}"
        )


def test_every_coverage_entry_uses_a_known_status():
    for row in coverage_matrix():
        assert row["status"] in STATUSES, f"{row['id']} has bogus status {row['status']!r}"


def test_non_met_entries_carry_a_caveat():
    """Anything short of "met" must say what the shortfall is (18-Q-001 §6 field 7)."""
    for row in coverage_matrix():
        if row["status"] != "met":
            assert row["caveat"].strip(), (
                f"{row['id']} is {row['status']} but records no caveat; the limitation "
                "must be stated so it can flow into the report"
            )


# ===========================================================================
# CFTT coverage matrix — honesty guards
# ===========================================================================
def test_met_assertions_do_not_rest_on_capabilities_the_tool_lacks():
    """The core honesty guard.

    eRakshak performs a logical acquisition only. Nothing may be marked "met" while its
    evidence leans on physical/raw acquisition or unallocated-space recovery.
    """
    for row in coverage_matrix():
        if row["status"] == "met":
            hit = _OVERCLAIM_PATTERN.search(row["evidence"])
            assert hit is None, (
                f"{row['id']} is marked 'met' but its evidence invokes "
                f"{hit.group(0)!r}, a capability eRakshak does not have: "
                f"{row['evidence']!r}"
            )


def test_no_core_assertion_is_dismissed_as_not_applicable():
    """CFTT v3.3 §6.2 sanctions "not applicable" only for unsupported OPTIONAL features."""
    bad = [
        r["id"]
        for r in coverage_matrix()
        if r["category"] == "core" and r["status"] == "not-applicable"
    ]
    assert bad == [], (
        f"core assertions marked not-applicable: {bad}. A core assertion the tool "
        "cannot satisfy is a failure or a documented limitation, never an N/A."
    )


def test_unverified_wording_is_prefixed_and_flagged():
    """A paraphrase must never be presented as the words of the standard."""
    for assertion in MDT_ASSERTIONS:
        if assertion["verified_wording"]:
            assert not assertion["text"].startswith(UNVERIFIED_PREFIX), (
                f"{assertion['id']} claims verified wording but carries the "
                "unverified prefix"
            )
        else:
            assert assertion["text"].startswith(UNVERIFIED_PREFIX), (
                f"{assertion['id']} has verified_wording=False but its text is not "
                f"prefixed {UNVERIFIED_PREFIX!r}"
            )


def test_email_assertion_is_honestly_not_met():
    """There is no email parser in triage/parsers/, so MDT-CA-07 must say so."""
    assert COVERAGE["MDT-CA-07"]["status"] == "not-met"


def test_deleted_recovery_is_not_overclaimed():
    """No file-system-level carving exists, so MDT-AO-20 cannot be a clean 'met'."""
    entry = COVERAGE["MDT-AO-20"]
    assert entry["status"] == "partially-met"
    assert "unallocated" in entry["caveat"].lower()


# ===========================================================================
# CFTT coverage matrix — summary + rendering
# ===========================================================================
def test_coverage_summary_counts_add_up():
    summary = coverage_summary()
    rows = coverage_matrix()

    assert summary["total"] == len(rows) == len(MDT_ASSERTIONS)
    assert sum(summary["counts"].values()) == summary["total"]
    assert sum(summary["core_counts"].values()) + sum(
        summary["optional_counts"].values()
    ) == summary["total"]

    # Counts must match a straight recount of the matrix, not a drifting tally.
    for status in STATUSES:
        assert summary["counts"][status] == sum(1 for r in rows if r["status"] == status)


def test_coverage_summary_conclusion_names_the_scheme_and_is_honest():
    summary = coverage_summary()
    conclusion = summary["conclusion"]
    # The scheme must be identified: two incompatible MDT-CA numberings exist.
    assert "3.3" in conclusion
    # The partial verdict must be attributed to SWGDE, not to NIST.
    assert "18-Q-001" in conclusion
    # No error rate has been characterised, and the conclusion must not imply one.
    assert "error rate" in conclusion.lower()
    assert summary["unverified_wording_ids"], (
        "at least one assertion is a paraphrase; the summary must list it"
    )


def test_render_coverage_html_escapes_assertion_text():
    """MDT-CA-02's own text contains an ampersand — it must come out escaped."""
    html_out = render_coverage_html()
    assert "calendar &amp; notes" in html_out
    assert "calendar & notes" not in html_out


# ===========================================================================
# SWGDE 18-Q-001 report
# ===========================================================================
def test_report_field_list_marks_tool_added_fields_as_not_required():
    """conclusion/reviewed_by/approved are NOT 18-Q-001 §6 fields."""
    by_key = {f["key"]: f for f in SWGDE_REPORT_FIELDS}
    for key in ("conclusion", "reviewed_by", "approved"):
        assert by_key[key]["required"] is False
        assert "tool-added" in by_key[key]["source"]
    # The seven genuine §6 obligations must be present and required.
    for key in ("purpose", "tester", "tested_at", "cases", "dataset_name", "limitations"):
        assert by_key[key]["required"] is True


def test_build_report_without_tester_is_incomplete():
    """18-Q-001 §6 field 2 is "Who performed the testing" — an unattributed report fails."""
    case = ValidationCase(
        case_id="T-1",
        description="trivial",
        artifact_class="hashing",
        expected={"x": 1},
        actual={"x": 1},
        passed=True,
    )
    rep = build_report(
        [case], tool_name="eRakshak", tool_version="0.1.0", dataset_name="fixture"
    )
    checks = validate_report(rep)
    assert checks["complete"] is False
    assert "tester" in checks["missing_required"]

    # Supplying the tester (and the other required fields) clears it.
    rep2 = build_report(
        [case],
        tool_name="eRakshak",
        tool_version="0.1.0",
        tester="A. Examiner",
        dataset_name="fixture",
    )
    checks2 = validate_report(rep2)
    assert checks2["missing_required"] == []
    assert checks2["complete"] is True


def test_validate_report_always_warns_about_tester_independence():
    """18-Q-001 §5.6: an in-house tool's tester should be independent of the developer."""
    rep = build_report(
        [], tool_name="eRakshak", tool_version="0.1.0", tester="dev", dataset_name="d"
    )
    warnings = " ".join(validate_report(rep)["warnings"])
    assert "independent of the developer" in warnings
    # An empty case list must be called out rather than passed over.
    assert "no cases" in warnings.lower() or "No test cases" in warnings


def test_build_report_derives_anomalies_from_failing_cases():
    """A report cannot claim a clean run over failing cases."""
    failing = ValidationCase(
        case_id="T-FAIL",
        description="deliberately failing",
        artifact_class="hashing",
        expected={"sha256": "aaaa"},
        actual={"sha256": "bbbb"},
        passed=False,
        anomalies=["digest mismatch"],
    )
    rep = build_report(
        [failing],
        tool_name="eRakshak",
        tool_version="0.1.0",
        tester="A. Examiner",
        dataset_name="fixture",
    )
    assert rep.failed_count() == 1
    assert any("digest mismatch" in a for a in rep.anomalies)
    # The failure must also surface as an identified limitation (§6 field 7)...
    assert any("T-FAIL" in lim for lim in rep.limitations)
    # ...and the generated conclusion must not claim success.
    assert "did NOT perform as expected" in rep.conclusion


def test_known_limitations_disclose_the_hard_dead_ends():
    """The standing disclosures must survive any refactor of this list."""
    blob = " ".join(known_limitations()).lower()
    assert "write-block" in blob                      # no write-blocking on mobile
    assert "file-based encryption" in blob or "fbe" in blob
    assert "non-rooted" in blob                       # content providers = live rows only
    assert "root is not decryption" in blob           # BFU / CE data
    assert "sqlcipher" in blob and "signal" in blob   # keystore-wrapped app content
    assert "secure_delete" in blob and "autovacuum" in blob
    assert "error rate" in blob                       # none characterised
    assert "unallocated" in blob                      # no slack/unallocated recovery


def test_known_limitations_appear_in_every_built_report():
    rep = build_report(
        [], tool_name="eRakshak", tool_version="0.1.0", tester="t", dataset_name="d"
    )
    for limitation in known_limitations():
        assert limitation in rep.limitations


def test_render_report_json_round_trips():
    case = ValidationCase(
        case_id="T-JSON",
        description="round trip",
        artifact_class="hashing",
        expected={"sha256": "aa", "blob": b"\x00\xff"},
        actual={"sha256": "aa", "nested": {"list": [1, 2, None, True]}},
        passed=True,
    )
    rep = build_report(
        [case],
        tool_name="eRakshak",
        tool_version="0.1.0",
        tester="A. Examiner",
        dataset_name="fixture",
        dataset_hash="deadbeef",
    )
    payload = json.loads(render_report_json(rep))

    assert payload["tool_name"] == "eRakshak"
    assert payload["case_count"] == 1
    assert payload["passed_count"] == 1
    assert payload["cases"][0]["case_id"] == "T-JSON"
    # Raw bytes must round-trip as an explicit, unambiguous hex record — never as a
    # guessed text decode that could be mistaken for recovered content.
    assert payload["cases"][0]["expected"]["blob"]["__bytes_hex__"] == "00ff"
    assert payload["validation"]["complete"] is True


def test_render_report_html_escapes_hostile_case_text():
    """Case text can contain bytes carved out of evidence; it is never trusted as markup."""
    case = ValidationCase(
        case_id="T-XSS",
        description="<script>alert('xss')</script> & \"quoted\"",
        artifact_class="<img src=x onerror=alert(1)>",
        expected={},
        actual={},
        passed=False,
        anomalies=["<b>anomaly</b>"],
    )
    rep = build_report(
        [case],
        tool_name="eRakshak",
        tool_version="0.1.0",
        tester="A. Examiner",
        dataset_name="fixture",
    )
    out = render_report_html(rep)

    assert "<script>alert('xss')</script>" not in out
    assert "&lt;script&gt;" in out
    assert "<img src=x onerror=alert(1)>" not in out
    assert "<b>anomaly</b>" not in out
    assert "&lt;b&gt;anomaly&lt;/b&gt;" in out


# ===========================================================================
# Self-validation harness
# ===========================================================================
def test_self_validation_runs_and_produces_cases(report):
    assert len(report.cases) >= 7
    assert report.tool_version
    assert report.dataset_hash, "the dataset must be fixed by a digest for reproducibility"
    assert report.environment["network_used"] is False
    assert report.environment["device_attached"] is False


def test_self_validation_hash_known_answer_passes(report):
    """The hard-coded SHA-256 vector must match; everything else rests on this."""
    case = _case(report, "KAT-HASH-001")
    assert case.passed, case.anomalies
    assert case.actual["sha256"] == case.expected["sha256"]


def test_self_validation_intact_case_passes(report):
    """An untouched case folder must verify as INTACT."""
    case = _case(report, "KAT-MANIFEST-INTACT-002")
    assert case.passed, case.anomalies
    assert case.actual["integrity_status"] == "INTACT"
    assert case.actual["failed"] == 0
    assert case.actual["verified"] == case.actual["total_files"] > 0


def test_self_validation_detects_tampering(report):
    """A single flipped byte must be detected and the modified file named."""
    case = _case(report, "KAT-MANIFEST-TAMPER-003")
    assert case.passed, case.anomalies
    assert case.actual["integrity_status"] == "TAMPERED"
    assert case.actual["failed"] == 1
    assert any("alpha.txt" in p for p in case.actual["failed_files"])


def test_self_validation_recovers_known_deleted_row(report):
    """The known deleted body text must be recovered, and never labelled LIVE."""
    case = _case(report, "KAT-SQLITE-DELETED-004")
    assert case.passed, case.anomalies
    assert case.actual["deleted_marker_recovered"] is True
    assert case.actual["any_carved_row_labelled_live"] is False


def test_self_validation_detects_rowid_gap_and_isolates_live_rows(report):
    gap_case = _case(report, "KAT-SQLITE-GAP-005")
    assert gap_case.passed, gap_case.anomalies
    assert {"after_rowid": 2, "before_rowid": 4, "missing": 1} in gap_case.actual["gaps"]

    live_case = _case(report, "KAT-SQLITE-LIVE-006")
    assert live_case.passed, live_case.anomalies
    # The deleted row must never appear among live rows.
    assert live_case.actual["deleted_marker_leaked_into_live"] is False
    assert live_case.actual["confidence_labels_present"] == ["live"]


def test_self_validation_timestamp_case_passes(report):
    case = _case(report, "KAT-TIME-007")
    assert case.passed, case.anomalies
    assert case.actual["parses_as_iso8601_utc"] is True
    assert case.actual["ends_with_Z"] is True


def test_harness_does_not_swallow_a_failing_case(report):
    """The negative control asks for a capability the tool lacks.

    It must be recorded ``passed=False`` with an explanatory anomaly. If this ever
    comes back True, the harness is no longer trustworthy — a self-test that cannot
    report a failure cannot report anything.
    """
    control = _case(report, NEGATIVE_CONTROL_CASE_ID)
    assert control.passed is False, (
        "the negative control was recorded as passed; the harness is swallowing "
        "failures and no other result in this report can be trusted"
    )
    assert control.anomalies, "a failing case must carry an anomaly description"
    assert "NEGATIVE CONTROL" in control.anomalies[0]
    # A failing case must propagate into the report's anomalies and limitations.
    assert any(NEGATIVE_CONTROL_CASE_ID in a for a in report.anomalies)
    assert any(NEGATIVE_CONTROL_CASE_ID in lim for lim in report.limitations)
    # ...and the conclusion must not claim an unqualified success.
    assert "did NOT perform as expected" in report.conclusion


def test_self_validation_report_renders_to_valid_json(report):
    payload = json.loads(render_report_json(report))
    assert payload["case_count"] == len(report.cases)
    assert payload["failed_count"] >= 1, "the negative control must show up as a failure"
    ids = [c["case_id"] for c in payload["cases"]]
    assert NEGATIVE_CONTROL_CASE_ID in ids


def test_self_validation_summary_separates_the_negative_control():
    """The control is designed to fail; folding it into a pass rate would mislead."""
    summary = self_validation_summary()
    assert summary["negative_control_passed"] is False
    assert summary["case_count"] == summary["excluding_negative_control"]["case_count"] + 1
    assert summary["excluding_negative_control"]["failed_ids"] == [], (
        "a real (non-control) case failed: "
        f"{summary['excluding_negative_control']['failed_ids']}"
    )
    # Run with no tester supplied, so the report must be flagged incomplete.
    assert summary["report_complete"] is False
    assert "tester" in summary["missing_required"]


def test_self_validation_with_explicit_case_dir_retains_fixtures(tmp_path):
    """Passing a case_dir keeps the dataset on disk so a reviewer can inspect it."""
    rep = run_self_validation(case_dir=tmp_path, tester="A. Examiner")
    assert rep.environment["fixtures_retained"] is True
    assert (tmp_path / "kat_vector.bin").exists()
    assert (tmp_path / "fixture_messages.db").exists()
    assert list(tmp_path.glob("cases/VALIDATION-INTACT/manifest.json"))
