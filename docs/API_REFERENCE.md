# API reference

[← back to README](../README.md)

Base path `/api/*`, served by Flask + Socket.IO on `localhost:5057` only — this is a field
tool, not a networked service. Full route table:

**Auth rule:** every `/api/*` route requires header `Authorization: Bearer <token>` **except**
`/api/health`, `/api/auth/login`, and four raw-URL resource routes (`.../report`,
`.../reports/<file>`, `.../media/<artifact_id>`, `.../export/download`) — those stay public
because `<img src>`/`<iframe>`/Playwright's PDF renderer can't attach custom headers.

## Auth

| Method + path | Purpose | Auth | Body | Response |
|---|---|---|---|---|
| `POST /api/auth/login` | Authenticate, issue a bearer token | Public | `username`, `password` | `token`, `expires_in`, `username` |
| `POST /api/auth/logout` | Invalidate the current token | Required | — | `ok` |
| `GET /api/auth/me` | Confirm session / get username | Required | — | `username` |

Single examiner account from `SNAGR_AUTH_USER`/`SNAGR_AUTH_PASS`, compared with
`hmac.compare_digest`. Tokens: `secrets.token_urlsafe(32)`, in-memory, 12h TTL — restarting
the engine logs everyone out.

## Meta

| Method + path | Purpose | Auth |
|---|---|---|
| `GET /api/health` | Liveness + version + adb availability | Public |
| `GET /api/validation` | Self-test + CFTT coverage (runs fresh each call) | Required |
| `GET /api/capabilities` | The dataset catalogue with no case attached — what this build can and cannot do | Required |
| `GET /api/llm/status[?refresh=1]` | Which case-intelligence back-ends this workstation can actually use, asked of the local Ollama daemon and the engine environment. Lists the chat models pulled locally and the embedding model backing semantic retrieval | Required |

## Devices & acquisition

| Method + path | Purpose | Auth |
|---|---|---|
| `GET /api/devices` | List connected real devices + mock corpus fixtures | Required |
| `POST /api/acquire` | Start a background acquisition (409 if one's already running) | Required |

## Case CRUD & datasets

| Method + path | Purpose | Auth |
|---|---|---|
| `GET /api/cases` | Lightweight case list | Required |
| `GET /api/case/<id>` | Case overview (counts, risk, throughput, graph stats) | Required |
| `DELETE /api/case/<id>` | Irreversibly delete a case | Required |
| `GET /api/case/<id>/capabilities` | Per-dataset state for this case: `populated` / `empty` / `not_collected` / `inaccessible` / `planned`, each with its reason, the acquisition flag that gates it, and `flag_actionable` — whether re-running with that flag on would actually change the outcome. `flag` is always the gating flag so the reason can name it; `flag_actionable` is what says it may be offered as the fix, and it is false where the gap closes some other way (a case brief, an account-data export import) or cannot close at all. Registered ahead of the generic dataset route | Required |
| `GET /api/case/<id>/<dataset>` | One of ~90 derived datasets by name | Required |
| `GET /api/case/<id>/manifest` | Chain-of-custody artifact manifest | Required |
| `GET /api/case/<id>/audit` | Audit/action log | Required |
| `GET /api/case/<id>/telegram/conversations[/<chat_id>]` | Threaded Telegram view | Required |
| `GET /api/case/<id>/whatsapp_backup/{messages,media,summary}` | WhatsApp backup sub-views | Required |

## Registry, tags, media, report, export

| Method + path | Purpose | Auth |
|---|---|---|
| `GET /api/registry/cases` \| `/api/registry/stats` | Cross-case searchable history | Required |
| `GET /api/case/<id>/reports` | Report generation history | Required |
| `GET /api/case/<id>/reports/<file>` | One historical report snapshot | **Public** |
| `GET/POST/DELETE /api/case/<id>/tags[/<tag_id>]` | Artifact tagging | Required |
| `GET /api/case/<id>/media/<artifact_id>` | Raw media bytes | **Public** |
| `GET /api/case/<id>/report` | Current report HTML | **Public** |
| `POST /api/case/<id>/report/regenerate` | Rebuild report + snapshot | Required |
| `POST /api/case/<id>/export` | Build export archive, return path | Required |
| `GET /api/case/<id>/export/download` | Build (if needed) + stream download | **Public** |

## Case intelligence / case bank / knowledge graph

| Method + path | Purpose | Auth |
|---|---|---|
| `POST /api/plan` | Preview a collection plan from a case brief. The response carries `retrieval_mode` (`hybrid` / `lexical`) and an `embedding` block, so a reader can tell a semantic ranking from a keyword one | Required |
| `GET/POST /api/casebank` | List/search/add retrieval-corpus case studies. A `?q=` search runs hybrid retrieval when a local embedding model is available and reports `retrieval_mode` either way | Required |
| `GET /api/knowledge-graph?crime_type=` | Learned artifact-priors graph | Required |
| `POST /api/case/<id>/outcome` | Record examiner-confirmed outcomes | Required |
| `POST /api/case/<id>/analyze` | Run/re-run AI case analysis | Required |
| `POST /api/case/<id>/investigate` | Run/re-run deep investigation — a bounded, deterministic multi-hypothesis pass cross-linking findings `analyze` scored independently (`triage/intel/investigator.py`). Requires a case profile from `/analyze` first | Required |
| `POST /api/case/<id>/ask` | "Ask this case" — free-text Q&A over the case's own already-collected evidence. Body: `{question, llm_provider?, top_k?, use_embeddings?}`. Retrieval always runs; synthesis only when a model is configured, grounded strictly to the retrieved passages (`triage/intel/case_qa.py`) | Required |
| `GET /api/case/<id>/linked-cases` | Other cases on this installation sharing a phone number/UPI ID/email with this one, indexed via `triage/registry.py`'s `case_identifiers` table | Required |
| `GET /api/nomenclature` \| `POST /api/nomenclature/check` | Controlled forensic vocabulary | Required |
| `POST /api/case/<id>/import/<app>` | Non-root import (instagram/snapchat/telegram export) | Required |

## Socket.IO (server → client only, no client-emitted events)

| Event | Payload | When |
|---|---|---|
| `progress` | `{stage, pct, detail, case_id}` | repeatedly during acquisition |
| `complete` | `{case_id, counts}` | acquisition finished |
| `failed` | `{case_id, error}` | acquisition raised |
