"""Cross-artifact contradiction detection.

Flags a message's *claim* against an independent artifact that can confirm or
contradict it — never against another guess. Two checks are wired into the pipeline:

    * :func:`check_message_vs_call` — a message claiming the phone was off/unreachable
      against a call log entry that required the phone to be on, in the same window.
    * :func:`check_message_vs_home` — a message claiming to be "at home" against the
      device's own *inferred* home location (a night-hours GPS cluster with a stated
      confidence, from :mod:`.place_identification`) — not an arbitrary nearby GPS
      point, which proves nothing about where "home" actually is.

Every result is a *candidate* contradiction, not a verdict: ``requires_verification``
travels with each one, matching the confidence discipline the rest of this codebase
uses for every AI-surfaced lead (see ``triage/intel/analysis.py``). None of this
infers guilt — a "phone was off" claim during a missed call it never rang for is
still consistent; a claimed "at home" message from someone who was, in fact, home the
first time the device was ever placed there is still consistent. The check only
surfaces genuine cross-artifact tension for a human to weigh.

**What was deliberately left out.** An earlier version of ``check_message_vs_location``
flagged *any* message containing "at home" that fell within five minutes of *any* GPS
fix at all — with no comparison to where home actually is, and a hardcoded "HIGH"
severity regardless. That is not a contradiction check; it is a keyword-proximity flag
mislabelled as one, and it would have fired on a truthful "at home" message exactly as
often as a false one. It has been replaced with :func:`check_message_vs_home`, which
only fires when the device's own inferred home cluster exists and the claim is
genuinely far from it. ``check_photo_vs_message`` and ``check_location_vs_timeline``
were unimplemented stubs (``return contradictions`` with no logic) and have been
removed rather than kept as dead surface.
"""

from __future__ import annotations

import datetime
import html
import math
from typing import Any, Dict, List, Optional

#: A call log entry with one of these types required the phone to be powered on and
#: network-registered at the time it was logged — the minimum bar for "the phone was
#: not off". An entry with no discernible type is not used as evidence either way.
_PHONE_WAS_ACTIVE_CALL_TYPES = {"incoming", "outgoing", "answered"}

#: Fixed phrases claiming the phone was unreachable. Matched as a whole phrase (not
#: single keywords like "off" or "died") to keep the false-positive rate low — "left
#: it at home" or "battery is dying" are common, unremarkable phrasing that a looser
#: match would flag on almost every device.
_UNREACHABLE_PHRASES = (
    "phone was off",
    "phone is off",
    "my phone was off",
    "was sleeping",
    "battery died",
    "no network",
    "no signal",
)

#: Fixed phrases claiming to be at the device's own inferred home location.
_AT_HOME_PHRASES = ("at home", "i am at home", "i'm at home", "reached home", "back home")

#: A contradiction needs the claimed location to be genuinely far from home, not a GPS
#: fix that is merely imprecise. 1.5 km is well outside normal GPS/network-location
#: jitter (tens to a few hundred metres) and inside "obviously a different place".
_HOME_CONTRADICTION_KM = 1.5

#: Minimum place-identification confidence before "home" is trusted enough to check
#: a claim against. A one- or two-point cluster is not a confident home inference.
_MIN_HOME_CONFIDENCE = 0.4


def parse_iso(ts: Optional[str]) -> Optional[datetime.datetime]:
    """Parse an ISO-8601 timestamp, always returning a timezone-AWARE datetime.

    Every timestamp in this pipeline is UTC by convention, but not every writer
    stamps the 'Z' suffix that makes that explicit — a naive
    ``2026-07-06T21:00:04`` and an aware ``2025-07-06T18:28:20Z`` show up side by
    side in the same case's own findings. Subtracting a naive datetime from an aware
    one raises ``TypeError`` rather than a wrong answer, which is safer than silently
    comparing the wrong thing — but it means every caller doing datetime arithmetic
    here has to get a normalised, comparable value or every subtraction below is one
    mixed-format pair away from crashing the whole check. A naive result is assumed
    UTC, matching the rest of this codebase's timestamp convention.
    """
    if not ts:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _find_matched_phrase(body_lower: str, phrases: tuple) -> Optional[str]:
    for phrase in phrases:
        if phrase in body_lower:
            return phrase
    return None


def check_message_vs_call(
    messages: List[Dict[str, Any]], calls: List[Dict[str, Any]], window_s: int = 1800
) -> List[Dict[str, Any]]:
    """A message claiming the phone was unreachable, near a call that needed it on.

    Only calls of a type that required an active, network-registered handset count as
    contradicting evidence (:data:`_PHONE_WAS_ACTIVE_CALL_TYPES`) — a call log entry
    with no usable ``call_type`` is skipped rather than assumed to qualify.
    """
    out: List[Dict[str, Any]] = []
    call_points = [
        (dt, c)
        for c in calls
        for dt in [parse_iso(c.get("timestamp"))]
        if dt is not None and str(c.get("call_type", "")).lower() in _PHONE_WAS_ACTIVE_CALL_TYPES
    ]
    for msg in messages:
        msg_dt = parse_iso(msg.get("timestamp"))
        body = str(msg.get("body") or "")
        if not msg_dt or not body:
            continue
        phrase = _find_matched_phrase(body.lower(), _UNREACHABLE_PHRASES)
        if not phrase:
            continue
        nearest = min(
            (
                (abs((dt - msg_dt).total_seconds()), dt, c)
                for dt, c in call_points
                if abs((dt - msg_dt).total_seconds()) <= window_s
            ),
            default=None,
            key=lambda t: t[0],
        )
        if nearest is None:
            continue
        gap_s, call_dt, call = nearest
        out.append(
            {
                "type": "message_vs_call",
                "severity": "medium",
                "requires_verification": True,
                "matched_phrase": phrase,
                "message_body": body,
                "message_timestamp": msg.get("timestamp"),
                "message_source_file": msg.get("source_file", ""),
                "call_timestamp": call.get("timestamp"),
                "call_type": call.get("call_type"),
                "call_number": call.get("number") or call.get("name"),
                "call_source_file": call.get("source_file", ""),
                "gap_seconds": round(gap_s),
                "rationale": (
                    f"Message says \"{phrase}\" at {msg.get('timestamp')}, but a "
                    f"{call.get('call_type', 'call')} call is logged at "
                    f"{call.get('timestamp')} ({round(gap_s)}s apart) — a call of that "
                    "type requires the handset to be powered on and registered on the "
                    "network. Candidate contradiction; verify against both source "
                    "artifacts before relying on it."
                ),
            }
        )
    return out


def check_message_vs_home(
    messages: List[Dict[str, Any]],
    locations: List[Dict[str, Any]],
    home_place: Optional[Dict[str, Any]],
    window_s: int = 900,
) -> List[Dict[str, Any]]:
    """A message claiming to be "at home" against the device's own inferred home.

    ``home_place`` is the ``'home'`` entry from
    :func:`triage.forensics.place_identification.identify_places_from_locations` — a
    night-hours GPS cluster with a stated confidence, computed from this device's own
    location history. Without a confident home inference (``None``, or below
    :data:`_MIN_HOME_CONFIDENCE`), this returns no results rather than guessing: there
    is nothing honest to compare the claim against.
    """
    if not home_place or (home_place.get("confidence") or 0) < _MIN_HOME_CONFIDENCE:
        return []
    center = home_place.get("center") or {}
    home_lat, home_lon = center.get("lat"), center.get("lon")
    if home_lat is None or home_lon is None:
        return []

    out: List[Dict[str, Any]] = []
    loc_points = [
        (dt, loc)
        for loc in locations
        for dt in [parse_iso(loc.get("timestamp"))]
        if dt is not None and loc.get("latitude") is not None and loc.get("longitude") is not None
    ]
    for msg in messages:
        msg_dt = parse_iso(msg.get("timestamp"))
        body = str(msg.get("body") or "")
        if not msg_dt or not body:
            continue
        phrase = _find_matched_phrase(body.lower(), _AT_HOME_PHRASES)
        if not phrase:
            continue
        nearest = min(
            (
                (abs((dt - msg_dt).total_seconds()), dt, loc)
                for dt, loc in loc_points
                if abs((dt - msg_dt).total_seconds()) <= window_s
            ),
            default=None,
            key=lambda t: t[0],
        )
        if nearest is None:
            continue
        gap_s, loc_dt, loc = nearest
        dist_km = haversine_km(home_lat, home_lon, loc["latitude"], loc["longitude"])
        if dist_km < _HOME_CONTRADICTION_KM:
            continue  # consistent with the claim — not a contradiction
        out.append(
            {
                "type": "message_vs_home",
                "severity": "medium",
                "requires_verification": True,
                "matched_phrase": phrase,
                "message_body": body,
                "message_timestamp": msg.get("timestamp"),
                "message_source_file": msg.get("source_file", ""),
                "location_timestamp": loc.get("timestamp"),
                "location_source_file": loc.get("source_file", ""),
                "distance_from_home_km": round(dist_km, 2),
                "home_confidence": round(float(home_place.get("confidence") or 0), 2),
                "rationale": (
                    f"Message says \"{phrase}\" at {msg.get('timestamp')}, but the "
                    f"nearest location fix ({round(gap_s)}s away) is "
                    f"{round(dist_km, 2)} km from this device's own inferred home "
                    f"location (confidence {round(float(home_place.get('confidence') or 0), 2)}, "
                    "computed from its night-time GPS clustering). Candidate "
                    "contradiction; verify against both source artifacts before "
                    "relying on it."
                ),
            }
        )
    return out


def detect_contradictions(
    messages: List[Dict[str, Any]],
    calls: List[Dict[str, Any]],
    locations: List[Dict[str, Any]],
    home_place: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Run every wired contradiction check and return the combined, unsorted list."""
    return check_message_vs_call(messages, calls) + check_message_vs_home(
        messages, locations, home_place
    )


def generate_contradiction_report(contradictions: List[Dict[str, Any]]) -> str:
    """HTML fragment for the triage report."""
    out = ["<div class='contradiction-report'>", "<h2>Candidate Contradictions</h2>"]
    if not contradictions:
        out.append("<p>No candidate contradictions detected.</p>")
    else:
        out.append(
            "<table><tr><th>Type</th><th>Message</th><th>Contradicting artifact</th>"
            "<th>Rationale</th></tr>"
        )
        for c in contradictions:
            other = (
                f"call {html.escape(str(c.get('call_timestamp', '')))}"
                if c.get("type") == "message_vs_call"
                else f"location fix {html.escape(str(c.get('location_timestamp', '')))} "
                f"({c.get('distance_from_home_km', '?')} km from home)"
            )
            out.append(
                f"<tr><td>{html.escape(c.get('type', ''))}</td>"
                f"<td>{html.escape(c.get('message_body', ''))}</td>"
                f"<td>{other}</td>"
                f"<td>{html.escape(c.get('rationale', ''))}</td></tr>"
            )
        out.append("</table>")
    out.append("</div>")
    return "\n".join(out)
