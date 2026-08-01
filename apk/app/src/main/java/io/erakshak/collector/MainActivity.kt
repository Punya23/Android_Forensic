package io.erakshak.collector

import android.Manifest
import android.app.Activity
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothManager
import android.content.Context
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.net.wifi.WifiManager
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.provider.CallLog
import android.provider.ContactsContract
import android.provider.Telephony
import android.view.Gravity
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * eRakshak Collector — Tier-1 forensic helper.
 *
 * Supported actions (trigger via ADB):
 *   dump_contacts    → contacts.json
 *   dump_calllog     → calllog.json
 *   dump_sms         → sms.json
 *   dump_wifi        → wifi.json   (saved networks + current association)
 *   dump_bluetooth   → bluetooth.json (bonded + recently seen devices)
 *   dump_all         → all of the above
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
            add(Manifest.permission.ACCESS_FINE_LOCATION)   // needed for WiFi SSID on Android 8.1+
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
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
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
        if (requestCode == REQ_CODE) {
            val denied = permissions.zip(grantResults.toTypedArray())
                .filter { it.second != PackageManager.PERMISSION_GRANTED }
                .map { it.first.substringAfterLast(".") }
            if (denied.isEmpty()) executeAction(pendingAction)
            else showResultScreen(pendingAction ?: "setup",
                "DENIED: ${denied.joinToString(", ")}\n\nPlease grant permissions and try again.")
        }
    }

    // ── Action dispatcher ─────────────────────────────────────────────────

    private fun executeAction(action: String?) {
        if (action == null) { showStatusScreen(); return }

        val results = mutableListOf<String>()
        var hasError = false

        fun run(name: String, block: () -> Pair<String, JSONArray>) {
            try {
                val (file, data) = block()
                writeJson(file, data)
                results += "✓ $file (${data.length()} records)"
            } catch (e: SecurityException) {
                writeErrorJson("$name.error.json", "SecurityException", e.message, action)
                results += "✗ $name: permission denied"
                hasError = true
            } catch (e: Exception) {
                writeErrorJson("$name.error.json", e.javaClass.simpleName, e.message, action)
                results += "✗ $name: ${e.message}"
                hasError = true
            }
        }

        when (action) {
            "dump_contacts"  -> run("contacts")  { "contacts.json"  to dumpContacts() }
            "dump_calllog"   -> run("calllog")   { "calllog.json"   to dumpCallLog() }
            "dump_sms"       -> run("sms")       { "sms.json"       to dumpSms() }
            "dump_wifi"      -> run("wifi")      { "wifi.json"      to dumpWifi() }
            "dump_bluetooth" -> run("bluetooth") { "bluetooth.json" to dumpBluetooth() }
            "dump_all" -> {
                run("contacts")  { "contacts.json"  to dumpContacts() }
                run("calllog")   { "calllog.json"   to dumpCallLog() }
                run("sms")       { "sms.json"       to dumpSms() }
                run("wifi")      { "wifi.json"      to dumpWifi() }
                run("bluetooth") { "bluetooth.json" to dumpBluetooth() }
            }
            else -> {
                writeErrorJson("unknown_action.error.json", "UnknownAction", "unknown action: $action", action)
                results += "✗ Unknown action: $action"
                hasError = true
            }
        }

        showResultScreen(action, results.joinToString("\n"), hasError)
        Handler(Looper.getMainLooper()).postDelayed({ finish() }, 2500)
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
            val numIdx  = cur.getColumnIndex(ContactsContract.CommonDataKinds.Phone.NUMBER)
            while (cur.moveToNext()) {
                out.put(JSONObject()
                    .put("name",   cur.getString(nameIdx) ?: "")
                    .put("number", cur.getString(numIdx)  ?: ""))
            }
        }
        return out
    }

    private fun dumpCallLog(): JSONArray {
        val out = JSONArray()
        contentResolver.query(
            CallLog.Calls.CONTENT_URI,
            arrayOf(CallLog.Calls.NUMBER, CallLog.Calls.CACHED_NAME,
                    CallLog.Calls.TYPE,   CallLog.Calls.DATE, CallLog.Calls.DURATION),
            null, null, "${CallLog.Calls.DATE} DESC"
        )?.use { cur ->
            while (cur.moveToNext()) {
                out.put(JSONObject()
                    .put("number",   cur.getString(0) ?: "")
                    .put("name",     cur.getString(1) ?: "")
                    .put("type",     cur.getInt(2))
                    .put("date",     cur.getLong(3))
                    .put("duration", cur.getInt(4)))
            }
        }
        return out
    }

    private fun dumpSms(): JSONArray {
        val out = JSONArray()
        contentResolver.query(
            Telephony.Sms.CONTENT_URI,
            arrayOf(Telephony.Sms.ADDRESS, Telephony.Sms.BODY,
                    Telephony.Sms.DATE,    Telephony.Sms.TYPE),
            null, null, "${Telephony.Sms.DATE} DESC"
        )?.use { cur ->
            while (cur.moveToNext()) {
                out.put(JSONObject()
                    .put("address", cur.getString(0) ?: "")
                    .put("body",    cur.getString(1) ?: "")
                    .put("date",    cur.getLong(2))
                    .put("type",    cur.getInt(3)))
            }
        }
        return out
    }

    // ── WiFi dump ─────────────────────────────────────────────────────────

    private fun dumpWifi(): JSONArray {
        val out = JSONArray()
        val wm = applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager

        // 1. Currently connected network
        @Suppress("DEPRECATION")
        val info = wm.connectionInfo
        if (info != null && info.networkId != -1) {
            val ssid = info.ssid.trim('"')
            out.put(JSONObject()
                .put("type",     "current_connection")
                .put("ssid",     ssid)
                .put("bssid",    info.bssid ?: "")
                .put("rssi",     info.rssi)
                .put("link_speed_mbps", info.linkSpeed)
                .put("ip_address", intToIp(info.ipAddress))
                .put("frequency_mhz", info.frequency)
                .put("hidden",   ssid.isEmpty()))
        }

        // 2. Saved / configured networks
        @Suppress("DEPRECATION")
        val configured = wm.configuredNetworks
        if (configured != null) {
            for (cfg in configured) {
                val ssid = (cfg.SSID ?: "").trim('"')
                out.put(JSONObject()
                    .put("type",     "saved_network")
                    .put("ssid",     ssid)
                    .put("bssid",    cfg.BSSID ?: "")
                    .put("network_id", cfg.networkId)
                    .put("priority", cfg.priority)
                    .put("hidden",   cfg.hiddenSSID)
                    .put("status",   when(cfg.status) {
                        android.net.wifi.WifiConfiguration.Status.CURRENT  -> "current"
                        android.net.wifi.WifiConfiguration.Status.ENABLED  -> "enabled"
                        android.net.wifi.WifiConfiguration.Status.DISABLED -> "disabled"
                        else -> "unknown"
                    }))
            }
        }

        // 3. Recent scan results (APs visible right now)
        @Suppress("DEPRECATION")
        val scanResults = wm.scanResults
        if (scanResults != null) {
            for (sr in scanResults) {
                out.put(JSONObject()
                    .put("type",          "scan_result")
                    .put("ssid",          sr.SSID ?: "")
                    .put("bssid",         sr.BSSID ?: "")
                    .put("capabilities",  sr.capabilities ?: "")
                    .put("frequency_mhz", sr.frequency)
                    .put("level_dbm",     sr.level)
                    .put("timestamp_us",  sr.timestamp))
            }
        }

        return out
    }

    private fun intToIp(ip: Int): String {
        return "${ip and 0xff}.${ip shr 8 and 0xff}.${ip shr 16 and 0xff}.${ip shr 24 and 0xff}"
    }

    // ── Bluetooth dump ────────────────────────────────────────────────────

    private fun dumpBluetooth(): JSONArray {
        val out = JSONArray()
        val btManager = applicationContext.getSystemService(Context.BLUETOOTH_SERVICE) as? BluetoothManager
        val adapter   = btManager?.adapter ?: BluetoothAdapter.getDefaultAdapter()

        if (adapter == null) {
            out.put(JSONObject().put("error", "Bluetooth not available on this device"))
            return out
        }

        // Adapter info
        out.put(JSONObject()
            .put("type",    "adapter")
            .put("enabled", adapter.isEnabled)
            .put("name",    safeBluetoothName(adapter))
            .put("address", safeBluetoothAddress(adapter))
            .put("state",   when(adapter.state) {
                BluetoothAdapter.STATE_ON          -> "on"
                BluetoothAdapter.STATE_OFF         -> "off"
                BluetoothAdapter.STATE_TURNING_ON  -> "turning_on"
                BluetoothAdapter.STATE_TURNING_OFF -> "turning_off"
                else -> "unknown"
            }))

        // Bonded (paired) devices — persisted across sessions
        val bondedDevices: Set<BluetoothDevice>? = try {
            adapter.bondedDevices
        } catch (e: SecurityException) { null }

        bondedDevices?.forEach { dev ->
            val obj = JSONObject()
                .put("type",       "bonded_device")
                .put("bond_state", "bonded")
            try {
                obj.put("name",    dev.name ?: "")
                obj.put("address", dev.address ?: "")
                obj.put("device_class", dev.bluetoothClass?.deviceClass ?: -1)
                obj.put("device_type",  when(dev.type) {
                    BluetoothDevice.DEVICE_TYPE_CLASSIC -> "classic"
                    BluetoothDevice.DEVICE_TYPE_LE      -> "ble"
                    BluetoothDevice.DEVICE_TYPE_DUAL    -> "dual"
                    else -> "unknown"
                })
                obj.put("uuids", JSONArray().also { arr ->
                    dev.uuids?.forEach { uuid -> arr.put(uuid.toString()) }
                })
            } catch (e: SecurityException) {
                obj.put("name", "[permission_denied]")
                obj.put("address", "[permission_denied]")
            }
            out.put(obj)
        }

        return out
    }

    private fun safeBluetoothName(adapter: BluetoothAdapter): String = try {
        adapter.name ?: ""
    } catch (e: SecurityException) { "[permission_denied]" }

    private fun safeBluetoothAddress(adapter: BluetoothAdapter): String = try {
        @Suppress("DEPRECATION") adapter.address ?: ""
    } catch (e: SecurityException) { "[permission_denied]" }

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
            text = "Actions:\n" +
                "  dump_contacts\n  dump_calllog\n  dump_sms\n" +
                "  dump_wifi\n  dump_bluetooth\n  dump_all"
            textSize = 12f; setTextColor(Color.DKGRAY)
            setBackgroundColor(Color.parseColor("#F5F5F5"))
            setPadding(32, 28, 32, 28); typeface = Typeface.MONOSPACE
        }
        layout.addView(info)
        setContentView(layout)
    }

    private fun showResultScreen(action: String, status: String, hasError: Boolean = false) {
        val layout = buildLayout()
        val color = if (hasError) Color.parseColor("#C62828") else Color.parseColor("#2E7D32")
        layout.addView(makeText(if (hasError) "⚠ Partial" else "✓ Done", 28f, color, bold = true))
        layout.addView(makeText("Action: $action", 13f, Color.GRAY))
        layout.addView(makeText("\n$status", 13f, Color.DKGRAY))
        setContentView(layout)
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

    // ── File write ────────────────────────────────────────────────────────

    private fun getOutputDir(): File {
        val dir = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            val pub = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS)
            if (pub.canWrite()) pub else getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS) ?: filesDir
        } else {
            Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS)
        }
        dir.mkdirs(); return dir
    }

    private fun writeJson(fileName: String, data: JSONArray) {
        File(getOutputDir(), fileName).writeText(data.toString(2), Charsets.UTF_8)
    }

    private fun writeErrorJson(fileName: String, type: String, msg: String?, action: String) {
        val arr = JSONArray().put(JSONObject()
            .put("error", type).put("message", msg ?: "").put("action", action))
        runCatching { File(getOutputDir(), fileName).writeText(arr.toString(2), Charsets.UTF_8) }
    }
}
