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
from typing import Any, Dict, List, Optional

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


def create_integrity_manifest(case_dir: Path) -> Dict[str, Any]:
    """Create integrity manifest with all hashes."""
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

    return {
        "case_id": case_dir.name,
        "exported_at": now_iso(),
        "tool_version": f"{TOOL_NAME} v{__version__}",
        "audit_hash": audit_hash,
        "artifacts": manifest,
    }


def add_verification_file(package_path: Path, manifest_data: Dict[str, Any]) -> None:
    """Add verification file to evidence package."""
    artifacts = manifest_data.get("artifacts", [])

    lines = [
        f"{manifest_data['tool_version']} — Evidence Package Verification",
        f"Case: {manifest_data['case_id']}",
        f"Exported: {manifest_data['exported_at']}",
        f"Audit-log SHA-256: {manifest_data['audit_hash']}",
        "",
        generate_verification_instructions(artifacts),
        "",
        "Per-artifact integrity (SHA-256):",
    ]

    for a in artifacts:
        # Some manifests use 'sha256_hash', others 'sha256'
        h = a.get("sha256_hash") or a.get("sha256", "-")
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
