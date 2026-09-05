"""Inline SVG chart primitives for the forensic HTML report.

Every chart renders as plain, self-contained SVG markup embedded directly into
report.html — no external library, no CDN, no <canvas>, no JavaScript. Shapes are
painted with SVG `fill`/`stroke` attributes rather than CSS `background`, so they
are always present in a "print to PDF" output regardless of the browser's "print
background graphics" setting (a real risk for the CSS-background badges used
elsewhere in this report, and unacceptable for a chart that is meant to *be* the
evidence summary).

Every function is defensive and honesty-first, matching the rest of this report:
malformed, empty, or all-zero input renders as an empty string rather than a
misleading chart. A donut with a fabricated 100% slice or a bar chart with no
bars is worse than no chart — the caller is expected to skip the enclosing
`<div class="chart-card">` when a chart function returns "".
"""

from __future__ import annotations

import html
import re
from typing import Any

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _esc(v: Any) -> str:
    return html.escape(str(v if v is not None else ""))


def _fmt(n: Any) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return _esc(n)


def bar_chart(
    items: list[tuple[str, int]],
    *,
    color: str = "#7a2e12",
    width: int = 620,
    max_items: int = 12,
    unit: str = "",
) -> str:
    """Horizontal bar chart of (label, count) pairs, largest first.

    Caps at `max_items` bars and states what was dropped rather than silently
    truncating — a report that hides a category reads as "there was none".
    """
    clean = [
        (str(label), value)
        for label, value in items
        if isinstance(value, (int, float)) and value > 0
    ]
    if not clean:
        return ""
    clean.sort(key=lambda p: -p[1])
    shown, dropped = clean[:max_items], clean[max_items:]
    max_val = max(v for _, v in shown) or 1

    label_w, value_w, row_h, row_gap = 190, 60, 20, 8
    track_w = max(40, width - label_w - value_w)
    row_stride = row_h + row_gap
    height = row_gap + len(shown) * row_stride

    bars: list[str] = []
    for i, (label, value) in enumerate(shown):
        y = row_gap + i * row_stride
        bar_w = max(2, round(track_w * value / max_val))
        display_label = label if len(label) <= 30 else label[:29] + "…"
        bars.append(
            f'<rect x="{label_w}" y="{y}" width="{track_w}" height="{row_h}" rx="3" fill="#eceeec"/>'
            f'<rect x="{label_w}" y="{y}" width="{bar_w}" height="{row_h}" rx="3" fill="{_esc(color)}">'
            f"<title>{_esc(label)}: {_fmt(value)}{_esc(unit)}</title></rect>"
            f'<text x="{label_w - 8}" y="{y + row_h * 0.68:.1f}" text-anchor="end" '
            f'font-size="11.5" fill="#1a1d21">{_esc(display_label)}</text>'
            f'<text x="{label_w + bar_w + 8}" y="{y + row_h * 0.68:.1f}" '
            f'font-size="11.5" fill="#5b6570">{_fmt(value)}{_esc(unit)}</text>'
        )

    svg = (
        f'<svg class="chart-svg" viewBox="0 0 {width} {height}" '
        f'role="img" aria-label="Bar chart">{"".join(bars)}</svg>'
    )
    caption = ""
    if dropped:
        rest = sum(v for _, v in dropped)
        plural = "y" if len(dropped) == 1 else "ies"
        caption = (
            f'<p class="chart-caption">+{len(dropped)} more categor{plural} not shown '
            f"({_fmt(rest)} row(s) total) — see the detailed sections below.</p>"
        )
    return svg + caption


def donut_chart(
    segments: list[tuple[str, int, str]],
    *,
    size: int = 168,
    thickness: int = 30,
) -> str:
    """Donut chart of (label, count, hex_color) segments, with an HTML legend.

    Returns "" for no/zero-total input rather than an empty ring, which would
    read as "0 of everything" instead of "nothing to chart".
    """
    clean = [
        (str(label), value, str(color))
        for label, value, color in segments
        if isinstance(value, (int, float)) and value > 0
    ]
    total = sum(v for _, v, _ in clean)
    if not clean or total <= 0:
        return ""

    r = (size - thickness) / 2
    c = size / 2
    circumference = 2 * 3.14159265358979 * r

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

    svg = (
        f'<svg class="chart-svg" viewBox="0 0 {size} {size}" width="{size}" height="{size}" '
        f'role="img" aria-label="Donut chart" style="max-width:{size}px;flex:none">'
        f'<g transform="rotate(-90 {c} {c})">{"".join(arcs)}</g>'
        f'<text x="{c}" y="{c - 4}" text-anchor="middle" font-size="20" font-weight="700" '
        f'fill="#1a1d21">{_fmt(total)}</text>'
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
        '<div style="display:flex;align-items:center;gap:18px;flex-wrap:wrap">'
        f"{svg}"
        f'<ul class="chart-legend">{legend_items}</ul>'
        "</div>"
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

    Beyond `max_bars` active days, contiguous days are grouped into wider bars
    and the caption states the grouping — aggregation, not silent truncation;
    every counted row is still represented in the total.
    """
    clean = [
        (str(d), value) for d, value in buckets if isinstance(value, (int, float)) and value > 0
    ]
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

    margin_l, margin_b, margin_t, margin_r = 40, 22, 10, 10
    plot_w = max(20, width - margin_l - margin_r)
    plot_h = max(10, height - margin_b - margin_t)
    max_val = max(v for _, v in clean) or 1
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

    first_label, last_label = clean[0][0], clean[-1][0]
    axis = (
        f'<line x1="{margin_l}" y1="{margin_t + plot_h}" x2="{width - margin_r}" '
        f'y2="{margin_t + plot_h}" stroke="#dfe2e6"/>'
        f'<text x="{margin_l}" y="{height - 4}" font-size="10" fill="#5b6570">{_esc(first_label)}</text>'
        f'<text x="{width - margin_r}" y="{height - 4}" font-size="10" fill="#5b6570" '
        f'text-anchor="end">{_esc(last_label)}</text>'
        f'<text x="{margin_l - 6}" y="{margin_t + 8}" font-size="10" fill="#5b6570" '
        f'text-anchor="end">{_fmt(max_val)}</text>'
    )

    svg = (
        f'<svg class="chart-svg" viewBox="0 0 {width} {height}" '
        f'role="img" aria-label="Activity timeline">{"".join(bars)}{axis}</svg>'
    )
    return svg + note
