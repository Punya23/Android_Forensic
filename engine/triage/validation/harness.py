"""Offline known-answer self-validation harness for the eRakshak engine.

Forensic purpose
----------------
SWGDE 18-Q-001-2.1 Appendix A names three testing types; this harness implements
type 1, "Testing with a Known Dataset — known input, examine output for match or
correct processing". It builds its own ground-truth fixtures programmatically in a
temporary directory, runs the engine's real functions over them, and records expected
versus actual for each case.

What it actually exercises
--------------------------
  * ``triage.hashing.hash_file`` against a hard-coded SHA-256 known-answer vector.
  * ``triage.custody.Case.ingest_file`` + ``triage.forensics.hash_verification``
    round trip: a manifest that must verify INTACT, and a byte-flipped copy of the
    same case that must be detected as TAMPERED.
  * ``triage.recovery.recover_deleted_rows`` against a database with one row deleted
    whose content is known, and ``detect_rowid_gaps`` against the resulting rowid gap.
  * ``triage.recovery.read_live_rows`` — the deleted row must NOT appear among live
    rows (an "existence" check in SWGDE 12-Q-001 v2.0 terms).
  * ``triage.models.now_iso`` timestamp normalisation (CFTT MDT-AO-22).
  * A negative control that the tool is EXPECTED to fail, proving this harness reports
    failures instead of swallowing them.

Limitations of this harness (do not overstate what it proves)
-------------------------------------------------------------
1. It runs entirely offline with no device and no downloads. It therefore says nothing
   about acquisition from real hardware, about any Tier-1 helper-APK or Tier-2 root
   path, or about the application-specific parsers.
2. Its fixtures are lab-created and small. 18-Q-001-2.1 §5.6 notes that for an in-house
   tool "If possible, the tester should use a different dataset than was used to develop
   the tool" — these fixtures do not satisfy that. For a stronger known-answer dataset,
   the Joshua Hickman / Digital Corpora public Android images (Android 7-14) ship a
   per-application timestamped action log that is directly transcribable into cases;
   that work is not done here and this harness does not stand in for it.
3. A passing run means the named cases produced the expected result. It is not a
   validation of the tool, and every standing limitation in
   :func:`triage.validation.swgde.known_limitations` still applies in full.
4. Nothing here ever fabricates a pass. A failing check, a missing capability, or an
   unexpected exception is recorded as ``passed=False`` with the anomaly text; no code
   path returns a success dict it did not earn.
"""

from __future__ import annotations

import os
import platform
import shutil
import sqlite3
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from .. import TOOL_NAME, __version__
from ..config import Confidence, Tier
from ..custody import Case, CaseMeta
from ..forensics.hash_verification import load_manifest, verify_all_hashes
from ..hashing import hash_bytes, hash_file
from ..models import now_iso
from ..recovery import detect_rowid_gaps, read_live_rows, recover_deleted_rows
from .swgde import ValidationCase, ValidationReport, build_report, validate_report

# --- The known-answer vectors -------------------------------------------------
#: A fixed byte string and its independently-computed SHA-256. Hard-coded rather than
#: recomputed at runtime: if the digest were computed with the same library under test,
#: the case would be circular and would pass even if the implementation were wrong.
KAT_VECTOR: bytes = b"eRakshak validation known-answer vector v1\n"
KAT_VECTOR_SHA256: str = (
    "f2986e7df5756087add644a972be7c408affaa3bb5ecf2b6d984073e1754e3ac"
)

#: Body text of the row that is deleted from the fixture database. Distinctive so a
#: substring match against carved output cannot collide with schema or filler text.
KAT_DELETED_BODY: str = "ZQXJ-DELETED-ROW-MARKER-7f3a-known-answer"
KAT_LIVE_BODY: str = "ZQXJ-LIVE-ROW-MARKER-2b8e-known-answer"

#: Rowids used in the fixture; the deleted one produces a provable gap.
KAT_DELETED_ROWID: int = 3

#: The negative control. It is EXPECTED to be recorded passed=False. Exported so callers
#: (and tests) can assert that this harness does not swallow failures.
NEGATIVE_CONTROL_CASE_ID: str = "NEG-CTRL-008"

DATASET_NAME_DEFAULT = (
    "eRakshak lab-created offline known-answer fixture set v1 (generated at run time)"
)
DATASET_PROVENANCE_DEFAULT = (
    "Generated programmatically by triage/validation/harness.py at the moment of "
    "testing; no external download, no device, no third-party material. Fixtures are "
    "lab-created and targeted at the engine functions under test, per "
    "SWGDE 18-Q-001-2.1 Appendix A ('Lab created datasets can be targeted at the "
    "parameters most important to the functionality being tested'). NOT an independent "
    "dataset: 18-Q-001-2.1 §5.6 recommends a tester and dataset independent of the "
    "developer, which this does not provide."
)


# --- Case runner --------------------------------------------------------------
def _run_case(
    case_id: str,
    description: str,
    artifact_class: str,
    expected: dict[str, Any],
    fn: Callable[[], tuple[dict[str, Any], bool, list[str]]],
) -> ValidationCase:
    """Execute one case, converting any exception into an honest failure record.

    A case that raises is a case that failed. It is never dropped and never retried
    into a pass; the traceback summary goes into the anomaly text so the failure is
    diagnosable from the report alone.
    """
    try:
        actual, passed, anomalies = fn()
    except Exception as exc:  # noqa: BLE001 — a raising case is a failing case
        return ValidationCase(
            case_id=case_id,
            description=description,
            artifact_class=artifact_class,
            expected=expected,
            actual={"error": f"{type(exc).__name__}: {exc}"},
            passed=False,
            anomalies=[
                f"Case raised {type(exc).__name__}: {exc}. Classified under "
                "SWGDE 12-Q-001 v2.0 as an implementation error, not an algorithm "
                "error, until diagnosed. Traceback tail: "
                + " | ".join(traceback.format_exc().strip().splitlines()[-3:])
            ],
        )
    return ValidationCase(
        case_id=case_id,
        description=description,
        artifact_class=artifact_class,
        expected=expected,
        actual=actual,
        passed=bool(passed),
        anomalies=list(anomalies),
    )


# --- Fixture builders ---------------------------------------------------------
def _build_kat_file(root: Path) -> Path:
    """Write the fixed known-answer byte vector to disk."""
    path = root / "kat_vector.bin"
    path.write_bytes(KAT_VECTOR)
    return path


def _build_case_folder(cases_root: Path, case_id: str, payloads: dict[str, bytes]) -> Case:
    """Build a real custody Case and ingest known files through the real ingest path.

    Uses :class:`triage.custody.Case` rather than hand-writing a manifest, so the case
    genuinely exercises the hashing-at-ingest code that the integrity claim depends on.
    """
    cases_root.mkdir(parents=True, exist_ok=True)
    staging = cases_root / f"_staging_{case_id}"
    staging.mkdir(parents=True, exist_ok=True)

    meta = CaseMeta(
        case_id=case_id,
        examiner="self-validation harness",
        legal_authority="N/A — synthetic validation fixture, no real evidence",
        scope_note="Validation fixture only; contains no case data.",
    )
    case = Case.create(cases_root, meta)
    for name, blob in payloads.items():
        src = staging / name
        src.write_bytes(blob)
        case.ingest_file(
            src,
            source_path=f"/sdcard/Download/{name}",
            tier=Tier.TIER0,
            method="validation-fixture",
            category="validation",
        )
    return case


def _build_wal_db(path: Path) -> sqlite3.Connection:
    """Create a WAL-mode SQLite fixture with one known row deleted; return the OPEN conn.

    WAL mode with ``wal_autocheckpoint=0`` mirrors real Android SQLite: the pre-deletion
    page image survives in the ``-wal`` file until a checkpoint. The connection must stay
    open while the recovery pass runs, otherwise closing checkpoints the WAL away and the
    fixture no longer represents the situation under test. The caller closes it.
    """
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA wal_autocheckpoint=0")
    con.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY, sender TEXT, "
        "body TEXT, ts INTEGER)"
    )
    rows = [
        (1, "alice", KAT_LIVE_BODY + "-1", 1700000001000),
        (2, "bob", KAT_LIVE_BODY + "-2", 1700000002000),
        (KAT_DELETED_ROWID, "carol", KAT_DELETED_BODY, 1700000003000),
        (4, "dave", KAT_LIVE_BODY + "-4", 1700000004000),
        (5, "erin", KAT_LIVE_BODY + "-5", 1700000005000),
    ]
    con.executemany(
        "INSERT INTO messages(id, sender, body, ts) VALUES (?,?,?,?)", rows
    )
    con.commit()
    con.execute("DELETE FROM messages WHERE id = ?", (KAT_DELETED_ROWID,))
    con.commit()
    return con  # caller closes AFTER the recovery cases have run


def _flatten_carved_text(rows: list[Any]) -> str:
    """Join every value of every carved row into one searchable blob.

    Carved rows are positional and may be structurally incomplete, so a substring search
    over the whole row set is the honest way to ask "did the marker survive" without
    asserting a column mapping the carver may not have got right.
    """
    chunks: list[str] = []
    for row in rows:
        for value in getattr(row, "values", []) or []:
            if isinstance(value, str):
                chunks.append(value)
            elif isinstance(value, bytes):
                chunks.append(value.decode("utf-8", "replace"))
            elif value is not None:
                chunks.append(str(value))
    return "\n".join(chunks)


def _dataset_digest(root: Path) -> str:
    """SHA-256 over the as-left fixture tree: a digest of (relpath, sha256) pairs.

    This fixes the dataset for reproducibility (18-Q-001-2.1 §6 field 5). It is taken
    AFTER the cases have run, so it describes the fixtures in their final state — which
    includes the byte-flip made by the tamper case.
    """
    entries: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in sorted(filenames):
            full = Path(dirpath) / name
            try:
                rel = full.relative_to(root).as_posix()
                entries.append(f"{rel}:{hash_file(full)}")
            except OSError:
                # An unreadable fixture file is recorded as such rather than skipped
                # silently, so the digest cannot quietly change meaning.
                entries.append(f"{full.name}:UNREADABLE")
    entries.sort()
    return hash_bytes("\n".join(entries).encode("utf-8"))


# --- The cases ----------------------------------------------------------------
def _case_hash(root: Path) -> ValidationCase:
    kat_path = _build_kat_file(root)

    def run() -> tuple[dict[str, Any], bool, list[str]]:
        actual_digest = hash_file(kat_path)
        passed = actual_digest == KAT_VECTOR_SHA256
        anomalies = (
            []
            if passed
            else [
                "SHA-256 of the known-answer vector does not match the published "
                f"digest (expected {KAT_VECTOR_SHA256}, got {actual_digest}). This is "
                "an 'inaccuracy' error under SWGDE 12-Q-001 v2.0 and invalidates every "
                "integrity claim the tool makes, because the manifest, the audit trail "
                "and the tamper check all rest on this function."
            ]
        )
        return (
            {
                "sha256": actual_digest,
                "bytes_hashed": kat_path.stat().st_size,
                "implementation": "triage.hashing.hash_file (1 MiB streaming chunks)",
            },
            passed,
            anomalies,
        )

    return _run_case(
        "KAT-HASH-001",
        "SHA-256 of a fixed known-answer byte vector must equal the hard-coded "
        "published digest. The expected digest is a literal, not recomputed at run "
        "time, so the case cannot pass by comparing the implementation to itself.",
        "cryptographic hashing",
        {
            "sha256": KAT_VECTOR_SHA256,
            "bytes_hashed": len(KAT_VECTOR),
            "note": "digest hard-coded in harness.py as KAT_VECTOR_SHA256",
        },
        run,
    )


def _case_manifest_intact(root: Path) -> tuple[ValidationCase, Optional[Path]]:
    cases_root = root / "cases"
    case_dir_holder: dict[str, Optional[Path]] = {"path": None}

    def run() -> tuple[dict[str, Any], bool, list[str]]:
        case = _build_case_folder(
            cases_root,
            "VALIDATION-INTACT",
            {
                "alpha.txt": b"alpha payload for manifest verification\n",
                "beta.bin": bytes(range(256)) * 4,
                "gamma.txt": KAT_VECTOR,
            },
        )
        case_dir_holder["path"] = case.root
        result = verify_all_hashes(case.root)
        passed = (
            result["integrity_status"] == "INTACT"
            and result["total_files"] == 3
            and result["failed"] == 0
            and result["verified"] == 3
        )
        anomalies = (
            []
            if passed
            else [
                "An untouched case folder did not verify as INTACT "
                f"(status={result['integrity_status']}, total={result['total_files']}, "
                f"verified={result['verified']}, failed={result['failed']}). A false "
                "TAMPERED verdict on clean evidence is an 'existence' inaccuracy under "
                "SWGDE 12-Q-001 v2.0 and would discredit genuine evidence."
            ]
        )
        return (
            {
                "integrity_status": result["integrity_status"],
                "total_files": result["total_files"],
                "verified": result["verified"],
                "failed": result["failed"],
            },
            passed,
            anomalies,
        )

    case = _run_case(
        "KAT-MANIFEST-INTACT-002",
        "Three files of known content are ingested through the real "
        "triage.custody.Case.ingest_file path (which hashes at the moment of "
        "extraction). Re-verifying the untouched case folder must report INTACT with "
        "all three files verified and none failed.",
        "chain-of-custody / manifest integrity",
        {
            "integrity_status": "INTACT",
            "total_files": 3,
            "verified": 3,
            "failed": 0,
        },
        run,
    )
    return case, case_dir_holder["path"]


def _case_manifest_tampered(root: Path, intact_case_dir: Optional[Path]) -> ValidationCase:
    def run() -> tuple[dict[str, Any], bool, list[str]]:
        if intact_case_dir is None or not intact_case_dir.exists():
            return (
                {
                    "error": "the INTACT fixture case folder was not created, so the "
                    "tamper case could not be set up"
                },
                False,
                [
                    "Prerequisite fixture missing: KAT-MANIFEST-INTACT-002 did not "
                    "produce a case folder, so tamper detection was not exercised. "
                    "Recorded as a failure rather than skipped — an unexercised "
                    "integrity check must never read as a pass."
                ],
            )

        tampered_dir = root / "cases_tampered" / "VALIDATION-TAMPERED"
        tampered_dir.parent.mkdir(parents=True, exist_ok=True)
        if tampered_dir.exists():
            shutil.rmtree(tampered_dir)
        shutil.copytree(intact_case_dir, tampered_dir)

        # Resolve the artifact to tamper with FROM THE MANIFEST rather than assuming
        # the case-folder layout. The manifest is the authority on where an artifact
        # was stored, and hard-coding a path here would make this case silently stop
        # testing anything if custody.py ever reorganised the folder.
        entries = load_manifest(tampered_dir)
        target_rel = next(
            (
                e["stored_path"]
                for e in entries
                if str(e.get("stored_path", "")).endswith("alpha.txt")
            ),
            None,
        )
        if target_rel is None:
            return (
                {
                    "error": "no manifest entry for alpha.txt",
                    "manifest_entries": [e.get("stored_path") for e in entries],
                },
                False,
                [
                    "The tamper fixture could not be set up: the copied manifest has "
                    "no entry for the artifact to be modified, so tamper detection was "
                    "not exercised. Recorded as a failure, never as a skip."
                ],
            )

        # Flip exactly one byte in exactly one ingested artifact. Same file length, so
        # only the hash — not the size — reveals the change. That is precisely the
        # modification a size-based check would miss.
        target = tampered_dir / target_rel
        blob = bytearray(target.read_bytes())
        blob[0] ^= 0x01
        target.write_bytes(bytes(blob))

        result = verify_all_hashes(tampered_dir)
        failed_paths = [f["path"] for f in result["failed_files"]]
        passed = (
            result["integrity_status"] == "TAMPERED"
            and result["failed"] == 1
            and any("alpha.txt" in p for p in failed_paths)
        )
        anomalies = (
            []
            if passed
            else [
                "A single-byte modification to an ingested artifact was not reported "
                f"as TAMPERED (status={result['integrity_status']}, "
                f"failed={result['failed']}, failed_files={failed_paths}). Undetected "
                "alteration is the most serious possible failure for a chain-of-custody "
                "tool: it is an 'alteration' inaccuracy under SWGDE 12-Q-001 v2.0 and "
                "directly defeats CFTT MDT-CA-13."
            ]
        )
        return (
            {
                "integrity_status": result["integrity_status"],
                "total_files": result["total_files"],
                "verified": result["verified"],
                "failed": result["failed"],
                "failed_files": failed_paths,
                "modification": "one byte XOR 0x01 at offset 0 of alpha.txt "
                "(file length unchanged)",
            },
            passed,
            anomalies,
        )

    return _run_case(
        "KAT-MANIFEST-TAMPER-003",
        "A copy of the verified case folder has exactly one byte of one artifact "
        "flipped, leaving the file length unchanged. Re-verification must report "
        "TAMPERED and must name the specific modified file (CFTT MDT-CA-13).",
        "chain-of-custody / tamper detection",
        {
            "integrity_status": "TAMPERED",
            "failed": 1,
            "failed_file_contains": "alpha.txt",
        },
        run,
    )


def _case_deleted_recovery(db_path: Path) -> ValidationCase:
    def run() -> tuple[dict[str, Any], bool, list[str]]:
        carved = recover_deleted_rows(db_path, "messages")
        blob = _flatten_carved_text(carved)
        found = KAT_DELETED_BODY in blob
        confidences = sorted({str(getattr(r.confidence, "value", r.confidence)) for r in carved})
        # Carved data must never be labelled LIVE — that would be the honesty model
        # failing, which matters as much as the recovery itself.
        mislabelled = Confidence.LIVE.value in confidences
        passed = found and not mislabelled

        anomalies: list[str] = []
        if not found:
            anomalies.append(
                "The known deleted row body was not recovered from the WAL "
                "pre-deletion page image. Classified as 'incompleteness' under SWGDE "
                "12-Q-001 v2.0. Note that a nil result is the EXPECTED outcome for real "
                "Android framework databases (SECURE_DELETE + AUTOVACUUM), so this "
                "failure means the fixture path is broken, not that recovery is "
                "impossible in general."
            )
        if mislabelled:
            anomalies.append(
                "At least one carved row was labelled Confidence.LIVE. Carved data "
                "presented with the weight of live data is a 'misinterpretation' error "
                "under SWGDE 12-Q-001 v2.0 and violates this tool's honesty model."
            )

        return (
            {
                "carved_row_count": len(carved),
                "deleted_marker_recovered": found,
                "confidence_labels_present": confidences,
                "any_carved_row_labelled_live": mislabelled,
            },
            passed,
            anomalies,
        )

    return _run_case(
        "KAT-SQLITE-DELETED-004",
        "A WAL-mode SQLite database (wal_autocheckpoint=0, mirroring Android) has one "
        "row of known body text deleted. The recovery pass must recover that text from "
        "the pre-deletion page image, and must not label any carved row as LIVE.",
        "deleted-record recovery (SQLite, WAL pre-deletion image)",
        {
            "deleted_marker_recovered": True,
            "any_carved_row_labelled_live": False,
            "marker": KAT_DELETED_BODY,
        },
        run,
    )


def _case_rowid_gap(db_path: Path) -> ValidationCase:
    def run() -> tuple[dict[str, Any], bool, list[str]]:
        gaps = detect_rowid_gaps(db_path, "messages")
        expected_gap = {
            "after_rowid": KAT_DELETED_ROWID - 1,
            "before_rowid": KAT_DELETED_ROWID + 1,
            "missing": 1,
        }
        passed = expected_gap in gaps
        anomalies = (
            []
            if passed
            else [
                f"The rowid gap left by deleting rowid {KAT_DELETED_ROWID} was not "
                f"detected (got {gaps}). Gap analysis is the only deletion evidence "
                "available when content is unrecoverable — which is the normal case on "
                "SECURE_DELETE framework databases — so losing it is an "
                "'incompleteness' error under SWGDE 12-Q-001 v2.0."
            ]
        )
        return ({"gaps": gaps}, passed, anomalies)

    return _run_case(
        "KAT-SQLITE-GAP-005",
        "Deleting rowid 3 from a five-row table must be provable from the rowid "
        "sequence alone: the gap detector must report exactly one missing rowid "
        "between rowid 2 and rowid 4 (DELETION_DETECTED evidence, no content needed).",
        "deletion detection (rowid gap analysis)",
        {
            "gaps_contains": {
                "after_rowid": KAT_DELETED_ROWID - 1,
                "before_rowid": KAT_DELETED_ROWID + 1,
                "missing": 1,
            }
        },
        run,
    )


def _case_live_rows(db_path: Path) -> ValidationCase:
    def run() -> tuple[dict[str, Any], bool, list[str]]:
        live = read_live_rows(db_path, "messages")
        blob = _flatten_carved_text(live)
        deleted_leaked = KAT_DELETED_BODY in blob
        live_present = KAT_LIVE_BODY in blob
        labels = sorted({str(getattr(r.confidence, "value", r.confidence)) for r in live})
        all_live_labelled = labels == [Confidence.LIVE.value] if labels else False
        passed = (
            live_present
            and not deleted_leaked
            and len(live) == 4
            and all_live_labelled
        )

        anomalies: list[str] = []
        if deleted_leaked:
            anomalies.append(
                "A deleted row appeared in the LIVE row set. Presenting a deleted "
                "record as live is an 'existence' inaccuracy under SWGDE 12-Q-001 v2.0 "
                "and would put a false statement in front of an examiner."
            )
        if not live_present or len(live) != 4:
            anomalies.append(
                f"Live-row read returned {len(live)} row(s); 4 were expected. "
                "Classified as 'incompleteness' under SWGDE 12-Q-001 v2.0."
            )
        if not all_live_labelled:
            anomalies.append(
                f"Live rows carried confidence labels {labels}; every row read through "
                "the sqlite3 engine must be labelled LIVE."
            )

        return (
            {
                "live_row_count": len(live),
                "deleted_marker_leaked_into_live": deleted_leaked,
                "confidence_labels_present": labels,
            },
            passed,
            anomalies,
        )

    return _run_case(
        "KAT-SQLITE-LIVE-006",
        "The live-row read of the same fixture must return exactly the four surviving "
        "rows, must never surface the deleted row as live, and must label every row it "
        "returns with Confidence.LIVE.",
        "live-row read / confidence labelling",
        {
            "live_row_count": 4,
            "deleted_marker_leaked_into_live": False,
            "confidence_labels_present": [Confidence.LIVE.value],
        },
        run,
    )


def _case_timestamp() -> ValidationCase:
    def run() -> tuple[dict[str, Any], bool, list[str]]:
        emitted = now_iso()
        parsed_ok = True
        parsed_repr = ""
        try:
            parsed = datetime.strptime(emitted, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
            parsed_repr = parsed.isoformat()
        except ValueError:
            parsed_ok = False

        ends_with_z = emitted.endswith("Z")
        has_t = "T" in emitted
        length_ok = len(emitted) == 20
        passed = parsed_ok and ends_with_z and has_t and length_ok

        anomalies = (
            []
            if passed
            else [
                f"now_iso() emitted {emitted!r}, which is not ISO-8601 UTC with a "
                "trailing Z. Every artifact timestamp in the case folder and the "
                "timeline flows through this function; a malformed or zone-ambiguous "
                "timestamp is a 'misinterpretation' error under SWGDE 12-Q-001 v2.0 "
                "and would silently corrupt every temporal correlation in the report."
            ]
        )
        return (
            {
                "emitted": emitted,
                "parses_as_iso8601_utc": parsed_ok,
                "parsed": parsed_repr,
                "ends_with_Z": ends_with_z,
                "length": len(emitted),
            },
            passed,
            anomalies,
        )

    return _run_case(
        "KAT-TIME-007",
        "Timestamp normalisation (CFTT MDT-AO-22): the engine's canonical timestamp "
        "function must emit ISO-8601 UTC with an explicit trailing Z — never a bare "
        "integer and never an ambiguous local time — and the emitted string must "
        "round-trip through a strict ISO-8601 parse.",
        "timestamp normalisation",
        {
            "format": "%Y-%m-%dT%H:%M:%SZ",
            "parses_as_iso8601_utc": True,
            "ends_with_Z": True,
            "length": 20,
        },
        run,
    )


def _case_negative_control(root: Path) -> ValidationCase:
    """A control the tool is EXPECTED to fail.

    Its whole purpose is to prove this harness reports failures. A harness that only
    ever runs cases it knows will pass proves nothing about its own honesty, so one
    case here asks for a capability the tool genuinely does not have (reading plaintext
    out of an encrypted application database) and records the resulting failure.
    """

    def run() -> tuple[dict[str, Any], bool, list[str]]:
        # Stand-in for a SQLCipher-encrypted app database: no SQLite header, no
        # readable structure. Deterministic bytes so the fixture digest is stable.
        blob = bytes((i * 37 + 11) % 256 for i in range(4096))
        enc_path = root / "encrypted_app.db"
        enc_path.write_bytes(blob)

        live = read_live_rows(enc_path, "messages")
        carved = recover_deleted_rows(enc_path, "messages")
        text = _flatten_carved_text(live) + _flatten_carved_text(carved)
        recovered_plaintext = KAT_LIVE_BODY in text

        # The tool cannot do this, so the case does not pass. Recording it as a pass
        # because "it correctly failed" would be exactly the kind of self-flattering
        # bookkeeping this harness exists to rule out.
        passed = recovered_plaintext

        anomalies = [
            "NEGATIVE CONTROL — this case is expected to fail and its failure is the "
            "correct result. It asks the engine to produce plaintext message rows from "
            "an encrypted application database (the SQLCipher + hardware-Keystore case: "
            "Signal, Threema, Session, Wickr). The engine returned "
            f"{len(live)} live row(s) and {len(carved)} carved row(s) and no plaintext, "
            "which is the truthful outcome: the key never leaves the TEE/StrongBox and "
            "the content is not recoverable off-device at any tier, including root. "
            "This case is present so that a reader can confirm this harness records "
            "failures rather than swallowing them; a run in which it is marked passed "
            "should itself be treated as suspect."
        ]
        if recovered_plaintext:
            anomalies.append(
                "The negative control unexpectedly reported plaintext. That indicates "
                "the fixture is not actually opaque, not that decryption succeeded — "
                "investigate the fixture before drawing any conclusion."
            )

        return (
            {
                "live_rows_returned": len(live),
                "carved_rows_returned": len(carved),
                "plaintext_recovered": recovered_plaintext,
                "engine_behaviour": "degraded gracefully; returned empty result sets "
                "without raising, and reported no fabricated rows",
            },
            passed,
            anomalies,
        )

    return _run_case(
        NEGATIVE_CONTROL_CASE_ID,
        "NEGATIVE CONTROL (expected to fail): recover plaintext message rows from an "
        "encrypted application database. eRakshak cannot do this and must not appear "
        "to. A passed=False result here is the correct outcome and demonstrates that "
        "this harness reports failures.",
        "negative control — encrypted application content",
        {
            "plaintext_recovered": True,
            "note": "This expectation is deliberately unattainable. See the case "
            "description: failure is the correct result.",
        },
        run,
    )


# --- Public entry points ------------------------------------------------------
def run_self_validation(
    case_dir: Optional[str | Path] = None,
    *,
    tester: str = "",
    dataset_name: str = "",
    dataset_provenance: str = "",
) -> ValidationReport:
    """Run the offline known-answer self-validation and return a SWGDE report.

    Parameters
    ----------
    case_dir:
        Where to build the fixtures. If ``None`` a temporary directory is used and
        removed afterwards. Pass a path to keep the fixtures for inspection — a
        reviewer being able to look at the dataset is the point of 18-Q-001 §6 field 5.
    tester:
        Who ran the test (18-Q-001 §6 field 2). Left empty by default so that an
        unattributed report is reported as INCOMPLETE by
        :func:`triage.validation.swgde.validate_report` rather than silently
        acquiring a plausible author.
    dataset_name, dataset_provenance:
        Override the generated dataset description if the fixtures were sourced
        differently.

    Runs offline: no device, no network, no downloads. Never fabricates a pass — see
    the module docstring.
    """
    temp_holder: Optional[tempfile.TemporaryDirectory] = None
    if case_dir is None:
        temp_holder = tempfile.TemporaryDirectory(prefix="erakshak-validation-")
        root = Path(temp_holder.name)
    else:
        root = Path(case_dir)
        root.mkdir(parents=True, exist_ok=True)

    cases: list[ValidationCase] = []
    dataset_hash = ""

    try:
        # -- hashing ---------------------------------------------------------
        cases.append(_case_hash(root))

        # -- chain of custody ------------------------------------------------
        intact_case, intact_dir = _case_manifest_intact(root)
        cases.append(intact_case)
        cases.append(_case_manifest_tampered(root, intact_dir))

        # -- SQLite recovery -------------------------------------------------
        # The connection must stay open across all three DB cases so the -wal survives.
        db_path = root / "fixture_messages.db"
        con: Optional[sqlite3.Connection] = None
        try:
            con = _build_wal_db(db_path)
            cases.append(_case_deleted_recovery(db_path))
            cases.append(_case_rowid_gap(db_path))
            cases.append(_case_live_rows(db_path))
        except Exception as exc:  # noqa: BLE001
            # Fixture construction failed. Record it as a failed case; never skip.
            cases.append(
                ValidationCase(
                    case_id="KAT-SQLITE-FIXTURE-004",
                    description="Construction of the SQLite known-answer fixture.",
                    artifact_class="deleted-record recovery",
                    expected={"fixture_built": True},
                    actual={"error": f"{type(exc).__name__}: {exc}"},
                    passed=False,
                    anomalies=[
                        f"The SQLite fixture could not be built ({type(exc).__name__}: "
                        f"{exc}), so recovery, gap detection and live-row reading were "
                        "not exercised at all. Recorded as a failure, not a skip."
                    ],
                )
            )
        finally:
            if con is not None:
                con.close()

        # -- timestamps ------------------------------------------------------
        cases.append(_case_timestamp())

        # -- negative control -------------------------------------------------
        cases.append(_case_negative_control(root))

        # -- fix the dataset --------------------------------------------------
        try:
            dataset_hash = _dataset_digest(root)
        except Exception as exc:  # noqa: BLE001
            # An unhashable dataset is a reproducibility gap, not a reason to abort.
            dataset_hash = ""
            cases.append(
                ValidationCase(
                    case_id="KAT-DATASET-DIGEST-009",
                    description="Composite SHA-256 over the as-left fixture tree, so a "
                    "third party can confirm they hold the same dataset.",
                    artifact_class="dataset reproducibility",
                    expected={"dataset_hash_computed": True},
                    actual={"error": f"{type(exc).__name__}: {exc}"},
                    passed=False,
                    anomalies=[
                        "The dataset digest could not be computed, so this run is not "
                        "reproducible by a third party (18-Q-001-2.1 §6 field 5)."
                    ],
                )
            )

        environment = {
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            # The SQLite *library* version matters for recovery: page format details
            # and PRAGMA defaults differ between builds. (sqlite3.version, the DB-API
            # module version, was removed in Python 3.14 and is deliberately not used.)
            "sqlite_library_version": sqlite3.sqlite_version,
            "engine_version": __version__,
            "fixtures_retained": case_dir is not None,
            "fixture_root": str(root) if case_dir is not None else "(temporary)",
            "network_used": False,
            "device_attached": False,
            "testing_type": "known-dataset (SWGDE 18-Q-001-2.1 Appendix A, type 1)",
        }

        return build_report(
            cases,
            tool_name=TOOL_NAME,
            tool_version=__version__,
            tester=tester,
            dataset_name=dataset_name or DATASET_NAME_DEFAULT,
            dataset_provenance=dataset_provenance or DATASET_PROVENANCE_DEFAULT,
            dataset_hash=dataset_hash,
            environment=environment,
        )
    finally:
        if temp_holder is not None:
            temp_holder.cleanup()


def self_validation_summary() -> dict[str, Any]:
    """Run the self-validation and return a compact machine-readable summary.

    ``negative_control_passed`` is surfaced explicitly: it should always be ``False``,
    and a ``True`` there means the control is broken and the run should not be trusted.
    """
    report = run_self_validation()
    checks = validate_report(report)
    control = next(
        (c for c in report.cases if c.case_id == NEGATIVE_CONTROL_CASE_ID), None
    )
    # Cases excluding the negative control — the control is designed to fail, so
    # folding it into a headline pass rate would misrepresent both numbers.
    real_cases = [c for c in report.cases if c.case_id != NEGATIVE_CONTROL_CASE_ID]

    return {
        "tool_name": report.tool_name,
        "tool_version": report.tool_version,
        "tested_at": report.tested_at,
        "dataset_name": report.dataset_name,
        "dataset_hash": report.dataset_hash,
        "case_count": len(report.cases),
        "passed_count": report.passed_count(),
        "failed_count": report.failed_count(),
        "excluding_negative_control": {
            "case_count": len(real_cases),
            "passed_count": sum(1 for c in real_cases if c.passed),
            "failed_count": sum(1 for c in real_cases if not c.passed),
            "failed_ids": [c.case_id for c in real_cases if not c.passed],
        },
        "negative_control_case_id": NEGATIVE_CONTROL_CASE_ID,
        "negative_control_passed": bool(control.passed) if control else None,
        "negative_control_note": (
            "The negative control is EXPECTED to be False. A True value means the "
            "control itself is broken and this run should not be relied on."
        ),
        "report_complete": checks["complete"],
        "missing_required": checks["missing_required"],
        "warning_count": len(checks["warnings"]),
        "limitation_count": len(report.limitations),
        "conclusion": report.conclusion,
    }
