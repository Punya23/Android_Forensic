"""Advanced forensic analysis — public API.

Import from here for a stable, clean external interface::

    from engine.triage.advanced import (
        AdvancedForensicFeatures,
        run_advanced_analysis,
    )
"""

from __future__ import annotations

from .features import (
    AdvancedForensicFeatures,
    run_advanced_analysis,
    # Configurable constants — expose so callers can tune them.
    CFG_PEAK_HOURS,
    CFG_QUIET_HOURS,
    CFG_MIN_GRAPH_EDGE_WEIGHT,
    CFG_BURST_GAP_SECONDS,
    CFG_MIN_BURST_SIZE,
    CFG_FAST_RESPONSE_THRESHOLD_S,
    CFG_SLOW_RESPONSE_THRESHOLD_S,
    CFG_MIN_MESSAGES_FOR_STATS,
    CFG_ANOMALY_ZSCORE_THRESHOLD,
    CFG_CHANNEL_SWITCH_WINDOW_S,
    CFG_TOP_CONTACTS_N,
    CFG_TIMELINE_BUCKET_HOURS,
)

__all__ = [
    "AdvancedForensicFeatures",
    "run_advanced_analysis",
    # Config constants
    "CFG_PEAK_HOURS",
    "CFG_QUIET_HOURS",
    "CFG_MIN_GRAPH_EDGE_WEIGHT",
    "CFG_BURST_GAP_SECONDS",
    "CFG_MIN_BURST_SIZE",
    "CFG_FAST_RESPONSE_THRESHOLD_S",
    "CFG_SLOW_RESPONSE_THRESHOLD_S",
    "CFG_MIN_MESSAGES_FOR_STATS",
    "CFG_ANOMALY_ZSCORE_THRESHOLD",
    "CFG_CHANNEL_SWITCH_WINDOW_S",
    "CFG_TOP_CONTACTS_N",
    "CFG_TIMELINE_BUCKET_HOURS",
]
