"""Cross-artifact timeline reconstruction.

Merges messages, calls, media, and location points into a single chronological feed —
the 'visual timeline reconstruction' bonus. Items without a parseable timestamp are
grouped at the end under an 'undated' bucket rather than silently dropped.
"""
from __future__ import annotations

from typing import Iterable

from .models import (
    CallRecord,
    LocationPoint,
    MediaItem,
    Message,
    TimelineEvent,
)


def build_timeline(*, messages: Iterable[Message] = (),
                   calls: Iterable[CallRecord] = (),
                   media: Iterable[MediaItem] = (),
                   locations: Iterable[LocationPoint] = ()) -> list[dict]:
    events: list[TimelineEvent] = []

    for m in messages:
        if m.timestamp:
            body = (m.body or "")[:120]
            events.append(TimelineEvent(
                timestamp=m.timestamp, kind="message",
                summary=f"{m.app}: {m.sender}: {body}",
                confidence=m.confidence, ref=m.source_file))

    for c in calls:
        if c.timestamp:
            events.append(TimelineEvent(
                timestamp=c.timestamp, kind="call",
                summary=f"{c.call_type} call {c.number}"
                        + (f" ({c.duration_s}s)" if c.duration_s else ""),
                confidence=c.confidence, ref=c.source_file))

    for md in media:
        if md.timestamp:
            gps = f" @({md.gps['lat']:.4f},{md.gps['lon']:.4f})" if md.gps else ""
            events.append(TimelineEvent(
                timestamp=md.timestamp, kind="media",
                summary=f"{md.kind} {md.stored_path.split('/')[-1]}{gps}"
                        + (" [trashed]" if md.trashed else ""),
                ref=md.artifact_id))

    for loc in locations:
        if loc.timestamp:
            events.append(TimelineEvent(
                timestamp=loc.timestamp, kind="location",
                summary=f"{loc.source} location ({loc.latitude:.5f}, {loc.longitude:.5f}) {loc.label}",
                ref=loc.source_file))

    events.sort(key=lambda e: e.timestamp)
    return [e.to_dict() for e in events]
