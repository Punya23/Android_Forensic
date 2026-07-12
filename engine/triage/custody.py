"""Chain-of-custody core: the case folder, the append-only audit log, and the hash
manifest.

Design principles (NIST SP 800-101r1 / SWGDE-aligned):
  * The audit log is append-only JSONL — every device interaction is one line, written
    and flushed immediately so a crash cannot silently drop the record of an action.
  * Hashes are computed at the moment of extraction and written straight into the
    manifest (SWGDE: "as early as possible").
  * We never compute a whole-device hash (NIST §3.4: live devices make it irreproducible).
  * Nothing here ever claims 'read-only' — device-altering actions are flagged as such.
"""
from __future__ import annotations

import json
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import TOOL_NAME, __version__
from .config import ACQUISITION_DISCLAIMER, STANDARDS_REFS
from .hashing import file_hashes
from .models import ArtifactRecord, AuditEvent, now_iso


@dataclass
class DeviceInfo:
    """Device intake block — captured once at the start of a case."""

    manufacturer: str = ""
    brand: str = ""
    model: str = ""
    product: str = ""
    android_version: str = ""
    sdk: str = ""
    build_id: str = ""
    serial: str = ""
    imei: str = ""
    carrier: str = ""
    rooted: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class CaseMeta:
    """Everything about the case that isn't an artifact or an audit line."""

    case_id: str
    examiner: str
    legal_authority: str = ""          # warrant / consent / exigency reference
    scope_note: str = ""               # minimisation: what we were authorised to look at
    created_at: str = field(default_factory=now_iso)
    tool_name: str = TOOL_NAME
    tool_version: str = __version__
    device: DeviceInfo = field(default_factory=DeviceInfo)
    pre_state: dict[str, Any] = field(default_factory=dict)  # locked?, battery, time-skew...
    custody_transfers: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["device"] = self.device.to_dict()
        return d


class Case:
    """Owns one per-device case folder and mediates all writes to it.

    Layout::

        cases/<case_id>/
            case.json          # CaseMeta
            audit.jsonl        # append-only audit trail
            manifest.json      # per-artifact hash manifest
            artifacts/         # raw pulled files, mirrored device tree
            derived/           # parsed JSON (messages, contacts, timeline, ...)
            report.html        # generated triage report
    """

    def __init__(self, root: Path, meta: CaseMeta):
        self.root = root
        self.meta = meta
        self.artifacts_dir = root / "artifacts"
        self.derived_dir = root / "derived"
        self._audit_path = root / "audit.jsonl"
        self._manifest_path = root / "manifest.json"
        self._case_path = root / "case.json"
        self._manifest: list[ArtifactRecord] = []
        self._lock = threading.Lock()

    # -- lifecycle -----------------------------------------------------------
    @classmethod
    def create(cls, cases_root: Path, meta: CaseMeta) -> "Case":
        root = Path(cases_root) / meta.case_id
        root.mkdir(parents=True, exist_ok=False)
        case = cls(root, meta)
        case.artifacts_dir.mkdir()
        case.derived_dir.mkdir()
        case._save_meta()
        case._manifest_path.write_text("[]")
        case._audit_path.touch()
        case.audit(AuditEvent(
            timestamp=now_iso(),
            action="case.create",
            detail=f"Case {meta.case_id} opened by {meta.examiner}",
            examiner=meta.examiner,
        ))
        return case

    @classmethod
    def open(cls, case_root: Path) -> "Case":
        case_root = Path(case_root)
        meta_raw = json.loads((case_root / "case.json").read_text())
        dev = DeviceInfo(**{k: v for k, v in meta_raw.pop("device", {}).items()
                            if k in DeviceInfo.__dataclass_fields__})
        meta = CaseMeta(**{k: v for k, v in meta_raw.items()
                           if k in CaseMeta.__dataclass_fields__})
        meta.device = dev
        case = cls(case_root, meta)
        if case._manifest_path.exists():
            case._manifest = [ArtifactRecord(**_coerce_artifact(r))
                              for r in json.loads(case._manifest_path.read_text())]
        return case

    # -- meta ----------------------------------------------------------------
    def _save_meta(self) -> None:
        self._case_path.write_text(json.dumps(self.meta.to_dict(), indent=2))

    def update_device(self, device: DeviceInfo) -> None:
        self.meta.device = device
        self._save_meta()

    def set_pre_state(self, state: dict[str, Any]) -> None:
        self.meta.pre_state = state
        self._save_meta()

    # -- audit ---------------------------------------------------------------
    def audit(self, event: AuditEvent) -> None:
        """Append one event and flush immediately (append-only, crash-safe)."""
        if not event.examiner:
            event.examiner = self.meta.examiner
        with self._lock:
            with open(self._audit_path, "a") as fh:
                fh.write(json.dumps(event.to_dict()) + "\n")
                fh.flush()

    def log(self, action: str, detail: str, *, command: str = "",
            result: str = "ok", alters_device: bool = False,
            tier: Optional[str] = None, **extra: Any) -> None:
        """Convenience wrapper to record an audit event."""
        self.audit(AuditEvent(
            timestamp=now_iso(), action=action, detail=detail, command=command,
            result=result, alters_device=alters_device, tier=tier, extra=extra,
        ))

    def read_audit(self) -> list[dict[str, Any]]:
        if not self._audit_path.exists():
            return []
        return [json.loads(line) for line in self._audit_path.read_text().splitlines()
                if line.strip()]

    # -- artifacts / manifest ------------------------------------------------
    def ingest_file(self, src: Path, *, source_path: str, tier: Any, method: str,
                    category: str = "other", app: Optional[str] = None,
                    flags: Optional[list[str]] = None,
                    move: bool = False) -> ArtifactRecord:
        """Copy (or move) a pulled file into the case folder, hash it, and record it.

        `source_path` is the path the file had on the device (for the manifest);
        `src` is where it currently lives on the workstation.
        """
        rel = _safe_rel(source_path)
        dest = self.artifacts_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if move:
            shutil.move(str(src), dest)
        else:
            shutil.copy2(src, dest)

        hashes = file_hashes(dest)
        rec = ArtifactRecord(
            artifact_id=f"a{len(self._manifest):05d}",
            source_path=source_path,
            stored_path=str(dest.relative_to(self.root)),
            size_bytes=dest.stat().st_size,
            sha256=hashes["sha256"],
            md5=hashes["md5"],
            tier=tier,
            method=method,
            extracted_at=now_iso(),
            category=category,
            app=app,
            flags=flags or [],
        )
        with self._lock:
            self._manifest.append(rec)
            self._flush_manifest()
        self.log("artifact.ingest",
                 f"{source_path} -> {rec.stored_path} ({rec.size_bytes} B)",
                 tier=str(getattr(tier, "value", tier)),
                 sha256=rec.sha256, artifact_id=rec.artifact_id)
        return rec

    def _flush_manifest(self) -> None:
        self._manifest_path.write_text(
            json.dumps([r.to_dict() for r in self._manifest], indent=2))

    @property
    def manifest(self) -> list[ArtifactRecord]:
        return list(self._manifest)

    # -- tags / bookmarks ----------------------------------------------------
    def _tags_path(self) -> Path:
        return self.root / "tags.json"

    def read_tags(self) -> list[dict[str, Any]]:
        p = self._tags_path()
        return json.loads(p.read_text()) if p.exists() else []

    def add_tag(self, *, ref: str, kind: str, label: str, note: str = "",
                by: str = "") -> dict[str, Any]:
        """Bookmark an artifact/row of interest (on-scene tagging). `ref` is a stable
        reference the UI supplies (e.g. 'message:42', 'media:a00003', 'recovered:7')."""
        tags = self.read_tags()
        tag = {"id": f"t{len(tags):04d}", "ref": ref, "kind": kind, "label": label,
               "note": note, "by": by or self.meta.examiner, "at": now_iso()}
        tags.append(tag)
        self._tags_path().write_text(json.dumps(tags, indent=2))
        self.log("tag.add", f"tagged {ref} as '{label}'", ref=ref, kind=kind)
        return tag

    def remove_tag(self, tag_id: str) -> bool:
        tags = self.read_tags()
        remaining = [t for t in tags if t["id"] != tag_id]
        if len(remaining) == len(tags):
            return False
        self._tags_path().write_text(json.dumps(remaining, indent=2))
        self.log("tag.remove", f"removed tag {tag_id}")
        return True

    # -- derived data --------------------------------------------------------
    def write_derived(self, name: str, data: Any) -> Path:
        """Persist a parsed dataset (list of dataclasses or dicts) as JSON."""
        path = self.derived_dir / f"{name}.json"
        payload = [d.to_dict() if hasattr(d, "to_dict") else d for d in data] \
            if isinstance(data, list) else data
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def read_derived(self, name: str) -> Any:
        path = self.derived_dir / f"{name}.json"
        if not path.exists():
            return [] if name != "summary" else {}
        return json.loads(path.read_text(encoding="utf-8"))

    # -- summary for the dashboard/report ------------------------------------
    def custody_summary(self) -> dict[str, Any]:
        audit = self.read_audit()
        return {
            "case": self.meta.to_dict(),
            "disclaimer": ACQUISITION_DISCLAIMER,
            "standards": STANDARDS_REFS,
            "artifact_count": len(self._manifest),
            "total_bytes": sum(r.size_bytes for r in self._manifest),
            "audit_event_count": len(audit),
            "device_altering_actions": sum(1 for e in audit if e.get("alters_device")),
        }


def _safe_rel(device_path: str) -> Path:
    """Turn an absolute device path into a safe relative path inside the case folder,
    preventing path-traversal via '..' segments in a hostile filename."""
    parts = [p for p in device_path.replace("\\", "/").split("/")
             if p and p not in (".", "..")]
    return Path(*parts) if parts else Path("unnamed")


def _coerce_artifact(rec: dict[str, Any]) -> dict[str, Any]:
    """Coerce a manifest dict back into ArtifactRecord kwargs (tier str -> Tier)."""
    from .config import Tier
    out = dict(rec)
    if isinstance(out.get("tier"), str):
        out["tier"] = Tier(out["tier"])
    return out
