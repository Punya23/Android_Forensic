"""Evidence-package export.

Bundles a case folder into a single ZIP with a top-level VERIFICATION.txt that restates the
per-artifact SHA-256 manifest and a hash of the audit log, so the package can be handed off
and later verified independently. This is the "export a sealed evidence package" capability
commercial suites provide at the end of a triage.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from . import TOOL_NAME, __version__
from .hashing import hash_file
from .models import now_iso


def export_case(case_dir: str | Path, out_path: str | Path | None = None) -> Path:
    case_dir = Path(case_dir)
    case_id = case_dir.name
    out_path = Path(out_path) if out_path else case_dir.parent / f"{case_id}_evidence.zip"

    manifest = json.loads((case_dir / "manifest.json").read_text()) \
        if (case_dir / "manifest.json").exists() else []
    audit_hash = hash_file(case_dir / "audit.jsonl") if (case_dir / "audit.jsonl").exists() else "—"

    lines = [
        f"{TOOL_NAME} v{__version__} — Evidence Package Verification",
        f"Case: {case_id}",
        f"Exported: {now_iso()}",
        f"Audit-log SHA-256: {audit_hash}",
        "",
        "Per-artifact integrity (SHA-256):",
    ]
    for a in manifest:
        lines.append(f"  {a['sha256']}  {a['source_path']}")
    lines.append("")
    lines.append("To verify: recompute SHA-256 of each file under artifacts/ and compare to "
                 "this list and to manifest.json. Any mismatch indicates tampering.")
    verification = "\n".join(lines)

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("VERIFICATION.txt", verification)
        for path in sorted(case_dir.rglob("*")):
            if path.is_file():
                zf.write(path, arcname=str(Path(case_id) / path.relative_to(case_dir)))

    return out_path
