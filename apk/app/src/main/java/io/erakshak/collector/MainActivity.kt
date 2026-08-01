package io.erakshak.collector

import android.app.Activity
import android.content.ContentResolver
import android.content.pm.PackageManager
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.provider.CallLog
import android.provider.ContactsContract
import android.provider.Telephony
import android.view.Gravity
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * eRakshak Collector — Tier-1 forensic helper.
 *
 * Triggered by ADB:
 *   adb shell am start -n io.erakshak.collector/.MainActivity --es action dump_contacts
 *   adb shell am start -n io.erakshak.collector/.MainActivity --es action dump_calllog
 *   adb shell am start -n io.erakshak.collector/.MainActivity --es action dump_sms
 *
 * On real devices (OxygenOS/MIUI etc.) `pm grant` is blocked, so the app
 * requests permissions via the standard Android dialog on first launch.
 * Grant them once — subsequent ADB triggers work silently.
 */
class MainActivity : Activity() {

    private var pendingAction: String? = null

    companion object {
        private const val REQ_CODE = 1001

        // All permissions the app may need — request all upfront on first launch
        private val ALL_PERMISSIONS = buildList {
            add(android.Manifest.permission.READ_CONTACTS)
            add(android.Manifest.permission.READ_CALL_LOG)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                add(android.Manifest.permission.READ_MEDIA_IMAGES)
                add(android.Manifest.permission.READ_MEDIA_VIDEO)
                add(android.Manifest.permission.READ_MEDIA_AUDIO)
            } else {
                add(android.Manifest.permission.READ_EXTERNAL_STORAGE)
            }
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
                add(android.Manifest.permission.WRITE_EXTERNAL_STORAGE)
            }
        }.toTypedArray()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        pendingAction = intent.getStringExtra("action")

        val missing = ALL_PERMISSIONS.filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }

        if (missing.isNotEmpty()) {
            // Show permission request screen
            showPermissionScreen(missing)
            ActivityCompat.requestPermissions(this, missing.toTypedArray(), REQ_CODE)
        } else {
            // All permissions already granted — execute action directly
            executeAction(pendingAction)
        }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQ_CODE) {
            val denied = permissions.zip(grantResults.toTypedArray())
                .filter { it.second != PackageManager.PERMISSION_GRANTED }
                .map { it.first.substringAfterLast(".") }

            if (denied.isEmpty()) {
                executeAction(pendingAction)
            } else {
                showResultScreen(
                    pendingAction ?: "setup",
                    "DENIED: ${denied.joinToString(", ")}\n\nGrant permissions and try again."
                )
                // Don't finish — let user see the error
            }
        }
    }

    // ── Core action executor ───────────────────────────────────────────────

    private fun executeAction(action: String?) {
        if (action == null) {
            // Manual launch with all permissions — show status screen
            showStatusScreen(allGranted = true)
            return
        }

        var statusMsg: String
        try {
            statusMsg = when (action) {
                "dump_contacts" -> {
                    val data = dumpContacts()
                    writeJson("contacts.json", data)
                    "contacts.json written (${data.length()} records)"
                }
                "dump_calllog" -> {
                    val data = dumpCallLog()
                    writeJson("calllog.json", data)
                    "calllog.json written (${data.length()} records)"
                }
                "dump_sms" -> {
                    val data = dumpSms()
                    writeJson("sms.json", data)
                    "sms.json written (${data.length()} records)"
                }
                else -> {
                    writeJson("error.json",
                        JSONArray().put(JSONObject().put("error", "unknown action: $action")))
                    "ERROR: unknown action '$action'"
                }
            }
        } catch (e: SecurityException) {
            val errArr = JSONArray().put(
                JSONObject().put("error", "SecurityException")
                    .put("message", e.message ?: "permission denied")
                    .put("action", action)
            )
            runCatching { writeJson("$action.error.json", errArr) }
            statusMsg = "PERMISSION DENIED: ${e.message}"
        } catch (e: Exception) {
            val errArr = JSONArray().put(
                JSONObject().put("error", e.javaClass.simpleName)
                    .put("message", e.message ?: "unknown error")
                    .put("action", action)
            )
            runCatching { writeJson("$action.error.json", errArr) }
            statusMsg = "ERROR: ${e.message}"
        }

        showResultScreen(action, statusMsg)
        Handler(Looper.getMainLooper()).postDelayed({ finish() }, 1800)
    }

    // ── UI screens ────────────────────────────────────────────────────────

    private fun showPermissionScreen(missing: List<String>) {
        val layout = buildLayout()

        layout.addView(makeText("🔐 Permissions Required", 22f, Color.parseColor("#1A237E"), bold = true))
        layout.addView(makeText("eRakshak needs the following permissions\nto collect forensic data:", 14f, Color.DKGRAY))

        val permsText = missing.joinToString("\n") { "  • " + it.substringAfterLast(".") }
        val box = TextView(this).apply {
            text = permsText
            textSize = 13f
            setTextColor(Color.DKGRAY)
            setBackgroundColor(Color.parseColor("#F5F5F5"))
            setPadding(32, 24, 32, 24)
            setTypeface(android.graphics.Typeface.MONOSPACE)
        }
        layout.addView(box)
        layout.addView(makeText("\nA dialog will appear — tap Allow for each.", 13f, Color.parseColor("#555555")))

        setContentView(layout)
    }

    private fun showStatusScreen(allGranted: Boolean) {
        val layout = buildLayout()
        layout.addView(makeText("eRakshak Collector", 22f, Color.parseColor("#1A237E"), bold = true))
        layout.addView(makeText("Forensic Tier-1 Helper", 14f, Color.GRAY))

        if (allGranted) {
            layout.addView(makeText("\n✓ All permissions granted", 14f, Color.parseColor("#2E7D32")))
        }

        val info = TextView(this).apply {
            text = "Trigger via ADB:\n\n" +
                    "adb shell am start \\\n" +
                    "  -n io.erakshak.collector/.MainActivity \\\n" +
                    "  --es action dump_contacts\n\n" +
                    "Actions: dump_contacts | dump_calllog | dump_sms"
            textSize = 12f
            setTextColor(Color.DKGRAY)
            setBackgroundColor(Color.parseColor("#F5F5F5"))
            setPadding(32, 28, 32, 28)
            setTypeface(android.graphics.Typeface.MONOSPACE)
        }
        layout.addView(info)
        layout.addView(makeText("\nv0.1.0 · Android ${Build.VERSION.RELEASE} · ${Build.MODEL}", 11f, Color.LTGRAY))
        setContentView(layout)
    }

    private fun showResultScreen(action: String, status: String) {
        val layout = buildLayout()
        val ok = !status.startsWith("ERROR") && !status.startsWith("PERMISSION") && !status.startsWith("DENIED")
        val color = if (ok) Color.parseColor("#2E7D32") else Color.parseColor("#C62828")

        layout.addView(makeText(if (ok) "✓ Done" else "✗ Failed", 28f, color, bold = true))
        layout.addView(makeText("Action: $action", 13f, Color.GRAY))
        layout.addView(makeText("\n$status", 13f, Color.DKGRAY))
        setContentView(layout)
    }

    private fun buildLayout() = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        gravity = Gravity.CENTER
        setPadding(64, 64, 64, 64)
        setBackgroundColor(Color.WHITE)
    }

    private fun makeText(text: String, size: Float, color: Int, bold: Boolean = false) =
        TextView(this).apply {
            this.text = text
            textSize = size
            setTextColor(color)
            gravity = Gravity.CENTER
            setPadding(0, 8, 0, 8)
            if (bold) setTypeface(typeface, android.graphics.Typeface.BOLD)
        }

    // ── Content-provider dumps ────────────────────────────────────────────

    private fun dumpContacts(): JSONArray {
        val out = JSONArray()
        contentResolver.query(
            ContactsContract.CommonDataKinds.Phone.CONTENT_URI,
            arrayOf(
                ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME,
                ContactsContract.CommonDataKinds.Phone.NUMBER
            ), null, null, null
        )?.use { cur ->
            val nameIdx = cur.getColumnIndex(ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME)
            val numIdx = cur.getColumnIndex(ContactsContract.CommonDataKinds.Phone.NUMBER)
            while (cur.moveToNext()) {
                out.put(JSONObject()
                    .put("name", cur.getString(nameIdx) ?: "")
                    .put("number", cur.getString(numIdx) ?: ""))
            }
        }
        return out
    }

    private fun dumpCallLog(): JSONArray {
        val out = JSONArray()
        contentResolver.query(
            CallLog.Calls.CONTENT_URI,
            arrayOf(
                CallLog.Calls.NUMBER, CallLog.Calls.CACHED_NAME,
                CallLog.Calls.TYPE, CallLog.Calls.DATE, CallLog.Calls.DURATION
            ), null, null, "${CallLog.Calls.DATE} DESC"
        )?.use { cur ->
            while (cur.moveToNext()) {
                out.put(JSONObject()
                    .put("number", cur.getString(0) ?: "")
                    .put("name", cur.getString(1) ?: "")
                    .put("type", cur.getInt(2))
                    .put("date", cur.getLong(3))
                    .put("duration", cur.getInt(4)))
            }
        }
        return out
    }

    private fun dumpSms(): JSONArray {
        val out = JSONArray()
        contentResolver.query(
            Telephony.Sms.CONTENT_URI,
            arrayOf(Telephony.Sms.ADDRESS, Telephony.Sms.BODY, Telephony.Sms.DATE, Telephony.Sms.TYPE),
            null, null, "${Telephony.Sms.DATE} DESC"
        )?.use { cur ->
            while (cur.moveToNext()) {
                out.put(JSONObject()
                    .put("address", cur.getString(0) ?: "")
                    .put("body", cur.getString(1) ?: "")
                    .put("date", cur.getLong(2))
                    .put("type", cur.getInt(3)))
            }
        }
        return out
    }

    // ── File write ────────────────────────────────────────────────────────

    private fun writeJson(fileName: String, data: JSONArray) {
        val dir: File = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            val pub = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS)
            if (pub.canWrite()) pub else getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS) ?: filesDir
        } else {
            Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS)
        }
        dir.mkdirs()
        File(dir, fileName).writeText(data.toString(2), Charsets.UTF_8)
    }
}
