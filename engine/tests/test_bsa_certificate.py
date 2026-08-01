"""Tests for the BSA 2023 s.63 Schedule certificate generator.

These tests are as much about the project's honesty model as about correctness: an
unsigned template must never be presentable as a certificate, absent identifiers must
render as an explicit NOT CAPTURED, and the repealed statute must not be named anywhere
on the generated document.
"""

from __future__ import annotations

import json

import pytest

from triage.forensics.bsa_certificate import (
    BSA_REFERENCE,
    BSA_SCHEDULE_PART_A,
    BSA_SCHEDULE_PART_B,
    IEA_65B_MIGRATION_NOTE,
    NOT_CAPTURED,
    TEMPLATE_DISCLAIMER,
    BsaCertificate,
    CertificateParty,
    build_certificate,
    ist_timestamp,
    render_certificate_html,
    render_certificate_text,
    validate_certificate,
)


# ---------------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------------


@pytest.fixture
def case_meta() -> dict:
    return {
        "case_id": "CASE-2025-0042",
        "examiner": "Insp. R. Sharma",
        "legal_authority": "Search warrant No. 118/2025, CJM Pune",
        "generated_at": "2025-03-14T09:05:00Z",
    }


@pytest.fixture
def device() -> dict:
    return {
        "manufacturer": "Xiaomi",
        "model": "Redmi Note 12",
        "imei": "356938035643809",
        "serial": "8ab12cd34ef5",
        "mac": "a4:50:46:11:22:33",
        "android_version": "13",
    }


@pytest.fixture
def manifest() -> list[dict]:
    """A realistic slice of a case manifest.json as written by triage.custody."""
    return [
        {
            "artifact_id": "a00000",
            "source_path": "/sdcard/DCIM/Camera/IMG_20250310_113045.jpg",
            "stored_path": "artifacts/sdcard/DCIM/Camera/IMG_20250310_113045.jpg",
            "size_bytes": 3_145_728,
            "sha256": "a" * 64,
            "md5": "b" * 32,
        },
        {
            "artifact_id": "a00001",
            "source_path": "/sdcard/Android/media/com.whatsapp/WhatsApp/Databases/msgstore.db.crypt15",
            "stored_path": "artifacts/sdcard/whatsapp/msgstore.db.crypt15",
            "size_bytes": 12_582_912,
            "sha256": "c" * 64,
            "md5": "d" * 32,
        },
    ]


# ---------------------------------------------------------------------------------
# 1-4: IST conversion
# ---------------------------------------------------------------------------------


def test_ist_timestamp_basic_offset():
    """UTC+05:30 exactly, rendered 24-hour."""
    assert ist_timestamp("2025-03-14T09:05:00Z") == "2025-03-14 14:35:00 IST"


def test_ist_timestamp_crosses_midnight_forward():
    """19:30Z on the 14th is 01:00 IST on the 15th — the date must roll over too."""
    assert ist_timestamp("2025-03-14T19:30:00Z") == "2025-03-15 01:00:00 IST"
    # And the very edge: 18:30Z is exactly 00:00 IST the next day.
    assert ist_timestamp("2025-03-14T18:30:00Z") == "2025-03-15 00:00:00 IST"
    # One second before is still the same day, at 23:59:59.
    assert ist_timestamp("2025-03-14T18:29:59Z") == "2025-03-14 23:59:59 IST"


def test_ist_timestamp_offset_is_constant_year_round():
    """India observes no daylight saving: the offset must be identical in January and
    July, and must not track any northern-hemisphere DST transition."""
    winter = ist_timestamp("2025-01-15T12:00:00Z")
    summer = ist_timestamp("2025-07-15T12:00:00Z")
    assert winter == "2025-01-15 17:30:00 IST"
    assert summer == "2025-07-15 17:30:00 IST"
    # Straddling the US/EU DST switch dates changes nothing.
    assert ist_timestamp("2025-03-09T06:00:00Z") == "2025-03-09 11:30:00 IST"
    assert ist_timestamp("2025-11-02T06:00:00Z") == "2025-11-02 11:30:00 IST"


def test_ist_timestamp_tolerates_variants_and_degrades_on_garbage():
    """Offsets, naive strings and fractional seconds all work; garbage is flagged, not
    silently replaced with 'now' (a plausible-but-wrong legal timestamp is worse)."""
    assert ist_timestamp("2025-03-14T09:05:00+00:00") == "2025-03-14 14:35:00 IST"
    assert ist_timestamp("2025-03-14T09:05:00") == "2025-03-14 14:35:00 IST"
    assert ist_timestamp("2025-03-14T14:35:00+05:30") == "2025-03-14 14:35:00 IST"
    assert ist_timestamp("2025-03-14T09:05:00.123456Z").startswith("2025-03-14 14:35:00")
    assert ist_timestamp("not a timestamp").startswith("UNPARSEABLE TIMESTAMP")
    assert ist_timestamp("").startswith("UNPARSEABLE TIMESTAMP")
    # None means "now" and must still produce a well-formed IST stamp.
    assert ist_timestamp(None).endswith(" IST")


# ---------------------------------------------------------------------------------
# 5-7: construction
# ---------------------------------------------------------------------------------


def test_build_certificate_from_realistic_manifest(case_meta, device, manifest):
    cert = build_certificate(case_meta, device, manifest, place="Pune")

    assert isinstance(cert, BsaCertificate)
    assert cert.case_id == "CASE-2025-0042"
    assert cert.place == "Pune"
    assert cert.artifact_count == 2
    assert cert.total_bytes == 3_145_728 + 12_582_912
    assert cert.generated_at_ist == "2025-03-14 14:35:00 IST"
    assert cert.device["make_and_model"] == "Xiaomi Redmi Note 12"
    assert cert.device["serial"] == "8ab12cd34ef5"
    assert cert.device["imei"] == "356938035643809"

    # Both Parts are fully populated in statutory order.
    assert [f["no"] for f in cert.part_a["fields"]] == [d["no"] for d in BSA_SCHEDULE_PART_A]
    assert [f["no"] for f in cert.part_b["fields"]] == [d["no"] for d in BSA_SCHEDULE_PART_B]
    assert cert.part_a["subtitle"] == "(To be filled by the Party)"
    assert cert.part_b["subtitle"] == "(To be filled by the Expert)"

    # Device particulars must agree between Part A and Part B.
    a = {f["no"]: f["value"] for f in cert.part_a["fields"]}
    b = {f["no"]: f["value"] for f in cert.part_b["fields"]}
    assert a["A8"] == b["B8"] == "Xiaomi Redmi Note 12"
    assert a["A10"] == b["B10"] == "8ab12cd34ef5"
    assert a["A15"] == b["B13"]  # identical hash statement in both Parts


def test_named_algorithm_accompanies_every_hash_value(case_meta, device, manifest):
    """A bare hex string on a legal form is unattributable — the Schedule pairs the value
    with a named algorithm, so every row we emit must carry one."""
    cert = build_certificate(case_meta, device, manifest, place="Pune")
    assert len(cert.hash_values) == 2
    for h in cert.hash_values:
        assert h["algorithm"] == "SHA-256"
        assert h["md5_algorithm"] == "MD5"
        assert h["sha256"] and h["md5"]

    txt = render_certificate_text(cert)
    htm = render_certificate_html(cert)
    for h in cert.hash_values:
        assert f"SHA-256: {h['sha256']}" in txt
        assert h["sha256"] in htm
    # The statutory (unhyphenated) spelling is surfaced alongside ours.
    assert "SHA256" in htm
    assert a_value_of(cert, "A16") == "SHA256"
    assert a_value_of(cert, "B14") == "SHA256"


def a_value_of(cert: BsaCertificate, no: str) -> str:
    """Helper: look up a pre-filled Schedule value by its ordering handle."""
    for part in (cert.part_a, cert.part_b):
        for f in part["fields"]:
            if f["no"] == no:
                return f["value"]
    raise AssertionError(f"no such field {no}")


def test_legacy_sha256_hash_key_is_tolerated(case_meta, device):
    """Older case folders wrote 'sha256_hash'/'md5_hash'. They must still certify."""
    legacy = [
        {
            "artifact_id": "a00000",
            "stored_path": "artifacts/old/thing.db",
            "size_bytes": 1024,
            "sha256_hash": "e" * 64,
            "md5_hash": "f" * 32,
        }
    ]
    cert = build_certificate(case_meta, device, legacy, place="Pune")
    assert cert.artifact_count == 1
    assert cert.total_bytes == 1024
    assert cert.hash_values[0]["sha256"] == "e" * 64
    assert cert.hash_values[0]["md5"] == "f" * 32
    assert cert.hash_values[0]["algorithm"] == "SHA-256"
    # A single-artifact production states the actual hash in the Schedule blank.
    assert "e" * 64 in a_value_of(cert, "A15")


# ---------------------------------------------------------------------------------
# 8-9: signatures
# ---------------------------------------------------------------------------------


def test_both_signature_blocks_always_rendered_and_default_unsigned(
    case_meta, device, manifest
):
    cert = build_certificate(case_meta, device, manifest, place="Pune")
    assert cert.custodian.signed is False
    assert cert.expert.signed is False
    assert cert.custodian.role == "custodian"
    assert cert.expert.role == "expert"
    assert "UNSIGNED" in cert.unsigned_warning

    htm = render_certificate_html(cert)
    txt = render_certificate_text(cert)
    for out in (htm, txt):
        assert "PART A" in out
        assert "PART B" in out
        assert "(Name and signature)" in out  # Part A block: no designation
        assert "(Name, designation and signature)" in out  # Part B block: designation
        assert "NOT SIGNED" in out
    # Two signature blocks, no more and no fewer.
    assert htm.count("class='bsa-sig'") == 2
    assert txt.count("SIGNATURE BLOCK —") == 2


def test_partial_signature_is_not_treated_as_signed(case_meta, device, manifest):
    """s.63(4) needs BOTH signatures; one alone must not read as effective."""
    cert = build_certificate(
        case_meta,
        device,
        manifest,
        place="Pune",
        custodian={
            "name": "Insp. R. Sharma",
            "designation": "Investigating Officer",
            "organisation": "Pune City Police",
            "signed": True,
            "signed_at": "2025-03-14T10:00:00Z",
        },
        expert={"name": "Dr. A. Iyer", "designation": "Cyber Forensics Examiner"},
    )
    assert "PARTIALLY SIGNED" in cert.unsigned_warning
    v = validate_certificate(cert)
    assert v["complete"] is False
    assert "PART B signature (B16)" in v["missing"]
    assert "PART A signature (A18)" not in v["missing"]


# ---------------------------------------------------------------------------------
# 10-11: validation
# ---------------------------------------------------------------------------------


def test_validate_unsigned_certificate_is_incomplete(case_meta, device, manifest):
    cert = build_certificate(case_meta, device, manifest, place="Pune")
    v = validate_certificate(cert)
    assert v["complete"] is False
    assert "PART A signature (A18)" in v["missing"]
    assert "PART B signature (B16)" in v["missing"]
    assert "PART B expert name (B1)" in v["missing"]
    assert any("BOTH" in w for w in v["warnings"])
    # The rendered document says so on its face.
    assert "INCOMPLETE" in render_certificate_html(cert)
    assert "INCOMPLETE" in render_certificate_text(cert)


def test_validate_fully_signed_certificate_is_complete(case_meta, device, manifest):
    cert = build_certificate(
        case_meta,
        device,
        manifest,
        place="Pune",
        custodian={
            "name": "Insp. R. Sharma",
            "designation": "Investigating Officer",
            "organisation": "Pune City Police",
            "signed": True,
            "signed_at": "2025-03-14T10:00:00Z",
        },
        expert={
            "name": "Dr. A. Iyer",
            "designation": "Cyber Forensics Examiner",
            "organisation": "State FSL",
            "signed": True,
            "signed_at": "2025-03-14T11:15:00Z",
        },
    )
    v = validate_certificate(cert)
    assert v["missing"] == []
    assert v["complete"] is True
    assert cert.unsigned_warning == ""
    # Even 'complete' must still carry the human-verification warning — the tool never
    # certifies anything itself.
    assert any("independently verified" in w for w in v["warnings"])
    htm = render_certificate_html(cert)
    assert "STRUCTURALLY COMPLETE" in htm
    assert TEMPLATE_DISCLAIMER in render_certificate_text(cert)


# ---------------------------------------------------------------------------------
# 12-14: honesty guarantees
# ---------------------------------------------------------------------------------


def test_repealed_statute_named_only_in_the_migration_note(case_meta, device, manifest):
    """The generated document must cite BSA 2023 s.63 and the Schedule, and must never
    name the repealed 1872 statute — that name belongs only to the migration note."""
    needle = "Indian Evidence Act"
    assert needle in IEA_65B_MIGRATION_NOTE
    assert "65B" in IEA_65B_MIGRATION_NOTE
    assert "repeal" in IEA_65B_MIGRATION_NOTE.lower()
    assert "2024-07-01" in IEA_65B_MIGRATION_NOTE

    cert = build_certificate(case_meta, device, manifest, place="Pune")
    surfaces = [
        render_certificate_html(cert),
        render_certificate_text(cert),
        json.dumps(cert.to_dict()),
        BSA_REFERENCE,
        TEMPLATE_DISCLAIMER,
        json.dumps(BSA_SCHEDULE_PART_A),
        json.dumps(BSA_SCHEDULE_PART_B),
    ]
    for s in surfaces:
        assert needle not in s

    # ...and the correct statute IS cited.
    assert "Bharatiya Sakshya Adhiniyam, 2023" in BSA_REFERENCE
    assert "section 63" in BSA_REFERENCE
    assert "SCHEDULE" in BSA_REFERENCE
    assert "2024-07-01" in BSA_REFERENCE
    assert "section 63(4)" in render_certificate_html(cert)
    assert "SECTION 63(4)" in render_certificate_text(cert)
    assert "THE SCHEDULE [See section 63(4)(c)]" in render_certificate_text(cert)


def test_output_declares_itself_an_illustrative_template(case_meta, device, manifest):
    cert = build_certificate(case_meta, device, manifest, place="Pune")
    assert "ILLUSTRATIVE TEMPLATE" in TEMPLATE_DISCLAIMER
    assert TEMPLATE_DISCLAIMER in cert.caveats
    for out in (render_certificate_html(cert), render_certificate_text(cert)):
        assert "ILLUSTRATIVE TEMPLATE" in out
        assert "NOT A FILED OR EXECUTED LEGAL CERTIFICATE" in out
        assert "must be reviewed by counsel" in out
    assert TEMPLATE_DISCLAIMER in cert.to_dict()["template_disclaimer"]


def test_html_escapes_hostile_values(case_meta, device, manifest):
    """Manifest paths and metadata come off a seized device; they are hostile input."""
    hostile = "<script>alert(1)</script>"
    meta = dict(case_meta, case_id=hostile)
    dev = dict(device, model=hostile)
    man = list(manifest) + [
        {
            "artifact_id": hostile,
            "stored_path": f"/sdcard/{hostile}/x.jpg",
            "size_bytes": 10,
            "sha256": "9" * 64,
            "md5": "8" * 32,
        }
    ]
    cert = build_certificate(
        meta, dev, man, place=hostile, custodian={"name": hostile}, expert={"name": hostile}
    )
    htm = render_certificate_html(cert)
    assert "<script>" not in htm
    assert "alert(1)" in htm  # the value is preserved, just neutralised
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in htm
    # Quotes are escaped too, so a value cannot break out of an attribute.
    cert2 = build_certificate(meta, dev, man, place='" onmouseover="x')
    assert '" onmouseover="' not in render_certificate_html(cert2)


def test_missing_imei_and_serial_render_as_explicit_placeholder(case_meta, manifest):
    """A blank on a legal form reads as 'inapplicable'. 'We could not read it' is a
    different claim and must be stated."""
    bare_device = {"manufacturer": "Samsung", "model": "SM-A536E", "android_version": "14"}
    cert = build_certificate(case_meta, bare_device, manifest, place="Pune")

    assert cert.device["imei"] == NOT_CAPTURED
    assert cert.device["serial"] == NOT_CAPTURED
    assert a_value_of(cert, "A10") == NOT_CAPTURED
    assert a_value_of(cert, "A11") == NOT_CAPTURED
    assert a_value_of(cert, "B10") == NOT_CAPTURED
    assert a_value_of(cert, "B11") == NOT_CAPTURED
    # Colour can never be obtained by a logical acquisition.
    assert a_value_of(cert, "A9") == NOT_CAPTURED

    assert any("Serial Number was NOT CAPTURED" in c for c in cert.caveats)
    assert any("IMEI/UIN/UID/MAC/Cloud ID was NOT CAPTURED" in c for c in cert.caveats)
    v = validate_certificate(cert)
    assert "Serial Number (A10/B10)" in v["missing"]
    assert "IMEI/UIN/UID/MAC/Cloud ID (A11/B11)" in v["missing"]
    assert NOT_CAPTURED in render_certificate_text(cert)


def test_empty_manifest_certifies_nothing(case_meta, device):
    cert = build_certificate(case_meta, device, [], place="Pune")
    assert cert.artifact_count == 0
    assert cert.total_bytes == 0
    assert cert.hash_values == []
    assert a_value_of(cert, "A15") == NOT_CAPTURED
    assert a_value_of(cert, "B13") == NOT_CAPTURED
    assert any("NO artifacts" in c for c in cert.caveats)

    v = validate_certificate(cert)
    assert v["complete"] is False
    assert "Hash value/s (Schedule fields A15/B13)" in v["missing"]
    assert any("no electronic record to certify" in w for w in v["warnings"])
    assert "Nothing can be certified" in render_certificate_html(cert)


def test_malformed_manifest_entries_are_skipped_not_raised(case_meta, device):
    """Never raise on bad input; skip the record and say that you did."""
    junk = [
        "not a dict",
        None,
        {"artifact_id": "a00001", "stored_path": "x", "size_bytes": "banana", "sha256": "1" * 64},
        {"artifact_id": "a00002", "stored_path": "y", "size_bytes": 5},  # no hash at all
    ]
    cert = build_certificate(case_meta, device, junk, place="Pune")
    assert cert.artifact_count == 2  # two dict rows survived
    assert cert.total_bytes == 5  # 'banana' excluded, not crashed on
    assert any("was not an object and was skipped" in c for c in cert.caveats)
    assert any("non-numeric size_bytes" in c for c in cert.caveats)
    assert any("carry no SHA-256 value" in c for c in cert.caveats)
    hashes = {h["artifact_id"]: h["sha256"] for h in cert.hash_values}
    assert hashes["a00002"] == NOT_CAPTURED
    v = validate_certificate(cert)
    assert any("cannot be certified" in w for w in v["warnings"])
    # Rendering must survive it too.
    assert render_certificate_html(cert)
    assert render_certificate_text(cert)


def test_certificate_json_round_trip(case_meta, device, manifest):
    cert = build_certificate(
        case_meta,
        device,
        manifest,
        place="Pune",
        custodian={"name": "Insp. R. Sharma", "signed": True, "signed_at": "2025-03-14T10:00:00Z"},
        expert={"name": "Dr. A. Iyer", "designation": "Cyber Forensics Examiner"},
    )
    d = cert.to_dict()
    blob = json.dumps(d)  # must be JSON-safe with no custom encoder
    assert json.loads(blob) == d

    assert d["case_id"] == "CASE-2025-0042"
    assert d["custodian"]["signed"] is True
    assert d["custodian"]["signed_at_ist"] == "2025-03-14 15:30:00 IST"
    assert d["expert"]["signed"] is False
    assert d["expert"]["signed_at_ist"] == ""
    assert d["statute"] == BSA_REFERENCE
    assert len(d["part_a"]["fields"]) == len(BSA_SCHEDULE_PART_A)
    assert len(d["part_b"]["fields"]) == len(BSA_SCHEDULE_PART_B)
    assert d["hash_values"][0]["algorithm"] == "SHA-256"


# ---------------------------------------------------------------------------------
# 18-20: schedule descriptors themselves
# ---------------------------------------------------------------------------------


def test_schedule_descriptors_are_well_formed_and_flag_unverified_items():
    for descriptors, prefix, count in (
        (BSA_SCHEDULE_PART_A, "A", 21),
        (BSA_SCHEDULE_PART_B, "B", 19),
    ):
        assert len(descriptors) == count
        seen = set()
        for i, d in enumerate(descriptors, start=1):
            assert set(d) == {"no", "label", "help"}
            assert d["no"] == f"{prefix}{i}"
            assert d["no"] not in seen
            seen.add(d["no"])
            assert d["label"].strip()
            assert d["help"].strip()

    # Anything the research could not confirm carries an explicit UNVERIFIED- marker.
    unverified = [
        d["no"]
        for d in (BSA_SCHEDULE_PART_A + BSA_SCHEDULE_PART_B)
        if "UNVERIFIED-" in d["help"]
    ]
    # The tick-box glyph fields plus the invented numbering plus the Part B case citation.
    assert {"A6", "A14", "A16", "B6", "B14"}.issubset(set(unverified))
    assert "UNVERIFIED-NUMBERING" in BSA_SCHEDULE_PART_A[0]["help"]
    assert "UNVERIFIED-CITATION" in BSA_SCHEDULE_PART_B[0]["help"]


def test_statutory_wording_differences_between_parts_are_preserved():
    """Part A/Part B are NOT copies of each other — a widely circulated third-party form
    duplicates Part A as Part B, which is exactly the error to avoid."""
    a = {d["no"]: d["label"] for d in BSA_SCHEDULE_PART_A}
    b = {d["no"]: d["label"] for d in BSA_SCHEDULE_PART_B}

    # Part A: the party says "I have produced"; Part B: the expert says "are obtained from".
    assert a["A5"].startswith("I have produced electronic record/output of the digital record")
    assert b["B5"].startswith("The produced electronic record/output of the digital record are obtained")

    # Signature blocks differ: designation is required only in Part B.
    assert a["A18"] == "(Name and signature)"
    assert b["B16"] == "(Name, designation and signature)"

    # The lawful-control recital and the Owned/Maintained/Managed/Operated boxes exist
    # only in Part A.
    joined_b = " ".join(b.values())
    assert "under the lawful control" in a["A13"]
    assert "under the lawful control" not in joined_b
    assert "Owned" in a["A14"]
    assert "Owned" not in joined_b

    # Both Parts carry the same eight source classes and the same four algorithms.
    for cls in ("Computer / Storage Media", "DVR", "Mobile", "Flash Drive", "CD/DVD", "Server", "Cloud", "Other"):
        assert cls in a["A6"] and cls in b["B6"]
    for algo in ("SHA1", "SHA256", "MD5", "Other"):
        assert algo in a["A16"] and algo in b["B14"]

    # Statutory spellings, not commentary spellings.
    assert "SHA-256" not in a["A16"]
    assert "UUID" not in a["A11"] and "UUID" not in b["B11"]
    assert "IMEI/UIN/UID/MAC/Cloud ID" in a["A11"]

    # 24-hour IST and DD/MM/YYYY in BOTH Parts.
    assert "In 24 hours format" in a["A20"] and "In 24 hours format" in b["B18"]
    assert "DD/MM/YYYY" in a["A19"] and "DD/MM/YYYY" in b["B17"]


def test_lawful_control_recital_is_never_pre_affirmed(case_meta, device, manifest):
    """On a seized device the person in lawful control during the period of regular use
    was the suspect. The tool must not assert otherwise on the examiner's behalf."""
    cert = build_certificate(case_meta, device, manifest, place="Pune")
    assert a_value_of(cert, "A13").startswith("REQUIRES REVIEW")
    assert "lawful control" in a_value_of(cert, "A13")
    assert a_value_of(cert, "A14").startswith("REQUIRES REVIEW")
    # A13 being unreviewed does not let the certificate pass as complete.
    assert validate_certificate(cert)["complete"] is False


def test_certificate_party_defaults_and_dict():
    p = CertificateParty()
    assert p.name == "" and p.designation == "" and p.organisation == ""
    assert p.signed is False and p.signed_at == ""
    d = p.to_dict()
    assert d["signed"] is False and d["signed_at_ist"] == ""
    signed = CertificateParty(
        name="Dr. A. Iyer", role="expert", signed=True, signed_at="2025-03-14T19:00:00Z"
    )
    assert signed.to_dict()["signed_at_ist"] == "2025-03-15 00:30:00 IST"
    # A signature timestamp supplied without signed=True is dropped, not honoured.
    from triage.forensics.bsa_certificate import _party

    ghost = _party({"name": "X", "signed": False, "signed_at": "2025-03-14T19:00:00Z"}, "expert")
    assert ghost.signed is False and ghost.signed_at == ""


def test_defaults_are_structurally_obvious_not_plausible():
    """An empty BsaCertificate must not look like a real one."""
    cert = BsaCertificate()
    assert cert.hash_algorithm == "SHA-256"
    assert cert.hash_values == [] and cert.caveats == []
    assert cert.custodian.signed is False and cert.expert.signed is False
    v = validate_certificate(cert)
    assert v["complete"] is False
    assert "Case identifier" in v["missing"]
    # And it still renders without raising, saying plainly that it is incomplete.
    assert "INCOMPLETE" in render_certificate_html(cert)
    assert validate_certificate("not a certificate")["complete"] is False
