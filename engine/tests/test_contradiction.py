"""Tests for triage/forensics/contradiction.py.

The property under test: a contradiction check must fire on genuine cross-artifact
tension and stay silent on a claim that's actually consistent with the evidence — the
exact defect the original ``check_message_vs_location`` had (it flagged ANY "at home"
message near ANY GPS point, with no comparison to where home actually is).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from triage.forensics.contradiction import (  # noqa: E402
    check_message_vs_call,
    check_message_vs_home,
    detect_contradictions,
    haversine_km,
)

_HOME = {"center": {"lat": 19.0000, "lon": 72.8000}, "confidence": 0.8}


def test_call_contradiction_fires_within_window():
    msgs = [{"body": "phone was off", "timestamp": "2026-07-06T20:00:00Z", "source_file": "m.db"}]
    calls = [{"call_type": "incoming", "timestamp": "2026-07-06T20:05:00Z", "number": "+91999", "source_file": "c.json"}]
    out = check_message_vs_call(msgs, calls)
    assert len(out) == 1
    assert out[0]["matched_phrase"] == "phone was off"
    assert out[0]["call_type"] == "incoming"


def test_call_contradiction_silent_outside_window():
    msgs = [{"body": "phone was off", "timestamp": "2026-07-06T20:00:00Z", "source_file": "m.db"}]
    calls = [{"call_type": "incoming", "timestamp": "2026-07-06T23:00:00Z", "number": "+91999", "source_file": "c.json"}]
    assert check_message_vs_call(msgs, calls) == []


def test_call_contradiction_ignores_untyped_call():
    """A call log entry with no usable call_type must not count as evidence either way."""
    msgs = [{"body": "phone was off", "timestamp": "2026-07-06T20:00:00Z", "source_file": "m.db"}]
    calls = [{"timestamp": "2026-07-06T20:05:00Z", "number": "+91999", "source_file": "c.json"}]
    assert check_message_vs_call(msgs, calls) == []


def test_call_contradiction_ignores_missed_call():
    """A missed call does not prove the phone was on and receiving — no signal either
    way, so it must not be treated as contradicting evidence."""
    msgs = [{"body": "phone was off", "timestamp": "2026-07-06T20:00:00Z", "source_file": "m.db"}]
    calls = [{"call_type": "missed", "timestamp": "2026-07-06T20:05:00Z", "number": "+91999", "source_file": "c.json"}]
    assert check_message_vs_call(msgs, calls) == []


def test_call_contradiction_no_false_fire_on_unrelated_message():
    msgs = [{"body": "see you at 8", "timestamp": "2026-07-06T20:00:00Z", "source_file": "m.db"}]
    calls = [{"call_type": "incoming", "timestamp": "2026-07-06T20:05:00Z", "number": "+91999", "source_file": "c.json"}]
    assert check_message_vs_call(msgs, calls) == []


def test_home_contradiction_fires_when_far_from_inferred_home():
    msgs = [{"body": "reached home", "timestamp": "2026-07-06T21:00:00Z", "source_file": "m.db"}]
    locs = [{"latitude": 19.05, "longitude": 72.85, "timestamp": "2026-07-06T21:02:00Z", "source_file": "exif"}]
    out = check_message_vs_home(msgs, locs, _HOME)
    assert len(out) == 1
    assert out[0]["distance_from_home_km"] > 1.5


def test_home_contradiction_silent_when_claim_is_true():
    """The exact defect the original mock had: a truthful 'at home' message near the
    device's own home GPS must NOT be flagged as a contradiction."""
    msgs = [{"body": "reached home", "timestamp": "2026-07-06T21:00:00Z", "source_file": "m.db"}]
    locs = [{"latitude": 19.001, "longitude": 72.801, "timestamp": "2026-07-06T21:02:00Z", "source_file": "exif"}]
    assert check_message_vs_home(msgs, locs, _HOME) == []


def test_home_contradiction_requires_confident_home_inference():
    """No home cluster, or a low-confidence one, means nothing honest to compare
    against — must return nothing rather than guess."""
    msgs = [{"body": "reached home", "timestamp": "2026-07-06T21:00:00Z", "source_file": "m.db"}]
    locs = [{"latitude": 19.5, "longitude": 73.5, "timestamp": "2026-07-06T21:02:00Z", "source_file": "exif"}]
    assert check_message_vs_home(msgs, locs, None) == []
    assert check_message_vs_home(msgs, locs, {"center": {"lat": 19.0, "lon": 72.8}, "confidence": 0.1}) == []


def test_home_contradiction_requires_a_center():
    msgs = [{"body": "reached home", "timestamp": "2026-07-06T21:00:00Z", "source_file": "m.db"}]
    locs = [{"latitude": 19.5, "longitude": 73.5, "timestamp": "2026-07-06T21:02:00Z", "source_file": "exif"}]
    assert check_message_vs_home(msgs, locs, {"confidence": 0.9}) == []


def test_detect_contradictions_combines_both_checks():
    msgs = [
        {"body": "phone was off", "timestamp": "2026-07-06T20:00:00Z", "source_file": "m.db"},
        {"body": "reached home", "timestamp": "2026-07-06T21:00:00Z", "source_file": "m.db"},
    ]
    calls = [{"call_type": "incoming", "timestamp": "2026-07-06T20:05:00Z", "number": "+91999", "source_file": "c.json"}]
    locs = [{"latitude": 19.05, "longitude": 72.85, "timestamp": "2026-07-06T21:02:00Z", "source_file": "exif"}]
    out = detect_contradictions(msgs, calls, locs, _HOME)
    assert {c["type"] for c in out} == {"message_vs_call", "message_vs_home"}


def test_every_result_requires_verification():
    msgs = [{"body": "phone was off", "timestamp": "2026-07-06T20:00:00Z", "source_file": "m.db"}]
    calls = [{"call_type": "incoming", "timestamp": "2026-07-06T20:05:00Z", "number": "+91999", "source_file": "c.json"}]
    for c in check_message_vs_call(msgs, calls):
        assert c["requires_verification"] is True


def test_haversine_zero_distance():
    assert haversine_km(19.0, 72.8, 19.0, 72.8) == 0.0


def test_no_crash_on_empty_input():
    assert check_message_vs_call([], []) == []
    assert check_message_vs_home([], [], _HOME) == []
    assert detect_contradictions([], [], [], None) == []


def test_parse_iso_normalises_naive_to_utc_aware():
    """Real acquisitions mix naive ('2026-07-06T21:00:04') and 'Z'-suffixed aware
    timestamps within the same case — subtracting a naive datetime from an aware one
    raises TypeError unless both are normalised to the same awareness."""
    from triage.forensics.contradiction import parse_iso

    naive = parse_iso("2026-07-06T21:00:04")
    aware = parse_iso("2026-07-06T21:00:04Z")
    assert naive.tzinfo is not None
    assert aware.tzinfo is not None
    assert naive == aware  # same instant, regardless of which format wrote it


def test_call_contradiction_survives_mixed_naive_and_aware_timestamps():
    msgs = [{"body": "phone was off", "timestamp": "2026-07-06T20:00:00", "source_file": "m.db"}]
    calls = [{"call_type": "incoming", "timestamp": "2026-07-06T20:05:00Z", "number": "+91999", "source_file": "c.json"}]
    out = check_message_vs_call(msgs, calls)  # must not raise TypeError
    assert len(out) == 1


def test_home_contradiction_survives_mixed_naive_and_aware_timestamps():
    msgs = [{"body": "reached home", "timestamp": "2026-07-06T21:00:00", "source_file": "m.db"}]
    locs = [{"latitude": 19.05, "longitude": 72.85, "timestamp": "2026-07-06T21:02:00Z", "source_file": "exif"}]
    out = check_message_vs_home(msgs, locs, _HOME)  # must not raise TypeError
    assert len(out) == 1
