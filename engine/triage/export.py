"""Evidence-package export.

Bundles a case folder into a single ZIP with a top-level VERIFICATION.txt that restates the
per-artifact SHA-256 manifest and a hash of the audit log, so the package can be handed off
and later verified independently. This is the "export a sealed evidence package" capability
commercial suites provide at the end of a triage.
"""

from __future__ import annotations

import json
import logging
import zipfile
from pathlib import Path
from typing import Any, Dict, List

from . import TOOL_NAME, __version__
from .hashing import hash_file
from .models import now_iso

logger = logging.getLogger(__name__)


def generate_verification_instructions(manifest: List[Dict]) -> str:
    """Generate verification instructions for package."""
    lines = [
        "To verify this evidence package:",
        "1. Recompute the SHA-256 hash of each file under the artifacts/ directory.",
        "2. Compare the computed hashes to the list below and to manifest.json.",
        "3. Any mismatch indicates that the evidence file has been modified or corrupted.",
        "",
        "You can use standard command-line tools for verification:",
        "  - Linux/Mac: sha256sum <file>",
        "  - Windows: certutil -hashfile <file> SHA256",
        "",
        "Alternatively, use the Android_Forensic tool's integrity_report module.",
        "",
        "----------------------------------------------------------------------",
    ]
    return "\n".join(lines)


def verify_artifacts_on_disk(
    case_dir: Path, artifacts: List[Dict]
) -> Dict[str, Any]:
    """Recompute each artifact's SHA-256 from disk and compare to the manifest.

    A sealed evidence package must attest to the files it actually contains, not to
    hashes recorded earlier — a file altered or truncated between ingest and export
    would otherwise be shipped with its original (now-wrong) hash and no mismatch
    flagged. This re-hashes every stored artifact at seal time so the package is
    self-attesting.
    """
    results: List[Dict[str, Any]] = []
    verified = missing = mismatched = 0
    for a in artifacts:
        rel = a.get("stored_path") or a.get("path")
        expected = a.get("sha256") or a.get("sha256_hash") or ""
        if not rel:
            continue
        fpath = case_dir / rel
        if not fpath.exists():
            status = "MISSING"
            actual = ""
            missing += 1
        else:
            actual = hash_file(fpath)
            if expected and actual.lower() == expected.lower():
                status = "OK"
                verified += 1
            else:
                status = "MISMATCH"
                mismatched += 1
        results.append(
            {
                "stored_path": rel,
                "expected": expected,
                "actual": actual,
                "status": status,
            }
        )

    total = verified + missing + mismatched
    overall = (
        "INTACT"
        if total > 0 and mismatched == 0 and missing == 0
        else "COMPROMISED" if (mismatched or missing) else "UNKNOWN"
    )
    return {
        "overall": overall,
        "total": total,
        "verified": verified,
        "missing": missing,
        "mismatched": mismatched,
        "results": results,
    }


def create_integrity_manifest(case_dir: Path) -> Dict[str, Any]:
    """Create integrity manifest with all hashes, re-verified against the files on disk."""
    manifest_path = case_dir / "manifest.json"
    manifest = []
    if manifest_path.exists():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest = data.get("artifacts", []) if isinstance(data, dict) else data
        except json.JSONDecodeError:
            pass

    audit_file = case_dir / "audit.jsonl"
    audit_hash = hash_file(audit_file) if audit_file.exists() else "—"

    verification = verify_artifacts_on_disk(case_dir, manifest)
    if verification["mismatched"] or verification["missing"]:
        logger.error(
            "Export integrity check FAILED for %s: %d mismatched, %d missing of %d",
            case_dir.name,
            verification["mismatched"],
            verification["missing"],
            verification["total"],
        )

    return {
        "case_id": case_dir.name,
        "exported_at": now_iso(),
        "tool_version": f"{TOOL_NAME} v{__version__}",
        "audit_hash": audit_hash,
        "artifacts": manifest,
        "seal_verification": verification,
    }


def add_verification_file(package_path: Path, manifest_data: Dict[str, Any]) -> None:
    """Add verification file to evidence package."""
    artifacts = manifest_data.get("artifacts", [])
    sv = manifest_data.get("seal_verification", {})

    lines = [
        f"{manifest_data['tool_version']} — Evidence Package Verification",
        f"Case: {manifest_data['case_id']}",
        f"Exported: {manifest_data['exported_at']}",
        f"Audit-log SHA-256: {manifest_data['audit_hash']}",
        "",
    ]

    if sv:
        lines += [
            "Seal-time re-verification (files re-hashed at export):",
            f"  Status: {sv.get('overall', 'UNKNOWN')}",
            f"  Verified: {sv.get('verified', 0)}  "
            f"Mismatched: {sv.get('mismatched', 0)}  "
            f"Missing: {sv.get('missing', 0)}  "
            f"of {sv.get('total', 0)}",
        ]
        bad = [
            r for r in sv.get("results", []) if r.get("status") != "OK"
        ]
        if bad:
            lines.append("  ** Artifacts that FAILED re-verification at seal time: **")
            for r in bad:
                lines.append(
                    f"    [{r['status']}] {r['stored_path']}"
                    + (f"  (expected {r['expected']}, got {r['actual']})"
                       if r["status"] == "MISMATCH" else "")
                )
        lines.append("")

    lines += [
        generate_verification_instructions(artifacts),
        "",
        "Per-artifact integrity (SHA-256, as recorded at acquisition):",
    ]

    for a in artifacts:
        # Manifest records use 'sha256'; tolerate the legacy 'sha256_hash' key.
        h = a.get("sha256") or a.get("sha256_hash") or "-"
        p = a.get("stored_path") or a.get("path") or a.get("source_path", "-")
        lines.append(f"  {h}  {p}")

    lines.append("")
    verification = "\n".join(lines)

    # Append to existing zip
    with zipfile.ZipFile(package_path, "a", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("VERIFICATION.txt", verification)

    logger.info("Added VERIFICATION.txt to evidence package")


def export_with_hashes(case_dir: Path, output_path: Path) -> None:
    """Export evidence package with hashes."""
    case_dir = Path(case_dir)
    case_id = case_dir.name

    # 1. Create the basic zip
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(case_dir.rglob("*")):
            if path.is_file():
                zf.write(path, arcname=str(Path(case_id) / path.relative_to(case_dir)))

    # 2. Generate and add integrity manifest
    manifest_data = create_integrity_manifest(case_dir)
    add_verification_file(output_path, manifest_data)


def export_case(case_dir: str | Path, out_path: str | Path | None = None) -> Path:
    """Export case with hashes included."""
    case_dir = Path(case_dir)
    case_id = case_dir.name
    output_path = (
        Path(out_path) if out_path else case_dir.parent / f"{case_id}_evidence.zip"
    )

    export_with_hashes(case_dir, output_path)
    return output_path
