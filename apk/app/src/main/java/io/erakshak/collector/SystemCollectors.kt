package io.erakshak.collector

import android.accounts.AccountManager
import android.app.AppOpsManager
import android.app.usage.UsageStatsManager
import android.content.Context
import android.content.pm.ApplicationInfo
import android.content.pm.PackageInfo
import android.content.pm.PackageManager
import android.os.Build
import android.os.Process
import android.provider.CalendarContract
import android.provider.Settings
import org.json.JSONArray
import org.json.JSONObject
import java.util.TimeZone

/**
 * Installed-app inventory with investigative classification. Surfaces every package with its
 * version, install/update times, installer source, requested + granted dangerous permissions,
 * and — via [KnownApps] — whether it's a messaging / social / crypto / dating / browser app or
 * a **vault / anti-forensic** app worth flagging. Requires QUERY_ALL_PACKAGES for the full set.
 */
object AppsCollector {
    @Suppress("DEPRECATION")
    fun collect(ctx: Context): CollectionResult {
        return try {
            val pm = ctx.packageManager
            val pkgs: List<PackageInfo> = pm.getInstalledPackages(PackageManager.GET_PERMISSIONS)
            val out = JSONArray()
            for (pi in pkgs) {
                val ai = pi.applicationInfo
                val label = runCatching { ai?.let { pm.getApplicationLabel(it).toString() } }.getOrNull()
                val cls = KnownApps.classify(pi.packageName, label)
                val isSystem = ai != null && (ai.flags and ApplicationInfo.FLAG_SYSTEM) != 0

                val granted = JSONArray()
                val requested = JSONArray()
                val perms = pi.requestedPermissions
                val flags = pi.requestedPermissionsFlags
                if (perms != null) {
                    for (i in perms.indices) {
                        requested.put(perms[i])
                        val isGranted = flags != null && i < flags.size &&
                            (flags[i] and PackageInfo.REQUESTED_PERMISSION_GRANTED) != 0
                        if (isGranted) granted.put(perms[i])
                    }
                }

                val installer = runCatching {
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R)
                        pm.getInstallSourceInfo(pi.packageName).installingPackageName
                    else pm.getInstallerPackageName(pi.packageName)
                }.getOrNull() ?: ""

                val versionCode = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P)
                    pi.longVersionCode else pi.versionCode.toLong()

                out.put(JSONObject()
                    .put("package", pi.packageName)
                    .put("label", label ?: pi.packageName)
                    .put("version_name", pi.versionName ?: "")
                    .put("version_code", versionCode)
                    .put("first_install", pi.firstInstallTime)
                    .put("last_update", pi.lastUpdateTime)
                    .put("installer", installer)
                    .put("is_system", isSystem)
                    .put("category", cls.category)
                    .put("friendly_name", cls.friendlyName ?: JSONObject.NULL)
                    .put("notable", cls.notable)
                    .put("requested_permissions", requested)
                    .put("granted_permissions", granted))
            }
            CollectionResult("apps", "apps.json", out, out.length(),
                if (out.length() == 0) CollectionResult.EMPTY else CollectionResult.OK)
        } catch (e: Exception) {
            CollectionResult("apps", "apps.json", JSONArray(), 0, CollectionResult.ERROR, e.message)
        }
    }
}

/** Device accounts (Google / WhatsApp / Telegram / …) — proves which app identities exist. */
object AccountsCollector {
    fun collect(ctx: Context): CollectionResult {
        return try {
            val am = AccountManager.get(ctx)
            val accounts = am.accounts   // may throw SecurityException without GET_ACCOUNTS
            val out = JSONArray()
            for (a in accounts) {
                out.put(JSONObject()
                    .put("name", a.name)
                    .put("type", a.type)
                    .put("app", KnownApps.accountTypeToApp(a.type.lowercase()) ?: JSONObject.NULL))
            }
            CollectionResult("accounts", "accounts.json", out, out.length(),
                if (out.length() == 0) CollectionResult.EMPTY else CollectionResult.OK)
        } catch (e: SecurityException) {
            CollectionResult("accounts", "accounts.json", JSONArray(), 0, CollectionResult.DENIED, e.message)
        } catch (e: Exception) {
            CollectionResult("accounts", "accounts.json", JSONArray(), 0, CollectionResult.ERROR, e.message)
        }
    }
}

/** Calendar events — often the clearest timeline of a subject's real-world plans. */
object CalendarCollector {
    fun collect(ctx: Context): CollectionResult {
        if (!ctx.granted(android.Manifest.permission.READ_CALENDAR))
            return CollectionResult("calendar", "calendar.json", JSONArray(), 0,
                CollectionResult.DENIED, "READ_CALENDAR not granted")
        return try {
            val out = JSONArray()
            ctx.contentResolver.query(
                CalendarContract.Events.CONTENT_URI,
                arrayOf(
                    CalendarContract.Events.TITLE, CalendarContract.Events.DESCRIPTION,
                    CalendarContract.Events.EVENT_LOCATION, CalendarContract.Events.DTSTART,
                    CalendarContract.Events.DTEND, CalendarContract.Events.ALL_DAY,
                    CalendarContract.Events.ORGANIZER,
                    CalendarContract.Events.CALENDAR_DISPLAY_NAME,
                ), null, null, "${CalendarContract.Events.DTSTART} DESC"
            )?.use { c ->
                while (c.moveToNext()) {
                    out.put(JSONObject()
                        .put("title", with(Cur) { c.strOrNull(CalendarContract.Events.TITLE) } ?: "")
                        .put("description", with(Cur) { c.strOrNull(CalendarContract.Events.DESCRIPTION) } ?: "")
                        .put("location", with(Cur) { c.strOrNull(CalendarContract.Events.EVENT_LOCATION) } ?: "")
                        .put("dtstart", with(Cur) { c.longOrNull(CalendarContract.Events.DTSTART) } ?: 0L)
                        .put("dtend", with(Cur) { c.longOrNull(CalendarContract.Events.DTEND) } ?: 0L)
                        .put("all_day", (with(Cur) { c.intOrNull(CalendarContract.Events.ALL_DAY) } ?: 0) == 1)
                        .put("organizer", with(Cur) { c.strOrNull(CalendarContract.Events.ORGANIZER) } ?: "")
                        .put("calendar", with(Cur) { c.strOrNull(CalendarContract.Events.CALENDAR_DISPLAY_NAME) } ?: ""))
                }
            }
            CollectionResult("calendar", "calendar.json", out, out.length(),
                if (out.length() == 0) CollectionResult.EMPTY else CollectionResult.OK)
        } catch (e: SecurityException) {
            CollectionResult("calendar", "calendar.json", JSONArray(), 0, CollectionResult.DENIED, e.message)
        } catch (e: Exception) {
            CollectionResult("calendar", "calendar.json", JSONArray(), 0, CollectionResult.ERROR, e.message)
        }
    }
}

/**
 * App-usage telemetry over the last 30 days (foreground time + last-used). Requires the
 * PACKAGE_USAGE_STATS special access, which the engine enables with
 * `appops set io.erakshak.collector GET_USAGE_STATS allow`.
 */
object UsageCollector {
    private const val WINDOW_MS = 30L * 24 * 60 * 60 * 1000

    fun collect(ctx: Context): CollectionResult {
        if (!hasUsageAccess(ctx))
            return CollectionResult("usage", "usage.json", JSONArray(), 0,
                CollectionResult.DENIED, "PACKAGE_USAGE_STATS not allowed (appops GET_USAGE_STATS)")
        return try {
            val usm = ctx.getSystemService(Context.USAGE_STATS_SERVICE) as UsageStatsManager
            // Deterministic window: use device uptime-relative math is unavailable here; the
            // engine timestamps the run, so absolute now is acceptable for triage.
            val end = System.currentTimeMillis()
            val stats = usm.queryUsageStats(UsageStatsManager.INTERVAL_DAILY, end - WINDOW_MS, end)
            // Aggregate per package (INTERVAL_DAILY returns one row per day per package).
            val agg = HashMap<String, JSONObject>()
            for (s in stats) {
                val o = agg.getOrPut(s.packageName) {
                    JSONObject().put("package", s.packageName)
                        .put("total_foreground_ms", 0L).put("last_used", 0L)
                }
                o.put("total_foreground_ms", o.getLong("total_foreground_ms") + s.totalTimeInForeground)
                if (s.lastTimeUsed > o.getLong("last_used")) o.put("last_used", s.lastTimeUsed)
            }
            val out = JSONArray()
            agg.values
                .filter { it.getLong("total_foreground_ms") > 0 || it.getLong("last_used") > 0 }
                .sortedByDescending { it.getLong("total_foreground_ms") }
                .forEach { out.put(it) }
            CollectionResult("usage", "usage.json", out, out.length(),
                if (out.length() == 0) CollectionResult.EMPTY else CollectionResult.OK)
        } catch (e: Exception) {
            CollectionResult("usage", "usage.json", JSONArray(), 0, CollectionResult.ERROR, e.message)
        }
    }

    private fun hasUsageAccess(ctx: Context): Boolean = try {
        val ops = ctx.getSystemService(Context.APP_OPS_SERVICE) as AppOpsManager
        val mode = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q)
            ops.unsafeCheckOpNoThrow(AppOpsManager.OPSTR_GET_USAGE_STATS, Process.myUid(), ctx.packageName)
        else @Suppress("DEPRECATION")
        ops.checkOpNoThrow(AppOpsManager.OPSTR_GET_USAGE_STATS, Process.myUid(), ctx.packageName)
        mode == AppOpsManager.MODE_ALLOWED
    } catch (e: Exception) { false }
}

/** Device / OS / system info block plus best-effort root indicators. */
object DeviceCollector {
    fun collect(ctx: Context): CollectionResult {
        return try {
            @Suppress("HardwareIds")
            val androidId = runCatching {
                Settings.Secure.getString(ctx.contentResolver, Settings.Secure.ANDROID_ID)
            }.getOrNull() ?: ""
            val o = JSONObject()
                .put("manufacturer", Build.MANUFACTURER)
                .put("brand", Build.BRAND)
                .put("model", Build.MODEL)
                .put("device", Build.DEVICE)
                .put("product", Build.PRODUCT)
                .put("hardware", Build.HARDWARE)
                .put("android_version", Build.VERSION.RELEASE)
                .put("sdk", Build.VERSION.SDK_INT)
                .put("build_id", Build.DISPLAY)
                .put("fingerprint", Build.FINGERPRINT)
                .put("bootloader", Build.BOOTLOADER)
                .put("tags", Build.TAGS ?: "")
                .put("android_id", androidId)
                .put("timezone", TimeZone.getDefault().id)
                .put("locale", java.util.Locale.getDefault().toString())
                .put("collected_at_ms", System.currentTimeMillis())
                .put("root_indicators", rootIndicators())
            CollectionResult("device", "device_extra.json", o, 1, CollectionResult.OK)
        } catch (e: Exception) {
            CollectionResult("device", "device_extra.json", JSONObject(), 0, CollectionResult.ERROR, e.message)
        }
    }

    private fun rootIndicators(): JSONArray {
        val hits = JSONArray()
        val suPaths = listOf(
            "/system/bin/su", "/system/xbin/su", "/sbin/su", "/su/bin/su",
            "/system/app/Superuser.apk", "/data/adb/magisk", "/system/bin/magisk",
        )
        for (p in suPaths) if (runCatching { java.io.File(p).exists() }.getOrDefault(false)) hits.put(p)
        if ((Build.TAGS ?: "").contains("test-keys")) hits.put("build-tags:test-keys")
        return hits
    }
}
