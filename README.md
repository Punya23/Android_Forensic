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
