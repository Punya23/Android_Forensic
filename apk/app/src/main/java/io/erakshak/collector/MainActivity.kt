package io.erakshak.collector

import android.app.Activity
import android.content.ContentResolver
import android.os.Bundle
import android.os.Environment
import android.provider.CallLog
import android.provider.ContactsContract
import android.provider.Telephony
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * eRakshak Collector — Tier-1 helper.
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
 */
class MainActivity : Activity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val action = intent.getStringExtra("action") ?: "dump_contacts"
        try {
            when (action) {
                "dump_contacts" -> write("contacts.json", dumpContacts())
                "dump_calllog" -> write("calllog.json", dumpCallLog())
                "dump_sms" -> write("sms.json", dumpSms())
                else -> write("error.json", JSONArray().put(JSONObject().put("error", "unknown action $action")))
            }
        } catch (e: SecurityException) {
            // Permission not granted — surface it as JSON rather than crashing so the
            // engine's audit log records the failure cleanly.
            write("$action.error.json", JSONArray().put(JSONObject().put("error", e.message)))
        }
        finish()
    }

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

    private fun write(fileName: String, data: JSONArray) {
        val dir = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS)
        val file = File(dir, fileName)
        file.writeText(data.toString(2))
    }
}
