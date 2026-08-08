package io.erakshak.collector

import android.app.Notification
import android.content.ComponentName
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.CopyOnWriteArrayList

/**
 * NotificationWatcher — a lightweight NotificationListenerService that buffers
 * every notification posted while the app is active, then lets the
 * NotificationCollector drain the buffer on demand.
 *
 * Activation: Settings → Apps → Special app access → Notification access →
 *             SNAGR Collector → Allow
 */
class NotificationWatcher : NotificationListenerService() {

    override fun onNotificationPosted(sbn: StatusBarNotification?) {
        sbn ?: return
        val extras = sbn.notification?.extras ?: return
        val record = JSONObject()
            .put("package",     sbn.packageName ?: "")
            .put("post_time",   sbn.postTime)
            .put("is_ongoing",  sbn.isOngoing)
            .put("title",       extras.getCharSequence(Notification.EXTRA_TITLE)?.toString() ?: "")
            .put("text",        extras.getCharSequence(Notification.EXTRA_TEXT)?.toString() ?: "")
            .put("big_text",    extras.getCharSequence(Notification.EXTRA_BIG_TEXT)?.toString() ?: "")
            .put("sub_text",    extras.getCharSequence(Notification.EXTRA_SUB_TEXT)?.toString() ?: "")
            .put("channel_id",  if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
                                    sbn.notification?.channelId ?: "" else "")
        buffer.add(record)
    }

    companion object {
        val buffer: MutableList<JSONObject> = CopyOnWriteArrayList()
    }
}

/**
 * Collects notification history from three sources (best-effort, most accurate first):
 *
 *  1. Android 11+ NotificationManager.getNotificationHistory() — requires the user
 *     to have granted notification access in Settings once.
 *  2. Live NotificationWatcher buffer — notifications captured since the service started.
 *  3. adb-readable notification history log (not available without root; skipped silently).
 */
object NotificationCollector {

    fun collect(ctx: Context): CollectionResult {
        val out = JSONArray()
        val pm = ctx.packageManager

        // ── Source 1: Android 11+ notification history (via reflection) ───
        // NotificationManager.getNotificationHistory() is @SystemApi — not in the
        // public SDK stub, so we call it reflectively. The runCatching block makes
        // it a no-op on devices where it is blocked.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            runCatching {
                val nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE)
                    as android.app.NotificationManager
                val getHistory = nm.javaClass.getMethod("getNotificationHistory")
                val history = getHistory.invoke(nm) ?: return@runCatching
                val getNotifs = history.javaClass.getMethod("getHistoricNotifications")
                @Suppress("UNCHECKED_CAST")
                val notifs = getNotifs.invoke(history) as? Array<*> ?: return@runCatching
                for (n in notifs) {
                    n ?: continue
                    val nClass = n.javaClass
                    val pkg     = runCatching { nClass.getMethod("getPackageName").invoke(n) as? String }.getOrNull() ?: ""
                    val whenMs  = runCatching { nClass.getMethod("getWhen").invoke(n) as? Long }.getOrNull() ?: 0L
                    val channel = runCatching { nClass.getMethod("getChannelId").invoke(n) as? String }.getOrNull() ?: ""
                    val extras  = runCatching { nClass.getMethod("getExtras").invoke(n) as? android.os.Bundle }.getOrNull()

                    val appLabel = runCatching {
                        pm.getApplicationLabel(pm.getApplicationInfo(pkg, 0)).toString()
                    }.getOrElse { pkg }

                    out.put(JSONObject()
                        .put("source",      "history_api")
                        .put("package",     pkg)
                        .put("app_label",   appLabel)
                        .put("post_time",   whenMs)
                        .put("title",       extras?.getCharSequence(Notification.EXTRA_TITLE)?.toString() ?: "")
                        .put("text",        extras?.getCharSequence(Notification.EXTRA_TEXT)?.toString() ?: "")
                        .put("big_text",    extras?.getCharSequence(Notification.EXTRA_BIG_TEXT)?.toString() ?: "")
                        .put("channel_id",  channel)
                    )
                }
            }
        }

        // ── Source 2: live watcher buffer ─────────────────────────────────
        for (record in NotificationWatcher.buffer) {
            val pkg = record.optString("package")
            val appLabel = runCatching {
                pm.getApplicationLabel(pm.getApplicationInfo(pkg, 0)).toString()
            }.getOrElse { pkg }
            out.put(record.put("source", "live_watcher").put("app_label", appLabel))
        }

        // ── Check if notification access is enabled ────────────────────────
        val listenerEnabled = isNotificationListenerEnabled(ctx)

        val note = when {
            out.length() == 0 && !listenerEnabled ->
                "Notification access NOT granted. Go to Settings → Apps → Special app access → Notification access → SNAGR Collector → Allow, then re-run."
            out.length() == 0 && listenerEnabled ->
                "Notification access granted but history is empty (no recent notifications)."
            else -> null
        }

        return CollectionResult(
            "notifications", "notifications.json", out, out.length(),
            if (!listenerEnabled) CollectionResult.DENIED
            else if (out.length() == 0) CollectionResult.EMPTY
            else CollectionResult.OK,
            note
        )
    }

    private fun isNotificationListenerEnabled(ctx: Context): Boolean {
        val flat = android.provider.Settings.Secure.getString(
            ctx.contentResolver,
            "enabled_notification_listeners"
        ) ?: return false
        return flat.contains(ctx.packageName)
    }
}
