# eRakshak Collector — Tier-1 Helper APK

A **sideloaded** Android app that reads the artifacts the `shell` UID cannot reach without an
app identity, and writes them as JSON to public `Download/` (via MediaStore on Android 10+) for
the engine to `adb pull`. As of v0.2 it is a full non-root collector.

## Collectors (v0.2)

Each `--es action <name>` runs one collector; `dump_all` runs them all (off the main thread)
and writes a `collector_manifest.json` summarising what ran, row counts, and any permission
denials. Every collector returns a clean status instead of crashing when a permission is missing.

| Action | Output | Permission | Grantable via `pm grant`? |
|---|---|---|---|
| `dump_contacts` | `contacts.json` (merged numbers + emails) | READ_CONTACTS | ✅ |
| `dump_calllog` | `calllog.json` | READ_CALL_LOG | ❌ hard-restricted → Dialer role swap |
| `dump_sms` | `sms.json` (+ MMS text) | READ_SMS | ❌ hard-restricted → SMS role swap |
| `dump_media` | `media_inventory.json` (MediaStore: trashed/favorite/owner-app/EXIF-GPS) | READ_MEDIA_* / ACCESS_MEDIA_LOCATION | ✅ |
| `dump_apps` | `apps.json` (inventory + vault/messaging classification) | QUERY_ALL_PACKAGES | ✅ (install-time) |
| `dump_accounts` | `accounts.json` | GET_ACCOUNTS | ✅ (OEM-dependent visibility) |
| `dump_calendar` | `calendar.json` | READ_CALENDAR | ✅ |
| `dump_usage` | `usage.json` | PACKAGE_USAGE_STATS | via `appops set … GET_USAGE_STATS allow` |
| `dump_device` | `device_extra.json` (Build/props, root indicators) | — | — |
| `dump_all` | all of the above + `collector_manifest.json` | — | — |

The engine drives `dump_all` when **Full collection** is enabled in the Acquisition view (or
`--tier1-collect-all` on the CLI): install → grant the non-restricted permissions → enable the
usage appop → `am start … dump_all` → pull every JSON → uninstall — all logged with
`alters_device: true`.

---

The original minimal surface — **contacts**, and (behind an explicit, revert-after-use step)
**call log** and **SMS** — is still available as the individual actions above.

This is the **Tier-1** path in the acquisition model. It is **state-changing** by
definition (it installs an app and grants it permissions), so every step it triggers is
logged in the case audit trail with `alters_device: true`. It is never run silently and
never required for the Tier-0 core demo.

## Why a helper APK at all?

`adb shell` runs as UID 2000, which holds none of the dangerous content-provider
permissions. There is no non-root way to read `content://contacts` / `call_log` / `sms`
from the bare shell. The industry-standard workaround (used by Cellebrite's and Oxygen's
"agent" methods) is to install a tiny unprivileged app that requests those permissions
through the normal Android model. This is that app, kept deliberately small and auditable.

## Permission tiers (mirrors the engine's honesty model)

| Artifact | Permission | Grantable via `pm grant` (no root)? | Notes |
|---|---|---|---|
| Contacts | `READ_CONTACTS` | ✅ Yes (dangerous, not hard-restricted) | The clean Tier-1 win. |
| Media metadata | `READ_MEDIA_IMAGES/VIDEO/AUDIO` | ✅ Yes | For full MediaStore enumeration. |
| Call log | `READ_CALL_LOG` | ❌ No (hard-restricted) | Needs a temporary default-Dialer role swap — intrusive, log & revert. |
| SMS | `READ_SMS` | ❌ No (hard-restricted) | Needs a temporary default-SMS role swap — intrusive, log & revert. |

## Acquisition flow (scripted by the engine, logged in the audit trail)

```bash
# 1. install (logged: alters_device=true)
adb install -r eRakshakCollector.apk

# 2a. contacts — clean grant, no root
adb shell pm grant io.erakshak.collector android.permission.READ_CONTACTS
adb shell am start -n io.erakshak.collector/.MainActivity --es action dump_contacts

# 2b. media metadata
adb shell pm grant io.erakshak.collector android.permission.READ_MEDIA_IMAGES

# 3. (INTRUSIVE, optional) call log / SMS via role swap — must be reverted afterwards
adb shell cmd role add-role-holder android.app.role.DIALER io.erakshak.collector
adb shell am start -n io.erakshak.collector/.MainActivity --es action dump_calllog
# ... then restore the original default dialer and uninstall:
adb shell cmd role remove-role-holder android.app.role.DIALER io.erakshak.collector

# 4. pull the JSON the app wrote, then uninstall
adb pull /sdcard/Download/contacts.json
adb pull /sdcard/Download/calllog.json
adb uninstall io.erakshak.collector
```

The engine's `parsers/contacts.py` and `parsers/calllog.py` ingest exactly the JSON shape
this app emits, and the pipeline picks up `contacts.json` / `calllog.json` automatically
when they appear in `/sdcard/Download`.

## Building

This needs Android Studio / the Android SDK (not bundled in this repo). The `src/` here is
the complete app source — open `apk/` as a Gradle project, or:

```bash
cd apk && ./gradlew assembleDebug
# output: app/build/outputs/apk/debug/app-debug.apk
```

> For the hackathon Round-1 demo, the **contacts** path is the only Tier-1 artifact on the
> must-ship list. The call-log/SMS role-swap is deliberately gated as an advanced step.
> The engine already handles the JSON either way, so the demo works whether or not the APK
> is built — the mock corpus ships pre-made `contacts.json` / `calllog.json` so the
> Contacts view is populated without any device.
