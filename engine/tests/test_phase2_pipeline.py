"""Regression tests for the "Phase 2" forensic stages as wired into run_acquisition().

Those stages shipped dead: each module was unit-tested with hand-built dicts
(``test_phase2_modules.py``), but the pipeline handed them dataclass objects and the wrong
field names, so four of the five raised ``AttributeError`` on every run — and the failure was
swallowed into an audit-log line nobody reads. These tests drive the real pipeline end to end.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.make_corpus import build  # noqa: E402
from triage.acquire import MockDeviceSource  # noqa: E402
from triage.forensics.financial import detect_upi_transactions  # noqa: E402
from triage.forensics.location_enhanced import analyze_visit_durations  # noqa: E402
from triage.parsers.telegram_advanced import (  # noqa: E402
    analyze_telegram_groups,
    detect_telegram_bots,
)
from triage.parsers.whatsapp_advanced import (  # noqa: E402
    analyze_whatsapp_reactions,
    detect_whatsapp_admins,
)
from triage.pipeline import PipelineConfig, run_acquisition  # noqa: E402

UPI_DEBIT_SMS = {
    "address": "VM-HDFCBK",
    "body": "Rs.750 debited from A/c XX4471 to VPA shop@paytm. UPI Ref: 412398765432",
    "type": 1,
    "date": 1751826090000,
}


def _run(tmp_path: Path, extra_sms: list[dict] | None = None) -> Path:
    corpus = tmp_path / "device"
    corpus.mkdir()
    build(corpus)
    if extra_sms:
        sms_file = next(corpus.rglob("sms.json"))
        sms_file.write_text(json.dumps(json.loads(sms_file.read_text()) + extra_sms))
    cfg = PipelineConfig(
        case_id="PH2-001",
        examiner="Tester",
        legal_authority="warrant#1",
        cases_root=tmp_path / "cases",
    )
    return Path(run_acquisition(MockDeviceSource(corpus), cfg)["case_dir"])


@pytest.fixture()
def case_dir(tmp_path):
    return _run(tmp_path, [UPI_DEBIT_SMS])


def _derived(case_dir: Path, name: str):
    return json.loads((case_dir / "derived" / f"{name}.json").read_text())


# --- the pipeline stages must actually run --------------------------------------------
def test_no_phase2_stage_errors(case_dir):
    """Every `phase2.*` audit entry must be a success — a swallowed AttributeError is a
    stage that silently produced nothing."""
    entries = [json.loads(line) for line in (case_dir / "audit.jsonl").read_text().splitlines()]
    failed = [e for e in entries if str(e.get("action", "")).startswith("phase2.") and e.get("result") == "error"]
    assert not failed, [(e["action"], e.get("detail") or e.get("message")) for e in failed]


def test_upi_payment_reaches_derived_dataset(case_dir):
    txns = _derived(case_dir, "upi_transactions")
    assert [t["amount"] for t in txns] == [750.0]
    assert txns[0]["upi_id"] == "shop@paytm"
    assert txns[0]["transaction_id"] == "412398765432"
    assert "shop@paytm" in {r["to"] for flows in _derived(case_dir, "money_trail").values() for r in flows}


def test_visit_durations_are_computed_from_pipeline_locations(case_dir):
    visits = _derived(case_dir, "visit_durations")
    assert visits and all({"lat", "lon", "duration_seconds"} <= set(v) for v in visits)


def test_no_fir_or_expert_report_is_generated(case_dir):
    """The examiner's HTML report is the police-facing deliverable; the engine must not
    auto-draft an FIR (accused/incident unknown to the tool) or a pre-signed declaration."""
    derived = {p.stem for p in (case_dir / "derived").glob("*.json")}
    assert not derived & {"fir_draft", "expert_report", "matched_statutes", "whatsapp_call_analysis"}


# --- the report is what an investigating officer reads to draft the FIR ----------------
def test_report_lists_each_payment_with_its_source_and_a_verification_caveat(case_dir):
    html = (case_dir / "report.html").read_text()
    assert "Financial trail" in html
    for fact in ("shop@paytm", "412398765432", "750.00", "device owner", "Rs.750 debited from A/c XX4471"):
        assert fact in html, fact
    assert "not verified against bank records" in html


def test_report_has_no_financial_section_when_no_payment_was_detected(tmp_path):
    assert "Financial trail" not in (_run(tmp_path) / "report.html").read_text()


# --- API exposure: a dataset the pipeline writes must be readable over the API ---------
def test_every_dataset_the_pipeline_writes_is_servable():
    """`GET /api/case/<id>/<dataset>` 404s anything outside its allowlist, so a dataset added
    to run_acquisition() without a matching entry is computed, saved, and invisible."""
    import re

    root = Path(__file__).resolve().parent.parent / "triage"
    written = set(re.findall(r'write_derived\(\s*"([a-z_0-9]+)"', (root / "pipeline.py").read_text()))
    server = (root / "server.py").read_text()
    allow = set()
    for name in ("list_sets", "obj_sets"):
        block = re.search(name + r"\s*=\s*\{(.*?)\n\s*\}", server, re.S).group(1)
        allow |= set(re.findall(r'"([a-z_0-9]+)"', re.sub(r"#.*", "", block)))
    assert not written - allow, sorted(written - allow)


# --- financial: direction + field contract --------------------------------------------
def test_bank_debit_sms_reads_the_body_field_and_is_outgoing():
    # Message.to_dict() carries `body`, not `text`, and the payer is the account holder —
    # the bank that sent the SMS did not pay anyone.
    (txn,) = detect_upi_transactions([{
        "app": "sms", "sender": "VM-HDFCBK", "timestamp": "2026-07-06T10:00:00Z",
        "body": UPI_DEBIT_SMS["body"],
    }])
    assert txn["is_credit"] is False
    assert (txn["sender"], txn["receiver"]) == ("device owner", "shop@paytm")


def test_bank_credit_sms_is_incoming():
    (txn,) = detect_upi_transactions([{
        "app": "sms", "sender": "VM-HDFCBK", "timestamp": "2026-07-06T10:00:00Z",
        "body": "Rs.1200 credited to A/c XX4471 from VPA friend@ybl UPI Ref: 412398765999",
    }])
    assert txn["is_credit"] is True
    assert (txn["sender"], txn["receiver"]) == ("friend@ybl", "device owner")


# --- location: the pipeline's dicts use latitude/longitude ----------------------------
def test_visit_durations_accept_latitude_longitude_keys():
    (visit,) = analyze_visit_durations([
        {"latitude": 19.076, "longitude": 72.8777, "timestamp": "2026-07-06T10:00:00Z"},
        {"latitude": 19.0761, "longitude": 72.8778, "timestamp": "2026-07-06T10:30:00Z"},
    ])
    assert visit["duration_seconds"] == 1800 and visit["visit_count"] == 2


def test_epoch_aware_and_naive_iso_sightings_are_comparable():
    start = datetime(2026, 7, 6, 10, 0, tzinfo=timezone.utc)
    (visit,) = analyze_visit_durations([
        {"latitude": 19.076, "longitude": 72.8777, "timestamp": start.timestamp()},
        {"latitude": 19.0761, "longitude": 72.8778, "timestamp": (start + timedelta(minutes=20)).isoformat()},
        {"latitude": 19.0761, "longitude": 72.8777, "timestamp": "2026-07-06T10:30:00"},  # naive: read as UTC
    ])
    assert visit["duration_seconds"] == 1800 and visit["visit_count"] == 3


def test_single_sighting_is_not_given_an_invented_duration():
    (visit,) = analyze_visit_durations(
        [{"latitude": 19.076, "longitude": 72.8777, "timestamp": "2026-07-06T10:00:00Z"}]
    )
    assert visit["duration_seconds"] == 0 and visit["single_observation"] is True


# --- evidence integrity: analysers must not write to the stored artifact --------------
def _wal_db(path: Path) -> None:
    """A msgstore.db copy with its WAL sidecar left behind, as a pulled live DB would be."""
    live = path.parent / "live" / path.name
    live.parent.mkdir()
    con = sqlite3.connect(live)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE group_participants (gjid TEXT, jid TEXT, is_admin INTEGER)")
    con.execute("INSERT INTO group_participants VALUES ('g1@g.us', 'a@s.whatsapp.net', 1)")
    con.execute("CREATE TABLE message_reactions (message_row_id INTEGER, reaction_text TEXT, sender_jid TEXT)")
    con.execute("INSERT INTO message_reactions VALUES (7, 'x', 'a@s.whatsapp.net')")
    con.commit()
    shutil.copy(live, path)
    shutil.copy(str(live) + "-wal", str(path) + "-wal")
    con.close()


def _digest(*paths: Path) -> list[str]:
    return [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]


@pytest.mark.parametrize(
    "analyse",
    [analyze_whatsapp_reactions, detect_whatsapp_admins, detect_telegram_bots, analyze_telegram_groups],
)
def test_advanced_analysers_leave_the_stored_database_untouched(tmp_path, analyse):
    db = tmp_path / "msgstore.db"
    _wal_db(db)
    wal = Path(str(db) + "-wal")
    before = _digest(db, wal)
    analyse(str(db))
    assert wal.exists(), "opening the evidence read-write checkpointed and deleted the WAL"
    assert _digest(db, wal) == before
