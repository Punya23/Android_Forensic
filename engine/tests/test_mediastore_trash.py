"""Tests for MediaStore trash recovery — the non-root deleted-media technique.

Locks in the properties that keep it honest:
  * a trashed file we actually pulled is RECOVERED_VERIFIED and carries a real deletion
    time (date_expires − 30 days);
  * a trashed MediaStore row whose file we did NOT recover is DELETION_DETECTED, never
    conflated with recovered content;
  * DB and filesystem evidence for the same item merge into one record;
  * a normal (untrashed) file produces nothing.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from triage.config import Confidence, Tier  # noqa: E402
from triage.forensics import (  # noqa: E402
    TRASH_WINDOW_DAYS,
    analyze_mediastore_trash,
    parse_trash_filename,
)
from triage.models import ArtifactRecord, MediaInventoryItem  # noqa: E402

NOW = datetime(2026, 7, 23, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _artifact(source_path: str, **kw) -> ArtifactRecord:
    return ArtifactRecord(
        artifact_id=kw.get("artifact_id", "A1"),
        source_path=source_path,
        stored_path=kw.get("stored_path", "artifacts/x.jpg"),
        size_bytes=kw.get("size_bytes", 318),
        sha256=kw.get("sha256", "deadbeef"),
        md5="x",
        tier=Tier.TIER0,
        method="adb pull",
        extracted_at="",
        flags=kw.get("flags", ["trashed"]),
    )


# --- filename parsing --------------------------------------------------------
def test_parse_trashed_filename():
    assert parse_trash_filename(".trashed-1753500000-IMG.jpg") == (
        "trashed",
        1753500000,
        "IMG.jpg",
    )


def test_parse_pending_filename():
    assert parse_trash_filename(".pending-1753000000-part.mp4") == (
        "pending",
        1753000000,
        "part.mp4",
    )


def test_parse_full_path_not_just_basename():
    assert parse_trash_filename("/sdcard/DCIM/Camera/.trashed-1700000000-a.jpg") == (
        "trashed",
        1700000000,
        "a.jpg",
    )


def test_parse_normal_filename_is_none():
    assert parse_trash_filename("IMG_20260101.jpg") is None
    assert parse_trash_filename("trashed-nope.jpg") is None


# --- recovered file (filesystem side) ----------------------------------------
def test_recovered_trashed_file_is_recovered_verified_with_deletion_time():
    expires = int((NOW + timedelta(days=12)).timestamp())  # trashed 18 days ago
    man = [_artifact(f"/sdcard/DCIM/Camera/.trashed-{expires}-evidence.jpg")]
    res = analyze_mediastore_trash([], man, now=NOW)

    assert len(res["items"]) == 1
    it = res["items"][0]
    assert it["file_recoverable"] is True
    assert it["confidence"] == Confidence.RECOVERED_VERIFIED.value
    assert it["artifact_id"] == "A1"
    # date_expires − 30 days = the deletion time.
    expected = (
        (
            datetime.fromtimestamp(expires, tz=timezone.utc)
            - timedelta(days=TRASH_WINDOW_DAYS)
        )
        .date()
        .isoformat()
    )
    assert it["estimated_deleted_at"].startswith(expected)
    assert it["deleted_at_is_estimate"] is True
    assert it["days_until_auto_purge"] == 12.0


# --- deletion-detected (DB side only) ----------------------------------------
def test_db_only_trashed_row_is_deletion_detected_not_recovered():
    inv = [
        MediaInventoryItem(
            media_id=7,
            kind="video",
            display_name="ghost.mp4",
            size_bytes=9999,
            is_trashed=True,
        )
    ]
    res = analyze_mediastore_trash(inv, [], now=NOW)

    assert len(res["items"]) == 1
    it = res["items"][0]
    assert it["file_recoverable"] is False
    assert it["confidence"] == Confidence.DELETION_DETECTED.value
    assert it["source"] == "mediastore"


def test_recovered_and_detected_are_not_conflated():
    expires = int((NOW + timedelta(days=5)).timestamp())
    inv = [
        MediaInventoryItem(
            media_id=2, kind="video", display_name="ghost.mp4", is_trashed=True
        )
    ]
    man = [_artifact(f"/sdcard/DCIM/.trashed-{expires}-real.jpg")]
    res = analyze_mediastore_trash(inv, man, now=NOW)
    s = res["summary"]
    assert s["file_recovered"] == 1
    assert s["deletion_detected_only"] == 1
    assert s["total"] == 2


# --- fusion ------------------------------------------------------------------
def test_db_and_filesystem_evidence_merge_into_one_record():
    expires = int((NOW + timedelta(days=3)).timestamp())
    inv = [
        MediaInventoryItem(
            media_id=1,
            kind="image",
            display_name="evidence.jpg",
            size_bytes=318,
            is_trashed=True,
            owner_app="Camera",
            date_expires=_iso(datetime.fromtimestamp(expires, tz=timezone.utc)),
        )
    ]
    man = [_artifact(f"/sdcard/DCIM/Camera/.trashed-{expires}-evidence.jpg")]
    res = analyze_mediastore_trash(inv, man, now=NOW)

    assert (
        len(res["items"]) == 1
    ), "the same item from both sources must not double-count"
    it = res["items"][0]
    assert it["source"] == "both"
    assert it["file_recoverable"] is True  # filesystem wins the content
    assert it["media_id"] == 1  # DB contributes the catalogue id
    assert it["owner_app"] == "Camera"


# --- pending -----------------------------------------------------------------
def test_pending_is_labelled_not_a_deletion():
    inv = [
        MediaInventoryItem(
            media_id=5, kind="image", display_name="part.jpg", is_pending=True
        )
    ]
    res = analyze_mediastore_trash(inv, [], now=NOW)
    it = res["items"][0]
    assert it["state"] == "pending"
    assert "not a user deletion" in it["note"]


# --- negative ----------------------------------------------------------------
def test_untrashed_media_produces_nothing():
    inv = [
        MediaInventoryItem(
            media_id=9,
            kind="image",
            display_name="normal.jpg",
            is_trashed=False,
            is_favorite=True,
        )
    ]
    res = analyze_mediastore_trash(inv, [], now=NOW)
    assert res["items"] == []
    assert res["summary"]["total"] == 0


# --- ordering ----------------------------------------------------------------
def test_recovered_and_soonest_expiry_sort_first():
    soon = int((NOW + timedelta(days=1)).timestamp())
    later = int((NOW + timedelta(days=20)).timestamp())
    man = [
        _artifact(f"/sdcard/.trashed-{later}-later.jpg", artifact_id="L"),
        _artifact(f"/sdcard/.trashed-{soon}-soon.jpg", artifact_id="S"),
    ]
    res = analyze_mediastore_trash([], man, now=NOW)
    # Both recoverable; the one auto-purging soonest is the more urgent → first.
    assert res["items"][0]["original_name"] == "soon.jpg"


def test_requires_verification_flag_is_always_set():
    man = [_artifact("/sdcard/.trashed-1800000000-x.jpg")]
    res = analyze_mediastore_trash([], man, now=NOW)
    assert all(i["requires_verification"] for i in res["items"])
