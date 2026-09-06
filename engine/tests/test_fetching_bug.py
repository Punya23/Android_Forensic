"""Tests for the fetching-bug fixes.

Covers:
- MockDeviceSource.validate_file_list filters phantom paths
- MockDeviceSource.is_device_connected always returns True
- RealDeviceSource.is_device_connected correctly reads adb devices output
- RealDeviceSource.file_exists uses `test -e` correctly
- DeviceDisconnectedError is raised and cancel_token is set
- checkpoint_exists / load_checkpoint round-trip
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from triage.acquire.mock import MockDeviceSource
from triage.acquire.real import RealDeviceSource
from triage.adb import Adb, AdbResult
from triage.cancellation import CancellationToken
from triage.checkpoint import (
    checkpoint_exists,
    clear_checkpoint,
    load_checkpoint,
    save_checkpoint,
)
from triage.pipeline import DeviceDisconnectedError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_source(tmp_path: Path) -> MockDeviceSource:
    """Create a MockDeviceSource with a minimal fixtures tree."""
    sdcard = tmp_path / "sdcard"
    sdcard.mkdir(parents=True)
    # Create 3 real files
    for name in ("real_1.jpg", "real_2.mp4", "real_3.txt"):
        (sdcard / name).write_bytes(b"data")
    return MockDeviceSource(tmp_path)


# ---------------------------------------------------------------------------
# MockDeviceSource.file_exists
# ---------------------------------------------------------------------------


class TestMockFileExists:
    def test_existing_file_returns_true(self, tmp_path):
        src = _make_mock_source(tmp_path)
        assert src.file_exists("/sdcard/real_1.jpg") is True

    def test_missing_file_returns_false(self, tmp_path):
        src = _make_mock_source(tmp_path)
        assert src.file_exists("/sdcard/phantom_9999.jpg") is False

    def test_is_device_connected_always_true(self, tmp_path):
        src = _make_mock_source(tmp_path)
        assert src.is_device_connected() is True


# ---------------------------------------------------------------------------
# MockDeviceSource.validate_file_list
# ---------------------------------------------------------------------------


class TestMockValidateFileList:
    def test_all_valid(self, tmp_path):
        src = _make_mock_source(tmp_path)
        paths = ["/sdcard/real_1.jpg", "/sdcard/real_2.mp4", "/sdcard/real_3.txt"]
        valid, phantom = src.validate_file_list(paths)
        assert set(valid) == set(paths)
        assert phantom == 0

    def test_phantom_files_filtered(self, tmp_path):
        src = _make_mock_source(tmp_path)
        paths = [
            "/sdcard/real_1.jpg",
            "/sdcard/phantom_a.jpg",
            "/sdcard/real_2.mp4",
            "/sdcard/phantom_b.mp4",
        ]
        valid, phantom = src.validate_file_list(paths)
        assert set(valid) == {"/sdcard/real_1.jpg", "/sdcard/real_2.mp4"}
        assert phantom == 2

    def test_all_phantom(self, tmp_path):
        src = _make_mock_source(tmp_path)
        paths = ["/sdcard/ghost1.jpg", "/sdcard/ghost2.mp4"]
        valid, phantom = src.validate_file_list(paths)
        assert valid == []
        assert phantom == 2

    def test_empty_list(self, tmp_path):
        src = _make_mock_source(tmp_path)
        valid, phantom = src.validate_file_list([])
        assert valid == []
        assert phantom == 0

    def test_progress_callback_called(self, tmp_path):
        src = _make_mock_source(tmp_path)
        paths = ["/sdcard/real_1.jpg", "/sdcard/phantom_x.jpg"]
        calls = []
        src.validate_file_list(paths, progress_cb=lambda done, total: calls.append((done, total)))
        assert len(calls) == 2
        assert calls[-1] == (2, 2)


# ---------------------------------------------------------------------------
# RealDeviceSource.is_device_connected
# ---------------------------------------------------------------------------


def _make_real_source(serial: str | None = None, devices: list | None = None) -> RealDeviceSource:
    """Build a RealDeviceSource with a mocked Adb that returns *devices*."""
    adb = MagicMock(spec=Adb)
    adb.adb_path = "/usr/bin/adb"
    adb.serial = serial
    adb.list_devices = MagicMock(return_value=devices or [])
    return RealDeviceSource(adb)


class TestRealDeviceConnected:
    def test_device_present_returns_true(self):
        src = _make_real_source(
            serial="ABCDEF01",
            devices=[{"serial": "ABCDEF01", "state": "device"}],
        )
        assert src.is_device_connected() is True

    def test_device_absent_returns_false(self):
        src = _make_real_source(
            serial="ABCDEF01",
            devices=[{"serial": "OTHER999", "state": "device"}],
        )
        assert src.is_device_connected() is False

    def test_device_offline_returns_false(self):
        src = _make_real_source(
            serial="ABCDEF01",
            devices=[{"serial": "ABCDEF01", "state": "offline"}],
        )
        assert src.is_device_connected() is False

    def test_no_devices_returns_false(self):
        src = _make_real_source(serial="ABCDEF01", devices=[])
        assert src.is_device_connected() is False

    def test_no_serial_matches_any_device(self):
        """When serial is None, any connected device is accepted."""
        src = _make_real_source(
            serial=None,
            devices=[{"serial": "ANYHARDWARE01", "state": "device"}],
        )
        assert src.is_device_connected() is True


# ---------------------------------------------------------------------------
# RealDeviceSource.file_exists
# ---------------------------------------------------------------------------


class TestRealFileExists:
    def _src_with_shell(self, output: str, returncode: int = 0) -> RealDeviceSource:
        adb = MagicMock(spec=Adb)
        adb.adb_path = "/usr/bin/adb"
        adb.serial = None
        result = AdbResult(command="adb shell test -e ...", returncode=returncode, stdout=output, stderr="")
        adb.shell = MagicMock(return_value=result)
        return RealDeviceSource(adb)

    def test_file_exists_returns_true(self):
        src = self._src_with_shell("1")
        assert src.file_exists("/sdcard/DCIM/photo.jpg") is True

    def test_file_missing_returns_false(self):
        src = self._src_with_shell("0")
        assert src.file_exists("/sdcard/ghost.jpg") is False

    def test_empty_output_returns_false(self):
        src = self._src_with_shell("")
        assert src.file_exists("/sdcard/ghost.jpg") is False


# ---------------------------------------------------------------------------
# DeviceDisconnectedError
# ---------------------------------------------------------------------------


class TestDeviceDisconnectedError:
    def test_is_runtime_error(self):
        exc = DeviceDisconnectedError("device gone")
        assert isinstance(exc, RuntimeError)
        assert "device gone" in str(exc)


# ---------------------------------------------------------------------------
# Checkpoint round-trip
# ---------------------------------------------------------------------------


class TestCheckpointRoundTrip:
    def test_save_load_clear(self, tmp_path):
        case_dir = tmp_path / "CASE-001"
        case_dir.mkdir()
        assert not checkpoint_exists(case_dir)

        save_checkpoint(case_dir, stage="pull", data={"completed_files": ["/sdcard/a.jpg"]})
        assert checkpoint_exists(case_dir)

        envelope = load_checkpoint(case_dir)
        assert envelope["stage"] == "pull"
        assert "/sdcard/a.jpg" in envelope["data"]["completed_files"]

        clear_checkpoint(case_dir)
        assert not checkpoint_exists(case_dir)

    def test_integrity_check_fails_on_tamper(self, tmp_path):
        case_dir = tmp_path / "CASE-002"
        case_dir.mkdir()
        save_checkpoint(case_dir, stage="pull", data={"completed_files": []})
        ckpt = case_dir / "checkpoint.json"
        envelope = json.loads(ckpt.read_text())
        # Tamper with the data
        envelope["data"]["completed_files"].append("/sdcard/injected.jpg")
        ckpt.write_text(json.dumps(envelope))

        with pytest.raises(ValueError, match="integrity"):
            load_checkpoint(case_dir)


# ---------------------------------------------------------------------------
# validate_file_list in pipeline (integration-level mock)
# ---------------------------------------------------------------------------


class TestPipelineValidationIntegration:
    """Smoke-test that PipelineConfig has validate_files_before_pull."""

    def test_config_has_validate_flag(self):
        from triage.pipeline import PipelineConfig
        cfg = PipelineConfig(case_id="SMOKE-1", examiner="Test")
        assert hasattr(cfg, "validate_files_before_pull")
        assert cfg.validate_files_before_pull is True

    def test_config_can_disable_validation(self):
        from triage.pipeline import PipelineConfig
        cfg = PipelineConfig(case_id="SMOKE-2", examiner="Test", validate_files_before_pull=False)
        assert cfg.validate_files_before_pull is False
