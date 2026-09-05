# SNAGR Collector — Tier-1 helper APK

[← back to the main README](../README.md)

A **sideloaded** Android app that reads the artifacts `adb shell` cannot reach, and writes
them as JSON into public `Download/` (via MediaStore on Android 10+) for the engine to
`adb pull`. Installed, run, pulled from, and uninstalled — all inside one acquisition, all
logged.

## Why a helper APK at all

`adb shell` runs as UID 2000, which holds none of the dangerous content-provider
permissions. There is no non-root way to read `content://contacts`, `call_log` or `sms`
from the bare shell. The industry-standard workaround — Cellebrite's and Oxygen's "agent"
methods do the same thing — is a small unprivileged app that requests those permissions
through the normal Android model. This is that app, kept deliberately small and auditable.

Being Tier 1 means it is **state-changing by definition**: it installs software and grants
it permissions. Every step is logged in the case audit trail with `alters_device: true`, it
is never run silently, and the Tier-0 core never depends on it.

## Collectors

One `--es action <name>` per collector; `dump_all` runs all fourteen off the main thread
and writes `collector_manifest.json` summarising what ran, row counts, and every denial.

| Action | Output | Permission | Grantable via `pm grant`? |
|---|---|---|---|
| `dump_contacts` | `contacts.json` (merged numbers + emails) | `READ_CONTACTS` | ✅ |
| `dump_calllog` | `calllog.json` | `READ_CALL_LOG` | ✅ via `adb shell pm grant` — hard-restricted for a *normal* install, but `pm grant` issued from the ADB shell UID is allowlisted for it (see the honesty note in `pipeline.py`, tag P2-5); the flow aborts rather than falling back to a role swap if that ever stops being true on some future build |
| `dump_sms` | `sms.json` (+ MMS text) | `READ_SMS` | ✅ same as above |
| `dump_calendar` | `calendar.json` | `READ_CALENDAR` | ✅ |
| `dump_accounts` | `accounts.json` (Google / WhatsApp / Telegram / Snapchat identities) | `GET_ACCOUNTS` | ✅ — visibility is OEM-dependent |
| `dump_apps` | `apps.json` (inventory + vault/messaging classification) | `QUERY_ALL_PACKAGES` | ✅ install-time |
| `dump_usage` | `usage.json` | `PACKAGE_USAGE_STATS` | via `appops set … GET_USAGE_STATS allow` |
| `dump_media` | `media_inventory.json` (trashed / favorite / owner-app / EXIF GPS) | `READ_MEDIA_*`, `ACCESS_MEDIA_LOCATION` | ✅ |
| `dump_recordings` | `recordings.json` (OEM call-recording folders, 10+ vendor paths) | `READ_EXTERNAL_STORAGE` (≤ SDK 32) / `READ_MEDIA_AUDIO` | ✅ — but see the scoped-storage note below |
| `dump_notifications` | `notifications.json` | Android 11+ history via reflection, plus a live watcher buffer | ❌ — `@SystemApi`, blocked on many builds |
| `dump_location` | `location.json` (last-known fix per provider) | `ACCESS_FINE_LOCATION` / `ACCESS_COARSE_LOCATION` | ✅ |
| `dump_wifi` | `wifi.json` (current association, scan results, saved list where readable) | `ACCESS_WIFI_STATE` + location for scans | ✅ |
| `dump_bluetooth` | `bluetooth.json` (adapter + bonded devices) | `BLUETOOTH_CONNECT` (Android 12+) / `BLUETOOTH` | ✅ |
| `dump_device` | `device_extra.json` (Build props, root indicators) | — | — |
| `dump_all` | all of the above + `collector_manifest.json` | — | — |

The engine drives `dump_all` when **Full collection** is enabled in the Acquisition view,
or `--tier1-collect-all` on the CLI: install → grant the non-restricted permissions →
enable the usage appop → `am start … dump_all` → pull every JSON → uninstall.

## An empty file is never allowed to mean "nothing was there"

Every collector returns a `CollectionResult` instead of throwing, so a missing permission
becomes a labelled `denied` row in `collector_manifest.json` rather than an aborted run —
and, more importantly, rather than an empty JSON file that looks exactly like a device with
nothing on it. The statuses are `ok` / `empty` / `denied` / `unsupported` / `error`.

Several Android APIs make that distinction hard, because they answer a blocked call with an
empty result rather than an error. Those cases are detected and their reason recorded:

- `getConfiguredNetworks()` returns an **empty list** to non-system apps on Android 10+, not
  null and not an exception. Only the root-tier `WifiConfigStore.xml` pull can see the saved
  networks — see [`docs/NETWORK_ARTIFACTS.md`](../docs/NETWORK_ARTIFACTS.md).
- Wi-Fi scan results come back empty whenever the device's master location toggle is off,
  even with `ACCESS_FINE_LOCATION` granted (Android 8.1+).
- An empty bonded-device set while the Bluetooth adapter is off is inconclusive on some OEM
  stacks, not evidence of no pairings.
- `dump_recordings` walks the known OEM folders directly with `File`, which scoped storage
  restricts from Android 11 on. An empty `recordings.json` on a modern device may mean the
  folder was unreachable rather than empty; the result carries "No call recording files
  found in known OEM paths", which is a statement about the *paths searched*.

Where a device detail cannot be read due to a `SecurityException`, the app writes the
literal string `[permission_denied]` into the field rather than omitting it.

## Permission reality

| Artifact | Permission | `pm grant` without root? | Notes |
|---|---|---|---|
| Contacts, calendar, media, location, Wi-Fi, Bluetooth | dangerous, not hard-restricted | ✅ | The clean Tier-1 wins |
| Call log | `READ_CALL_LOG` | ✅ from `adb shell pm grant` only | Blocked if requested through the runtime dialog like a normal app; the ADB-shell grant path is allowlisted around that restriction. No role swap is performed — if the grant fails, the flow aborts and logs it rather than escalating to a role change |
| SMS | `READ_SMS` | ✅ from `adb shell pm grant` only | Same treatment as call log |
| App usage | `PACKAGE_USAGE_STATS` | ❌ (not a runtime permission) | Granted with `appops`, not `pm grant` |
| Wi-Fi passwords, Bluetooth link keys | — | ❌ ever | Root-only. No app can read them |

## The flow, by hand

```bash
# 1. install (logged: alters_device=true)
adb install -r SNAGRCollector.apk

# 2. clean grants, no root
adb shell pm grant io.erakshak.collector android.permission.READ_CONTACTS
adb shell pm grant io.erakshak.collector android.permission.READ_MEDIA_IMAGES
adb shell am start -n io.erakshak.collector/.MainActivity --es action dump_all

# 3. INTRUSIVE, optional — call log / SMS. `pm grant` issued from the ADB shell UID is
#    allowlisted for these two even though they're hard-restricted for a normal app; no
#    role swap happens. If the grant ever fails on some future build, the engine aborts
#    that flow and logs it rather than escalating to a role change.
adb shell pm grant io.erakshak.collector android.permission.READ_CALL_LOG
adb shell am start -n io.erakshak.collector/.MainActivity --es action dump_calllog

# 4. pull what it wrote, then remove the app
adb pull /sdcard/Download/collector_manifest.json
adb pull /sdcard/Download/contacts.json
adb uninstall io.erakshak.collector
```

The engine's own driver (`pipeline._run_tier1_collect_all` / `_run_tier1_calllog_helper` /
`_run_tier1_sms_helper` / `_run_tier1_contacts_helper`) doesn't just sleep a fixed few
seconds after step 2/3 and hope — it polls for `collector_manifest.json` to appear on the
device (written last, after every requested collector has run), and gives OEMs with a
known interactive quirk — OnePlus's runtime-dialog fallback, Xiaomi's Mi Account prompt,
OPPO/Realme's lock-screen PIN, Vivo's "Verify apps over USB" — up to 90s instead of the
generic 20s, so the examiner has real time to clear whatever's on screen before the pull
happens. See `_TIER1_INTERACTIVE_QUIRKS` in `pipeline.py`.

`engine/triage/parsers/collector.py` ingests exactly the JSON shapes this app emits, and
the pipeline picks them up automatically when they appear in `/sdcard/Download`.

## Developer Options / USB debugging, brand by brand

None of this can be automated, on any brand, ever — and that's not a gap in this tool.
Android requires a human to tap through Developer Options → USB debugging → the "Allow
USB debugging?" prompt, on the device's own screen, before *any* ADB tool can talk to it.
The entire point of that prompt is that the computer side isn't trusted yet, so nothing
issued from the computer side can substitute for it. Cellebrite, Oxygen, and every other
non-root forensic tool hits the same wall.

What this repo does instead:

- `python -m triage.cli check-device --brand <brand>` reports the exact ADB state
  (`no_device` / `unauthorized` / `offline` / `device`) and prints that brand's full
  checklist — the generic AOSP sequence plus whatever extra friction that OEM is known to
  add. See `engine/triage/preflight.py` for the source of truth; `triage.config.OEM_QUIRKS`
  for the same brand keys used everywhere else in the codebase (device intake, the
  report's OEM-quirk footnotes, and the `_TIER1_INTERACTIVE_QUIRKS` wait logic above).
- `--reassert-dev-options` runs the *one* legitimately automatable step: once an ADB shell
  session already exists, `settings put global development_settings_enabled 1` +
  `adb_enabled 1` re-enable Developer Options if an OEM build (MIUI does this) silently
  flips it back off between sessions on the same device. It cannot perform the first-time
  enable — there's no ADB session to run it over yet on a device that's never had USB
  debugging on.

Known extra steps per brand (beyond "Build number ×7 → Developer options → USB debugging
→ tap Allow"):

| Brand | Extra step |
|---|---|
| Xiaomi / Redmi / POCO | Separate "USB debugging (Security settings)" toggle, needs a signed-in Mi Account + active SIM; also enable "Install via USB"; disable battery saver |
| OPPO / Realme | May prompt for the lock-screen PIN during `adb install`; keep the collector in the foreground |
| OnePlus | No extra Developer Options step, but `pm grant` is blocked at collection time — expect on-screen permission dialogs instead |
| Vivo / iQOO | Disable "Verify apps over USB" if present; keep the collector in the foreground (i Manager kills background apps) |
| Honor | ADB authorization can time out faster than stock Android — re-authorize promptly |
| Huawei | Only AOSP-based HarmonyOS (≤3.x) works at all; HarmonyOS NEXT has no Android layer and no Developer-Options equivalent reaches it |
| Samsung | No extra step to *reach* the device, but Secure Folder content stays encrypted regardless |
| Google / Motorola / Nothing | Stock sequence only |

## Building

Needs the Android SDK (not bundled). Open `apk/` as a Gradle project, or:

```bash
cd apk && ./gradlew assembleDebug
# → app/build/outputs/apk/debug/app-debug.apk
```

`assembleRelease` currently produces an **unsigned** APK — there is no `signingConfigs`
block. Tracked in [`docs/NOTES.md`](../docs/NOTES.md) with the rest of the known gaps.

The package id is still `io.erakshak.collector` after the SNAGR rename. That is deliberate,
not an oversight: the string is duplicated across Kotlin, Python and test assertions with no
single source of truth, nothing but `adb` ever sees it, and the reasoning is recorded in
`docs/NOTES.md`.

## Demoing without the APK

The mock corpus (`engine/tools/make_corpus.py`) ships pre-made collector output —
`contacts.json`, `calllog.json`, `sms.json`, `accounts.json`, `apps.json`, `calendar.json`,
`media_inventory.json`, `usage.json` — so those views populate with no device and no build.
The engine ingests the JSON identically either way.
