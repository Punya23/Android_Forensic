<div align="center">

# 🛡️ SNAGR

**Android Rapid Evidence Triage & Forensic Preview**

Seized phone → readable evidence preview in minutes, not days.
Minimally-invasive. Fully-logged. Never claims more than it did.

`ERH26_PS_02` · Python · Flask · Electron · React · TypeScript · Kotlin · SQLite

</div>

---

## The pitch

On-scene officers can't tell, in the moment, whether a seized Android phone holds the
case-breaking message — or nothing at all. Commercial triage tools are Windows-locked,
license-gated, and often quietly overclaim ("read-only acquisition" doesn't exist for
mobile — SWGDE says so). **SNAGR** pulls what's reachable non-root in minutes, recovers
deleted rows where physics allows it, and logs every device touch to a hash-chained audit
trail — so the report is something you can actually stand behind.

## Highlights

- 🔓 **Tiered acquisition** — Tier 0 (zero touch) → Tier 1 (sideloaded helper) → Tier 2 (root), every tier opt-in and logged
- 🧬 **Deleted-record recovery** — WAL / freelist / freeblock / rollback-journal carving, confidence-badged (Live / Recovered / Carved / Deletion-Detected)
- 💬 **Multi-app coverage** — WhatsApp, Telegram, Instagram, Snapchat, SMS, browser history — including their deleted messages
- 🗺️ **Location & social graph** — EXIF/GPS trace, cell/BT/Wi-Fi history, cross-channel comms graph
- 🚦 **Traffic-light verdict** — RED/AMBER/GREEN scorecard built for a five-minute field decision
- 📜 **Court-shaped report** — NIST/SWGDE-aligned, BSA 2023 §63 certificate, sealed SHA-256 export
- 🧠 **Case intelligence** — plain-language brief → ontology-ranked collection plan, offline by default
- 📴 **Works with zero phone** — full pipeline demoable against a synthetic mock corpus

## How it flows

```mermaid
flowchart LR
    A["📱 Device"] -->|adb pull / Collector APK / root| B["⚙️ Engine<br/>Flask :5057"]
    B -->|recover + badge| C["🗂️ Case store<br/>hash-chained"]
    B <-->|live progress| D["🖥️ Dashboard<br/>:5173"]
    C -->|seal| E["📄 Report + export<br/>BSA §63"]
    style A fill:#1c7d3f,color:#fff,stroke:#0d4a24
    style E fill:#a5322f,color:#fff,stroke:#5c1c19
```

## Quick start (no phone required)

```bash
# engine
cd engine && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && cp .env.example .env
python tools/make_corpus.py _corpus/device_A
python -m triage.server --port 5057

# dashboard (new terminal)
cd app && npm install && npm run dev   # → localhost:5173
```
Sign in with the creds from `engine/.env`, pick the mock device, click **Begin Acquisition**.

Or one shot: `./run.sh`

## 📚 Full documentation

| | |
|---|---|
| [**Capabilities**](docs/CAPABILITIES.md) | Every verified feature vs. its commercial equivalent |
| [**Architecture**](docs/ARCHITECTURE.md) | Diagrams, workflow, folder structure, stack rationale |
| [**API reference**](docs/API_REFERENCE.md) | Every route, auth rule, Socket.IO event |
| [**Database**](docs/DATABASE.md) | Schema, per-case file layout, example payloads |
| [**Setup & deployment**](docs/SETUP.md) | Full install, deps, env vars, packaging gaps |
| [**Network artifacts**](docs/NETWORK_ARTIFACTS.md) | Wi-Fi, Bluetooth, USB & hotspot — what the device stores and what we can read |
| [**Notes**](docs/NOTES.md) | Forensic soundness, WhatsApp module deep-dive, known gaps |

## Reality check

Not production-ready as a court-grade instrument yet, and says so on every report. No
slack-space carving, no lock-screen bypass, no Signal decryption — see
[`docs/NOTES.md`](docs/NOTES.md) for the full honesty ledger, including what's built but
deliberately not wired up.

---

<div align="center">

**1061 tests passing** · Runs fully offline · No account, no cloud, no telemetry

</div>
