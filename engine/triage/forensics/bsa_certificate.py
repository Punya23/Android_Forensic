"""BSA 2023 s.63 Schedule (Part A / Part B) certificate generator.

FORENSIC PURPOSE
----------------
Electronic records tendered in Indian criminal proceedings instituted on or after
2024-07-01 are governed by section 63 of the Bharatiya Sakshya Adhiniyam, 2023
(Act 47 of 2023). s.63(4) requires that a certificate "specified in the Schedule"
travel with the electronic record *at each instance where it is being submitted for
admission*. The Schedule is a two-part form: PART A is signed by the party (the person
in charge of the device or the management of the relevant activities) and PART B by an
expert. Both signatures are required — the statute's operative words are "...and an
expert...".

This module pre-fills that form from the case manifest so an examiner does not have to
re-type forty device identifiers and hash values by hand.

LIMITATIONS (read these before using the output for anything)
------------------------------------------------------------
1. The output of this module is an ILLUSTRATIVE TEMPLATE. It is *not* a certificate.
   A certificate under s.63(4) comes into existence only when a human being signs it
   having satisfied themselves of its contents. Nothing here is signed, sworn or
   affirmed, and the tool makes no representation about the truth of the recitals.
2. The statutory Schedule contains NO numbered fields — it is a free-text affidavit-style
   form with tick-boxes and blanks. The "A1..A21"/"B1..B19" identifiers used here are
   OUR OWN ordering handles for programmatic filling. They must not be reproduced on a
   document tendered to a court as though they were statutory numbering.
3. The Schedule provides no blank for describing the record or the manner of its
   production, even though s.63(4)(a) demands exactly that. We fold our description into
   the "any other relevant information ... (specify)" field and flag the gap; practitioners
   normally bolt on an annexure.
4. The lawful-control/regular-use recital in Part A is a *fixed* statutory recital with no
   blanks. In a seizure scenario it is frequently false as written — the person in lawful
   control of a seized handset during its period of regular use was the suspect, not the
   deponent. The tool therefore never pre-affirms it; it is emitted as REQUIRES REVIEW.
5. Field text was cross-checked against three official prints of the Act. Where a
   typographic element could not be confirmed in all three, the field's ``help`` text
   carries an ``UNVERIFIED-`` marker.

This module never shells out and never touches the device. It is a pure function of
(case metadata, device metadata, manifest).
"""

from __future__ import annotations

import html
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

# --------------------------------------------------------------------------------------
# Citations and constants
# --------------------------------------------------------------------------------------

BSA_REFERENCE: str = (
    "Bharatiya Sakshya Adhiniyam, 2023 (Act 47 of 2023), section 63 "
    "(Admissibility of electronic records) read with THE SCHEDULE "
    "[See section 63(4)(c)] — CERTIFICATE, PART A (to be filled by the Party) and "
    "PART B (to be filled by the Expert). Act assented 25 December 2023; "
    "brought into force 2024-07-01."
)

# The ONLY place in this module where the repealed statute may be named. Rendering
# functions deliberately do not emit this note, so the phrase never appears on the
# generated certificate itself.
IEA_65B_MIGRATION_NOTE: str = (
    "MIGRATION NOTE — the Indian Evidence Act, 1872 (and with it s.65B, the former "
    "certificate provision for electronic records) stands repealed with effect from "
    "2024-07-01 by the Bharatiya Sakshya Adhiniyam, 2023; s.63 BSA now occupies the field "
    "and s.61/s.62 BSA route all proof of electronic records through it. The four "
    "substantive conditions of s.63(2)(a)-(d) are a near-verbatim carry-over of the old "
    "s.65B(2)(a)-(d), so the 'condition precedent' jurisprudence (Anvar P.V. v. P.K. "
    "Basheer, (2014) 10 SCC 473, as restored by Arjun Panditrao Khotkar v. Kailash "
    "Kushanrao Gorantyal, (2020) 7 SCC 1) migrates intact. What changed is the FORM and "
    "the SIGNATORIES: the old provision prescribed no form and needed one signatory (a "
    "person in a responsible official position), whereas s.63(4) prescribes the two-part "
    "Schedule and requires BOTH a person in charge of the computer/communication device "
    "(or the management of the relevant activities) AND an expert to sign; the Schedule "
    "newly demands the hash value plus its named algorithm (SHA1/SHA256/MD5/Other) in "
    "both Parts with a hash report enclosed, express device identifiers (Make & Model, "
    "Color, Serial Number, IMEI/UIN/UID/MAC/Cloud ID), a source-type tick-list that "
    "contemplates Cloud for the first time, and Date/Time (IST, 24-hour)/Place stamps; "
    "and the certificate must now be submitted with the record 'at each instance where it "
    "is being submitted for admission' rather than once."
)

#: Rendered on every output. The tool must never imply it produced a filed certificate.
TEMPLATE_DISCLAIMER: str = (
    "ILLUSTRATIVE TEMPLATE — THIS IS NOT A FILED OR EXECUTED LEGAL CERTIFICATE. "
    "It was pre-filled automatically by the triage tool from the case manifest and is a "
    "drafting aid only. It has not been signed, sworn or affirmed by anyone. Before it can "
    "be tendered it must be reviewed by counsel, every recital independently verified, "
    "PART A signed by the person in charge of the device or the management of the relevant "
    "activities, and PART B signed by an expert. The tool makes no representation that any "
    "statement in this template is true."
)

#: Placeholder for a value the acquisition did not capture. Never leave a blank that a
#: reader could mistake for a verified-empty value.
NOT_CAPTURED: str = "NOT CAPTURED"

#: Placeholder for a field a human must decide, not the tool.
REQUIRES_REVIEW: str = "REQUIRES REVIEW — NOT PRE-FILLED BY THE TOOL"

_IST = timezone(timedelta(hours=5, minutes=30), "IST")

# The Schedule prints the algorithm names unhyphenated: "SHA1", "SHA256", "MD5".
# The hyphenated forms are commentary usage. Map ours onto the statutory spelling.
_STATUTORY_ALGO = {
    "SHA-256": "SHA256",
    "SHA256": "SHA256",
    "SHA-1": "SHA1",
    "SHA1": "SHA1",
    "MD5": "MD5",
}

# UNVERIFIED marker reused by the tick-box fields: the box glyphs are typographically
# present in two of the three official prints and were stripped by text extraction from
# the third (count and position agree across all three).
_UNVERIFIED_GLYPH = (
    "UNVERIFIED-GLYPH: the tick-box glyphs render as boxes in two official prints and were "
    "stripped by text extraction from the third; box count and order agree across all "
    "three. Confirm against a certified gazette page image before filing."
)

_UNVERIFIED_NUMBERING = (
    "UNVERIFIED-NUMBERING: the statutory Schedule is UNNUMBERED. This 'no' is an internal "
    "ordering handle invented by this tool and must not be printed on a tendered document."
)


# --------------------------------------------------------------------------------------
# Schedule field descriptors (verbatim statutory wording)
# --------------------------------------------------------------------------------------

BSA_SCHEDULE_PART_A: list[dict] = [
    {
        "no": "A1",
        "label": "I, ______________________ (Name)",
        "help": "Deponent: the person in charge of the computer/communication device or "
        "of the management of the relevant activities. " + _UNVERIFIED_NUMBERING,
    },
    {
        "no": "A2",
        "label": "Son/daughter/spouse of ______________________",
        "help": "Statutory relation blank. Strike out the inapplicable alternatives.",
    },
    {
        "no": "A3",
        "label": "residing/employed at ______________________",
        "help": "Address or place of employment of the deponent. The tool pre-fills the "
        "deponent's organisation, if supplied; the residential alternative is never guessed.",
    },
    {
        "no": "A4",
        "label": "do hereby solemnly affirm and sincerely state and submit as follows:—",
        "help": "Fixed affirmation opening; no blank. Affirmation is an act of the "
        "deponent, not of this tool.",
    },
    {
        "no": "A5",
        "label": "I have produced electronic record/output of the digital record taken "
        "from the following device/digital record source (tick mark):—",
        "help": "Fixed recital introducing the source tick-list.",
    },
    {
        "no": "A6",
        "label": "Computer / Storage Media □   DVR □   Mobile □   Flash Drive □   "
        "CD/DVD □   Server □   Cloud □   Other □",
        "help": "Eight statutory source classes. This tool acquires from an Android "
        "handset, so it proposes 'Mobile'; the deponent must confirm. " + _UNVERIFIED_GLYPH,
    },
    {
        "no": "A7",
        "label": "Other: ______________________",
        "help": "Free text, used only if the 'Other' box is ticked.",
    },
    {
        "no": "A8",
        "label": "Make & Model: ______________________",
        "help": "From the device's ro.product.manufacturer / ro.product.model build "
        "properties as reported by the device itself; these are self-reported by the OS "
        "and can be spoofed on a modified build.",
    },
    {
        "no": "A9",
        "label": "Color: ______________________",
        "help": "Physical observation. A logical acquisition cannot determine the colour "
        "of the handset — this is always left as " + NOT_CAPTURED + " for the examiner.",
    },
    {
        "no": "A10",
        "label": "Serial Number: ______________________",
        "help": "From ro.serialno / ro.boot.serialno. Absent on many non-rooted devices "
        "running Android 10+, where the serial is not readable without a privileged "
        "permission; rendered as " + NOT_CAPTURED + " rather than blank when unavailable.",
    },
    {
        "no": "A11",
        "label": "IMEI/UIN/UID/MAC/Cloud ID ______________________ (as applicable)",
        "help": "Statutory identifier list is exactly IMEI/UIN/UID/MAC/Cloud ID — 'UUID' "
        "is not a statutory term and is never written here. IMEI is not readable without "
        "READ_PRIVILEGED_PHONE_STATE on Android 10+; a missing IMEI is reported as "
        + NOT_CAPTURED
        + ", never as 'none'.",
    },
    {
        "no": "A12",
        "label": "and any other relevant information, if any, about the device/digital "
        "record ______ (specify)",
        "help": "The Schedule provides NO dedicated blank for identifying the record or "
        "describing the manner of its production even though s.63(4)(a) requires both, so "
        "the tool places its record description, format and production method here. "
        "Consider an annexure for anything longer than a line.",
    },
    {
        "no": "A13",
        "label": "The digital device or the digital record source was under the lawful "
        "control for regularly creating, storing or processing information for the purposes "
        "of carrying out regular activities and during this period, the computer or the "
        "communication device was working properly and the relevant information was "
        "regularly fed into the computer during the ordinary course of business. If the "
        "computer/digital device at any point of time was not working properly or out of "
        "operation, then it has not affected the electronic/digital record or its accuracy.",
        "help": "Fixed statutory recital with no blanks — it maps onto the s.63(2)(a)-(d) "
        "conditions. NEVER pre-affirmed by the tool: on a seized handset the person in "
        "lawful control during the period of regular use was the device's user, not the "
        "investigator, and asserting otherwise would be false.",
    },
    {
        "no": "A14",
        "label": "Owned □   Maintained □   Managed □   Operated □   by me (select as applicable)",
        "help": "Four control modes. The tool cannot know which applies and ticks none. "
        + _UNVERIFIED_GLYPH,
    },
    {
        "no": "A15",
        "label": "I state that the HASH value/s of the electronic/digital record/s is "
        "______________________, obtained through the following algorithm:—",
        "help": "Hash values are computed by the tool at the moment each artifact is "
        "ingested into the case folder and are reproduced in full in the enclosed hash "
        "report. The Part A and Part B hash values must be identical — a mismatch is fatal "
        "on the face of the certificate.",
    },
    {
        "no": "A16",
        "label": "□ SHA1:   □ SHA256:   □ MD5:   □ Other ______ (Legally acceptable standard)",
        "help": "Statute spells these SHA1 / SHA256 / MD5 (unhyphenated). The tool's "
        "primary algorithm is SHA-256, ticked as SHA256; MD5 is recorded alongside only as "
        "a legacy cross-check and is not relied on for integrity. " + _UNVERIFIED_GLYPH,
    },
    {
        "no": "A17",
        "label": "(Hash report to be enclosed with the certificate)",
        "help": "Statutory note. The case manifest (manifest.json) plus the hash "
        "verification section of the report serve as the hash report; they must be "
        "physically enclosed.",
    },
    {
        "no": "A18",
        "label": "(Name and signature)",
        "help": "Part A carries NO 'designation' in the statutory signature block — only "
        "name and signature. A manual signature by the deponent is required; the Schedule "
        "contains no digital-signature or DSC field anywhere.",
    },
    {
        "no": "A19",
        "label": "Date (DD/MM/YYYY): ______",
        "help": "Date of the affirmation, not of the acquisition. Pre-filled with the "
        "certificate generation date in IST for convenience; correct it to the date of "
        "actual signature.",
    },
    {
        "no": "A20",
        "label": "Time (IST): ________hours (In 24 hours format)",
        "help": "Statute requires Indian Standard Time in 24-hour format. All engine "
        "timestamps are stored in UTC and converted here at UTC+05:30 (India observes no "
        "daylight saving, so the offset is constant year-round).",
    },
    {
        "no": "A21",
        "label": "Place: ____________",
        "help": "Place of affirmation. Never inferred by the tool from device location "
        "data — supply it explicitly.",
    },
]


BSA_SCHEDULE_PART_B: list[dict] = [
    {
        "no": "B1",
        "label": "I, ______________________ (Name)",
        "help": "The expert. Per Pune Bar Association v. Union of India (Supreme Court, "
        "2026), Part B is not confined to an Examiner of Electronic Evidence notified "
        "under s.79A of the Information Technology Act, 2000: any person with special "
        "skill and expertise in computer science and cyber forensics may sign, subject to "
        "the court being satisfied of their credentials. "
        "UNVERIFIED-CITATION: reported as 2026 SCC OnLine SC 1297 / 2026 LiveLaw (SC) 551; "
        "the judgment text was not retrieved and the exact decision date (22 vs 28 May "
        "2026) is unconfirmed. Verify before relying on it. " + _UNVERIFIED_NUMBERING,
    },
    {
        "no": "B2",
        "label": "Son/daughter/spouse of ______________________",
        "help": "Statutory relation blank. Strike out the inapplicable alternatives.",
    },
    {
        "no": "B3",
        "label": "residing/employed at ______________________",
        "help": "Address or place of employment of the expert.",
    },
    {
        "no": "B4",
        "label": "do hereby solemnly affirm and sincerely state and submit as follows:—",
        "help": "Fixed affirmation opening, identical wording to Part A.",
    },
    {
        "no": "B5",
        "label": "The produced electronic record/output of the digital record are obtained "
        "from the following device/digital record source (tick mark):—",
        "help": "Fixed recital. Note the wording differs from Part A: the expert speaks to "
        "provenance ('are obtained from'), the party to production ('I have produced').",
    },
    {
        "no": "B6",
        "label": "Computer / Storage Media □   DVR □   Mobile □   Flash Drive □   "
        "CD/DVD □   Server □   Cloud □   Other □",
        "help": "Identical eight-class source list to Part A; must agree with Part A. "
        + _UNVERIFIED_GLYPH,
    },
    {
        "no": "B7",
        "label": "Other: ______________________",
        "help": "Free text, used only if the 'Other' box is ticked.",
    },
    {
        "no": "B8",
        "label": "Make & Model: ______________________",
        "help": "Must agree with Part A field A8.",
    },
    {
        "no": "B9",
        "label": "Color: ______________________",
        "help": "Physical observation; not obtainable by logical acquisition.",
    },
    {
        "no": "B10",
        "label": "Serial Number: ______________________",
        "help": "Must agree with Part A field A10.",
    },
    {
        "no": "B11",
        "label": "IMEI/UIN/UID/MAC/Cloud ID ______________________ (as applicable)",
        "help": "Must agree with Part A field A11.",
    },
    {
        "no": "B12",
        "label": "and any other relevant information, if any, about the device/digital "
        "record ______ (specify)",
        "help": "Same statutory gap as A12 — the only place in Part B to record the "
        "manner of production, the tooling used and its version.",
    },
    {
        "no": "B13",
        "label": "I state that the HASH value/s of the electronic/digital record/s is "
        "______________________, obtained through the following algorithm:—",
        "help": "The expert's substantive contribution under the statutory form is exactly "
        "this: the hash value plus the named algorithm plus the enclosed hash report. The "
        "Schedule asks the expert for no opinion, no methodology and no qualifications.",
    },
    {
        "no": "B14",
        "label": "□ SHA1:   □ SHA256:   □ MD5:   □ Other ______ (Legally acceptable standard)",
        "help": "Must name the same algorithm and produce the same values as Part A. "
        + _UNVERIFIED_GLYPH,
    },
    {
        "no": "B15",
        "label": "(Hash report to be enclosed with the certificate)",
        "help": "Statutory note; the same enclosure serves both Parts.",
    },
    {
        "no": "B16",
        "label": "(Name, designation and signature)",
        "help": "Part B REQUIRES a designation — unlike Part A, which asks only for name "
        "and signature. State the designation that evidences the claimed expertise.",
    },
    {
        "no": "B17",
        "label": "Date (DD/MM/YYYY): ______",
        "help": "Date of the expert's affirmation.",
    },
    {
        "no": "B18",
        "label": "Time (IST): ________hours (In 24 hours format)",
        "help": "IST, 24-hour format, as in Part A.",
    },
    {
        "no": "B19",
        "label": "Place: ____________",
        "help": "Place of the expert's affirmation.",
    },
]


# --------------------------------------------------------------------------------------
# Timestamps
# --------------------------------------------------------------------------------------


def ist_timestamp(iso_utc: Optional[str] = None) -> str:
    """Render a UTC ISO-8601 instant as 24-hour Indian Standard Time.

    Returns ``"YYYY-MM-DD HH:MM:SS IST"``. The Schedule demands IST in 24-hour format, but
    every timestamp the engine records is UTC-with-Z, so the conversion has to happen at
    the presentation boundary — never in storage.

    India observes no daylight saving time; the offset is a constant UTC+05:30. That is why
    a fixed-offset ``timezone`` is correct here and a DST-aware zone lookup would be noise.

    ``iso_utc=None`` means "now". Unparseable input degrades to an explicit marker rather
    than raising or silently substituting the current time — a wrong-but-plausible
    timestamp on a certificate is worse than a visible failure.
    """
    if iso_utc is None:
        dt = datetime.now(timezone.utc)
    else:
        dt = _parse_iso_utc(iso_utc)
        if dt is None:
            return f"UNPARSEABLE TIMESTAMP ({iso_utc!s})"
    return dt.astimezone(_IST).strftime("%Y-%m-%d %H:%M:%S IST")


def _parse_iso_utc(raw: str) -> Optional[datetime]:
    """Parse an ISO-8601 string into an aware UTC datetime, or None. Never raises."""
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    # datetime.fromisoformat accepts 'Z' on modern Pythons, but normalise anyway so the
    # behaviour does not depend on the interpreter version.
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        # A naive timestamp in this codebase is always UTC (see models.now_iso).
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _ist_date_time(iso_utc: Optional[str] = None) -> tuple[str, str]:
    """Return (DD/MM/YYYY, HH:MM) in IST — the two shapes the Schedule blanks want."""
    if iso_utc is None:
        dt: Optional[datetime] = datetime.now(timezone.utc)
    else:
        dt = _parse_iso_utc(iso_utc)
    if dt is None:
        return (NOT_CAPTURED, NOT_CAPTURED)
    local = dt.astimezone(_IST)
    return (local.strftime("%d/%m/%Y"), local.strftime("%H:%M"))


def _now_iso() -> str:
    """UTC ISO-8601 with trailing Z (mirrors triage.models.now_iso; kept local so this
    module stays importable without pulling the whole model layer)."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --------------------------------------------------------------------------------------
# Dataclasses
# --------------------------------------------------------------------------------------


@dataclass
class CertificateParty:
    """One of the two signatories the Schedule demands.

    ``signed`` defaults to False and is *only* ever set True by an explicit human action
    recorded upstream. The tool never signs anything on anyone's behalf.
    """

    name: str = ""
    designation: str = ""
    organisation: str = ""
    role: str = ""  # "custodian" (Part A) | "expert" (Part B)
    signed: bool = False
    signed_at: str = ""  # ISO-8601 UTC with trailing Z

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["signed_at_ist"] = ist_timestamp(self.signed_at) if self.signed_at else ""
        return d


@dataclass
class BsaCertificate:
    """A pre-filled, UNSIGNED s.63(4) Schedule template.

    Every string field defaults to empty and every collection to an empty one, so an
    incompletely-populated certificate is structurally obvious rather than plausible.
    """

    case_id: str = ""
    generated_at_ist: str = ""
    place: str = ""
    record_description: str = ""
    record_format: str = ""
    production_method: str = ""
    device: dict = field(default_factory=dict)
    hash_algorithm: str = "SHA-256"
    hash_values: list[dict] = field(default_factory=list)
    artifact_count: int = 0
    total_bytes: int = 0
    part_a: dict = field(default_factory=dict)
    part_b: dict = field(default_factory=dict)
    custodian: CertificateParty = field(
        default_factory=lambda: CertificateParty(role="custodian")
    )
    expert: CertificateParty = field(
        default_factory=lambda: CertificateParty(role="expert")
    )
    unsigned_warning: str = ""
    caveats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "statute": BSA_REFERENCE,
            "template_disclaimer": TEMPLATE_DISCLAIMER,
            "case_id": self.case_id,
            "generated_at_ist": self.generated_at_ist,
            "place": self.place,
            "record_description": self.record_description,
            "record_format": self.record_format,
            "production_method": self.production_method,
            "device": dict(self.device),
            "hash_algorithm": self.hash_algorithm,
            "hash_values": [dict(h) for h in self.hash_values],
            "artifact_count": int(self.artifact_count),
            "total_bytes": int(self.total_bytes),
            "part_a": json.loads(json.dumps(self.part_a)),
            "part_b": json.loads(json.dumps(self.part_b)),
            "custodian": self.custodian.to_dict(),
            "expert": self.expert.to_dict(),
            "unsigned_warning": self.unsigned_warning,
            "caveats": list(self.caveats),
        }


# --------------------------------------------------------------------------------------
# Building
# --------------------------------------------------------------------------------------


def _s(value: Any) -> str:
    """Coerce to a trimmed string; None/'' become ''. Never raises."""
    if value is None:
        return ""
    try:
        return str(value).strip()
    except Exception:  # pragma: no cover - defensive; str() on a hostile __str__
        return ""


def _or_not_captured(value: Any) -> str:
    """A value we could not obtain must SAY so. A blank on a legal form reads as an
    assertion that the field is inapplicable, which is a different claim entirely."""
    s = _s(value)
    return s if s else NOT_CAPTURED


def _statutory_algo(name: str) -> str:
    """Map our algorithm name onto the Schedule's spelling (SHA1/SHA256/MD5/Other)."""
    key = _s(name).upper()
    return _STATUTORY_ALGO.get(key, "Other")


def _party(raw: Optional[dict], role: str) -> CertificateParty:
    """Build a signatory from an untrusted dict. Unknown keys are ignored; ``signed`` is
    only honoured when an explicit truthy flag is present."""
    if not isinstance(raw, dict):
        return CertificateParty(role=role)
    signed = bool(raw.get("signed", False))
    return CertificateParty(
        name=_s(raw.get("name")),
        designation=_s(raw.get("designation")),
        organisation=_s(raw.get("organisation")),
        role=_s(raw.get("role")) or role,
        signed=signed,
        # A signature timestamp without a signature is meaningless — drop it.
        signed_at=_s(raw.get("signed_at")) if signed else "",
    )


def _extract_hashes(manifest: list[dict]) -> tuple[list[dict], int, int, list[str]]:
    """Pull (artifact_id, path, sha256, md5) out of the case manifest.

    Tolerates the legacy key spelling ``sha256_hash``/``md5_hash`` that older cases wrote.
    A record whose hash is missing is still listed — with NOT CAPTURED — because silently
    dropping it would understate the size of the production.
    """
    rows: list[dict] = []
    warnings: list[str] = []
    total_bytes = 0
    missing_hash = 0

    if not isinstance(manifest, list):
        return ([], 0, 0, ["Manifest was not a list; no hash values could be extracted."])

    for idx, rec in enumerate(manifest):
        if not isinstance(rec, dict):
            # Degrade gracefully: skip the malformed record, but never hide that we did.
            warnings.append(f"Manifest entry #{idx} was not an object and was skipped.")
            continue
        sha = _s(rec.get("sha256")) or _s(rec.get("sha256_hash"))
        md5 = _s(rec.get("md5")) or _s(rec.get("md5_hash"))
        path = _s(rec.get("stored_path")) or _s(rec.get("source_path"))
        try:
            total_bytes += int(rec.get("size_bytes") or 0)
        except (TypeError, ValueError):
            warnings.append(
                f"Manifest entry #{idx} had a non-numeric size_bytes; excluded from the total."
            )
        if not sha:
            missing_hash += 1
        rows.append(
            {
                "artifact_id": _or_not_captured(rec.get("artifact_id")),
                "path": _or_not_captured(path),
                # The algorithm travels with every value — a bare hex string on a
                # certificate is unattributable and the Schedule expressly pairs them.
                "algorithm": "SHA-256",
                "sha256": _or_not_captured(sha),
                "md5_algorithm": "MD5",
                "md5": _or_not_captured(md5),
            }
        )

    if missing_hash:
        warnings.append(
            f"{missing_hash} of {len(rows)} manifest entries carry no SHA-256 value; they "
            "are listed as NOT CAPTURED and CANNOT be certified under the Schedule's hash "
            "requirement until re-hashed."
        )
    return (rows, len(rows), total_bytes, warnings)


def _field_map(descriptors: list[dict]) -> dict[str, dict]:
    return {d["no"]: d for d in descriptors}


def _build_part(
    descriptors: list[dict],
    title: str,
    subtitle: str,
    values: dict[str, str],
    ticks: dict[str, list[str]],
) -> dict:
    """Assemble one Part as a JSON-safe dict of ordered, value-carrying fields."""
    fields: list[dict] = []
    for d in descriptors:
        entry = {
            "no": d["no"],
            "label": d["label"],
            "help": d["help"],
            "value": values.get(d["no"], ""),
        }
        if d["no"] in ticks:
            entry["ticked"] = list(ticks[d["no"]])
        fields.append(entry)
    return {"title": title, "subtitle": subtitle, "fields": fields}


def build_certificate(
    case_meta: dict,
    device: dict,
    manifest: list[dict],
    *,
    place: str = "",
    custodian: Optional[dict] = None,
    expert: Optional[dict] = None,
) -> BsaCertificate:
    """Pre-fill the BSA 2023 s.63 Schedule from case metadata, device metadata and the
    case manifest.

    ``manifest`` is the ``manifest.json`` list produced by ``triage.custody``; each record
    is expected to carry ``artifact_id``, ``stored_path``, ``source_path``, ``sha256``,
    ``md5`` and ``size_bytes``. The legacy ``sha256_hash``/``md5_hash`` spellings are
    tolerated so older case folders still certify.

    The result is deliberately UNSIGNED and self-describing as a template.
    """
    case_meta = case_meta if isinstance(case_meta, dict) else {}
    device = device if isinstance(device, dict) else {}

    generated_at_utc = _s(case_meta.get("generated_at")) or _now_iso()
    gen_ist = ist_timestamp(generated_at_utc)
    date_str, time_str = _ist_date_time(generated_at_utc)

    hash_values, artifact_count, total_bytes, hash_warnings = _extract_hashes(manifest)

    cust = _party(custodian, "custodian")
    exp = _party(expert, "expert")
    # Fall back to the recorded examiner for the Part A deponent only if nothing was
    # supplied — and say so in the caveats, because the examiner is often NOT the person
    # in lawful control of the device.
    examiner = _s(case_meta.get("examiner"))
    examiner_used_as_custodian = False
    if not cust.name and examiner:
        cust.name = examiner
        examiner_used_as_custodian = True

    manufacturer = _s(device.get("manufacturer"))
    model = _s(device.get("model"))
    make_model = " ".join(p for p in (manufacturer, model) if p) or NOT_CAPTURED
    serial = _or_not_captured(device.get("serial") or device.get("boot_serial"))
    imei = _s(device.get("imei"))
    mac = _s(device.get("mac"))
    ident_bits = []
    if imei:
        ident_bits.append(f"IMEI: {imei}")
    if mac:
        ident_bits.append(f"MAC: {mac}")
    identifiers = "; ".join(ident_bits) if ident_bits else NOT_CAPTURED

    android_version = _or_not_captured(device.get("android_version"))

    record_description = (
        _s(case_meta.get("record_description"))
        or f"Logical triage acquisition of an Android device in case "
        f"{_or_not_captured(case_meta.get('case_id'))}: {artifact_count} artifact(s) "
        f"totalling {total_bytes:,} bytes, each hashed at the moment of ingestion and "
        f"listed in the enclosed manifest."
    )
    record_format = (
        _s(case_meta.get("record_format"))
        or "Case folder: artifact files copied byte-for-byte from the device, plus "
        "manifest.json (JSON hash manifest), audit.jsonl (append-only audit trail) and "
        "report.html (human-readable report)."
    )
    production_method = (
        _s(case_meta.get("production_method"))
        or "Minimally-invasive logical acquisition over ADB by the SNAGR triage engine. "
        "No write-blocking exists for mobile devices (SWGDE 18-F-003); every device "
        "interaction is timestamped in the audit trail. Legal authority: "
        + _or_not_captured(case_meta.get("legal_authority"))
    )

    if artifact_count == 1:
        hash_summary = (
            f"{hash_values[0]['sha256']} ({_statutory_algo('SHA-256')}) — see enclosed hash report"
        )
    elif artifact_count > 1:
        hash_summary = (
            f"{artifact_count} hash value(s), one per artifact, each computed with "
            f"{_statutory_algo('SHA-256')} — set out in full in the enclosed hash report "
            "(manifest.json)"
        )
    else:
        hash_summary = NOT_CAPTURED

    other_info = (
        f"Record: {record_description} | Format: {record_format} | "
        f"Manner of production: {production_method} | Android version: {android_version}"
    )

    part_a_values = {
        "A1": _or_not_captured(cust.name),
        "A2": NOT_CAPTURED,
        "A3": _or_not_captured(cust.organisation),
        "A4": "(fixed statutory affirmation — to be affirmed by the deponent in person)",
        "A5": "(fixed statutory recital)",
        "A6": "Mobile (proposed by the tool — confirm before signing)",
        "A7": "",
        "A8": make_model,
        "A9": NOT_CAPTURED,
        "A10": serial,
        "A11": identifiers,
        "A12": other_info,
        "A13": REQUIRES_REVIEW
        + " — this fixed recital asserts lawful control over the device during its period "
        "of regular use; on a seized device that is usually the user, not the examiner.",
        "A14": REQUIRES_REVIEW,
        "A15": hash_summary,
        "A16": _statutory_algo("SHA-256"),
        "A17": "Enclose manifest.json (hash manifest) with this certificate.",
        "A18": _or_not_captured(cust.name) + " — SIGNATURE PENDING",
        "A19": date_str,
        "A20": time_str,
        "A21": _or_not_captured(place),
    }
    part_b_values = {
        "B1": _or_not_captured(exp.name),
        "B2": NOT_CAPTURED,
        "B3": _or_not_captured(exp.organisation),
        "B4": "(fixed statutory affirmation — to be affirmed by the expert in person)",
        "B5": "(fixed statutory recital)",
        "B6": "Mobile (proposed by the tool — confirm before signing)",
        "B7": "",
        "B8": make_model,
        "B9": NOT_CAPTURED,
        "B10": serial,
        "B11": identifiers,
        "B12": other_info,
        "B13": hash_summary,
        "B14": _statutory_algo("SHA-256"),
        "B15": "Enclose manifest.json (hash manifest) with this certificate.",
        "B16": (_or_not_captured(exp.name) + ", " + _or_not_captured(exp.designation))
        + " — SIGNATURE PENDING",
        "B17": date_str,
        "B18": time_str,
        "B19": _or_not_captured(place),
    }

    ticks_a = {"A6": ["Mobile"], "A16": [_statutory_algo("SHA-256")], "A14": []}
    ticks_b = {"B6": ["Mobile"], "B14": [_statutory_algo("SHA-256")]}

    part_a = _build_part(
        BSA_SCHEDULE_PART_A, "PART A", "(To be filled by the Party)", part_a_values, ticks_a
    )
    part_b = _build_part(
        BSA_SCHEDULE_PART_B, "PART B", "(To be filled by the Expert)", part_b_values, ticks_b
    )

    caveats: list[str] = [
        TEMPLATE_DISCLAIMER,
        "The statutory Schedule is UNNUMBERED. The A1..A21 / B1..B19 handles used here are "
        "internal to this tool and must not be reproduced on a document tendered to a court.",
        "s.63(4)(a) requires the certificate to identify the electronic record and describe "
        "the manner in which it was produced, but the Schedule provides no blank for it; the "
        "description has been placed in the 'any other relevant information ... (specify)' "
        "field and may warrant a separate annexure.",
        "s.63(4) requires the certificate to be submitted along with the electronic record "
        "AT EACH INSTANCE where it is tendered for admission — regenerate and re-sign it for "
        "every tender, do not reuse a single executed copy.",
        "Part A and Part B must state identical hash values. Verify them against each other "
        "before filing; a discrepancy is fatal on the face of the certificate.",
        "The Schedule contains no digital-signature/DSC field. Both Parts require a manual "
        "signature; this tool cannot and does not apply one.",
        "Device identifiers (Make & Model, Serial Number, IMEI) are self-reported by the "
        "device's operating system and can be altered on a modified build; they are not "
        "independently verified by this tool.",
    ]
    if serial == NOT_CAPTURED:
        caveats.append(
            "Serial Number was NOT CAPTURED — commonly unreadable without privileged "
            "permissions on Android 10+. This is 'not obtainable', not 'not present'."
        )
    if identifiers == NOT_CAPTURED:
        caveats.append(
            "IMEI/UIN/UID/MAC/Cloud ID was NOT CAPTURED — reading the IMEI requires "
            "READ_PRIVILEGED_PHONE_STATE on Android 10+. Obtain it from the seizure memo or "
            "the handset label and enter it manually."
        )
    if examiner_used_as_custodian:
        caveats.append(
            "No custodian was supplied, so the recorded examiner has been pre-filled as the "
            "Part A deponent. The Part A deponent must be the person in charge of the device "
            "or of the management of the relevant activities — confirm this is correct."
        )
    if artifact_count == 0:
        caveats.append(
            "The manifest contained NO artifacts. There is no electronic record to certify "
            "and no hash value to state; this template cannot be completed as it stands."
        )
    caveats.extend(hash_warnings)

    unsigned_warning = (
        "UNSIGNED — both statutory signature blocks are empty. A certificate under s.63(4) "
        "requires BOTH the person in charge of the computer/communication device (or of the "
        "management of the relevant activities) to sign PART A AND an expert to sign PART B. "
        "Until both are signed this document has no evidentiary effect whatsoever."
    )
    if cust.signed and exp.signed:
        unsigned_warning = ""
    elif cust.signed or exp.signed:
        pending = "PART B (expert)" if cust.signed else "PART A (party)"
        unsigned_warning = (
            f"PARTIALLY SIGNED — {pending} is still unsigned. s.63(4) requires BOTH "
            "signatures; a single signature does not make the certificate effective."
        )

    return BsaCertificate(
        case_id=_or_not_captured(case_meta.get("case_id")),
        generated_at_ist=gen_ist,
        place=_or_not_captured(place),
        record_description=record_description,
        record_format=record_format,
        production_method=production_method,
        device={
            "manufacturer": _or_not_captured(manufacturer),
            "model": _or_not_captured(model),
            "make_and_model": make_model,
            "imei": _or_not_captured(imei),
            "serial": serial,
            "mac": _or_not_captured(mac),
            "android_version": android_version,
        },
        hash_algorithm="SHA-256",
        hash_values=hash_values,
        artifact_count=artifact_count,
        total_bytes=total_bytes,
        part_a=part_a,
        part_b=part_b,
        custodian=cust,
        expert=exp,
        unsigned_warning=unsigned_warning,
        caveats=caveats,
    )


# --------------------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------------------

def validate_certificate(cert: BsaCertificate) -> dict:
    """Report exactly what is still missing. An unsigned template is NEVER 'complete'.

    Returns ``{"complete": bool, "missing": [...], "warnings": [...]}``. The point of this
    function is to make it impossible for a caller to present a half-filled template as a
    valid s.63(4) certificate by accident.
    """
    missing: list[str] = []
    warnings: list[str] = []

    if not isinstance(cert, BsaCertificate):
        return {
            "complete": False,
            "missing": ["Not a BsaCertificate instance"],
            "warnings": ["validate_certificate received an unexpected object type."],
        }

    def _blank(v: Any) -> bool:
        s = _s(v)
        return (not s) or s == NOT_CAPTURED or s.startswith(REQUIRES_REVIEW)

    if _blank(cert.case_id):
        missing.append("Case identifier")
    if _blank(cert.place):
        missing.append("Place (Schedule fields A21/B19)")
    if _blank(cert.record_description):
        missing.append("Record description (s.63(4)(a))")
    if _blank(cert.production_method):
        missing.append("Manner of production (s.63(4)(a))")
    if not cert.hash_values:
        missing.append("Hash value/s (Schedule fields A15/B13)")

    dev = cert.device if isinstance(cert.device, dict) else {}
    if _blank(dev.get("make_and_model")):
        missing.append("Make & Model (A8/B8)")
    if _blank(dev.get("serial")):
        missing.append("Serial Number (A10/B10)")
    if _blank(dev.get("imei")) and _blank(dev.get("mac")):
        missing.append("IMEI/UIN/UID/MAC/Cloud ID (A11/B11)")

    if _blank(cert.custodian.name):
        missing.append("PART A deponent name (A1)")
    if not cert.custodian.signed:
        missing.append("PART A signature (A18)")
    if _blank(cert.expert.name):
        missing.append("PART B expert name (B1)")
    if _blank(cert.expert.designation):
        missing.append("PART B expert designation (B16)")
    if not cert.expert.signed:
        missing.append("PART B signature (B16)")

    if not cert.custodian.signed or not cert.expert.signed:
        warnings.append(
            "s.63(4) requires BOTH a party signature (PART A) and an expert signature "
            "(PART B). This document is not a certificate until both are present."
        )
    if cert.artifact_count == 0:
        warnings.append("No artifacts are listed — there is no electronic record to certify.")
    for h in cert.hash_values:
        if _s(h.get("sha256")) == NOT_CAPTURED:
            warnings.append(
                f"Artifact {h.get('artifact_id')} has no SHA-256 value and cannot be "
                "certified under the Schedule's hash requirement."
            )
    if cert.custodian.signed and not cert.custodian.signed_at:
        warnings.append("PART A is marked signed but carries no signature timestamp.")
    if cert.expert.signed and not cert.expert.signed_at:
        warnings.append("PART B is marked signed but carries no signature timestamp.")

    warnings.append(
        "Even when structurally complete, this remains a tool-generated template whose "
        "recitals must be independently verified by the signatories before tender."
    )

    return {"complete": not missing, "missing": missing, "warnings": warnings}


# --------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------


def _e(value: Any) -> str:
    """HTML-escape anything. Every interpolated value goes through this — manifest paths
    and examiner names are attacker-influenced input on a seized device."""
    return html.escape(_s(value), quote=True)


_CSS = (
    "<style>"
    ".bsa-cert{font-family:Georgia,'Times New Roman',serif;line-height:1.45;max-width:60em}"
    ".bsa-cert h2,.bsa-cert h3{margin:1.2em 0 .4em}"
    ".bsa-cert .bsa-warn{border:2px solid #a5322f;background:#f6dedd;color:#5c1b19;"
    "padding:.8em 1em;margin:1em 0;font-weight:bold}"
    ".bsa-cert .bsa-note{border-left:4px solid #888;background:#f4f4f4;padding:.6em .9em;"
    "margin:.8em 0;font-size:.92em}"
    ".bsa-cert table{border-collapse:collapse;width:100%;margin:.6em 0;font-size:.9em}"
    ".bsa-cert th,.bsa-cert td{border:1px solid #bbb;padding:.4em .55em;vertical-align:top;"
    "text-align:left}"
    ".bsa-cert th{background:#ececec}"
    ".bsa-cert .bsa-no{white-space:nowrap;width:3.2em;font-family:monospace}"
    ".bsa-cert .bsa-help{color:#555;font-size:.86em;display:block;margin-top:.25em}"
    ".bsa-cert .bsa-unverified{color:#8a5b00;font-weight:bold}"
    ".bsa-cert .bsa-sig{border:1px solid #333;padding:.9em 1em;margin:1em 0}"
    ".bsa-cert .bsa-pending{color:#a5322f;font-weight:bold}"
    ".bsa-cert code{font-family:monospace;font-size:.88em;word-break:break-all}"
    "</style>"
)


def _render_part_html(part: dict) -> str:
    rows = [
        f"<h3>{_e(part.get('title'))} <small>{_e(part.get('subtitle'))}</small></h3>",
        "<table><thead><tr><th class='bsa-no'>Ref</th><th>Schedule field (verbatim)</th>"
        "<th>Pre-filled value</th></tr></thead><tbody>",
    ]
    for f in part.get("fields", []) or []:
        helptext = _s(f.get("help"))
        cls = "bsa-help bsa-unverified" if "UNVERIFIED-" in helptext else "bsa-help"
        ticked = f.get("ticked")
        tick_html = (
            f"<br><em>Ticked: {_e(', '.join(ticked))}</em>"
            if ticked
            else ("<br><em>Ticked: none</em>" if ticked == [] else "")
        )
        rows.append(
            "<tr>"
            f"<td class='bsa-no'>{_e(f.get('no'))}</td>"
            f"<td>{_e(f.get('label'))}<span class='{cls}'>{_e(helptext)}</span></td>"
            f"<td>{_e(f.get('value'))}{tick_html}</td>"
            "</tr>"
        )
    rows.append("</tbody></table>")
    return "".join(rows)


def _signature_block_html(party: CertificateParty, part_label: str, caption: str) -> str:
    status = (
        f"SIGNED at {_e(ist_timestamp(party.signed_at))}"
        if party.signed
        else "<span class='bsa-pending'>NOT SIGNED — signature required</span>"
    )
    return (
        "<div class='bsa-sig'>"
        f"<b>{_e(part_label)} — {_e(caption)}</b><br>"
        f"Name: {_e(party.name) or _e(NOT_CAPTURED)}<br>"
        f"Designation: {_e(party.designation) or _e(NOT_CAPTURED)}<br>"
        f"Organisation: {_e(party.organisation) or _e(NOT_CAPTURED)}<br>"
        f"Status: {status}<br><br>"
        "Signature: ______________________________<br>"
        "Date (DD/MM/YYYY): ____________&nbsp;&nbsp;Time (IST): ________hours "
        "(In 24 hours format)&nbsp;&nbsp;Place: ______________"
        "</div>"
    )


def render_certificate_html(cert: BsaCertificate) -> str:
    """Render the pre-filled Schedule as a self-contained HTML fragment.

    No external assets, no scripts. Every interpolated value is HTML-escaped — manifest
    paths and free-text metadata come from a seized device and are hostile input.
    """
    v = validate_certificate(cert)
    parts: list[str] = ["<div class='bsa-cert'>", _CSS]
    parts.append("<h2>Certificate under section 63(4), Bharatiya Sakshya Adhiniyam, 2023</h2>")
    parts.append(f"<div class='bsa-warn'>{_e(TEMPLATE_DISCLAIMER)}</div>")
    if cert.unsigned_warning:
        parts.append(f"<div class='bsa-warn'>{_e(cert.unsigned_warning)}</div>")
    parts.append(f"<p class='bsa-note'>{_e(BSA_REFERENCE)}</p>")

    parts.append("<h3>Case and production summary</h3><table><tbody>")
    for k, val in (
        ("Case", cert.case_id),
        ("Generated at", cert.generated_at_ist),
        ("Place", cert.place),
        ("Record described", cert.record_description),
        ("Record format", cert.record_format),
        ("Manner of production", cert.production_method),
        ("Artifacts", f"{cert.artifact_count}"),
        ("Total bytes", f"{cert.total_bytes:,}"),
        ("Hash algorithm", f"{cert.hash_algorithm} (Schedule spelling: {_statutory_algo(cert.hash_algorithm)})"),
    ):
        parts.append(f"<tr><th>{_e(k)}</th><td>{_e(val)}</td></tr>")
    parts.append("</tbody></table>")

    parts.append(_render_part_html(cert.part_a))
    parts.append(_signature_block_html(cert.custodian, "PART A", "(Name and signature)"))
    parts.append(_render_part_html(cert.part_b))
    parts.append(
        _signature_block_html(cert.expert, "PART B", "(Name, designation and signature)")
    )

    parts.append("<h3>Hash report (to be enclosed with the certificate)</h3>")
    if not cert.hash_values:
        parts.append(
            "<p class='bsa-note'>No hash values — the manifest listed no artifacts. "
            "Nothing can be certified.</p>"
        )
    else:
        parts.append(
            "<table><thead><tr><th>Artifact</th><th>Path</th><th>Algorithm</th>"
            "<th>Hash value</th></tr></thead><tbody>"
        )
        for h in cert.hash_values:
            # The algorithm name is emitted on the SAME row as the value: a bare hex
            # string is unattributable and the Schedule pairs value with algorithm.
            parts.append(
                "<tr>"
                f"<td>{_e(h.get('artifact_id'))}</td>"
                f"<td><code>{_e(h.get('path'))}</code></td>"
                f"<td>{_e(h.get('algorithm'))}</td>"
                f"<td><code>{_e(h.get('sha256'))}</code></td>"
                "</tr>"
                "<tr>"
                f"<td></td><td></td>"
                f"<td>{_e(h.get('md5_algorithm'))}</td>"
                f"<td><code>{_e(h.get('md5'))}</code></td>"
                "</tr>"
            )
        parts.append("</tbody></table>")

    parts.append("<h3>Completeness check</h3>")
    parts.append(
        f"<p class='bsa-note'>Status: <b>"
        f"{'STRUCTURALLY COMPLETE (still requires human verification)' if v['complete'] else 'INCOMPLETE'}"
        "</b></p>"
    )
    if v["missing"]:
        parts.append("<p>Missing before this can be tendered:</p><ul>")
        for m in v["missing"]:
            parts.append(f"<li>{_e(m)}</li>")
        parts.append("</ul>")

    parts.append("<h3>Caveats</h3><ul>")
    for c in cert.caveats:
        parts.append(f"<li>{_e(c)}</li>")
    for w in v["warnings"]:
        parts.append(f"<li>{_e(w)}</li>")
    parts.append("</ul>")
    parts.append("</div>")
    return "".join(parts)


def _wrap(text: str, width: int = 96, indent: str = "      ") -> list[str]:
    """Cheap greedy wrap so the sealed text export stays readable in a fixed-width viewer."""
    words = _s(text).split()
    lines: list[str] = []
    cur = ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(indent + cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(indent + cur)
    return lines


def render_certificate_text(cert: BsaCertificate) -> str:
    """Plain-text rendering for the sealed export (and for hashing into the audit trail)."""
    v = validate_certificate(cert)
    out: list[str] = []
    bar = "=" * 96
    out.append(bar)
    out.append("CERTIFICATE UNDER SECTION 63(4), BHARATIYA SAKSHYA ADHINIYAM, 2023")
    out.append("THE SCHEDULE [See section 63(4)(c)]")
    out.append(bar)
    out.append("")
    out.extend(_wrap(TEMPLATE_DISCLAIMER, indent="  "))
    out.append("")
    if cert.unsigned_warning:
        out.extend(_wrap(cert.unsigned_warning, indent="  "))
        out.append("")
    out.extend(_wrap(BSA_REFERENCE, indent="  "))
    out.append("")
    out.append("-" * 96)
    out.append("CASE AND PRODUCTION SUMMARY")
    out.append("-" * 96)
    for k, val in (
        ("Case", cert.case_id),
        ("Generated at", cert.generated_at_ist),
        ("Place", cert.place),
        ("Artifacts", str(cert.artifact_count)),
        ("Total bytes", f"{cert.total_bytes:,}"),
        (
            "Hash algorithm",
            f"{cert.hash_algorithm} (Schedule spelling: {_statutory_algo(cert.hash_algorithm)})",
        ),
        ("Make & Model", _s(cert.device.get("make_and_model"))),
        ("Serial Number", _s(cert.device.get("serial"))),
        ("IMEI", _s(cert.device.get("imei"))),
        ("MAC", _s(cert.device.get("mac"))),
        ("Android version", _s(cert.device.get("android_version"))),
    ):
        out.append(f"  {k:<18}: {val}")
    out.append("")
    out.append("  Record described     :")
    out.extend(_wrap(cert.record_description))
    out.append("  Record format        :")
    out.extend(_wrap(cert.record_format))
    out.append("  Manner of production :")
    out.extend(_wrap(cert.production_method))
    out.append("")

    for part in (cert.part_a, cert.part_b):
        out.append("-" * 96)
        out.append(f"{_s(part.get('title'))}  {_s(part.get('subtitle'))}")
        out.append("-" * 96)
        for f in part.get("fields", []) or []:
            out.append(f"  [{_s(f.get('no'))}] {_s(f.get('label'))}")
            out.append(f"        VALUE: {_s(f.get('value')) or '(blank)'}")
            if f.get("ticked"):
                out.append(f"        TICKED: {', '.join(f['ticked'])}")
            out.append(f"        NOTE : {_s(f.get('help'))}")
            out.append("")

    for party, part_label, caption in (
        (cert.custodian, "PART A", "(Name and signature)"),
        (cert.expert, "PART B", "(Name, designation and signature)"),
    ):
        out.append("-" * 96)
        out.append(f"SIGNATURE BLOCK — {part_label} {caption}")
        out.append("-" * 96)
        out.append(f"  Name         : {_s(party.name) or NOT_CAPTURED}")
        out.append(f"  Designation  : {_s(party.designation) or NOT_CAPTURED}")
        out.append(f"  Organisation : {_s(party.organisation) or NOT_CAPTURED}")
        out.append(
            "  Status       : "
            + (
                f"SIGNED at {ist_timestamp(party.signed_at)}"
                if party.signed
                else "NOT SIGNED — signature required"
            )
        )
        out.append("  Signature    : ______________________________")
        out.append(
            "  Date (DD/MM/YYYY): ________   Time (IST): ________hours "
            "(In 24 hours format)   Place: ____________"
        )
        out.append("")

    out.append("-" * 96)
    out.append("HASH REPORT (to be enclosed with the certificate)")
    out.append("-" * 96)
    if not cert.hash_values:
        out.append("  No hash values — the manifest listed no artifacts. Nothing can be certified.")
    else:
        for h in cert.hash_values:
            out.append(f"  {_s(h.get('artifact_id'))}  {_s(h.get('path'))}")
            out.append(f"        {_s(h.get('algorithm'))}: {_s(h.get('sha256'))}")
            out.append(f"        {_s(h.get('md5_algorithm'))}: {_s(h.get('md5'))}")
    out.append("")
    out.append("-" * 96)
    out.append("COMPLETENESS CHECK")
    out.append("-" * 96)
    out.append(
        "  Status: "
        + (
            "STRUCTURALLY COMPLETE (still requires human verification)"
            if v["complete"]
            else "INCOMPLETE"
        )
    )
    for m in v["missing"]:
        out.append(f"    MISSING: {m}")
    out.append("")
    out.append("-" * 96)
    out.append("CAVEATS")
    out.append("-" * 96)
    for c in list(cert.caveats) + list(v["warnings"]):
        out.append("  - " + c.replace("\n", " "))
    out.append(bar)
    return "\n".join(out)
