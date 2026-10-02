"""Synthetic cases must never read as real seizures; placeholder rows are not messages.

A mock-corpus case showed 'acquired by …' on every surface with no hint it was synthetic, and
undecryptable-backup stand-in rows were counted in 'N messages'. One rule (triage.demo) now
drives the Overview, Case History and HTML report.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.test_phase2_pipeline import _run  # noqa: E402
from tests.test_whatsapp_batch_import_route import _make_server_app  # noqa: E402
from triage import registry  # noqa: E402
from triage.custody import Case, CaseMeta  # noqa: E402
from triage.demo import is_demo_case, is_placeholder_message  # noqa: E402
from triage.report.html_report import generate_report  # noqa: E402


def test_rule_matches_mock_note_only():
    assert is_demo_case({"pre_state": {"note": "MOCK DEVICE — synthetic fixtures for demo"}})
    assert not is_demo_case({"pre_state": {"note": "locked on arrival"}})
    assert not is_demo_case({"pre_state": {}})
    assert not is_demo_case({})
    assert is_placeholder_message({"flags": ["metadata_only", "encrypted"]})
    assert not is_placeholder_message({"flags": []})
    assert not is_placeholder_message({})


@pytest.fixture()
def mock_case(tmp_path):
    return Case.open(_run(tmp_path))


def test_mock_acquisition_is_recognised_as_demo(mock_case):
    assert is_demo_case(mock_case.custody_summary()["case"])


def test_case_history_row_is_tagged_demo(mock_case, tmp_path):
    registry.upsert_case(tmp_path, mock_case)
    row = next(r for r in registry.list_cases(tmp_path) if r["case_id"] == mock_case.meta.case_id)
    assert row["device_model"].endswith("[demo]")


def test_report_carries_demonstration_banner(mock_case):
    html = generate_report(mock_case.root).read_text()
    assert "DEMONSTRATION DATA" in html


def test_real_case_is_not_labelled_demo(tmp_path):
    real = Case.create(tmp_path, CaseMeta(case_id="REAL-1", examiner="Insp. Rao"))
    assert not is_demo_case(real.custody_summary()["case"])
    registry.upsert_case(tmp_path, real)
    row = next(r for r in registry.list_cases(tmp_path) if r["case_id"] == "REAL-1")
    assert "[demo]" not in row["device_model"]


def test_overview_counts_exclude_placeholder_rows(tmp_path):
    app, _ = _make_server_app(tmp_path)
    case = Case.create(tmp_path, CaseMeta(case_id="PH-1", examiner="Insp. Rao"))
    base = {"app": "whatsapp", "timestamp": None, "confidence": "live", "source_file": "x", "provenance": "p"}
    case.write_derived(
        "messages",
        [
            {**base, "sender": "a", "body": "hello", "flags": []},
            {**base, "sender": "<encrypted>", "body": "[encrypted backup: key required]", "flags": ["metadata_only"]},
        ],
    )
    c = app.test_client()
    data = c.post("/api/auth/login", json={"username": "admin", "password": "snagr-demo"}).get_json()
    c.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {data['token']}"
    counts = c.get("/api/case/PH-1").get_json()["counts"]
    assert counts["messages"] == 1
    assert counts["message_placeholders"] == 1
