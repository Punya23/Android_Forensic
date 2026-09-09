"""Chart primitives for the forensic HTML report.

Every chart is plain, self-contained markup embedded directly into report.html —
no external library, no CDN, no <canvas>, no JavaScript. Every *bar* is a real
SVG <rect> painted with a `fill` attribute rather than a CSS `background`, so it
is always present in a "print to PDF" output regardless of the browser's "print
background graphics" setting (a real risk for the CSS-background badges used
elsewhere in this report, and unacceptable for a chart that is meant to *be* the
evidence summary).

Layout rule, learned the hard way: **text is HTML, geometry is SVG.**

    An early version drew bar labels and values as SVG <text> inside a fixed
    620-unit viewBox scaled to the container with `width:100%`. That has two
    failure modes, both of which shipped and were caught in review:
      * text positioned past the viewBox edge is *clipped*, not overflowed
        (`svg:root{overflow:hidden}`) — a value read "192 interac" and a label
        lost its leading letter. In a legal report, clipped text is lost
        evidence, and no character-count cap can bound a rendered width.
      * scaling the canvas scales the type with it, so the same font-size
        rendered at ~5.5 CSS px in a narrow grid column and ~19 px in a
        full-width card.
    Bar labels and values are therefore HTML elements, laid out by flexbox: they
    render at their true CSS size at any container width, the browser (not an
    estimate) decides where they wrap, and nothing can be clipped. Only
    `timeline_chart`, which is a genuine coordinate plot, still positions text
    inside SVG — and it derives its gutters from the text it is about to draw.

Every function is defensive and honesty-first, matching the rest of this report:
malformed, empty, or all-zero input renders as an empty string rather than a
misleading chart. A donut with a fabricated 100% slice or a bar chart with no
bars is worse than no chart — the caller is expected to skip the enclosing
`<div class="chart-card">` when a chart function returns "". Nothing is ever
dropped silently: a capped bar list and a folded donut segment both say so in a
caption.
"""

from __future__ import annotations

import html
import math
import re
from typing import Any

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")

# Neutral grey used for the folded "other segments" slice in a donut.
_FOLD_COLOR = "#9aa3ad"


def _esc(v: Any) -> str:
    return html.escape(str(v if v is not None else ""))


def _fmt(n: Any) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError, OverflowError):
        return _esc(n)


def _pos(v: Any) -> bool:
    """True for a value that is a real, positive, renderable magnitude.

    Rejects bools (a flag is not a count), and NaN/inf, which would otherwise
    propagate into an SVG coordinate as ``nan`` and render as nothing at all.
    """
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    if isinstance(v, float) and not math.isfinite(v):
        return False
    return v > 0


def _text_w(s: str, font_size: float) -> float:
    """Upper bound on the rendered width of `s`, in SVG user units.

    Only needed where text must be positioned inside a coordinate space
    (`timeline_chart`); the bar chart uses real HTML text and needs no estimate.
    Deliberately over-estimates — a label narrower than its reserved gutter is
    merely indented, one wider than it is cut off.
    """
    ems = 0.0
    for ch in s:
        if ord(ch) > 0x2000:  # CJK / Devanagari / symbols render wider
            ems += 1.15
        elif ch.isupper():
            ems += 0.95
        else:
            ems += 0.62
    return ems * font_size


def bar_chart(
    items: list[tuple[str, int]],
    *,
    color: str = "#7a2e12",
    max_items: int = 12,
    unit: str = "",
    label_max_chars: int = 60,
) -> str:
    """Horizontal bar chart of (label, count) pairs, largest first.

    Rendered as flex rows of HTML text around an SVG bar, so labels and values
    render at their true size and can never be clipped at any container width
    (see the module docstring). Caps at `max_items` bars and states what was
    dropped rather than silently truncating — a report that hides a category
    reads as "there was none".
    """
    clean = [(str(label), value) for label, value in items if _pos(value)]
    if not clean:
        return ""
    clean.sort(key=lambda p: -p[1])
    shown, dropped = clean[:max_items], clean[max_items:]
    max_val = max(v for _, v in shown) or 1

    rows: list[str] = []
    for label, value in shown:
        # Floor the width so a legitimately tiny value is still a visible mark
        # rather than reading as an absent bar.
        pct = min(100.0, max(0.6, value / max_val * 100.0))
        display = (
            label
            if len(label) <= label_max_chars
            else label[: label_max_chars - 1] + "…"
        )
        rows.append(
            '<div class="bar-row">'
            f'<div class="bar-label" title="{_esc(label)}">{_esc(display)}</div>'
            '<div class="bar-track">'
            '<svg viewBox="0 0 100 10" preserveAspectRatio="none" aria-hidden="true">'
            '<rect x="0" y="0" width="100" height="10" fill="#eceeec"/>'
            f'<rect x="0" y="0" width="{pct:.2f}" height="10" fill="{_esc(color)}"/>'
            "</svg></div>"
            f'<div class="bar-val">{_fmt(value)}{_esc(unit)}</div>'
            "</div>"
        )

    caption = ""
    if dropped:
        rest = sum(v for _, v in dropped)
        plural = "y" if len(dropped) == 1 else "ies"
        caption = (
            f'<p class="chart-caption">+{len(dropped)} more categor{plural} not shown '
            f"({_fmt(rest)} row(s) total) — see the detailed sections below.</p>"
        )
    return f'<div class="bar-chart">{"".join(rows)}</div>{caption}'


def donut_chart(
    segments: list[tuple[str, int, str]],
    *,
    size: int = 168,
    thickness: int = 30,
    max_segments: int = 8,
) -> str:
    """Donut chart of (label, count, hex_color) segments, with an HTML legend.

    Returns "" for no/zero-total input rather than an empty ring, which would
    read as "0 of everything" instead of "nothing to chart". Beyond
    `max_segments`, the smallest slices are folded into a single "other" segment
    and named in a caption — folded, never dropped, and the total still ties out.
    """
    clean = [
        (str(label), value, str(color))
        for label, value, color in segments
        if _pos(value)
    ]
    total = sum(v for _, v, _ in clean)
    if not clean or total <= 0:
        return ""

    clean.sort(key=lambda s: -s[1])
    caption = ""
    if len(clean) > max_segments:
        folded = clean[max_segments - 1 :]
        names = ", ".join(lbl for lbl, _, _ in folded)
        clean = clean[: max_segments - 1] + [
            (f"OTHER ({len(folded)} tiers)", sum(v for _, v, _ in folded), _FOLD_COLOR)
        ]
        caption = (
            f'<p class="chart-caption">The {len(folded)} smallest tiers are combined into '
            f"“other” to keep the chart readable: {_esc(names)}. They are folded, not "
            "dropped — the total still counts every row.</p>"
        )

    r = (size - thickness) / 2
    c = size / 2
    circumference = 2 * math.pi * r

    arcs: list[str] = []
    offset = 0.0
    for label, value, color in clean:
        frac = value / total
        seg_len = frac * circumference
        arcs.append(
            f'<circle cx="{c}" cy="{c}" r="{r}" fill="none" stroke="{_esc(color)}" '
            f'stroke-width="{thickness}" '
            f'stroke-dasharray="{seg_len:.2f} {max(0.0, circumference - seg_len):.2f}" '
            f'stroke-dashoffset="{-offset:.2f}">'
            f"<title>{_esc(label)}: {_fmt(value)} ({frac * 100:.0f}%)</title></circle>"
        )
        offset += seg_len

    # Shrink the centre total if a large number would otherwise collide with the
    # ring (the hole is `size - 2*thickness` wide).
    total_str = _fmt(total)
    total_fs = 20.0
    hole = size - 2 * thickness - 8
    while total_fs > 10 and _text_w(total_str, total_fs) > hole:
        total_fs -= 1.0

    svg = (
        f'<svg class="chart-svg" viewBox="0 0 {size} {size}" width="{size}" height="{size}" '
        f'role="img" aria-label="Donut chart" style="max-width:{size}px;flex:none">'
        f'<g transform="rotate(-90 {c} {c})">{"".join(arcs)}</g>'
        f'<text x="{c}" y="{c - 4}" text-anchor="middle" font-size="{total_fs:g}" '
        f'font-weight="700" fill="#1a1d21">{_esc(total_str)}</text>'
        f'<text x="{c}" y="{c + 14}" text-anchor="middle" font-size="10" fill="#5b6570" '
        f'letter-spacing="0.04em">TOTAL</text>'
        "</svg>"
    )
    legend_items = "".join(
        f'<li><svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">'
        f'<rect width="10" height="10" rx="2" fill="{_esc(color)}"/></svg>'
        f"{_esc(label)}: <b>{_fmt(value)}</b> ({value / total * 100:.0f}%)</li>"
        for label, value, color in clean
    )
    return (
        '<div class="donut-wrap">'
        f"{svg}"
        f'<ul class="chart-legend">{legend_items}</ul>'
        f"</div>{caption}"
    )


def bucket_by_day(timestamps: list[Any]) -> list[tuple[str, int]]:
    """Count ISO-ish timestamps per calendar day (UTC date component only).

    Non-strings and values without a leading YYYY-MM-DD are silently skipped —
    this is a chart aid, not a claim that every row carried a usable timestamp.
    Returned in ascending date order.
    """
    counts: dict[str, int] = {}
    for ts in timestamps:
        if not isinstance(ts, str):
            continue
        m = _DATE_RE.match(ts)
        if not m:
            continue
        day = m.group(0)
        counts[day] = counts.get(day, 0) + 1
    return sorted(counts.items())


def timeline_chart(
    buckets: list[tuple[str, int]],
    *,
    color: str = "#2258a8",
    width: int = 620,
    height: int = 130,
    max_bars: int = 60,
) -> str:
    """Vertical bar chart of (date_label, count) pairs, already time-ordered.

    This one stays a true SVG coordinate plot, so its axis gutters are derived
    from the text about to be drawn into them rather than fixed — a six-figure
    peak used to lose its leading digit off the left edge. Beyond `max_bars`
    active days, contiguous days are grouped into wider bars and the caption
    states the grouping — aggregation, not silent truncation; every counted row
    is still represented in the total.

    `width` should approximate the width the chart will actually render at, so
    the axis type is not scaled up or down relative to the rest of the report.
    """
    clean = [(str(d), value) for d, value in buckets if _pos(value)]
    if not clean:
        return ""

    note = ""
    if len(clean) > max_bars:
        group_size = -(-len(clean) // max_bars)  # ceil division
        grouped: list[tuple[str, int]] = []
        for i in range(0, len(clean), group_size):
            chunk = clean[i : i + group_size]
            grouped.append((chunk[0][0], sum(v for _, v in chunk)))
        note = (
            f'<p class="chart-caption">{len(clean)} active day(s) grouped into '
            f"{len(grouped)} bar(s) of up to {group_size} day(s) each so the chart stays "
            "readable — every counted row is still included, none were dropped.</p>"
        )
        clean = grouped

    max_val = max(v for _, v in clean) or 1
    axis_fs = 10.0
    # Left gutter must fit the y-axis maximum; right gutter must fit half of the
    # end-date label's overhang past the plot edge is handled by anchoring it end.
    margin_l = max(40, math.ceil(_text_w(_fmt(max_val), axis_fs)) + 12)
    margin_b, margin_t, margin_r = 22, 10, 10
    plot_w = max(20, width - margin_l - margin_r)
    plot_h = max(10, height - margin_b - margin_t)
    n = len(clean)
    bar_gap = 2 if n > 1 else 0
    bar_w = max(1.0, (plot_w - bar_gap * (n - 1)) / n)

    bars: list[str] = []
    for i, (label, value) in enumerate(clean):
        x = margin_l + i * (bar_w + bar_gap)
        h = max(1.0, plot_h * value / max_val)
        y = margin_t + (plot_h - h)
        bars.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{h:.1f}" fill="{_esc(color)}">'
            f"<title>{_esc(label)}: {_fmt(value)}</title></rect>"
        )

    baseline = margin_t + plot_h
    axis = [
        f'<line x1="{margin_l}" y1="{baseline}" x2="{width - margin_r}" '
        f'y2="{baseline}" stroke="#dfe2e6"/>',
        f'<text x="{margin_l - 6}" y="{margin_t + 8}" font-size="{axis_fs:g}" fill="#5b6570" '
        f'text-anchor="end">{_esc(_fmt(max_val))}</text>',
    ]
    if n == 1:
        # One bucket is a single day, not a range — printing the same date at both
        # ends would read as a span.
        axis.append(
            f'<text x="{margin_l + plot_w / 2:.1f}" y="{height - 4}" font-size="{axis_fs:g}" '
            f'fill="#5b6570" text-anchor="middle">{_esc(clean[0][0])}</text>'
        )
    else:
        axis.append(
            f'<text x="{margin_l}" y="{height - 4}" font-size="{axis_fs:g}" '
            f'fill="#5b6570">{_esc(clean[0][0])}</text>'
        )
        axis.append(
            f'<text x="{width - margin_r}" y="{height - 4}" font-size="{axis_fs:g}" '
            f'fill="#5b6570" text-anchor="end">{_esc(clean[-1][0])}</text>'
        )

    svg = (
        f'<svg class="chart-svg" viewBox="0 0 {width} {height}" '
        f'role="img" aria-label="Activity timeline">{"".join(bars)}{"".join(axis)}</svg>'
    )
    return svg + note
