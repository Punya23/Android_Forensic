"""Cross-artifact timeline reconstruction.

Merges messages, calls, media, and location points into a single chronological feed —
the 'visual timeline reconstruction' bonus. Items without a parseable timestamp are
grouped at the end under an 'undated' bucket rather than silently dropped.

New in v2: ``telegram_messages`` and ``telegram_media`` keyword arguments add
``kind="telegram_message"`` and ``kind="telegram_media"`` events, complete with
confidence badges, so deleted Telegram messages appear inline with all other evidence.
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
from .config import Confidence
from .parsers.notification import build_notification_timeline
from .parsers.bluetooth import build_bluetooth_timeline
from .parsers.celltower import build_celltower_timeline
from .parsers.screen_time import build_screen_timeline
from .parsers.google_search import build_search_timeline


def build_bond_timeline_events(bonds: Iterable[dict]) -> list[TimelineEvent]:
    """Adapt root-tier Bluetooth *bond* records (bt_config.conf) to timeline events.

    Kept here rather than in the parser because the bond timestamp is the single most
    over-claimed field in Bluetooth forensics: it records when the pairing record was
    written, NOT when the two devices were connected or near each other. The summary is
    therefore worded as a pairing, and the confidence is never LIVE — a bond record is a
    persisted artefact, not an observation of a live connection.
    """
    events: list[TimelineEvent] = []
    for bond in bonds or ():
        if not isinstance(bond, dict):
            continue
        ts = bond.get("bond_timestamp") or ""
        addr = bond.get("address", "")
        name = bond.get("name") or "(unnamed device)"
        vendor = bond.get("vendor")
        label = f"{name} [{addr}]" + (f" — {vendor}" if vendor else "")
        events.append(
            TimelineEvent(
                timestamp=ts,
                kind="bluetooth_bond",
                summary=(
                    f"Bluetooth pairing record written for {label}. This is a bond/"
                    f"pairing-store timestamp, NOT a connection or co-location time."
                ),
                confidence=Confidence.RECOVERED_VERIFIED,
                ref=addr,
            )
        )
    return events


def build_timeline(
    *,
    messages: Iterable[Message] = (),
    calls: Iterable[CallRecord] = (),
    media: Iterable[MediaItem] = (),
    locations: Iterable[LocationPoint] = (),
    telegram_messages: Iterable[dict] = (),
    telegram_media: Iterable[dict] = (),
    calendar_events: Iterable[dict] = (),
    media_inventory: Iterable[dict] = (),
    notifications: Iterable[dict] = (),
    bluetooth_devices: Iterable[dict] = (),
    cell_towers: Iterable[dict] = (),
    screen_events: Iterable[dict] = (),
    searches: Iterable[dict] = (),
    bluetooth_bonds: Iterable[dict] = (),
) -> list[dict]:
    """Build a sorted, unified timeline from all evidence types.

    Parameters
    ----------
    messages:
        General :class:`Message` objects (WhatsApp, SMS, Signal, …).
    calls:
        :class:`CallRecord` objects.
    media:
        :class:`MediaItem` objects from the media gallery.
    locations:
        :class:`LocationPoint` objects.
    telegram_messages:
        Raw dicts from ``recover_telegram_messages()["messages"]``.  Each dict
        must have at minimum ``"timestamp"`` (ISO-8601 or ``None``),
        ``"body"``, ``"sender"``, and ``"confidence"`` keys.  Events produced
        have ``kind="telegram_message"``.
    telegram_media:
        Raw dicts from the ``telegram_media`` derived dataset, each having
        ``"artifact_id"``, ``"rel_path"``, ``"parent_message_ts"``, and
        ``"confidence"``.  Events produced have ``kind="telegram_media"``.

    Returns
    -------
    List of ``TimelineEvent.to_dict()`` sorted by ``timestamp`` ascending.
    All items without a parseable timestamp are collected in an ``undated``
    sub-list appended to the end of the sorted section (never silently dropped).
    """
    events: list[TimelineEvent] = []
    undated: list[TimelineEvent] = []

    # --- Standard message types ---
    for m in messages:
        body = (m.body or "")[:120]
        ev = TimelineEvent(
            timestamp=m.timestamp or "",
            kind="message",
            summary=f"{m.app}: {m.sender}: {body}",
            confidence=m.confidence,
            ref=m.source_file,
        )
        (events if m.timestamp else undated).append(ev)

    for c in calls:
        ev = TimelineEvent(
            timestamp=c.timestamp or "",
            kind="call",
            summary=f"{c.call_type} call {c.number}"
            + (f" ({c.duration_s}s)" if c.duration_s else ""),
            confidence=c.confidence,
            ref=c.source_file,
        )
        (events if c.timestamp else undated).append(ev)

    for md in media:
        gps = f" @({md.gps['lat']:.4f},{md.gps['lon']:.4f})" if md.gps else ""
        ev = TimelineEvent(
            timestamp=md.timestamp or "",
            kind="media",
            summary=f"{md.kind} {md.stored_path.split('/')[-1]}{gps}"
            + (" [trashed]" if md.trashed else ""),
            ref=md.artifact_id,
        )
        (events if md.timestamp else undated).append(ev)

    for loc in locations:
        ev = TimelineEvent(
            timestamp=loc.timestamp or "",
            kind="location",
            summary=f"{loc.source} location ({loc.latitude:.5f}, {loc.longitude:.5f}) {loc.label}",
            ref=loc.source_file,
        )
        (events if loc.timestamp else undated).append(ev)

    # --- Telegram-specific events ---
    for tg in telegram_messages:
        ts = tg.get("timestamp")
        body = (tg.get("body") or "")[:120]
        sender = tg.get("sender") or "<unknown>"
        conf_val = tg.get("confidence", Confidence.LIVE.value)
        try:
            conf = Confidence(conf_val)
        except ValueError:
            conf = Confidence.CARVED_PARTIAL

        ev = TimelineEvent(
            timestamp=ts or "",
            kind="telegram_message",
            summary=f"Telegram [{conf_val}] {sender}: {body}",
            confidence=conf,
            ref=tg.get("source_file", ""),
        )
        (events if ts else undated).append(ev)

    for tm in telegram_media:
        ts = tm.get("parent_message_ts") or tm.get("timestamp")
        conf_val = tm.get("confidence", Confidence.LIVE.value)
        try:
            conf = Confidence(conf_val)
        except ValueError:
            conf = Confidence.CARVED_PARTIAL

        ev = TimelineEvent(
            timestamp=ts or "",
            kind="telegram_media",
            summary=f"Telegram media: {tm.get('rel_path', tm.get('artifact_id', ''))}",
            confidence=conf,
            ref=tm.get("artifact_id", ""),
        )
        (events if ts else undated).append(ev)

    # --- Calendar events ---
    for ce in calendar_events:
        ts = ce.get("dtstart")
        title = (ce.get("title") or "(no title)")[:120]
        loc = ce.get("location") or ""
        summary = f"Calendar: {title}" + (f" @ {loc}" if loc else "")
        ev = TimelineEvent(
            timestamp=ts or "",
            kind="calendar",
            summary=summary,
            ref=ce.get("source_file", ""),
        )
        (events if ts else undated).append(ev)

    # --- MediaStore inventory (metadata-only catalogue; date_taken preferred) ---
    for mi in media_inventory:
        ts = mi.get("date_taken") or mi.get("date_added")
        name = (
            mi.get("display_name")
            or mi.get("relative_path")
            or str(mi.get("media_id", ""))
        )
        owner = mi.get("owner_app")
        badges = []
        if mi.get("is_trashed"):
            badges.append("trashed")
        if mi.get("is_favorite"):
            badges.append("favorite")
        suffix = f" [{', '.join(badges)}]" if badges else ""
        summary = (
            f"{mi.get('kind', 'media')} {name}"
            + (f" ({owner})" if owner else "")
            + suffix
        )
        ev = TimelineEvent(
            timestamp=ts or "",
            kind="media_inventory",
            summary=summary,
            ref=str(mi.get("media_id", "")),
        )
        (events if ts else undated).append(ev)

    # --- Dumpsys history events ---
    for ev in build_notification_timeline(list(notifications)):
        (events if ev.timestamp else undated).append(ev)

    for ev in build_bluetooth_timeline(list(bluetooth_devices)):
        (events if ev.timestamp else undated).append(ev)

    for ev in build_celltower_timeline(list(cell_towers)):
        (events if ev.timestamp else undated).append(ev)

    # Screen on/off + unlock events and search queries (P1-7). Both are pattern-of-life
    # anchors: a screen-on at 03:14 or a search query is often what places a person at
    # the device at a given moment, so they belong on the same axis as the messages.
    for ev in build_screen_timeline(list(screen_events)):
        (events if ev.timestamp else undated).append(ev)

    for ev in build_search_timeline(list(searches)):
        (events if ev.timestamp else undated).append(ev)

    for ev in build_bond_timeline_events(list(bluetooth_bonds)):
        (events if ev.timestamp else undated).append(ev)

    # Sort timestamped events; append undated at the end.
    events.sort(key=lambda e: e.timestamp)
    all_events = events + undated
    return [e.to_dict() for e in all_events]
