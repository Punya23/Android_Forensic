"""Cross-case registry: a SQLite index of every case in ``cases_root``, plus a
history of every report ever generated for it.

The case folder (``case.json`` / ``derived/*.json`` / ``report.html``) remains the
source of truth — this registry is a derived, rebuildable cache that makes "list every
case we've ever worked" and "show me the report we generated on <date>" fast instead of
re-scanning and re-parsing every case folder on every request. Losing ``registry.db``
loses no evidence: :func:`sync_registry` rebuilds it from the case folders on disk.

Two tables:
  * ``cases``   — one row per case, kept current by :func:`upsert_case`.
  * ``reports`` — one row per report *generation*, appended by :func:`record_report`.
    Regenerating a report never overwrites history: each run is snapshotted to
    ``<case>/reports/report-<timestamp>.html`` and ``report.html`` at the case root is
    updated to match (unchanged behaviour for existing report-viewing code).
"""

from __future__ import annotations

import shutil
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

REGISTRY_FILENAME = "registry.db"

# Guards writes across threads (the Flask dev server and the SocketIO acquisition
# worker thread both write). SQLite serialises via its own file locking too, but this
# avoids "database is locked" retries under normal use.
_WRITE_LOCK = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    case_id             TEXT PRIMARY KEY,
    examiner            TEXT DEFAULT '',
    device_model        TEXT DEFAULT '',
    legal_authority     TEXT DEFAULT '',
    scope_note          TEXT DEFAULT '',
    crime_type          TEXT DEFAULT '',
    created_at          TEXT DEFAULT '',
    updated_at          TEXT DEFAULT '',
    artifact_count      INTEGER DEFAULT 0,
    total_bytes         INTEGER DEFAULT 0,
    audit_event_count   INTEGER DEFAULT 0,
    tag_count           INTEGER DEFAULT 0,
    report_count        INTEGER DEFAULT 0,
    latest_report_at    TEXT DEFAULT '',
    latest_report_path  TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS reports (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id       TEXT NOT NULL,
    generated_at  TEXT NOT NULL,
    path          TEXT NOT NULL,
    size_bytes    INTEGER DEFAULT 0,
    trigger       TEXT DEFAULT 'manual'
);
CREATE INDEX IF NOT EXISTS idx_reports_case ON reports(case_id);

-- Cross-case identifiers: one row per (case, category, normalised value), so the same
-- phone number/UPI ID/email surfacing in two cases on this installation is a single
-- indexed lookup rather than an O(n^2) in-memory comparison against every other case.
-- `value` is what's shown to the examiner (as it appeared in the evidence); `norm` is
-- what cross-case matching actually joins on (see case_reference.normalize_for_matching)
-- so "+91 98200 44711" and "+919820044711" are recognised as the same number without
-- ever displaying the normalised form as if it were the original artifact text.
CREATE TABLE IF NOT EXISTS case_identifiers (
    case_id          TEXT NOT NULL,
    category         TEXT NOT NULL,
    value            TEXT NOT NULL,
    norm             TEXT NOT NULL,
    source_dataset   TEXT DEFAULT '',
    source_ref       TEXT DEFAULT '',
    PRIMARY KEY (case_id, category, value)
);
CREATE INDEX IF NOT EXISTS idx_case_identifiers_norm ON case_identifiers(category, norm);
"""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _connect(cases_root: Path) -> sqlite3.Connection:
    cases_root.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(Path(cases_root) / REGISTRY_FILENAME))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def upsert_case(cases_root: Path, case: Any) -> None:
    """Index (or refresh) one case's summary row from its live custody data."""
    summary = case.custody_summary()
    meta = summary["case"]
    device = meta.get("device", {}) or {}
    case_profile = case.read_derived("case_profile") or {}
    tag_count = len(case.read_tags())

    with _WRITE_LOCK:
        conn = _connect(cases_root)
        try:
            conn.execute(
                """
                INSERT INTO cases (
                    case_id, examiner, device_model, legal_authority, scope_note,
                    crime_type, created_at, updated_at, artifact_count, total_bytes,
                    audit_event_count, tag_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(case_id) DO UPDATE SET
                    examiner=excluded.examiner,
                    device_model=excluded.device_model,
                    legal_authority=excluded.legal_authority,
                    scope_note=excluded.scope_note,
                    crime_type=excluded.crime_type,
                    updated_at=excluded.updated_at,
                    artifact_count=excluded.artifact_count,
                    total_bytes=excluded.total_bytes,
                    audit_event_count=excluded.audit_event_count,
                    tag_count=excluded.tag_count
                """,
                (
                    meta["case_id"],
                    meta.get("examiner", ""),
                    device.get("model", ""),
                    meta.get("legal_authority", ""),
                    meta.get("scope_note", ""),
                    case_profile.get("crime_type", ""),
                    meta.get("created_at", ""),
                    _now(),
                    summary.get("artifact_count", 0),
                    summary.get("total_bytes", 0),
                    summary.get("audit_event_count", 0),
                    tag_count,
                ),
            )
            conn.commit()
        finally:
            conn.close()


def record_report(
    cases_root: Path, case_root: Path, case_id: str, *, trigger: str = "manual"
) -> dict[str, Any]:
    """Snapshot the just-generated ``report.html`` into history and index it.

    Called right after :func:`triage.report.generate_report` writes
    ``<case_root>/report.html``. Copies it to
    ``<case_root>/reports/report-<timestamp>.html`` (so a prior version survives the
    next regeneration) and records the event in the ``reports`` table.
    """
    src = Path(case_root) / "report.html"
    if not src.exists():
        return {"recorded": False, "reason": "report.html not found"}

    history_dir = Path(case_root) / "reports"
    history_dir.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    snapshot_name = f"report-{stamp}.html"
    snapshot_path = history_dir / snapshot_name
    # Two reports generated within the same second would collide; disambiguate.
    n = 1
    while snapshot_path.exists():
        snapshot_path = history_dir / f"report-{stamp}-{n}.html"
        n += 1
    shutil.copy2(src, snapshot_path)
    size_bytes = snapshot_path.stat().st_size
    generated_at = _now()

    with _WRITE_LOCK:
        conn = _connect(cases_root)
        try:
            conn.execute(
                "INSERT INTO reports (case_id, generated_at, path, size_bytes, trigger) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    case_id,
                    generated_at,
                    str(snapshot_path.relative_to(case_root)),
                    size_bytes,
                    trigger,
                ),
            )
            conn.execute(
                """
                UPDATE cases SET report_count = report_count + 1,
                                  latest_report_at = ?,
                                  latest_report_path = ?
                WHERE case_id = ?
                """,
                (generated_at, str(snapshot_path.relative_to(case_root)), case_id),
            )
            conn.commit()
        finally:
            conn.close()

    return {
        "recorded": True,
        "generated_at": generated_at,
        "path": snapshot_path.name,
        "size_bytes": size_bytes,
        "trigger": trigger,
    }


def delete_case_row(cases_root: Path, case_id: str) -> None:
    with _WRITE_LOCK:
        conn = _connect(cases_root)
        try:
            conn.execute("DELETE FROM cases WHERE case_id = ?", (case_id,))
            conn.execute("DELETE FROM reports WHERE case_id = ?", (case_id,))
            conn.execute("DELETE FROM case_identifiers WHERE case_id = ?", (case_id,))
            conn.commit()
        finally:
            conn.close()


def upsert_case_identifiers(cases_root: Path, case_id: str, identifiers: dict) -> int:
    """(Re-)index one case's extracted identifiers for cross-case linking.

    *identifiers* is the ``{category: [IdentifierMatch, ...]}`` shape from
    :func:`triage.forensics.case_reference.extract_case_identifiers`. Replaces this
    case's rows atomically (delete-then-insert in one transaction) so a re-run after
    new artifacts are collected doesn't leave stale identifiers from an earlier,
    smaller extraction. Returns the number of identifiers indexed.
    """
    from .forensics.case_reference import normalize_for_matching

    rows = []
    for category, matches in (identifiers or {}).items():
        for m in matches:
            value = m.value if hasattr(m, "value") else m.get("value", "")
            source_dataset = m.source_dataset if hasattr(m, "source_dataset") else m.get("source_dataset", "")
            source_ref = m.source_ref if hasattr(m, "source_ref") else m.get("source_ref", "")
            if not value:
                continue
            rows.append(
                (
                    case_id,
                    category,
                    value,
                    normalize_for_matching(category, value),
                    source_dataset,
                    source_ref,
                )
            )

    with _WRITE_LOCK:
        conn = _connect(cases_root)
        try:
            conn.execute("DELETE FROM case_identifiers WHERE case_id = ?", (case_id,))
            conn.executemany(
                "INSERT INTO case_identifiers "
                "(case_id, category, value, norm, source_dataset, source_ref) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()
        finally:
            conn.close()
    return len(rows)


def sync_registry(cases_root: Path) -> int:
    """Backfill the registry from case folders that predate it or were never indexed.

    Safe to call repeatedly (e.g. once at server startup) — existing rows are left
    alone; only case folders with no row are indexed. Returns the number indexed.
    """
    from .custody import Case

    cases_root = Path(cases_root)
    if not cases_root.exists():
        return 0

    with _WRITE_LOCK:
        conn = _connect(cases_root)
        try:
            known = {r["case_id"] for r in conn.execute("SELECT case_id FROM cases")}
        finally:
            conn.close()

    indexed = 0
    for d in sorted(cases_root.iterdir()):
        if not (d / "case.json").exists():
            continue
        if d.name in known:
            continue
        try:
            case = Case.open(d)
            upsert_case(cases_root, case)
            report_path = d / "report.html"
            if report_path.exists():
                # Index the existing report as history without duplicating the file.
                with _WRITE_LOCK:
                    conn = _connect(cases_root)
                    try:
                        generated_at = time.strftime(
                            "%Y-%m-%dT%H:%M:%SZ",
                            time.gmtime(report_path.stat().st_mtime),
                        )
                        conn.execute(
                            "INSERT INTO reports (case_id, generated_at, path, size_bytes, trigger) "
                            "VALUES (?, ?, ?, ?, ?)",
                            (
                                d.name,
                                generated_at,
                                "report.html",
                                report_path.stat().st_size,
                                "backfill",
                            ),
                        )
                        conn.execute(
                            "UPDATE cases SET report_count = report_count + 1, "
                            "latest_report_at = ?, latest_report_path = ? WHERE case_id = ?",
                            (generated_at, "report.html", d.name),
                        )
                        conn.commit()
                    finally:
                        conn.close()
            indexed += 1
        except Exception:  # pragma: no cover - a malformed case folder must not block startup
            continue
    return indexed


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def list_cases(
    cases_root: Path, *, q: str = "", sort: str = "-updated_at", limit: int = 500
) -> list[dict[str, Any]]:
    conn = _connect(cases_root)
    try:
        sql = "SELECT * FROM cases"
        params: list[Any] = []
        if q:
            sql += (
                " WHERE case_id LIKE ? OR examiner LIKE ? OR device_model LIKE ? "
                "OR crime_type LIKE ?"
            )
            like = f"%{q}%"
            params += [like, like, like, like]
        column = sort.lstrip("-")
        allowed = {
            "created_at",
            "updated_at",
            "examiner",
            "device_model",
            "artifact_count",
            "total_bytes",
            "report_count",
            "case_id",
        }
        if column not in allowed:
            column = "updated_at"
        direction = "DESC" if sort.startswith("-") else "ASC"
        sql += f" ORDER BY {column} {direction} LIMIT ?"
        params.append(limit)
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def get_case_row(cases_root: Path, case_id: str) -> Optional[dict[str, Any]]:
    conn = _connect(cases_root)
    try:
        row = conn.execute(
            "SELECT * FROM cases WHERE case_id = ?", (case_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_reports(cases_root: Path, case_id: str) -> list[dict[str, Any]]:
    conn = _connect(cases_root)
    try:
        rows = conn.execute(
            "SELECT * FROM reports WHERE case_id = ? ORDER BY generated_at DESC",
            (case_id,),
        )
        return [dict(r) for r in rows]
    finally:
        conn.close()


def find_linked_cases(cases_root: Path, case_id: str) -> list[dict[str, Any]]:
    """Every OTHER case on this installation sharing an identifier with *case_id*.

    A single indexed self-join against ``case_identifiers`` (see the table's
    definition) rather than pairwise comparison against every case in memory. Returns
    one row per (other_case_id, category, value) match, each carrying both cases'
    original (non-normalised) values and their provenance — so a match is never just
    "these two cases are linked" with no way to check it, but exactly which artifact in
    each case produced the shared value.

    This is a fact about the *installation's case history*, never evidence linking two
    investigations — the disclaimer travels with the API response, matching how
    precedent retrieval is presented in the case-intelligence plan.
    """
    conn = _connect(cases_root)
    try:
        rows = conn.execute(
            """
            SELECT a.category AS category,
                   a.value AS this_value, a.source_dataset AS this_source_dataset,
                   a.source_ref AS this_source_ref,
                   b.case_id AS other_case_id,
                   b.value AS other_value, b.source_dataset AS other_source_dataset,
                   b.source_ref AS other_source_ref
            FROM case_identifiers a
            JOIN case_identifiers b
              ON a.category = b.category AND a.norm = b.norm AND a.case_id != b.case_id
            WHERE a.case_id = ?
            ORDER BY b.case_id, a.category
            """,
            (case_id,),
        )
        matches = [dict(r) for r in rows]
    finally:
        conn.close()

    # Group by the other case so the caller gets "these N shared identifiers link to
    # case X" rather than a flat list an examiner has to re-group themselves.
    by_case: dict[str, dict[str, Any]] = {}
    for m in matches:
        other = m["other_case_id"]
        entry = by_case.setdefault(other, {"case_id": other, "shared": []})
        entry["shared"].append(
            {
                "category": m["category"],
                "this_value": m["this_value"],
                "this_source": f"{m['this_source_dataset']}: {m['this_source_ref']}",
                "other_value": m["other_value"],
                "other_source": f"{m['other_source_dataset']}: {m['other_source_ref']}",
            }
        )
    out = list(by_case.values())
    out.sort(key=lambda e: len(e["shared"]), reverse=True)
    return out


def registry_stats(cases_root: Path) -> dict[str, Any]:
    conn = _connect(cases_root)
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS cases, COALESCE(SUM(artifact_count),0) AS artifacts, "
            "COALESCE(SUM(total_bytes),0) AS bytes, COALESCE(SUM(report_count),0) AS reports "
            "FROM cases"
        ).fetchone()
        return dict(row)
    finally:
        conn.close()
