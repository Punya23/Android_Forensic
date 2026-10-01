"""Derived analyses must follow the data they are derived from.

run_acquisition() computes graph / flags / timeline / risk / financial trail once. The export
import routes then changed the message pool and left all of them stale, and nothing could
re-derive them — so an imported WhatsApp chat showed up in Messages but never in the
communication graph, the timeline or the risk verdict.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.test_phase2_pipeline import _run  # noqa: E402
from tests.test_whatsapp_batch_import_route import _make_server_app  # noqa: E402
from triage import rebuild  # noqa: E402
from triage.custody import Case, CaseMeta  # noqa: E402


def _msg(app: str, sender: str, body: str, ts: str) -> dict:
    return {"app": app, "sender": sender, "body": body, "timestamp": ts, "direction": "unknown",
            "confidence": "live", "source_file": "export.zip", "provenance": "imported export", "flags": []}


ADDED = [
    _msg("instagram", "ig_stranger", "send the bitcoin payment tonight", "2026-07-07T09:00:00Z"),
    _msg("sms", "VM-HDFCBK", "Rs.750 debited from A/c XX4471 to VPA shop@paytm. UPI Ref: 412398765432",
         "2026-07-07T09:05:00Z"),
]


@pytest.fixture()
def case(tmp_path):
    return Case.open(_run(tmp_path))


def _derived_bytes(case: Case) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in case.derived_dir.glob("*.json")}


def test_imported_messages_flow_into_every_message_derived_dataset(case):
    before = {n: case.read_derived(n) for n in ("messages", "timeline", "flags")}

    summary = rebuild.apply_imported_messages(case, ADDED)

    assert len(case.read_derived("messages")) == len(before["messages"]) + 2
    assert summary["added"] == 2 and summary["messages"] == len(before["messages"]) + 2

    stranger = [n for n in case.read_derived("graph")["nodes"] if n["label"] == "ig_stranger"]
    assert stranger and "instagram" in stranger[0]["channels"]

    assert any(f["term"] == "bitcoin" for f in case.read_derived("flags")[len(before["flags"]):])

    timeline = case.read_derived("timeline")
    assert len(timeline) == len(before["timeline"]) + 2
    dated = [e["timestamp"] for e in timeline if e["timestamp"]]
    assert dated == sorted(dated)
    assert all(not e["timestamp"] for e in timeline[len(dated):]), "undated events stay at the end"

    assert [t["amount"] for t in case.read_derived("upi_transactions")] == [750.0]
    assert case.read_derived("risk")["level"] in ("red", "amber", "green")


def test_rebuild_refreshes_a_graph_written_by_older_code(case):
    graph = case.read_derived("graph")
    del graph["stats"]["identity_normalisation"]
    case.write_derived("graph", graph)

    rebuild.rebuild_analysis(case)

    assert "identity_normalisation" in case.read_derived("graph")["stats"]


def test_rebuild_with_nothing_added_leaves_messages_timeline_and_flags_alone(case):
    before = {n: case.read_derived(n) for n in ("messages", "timeline", "flags")}
    rebuild.rebuild_analysis(case)
    assert {n: case.read_derived(n) for n in before} == before


def test_a_failing_analysis_writes_nothing(case, monkeypatch):
    def boom(**_):
        raise RuntimeError("graph builder broke")

    monkeypatch.setattr(rebuild, "build_communication_graph", boom)
    before = _derived_bytes(case)

    with pytest.raises(RuntimeError, match="graph builder broke"):
        rebuild.apply_imported_messages(case, ADDED)

    assert _derived_bytes(case) == before, "an import must not be half-applied"


# --- through the HTTP routes ------------------------------------------------------------
@pytest.fixture()
def client(tmp_path):
    app, _ = _make_server_app(tmp_path)
    Case.create(tmp_path, CaseMeta(case_id="IMP-1", examiner="Insp. Rao"))
    c = app.test_client()
    data = c.post("/api/auth/login", json={"username": "admin", "password": "snagr-demo"}).get_json()
    c.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {data['token']}"
    c.environ_base["HTTP_X_CSRF_TOKEN"] = data["csrf_token"]
    return c


def test_whatsapp_import_route_updates_graph_and_timeline(client):
    chat = (
        "[06/07/2026, 09:00:00] Rahul: Good morning!\n"
        "[06/07/2026, 09:01:30] Priya: Morning, did the cash arrive?\n"
    ).encode()
    resp = client.post("/api/case/IMP-1/import/whatsapp",
                       data={"file": (io.BytesIO(chat), "WhatsApp Chat with Priya/_chat.txt")},
                       content_type="multipart/form-data")
    assert resp.status_code == 200, resp.data

    labels = {n["label"] for n in client.get("/api/case/IMP-1/graph").get_json()["nodes"]}
    assert {"Rahul", "Priya"} <= labels
    assert len(client.get("/api/case/IMP-1/timeline").get_json()) == 2
    assert any(f["term"] == "cash" for f in client.get("/api/case/IMP-1/flags").get_json())


def test_rebuild_route_reports_what_it_recomputed(client):
    resp = client.post("/api/case/IMP-1/rebuild")
    assert resp.status_code == 200, resp.data
    assert {"messages", "participants", "flags", "upi_transactions"} <= set(resp.get_json())
    assert client.post("/api/case/NOPE/rebuild").status_code == 404
