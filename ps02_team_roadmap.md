# PS-02: Android Rapid Evidence Triage & Forensic Preview Tool
## Team of 4 — Round 1 Submission Roadmap

> **Round 1 Goal**: A working, demonstrable prototype that shows forensic triage of a non-rooted Android device — not production-grade, but credibly real. Judges want to see that you *understand the problem* and have *something running*, not a full Cellebrite clone.

---

## Team Role Assignment (4 People)

| Role | Owner | Primary Focus |
|---|---|---|
| **Engineer A** | Person 1 | Acquisition Engine (Python/ADB) + Chain of Custody |
| **Engineer B** | Person 2 | Parsing & Recovery Engine (Python) + App-Specific Parsers |
| **Engineer C** | Person 3 | Electron Shell + React/TS Frontend Dashboard |
| **Engineer D** | Person 4 | Demo Staging, QA, Documentation, Pitch Deck |

> **Note**: Engineer D is NOT dead weight — demo staging, device seeding, fallback recording, and pitch writing are full-time jobs in the final week. Assign your strongest communicator here.

---

## Realistic Round 1 Scope (What to Ship vs. What to Gate)

### ✅ Must Ship (Core Demo Path)
- Device connect → ADB Tier 0 acquisition (photos, media, GPS, deleted trash) in < 2 min
- WhatsApp media harvesting + guided Export Chat ingestion
- Basic SQLite deleted-row recovery with confidence badges
- Chain-of-custody event log + SHA-256 per-file manifest
- **Tier 1, contacts only**: Collector helper APK + `pm grant READ_CONTACTS` — cheap, low-risk
  (single permission grant, no role-holder swap), and closes an explicitly-named requirement
  (I.a "contacts", III.b "categorized views: ... contacts"). Don't ship Round 1 without this.
- Forensic Preview Dashboard: media gallery, basic timeline, case metadata, **Contacts view**,
  **Calls view** (see note below)
- PDF/HTML triage report with triage disclaimer

> **Why Contacts moved up from bonus**: the problem statement names calls/SMS/contacts/location as
> the four Rapid Acquisition artifacts (I.a) and contacts/calls as two of five required dashboard
> categories (III.b). Contacts is the only one of the three "intrusive" artifacts that's actually
> cheap — a single `pm grant`, no default-app role swap. Shipping it turns "we didn't cover 3 of 4
> named artifact types" into "we covered 2 of 4 cleanly and can explain exactly why the other 2
> need a more invasive, logged step we chose not to take in Round 1."
>
> **Calls/SMS view still ships even without the data**: build the Calls view to render a clear,
> honest empty state — "Not acquired at this tier: requires temporarily reassigning the default
> Dialer/SMS app, a more invasive step than Round 1 attempts — see Advanced Mode." That's a better
> live-demo moment than a missing tab, and it's a free way to restate the project's honesty pitch.

### ⚠️ Ship If Time Allows (Bonus Points)
- Tier 1: MediaStore bulk enumeration/metadata via helper APK (`pm grant READ_MEDIA_*`)
- Call log / SMS via role-holder-swap (the intrusive Tier 1 step) — populates the Calls view built above
- `sqlite-dissect` + `sqbrite` dual-engine recovery pipeline
- Keyword/regex flagging across pulled artifacts
- Cross-artifact chronological timeline view
- ALEAPP subprocess integration

### 🚫 Do NOT Attempt for Round 1
- Signal bypass (impossible without root/cooperation — be honest)
- Signal consent-based passphrase flow — real and legitimate, but explicitly **deferred to Round 2**;
  don't start this in Round 1, it isn't on the critical path to qualifying (see Week 3 note below)
- Telegram `cache4.db` without root
- WhatsApp APK-downgrade key-extraction (too fragile, too risky live)
- USB-bootable live OS packaging
- PostgreSQL lab sync
- Full `NotificationListenerService` monitoring

---

## 30-Day Timeline

### 📅 Week 1 — Days 1–7: Foundation Sprint

**Day 1–2 (All hands)**
- [ ] Acquire 2 test Android devices (different OEMs — e.g., Pixel + Samsung/Xiaomi). **This is a blocker for everything else.**
- [ ] Set up monorepo: `/engine` (Python), `/app` (Electron+React), `/apk` (Kotlin helper)
- [ ] Engineer A: scaffold Flask service + ADB connectivity check + basic `adb pull` of DCIM
- [ ] Engineer B: generate synthetic test corpus — fake WhatsApp `.txt` exports, SQLite DBs with deliberate deletes, known GPS EXIF photos
- [ ] Engineer C: scaffold Electron shell + React app skeleton with Tailwind, IPC channel stubs
- [ ] Engineer D: draft mock case scenario (fake consent doc + device metadata form), begin pitch deck outline
- [ ] All: agree on API contract between Flask engine ↔ Electron IPC ↔ React UI
- [ ] Set up GitHub repo with CI (lint + pytest + TypeScript typecheck)

**Day 3–7 — Acquisition Engine + Chain of Custody (Engineer A primary)**
- [ ] **Tier 0 fully working**: `adb pull` of `/sdcard/DCIM`, `/sdcard/Pictures`, `/sdcard/Download`, `/sdcard/Movies`, `Android/media/com.whatsapp/WhatsApp/Media/`
- [ ] `.trashed-*` file detection (MediaStore trash pattern) — pull and flag
- [ ] `adb shell dumpsys location` → parse last-known GPS coordinates
- [ ] EXIF GPS extraction from pulled photos (use `Pillow`/`piexif`)
- [ ] Chain-of-custody module: device intake block (make/model/OS/IMEI via `adb shell getprop`), pre-acquisition snapshot, append-only JSONL event log
- [ ] SHA-256 per-artifact manifest — computed at pull time, written immediately
- [ ] Flask-SocketIO progress events — real-time streaming to frontend

**Day 3–7 — Synthetic Test Corpus (Engineer B primary)**
- [ ] Script that generates: WhatsApp `.txt`/`.zip` exports with realistic timestamps and phone numbers
- [ ] Script that generates: SQLite DB with 50 rows inserted + 15 deleted in known freelist positions (for recovery testing)
- [ ] Script that seeds: EXIF-tagged fake GPS photos, `.trashed-*` renamed copies
- [ ] Wire up `sqlite-dissect` (`pip install sqlite-dissect`) against synthetic corpus, confirm row recovery works

**Day 3–7 — Electron Shell (Engineer C primary)**
- [ ] Electron main process: USB device-attach detection (poll `adb devices`)
- [ ] IPC bridge: Electron ↔ Flask via localhost HTTP + WebSocket
- [ ] Basic dashboard layout: sidebar nav (Cases / Acquisition / Media / Messages / Timeline / Report)
- [ ] Device connect screen: show device info, acquisition tier selector

**Day 3–7 — PM + Docs (Engineer D)**
- [ ] Write mock warrant/consent template (PDF)
- [ ] Define the demo narrative: what data will be on the phone, what will the officer "discover"
- [ ] Research Section 65B certificate format for the report

---

### 📅 Week 2 — Days 8–14: App Parsers + Recovery Engine

**Engineer A: Collector Helper APK (Tier 1)**
- [ ] Scaffold minimal Kotlin APK (use AI for boilerplate — nobody needs to be an Android expert here)
- [ ] Request `READ_CONTACTS`, `READ_MEDIA_IMAGES`, `READ_MEDIA_VIDEO`, `READ_MEDIA_AUDIO` permissions
- [ ] `adb install` + `pm grant` flow scripted and logged in CoC event log
- [ ] Export contacts as JSON via ContentProvider query
- [ ] Validate `pm grant` behavior on both test devices — **expect OEM differences, document them**
- [ ] Call log/SMS via role-holder-swap — build as separate, explicitly-flagged action (NOT bundled)

**Engineer B: App-Specific Parsers + Recovery Pipeline**
- [ ] **WhatsApp media**: parse `WhatsApp/Media/` folder structure, categorize by type (images/video/voice/documents)
- [ ] **WhatsApp guided export**: dashboard flow walks officer through native Export Chat; tool watches `/sdcard` for `.txt`/`.zip` arrival, auto-ingests
- [ ] **WhatsApp chat parser**: parse `_chat.txt` format (WhatsApp export format), extract messages, timestamps, senders
- [ ] **Telegram Tier 0**: pull `Android/media/org.telegram.messenger/Telegram/` if present — pre-stage demo device with "Save to Gallery" ON
- [ ] **sqlite-dissect pipeline**: input a `.db` file → output live rows + recovered rows with confidence badges (Live / Recovered-Verified / Carved-Partial / Deletion-Detected)
- [ ] **sqbrite secondary pass**: cross-check freelist cases
- [ ] Begin ALEAPP subprocess wrapper — shell out to CLI, parse TSV output

**Engineer C: Dashboard Core Views**
- [ ] Media Gallery view: grid of photos/videos pulled, click to preview, EXIF metadata sidebar
- [ ] Messages view: WhatsApp chat display, confidence badge per message, timestamps
- [ ] **Contacts view**: simple searchable list/table (name, number) from the Tier 1 `pm grant` pull — must ship, see Scope note above
- [ ] **Calls view**: table if Tier 1 role-holder-swap data exists; otherwise a clear "Not acquired at this tier" empty state with the one-line rationale — ships either way
- [ ] Case Management: create case, case metadata form, case folder structure
- [ ] Real-time progress bar driven by Flask-SocketIO events (show what's being pulled)
- [ ] Confidence badge component (color-coded: green=Live, yellow=Recovered, orange=Carved, red=DeletionDetected)

**Engineer D: Demo Device Staging**
- [ ] On the demo phone: install WhatsApp, create 3 conversations with media and text, delete some messages
- [ ] Enable Telegram "Save to Gallery", receive some photos/videos
- [ ] Take GPS-tagged photos (ensure EXIF location is embedded)
- [ ] Move some photos to trash (`.trashed-*`)
- [ ] Verify that `adb pull` of all the above produces expected results — document which works and which doesn't by OEM
- [ ] Iterate with Engineer A/B as issues are found

---

### 📅 Week 3 — Days 15–21: Dashboard Polish + Integration

**Engineer A + B: Integration Hardening**
- [ ] End-to-end run: device connect → Tier 0 pull → WhatsApp parse → recovery engine → dashboard
- [ ] Fix any broken paths, timeouts, unicode issues in pulled filenames
- [ ] ALEAPP integration: parse artifacts, display ALEAPP-extracted events in timeline
- [ ] Keyword/regex flagging: scan all pulled text artifacts for a configurable keyword list
- [ ] Tier 1 call log/SMS role-holder-swap (only if Contacts/MediaStore Tier 1 landed cleanly in Week 2 with time to spare)

> Signal's consent-based passphrase flow is real (see implementation plan Section 3) but is
> explicitly **out of scope for this round** — don't start it here even if time looks available;
> it competes for the same integration-hardening time as the golden path and isn't needed to
> qualify. Slot it into the Round 2 roadmap instead (see pitch deck "Next Steps").

**Engineer C: Timeline + Reporting**
- [ ] Cross-artifact timeline view: merge WhatsApp messages + GPS points + call log entries + media timestamps into a single chronological feed
- [ ] Date-range filter + keyword filter wired to timeline
- [ ] Report export: generate HTML triage report with all fields from CoC module, artifact summary table, triage disclaimer banner, Section 65B certificate block
- [ ] PDF export (use `weasyprint` or `pdfkit` via Python)
- [ ] Locations view: display GPS points on a map (use Leaflet.js with OpenStreetMap — offline tiles if needed)

**Engineer D: QA + Documentation**
- [ ] Run the full pipeline on both test devices end-to-end, log every bug
- [ ] Document every OEM-specific behavior found (path differences, pm grant failures)
- [ ] Write "Supported Artifacts Matrix" (based on Section 0 of the implementation plan — this is already half-written)
- [ ] Write "Acquisition Method" section for the submission doc
- [ ] Write "Limitations" section (what the tool doesn't do and why — judges respect honesty)

---

### 📅 Week 4 — Days 22–30: Hardening, Demo Lock-In, Submission

**Days 22–27: Cross-Device Hardening + Report Polish**

- [ ] Re-run full pipeline on all test devices — fix OEM path/permission breakage
- [ ] Package Python engine: `PyInstaller` bundle with bundled `adb` platform-tools (so zero install on target machine)
- [ ] Package Electron app with bundled Python bundle — single downloadable `.dmg`/`.exe`
- [ ] Final report QA: NIST/SWGDE field alignment check, hashes reproducible, 65B block correct
- [ ] Stress test: what happens on disconnect mid-pull? On locked device? On denied USB debugging? Handle gracefully.
- [ ] Freeze feature list — no new features after Day 26

**Days 27–28: Demo Staging Lock-In**
- [ ] Final demo phone: wipe and re-seed from scratch with the exact agreed-upon dataset
- [ ] Rehearse golden path with a stopwatch — target: device plug-in to dashboard populated in < 5 minutes
- [ ] Engineer A + B walk through the forensic narrative (what data was "found," what was "recovered")
- [ ] Engineer C verifies UI looks perfect on the demo machine/display resolution
- [ ] Engineer D records a full fallback video of the entire demo flow — this is **non-negotiable** for a live on-stage demo

**Days 29–30: Submission Finalization**
- [ ] Final documentation pass: README, acquisition method doc, limitations, supported artifacts matrix
- [ ] Submission deliverables:
  - [ ] Packaged executable / installer
  - [ ] Source code (GitHub link)
  - [ ] Technical documentation PDF
  - [ ] Demo video (fallback recording serves double duty here)
  - [ ] Pitch deck (8–10 slides)
- [ ] Engineer D: final pitch rehearsal with all 4 team members

---

## Critical Dependency Map

```
Test Devices (Day 1–2)
    └─► Tier 0 acquisition (Day 3–7)
            └─► WhatsApp/Telegram parsers (Day 8–14)
            └─► Recovery engine (Day 8–14)
                    └─► Dashboard views (Day 8–21)
                    └─► Timeline + Report (Day 15–21)
                            └─► Demo staging + QA (Day 22–28)
                                    └─► Submission (Day 29–30)

Synthetic Corpus (Day 1–7)
    └─► Recovery engine development (Day 3–14) [unblocks B from needing devices]

Flask skeleton (Day 1–2)
    └─► All Python work (Day 3–30)

Electron shell (Day 1–2)
    └─► All frontend work (Day 3–30)
```

> **Critical path**: test devices → Tier 0 → parsers → dashboard. Everything else is parallel. If devices are delayed past Day 2, use synthetic corpus for all backend work and validate against real hardware in Days 22–27.

---

## Round 1 Demo Script (5-Minute Golden Path)

1. **[0:00]** Plug in demo Android phone. Dashboard shows "Device Connected: [Make/Model/Android Version]"
2. **[0:15]** Officer fills in Case ID + Legal Authority reference (warrant number). Click "Begin Acquisition."
3. **[0:30]** Progress bar starts. Live event log shows each pull action as it happens.
4. **[2:00]** Media Gallery populates: X photos, Y videos — including "Recovered from Trash" items with orange badge.
5. **[2:30]** GPS map view shows 3 location points from EXIF data + last known location.
6. **[3:00]** Messages view shows WhatsApp chat (from guided export). One message shows "Recovered — Verified" badge with source page number.
7. **[3:30]** Acquisition complete. SHA-256 manifest shows N files, total size.
8. **[4:00]** Click "Generate Report." HTML/PDF opens with NIST-aligned fields, triage disclaimer, 65B block.
9. **[4:30]** Judge Q&A: show the audit log, explain what "minimally-invasive, fully-logged acquisition" means vs. "read-only."

---

## Pitch Deck Outline (Engineer D owns)

1. **Problem**: Field investigators need fast, reliable triage — current tools are commercial, Windows-only, or overclaim capability
2. **Honest Feasibility**: show the 3-tier matrix — be the team that *knows the limits*
3. **Architecture**: one-slide diagram (Electron → Flask → ADB → Device)
4. **Live Demo**: 5 minutes on stage
5. **What We Recover**: photos, GPS, WhatsApp messages, deleted data — with provenance badges
6. **Chain of Custody**: NIST/SWGDE alignment, 65B certificate — this is a differentiator
7. **What We Don't Claim**: Signal bypass, physical extraction — and why that's honest, not weak
8. **Open Source Stack**: ALEAPP, sqlite-dissect, sqbrite — no commercial dependencies
9. **Team + Timeline**: 30-day sprint, 4 engineers
10. **Next Steps**: Tier 2 root mode, portable packaging, lab sync, Signal consent-based decrypt flow

---

## Key Technical Decisions (Don't Re-debate These)

| Decision | Rationale |
|---|---|
| Electron + React/TS (not Next.js) | Desktop-native, offline-first, USB access via Node IPC |
| Python/Flask backend (not Node) | All forensic libraries (ALEAPP, sqlite-dissect, etc.) are Python |
| Embedded SQLite per case | Zero network dependency in the field |
| `sqlite-dissect` as primary recovery engine | DoD-permissive license, pip-installable, well-validated |
| MVP = Tier 0 + Tier 1 contacts-only, rest of Tier 1 as bonus | Tier 0 is reliable on ALL devices; contacts is the one cheap, low-risk Tier 1 win and closes a named requirement (I.a, III.b) — call log/SMS's role-holder-swap is riskier and deferred |
| "Minimally-invasive, fully-logged" (NOT "read-only") | SWGDE/NIST explicitly say read-only is impossible on mobile |
| NO MVT code | Modified MPL-2.0 + consent restriction directly conflicts with forensic use case |
| Signal = consent-based only | Hardware-backed Keystore — no passive bypass exists |

---

## Risk Flags for Round 1

> [!CAUTION]
> **No test devices = blocked team.** If you don't have real Android hardware by Day 2, Days 3–14 engineering work is happening in a vacuum. Borrow, buy, or factory-reset personal phones — this is non-negotiable.

> [!WARNING]
> **OEM variance is real.** `pm grant` behavior, scoped storage paths, and `.trashed-*` detection differ between Pixel, Samsung (OneUI), and Xiaomi (HyperOS/MIUI). Don't assume Pixel behavior generalizes — test on your actual demo device and lock in that hardware early.

> [!WARNING]
> **WhatsApp APK-downgrade trick is fragile.** Do not attempt this for Round 1. Gate it as "Advanced Lab Mode (pre-validated)" and don't touch it until Week 3 at the earliest — only if Tier 0/1 is already bulletproof.

> [!IMPORTANT]
> **Record the fallback video.** A live demo on stage is the highest-risk deliverable. The recorded fallback must be done by Day 27, not the night before.

> [!NOTE]
> **Engineer D is a full-time role.** Demo staging, device seeding, pitch rehearsal, and documentation are not weekend tasks. Assign your most organized team member here from Day 1.
