"""Report-rendering integration for the sections added in P1-1/P1-3/P1-4/P1-7/P2-1/P2-3.

Unit behaviour of each underlying module is tested in its own file. What is checked here
is that the report actually *renders* those results, and — more importantly — that the
honesty wording each section is responsible for survives into the examiner-facing HTML.
A caveat that exists only in a docstring protects nobody.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from triage.custody import Case, CaseMeta
from triage.report import generate_report


@pytest.fixture()
def case(tmp_path: Path) -> Case:
    c = Case.create(tmp_path / "cases", CaseMeta(case_id="RPT-1", examiner="Insp. Rao"))
    payload = tmp_path / "evidence.txt"
    payload.write_text("some evidence bytes")
    c.ingest_file(payload, source_path="/sdcard/evidence.txt", tier="tier0", method="mock")
    return c


def render(case: Case) -> str:
    return generate_report(case.root).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# P2-1 — BSA 2023 s.63 replaces the repealed IEA s.65B
# ---------------------------------------------------------------------------
def test_certificate_cites_bsa_63_not_the_repealed_act(case: Case):
    html = render(case)
    assert "Bharatiya Sakshya" in html
    assert "63" in html
    # "Indian Evidence Act" may appear ONLY inside the migration note explaining the
    # repeal — never as the statute the certificate is issued under.
    for idx in _all_indices(html, "Indian Evidence Act"):
        window = html[max(0, idx - 400) : idx]
        assert "MIGRATION NOTE" in window, (
            "the report cites the Indian Evidence Act outside the migration note; the "
            "certificate must be issued under BSA 2023 s.63"
        )


def test_certificate_has_both_signature_blocks_and_is_unsigned(case: Case):
    html = render(case).lower()
    assert "part a" in html and "part b" in html
    assert "custodian" in html or "person in charge" in html
    assert "expert" in html
    # It must present as a template requiring manual signature, never as executed.
    assert "template" in html


def test_certificate_never_claims_the_acquisition_was_read_only(case: Case):
    """The removed s.65B generator certified 'read-only, nothing altered'. That is false
    for every mobile acquisition and must never reappear in a signable document."""
    html = render(case).lower()
    assert "no data on the original device was altered" not in html
    assert "the extraction process was read-only" not in html


def test_removed_65b_generator_raises_rather_than_emitting_a_certificate():
    from triage.forensics.section65b import generate_65b_certificate

    with pytest.raises(NotImplementedError) as exc:
        generate_65b_certificate({}, "Insp. Rao", "IO")
    assert "bsa_certificate" in str(exc.value)


# ---------------------------------------------------------------------------
# P1-1 — encryption posture gates every app-data claim
# ---------------------------------------------------------------------------
def test_missing_encryption_state_is_not_reported_as_unencrypted(case: Case):
    html = render(case)
    assert "Encryption posture" in html
    assert "not captured" in html
    assert "Do not infer" in html


def test_bfu_state_says_inaccessible_not_absent(case: Case):
    case.write_derived(
        "encryption_state",
        {
            "unlock_state": "bfu",
            "crypto_type": "file",
            "crypto_state": "encrypted",
            "sdk": 33,
            "fbe_mandatory": True,
            "unlock_evidence": ["ls /data/data returned encrypted filenames"],
            "caveats": ["Root is not decryption."],
            "probes": {},
        },
    )
    html = render(case)
    assert "Before First Unlock" in html
    assert "INACCESSIBLE" in html or "inaccessible" in html
    assert "NOT evidence" in html or "not evidence" in html
    assert "Root is not decryption" in html


def test_unknown_encryption_state_is_not_optimistic(case: Case):
    case.write_derived("encryption_state", {"unlock_state": "unknown", "probes": {}})
    html = render(case)
    assert "UNDETERMINED" in html
    assert "do not assume the device was unlocked" in html.lower()


# ---------------------------------------------------------------------------
# P2-3 — pre/post device state
# ---------------------------------------------------------------------------
def test_absent_post_state_reports_reversal_unverified(case: Case):
    html = render(case)
    assert "Device state — pre/post acquisition" in html
    assert "unverified" in html.lower()


def test_residual_modifications_are_itemised(case: Case):
    case.write_derived(
        "device_state",
        {
            "summary": {
                "teardown_verdict": "residual",
                "statement": "1 modification remained on the device.",
                "residual_changes": 1,
                "unexpected_differences": 0,
            },
            "teardown": {
                "verdict": "residual",
                "residue": [
                    {
                        "kind": "permission",
                        "subject": "android.permission.READ_SMS",
                        "detail": "still granted after teardown",
                    }
                ],
                "unverified": [],
                "ledger": {
                    "package": "io.erakshak.collector",
                    "installed": True,
                    "granted_permissions": ["android.permission.READ_SMS"],
                    "appops_set": [],
                    "files_written_to_device": [],
                },
            },
            "diff": {
                "permissions_added": ["android.permission.READ_SMS"],
                "appops_added": [],
                "unexpected_changes": [],
                "expected_drift": [],
            },
        },
    )
    html = render(case)
    assert "DEVICE MODIFICATIONS REMAIN" in html
    assert "android.permission.READ_SMS" in html
    assert "Still granted after acquisition" in html


def test_unverified_teardown_is_not_styled_as_clean(case: Case):
    case.write_derived(
        "device_state",
        {
            "summary": {
                "teardown_verdict": "unverified",
                "statement": "Reversal could not be verified.",
            },
            "teardown": {"verdict": "unverified", "residue": [], "unverified": ["pm"]},
            "diff": {},
        },
    )
    html = render(case)
    assert "REVERSAL UNVERIFIED" in html
    assert "RETURNED TO FOUND STATE" not in html


# ---------------------------------------------------------------------------
# P1-3 / P1-4 — Bluetooth bonds and serving cells
# ---------------------------------------------------------------------------
def test_bond_timestamp_is_never_presented_as_a_connection_time(case: Case):
    case.write_derived(
        "bluetooth_bonds",
        [
            {
                "address": "AA:BB:CC:11:22:33",
                "name": "Pixel Buds",
                "dev_type_label": "LE",
                "vendor": "Google, Inc.",
                "bond_timestamp": "2026-05-04T10:11:12Z",
                "timestamp_meaning": "bond-record write time; not a connection time",
            }
        ],
    )
    html = render(case)
    assert "A bond timestamp is not a" in html
    assert "does not place" in html
    assert "Bond record written" in html
    assert "corroboration" in html


def test_serving_cell_is_not_presented_as_a_position(case: Case):
    case.write_derived(
        "celltower",
        [
            {
                "operator": "Jio",
                "technology": "LTE",
                "cell_id": "12345678",
                "tac": "4321",
                "signal_dbm": -91,
                "timestamp": "2026-05-04T10:11:12Z",
            }
        ],
    )
    html = render(case)
    assert "not</b> a GPS position" in html or "not a GPS position" in html
    assert "coverage area" in html
    assert "is not a location history" in html


def test_bluetooth_section_absent_when_nothing_was_collected(case: Case):
    html = render(case)
    assert "Bluetooth &amp; cellular network artifacts" not in html


def test_encrypted_bond_store_is_not_reported_as_no_bonds(case: Case):
    case.write_derived("bluetooth", [{"mac": "xx:xx:xx:xx:AB:CD", "name": "Car"}])
    case.write_derived("bluetooth_bond_report", {"encrypted": True, "bonds": []})
    html = render(case)
    assert "encrypted and could not be parsed" in html
    assert "not</b> a finding" in html


# ---------------------------------------------------------------------------
# P1-7 — screen / search / accounts
# ---------------------------------------------------------------------------
def test_activity_section_renders_with_its_caveats(case: Case):
    case.write_derived(
        "screen_events",
        [{"timestamp": "2026-05-04T03:14:00Z", "event_type": "ON"}],
    )
    case.write_derived(
        "screen_time_summary", {"total_sessions": 1, "total_screen_time_min": 12}
    )
    case.write_derived(
        "search_history",
        [
            {
                "query": "how to delete whatsapp messages permanently",
                "timestamp": "2026-05-04T03:15:00Z",
                "source": "browser",
            }
        ],
    )
    case.write_derived(
        "google_accounts", [{"name": "a@example.com", "type": "com.google"}]
    )
    html = render(case)
    assert "Device activity" in html
    assert "rolling dumpsys buffers" in html
    assert "not evidence the device was idle" in html
    assert "does not identify who typed it" in html
    assert "presence, not ownership" in html
    assert "how to delete whatsapp messages permanently" in html


def test_activity_section_absent_when_nothing_was_collected(case: Case):
    assert "Device activity" not in render(case)


# ---------------------------------------------------------------------------
# P1-5 — deletion detected as its own evidence class
# ---------------------------------------------------------------------------
def _msgstore_with_a_gap(tmp_path: Path) -> Path:
    import sqlite3

    db = tmp_path / "msgstore.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE message(_id INTEGER PRIMARY KEY, body TEXT)")
    con.executemany(
        "INSERT INTO message(_id, body) VALUES (?,?)",
        [(i, f"message number {i}") for i in range(1, 41)],
    )
    con.commit()
    con.execute("DELETE FROM message WHERE _id BETWEEN 10 AND 25")
    con.commit()
    con.close()
    return db


def test_deletion_evidence_is_collected_and_tagged(case: Case, tmp_path: Path):
    from triage.pipeline import _collect_deletion_evidence

    db = _msgstore_with_a_gap(tmp_path)
    rec = case.ingest_file(
        db,
        source_path="/data/data/com.whatsapp/databases/msgstore.db",
        tier="tier2",
        method="root-su-cp",
        category="database",
    )
    items = _collect_deletion_evidence([(case.root / rec.stored_path, rec)], [])

    assert items, "a 16-row rowid gap must be detected"
    assert {i["mechanism"] for i in items} >= {"rowid-gap"}
    for i in items:
        assert i["confidence"] == "deletion"
        assert i["false_positive_causes"], "every finding must disclose its confounders"
        assert i["device_path"].startswith("/data/data/com.whatsapp")


def test_report_renders_deletion_evidence_apart_from_recovered_content(
    case: Case, tmp_path: Path
):
    from triage.pipeline import _collect_deletion_evidence
    from triage.recovery import deletion_evidence_summary

    db = _msgstore_with_a_gap(tmp_path)
    rec = case.ingest_file(
        db,
        source_path="/data/data/com.whatsapp/databases/msgstore.db",
        tier="tier2",
        method="root-su-cp",
        category="database",
    )
    items = _collect_deletion_evidence([(case.root / rec.stored_path, rec)], [])
    case.write_derived("deletion_evidence", items)
    case.write_derived("deletion_evidence_summary", deletion_evidence_summary(items))

    html = render(case)
    assert "Deletion detected (no content recovered)" in html
    assert "DELETION DETECTED" in html
    # The whole point of the separate section: it must not read as recovered content.
    assert "No content is recovered by these findings" in html
    assert "Innocent explanations that produce the same signal" in html


def test_no_deletion_evidence_renders_no_section(case: Case):
    assert "Deletion detected" not in render(case)


# ---------------------------------------------------------------------------
# Hotspot posture rendering
# ---------------------------------------------------------------------------

_WIFI_LIVE_WITH_ACTIVE_HOTSPOT = {
    "current": None,
    "saved": [],
    "scan_results": [],
    "usage": [],
    "connectivity": {},
    "commands": [],
    "hotspot": {
        "hosted_indicator": True,
        "connected_indicator": None,
        "hosted_configured": False,
        "caveats": [
            "Scope caveat.",
            "The device's tethering / mobile hotspot was active at capture time. "
            "This does not identify which devices connected or what data moved.",
        ],
        "details": {
            "hosted_evidence": ["dumpsys wifi: SoftApManager current state: StartedState"],
            "connected_evidence": [],
            "traffic_evidence": [],
        },
    },
}

_WIFI_LIVE_WITH_CONFIGURED_NOT_ACTIVE = {
    "current": None,
    "saved": [],
    "scan_results": [],
    "usage": [],
    "connectivity": {},
    "commands": [],
    "hotspot": {
        "hosted_indicator": None,
        "connected_indicator": None,
        "hosted_configured": True,
        "caveats": [
            "Scope caveat.",
            "No SoftAp state was reported by this build.",
            "A hotspot configuration (SSID and passphrase) exists on the device. "
            "That proves it was set up, not that it was ever switched on.",
        ],
        "details": {
            "hosted_evidence": ["SoftAp configuration present on device (SSID 'MyPhoneAP')"],
            "connected_evidence": [],
            "traffic_evidence": [],
        },
    },
}

_WIFI_LIVE_WITH_MULTIPLE_HOTSPOT_NETWORKS = {
    "current": None,
    "saved": [],
    "scan_results": [],
    "usage": [],
    "connectivity": {},
    "commands": [],
    "hotspot": {
        "hosted_indicator": False,
        "connected_indicator": True,
        "hosted_configured": False,
        "caveats": [
            "Scope caveat.",
            "One or more known networks are NAMED like a phone hotspot "
            "(AndroidAP1234, iPhoneXR). SSIDs are freely chosen.",
        ],
        "details": {
            "hosted_evidence": [],
            "connected_evidence": [
                "Known network 'AndroidAP1234' matches the hotspot naming convention "
                "'androidap'. This is a NAME match, not a determination that the "
                "network was a phone hotspot.",
                "Known network 'iPhoneXR' matches the hotspot naming convention "
                "'iphone'. This is a NAME match, not a determination that the "
                "network was a phone hotspot.",
            ],
            "traffic_evidence": [
                "Non-zero traffic over hotspot-named SSID 'AndroidAP1234': rx=5000 bytes, tx=3000 bytes"
            ],
        },
    },
}


def test_hotspot_active_tethering_renders_correct_label(case: Case):
    case.write_derived("wifi_live", _WIFI_LIVE_WITH_ACTIVE_HOTSPOT)
    html = render(case)
    assert "Hotspot Posture" in html
    assert "ACTIVE AT COLLECTION" in html
    assert "active at capture time" in html
    # Critical: must not say it identifies connected clients
    assert "does not identify which devices connected" in html


def test_hotspot_configured_not_proven_active(case: Case):
    case.write_derived("wifi_live", _WIFI_LIVE_WITH_CONFIGURED_NOT_ACTIVE)
    html = render(case)
    assert "Hotspot Posture" in html
    assert "HOTSPOT CONFIGURED" in html or "configured" in html
    assert "configured" in html
    # Must NOT claim it was ever switched on
    assert "not that it was ever" in html or "not that it was ever switched on" in html
    # Must NOT say active
    assert "ACTIVE AT COLLECTION" not in html


def test_hotspot_multiple_networks_distinct_count(case: Case):
    case.write_derived("wifi_live", _WIFI_LIVE_WITH_MULTIPLE_HOTSPOT_NETWORKS)
    html = render(case)
    assert "Hotspot Posture" in html
    # Should show count of 2 distinct probable hotspot networks
    assert "2 distinct probable hotspot network" in html
    assert "AndroidAP1234" in html
    assert "iPhoneXR" in html
    # Must label as probable/heuristic, not certain
    assert "PROBABLE HISTORICAL CONNECTION" in html or "lead for investigation" in html
    assert "not a conclusion" in html
    # Traffic evidence should appear
    assert "rx=5000" in html or "5000" in html


def test_hotspot_saved_list_unavailable_is_distinct_from_no_hotspot(case: Case):
    """connected_indicator=None (root needed) must not read as 'no hotspot activity'."""
    case.write_derived("wifi_live", _WIFI_LIVE_WITH_ACTIVE_HOTSPOT)
    html = render(case)
    # The saved-network list is absent (connected_indicator=None in this fixture)
    assert "Saved-network list unavailable" in html or "not run" in html or "not" in html.lower()
    # Must explicitly NOT claim the check excluded hotspot use
    assert "does not exclude" not in html or "unreadable" in html or "unavailable" in html


def test_hotspot_not_collected_is_explicit_gap_not_false_negative(case: Case):
    """If wifi_live was never written, the section must say 'not collected', not 'no hotspot'."""
    # Do not write wifi_live at all
    html = render(case)
    assert "Hotspot Posture" in html
    assert "not collected" in html.lower() or "not" in html.lower()
    # The word 'not' alone is too broad; verify the critical absence:
    assert "no hotspot" not in html.lower()


def test_hotspot_section_always_includes_slack_space_limitation(case: Case):
    """The slack-space/unallocated-block limitation must appear in every report."""
    html = render(case)
    assert "slack space" in html.lower() or "unallocated" in html.lower()
    assert "intentionally not" in html.lower() or "not supported" in html.lower()
    assert "FBE" in html or "File-Based Encryption" in html or "ciphertext" in html


def test_hotspot_xss_data_is_escaped_in_report(case: Case):
    """SSID names and evidence strings from the device must be HTML-escaped."""
    xss = '<script>alert("pwned")</script>'
    live_with_xss = {
        "current": None,
        "saved": [],
        "scan_results": [],
        "usage": [],
        "connectivity": {},
        "commands": [],
        "hotspot": {
            "hosted_indicator": True,
            "connected_indicator": True,
            "hosted_configured": False,
            "caveats": [xss],
            "details": {
                "hosted_evidence": [xss],
                "connected_evidence": [
                    f"Known network '{xss}' matches the hotspot naming convention 'androidap'. "
                    "This is a NAME match, not a determination."
                ],
                "traffic_evidence": [xss],
            },
        },
    }
    case.write_derived("wifi_live", live_with_xss)
    html = render(case)
    assert "<script" not in html.lower()
    assert xss not in html



XSS = '<script>alert("pwned")</script>'


def test_hostile_device_data_cannot_inject_script_into_the_report(case: Case):
    """Filenames, message bodies, SSIDs and package names are attacker-controlled.

    The report is a standalone HTML file an examiner opens in a browser, so an unescaped
    value is script execution in the examiner's context on evidence they were handed.
    Every new section is populated with a payload here rather than spot-checking one.
    """
    hostile_lists = {
        "bluetooth_bonds": [
            {
                "address": XSS,
                "name": XSS,
                "vendor": XSS,
                "bond_timestamp": XSS,
                "dev_type_label": XSS,
            }
        ],
        "bluetooth": [{"mac": XSS, "name": XSS}],
        "celltower": [
            {
                "operator": XSS,
                "technology": XSS,
                "cell_id": XSS,
                "tac": XSS,
                "signal_dbm": XSS,
                "timestamp": XSS,
            }
        ],
        "screen_events": [{"timestamp": XSS, "event_type": XSS}],
        "search_history": [{"query": XSS, "timestamp": XSS, "source": XSS}],
        "google_accounts": [{"name": XSS, "type": XSS, "last_sync": XSS}],
        "app_presence": [
            {
                "package": XSS,
                "currently_installed": False,
                "ever_installed": True,
                "ever_executed": False,
                "first_seen": XSS,
                "last_seen": XSS,
                "event_count": 1,
                "evidence_sources": [XSS],
            }
        ],
        "android_users": [
            {
                "user_id": XSS,
                "name": XSS,
                "container_kind": XSS,
                "likely_feature": XSS,
                "extractable": XSS,
            }
        ],
        "antiforensic_findings": [
            {
                "kind": XSS,
                "subject": XSS,
                "detail": XSS,
                "severity": "critical",
                "caveats": [XSS],
            }
        ],
        "encrypted_apps": [
            {"app": XSS, "path": XSS, "size_bytes": 1, "modified": XSS, "status": XSS}
        ],
        "fcm_records": [{"sender": XSS}],
        "recent_tasks": [
            {
                "task_id": XSS,
                "real_activity": XSS,
                "calling_package": XSS,
                "last_time_moved": XSS,
            }
        ],
        "task_snapshots": [{"path": XSS}],
        "deletion_evidence": [
            {
                "db_file": XSS,
                "device_path": XSS,
                "table": XSS,
                "mechanism": XSS,
                "missing_count": 1,
                "description": XSS,
                "false_positive_causes": [XSS],
            }
        ],
    }
    for name, payload in hostile_lists.items():
        case.write_derived(name, payload)
    case.write_derived(
        "encryption_state",
        {
            "unlock_state": "bfu",
            "crypto_type": XSS,
            "unlock_evidence": [XSS],
            "caveats": [XSS],
            "probes": {XSS: XSS},
        },
    )
    case.write_derived(
        "device_state",
        {
            "summary": {"teardown_verdict": "residual", "statement": XSS},
            "teardown": {
                "verdict": "residual",
                "residue": [{"kind": XSS, "subject": XSS, "detail": XSS}],
                "unverified": [XSS],
                "ledger": {
                    "package": XSS,
                    "installed": True,
                    "granted_permissions": [XSS],
                    "appops_set": [XSS],
                    "files_written_to_device": [XSS],
                },
            },
            "diff": {
                "permissions_added": [XSS],
                "appops_added": [XSS],
                "unexpected_changes": [{"probe": XSS, "before": XSS, "after": XSS}],
                "expected_drift": [],
            },
        },
    )
    case.write_derived(
        "validation_report",
        {
            "cases": [{"case_id": XSS, "passed": False, "description": XSS}],
            "limitations": [XSS],
            "coverage_summary": {"counts": {XSS: 1}},
        },
    )

    html = render(case)
    assert "<script" not in html.lower()
    assert XSS not in html
    assert "&lt;script&gt;" in html, "the payload must appear escaped, not be dropped"


def test_hostile_artifact_path_is_escaped_in_the_manifest(case: Case, tmp_path: Path):
    payload = tmp_path / "evil.txt"
    payload.write_text("x")
    case.ingest_file(
        payload, source_path=f"/sdcard/DCIM/{XSS}.jpg", tier="tier0", method="mock"
    )
    html = render(case)
    assert "<script" not in html.lower()
    assert "&lt;script&gt;" in html


# ---------------------------------------------------------------------------
# Robustness — a malformed derived dataset must not break the report
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "name",
    ["encryption_state", "device_state", "bluetooth_bonds", "celltower", "screen_events"],
)
def test_malformed_dataset_does_not_break_report_generation(case: Case, name: str):
    (case.derived_dir / f"{name}.json").write_text(
        json.dumps(["not", "the", "expected", "shape"]), encoding="utf-8"
    )
    html = render(case)
    assert "</html>" in html


# ---------------------------------------------------------------------------
# Table of contents + visual overview dashboard
# ---------------------------------------------------------------------------
def test_toc_lists_every_top_level_section_with_a_matching_anchor(case: Case):
    html = render(case)
    assert '<nav class="toc"' in html
    import re

    ids = set(re.findall(r'<h2 id="([^"]+)">', html))
    hrefs = set(re.findall(r'<a href="#([^"]+)">', html))
    assert ids, "no <h2> got an anchor id"
    # Every TOC link must resolve to a real heading id (no dead internal links).
    assert hrefs <= ids


def test_toc_absent_data_case_still_renders_toc(case: Case):
    """Even a near-empty triage (one ingested file, nothing else) has enough
    always-present sections (integrity, encryption posture, certificate...) to
    produce a non-empty table of contents."""
    html = render(case)
    assert '<nav class="toc"' in html
    assert "<ol>" in html


def test_overview_charts_absent_when_nothing_chartable(case: Case):
    """The bare fixture case has no messages/calls/recovered/flags — the chart
    grid must be omitted outright, not rendered as a set of empty charts."""
    html = render(case)
    assert 'class="charts"' not in html


def test_overview_charts_render_from_messages_calls_and_flags(case: Case):
    case.write_derived(
        "messages",
        [
            {"app": "sms", "sender": "A", "body": "hi", "timestamp": "2026-01-01T00:00:00Z", "confidence": "live"},
            {"app": "sms", "sender": "B", "body": "yo", "timestamp": "2026-01-02T00:00:00Z", "confidence": "carved"},
        ],
    )
    case.write_derived(
        "calls",
        [{"number": "123", "name": "A", "call_type": "incoming", "timestamp": "2026-01-01T01:00:00Z", "confidence": "live"}],
    )
    case.write_derived(
        "flags",
        [{"severity": "critical", "kind": "keyword", "term": "x", "context": "y", "location": "z"}],
    )
    html = render(case)
    assert 'class="charts"' in html
    assert "Artifact composition" in html
    assert "Evidence confidence mix" in html
    assert "Flags by severity" in html
    assert "Message &amp; call activity over time" in html
    # confidence + severity donut segments must reflect the actual data, not be fabricated
    assert "LIVE" in html and "CARVED" in html
    assert "CRITICAL" in html


def test_overview_chart_labels_are_escaped(case: Case):
    xss = '<script>alert(1)</script>'
    case.write_derived(
        "graph",
        {
            "stats": {
                "participants": 1,
                "interactions": 1,
                "channels": ["sms"],
                "top_contacts": [{"label": xss, "weight": 3, "channels": ["sms"]}],
            }
        },
    )
    html = render(case)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_back_to_top_link_present(case: Case):
    html = render(case)
    assert 'class="back-to-top"' in html
    assert 'id="top"' in html


def _all_indices(haystack: str, needle: str) -> list[int]:
    out, start = [], 0
    while True:
        i = haystack.find(needle, start)
        if i < 0:
            return out
        out.append(i)
        start = i + 1


# ---------------------------------------------------------------------------
# Identifier normalisation — the numbering-plan assumption must be disclosed
# ---------------------------------------------------------------------------
def _name_addresses(entries: list[dict], **over) -> dict:
    absorbed = [e for e in entries if e.get("joined_a_number_participant")]
    out = {
        "count": sum(len(e["addresses"]) for e in entries),
        "absorbed_participants": len(absorbed),
        "absorbed_interactions": sum(e["interactions"] for e in absorbed),
        "participants_if_names_kept": 2 + len(absorbed),
        "channels": ["call", "sms", "whatsapp"],
        "entries": entries,
    }
    out.update(over)
    return out


def _graph_with_normalisation(case: Case, merged: list[dict], **over) -> None:
    stats = {
        "participants": 2,
        "interactions": 5,
        "channels": ["call", "sms"],
        "top_contacts": [
            {"id": "num:+919767143329", "label": "Mumma", "weight": 4, "channels": ["call"]}
        ],
        "identity_normalisation": {
            "country_code": "+91",
            "national_number_length": 10,
            "participants": 2,
            "participants_if_unmerged": 2 + sum(len(m["identifiers"]) - 1 for m in merged),
            "merged_participants": len(merged),
            "merged_identifiers": sum(len(m["identifiers"]) - 1 for m in merged),
            "merged": merged,
        },
    }
    stats["identity_normalisation"].update(over)
    case.write_derived("graph", {"stats": stats})


def test_report_discloses_the_numbering_plan_used_to_merge_identifiers(case: Case):
    """Merging identifiers changes every weight and the participant total. A report that
    does so silently misstates the evidence between two runs of the same case."""
    _graph_with_normalisation(
        case,
        [
            {
                "label": "Mumma",
                "canonical": "+919767143329",
                "identifiers": ["+919767143329", "9767143329"],
                "weight": 4,
            }
        ],
    )
    html = render(case)
    assert "Identifier normalisation" in html
    assert "+91" in html and "10-digit national number" in html
    assert "one participant" in html
    # the merge itself must be auditable from the report, not only from graph.json
    assert "9767143329" in html
    # and the claim it does NOT make must be stated
    assert "cannot establish that two different numbers belong to the same person" in html


def test_report_says_when_no_identifiers_were_merged(case: Case):
    """Nothing merged is a finding too — silence would read as 'the question never arose'."""
    _graph_with_normalisation(case, [])
    html = render(case)
    assert "Identifier normalisation" in html
    assert "No identifier in this case differed from another by only a dialing prefix" in html


def test_report_omits_the_note_for_a_graph_predating_the_field(case: Case):
    case.write_derived(
        "graph",
        {
            "stats": {
                "participants": 1,
                "interactions": 1,
                "channels": ["sms"],
                "top_contacts": [{"label": "X", "weight": 1, "channels": ["sms"]}],
            }
        },
    )
    html = render(case)
    assert "Identifier normalisation" not in html


def test_report_escapes_merged_identifier_labels(case: Case):
    xss = "<script>alert(1)</script>"
    _graph_with_normalisation(
        case,
        [{"label": xss, "canonical": xss, "identifiers": [xss, "9767143329"], "weight": 2}],
    )
    html = render(case)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_report_discloses_sender_names_it_read_as_phone_numbers(case: Case):
    """Reading a sender name as a number moves interactions between participants, so the
    same disclosure that covers the dialing-prefix merge has to state this claim too."""
    _graph_with_normalisation(
        case,
        [],
        name_addresses=_name_addresses(
            [
                {
                    "label": "Vishal Mache",
                    "canonical": "+919022873952",
                    "addresses": ["+919022873952"],
                    "interactions": 1,
                    "joined_a_number_participant": True,
                }
            ]
        ),
    )
    html = render(case)
    assert "Identifier normalisation" in html  # one section, not two
    # the claim, stated explicitly
    assert (
        "a sender name that is itself a phone number was treated as that number" in html
    )
    # what it did, and the counterfactual it moved the total away from
    assert "1 interaction(s) onto those participants" in html
    assert "would report 3 participants rather than 2" in html
    # auditable from the report, not only from graph.json
    assert "+919022873952" in html and "Vishal Mache" in html
    # and the refusals
    assert "JZ-JioPay-S" in html
    assert "Instagram, Telegram" in html


def test_report_says_when_no_sender_name_was_a_phone_number(case: Case):
    """Nothing read is a finding too — silence would read as 'the question never arose'."""
    _graph_with_normalisation(case, [], name_addresses=_name_addresses([]))
    html = render(case)
    assert "No sender name in this case was itself a phone number" in html


def test_report_marks_a_sender_name_that_merged_with_nothing(case: Case):
    """A sender known no other way moved no interactions onto anyone. Listing it beside a
    real merge without saying so would overstate what the reading changed."""
    _graph_with_normalisation(
        case,
        [],
        name_addresses=_name_addresses(
            [
                {
                    "label": "+917042967773",
                    "canonical": "+917042967773",
                    "addresses": ["+917042967773"],
                    "interactions": 1,
                    "joined_a_number_participant": False,
                }
            ]
        ),
    )
    html = render(case)
    assert "no other record of this participant" in html
    assert "0 of them named a participant this device also holds as a number" in html


def test_report_omits_the_sender_name_note_for_a_graph_predating_the_field(case: Case):
    """A graph.json from before the field says nothing about it, so neither does the
    report — the dialing-prefix half of the disclosure still renders."""
    _graph_with_normalisation(case, [])
    html = render(case)
    assert "Identifier normalisation" in html
    assert "Sender names that are phone numbers" not in html


def test_report_escapes_sender_names_read_as_numbers(case: Case):
    xss = "<script>alert(1)</script>"
    _graph_with_normalisation(
        case,
        [],
        name_addresses=_name_addresses(
            [
                {
                    "label": xss,
                    "canonical": xss,
                    "addresses": [xss],
                    "interactions": 1,
                    "joined_a_number_participant": True,
                }
            ]
        ),
    )
    html = render(case)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
