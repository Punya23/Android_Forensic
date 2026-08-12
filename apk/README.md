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
| `dump_calllog` | `calllog.json` | `READ_CALL_LOG` | ❌ hard-restricted → Dialer role swap |
| `dump_sms` | `sms.json` (+ MMS text) | `READ_SMS` | ❌ hard-restricted → SMS role swap |
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
| Call log | `READ_CALL_LOG` | ❌ hard-restricted | Needs a temporary default-Dialer role swap — intrusive; log it and revert |
| SMS | `READ_SMS` | ❌ hard-restricted | Needs a temporary default-SMS role swap — same treatment |
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

# 3. INTRUSIVE, optional — call log / SMS via role swap. Must be reverted.
adb shell cmd role add-role-holder android.app.role.DIALER io.erakshak.collector
adb shell am start -n io.erakshak.collector/.MainActivity --es action dump_calllog
adb shell cmd role remove-role-holder android.app.role.DIALER io.erakshak.collector

# 4. pull what it wrote, then remove the app
adb pull /sdcard/Download/collector_manifest.json
adb pull /sdcard/Download/contacts.json
adb uninstall io.erakshak.collector
```

`engine/triage/parsers/collector.py` ingests exactly the JSON shapes this app emits, and
the pipeline picks them up automatically when they appear in `/sdcard/Download`.

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
