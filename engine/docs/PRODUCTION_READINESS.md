# eRakshak — Production-Readiness Assessment & Roadmap

_Synthesised from a 12-axis deep web-research + adversarial-verification + 5-dimension codebase audit workflow. Every finding below was verified against the code._

**Coverage:** 12 research axes · 59 claims verified · 29 refuted/corrected.

> Generated 2026-07-27 from workflow wf_959fec02-74c. Durable record of an ephemeral run.


**Progress:** ALL 23 roadmap items (P0, P1, P2, P3) are implemented and tested. See the per-item ✅ markers below.

The do-not-build list at the bottom remains authoritative and unbuilt: there is still no slack-space /
unallocated / raw-block carver, no bootloader-unlock path, no LSKF/FBE key attack, no claim of deleted-record
recovery on a non-rooted device, and no attempt to decrypt SQLCipher app content. Those are dead ends, not
backlog.

What closing the roadmap does NOT establish: the tool has still never been validated against a ground-truthed
reference image by an independent tester, and it has no characterised error rate. The self-validation harness
(P2-4) produces the report structure and runs real known-answer tests, but SWGDE 18-Q-001 recommends the tester
be independent of the developer — a tool testing itself cannot satisfy that. Treat the roadmap as complete and
the instrument as unvalidated until that work is done.


---
## Verdict

eRakshak's honesty model and tiered architecture are genuinely well-conceived, and — importantly — it already invests in the RIGHT recovery surface (application-layer SQLite: freelist, WAL, MediaStore trash) rather than the mythical 'slack space' the user is chasing. But it is not production-ready. Its central integrity guarantee is silently broken (hash verification checks ZERO files because custody writes a JSON list keyed `sha256` while the verifier expects a dict keyed `sha256_hash`), it silently drops recoverable evidence within its own correct scope (rollback -journal sidecars pulled but never parsed; in-page freeblocks carved as text only, losing rowid/columns/typed values), it inflates recovered rows via a dead-code dedup bug (sqbrite_cross_check is fed primary_rows=[] so nothing is de-duplicated), and it over-labels unvalidated WAL frames as 'RECOVERED_VERIFIED' with no salt/checksum/commit check — a direct honesty-model violation, since stale post-checkpoint frames must be lower-confidence. Its compliance scaffolding cites a repealed statute (IEA s.65B, not BSA s.63) and has no validation regime and no characterized error rate. Fix the P0 correctness/integrity defects before ANY evidentiary use, then close the AFU/BFU honesty gate and BSA s.63 compliance, then add breadth. And do NOT build a slack/unallocated-space carver: on any Android 10+ device it would produce FBE ciphertext noise dressed up as 'recovered data' — exactly the overstatement the honesty model exists to prevent. Verified 8/8 audited defects directly against the code; the audit is accurate.


---
## Honest answers to the direct questions

### Is it a production-level forensic tool?

Not production-ready as a court-grade instrument today — but the bones are genuinely strong and it is well ahead of typical homebrew tools. Already right and hard to get right: SHA-256+MD5 per-artifact hashing at ingest (per-artifact, NOT whole-device — correct per NIST SP 800-101r1, since live-device whole-image hashes are non-reproducible); a disciplined Tier 0/1/2 state-change model with alters_device audit logging; an honest acquisition disclaimer that explicitly denies any write-blocking; WAL sidecar pulling (already fixed, commit 6f6eda6); a confidence enum (live/recovered/carved/deletion) applied across renderers; UTC timestamp normalization; graceful non-raising degradation. What BLOCKS an evidentiary claim: (1) the tool's core integrity guarantee is silently non-functional — hash_verification.load_manifest expects a dict wrapper and key `sha256_hash`, but custody writes a top-level JSON LIST with key `sha256`, so verify_all_hashes reads ZERO files for every real case and returns 'UNKNOWN', never 'TAMPERED' (same wrong schema in integrity_report/hash_comparison/hash_timeline); until fixed, the tool does NOT actually verify evidence integrity while the UI implies it does; (2) the report's hash-verification section calls _generate_hash_verification_section, which is defined NOWHERE, and a bare `except: pass` swallows the NameError, so the report silently omits integrity results; (3) the audit log is not tamper-evident — no hash-chaining/HMAC/signature, so from the case folder alone audit edits are undetectable; (4) no recompute-and-verify at export. Against the standards: NIST CFTT — no MDT-CA coverage mapping and no characterized error rate (Daubert's weak spot); SWGDE 18-Q-001 — no known-answer validation report against a ground-truthed reference image (e.g. Josh Hickman); ISO 17025/27037 — the tool can only PROVIDE validation/versioning/hashing to a lab (its per-artifact hashing is the right substrate) but that validation package doesn't exist yet; BSA s.63 — the certificate cites the REPEALED IEA s.65B (1872) rather than BSA 2023 s.63 (in force 2024-07-01), which for an Indian tool requires the Schedule form, DUAL signatures (custodian + expert), and a hash + IMEI/MAC/serial. Add the missing AFU/BFU/FBE state capture (an honesty gate) and the absence of a post-acquisition state snapshot to show a Tier-1-modified device was returned to its found state. Net: fix the P0 integrity/evidence-loss defects first (they lose or misrepresent evidence while the UI claims completeness), then the AFU/BFU gate and BSA s.63, then a real SWGDE/CFTT validation regime. Position it as fast, transparent FIELD TRIAGE that complements a CFSL/C-DAC workflow — not a Cellebrite/GrayKey replacement, and not yet a CFSL-grade instrument.

### Slack space / "deleted data stays on the chip until overwritten"

Your mental model — 'delete only removes the pointer, the bytes stay on the chip until overwritten, so acquire the slack/unallocated space' — is roughly a decade out of date and is substantially FALSE on any device that shipped with Android 10+. It fails at four independent layers at once: (1) ext4 unlink zeroes the extent tree in the inode, so metadata undelete is dead — only raw carving or a narrow jbd2-journal window remain, both needing a raw image you cannot get; (2) F2FS — the default /data filesystem on most flagships since ~2018 (Pixel 3) — is log-structured, so an OVERWRITE does leave the prior version as an 'invalid' block (the one place your model survives), BUT real-time discard is default-on and vold runs GC_URGENT + device manual_gc every idle-charging window (~420s budget), so the window is HOURS not days and a phone left powered on a charger in the evidence locker is actively destroying it — power it off; (3) the eMMC/UFS FTL makes a logical block address a lookup key, not a physical location — no host command addresses a NAND page, so 'slack space'/'physical block' reasoning is meaningless on managed NAND, and TRIM/discard makes discarded content unreadable from every interface an examiner can reach (read-back is device-undefined zeros-or-garbage, never the old data — DZAT/DRAT are SATA/NVMe concepts that don't apply); (4) FBE is mandatory on Android 10+, so raw /data unallocated is AES-XTS ciphertext, Android 11+ dm-default-key encrypts even directory structure/filenames/sizes, and the per-file key is HKDF-derived from a 16-byte nonce in the inode — free the inode and the nonce dies, so the surviving ciphertext is cryptographically ORPHANED, unrecoverable in principle even with the CE master key. There is also no supported path to the raw device: SELinux blocks the shell user from /dev/block/by-name/userdata, and bootloader unlock mandates a factory reset that wipes the evidence. Even Cellebrite's best answer for FBE devices — full-file-system extraction — by construction contains ZERO unallocated space. File slack specifically is dead (4K block = page size with page-cache tail zero-fill; F2FS allocates fresh pages; FBE turns residue into noise under a different key); the only legitimate 'slack' finding is ext4 directory-block slack, which yields deleted FILENAMES, not content. Android 10 is the hard cutoff — below it raw carving can sometimes be justified, at/above it never, absent keys + a raw image. WHAT ACTUALLY IS RECOVERABLE is application-layer, not storage-layer: SQLite WAL and rollback journals (pre-deletion page images until checkpoint — highest yield); SQLite freelist/freeblock carving but ONLY for app-BUNDLED SQLite (WhatsApp/SQLCipher, Signal, Chromium), because AOSP framework SQLite is compiled with SECURE_DELETE + AUTOVACUUM=1, making SMS/contacts/calllog freelist recovery near-dead (report per-database, not blanket); MediaStore .trashed-<expiry> files (30-day window, a REAL deletion timestamp — already implemented, commit 5385fe5); Google Photos/Samsung Gallery recycle bins; WhatsApp crypt15 backups predating the deletion; thumbnails/Glide caches; notification history; soft-delete tombstone rows. So do NOT build a slack/unallocated/block carver — it would present ciphertext noise as 'recovered data.' eRakshak is already correct here: its carving is 100% SQLite-internal and it has no block-level carver. The gap is the opposite — it leaves recoverable evidence on the table WITHIN that correct scope (rollback journals pulled but never parsed; in-page freeblocks carved as text only). Report /data unallocated space as 'not acquired, not acquirable — fixed capability limitation', never as a per-case 'nothing found.'

### Are Wi-Fi and Bluetooth connections fetched?

Both are fetched, but thinly, and each has a load-bearing gap. WI-FI: the engine pulls SAVED CREDENTIALS ONLY — wpa_supplicant.conf (≤Android 8, plaintext PSK) and WifiConfigStore.xml (≥Android 9: SSID, PreSharedKey, AllowedKeyMgmt) — and it is ROOT-ONLY (Tier 2, explicit `su -c id` gate in pipeline.py _run_tier2_wifi:2599; returns empty on a non-rooted device). It has a dashboard view. Missing: (1) NO connection history — the WifiConfigStore fields that matter (HasEverConnected, IsMostRecentlyConnected, NumRebootsSinceLastUse, ConnectChoiceTimeStamp, and critically RandomizedMacAddress) are ignored, and WifiNetwork.timestamp is declared but never populated; (2) NO non-root path — dumpsys wifi/netstats/connectivity give current SSID/BSSID, the saved list, scan-result BSSIDs and hour-bucketed per-network connected-time WITHOUT root and are the entire realistic non-root Wi-Fi surface, yet the engine captures none of it; (3) NO scan history, NO BSSID geolocation. Honesty caveats to bake in: WifiConfigStore has NO reliable per-join timestamp (only recency flags + ConnectChoiceTimeStamp, a preference event, not a join) — precise 'joined X at T' must come from volatile netstats captured live before shutdown and be labeled approximate; and on Android 10+ MAC randomization means the router logged a per-SSID RandomizedMacAddress, not the hardware MAC — that device-side field is the ONLY bridge back to router/ISP logs, and the engine discards it. On Android 10+ v3 stores the PSK is often keystore-encrypted, so password recovery is frequently dead — don't promise it. BLUETOOTH: NON-ROOT only, `dumpsys bluetooth_manager` -> per-device mac/name/bond_state/connected/last_seen(best-effort)/device_class/is_paired. Missing: (1) the crown-jewel bond store /data/misc/bluedroid/bt_config.conf is NEVER read — there is no root-tier BT acquisition at all — so persistent bonds, per-device pairing timestamps, AddrType, and link keys are absent, and dumpsys is MAC-REDACTED (xx:..:AB:CD) on Android 8+/11+; (2) NO MAC OUI vendor resolution despite the docstring claiming it; (3) Bluetooth AND cell-tower events never reach the unified timeline (build_bluetooth_timeline/build_celltower_timeline are defined but never called in pipeline.py) and have NO dashboard view (API-reachable, unrendered). The #1 overstatement to prevent when bt_config.conf is added: its timestamps are adapter-setup / bond-write, NEVER a 'connection' or co-location time — a bond proves the two devices paired once, not that they were together at a later moment; corroborate with app DBs (carservicedata.db, Samsung subBuffer.log) for an actual connection time.

### Rooted vs non-rooted phones

NON-ROOT (Tier 0 + Tier 1) yields: Tier 0 (zero state change) — /sdcard shared storage (DCIM, Pictures, Download, Android/media/com.whatsapp media), read-only dumpsys (location/notification/bluetooth/telephony), a framebuffer screenshot; NO /data/data. Tier 1 (state-changing, via the io.erakshak.collector helper APK) — contacts, call log, SMS, media inventory, installed apps+permissions, accounts, calendar, usagestats, pulled through the app's OWN ContentProvider/MediaStore queries. This is the only non-root route to app-scoped structured data and it is LIVE ROWS ONLY — content providers never expose the SQLite freelist/WAL, so there is NO deleted-record recovery on a non-rooted device, full stop (claiming otherwise is the single most dangerous overstatement a triage tool can make). The hard ceiling matches the research exactly: app-private /data/data DBs (WhatsApp msgstore, Signal, browser) are sealed by scoped storage + FBE + UID isolation; adb backup is dead on Android 12+; run-as needs a debuggable app. One code-level overclaim: config comments label the Tier-1 SMS/call-log flows 'role-swap', but the code only does `pm grant READ_SMS/READ_CALL_LOG` with no RoleManager change — the label is wrong (and per research an adb-installed agent gets restricted permissions allowlisted, so it may not even need the role); fix the comment. ROOT (Tier 2) yields app-private DBs via `su -c cp`->/sdcard->pull, WITH -wal/-shm/-journal sidecars: Telegram cache4.db, Instagram direct.db, Snapchat arroyo.db, Wi-Fi config, WhatsApp crypt15 backups. The decisive missing concept: ROOT IS NOT DECRYPTION. On FBE (mandatory Android 10+) a root shell reads /data as ciphertext with encrypted filenames until first unlock — BFU exposes only Device-Encrypted data (telephony.db, accounts_de.db, WifiConfigStore, bt_config), AFU exposes Credential-Encrypted app sandboxes. The engine never determines AFU vs BFU or ro.crypto.type, so on a BFU device it will `su cp` a CE sandbox, get ciphertext/empty, and report 'not found' instead of the correct 'present but cryptographically inaccessible (BFU).' That AFU/BFU field IS the honesty model for rooted acquisition and it is entirely absent. Root also does not defeat the hardware Keystore: Signal/Threema SQLCipher DBs pulled with root remain undecryptable (keys are non-exportable, boot-bound), so they must be reported 'encrypted-present, content-not-recoverable', not silently dropped.


---
## Roadmap

Effort/feasibility/root tags are the audit's own. Status added by us.


### P0-correctness

#### P0-1 — Fix manifest schema mismatch so hash verification actually runs ✅ DONE (commit f2d2c74)

`feasibility=proven  effort=small  requires_root=no`

- **Why:** custody.py:208 writes a top-level JSON LIST with key `sha256`; hash_verification.load_manifest does data.get('artifacts',[]) (expects a dict wrapper) and reads artifact.get('sha256_hash') (wrong key). On a list, .get raises AttributeError, is swallowed, returns [] -> verify_all_hashes checks ZERO files and reports 'UNKNOWN', never 'TAMPERED', for every real case. The tool's core integrity guarantee is decorative. Same wrong schema in integrity_report.py, hash_comparison.py, hash_timeline.py.

- **Files:** engine/triage/forensics/hash_verification.py, engine/triage/forensics/integrity_report.py, engine/triage/forensics/hash_comparison.py, engine/triage/forensics/hash_timeline.py

- **Acceptance:** On a real case folder, verify_all_hashes returns total_files == manifest artifact count; status INTACT when untouched and TAMPERED when one stored artifact byte is flipped. A regression test flips a stored file and asserts detection. A single shared manifest-reader helper handles the list schema and `sha256` key.


#### P0-2 — Parse rollback journals (-journal), not just -wal, in recovery ✅ DONE (commit 0d039f4)

`feasibility=proven  effort=medium  requires_root=no`

- **Why:** sqlite_recovery._recover_from_wal opens only `<db>-wal` (line 624). The -journal sidecar IS pulled to disk (priority.py:94, _colocate_sqlite_sidecars) but the recovery engine never reads it. For rollback-mode (non-WAL) DBs the pre-deletion page images live in the journal — that entire deleted-content surface is collected and then silently ignored. Evidence loss inside the tool's own correct scope.

- **Files:** engine/triage/recovery/sqlite_recovery.py

- **Acceptance:** A rollback-journal-mode fixture DB with deleted rows recovers those rows from the -journal with provenance 'journal page N'; recover_deleted_rows reads both -wal and -journal; a unit test covers a TRUNCATE/PERSIST journal left on disk.


#### P0-3 — Structured record recovery for in-page freeblocks and page-unallocated (not text-only) ✅ DONE (commit 0d039f4)

`feasibility=proven  effort=medium  requires_root=no`

- **Why:** sqlite_recovery routes freeblock + unallocated regions through _carve_text_runs (a single anchored text string, CARVED_PARTIAL, no rowid/columns/typed values), while structured _carve_region is applied only to freelist pages and WAL frames. Deleted cells whose header survived in a live page's freeblock lose all structure and are severely under-recovered — exactly the intact-header cells FQLite recovers fully.

- **Files:** engine/triage/recovery/sqlite_recovery.py

- **Acceptance:** A DB with an in-page freeblock deletion recovers the row's rowid + typed columns via structured carve, falling back to text only when the first-column serial type is destroyed; confidence downgrades per the FQLite taxonomy (intact=recovered, front-clobbered=partial, first-serial-destroyed=unrecoverable/no content emitted).


#### P0-4 — Fix the sqbrite dedup (pass real primary fingerprints) ✅ DONE (commit 2dcf8cd)

`feasibility=proven  effort=small  requires_root=no`

- **Why:** pipeline.py:755 builds `primary` from recovered_rows, then line 756 calls sqbrite_cross_check(stored, primary_rows=[]) with an EMPTY list, so the fingerprint set is empty and NO primary-vs-sqbrite dedup occurs. Every sqbrite hit is appended, inflating and duplicating recovered rows while the module claims to surface only rows the primary pass missed — an overstatement of recovery volume.

- **Files:** engine/triage/pipeline.py

- **Acceptance:** sqbrite_cross_check receives the actual primary rows for that DB; a row found by both engines appears once in merged output; a unit test asserts zero duplicate fingerprints post-merge.


#### P0-5 — Validate WAL frames (salt/checksum/commit) and stop labeling unvalidated carves RECOVERED_VERIFIED ✅ DONE (commit 2dcf8cd)

`feasibility=proven  effort=medium  requires_root=no`

- **Why:** _recover_from_wal reads EVERY frame blindly (no salt match, no cumulative-checksum verify, no commit-frame detection) and labels each carve RECOVERED_VERIFIED (line 652). Stale frames from a superseded WAL generation (post-checkpoint salt reset) are carved and presented as verified — a direct honesty-model violation; research is explicit that stale frames must be lower-confidence. Freelist carves are likewise labeled verified with no overwrite check.

- **Files:** engine/triage/recovery/sqlite_recovery.py

- **Acceptance:** WAL header Salt-1/Salt-2 and per-frame cumulative Fibonacci checksum are verified; frames matching current salts+checksum -> RECOVERED_VERIFIED, mismatched/stale -> CARVED_PARTIAL; a test with a reset WAL (new salt) demonstrates the downgrade; freelist carves with reuse/overlap are downgraded to partial.


#### P0-6 — Define/restore the report hash-verification section (fix swallowed NameError) ✅ DONE (commit f2d2c74)

`feasibility=proven  effort=small  requires_root=no`

- **Why:** report.py:131 calls _generate_hash_verification_section, which is defined NOWHERE; the bare `except Exception: pass` at 132-133 swallows the NameError, so the examiner-facing triage report silently omits any hash-verification result. Combined with P0-1, integrity is both broken and invisible.

- **Files:** engine/triage/report.py

- **Acceptance:** The report renders a hash-verification section showing per-artifact INTACT/TAMPERED and an overall status derived from the fixed verify_all_hashes; removing/renaming the function fails a test rather than silently passing; the bare except is narrowed so a missing section is logged, not hidden.


#### P0-7 — Recompute-and-verify artifacts at export/seal time ✅ DONE (commit f2d2c74)

`feasibility=proven  effort=small  requires_root=no`

- **Why:** export.py copies stored manifest hashes into VERIFICATION.txt and hashes audit.jsonl, but never recomputes the artifact files before sealing. A file altered/corrupted between ingest and export is packaged with its original (now-wrong) hash and no mismatch is flagged — the sealed evidence bundle can silently disagree with its own manifest.

- **Files:** engine/triage/export.py

- **Acceptance:** export recomputes each artifact SHA-256, compares to the manifest, and refuses to seal (or loudly flags in VERIFICATION.txt) on any mismatch; a test with a tampered artifact fails export verification.



### P1-capability

#### P1-1 — AFU/BFU + FBE encryption-state detection as a first-class field gating CE-artifact claims ✅ DONE (commit b2f09b8) — forensics/encryption_state.py + `_ce_gate` on every Tier-2 CE pull; report section; dashboard Encryption view

`feasibility=proven  effort=medium  requires_root=partial`

- **Why:** The single most consequential missing variable. Root is not decryption: on FBE (Android 10+) CE storage (/data/data) is ciphertext until first unlock. The tool never determines AFU/BFU or ro.crypto.type, so it can `su cp` a CE sandbox on a BFU device, get ciphertext/empty, and report 'not found' instead of the correct 'present but cryptographically inaccessible (BFU).' This gating logic IS the honesty model for rooted acquisition.

- **Files:** engine/triage/adb.py, engine/triage/acquire/real.py, engine/triage/custody.py, engine/triage/report.py

- **Acceptance:** pre_state records android_sdk, ro.crypto.type (file/block/none), ro.crypto.state, and a derived AFU/BFU determination (readability of a known CE canary path); every CE-class artifact is tagged with that state, and on a BFU device CE sandboxes are reported 'present, encrypted, inaccessible (BFU)' rather than absent.


#### P1-2 — Non-root live Wi-Fi capture via dumpsys (wifi/netstats/connectivity) with a labeled coarse timeline ✅ DONE (commit b2f09b8) — parsers/wifi_live.py; every netstats row labelled approximate/hour-bucketed; dashboard WifiLive view

`feasibility=proven  effort=medium  requires_root=no`

- **Why:** Wi-Fi is root-only today and returns empty on non-rooted devices. dumpsys wifi/netstats/connectivity give current SSID/BSSID, the saved-SSID list, scan-result BSSIDs, and hour-bucketed per-network connected-time WITHOUT root — the entire realistic non-root Wi-Fi surface — and must be captured live before shutdown (volatile). The engine captures none of it.

- **Files:** engine/triage/pipeline.py, engine/triage/parsers/wifi.py, app/src/views/WiFi.tsx

- **Acceptance:** On a non-rooted authorized device the run captures current + saved + scanned SSIDs/BSSIDs and a coarse netstats connection timeline, each labeled volatile/approximate (hour-bucketed); WifiNetwork.timestamp is populated where available; connection times explicitly flagged not-authoritative.


#### P1-3 — Root-tier Bluetooth bond store (bt_config.conf) with correct timestamp labeling + OUI resolution ✅ DONE — parsers/bt_config.py + parsers/oui.py; bond timestamps labelled 'pairing-record write, NOT connection/co-location'; link keys recorded as present, never displayed

`feasibility=proven  effort=medium  requires_root=yes`

- **Why:** The crown-jewel BT artifact /data/misc/bluedroid/bt_config.conf is never read — only MAC-redacted dumpsys is used. bt_config holds persistent bonds, per-device pairing time, DevClass/DevType/AddrType, and link keys. Research's #1 BT overstatement is calling a bond timestamp a 'connection time'; bake the caveat in from the start.

- **Files:** engine/triage/pipeline.py, engine/triage/parsers/bluetooth.py

- **Acceptance:** On a rooted device bt_config.conf (+ .bak) is pulled and parsed into per-device bond records (Name, Address, DevClass, DevType, AddrType, Timestamp) with timestamps explicitly labeled 'bond/first-pair, NOT connection/co-location'; OUI vendor resolved only for AddrType=0 classic devices; link keys stored-but-never-displayed; unknown Gabeldorsche keys handled gracefully.


#### P1-4 — Wire Bluetooth + cell-tower into the unified timeline and add dashboard views ✅ DONE (commit dbab739) — BT + celltower reach the unified timeline, the report, and dedicated dashboard views; summaries wired

`feasibility=proven  effort=small  requires_root=no`

- **Why:** build_bluetooth_timeline/build_celltower_timeline and get_*_summary are defined but never called in pipeline.py; the BT and celltower datasets are API-reachable but never rendered. High-value pattern-of-life and location artifacts exist only in isolation, uncorrelated.

- **Files:** engine/triage/pipeline.py, app/src/views, app/src/components/Sidebar.tsx, app/src/App.tsx

- **Acceptance:** BT and celltower events appear in the unified timeline; each has a dashboard view and sidebar entry; get_*_summary render in the report.


#### P1-5 — Emit rowid-gap / live-vs-recovered 'deletion detected' as a confidence-tagged evidence class ✅ DONE — DeletionEvidence emitted with Confidence.DELETION_DETECTED across 5 named mechanisms, each carrying false-positive causes

`feasibility=proven  effort=small  requires_root=partial`

- **Why:** DELETION_DETECTED confidence is defined (config.py:30) and documented but never emitted; detect_rowid_gaps returns plain dicts with no confidence field. The highest-value HONEST output — 'N messages were deleted here even though the text is unrecoverable' — is computed but not surfaced as a first-class finding with its own (high, structural) evidentiary weight distinct from content recovery.

- **Files:** engine/triage/recovery/sqlite_recovery.py, engine/triage/pipeline.py, engine/triage/report.py

- **Acceptance:** rowid gaps and live-vs-recovered set differences surface as DELETION_DETECTED rows with the mechanism named and false-positive caveats disclosed (rolled-back transactions, WITHOUT ROWID tables, per-thread vs global counters); rendered distinctly from recovered content.


#### P1-6 — Follow SQLite overflow-page chains in record recovery ✅ DONE — SQLite overflow-page chains followed with cycle/range guards; an incomplete chain downgrades the row to CARVED_PARTIAL and marks it truncated

`feasibility=plausible  effort=medium  requires_root=no`

- **Why:** _parse_record truncates payload to what fits on the page (take = min(payload_len, avail)) and never walks the 4-byte overflow pointer. Long messages, media captions, and large TEXT/BLOB columns that spilled to overflow pages are truncated or dropped for every carve source (freelist, WAL, sqbrite) — under-recovering exactly the long-message content that matters most.

- **Files:** engine/triage/recovery/sqlite_recovery.py

- **Acceptance:** A record with a column spilled to an overflow page is reconstructed fully when the overflow pages are intact, and explicitly marked truncated when an overflow page was reused/unavailable; test with a >1-page TEXT value in both live and carved contexts.


#### P1-7 — Wire the dead-but-tested parsers into the run path (google_maps, google_search, screen_time, signal) ✅ DONE (commit dbab739) — google_maps / google_search / screen_time / signal now invoked by run_acquisition

`feasibility=proven  effort=small  requires_root=partial`

- **Why:** These parsers are fully written, exported in parsers/__init__.py, and covered by tests, but have ZERO call sites in run_acquisition — Google location history, Google search history, screen-unlock/power events, and Signal consent-export are silently absent from every run. Cheap capability recovery already paid for.

- **Files:** engine/triage/pipeline.py

- **Acceptance:** each parser is invoked when its input is present, produces a dataset reachable via the API and report, and is exercised by an end-to-end run over fixtures; the Signal path reports SQLCipher DBs as 'encrypted-present' rather than silently failing.



### P2-compliance

#### P2-1 — Replace IEA s.65B certificate with BSA s.63 (2023) Schedule form, dual-signature ✅ DONE (commit dbab739) — BSA 2023 s.63 Schedule Part A/B, dual signature; _section_65b deleted; forensics/section65b.py now raises

`feasibility=proven  effort=medium  requires_root=no`

- **Why:** report._section_65b outputs a 'Section 65B, Indian Evidence Act, 1872' certificate; that statute was REPEALED and replaced by BSA 2023 s.63 on 2024-07-01. s.63 requires the Schedule form, signatures by BOTH a custodian/person-in-charge AND an expert, and a HASH value (algorithm named) + device identifiers (IMEI/MAC/serial), 24-hr IST time, and place. For an Indian tool this is a direct admissibility miss.

- **Files:** engine/triage/report.py

- **Acceptance:** the report emits a BSA s.63 Schedule Part A/Part B certificate pre-filled with computed SHA-256 (MD5/SHA-1 optional), auto-captured IMEI/model/serial, IST 24-hr timestamp and place, and two signature blocks (custodian/IO + expert); labeled an illustrative template pending counsel review.


#### P2-2 — Tamper-evident (hash-chained) audit log ✅ DONE (commit dbab739) — forensics/audit_chain.py wired into Case.audit(); verdict in the report and VERIFICATION.txt

`feasibility=proven  effort=medium  requires_root=no`

- **Why:** audit.jsonl has no hash-chaining, HMAC, or signature — 'append-only' is only a file-mode convention. Nothing links line N to N-1, so an examiner who edits, reorders, or deletes an audit line leaves no internal evidence; from the case folder alone, audit tampering is undetectable. SWGDE/BSA chain-of-custody expects a defensible trail.

- **Files:** engine/triage/custody.py, engine/triage/models.py, engine/triage/export.py

- **Acceptance:** each AuditEvent stores prev_hash (hash of the previous event); a verifier detects any edit/reorder/deletion; the sealed export records the signed/printed chain head out-of-band; a test that mutates one line fails verification.


#### P2-3 — Post-acquisition device-state snapshot + verified Tier-1 teardown ✅ DONE (commit dbab739) — triage/device_state.py: pre/post snapshot, ledger-driven verified teardown, 'unverified' as a first-class verdict

`feasibility=proven  effort=medium  requires_root=no`

- **Why:** only set_pre_state exists; Tier-1 installs an APK, grants runtime perms, sets a GET_USAGE_STATS appop, and launches activities, but there is no post_state to show the device was returned to its found state, and reversal relies solely on best-effort uninstall (logs error but proceeds). If uninstall fails, READ_CONTACTS/READ_SMS/READ_CALL_LOG grants and the appop persist silently with no compensating action.

- **Files:** engine/triage/pipeline.py, engine/triage/acquire/real.py, engine/triage/custody.py

- **Acceptance:** run_acquisition records a post_state (package present? perms still granted? appops set?), explicitly pm-revokes granted permissions and resets the appop, verifies uninstall, and the report shows a pre/post diff of every device-altering action.


#### P2-4 — SWGDE 18-Q-001 validation report + CFTT MDT-CA coverage mapping against a ground-truthed reference image ✅ DONE (commit b2f09b8) — triage/validation/: offline known-answer harness with a negative control + honest CFTT coverage matrix; run per-case, plus GET /api/validation

`feasibility=proven  effort=large  requires_root=no`

- **Why:** No characterized error rate (Daubert's weak spot), no validation regime, no coverage matrix. SWGDE 18-Q-001-2.1 requires a known-answer test (purpose/scope, tester, date, dataset+expected, results/anomalies, identified limitations) before use and after each major version; validate against a Josh Hickman Android image with a data-population key. Map coverage to CFTT MDT-CA-01..11 with met/unmet labels.

- **Files:** tests/validation, engine/triage/report.py

- **Acceptance:** a machine-readable validation report is emitted per release with the 18-Q-001 fields and per-artifact pass/fail counts against a public reference image; the report includes a CFTT MDT-CA coverage table with unmet assertions honestly labeled.


#### P2-5 — Correct capability-overclaim labels and remove result-fabricating dead code ✅ DONE (commit dbab739) — fabricated-success stubs deleted, role-swap labels corrected, transport-reuse counter renamed to what it measures

`feasibility=proven  effort=small  requires_root=no`

- **Why:** config comments label Tier-1 SMS/call-log flows 'role-swap' though the code only does pm grant (no RoleManager change); _async_pull_files/_async_process_file return fabricated {'status':'pulled'} and _run_optimized_acquisition returns {}; the persistent-transport transport_reuses counter measures a no-op (run() always spawns a fresh subprocess). These overclaims, if ever surfaced, poison the honesty model.

- **Files:** engine/triage/config.py, engine/triage/pipeline.py, engine/triage/adb.py

- **Acceptance:** comments describe the actual mechanism (adb-install-allowlisted restricted-permission grant, no role swap); no code path returns fabricated success; dead async stubs and the no-op transport-reuse counter are removed or made real.



### P3-breadth

#### P3-1 — App-presence/execution evidence: gass.db + raw usagestats protobuf + packages.xml ✅ DONE (commit b2f09b8) — parsers/app_presence.py; 'present but since uninstalled' emitted as DELETION_DETECTED

`feasibility=proven  effort=medium  requires_root=yes`

- **Why:** Strongest 'app was present / executed' chain: gass.db carries the APK SHA-256 and is NOT removed on uninstall; raw /data/system/usagestats protobuf survives uninstall ~1 year; packages.xml holds install times, installer source, and signatures. Today app inventory comes only from the Tier-1 Collector APK (a live list), not these persistent stores.

- **Files:** engine/triage/parsers, engine/triage/pipeline.py, engine/triage/config.py

- **Acceptance:** gass.db, usagestats protobuf (reusing ALEAPP schemas), and packages.xml are parsed on rooted/FFS; 'has the user ever run app X' is answered with persistence caveats and AFU/BFU tagging.


#### P3-2 — Anti-forensics structural detection: work-profile / dual-app clones / Secure Folder, vault apps, factory-reset trace ✅ DONE (commit b2f09b8) — parsers/antiforensics.py; observations only, every finding carries innocent explanations

`feasibility=proven  effort=large  requires_root=yes`

- **Why:** Enumerate /data/user/* and /data/system/users/*.xml — any non-zero user (10/95/150/999) is a work-profile clone, Dual Messenger, or Samsung Secure Folder hiding a second app instance; flag vault/secure-delete/Tor/Shelter packages from packages.xml+gass.db+usagestats even after uninstall; /data/misc/bootstat mtime = factory-reset time. Reliable even when the hidden container itself is unextractable. eRakshak has only inventory-based anti_forensic scoring today.

- **Files:** engine/triage/parsers, engine/triage/pipeline.py, engine/triage/analysis/risk.py

- **Acceptance:** non-zero Android users are enumerated and flagged as potential cloned/hidden containers (Secure Folder reported 'present, locked'); vault/anti-forensic packages surfaced including uninstalled ones; factory-reset timestamp reported from bootstat mtime; magic-byte carving offered to defeat extension-renamed vault media.


#### P3-3 — FCM queued-message mining + 'encrypted-present, content-not-recoverable' reporting for SQLCipher apps ✅ DONE (commit b2f09b8) — parsers/encrypted_apps.py + parsers/fcm.py; encrypted-present reported as a finding, never as absence

`feasibility=proven  effort=medium  requires_root=yes`

- **Why:** FCM LevelDB queued push payloads are a documented second-chance content source for otherwise-encrypted messengers; and the honest posture for SQLCipher+hardware-Keystore apps (Signal/Threema/Session/Wickr) is to detect and report the encrypted DB's existence/metadata rather than attempt impossible content. Signal's parser is currently dead and falls through to a generic parser that cannot read SQLCipher.

- **Files:** engine/triage/parsers, engine/triage/pipeline.py, engine/triage/report.py

- **Acceptance:** FCM queued payloads are parsed where present; Signal/Threema/Session DBs are reported 'present, encrypted (SQLCipher/Keystore), content not recoverable' with path/size/timestamps — never as empty/absent and never attempted-and-fabricated.


#### P3-4 — recent_tasks + task snapshots (AFU-gated) ✅ DONE (commit b2f09b8) — parsers/recent_tasks.py + parsers/abx.py; BFU produces an explicit skip with a reason, never an empty list

`feasibility=proven  effort=large  requires_root=yes`

- **Why:** App-switcher task snapshots can contain chat content, payment screens, and unsent messages; recent_tasks gives per-app last-activity with timestamps. High value, AFU + root only, and volatile (cleared on force-close/swipe) — so must be gated on the AFU determination from P1-1 and labeled volatile.

- **Files:** engine/triage/parsers, engine/triage/pipeline.py

- **Acceptance:** on an AFU rooted device, /data/system_ce/0/recent_tasks (ABX) is parsed and /data/system_ce/0/snapshots JPGs are cataloged with task-ID correlation and an explicit volatility caveat; skipped with a clear reason when BFU.



---
## Do NOT build (verified dead-ends)

- **A slack-space / file-slack / unallocated-space / raw-block carver for /data** — On Android 10+ FBE makes /data unallocated AES-XTS ciphertext, and Android 11+ dm-default-key encrypts even directory structure/filenames/sizes; F2FS allocates fresh pages while real-time discard + vold GC_URGENT destroy invalid blocks within hours; the managed-NAND FTL means no host command reaches a physical page; ext4 unlink zeroes the extent tree; and the per-file key is HKDF-derived from an inode nonce that dies with the file, so orphaned ciphertext is unrecoverable in principle. It would present ciphertext noise as 'recovered data.' SELinux blocks /dev/block/by-name/userdata anyway, and bootloader unlock triggers a factory reset. eRakshak correctly has NO such carver — keep it that way; report unallocated space as a fixed 'not acquirable' capability limitation.

- **Freelist/freeblock carving of FRAMEWORK SQLite DBs (mmssms.db, contacts2.db, calllog.db, downloads) as a deleted-content source** — AOSP compiles platform SQLite with -DSQLITE_SECURE_DELETE (freed cells zeroed) and -DSQLITE_DEFAULT_AUTOVACUUM=1 (freelist pages truncated out of the file), so deleted-SMS/contacts/calllog freelist yield is near-zero by construction. If a framework DB is carved at all it must report 'zero recoverable — secure_delete active' per-database, so the null result is informative — never 'nothing found' implying it might be elsewhere. (App-BUNDLED SQLCipher/Chromium DBs are the opposite and DO carve, which is why eRakshak's per-DB approach is right; detect bundled-vs-framework via the page-1 auto_vacuum/header flags, don't assume.)

- **Bootloader-unlock-to-root then dd of userdata on a locked evidence device** — AOSP mandates a factory data reset on the unlock transition — it destroys the very evidence sought and clears RAM (killing any key-recovery path too). It is never an acquisition path; only devices already rooted/bootloader-unlocked at seizure qualify. This should be a hard, un-bypassable guardrail in the tool, not a feature or even a warning.

- **Offline LSKF brute-force, CE decryption from a disk image, or FBE master-key-from-RAM recovery** — Rate-limiting is enforced inside the TEE/Gatekeeper or Secure Element/Weaver; Quarkslab only succeeded after patching Gatekeeper, and SE devices (Titan M/Knox Vault) don't yield the Weaver secret even with a fully compromised TEE. The 2021 RAM-key technique is structurally defeated by hardware-wrapped keys (raw key never in software, bound to the current boot) on modern flagships. Out of scope for a software triage tool and legally fraught; do not surface it as a capability.

- **Deleted-message / SQLite-freelist recovery on a NON-rooted device** — Content providers return LIVE rows only; the raw evidentiary .db plus its -wal/freelist never leave /data/data without root, a full-file-system exploit, or the app's own export. Claiming deleted-message recovery from a non-rooted device is the single most dangerous overstatement a triage tool can make. eRakshak's Tier-1 is live-rows-only — keep it explicitly labeled that way and never imply freelist/WAL access non-root.

- **A 'physical extraction' tier (chip-off / JTAG / EDL / MTK-BROM) presented as content recovery** — On Android 10+ these yield FBE + dm-default-key ciphertext with keys sealed in KeyMint behind Verified Boot. A successful physical dump is an undecryptable blob unless the device was AFU or the keys were obtained separately. Do not present physical extraction as a superior tier; on FBE an AFU decrypted FFS of mounted /data beats a raw dd, and a raw image should be labeled 'ciphertext only — not decryptable without live keys.'

- **Precise Wi-Fi 'joined SSID X at time T' from WifiConfigStore.xml, or a Bluetooth bond timestamp presented as a 'connection/co-location' time** — WifiConfigStore has NO reliable per-join epoch — only recency flags plus ConnectChoiceTimeStamp, which is a user-preference event, not a join; precise join time lives only in volatile, hour-bucketed netstats captured live. bt_config.conf timestamps are adapter-setup / bond-write, not connection or co-location — a bond proves the two devices paired once, never that they were together at a later moment. Report both as bond/recency and require independent corroboration (app DBs) before any presence claim.

- **Decrypting Signal/Threema/Session/Wickr (SQLCipher + hardware-Keystore) content or exporting StrongBox/auth-bound app keys** — Those keys are non-exportable by hardware design and are boot-bound; a root/FFS pull captures the encrypted DB but cannot decrypt it, and no amount of on-device software changes that. The only honest output is 'encrypted-present, content-not-recoverable' with the DB's path/size/timestamps — never an attempt that risks emitting fabricated or garbage 'messages.'


---
## Codebase audit — 5 dimensions (present vs gaps)


### Deleted-data recovery capability (SQLite carving + acquisition sidecar handling)

**Present / already correct:**

- FREELIST trunk + leaf page carving IS implemented: engine/triage/recovery/sqlite_recovery.py:402-424 (_freelist_pages walks the first-trunk pointer at bytes 32:36, each trunk's next-trunk/nleaf header, and every leaf entry). Freelist pages are carved structurally at sqlite_recovery.py:582-590 with Confidence.RECOVERED_VERIFIED.

- IN-PAGE FREEBLOCK detection IS implemented: sqlite_recovery.py:340-353 (_freeblock_regions walks the freeblock linked list from the 2-byte pointer at hdr_off+1). BUT freeblocks are only TEXT-carved, not structurally re-parsed — sqlite_recovery.py:603-614 routes freeblock + unallocated regions through _carve_text_runs, never through the structured _carve_region.

- PAGE UNALLOCATED / slack region IS handled: sqlite_recovery.py:356-367 (_unallocated_region returns the gap between the cell-pointer array end and the cell-content-area start). Carved via text runs at sqlite_recovery.py:604-614 (kind='unallocated').

- WAL FRAME recovery IS implemented: sqlite_recovery.py:622-654 (_recover_from_wal validates magic 0x377F0682/0683, reads page_size from bytes 8:12, iterates 24+page_size frames from offset 32, structurally carves each table-leaf page image as RECOVERED_VERIFIED).

- RECORD SERIAL-TYPE DECODING IS implemented: _read_varint sqlite_recovery.py:45-62, _serial_size sqlite_recovery.py:66-79, _decode_value (NULL/int/float/0/1/BLOB/TEXT) sqlite_recovery.py:82-110, full record parse _parse_record sqlite_recovery.py:147-174.

- SCHEMA HINTS ARE implemented: recover_deleted_rows accepts schema_hint (sqlite_recovery.py:529, 566-576); _schema_tables introspects col counts via sqlite3 (sqlite_recovery.py:428-446); expected_cols validates carve column counts; WhatsApp column map map_columns_to_whatsapp sqlite_recovery.py:496-523. Pipeline builds a WA hint at pipeline.py:725-728.

- TEXT CARVING IS implemented: _carve_text_runs sqlite_recovery.py:246-311, with column-name prefix anchoring (data=/body=/msg=/text=/content=/message=) sqlite_recovery.py:228-243, min length 4, printable ratio >=0.8 (line 280).

- ROWID-GAP DETECTION IS implemented: detect_rowid_gaps sqlite_recovery.py:657-671 (ORDER BY rowid, reports after/before/missing count).

- SECONDARY raw-byte record-signature scan IS implemented: engine/triage/recovery/sqbrite.py:171-223 (sqbrite_scan slides over raw .db bytes from offset 100, byte pre-filter 2..200, _try_parse_record_at sqbrite.py:140-168, _has_useful_text sqbrite.py:121-131). All results are CARVED_PARTIAL (sqbrite.py:42).

- WAL SIDECAR IS pulled during acquisition AND consumed. Priority: priority.py:94-95 scores -wal/-shm/-journal names 100. Tier-0: _colocate_sqlite_sidecars pipeline.py:1784-1799 re-pulls sidecars to the exact name <stored>-wal after content-hash rename (called from _pull_and_process_file pipeline.py:1778). Tier-2 root pulls also fetch sidecars (_SQLITE_SIDECAR_SUFFIXES pipeline.py:2222; root-copy helper ~pipeline.py:2227-2264, Telegram/Instagram/Snapchat pulls pipeline.py:2301-2568). The carver reads <db>-wal at sqlite_recovery.py:624, so WAL is end-to-end wired.

- BINARY-GARBAGE FILTERING exists at multiple layers: _has_content sqlite_recovery.py:177-187 (needs a >=2-char string, a number not in {0,1}, or >=2-byte blob); _try_carve_cell rejects >60% NULL rows and rowid<0 or >2^48 (sqlite_recovery.py:210-218); text _flush requires printable>=80% (line 280); sqbrite _has_useful_text needs printable>=75% (sqbrite.py:129).

- CONFIDENCE assignment: LIVE (read_live_rows sqlite_recovery.py:466); RECOVERED_VERIFIED for clean freelist/WAL carves, auto-downgraded to CARVED_PARTIAL when payload truncated or col-count mismatches (_carve_region sqlite_recovery.py:384; clean flag set at 215-216); CARVED_PARTIAL for all text carves (line 287) and all sqbrite rows; DELETION_DETECTED enum defined (config.py:30) for rowid gaps.


**Gaps:**

- _[major, medium]_ ROLLBACK JOURNAL (-journal) is NEVER parsed for recovery. The -journal sidecar is pulled during acquisition (priority.py:94, _SQLITE_SIDECAR_SUFFIXES pipeline.py:2222, _colocate_sqlite_sidecars pipeline.py:1791) but the recovery engine only ever reads '-wal' (sqlite_recovery.py:624). For rollback-mode (non-WAL) databases the journal holds the pre-deletion page images — that entire deleted-content surface is collected to disk and then silently ignored.  (`engine/triage/recovery/sqlite_recovery.py`)

- _[major, large]_ NO filesystem-level or block-level carving of any kind. All carving is SQLite-internal (freelist pages, in-page freeblocks, page unallocated gap, WAL frames). There is no unallocated-disk / file-slack / raw-partition / ext4/f2fs carving anywhere in engine/triage/ (every 'unallocated' reference is the intra-page SQLite gap only). Deleted DB files that were unlinked, or records living outside any pulled .db, are unrecoverable.  (`engine/triage/recovery/`)

- _[major, medium]_ OVERFLOW-PAGE chains are not followed. _parse_record truncates the payload to what fits on the page (take = min(payload_len, avail), sqlite_recovery.py:205-207, 167-171) and _try_carve_cell never walks the 4-byte overflow pointer. Long messages / media captions / large TEXT/BLOB columns that spilled to overflow pages are truncated or dropped for every carve source (freelist, WAL, sqbrite).  (`engine/triage/recovery/sqlite_recovery.py`)

- _[major, medium]_ In-page freeblocks and the page unallocated region get TEXT-ONLY carving, never structured record recovery. sqlite_recovery.py:603-614 sends both region kinds through _carve_text_runs; the structured _carve_region is applied only to freelist pages (line 588) and WAL frames (line 649). Deleted cells whose header survived in a live page's freeblock/unallocated area lose all column structure, rowid, and typed values — they surface as a single anchored text string.  (`engine/triage/recovery/sqlite_recovery.py`)

- _[minor, medium]_ Only table-LEAF pages (0x0D) are carved. _btree_header_offset + the _LEAF_TABLE guard (sqlite_recovery.py:601, 647) skip interior-table pages (0x05), index-leaf/interior pages (0x0A/0x02). Records reachable only through index pages, and any WITHOUT ROWID table content, are not recovered.  (`engine/triage/recovery/sqlite_recovery.py`)

- _[minor, small]_ -shm is pulled (priority.py:94, pipeline.py:2222) but never read by the recovery engine. This is largely benign (the -shm holds no durable record content, only a WAL index) but is worth noting: the sidecar is collected and then unused.  (`engine/triage/recovery/sqlite_recovery.py`)

- _[minor, small]_ sqbrite raw-byte scan is run ONLY over the primary .db file (sqbrite.py:193, off starts at 100). It does not scan the -wal or -journal sidecars, so its 'cross-check coverage' claim (module docstring sqbrite.py:15-19) does not extend to WAL/journal-resident deleted records.  (`engine/triage/recovery/sqbrite.py`)

- _[major, small]_ sqbrite de-duplication against the primary engine is DEFEATED in the pipeline: pipeline.py:755 builds `primary` from recovered_rows but pipeline.py:756 calls sqbrite_cross_check(stored, primary_rows=[]) with an EMPTY list, so the fingerprint set is empty (sqbrite.py:243-249) and no primary-vs-sqbrite dedup occurs — every sqbrite hit is appended, inflating/duplicating recovered rows despite the module's stated dedup posture.  (`engine/triage/pipeline.py`)

- _[minor, medium]_ RECOVERED_VERIFIED overstates certainty for freelist/unchecked WAL carves. A freelist page may be partially reused and a WAL frame may be stale/superseded, yet any structurally-clean, column-count-matching carve is labelled 'recovered' (sqlite_recovery.py:384-390, 649-652). There is no salt/checksum/commit-frame validation on WAL frames (_recover_from_wal reads every frame blindly, sqlite_recovery.py:642-653) and no overwrite check on freelist content.  (`engine/triage/recovery/sqlite_recovery.py`)

- _[minor, small]_ DELETION_DETECTED confidence is defined (config.py:30) and documented (sqlite_recovery.py:23) but the two recovery modules never EMIT a CarvedRow with it — detect_rowid_gaps returns plain dicts without a confidence field (sqlite_recovery.py:657-671), so rowid-gap evidence is not surfaced as a confidence-tagged recovery record by this engine.  (`engine/triage/recovery/sqlite_recovery.py`)

- _[minor, medium]_ Binary/BLOB 'garbage' is only weakly filtered: _has_content accepts any blob >=2 bytes (sqlite_recovery.py:186) and _decode_value returns raw bytes for BLOB serials (sqlite_recovery.py:103-104), so thumbnails/protobuf/binary columns pass the noise gate and surface as recovered rows. sqbrite's byte-2..200 pre-filter (sqbrite.py:208) plus step=1 exhaustive scan is high-false-positive by construction; every match is emitted as CARVED_PARTIAL with no structural corroboration.  (`engine/triage/recovery/sqlite_recovery.py`)


### Wi-Fi and Bluetooth (and cell tower) artifact coverage in the eRakshak forensic engine

**Present / already correct:**

- WI-FI — SAVED CREDENTIALS ONLY. engine/triage/parsers/wifi.py parses two saved-config formats: wpa_supplicant.conf (Android <=8) via parse_wpa_supplicant_conf (wifi.py:51-98) extracting ssid, psk/wep_key0 password, key_mgmt->security label; and WifiConfigStore.xml (Android >=9) via parse_wifi_config_store_xml (wifi.py:105-193) extracting SSID, PreSharedKey, AllowedKeyMgmt. Dispatcher parse_wifi_config (wifi.py:200-218) picks by extension. Output model WifiNetwork (models.py:245-261) fields: ssid, password, security, timestamp(Optional, unused), confidence, source_file.

- WI-FI acquisition is Tier-2 ROOT ONLY. pipeline.py _run_tier2_wifi (pipeline.py:2599-2750) gated on cfg.tier2_wifi (pipeline.py:685). It runs an explicit root check `su -c 'id'` (pipeline.py:2629); if root_check fails it logs 'root not available; Wi-Fi credential recovery skipped' and returns [] (pipeline.py:2638-2645). Reads REMOTE_XML=/data/misc/wifi/WifiConfigStore.xml and REMOTE_CONF=/data/misc/wifi/wpa_supplicant.conf (pipeline.py:2623-2624), probes with `su -c test -f` (pipeline.py:2656), copies via `su -c cp` to /sdcard staging (pipeline.py:2679), pulls, then parse_wifi_config (pipeline.py:2731).

- BLUETOOTH — NON-ROOT ONLY, dumpsys. pipeline.py:566-576 runs source.shell_readonly('dumpsys bluetooth_manager') and calls parse_bluetooth_history, writing derived dataset 'bluetooth' (pipeline.py:572). Parser engine/triage/parsers/bluetooth.py parse_bluetooth_history (bluetooth.py:183-263) extracts per-device: mac, name/alias, bond_state, connected flag, last_seen, device_class, is_paired.

- BLUETOOTH pairing/last-seen TIMESTAMP extraction exists but is best-effort from dumpsys text. _RE_LAST_SEEN / _RE_LAST_SEEN_ALT (bluetooth.py:61-62) match lastSeen/last_connected/last_active; parse_bluetooth_timestamp (bluetooth.py:70-113) normalizes epoch-ms/s, ISO, and Android-log formats to ISO-8601 UTC. Device class decoded via _DEVICE_CLASS_MAP (bluetooth.py:41-52).

- CELL TOWER — NON-ROOT dumpsys. pipeline.py:581-591 runs source.shell_readonly('dumpsys telephony.registry'), parse_celltower_history, writes derived 'celltower'. Parser celltower.py:166-260 extracts cell_id, lac, mcc, mnc, signal_asu/label, operator, timestamp, network_type.

- API REACHABILITY — all three reach the API. server.py case_dataset list_sets includes 'wifi' (server.py:1103), and 'notifications','bluetooth','celltower' were added (server.py:1109-1113 with comment noting they were previously 'unreachable over the API until now'). Summary datasets include 'wifi' (server.py:938).

- DASHBOARD — Wi-Fi has a full view. app/src/views/WiFi.tsx (WifiView, fetches dataset 'wifi', WiFi.tsx:91), routed in App.tsx:33,77, sidebar entry 'Wi-Fi Passwords' (Sidebar.tsx:49). WifiNetwork TS type at app/src/lib/types.ts:666. Acquisition.tsx exposes the tier2_wifi root toggle (Acquisition.tsx:39,554-563).


**Gaps:**

- _[major, medium]_ NO Wi-Fi CONNECTION HISTORY. The engine never records when the device joined which network — no last-connect / lastConnected / association timestamps are parsed. WifiConfigStore.xml fields like LastConnected/Status are ignored (parser only reads SSID, PreSharedKey, AllowedKeyMgmt at wifi.py:150-156). WifiNetwork.timestamp (models.py:259) is declared but never populated by either parser.  (`engine/triage/parsers/wifi.py`)

- _[major, large]_ NO Wi-Fi SCAN HISTORY. No collection of scanned/seen (non-saved) networks; no dumpsys wifi / WifiScanner / scan-result parsing anywhere in the engine (grep for scan history returned nothing).  (`engine/triage/parsers/wifi.py`)

- _[major, large]_ Wi-Fi is ROOT-ONLY with NO non-root fallback. _run_tier2_wifi returns [] immediately when `su -c id` fails (pipeline.py:2638-2645). There is no non-root path (e.g. dumpsys wifi for SSID/BSSID connection info), so on non-rooted devices zero Wi-Fi artifacts are produced.  (`engine/triage/pipeline.py`)

- _[major, large]_ Bluetooth bond store /data/misc/bluedroid/bt_config.conf is NEVER read. No root-tier Bluetooth acquisition exists; grep for bluedroid/bt_config across engine/ returns nothing. Only non-root `dumpsys bluetooth_manager` (pipeline.py:567) is used, which omits persistent bond records, per-device pairing timestamps, and link keys that bt_config.conf holds.  (`engine/triage/pipeline.py`)

- _[minor, medium]_ Bluetooth pairing timestamps are unreliable in practice: dumpsys bluetooth_manager rarely emits lastSeen/last_connected fields, so last_seen is usually empty. There is no authoritative pairing-time source (that lives in bt_config.conf, which is not read).  (`engine/triage/parsers/bluetooth.py`)

- _[minor, medium]_ NO MAC OUI vendor resolution. Despite the docstring claim that 'MAC manufacturer prefix (OUI) can identify the device vendor / type' (bluetooth.py:13), there is no OUI lookup table or resolver anywhere in the engine (grep for oui/OUI across engine/ finds only that comment). MAC vendor is never populated for Bluetooth devices.  (`engine/triage/parsers/bluetooth.py`)

- _[major, small]_ Bluetooth and cell-tower events do NOT reach the main timeline. build_bluetooth_timeline (bluetooth.py:270) and build_celltower_timeline (celltower.py:267) are defined but never imported or called in pipeline.py (only get_*_history and parse_* are imported at pipeline.py:79-82; build_* absent). Likewise get_bluetooth_summary / get_celltower_summary (bluetooth.py:308, celltower.py:309) are unused. So these artifacts appear only as standalone datasets, not correlated in the unified timeline.  (`engine/triage/pipeline.py`)

- _[major, medium]_ Bluetooth/cell-tower have NO dashboard view. app/src/views has no Bluetooth or CellTower/Cellular component (only WiFi.tsx among these); Sidebar.tsx has no bluetooth/celltower entry. The 'bluetooth' and 'celltower' datasets are API-reachable but never rendered in the UI.  (`app/src/views`)

- _[minor, small]_ Pipeline bypasses the parser's own fallback logic. It calls source.shell_readonly('dumpsys bluetooth_manager') / 'dumpsys telephony.registry' directly (pipeline.py:567,582) instead of get_bluetooth_history/get_celltower_history (bluetooth.py:120, celltower.py:113), so the older-Android fallbacks (`dumpsys bluetooth`, `dumpsys phone`) are never exercised despite those functions being imported.  (`engine/triage/pipeline.py`)


### Root vs non-root acquisition handling (root detection, tier capabilities, AFU/BFU & lock state, device-altering-action logging/reversibility, SQLite WAL, non-root app-private access, capability overclaims)

**Present / already correct:**

- ROOT DETECTION: engine/triage/adb.py:273-277 `is_root_available()` is a read-only, non-escalating probe — runs `su -c id 2>/dev/null || id` and returns True only if 'uid=0' appears in stdout. Exposed via engine/triage/acquire/real.py:83-84 `root_available()`, recorded on the device block (real.py:34 `rooted=self.adb.is_root_available()`), and in the pre-state snapshot (real.py:47 `root_available`).

- TIER MODEL: engine/triage/config.py:13-19 defines Tier enum — TIER0 'zero device-state change: adb pull of shared storage, dumpsys'; TIER1 'shell-level but state-changing: helper APK + pm grant'; TIER2 'root required: raw app-private DBs'. Every artifact/log line carries a tier value.

- TIER-0 SCOPE (no root, no state change): engine/triage/config.py:38-48 TIER0_PULL_ROOTS are all /sdcard shared-storage paths only (DCIM, Pictures, Download, WhatsApp, Android/media/...). Enumerated read-only via find (pipeline.py:415-424) and pulled (pipeline.py:487-499). Plus read-only dumpsys location/notification/bluetooth/telephony (pipeline.py:535-593) and read-only framebuffer screenshot via `exec-out screencap -p` with nothing written to device (real.py:66-81).

- TIER-1 SCOPE (non-root, device-altering, helper APK): pipeline.py:3159-3571 — installs io.erakshak.collector, `pm grant`s runtime permissions, triggers dump actions, pulls contacts.json/calllog.json/sms.json/media_inventory.json/apps.json/accounts.json/calendar.json/usage.json. This is the ONLY non-root path to app-scoped data (via the app's own ContentProvider/MediaStore queries); parsed at pipeline.py:1596-1686.

- TIER-2 SCOPE (root required): pipeline.py:2270-3029 — Telegram cache4.db, Instagram direct.db, Snapchat arroyo.db/main.db, Wi-Fi config, WhatsApp crypt* backups. All reach /data/data or /data/misc via `su -c cp` to /sdcard then adb pull (`_su_pull_sqlite` pipeline.py:2225-2267).

- GRACEFUL GATING: Every Tier-1 and Tier-2 stage is gated behind an explicit cfg flag (all default False, pipeline.py:142-168) AND `isinstance(source, RealDeviceSource)`; a mock/absent-capability source logs result='skipped' and continues (e.g. pipeline.py:645-651, 662-668). The whole pipeline is non-raising: each stage wraps work in try/except and logs errors, run continues (pipeline.py:8-9 docstring, and per-stage except blocks throughout).

- ROOT-ABSENT DEGRADATION (explicit skip): Wi-Fi (pipeline.py:2629-2645) and WhatsApp-backup (pipeline.py:2795-2811) do an explicit `su -c 'id'` root pre-check and log result='skipped' with a stated reason when root is missing, then return empty — no crash.

- DEVICE-ALTERING ACTIONS LOGGED: `_log_tier1_step` (pipeline.py:3585-3596) logs every install / `pm grant` / `am start` dump / uninstall with `alters_device=True`, the exact command string, and truncated stderr. Tier-2 su-cp/pull steps log with `alters_device=False` and the verbatim command (e.g. pipeline.py:2239-2250, 2680-2707). custody.py:269 aggregates a `device_altering_actions` count into the custody summary.

- HELPER-APK REVERSAL: `_best_effort_uninstall` (pipeline.py:3574-3582) runs `adb uninstall io.erakshak.collector` at the end of every Tier-1 flow and on every early-return error path, removing the installed package (and with it its granted permissions/appops).

- PRE-STATE + SCREEN-LOCK CAPTURE: real.py:42-48 `pre_state()` records screen_locked, battery_level, device_time, root_available; captured and logged at pipeline.py:232-236 via `case.set_pre_state`. Screen-lock read read-only at adb.py:289-295 `is_screen_locked()` (dumpsys window grep of mDreamingLockscreen/mShowingLockscreen, tri-state Optional[bool]).

- SQLITE WAL HANDLING (thorough): `_SQLITE_SIDECAR_SUFFIXES = ('-wal','-shm','-journal')` (pipeline.py:2222) with a detailed rationale comment (pipeline.py:2216-2221) that a bare .db loses un-checkpointed/superseded rows. Tier-0: `_colocate_sqlite_sidecars` (pipeline.py:1784-1799) re-pulls sidecars to the exact `<db>-wal` name after content-hash rename, using `pull_to_path` (base.py:54-71). Tier-2: `_su_pull_sqlite` (pipeline.py:2225-2267) byte-copies the DB with `su -c cp` (explicitly NOT checkpointing) and copies each sidecar alongside.

- HONEST ACQUISITION FRAMING: config.py:83-89 ACQUISITION_DISCLAIMER explicitly states no write-blocking exists for mobile devices and every interaction is logged — the tool does not claim 'read-only' acquisition.


**Gaps:**

- _[major, medium]_ No AFU vs BFU (After/Before First Unlock) or userdata-encryption (FBE/FDE) state is ever determined. A repo-wide grep for afu/bfu/first-unlock/fbe/file-based-encryption returns nothing. pre_state (real.py:42-48) captures only screen_locked, which is NOT the same as the crypto/unlock state that decides what app-private data is even decryptable. The tool therefore cannot record whether the device was in the AFU state that makes Tier-2 root extraction meaningful.  (`engine/triage/acquire/real.py (pre_state) / engine/triage/adb.py (new probe)`)

- _[major, medium]_ No post-acquisition device-state snapshot exists. Only `set_pre_state` is defined/called (custody.py:136, pipeline.py:233); there is no post_state/set_post_state anywhere. Because Tier-1 flows install an APK, grant permissions, set an appop and launch activities, the audit trail cannot demonstrate the device was returned to its found state — pre/post comparison is impossible.  (`engine/triage/pipeline.py (end of run_acquisition) + engine/triage/custody.py`)

- _[minor, small]_ Granted permissions/appops are never explicitly revoked — no `pm revoke` or appops reset exists (grep empty). Reversal relies solely on `_best_effort_uninstall` (pipeline.py:3574-3582), which is un-verified (logs error but proceeds). If uninstall fails, the READ_CONTACTS/READ_SMS/READ_CALL_LOG grants and the GET_USAGE_STATS appop (pipeline.py:3467-3494) persist with no compensating action and no post-state to detect it.  (`engine/triage/pipeline.py (_best_effort_uninstall / _run_tier1_* teardown)`)

- _[major, medium]_ Capability overclaim: config comments label the SMS and call-log Tier-1 flows as 'role-swap' (pipeline.py:142-143), but the code performs only `pm grant android.permission.READ_SMS` / `READ_CALL_LOG` (pipeline.py:3275, 3187) — no RoleManager / default-SMS-app change is done. READ_SMS/READ_CALL_LOG are hard-restricted permissions on Android 10+, where `pm grant` to a non-default app typically fails; the flow degrades gracefully (grant.ok check → uninstall → empty) but the advertised 'role-swap' capability is neither implemented nor reliably functional.  (`engine/triage/pipeline.py (_run_tier1_sms_helper / _run_tier1_calllog_helper) + config comments`)

- _[minor, small]_ Capability overclaim / dead telemetry: the persistent ADB transport (adb.py:87-194) advertises connection reuse and `connection_stats` counts `transport_reuses` (adb.py:311-319), but `run()` always spawns a fresh subprocess (adb.py:210-242, admitted in its own docstring) and `_connect_transport` is only ever called by `_initialize_optimizations` (pipeline.py:3763-3779), which is never invoked by run_acquisition. The 'warm transport / reduced handshake' capability does nothing; the reuse counter measures a no-op.  (`engine/triage/adb.py (persistent transport) + engine/triage/pipeline.py:3763`)

- _[minor, small]_ Mock/stub code presented as functionality: `_async_pull_files`/`_async_process_file`/`_async_parse_messages`/`_async_sqlite_query` return fabricated {"status":"pulled"}/empty data (pipeline.py:3720-3755) and `_run_async_acquisition`/`_run_optimized_acquisition` return {} (pipeline.py:3738-3785). None are on the live acquisition path, but they are named as if they perform real async/optimized acquisition.  (`engine/triage/pipeline.py:3715-3785 (Task 3 / Task 11 async stubs)`)

- _[minor, small]_ Inconsistent root-absent degradation for Tier-2 chat apps: Telegram/Instagram/Snapchat have no explicit root pre-check (unlike Wi-Fi/WA-backup) and log a failed su-cp as result='error' rather than a graceful result='skipped' with reason (pipeline.py:2306-2315, 2506-2512, 2560-2566). The run does not crash, but a rootless device produces error-level audit noise instead of a clean 'root not available' degradation record.  (`engine/triage/pipeline.py (_run_tier2_telegram / _run_tier2_instagram / _run_tier2_snapchat)`)

- _[minor, small]_ Root-detection heuristic edge case: adb.py:276 `su -c id 2>/dev/null || id` falls through to plain `id`, so on a userdebug / `adb root` build where the adb shell already runs as uid 0, the tool reports rooted=True even when no `su` binary exists; conversely a working `su` requiring interactive/GUI approval may report False. It is also re-invoked on every call (device_info, pre_state, root_available) with no caching.  (`engine/triage/adb.py:273-277 (is_root_available)`)


### Evidence integrity & standards compliance (hashing, chain-of-custody, report/certificate, timestamps, export, confidence model)

**Present / already correct:**

- HASHING ALGORITHMS: SHA-256 (primary) + MD5 (legacy/compat only, never sole identity) computed together in a single streaming 1-MiB-chunk pass — engine/triage/hashing.py:29-40 (file_hashes), generic hash_file at hashing.py:15-21, hash_bytes at 24-26; PRIMARY_HASH='sha256' at engine/triage/config.py:91; SWGDE MD5/SHA1 position cited config.py:80.

- HASHING GRANULARITY: per-artifact (per-file), NOT per-image/whole-device — deliberately avoids a whole-device hash because live-device volatility makes it irreproducible (custody.py:9, report.py:511-516 citing NIST 800-101r1 §3.4).

- HASHING TIMING: hashes computed at the moment of extraction inside Case.ingest_file — file_hashes(dest) at custody.py:183, written into ArtifactRecord.sha256/.md5 (custody.py:189-190) and flushed to manifest.json immediately (custody.py:198-209); ArtifactRecord schema at models.py:59-73.

- HASH VERIFICATION LOGIC EXISTS: forensics/hash_verification.py:35-119 recomputes SHA-256 and compares to manifest (verify_single_file / verify_all_hashes), producing INTACT/TAMPERED/UNKNOWN; auto_verify_on_open (forensics/auto_verify.py:91-120) caches to derived/verification.json and re-verifies when manifest mtime changes; pipeline wires _auto_verify_on_complete (pipeline.py:3936-3944) and _verify_hash (pipeline.py:3894-3917).

- AUDIT LOG APPEND-ONLY + CRASH-SAFE: audit.jsonl written one JSON event per line in append mode with immediate flush under a threading.Lock — custody.py:141-148 (audit) and 150-157 (log convenience); created at case open custody.py:104-110.

- AUDIT LOG FIELDS: timestamp, action, detail, examiner, command (exact shell command), result (ok/error/skipped), alters_device flag, tier, extra — models.py:43-55 (AuditEvent); device-altering actions are counted (custody.py:269) and surfaced in the report and 65B cert.

- REPORT — TOOL VERSION: TOOL_NAME + __version__ stamped in report header (report.py:96-98) and in the 65B certificate (report.py:916-917).

- REPORT — METHOD STATEMENT: ACQUISITION_DISCLAIMER banner ('Minimally-invasive, fully-logged logical acquisition. No write-blocking exists for mobile devices...') rendered at report.py:101, defined config.py:84-89; standards footer lists NIST 800-101r1 + 3 SWGDE docs (config.py:76-81, report.py:551-554).

- REPORT — EXAMINER & LEGAL AUTHORITY: examiner in Case card (report.py:316) and cert (919-921, 935); legal_authority rendered with '— (record before use)' fallback (report.py:318) and 'NOT RECORDED' fallback in cert (report.py:921); scope_note/minimisation shown (report.py:318).

- REPORT — DEVICE STATE: device intake block (manufacturer/model/android/build/serial/imei/carrier/root) report.py:323-338; Pre-acquisition state card (screen_locked/battery/device_time/root) report.py:339-344, populated by acquire/real.py:42-48.

- REPORT — PER-ARTIFACT PROVENANCE: hash manifest table (artifact_id/source_path/tier/size/SHA-256) report.py:511-527; each ArtifactRecord also carries method + extracted_at + tier (models.py:59-73); recovered rows carry confidence tier + byte-level provenance string (report.py:427-439); full audit trail reproduced report.py:530-545.

- SECTION 65B CERTIFICATE PRESENT: _section_65b (report.py:889-937) renders a 'Section 65B (Indian Evidence Act) — Certificate' with a Statement under 65B(4), examiner, legal authority, artifact count/total bytes, device-altering-action count, a Telegram-specific no-decryption clause (report.py:892-907), a triage-preview caveat, and a signature block; explicitly labelled an 'illustrative template — verify wording ... before evidentiary use' (report.py:911-913).

- TIMESTAMPS NORMALISED TO UTC: now_iso() emits UTC ISO-8601 with trailing Z via time.gmtime (models.py:15-17); used for all audit events and ArtifactRecord.extracted_at.

- DEVICE LOCAL TIME CAPTURED: device wall-clock WITH tz offset captured once via `date +%Y-%m-%dT%H:%M:%S%z` (adb.py:286-287) and stored in pre_state['device_time'] (acquire/real.py:46); base contract notes 'device time / skew' (acquire/base.py:39).

- EPOCH CONVERSION HELPERS: Unix seconds (collector.py:37-42 _s_to_iso; telegram.py:779-789 _epoch_to_iso), Unix milliseconds (÷1000: collector.py:27-33, whatsapp_db.py:179-186, whatsapp_e2e.py:353-362, snapchat.py:141-151, calllog.py:23-27, whatsapp_backup.py:427-437), auto second-vs-millisecond detection (value>9,999,999,999 → //1000: notification.py:171-175, celltower.py:79-82, google_search.py:88-91, google_maps.py:71-74), and WebKit/Chrome 1601-epoch microseconds (offset 11644473600s: browser.py:20-30 _webkit_to_iso) — all pin tz=timezone.utc.

- EXPORT FORMATS: (1) sealed evidence ZIP + top-level VERIFICATION.txt restating per-artifact SHA-256 and a single SHA-256 of audit.jsonl — export.py export_case/export_with_hashes/add_verification_file (export.py:66-122); (2) self-contained printable HTML triage report → report.html (report.py:557-559, print CSS report.py:975); (3) detailed hash-integrity HTML → reports/detailed_hash_integrity.html (integrity_report.py, pipeline.py:3919-3932); (4) hash-verification dashboard HTML (hash_verification.py:generate_verification_dashboard); (5) derived datasets as JSON (custody.write_derived custody.py:245-251) incl. full-provenance Telegram JSON (telegram.export_recovered_messages_json).

- CONFIDENCE MODEL DEFINED & APPLIED: Confidence enum LIVE / RECOVERED_VERIFIED('recovered') / CARVED_PARTIAL('carved') / DELETION_DETECTED('deletion') at config.py:23-31; colour map _CONF_COLORS report.py:22-27 applied consistently across recovered rows (431-435), Telegram (716-720), Instagram/Snapchat (778-780), discovered chats (821-823), MediaStore trash (593-594), Wi-Fi (646-649); model rows default to Confidence.LIVE (models.py Message/Contact/CallRecord/LocationPoint).


**Gaps:**

- _[major, medium]_ Audit log is NOT tamper-evident: audit.jsonl has no hash-chaining (no prev_hash/per-line digest), no HMAC, and no signature — 'append-only' is only a convention (file opened in 'a' mode, custody.py:146). Nothing links line N to line N-1, so an examiner who edits, reorders, or deletes an audit line leaves no internal evidence. The only integrity artifact is a single SHA-256 of the whole audit.jsonl computed at export time (export.py:54-55, surfaced in VERIFICATION.txt export.py:74); that detects post-export edits ONLY if the hash was retained out-of-band, and cannot detect any edit made before export. From the case folder alone, audit-log tampering is undetectable.  (`engine/triage/custody.py:141-148 (add per-event hash-chain: prev_hash + running digest); engine/triage/models.py:43-55 (add prev_hash field to AuditEvent)`)

- _[blocker, small]_ The hash-verification suite cannot read the real manifest, so integrity verification silently checks ZERO files. custody.py writes manifest.json as a plain JSON LIST of records whose hash key is 'sha256' and path key is 'stored_path' (custody.py:207-209, models.py:63-64). But load_manifest does data.get('artifacts', []) expecting a dict wrapper (hash_verification.py:29) — on a list this raises AttributeError, is swallowed, and returns [] — and verify_all_hashes then reads artifact.get('sha256_hash') (wrong key) at hash_verification.py:66. Result: total_files=0 → integrity_status 'UNKNOWN' for every real case. Same wrong schema in integrity_report.py:73/82-83, hash_comparison.py:37, hash_timeline.py:36. (export.py:82-83 is the only consumer that handles both list-vs-dict and sha256_hash-vs-sha256.) So auto-verify-on-open, the integrity report, and the verification dashboard never actually validate genuine evidence.  (`engine/triage/forensics/hash_verification.py:19-32 (load_manifest) and :62-67 (key names); mirror fix in integrity_report.py:73,82-83, hash_comparison.py:37, hash_timeline.py:36`)

- _[major, small]_ The report's 'Hash Verification Section' never renders. report.py:131 calls _generate_hash_verification_section(...), but that function is defined nowhere in the codebase (grep: NOT DEFINED), so it raises NameError which is swallowed by the bare 'except Exception: pass' at report.py:132-133. The examiner-facing triage report therefore silently omits any live hash-verification result.  (`engine/triage/report.py:129-133`)

- _[major, small]_ No recompute-and-verify at export time. export_case/export_with_hashes (export.py:97-122) only copy the stored manifest hashes into VERIFICATION.txt and hash audit.jsonl; they never recompute the artifact files to confirm the bundled evidence still matches the manifest before sealing. A file corrupted/altered between ingest and export is packaged with its original (now-wrong) hash and no mismatch is flagged.  (`engine/triage/export.py:97-122`)

- _[major, medium]_ Certificate cites the repealed statute, not current law. _section_65b outputs a 'Section 65B, Indian Evidence Act, 1872' certificate (report.py:889-937) — there is NO Section 63 Bharatiya Sakshya Adhiniyam (BSA) 2023 output, which replaced IEA s65B on 2024-07-01. For evidence produced under today's Indian regime the certificate references the wrong Act/section.  (`engine/triage/report.py:889-937`)

- _[major, medium]_ Device timezone is recorded but never applied. pre_state['device_time'] captures the device offset (adb.py:286-287) yet no parser consumes it: every epoch helper hard-codes tz=timezone.utc (e.g. collector.py:32,42; snapchat.py:151; telegram.py:788), so local-epoch values from the device are emitted as if UTC. There is no capture of persist.sys.timezone as a distinct field and no normalisation using the device offset, so displayed times can be wrong by the device's UTC offset with no flag.  (`engine/triage/acquire/real.py:42-48 (capture tz explicitly) and the _ms_to_iso/_epoch_to_iso helpers across engine/triage/parsers/*`)

- _[minor, small]_ EXIF timestamps are neither UTC-normalised nor Z-suffixed, unlike every other timestamp. _parse_exif_datetime emits 'YYYY-MM-DDTHH:MM:SS' with no timezone and no trailing Z (exif.py:108-125, note at 110-113), and falls back to the raw string if no format matches. These naive values sit alongside UTC-Z timestamps in the same report/timeline with no marker distinguishing them, breaking cross-source time correlation.  (`engine/triage/parsers/exif.py:108-125`)

- _[minor, small]_ No Apple/Cocoa (2001-epoch) or NTFS FILETIME (100-ns) epoch support beyond the browser-specific WebKit offset; artifacts using Mac absolute time or FILETIME are not decoded. Also the confidence default is inconsistent — recovered/message renderers fall back to 'carved' (e.g. report.py:432,715,778) while the Wi-Fi renderer falls back to 'live' (report.py:646), so an unlabelled Wi-Fi row is presented with maximum weight rather than minimum.  (`engine/triage/parsers/*.py (epoch helpers); engine/triage/report.py:646 (confidence fallback)`)


### Artifact coverage breadth of the eRakshak forensic engine (parsers/ + forensics/), with pipeline.py wiring status and gaps vs. high-value Android artifact checklist

**Present / already correct:**

- INVENTORY parsers/ (26 modules). appchat.py: shared chat-thread building blocks (thread_conversations/count_by_confidence) for app recoveries. appdb.py: heuristic generic chat-DB parser (Telegram/Signal-plaintext/other SQLite). appfinder.py: Dynamic App Finder, generic SQLite chat-table discovery for unknown apps. bluetooth.py: parses `dumpsys bluetooth_manager` device history. browser.py: Chromium/Chrome `History` DB `urls` table. calllog.py: call-log JSON from Collector APK. celltower.py: parses `dumpsys telephony.registry` cell-tower history. collector.py: Tier-1 Collector-APK JSON (media inventory, installed apps+perms, accounts, calendar, usagestats). contacts.py: contacts JSON from Collector APK. exif.py: EXIF/GPS/datetime/device/orientation from images. google_maps.py: Google Maps/Takeout location history + current location. google_search.py: Google account + browser/Google search-cache history. instagram.py: Instagram direct.db recovery + DYI export. media.py: WhatsApp Media-folder catalogue/metadata. notification.py: parses `dumpsys notification --history`. screen_time.py: `dumpsys power`+batterystats screen-time/usagestats/app-usage/patterns. signal.py: Signal backup + plaintext-export DB (consent). sms.py: SMS JSON from Collector APK. snapchat.py: Snapchat arroyo.db/main.db protobuf recovery. telegram.py: Telegram cache4.db live query + deleted-row forensic recovery. whatsapp_backup.py: msgstore.db.crypt15 key extraction/decrypt/recovery. whatsapp_batch.py: concurrent multi-export batch parser. whatsapp_db.py: WhatsApp msgstore.db live parser. whatsapp_e2e.py: WhatsApp E2E deleted-message recovery. whatsapp_txt.py: WhatsApp native 'Export Chat' .txt. wifi.py: Wi-Fi creds (wpa_supplicant.conf / WifiConfigStore.xml).

- INVENTORY forensics/ (27 modules). auto_verify.py: hash-verify on case open. batch_transfer.py: compress+transfer batching. continuous_hash.py: on-the-fly hash verify. gps_clustering.py: Haversine/greedy GPS clustering. hash_alerts.py: hash-mismatch alerts. hash_comparison.py: cross-case hash compare. hash_timeline.py: hashing-performance timeline. hash_verification.py: manifest hash-verify dashboard. incremental_parse.py: parse large files as they arrive. integrity_report.py: HTML hash-integrity report. location_anomaly.py: late-night/unusual/new-location anomaly flags. location_correlation.py: link photo GPS to messages. location_models.py: MediaLocation/LocationCluster dataclasses. location_summary.py: aggregate location report + HTML. location_timeline.py: location timeline + Folium maps/HTML. media_extraction.py: priority-based selective media pull. media_location.py: extract GPS from WhatsApp/Telegram/SMS/Instagram media filenames. mediastore_trash.py: non-root MediaStore .trashed/pending deleted-media recovery + deletion timestamps. memory_mapped.py: mmap large-file processing. movement_analysis.py: speed/movement-type/stationary from GPS. performance_dashboard.py: real-time acquisition metrics. place_identification.py: home/work/frequent place ID. prefetch.py: predict/prefetch files. profile_optimizer.py: learn optimal file order across runs.

- WIRED parsers (called in pipeline.py run path): exif.extract_gps/extract_datetime (L1458-1459); whatsapp_txt.parse_whatsapp_export (L1489); whatsapp_db.parse_whatsapp_db (L1538); whatsapp_e2e.recover_e2e_messages (L1556); telegram.parse_telegram_db (L1577) + recover_telegram_messages (L2336); appdb.parse_app_db for signal/other (L1579); contacts.parse_contacts_json (L1599, L3411); calllog.parse_calllog_json (L1611, L3235); sms.parse_sms_json (L1622, L3323); browser.parse_browser_history (L1508); collector.parse_media_inventory/parse_apps/parse_accounts/parse_calendar/parse_usage (L1633-1678); media.parse_whatsapp_media_folder (L1920); instagram.recover_instagram_messages (L787, L2530); snapchat.recover_snapchat_messages (L806, L2584); appfinder.scan_sqlite_for_chats (L826); appchat.thread_conversations (L1125,1130); notification.parse_notification_history (L555); bluetooth.parse_bluetooth_history (L570); celltower.parse_celltower_history (L585); wifi.parse_wifi_config (L2620,2731); whatsapp_backup.recover_messages_from_db/recover_media_files (L2780-2998).

- WIRED forensics (called in pipeline.py run path): mediastore_trash.analyze_mediastore_trash (L1096); media_location.extract_all_media_locations (L3639 via _process_media_locations called L1018); location_timeline.build_location_timeline (L3654 via L1020); place_identification.identify_places_from_locations (L1022); location_anomaly.detect_location_anomalies (L1024); location_summary.generate_location_summary (L1026) + generate_location_html_summary (L3710 via L1042). gps_clustering, movement_analysis, location_correlation, location_models are imported and reachable transitively through these wired location functions.

- CHECKLIST PRESENT: Chrome/Chromium History -> parsers/browser.py wired (pipeline L1508). notification_log content -> covered via `dumpsys notification --history` parsers/notification.py wired (pipeline L550-555), though not the /data/system/notification_log file. Contacts/Call-log/SMS content -> covered via Tier-1 Collector-APK JSON (parse_contacts_json/parse_calllog_json/parse_sms_json wired), NOT the raw contacts2.db/calllog.db/mmssms.db provider DBs. usagestats/app-usage -> covered via Tier-1 collector.parse_usage (wired, pipeline L1678) reading UsageStatsManager (appops GET_USAGE_STATS granted at pipeline L3487), NOT the raw /data/system/usagestats XML. accounts -> device accounts via collector.parse_accounts (wired), NOT raw accounts.db. Hidden vault app detection -> PARTIAL: installed-app category=='anti_forensic' scored in analysis/risk.py L24-30 and surfaced in report.py L844-853, plus screen_time.py keyword list (vault/secret/incognito, L42) but screen_time is dead; detection is inventory-based, not filesystem hidden-vault scanning.


**Gaps:**

- _[major, medium]_ DEAD CODE (present but NOT wired in pipeline.py): parsers/google_maps.py (Google Maps/Takeout location history incl parse_google_takeout_location), parsers/google_search.py (Google account + search history), parsers/screen_time.py (dumpsys power/batterystats screen-unlock/power-events/usagestats/app-usage), parsers/signal.py (Signal backup/plaintext DB), parsers/whatsapp_batch.py. All are exported in parsers/__init__.py and covered by tests but have zero call sites in pipeline.py run_acquisition; verified via cross-engine grep (only tests + __init__ reference them).  (`engine/triage/pipeline.py`)

- _[major, medium]_ DEAD CODE forensics (present but only reachable via uncalled helper wrappers): prefetch.py, profile_optimizer.py, performance_dashboard.py, continuous_hash.py, hash_alerts.py, integrity_report.py, auto_verify.py, hash_verification.py, hash_comparison.py, hash_timeline.py, batch_transfer.py, incremental_parse.py, memory_mapped.py, media_extraction.py. The pipeline wrappers _initialize_optimizations/_run_optimized_acquisition/_get_optimal_file_order/_track_performance/_generate_performance_summary/_initialize_hashing/_process_hash/_verify_hash/_generate_hash_report/_auto_verify_on_complete are DEFINED at pipeline.py L3763-3942 but have NO call sites in run_acquisition (grep for call sites returned empty). Only _display_hash_realtime/_emit_hash_progress are live (L1432).  (`engine/triage/pipeline.py`)

- _[major, large]_ recent_tasks (recent-tasks stack) plus task snapshot images: MISSING entirely. No parser, no pull root, no reference (grep recent_tasks = 0 hits).  (`engine/triage/parsers/`)

- _[minor, medium]_ appops: NOT parsed as an artifact. `appops` appears only as a grant command (`appops set <pkg> GET_USAGE_STATS allow`, pipeline.py L3487) to enable the Collector APK; the appops state/dump is never collected or parsed.  (`engine/triage/parsers/`)

- _[major, medium]_ packages.xml (installed-package registry / install timestamps / signatures): MISSING. No parser, no reference (grep = 0 hits). App inventory instead comes from the Collector APK JSON, not /data/system/packages.xml.  (`engine/triage/parsers/`)

- _[major, large]_ dropbox / ANR / tombstones (crash + system dropbox artifacts): MISSING. grep tombstone/anr/dropbox = 0 hits.  (`engine/triage/parsers/`)

- _[major, medium]_ Raw provider databases telephony.db, contacts2.db, calllog.db, mmssms.db: NOT directly targeted or parsed. Content is obtained only via Tier-1 Collector-APK JSON exports; the raw /data provider DBs are not in TIER0_PULL_ROOTS (config.py L38-48) and have no dedicated parser. priority.py L43-51 lists calllog/mmssms/telephony as high-value only IF such a file is otherwise pulled.  (`engine/triage/config.py`)

- _[major, medium]_ Chrome 'Login Data' and 'Cookies' DBs: MISSING. Only the History DB is parsed (browser.py). grep 'Login Data'/'Cookies' = 0 hits.  (`engine/triage/parsers/browser.py`)

- _[major, small]_ Google location history: parser EXISTS (parsers/google_maps.py parse_google_takeout_location/parse_maps_cache) but is DEAD (never called in pipeline). Effectively no coverage in a run.  (`engine/triage/pipeline.py`)

- _[minor, large]_ GnssLog (raw GNSS logs): MISSING. grep GnssLog/gnss = 0 hits.  (`engine/triage/parsers/`)

- _[minor, small]_ accounts.db (raw AccountManager DB): NOT parsed directly. Accounts come only via Collector-APK accounts.json (collector.parse_accounts). Raw accounts.db has no parser.  (`engine/triage/parsers/collector.py`)

- _[minor, medium]_ USB connection history: MISSING. grep usb/USB = 0 hits anywhere in triage.  (`engine/triage/parsers/`)

- _[major, small]_ Screen unlock and power events: parser EXISTS (parsers/screen_time.py parse_screen_time/parse_battery_stats over dumpsys power+batterystats) but is DEAD (never wired in pipeline). Effectively no coverage.  (`engine/triage/pipeline.py`)

- _[major, large]_ Per-app dedicated DB parsers for Signal, Discord, Viber, WeChat, Line, Kik, Threema, Session, Facebook Messenger, TikTok, Tinder, Truecaller, UPI/banking: MISSING as dedicated parsers. Signal has a dead parser (signal.py) and only falls through to the generic appdb.py heuristic (which cannot read SQLCipher). Discord/Viber/WeChat/Messenger/TikTok/Truecaller/Tinder appear only as notable-app NAME tags (collector.py _PKG_APP L68-76, notification.py package labels) or notification text, not as message-DB parsers. These apps' DBs live in /data/data (root-only) and are not in TIER0_PULL_ROOTS; only if such a DB were separately pulled would the generic appfinder.scan_sqlite_for_chats attempt discovery.  (`engine/triage/parsers/`)

- _[minor, medium]_ Hidden vault app detection: only PARTIAL/inventory-based (risk.py L24-30 scores installed apps whose category=='anti_forensic'; report.py L844 surfaces them). No filesystem-level detection of vault-hidden/encrypted containers, decoy calculators, or hidden .nomedia stores.  (`engine/triage/analysis/risk.py`)

- _[major, large]_ Work-profile and dual-app / cloned-app detection: MISSING. grep work_profile/work.profile/dual/clone = 0 relevant hits. No user-id (userId 10/11) or dual-app clone-space enumeration.  (`engine/triage/parsers/`)

- _[minor, medium]_ Factory reset traces: MISSING as detection. 'factory reset' appears only as an incidental string/keyword in bluetooth.py and google_search.py, not as any dedicated factory-reset-trace analysis.  (`engine/triage/parsers/`)

