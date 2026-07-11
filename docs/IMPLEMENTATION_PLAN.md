# Android Rapid Evidence Triage & Forensic Preview Tool
## 30-Day Implementation Plan — ERH26_PS_02

**Team:** 2–3 engineers · **Demo:** live, on a real phone, on stage · **Goal:** win

This plan is grounded in a feasibility research pass (7 parallel deep-dives, ~200 sources) run
before any architecture was committed to. The single biggest risk for this problem statement is
not "can we build a UI" — it's **overclaiming forensic capability in front of judges who know
mobile forensics**. Cellebrite/Oxygen-class tools spend millions on techniques (root exploits,
chip-off, GrayKey-class hardware) this project cannot replicate in 30 days. The winning strategy
is: **be the team that is precisely honest about what's possible on a non-rooted stock phone,
build that extremely well, and clearly gate anything requiring root as an advanced/optional mode.**

---

## 0. Read this first: the Honest Feasibility Matrix

| Artifact | Non-root, live demo | What it actually takes | Root available |
|---|---|---|---|
| Photos/videos/downloads (DCIM, Pictures, Download) | ✅ Reliable | Plain `adb pull` | — |
| GPS location from photo EXIF | ✅ Reliable | Pulled photo + EXIF parse | — |
| Recently-deleted media (`.trashed-*` MediaStore trash) | ✅ Reliable | Plain `adb pull`, filename pattern | — |
| Legacy thumbnail cache (`.thumbnails/`) | ✅ Reliable (if present) | Plain `adb pull` | — |
| Last known location (`dumpsys location`) | ✅ Reliable | `adb shell dumpsys location` | — |
| WhatsApp media (photos/video/voice notes already received) | ✅ Reliable | `adb pull Android/media/com.whatsapp` | — |
| WhatsApp chat text (guided) | ✅ Reliable, but manual per-chat | Officer taps native "Export Chat"; tool ingests the file | — |
| Telegram cached media (photos/video saved to gallery) | ⚠️ Conditional | Only if "Save to Gallery" was enabled by the user | — |
| Contacts | ⚠️ Non-root possible, but state-changing | Sideload helper APK + `adb shell pm grant READ_CONTACTS` | Simpler with root |
| Media/thumbnails via MediaStore (bulk, with metadata) | ⚠️ Non-root possible, state-changing | Helper APK + `pm grant READ_MEDIA_*` | Simpler with root |
| Call log / SMS | 🔶 Non-root technically possible, but intrusive | Requires temporarily making the helper app the default Dialer/SMS handler (`cmd role add-role-holder`) — must be logged and reverted | Clean access |
| WhatsApp full message database (msgstore.db) decrypted | 🔶 Advanced/bonus only | APK-downgrade + `adb backup` key-extraction trick — fragile, OEM-dependent, unmaintained upstream tooling, must be pre-validated on the exact demo device | Clean access |
| Telegram full chat history (cache4.db) | ❌ Infeasible without root | App-private storage, `allowBackup=false`, no loophole | Clean access — DB itself isn't even encrypted |
| Signal — anything, locked/non-cooperating phone | ❌ Infeasible | Hardware-backed Android Keystore key, architecturally non-exportable | Still needs Frida/live-memory tricks or vendor Keystore-extraction (Cellebrite/GrayKey-class) |
| Signal — with device owner's cooperation | ⚠️ Consent-based, not a bypass | User discloses local-backup passphrase or Secure Backups recovery key | N/A |
| Deleted SQLite rows (freelist/WAL/journal, not yet vacuumed) | ✅ Genuinely recoverable | `sqlite-dissect` / `sqbrite` carving, on whatever plaintext DB you legitimately obtained | Same |
| Deleted rows after `VACUUM` / full overwrite | ❌ Out of scope for 30 days | Requires raw physical image + disk-level carving (bulk_extractor-class) | Even with root, needs a physical image |

**Rule for the whole project: never claim "read-only acquisition."** SWGDE's current guidance
(18-F-003 v2.0) states plainly that *no write-blocking method exists for mobile devices* — enabling
USB debugging, authorizing the RSA key, and (for Tier 1) installing a helper APK are all real,
state-changing actions. The forensically defensible claim — and the one NIST/SWGDE actually
endorse — is **"minimally-invasive, fully-logged acquisition"**: every touch to the device is
timestamped, justified, and reversible where possible. Say this explicitly in the pitch; it is a
differentiator, not a weakness, because it shows the team understands mobile forensics better than
a team that oversells "read-only."

---

## 1. System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Electron shell (Node.js/TypeScript)                         │
│   - App lifecycle, USB/device-attach detection               │
│   - Spawns & supervises the local Python service              │
│   - Bundles platform-tools (adb) per-OS — zero install        │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ React + TypeScript renderer (Tailwind)                   │ │
│  │  Forensic Preview Dashboard — categorized views,          │ │
│  │  timeline, keyword/date filters, case management,          │ │
│  │  live progress (5–10 min acquisition countdown)            │ │
│  └─────────────────────────────────────────────────────────┘ │
└───────────────────────────┬───────────────────────────────────┘
                            │ localhost HTTP + WebSocket (Flask-SocketIO)
┌───────────────────────────▼───────────────────────────────────┐
│  Python forensic engine (Flask + Flask-SocketIO)               │
│   - ADB orchestration (adbutils) — Tier 0/1/2 acquisition       │
│   - Chain-of-custody & hashing module (SHA-256 manifest,        │
│     append-only event log)                                     │
│   - ALEAPP subprocess wrapper (broad OS artifact parsing)       │
│   - App-specific recovery: WhatsApp / Signal / Telegram         │
│   - Deleted-data recovery: sqlite-dissect + sqbrite             │
│   - Keyword/known-hash flagging                                │
│   - Report generation (HTML/PDF, NIST/SWGDE-aligned)            │
└───────────────────────────┬───────────────────────────────────┘
                            │ adb / USB
┌───────────────────────────▼───────────────────────────────────┐
│  Android target device (non-rooted, USB debugging authorized)  │
│   + optional sideloaded "Collector" helper APK (Kotlin)         │
└─────────────────────────────────────────────────────────────────┘

Per-case storage: embedded SQLite + structured case folder
(raw pulled artifacts + hash manifest JSON + audit-log JSONL) —
offline-first, no server dependency in the field.
Optional "Lab Sync" pushes a case to a central PostgreSQL instance
back at the station for multi-case search/cross-referencing.
```

**Why this stack (and where it deviates from the default full-stack preferences):**

- **Electron + React/TS**, not Next.js: this is a native desktop tool that needs direct USB/ADB
  access, must run fully offline in an interrogation room, and packages as a standalone
  executable. Next.js's server-rendering model doesn't fit; Electron's Node main process does,
  while still using React/TypeScript/Tailwind end to end.
- **Python/Flask as the primary backend, not Node/NestJS or Go**: essentially every forensic
  library that matters here — ALEAPP, `sqlite-dissect`, `wa-crypt-tools`,
  `WhatsApp-Chat-Exporter`, `signalbackup-tools`, `adbutils` — is Python (or trivially
  subprocess-callable from Python). Node/Go equivalents are thin or nonexistent. This also folds
  the stated "Python + Flask for ML" preference and the "backend" role into one service, since the
  forensic engine *is* the data-processing core of this product. Electron's Node layer still
  exists — it's the thin orchestration/IPC/packaging layer — so the Node preference isn't dropped,
  just scoped correctly.
- **Embedded SQLite per case, not PostgreSQL, as the primary store**: the tool has to work with
  zero network dependency at a crime scene. A running Postgres server is not a reasonable field
  requirement. PostgreSQL still shows up — as an optional central "Lab Sync" target once back at
  the station, satisfying the stated DB preference where it actually applies (multi-case search,
  not single-case field capture).

---

## 2. Acquisition Tiers (the core design abstraction)

Every artifact the tool touches must be tagged with the tier that produced it — this tagging is
what the chain-of-custody report and the "how did you get this" judge question both hang off of.

- **Tier 0 — zero device-state change.** `adb shell dumpsys location`; `adb pull` of
  `/sdcard/DCIM`, `/Pictures`, `/Download`, `/Movies`, `Android/media/<pkg>` (WhatsApp/Telegram
  media folders), including renamed `.trashed-*` files and legacy `.thumbnails/`. No permissions
  granted, nothing installed. This is the **headline, always-on** tier — build it first, make it
  bulletproof, and let it start populating the dashboard within seconds of connecting the device.
- **Tier 1 — shell-level, device-state-changing (logged).** Sideload a small "Collector" helper
  APK; `adb shell pm grant <helper> READ_CONTACTS` / `READ_MEDIA_IMAGES` / `READ_MEDIA_VIDEO` /
  `READ_MEDIA_AUDIO` (all "dangerous," not "hard-restricted," so grantable without root). Gets
  contacts and full MediaStore enumeration/metadata/thumbnails. **Call log/SMS require a separate,
  more intrusive step** (temporarily assigning the helper app as the default Dialer/SMS role via
  `cmd role add-role-holder`) — treat this as its own explicitly-flagged action, logged and
  reverted immediately after, never bundled silently with the Tier 1 grants.
- **Tier 2 — root required.** Raw `contacts2.db`/`mmssms.db`, Telegram's `cache4.db`, Google
  Photos/Samsung Gallery private trash databases, MediaProvider's internal thumbnail cache. Build
  this as a **clearly gated "Advanced / Lab Mode"** — only active if the tool detects root, never
  silently attempted, never required for the primary demo.

Every ADB/pm/cmd invocation is logged with timestamp + exact command + result into the append-only
audit log before anything else happens with the data.

---

## 3. App-Specific Recovery Strategy

### WhatsApp — the strongest non-root story of the three
1. **Media harvesting** (Tier 0): pull `Android/media/com.whatsapp/WhatsApp/Media/*` — plain
   files, no key needed, no root.
2. **Guided native export** (Tier 0/1, human-in-the-loop): the dashboard walks the officer through
   tapping WhatsApp's own *Export Chat* per conversation of interest; the tool watches
   `/sdcard` for the resulting `.txt`/`.zip` and ingests it automatically. Zero exploits, works on
   every current build, but manual and per-chat — say this plainly.
3. **Advanced/bonus: full `msgstore.db` decryption.** The APK-downgrade + `adb backup`
   key-extraction trick (`adb install -r -d` an old WhatsApp APK to reuse the existing private
   data, then `adb backup` succeeds because the old APK allows it) is real and cited by commercial
   vendors, but the open-source implementations are unmaintained and OEM-dependent, and it cannot
   touch a passworded end-to-end-encrypted backup. **Pre-validate this against the exact demo
   phone's WhatsApp build well before the event, keep a recorded fallback, and label it clearly as
   an advanced/lab-validated capability**, not a guaranteed live feature. Decrypt with
   `WhatsApp-Chat-Exporter` (MIT — safe to embed) rather than `WhatsApp Viewer` (doesn't support
   crypt15) or by directly importing `wa-crypt-tools` (GPL-3.0 — call it as an isolated subprocess
   instead of linking it in, to avoid copyleft obligations).
4. Optional stretch: `NotificationListenerService`/`AccessibilityService`-based live capture —
   explicitly labeled **forward-looking monitoring**, not historical recovery, and needs its own
   consent framing. Cut first if time runs short.

### Telegram — media yes, chat history no (without root)
- `cache4.db` (the real chat history) sits in app-private storage, `allowBackup=false`: **not
  reachable without root, full stop.** Don't attempt to fake this in the demo.
- If — and only if — the device has "Save to Gallery" enabled, cached photos/videos land in
  `Android/media/org.telegram.messenger/Telegram/...` and are a genuine Tier 0 win via `adb pull`.
  **This must be pre-staged on the demo device** (enable the setting, send/receive real media,
  verify the pull works) days in advance — it will show nothing if not seeded.
- Gate full `cache4.db` parsing (trivial once you have root — it isn't even encrypted) behind the
  Tier 2 / Advanced Lab Mode, using a plain SQLite reader plus the same deleted-row recovery engine
  as everything else.

### Signal — set expectations correctly, this is the app to be humble about
- Local DB key is wrapped by the Android Keystore (hardware-backed on most devices) — **there is
  no passive bypass, root included**, without live-memory instrumentation (Frida) or vendor
  Keystore-extraction hardware neither Cellebrite nor Oxygen give away for free.
- The only real avenues require the phone owner's active cooperation: a disclosed 30-digit local
  backup passphrase (parse offline with `signalbackup-tools`), a disclosed Secure Backups recovery
  key (new, still rolling out through 2026), or the phone's own "Link a device" flow while
  unlocked. **Build the demo around a consent-based flow**: officer enters a disclosed passphrase,
  tool decrypts and displays the backup — and say outright in the pitch that this is cooperative
  acquisition, not a forensic bypass, because that's exactly what NIST/SWGDE would expect an
  honest examiner to say too.

---

## 4. Deleted & Cached Data Recovery Engine

- **Primary engine: `sqlite-dissect`** (DC3/DoD, permissive license, `pip install sqlite-dissect`).
  Pure Python, handles freelist/unallocated-space signature carving, WAL frame parsing (an
  un-checkpointed deleted message is fully recoverable here), and rollback-journal parsing, with a
  CLI and an importable API and CSV/JSON export — wire it directly into the Flask service.
- **Secondary pass: `sqbrite`** (MIT, small pure-Python codebase) as a cross-check against
  freelist-only cases — its heuristics differ enough from `sqlite-dissect`'s schema-signature
  approach to catch rows the other misses, and it's simple enough to fork within the 30 days.
- **Explicitly out of scope:** recovery after a real `VACUUM` or full page overwrite — that's a
  raw unallocated-disk-cluster carving problem (bulk_extractor/photorec-class), a different product
  surface entirely. Say this plainly in the docs rather than letting "deleted message recovery"
  sound like a blanket guarantee.
- **Never present a carved row with the same visual weight as a live one.** Implement a 4-tier
  provenance/confidence badge on every non-live row, shown with its source file, page number, and
  carve method so an examiner can independently verify it in a hex viewer:
  1. **Live** — normal query result.
  2. **Recovered — Verified** — intact freelist page or un-checkpointed WAL/journal frame,
     schema-consistent header, no ambiguity.
  3. **Carved — Partial/Unconfirmed** — signature-matched over freeblocks/unallocated space with a
     partially-overwritten header or inferred serial types; show a disclaimer that fields may be
     corrupted or belong to an overlapping record.
  4. **Deletion Detected — No Content** — a rowid/AUTOINCREMENT gap proves something was deleted,
     with zero recoverable content (the DFIR "gap analysis" technique — useful even when carving
     fails).
- Report your own measured recovery/false-positive rate on a self-built test corpus rather than
  just a raw "N deleted messages found" count — this is the credible version of the claim.

---

## 5. Build vs. Reuse — What Not to Build From Scratch

| Component | Use | License | Notes |
|---|---|---|---|
| **ALEAPP** | Fork/vendor `scripts/artifacts/` plugins, or shell out to its CLI and parse HTML/TSV (exactly how Autopsy's own Android module does it since v4.18) | MIT | Broad, actively-maintained OS/app artifact coverage for free — biggest single time-saver available |
| **Andriller** | Reference for acquisition patterns + WhatsApp crypt decode | MIT (re-verify which edition/fork before depending on it — "Andriller CE" branding suggests a possible pro split) | Secondary reference, not a hard dependency |
| **`sqlite-dissect`** | Primary deleted-data recovery engine | DoD custom permissive | pip-installable, embeddable |
| **`sqbrite`** | Secondary recovery cross-check | MIT | Small enough to fork/adapt |
| **`WhatsApp-Chat-Exporter`** | WhatsApp DB decrypt/parse once key is obtained | MIT | Prefer over GPL `wa-crypt-tools` for anything embedded directly |
| **`signalbackup-tools`** | Signal local-backup decrypt (consent-based flow) | GPL-3.0 | Call as an isolated subprocess, don't link |
| **Autopsy source** | Read for Java Android-parsing schema reference only | Apache-2.0 | Too heavy to embed; not worth depending on |
| **MVT (Mobile Verification Toolkit)** | **Do not depend on for core code.** Its license adds a non-OSI "Consensual Use Restriction" explicitly banning use without the device owner's consent — which directly conflicts with this tool's actual use case (triaging a seized suspect's phone). At most, borrow the *idea* of IOC/hash-matching as an optional, clearly-labeled "known-spyware check," never vendored code | Modified MPL-2.0 + consent restriction (non-OSI) | Real legal exposure if misused — flag this to the whole team on day 1 |
| Cellebrite / Oxygen / MOBILedit / Berla | Feature-parity benchmark only in the pitch deck | Proprietary, Cellebrite additionally export-controlled | Never imply capability parity (lock-bypass, physical extraction) the open-source stack can't match |

---

## 6. Chain of Custody & Hashing Module (NIST SP 800-101r1 / SWGDE-aligned)

Implement as a first-class module, not an afterthought — this is worth real evaluation-criteria
points and is genuinely cheap to build correctly:

1. **Device intake block**: make/model/OS+build, IMEI/serial/ICCID, carrier — captured once.
2. **Pre-acquisition state snapshot**: locked/unlocked, displayed device time vs. a reference
   clock, battery %, a photo of the visible screen — logged *before* any interaction.
3. **Legal authority field**: warrant/consent/exigency reference + scope limits.
4. **Append-only state-change event log**: every action that alters the device — USB debugging
   enabled, unlock method, helper APK installed, permissions granted, role-holder swaps, root
   obtained — each with timestamp, examiner ID, and justification. This is what replaces the
   (unsupportable) "read-only" claim.
5. **Tool/process metadata**: tool name, version, build — tool behavior changes across versions,
   so this belongs in every case record.
6. **Per-artifact manifest**: SHA-256 (primary — SWGDE's current position paper deprecates
   MD5/SHA1 as sole hashes) computed at the moment of extraction, written immediately into the
   manifest. **Never compute or claim a whole-device hash** — NIST 800-101r1 explicitly says
   back-to-back full-device hashes won't match because mobile devices are constantly live; only
   per-file hashes are expected to be reproducible.
7. **Custody transfer records**: who took possession, when, why.
8. **Triage disclaimer flag**: every generated report is programmatically labeled as a
   triage/preview result, not a full forensic examination — both NIST and SWGDE are explicit that
   field triage is not a substitute for full lab examination, and the report should say so on its
   face.

If this is aimed at Indian law enforcement (the "state forensic laboratory" phrasing suggests it
is), also add a **Section 65B (Indian Evidence Act)-style certificate block** to the generated
report — an examiner declaration, system description, and hash values in the format Indian courts
expect for electronic evidence admissibility. Verify the exact wording against current guidance;
treat this as a strong differentiator for domain-savvy judges, not a legal guarantee.

---

## 7. Team Split (2–3 engineers)

- **Engineer A — Acquisition & Chain of Custody (Python).** ADB orchestration, all three tiers,
  the Collector helper APK (Kotlin — flag this as a new skill area if nobody on the team has
  Android dev experience; mitigate with AI-scaffolded boilerplate), the chain-of-custody/hashing
  module.
- **Engineer B — Parsing & Recovery Engine (Python).** ALEAPP integration, WhatsApp/Signal/
  Telegram-specific parsers, the `sqlite-dissect`/`sqbrite` recovery pipeline, keyword/hash
  flagging.
- **Engineer C (or shared if only 2 people) — Dashboard & Demo (Electron/React/TS).** Forensic
  Preview Dashboard, timeline view, filters, case management, report export, and — critically —
  the live-demo rehearsal and fallback recording. If the team is only 2 people, split this across
  both engineers starting Week 3, after their backend pieces stabilize.

---

## 8. 30-Day Schedule

### Days 1–2 — Lock the foundations
- **Top priority, do this first:** acquire real non-rooted test phones spanning at least two
  Android versions/OEMs (e.g., a Pixel/near-AOSP device plus a Samsung or Xiaomi), and — if at all
  possible — one rootable device for the Advanced/Lab-Mode track. Nearly every subsequent
  empirical claim in this plan needs validation against real hardware; OEM skins (MIUI/HyperOS
  confirmed, likely others) alter stock `pm grant`/scoped-storage behavior in undocumented ways.
- Draft a mock case scenario (fake warrant/consent doc) to drive the legal-authority field and the
  demo narrative.
- AI-accelerated scaffolding: Electron+React+TS shell, Flask service skeleton, repo/CI setup,
  a synthetic test-corpus generator (seeded fake WhatsApp/Telegram/Signal exports, SQLite DBs with
  known deleted rows) so recovery-engine work isn't blocked on device availability.

### Days 3–7 — Acquisition Engine + Chain of Custody
- Tier 0 fully working: `adb pull` of shared storage, `.trashed-*` detection, EXIF GPS parsing,
  `dumpsys location` — dashboard shows first results within seconds of device connect.
- Collector helper APK v1: requests `READ_CONTACTS`/`READ_MEDIA_*`; validate `pm grant` behavior
  empirically on every test device (this is exactly where the research flagged OEM variance).
  Build the separate, explicitly-flagged call-log/SMS role-holder-swap path.
  ALEAPP subprocess wrapper.
- Chain-of-custody module: device intake, pre-acquisition snapshot, append-only event log,
  per-artifact SHA-256 manifest — get this solid early since everything downstream logs into it.

### Days 8–14 — App-Specific Recovery + Deleted-Data Engine
- WhatsApp: media harvesting, guided Export-Chat ingestion flow, evaluate the APK-downgrade
  key-extraction trick against your actual test devices (expect it to be flaky — that's the
  expected result, not a bug in your work).
- Telegram: shared-storage media pull; stage a device with "Save to Gallery" on.
- Signal: `signalbackup-tools` integration behind a consent-based "enter disclosed passphrase" UI
  flow.
- `sqlite-dissect` + `sqbrite` pipeline wired up against whatever plaintext/decrypted DBs the tool
  legitimately obtains; implement the 4-tier confidence badge end to end.

### Days 15–21 — Dashboard, Timeline, Keyword Flagging
- Forensic Preview Dashboard: categorized views (messages/contacts/calls/media/locations),
  keyword + date-range filters, case management, confidence badges surfaced in the UI.
- Bonus: cross-artifact timeline reconstruction (calls + messages + locations, chronological).
- Bonus: keyword/regex flagging + known-hash matching against a sample hash-set.
- Progress streaming (Flask-SocketIO) driving a real 5–10-minute countdown in the UI — this is a
  named evaluation criterion, make it visible and honest.

### Days 22–27 — Cross-Device Hardening
- Re-run the full pipeline on every test device acquired on Day 1–2; fix OEM-specific path/
  permission breakage as it appears (expect it to appear).
- Report generation: NIST/SWGDE-aligned fields, triage disclaimer, optional 65B-style certificate
  block.
- Package as a portable, no-install build (bundled Python via PyInstaller + bundled `adb`
  platform-tools) — this is the realistic version of the "lightweight portable deployment" bonus; a
  full USB-bootable live-Linux distro is very likely out of scope for 30 days and shouldn't be
  attempted at the expense of the core product.

### Days 28–30 — Demo Lock-In
- Freeze features. Pre-stage the actual demo phone with real seeded data (deleted WhatsApp
  messages, Telegram media with Save-to-Gallery on, trashed photos, and — if a rooted device is
  available — call log/SMS/contacts).
- Rehearse the golden path against a stopwatch to hit the 5–10 minute target for real, not
  approximately.
- Record a full fallback video of the entire flow — this is a **live, on-stage demo**, the
  highest-risk demo format the team identified; a recorded fallback is not optional.
- Finish documentation: acquisition method + tier matrix, supported-artifacts matrix (this doc's
  Section 0, essentially), limitations section, sample case folder with hashes/audit log.

---

## 9. Using AI-Assisted Development to Punch Above a Normal 30-Day Scope

The team explicitly wants to lean on this — concrete tactics, not just "use AI":

1. **Scaffold every module skeleton + unit tests before writing the real logic**, using the
   synthetic test corpus from Day 1 — this turns "write a WhatsApp export parser" into "fill in a
   generated stub against generated fixtures," which is both faster and safer.
2. **Generate the synthetic seeded test corpus itself** (fake WhatsApp/Telegram/Signal exports,
   SQLite DBs with deliberately deleted rows in known freelist/WAL positions) so recovery-engine
   work never blocks on physical device availability, and so the team can measure and report a real
   recovery/false-positive rate (Section 4) instead of an anecdotal one.
3. **Draft the documentation deliverable from this plan's research findings directly** — the
   acquisition-method write-up, the supported-artifacts matrix, and the limitations section are
   already substantially written above; don't hand-write 20 pages from scratch.
4. **Offload Electron/React boilerplate** (component scaffolding, Tailwind layout, IPC wiring) so
   human attention stays on the harder forensic-engine problems (Sections 2–4), which are the parts
   judges will actually probe.
5. **Use an agent to generate adversarial test scenarios** for the acquisition pipeline — locked
   device, USB disconnect mid-pull, unexpected OEM permission-grant failures — and run them against
   every test device acquired on Day 1, since OEM variance is the single most-repeated risk across
   every research finding above.

---

## 10. Risk Register

| Risk | Mitigation |
|---|---|
| No test devices confirmed yet (biggest open risk) | Acquire devices Day 1–2, non-negotiable; block on this before deep-building anything device-dependent |
| OEM skins break `pm grant`/scoped-storage assumptions | Empirically validate every permission path per device Days 3–7 and 22–27; never hard-code a single behavior |
| WhatsApp key-extraction trick fails live on stage | Pre-validate against the exact demo device days in advance; label as advanced/bonus; keep a recorded fallback |
| Judges expect Signal/Telegram full recovery like Cellebrite | Preempt in the pitch with the Honest Feasibility Matrix — showing you *know* the limits reads as more credible than a shaky attempt to fake it |
| Team overclaims "read-only acquisition" | Standardize on "minimally-invasive, fully-logged acquisition" everywhere — UI copy, docs, pitch |
| MVT license conflicts with actual use case | Don't vendor MVT code; idea-only reference, flagged Day 1 |
| GPL/AGPL code (`wa-crypt-tools`, `dissect.sql`) creates copyleft exposure if linked in | Shell out as subprocess, or prefer MIT alternatives already listed in Section 5 |
| Live on-stage demo fails | Rehearsed golden path + full recorded fallback, locked by Day 28 |
| 30-day scope creep (Signal/root features eating time from the reliable non-root core) | Sections 0/2/3 define the MVP cutline explicitly — Tier 0/1 + WhatsApp export/media + deleted-data recovery + CoC is the must-ship core; everything root-gated or Signal-cooperative is bonus |

---

## 11. Deliverables Checklist (mapped to the problem statement)

- [ ] Working prototype/demo on a real Android device (non-rooted primary path, root-gated
      advanced mode)
- [ ] Forensic Preview Dashboard demonstration (categorized views, timeline, filters)
- [ ] Sample case folder with integrity hashes and audit log (Section 6 output)
- [ ] Documentation: acquisition method (Sections 2–3), supported artifacts (Section 0),
      limitations (throughout), NIST/SWGDE compliance mapping (Section 6)
- [ ] Bonus: visual timeline reconstruction (Week 3)
- [ ] Bonus: keyword/known-hash auto-flagging (Week 3)
- [ ] Bonus: portable/no-install packaging (Week 4, scoped realistically)
- [ ] Bonus: NIST/SWGDE compliance alignment (built in throughout, not bolted on)
