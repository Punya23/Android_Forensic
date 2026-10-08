"""The chain-of-custody record: who held the evidence, what was done to the device and the data,
and whether the record itself can still be trusted.

The audit log and manifest already hold the facts; this module only reads them back as a custody
record — it never writes evidence. The one thing it adds is the handover log (``add_transfer``),
and a handover goes into the hash-chained audit log as well as the case, so a handover cannot be
edited or removed afterwards without breaking the chain.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from .custody import Case
from .models import AuditEvent, now_iso

#: What a recorded handover can be. "sealed" closes the record: nothing is implied after it.
TRANSFER_ACTIONS = ("received", "released", "transferred", "examined", "returned", "sealed")
_MAX_NAME = 200
_MAX_TEXT = 1000

#: Custody phases, in the order a case moves through them.
_PHASES = (
    ("opened", "Case opened"),
    ("intake", "Device received and recorded"),
    ("altered", "Changes made to the device"),
    ("acquired", "Evidence acquired"),
    ("restored", "Device state after acquisition"),
    ("analysed", "Analysis"),
    ("reported", "Report issued"),
    ("handover", "Handovers"),
    ("other", "Other recorded actions"),
)
#: First matching prefix wins, so the specific "restored" actions come before the broad "tier1.".
_PREFIX_PHASE = (
    ("case.create", "opened"),
    ("device.intake", "intake"),
    ("device.prestate", "intake"),
    ("device.encryption", "intake"),
    ("tier1.helper.revoke", "restored"),
    ("tier1.helper.uninstall", "restored"),
    ("tier1.helper.appops_reset", "restored"),
    ("tier1.helper.notification_access_reset", "restored"),
    ("tier1.helper.rm_output", "restored"),
    ("tier1.helper.retained", "restored"),
    ("tier1.teardown", "restored"),
    ("device.poststate", "restored"),
    ("artifact.", "acquired"),
    ("fs.", "acquired"),
    ("shell.", "acquired"),
    ("parse.", "acquired"),
    ("tier1.", "acquired"),
    ("tier2.", "acquired"),
    ("screen.", "acquired"),
    ("appfinder", "acquired"),
    ("analysis.", "analysed"),
    ("recover", "analysed"),
    ("intel.", "analysed"),
    ("registry.", "analysed"),
    ("flag.", "analysed"),
    ("validation.", "analysed"),
    ("report.", "reported"),
    ("custody.", "handover"),
)


def _phase_of(event: dict[str, Any]) -> str:
    action = str(event.get("action", ""))
    for prefix, phase in _PREFIX_PHASE:
        if action.startswith(prefix):
            # A device-altering helper step (install, grant) is a change to the device, not
            # acquisition — it is the part an examiner must be able to account for.
            if phase == "acquired" and event.get("alters_device") and action.startswith("tier1."):
                return "altered"
            return phase
    return "altered" if event.get("alters_device") else "other"


def evidence_set_hash(manifest: list[Any]) -> str:
    """One SHA-256 over every acquired file's hash and source path, order-independent.

    Re-computing it later and getting the same value shows the acquired set is unchanged; it also
    gives the whole set a single reference number to quote on a form."""
    lines = sorted(f"{r.sha256}  {r.source_path}" for r in manifest)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _brief(e: dict[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": e.get("timestamp", ""),
        "action": e.get("action", ""),
        "detail": e.get("detail", ""),
        "actor": e.get("examiner", ""),
        "alters_device": bool(e.get("alters_device")),
        "result": e.get("result", "ok"),
        "command": e.get("command", ""),
    }


def build_timeline(audit: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group the audit log into custody phases with who/when/what for each."""
    groups: dict[str, list[dict[str, Any]]] = {key: [] for key, _ in _PHASES}
    for ev in audit:
        groups[_phase_of(ev)].append(ev)

    phases: list[dict[str, Any]] = []
    for key, title in _PHASES:
        events = groups[key]
        if not events:
            continue
        phase: dict[str, Any] = {
            "key": key,
            "title": title,
            "started": events[0].get("timestamp", ""),
            "ended": events[-1].get("timestamp", ""),
            "actors": sorted({e.get("examiner", "") for e in events if e.get("examiner")}),
            "event_count": len(events),
            "device_altering": sum(1 for e in events if e.get("alters_device")),
            "errors": sum(1 for e in events if e.get("result") == "error"),
        }
        if key == "acquired":
            ingests = [e for e in events if e.get("action") == "artifact.ingest"]
            phase["summary"] = f"{len(ingests)} file(s) copied and hashed; {len(events) - len(ingests)} other step(s)"
            # Thousands of per-file lines would bury the rest: list only the non-ingest steps.
            phase["events"] = [_brief(e) for e in events if e.get("action") != "artifact.ingest"][:80]
        else:
            phase["events"] = [_brief(e) for e in events][:80]
        phases.append(phase)
    return phases


def build_record(case: Case) -> dict[str, Any]:
    """Everything the custody screen shows, assembled from the case on disk."""
    meta = case.meta
    audit = case.read_audit()
    manifest = list(case.manifest)
    chain = case.verify_audit_chain()
    seal = case.audit_seal()

    by_tier: dict[str, dict[str, int]] = {}
    for r in manifest:
        t = by_tier.setdefault(getattr(r.tier, "value", str(r.tier)), {"files": 0, "bytes": 0})
        t["files"] += 1
        t["bytes"] += r.size_bytes

    summary = (meta.device_state or {}).get("summary") or {}
    pre = meta.pre_state or {}
    return {
        "case": {
            "case_id": meta.case_id,
            "examiner": meta.examiner,
            "legal_authority": meta.legal_authority,
            "scope_note": meta.scope_note,
            "opened_at": meta.created_at,
            "tool": f"{meta.tool_name} {meta.tool_version}",
        },
        "device": meta.device.to_dict(),
        "condition_on_receipt": {
            "screen_locked": pre.get("screen_locked"),
            "battery_level": pre.get("battery_level"),
            "device_time": pre.get("device_time"),
            "root_available": pre.get("root_available"),
            "note": pre.get("note", ""),
            "synthetic": "MOCK" in str(pre.get("note", "")).upper(),
        },
        "integrity": {
            "audit_chain": {
                "valid": chain.get("valid"),
                "total": chain.get("total"),
                "verified": chain.get("verified"),
                "first_bad_line": chain.get("first_bad_line"),
                "reason": chain.get("reason"),
                "head": seal["chain_head"],
                "checked_at": now_iso(),
            },
            "evidence": {
                "files": len(manifest),
                "bytes": sum(r.size_bytes for r in manifest),
                "set_sha256": evidence_set_hash(manifest),
                "by_tier": by_tier,
            },
            "seal_note": seal["instructions"],
        },
        "device_state": {
            "verdict": summary.get("teardown_verdict", "unverified"),
            "statement": summary.get("statement", ""),
            "returned_to_found_state": bool(summary.get("returned_to_found_state")),
            "device_altering_actions": sum(1 for e in audit if e.get("alters_device")),
        },
        "timeline": build_timeline(audit),
        "transfers": list(meta.custody_transfers),
        "transfer_actions": list(TRANSFER_ACTIONS),
    }


def add_transfer(case: Case, body: dict[str, Any], recorded_by: str) -> dict[str, Any]:
    """Record one handover. Validates at the boundary and writes it to the hash-chained audit log
    first, so the entry carries the audit hash that pins it in place."""
    action = str(body.get("action", "")).strip().lower()
    if action not in TRANSFER_ACTIONS:
        raise ValueError(f"action must be one of: {', '.join(TRANSFER_ACTIONS)}")

    def text(key: str, limit: int) -> str:
        value = str(body.get(key) or "").strip()
        if len(value) > limit:
            raise ValueError(f"{key} is longer than {limit} characters")
        return value

    from_person, to_person = text("from_person", _MAX_NAME), text("to_person", _MAX_NAME)
    if action != "sealed" and not (from_person or to_person):
        raise ValueError("name who handed the evidence over and/or who received it")
    entry = {
        "id": uuid.uuid4().hex[:12],
        "at": now_iso(),
        "action": action,
        "from_person": from_person,
        "to_person": to_person,
        "purpose": text("purpose", _MAX_TEXT),
        "location": text("location", _MAX_NAME),
        "notes": text("notes", _MAX_TEXT),
        "recorded_by": recorded_by,
    }
    who = " → ".join(p for p in (from_person, to_person) if p) or "—"
    case.audit(
        AuditEvent(
            timestamp=entry["at"],
            action="custody.transfer",
            detail=f"Evidence {action}: {who}" + (f" — {entry['purpose']}" if entry["purpose"] else ""),
            examiner=recorded_by,
            extra={k: entry[k] for k in ("id", "action", "from_person", "to_person", "location")},
        )
    )
    entry["audit_entry_hash"] = case.audit_head  # the chain head just written: pins this entry
    case.add_custody_transfer(entry)
    return entry
