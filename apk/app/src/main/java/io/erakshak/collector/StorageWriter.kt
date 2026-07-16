package io.erakshak.collector

import android.content.ContentValues
import android.content.Context
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import java.io.File

/**
 * Writes collector output to the public `Download/` folder so the engine can
 * `adb pull /sdcard/Download/<file>` on every Android version.
 *
 * Android 10+ scoped storage blocks a plain `File` write to public Download, so we insert via
 * MediaStore (which an app is always allowed to do for its own Downloads). We delete any
 * same-named prior file first so re-runs overwrite rather than accumulate `contacts (1).json`.
 * If MediaStore fails we fall back to a direct File write, then to the app-scoped external dir.
 */
object StorageWriter {

    /** Returns the on-device path the file landed at (best-effort, for the audit/result UI). */
    fun write(ctx: Context, fileName: String, text: String): String {
        val bytes = text.toByteArray(Charsets.UTF_8)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            runCatching { return writeViaMediaStore(ctx, fileName, bytes) }
        }
        // Legacy (<= Android 9) or MediaStore fallback: direct File write to public Downloads.
        runCatching {
            val dir = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS)
            if (dir != null && (dir.exists() || dir.mkdirs()) && dir.canWrite()) {
                val f = File(dir, fileName)
                f.writeBytes(bytes)
                return f.absolutePath
            }
        }
        // Last resort: app-scoped external files dir (still adb-pullable on most OEMs).
        val fallbackDir = ctx.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS) ?: ctx.filesDir
        fallbackDir.mkdirs()
        val f = File(fallbackDir, fileName)
        f.writeBytes(bytes)
        return f.absolutePath
    }

    private fun writeViaMediaStore(ctx: Context, fileName: String, bytes: ByteArray): String {
        val resolver = ctx.contentResolver
        val collection = MediaStore.Downloads.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)
        // Overwrite semantics: drop any prior copy this app wrote.
        runCatching {
            resolver.delete(collection, "${MediaStore.Downloads.DISPLAY_NAME}=?", arrayOf(fileName))
        }
        val values = ContentValues().apply {
            put(MediaStore.Downloads.DISPLAY_NAME, fileName)
            put(MediaStore.Downloads.MIME_TYPE, "application/json")
            put(MediaStore.Downloads.IS_PENDING, 1)
        }
        val uri = resolver.insert(collection, values)
            ?: throw IllegalStateException("MediaStore insert returned null")
        resolver.openOutputStream(uri)?.use { it.write(bytes) }
            ?: throw IllegalStateException("openOutputStream returned null")
        values.clear()
        values.put(MediaStore.Downloads.IS_PENDING, 0)
        resolver.update(uri, values, null, null)
        return "/sdcard/Download/$fileName"
    }
}
