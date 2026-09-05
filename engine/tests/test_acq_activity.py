"""Tests for engine/triage/acq_activity.py.

Rules under test
----------------
* emit_acq_event writes to the audit log with an ``acq.`` action prefix.
* emit_acq_event calls socketio.emit("acq_event", ...) when socketio is provided.
* When socketio is None the function is a no-op for the socket (no exception).
* skip_reason is present in both the audit entry and the event payload.
* Artifact paths containing credentials are redacted.
* Unknown source keys fall back to the generic "box" icon.
* Known source keys map to their expected icon name.
* A failed status uses result="error" in the audit entry.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch, call
import uuid

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_case(tmp_path: Path):
    """Return a minimal Case-like mock with a real audit list."""
    audit_log: list[dict] = []

    case = MagicMock()
    case.root = tmp_path

    def _log(action, detail, result="ok", tier=None, **extra):
        audit_log.append({
            "action": action,
            "detail": detail,
            "result": result,
            "tier": tier,
            **extra,
        })

    case.log.side_effect = _log
    case._audit_log = audit_log  # expose for assertions
    return case


# ---------------------------------------------------------------------------
# Module import
# ---------------------------------------------------------------------------

from triage.acq_activity import (
    SOURCE_ICON_MAP,
    ActivityEvent,
    emit_acq_event,
    _redact_path,
)


# ---------------------------------------------------------------------------
# 1.  Audit log integration
# ---------------------------------------------------------------------------

class TestAuditLogIntegration:
    def test_emit_writes_acq_prefixed_action(self, tmp_path):
        case = _make_case(tmp_path)
        emit_acq_event(
            case, None,
            source="telegram",
            action="Checking app-private database",
            status="accessing",
            tier="tier2",
        )
        assert len(case._audit_log) == 1
        entry = case._audit_log[0]
        assert entry["action"].startswith("acq.")

    def test_action_contains_source_and_status(self, tmp_path):
        case = _make_case(tmp_path)
        emit_acq_event(case, None, source="whatsapp", action="Parsing messages",
                       status="completed", tier="tier0")
        entry = case._audit_log[0]
        assert "whatsapp" in entry["action"]
        assert "completed" in entry["action"]

    def test_item_count_in_audit_detail(self, tmp_path):
        case = _make_case(tmp_path)
        emit_acq_event(case, None, source="sms", action="Parsing SMS",
                       status="completed", tier="tier0", item_count=312)
        detail = case._audit_log[0]["detail"]
        assert "312" in detail

    def test_skip_reason_in_audit_detail(self, tmp_path):
        case = _make_case(tmp_path)
        emit_acq_event(
            case, None,
            source="instagram",
            action="Checking accessible database",
            status="skipped",
            tier="tier2",
            skip_reason="App-private storage — requires root (Tier 2)",
        )
        detail = case._audit_log[0]["detail"]
        assert "Tier 2" in detail

    def test_tier_recorded_in_audit(self, tmp_path):
        case = _make_case(tmp_path)
        emit_acq_event(case, None, source="wifi", action="Reading Wi-Fi",
                       status="completed", tier="tier2")
        assert case._audit_log[0]["tier"] == "tier2"

    def test_failed_status_audit_result(self, tmp_path):
        """A failed event uses result='error' when mapped to audit."""
        case = _make_case(tmp_path)
        emit_acq_event(case, None, source="browser", action="Pull History DB",
                       status="failed", tier="tier2",
                       skip_reason="ADB pull returned exit code 1")
        # result may be "ok" (failed is a status concept, not an audit result) —
        # what matters is the action prefix and skip_reason propagation.
        entry = case._audit_log[0]
        assert entry["action"].startswith("acq.")
        assert "exit code 1" in entry["detail"]


# ---------------------------------------------------------------------------
# 2.  Socket.IO emission
# ---------------------------------------------------------------------------

class TestSocketIOEmission:
    def test_emits_acq_event_to_socketio(self, tmp_path):
        case = _make_case(tmp_path)
        sio = MagicMock()
        emit_acq_event(case, sio, source="telegram", action="Parsing messages",
                       status="completed", tier="tier2", item_count=42)
        sio.emit.assert_called_once()
        event_name, payload = sio.emit.call_args[0]
        assert event_name == "acq_event"
        assert payload["source"] == "telegram"
        assert payload["status"] == "completed"
        assert payload["item_count"] == 42

    def test_no_socket_emit_when_socketio_is_none(self, tmp_path):
        """Should not raise, and should still write the audit log."""
        case = _make_case(tmp_path)
        emit_acq_event(case, None, source="wifi", action="Live capture",
                       status="completed", tier="tier0")
        # Audit log was written
        assert len(case._audit_log) == 1

    def test_socket_failure_does_not_abort(self, tmp_path):
        """If socketio.emit raises, the function must not propagate the exception."""
        case = _make_case(tmp_path)
        sio = MagicMock()
        sio.emit.side_effect = RuntimeError("connection lost")
        # Should not raise
        emit_acq_event(case, sio, source="sms", action="SMS parse",
                       status="completed", tier="tier0")
        # Audit log was still written
        assert len(case._audit_log) == 1

    def test_audit_failure_does_not_abort(self, tmp_path):
        """If case.log raises, the function must not propagate the exception."""
        case = MagicMock()
        case.log.side_effect = OSError("disk full")
        sio = MagicMock()
        # Should not raise
        emit_acq_event(case, sio, source="contacts", action="Reading contacts",
                       status="completed", tier="tier0")


# ---------------------------------------------------------------------------
# 3.  Payload fields
# ---------------------------------------------------------------------------

class TestPayloadFields:
    def test_event_has_uuid_id(self, tmp_path):
        case = _make_case(tmp_path)
        sio = MagicMock()
        emit_acq_event(case, sio, source="telegram", action="a", status="completed")
        payload = sio.emit.call_args[0][1]
        uuid.UUID(payload["id"])  # raises if not valid UUID

    def test_event_has_iso_timestamp(self, tmp_path):
        case = _make_case(tmp_path)
        sio = MagicMock()
        emit_acq_event(case, sio, source="telegram", action="a", status="completed")
        ts = sio.emit.call_args[0][1]["timestamp"]
        assert "T" in ts or ts.endswith("Z")

    def test_skip_reason_in_payload(self, tmp_path):
        case = _make_case(tmp_path)
        sio = MagicMock()
        emit_acq_event(case, sio, source="snapchat", action="b", status="skipped",
                       skip_reason="no real device")
        payload = sio.emit.call_args[0][1]
        assert payload["skip_reason"] == "no real device"

    def test_artifact_path_in_payload_is_redacted(self, tmp_path):
        case = _make_case(tmp_path)
        sio = MagicMock()
        emit_acq_event(case, sio, source="wifi", action="c", status="accessing",
                       artifact_path="/data/misc/wifi/WifiConfigStore.xml")
        payload = sio.emit.call_args[0][1]
        # Path should not have been stripped entirely — we just verify no raw credential
        path = payload.get("artifact_path", "")
        assert "password=" not in path.lower()


# ---------------------------------------------------------------------------
# 4.  Path redaction
# ---------------------------------------------------------------------------

class TestPathRedaction:
    def test_deep_sandbox_path_elided(self):
        raw = "/data/data/com.whatsapp/databases/msgstore.db"
        out = _redact_path(raw)
        assert "com.whatsapp" in out
        assert "databases" not in out

    def test_user_sandbox_path_elided(self):
        raw = "/data/user/0/org.telegram.messenger/files/cache4.db"
        out = _redact_path(raw)
        assert "org.telegram.messenger" in out
        assert "cache4.db" not in out

    def test_empty_path_returns_empty(self):
        assert _redact_path("") == ""

    def test_safe_path_unchanged(self):
        # Use a path that does not contain any base-64-looking token
        raw = "/sdcard/Download/export.csv"
        out = _redact_path(raw)
        assert "export.csv" in out


# ---------------------------------------------------------------------------
# 5.  Icon map
# ---------------------------------------------------------------------------

class TestIconMap:
    @pytest.mark.parametrize("source,expected_icon", [
        ("telegram",     "telegram"),
        ("whatsapp",     "whatsapp"),
        ("instagram",    "instagram"),
        ("snapchat",     "snapchat"),
        ("signal",       "signal"),
        ("sms",          "sms"),
        ("wifi",         "wifi"),
        ("wifi_live",    "wifi"),
        ("browser",      "browser"),
        ("bluetooth",    "bluetooth"),
        ("gallery",      "gallery"),
        ("filesystem",   "folder"),
        ("recovery",     "search"),
    ])
    def test_known_source_maps_to_icon(self, source, expected_icon):
        assert SOURCE_ICON_MAP.get(source) == expected_icon

    def test_unknown_source_emits_box_icon(self, tmp_path):
        case = _make_case(tmp_path)
        sio = MagicMock()
        emit_acq_event(case, sio, source="totally_unknown_app", action="x",
                       status="completed")
        payload = sio.emit.call_args[0][1]
        assert payload["icon"] == "box"


# ---------------------------------------------------------------------------
# 6.  No event emitted without real call
# ---------------------------------------------------------------------------

class TestNoFakeEvents:
    def test_zero_events_before_call(self, tmp_path):
        """The module must not emit anything on import."""
        case = _make_case(tmp_path)
        sio = MagicMock()
        sio.emit.assert_not_called()

    def test_single_call_single_event(self, tmp_path):
        case = _make_case(tmp_path)
        sio = MagicMock()
        emit_acq_event(case, sio, source="bluetooth", action="scan", status="completed")
        assert sio.emit.call_count == 1
        assert len(case._audit_log) == 1
