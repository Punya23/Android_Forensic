package io.erakshak.collector

import android.content.ContentResolver
import android.content.ContentUris
import android.content.Context
import android.media.ExifInterface
import android.os.Build
import android.os.Bundle
import android.provider.MediaStore
import org.json.JSONArray
import org.json.JSONObject

/**
 * MediaStore enumeration — the high-value non-root media artifact.
 *
 * Instead of `adb pull`-ing gigabytes, we enumerate the media *index* with rich metadata:
 * per-file size, timestamps (`date_taken`), dimensions/duration, the owning app
 * (`owner_package_name` — attributes a photo to WhatsApp/Instagram/Camera), the containing
 * bucket, and — crucially — the **trashed** and **favorite** flags Android 11+ exposes. GPS is
 * read from unredacted EXIF (needs ACCESS_MEDIA_LOCATION) for a capped number of images.
 *
 * The engine can then decide which specific files to pull for full extraction. This mirrors
 * how commercial agents surface a media catalogue before selective acquisition.
 */
object MediaCollector {

    private const val MAX_ROWS = 50_000
    private const val MAX_GPS_LOOKUPS = 800

    fun collect(ctx: Context): CollectionResult {
        // Media read permission differs by Android version.
        val hasMediaPerm = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            ctx.granted(android.Manifest.permission.READ_MEDIA_IMAGES) ||
                ctx.granted(android.Manifest.permission.READ_MEDIA_VIDEO) ||
                ctx.granted(android.Manifest.permission.READ_MEDIA_AUDIO)
        } else {
            ctx.granted(android.Manifest.permission.READ_EXTERNAL_STORAGE)
        }
        if (!hasMediaPerm)
            return CollectionResult("media", "media_inventory.json", JSONArray(), 0,
                CollectionResult.DENIED, "no READ_MEDIA_* / READ_EXTERNAL_STORAGE granted")

        return try {
            val out = JSONArray()
            val gpsBudget = intArrayOf(MAX_GPS_LOOKUPS)  // mutable box shared across queries
            queryKind(ctx, MediaStore.Images.Media.EXTERNAL_CONTENT_URI, "image", out, gpsBudget)
            queryKind(ctx, MediaStore.Video.Media.EXTERNAL_CONTENT_URI, "video", out, gpsBudget)
            queryKind(ctx, MediaStore.Audio.Media.EXTERNAL_CONTENT_URI, "audio", out, gpsBudget)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                runCatching {
                    queryKind(ctx, MediaStore.Downloads.EXTERNAL_CONTENT_URI, "download", out, gpsBudget)
                }
            }
            CollectionResult("media", "media_inventory.json", out, out.length(),
                if (out.length() == 0) CollectionResult.EMPTY else CollectionResult.OK)
        } catch (e: SecurityException) {
            CollectionResult("media", "media_inventory.json", JSONArray(), 0, CollectionResult.DENIED, e.message)
        } catch (e: Exception) {
            CollectionResult("media", "media_inventory.json", JSONArray(), 0, CollectionResult.ERROR, e.message)
        }
    }

    private fun projection(): Array<String> {
        val cols = mutableListOf(
            MediaStore.MediaColumns._ID,
            MediaStore.MediaColumns.DISPLAY_NAME,
            MediaStore.MediaColumns.MIME_TYPE,
            MediaStore.MediaColumns.SIZE,
            MediaStore.MediaColumns.DATE_ADDED,
            MediaStore.MediaColumns.DATE_MODIFIED,
            MediaStore.MediaColumns.WIDTH,
            MediaStore.MediaColumns.HEIGHT,
            MediaStore.MediaColumns.DATA,
        )
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            cols += MediaStore.MediaColumns.DATE_TAKEN
            cols += MediaStore.MediaColumns.DURATION
            cols += MediaStore.MediaColumns.BUCKET_DISPLAY_NAME
            cols += MediaStore.MediaColumns.OWNER_PACKAGE_NAME
            cols += MediaStore.MediaColumns.RELATIVE_PATH
            cols += MediaStore.MediaColumns.IS_PENDING
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            cols += MediaStore.MediaColumns.IS_FAVORITE
            cols += MediaStore.MediaColumns.IS_TRASHED
        }
        return cols.toTypedArray()
    }

    private fun queryKind(
        ctx: Context, uri: android.net.Uri, kind: String, out: JSONArray, gpsBudget: IntArray
    ) {
        if (out.length() >= MAX_ROWS) return
        val proj = projection()
        val cursor = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            // Include trashed items (and pending) so recently-deleted media shows up.
            val args = Bundle().apply {
                putInt(MediaStore.QUERY_ARG_MATCH_TRASHED, MediaStore.MATCH_INCLUDE)
                putString(ContentResolver.QUERY_ARG_SQL_SORT_ORDER,
                    "${MediaStore.MediaColumns.DATE_ADDED} DESC")
            }
            ctx.contentResolver.query(uri, proj, args, null)
        } else {
            ctx.contentResolver.query(uri, proj, null, null,
                "${MediaStore.MediaColumns.DATE_ADDED} DESC")
        }
        cursor?.use { c ->
            while (c.moveToNext() && out.length() < MAX_ROWS) {
                val id = with(Cur) { c.longOrNull(MediaStore.MediaColumns._ID) } ?: continue
                val o = JSONObject()
                    .put("kind", kind)
                    .put("id", id)
                    .put("display_name", with(Cur) { c.strOrNull(MediaStore.MediaColumns.DISPLAY_NAME) } ?: "")
                    .put("mime_type", with(Cur) { c.strOrNull(MediaStore.MediaColumns.MIME_TYPE) } ?: "")
                    .put("size", with(Cur) { c.longOrNull(MediaStore.MediaColumns.SIZE) } ?: 0L)
                    .put("date_added", with(Cur) { c.longOrNull(MediaStore.MediaColumns.DATE_ADDED) } ?: 0L)
                    .put("date_modified", with(Cur) { c.longOrNull(MediaStore.MediaColumns.DATE_MODIFIED) } ?: 0L)
                    .put("width", with(Cur) { c.intOrNull(MediaStore.MediaColumns.WIDTH) } ?: 0)
                    .put("height", with(Cur) { c.intOrNull(MediaStore.MediaColumns.HEIGHT) } ?: 0)
                    .put("data_path", with(Cur) { c.strOrNull(MediaStore.MediaColumns.DATA) } ?: "")
                    .put("date_taken", with(Cur) { c.longOrNull(MediaStore.MediaColumns.DATE_TAKEN) } ?: 0L)
                    .put("duration", with(Cur) { c.longOrNull(MediaStore.MediaColumns.DURATION) } ?: 0L)
                    .put("bucket", with(Cur) { c.strOrNull(MediaStore.MediaColumns.BUCKET_DISPLAY_NAME) } ?: "")
                    .put("owner_package", with(Cur) { c.strOrNull(MediaStore.MediaColumns.OWNER_PACKAGE_NAME) } ?: "")
                    .put("relative_path", with(Cur) { c.strOrNull(MediaStore.MediaColumns.RELATIVE_PATH) } ?: "")
                    .put("is_pending", (with(Cur) { c.intOrNull(MediaStore.MediaColumns.IS_PENDING) } ?: 0) == 1)
                    .put("is_favorite", (with(Cur) { c.intOrNull(MediaStore.MediaColumns.IS_FAVORITE) } ?: 0) == 1)
                    .put("is_trashed", (with(Cur) { c.intOrNull(MediaStore.MediaColumns.IS_TRASHED) } ?: 0) == 1)

                if (kind == "image" && gpsBudget[0] > 0) {
                    readGps(ctx, uri, id)?.let { (lat, lon) ->
                        o.put("gps_lat", lat).put("gps_lon", lon)
                        gpsBudget[0] = gpsBudget[0] - 1
                    }
                }
                out.put(o)
            }
        }
    }

    /** Read EXIF lat/long via the MediaStore (unredacted requires ACCESS_MEDIA_LOCATION). */
    private fun readGps(ctx: Context, baseUri: android.net.Uri, id: Long): Pair<Double, Double>? {
        return try {
            var itemUri = ContentUris.withAppendedId(baseUri, id)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                itemUri = MediaStore.setRequireOriginal(itemUri)
            }
            ctx.contentResolver.openInputStream(itemUri)?.use { input ->
                val exif = ExifInterface(input)
                val latlong = FloatArray(2)
                if (exif.getLatLong(latlong) && !(latlong[0] == 0f && latlong[1] == 0f)) {
                    Pair(latlong[0].toDouble(), latlong[1].toDouble())
                } else null
            }
        } catch (e: Exception) {
            null
        }
    }
}
