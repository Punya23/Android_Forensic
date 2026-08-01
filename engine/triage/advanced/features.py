"""Advanced forensic analysis features — social graph, pattern detection, anomalies.

All numeric thresholds are exposed as module-level constants (prefixed
``CFG_``) so callers can override them per-case without subclassing.

Example::

    from triage.advanced.features import AdvancedForensicFeatures, CFG_PEAK_HOURS
    CFG_PEAK_HOURS = (6, 23)   # extend quiet hours window

    aff = AdvancedForensicFeatures()
    result = aff.detect_anomalies(messages)
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..models import Contact, Message
from ..config import Confidence

# ---------------------------------------------------------------------------
# Configurable constants — tweak per-case; never hard-code in method bodies
# ---------------------------------------------------------------------------

# Hours of day considered "peak" communication hours (inclusive).
CFG_PEAK_HOURS: Tuple[int, int] = (8, 22)

# Hours considered "quiet" (night time) — communication here is anomalous.
CFG_QUIET_HOURS: Tuple[int, int] = (0, 5)

# Minimum messages exchanged with a contact to include in the social graph.
CFG_MIN_GRAPH_EDGE_WEIGHT: int = 1

# Message gap (seconds) that is flagged as a "burst" end.
CFG_BURST_GAP_SECONDS: int = 300  # 5 minutes

# Minimum burst size to include in pattern output.
CFG_MIN_BURST_SIZE: int = 3

# Response time threshold (seconds) below which a reply is "fast".
CFG_FAST_RESPONSE_THRESHOLD_S: int = 60

# Response time threshold above which a reply is "slow".
CFG_SLOW_RESPONSE_THRESHOLD_S: int = 3600

# Minimum number of messages to compute meaningful statistics.
CFG_MIN_MESSAGES_FOR_STATS: int = 5

# Z-score threshold for hourly volume anomaly detection.
CFG_ANOMALY_ZSCORE_THRESHOLD: float = 2.5

# Communication channel "switch" — within this window (seconds) switching
# between different senders counts as a switch event.
CFG_CHANNEL_SWITCH_WINDOW_S: int = 600  # 10 minutes

# Maximum contacts to list in the "top contacts" report field.
CFG_TOP_CONTACTS_N: int = 10

# Timeline bucket granularity (hours) for activity heatmap.
CFG_TIMELINE_BUCKET_HOURS: int = 1


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 string to a timezone-aware datetime (UTC)."""
    if not ts:
        return None
    try:
        # Python 3.7+ fromisoformat doesn't handle trailing 'Z'.
        ts_clean = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts_clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _safe_mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _safe_std(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = _safe_mean(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return variance**0.5


def _hour_of(dt: datetime) -> int:
    return dt.hour


def _contacts_lookup(contacts: List[Contact]) -> Dict[str, str]:
    """Build phone → name lookup from a contacts list."""
    lookup: Dict[str, str] = {}
    for c in contacts:
        if c.number and c.name:
            digits = re.sub(r"\D", "", c.number)
            if digits:
                lookup[digits] = c.name
    return lookup


def _resolve_sender(sender: str, lookup: Dict[str, str]) -> str:
    digits = re.sub(r"\D", "", sender)
    return lookup.get(digits, sender)


def _sorted_messages(messages: List[Message]) -> List[Message]:
    """Return messages sorted by timestamp (oldest first), skipping undated."""
    timed = [(m, _parse_iso(m.timestamp)) for m in messages if m.timestamp]
    timed.sort(key=lambda x: x[1])  # type: ignore[arg-type]
    return [m for m, _ in timed]


# ---------------------------------------------------------------------------
# AdvancedForensicFeatures class
# ---------------------------------------------------------------------------


class AdvancedForensicFeatures:
    """Provides advanced forensic analysis beyond raw message extraction.

    All methods are stateless and accept only their documented parameters;
    no state is stored between calls (safe for concurrent pipelines).
    """

    # -----------------------------------------------------------------------
    def analyze_social_graph(
        self,
        messages: List[Message],
        contacts: Optional[List[Contact]] = None,
        min_edge_weight: int = CFG_MIN_GRAPH_EDGE_WEIGHT,
    ) -> Dict[str, Any]:
        """Build a social network graph from message data.

        Parameters
        ----------
        messages:
            All parsed messages.
        contacts:
            Optional contact list for name resolution.
        min_edge_weight:
            Minimum message count to include an edge in the graph.

        Returns
        -------
        dict
            Keys: ``nodes``, ``edges``, ``stats``, ``top_contacts``.
        """
        lookup = _contacts_lookup(contacts or [])
        edge_counts: Dict[Tuple[str, str], int] = defaultdict(int)
        node_messages: Dict[str, int] = defaultdict(int)
        node_sent: Dict[str, int] = defaultdict(int)
        node_received: Dict[str, int] = defaultdict(int)

        for m in messages:
            sender = _resolve_sender(m.sender, lookup)
            node_messages[sender] += 1
            if m.direction == "outgoing":
                node_sent["SUBJECT"] += 1
                node_received[sender] += 1
                edge_counts[("SUBJECT", sender)] += 1
            elif m.direction == "incoming":
                node_sent[sender] += 1
                node_received["SUBJECT"] += 1
                edge_counts[(sender, "SUBJECT")] += 1

        edges = [
            {
                "source": src,
                "target": tgt,
                "weight": w,
                "direction": "outgoing" if src == "SUBJECT" else "incoming",
            }
            for (src, tgt), w in edge_counts.items()
            if w >= min_edge_weight
        ]

        nodes = [
            {
                "id": name,
                "messages": node_messages.get(name, 0),
                "sent": node_sent.get(name, 0),
                "received": node_received.get(name, 0),
            }
            for name in set(node_messages) | {"SUBJECT"}
        ]

        top_contacts = sorted(
            node_messages.items(),
            key=lambda x: x[1],
            reverse=True,
        )[:CFG_TOP_CONTACTS_N]

        return {
            "nodes": nodes,
            "edges": edges,
            "stats": {
                "unique_contacts": len(node_messages),
                "total_edges": len(edges),
                "most_active": top_contacts[0][0] if top_contacts else None,
            },
            "top_contacts": [{"name": n, "messages": c} for n, c in top_contacts],
        }

    # -----------------------------------------------------------------------
    def detect_communication_patterns(
        self,
        messages: List[Message],
        burst_gap_s: int = CFG_BURST_GAP_SECONDS,
        min_burst_size: int = CFG_MIN_BURST_SIZE,
        fast_response_s: int = CFG_FAST_RESPONSE_THRESHOLD_S,
        slow_response_s: int = CFG_SLOW_RESPONSE_THRESHOLD_S,
    ) -> Dict[str, Any]:
        """Detect communication patterns: bursts, response times, active hours.

        Parameters
        ----------
        messages:
            All parsed messages (need not be pre-sorted).
        burst_gap_s:
            Gap in seconds that ends a burst.
        min_burst_size:
            Minimum messages to qualify as a burst.
        fast_response_s:
            Seconds below which a response is "fast".
        slow_response_s:
            Seconds above which a response is "slow".

        Returns
        -------
        dict
            Keys: ``bursts``, ``response_times``, ``hourly_distribution``,
            ``daily_distribution``, ``peak_hour``, ``quiet_hours_activity``.
        """
        sorted_msgs = _sorted_messages(messages)
        if not sorted_msgs:
            return {
                "bursts": [],
                "response_times": {},
                "hourly_distribution": {},
                "daily_distribution": {},
                "peak_hour": None,
                "quiet_hours_activity": 0,
            }

        # Burst detection.
        bursts: List[Dict[str, Any]] = []
        current_burst: List[Message] = [sorted_msgs[0]]
        prev_dt = _parse_iso(sorted_msgs[0].timestamp)

        for m in sorted_msgs[1:]:
            dt = _parse_iso(m.timestamp)
            if dt and prev_dt:
                gap = (dt - prev_dt).total_seconds()
                if gap <= burst_gap_s:
                    current_burst.append(m)
                else:
                    if len(current_burst) >= min_burst_size:
                        bursts.append(
                            {
                                "start": current_burst[0].timestamp,
                                "end": current_burst[-1].timestamp,
                                "count": len(current_burst),
                                "senders": list({x.sender for x in current_burst}),
                            }
                        )
                    current_burst = [m]
                prev_dt = dt

        if len(current_burst) >= min_burst_size:
            bursts.append(
                {
                    "start": current_burst[0].timestamp,
                    "end": current_burst[-1].timestamp,
                    "count": len(current_burst),
                    "senders": list({x.sender for x in current_burst}),
                }
            )

        # Response time analysis (incoming → outgoing pairs).
        fast = slow = medium = 0
        response_times: List[float] = []
        prev_incoming: Optional[datetime] = None
        for m in sorted_msgs:
            dt = _parse_iso(m.timestamp)
            if not dt:
                continue
            if m.direction == "incoming":
                prev_incoming = dt
            elif m.direction == "outgoing" and prev_incoming:
                delta = (dt - prev_incoming).total_seconds()
                if delta >= 0:
                    response_times.append(delta)
                    if delta < fast_response_s:
                        fast += 1
                    elif delta > slow_response_s:
                        slow += 1
                    else:
                        medium += 1
                prev_incoming = None

        # Hourly distribution.
        hourly: Dict[int, int] = defaultdict(int)
        daily: Dict[str, int] = defaultdict(int)
        quiet_activity = 0
        for m in sorted_msgs:
            dt = _parse_iso(m.timestamp)
            if dt:
                h = dt.hour
                hourly[h] += 1
                daily[dt.strftime("%A")] += 1
                if CFG_QUIET_HOURS[0] <= h <= CFG_QUIET_HOURS[1]:
                    quiet_activity += 1

        peak_hour = max(hourly, key=hourly.get) if hourly else None  # type: ignore[arg-type]

        return {
            "bursts": bursts,
            "response_times": {
                "fast": fast,
                "medium": medium,
                "slow": slow,
                "mean_s": round(_safe_mean(response_times), 1),
                "samples": len(response_times),
            },
            "hourly_distribution": dict(hourly),
            "daily_distribution": dict(daily),
            "peak_hour": peak_hour,
            "quiet_hours_activity": quiet_activity,
        }

    # -----------------------------------------------------------------------
    def analyze_timeline(
        self,
        messages: List[Message],
        contacts: Optional[List[Contact]] = None,
        bucket_hours: int = CFG_TIMELINE_BUCKET_HOURS,
    ) -> Dict[str, Any]:
        """Reconstruct a chronological activity timeline.

        Parameters
        ----------
        messages:
            All parsed messages.
        contacts:
            Optional contact list for name resolution.
        bucket_hours:
            Granularity (in hours) for the activity heatmap.

        Returns
        -------
        dict
            Keys: ``events``, ``activity_heatmap``, ``date_range``,
            ``total_days_active``, ``avg_messages_per_active_day``.
        """
        lookup = _contacts_lookup(contacts or [])
        sorted_msgs = _sorted_messages(messages)

        events: List[Dict[str, Any]] = []
        activity_by_bucket: Dict[str, int] = defaultdict(int)
        dates_active: set = set()

        for m in sorted_msgs:
            dt = _parse_iso(m.timestamp)
            if not dt:
                continue
            sender = _resolve_sender(m.sender, lookup)
            events.append(
                {
                    "timestamp": m.timestamp,
                    "sender": sender,
                    "direction": m.direction,
                    "confidence": m.confidence.value,
                    "has_flags": bool(m.flags),
                }
            )
            bucket_start = dt.replace(
                minute=0,
                second=0,
                microsecond=0,
                hour=(dt.hour // bucket_hours) * bucket_hours,
            )
            activity_by_bucket[bucket_start.isoformat()] += 1
            dates_active.add(dt.date().isoformat())

        start = events[0]["timestamp"] if events else None
        end = events[-1]["timestamp"] if events else None
        n_days = len(dates_active)
        avg_per_day = round(len(events) / n_days, 1) if n_days else 0.0

        return {
            "events": events,
            "activity_heatmap": dict(activity_by_bucket),
            "date_range": {"start": start, "end": end},
            "total_days_active": n_days,
            "avg_messages_per_active_day": avg_per_day,
        }

    # -----------------------------------------------------------------------
    def detect_anomalies(
        self,
        messages: List[Message],
        zscore_threshold: float = CFG_ANOMALY_ZSCORE_THRESHOLD,
        switch_window_s: int = CFG_CHANNEL_SWITCH_WINDOW_S,
    ) -> Dict[str, Any]:
        """Detect statistically anomalous communication events.

        Detects:
        - Hours with unusually high message volume (z-score).
        - Quiet-hours activity (potential covert communication).
        - Rapid sender-switch events (multiple contacts within a short window).
        - Confidence downgrade events (carved/recovered rows amid live data).

        Parameters
        ----------
        messages:
            All parsed messages.
        zscore_threshold:
            Hourly volume z-score above which an hour is flagged.
        switch_window_s:
            Window (seconds) within which rapid sender switching is counted.

        Returns
        -------
        dict
            Keys: ``volume_spikes``, ``quiet_hours_events``,
            ``rapid_switches``, ``confidence_downgrades``, ``summary``.
        """
        sorted_msgs = _sorted_messages(messages)
        hourly: Dict[int, int] = defaultdict(int)
        hourly_ts: Dict[int, List[str]] = defaultdict(list)

        quiet_events: List[Dict[str, Any]] = []
        confidence_downgrades: List[Dict[str, Any]] = []

        for m in sorted_msgs:
            dt = _parse_iso(m.timestamp)
            if not dt:
                continue
            h = dt.hour
            hourly[h] += 1
            hourly_ts[h].append(m.timestamp or "")

            if CFG_QUIET_HOURS[0] <= h <= CFG_QUIET_HOURS[1]:
                quiet_events.append(
                    {
                        "timestamp": m.timestamp,
                        "sender": m.sender,
                        "direction": m.direction,
                    }
                )

            if m.confidence in (
                Confidence.CARVED_PARTIAL,
                Confidence.DELETION_DETECTED,
            ):
                confidence_downgrades.append(
                    {
                        "timestamp": m.timestamp,
                        "confidence": m.confidence.value,
                        "provenance": m.provenance,
                    }
                )

        # Volume spike detection via z-score.
        counts = list(hourly.values())
        mean_v = _safe_mean([float(c) for c in counts])
        std_v = _safe_std([float(c) for c in counts])
        volume_spikes: List[Dict[str, Any]] = []
        for h, count in hourly.items():
            z = (count - mean_v) / std_v if std_v > 0 else 0.0
            if z >= zscore_threshold:
                volume_spikes.append(
                    {
                        "hour": h,
                        "count": count,
                        "zscore": round(z, 2),
                        "timestamps": hourly_ts[h][:5],  # sample
                    }
                )

        # Rapid sender-switch detection.
        rapid_switches: List[Dict[str, Any]] = []
        window_msgs: List[Message] = []
        window_start_dt: Optional[datetime] = None

        for m in sorted_msgs:
            dt = _parse_iso(m.timestamp)
            if not dt:
                continue
            if window_start_dt is None:
                window_start_dt = dt
                window_msgs = [m]
                continue
            elapsed = (dt - window_start_dt).total_seconds()
            if elapsed <= switch_window_s:
                window_msgs.append(m)
            else:
                senders = {x.sender for x in window_msgs}
                if len(senders) > 2:
                    rapid_switches.append(
                        {
                            "window_start": window_start_dt.isoformat(),
                            "window_end": dt.isoformat(),
                            "unique_senders": len(senders),
                            "message_count": len(window_msgs),
                            "senders": list(senders)[:5],
                        }
                    )
                window_start_dt = dt
                window_msgs = [m]

        return {
            "volume_spikes": volume_spikes,
            "quiet_hours_events": quiet_events,
            "rapid_switches": rapid_switches,
            "confidence_downgrades": confidence_downgrades,
            "summary": {
                "total_spikes": len(volume_spikes),
                "quiet_hours_total": len(quiet_events),
                "rapid_switch_events": len(rapid_switches),
                "confidence_downgrade_n": len(confidence_downgrades),
            },
        }

    # -----------------------------------------------------------------------
    def calculate_recovery_metrics(
        self,
        recovered_messages: List[Message],
    ) -> Dict[str, Any]:
        """Compute quality metrics for the recovered/carved message set.

        Parameters
        ----------
        recovered_messages:
            Messages with confidence ≠ LIVE (the carved / recovered subset).

        Returns
        -------
        dict
            Keys: ``total``, ``by_confidence``, ``with_timestamp``,
            ``with_sender``, ``with_body_gt_n``, ``recovery_rate_estimate``.
        """

        by_conf: Dict[str, int] = defaultdict(int)
        with_ts = 0
        with_sender = 0
        with_body = 0

        for m in recovered_messages:
            by_conf[m.confidence.value] += 1
            if m.timestamp:
                with_ts += 1
            if m.sender and m.sender not in ("<recovered>", "<carved>", "<system>"):
                with_sender += 1
            if m.body and len(m.body.strip()) > CFG_MIN_MESSAGES_FOR_STATS:
                with_body += 1

        total = len(recovered_messages)
        completeness = round(with_body / total * 100, 1) if total else 0.0

        return {
            "total": total,
            "by_confidence": dict(by_conf),
            "with_timestamp": with_ts,
            "with_identified_sender": with_sender,
            "with_substantive_body": with_body,
            "body_completeness_pct": completeness,
            "recovery_rate_estimate": completeness,
        }

    # -----------------------------------------------------------------------
    def generate_advanced_report(
        self,
        messages: List[Message],
        contacts: Optional[List[Contact]] = None,
    ) -> Dict[str, Any]:
        """Generate a unified advanced analysis report.

        Runs all five analysis methods and combines their output into a single
        dict suitable for ``case.write_derived("advanced", result)``.

        Parameters
        ----------
        messages:
            All messages (live + recovered).
        contacts:
            Optional contact list for name resolution.

        Returns
        -------
        dict
            Combined result from all analysis methods.
        """
        live_msgs = [m for m in messages if m.confidence.value == "live"]
        recovered = [m for m in messages if m.confidence.value != "live"]

        return {
            "social_graph": self.analyze_social_graph(messages, contacts),
            "communication_patterns": self.detect_communication_patterns(messages),
            "timeline": self.analyze_timeline(messages, contacts),
            "anomalies": self.detect_anomalies(messages),
            "recovery_metrics": self.calculate_recovery_metrics(recovered),
            "meta": {
                "total_messages": len(messages),
                "live_messages": len(live_msgs),
                "recovered_messages": len(recovered),
            },
        }


# ---------------------------------------------------------------------------
# Module-level convenience function
# ---------------------------------------------------------------------------


def run_advanced_analysis(
    case_dir: Path,
    messages: List[Message],
    contacts: Optional[List[Contact]] = None,
    features_instance: Optional[AdvancedForensicFeatures] = None,
) -> Dict[str, Any]:
    """Run the full advanced analysis suite and return results.

    Parameters
    ----------
    case_dir:
        Root of the case folder (used for provenance logging; not read/written
        by this function directly — the caller is responsible for persistence).
    messages:
        All messages from the acquisition pipeline.
    contacts:
        Optional contact list.
    features_instance:
        Optional pre-constructed :class:`AdvancedForensicFeatures` instance.
        Defaults to a fresh instance if not supplied.

    Returns
    -------
    dict
        Full advanced analysis report.
    """
    aff = features_instance or AdvancedForensicFeatures()
    report = aff.generate_advanced_report(messages, contacts)
    report["case_dir"] = str(case_dir)
    return report
