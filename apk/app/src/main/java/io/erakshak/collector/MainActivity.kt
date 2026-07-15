package io.erakshak.collector

import android.app.Activity
import android.content.ContentResolver
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.provider.CallLog
import android.provider.ContactsContract
import android.provider.Telephony
import android.view.Gravity
import android.widget.LinearLayout
import android.widget.TextView
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * eRakshak Collector — Tier-1 forensic helper.
 *
 * A deliberately tiny activity that dumps the requested content-provider data to JSON in
 * shared storage, where the desktop engine pulls it via `adb pull`. It performs no network
 * I/O, keeps no persistent state, and reads only what it was explicitly asked to via the
 * `action` extra so its behaviour is auditable:
 *
 *   adb shell am start -n io.erakshak.collector/.MainActivity --es action dump_contacts
 *   adb shell am start -n io.erakshak.collector/.MainActivity --es action dump_calllog
 *   adb shell am start -n io.erakshak.collector/.MainActivity --es action dump_sms
 *
 * Output files (Download/): contacts.json, calllog.json, sms.json — matching the shapes
 * that engine/triage/parsers/{contacts,calllog}.py expect.
 *
 * When opened manually (no action extra), a status screen is shown instead of crashing.
 */
class MainActivity : Activity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val action = intent.getStringExtra("action")

        // ── Manual launch (no ADB action) ────────────────────────────────────
        // Show a simple status screen instead of doing anything (and crashing).
        if (action == null) {
            showStatusScreen()
            return
        }

        // ── ADB-triggered action ─────────────────────────────────────────────
        var statusMsg = "OK"
        try {
            when (action) {
                "dump_contacts" -> {
                    val data = dumpContacts()
                    writeJson("contacts.json", data)
                    statusMsg = "contacts.json written (${data.length()} records)"
                }
                "dump_calllog" -> {
                    val data = dumpCallLog()
                    writeJson("calllog.json", data)
                    statusMsg = "calllog.json written (${data.length()} records)"
                }
                "dump_sms" -> {
                    val data = dumpSms()
                    writeJson("sms.json", data)
                    statusMsg = "sms.json written (${data.length()} records)"
                }
                else -> {
                    writeJson(
                        "error.json",
                        JSONArray().put(JSONObject().put("error", "unknown action: $action"))
                    )
                    statusMsg = "ERROR: unknown action '$action'"
                }
            }
        } catch (e: SecurityException) {
            // Permission not granted — surface as JSON rather than crashing so the
            // engine's audit log records the failure cleanly.
            val errArr = JSONArray().put(
                JSONObject()
                    .put("error", "SecurityException")
                    .put("message", e.message ?: "permission denied")
                    .put("action", action)
            )
            runCatching { writeJson("$action.error.json", errArr) }
            statusMsg = "PERMISSION DENIED: ${e.message}"
        } catch (e: Exception) {
            val errArr = JSONArray().put(
                JSONObject()
                    .put("error", e.javaClass.simpleName)
                    .put("message", e.message ?: "unknown error")
                    .put("action", action)
            )
            runCatching { writeJson("$action.error.json", errArr) }
            statusMsg = "ERROR: ${e.message}"
        }

        // Brief status display before finishing so the action is visible on-screen
        showResultScreen(action, statusMsg)

        // Finish after a short pause so the engine can see the result in logcat
        android.os.Handler(mainLooper).postDelayed({ finish() }, 1500)
    }

    // ── Status screen (manual launch) ────────────────────────────────────────

    private fun showStatusScreen() {
        val layout = buildLayout()

        val title = TextView(this).apply {
            text = "eRakshak Collector"
            textSize = 22f
            setTextColor(Color.parseColor("#1A237E"))
            setPadding(0, 0, 0, 24)
            gravity = Gravity.CENTER
        }

        val subtitle = TextView(this).apply {
            text = "Forensic Tier-1 Helper"
            textSize = 14f
            setTextColor(Color.GRAY)
            gravity = Gravity.CENTER
            setPadding(0, 0, 0, 40)
        }

        val info = TextView(this).apply {
            text = "This app is controlled by the eRakshak engine via ADB.\n\n" +
                    "It should not be opened manually.\n\n" +
                    "Trigger actions via:\n\n" +
                    "  adb shell am start \\\n" +
                    "    -n io.erakshak.collector/.MainActivity \\\n" +
                    "    --es action dump_contacts"
            textSize = 13f
            setTextColor(Color.DKGRAY)
            setBackgroundColor(Color.parseColor("#F5F5F5"))
            setPadding(32, 32, 32, 32)
            setTypeface(android.graphics.Typeface.MONOSPACE)
        }

        val version = TextView(this).apply {
            text = "v0.1.0 · minSdk 26 · Android ${Build.VERSION.RELEASE}"
            textSize = 11f
            setTextColor(Color.LTGRAY)
            gravity = Gravity.CENTER
            setPadding(0, 40, 0, 0)
        }

        layout.addView(title)
        layout.addView(subtitle)
        layout.addView(info)
        layout.addView(version)
        setContentView(layout)
    }

    private fun showResultScreen(action: String, status: String) {
        val layout = buildLayout()

        val ok = !status.startsWith("ERROR") && !status.startsWith("PERMISSION")
        val color = if (ok) Color.parseColor("#2E7D32") else Color.parseColor("#C62828")

        val title = TextView(this).apply {
            text = if (ok) "✓ Done" else "✗ Failed"
            textSize = 26f
            setTextColor(color)
            gravity = Gravity.CENTER
            setPadding(0, 0, 0, 16)
        }

        val actionView = TextView(this).apply {
            text = "Action: $action"
            textSize = 13f
            setTextColor(Color.GRAY)
            gravity = Gravity.CENTER
        }

        val msg = TextView(this).apply {
            text = status
            textSize = 13f
            setTextColor(Color.DKGRAY)
            gravity = Gravity.CENTER
            setPadding(0, 24, 0, 0)
        }

        layout.addView(title)
        layout.addView(actionView)
        layout.addView(msg)
        setContentView(layout)
    }

    private fun buildLayout(): LinearLayout = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        gravity = Gravity.CENTER
        setPadding(64, 64, 64, 64)
        setBackgroundColor(Color.WHITE)
    }

    // ── Content-provider dumps ────────────────────────────────────────────────

    private fun dumpContacts(): JSONArray {
        val out = JSONArray()
        val cr: ContentResolver = contentResolver
        val cursor = cr.query(
            ContactsContract.CommonDataKinds.Phone.CONTENT_URI,
            arrayOf(
                ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME,
                ContactsContract.CommonDataKinds.Phone.NUMBER
            ), null, null, null
        )
        cursor?.use {
            val nameIdx = it.getColumnIndex(ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME)
            val numIdx = it.getColumnIndex(ContactsContract.CommonDataKinds.Phone.NUMBER)
            while (it.moveToNext()) {
                out.put(
                    JSONObject()
                        .put("name", it.getString(nameIdx) ?: "")
                        .put("number", it.getString(numIdx) ?: "")
                )
            }
        }
        return out
    }

    private fun dumpCallLog(): JSONArray {
        val out = JSONArray()
        val cursor = contentResolver.query(
            CallLog.Calls.CONTENT_URI,
            arrayOf(
                CallLog.Calls.NUMBER, CallLog.Calls.CACHED_NAME,
                CallLog.Calls.TYPE, CallLog.Calls.DATE, CallLog.Calls.DURATION
            ), null, null, "${CallLog.Calls.DATE} DESC"
        )
        cursor?.use {
            while (it.moveToNext()) {
                out.put(
                    JSONObject()
                        .put("number", it.getString(0) ?: "")
                        .put("name", it.getString(1) ?: "")
                        .put("type", it.getInt(2))
                        .put("date", it.getLong(3))
                        .put("duration", it.getInt(4))
                )
            }
        }
        return out
    }

    private fun dumpSms(): JSONArray {
        val out = JSONArray()
        val cursor = contentResolver.query(
            Telephony.Sms.CONTENT_URI,
            arrayOf(Telephony.Sms.ADDRESS, Telephony.Sms.BODY, Telephony.Sms.DATE, Telephony.Sms.TYPE),
            null, null, "${Telephony.Sms.DATE} DESC"
        )
        cursor?.use {
            while (it.moveToNext()) {
                out.put(
                    JSONObject()
                        .put("address", it.getString(0) ?: "")
                        .put("body", it.getString(1) ?: "")
                        .put("date", it.getLong(2))
                        .put("type", it.getInt(3))
                )
            }
        }
        return out
    }

    // ── File write (handles Android 10+ scoped storage) ──────────────────────

    private fun writeJson(fileName: String, data: JSONArray) {
        val dir: File = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            // Android 10+: apps can always write to their own scoped Downloads location
            // without WRITE_EXTERNAL_STORAGE. Use getExternalFilesDir as a safe fallback
            // if the public Downloads dir isn't accessible.
            val pub = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS)
            if (pub.canWrite()) pub else getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS)
                ?: filesDir
        } else {
            Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS)
        }
        dir.mkdirs()
        File(dir, fileName).writeText(data.toString(2), Charsets.UTF_8)
    }
}
