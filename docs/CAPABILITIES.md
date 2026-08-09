# What SNAGR does (verified, working)

[← back to README](../README.md)

| Capability | Status | Comparable commercial feature |
|---|---|---|
| Tier-0 acquisition (adb pull of shared storage, dumpsys location) | ✅ | UFED logical extraction |
| Acquisition throughput metric (MB/min) | ✅ | MDI "up to 4GB/min" |
| Manual screen capture (read-only framebuffer) | ✅ | Oxygen/MDI screenshot mode |
| Per-artifact SHA-256 hashing + append-only, hash-chained audit trail | ✅ | UFED chain-of-custody |
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
| **Notification history parser** (`dumpsys notification --history`) | ✅ | device activity analysis |
| **Bluetooth device history** (`dumpsys bluetooth_manager`) | ✅ | connected devices analysis |
| **Cell tower history** (`dumpsys telephony.registry`) | ✅ | location data analysis |
| Forensic Preview Dashboard (Electron + React), live progress, ~90 dataset views | ✅ | MDI field dashboard |
| NIST/SWGDE-aligned HTML report + **BSA 2023 s.63** Schedule certificate (Part A/B, dual signature) | ✅ | court-ready reporting |
| **Sealed evidence-package export** (ZIP + SHA-256 verification manifest) | ✅ | evidence export |
| Runs **with no phone** via a synthetic mock corpus (dev + demo fallback) | ✅ | — |
| **Sign-in gate** — single-examiner bearer-token auth in front of the dashboard/API | ✅ | — |

## New in v0.2 — expanded collection & app coverage

| Capability | Tier | Comparable commercial feature |
|---|---|---|
| **Collector APK `dump_all`** — one action captures everything below, then self-uninstalls | 1 (non-root) | Oxygen Android Agent |
| **MediaStore inventory** — every media file's metadata (size, `date_taken`, owner app, dimensions) without pulling the files | 1 | media catalogue before selective pull |
| **Trashed / favorite media detection** (`IS_TRASHED` / `IS_FAVORITE`) + EXIF GPS via `ACCESS_MEDIA_LOCATION` | 1 | recycle-bin / geotag surfacing |
| **Installed-app inventory** with investigative classification (messaging / crypto / dating / browser) | 1 | UFED installed applications |
| **Vault / anti-forensic app detection** (AppLock, Calculator Vault, hiders) | 1 | AXIOM "potentially unwanted apps" |
| **Accounts** (Google / WhatsApp / Telegram / Snapchat identities via AccountManager) | 1 | User Accounts |
| **Calendar events**, **app-usage telemetry** | 1 | Organizer / app usage |
| **Instagram Direct recovery** — `direct.db` live + deleted DMs, µs timestamps, + DYI-export ingest | 2 (root/image) | Instagram `direct.db` decode |
| **Snapchat recovery** — `arroyo.db` (schema-less protobuf), WAL/freelist ephemeral carve | 2 (root/image) | arroyo.db decode |
| **Dynamic App Finder** — auto-classifies chat tables in *unknown* app SQLite DBs | 0–2 | Cellebrite App Genie / Magnet Dynamic App Finder |

Where we deliberately **don't** claim parity (and say so, honestly): cloud extraction,
lock-screen bypass, physical/chip-off imaging, and defeating Signal's hardware-backed
Keystore — none are achievable non-root in scope. See [`docs/IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) §0.

## New in v0.3 — integrity, encryption posture, and persistent artifacts

Driven by a deep-research + adversarial-audit pass; the full findings live in
[`engine/docs/PRODUCTION_READINESS.md`](../engine/docs/PRODUCTION_READINESS.md).

| Capability | Tier | Why it matters |
|---|---|---|
| **FBE / AFU-BFU encryption posture** determined before any root pull | 0 | *Root is not decryption.* Reports "present, encrypted, inaccessible (BFU)" instead of "not found" |
| **Tamper-evident audit log** — every entry hash-chained to its predecessor | — | An edited/reordered/deleted audit line breaks verification at a known line number |
| **Post-acquisition device snapshot + verified Tier-1 teardown** | 1 | "Unverified" is a distinct verdict from "clean" |
| **Deletion detected as its own evidence class** | 0–2 | Rendered apart from carved content, false-positive causes listed |
| **SQLite overflow-page chains** followed during carving | 0–2 | Long messages no longer silently truncated |
| **Rollback-journal (`-journal`) recovery** alongside WAL | 0–2 | Pre-deletion page images for non-WAL databases |
| **Non-root live Wi-Fi / Bluetooth bond store / App presence & execution** | 0/2 | The realistic non-root/root surface, honestly scoped |
| **Structural anti-forensics** (work profile, dual-app clone, factory-reset trace) | 2 | Observations only — every finding lists innocent explanations |
| **Tool self-validation** — known-answer report + CFTT coverage matrix, per acquisition | — | Includes a deliberate negative control that *must* fail |

## New in v0.4 — radio artifacts and their time claims

Full detail: [`docs/NETWORK_ARTIFACTS.md`](NETWORK_ARTIFACTS.md).

| Capability | Tier | Why it matters |
|---|---|---|
| **Wi-Fi credential recovery across every Android era** — APEX (11+), pre-APEX (9–10), `wpa_supplicant` (≤8), all probed, all parsed | 2 | Probing only the Android 9 path on a modern device reports "no saved networks" — a finding, not a miss |
| **Own-hotspot credentials** (`WifiConfigStoreSoftAp.xml`) | 2 | The SSID+passphrase this device *offered* — matches directly against another device's saved list |
| **Saved vs actually joined** (`HasEverConnected`) + per-network provenance | 2 | "Was at this address" and "was told the password" stop looking the same |
| **Wi-Fi timestamps kept under their original field name** | 2 | Android stores no "last connected"; nothing is relabelled into one |
| **Bluetooth OPP transfer history** (`btopp.db`) — peer, file, bytes, outcome, wall-clock time, deleted rows carved | 2 | The only Bluetooth artifact that proves an *active link* at a stated time |
| **Bluetooth connection-recency ranking** (`bluetooth_db`) | 2 | `last_active_time` is a counter — exposed as a rank, never as a date |
| **Hotspot / tethering posture**, tri-state | 0 | "Not reported by this build" is distinct from "the hotspot was off" |
| **USB cable state, pre- and post-acquisition** | 0 | A cable pulled mid-run explains a truncated pull |
