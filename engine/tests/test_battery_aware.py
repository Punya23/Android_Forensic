"""Unit tests for Phase 2 -- battery-aware acquisition.

Covers the two new, independent pieces in isolation (no device/pipeline needed):

  1. ``forensics.battery_priority.should_pull_category`` -- the file-category ->
     battery-band gate used to filter Tier-0 files before they're pulled.
  2. ``battery_monitor.BatteryMonitor`` -- the background poller that feeds it a
     live reading, using a fake source instead of a real/mock device.

Full pipeline integration (battery_aware=True end to end) is exercised manually
against MockDeviceSource -- see the "manual verification" notes in the Phase 2
write-up; a fixtures-backed integration test can be added once Phase 3 lands.
"""

import time

import pytest

from triage.battery_monitor import BatteryMonitor
from triage.forensics.battery_priority import (
    should_pull_category,
    prioritize_artifacts,
    get_artifact_priority,
)


# --- should_pull_category ----------------------------------------------------

def test_database_and_app_export_always_pulled():
    # Critical: holds messages/contacts/calls. Must survive even at 1% battery.
    assert should_pull_category("database", 1) is True
    assert should_pull_category("app-export", 1) is True


def test_document_gated_at_medium_band():
    assert should_pull_category("document", 31) is True
    assert should_pull_category("document", 30) is False
    assert should_pull_category("document", 0) is False


@pytest.mark.parametrize("category", ["image", "video", "audio"])
def test_media_gated_at_low_band(category):
    assert should_pull_category(category, 51) is True
    assert should_pull_category(category, 50) is False
    assert should_pull_category(category, 10) is False


def test_unrecognised_category_treated_as_low():
    assert should_pull_category("other", 60) is True
    assert should_pull_category("other", 40) is False


def test_consistent_with_existing_artifact_bands():
    # should_pull_category must not contradict the pre-existing artifact-level
    # bands used by prioritize_artifacts()/generate_battery_report() -- both
    # should agree that "critical" is always kept and "low" needs >50%.
    assert get_artifact_priority({"category": "messages"}) == "critical"
    kept = prioritize_artifacts(10, [{"category": "messages", "id": "1"}])
    assert len(kept) == 1  # critical survives even at 10%
    assert should_pull_category("database", 10) is True  # file-level agrees


# --- BatteryMonitor -----------------------------------------------------------

class _FakeSource:
    """Minimal stand-in for AcquisitionSource.pre_state()."""

    def __init__(self, levels):
        self._levels = iter(levels)
        self.calls = 0

    def pre_state(self):
        self.calls += 1
        try:
            level = next(self._levels)
        except StopIteration:
            level = None
        return {"battery_level": level}


def test_monitor_seeds_initial_level_before_first_poll():
    source = _FakeSource([50])
    mon = BatteryMonitor(source, interval_s=60, initial_level=77)
    # Not started yet -- must report the seeded value, not None, and must not
    # have touched the source.
    assert mon.level() == 77
    assert source.calls == 0


def test_monitor_updates_level_on_poll():
    source = _FakeSource([42, 30])
    mon = BatteryMonitor(source, interval_s=0.05, initial_level=None)
    mon.start()
    try:
        deadline = time.monotonic() + 2.0
        while mon.level() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert mon.level() == 42
    finally:
        mon.stop()


def test_monitor_survives_source_errors():
    class _Boom:
        def pre_state(self):
            raise RuntimeError("adb hiccup")

    mon = BatteryMonitor(_Boom(), interval_s=0.05, initial_level=99)
    mon.start()
    try:
        time.sleep(0.2)
        # A polling error must never crash the thread or clobber the last
        # known-good reading.
        assert mon.level() == 99
    finally:
        mon.stop()


def test_monitor_stop_is_idempotent_and_joins_thread():
    mon = BatteryMonitor(_FakeSource([50]), interval_s=0.05, initial_level=50)
    mon.start()
    mon.stop()
    mon.stop()  # must not raise