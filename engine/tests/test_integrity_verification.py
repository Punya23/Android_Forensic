"""Integrity-verification regression tests.

These lock down the P0 defect where hash verification silently checked ZERO files
(manifest written as a top-level JSON list keyed ``sha256``; verifier expected a
``{"artifacts": [...]}`` dict keyed ``sha256_hash``). A green run here proves the
integrity guarantee is real: it verifies intact evidence AND catches tampering, the
report renders the verdict, and export re-hashes files at seal time.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.make_corpus import build  # noqa: E402
from triage.acquire import MockDeviceSource  # noqa: E402
from triage.pipeline import PipelineConfig, run_acquisition  # noqa: E402
from triage.forensics.hash_verification import verify_all_hashes  # noqa: E402
from triage.forensics import integrity_report, hash_timeline  # noqa: E402
from triage.report import _generate_hash_verification_section  # noqa: E402
from triage import export  # noqa: E402


@pytest.fixture()
def case_dir(tmp_path):
    dest = tmp_path / "device"
    dest.mkdir()
    build(dest)
    source = MockDeviceSource(dest)
    cfg = PipelineConfig(
        case_id="INTEG-001", examiner="Tester", cases_root=tmp_path / "cases"
    )
    summary = run_acquisition(source, cfg)
    return Path(summary["case_dir"])


def _manifest(case_dir: Path) -> list:
    return json.loads((case_dir / "manifest.json").read_text(encoding="utf-8"))


def _first_stored_file(case_dir: Path) -> Path:
    for art in _manifest(case_dir):
        p = case_dir / art["stored_path"]
        if p.exists() and p.stat().st_size > 0:
            return p
    raise AssertionError("no non-empty stored artifact to tamper with")


# --- P0-1: verification actually reads the manifest and checks files --------


def test_verify_all_hashes_reports_intact_and_nonzero(case_dir):
    v = verify_all_hashes(case_dir)
    # The core regression: this used to be 0 because load_manifest returned [].
    assert v["total_files"] > 0
    assert v["total_files"] == len(
        [a for a in _manifest(case_dir) if (case_dir / a["stored_path"]).exists()]
    )
    assert v["verified"] == v["total_files"]
    assert v["failed"] == 0
    assert v["integrity_status"] == "INTACT"


def test_verify_all_hashes_detects_tampering(case_dir):
    target = _first_stored_file(case_dir)
    target.write_bytes(target.read_bytes() + b"\x00tampered")

    v = verify_all_hashes(case_dir)
    assert v["failed"] >= 1
    assert v["integrity_status"] == "TAMPERED"
    tampered_paths = {f["path"] for f in v["failed_files"]}
    rel = str(target.relative_to(case_dir))
    assert rel in tampered_paths


def test_verify_missing_file_is_a_failure_not_a_pass(case_dir):
    target = _first_stored_file(case_dir)
    target.unlink()
    v = verify_all_hashes(case_dir)
    assert v["integrity_status"] == "TAMPERED"
    assert v["failed"] >= 1


# --- P0-6: the report renders the verdict, never a blank section ------------


def test_report_hash_section_renders_intact(case_dir):
    html = _generate_hash_verification_section(case_dir)
    assert "Evidence Integrity" in html
    assert "INTACT" in html
    assert "TAMPERED" not in html


def test_report_hash_section_renders_tampered(case_dir):
    target = _first_stored_file(case_dir)
    target.write_bytes(b"corrupted")
    html = _generate_hash_verification_section(case_dir)
    assert "TAMPERED" in html
    assert str(target.relative_to(case_dir)) in html


def test_full_report_html_contains_integrity_section(case_dir):
    # run_acquisition already wrote report.html; the section must be present,
    # not swallowed by the old bare `except: pass`.
    report_html = (case_dir / "report.html").read_text(encoding="utf-8")
    assert "Evidence Integrity" in report_html


# --- P0-7: export recomputes hashes at seal time ---------------------------


def test_export_seal_verification_intact(case_dir):
    m = export.create_integrity_manifest(case_dir)
    sv = m["seal_verification"]
    assert sv["overall"] == "INTACT"
    assert sv["total"] > 0
    assert sv["mismatched"] == 0 and sv["missing"] == 0


def test_export_seal_verification_flags_mismatch(case_dir):
    target = _first_stored_file(case_dir)
    target.write_bytes(target.read_bytes() + b"x")
    m = export.create_integrity_manifest(case_dir)
    sv = m["seal_verification"]
    assert sv["overall"] == "COMPROMISED"
    assert sv["mismatched"] >= 1
    bad = [r for r in sv["results"] if r["status"] == "MISMATCH"]
    assert any(r["stored_path"] == str(target.relative_to(case_dir)) for r in bad)


def test_export_zip_verification_txt_carries_seal_status(case_dir, tmp_path):
    out = export.export_case(case_dir, tmp_path / "evidence.zip")
    import zipfile

    with zipfile.ZipFile(out) as zf:
        txt = zf.read("VERIFICATION.txt").decode("utf-8")
    assert "Seal-time re-verification" in txt
    assert "Status: INTACT" in txt


# --- shared-loader consumers no longer silently read an empty manifest -----


def test_integrity_report_summary_nonzero(case_dir):
    summary = integrity_report.get_integrity_summary(case_dir)
    assert summary["total_files"] > 0


def test_hash_timeline_uses_real_manifest(case_dir):
    tl = hash_timeline.get_hash_timeline(case_dir)
    assert len(tl) > 0
    # timestamps come from the manifest's extracted_at, not a 1700000000 placeholder
    assert all(entry["timestamp"] > 0 for entry in tl)


def test_hash_timeline_html_escapes_hostile_filename(case_dir):
    """The manifest's ``path``/``stored_path`` is an attacker-controlled device file
    path (see [[erakshak-honesty-invariants]]). generate_timeline_html() writes a
    standalone hash_timeline.html an examiner opens in a browser, so an unescaped
    path is script execution in the examiner's context on evidence they were handed.
    """
    xss = '<script>alert("pwned")</script>'
    manifest_path = case_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest, "fixture produced an empty manifest"
    manifest[0]["path"] = xss
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    report_path = hash_timeline.generate_timeline_html(case_dir)
    assert report_path
    html = Path(report_path).read_text(encoding="utf-8")

    assert "<script" not in html.lower()
    assert xss not in html
    assert "&lt;script&gt;" in html
