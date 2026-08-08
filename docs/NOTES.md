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

## Known gaps & unwired scaffolding

In keeping with this project's own honesty model, applied to itself:

| Item | State |
|---|---|
| `engine/security/`, `engine/analytics/`, `engine/integration/`, `engine/advanced_forensics/`, `engine/triage/notifications/` | Present on disk, **zero import references anywhere in the codebase** — not called from `pipeline.py`, `server.py`, or each other. Not part of the working pipeline. |
| `TWILIO_*` / `SMTP_*` env vars | Read by the unwired `notifications/` module above — configuring them does nothing today. |
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
