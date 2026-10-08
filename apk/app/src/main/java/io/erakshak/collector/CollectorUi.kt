package io.erakshak.collector

import android.app.Activity
import android.content.Intent
import android.content.res.ColorStateList
import android.content.res.Configuration
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.text.SpannableString
import android.text.Spanned
import android.text.style.ForegroundColorSpan
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.ScrollView
import android.widget.TextView
import androidx.core.graphics.PathParser
import java.util.Locale

/**
 * The Collector's screen, in the dashboard's design language: the same panel/ink/accent tokens
 * (light and dark), the SNAGR mark, and a live view of what is being collected right now.
 *
 * Views are built once and mutated by [ProgressScreen.update] / [ProgressScreen.tick]; rebuilding
 * the tree per collector would reset the scroll position and flicker the spinner.
 */
class Palette(night: Boolean) {
    // Mirrors app/src/index.css (--color-*): the dashboard's light and dark tokens.
    val ink = if (night) 0xFFE6E8EB.toInt() else 0xFF181A1D.toInt()
    val panel = if (night) 0xFF0F1113.toInt() else 0xFFF4F5F6.toInt()
    val card = if (night) 0xFF16181B.toInt() else 0xFFFFFFFF.toInt()
    val line = if (night) 0xFF26292D.toInt() else 0xFFDEE1E5.toInt()
    val muted = if (night) 0xFF8B9097.toInt() else 0xFF646A72.toInt()
    val accent = if (night) 0xFF4C8DD6.toInt() else 0xFF2C6BB0.toInt()
    val live = if (night) 0xFF4AAA6E.toInt() else 0xFF1C7D3F.toInt()
    val deletion = if (night) 0xFFD65C56.toInt() else 0xFFA5322F.toInt()
    val warn = if (night) 0xFFD2A048.toInt() else 0xFFA6741A.toInt()

    companion object {
        fun of(a: Activity) = Palette(
            (a.resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK) ==
                Configuration.UI_MODE_NIGHT_YES
        )
    }
}

/** The SNAGR mark (hexagon container, one-stroke S, bright node) — same geometry as Logo.tsx. */
class LogoView(activity: Activity, p: Palette) : View(activity) {
    private val hex = PathParser.createPathFromPathData(
        "M16 2.6 27.6 9.3a2.4 2.4 0 0 1 1.2 2.1v9.2a2.4 2.4 0 0 1-1.2 2.1L16 29.4 4.4 22.7" +
            "a2.4 2.4 0 0 1-1.2-2.1v-9.2a2.4 2.4 0 0 1 1.2-2.1L16 2.6Z"
    )
    private val s = PathParser.createPathFromPathData(
        "M20.6 12.2c-.9-1.9-3-2.7-5-2.4-2.4.4-3.6 2.2-3 4 .6 1.9 2.7 2.4 4.4 2.9 2 .6 3.9 1.3 " +
            "4 3.3.1 2-1.8 3.4-4.2 3.3-2-.1-3.7-1-4.6-2.6"
    )
    private val fill = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL; color = (p.accent and 0x00FFFFFF) or 0x24000000
    }
    private val edge = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE; strokeWidth = 1.6f; strokeJoin = Paint.Join.ROUND; color = p.accent
    }
    private val stroke = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE; strokeWidth = 2.1f; strokeCap = Paint.Cap.ROUND; color = p.ink
    }
    private val node = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = p.accent }

    override fun onDraw(canvas: Canvas) {
        canvas.scale(width / 32f, height / 32f)
        canvas.drawPath(hex, fill)
        canvas.drawPath(hex, edge)
        canvas.drawPath(s, stroke)
        canvas.drawCircle(16f, 2.6f, 1.9f, node)
    }
}

class ProgressScreen(
    private val act: Activity,
    private val wanted: List<String>,
    private val labels: Map<String, String>,
    private val facts: List<Pair<String, String>>,
    version: String,
) {
    /** One thing the examiner can fix before collection starts (Location off, Wi-Fi off). */
    class Issue(val title: String, val detail: String, val settings: Intent)

    private class Row(val glyph: TextView, val spinner: ProgressBar, val detail: TextView)

    private val p = Palette.of(act)
    private val rows = LinkedHashMap<String, Row>()
    private val started = System.currentTimeMillis()
    private var runningName: String? = null
    private var runningSince = 0L
    private var finished = false

    private lateinit var title: TextView
    private lateinit var sub: TextView
    private lateinit var preflight: LinearLayout
    private lateinit var percent: TextView
    private lateinit var counts: TextView
    private lateinit var elapsed: TextView
    private lateinit var bar: ProgressBar
    private lateinit var nowCard: LinearLayout
    private lateinit var nowLabel: TextView
    private lateinit var nowHint: TextView
    private lateinit var nowTime: TextView
    private var continueBtn: Button? = null

    val root: View = build(version)

    // ── Construction ──────────────────────────────────────────────────────

    private fun dp(v: Int) = (v * act.resources.displayMetrics.density).toInt()

    private fun text(
        s: CharSequence, size: Float, color: Int, bold: Boolean = false, mono: Boolean = false,
    ) = TextView(act).apply {
        text = s; textSize = size; setTextColor(color)
        typeface = when {
            mono -> Typeface.MONOSPACE
            bold -> Typeface.create("sans-serif-medium", Typeface.NORMAL)
            else -> Typeface.SANS_SERIF
        }
    }

    private fun cardBox(): LinearLayout = LinearLayout(act).apply {
        orientation = LinearLayout.VERTICAL
        setPadding(dp(16), dp(14), dp(16), dp(14))
        background = GradientDrawable().apply {
            setColor(p.card); cornerRadius = dp(14).toFloat(); setStroke(dp(1), p.line)
        }
        layoutParams = LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT,
        ).apply { topMargin = dp(12) }
    }

    private fun heading(s: String) = text(s.uppercase(Locale.ROOT), 11f, p.muted, bold = true).apply {
        letterSpacing = 0.12f; setPadding(0, 0, 0, dp(8))
    }

    private fun build(version: String): View {
        val content = LinearLayout(act).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(20), dp(16), dp(28))
        }

        // Header: mark + wordmark on the left, version on the right.
        val header = LinearLayout(act).apply { gravity = Gravity.CENTER_VERTICAL }
        header.addView(LogoView(act, p), LinearLayout.LayoutParams(dp(34), dp(34)))
        val word = SpannableString("SNAGR").apply {
            setSpan(ForegroundColorSpan(p.accent), 4, 5, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
        }
        header.addView(text(word, 19f, p.ink, bold = true).apply {
            letterSpacing = 0.2f; setPadding(dp(10), 0, 0, 0)
        })
        header.addView(View(act), LinearLayout.LayoutParams(0, 1, 1f))
        header.addView(text("Collector v$version", 11f, p.muted, mono = true))
        content.addView(header)

        title = text("Starting…", 24f, p.ink, bold = true).apply { setPadding(0, dp(22), 0, 0) }
        sub = text("", 13f, p.muted).apply { setPadding(0, dp(4), 0, 0) }
        content.addView(title)
        content.addView(sub)

        preflight = cardBox().apply { visibility = View.GONE }
        content.addView(preflight)

        // Overall progress.
        val overall = cardBox()
        val top = LinearLayout(act).apply { gravity = Gravity.CENTER_VERTICAL }
        percent = text("0%", 30f, p.ink, bold = true, mono = true)
        top.addView(percent)
        top.addView(View(act), LinearLayout.LayoutParams(0, 1, 1f))
        val right = LinearLayout(act).apply { orientation = LinearLayout.VERTICAL; gravity = Gravity.END }
        counts = text("", 12f, p.muted, mono = true).apply { gravity = Gravity.END }
        elapsed = text("00:00", 12f, p.muted, mono = true).apply { gravity = Gravity.END }
        right.addView(counts); right.addView(elapsed)
        top.addView(right)
        overall.addView(top)
        bar = ProgressBar(act, null, android.R.attr.progressBarStyleHorizontal).apply {
            max = wanted.size.coerceAtLeast(1) * 100
            progressTintList = ColorStateList.valueOf(p.accent)
            progressBackgroundTintList = ColorStateList.valueOf(p.line)
        }
        overall.addView(bar, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, dp(10),
        ).apply { topMargin = dp(10) })
        content.addView(overall)

        // "Now collecting": the one category in flight, with its own loader and timer.
        nowCard = cardBox().apply {
            visibility = View.GONE; orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL
        }
        val spin = ProgressBar(act).apply { indeterminateTintList = ColorStateList.valueOf(p.accent) }
        nowCard.addView(spin, LinearLayout.LayoutParams(dp(30), dp(30)).apply { rightMargin = dp(14) })
        val nowText = LinearLayout(act).apply { orientation = LinearLayout.VERTICAL }
        nowLabel = text("", 16f, p.ink, bold = true)
        nowHint = text("", 12f, p.muted)
        nowText.addView(nowLabel); nowText.addView(nowHint)
        nowCard.addView(nowText, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        nowTime = text("", 12f, p.muted, mono = true)
        nowCard.addView(nowTime)
        content.addView(nowCard)

        // Category checklist.
        val list = cardBox()
        list.addView(heading("Categories"))
        wanted.forEach { name ->
            val line = LinearLayout(act).apply {
                orientation = LinearLayout.HORIZONTAL; setPadding(0, dp(7), 0, dp(7))
            }
            val slot = FrameLayout(act)
            val glyph = text("•", 16f, p.muted, bold = true).apply { gravity = Gravity.CENTER }
            val spinner = ProgressBar(act).apply {
                indeterminateTintList = ColorStateList.valueOf(p.accent); visibility = View.GONE
            }
            slot.addView(glyph, FrameLayout.LayoutParams(dp(22), dp(22)))
            slot.addView(spinner, FrameLayout.LayoutParams(dp(18), dp(18)).apply { gravity = Gravity.CENTER })
            line.addView(slot, LinearLayout.LayoutParams(dp(22), dp(22)).apply { rightMargin = dp(12) })
            val col = LinearLayout(act).apply { orientation = LinearLayout.VERTICAL }
            col.addView(text(labels[name] ?: name, 14f, p.ink))
            val detail = text("", 11.5f, p.muted)
            detail.visibility = View.GONE
            col.addView(detail)
            line.addView(col, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
            list.addView(line)
            rows[name] = Row(glyph, spinner, detail)
        }
        content.addView(list)

        // Session facts.
        val info = cardBox()
        info.addView(heading("This session"))
        facts.filter { it.second.isNotBlank() }.forEach { (k, v) ->
            val line = LinearLayout(act).apply { setPadding(0, dp(4), 0, dp(4)) }
            line.addView(text(k, 12.5f, p.muted), LinearLayout.LayoutParams(dp(96), ViewGroup.LayoutParams.WRAP_CONTENT))
            line.addView(text(v, 12.5f, p.ink, mono = true), LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
            info.addView(line)
        }
        content.addView(info)

        content.addView(text(
            "Read-only collection. Nothing leaves this phone until the SNAGR engine pulls it over USB; " +
                "the helper and its access are removed afterwards.",
            11.5f, p.muted,
        ).apply { setPadding(dp(4), dp(14), dp(4), 0) })

        return ScrollView(act).apply { setBackgroundColor(p.panel); isFillViewport = true; addView(content) }
    }

    // ── State ─────────────────────────────────────────────────────────────

    /** A plain headline with no progress yet (permissions, idle, errors). */
    fun setStage(headline: String, detail: String, tone: Int = p.ink) {
        title.text = headline; title.setTextColor(tone); sub.text = detail
    }

    fun update(done: List<CollectionResult>, running: String?, isFinished: Boolean) {
        if (running != runningName) { runningName = running; runningSince = System.currentTimeMillis() }
        finished = isFinished
        val byName = done.associateBy { it.name }

        rows.forEach { (name, row) ->
            val r = byName[name]
            row.spinner.visibility = if (r == null && name == running) View.VISIBLE else View.GONE
            row.glyph.visibility = if (row.spinner.visibility == View.VISIBLE) View.INVISIBLE else View.VISIBLE
            when {
                r != null -> {
                    val (mark, color) = when (r.status) {
                        CollectionResult.OK -> "✓" to p.live
                        CollectionResult.EMPTY -> "○" to p.muted
                        CollectionResult.DENIED -> "✗" to p.deletion
                        CollectionResult.UNSUPPORTED -> "–" to p.muted
                        else -> "!" to p.warn
                    }
                    row.glyph.text = mark; row.glyph.setTextColor(color)
                    val line = when (r.status) {
                        CollectionResult.OK -> "${r.count} records"
                        CollectionResult.EMPTY -> r.error ?: "nothing found"
                        else -> r.error ?: r.status
                    }
                    row.detail.text = line.take(110)
                    row.detail.setTextColor(if (r.status == CollectionResult.OK) p.live else color)
                    row.detail.visibility = View.VISIBLE
                }
                name == running -> {
                    row.detail.text = "collecting…"; row.detail.setTextColor(p.accent)
                    row.detail.visibility = View.VISIBLE
                }
                else -> { row.glyph.text = "•"; row.glyph.setTextColor(p.muted); row.detail.visibility = View.GONE }
            }
        }

        val total = wanted.size.coerceAtLeast(1)
        percent.text = "${done.size * 100 / total}%"
        bar.progress = done.size * 100 + if (running != null) 40 else 0
        counts.text = "${done.size}/${wanted.size} categories · ${done.sumOf { it.count }} records"

        val gaps = done.count { it.status == CollectionResult.DENIED || it.status == CollectionResult.ERROR }
        when {
            isFinished && gaps == 0 ->
                setStage("Collection complete", "Evidence is ready for the engine to pull. You can leave the phone connected.", p.live)
            isFinished ->
                setStage("Complete, with $gaps gap${if (gaps == 1) "" else "s"}", "Some categories were denied or failed — each reason is listed below and in the case log.", p.warn)
            else -> setStage("Collecting evidence", "Keep this screen open and the phone unlocked.")
        }

        nowCard.visibility = if (running != null && !isFinished) View.VISIBLE else View.GONE
        if (running != null) {
            nowLabel.text = labels[running] ?: running
            nowHint.text = HINTS[running] ?: ""
        }
        tick()
    }

    /** Called once a second: only the clocks change, so nothing else is touched. */
    fun tick() {
        val now = System.currentTimeMillis()
        elapsed.text = clock(now - started)
        nowTime.text = if (runningName != null && !finished) clock(now - runningSince) else ""
    }

    private fun clock(ms: Long): String {
        val s = ms / 1000
        return String.format(Locale.ROOT, "%02d:%02d", s / 60, s % 60)
    }

    // ── Pre-flight: Location / Wi-Fi switched off ─────────────────────────

    /** Offer to fix [issues] first. [onContinue] runs when the examiner skips or the clock runs out. */
    fun showPreflight(issues: List<Issue>, secondsLeft: Int, onContinue: () -> Unit) {
        preflight.removeAllViews()
        preflight.visibility = View.VISIBLE
        preflight.addView(heading("Before we start"))
        issues.forEach { issue ->
            val line = LinearLayout(act).apply { gravity = Gravity.CENTER_VERTICAL; setPadding(0, dp(6), 0, dp(6)) }
            val col = LinearLayout(act).apply { orientation = LinearLayout.VERTICAL }
            col.addView(text(issue.title, 14f, p.ink, bold = true))
            col.addView(text(issue.detail, 12f, p.muted))
            line.addView(col, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
            line.addView(Button(act).apply {
                text = "Turn on"; isAllCaps = false; textSize = 13f; setTextColor(Color.WHITE)
                background = GradientDrawable().apply { setColor(p.accent); cornerRadius = dp(8).toFloat() }
                setOnClickListener { act.startActivity(issue.settings) }
            }, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, dp(40)))
            preflight.addView(line)
        }
        continueBtn = Button(act).apply {
            text = "Continue without (${secondsLeft}s)"; isAllCaps = false; textSize = 13f; setTextColor(p.ink)
            background = GradientDrawable().apply {
                setColor(p.panel); cornerRadius = dp(8).toFloat(); setStroke(dp(1), p.line)
            }
            setOnClickListener { onContinue() }
        }
        preflight.addView(
            continueBtn,
            LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(44)).apply { topMargin = dp(8) },
        )
        setStage("Almost ready", "Location and Wi-Fi evidence needs them switched on. Turn them on, or continue without.", p.warn)
    }

    fun setCountdown(secondsLeft: Int) { continueBtn?.text = "Continue without (${secondsLeft}s)" }

    fun hidePreflight() { preflight.visibility = View.GONE }

    companion object {
        private val HINTS = mapOf(
            "contacts" to "Address-book entries",
            "calllog" to "Incoming, outgoing and missed calls",
            "sms" to "Text messages",
            "calendar" to "Events and reminders",
            "accounts" to "Signed-in accounts",
            "apps" to "Installed applications",
            "usage" to "Which apps were used, and when",
            "recordings" to "Call-recording index",
            "notifications" to "Notifications on the device",
            "location" to "Last known location fix",
            "wifi" to "Connected and nearby Wi-Fi networks",
            "bluetooth" to "Paired Bluetooth devices",
            "device" to "Hardware and system details",
            "media" to "Largest step — indexing photos, videos and audio. This can take a few minutes.",
        )
    }
}
