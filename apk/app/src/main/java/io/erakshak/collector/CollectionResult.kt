package io.erakshak.collector

import org.json.JSONObject

/**
 * The outcome of a single collector run.
 *
 * Every collector returns one of these instead of throwing, so a permission that was never
 * granted (or an OEM quirk) degrades to a clearly-labelled row in `collector_manifest.json`
 * rather than aborting the whole dump. `payload` is either a [org.json.JSONArray] (record
 * lists) or a [org.json.JSONObject] (single blocks like device info).
 */
data class CollectionResult(
    val name: String,
    val fileName: String,
    val payload: Any,
    val count: Int,
    val status: String,          // ok | denied | error | unsupported | empty
    val error: String? = null,
) {
    fun summary(): JSONObject = JSONObject()
        .put("collector", name)
        .put("file", fileName)
        .put("count", count)
        .put("status", status)
        .apply { error?.let { put("error", it) } }

    companion object {
        const val OK = "ok"
        const val DENIED = "denied"
        const val ERROR = "error"
        const val UNSUPPORTED = "unsupported"
        const val EMPTY = "empty"
    }
}
