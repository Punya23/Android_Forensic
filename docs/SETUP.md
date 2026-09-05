# Setup, dependencies & deployment

[← back to README](../README.md)

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.x (built against 3.14 locally; not hard-pinned) | `engine/requirements.txt` has no version floor beyond package minimums |
| Node.js | ≥ 18 (20 LTS recommended) | not pinned in `package.json`; inferred from Vite 5 / Electron 31 |
| Android SDK / `adb` | any recent platform-tools | only needed for a **real device**; the mock corpus needs neither |
| Android Studio (bundled JDK 21) | required only to build the APK | the *system* JDK is too new for Gradle 8.9 — see below |

## 1 — Engine (Python)

```bash
cd "Digital forensic tool/engine"
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# credentials — copy the template and set real values (defaults to examiner/snagr
# with a startup warning if you skip this)
cp .env.example .env
# edit .env: SNAGR_AUTH_USER=..., SNAGR_AUTH_PASS=...

# generate a synthetic seized-device corpus (WhatsApp chat, deleted-message DBs,
# GPS photos, a trashed photo, contacts/calls JSON) — no phone required
python tools/make_corpus.py _corpus/device_A

# start the API the dashboard talks to
python -m triage.server --port 5057
```

Or run a full acquisition straight from the CLI, no dashboard:
```bash
python -m triage.cli acquire --mock _corpus/device_A --case CASE-001 \
    --examiner "Insp. R. Sharma" --authority "Search Warrant #MH-2026-4471"
open cases/CASE-001/report.html
```

## 2 — Dashboard (Electron + React)

```bash
cd "Digital forensic tool/app"
npm install
npm run dev            # http://localhost:5173  (browser)
# or the full desktop app:
npm run electron:dev   # launches Electron, auto-starts the engine
```

Sign in with the credentials from `engine/.env`, click through the one-time onboarding
screen, pick the **SM-G991B (Galaxy S21)** mock corpus on the Acquisition view, and click
**Begin Acquisition**. You'll see the live progress bar, then the populated Overview,
Messages, Recovered/Deleted, Media, Locations, Timeline, Chain-of-Custody, and Report views.

## 3 — One-command demo (both, from scratch)

```bash
./run.sh          # sets up venv, installs deps, builds corpus, starts engine + dashboard (Vite only, not Electron)
```

## 4 — Using a real device (Tier 0 / Tier 1)

1. Enable **USB debugging** on the (unlocked, consenting) device and authorise the
   workstation's RSA key when prompted. This step needs a finger on the device's own
   screen — Android will not let *any* tool, this one included, do it from the
   workstation side, on any brand. If you're not sure what to tap, or the device is a
   brand you haven't seen before:
   ```bash
   python -m triage.cli check-device --brand xiaomi   # or oppo, oneplus, vivo, samsung, honor, huawei…
   ```
   prints the exact ADB connection state (no device / unauthorized / offline / ready)
   plus that brand's Developer-Options checklist — see [`triage/preflight.py`](../engine/triage/preflight.py)
   and the "Developer Options / USB debugging, brand by brand" section of
   [`apk/README.md`](../apk/README.md#developer-options--usb-debugging-brand-by-brand)
   for the full per-OEM detail.
2. `cd engine && source .venv/bin/activate`
3. `python -m triage.cli devices` to confirm it's detected.
4. `python -m triage.cli acquire --serial <SERIAL> --case CASE-002 --examiner "..."`

The same acquisition also runs from the dashboard's device picker. **Tier 1** (contacts /
calls / SMS / full `dump_all`) uses the sideloaded helper in [`apk/`](../apk/README.md); enable
it from the Acquisition view's tier checkboxes — every step (install → grant → dump →
uninstall) is logged with `alters_device: true`.

## 5 — Building the Collector APK (only needed to modify the Tier-1 helper)

```bash
cd apk
JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home" \
  ANDROID_HOME="$HOME/Library/Android/sdk" ./gradlew :app:assembleDebug --offline
```
Output: `apk/app/build/outputs/apk/debug/app-debug.apk`.

> **JDK gotcha:** the system JDK is typically newer than Gradle 8.9 can parse
> (`Unsupported class file major version 70`-style errors). Build with **Android Studio's
> bundled JDK 21** as shown above, not whatever `java -version` resolves to on PATH.

---

## Dependencies

### Python (`engine/requirements.txt`)

```
flask>=3.0                # HTTP API
flask-cors>=4.0
flask-socketio>=5.3       # live acquisition progress
simple-websocket>=1.0
python-dotenv>=1.0.0      # loads engine/.env

adbutils>=2.5.0           # ADB orchestration

Pillow>=10.0               # image + EXIF
piexif>=1.1.3              # robust GPS EXIF decode

Jinja2>=3.1
reportlab>=4.0.0           # report PDF fallback
python-docx>=1.0.0
pandas>=2.0.0

twilio>=8.0.0               # ⚠ notifications module unwired — see NOTES.md
slack_sdk>=3.0.0            # ⚠ same
pydantic>=2.0.0

pytest>=8.0                 # dev/test
```

No SQLAlchemy, no `psycopg2`/`pymysql`, no ORM of any kind — persistence is stdlib
`sqlite3` plus flat JSON/JSONL (see [`DATABASE.md`](DATABASE.md)).

### Dashboard (`app/package.json`)

| dependency | version | | devDependency | version |
|---|---|---|---|---|
| react / react-dom | ^18.3.1 | | typescript | ^5.5.3 |
| electron | ^31.0.0 | | vite | ^5.3.4 |
| socket.io-client | ^4.7.5 | | @vitejs/plugin-react | ^4.3.1 |
| leaflet / react-leaflet | ^1.9.4 / ^4.2.1 | | tailwindcss | ^3.4.6 |
| leaflet.markercluster | ^1.5.3 | | concurrently | ^8.2.2 |
| playwright | ^1.61.1 (PDF export) | | wait-on | ^7.2.0 |

No `"engines"` field is declared and there's no `.nvmrc` — Node ≥ 18 is inferred from
Vite 5 / Electron 31's own requirements, not enforced in-repo.

### Android (`apk/`)

- **AGP** 8.2.0 · **Kotlin** 1.9.22 · **Gradle** 8.9 (wrapper-managed)
- `compileSdk 34` · `minSdk 26` · `targetSdk 34` · Java/Kotlin target: **17**
- Runtime dependency: `androidx.core:core-ktx:1.12.0` only — JSON parsing uses the
  platform-bundled `org.json`, no extra libraries.

---

## Deployment

### Dev (what actually works today)

| Component | Command | Port |
|---|---|---|
| Engine | `python -m triage.server --port 5057` | 5057 |
| Dashboard (browser) | `npm run dev` | 5173 (proxies `/api`, `/socket.io` → 5057) |
| Dashboard (desktop) | `npm run electron:dev` | spawns the engine, loads `localhost:5173` |
| Both at once | `./run.sh` | (Vite only, not Electron) |

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `SNAGR_AUTH_USER` / `SNAGR_AUTH_PASS` | `examiner` / `snagr` | Sign-in credentials — set real values in `engine/.env` before real evidence |
| `SNAGR_LLM` | `heuristic` | Case-intelligence backend: `heuristic` (offline, default) / `ollama` |
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | Local LLM endpoint, if `SNAGR_LLM=ollama` |
| `SNAGR_LLM_MODEL` | `llama3.1` | Ollama model name |
| `SNAGR_EMBED_MODEL` | `nomic-embed-text` | Local embedding model used for semantic precedent retrieval. Pull it with `ollama pull nomic-embed-text` |
| `SNAGR_EMBEDDINGS` | *(on)* | Set to `off` to force pure BM25 retrieval — for an air-gapped box, or to reproduce a plan exactly as a lexical-only run produced it |
| `ANDROID_HOME` | — | Android SDK location, for locating `adb` |
| `ALEAPP_PATH` | — | Path to an external ALEAPP install, if used |
| `SIGNALBACKUP_TOOLS_PATH` | — | Path to `signalbackup-tools`, if used |

### Optional: local models (Ollama)

Everything works with no model installed — the default `heuristic` back-end is pure
regex/ontology and precedent retrieval falls back to BM25. Installing Ollama upgrades two
independent things, and neither sends case text anywhere:

```bash
ollama pull llama3.1:8b        # case-brief understanding: crime type, parties, roles
ollama pull nomic-embed-text   # semantic precedent retrieval (hybrid with BM25)
```

Then pick **Ollama (local model)** in the acquisition screen's AI back-end selector — it
lists the models actually pulled on the workstation and disables back-ends it cannot
reach, with the reason. Check what the engine sees with `GET /api/llm/status`.

If either model is missing the run still completes: the plan degrades to the
deterministic path, says so in the audit log and on screen, and records
`retrieval_mode: lexical` so nobody later reads the plan as having had a basis it did
not have.

### Packaging (`npm run electron:build`) — ⚠ untuned

`app/package.json` has **no `"build"` config block** for electron-builder, and no
`electron-builder.yml` exists — so `electron:build` currently runs on electron-builder's
bare defaults (autodetected target per OS: dmg/nsis/AppImage), with no `productName`,
`appId`, or output directory pinned. More importantly: `electron/main.cjs` expects a
packaged build to find a standalone `triage-engine` executable under
`resources/engine/triage-engine` — **no build step in this repo produces that binary**
(no PyInstaller spec wired to the packaging script, despite `build_package.py` existing at
the repo root). Packaging the desktop app today needs this gap closed first.

### APK release build — ⚠ unsigned

`./gradlew :app:assembleDebug` works today (see above). A release build
(`./gradlew :app:assembleRelease`) would run, but `apk/app/build.gradle`'s `release` block
only sets `minifyEnabled false` — **no `signingConfigs`** — so the output APK would be
unsigned and need manual signing (`apksigner` + a keystore) before it could be installed
outside a debug context.

### What's *not* a real deployment path

`deploy/docker-compose.yml` exists but references `deploy/Dockerfile.gateway`, which does
not exist anywhere in the repo, and describes an `api_gateway` / `graphql_server` /
`webhook_worker` + Redis architecture that doesn't correspond to anything else in this
project (engine on 5057, dashboard, APK). Treat it as orphaned scaffolding, not a working
deployment — `docker compose up` would fail immediately on the missing Dockerfile. No
`.github/workflows/` exists either — there is currently no CI/CD.

---

## Tests

```bash
cd engine && source .venv/bin/activate && python -m pytest tests/ -q
```

**1007 tests**, covering deleted-record recovery (freelist / freeblock / gap detection /
WAL / corrupt-DB safety), every parser, the auth gate, and the full end-to-end pipeline.
