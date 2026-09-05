"""Tests for triage/forensics/scam_detection.py.

The property under test: the original single-alternation regex classified on any ONE
matching word, including words with no scam-specific meaning ("video" alone → labelled
sextortion). Every test here either confirms a genuine hit still fires, or confirms a
bare common word no longer does.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from triage.forensics.scam_detection import (  # noqa: E402
    classify_scam_type,
    detect_scam_patterns,
    get_scam_actions,
    get_scam_statutes,
)


def test_bare_video_does_not_classify_as_sextortion():
    assert classify_scam_type("here's the video from the party") is None


def test_bare_refund_does_not_classify():
    assert classify_scam_type("your refund of Rs 500 has been processed") is None


def test_bare_police_does_not_classify():
    assert classify_scam_type("call the police if you see anything suspicious") is None


def test_strong_upi_signal_classifies():
    r = classify_scam_type("scan this QR to receive your refund")
    assert r["scam_type"] == "upi_fraud"
    assert r["tier"] == "strong"
    assert "scan" in r["matched_terms"][0].lower()


def test_strong_digital_arrest_signal_classifies():
    r = classify_scam_type("this is a digital arrest, CBI is investigating you")
    assert r["scam_type"] == "digital_arrest"
    assert r["tier"] == "strong"


def test_strong_sextortion_signal_classifies():
    r = classify_scam_type("pay now or we will leak your nude video")
    assert r["scam_type"] == "sextortion"
    assert r["tier"] == "strong"


def test_two_weak_signals_classify():
    r = classify_scam_type("please send money via gpay or phonepe today")
    assert r is not None
    assert r["scam_type"] == "upi_fraud"
    assert r["tier"] == "weak"
    assert len(r["matched_terms"]) >= 2


def test_one_weak_signal_does_not_classify():
    assert classify_scam_type("I'll pay you back via gpay") is None


def test_matched_terms_are_distinct_not_double_counted():
    """Two patterns matching the identical substring must not fake a second signal."""
    r = classify_scam_type("gpay gpay gpay")
    assert r is None  # same term repeated, still only one distinct weak signal


def test_empty_and_none_input():
    assert classify_scam_type("") is None
    assert classify_scam_type(None) is None


def test_detect_scam_patterns_preserves_original_message_fields():
    msgs = [{"body": "scan this QR code now", "sender": "unknown", "source_file": "m.db"}]
    hits = detect_scam_patterns(msgs)
    assert len(hits) == 1
    assert hits[0]["sender"] == "unknown"
    assert hits[0]["scam_type"] == "upi_fraud"
    assert "matched_terms" in hits[0]


def test_every_scam_type_has_actions_and_statutes():
    for scam_type in ("upi_fraud", "digital_arrest", "investment_fraud", "sextortion"):
        assert get_scam_actions(scam_type)
        assert get_scam_statutes(scam_type)


def test_unknown_scam_type_has_fallback_action():
    assert get_scam_actions("unknown_type") == ["Report to Cyber Cell"]
    assert get_scam_statutes("unknown_type") == []
