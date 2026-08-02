package io.erakshak.collector

import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import org.json.JSONArray
import org.json.JSONObject

/**
 * eRakshak Collector — Tier-1 forensic helper.
 *
 * Supported actions (trigger via ADB):
 *   dump_contacts    → contacts.json
 *   dump_calllog     → calllog.json
 *   dump_sms         → sms.json
 *   dump_media       → media_inventory.json (MediaStore catalogue + EXIF/MP4 GPS)
 *   dump_apps        → apps.json
 *   dump_accounts    → accounts.json
 *   dump_calendar    → calendar.json
 *   dump_usage       → usage.json
 *   dump_device      → device_extra.json
 *   dump_wifi        → wifi.json      (saved networks + current association + scan)
 *   dump_bluetooth   → bluetooth.json (adapter + bonded devices)
 *   dump_location    → location.json  (last-known fix per provider)
 *   dump_all         → all of the above + collector_manifest.json
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

    companion object {
        private const val REQ_CODE = 1001

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
        pendingAction = intent.getStringExtra("action")

        val missing = ALL_PERMISSIONS.filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }

        if (missing.isNotEmpty()) {
            showPermissionScreen(missing)
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
     * the slowest stage by far — sits in the middle, and the location/radio artifacts follow.
     */
    private val registry: LinkedHashMap<String, (Context) -> CollectionResult> = linkedMapOf(
        "contacts" to { c: Context -> ContactsCollector.collect(c) },
        "calllog" to { c: Context -> CallLogCollector.collect(c) },
        "sms" to { c: Context -> SmsCollector.collect(c) },
        "calendar" to { c: Context -> CalendarCollector.collect(c) },
        "accounts" to { c: Context -> AccountsCollector.collect(c) },
        "apps" to { c: Context -> AppsCollector.collect(c) },
        "usage" to { c: Context -> UsageCollector.collect(c) },
        "media" to { c: Context -> MediaCollector.collect(c) },
        "location" to { c: Context -> LocationCollector.collect(c) },
        "wifi" to { c: Context -> WifiCollector.collect(c) },
        "bluetooth" to { c: Context -> BluetoothCollector.collect(c) },
        "device" to { c: Context -> DeviceCollector.collect(c) },
    )

    // ── Action dispatcher ─────────────────────────────────────────────────

    private fun executeAction(action: String?) {
        if (action == null) { showStatusScreen(); return }

        val wanted: List<String> = when {
            action == "dump_all" -> registry.keys.toList()
            action.startsWith("dump_") -> listOf(action.removePrefix("dump_"))
            else -> emptyList()
        }
        if (wanted.isEmpty() || wanted.any { it !in registry }) {
            showResultScreen(
                action,
                "✗ Unknown action: $action\n\nKnown: dump_all, " +
                    registry.keys.joinToString(", ") { "dump_$it" },
                hasError = true,
            )
            return
        }

        showProgressScreen(wanted.size)
        // MediaStore enumeration plus per-file EXIF/MP4 GPS reads can run for tens of seconds.
        // Running that on the main thread risks an ANR kill mid-collection, which would leave a
        // half-written evidence set, so collection happens on a worker and only the result
        // screen is posted back.
        Thread {
            val results = wanted.map { name -> runCollector(name) }
            val manifest = writeManifest(action, results)
            Handler(Looper.getMainLooper()).post {
                val hasError = results.any {
                    it.status == CollectionResult.ERROR || it.status == CollectionResult.DENIED
                }
                showResultScreen(action, renderSummary(results, manifest), hasError)
                Handler(Looper.getMainLooper()).postDelayed({ finish() }, 3000)
            }
        }.apply { name = "erakshak-collect"; isDaemon = false }.start()
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

    private fun renderSummary(results: List<CollectionResult>, manifest: String?): String {
        val body = results.joinToString("\n") { r ->
            val mark = when (r.status) {
                CollectionResult.OK -> "✓"
                CollectionResult.EMPTY -> "○"
                CollectionResult.DENIED -> "✗"
                CollectionResult.UNSUPPORTED -> "–"
                else -> "!"
            }
            val detail = when (r.status) {
                CollectionResult.OK -> "${r.count} records"
                CollectionResult.EMPTY -> "empty"
                else -> "${r.status}: ${r.error ?: "no detail"}"
            }
            "$mark ${r.fileName} — $detail"
        }
        return if (manifest == null) body else "$body\n\n→ $manifest"
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

    private fun showPermissionScreen(missing: List<String>) {
        val layout = buildLayout()
        layout.addView(makeText("🔐 Permissions Required", 22f, Color.parseColor("#1A237E"), bold = true))

        // Show OEM-specific guidance first if applicable
        val guidance = oemGuidanceText()
        if (guidance != null) {
            val guidanceBox = TextView(this).apply {
                text = guidance
                textSize = 12f
                setTextColor(Color.parseColor("#5D4037"))
                setBackgroundColor(Color.parseColor("#FFF8E1"))
                setPadding(32, 20, 32, 20)
                setTypeface(null, Typeface.ITALIC)
            }
            layout.addView(guidanceBox)
        }

        layout.addView(makeText("Grant each permission when the dialog appears:", 14f, Color.DKGRAY))
        val box = TextView(this).apply {
            text = missing.joinToString("\n") { "  • " + it.substringAfterLast(".") }
            textSize = 13f; setTextColor(Color.DKGRAY)
            setBackgroundColor(Color.parseColor("#F5F5F5"))
            setPadding(32, 24, 32, 24); typeface = Typeface.MONOSPACE
        }
        layout.addView(box)
        setContentView(ScrollView(this).apply { setBackgroundColor(Color.WHITE); addView(layout) })
    }

    private fun showStatusScreen() {
        val layout = buildLayout()
        layout.addView(makeText("eRakshak Collector", 22f, Color.parseColor("#1A237E"), bold = true))
        layout.addView(makeText("✓ All permissions granted — ready", 14f, Color.parseColor("#2E7D32")))
        val info = TextView(this).apply {
            text = "Actions:\n  dump_all\n" +
                registry.keys.joinToString("\n") { "  dump_$it" }
            textSize = 12f; setTextColor(Color.DKGRAY)
            setBackgroundColor(Color.parseColor("#F5F5F5"))
            setPadding(32, 28, 32, 28); typeface = Typeface.MONOSPACE
        }
        layout.addView(info)
        setContentView(ScrollView(this).apply { setBackgroundColor(Color.WHITE); addView(layout) })
    }

    private fun showProgressScreen(count: Int) {
        val layout = buildLayout()
        layout.addView(makeText("⏳ Collecting", 24f, Color.parseColor("#1A237E"), bold = true))
        layout.addView(makeText("$count collector(s) running…", 14f, Color.DKGRAY))
        layout.addView(
            makeText("Keep this screen in the foreground.", 12f, Color.GRAY)
        )
        setContentView(layout)
    }

    private fun showResultScreen(action: String, status: String, hasError: Boolean = false) {
        val layout = buildLayout()
        val color = if (hasError) Color.parseColor("#C62828") else Color.parseColor("#2E7D32")
        layout.addView(makeText(if (hasError) "⚠ Partial" else "✓ Done", 28f, color, bold = true))
        layout.addView(makeText("Action: $action", 13f, Color.GRAY))
        layout.addView(TextView(this).apply {
            text = status
            textSize = 12f; setTextColor(Color.DKGRAY)
            setBackgroundColor(Color.parseColor("#F5F5F5"))
            setPadding(24, 24, 24, 24); typeface = Typeface.MONOSPACE
        })
        setContentView(ScrollView(this).apply { setBackgroundColor(Color.WHITE); addView(layout) })
    }

    private fun buildLayout() = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL; gravity = Gravity.CENTER
        setPadding(64, 64, 64, 64); setBackgroundColor(Color.WHITE)
    }

    private fun makeText(text: String, size: Float, color: Int, bold: Boolean = false) =
        TextView(this).apply {
            this.text = text; textSize = size; setTextColor(color); gravity = Gravity.CENTER
            setPadding(0, 8, 0, 8)
            if (bold) setTypeface(typeface, Typeface.BOLD)
        }

    // File output is handled by StorageWriter, which inserts via MediaStore on Android 10+.
    // A plain File write to public Downloads is blocked by scoped storage there, so the engine
    // would find nothing at /sdcard/Download/ to pull.
}
