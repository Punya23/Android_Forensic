package io.erakshak.collector

import android.app.Activity
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
import org.json.JSONArray
import org.json.JSONObject

/**
 * eRakshak Collector — Tier-1 forensic helper.
 *
 * A deliberately small, auditable activity that dumps the requested content-provider /
 * platform data to JSON in shared storage, where the desktop engine pulls it via `adb pull`.
 * It performs no network I/O, keeps no persistent state, and reads only what the `action`
 * extra asks for, and only what it was actually granted.
 *
 * ADB-driven actions:
 *
 *   adb shell am start -n io.erakshak.collector/.MainActivity --es action dump_contacts
 *   adb shell am start -n io.erakshak.collector/.MainActivity --es action dump_calllog
 *   adb shell am start -n io.erakshak.collector/.MainActivity --es action dump_sms
 *   adb shell am start -n io.erakshak.collector/.MainActivity --es action dump_media
 *   adb shell am start -n io.erakshak.collector/.MainActivity --es action dump_apps
 *   adb shell am start -n io.erakshak.collector/.MainActivity --es action dump_accounts
 *   adb shell am start -n io.erakshak.collector/.MainActivity --es action dump_calendar
 *   adb shell am start -n io.erakshak.collector/.MainActivity --es action dump_usage
 *   adb shell am start -n io.erakshak.collector/.MainActivity --es action dump_device
 *   adb shell am start -n io.erakshak.collector/.MainActivity --es action dump_all
 *
 * Output (public Download/): contacts.json, calllog.json, sms.json, media_inventory.json,
 * apps.json, accounts.json, calendar.json, usage.json, device_extra.json, and
 * collector_manifest.json (a summary of what ran, how many rows, and any denials).
 *
 * When opened manually (no action), a status screen is shown instead of doing anything.
 */
class MainActivity : Activity() {

    private val ui = Handler(Looper.getMainLooper())

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val action = intent.getStringExtra("action")

        if (action == null) {
            showStatusScreen()
            return
        }

        val collectors = collectorsFor(action)
        if (collectors == null) {
            writeResults(action, listOf(
                CollectionResult("error", "error.json",
                    JSONArray().put(JSONObject().put("error", "unknown action: $action")),
                    0, CollectionResult.ERROR, "unknown action")
            ))
            showResultScreen(action, listOf("✗ unknown action '$action'"))
            ui.postDelayed({ finish() }, 1800)
            return
        }

        showBusyScreen(action)
        // Heavy collectors (media/apps) must run off the main thread to avoid ANR.
        Thread {
            val results = collectors.map { runCatching { it(this) }.getOrElse { e ->
                CollectionResult("unknown", "error.json", JSONArray(), 0,
                    CollectionResult.ERROR, e.message)
            } }
            writeResults(action, results)
            val lines = results.map { r ->
                val icon = if (r.status == CollectionResult.OK) "✓" else
                    if (r.status == CollectionResult.EMPTY) "•" else "✗"
                "$icon ${r.name}: ${r.count} (${r.status})" +
                    (r.error?.let { " — ${it.take(60)}" } ?: "")
            }
            ui.post {
                showResultScreen(action, lines)
                ui.postDelayed({ finish() }, 2200)
            }
        }.start()
    }

    /** Map an action to the collectors it runs, or null if unknown. */
    private fun collectorsFor(action: String): List<(Activity) -> CollectionResult>? = when (action) {
        "dump_contacts" -> listOf(ContactsCollector::collect)
        "dump_calllog" -> listOf(CallLogCollector::collect)
        "dump_sms" -> listOf(SmsCollector::collect)
        "dump_media" -> listOf(MediaCollector::collect)
        "dump_apps" -> listOf(AppsCollector::collect)
        "dump_accounts" -> listOf(AccountsCollector::collect)
        "dump_calendar" -> listOf(CalendarCollector::collect)
        "dump_usage" -> listOf(UsageCollector::collect)
        "dump_device" -> listOf(DeviceCollector::collect)
        "dump_all" -> listOf(
            ContactsCollector::collect, CallLogCollector::collect, SmsCollector::collect,
            MediaCollector::collect, AppsCollector::collect, AccountsCollector::collect,
            CalendarCollector::collect, UsageCollector::collect, DeviceCollector::collect,
        )
        else -> null
    }

    /** Write each result's payload plus a collector_manifest.json summary. */
    private fun writeResults(action: String, results: List<CollectionResult>) {
        for (r in results) {
            runCatching {
                val text = when (val p = r.payload) {
                    is JSONArray -> p.toString(2)
                    is JSONObject -> p.toString(2)
                    else -> p.toString()
                }
                // Don't emit empty error.json placeholders on success.
                if (!(r.fileName == "error.json" && r.count == 0 && r.status == CollectionResult.OK)) {
                    StorageWriter.write(this, r.fileName, text)
                }
            }
        }
        val manifest = JSONObject()
            .put("tool", "eRakshak Collector")
            .put("version", VERSION)
            .put("action", action)
            .put("android_sdk", Build.VERSION.SDK_INT)
            .put("collected_at_ms", System.currentTimeMillis())
            .put("results", JSONArray().apply { results.forEach { put(it.summary()) } })
        runCatching { StorageWriter.write(this, "collector_manifest.json", manifest.toString(2)) }
    }

    // ── UI ────────────────────────────────────────────────────────────────────

    private fun showStatusScreen() {
        val layout = buildLayout()
        layout.addView(title("eRakshak Collector"))
        layout.addView(subtitle("Forensic Tier-1 Helper · v$VERSION"))
        layout.addView(mono(
            "Controlled by the eRakshak engine via ADB.\n" +
                "Not meant to be opened manually.\n\n" +
                "Example:\n" +
                "  adb shell am start \\\n" +
                "    -n io.erakshak.collector/.MainActivity \\\n" +
                "    --es action dump_all"
        ))
        layout.addView(subtitle("minSdk 26 · Android ${Build.VERSION.RELEASE}"))
        setContentView(wrap(layout))
    }

    private fun showBusyScreen(action: String) {
        val layout = buildLayout()
        layout.addView(title("Collecting…"))
        layout.addView(subtitle("action: $action"))
        setContentView(wrap(layout))
    }

    private fun showResultScreen(action: String, lines: List<String>) {
        val ok = lines.none { it.startsWith("✗") }
        val layout = buildLayout()
        val head = TextView(this).apply {
            text = if (ok) "✓ Done" else "Completed with issues"
            textSize = 24f
            setTextColor(if (ok) Color.parseColor("#2E7D32") else Color.parseColor("#C62828"))
            gravity = Gravity.CENTER
            setPadding(0, 0, 0, 12)
        }
        layout.addView(head)
        layout.addView(subtitle("action: $action"))
        for (l in lines) {
            layout.addView(TextView(this).apply {
                text = l
                textSize = 13f
                setTextColor(Color.DKGRAY)
                setPadding(0, 8, 0, 0)
                typeface = Typeface.MONOSPACE
            })
        }
        setContentView(wrap(layout))
    }

    private fun buildLayout(): LinearLayout = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        gravity = Gravity.CENTER_HORIZONTAL
        setPadding(56, 72, 56, 72)
        setBackgroundColor(Color.WHITE)
    }

    private fun wrap(inner: LinearLayout): ScrollView = ScrollView(this).apply {
        setBackgroundColor(Color.WHITE)
        addView(inner)
    }

    private fun title(t: String) = TextView(this).apply {
        text = t; textSize = 22f; setTextColor(Color.parseColor("#1A237E"))
        gravity = Gravity.CENTER; setPadding(0, 0, 0, 8)
    }

    private fun subtitle(t: String) = TextView(this).apply {
        text = t; textSize = 13f; setTextColor(Color.GRAY)
        gravity = Gravity.CENTER; setPadding(0, 0, 0, 16)
    }

    private fun mono(t: String) = TextView(this).apply {
        text = t; textSize = 12f; setTextColor(Color.DKGRAY)
        setBackgroundColor(Color.parseColor("#F5F5F5")); setPadding(28, 28, 28, 28)
        typeface = Typeface.MONOSPACE
    }

    companion object {
        const val VERSION = "0.2.0"
    }
}
