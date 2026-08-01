"""End-to-end audit-chain integration: Case -> audit.jsonl -> report -> sealed export (P2-2).

The unit behaviour of the chain primitives lives in test_audit_chain.py. What is tested
here is that the chain is actually *applied* by the custody layer, survives a reopen, and
is surfaced where an examiner will see it — the failure mode being a correct chain module
that nothing ever calls.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from triage.custody import Case, CaseMeta
from triage.export import create_integrity_manifest, export_case
from triage.forensics.audit_chain import GENESIS_HASH, verify_chain


@pytest.fixture()
def case(tmp_path: Path) -> Case:
    c = Case.create(tmp_path / "cases", CaseMeta(case_id="CHAIN-1", examiner="Insp. Rao"))
    for i in range(12):
        c.log("test.event", f"synthetic audit event {i}", tier="tier0")
    return c


def _audit_lines(case: Case) -> list[dict]:
    return [
        json.loads(line)
        for line in (case.root / "audit.jsonl").read_text().splitlines()
        if line.strip()
    ]


# ---------------------------------------------------------------------------
# The chain is actually applied
# ---------------------------------------------------------------------------
def test_every_audit_line_is_chained(case: Case):
    lines = _audit_lines(case)
    assert len(lines) >= 13  # case.create emits one, plus our 12
    for entry in lines:
        assert "prev_hash" in entry, "an unchained audit line defeats tamper evidence"
        assert "entry_hash" in entry


def test_first_line_links_to_genesis(case: Case):
    assert _audit_lines(case)[0]["prev_hash"] == GENESIS_HASH


def test_each_line_links_to_its_predecessor(case: Case):
    lines = _audit_lines(case)
    for prev, cur in zip(lines, lines[1:]):
        assert cur["prev_hash"] == prev["entry_hash"]


def test_fresh_case_verifies(case: Case):
    result = case.verify_audit_chain()
    assert result["valid"] is True
    assert result["verified"] == result["total"] > 0


# ---------------------------------------------------------------------------
# Tamper detection through the custody layer
# ---------------------------------------------------------------------------
def test_edited_detail_breaks_the_chain(case: Case):
    path = case.root / "audit.jsonl"
    lines = path.read_text().splitlines()
    entry = json.loads(lines[5])
    entry["detail"] = "…evidence handled correctly…"  # the classic after-the-fact edit
    lines[5] = json.dumps(entry)
    path.write_text("\n".join(lines) + "\n")

    result = verify_chain(path)
    assert result["valid"] is False
    assert result["first_bad_line"] == 6  # 1-indexed


def test_deleted_line_breaks_the_chain(case: Case):
    path = case.root / "audit.jsonl"
    lines = path.read_text().splitlines()
    del lines[7]
    path.write_text("\n".join(lines) + "\n")
    assert verify_chain(path)["valid"] is False


def test_reordered_lines_break_the_chain(case: Case):
    path = case.root / "audit.jsonl"
    lines = path.read_text().splitlines()
    lines[4], lines[5] = lines[5], lines[4]
    path.write_text("\n".join(lines) + "\n")
    assert verify_chain(path)["valid"] is False


def test_appended_forged_line_breaks_the_chain(case: Case):
    path = case.root / "audit.jsonl"
    forged = {
        "timestamp": "2026-08-01T00:00:00Z",
        "action": "artifact.ingest",
        "detail": "an event that never happened",
        "examiner": "Insp. Rao",
        "prev_hash": "0" * 64,
        "entry_hash": "f" * 64,
    }
    with open(path, "a") as fh:
        fh.write(json.dumps(forged) + "\n")
    assert verify_chain(path)["valid"] is False


# ---------------------------------------------------------------------------
# Reopening a case must CONTINUE the chain, not restart it
# ---------------------------------------------------------------------------
def test_reopened_case_continues_the_chain(case: Case):
    head_before = case.audit_seal()["chain_head"]

    reopened = Case.open(case.root)
    reopened.log("test.event", "written after reopen", tier="tier0")

    lines = _audit_lines(reopened)
    assert lines[-1]["prev_hash"] == head_before, (
        "reopening restarted the chain — to a verifier that is indistinguishable "
        "from every prior entry having been deleted"
    )
    assert reopened.verify_audit_chain()["valid"] is True


# ---------------------------------------------------------------------------
# Surfaced where an examiner will actually see it
# ---------------------------------------------------------------------------
def test_custody_summary_carries_the_chain_verdict(case: Case):
    chain = case.custody_summary()["audit_chain"]
    assert chain["valid"] is True
    assert chain["total"] > 0


def test_report_renders_the_chain_verdict(case: Case):
    from triage.report import generate_report

    out = generate_report(case.root)
    html = out.read_text(encoding="utf-8")
    assert "Audit-log tamper evidence" in html
    assert "AUDIT CHAIN VERIFIED" in html
    # The honest limitation must travel with the claim.
    assert "not non-repudiation" in html
    assert "out of band" in html


def test_report_shows_a_broken_chain_as_broken(case: Case):
    from triage.report import generate_report

    path = case.root / "audit.jsonl"
    lines = path.read_text().splitlines()
    entry = json.loads(lines[3])
    entry["alters_device"] = True  # retro-fit a device modification that never happened
    entry["detail"] = "pm grant android.permission.READ_SMS"
    lines[3] = json.dumps(entry)
    path.write_text("\n".join(lines) + "\n")

    html = generate_report(case.root).read_text(encoding="utf-8")
    assert "AUDIT CHAIN BROKEN" in html
    assert "AUDIT CHAIN VERIFIED" not in html


# ---------------------------------------------------------------------------
# Sealed export
# ---------------------------------------------------------------------------
def test_export_manifest_carries_chain_verification_and_seal(case: Case):
    data = create_integrity_manifest(case.root)
    assert data["audit_chain"]["valid"] is True
    assert data["audit_chain_seal"]["chain_head"] == data["audit_chain"]["head"]
    assert data["audit_chain_seal"]["algorithm"] == "sha256"


def test_verification_txt_contains_the_chain_head_and_out_of_band_warning(case: Case):
    out = export_case(case.root)
    with zipfile.ZipFile(out) as zf:
        text = zf.read("VERIFICATION.txt").decode("utf-8")
    assert "Audit-log tamper evidence (hash chain):" in text
    assert "Status: VALID" in text
    assert case.audit_seal()["chain_head"] in text
    assert "out of band" in text.lower()


def test_verification_txt_flags_a_broken_chain(case: Case):
    path = case.root / "audit.jsonl"
    lines = path.read_text().splitlines()
    del lines[2]
    path.write_text("\n".join(lines) + "\n")

    out = export_case(case.root)
    with zipfile.ZipFile(out) as zf:
        text = zf.read("VERIFICATION.txt").decode("utf-8")
    assert "Status: BROKEN" in text
    assert "Status: VALID" not in text


def test_reserialising_a_line_without_changing_it_still_verifies(case: Case):
    """Canonicalisation must ignore formatting, or the chain would false-positive.

    An audit line rewritten by a tool that reorders keys or changes whitespace has not
    been tampered with. If that broke verification, every real broken-chain report would
    be dismissed as noise — so this must pass, and only a CONTENT change may fail.
    """
    path = case.root / "audit.jsonl"
    lines = path.read_text().splitlines()
    entry = json.loads(lines[4])
    reordered = {k: entry[k] for k in sorted(entry, reverse=True)}
    lines[4] = json.dumps(reordered, indent=None, separators=(", ", ": "))
    path.write_text("\n".join(lines) + "\n")

    assert verify_chain(path)["valid"] is True


def test_chain_survives_unicode_and_newlines_in_details(tmp_path: Path):
    c = Case.create(tmp_path / "cases", CaseMeta(case_id="UNI-1", examiner="निरीक्षक"))
    c.log("test.event", "मैसेज मिटाया गया\nsecond line\ttab", tier="tier0", extra_key="✓")
    c.log("test.event", 'quote " and backslash \\ and emoji 🔒', tier="tier0")
    assert c.verify_audit_chain()["valid"] is True
