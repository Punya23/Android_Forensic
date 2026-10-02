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
| `GET /api/llm/fit` | Which local model this laptop can carry (`triage/intel/hardware.py`; CLI: `python -m triage.intel.hardware`). Free RAM / VRAM (Apple unified or NVIDIA), every pulled and downloadable chat model with its real footprint (weights + KV cache + overhead) and a `fits` / `tight` / `too_big` verdict, the model to use from those already pulled, the best one to download, what Ollama holds in memory now, and advice | Required |

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
| `POST /api/case/<id>/ask` | "Ask this case" — a real grep of the case's own collected evidence (messages, recovered rows, calls, browser, locations, contacts, notifications, search history, calendar). Body: `{question?, scope?: "question"|"brief", synthesize?, use_brief?, llm_provider?, top_k?}`. The question (or, with `scope:"brief"`, the case brief's `case_profile` JSON — 409 if none) becomes literal search terms, extended by the local model when available (6 s cap, literal terms only, falls back and says so); returns highlighted, cited `passages` plus a `search` block (terms, per-term hit counts, notes). `synthesize:false` returns the hits without waiting for a model-written summary. Embeddings are no longer used | Required |
| `POST /api/case/<id>/ask/stream` | Chat-style "ask this case": same body as `/ask` (no `synthesize`), response is newline-delimited JSON (`application/x-ndjson`): first `{"type":"search","bundle":{…same as /ask…}}` (the real grep matches from the literal terms, sent at once, no model involved), optionally a second `search` event when the model's extra terms arrive in time (≤10 s) and re-rank the matches, then `{"type":"token","text":…}` per model chunk, then `{"type":"done","method","note","disclaimer"}`. The model reads only the best 8 readable hits (live evidence first, text windowed around the match, binary residue stripped), output capped, one request at a time; if it is missing, busy past 20 s, slow or fails, the stream ends after the matches with a `note` — the matches are always the result | Required |
| `GET /api/case/<id>/linked-cases` | Other cases on this installation sharing a phone number/UPI ID/email with this one, indexed via `triage/registry.py`'s `case_identifiers` table | Required |
| `GET /api/nomenclature` \| `POST /api/nomenclature/check` | Controlled forensic vocabulary | Required |
| `POST /api/case/<id>/import/<app>` | Non-root import (instagram/snapchat/telegram/whatsapp export). Merges into the `messages` pool and re-derives graph / timeline / flags / risk / financial trail all-or-nothing (`triage/rebuild.py`) | Required |
| `POST /api/case/<id>/rebuild` | Re-derive graph / financial trail / risk from the case's stored messages, calls and contacts — for cases built by older engine code. Returns `{added, messages, participants, flags, upi_transactions}` | Required |

## Socket.IO (server → client only, no client-emitted events)

| Event | Payload | When |
|---|---|---|
| `progress` | `{stage, pct, detail, case_id}` | repeatedly during acquisition |
| `complete` | `{case_id, counts}` | acquisition finished |
| `failed` | `{case_id, error}` | acquisition raised |
