"""Tests for the per-dataset capability layer (``triage/capabilities.py``).

The property under test is a single honesty rule, applied to the user interface:

    a dataset that is empty because nobody looked must never render the same way
    as a dataset that is empty because someone looked and found nothing.

Every case below is one way that rule can be broken. The states themselves matter less
than the fact that they stay *distinct*: ``empty`` is a finding about the device,
``not_collected`` and ``inaccessible`` are findings about the acquisition, and
``planned`` is a fact about this build.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from triage.capabilities import (  # noqa: E402
    CATALOGUE,
    EMPTY,
    INACCESSIBLE,
    NOT_COLLECTED,
    PLANNED,
    POPULATED,
    Capability,
    case_capabilities,
    resolve,
)


@pytest.fixture()
def case_dir(tmp_path: Path) -> Path:
    (tmp_path / "derived").mkdir(parents=True)
    return tmp_path


def write(case_dir: Path, name: str, value) -> None:
    (case_dir / "derived" / f"{name}.json").write_text(json.dumps(value))


# ---------------------------------------------------------------------------
# The four kinds of absence stay four kinds
# ---------------------------------------------------------------------------


def test_populated_when_the_dataset_has_rows(case_dir: Path):
    write(case_dir, "messages", [{"body": "hi"}])
    out = resolve(CATALOGUE["messages"], case_dir / "derived")
    assert out["state"] == POPULATED
    assert out["count"] == 1


def test_flag_off_reports_not_collected_and_names_the_flag(case_dir: Path):
    write(case_dir, "wifi", [])
    out = resolve(CATALOGUE["wifi"], case_dir / "derived", {"tier2_wifi": False})
    assert out["state"] == NOT_COLLECTED
    assert "tier2_wifi" in out["reason"]


def test_flag_on_but_no_root_reports_inaccessible(case_dir: Path):
    write(case_dir, "wifi", [])
    out = resolve(
        CATALOGUE["wifi"], case_dir / "derived", {"tier2_wifi": True}, root_available=False
    )
    assert out["state"] == INACCESSIBLE
    assert "root" in out["reason"].lower()


def test_empty_dataset_with_corroborating_sibling_is_a_finding(case_dir: Path):
    """Search history is empty but the browser DB parsed — the stage genuinely ran."""
    write(case_dir, "browser", [{"url": "https://example.test"}])
    out = resolve(CATALOGUE["search_history"], case_dir / "derived")
    assert out["state"] == EMPTY
    assert "finding about the device" in out["reason"]


def test_missing_file_without_corroboration_is_not_reported_as_clean(case_dir: Path):
    """No search_history AND no browser: nothing proves the stage ever ran."""
    out = resolve(CATALOGUE["search_history"], case_dir / "derived")
    assert out["state"] == INACCESSIBLE
    assert out["state"] != EMPTY


def test_planned_capability_never_renders_as_empty(case_dir: Path):
    out = resolve(CATALOGUE["ios_acquisition"], case_dir / "derived")
    assert out["state"] == PLANNED
    assert out["reason"]  # a planned item must say why it is not built


# ---------------------------------------------------------------------------
# The specific conflations this module exists to prevent
# ---------------------------------------------------------------------------


def test_unconditionally_written_empty_file_is_not_treated_as_checked(case_dir: Path):
    """``collector_wifi`` is written on every run, so an empty one proves nothing.

    Without this rule a Tier-1 stage that never executed (no device, no APK install)
    produced an empty file that read as "the Collector looked and saw no networks".
    """
    write(case_dir, "collector_wifi", [])
    out = resolve(
        CATALOGUE["collector_wifi"], case_dir / "derived", {"tier1_collect_all": True}
    )
    assert out["state"] == INACCESSIBLE

    # With the Collector's own run manifest present, the same empty file *is* a finding.
    write(case_dir, "collector_manifest", {"ran": True})
    out2 = resolve(
        CATALOGUE["collector_wifi"], case_dir / "derived", {"tier1_collect_all": True}
    )
    assert out2["state"] == EMPTY


def test_stage_recorded_outcome_outranks_inference(case_dir: Path):
    """``telegram_presence`` knows why it failed; the catalogue must defer to it."""
    write(case_dir, "telegram_conversations", {})
    write(
        case_dir,
        "telegram_presence",
        {
            "attempted": True,
            "available": False,
            "reason": "mock/synthetic source — no real device to pull cache4.db from",
        },
    )
    out = resolve(
        CATALOGUE["telegram_conversations"],
        case_dir / "derived",
        {"tier2_telegram": True},
    )
    assert out["state"] == INACCESSIBLE
    assert "mock/synthetic source" in out["reason"]


def test_ai_findings_without_a_brief_says_so(case_dir: Path):
    out = resolve(
        CATALOGUE["ai_findings"],
        case_dir / "derived",
        {"run_ai_analysis": True, "case_description_present": False},
    )
    assert out["state"] == NOT_COLLECTED
    assert "brief" in out["reason"].lower()
    # The fix must not require re-pulling evidence — say so, or examiners re-acquire.
    assert "re-run the analysis" in out["reason"]


def test_unknown_config_is_never_read_as_a_deliberate_skip(case_dir: Path):
    """An older case with no ``acquisition_config`` must not claim stages were skipped."""
    write(case_dir, "wifi", [])
    out = resolve(CATALOGUE["wifi"], case_dir / "derived", {})
    assert out["state"] != NOT_COLLECTED


# ---------------------------------------------------------------------------
# Whole-case resolution
# ---------------------------------------------------------------------------


def test_case_capabilities_covers_the_catalogue_exactly_once(case_dir: Path):
    result = case_capabilities(case_dir, {})
    assert len(result["items"]) == len(CATALOGUE)
    assert set(result["by_dataset"]) == set(CATALOGUE)
    assert sum(result["counts"].values()) == len(CATALOGUE)


def test_root_availability_is_read_from_the_device_state(case_dir: Path):
    write(case_dir, "device_state", {"pre": {"root_available": False}})
    result = case_capabilities(case_dir, {})
    assert result["root_available"] is False


def test_every_catalogue_entry_explains_itself():
    """A capability with no requirement text cannot tell an examiner anything."""
    for cap in CATALOGUE.values():
        assert isinstance(cap, Capability)
        if cap.planned:
            assert cap.planned_note, f"{cap.dataset} is planned but says nothing"
        else:
            assert cap.requires, f"{cap.dataset} has no stated precondition"


def test_populated_state_carries_no_excuse(case_dir: Path):
    """A populated dataset must not ship a reason — there is nothing to explain."""
    write(case_dir, "messages", [{"body": "hi"}])
    out = resolve(CATALOGUE["messages"], case_dir / "derived")
    assert out["reason"] == ""
