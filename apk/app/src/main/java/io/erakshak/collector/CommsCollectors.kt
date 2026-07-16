package io.erakshak.collector

import android.content.Context
import android.content.pm.PackageManager
import android.database.Cursor
import android.provider.CallLog
import android.provider.ContactsContract
import android.provider.Telephony
import org.json.JSONArray
import org.json.JSONObject

/** Small helpers shared by the content-provider collectors. */
internal object Cur {
    fun Cursor.strOrNull(name: String): String? {
        val i = getColumnIndex(name); if (i < 0) return null
        return if (isNull(i)) null else getString(i)
    }

    fun Cursor.longOrNull(name: String): Long? {
        val i = getColumnIndex(name); if (i < 0) return null
        return if (isNull(i)) null else getLong(i)
    }

    fun Cursor.intOrNull(name: String): Int? {
        val i = getColumnIndex(name); if (i < 0) return null
        return if (isNull(i)) null else getInt(i)
    }
}

internal fun Context.granted(perm: String): Boolean =
    checkSelfPermission(perm) == PackageManager.PERMISSION_GRANTED

/**
 * Contacts — merges phone numbers and emails per contact so a single person appears once with
 * all their numbers/emails, while keeping the flat `name`/`number`/`email` fields the engine's
 * `parse_contacts_json` expects.
 */
object ContactsCollector {
    fun collect(ctx: Context): CollectionResult {
        if (!ctx.granted(android.Manifest.permission.READ_CONTACTS))
            return CollectionResult("contacts", "contacts.json", JSONArray(), 0,
                CollectionResult.DENIED, "READ_CONTACTS not granted")
        return try {
            // contactId → aggregated record
            val byId = LinkedHashMap<String, JSONObject>()
            val numbersById = HashMap<String, MutableList<String>>()
            val emailsById = HashMap<String, MutableList<String>>()

            ctx.contentResolver.query(
                ContactsContract.CommonDataKinds.Phone.CONTENT_URI,
                arrayOf(
                    ContactsContract.CommonDataKinds.Phone.CONTACT_ID,
                    ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME,
                    ContactsContract.CommonDataKinds.Phone.NUMBER,
                    ContactsContract.CommonDataKinds.Phone.TYPE,
                ), null, null, null
            )?.use { c ->
                while (c.moveToNext()) {
                    val id = with(Cur) { c.strOrNull(ContactsContract.CommonDataKinds.Phone.CONTACT_ID) } ?: continue
                    val name = with(Cur) { c.strOrNull(ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME) } ?: ""
                    val num = with(Cur) { c.strOrNull(ContactsContract.CommonDataKinds.Phone.NUMBER) } ?: ""
                    val rec = byId.getOrPut(id) {
                        JSONObject().put("contact_id", id).put("name", name)
                    }
                    if (rec.optString("name").isEmpty() && name.isNotEmpty()) rec.put("name", name)
                    if (num.isNotEmpty()) numbersById.getOrPut(id) { mutableListOf() }.add(num)
                }
            }

            if (ctx.granted(android.Manifest.permission.READ_CONTACTS)) {
                ctx.contentResolver.query(
                    ContactsContract.CommonDataKinds.Email.CONTENT_URI,
                    arrayOf(
                        ContactsContract.CommonDataKinds.Email.CONTACT_ID,
                        ContactsContract.CommonDataKinds.Email.ADDRESS,
                    ), null, null, null
                )?.use { c ->
                    while (c.moveToNext()) {
                        val id = with(Cur) { c.strOrNull(ContactsContract.CommonDataKinds.Email.CONTACT_ID) } ?: continue
                        val addr = with(Cur) { c.strOrNull(ContactsContract.CommonDataKinds.Email.ADDRESS) } ?: continue
                        if (addr.isNotEmpty()) emailsById.getOrPut(id) { mutableListOf() }.add(addr)
                    }
                }
            }

            val out = JSONArray()
            for ((id, rec) in byId) {
                val nums = numbersById[id]?.distinct() ?: emptyList()
                val mails = emailsById[id]?.distinct() ?: emptyList()
                rec.put("number", nums.firstOrNull() ?: "")
                rec.put("email", mails.firstOrNull() ?: "")
                rec.put("numbers", JSONArray(nums))
                rec.put("emails", JSONArray(mails))
                out.put(rec)
            }
            CollectionResult("contacts", "contacts.json", out, out.length(),
                if (out.length() == 0) CollectionResult.EMPTY else CollectionResult.OK)
        } catch (e: SecurityException) {
            CollectionResult("contacts", "contacts.json", JSONArray(), 0, CollectionResult.DENIED, e.message)
        } catch (e: Exception) {
            CollectionResult("contacts", "contacts.json", JSONArray(), 0, CollectionResult.ERROR, e.message)
        }
    }
}

/** Call log — all useful columns, keeping the engine's number/name/type/date/duration shape. */
object CallLogCollector {
    fun collect(ctx: Context): CollectionResult {
        if (!ctx.granted(android.Manifest.permission.READ_CALL_LOG))
            return CollectionResult("calllog", "calllog.json", JSONArray(), 0,
                CollectionResult.DENIED, "READ_CALL_LOG not granted (needs default-Dialer role swap)")
        return try {
            val out = JSONArray()
            ctx.contentResolver.query(
                CallLog.Calls.CONTENT_URI,
                arrayOf(
                    CallLog.Calls.NUMBER, CallLog.Calls.CACHED_NAME, CallLog.Calls.TYPE,
                    CallLog.Calls.DATE, CallLog.Calls.DURATION,
                    CallLog.Calls.GEOCODED_LOCATION, CallLog.Calls.IS_READ,
                ), null, null, "${CallLog.Calls.DATE} DESC"
            )?.use { c ->
                while (c.moveToNext()) {
                    out.put(JSONObject()
                        .put("number", with(Cur) { c.strOrNull(CallLog.Calls.NUMBER) } ?: "")
                        .put("name", with(Cur) { c.strOrNull(CallLog.Calls.CACHED_NAME) } ?: "")
                        .put("type", with(Cur) { c.intOrNull(CallLog.Calls.TYPE) } ?: 0)
                        .put("date", with(Cur) { c.longOrNull(CallLog.Calls.DATE) } ?: 0L)
                        .put("duration", with(Cur) { c.intOrNull(CallLog.Calls.DURATION) } ?: 0)
                        .put("geocoded_location", with(Cur) { c.strOrNull(CallLog.Calls.GEOCODED_LOCATION) } ?: "")
                        .put("is_read", with(Cur) { c.intOrNull(CallLog.Calls.IS_READ) } ?: 0))
                }
            }
            CollectionResult("calllog", "calllog.json", out, out.length(),
                if (out.length() == 0) CollectionResult.EMPTY else CollectionResult.OK)
        } catch (e: SecurityException) {
            CollectionResult("calllog", "calllog.json", JSONArray(), 0, CollectionResult.DENIED, e.message)
        } catch (e: Exception) {
            CollectionResult("calllog", "calllog.json", JSONArray(), 0, CollectionResult.ERROR, e.message)
        }
    }
}

/** SMS (+ best-effort MMS text) — keeps the engine's address/body/date/type shape. */
object SmsCollector {
    fun collect(ctx: Context): CollectionResult {
        if (!ctx.granted(android.Manifest.permission.READ_SMS))
            return CollectionResult("sms", "sms.json", JSONArray(), 0,
                CollectionResult.DENIED, "READ_SMS not granted (needs default-SMS role swap)")
        return try {
            val out = JSONArray()
            ctx.contentResolver.query(
                Telephony.Sms.CONTENT_URI,
                arrayOf(
                    Telephony.Sms.ADDRESS, Telephony.Sms.BODY, Telephony.Sms.DATE,
                    Telephony.Sms.TYPE, Telephony.Sms.READ, Telephony.Sms.THREAD_ID,
                ), null, null, "${Telephony.Sms.DATE} DESC"
            )?.use { c ->
                while (c.moveToNext()) {
                    out.put(JSONObject()
                        .put("address", with(Cur) { c.strOrNull(Telephony.Sms.ADDRESS) } ?: "")
                        .put("body", with(Cur) { c.strOrNull(Telephony.Sms.BODY) } ?: "")
                        .put("date", with(Cur) { c.longOrNull(Telephony.Sms.DATE) } ?: 0L)
                        .put("type", with(Cur) { c.intOrNull(Telephony.Sms.TYPE) } ?: 0)
                        .put("read", with(Cur) { c.intOrNull(Telephony.Sms.READ) } ?: 0)
                        .put("thread_id", with(Cur) { c.longOrNull(Telephony.Sms.THREAD_ID) } ?: 0L)
                        .put("kind", "sms"))
                }
            }
            // Best-effort MMS text parts (never lets an MMS failure kill the SMS dump).
            runCatching { appendMms(ctx, out) }
            CollectionResult("sms", "sms.json", out, out.length(),
                if (out.length() == 0) CollectionResult.EMPTY else CollectionResult.OK)
        } catch (e: SecurityException) {
            CollectionResult("sms", "sms.json", JSONArray(), 0, CollectionResult.DENIED, e.message)
        } catch (e: Exception) {
            CollectionResult("sms", "sms.json", JSONArray(), 0, CollectionResult.ERROR, e.message)
        }
    }

    private fun appendMms(ctx: Context, out: JSONArray) {
        ctx.contentResolver.query(
            Telephony.Mms.CONTENT_URI,
            arrayOf(Telephony.Mms._ID, Telephony.Mms.DATE, Telephony.Mms.MESSAGE_BOX,
                Telephony.Mms.THREAD_ID),
            null, null, "${Telephony.Mms.DATE} DESC"
        )?.use { c ->
            while (c.moveToNext()) {
                val id = with(Cur) { c.strOrNull(Telephony.Mms._ID) } ?: continue
                val dateSec = with(Cur) { c.longOrNull(Telephony.Mms.DATE) } ?: 0L
                val box = with(Cur) { c.intOrNull(Telephony.Mms.MESSAGE_BOX) } ?: 0
                val thread = with(Cur) { c.longOrNull(Telephony.Mms.THREAD_ID) } ?: 0L
                val text = mmsText(ctx, id)
                out.put(JSONObject()
                    .put("address", "")
                    .put("body", text)
                    .put("date", dateSec * 1000L)   // MMS DATE is seconds; normalise to ms
                    .put("type", box)               // MESSAGE_BOX aligns with Sms.TYPE (1=inbox,2=sent)
                    .put("thread_id", thread)
                    .put("kind", "mms"))
            }
        }
    }

    private fun mmsText(ctx: Context, mmsId: String): String {
        val sb = StringBuilder()
        val partUri = android.net.Uri.parse("content://mms/part")
        ctx.contentResolver.query(partUri, null, "mid=?", arrayOf(mmsId), null)?.use { p ->
            while (p.moveToNext()) {
                val ct = with(Cur) { p.strOrNull("ct") } ?: ""
                if (ct == "text/plain") {
                    val body = with(Cur) { p.strOrNull("text") }
                    if (!body.isNullOrEmpty()) sb.append(body)
                }
            }
        }
        return sb.toString()
    }
}
