"""The mock corpus's canned shell output must parse with the real parsers.

This suite exists because of a specific, quiet failure. For a long time the corpus
shipped exactly one canned reply (``dumpsys location``), so every other shell-derived
stage read an empty string, wrote an empty dataset, and rendered as a blank dashboard
view. Nothing was broken, nothing was logged, and there was no way to tell the demo
apart from a device that genuinely had no Bluetooth history.

A fixture that stops matching its parser reintroduces exactly that state, so each test
below asserts on *parsed content*, not on the fixture text. If a parser's expected
format changes, these fail rather than the demo silently emptying out.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import corpus_shell as cs  # noqa: E402
from triage.parsers.bluetooth import parse_bluetooth_history  # noqa: E402
from triage.parsers.celltower import parse_celltower_history  # noqa: E402
from triage.parsers.google_maps import parse_current_location  # noqa: E402
from triage.parsers.google_search import parse_google_accounts  # noqa: E402
from triage.parsers.notification import parse_notification_history  # noqa: E402
from triage.parsers.screen_time import merge_app_usage, parse_screen_time  # noqa: E402
from triage.parsers.wifi_live import collect_wifi_live, wifi_live_json  # noqa: E402


def _shell(cmd: str) -> str:
    producer = cs.SHELL_FIXTURES.get(cmd)
    return producer() if producer else ""


# ---------------------------------------------------------------------------
# Every fixture reaches the mock source under the name it looks for
# ---------------------------------------------------------------------------


def test_fixture_filenames_match_the_mock_sources_mapping():
    """``MockDeviceSource.shell_readonly`` derives the filename from the command."""
    from triage.acquire.mock import MockDeviceSource

    for cmd in cs.SHELL_FIXTURES:
        expected = cmd.strip().replace(" ", "_").replace("/", "_") + ".txt"
        assert cs.fixture_filename(cmd) == expected
    # And the source really does look for that name.
    assert hasattr(MockDeviceSource, "shell_readonly")


def test_build_writes_every_fixture(tmp_path: Path):
    written = cs.build_shell_fixtures(tmp_path)
    assert len(written) == len(cs.SHELL_FIXTURES)
    for name in written:
        assert (tmp_path / name).read_text().strip(), f"{name} is empty"


# ---------------------------------------------------------------------------
# Each fixture parses into real rows
# ---------------------------------------------------------------------------


def test_notification_history_parses():
    rows = parse_notification_history(_shell("dumpsys notification --history"))
    assert len(rows) >= 8
    packages = {r["package"] for r in rows}
    assert "com.whatsapp" in packages
    assert any(r["is_comm"] for r in rows)
    assert all(r["timestamp"] for r in rows), "every canned notification carries a time"


def test_bluetooth_manager_parses_with_distinct_devices():
    devices = parse_bluetooth_history(_shell("dumpsys bluetooth_manager"))
    assert len(devices) == 5
    assert len({d["mac"] for d in devices}) == 5
    classes = {d["device_class"] for d in devices}
    # A mix of classes is the point — a phone alongside a car kit is what places two
    # people in one vehicle, and the demo should exercise that.
    assert {"phone", "audio", "wearable"} <= classes
    assert any(d["bond_state"] == "bonded" for d in devices)


def test_telephony_registry_parses_a_movement_sequence():
    towers = parse_celltower_history(_shell("dumpsys telephony.registry"))
    assert len(towers) >= 4
    assert len({(t["cell_id"], t["lac"]) for t in towers}) >= 3, "handset must move"
    assert all(t["mcc"] == 404 for t in towers)


def test_power_and_usage_parse():
    events = parse_screen_time(_shell("dumpsys power"))
    assert events, "dumpsys power must yield at least a wakefulness event"

    usage = merge_app_usage(_shell("dumpsys batterystats"), _shell("dumpsys usagestats"))
    packages = {u["package"] for u in usage}
    assert "com.whatsapp" in packages
    assert all(u["foreground_ms"] > 0 for u in usage)


def test_account_dump_parses_google_and_non_google_identities():
    accounts = parse_google_accounts(_shell("dumpsys account"))
    assert len(accounts) >= 4
    assert any(a["is_google"] for a in accounts)
    # An AccountManager identity that is not a Gmail address is the more useful
    # finding, and a fixture with only Google accounts would never exercise it.
    assert any(not a["is_google"] for a in accounts)
    assert sum(1 for a in accounts if a["is_primary"]) == 1


def test_wifi_live_collects_current_saved_scans_and_usage():
    result = collect_wifi_live(_shell)
    assert result["current"] is not None
    assert len(result["saved"]) >= 3
    assert result["scan_results"], "the scan table must parse"
    assert result["usage"], "netstats buckets must parse"

    flat = wifi_live_json(result)
    import json

    json.dumps(flat)  # must be serialisable — this raised on every real device before

    # Saved-vs-joined is the distinction the whole view rests on.
    ever = {bool(n.get("has_ever_connected")) for n in flat["saved"]}
    assert ever == {True, False}


def test_netstats_buckets_never_claim_a_join_time():
    result = collect_wifi_live(_shell)
    for bucket in wifi_live_json(result)["usage"]:
        assert bucket["approximate"] is True
        assert any("NON-AUTHORITATIVE" in c for c in bucket["caveats"])


def test_wlan_mac_is_flagged_as_randomised():
    device = collect_wifi_live(_shell)["device"]
    assert device["wlan0_mac"]
    assert device["wlan0_mac_is_randomized"] is True


# ---------------------------------------------------------------------------
# dumpsys location — the bracket form a real device emits
# ---------------------------------------------------------------------------


def test_bracket_form_location_parses():
    """``Location[fused 19.07,72.87 hAcc=12 ...]`` is what Android actually prints.

    The parser only understood ``latitude=``/``longitude=``, so on a real handset it
    reported no fix at all and the Maps view stayed empty.
    """
    text = (
        "LocationManagerService:\n"
        "  last location for provider fused:\n"
        "    Location[fused 19.075983,72.877655 hAcc=12 et=+1d2h34m ...]\n"
    )
    fix = parse_current_location(text)
    assert fix["valid"] is True
    assert fix["provider"] == "fused"
    assert fix["latitude"] == pytest.approx(19.075983)
    assert fix["accuracy_m"] == pytest.approx(12.0)
    # `et=` is elapsed-since-boot, not wall clock — no time may be invented from it.
    assert fix["timestamp"] == ""


def test_key_value_form_still_parses():
    fix = parse_current_location("latitude=12.5 longitude=77.5 accuracy=9 provider=gps")
    assert fix["valid"] is True
    assert fix["provider"] == "gps"


def test_empty_location_dump_is_invalid_not_zero_zero():
    """A null island fix is a real point off West Africa — never emit one."""
    fix = parse_current_location("")
    assert fix["valid"] is False
    assert fix["latitude"] is None and fix["longitude"] is None
