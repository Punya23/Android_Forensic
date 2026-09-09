"""Unit tests for the inline-SVG chart primitives used by the HTML report.

Charts are a visual aid layered on top of already-reported figures, but the same
honesty rules as the rest of the report apply: no chart for zero/empty data, no
silent truncation, and every attacker-controlled label must be escaped — a chart
label is still text rendered into the examiner's browser.
"""

from __future__ import annotations

from triage.report import charts


# ---------------------------------------------------------------------------
# bar_chart
# ---------------------------------------------------------------------------
def test_bar_chart_empty_input_renders_nothing():
    assert charts.bar_chart([]) == ""


def test_bar_chart_all_zero_values_renders_nothing():
    assert charts.bar_chart([("Messages", 0), ("Calls", 0)]) == ""


def test_bar_chart_renders_bars_proportional_to_value():
    out = charts.bar_chart([("A", 10), ("B", 5)])
    assert "<svg" in out
    assert "A" in out and "B" in out
    # Larger value must produce a wider bar than the smaller one. Bar widths are
    # percentages of the track, so the largest is always 100.
    import re

    rects = re.findall(r'<rect x="0" y="0" width="([\d.]+)" height="10" fill="#7a2e12"', out)
    assert len(rects) == 2
    assert float(rects[0]) == 100.0
    assert float(rects[1]) == 50.0


def test_bar_chart_label_and_value_are_html_not_svg_text():
    """Regression: labels/values used to be SVG <text> inside a fixed viewBox, which
    clipped anything wider than its hard-coded column and rescaled the type with the
    container. They must be real HTML so the browser bounds them."""
    out = charts.bar_chart([("Some Very Long Participant Name Indeed", 10)], unit=" calls")
    assert "<text" not in out, "chart text must not be SVG <text> any more"
    assert 'class="bar-label"' in out and 'class="bar-val"' in out
    assert "10 calls" in out


def test_bar_chart_keeps_full_label_in_title_when_truncated():
    long_label = "X" * 90
    out = charts.bar_chart([(long_label, 3)])
    assert f'title="{long_label}"' in out, "the untruncated label must survive in the title"
    assert "…" in out


def test_bar_chart_tiny_value_still_renders_a_visible_bar():
    out = charts.bar_chart([("Big", 3000), ("Tiny", 1)])
    import re

    widths = [
        float(w)
        for w in re.findall(r'<rect x="0" y="0" width="([\d.]+)" height="10" fill="#7a2e12"', out)
    ]
    assert widths[0] == 100.0
    assert widths[1] > 0, "a real but tiny count must not render as an absent bar"


def test_charts_reject_bool_and_non_finite_values():
    """A bool is not a count, and NaN/inf would reach an SVG attribute as 'nan'."""
    assert charts.bar_chart([("flag", True)]) == ""
    assert charts.bar_chart([("nan", float("nan"))]) == ""
    assert charts.bar_chart([("inf", float("inf"))]) == ""
    assert charts.donut_chart([("inf", float("inf"), "#000")]) == ""
    assert charts.timeline_chart([("2026-01-01", float("inf"))]) == ""


def test_bar_chart_caps_items_and_states_what_was_dropped():
    items = [(f"cat-{i}", i + 1) for i in range(20)]
    svg = charts.bar_chart(items, max_items=5)
    assert "cat-19" in svg  # highest value kept
    assert "+15 more categories not shown" in svg
    assert "chart-caption" in svg


def test_bar_chart_escapes_hostile_labels():
    xss = '<script>alert(1)</script>'
    svg = charts.bar_chart([(xss, 3)])
    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg


# ---------------------------------------------------------------------------
# donut_chart
# ---------------------------------------------------------------------------
def test_donut_chart_empty_or_zero_total_renders_nothing():
    assert charts.donut_chart([]) == ""
    assert charts.donut_chart([("live", 0, "#000")]) == ""


def test_donut_chart_renders_segments_and_legend_with_percentages():
    html_out = charts.donut_chart([("LIVE", 3, "#1c7d3f"), ("CARVED", 1, "#a6741a")])
    assert "<svg" in html_out
    assert "LIVE" in html_out and "CARVED" in html_out
    assert "75%" in html_out
    assert "25%" in html_out
    assert ">4<" in html_out  # total in the donut centre


def test_donut_chart_escapes_hostile_labels():
    xss = '<script>alert(1)</script>'
    html_out = charts.donut_chart([(xss, 2, "#000")])
    assert "<script>" not in html_out
    assert "&lt;script&gt;" in html_out


# ---------------------------------------------------------------------------
# bucket_by_day / timeline_chart
# ---------------------------------------------------------------------------
def test_bucket_by_day_counts_and_skips_unparseable_timestamps():
    ts = [
        "2026-01-01T00:00:00Z",
        "2026-01-01T12:00:00Z",
        "2026-01-02T00:00:00Z",
        None,
        "not-a-date",
        42,
    ]
    buckets = charts.bucket_by_day(ts)
    assert buckets == [("2026-01-01", 2), ("2026-01-02", 1)]


def test_timeline_chart_empty_input_renders_nothing():
    assert charts.timeline_chart([]) == ""


def test_timeline_chart_renders_bars_for_each_bucket():
    buckets = [("2026-01-01", 3), ("2026-01-02", 7)]
    svg = charts.timeline_chart(buckets)
    assert "<svg" in svg
    assert "2026-01-01" in svg and "2026-01-02" in svg


def test_timeline_chart_axis_maximum_is_never_clipped_off_the_left():
    """Regression: the y-axis maximum was end-anchored in a fixed 34-unit gutter, so a
    six-figure peak lost its leading digit — a wrong-looking axis maximum in a legal
    report is worse than a wrong-looking bar."""
    svg = charts.timeline_chart([("2026-01-01", 1_234_567), ("2026-01-02", 5)])
    import re

    m = re.search(r'<text x="([\d.]+)"[^>]*text-anchor="end">([\d,]+)</text>', svg)
    assert m, "the axis maximum label must be present"
    x_end, label = float(m.group(1)), m.group(2)
    assert label == "1,234,567"
    # The label is drawn leftwards from x_end; it must start at or after x=0.
    assert x_end - charts._text_w(label, 10) >= 0


def test_timeline_chart_single_bucket_does_not_read_as_a_range():
    svg = charts.timeline_chart([("2026-01-01", 4)])
    assert svg.count("2026-01-01") == 2, "one date in the <title> and one centred axis label"
    assert 'text-anchor="middle"' in svg


def test_donut_chart_folds_excess_segments_and_says_so():
    segs = [(f"tier{i}", 12 - i, "#123456") for i in range(12)]
    out = charts.donut_chart(segs, max_segments=5)
    assert "OTHER (8 tiers)" in out
    assert "folded, not" in out
    assert "tier11" in out, "folded tiers must still be named, never silently dropped"


def test_donut_chart_shrinks_centre_text_for_huge_totals():
    small = charts.donut_chart([("live", 3157, "#1c7d3f")])
    huge = charts.donut_chart([("live", 987654321, "#1c7d3f")])
    import re

    def _total_fs(out: str) -> float:
        return float(re.search(r'font-size="([\d.]+)" font-weight="700"', out).group(1))

    assert _total_fs(small) == 20
    assert _total_fs(huge) < 20, "a huge total must shrink rather than collide with the ring"


def test_timeline_chart_groups_beyond_max_bars_without_dropping_rows():
    buckets = [(f"2026-01-{i:02d}", 1) for i in range(1, 31)]  # 30 days, 1 each
    svg = charts.timeline_chart(buckets, max_bars=10)
    assert "grouped into" in svg
    assert "none were dropped" in svg
    # total count must be conserved across the grouping
    import re

    totals = re.findall(r"<title>[^:]+: ([\d,]+)</title>", svg)
    assert sum(int(t.replace(",", "")) for t in totals) == 30
