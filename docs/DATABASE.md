# Database / data model

[← back to README](../README.md)

The tool's entire persistence layer: **one SQLite database** (a rebuildable cross-case
index, not the source of truth) plus **flat JSON/JSONL per case** (the actual system of
record). No PostgreSQL, MySQL, or ORM anywhere in the repo — confirmed by grep across
`requirements.txt` and `package.json`.

## Cross-case registry — `cases/registry.db` (SQLite, WAL mode)

```mermaid
erDiagram
    CASES ||--o{ REPORTS : "generates"
    CASES ||--o{ CASE_IDENTIFIERS : "extracted from"
    CASES {
        text case_id PK
        text examiner
        text device_model
        text crime_type
        text created_at
        text updated_at
        integer artifact_count
        integer total_bytes
        integer audit_event_count
        integer tag_count
        integer report_count
    }
    REPORTS {
        integer id PK
        text case_id FK
        text generated_at
        text path
        integer size_bytes
        text trigger
    }
    CASE_IDENTIFIERS {
        text case_id PK_FK
        text category PK
        text value PK
        text norm
        text source_dataset
        text source_ref
    }
```

Verbatim DDL (`engine/triage/registry.py`):
```sql
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

-- Cross-case identifier index (v0.7) — feeds GET /api/case/:id/linked-cases. `value` is
-- exactly as it appeared in the evidence (always the citation shown to the examiner);
-- `norm` is what cross-case matching actually joins on (case_reference.normalize_for_matching
-- — e.g. "+91 98200 44711" and "+919820044711" both normalise to the same 10 digits), so a
-- lookup never has to re-derive it and a citation never shows the normalised form as if it
-- were the original artifact text.
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
```

No foreign key is enforced (SQLite FKs off, none declared) — the relationship is
application-level only. `sync_registry()` rebuilds `cases`/`reports` from each case's
`case.json` on demand; `case_identifiers` is rebuilt per case on every acquisition and
lazily on first `GET /api/case/:id/linked-cases` request for an older case. **Deleting
`registry.db` loses no evidence** — everything in it is re-derivable from the case
folders on disk.

## Per-case folder — `cases/<case_id>/` (the real source of truth)

```
cases/<case_id>/
├── case.json          # CaseMeta — device, examiner, legal authority, pre/post state
├── audit.jsonl         # append-only, hash-chained action log (one JSON object/line)
├── manifest.json        # JSON array of ArtifactRecord — one per ingested file
├── tags.json            # on-scene bookmarks
├── artifacts/           # raw pulled files, mirrored device path
├── derived/*.json        # ~90 parsed datasets (messages, contacts, locations, timeline, …)
├── report.html            # current triage report
└── reports/                # timestamped report snapshots (history)
```

**`audit.jsonl`** — one line per action, hash-chained (`entry_hash`/`prev_hash`) via
`forensics/audit_chain.py`:
```json
{"timestamp": "2026-08-05T16:13:29Z", "action": "case.create", "detail": "Case CASE-REAL-005 opened by SNAGR Investigator", "examiner": "SNAGR Investigator", "command": "", "result": "ok", "alters_device": false, "tier": null, "extra": {}, "entry_hash": "e274513...", "prev_hash": "128d365..."}
```
Cases created before hash-chaining shipped have no `entry_hash`/`prev_hash` — `verify_chain()`
reports those as `valid: False` rather than silently trusting them.

**`manifest.json`** — one `ArtifactRecord` per ingested file:
```json
{
  "artifact_id": "a00000",
  "source_path": "/sdcard/Download/calllog.json",
  "stored_path": "artifacts/sdcard/Download/calllog.json",
  "size_bytes": 238,
  "sha256": "b3a7c757...",
  "md5": "9179cd7a...",
  "tier": "tier0",
  "method": "mock",
  "extracted_at": "2026-07-16T16:24:58Z",
  "category": "other",
  "app": null,
  "flags": []
}
```

**`derived/`** dataset names (subset — the full pipeline writes ~90): `messages`, `contacts`,
`calls`, `media`, `locations`, `location_trace_summary`, `timeline`, `recovered`,
`deletion_evidence`, `graph`, `risk`, `apps`, `accounts`, `calendar`, `wifi`, `bluetooth`,
`celltower`, `instagram_conversations`, `snapchat_conversations`, `telegram_conversations`,
`encrypted_apps`, `device_state`, `case_profile`, `collection_plan`, `ai_findings`,
`investigation_trace`, `validation_report`, … `case.read_derived(name)` returns `[]`/`{}` for a dataset that was
collected-but-empty, vs a 404 from `GET /api/case/<id>/<dataset>` for one never in scope —
that distinction is load-bearing (see [`NOTES.md`](NOTES.md#forensic-soundness-notes)).

## Two more first-party stores (JSON/JSONL, not SQL)

- **Case bank** — `engine/triage/intel/data/case_studies.jsonl`, a retrieval corpus of
  worked-case "what actually yielded evidence" studies, BM25-searched in Python.
- **Knowledge graph** — `cases/knowledge_graph.json`, one global file (not per-case): a
  Beta-posterior model over `(crime_type, artifact)` pairs, updated after every case with
  an outcome recorded.
