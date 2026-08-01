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


def _all_indices(haystack: str, needle: str) -> list[int]:
    out, start = [], 0
    while True:
        i = haystack.find(needle, start)
        if i < 0:
            return out
        out.append(i)
        start = i + 1
