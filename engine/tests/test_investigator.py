"""Tests for triage/intel/investigator.py — the bounded, deterministic multi-hypothesis
investigation pass.

Properties under test:
    * both hypotheses always run deterministically, with no model configured;
    * a hypothesis with insufficient data is 'blocked', never silently 'no gap
      found'/'no correlation found' — the absent-vs-inaccessible distinction this
      codebase enforces everywhere else;
    * a location/communication correlation only fires within the time window and cites
      both sides by id;
    * the narrative is LLM-gated and additive — never required, never changes the
      deterministic hypothesis results.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from triage.intel.analysis import Finding  # noqa: E402
from triage.intel.investigator import investigate  # noqa: E402
from triage.intel.llm import HeuristicProvider  # noqa: E402
from triage.intel.planner import CaseProfile, CollectionPlan  # noqa: E402


def _profile(**kw) -> CaseProfile:
    defaults = dict(description="test case", crime_type="general", crime_label="General")
    defaults.update(kw)
    return CaseProfile(**defaults)


def _finding(entities=None, category="message", ts="2026-07-06T21:00:00Z", fid="F-MSG-0001") -> Finding:
    return Finding(
        id=fid,
        title="t",
        severity="medium",
        score=3.0,
        category=category,
        timestamp=ts,
        entities_matched=entities or [],
    )


# --- always deterministic, heuristic provider produces no narrative ----------
def test_heuristic_provider_produces_no_narrative():
    profile = _profile()
    bundle = investigate({}, profile, [], provider=HeuristicProvider())
    assert bundle["narrative"] == ""
    assert bundle["analysis_method"] == "deterministic"
    assert len(bundle["hypotheses"]) == 2  # both hypotheses still ran


def test_default_provider_with_no_arg_is_also_deterministic():
    """No provider passed at all must resolve to the same offline default."""
    bundle = investigate({}, _profile(), [])
    assert bundle["analysis_method"] == "deterministic"


# --- channel_gap hypothesis ---------------------------------------------------
def test_channel_gap_blocked_with_no_adverse_entities():
    profile = _profile(roles=[])
    bundle = investigate({"contacts": [{"name": "X"}]}, profile, [])
    h = next(h for h in bundle["hypotheses"] if h["kind"] == "channel_gap")
    assert h["status"] == "blocked"


def test_channel_gap_blocked_with_no_contacts_collected():
    profile = _profile(roles=[{"name": "Rahul", "role": "accused", "adverse": True}])
    bundle = investigate({"contacts": []}, profile, [])
    h = next(h for h in bundle["hypotheses"] if h["kind"] == "channel_gap")
    assert h["status"] == "blocked"
    assert "Tier 1" in h["detail"] or "not collected" in h["detail"].lower() or "cannot tell" in h["detail"].lower()


def test_channel_gap_detected_when_contact_exists_with_no_finding():
    profile = _profile(roles=[{"name": "Rahul", "role": "accused", "adverse": True}])
    bundle = investigate(
        {"contacts": [{"name": "Rahul Verma", "number": "+91999"}]},
        profile,
        [_finding(entities=[])],  # a finding exists, but doesn't cite Rahul
    )
    h = next(h for h in bundle["hypotheses"] if h["kind"] == "channel_gap")
    assert h["status"] == "answered"
    assert "Rahul" in h["detail"]


def test_channel_gap_answered_clean_when_finding_covers_the_contact():
    profile = _profile(roles=[{"name": "Rahul", "role": "accused", "adverse": True}])
    bundle = investigate(
        {"contacts": [{"name": "Rahul Verma", "number": "+91999"}]},
        profile,
        [_finding(entities=["Rahul"])],
    )
    h = next(h for h in bundle["hypotheses"] if h["kind"] == "channel_gap")
    assert h["status"] == "answered"
    assert "no channel gap" in h["detail"].lower()


def test_channel_gap_cites_deprioritised_artifacts():
    profile = _profile(roles=[{"name": "Rahul", "role": "accused", "adverse": True}])
    plan = CollectionPlan(
        crime_type="general",
        crime_label="General",
        deprioritised=[{"artifact": "instagram", "reason": "expensive"}],
    )
    bundle = investigate(
        {"contacts": [{"name": "Rahul", "number": "1"}]},
        profile,
        [],
        plan=plan,
    )
    h = next(h for h in bundle["hypotheses"] if h["kind"] == "channel_gap")
    assert "instagram" in h["detail"]


# --- location_correlation hypothesis ------------------------------------------
def test_location_correlation_blocked_with_no_anomalies():
    bundle = investigate({"location_anomalies": []}, _profile(), [_finding()])
    h = next(h for h in bundle["hypotheses"] if h["kind"] == "location_correlation")
    assert h["status"] == "blocked"


def test_location_correlation_blocked_with_no_timestamped_findings():
    anomalies = [{"type": "x", "timestamp": "2026-07-06T21:00:00Z", "severity": "info", "explanation": ""}]
    bundle = investigate({"location_anomalies": anomalies}, _profile(), [])
    h = next(h for h in bundle["hypotheses"] if h["kind"] == "location_correlation")
    assert h["status"] == "blocked"


def test_location_correlation_fires_within_window():
    anomalies = [{"type": "late_night", "timestamp": "2026-07-06T21:05:00Z", "severity": "warn", "explanation": "e"}]
    bundle = investigate(
        {"location_anomalies": anomalies},
        _profile(),
        [_finding(ts="2026-07-06T21:00:00Z")],
    )
    h = next(h for h in bundle["hypotheses"] if h["kind"] == "location_correlation")
    assert h["status"] == "answered"
    assert len(bundle["linked_findings"]) == 1
    lf = bundle["linked_findings"][0]
    assert lf["right_ref"] == "F-MSG-0001"
    assert lf["gap_seconds"] == 300


def test_location_correlation_silent_outside_window():
    anomalies = [{"type": "x", "timestamp": "2026-07-06T23:00:00Z", "severity": "info", "explanation": ""}]
    bundle = investigate(
        {"location_anomalies": anomalies},
        _profile(),
        [_finding(ts="2026-07-06T21:00:00Z")],
    )
    assert bundle["linked_findings"] == []
    h = next(h for h in bundle["hypotheses"] if h["kind"] == "location_correlation")
    assert h["status"] == "answered"
    assert "0" not in h["detail"] or "none fell" in h["detail"].lower()


def test_location_correlation_only_considers_message_and_call_categories():
    anomalies = [{"type": "x", "timestamp": "2026-07-06T21:05:00Z", "severity": "info", "explanation": ""}]
    bundle = investigate(
        {"location_anomalies": anomalies},
        _profile(),
        [_finding(ts="2026-07-06T21:00:00Z", category="browser")],
    )
    assert bundle["linked_findings"] == []


def test_linked_finding_cites_both_sides_by_id():
    anomalies = [{"type": "x", "timestamp": "2026-07-06T21:05:00Z", "severity": "info", "explanation": ""}]
    bundle = investigate(
        {"location_anomalies": anomalies}, _profile(), [_finding(ts="2026-07-06T21:00:00Z", fid="F-CALL-0009")]
    )
    lf = bundle["linked_findings"][0]
    assert lf["right_ref"] == "F-CALL-0009"
    assert "location_anomalies[" in lf["left_ref"]


# --- shape / robustness --------------------------------------------------------
def test_bundle_always_has_disclaimer():
    bundle = investigate({}, _profile(), [])
    assert bundle["disclaimer"]


def test_location_correlation_survives_mixed_naive_and_aware_timestamps():
    """The exact bug found against real acquisition data: a message finding with a
    naive timestamp and a location anomaly (or another finding) with a 'Z'-suffixed
    aware one must not raise TypeError when correlated."""
    anomalies = [{"type": "x", "timestamp": "2026-07-06T21:05:00Z", "severity": "info", "explanation": ""}]
    bundle = investigate(
        {"location_anomalies": anomalies},
        _profile(),
        [_finding(ts="2026-07-06T21:00:00")],  # naive — no trailing Z
    )
    h = next(h for h in bundle["hypotheses"] if h["kind"] == "location_correlation")
    assert h["status"] == "answered"
    assert len(bundle["linked_findings"]) == 1


def test_never_raises_on_malformed_input():
    """A hypothesis erroring must be recorded as blocked, not crash the whole pass."""
    bundle = investigate(
        {"contacts": "not a list", "location_anomalies": [{"timestamp": None}]},
        _profile(roles=[{"name": "X", "role": "accused", "adverse": True}]),
        [_finding()],
    )
    assert len(bundle["hypotheses"]) == 2
    assert all(h["status"] in ("pending", "answered", "blocked") for h in bundle["hypotheses"])
