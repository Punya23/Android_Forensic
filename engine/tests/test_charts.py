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
    svg = charts.bar_chart([("A", 10), ("B", 5)])
    assert "<svg" in svg
    assert "A" in svg and "B" in svg
    # Larger value must produce a wider bar than the smaller one.
    import re

    rects = re.findall(r'<rect x="190" y="[\d.]+" width="([\d.]+)"[^>]*fill="#7a2e12"', svg)
    assert len(rects) == 2
    assert float(rects[0]) > float(rects[1])


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


def test_timeline_chart_groups_beyond_max_bars_without_dropping_rows():
    buckets = [(f"2026-01-{i:02d}", 1) for i in range(1, 31)]  # 30 days, 1 each
    svg = charts.timeline_chart(buckets, max_bars=10)
    assert "grouped into" in svg
    assert "none were dropped" in svg
    # total count must be conserved across the grouping
    import re

    totals = re.findall(r"<title>[^:]+: ([\d,]+)</title>", svg)
    assert sum(int(t.replace(",", "")) for t in totals) == 30
