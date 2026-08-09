"""Bluetooth OPP file transfers and the connection-order store (Root Tier 2).

Two artifacts, both under the Bluetooth stack's private data dir, that answer the
question ``bt_config.conf`` cannot:

``btopp.db``
    The Object Push Profile transfer log.  Each row is one file sent to or
    received from a peer device, with a **wall-clock epoch-ms timestamp**, the
    peer's BD_ADDR, the filename, the byte count and the outcome.  This is the
    strongest "when" in Bluetooth forensics available without a HCI snoop log:
    a completed transfer row cannot exist unless the two devices held an active
    link at that time.  Contrast with the bond timestamp in
    :mod:`triage.parsers.bt_config`, which only records when a *pairing record*
    was written.

``bluetooth_db``
    The Android 11+ Room database behind ``DatabaseManager``.  Its
    ``metadata.last_active_time`` column is the single most misread field in
    Bluetooth forensics: despite the name it is **not a time**.  AOSP assigns it
    from a process-wide counter (``sCurrentConnectionNumber++``) each time a
    device connects, so it is an *ordinal* — it ranks devices by recency of
    connection and nothing more.  Rendering it as a date produces a 1970
    timestamp that reads as a real finding.  This module exposes it as a rank.

Both paths require **root** (Tier 2).  Deleted transfer rows are carved through
the standard :mod:`triage.recovery` machinery, so a cleared transfer history
still yields rows — badged ``Recovered``/``Carved`` rather than ``Live``.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from ..config import Confidence
from ..models import Serialisable, TimelineEvent


#: Every known on-device location of the OPP transfer log and the connection-order
#: store, as ``(device path, local staging name)``.  Credential-encrypted (``/data/data``,
#: ``/data/user/0``) and device-encrypted (``/data/user_de/0``) roots are both probed:
#: which one the Bluetooth stack uses moved across versions, and on a locked FBE
#: device only the ``_de`` copy is readable at all.
BT_TRANSFER_PATHS: list[tuple[str, str]] = [
    ("/data/user_de/0/com.android.bluetooth/databases/btopp.db", "btopp.db"),
    ("/data/user_de/0/com.android.bluetooth/databases/btopp.db-wal", "btopp.db-wal"),
    ("/data/user_de/0/com.android.bluetooth/databases/btopp.db-shm", "btopp.db-shm"),
    (
        "/data/data/com.android.bluetooth/databases/btopp.db",
        "btopp.ce.db",
    ),
    (
        "/data/data/com.android.bluetooth/databases/btopp.db-wal",
        "btopp.ce.db-wal",
    ),
    (
        "/data/user_de/0/com.android.bluetooth/databases/bluetooth_db",
        "bluetooth_db",
    ),
    (
        "/data/user_de/0/com.android.bluetooth/databases/bluetooth_db-wal",
        "bluetooth_db-wal",
    ),
]

#: ``BluetoothShare.DIRECTION_*``.
_DIRECTION = {0: "outbound", 1: "inbound"}

#: ``BluetoothShare`` status codes.  Deliberately HTTP-shaped in AOSP; anything
#: >= 400 is a failure and must not be reported as a completed transfer.
_STATUS = {
    190: "pending",
    192: "running",
    200: "success",
    400: "bad-request",
    403: "forbidden",
    404: "not-found",
    406: "not-acceptable",
    411: "length-required",
    412: "precondition-failed",
    490: "canceled",
    491: "unknown-error",
    492: "file-error",
    493: "no-storage",
    494: "storage-full",
    495: "connection-error",
    496: "obex-protocol-error",
}


def _iso(epoch_ms: Any) -> Optional[str]:
    """Epoch-ms → ISO-8601, or ``None`` when the value cannot be a real date.

    Zero, negative and pre-2008 values are dropped rather than rendered: a
    1970-01-01 row in a report reads as a finding about January 1970.
    """
    if not isinstance(epoch_ms, (int, float)) or isinstance(epoch_ms, bool):
        return None
    seconds = epoch_ms / 1000.0
    if not (1_199_145_600 <= seconds <= 4_102_444_800):
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(seconds))


@dataclass
class BluetoothTransfer(Serialisable):
    """One row of the Bluetooth OPP transfer log.

    ``timestamp`` is a wall-clock device time.  Unlike a bond timestamp it is
    evidence of an **active link** with ``peer_address`` at that moment — subject
    to ``status``: a failed or canceled row proves an attempt, not a delivery.
    """

    peer_address: str  # BD_ADDR of the other device
    direction: str  # inbound / outbound / unknown
    filename: str
    mimetype: str = ""
    timestamp: Optional[str] = None  # ISO-8601, device clock
    status: str = ""  # success / canceled / connection-error / …
    status_code: Optional[int] = None
    succeeded: Optional[bool] = None
    total_bytes: Optional[int] = None
    transferred_bytes: Optional[int] = None
    local_path: str = ""  # where the file landed / was read from
    confidence: Confidence = Confidence.LIVE
    provenance: str = ""  # "live query", "freelist page 4", "WAL frame 12", …
    source_file: str = ""
    caveats: list[str] = field(default_factory=list)


_COLUMNS = (
    "_id",
    "uri",
    "hint",
    "_data",
    "mimetype",
    "direction",
    "destination",
    "visibility",
    "confirm",
    "status",
    "total_bytes",
    "current_bytes",
    "timestamp",
)


def _row_to_transfer(
    values: dict[str, Any],
    *,
    confidence: Confidence,
    provenance: str,
    source_file: str,
) -> Optional[BluetoothTransfer]:
    """Build a transfer from a column-name-keyed row, or ``None`` if it is junk.

    A carved row that has neither a peer address nor a filename carries no
    evidential content and is dropped rather than padded with placeholders.
    """
    peer = str(values.get("destination") or "").strip()
    filename = str(values.get("hint") or "").strip()
    local_path = str(values.get("_data") or "").strip()
    if not peer and not filename and not local_path:
        return None

    raw_status = values.get("status")
    status_code = raw_status if isinstance(raw_status, int) else None
    succeeded: Optional[bool] = None
    if status_code is not None:
        # AOSP treats < 200 as in-flight, 200 as done, >= 400 as failed.
        succeeded = status_code == 200 if status_code >= 200 else None

    raw_direction = values.get("direction")
    direction = _DIRECTION.get(raw_direction, "unknown") if isinstance(raw_direction, int) else "unknown"

    timestamp = _iso(values.get("timestamp"))
    caveats: list[str] = []
    if timestamp is None:
        caveats.append(
            "No usable transfer timestamp on this row — the value was absent, zero, "
            "or outside any plausible date range. Absence of a time is not evidence "
            "the transfer did not happen."
        )
    else:
        caveats.append(
            "Transfer time is the DEVICE clock at the time of the transfer. If the "
            "device clock was wrong or was changed, this is wrong by the same amount."
        )
    if succeeded is False:
        caveats.append(
            f"status={status_code} ({_STATUS.get(status_code, 'unknown')}) — this row "
            f"records a transfer ATTEMPT that did not complete. The link existed; the "
            f"file did not necessarily arrive."
        )
    if not peer:
        caveats.append(
            "No peer address on this row — the counterparty device is unidentified."
        )

    return BluetoothTransfer(
        peer_address=peer,
        direction=direction,
        filename=filename or Path(local_path).name,
        mimetype=str(values.get("mimetype") or ""),
        timestamp=timestamp,
        status=_STATUS.get(status_code, "") if status_code is not None else "",
        status_code=status_code,
        succeeded=succeeded,
        total_bytes=values.get("total_bytes") if isinstance(values.get("total_bytes"), int) else None,
        transferred_bytes=(
            values.get("current_bytes") if isinstance(values.get("current_bytes"), int) else None
        ),
        local_path=local_path,
        confidence=confidence,
        provenance=provenance,
        source_file=source_file,
        caveats=caveats,
    )


def _detect_table(db_path: Path) -> Optional[str]:
    """Return the OPP transfer table name, tolerating OEM renames.

    AOSP calls it ``btopp``; a couple of OEM stacks ship ``btopp_share`` or
    ``share``.  Matching on the column signature rather than the name means a
    renamed table is still found.
    """
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            names = [
                r[0]
                for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            ]
            for name in names:
                cols = {r[1] for r in con.execute(f"PRAGMA table_info('{name}')")}
                if {"destination", "direction", "timestamp"} <= cols:
                    return name
        finally:
            con.close()
    except sqlite3.Error:
        return None
    return None


def parse_btopp(path: Any) -> dict[str, Any]:
    """Parse ``btopp.db`` — live rows plus carved deleted rows.

    Returns ``{"transfers": [...], "table": str|None, "caveats": [...],
    "source_file": str}``.  A database that exists but has no transfer table is
    reported with an explicit caveat: "present but no transfer table" is a
    different fact from "no transfers", and neither means "no Bluetooth use".
    """
    db_path = Path(path)
    out: dict[str, Any] = {
        "transfers": [],
        "table": None,
        "source_file": db_path.name,
        "caveats": [
            "The OPP log covers file transfers only. Audio streaming, tethering, "
            "keyboards and every other Bluetooth profile leave no row here — an "
            "empty transfer log is not evidence of no Bluetooth activity.",
        ],
    }
    if not db_path.exists():
        return out

    table = _detect_table(db_path)
    if table is None:
        out["caveats"].append(
            f"{db_path.name} is present but carries no recognisable OPP transfer "
            f"table. The database was read; it did not contain the expected schema."
        )
        return out
    out["table"] = table

    from ..recovery import read_live_rows, recover_deleted_rows, rows_meta_colnames

    transfers: list[BluetoothTransfer] = []

    live = read_live_rows(db_path, table)
    colnames = rows_meta_colnames.get((db_path.name, table)) or list(_COLUMNS)
    for row in live:
        values = dict(zip(colnames, row.values))
        rec = _row_to_transfer(
            values,
            confidence=row.confidence,
            provenance=row.provenance or "live query",
            source_file=db_path.name,
        )
        if rec is not None:
            transfers.append(rec)

    live_keys = {
        (t.peer_address, t.filename, t.timestamp, t.total_bytes) for t in transfers
    }
    try:
        carved = recover_deleted_rows(db_path, table)
    except Exception:
        carved = []
    for row in carved:
        values = dict(zip(colnames, row.values))
        rec = _row_to_transfer(
            values,
            confidence=row.confidence,
            provenance=row.provenance or "carved",
            source_file=db_path.name,
        )
        if rec is None:
            continue
        # A carve that reproduces a live row is the same event seen twice, not a
        # second transfer. Only genuinely deleted rows are added.
        if (rec.peer_address, rec.filename, rec.timestamp, rec.total_bytes) in live_keys:
            continue
        rec.caveats.append(
            "Recovered from unallocated space — the row was deleted from the live "
            "table. Field values may be partial where the record was overwritten."
        )
        transfers.append(rec)

    transfers.sort(key=lambda t: t.timestamp or "")
    out["transfers"] = transfers
    return out


# ---------------------------------------------------------------------------
# bluetooth_db — connection ORDER, not connection time
# ---------------------------------------------------------------------------


@dataclass
class BluetoothConnectionRank(Serialisable):
    """A device's position in the connection-recency ordering.

    ``ordinal`` is AOSP's ``metadata.last_active_time`` verbatim.  It is a
    counter, not a date; ``rank`` 1 is the most recently connected device.
    """

    address: str
    ordinal: int
    rank: int
    name: str = ""
    caveats: list[str] = field(default_factory=list)


def parse_bluetooth_metadata_db(path: Any) -> dict[str, Any]:
    """Parse the Android 11+ ``bluetooth_db`` metadata table into a connection ranking.

    The ordering is real evidence — "the suspect's headset was the last thing this
    phone connected to" is a finding — but it is *only* an ordering.  No date can
    be derived from it, and this function never returns one.
    """
    db_path = Path(path)
    out: dict[str, Any] = {
        "devices": [],
        "source_file": db_path.name,
        "caveats": [
            "metadata.last_active_time is a connection COUNTER, not a timestamp. It "
            "orders devices by how recently they connected and carries no date. Any "
            "tool that renders it as a time is wrong.",
        ],
    }
    if not db_path.exists():
        return out

    rows: list[tuple[str, int, str]] = []
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            cols = {r[1] for r in con.execute("PRAGMA table_info('metadata')")}
            if "address" not in cols:
                out["caveats"].append(
                    f"{db_path.name} has no metadata.address column; schema not recognised."
                )
                return out
            name_col = next(
                (c for c in ("name", "device_name", "alias") if c in cols), None
            )
            select = "address, last_active_time" + (f", {name_col}" if name_col else "")
            if "last_active_time" not in cols:
                out["caveats"].append(
                    f"{db_path.name} carries no last_active_time column — this Android "
                    f"version does not record connection order."
                )
                return out
            for row in con.execute(f"SELECT {select} FROM metadata"):
                addr = str(row[0] or "").strip()
                ordinal = row[1]
                if not addr or not isinstance(ordinal, int) or ordinal <= 0:
                    continue
                rows.append((addr, ordinal, str(row[2] or "") if name_col else ""))
        finally:
            con.close()
    except sqlite3.Error as exc:
        out["caveats"].append(f"{db_path.name} could not be read: {exc}")
        return out

    rows.sort(key=lambda r: r[1], reverse=True)
    out["devices"] = [
        BluetoothConnectionRank(
            address=addr,
            ordinal=ordinal,
            rank=idx + 1,
            name=name,
            caveats=(
                ["Most recently connected device at the time of the last write."]
                if idx == 0
                else []
            ),
        )
        for idx, (addr, ordinal, name) in enumerate(rows)
    ]
    return out


# ---------------------------------------------------------------------------
# Timeline + summary
# ---------------------------------------------------------------------------


def _as_confidence(raw: Any) -> Confidence:
    """Coerce a serialised confidence back to the enum, defaulting to LIVE.

    Rows arrive here both as dataclasses and as round-tripped JSON, and an
    unrecognised label must not take the whole timeline build down with it.
    """
    if isinstance(raw, Confidence):
        return raw
    try:
        return Confidence(raw)
    except ValueError:
        return Confidence.LIVE


def build_transfer_timeline(transfers: Iterable[Any]) -> list[dict[str, Any]]:
    """Adapt OPP transfers to timeline events.

    Only rows with a real timestamp produce an event.  The summary states the
    outcome explicitly so a canceled transfer can never be read as a delivered
    file, and says "active Bluetooth link" — which a transfer row does prove —
    rather than "proximity", which it does not measure.
    """
    events: list[dict[str, Any]] = []
    for item in transfers or ():
        data = item.to_dict() if hasattr(item, "to_dict") else dict(item)
        ts = data.get("timestamp")
        if not ts:
            continue
        peer = data.get("peer_address") or "(unidentified device)"
        name = data.get("filename") or "(unnamed file)"
        direction = data.get("direction", "unknown")
        verb = {
            "inbound": "received from",
            "outbound": "sent to",
        }.get(direction, "exchanged with")
        status = data.get("status") or "unknown outcome"
        size = data.get("total_bytes")
        size_part = f", {size} bytes" if isinstance(size, int) and size > 0 else ""
        events.append(
            TimelineEvent(
                timestamp=ts,
                kind="bluetooth_transfer",
                summary=(
                    f"[Bluetooth] file '{name}' {verb} {peer}{size_part} — "
                    f"outcome: {status}. A transfer row requires an active Bluetooth "
                    f"link at this time."
                ),
                confidence=_as_confidence(data.get("confidence")),
                ref=peer,
            ).to_dict()
        )
    events.sort(key=lambda e: e["timestamp"])
    return events


def bt_transfer_summary(result: dict[str, Any]) -> dict[str, Any]:
    """Aggregate a :func:`parse_btopp` result for the dashboard scorecard."""
    rows = [
        t.to_dict() if hasattr(t, "to_dict") else dict(t)
        for t in (result.get("transfers") or [])
    ]
    dated = [r for r in rows if r.get("timestamp")]
    peers = {r.get("peer_address") for r in rows if r.get("peer_address")}
    return {
        "total": len(rows),
        "inbound": sum(1 for r in rows if r.get("direction") == "inbound"),
        "outbound": sum(1 for r in rows if r.get("direction") == "outbound"),
        "succeeded": sum(1 for r in rows if r.get("succeeded") is True),
        "failed": sum(1 for r in rows if r.get("succeeded") is False),
        "recovered": sum(
            1 for r in rows if r.get("confidence") != Confidence.LIVE.value
        ),
        "distinct_peers": len(peers),
        "first_transfer": dated[0]["timestamp"] if dated else None,
        "last_transfer": dated[-1]["timestamp"] if dated else None,
        "undated_rows": len(rows) - len(dated),
    }
