package io.erakshak.collector

import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.location.LocationManager
import android.net.wifi.WifiManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.view.WindowManager
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import org.json.JSONArray
import org.json.JSONObject

/**
 * SNAGR Collector — Tier-1 forensic helper.
 *
 * Supported actions (trigger via ADB):
 *   dump_contacts      → contacts.json
 *   dump_calllog       → calllog.json
 *   dump_sms           → sms.json
 *   dump_media         → media_inventory.json (MediaStore catalogue + EXIF/MP4 GPS)
 *   dump_apps          → apps.json
 *   dump_accounts      → accounts.json
 *   dump_calendar      → calendar.json
 *   dump_usage         → usage.json
 *   dump_device        → device_extra.json
 *   dump_wifi          → wifi.json      (saved networks + current association + scan)
 *   dump_bluetooth     → bluetooth.json (adapter + bonded devices)
 *   dump_location      → location.json  (last-known fix per provider)
 *   dump_recordings    → recordings.json (call recording audio file index)
 *   dump_notifications → notifications.json (notification history, needs Notification Access)
 *   dump_all           → all of the above + collector_manifest.json
 *
 * Every action routes through the same [CollectionResult] registry, so a denied permission is
 * recorded as a `denied` row in `collector_manifest.json` rather than silently producing an
 * empty file. "Nothing was there" and "we were not allowed to look" are different findings.
 *
 * Install with:
 *   adb install -r app-debug.apk
 *
 * OEM-specific notes (detected at runtime):
 *   Samsung One UI   — Knox / Secure Folder content is NOT accessible via ADB.
 *   Xiaomi/HyperOS   — Enable Developer Options > 'Install via USB' before install.
 *   OPPO/Realme/ColorOS — OS may ask for lock screen PIN during `adb install`.
 *   OnePlus/OxygenOS — pm grant is blocked; runtime permission dialog is used instead.
 *   Huawei/HarmonyOS — Only AOSP-based builds (≤3.x) are supported.
 */
class MainActivity : Activity() {

    private var pendingAction: String? = null
    private lateinit var screen: ProgressScreen
    private var started = false
    private var finishedAll = false

    companion object {
        private const val REQ_CODE = 1001
        private const val PREFLIGHT_SECONDS = 30

        private val ALL_PERMISSIONS = buildList {
            add(Manifest.permission.READ_CONTACTS)
            add(Manifest.permission.READ_CALL_LOG)
            add(Manifest.permission.READ_SMS)
            add(Manifest.permission.READ_CALENDAR)
            add(Manifest.permission.GET_ACCOUNTS)
            // Fine location doubles as the gate for WiFi SSID/scan results on Android 8.1+.
            add(Manifest.permission.ACCESS_FINE_LOCATION)
            add(Manifest.permission.ACCESS_COARSE_LOCATION)
            add(Manifest.permission.ACCESS_WIFI_STATE)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                add(Manifest.permission.BLUETOOTH_CONNECT)
                add(Manifest.permission.BLUETOOTH_SCAN)
            } else {
                @Suppress("DEPRECATION")
                add(Manifest.permission.BLUETOOTH)
                @Suppress("DEPRECATION")
                add(Manifest.permission.BLUETOOTH_ADMIN)
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                add(Manifest.permission.READ_MEDIA_IMAGES)
                add(Manifest.permission.READ_MEDIA_VIDEO)
                add(Manifest.permission.READ_MEDIA_AUDIO)
            } else {
                add(Manifest.permission.READ_EXTERNAL_STORAGE)
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                // Runtime-gated from Android 10: without it MediaStore hands back EXIF with the
                // GPS tags stripped, which would look identical to a photo that never had any.
                add(Manifest.permission.ACCESS_MEDIA_LOCATION)
            } else {
                add(Manifest.permission.WRITE_EXTERNAL_STORAGE)
            }
        }.toTypedArray()
    }

    // ── Lifecycle ─────────────────────────────────────────────────────────

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // A screen that dims mid-collection stops the foreground-only readers (location, Wi-Fi).
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        pendingAction = intent.getStringExtra("action")
        screen = buildScreen(pendingAction)
        setContentView(screen.root)

        val missing = ALL_PERMISSIONS.filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }

        // Ask once per install. Every dump_<name> action is a fresh launch of this Activity, so
        // re-asking for whatever is still missing would re-prompt the examiner on each one — and
        // a permission the OS refuses to grant (restricted, or denied) would never stop asking.
        // Collectors report their own `denied` status, so proceeding loses nothing.
        val prefs = getSharedPreferences("collector", MODE_PRIVATE)
        if (missing.isNotEmpty() && !prefs.getBoolean("permissions_requested", false)) {
            // The flag is saved when the examiner has answered (onRequestPermissionsResult), not
            // here: a relaunch while dialogs are still open must not skip them and collect early.
            screen.setStage(
                "Permissions needed",
                oemGuidanceText() ?: "Tap Allow on each prompt so every category can be collected.",
            )
            ActivityCompat.requestPermissions(this, missing.toTypedArray(), REQ_CODE)
        } else {
            executeAction(pendingAction)
        }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int, permissions: Array<out String>, grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode != REQ_CODE) return
        // An empty result means this request was cancelled by a newer one (the Activity was
        // relaunched) — the examiner has not answered yet, so neither record it nor collect.
        if (permissions.isEmpty()) return
        getSharedPreferences("collector", MODE_PRIVATE).edit()
            .putBoolean("permissions_requested", true).apply()
        // Collect regardless of what was denied. Refusing to run because one permission was
        // withheld would throw away every artifact the granted permissions still reach — a
        // denied READ_SMS is no reason to skip location, media or the app inventory. Each
        // collector reports its own `denied` status, and `collector_manifest.json` records the
        // full grant state so the report can say exactly what was out of reach and why.
        executeAction(pendingAction)
    }

    // ── Collector registry ────────────────────────────────────────────────

    /**
     * Every collector, keyed by the suffix of its `dump_<name>` action and listed in the order
     * `dump_all` runs them. Cheap content-provider reads come first so a device that dies or is
     * unplugged mid-run still yields the high-value comms artifacts; MediaStore enumeration —
     * the slowest stage by far — runs last, so stopping early still leaves every small artifact.
     */
    private val registry: LinkedHashMap<String, (Context) -> CollectionResult> = linkedMapOf(
        "contacts" to { c: Context -> ContactsCollector.collect(c) },
        "calllog" to { c: Context -> CallLogCollector.collect(c) },
        "sms" to { c: Context -> SmsCollector.collect(c) },
        "calendar" to { c: Context -> CalendarCollector.collect(c) },
        "accounts" to { c: Context -> AccountsCollector.collect(c) },
        "apps" to { c: Context -> AppsCollector.collect(c) },
        "usage" to { c: Context -> UsageCollector.collect(c) },
        "recordings" to { c: Context -> CallRecordingsCollector.collect(c) },
        "notifications" to { c: Context -> NotificationCollector.collect(c) },
        "location" to { c: Context -> LocationCollector.collect(c) },
        "wifi" to { c: Context -> WifiCollector.collect(c) },
        "bluetooth" to { c: Context -> BluetoothCollector.collect(c) },
        "device" to { c: Context -> DeviceCollector.collect(c) },
        "media" to { c: Context -> MediaCollector.collect(c) },
    )

    /** Human-readable label per registry key, for the live progress checklist. */
    private val displayNames: Map<String, String> = mapOf(
        "contacts" to "Contacts", "calllog" to "Call log", "sms" to "SMS",
        "calendar" to "Calendar", "accounts" to "Accounts", "apps" to "Installed apps",
        "usage" to "App usage", "media" to "Media inventory", "recordings" to "Call recordings",
        "notifications" to "Notification history", "location" to "Location",
        "wifi" to "Wi-Fi", "bluetooth" to "Bluetooth", "device" to "Device info",
    )

    // ── Action dispatcher ─────────────────────────────────────────────────

    private fun wantedFor(action: String?): List<String> = when {
        action == "dump_all" -> registry.keys.toList()
        action != null && action.startsWith("dump_") -> listOf(action.removePrefix("dump_"))
        else -> emptyList()
    }

    private fun buildScreen(action: String?): ProgressScreen {
        val version = runCatching { packageManager.getPackageInfo(packageName, 0).versionName }
            .getOrNull() ?: "?"
        val facts = listOf(
            "Case" to (intent.getStringExtra("case_id") ?: ""),
            "Examiner" to (intent.getStringExtra("examiner") ?: ""),
            "Device" to "${Build.MANUFACTURER} ${Build.MODEL}".trim(),
            "Android" to "${Build.VERSION.RELEASE} (API ${Build.VERSION.SDK_INT})",
            "Task" to (action ?: "none"),
        )
        // With no task (launched by hand) show every category, all pending, rather than an empty list.
        val shown = wantedFor(action).ifEmpty { registry.keys.toList() }
        return ProgressScreen(this, shown, displayNames, facts, version)
    }

    /** What the examiner can switch on now so Location and Wi-Fi evidence is not empty. */
    private fun preflightIssues(wanted: List<String>): List<ProgressScreen.Issue> {
        val issues = mutableListOf<ProgressScreen.Issue>()
        if ("location" in wanted || "wifi" in wanted) {
            val lm = getSystemService(Context.LOCATION_SERVICE) as? LocationManager
            val on = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) lm?.isLocationEnabled ?: true else true
            if (!on) issues += ProgressScreen.Issue(
                "Location is off",
                "Needed for the last known location fix and nearby Wi-Fi networks.",
                Intent(Settings.ACTION_LOCATION_SOURCE_SETTINGS),
            )
        }
        if ("wifi" in wanted) {
            val wm = applicationContext.getSystemService(Context.WIFI_SERVICE) as? WifiManager
            if (wm != null && !wm.isWifiEnabled) issues += ProgressScreen.Issue(
                "Wi-Fi is off",
                "Needed to record the connected and nearby Wi-Fi networks.",
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) Intent(Settings.Panel.ACTION_INTERNET_CONNECTIVITY)
                else Intent(Settings.ACTION_WIFI_SETTINGS),
            )
        }
        return issues
    }

    private fun executeAction(action: String?) {
        if (action == null) {
            screen.setStage(
                "Ready",
                "No task was requested. The SNAGR engine starts collection from the dashboard.",
            )
            return
        }
        val wanted = wantedFor(action)
        if (wanted.isEmpty() || wanted.any { it !in registry }) {
            screen.setStage(
                "Unknown action",
                "$action — known: dump_all, " + registry.keys.joinToString(", ") { "dump_$it" },
                Palette.of(this).deletion,
            )
            return
        }
        val issues = preflightIssues(wanted)
        if (issues.isEmpty()) beginCollection(action, wanted) else awaitPreflight(action, wanted)
    }

    /**
     * Location or Wi-Fi is off: ask the examiner to switch it on, but never wait forever — the
     * engine is waiting on this run, so after [PREFLIGHT_SECONDS] it proceeds and the collectors
     * report the gap themselves.
     */
    private fun awaitPreflight(action: String, wanted: List<String>) {
        val handler = Handler(Looper.getMainLooper())
        var left = PREFLIGHT_SECONDS
        var shownFor: List<String>? = null
        val step = object : Runnable {
            override fun run() {
                if (started) return
                val issues = preflightIssues(wanted)
                if (issues.isEmpty() || left <= 0) { beginCollection(action, wanted); return }
                val keys = issues.map { it.title }
                if (keys != shownFor) {
                    shownFor = keys
                    screen.showPreflight(issues, left) { beginCollection(action, wanted) }
                } else {
                    screen.setCountdown(left)
                }
                left--
                handler.postDelayed(this, 1000)
            }
        }
        step.run()
    }

    private fun beginCollection(action: String, wanted: List<String>) {
        if (started) return
        started = true
        screen.hidePreflight()
        val main = Handler(Looper.getMainLooper())
        val done = mutableListOf<CollectionResult>()
        screen.update(emptyList(), wanted.first(), false)

        // Clocks only; the rest of the screen is touched when a collector starts or finishes.
        val clock = object : Runnable {
            override fun run() {
                if (finishedAll) return
                screen.tick()
                main.postDelayed(this, 1000)
            }
        }
        main.postDelayed(clock, 1000)

        // MediaStore enumeration plus per-file EXIF/MP4 GPS reads can run for tens of seconds.
        // Running that on the main thread risks an ANR kill mid-collection, which would leave a
        // half-written evidence set, so collection happens on a worker; each collector's start
        // and result are posted back so the examiner sees what is running right now.
        Thread {
            for ((i, name) in wanted.withIndex()) {
                if (i > 0) {
                    val snapshot = done.toList()
                    main.post { screen.update(snapshot, name, false) }
                }
                done.add(runCollector(name))
            }
            writeManifest(action, done)
            val snapshot = done.toList()
            main.post {
                finishedAll = true
                screen.update(snapshot, null, true)
                main.postDelayed({ finish() }, 8000)
            }
        }.apply { name = "snagr-collect"; isDaemon = false }.start()
    }

    /**
     * Run one collector and persist its payload.
     *
     * A denied or failed collector still writes its (empty) output file. The engine treats a
     * missing file as "not produced" and cannot tell that apart from "produced, but empty" —
     * writing the file plus a manifest row with the reason keeps the two distinguishable, which
     * is the whole point of the honesty model.
     */
    private fun runCollector(name: String): CollectionResult {
        val result = try {
            registry.getValue(name)(this)
        } catch (e: SecurityException) {
            CollectionResult(name, "$name.json", JSONArray(), 0, CollectionResult.DENIED, e.message)
        } catch (e: Exception) {
            CollectionResult(name, "$name.json", JSONArray(), 0, CollectionResult.ERROR, e.message)
        }
        return try {
            StorageWriter.write(this, result.fileName, payloadText(result.payload))
            result
        } catch (e: Exception) {
            // The collection itself worked; only persistence failed. Say so precisely.
            result.copy(
                status = CollectionResult.ERROR,
                error = "collected ${result.count} record(s) but write failed: ${e.message}",
            )
        }
    }

    private fun payloadText(payload: Any): String = when (payload) {
        is JSONArray -> payload.toString(2)
        is JSONObject -> payload.toString(2)
        else -> JSONArray().put(payload.toString()).toString(2)
    }

    /**
     * Write `collector_manifest.json` — the per-run audit record.
     *
     * The engine ingests this alongside the data files so the case log can state, for each
     * collector, whether it ran, what it was denied, and how many records it produced.
     */
    private fun writeManifest(action: String, results: List<CollectionResult>): String? {
        val manifest = JSONObject()
            .put("action", action)
            .put("collected_at_ms", System.currentTimeMillis())
            .put("package", packageName)
            .put("sdk_int", Build.VERSION.SDK_INT)
            .put("manufacturer", Build.MANUFACTURER)
            .put("model", Build.MODEL)
            .put("collectors", JSONArray().also { arr -> results.forEach { arr.put(it.summary()) } })
            .put("permissions", JSONArray().also { arr ->
                ALL_PERMISSIONS.forEach { p ->
                    arr.put(
                        JSONObject()
                            .put("permission", p)
                            .put(
                                "granted",
                                ContextCompat.checkSelfPermission(this, p) ==
                                    PackageManager.PERMISSION_GRANTED,
                            )
                    )
                }
            })
        return runCatching {
            StorageWriter.write(this, "collector_manifest.json", manifest.toString(2))
        }.getOrNull()
    }

    // ── UI screens ────────────────────────────────────────────────────────

    /**
     * Detect the OEM skin from Build constants and return brand-specific
     * guidance text shown on the permission screen.
     * This mirrors the approach taken for OnePlus/OxygenOS in commit 6485e5e.
     */
    private fun oemGuidanceText(): String? {
        val mfr = Build.MANUFACTURER.lowercase()
        val brand = Build.BRAND.lowercase()
        return when {
            brand == "samsung" || mfr == "samsung" ->
                "⚠️ Samsung One UI detected.\n" +
                "Knox Secure Folder content is encrypted and NOT accessible via ADB. " +
                "Data inside Secure Folder will not be collected."

            brand in listOf("xiaomi", "redmi", "poco") || mfr == "xiaomi" ->
                "⚠️ Xiaomi / HyperOS / MIUI detected.\n" +
                "Required steps before install:\n" +
                "  1. Settings → Additional settings → Developer options\n" +
                "  2. Enable \"Install via USB\"\n" +
                "  3. Log in with a Mi Account if prompted.\n" +
                "Battery saver may kill this app mid-run — disable it before collection."

            brand in listOf("oppo") || mfr == "oppo" ->
                "⚠️ OPPO / ColorOS detected.\n" +
                "The OS may ask for your lock screen PIN when installing the APK via ADB. " +
                "Have the device owner enter it on-screen. " +
                "ColorOS may kill background processes — keep the app in the foreground."

            brand == "realme" || mfr == "realme" ->
                "⚠️ Realme UI (ColorOS) detected.\n" +
                "The OS may ask for your lock screen PIN when installing the APK via ADB. " +
                "Keep this app in the foreground during collection."

            brand == "oneplus" || mfr == "oneplus" ->
                "⚠️ OnePlus / OxygenOS detected.\n" +
                "Runtime permission dialog is used (pm grant is blocked on this OS). " +
                "Tap Allow for each permission when the dialog appears."

            brand == "honor" || mfr == "honor" ->
                "⚠️ Honor / MagicOS detected.\n" +
                "USB debugging authorization may time out quickly. " +
                "Re-authorize ADB in Developer Options if the connection drops."

            brand == "huawei" || mfr == "huawei" ->
                "⚠️ Huawei / HarmonyOS detected.\n" +
                "Only AOSP-based HarmonyOS (≤3.x) is supported. " +
                "HarmonyOS NEXT devices without an Android layer are NOT compatible. " +
                "Google services / GMS artifacts will not be present on this device."

            else -> null  // Google, Motorola, Nothing — stock-like, no special guidance
        }
    }

    // File output is handled by StorageWriter, which inserts via MediaStore on Android 10+.
    // A plain File write to public Downloads is blocked by scoped storage there, so the engine
    // would find nothing at /sdcard/Download/ to pull.
}
