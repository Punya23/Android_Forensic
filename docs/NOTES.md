# Forensic soundness, WhatsApp module, and known gaps

[← back to README](../README.md)

## Forensic soundness notes

- **No "read-only" claim.** Acquisition is described as *minimally-invasive, fully-logged*.
  Every adb/pm/cmd invocation is written to `cases/<id>/audit.jsonl` with `alters_device`.
- **Per-file SHA-256**, computed at extraction time — never a whole-device hash
  (irreproducible on a live device per NIST SP 800-101r1 §3.4).
- **Confidence tiers.** Live data and carved data are never shown with the same weight;
  carved rows carry `source file · page · offset` provenance for independent verification.
- **Triage disclaimer** is stamped on every report — this is a preview, not a full
  examination.
- **Absent is not the same finding as inaccessible.** A credential-encrypted path that
  could not be read is reported as present-and-encrypted, never as missing.
- **"Could not check" is never rendered as "checked and clean."** Teardown verification,
  hash verification, and the audit chain each have a distinct *unverified* state.
- **The same rule now applies to the screen.** `triage/capabilities.py` resolves every
  dataset the dashboard can request into one of `populated` / `empty` / `not_collected` /
  `inaccessible` / `planned`, and the view renders that reason instead of a blank panel.
  An empty dataset is only reported as a finding about the device when something
  corroborates that its stage actually ran — for the datasets the pipeline writes
  unconditionally, an empty file proves nothing and is not allowed to read as "checked".
- **Not independently validated.** The self-validation harness runs real known-answer tests
  (including a negative control designed to fail), but the tool has never been tested
  against a ground-truthed reference image by a tester independent of its developer.
- **False-precision guards, actively maintained.** A zero-filled EXIF GPS tag decodes to
  `0.0, 0.0` — a real point off West Africa, not "no data" — and is explicitly excluded at
  the parser, the report, and the dashboard, not just one of the three (fixed 2026-08-08
  across `parsers/exif.py`, `report/html_report.py`, `views/Locations.tsx`, `views/Media.tsx`
  after it surfaced as a real report defect).
- **Deliberately unbuilt.** No slack-space/unallocated/raw-block carver for `/data` (on
  Android 10+ that yields FBE ciphertext dressed up as recovered data), no bootloader-unlock
  path, no offline lock-screen/FBE key attack, no SQLCipher decrypt attempt. Reasoning in
  `engine/docs/PRODUCTION_READINESS.md` → "Do NOT build".

See [`docs/IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) for the full feasibility
matrix (what is/isn't achievable non-root for WhatsApp/Signal/Telegram).

---

## WhatsApp recovery module (deep dive)

The engine includes a multi-layer WhatsApp forensic recovery pipeline covering live data,
deleted records, encrypted backups, and media cataloguing.

### Data extraction

| Feature | Parser | Details |
|---|---|---|
| WhatsApp export (`.txt`, `.zip`) | `whatsapp_txt.py` | Bracket & dash formats; multi-locale timestamps |
| WhatsApp live DB | `whatsapp_db.py` | Schema-aware; version-tolerant JOIN query |
| WhatsApp encrypted backups | `whatsapp_batch.py` | crypt15 (AES-GCM) / crypt14/12 (AES-CBC) |
| WhatsApp Media folder | `media.py` | Images, Video, Audio, Documents, GIFs, Stickers |

### Data recovery

| Technique | Module | Confidence |
|---|---|---|
| SQLite freelist / freeblock carving | `recovery/` | `CARVED_PARTIAL` |
| WAL frame reconstruction | `whatsapp_e2e.py` | `RECOVERED_VERIFIED` |
| Rowid gap detection (deletion proof) | `recovery/` | `DELETION_DETECTED` |
| Encrypted backup decryption (key required) | `whatsapp_e2e.py` | `RECOVERED_VERIFIED` |

### Confidence badging

| Badge | Value | Meaning |
|---|---|---|
| 🟢 Live | `Confidence.LIVE` | Normal query result from an intact table |
| 🟡 Recovered | `Confidence.RECOVERED_VERIFIED` | Intact WAL frame or un-checkpointed page |
| 🟠 Carved | `Confidence.CARVED_PARTIAL` | Signature-matched over freeblock/unallocated space |
| 🔴 Deletion | `Confidence.DELETION_DETECTED` | Rowid gap proves deletion; no content recovered |

### Advanced analysis

Social graph (link analysis across channels) · burst detection · response-time analysis ·
anomaly detection (z-score volume spikes, quiet-hours activity) · timeline reconstruction.

```python
# Full CLI triage with E2E recovery and advanced analysis
python -m triage.cli acquire --mock _corpus/device_A --case CASE-001 \
    --examiner "Insp. R. Sharma" --authority "Search Warrant #MH-2026-4471"
```

```python
# Python API
from triage.parsers import parse_whatsapp_db, recover_e2e_messages, simulate_e2e_decryption_workflow
from triage.advanced import AdvancedForensicFeatures

msgs = parse_whatsapp_db(Path("msgstore.db"))                      # live messages
report = simulate_e2e_decryption_workflow(Path("msgstore.db.crypt15"))  # WAL/freeblock, no key
aff = AdvancedForensicFeatures()
graph = aff.analyze_social_graph(msgs)
```

---

## Defects found by making the demo real (2026-09-01)

Filling the mock corpus with realistic `dumpsys` output surfaced four defects that an
empty corpus had been hiding. Three of them only ever fired **on a real device** — the
demo could not reach them, so nothing looked wrong.

| Defect | Effect before the fix | Fix |
|---|---|---|
| Six dashboard views fetched with a bare `fetch` instead of the authed client | Snapchat, Instagram, Telegram, WhatsApp Backup, Discovered Chats and the shared `ChatView` all got `401` after the sign-in gate landed and rendered as empty tabs. Recovered Snapchat messages existed in the case folder the whole time | Every one routed through `api.*`, which attaches the bearer token (`app/src/lib/api.ts`) |
| `wifi_live` wrote dataclasses straight to JSON | `Object of type WifiConnectionState is not JSON serializable` — the stage failed on **every device that had Wi-Fi state to report**. An empty capture serialised fine, so a corpus with no canned `dumpsys wifi` never triggered it | `wifi_live_json()` flattens the collector result before `write_derived` |
| `parse_current_location` only understood `latitude=` / `longitude=` | `dumpsys location` prints `Location[fused 19.07,72.87 hAcc=12 ...]` on every modern build, so the Maps/location-history view was empty on real hardware | Bracket form parsed first, with `hAcc` as accuracy. `et=` is elapsed-since-boot and is deliberately **not** converted into a timestamp |
| The registry only synced at engine startup | A case acquired with `python -m triage.cli` — or copied in from another workstation — was missing from Case History with no indication it existed | `sync_registry()` runs on the registry read path; it only touches folders with no row |

The corpus itself was the fifth finding: it shipped one canned shell reply
(`dumpsys location`), so roughly twenty views were blank for a reason that had nothing to
do with the device. `engine/tools/corpus_shell.py` now supplies the rest, shaped the way
the framework actually prints them, and `tests/test_corpus_shell.py` asserts on *parsed
rows* so a fixture that stops matching its parser fails the suite instead of quietly
emptying the demo.

---

## Defects found building the deep-agent / cross-case features (2026-09-03)

Two more, found the same way as above — by running the new code against real
acquisition data instead of hand-built test fixtures, which is exactly why the mock
corpus and every acquisition in this codebase is real (adb-derived or realistically
shaped), never a shortcut sample.

| Defect | Effect before the fix | Fix |
|---|---|---|
| Mixed naive/aware timestamps across datasets | Real acquisitions mix a naive `2026-07-06T21:00:04` (one writer's format) with an aware `2025-07-06T18:28:20Z` (another's) inside the *same case's own findings*. Subtracting one from the other raised `TypeError: can't subtract offset-naive and offset-aware datetimes` — caught by `investigate()`'s own try/except and reported as `blocked`, so it never crashed an acquisition, but the location-correlation hypothesis could never actually answer on real data | `contradiction.parse_iso()` now always returns a timezone-aware datetime (a naive result is assumed UTC, matching this codebase's timestamp convention everywhere else); `investigator.py` reuses it instead of keeping a second, differently-behaved copy |
| WhatsApp JIDs matched the email regex | `case_reference.py`'s email extractor is a standard `word@word.tld` pattern; a WhatsApp JID (`<number>@s.whatsapp.net`, surfaced verbatim in a carved/recovered row's raw text) is shaped exactly like one, so a real phone number was being reported as an "email" and linked across cases under the wrong category — the same class of mislabelling this module was rewritten to fix for UPI IDs in the first place | `_is_real_email()` excludes known messaging-app-internal JID domains (`s.whatsapp.net`, `g.us`, `c.us`, `lid`, `broadcast`); a JID with a real phone number embedded (`@s.whatsapp.net`/`@c.us`) is recovered as a phone number instead of being silently dropped |

Also caught in review, before it shipped: the original `check_message_vs_location`
(part of the unwired `contradiction.py` this session rewrote) flagged *any* message
containing "at home" within five minutes of *any* GPS fix, with no comparison to where
home actually is — its own code comment admitted as much ("mock logic"). A truthful
"at home" message would have fired identically to a false one, with a hardcoded `HIGH`
severity either way. It was not wired as-is; `check_message_vs_home` replaces it,
comparing against this device's own confidence-scored inferred home cluster (from
`place_identification.py`, already computed, never previously used for this) and
firing only on genuine distance from it. Caught during implementation, not after —
included here because it's the same "read the code, don't trust the summary of it"
discipline the rest of this file is built on.

---

## Known gaps & unwired scaffolding

In keeping with this project's own honesty model, applied to itself:

| Item | State |
|---|---|
| `engine/security/`, `engine/analytics/`, `engine/integration/`, `engine/advanced_forensics/` | Present on disk, **zero import references anywhere in the codebase** — not called from `pipeline.py`, `server.py`, or each other. Audited (2026-09) file by file: every one is a **fabrication stub**, not merely unfinished — `security/audit.py`'s `verify_integrity()` always returns `True` regardless of file content; `security/hsm_integration.py`'s `sign_evidence()` returns the literal string `"hsm_digital_signature_placeholder"` for any input; `security/legal/section_65b.py` certifies against the Indian Evidence Act 1872 s.65B, repealed and replaced by BSA 2023 s.63 (the working report generator already made this exact fix once, per P2-1 below — this module would silently reintroduce it); `analytics/vision/face_recognition.py` and `object_detection.py` return an identical hardcoded detection for every image regardless of content; `analytics/vision/ocr_tamper.py`'s tamper detector always reports "low probability" — worse than no detector, since it would falsely reassure an examiner a manipulated image is clean; `advanced_forensics/filesystem/file_recovery.py` is exactly the raw-image slack-space carver this project's own "do NOT build" list forbids (production-readiness doc, below), and `advanced_forensics/memory/volatility_runner.py` targets full physical-memory dumps this tool has no acquisition path to obtain, with field values (`pid: 4, name: 'System'`) lifted from a Windows kernel process template. None of this is wired, and none of it should be — see `docs/CAPABILITIES.md` "New in v0.7" for what was built instead where a real, honest version of the underlying idea existed. |
| `engine/triage/notifications/` | The one exception to the row above: audited and found to be **real, working code** (SMTP/Twilio/Slack/Teams clients, the dependencies already declared in `requirements.txt`) — just never called. Wired in v0.7 (opt-in, off by default) to fire on acquisition completion; see `docs/CAPABILITIES.md`. |
| `deploy/docker-compose.yml` | References a missing `Dockerfile.gateway`; describes an unrelated architecture. Not a working deployment path. |
| Electron packaging (`electron:build`) | No electron-builder config committed; the packaged-app engine-bundling step (`resources/engine/triage-engine`) has no build step producing it. |
| APK release signing | No `signingConfigs` — `assembleRelease` would produce an unsigned APK. |
| CI/CD | No `.github/workflows/` — nothing is automated. |
| License | No `LICENSE` file in the repo — add one before public distribution. |
| Independent validation | Self-validation harness exists and runs; independent (non-developer) validation against a ground-truthed reference image has not happened. |
| Android `applicationId`/package (`io.erakshak.collector`) | Deliberately **not** renamed to SNAGR — the app label, project name, and every user-facing string are rebranded, but the package id is duplicated in 6+ places across Kotlin, Python (`device_state.COLLECTOR_PACKAGE`, `pipeline.py`, `retrieve_recordings_notifications.py`), and test assertions with no single source of truth; moving it means relocating the Kotlin source tree and touching every one of those call sites atomically. Real work for zero user-visible payoff — nobody but `adb` sees this string. |
| Audit-chain genesis preimage (`GENESIS_PREIMAGE = b"eRakshak-audit-chain/v1/genesis"`, `forensics/audit_chain.py`) | Deliberately **not** renamed — its SHA-256 is the hardcoded `GENESIS_HASH` every case's audit chain roots to. Changing the preimage would silently break `verify_chain()` for every case collected before the rename. The string's content is arbitrary either way; keeping the old one costs nothing and preserves verifiability. |

This list exists so the next person (or the judges) reads the same picture the code does —
consistent with never claiming more than what's actually wired up.
