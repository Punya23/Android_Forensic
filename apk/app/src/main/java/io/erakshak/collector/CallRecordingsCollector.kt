package io.erakshak.collector

import android.content.Context
import android.media.MediaMetadataRetriever
import android.os.Environment
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * Scans all common OEM call-recording storage paths and returns a JSON index.
 *
 * OEM path map:
 *   Samsung One UI      → DCIM/Call Recording/
 *   Samsung (newer)     → Recordings/Call/
 *   Xiaomi HyperOS/MIUI → MIUI/sound_recorder/call_rec/
 *   OPPO / ColorOS      → PhoneRecord/
 *   Realme UI           → Calls/
 *   Vivo / iQOO         → Sounds/  and  Music/Recordings/
 *   OnePlus OxygenOS    → Android/data/com.oneplus.telephony/  (best-effort)
 *   Honor MagicOS       → CallRecordings/
 *   Generic AOSP        → Record/Call Recording/  and  Recordings/
 */
object CallRecordingsCollector {

    private val AUDIO_EXTENSIONS = setOf("m4a", "mp3", "ogg", "aac", "wav", "amr", "3gp", "opus")

    private val CANDIDATE_PATHS = listOf(
        // Samsung One UI
        "DCIM/Call Recording",
        // Samsung newer / Galaxy
        "Recordings/Call",
        // Xiaomi HyperOS / MIUI
        "MIUI/sound_recorder/call_rec",
        // OPPO ColorOS
        "PhoneRecord",
        // Realme UI
        "Calls",
        // Vivo / iQOO
        "Sounds",
        "Music/Recordings",
        // Honor MagicOS
        "CallRecordings",
        // Motorola Hello UI
        "Recordings",
        // Generic AOSP / Pixel
        "Record/Call Recording",
        "Record",
    )

    fun collect(ctx: Context): CollectionResult {
        val sdcard = Environment.getExternalStorageDirectory()
        val out = JSONArray()

        for (relPath in CANDIDATE_PATHS) {
            val dir = File(sdcard, relPath)
            if (!dir.exists() || !dir.isDirectory) continue
            scanDir(dir, out)
        }

        // Also scan OnePlus app-private data if accessible (no root needed on some builds)
        runCatching {
            val onePlusDir = File(sdcard, "Android/data/com.oneplus.telephony")
            if (onePlusDir.exists()) scanDir(onePlusDir, out)
        }

        return CollectionResult(
            "recordings", "recordings.json", out, out.length(),
            if (out.length() == 0) CollectionResult.EMPTY else CollectionResult.OK,
            if (out.length() == 0) "No call recording files found in known OEM paths" else null
        )
    }

    private fun scanDir(dir: File, out: JSONArray) {
        dir.walkTopDown()
            .filter { it.isFile && it.extension.lowercase() in AUDIO_EXTENSIONS }
            .sortedByDescending { it.lastModified() }
            .forEach { file ->
                val meta = JSONObject()
                    .put("filename",    file.name)
                    .put("path",        file.absolutePath)
                    .put("size_bytes",  file.length())
                    .put("date_ms",     file.lastModified())
                    .put("extension",   file.extension.lowercase())

                // Try to extract playback duration via MediaMetadataRetriever
                runCatching {
                    MediaMetadataRetriever().use { mmr ->
                        mmr.setDataSource(file.absolutePath)
                        val dur = mmr.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)
                        if (dur != null) meta.put("duration_ms", dur.toLong())

                        // Some OEMs embed the contact name in the ARTIST or TITLE tag
                        val title  = mmr.extractMetadata(MediaMetadataRetriever.METADATA_KEY_TITLE)
                        val artist = mmr.extractMetadata(MediaMetadataRetriever.METADATA_KEY_ARTIST)
                        if (!title.isNullOrBlank())  meta.put("title",        title)
                        if (!artist.isNullOrBlank()) meta.put("contact_hint", artist)
                    }
                }

                // Fallback: parse phone number / contact hint from filename
                if (!meta.has("contact_hint")) {
                    val hint = extractHintFromFilename(file.name)
                    if (hint.isNotEmpty()) meta.put("contact_hint", hint)
                }

                out.put(meta)
            }
    }

    /**
     * Many OEMs encode the remote number or contact name in the filename.
     * e.g. "Call_recording_+919272166334_20260801_120000.m4a"
     *      "20260801_120000_Aai.m4a"
     */
    private fun extractHintFromFilename(name: String): String {
        // Phone number pattern
        val phoneRegex = Regex("""(\+?91\d{10}|\d{10,13})""")
        val phoneMatch = phoneRegex.find(name)
        if (phoneMatch != null) return phoneMatch.value

        // Try stripping date-like tokens and common prefixes to find a name segment
        val cleaned = name
            .replace(Regex("""(?i)(call[_\s]?recording[_\s]?|record[_\s]?|phonecall[_\s]?)"""), "")
            .replace(Regex("""\d{8}[_\-T]\d{6}"""), "")
            .replace(Regex("""\.\w{2,5}$"""), "")
            .replace(Regex("""[_\-]+"""), " ")
            .trim()
        return if (cleaned.length in 2..50) cleaned else ""
    }
}
