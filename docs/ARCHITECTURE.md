# Architecture

[← back to README](../README.md)

## End-to-end workflow

The full path from "plug in a phone" to "sealed, court-formatted evidence package":

```mermaid
sequenceDiagram
    participant U as Examiner
    participant D as Dashboard (Electron/React)
    participant E as Engine (Flask, :5057)
    participant W as Acquisition worker (thread)
    participant Dev as Android device
    participant CS as Case store (cases/CASE_ID/)

    U->>D: Sign in (username + password)
    D->>E: POST /api/auth/login
    E-->>D: bearer token (12h session)

    U->>D: Connect device, write case brief, pick tiers
    D->>E: POST /api/acquire (case_id, examiner, tiers, case_description)
    E->>W: spawn background worker
    E-->>D: 200 {case_id, started: true}

    W->>Dev: adb pull shared storage + dumpsys (Tier 0 — always)
    opt Tier 1 (opt-in)
        W->>Dev: sideload Collector APK, pm grant, dump_all, uninstall
    end
    opt Tier 2 (opt-in, root)
        W->>Dev: su cp app-private DBs (WhatsApp/Telegram/Instagram/Snapchat)
    end
    Dev-->>W: raw files

    loop every stage
        W->>CS: SHA-256 + hash-chained audit.jsonl append
        W-->>D: socket.io "progress" {stage, pct, detail, case_id}
    end

    W->>W: parse live rows + recover deleted rows (confidence-badged)
    W->>W: case-intelligence analysis (ranked, cited leads)
    W->>CS: write derived/*.json (~90 dataset files)
    W->>CS: render report.html (NIST/SWGDE + BSA s.63 certificate)
    W-->>D: socket.io "complete" {case_id, counts}

    U->>D: Browse case views, tag artifacts, search
    D->>E: GET /api/case/:id/:dataset (bearer token required)
    D->>E: GET /api/case/:id/report (public route — img-src/iframe can't send headers)
    E-->>D: JSON / report HTML

    U->>D: Download sealed export
    D->>E: GET /api/case/:id/export/download
    E-->>D: ZIP + SHA-256 manifest
```

Escalating invasiveness is the core abstraction — every artifact is tagged with the tier
that produced it, and Tier 1/2 are opt-in with every skip logged with a reason:

```mermaid
flowchart LR
    T0["Tier 0<br/>zero device-state change<br/>adb pull + dumpsys<br/>ALWAYS ON"]
    T1["Tier 1<br/>non-root, state-changing<br/>sideloaded Collector APK<br/>OPT-IN, logged"]
    T2["Tier 2<br/>root required<br/>su cp of app-private DBs<br/>OPT-IN, logged"]
    T0 --> T1 --> T2
    style T0 fill:#1c7d3f,color:#fff,stroke:#0d4a24
    style T1 fill:#a6741a,color:#fff,stroke:#5c400e
    style T2 fill:#a5322f,color:#fff,stroke:#5c1c19
```

## System architecture

```mermaid
flowchart LR
    subgraph DEV["Android device"]
        direction TB
        A["Shared storage<br/>DCIM / Download / WhatsApp media"]
        B["dumpsys<br/>location / wifi / bluetooth / telephony"]
        C["Collector APK<br/>sideloaded — Tier 1"]
        Dd["App-private DBs<br/>root only — Tier 2"]
    end

    subgraph ENG["Python Engine — Flask + Socket.IO, :5057"]
        direction TB
        E1["Acquisition<br/>adbutils orchestration"]
        E2["Parsers<br/>38 modules — WhatsApp/Telegram/IG/Snap/SMS/browser/…"]
        E3["Recovery<br/>WAL / freelist / freeblock / rollback-journal carving"]
        E4["Case Intelligence<br/>ontology + hybrid RAG (BM25 + local embeddings)<br/>pluggable LLM (heuristic/Ollama/Anthropic)"]
        E5["Custody<br/>hash-chained audit log + SHA-256 manifest"]
        E6["Report Engine<br/>HTML/PDF + BSA s.63 certificate"]
    end

    subgraph DASH["Electron + React Dashboard, :5173"]
        direction TB
        F1["Login + onboarding"]
        F2["~90 dataset views"]
        F3["Report viewer / PDF export"]
    end

    subgraph STORE["Case store — cases/CASE_ID/"]
        direction TB
        G1[("artifacts/<br/>raw pulled files")]
        G2[("derived/*.json<br/>~90 datasets")]
        G3[("audit.jsonl<br/>hash-chained")]
        G4[("registry.db<br/>SQLite — cross-case index")]
    end

    A -->|adb pull| E1
    B -->|adb shell| E1
    C -->|install + pull + uninstall| E1
    Dd -->|su cp + pull| E1

    E1 --> E2 --> E3 --> E4
    E2 --> G2
    E3 --> G2
    E4 --> G2
    E1 -->|SHA-256| G1
    E5 --> G3
    E6 --> F3

    E1 <-->|REST + WebSocket| F1
    F2 -->|GET /api/case/:id/:dataset| G2
    F3 -->|GET /api/case/:id/report| E6

    G2 -.rebuilds.-> G4
```

**Why this stack:**
- **Electron + React/TS, not Next.js** — a native desktop tool needs direct USB/ADB access,
  must run fully offline in an interrogation room, and packages as a standalone executable.
- **Python/Flask as the engine, not Node/Go** — every forensic library that matters here
  (ALEAPP, SQLite carving, `adbutils`) is Python or trivially subprocess-callable from it.
- **Per-case SQLite/JSON on disk, no server database** — the tool has to work with zero
  network dependency at a crime scene. `registry.db` is a rebuildable cache, not the source
  of truth — the case folder is. Full schema: [`docs/DATABASE.md`](DATABASE.md).

## Project folder structure

```
Digital forensic tool/
├── README.md                      # top-level pitch + quick start
├── run.sh                         # one-command demo launcher (venv + corpus + engine + dashboard)
├── DEMO_CHECKLIST.md / ps02_team_roadmap.md
├── docs/
│   ├── IMPLEMENTATION_PLAN.md     # 30-day plan + feasibility research
│   ├── CAPABILITIES.md / ARCHITECTURE.md / API_REFERENCE.md / DATABASE.md / SETUP.md / NOTES.md
│
├── engine/                        # Python forensic engine (the core)
│   ├── requirements.txt
│   ├── .env.example                # copy → .env for SNAGR_AUTH_USER/PASS
│   ├── docs/PRODUCTION_READINESS.md
│   ├── tools/                      # make_corpus.py + corpus_shell.py (canned dumpsys) + 4 more
│   ├── tests/                      # pytest suite — 1007 tests
│   ├── security/ analytics/ integration/ advanced_forensics/   # ⚠ present, fabrication stubs — see NOTES.md
│   └── triage/                     # the actual engine package
│       ├── server.py               # Flask + Socket.IO app, all API routes
│       ├── pipeline.py             # stage orchestration — run_acquisition()
│       ├── capabilities.py         # per-dataset state: populated/empty/not_collected/inaccessible/planned
│       ├── custody.py              # Case class — chain-of-custody, audit, manifest
│       ├── registry.py             # cross-case SQLite registry
│       ├── config.py / models.py / cli.py / adb.py / hashing.py
│       ├── acquire/                # base.py, mock.py, real.py — acquisition backends
│       ├── parsers/                # 38 files — whatsapp_db.py, telegram.py, instagram.py, snapchat.py, sms.py, wifi.py, exif.py …
│       ├── recovery/                # deep_sqlite.py, sqbrite.py, sqlite_recovery.py
│       ├── forensics/               # 48 files — audit_chain.py, bsa_certificate.py, comm_graph.py, hash_verification.py …
│       ├── intel/                   # llm.py, embeddings.py, planner.py, ontology.py, knowledge_graph.py, casebank.py,
│       │                           # investigator.py (deep investigation), case_qa.py (ask-this-case) + data/case_studies.jsonl
│       ├── report/                  # html_report.py, exporter_pdf.py, exporter_word.py, exporter_csv.py
│       ├── validation/              # cftt.py, harness.py, swgde.py — self-validation harness
│       └── notifications/           # email/SMS/Slack/Teams — real, wired to run_acquisition() (opt-in)
│
├── app/                            # Electron + React + TypeScript + Tailwind dashboard
│   ├── package.json / vite.config.ts / tailwind.config.js
│   ├── electron/                    # main.cjs, preload.cjs, pdf/pdfRenderer.cjs (Playwright)
│   └── src/
│       ├── App.tsx / main.tsx / index.css
│       ├── components/              # Sidebar.tsx, ChatView.tsx, GlobalSearch.tsx, RiskCard.tsx …
│       ├── lib/                     # api.ts (fetch client + auth), capabilities.tsx, hooks.ts, types.ts, tagStore.tsx
│       └── views/                   # 48 files — one per dataset (Overview, Messages, Locations, Report, Login, Onboarding,
│                                   # AskTheCase …)
│
└── apk/                             # Kotlin "Collector" Tier-1 helper APK
    ├── build.gradle / settings.gradle / gradlew
    ├── gradle/wrapper/gradle-wrapper.properties   # Gradle 8.9
    └── app/
        ├── build.gradle             # compileSdk 34 · minSdk 26 · targetSdk 34
        └── src/main/java/io/erakshak/collector/   # package id unchanged — see NOTES.md
            ├── MainActivity.kt
            ├── CommsCollectors.kt / MediaCollector.kt / LocationCollectors.kt
            ├── NotificationCollector.kt / SystemCollectors.kt / StorageWriter.kt
            └── KnownApps.kt / CollectionResult.kt
```

> **Path gotcha:** the repo root directory name ends with a literal space
> (`Digital forensic tool `, space before the closing quote). Always quote it in shell
> commands: `cd "/path/to/Digital forensic tool "`.
