"""End-to-end proof that the case-intelligence plan actually drives acquisition.

:mod:`tests.test_intel` and :mod:`tests.test_rag` exercise the planner in isolation — they
prove a good plan is *produced*. Nothing there proves the plan is *obeyed*: the wiring in
``pipeline.run_acquisition`` (apply overrides → mutate the config → log every skip → feed
observation back) could be deleted and every one of those tests would stay green.

These tests run the real pipeline against the mock corpus with a case brief and assert on
what the run left behind — the mutated config, the derived datasets and the chain-of-
custody log — so the plan-to-extraction path cannot rot silently.
"""

import json
import re
import sys
from dataclasses import fields
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.make_corpus import build  # noqa: E402
from triage.acquire import MockDeviceSource  # noqa: E402
from triage.flagging import DEFAULT_KEYWORDS  # noqa: E402
from triage.intel.nomenclature import extract_roles  # noqa: E402
from triage.intel.planner import (  # noqa: E402
    ALWAYS_ACQUIRED_CLAIM,
    ALWAYS_COLLECTED,
    PARTIAL_WITHOUT_ROOT,
    PIPELINE_FLAG_MAP,
    ROOT_ONLY_FLAGS,
    UNCONDITIONAL_TIER0_CLAIM,
    UNPLANNABLE_PIPELINE_FLAGS,
    build_plan,
    extract_profile,
    plan_case,
)
from triage.pipeline import PipelineConfig, run_acquisition  # noqa: E402

#: Classifies as homicide and names Telegram, so the plan must switch on the gated
#: Telegram pull (``tier2_telegram``) as well as the cheap Tier-1 collectors.
MURDER_TELEGRAM_BRIEF = (
    "Accused Ramesh is a suspect in the murder of Shubham; he coordinated over "
    "Telegram and WhatsApp with a co-accused."
)

#: Cybercrime doctrine ranks call logs and locations *low* — exactly the case where a
#: relevance-driven collector would be tempted to skip them.
CYBERCRIME_BRIEF = (
    "Accused Vivek is under investigation for cybercrime: he hacked a company "
    "database, ran a phishing operation and moved the proceeds through a crypto "
    "wallet. Complainant Sunita reported the data breach."
)

#: The cheap collectors that must be on after any successful plan, and after a failed
#: one. Skipping them saves nothing and evidence can only be collected once.
CHEAP_TIER1_FLAGS = ("tier1_contacts", "tier1_calllog", "tier1_sms", "tier1_collect_all")


def _audit(case_dir: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (case_dir / "audit.jsonl").read_text().splitlines()
        if line.strip()
    ]


def _derived(case_dir: Path, name: str):
    return json.loads(
        (case_dir / "derived" / f"{name}.json").read_text(encoding="utf-8")
    )


@pytest.fixture(scope="module")
def device_corpus(tmp_path_factory) -> Path:
    """The synthetic device, built once — a full acquisition per test is slow."""
    dest = tmp_path_factory.mktemp("device")
    build(dest)
    return dest


@pytest.fixture(scope="module")
def planned_run(device_corpus, tmp_path_factory) -> tuple[PipelineConfig, Path]:
    """One real acquisition driven by :data:`MURDER_TELEGRAM_BRIEF`.

    Returns the *same* config object the pipeline mutated, so a test can read the flags
    the plan set rather than the flags the caller passed in.
    """
    cfg = PipelineConfig(
        case_id="INTEL-PLAN-001",
        examiner="Tester",
        legal_authority="warrant#1",
        cases_root=tmp_path_factory.mktemp("planned_cases"),
        case_description=MURDER_TELEGRAM_BRIEF,
    )
    summary = run_acquisition(MockDeviceSource(device_corpus), cfg)
    return cfg, Path(summary["case_dir"])


# --- 1. the plan's overrides reach the config and the collection gates -------
def test_plan_overrides_are_applied_to_the_running_config(planned_run):
    cfg, case_dir = planned_run
    plan = _derived(case_dir, "collection_plan")

    # The plan asked for Telegram…
    telegram = next(a for a in plan["artifacts"] if a["artifact"] == "telegram")
    assert telegram["collect"] is True
    assert plan["pipeline_overrides"]["tier2_telegram"] is True

    # …the config the pipeline ran with carries it…
    assert cfg.tier2_telegram is True

    # …and the Tier-2 stage was actually reached. This is the assertion that cannot be
    # satisfied by planning alone: the gate only logs when the flag was True *during*
    # the run, so it proves the override landed before the tier stages read it.
    events = [e for e in _audit(case_dir) if e["action"] == "tier2.telegram"]
    assert events, "tier2_telegram was set but the Telegram stage never ran"
    assert events[-1]["result"] == "skipped"  # mock source has no device to pull from
    assert _derived(case_dir, "telegram_presence")["attempted"] is True


# --- 2. prioritise-never-exclude, end to end ---------------------------------
def test_low_ranked_cheap_collectors_still_run(device_corpus, tmp_path):
    cfg = PipelineConfig(
        case_id="INTEL-CHEAP-001",
        examiner="Tester",
        cases_root=tmp_path / "cases",
        case_description=CYBERCRIME_BRIEF,
    )
    summary = run_acquisition(MockDeviceSource(device_corpus), cfg)
    case_dir = Path(summary["case_dir"])
    plan = _derived(case_dir, "collection_plan")
    entries = {a["artifact"]: a for a in plan["artifacts"]}

    # The premise, asserted rather than assumed: this brief really does rank these
    # cheap artifacts at the bottom band.
    assert entries["call_logs"]["priority"] == "low"
    assert entries["locations"]["priority"] == "low"

    # Every cheap artifact is still collected, and none of them is logged as deferred.
    cheap = {a["artifact"] for a in plan["artifacts"] if a["cost"] == "cheap"}
    assert all(a["collect"] for a in plan["artifacts"] if a["artifact"] in cheap)
    assert not cheap & {d["artifact"] for d in plan["deprioritised"]}

    # The flags the tier stages read ended up on…
    for flag in CHEAP_TIER1_FLAGS:
        assert getattr(cfg, flag) is True, flag

    # …and the data is on disk. A low ranking reorders review, it never shrinks the pull.
    assert _derived(case_dir, "contacts")
    assert _derived(case_dir, "calls")
    assert _derived(case_dir, "messages")
    assert summary["counts"]["calls"] > 0
    assert summary["counts"]["contacts"] > 0


# --- 3. every plan-driven skip is reviewable in the custody log --------------
def test_every_plan_skip_is_recorded_in_the_audit_log(planned_run):
    _, case_dir = planned_run
    plan = _derived(case_dir, "collection_plan")
    skips = plan["deprioritised"]
    assert skips, "expected this brief to defer at least one expensive pull"

    events = [e for e in _audit(case_dir) if e["action"] == "intel.deprioritised"]
    # One entry per deferred artifact — a silent non-event cannot be reviewed later.
    assert len(events) == len(skips)
    details = [e["detail"] for e in events]
    for skip in skips:
        matching = [d for d in details if d.startswith(f"{skip['label']} not")]
        assert matching, f"no audit entry for deferred artifact {skip['artifact']}"
        # The reason travels with the entry; "skipped" without a why is not an answer
        # to "why was this not pulled on this run".
        assert skip["reason"] in matching[0]
    assert all(e["result"] == "skipped" for e in events)


# --- 4. case-brief keywords reach the flagging stage ------------------------
def test_case_brief_keywords_reach_the_flagging_stage(planned_run):
    cfg, case_dir = planned_run
    plan = _derived(case_dir, "collection_plan")
    plan_terms = [k["term"] for k in plan["extra_keywords"]]
    assert plan_terms

    cfg_terms = {rule.term for rule in cfg.keywords}
    assert set(plan_terms) <= cfg_terms, "plan keywords never reached cfg.keywords"
    # Case-specific, not just crime doctrine: the named accused is a flag term.
    assert "Ramesh" in cfg_terms

    # cfg.keywords is what scan_messages/scan_carved are handed, so a hit that only a
    # plan-added rule can explain proves the rules were actually applied to the data.
    plan_rules = [
        re.compile(k["term"] if k.get("is_regex") else re.escape(k["term"]), re.I)
        for k in plan["extra_keywords"]
    ]
    default_rules = [rule.compile() for rule in DEFAULT_KEYWORDS]
    hits = {f["term"] for f in _derived(case_dir, "flags") if f["kind"] == "keyword"}
    plan_only = [
        term
        for term in hits
        if any(p.fullmatch(term) for p in plan_rules)
        and not any(d.fullmatch(term) for d in default_rules)
    ]
    assert plan_only, "no flag was raised that only a case-brief keyword can explain"


# --- 5. a planning failure must not shrink the collection -------------------
def test_planning_failure_falls_back_to_the_full_cheap_sweep(
    device_corpus, tmp_path, monkeypatch
):
    def _boom(*args, **kwargs):
        raise RuntimeError("planner exploded")

    # The pipeline imports plan_case from the package namespace on each run, so patching
    # the package attribute is what the running code will resolve.
    monkeypatch.setattr("triage.intel.plan_case", _boom)

    cfg = PipelineConfig(
        case_id="INTEL-FAIL-001",
        examiner="Tester",
        cases_root=tmp_path / "cases",
        case_description=MURDER_TELEGRAM_BRIEF,
    )
    summary = run_acquisition(MockDeviceSource(device_corpus), cfg)
    case_dir = Path(summary["case_dir"])

    # The acquisition still completed and still produced a case.
    assert (case_dir / "report.html").exists()
    assert summary["counts"]["messages"] > 0

    # Nothing was targeted, so nothing may be narrowed: the cheap sweep is forced on.
    for flag in CHEAP_TIER1_FLAGS:
        assert getattr(cfg, flag) is True, flag
    assert _derived(case_dir, "contacts")
    assert _derived(case_dir, "calls")
    assert _derived(case_dir, "messages")

    # There is no plan, and the log says so rather than leaving the reader to infer it
    # from an absent dataset.
    assert not (case_dir / "derived" / "collection_plan.json").exists()
    errors = [
        e
        for e in _audit(case_dir)
        if e["action"] == "intel.plan" and e["result"] == "error"
    ]
    assert len(errors) == 1
    detail = errors[0]["detail"]
    assert "planner exploded" in detail
    assert "Targeted collection was NOT applied" in detail


# --- 6. feedback grades what was observed, not what was planned -------------
def test_provisional_feedback_grades_observed_collection_not_plan_intent(planned_run):
    _, case_dir = planned_run
    plan = _derived(case_dir, "collection_plan")
    learning = _derived(case_dir, "case_learning")
    yields = learning["yields"]

    assert learning["recorded"] is True
    assert learning["graded_from"] == "observed collection"
    # The run really did learn something, so an empty-yields pass is not possible.
    assert yields.get("deleted") == "decisive"

    # Telegram was planned for and its flag was set, but the mock source has no device
    # to pull cache4.db from, so this run observed nothing about it. Grading that "none"
    # would teach the graph to stop collecting an artifact nobody ever looked at.
    telegram = next(a for a in plan["artifacts"] if a["artifact"] == "telegram")
    assert telegram["collect"] is True
    stage = [e for e in _audit(case_dir) if e["action"] == "tier2.telegram"]
    assert stage and stage[-1]["result"] == "skipped"
    assert (
        "telegram" not in yields
    ), f"unobserved telegram was graded {yields.get('telegram')!r}"


# --- 7. the plan may not promise a collection it cannot cause ----------------
#: Briefs spanning the doctrine bands, so the guards below see promoted, demoted and
#: opt-in artifacts rather than one crime type's fixed ranking.
_GUARD_BRIEFS = (MURDER_TELEGRAM_BRIEF, CYBERCRIME_BRIEF)


def test_no_artifact_claims_an_acquisition_the_plan_cannot_cause():
    """Every "always acquired" promise must be backed by the run.

    A plan is a statement about what this acquisition does. ``browser`` ranked high for
    cybercrime and said "cheap to collect, so always acquired" while the only stage that
    can read a browser's History DB (``tier2_browser_history``) was unreachable from any
    plan — a skip with no flag, no reason and no log entry.

    So: an artifact may promise unconditional acquisition only when either it has no
    gating flag *and* the pipeline pulls it in a Tier-0 stage with no gate at all, or the
    plan actually set its flag. Anything else has to describe the collection as partial.
    """
    seen_ungated: set[str] = set()
    seen_flagged: set[str] = set()
    for brief in _GUARD_BRIEFS:
        for allow_tier2 in (True, False):
            profile = extract_profile(brief)
            plan = build_plan(profile, allow_tier2=allow_tier2)
            for artifact in plan.artifacts:
                claims = (
                    ALWAYS_ACQUIRED_CLAIM in artifact.rationale
                    or UNCONDITIONAL_TIER0_CLAIM in artifact.rationale
                )
                if not claims:
                    continue
                flag = PIPELINE_FLAG_MAP[artifact.artifact]
                if flag is None:
                    # No gate exists, so the claim can only be true if the pipeline
                    # really pulls it unconditionally — which is what ALWAYS_COLLECTED
                    # records, minus the artifacts whose Tier-0 pass is only partial.
                    assert artifact.artifact in ALWAYS_COLLECTED, artifact.artifact
                    assert artifact.artifact not in PARTIAL_WITHOUT_ROOT, artifact.artifact
                    seen_ungated.add(artifact.artifact)
                else:
                    assert (
                        plan.pipeline_overrides.get(flag) is True
                    ), f"{artifact.artifact} promises acquisition but never set {flag}"
                    seen_flagged.add(artifact.artifact)

    # The loop above is only meaningful if both branches were actually exercised.
    assert seen_ungated, "no ungated artifact made the claim — the guard tested nothing"
    assert seen_flagged, "no flagged artifact made the claim — the guard tested nothing"


def test_partially_reachable_artifacts_say_so_instead_of_claiming_full_collection():
    """``browser``/``locations`` are collected, but not in full without root.

    They must never carry the blanket "always acquired" wording, and the shortfall must
    be an explicit record with a reason — a partial history read as a complete one is
    the false-exculpatory finding the honesty model exists to prevent.
    """
    profile = extract_profile(CYBERCRIME_BRIEF)
    for allow_tier2 in (True, False):
        plan = build_plan(profile, allow_tier2=allow_tier2)
        entries = {a.artifact: a for a in plan.artifacts}
        partials = {p["artifact"]: p for p in plan.partial_collection}
        assert set(partials) == set(PARTIAL_WITHOUT_ROOT)
        for name, flag in ((n, PIPELINE_FLAG_MAP[n]) for n in PARTIAL_WITHOUT_ROOT):
            artifact = entries[name]
            # Still collected — a partial source is never an excuse to drop it.
            assert artifact.collect is True, name
            assert ALWAYS_ACQUIRED_CLAIM not in artifact.rationale, name
            assert partials[name]["pipeline_flag"] == flag
            assert partials[name]["reason"]
            assert partials[name]["root_stage_enabled"] is allow_tier2
            assert plan.pipeline_overrides.get(flag, False) is allow_tier2
            if not allow_tier2:
                assert "PARTIAL" in artifact.rationale, name

    # Ranking still cannot defer them, and their minutes are never counted as saved.
    plan = build_plan(profile, allow_tier2=True)
    deferred = {d["artifact"] for d in plan.deprioritised}
    assert not deferred & set(PARTIAL_WITHOUT_ROOT)
    assert not set(plan.estimated_savings["deprioritised_artifacts"]) & ALWAYS_COLLECTED


# --- 8. guards against a gated stage nothing can ever switch on --------------
def _gated_config_flags() -> set[str]:
    """Boolean ``tier2_*``/``run_*`` PipelineConfig fields — the gated stages."""
    return {
        f.name
        for f in fields(PipelineConfig)
        if f.name.startswith(("tier2_", "run_")) and isinstance(f.default, bool)
    }


def test_every_mapped_pipeline_flag_is_a_real_config_field():
    """A typo'd flag name is a stage the plan silently never enables.

    ``setattr`` in the pipeline is guarded by ``hasattr``, so a misspelled flag is
    dropped without a word and the artifact is planned for but never pulled.
    """
    config_fields = {f.name: f for f in fields(PipelineConfig)}
    mapped = {flag for flag in PIPELINE_FLAG_MAP.values() if flag}
    assert mapped, "PIPELINE_FLAG_MAP maps nothing — the guard would be vacuous"
    for flag in sorted(mapped):
        assert flag in config_fields, f"{flag} is not a PipelineConfig field"
        assert isinstance(
            config_fields[flag].default, bool
        ), f"{flag} is not a boolean gate"
    # Every root-only flag must be one the planner can actually reach, or `allow_tier2`
    # would be guarding a stage no plan ever touches.
    assert ROOT_ONLY_FLAGS <= mapped


def test_gated_stages_no_plan_can_enable_match_the_documented_allowlist():
    """A gated stage nothing can switch on is a stage that silently never runs.

    ``tier2_browser_history`` and ``tier2_maps_location`` sat in exactly that state: real
    collection code, reachable from no plan. Anything left unreachable now has to be
    listed with a reason, so the next gated stage added either gets wired into
    :data:`PIPELINE_FLAG_MAP` or fails here.
    """
    reachable = {flag for flag in PIPELINE_FLAG_MAP.values() if flag}
    unreachable = _gated_config_flags() - reachable
    assert unreachable == set(UNPLANNABLE_PIPELINE_FLAGS), (
        "gated stages no plan can enable have drifted from the documented allowlist: "
        f"undocumented={sorted(unreachable - set(UNPLANNABLE_PIPELINE_FLAGS))}, "
        f"stale={sorted(set(UNPLANNABLE_PIPELINE_FLAGS) - unreachable)}"
    )
    # An allowlist entry without a reason is just a suppression.
    for flag, reason in UNPLANNABLE_PIPELINE_FLAGS.items():
        assert len(reason) > 40, f"{flag} is allowlisted without a real justification"


# --- 9. the newly wired root stages actually run ----------------------------
def test_plan_reaches_the_browser_and_maps_root_stages(planned_run):
    """End to end: the flags the plan now sets are read by the tier stages.

    Asserting on the plan alone would pass with the wiring deleted — only the stage's own
    audit entry proves the override landed before the pipeline read it.
    """
    cfg, case_dir = planned_run
    plan = _derived(case_dir, "collection_plan")

    for artifact, flag, action in (
        ("browser", "tier2_browser_history", "tier2.browser_history"),
        ("locations", "tier2_maps_location", "tier2.maps_location"),
    ):
        assert plan["pipeline_overrides"][flag] is True, artifact
        assert getattr(cfg, flag) is True, flag
        events = [e for e in _audit(case_dir) if e["action"] == action]
        assert events, f"{flag} was set but the {action} stage never ran"
        # The mock source has no device to root-pull from; the point is the gate opened.
        assert events[-1]["result"] == "skipped"

    # And the partial-collection caveat is in the custody log for both artifacts.
    logged = {
        e["detail"].split(":")[0]
        for e in _audit(case_dir)
        if e["action"] == "intel.partial_collection"
    }
    assert logged == {p["label"] for p in plan["partial_collection"]}


def test_an_unreached_root_stage_is_logged_as_a_skip_with_a_reason(
    device_corpus, tmp_path
):
    """The silent skip this whole guard exists for: no root permitted, no log.

    With Tier-2 withheld the browser/Maps app-private stores are never read. Saying
    nothing would leave an empty browser history in the report indistinguishable from a
    browser history that was genuinely empty.
    """
    cfg = PipelineConfig(
        case_id="INTEL-PARTIAL-001",
        examiner="Tester",
        cases_root=tmp_path / "cases",
        case_description=CYBERCRIME_BRIEF,
        plan_allow_tier2=False,
    )
    summary = run_acquisition(MockDeviceSource(device_corpus), cfg)
    case_dir = Path(summary["case_dir"])
    plan = _derived(case_dir, "collection_plan")

    assert cfg.tier2_browser_history is False
    assert cfg.tier2_maps_location is False
    assert plan["partial_collection"], "no partial-collection record was written"

    events = [e for e in _audit(case_dir) if e["action"] == "intel.partial_collection"]
    assert len(events) == len(plan["partial_collection"])
    assert all(e["result"] == "skipped" for e in events)
    for partial in plan["partial_collection"]:
        assert partial["root_stage_enabled"] is False
        matching = [e for e in events if e["detail"].startswith(partial["label"])]
        assert matching, f"no audit entry for partial artifact {partial['artifact']}"
        assert partial["reason"] in matching[0]["detail"]
        assert partial["pipeline_flag"] in matching[0]["detail"]

    # Still collected, still ranked, never deferred — the skip is the root supplement.
    entries = {a["artifact"]: a for a in plan["artifacts"]}
    for name in PARTIAL_WITHOUT_ROOT:
        assert entries[name]["collect"] is True
    assert not {d["artifact"] for d in plan["deprioritised"]} & set(PARTIAL_WITHOUT_ROOT)


# --- 10. colon-form intake, which the tool's own guidance suggests -----------
#: The exact structured form ``validate_description`` tells the officer to write.
COLON_BRIEF = "Accused: Laksh. Deceased: Shubham. Complainant: Meera."


def test_colon_form_roles_are_parsed():
    """"Accused: Laksh" is the format the intake help text asks for.

    Unparsed, every named party fell through to ``third_party`` at 0.3 confidence: the
    accused stops being adverse, so lead scoring loses the one person it should weight
    toward and the plan's flag terms lose their severity.
    """
    roles = {a.name: a for a in extract_roles(COLON_BRIEF)}
    assert set(roles) == {"Laksh", "Shubham", "Meera"}
    assert roles["Laksh"].role == "accused"
    assert roles["Laksh"].adverse is True
    assert roles["Shubham"].role == "deceased"
    assert roles["Meera"].role == "complainant"
    assert all(a.confidence >= 0.9 for a in roles.values())
    assert all(a.evidence for a in roles.values())


def test_colon_form_roles_reach_the_profile_and_the_plan():
    profile = extract_profile(COLON_BRIEF)
    assert profile.adverse_entities() == ["Laksh"]
    assert "Shubham" in profile.victims
    assert not any(r["role"] == "third_party" for r in profile.roles)

    _, plan = plan_case(COLON_BRIEF, use_rag=False)
    # The accused is a "warn" flag term; a witness/complainant is only "info", so the
    # role really does change what the flagging stage does with the name.
    severity = {k["term"]: k["severity"] for k in plan.extra_keywords}
    assert severity.get("Laksh") == "warn"
    assert severity.get("Meera") == "info"


@pytest.mark.parametrize(
    "brief, expected",
    [
        ("Accused: Laksh.", ("Laksh", "accused")),
        ("Accused - Laksh.", ("Laksh", "accused")),
        ("Absconder: Ravi.", ("Ravi", "absconder")),
        ("Missing person: Neha.", ("Neha", "missing_person")),
        ("Accused Laksh.", ("Laksh", "accused")),  # the pre-existing form still works
    ],
)
def test_role_separator_variants(brief, expected):
    """The separator is punctuation, not meaning — and "Absconder" is not a person.

    ``Absconder``/``Survivor`` are role words that read like names at the start of a
    sentence, which the old capitalisation guess assigned as the party.
    """
    name, role = expected
    roles = {a.name: a.role for a in extract_roles(brief)}
    assert roles == {name: role}
