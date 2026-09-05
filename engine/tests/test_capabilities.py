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
    # The badge that says "re-run to collect" is only honest when a re-run collects.
    assert out["flag_actionable"] is True


def test_flag_on_but_no_root_reports_inaccessible(case_dir: Path):
    write(case_dir, "wifi", [])
    out = resolve(
        CATALOGUE["wifi"], case_dir / "derived", {"tier2_wifi": True}, root_available=False
    )
    assert out["state"] == INACCESSIBLE
    assert "root" in out["reason"].lower()


def test_flag_off_and_no_root_is_inaccessible_not_an_opt_in(case_dir: Path):
    """The ordering the whole no-root branch depends on: root outranks the flag.

    Both facts hold at once — the stage was gated off *and* the handset could not have
    run it. Resolving that to ``not_collected`` badges a toggle whose re-run the engine
    already knows returns the same nothing, at the price of a second acquisition and a
    second set of device-state changes on evidence.
    """
    write(case_dir, "wifi", [])
    out = resolve(
        CATALOGUE["wifi"],
        case_dir / "derived",
        {"tier2_wifi": False},
        root_available=False,
    )
    assert out["state"] == INACCESSIBLE
    assert out["flag_actionable"] is False
    # Neither fact is suppressed: the reason states the root failure and the flag.
    assert "root" in out["reason"].lower()
    assert "tier2_wifi" in out["reason"]


def test_dataset_with_a_non_root_route_is_not_swallowed_by_the_no_root_branch(
    case_dir: Path,
):
    """Instagram/Snapchat/Telegram conversations survive an unrooted handset.

    ``POST /api/case/<id>/import/<app>`` writes these same datasets from an account-data
    export, so "could not check — nothing you can do here" is false: the examiner can
    fill the view this afternoon without touching the phone. The reason has to name that
    route, and the flag must not be offered as the fix, because the pull it enables
    still cannot run.
    """
    for dataset, flag in (
        ("instagram_conversations", "tier2_instagram"),
        ("snapchat_conversations", "tier2_snapchat"),
        ("telegram_conversations", "tier2_telegram"),
    ):
        write(case_dir, dataset, [])
        for enabled in (True, False):
            out = resolve(
                CATALOGUE[dataset],
                case_dir / "derived",
                {flag: enabled},
                root_available=False,
            )
            assert out["state"] == NOT_COLLECTED, dataset
            assert out["flag_actionable"] is False, dataset
            assert "import" in out["reason"].lower(), dataset
            assert "root" in out["reason"].lower(), dataset


def test_root_only_datasets_still_resolve_inaccessible_without_root(case_dir: Path):
    """The carve-out is per-dataset, not a blanket amnesty for Tier 2."""
    for dataset in ("wifi", "bluetooth_bonds", "recent_tasks", "encrypted_apps"):
        write(case_dir, dataset, [])
        out = resolve(CATALOGUE[dataset], case_dir / "derived", {}, root_available=False)
        assert out["state"] == INACCESSIBLE, dataset


def test_unknown_root_status_is_not_read_as_an_unrooted_handset(case_dir: Path):
    """``None`` is a third answer. It is neither 'rooted' nor 'not rooted'."""
    write(case_dir, "wifi", [])
    out = resolve(
        CATALOGUE["wifi"], case_dir / "derived", {"tier2_wifi": False}, root_available=None
    )
    assert out["state"] == NOT_COLLECTED
    # It may still quote the precondition, but it must not assert the root finding.
    assert "root was not available" not in out["reason"].lower()
    assert out["flag_actionable"] is True


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


def _telegram_failed_to_pull(case_dir: Path) -> None:
    """The record ``_run_tier2_telegram`` leaves behind when ``su cp`` fails.

    This is what the stage actually writes on an unrooted handset with the Telegram flag
    ticked on (``_presence(False, ...)``, triage/pipeline.py) — and its absence from the
    previous round's fixtures is why the defect below shipped: every test wrote the
    conversations file and none wrote the presence record, so the branch that reads it
    was never exercised against a dataset with a non-root route.
    """
    write(
        case_dir,
        "telegram_presence",
        {
            "attempted": True,
            "available": False,
            "reason": "su cp failed: /system/bin/sh: su: not found",
            "package": "org.telegram.messenger",
        },
    )


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
    root_only_variant = Capability(
        **{
            **CATALOGUE["telegram_conversations"].__dict__,
            "root_only": True,
            "non_root_route": "",
        }
    )
    out = resolve(root_only_variant, case_dir / "derived", {"tier2_telegram": True})
    assert out["state"] == INACCESSIBLE
    assert "mock/synthetic source" in out["reason"]


def test_recorded_pull_failure_does_not_close_a_dataset_with_an_import_route(
    case_dir: Path,
):
    """The regression the outcome-record branch shipped: it outran the non-root carve-out.

    Unrooted handset, 'tier2_telegram' ticked ON — the exact case the carve-out was added
    for. The stage runs, the ``su cp`` fails, and it records that faithfully. Reading that
    record as ``inaccessible`` badged Telegram "n/a — nothing you can do here" while
    ``POST /api/case/<id>/import/telegram`` was sitting one tab away, ready to fill the
    same dataset from a Desktop export. The stage's own account of the failure is still
    the authority on *why*; it is not the authority on whether the gap can be closed.
    """
    _telegram_failed_to_pull(case_dir)
    out = resolve(
        CATALOGUE["telegram_conversations"],
        case_dir / "derived",
        {"tier2_telegram": True},
        root_available=False,
    )
    assert out["state"] == NOT_COLLECTED
    # The record is kept verbatim — it must not be traded away for the route.
    assert "su: not found" in out["reason"]
    assert "import" in out["reason"].lower()
    # Re-ticking the flag runs the same pull that just failed. It is not the fix.
    assert out["flag_actionable"] is False


def test_recorded_pull_failure_still_offers_the_route_with_the_flag_off(case_dir: Path):
    """Same record, flag off, root unknown: the route is the fix in every combination."""
    _telegram_failed_to_pull(case_dir)
    for root in (True, False, None):
        for enabled in (True, False):
            out = resolve(
                CATALOGUE["telegram_conversations"],
                case_dir / "derived",
                {"tier2_telegram": enabled},
                root_available=root,
            )
            assert out["state"] == NOT_COLLECTED, (root, enabled)
            assert out["flag_actionable"] is False, (root, enabled)
            assert "import" in out["reason"].lower(), (root, enabled)


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


def test_every_brief_gated_dataset_gets_the_same_treatment(case_dir: Path):
    """``investigation_trace`` has the identical gap and was badging it unfixable.

    It runs on the brief-derived case profile, so the pipeline never writes it without a
    brief; its ``ai_findings`` corroborator is missing for the same reason, and it fell
    through to "could not check" — telling the examiner a text field's worth of gap could
    not be closed at all. The treatment is a property of the capability now, not a branch
    written once for one dataset.
    """
    briefless = {"run_ai_analysis": True, "case_description_present": False}
    for dataset in ("ai_findings", "investigation_trace"):
        assert CATALOGUE[dataset].needs_case_brief, dataset
        out = resolve(CATALOGUE[dataset], case_dir / "derived", briefless)
        assert out["state"] == NOT_COLLECTED, dataset
        assert out["flag_actionable"] is False, dataset
        assert "brief" in out["reason"].lower(), dataset
        assert "does not need re-pulling" in out["reason"], dataset


def test_no_brief_and_flag_off_names_both_gaps(case_dir: Path):
    """Fixing the brief alone would not be enough here — the flag needs re-enabling too.

    Re-running the analysis needs both a brief and ``run_ai_analysis`` on. Naming only
    the brief would send the examiner back to a "fixed" case that still produces
    nothing, for a reason the reason text never mentioned.
    """
    both_off = {"run_ai_analysis": False, "case_description_present": False}
    out = resolve(CATALOGUE["ai_findings"], case_dir / "derived", both_off)
    assert out["state"] == NOT_COLLECTED
    assert out["flag_actionable"] is False
    assert "brief" in out["reason"].lower()
    assert "run_ai_analysis" in out["reason"]


# ---------------------------------------------------------------------------
# Fixed-shape envelopes: a stage that never ran must not badge itself "Collected"
# ---------------------------------------------------------------------------


#: The exact shape ``aleapp_result`` carries before ALEAPP is so much as looked for,
#: copied from its initialiser in triage/pipeline.py. It is written unconditionally at
#: the end of every run.
ALEAPP_UNRUN = {"available": False, "artifacts": {}, "report_dir": "", "error": None}


def test_envelope_shaped_file_is_not_reported_as_collected(case_dir: Path):
    """``len(value) == 0`` cannot see inside an envelope, and every stage here has one.

    This is the worst shape the failure takes: not a wrong reason on a gap badge but no
    badge at all. ``populated`` suppresses the nav-row state, the CapabilityBanner and the
    DatasetEmpty override alike, so the examiner is shown a view of a stage that never
    started with nothing on screen to say so.
    """
    envelopes = {
        # ALEAPP with run_aleapp off: four keys, none of them data.
        "aleapp": ALEAPP_UNRUN,
        # The generic app-finder's pre-declared output.
        "discovered_chats": {"tables": [], "messages": []},
        # run_advanced_analysis' full seven-key shape for zero input.
        "advanced": {
            "social_graph": {"nodes": [], "edges": [], "stats": {}, "top_contacts": []},
            "communication_patterns": {"bursts": [], "response_times": {}},
            "timeline": {"events": [], "total_days_active": 0},
            "anomalies": {"volume_spikes": [], "summary": {}},
            "recovery_metrics": {"total": 0, "by_confidence": {}},
            "meta": {"total_messages": 0, "live_messages": 0},
            "case_dir": "cases/CASE-X",
        },
        # The communication graph always carries the owner hub node, so it is never
        # length-zero even on a case that collected nothing at all.
        "graph": {
            "nodes": [{"id": "owner:self", "type": "owner", "weight": 0}],
            "edges": [],
            "stats": {"participants": 0, "interactions": 0, "channels": []},
        },
        # analyze_mediastore_trash always returns both keys.
        "mediastore_trash": {"items": [], "summary": {"total": 0}},
    }
    for dataset, blob in envelopes.items():
        write(case_dir, dataset, blob)
        out = resolve(CATALOGUE[dataset], case_dir / "derived", {})
        assert out["state"] != POPULATED, f"{dataset} badged Collected on an empty shell"
        assert out["count"] == 0, dataset
        assert out["reason"], f"{dataset} left the examiner without a reason"


def test_envelope_with_real_content_still_resolves_populated(case_dir: Path):
    """The content test must not swing the other way and hide collected data."""
    write(case_dir, "aleapp", {**ALEAPP_UNRUN, "available": True, "artifacts": {"Wi-Fi": [1, 2]}})
    write(case_dir, "discovered_chats", {"tables": [{"db": "x.db"}], "messages": []})
    write(
        case_dir,
        "graph",
        {"nodes": [], "edges": [{"source": "a"}], "stats": {"participants": 1}},
    )
    write(case_dir, "advanced", {"meta": {"total_messages": 7}, "recovery_metrics": {}})
    for dataset in ("aleapp", "discovered_chats", "graph", "advanced"):
        out = resolve(CATALOGUE[dataset], case_dir / "derived", {})
        assert out["state"] == POPULATED, dataset
        assert out["count"] > 0, dataset


def test_aleapp_that_ran_and_found_nothing_is_a_finding(case_dir: Path):
    """The envelope records whether the tool ran, so the two absences stay separable."""
    write(case_dir, "aleapp", ALEAPP_UNRUN)
    assert resolve(CATALOGUE["aleapp"], case_dir / "derived", {})["state"] == INACCESSIBLE

    write(case_dir, "aleapp", {**ALEAPP_UNRUN, "available": True})
    out = resolve(CATALOGUE["aleapp"], case_dir / "derived", {"run_aleapp": True})
    assert out["state"] == EMPTY


def test_unrun_envelope_offers_its_flag_when_the_flag_is_what_was_off(case_dir: Path):
    """With ``run_aleapp`` off the gap really is a re-runnable opt-in — say so."""
    write(case_dir, "aleapp", ALEAPP_UNRUN)
    out = resolve(CATALOGUE["aleapp"], case_dir / "derived", {"run_aleapp": False})
    assert out["state"] == NOT_COLLECTED
    assert out["flag_actionable"] is True
    assert "run_aleapp" in out["reason"]


def test_mediastore_trash_summary_key_is_not_proof_the_fusion_had_inputs(
    case_dir: Path,
):
    """``analyze_mediastore_trash`` builds ``summary`` even for empty inputs.

    A ``ran_when="summary"`` corroborator was added and then removed here: the function
    returns ``{"items": [...], "summary": {...}}`` unconditionally, so the ``summary``
    key is truthy on every non-exception call — proof the function did not raise, not
    proof either side of the fusion (``media_inventory``, ``media``) had anything to
    walk. Treating it as proof-of-run was the exact overstatement the envelope tests
    above catch, just pointed at ``empty`` instead of ``populated``.
    """
    assert CATALOGUE["mediastore_trash"].ran_when == ""
    write(case_dir, "mediastore_trash", {"items": [], "summary": {"total": 0}})
    out = resolve(CATALOGUE["mediastore_trash"], case_dir / "derived", {})
    assert out["state"] == INACCESSIBLE
    assert "written on every run" in out["reason"]


def test_signal_second_unconditional_write_is_not_read_as_a_clean_scan(case_dir: Path):
    """``signal_result`` is written twice: conditionally after the scan, then again,

    unconditionally, in the end-of-run block. The second write means an empty
    ``signal.json`` no longer proves the scan ran — a run that never reached it writes
    the same ``{}`` as one that ran and found nothing.
    """
    assert CATALOGUE["signal"].unconditional_write is True
    write(case_dir, "signal", {})
    out = resolve(CATALOGUE["signal"], case_dir / "derived", {})
    assert out["state"] == INACCESSIBLE
    assert "written on every run" in out["reason"]


def test_written_but_empty_is_never_described_as_a_stage_that_did_not_complete(
    case_dir: Path,
):
    """An unconditionally-written file that exists proves the run reached its write.

    Claiming it "did not complete" is an overstatement in the opposite direction from the
    one this module usually guards against, and it is still an overstatement. What is
    unknown is whether anything upstream reached a source — say that, and nothing more.
    """
    write(case_dir, "browser", [])
    out = resolve(CATALOGUE["browser"], case_dir / "derived", {})
    assert out["state"] == INACCESSIBLE
    assert "did not complete" not in out["reason"]
    assert "written on every run" in out["reason"]

    # A dataset whose file is genuinely absent keeps the stronger wording.
    missing = resolve(CATALOGUE["collector_wifi"], case_dir / "derived", {})
    assert missing["state"] == INACCESSIBLE
    assert "No result was recorded" in missing["reason"]


def test_browser_and_search_history_agree_about_one_source(case_dir: Path):
    """They sit four rows apart in the sidebar and are read from the same place.

    ``search_history`` is reconstructed from ``browser``, and both are written on every
    run. Rendering "0 — checked, nothing found" against one and "n/a — could not check"
    against the other told the examiner two different things about a single acquisition.
    """
    write(case_dir, "browser", [])
    write(case_dir, "search_history", [])
    states = {
        ds: resolve(CATALOGUE[ds], case_dir / "derived", {})["state"]
        for ds in ("browser", "search_history")
    }
    assert states["browser"] == states["search_history"] == INACCESSIBLE

    # And when a History database really was read, both become findings together.
    write(case_dir, "browser", [{"url": "https://example.test"}])
    assert resolve(CATALOGUE["browser"], case_dir / "derived", {})["state"] == POPULATED
    assert (
        resolve(CATALOGUE["search_history"], case_dir / "derived", {})["state"] == EMPTY
    )


def test_every_catalogued_flag_is_one_that_gates_that_dataset(case_dir: Path):
    """``fcm_records`` named 'tier2_app_presence'; its write is gated by another flag.

    The write is ``(encrypted_apps_result.get("fcm") or {}).get("records", [])`` inside
    ``if cfg.scan_encrypted_apps`` (triage/pipeline.py). With app-presence off and the
    encrypted-app scan on, the reason offered a toggle that collects nothing when it is
    re-ticked — a false opt-in promise, which is the class of defect this layer removes.
    """
    assert CATALOGUE["fcm_records"].flag == "scan_encrypted_apps"
    write(case_dir, "fcm_records", [])
    out = resolve(
        CATALOGUE["fcm_records"],
        case_dir / "derived",
        {"tier2_app_presence": False, "scan_encrypted_apps": True},
    )
    # The flag that was off does not gate this dataset, so it is not offered as the fix.
    assert out["flag_actionable"] is False


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


def test_root_availability_falls_back_to_the_records_written_early_in_the_run(
    case_dir: Path,
):
    """``device_state.json`` lands at 95%; a crashed or in-flight run has no such file.

    Both fallbacks are written in the first 4% of the same run from the same probe, so a
    run that died anywhere after device intake still knows whether the phone was rooted —
    and without them every Tier-2 dataset on that case badges as a re-runnable opt-in.
    """
    (case_dir / "case.json").write_text(json.dumps({"pre_state": {"root_available": False}}))
    assert case_capabilities(case_dir, {})["root_available"] is False

    # Even a case that never got as far as writing case.json's pre-state has the
    # encryption-posture record, which carries the same probe's answer.
    (case_dir / "case.json").write_text(json.dumps({"pre_state": {}}))
    write(case_dir, "encryption_state", {"root_available": False})
    assert case_capabilities(case_dir, {})["root_available"] is False


def test_root_availability_stays_unknown_when_nothing_recorded_it(case_dir: Path):
    """No record of the probe is not a record of an unrooted phone."""
    assert case_capabilities(case_dir, {})["root_available"] is None


def test_no_root_on_a_crashed_run_is_visible_to_every_tier_2_dataset(case_dir: Path):
    """The fallback is only worth having if it reaches ``resolve``."""
    (case_dir / "case.json").write_text(json.dumps({"pre_state": {"root_available": False}}))
    result = case_capabilities(case_dir, {"tier2_wifi": False})
    assert result["by_dataset"]["wifi"]["state"] == INACCESSIBLE
    assert result["by_dataset"]["wifi"]["flag_actionable"] is False


def test_every_catalogue_entry_explains_itself():
    """A capability with no requirement text cannot tell an examiner anything."""
    for cap in CATALOGUE.values():
        assert isinstance(cap, Capability)
        if cap.planned:
            assert cap.planned_note, f"{cap.dataset} is planned but says nothing"
        else:
            assert cap.requires, f"{cap.dataset} has no stated precondition"
        # "This gap is closable without root" is only useful with the route attached;
        # without one the reason trails off into a full stop and costs a search.
        if not cap.root_only:
            assert cap.non_root_route, f"{cap.dataset} claims a non-root route unnamed"
        # An envelope's content test only means anything if the emptiness of the file is
        # then treated as unproven — otherwise the "Collected" badge is replaced by a
        # "checked and clean" one, which is the same overstatement wearing a quieter hat.
        if cap.content_paths:
            assert cap.unconditional_write, (
                f"{cap.dataset} names content paths but its empty envelope would still "
                "be reported as a finding about the device"
            )


def test_ran_if_present_is_only_meaningful_with_unconditional_write():
    """A declared corroborator that ``resolve()`` never reaches is worse than none: it
    reads as a safeguard in the catalogue while doing nothing.

    ``ran_if_present`` is read in exactly one place, and only from inside the
    ``value is None or cap.unconditional_write`` branch. This is exactly how 'contacts'
    broke: ``ran_if_present=("calls",)`` was declared, but contacts.json is written
    unconditionally (triage/pipeline.py) and ``unconditional_write`` was left ``False`` —
    so with the file always present, that branch was never reached, and an unrooted or
    helper-less run fell straight through to "the stage ran and found nothing" regardless
    of what 'calls' held. 'location_impossible_travel' and 'investigation_trace' carried
    the identical, silent gap.

    'url_locations' is the one deliberate exception. triage/pipeline.py only calls
    ``write_derived`` for it inside ``if url_locations:``, so an empty result never
    leaves a file behind at all — ``resolve()`` reaches its corroborator through the
    independent ``value is None`` half of the same check, with nothing left for
    ``unconditional_write`` to add. Setting the flag there anyway would be inert for
    ``resolve()`` and false as documentation (the field means the file *is* written
    every run, which is not true here), so it stays off and is named here explicitly
    rather than quietly exempted — a future conditionally-written entry needs the same
    justification to join it, not just the absence of the flag.
    """
    conditionally_written = {"url_locations"}
    for cap in CATALOGUE.values():
        if not cap.ran_if_present or cap.dataset in conditionally_written:
            continue
        assert cap.unconditional_write, (
            f"{cap.dataset} declares ran_if_present={cap.ran_if_present!r} without "
            "unconditional_write=True, so resolve() never reaches the branch that "
            "reads it once the file is written empty-but-present"
        )


def test_populated_state_carries_no_excuse(case_dir: Path):
    """A populated dataset must not ship a reason — there is nothing to explain."""
    write(case_dir, "messages", [{"body": "hi"}])
    out = resolve(CATALOGUE["messages"], case_dir / "derived")
    assert out["reason"] == ""


# ---------------------------------------------------------------------------
# The same class of bug, found the same way, in the plain list-valued datasets the
# fixed-shape-envelope sweep did not cover: a dataset the pipeline writes
# unconditionally at the end of every run, catalogued with neither
# unconditional_write nor a working corroborator.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dataset", ["media_inventory", "apps", "accounts", "calendar", "usage"]
)
def test_dump_all_datasets_need_the_collector_manifest_to_read_as_clean(
    case_dir: Path, dataset: str
):
    """``_run_tier1_collect_all`` (triage/pipeline.py) returns having touched none of
    these five lists the moment the Collector APK is missing or ``pm install`` fails —
    both plain early returns before a pull. All five are still written, empty, at the
    end of the run, so without ``unconditional_write`` that empty file read as "0 /
    device is clean" instead of "the helper never ran".
    """
    write(case_dir, dataset, [])
    out = resolve(CATALOGUE[dataset], case_dir / "derived", {"tier1_collect_all": True})
    assert out["state"] == INACCESSIBLE

    # collector_manifest.json is the Collector's own last write of dump_all — only
    # reached past both early returns — so its presence corroborates all five the same
    # way it already does for collector_wifi/collector_bluetooth.
    write(case_dir, "collector_manifest", {"ran": True})
    out2 = resolve(CATALOGUE[dataset], case_dir / "derived", {"tier1_collect_all": True})
    assert out2["state"] == EMPTY


def test_contacts_corroborator_now_actually_fires(case_dir: Path):
    """``ran_if_present=("calls",)`` was declared on 'contacts' before this sweep too,
    but contacts.json is written unconditionally and ``unconditional_write`` was left
    False — so the branch that reads a corroborator was never reached, and an unrooted
    or helper-less run fell straight to "the stage ran and found nothing" regardless of
    what 'calls' held. Same shape as the dump_all datasets above, one row up.
    """
    write(case_dir, "contacts", [])
    out = resolve(CATALOGUE["contacts"], case_dir / "derived", {"tier1_contacts": True})
    assert out["state"] == INACCESSIBLE

    write(case_dir, "calls", [{"number": "+15551234567"}])
    out2 = resolve(CATALOGUE["contacts"], case_dir / "derived", {"tier1_contacts": True})
    assert out2["state"] == EMPTY


def test_screen_events_and_screen_app_usage_corroborate_each_other(case_dir: Path):
    """Both come from the same dumpsys try-block (triage/pipeline.py) and are both
    written unconditionally a second time at the end of the run — dumpsys power and
    dumpsys batterystats/usagestats either all answered or none did, so either one
    holding data proves the block ran for both.
    """
    write(case_dir, "screen_events", [])
    write(case_dir, "screen_app_usage", [])
    out = resolve(CATALOGUE["screen_events"], case_dir / "derived")
    assert out["state"] == INACCESSIBLE

    write(case_dir, "screen_app_usage", [{"app": "com.example", "foreground_ms": 900}])
    out2 = resolve(CATALOGUE["screen_events"], case_dir / "derived")
    assert out2["state"] == EMPTY


@pytest.mark.parametrize(
    "dataset", ["google_accounts", "maps_locations", "location_traces", "recovered"]
)
def test_unconditional_writes_with_no_corroborator_stay_unverified(
    case_dir: Path, dataset: str
):
    """None of these four has a sibling that can corroborate it without risking the
    same overstatement one level down (location_traces fuses six sources including the
    Tier-1 'tier1_collect_all'-gated media_inventory; recovered mixes the always-on
    db_artifacts scan with root-gated Telegram and browser-history recovery; the dumpsys
    account and location probes behind google_accounts/maps_locations have no sibling in
    their own try-block). An empty result is reported unverified rather than clean.
    """
    write(case_dir, dataset, [])
    out = resolve(CATALOGUE[dataset], case_dir / "derived")
    assert out["state"] == INACCESSIBLE
    assert out["state"] != EMPTY


def test_location_impossible_travel_is_corroborated_by_location_traces(case_dir: Path):
    """Written unconditionally right after location_traces, from the same try-block,
    with no guard of its own — the far more common case is "the trace exists and
    nothing in it was impossible", which must read as a finding, not as unverified.
    """
    write(case_dir, "location_impossible_travel", [])
    write(case_dir, "location_traces", [])
    out = resolve(CATALOGUE["location_impossible_travel"], case_dir / "derived")
    assert out["state"] == INACCESSIBLE

    write(case_dir, "location_traces", [{"lat": 1.0, "lon": 2.0, "timestamp": "t"}])
    out2 = resolve(CATALOGUE["location_impossible_travel"], case_dir / "derived")
    assert out2["state"] == EMPTY


def test_messages_names_sms_without_flag_off_reads_as_the_whole_view(case_dir: Path):
    """'tier1_sms' is the only non-root route to mmssms.db, but 'messages' also carries
    app-chat content from separate Tier-1/Tier-2 stages with their own flags. The
    generic not_collected wording ("re-run with it enabled to collect it") would read
    "it" as the whole Messages view, promising Instagram/Snapchat/Telegram/WhatsApp
    content that 'tier1_sms' cannot add — PARTIAL_FLAG_SCOPE keeps the reason to the
    slice this flag actually restores.
    """
    write(case_dir, "messages", [])
    out = resolve(CATALOGUE["messages"], case_dir / "derived", {"tier1_sms": False})
    assert out["state"] == NOT_COLLECTED
    assert out["flag_actionable"] is True
    assert "SMS" in out["reason"]
    assert "tier1_sms" in out["reason"]
    # Must not claim the whole view — that is the exact overclaim this wording exists
    # to avoid.
    assert "collect it" not in out["reason"]

    # An unknown 'tier1_sms' (older case, key absent) must not be read as a deliberate
    # skip — same rule as every other flag in this catalogue.
    out_unknown = resolve(CATALOGUE["messages"], case_dir / "derived", {})
    assert out_unknown["state"] != NOT_COLLECTED
