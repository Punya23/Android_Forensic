# eRakshak — Android Rapid Evidence Triage & Forensic Preview Tool

**Problem statement:** ERH26_PS_02 (Digital Forensics)

A field-deployable, forensically-minded tool that gives an on-scene officer a readable
**preview** of a connected Android device's high-value evidence — messages, recovered
deleted chats, contacts, calls, media, and locations — in minutes, in a
**minimally-invasive, fully-logged** manner that preserves chain of custody and does not
prejudice the later full laboratory examination.

> **Design honesty first.** This tool never claims "read-only" acquisition — SWGDE 18-F-003
> is explicit that no write-blocking exists for mobile devices. Instead it logs *every*
> device interaction to an append-only audit trail and labels every recovered row with its
> confidence and byte-level provenance. See [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md).

---

## What it does (verified, working)

| Capability | Status | Comparable commercial feature |
|---|---|---|
| Tier-0 acquisition (adb pull of shared storage, dumpsys location) | ✅ | UFED logical extraction |
| Acquisition throughput metric (MB/min) | ✅ | MDI "up to 4GB/min" |
| Manual screen capture (read-only framebuffer) | ✅ | Oxygen/MDI screenshot mode |
| Per-artifact SHA-256 hashing + append-only JSONL audit trail | ✅ | UFED chain-of-custody |
| **SQLite deleted-record recovery** (freelist / freeblock / unallocated / WAL carving) | ✅ | MDI/Cellebrite deleted-chat recovery |
| Multi-app messages: WhatsApp export, Telegram/app-DB, SMS | ✅ | Deleted chat from WhatsApp/Telegram/Signal |
| Browser history (Chromium) + deleted-URL recovery | ✅ | — |
| 4-tier confidence labelling (Live / Recovered-Verified / Carved-Partial / Deletion-Detected) | ✅ | carved-data flagging |
| **Communication social graph** (link analysis across channels) | ✅ | Oxygen social-graph visualization |
| **Traffic-light triage verdict** (RED/AMBER/GREEN + scorecard) | ✅ | Cyacomb traffic-light interface |
| **On-scene artifact tagging / bookmarking** | ✅ | MDI on-scene tagging |
| **Global cross-artifact search** | ✅ | search-everything |
| EXIF GPS + dumpsys last-known-location (offline map) | ✅ | location mapping |
| Contacts / call-log parsing (Tier-1 helper APK output) | ✅ | agent-based logical extraction |
| Keyword + known-hash flagging | ✅ | Cyacomb known-content detection |
| Cross-artifact timeline reconstruction | ✅ | timeline view |
| Forensic Preview Dashboard (Electron + React) with live 5–10 min progress | ✅ | MDI field dashboard |
| NIST/SWGDE-aligned HTML report + Section 65B certificate block | ✅ | court-ready reporting |
| **Sealed evidence-package export** (ZIP + SHA-256 verification manifest) | ✅ | evidence export |
| Runs **with no phone** via a synthetic mock corpus (dev + demo fallback) | ✅ | — |

### New in v0.2 — expanded collection & app coverage

| Capability | Tier | Comparable commercial feature |
|---|---|---|
| **Collector APK `dump_all`** — one action captures everything below, then self-uninstalls | 1 (non-root) | Oxygen Android Agent |
| **MediaStore inventory** — every media file's metadata (size, `date_taken`, owner app, dimensions) without pulling the files | 1 | media catalogue before selective pull |
| **Trashed / favorite media detection** (`IS_TRASHED` / `IS_FAVORITE`) + EXIF GPS via `ACCESS_MEDIA_LOCATION` | 1 | recycle-bin / geotag surfacing |
| **Installed-app inventory** with investigative classification (messaging / crypto / dating / browser) | 1 | UFED installed applications |
| **Vault / anti-forensic app detection** (AppLock, Calculator Vault, hiders — table + name heuristic) | 1 | AXIOM "potentially unwanted apps" |
| **Accounts** (Google / WhatsApp / Telegram / Snapchat identities via AccountManager) | 1 | User Accounts |
| **Calendar events**, **app-usage telemetry** (foreground time, last-used) | 1 | Organizer / app usage |
| **Instagram Direct recovery** — `direct.db` live + deleted DMs, µs timestamps, identity from shared_prefs, + DYI-export ingest | 2 (root/image) | Instagram `direct.db` decode |
| **Snapchat recovery** — `arroyo.db` `conversation_message` (schema-less protobuf), identity from `main.db` `Friend`, WAL/freelist ephemeral carve | 2 (root/image) | arroyo.db decode |
| **Dynamic App Finder** — auto-classifies chat tables in *unknown* app SQLite DBs (sender/text/time columns) | 0–2 | Cellebrite App Genie / Magnet Dynamic App Finder |
| Anti-forensic-app + trashed-media signals fold into the **traffic-light risk verdict** | — | insights |

All Tier-1 collection is driven by the (now much larger) `apk/` Collector helper; all Tier-2 app
recovery mirrors the Telegram module (root `su`-copy of the app-private DB, then standard SQLite
forensic recovery with confidence badges — **no app encryption is bypassed**). Every new dataset
has its own dashboard view and a report section. Everything demos with **no phone and no root**
against the synthetic corpus (`tools/make_corpus.py` seeds Instagram/Snapchat DBs + Collector JSON).

Where we deliberately **don't** claim parity (and say so, honestly): cloud extraction,
lock-screen bypass, physical/chip-off imaging, and defeating Signal's hardware-backed
Keystore — none are achievable non-root in scope. See `docs/IMPLEMENTATION_PLAN.md` §0.

---

## Repository layout

```
engine/      Python forensic engine (the core) — acquisition, recovery, parsing, report, Flask API
  triage/          the package
  tools/           make_corpus.py — synthetic mock-device generator
  tests/           pytest suite (14 tests)
app/         Electron + React + TypeScript + Tailwind dashboard
apk/         Kotlin "Collector" Tier-1 helper APK (contacts / call-log / SMS via ContentProviders)
docs/        IMPLEMENTATION_PLAN.md — the 30-day plan + feasibility research
```

---

## Quick start (no phone required)

Everything runs against a **synthetic mock device** so you can see the full pipeline
without hardware. Three terminals (or use the helper script below).

### 1. Engine

```bash
cd engine
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# generate a synthetic seized-device corpus (WhatsApp chat, deleted-message DBs,
# GPS photos, a trashed photo, contacts/calls JSON)
python tools/make_corpus.py _corpus/device_A

# option A — run a full acquisition from the CLI and open the report
python -m triage.cli acquire --mock _corpus/device_A --case CASE-001 \
    --examiner "Insp. R. Sharma" --authority "Search Warrant #MH-2026-4471"
open cases/CASE-001/report.html

# option B — start the API the dashboard talks to
python -m triage.server --port 5057
```

### 2. Dashboard

```bash
cd app
npm install
npm run dev            # http://localhost:5173  (browser)
# or the full desktop app:
npm run electron:dev   # launches Electron, auto-starts the engine
```

Open the dashboard, pick the **SM-G991B (Galaxy S21)** mock corpus, fill in an examiner
name, and click **Begin Acquisition**. You'll see the live progress bar, then the populated
Overview, Messages (with recovered/deleted rows badged), Recovered/Deleted, Media, Locations,
Timeline, Chain-of-Custody, and Report views.

### One-command demo

```bash
./run.sh          # sets up venv, builds corpus, runs acquisition, starts engine + dashboard
```

---

## Using a real device (Tier 0)

1. Enable **USB debugging** on the (unlocked, consenting) device and authorise the
   workstation's RSA key when prompted.
2. `cd engine && source .venv/bin/activate`
3. `python -m triage.cli devices` to confirm it's detected.
4. `python -m triage.cli acquire --serial <SERIAL> --case CASE-002 --examiner "..."`

The same acquisition also runs from the dashboard's device picker.

**Tier 1 (contacts / calls)** uses the sideloaded helper in [`apk/`](apk/README.md); the
engine ingests its `contacts.json` / `calllog.json` output automatically.
From the Acquisition view, enable **Run Tier-1 helper contacts capture** on real devices
to execute the helper flow (install → grant READ_CONTACTS → dump contacts → uninstall)
with every step logged in the audit trail.

---

## Tests

```bash
cd engine && source .venv/bin/activate && python -m pytest tests/ -q
```

14 tests cover deleted-record recovery (freelist / freeblock / gap detection / WAL /
corrupt-DB safety), the parsers, and the full end-to-end pipeline.

---

## Forensic soundness notes

- **No "read-only" claim.** Acquisition is described as *minimally-invasive, fully-logged*.
  Every adb/pm/cmd invocation is written to `cases/<id>/audit.jsonl` with `alters_device`.
- **Per-file SHA-256**, computed at extraction time — never a whole-device hash
  (irreproducible on a live device per NIST SP 800-101r1 §3.4).
- **Confidence tiers.** Live data and carved data are never shown with the same weight;
  carved rows carry `source file · page · offset` provenance for independent hex-level
  verification.
- **Triage disclaimer** is stamped on every report — this is a preview, not a full
  examination.

See [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md) for the full feasibility
matrix (what is and isn't achievable non-root for WhatsApp/Signal/Telegram) and the 30-day
plan this build follows.

---

## WhatsApp Recovery Module

The engine includes a multi-layer WhatsApp forensic recovery pipeline covering live data,
deleted records, encrypted backups, and media cataloguing.

### Features

#### Data Extraction
| Feature | Parser | Details |
|---|---|---|
| WhatsApp export (`.txt`, `.zip`) | `whatsapp_txt.py` | Bracket & dash formats; multi-locale timestamps |
| WhatsApp live DB | `whatsapp_db.py` | Schema-aware; version-tolerant JOIN query |
| WhatsApp encrypted backups | `whatsapp_batch.py` | crypt15 (AES-GCM) / crypt14/12 (AES-CBC) |
| Batch + parallel processing | `whatsapp_batch.py` | `ThreadPoolExecutor`; sequential fallback |
| WhatsApp Media folder | `media.py` | Images, Video, Audio, Documents, GIFs, Stickers |

#### Data Recovery
| Technique | Module | Confidence |
|---|---|---|
| SQLite freelist / freeblock carving | `recovery/` | `CARVED_PARTIAL` |
| WAL frame reconstruction | `whatsapp_e2e.py` | `RECOVERED_VERIFIED` |
| Freeblock chain walking | `whatsapp_e2e.py` | `CARVED_PARTIAL` |
| Rowid gap detection (deletion proof) | `recovery/` | `DELETION_DETECTED` |
| Encrypted backup decryption (key required) | `whatsapp_e2e.py` | `RECOVERED_VERIFIED` |
| Metadata extraction (no key needed) | `whatsapp_e2e.py` | `DELETION_DETECTED` |

#### Confidence Badging
Every recovered row is labelled with one of four confidence levels — never shown with the
same visual weight as live data:

| Badge | Value | Meaning |
|---|---|---|
| 🟢 Live | `Confidence.LIVE` | Normal query result from an intact table |
| 🟡 Recovered | `Confidence.RECOVERED_VERIFIED` | Intact WAL frame or un-checkpointed page |
| 🟠 Carved | `Confidence.CARVED_PARTIAL` | Signature-matched over freeblock/unallocated space |
| 🔴 Deletion | `Confidence.DELETION_DETECTED` | Rowid gap proves deletion; no content recovered |

#### Advanced Analysis
- **Social graph** — link analysis across all message channels
- **Burst detection** — identifies rapid-fire message clusters
- **Response time analysis** — fast / slow response pattern detection
- **Anomaly detection** — z-score volume spikes, quiet-hours activity, channel switching
- **Timeline reconstruction** — chronological activity heatmap

---

### Usage Examples

#### Command Line

```bash
# Full triage with E2E recovery and advanced analysis enabled
python -m triage.cli acquire --mock _corpus/device_A --case CASE-001 \
    --examiner "Insp. R. Sharma" --authority "Search Warrant #MH-2026-4471"

# View WhatsApp media summary only
python -c "
from pathlib import Path
from triage.parsers.media import get_whatsapp_media_summary
s = get_whatsapp_media_summary(Path('_corpus/device_A/WhatsApp/Media'))
print(s)
"
```

#### Python API

```python
from pathlib import Path
from triage.parsers import (
    parse_whatsapp_export, parse_whatsapp_db,
    parse_whatsapp_media_folder, get_whatsapp_media_summary,
    filter_media_by_date, get_media_by_type,
    recover_e2e_messages, simulate_e2e_decryption_workflow,
)
from triage.parsers.whatsapp_batch import (
    parse_whatsapp_batch, parse_whatsapp_directory, get_batch_stats,
)
from triage.advanced import AdvancedForensicFeatures, run_advanced_analysis

# -- Live messages --
msgs = parse_whatsapp_db(Path("msgstore.db"))

# -- Encrypted backup (key required) --
with open("/data/data/com.whatsapp/files/key", "rb") as f:
    key = f.read()
e2e_msgs = recover_e2e_messages(Path("msgstore.db.crypt15"), key_material=key)

# -- WAL + freeblock recovery (no key) --
report = simulate_e2e_decryption_workflow(Path("msgstore.db.crypt15"))
print(report["summary"])

# -- Batch directory parse --
all_msgs = parse_whatsapp_directory(Path("evidence/"), recursive=True)
stats = get_batch_stats(all_msgs)

# -- Media cataloguing --
media_items = parse_whatsapp_media_folder(Path("WhatsApp/Media"))
summary = get_whatsapp_media_summary(Path("WhatsApp/Media"))
images = get_media_by_type(media_items, "image")
recent = filter_media_by_date(media_items, "2024-01-01", "2024-12-31")

# -- Advanced analysis --
aff = AdvancedForensicFeatures()
graph  = aff.analyze_social_graph(msgs)
patterns = aff.detect_communication_patterns(msgs)
anomalies = aff.detect_anomalies(msgs)
report = run_advanced_analysis(Path("cases/CASE-001"), msgs)
```

---

### Supported Formats

#### Timestamp Formats (WhatsApp export)
| Format | Example |
|---|---|
| Bracket + 24h | `[06/07/2026, 21:00:04] Sender: body` |
| Dash + 24h | `06/07/2026, 21:00 - Sender: body` |
| Bracket + 12h AM/PM | `[06/07/2026, 9:00:04 PM] Sender: body` |
| European (dot separator) | `06.07.2026, 21:00:04 - Sender: body` |
| US locale | `07/06/2026, 9:00 PM - Sender: body` |

#### Media Types
| Folder | Type token | Extensions |
|---|---|---|
| `WhatsApp Images/` | `image` | `.jpg`, `.jpeg`, `.png`, `.heic`, `.webp` |
| `WhatsApp Video/` | `video` | `.mp4`, `.3gp`, `.mkv`, `.mov` |
| `WhatsApp Voice Notes/` | `voice_note` | `.opus`, `.m4a`, `.ogg` |
| `WhatsApp Audio/` | `audio` | `.mp3`, `.aac`, `.wav` |
| `WhatsApp Documents/` | `document` | `.pdf`, `.docx`, `.xlsx`, `.apk` |
| `WhatsApp Animated Gifs/` | `gif` | `.mp4`, `.gif` |
| `WhatsApp Stickers/` | `sticker` | `.webp` |

> **Note**: Folder discovery is dynamic — future WhatsApp folder names are picked up
> automatically via token matching rather than a hard-coded list.

