"""The short report: 2-3 printed pages, case-specific, honest, and safe to render.

The full report is ~100 pages; the summary keeps the verdict, what was collected, the
strongest findings, key people and the limits, and points at the full report for the rest.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.test_whatsapp_batch_import_route import _make_server_app  # noqa: E402
from triage.custody import Case, CaseMeta  # noqa: E402
from triage.report.summary_report import build_summary_report  # noqa: E402


def _case(tmp_path, **meta):
    case = Case.create(tmp_path, CaseMeta(case_id="SUM-1", examiner="Insp. Rao", **meta))
    case.write_derived("risk", {"level": "red", "score": 91, "headline": "Strong indicators present", "reasons": [
        {"points": 40, "label": "critical keyword hits", "detail": "12 hit(s)", "severity": "critical"}]})
    case.write_derived("flags", [
        {"kind": "keyword", "term": "weapon", "context": "bring the weapon tonight", "location": "wa msg", "severity": "critical"},
        {"kind": "keyword", "term": "weapon", "context": "weapon again", "location": "wa msg", "severity": "critical"},
        {"kind": "keyword", "term": "<script>x</script>", "context": "c", "location": "l", "severity": "warn"},
    ])
    case.write_derived("messages", [{"app": "whatsapp", "sender": "A", "body": "hi", "flags": []}] * 3)
    case.write_derived("graph", {"nodes": [], "edges": [], "stats": {"participants": 2, "interactions": 5, "channels": [],
        "top_contacts": [{"id": "name:A", "label": "A", "weight": 5, "channels": ["whatsapp"]}]}})
    return case


def test_summary_has_verdict_counts_findings_and_people(tmp_path):
    html = build_summary_report(_case(tmp_path))
    assert "SUM-1" in html and "Insp. Rao" in html
    assert "91" in html and "Strong indicators present" in html
    assert "weapon" in html and "2 hits" in html
    assert ">A<" in html or "A </" in html or "A</" in html


def test_summary_escapes_untrusted_text(tmp_path):
    html = build_summary_report(_case(tmp_path))
    assert "<script>x</script>" not in html and "&lt;script&gt;" in html


def test_summary_is_short_and_labels_demo_data(tmp_path):
    html = build_summary_report(_case(tmp_path, ))
    assert len(html) < 40_000
    case = Case.create(tmp_path, CaseMeta(case_id="SUM-2", examiner="x", pre_state={"note": "MOCK DEVICE synthetic"}))
    assert "DEMONSTRATION DATA" in build_summary_report(case)
    assert "DEMONSTRATION DATA" not in html


def test_summary_route_serves_html(tmp_path):
    app, _ = _make_server_app(tmp_path)
    _case(tmp_path)
    c = app.test_client()
    t = c.post("/api/auth/login", json={"username": "admin", "password": "snagr-demo"}).get_json()
    c.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {t['token']}"
    r = c.get("/api/case/SUM-1/report/summary")
    assert r.status_code == 200 and r.mimetype == "text/html" and b"SUM-1" in r.data


def test_summary_loads_in_an_iframe_without_an_auth_header(tmp_path):
    # The report view embeds it as <iframe src>, which cannot send Authorization — like /report.
    app, _ = _make_server_app(tmp_path)
    _case(tmp_path)
    r = app.test_client().get("/api/case/SUM-1/report/summary")
    assert r.status_code == 200 and r.mimetype == "text/html"


def test_summary_discloses_a_capped_run(tmp_path):
    case = _case(tmp_path)
    assert "PARTIAL COLLECTION" not in build_summary_report(case)
    case.write_derived("acquisition_caps", {"selected_files": 3, "available_files": 40, "selected_bytes": 3_000_000,
        "available_bytes": 90_000_000, "skipped_files": 37, "total_cap_bytes": 5 * 1024**3, "bucket_cap_bytes": 100 * 1024**2})
    html = build_summary_report(case)
    assert "PARTIAL COLLECTION" in html and "3 of 40" in html and "37 files were left" in html
