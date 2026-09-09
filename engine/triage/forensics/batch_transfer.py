"""Batch Transfer — tar-based bulk pull for many small files.

``adb pull`` pays a fixed per-invocation cost (sync-protocol handshake + a fresh
subprocess) on top of the bytes actually moved. A device with thousands of small
media files (the common case for DCIM/WhatsApp/Telegram/Signal media) spends far
more time paying that per-file cost than it does moving bytes over the wire.

This module groups files into fixed-size chunks, tars each chunk on the device
with a single ``adb shell`` call, pulls ONE archive per chunk with a single
``adb pull``, and extracts it locally — turning N adb invocations into roughly
``N / chunk_size + 2`` (list push + tar + pull, per chunk).

Every step degrades gracefully. If tar creation, the pull, or extraction fails
for a chunk (older/OEM device without a compatible ``tar``, a permission error,
a timeout, a corrupt archive...) that chunk's files are simply absent from the
result — never raised as an exception — so the caller can fall back to pulling
them one by one. This module never *drops* a file: it only ever hands back
fewer files than it was asked to try, and the caller is expected to treat the
difference as "needs the slow path", not as "these files don't exist".
"""

from __future__ import annotations

import logging
import tarfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List

from ..adb import Adb

logger = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE = 150
REMOTE_TMP_DIR = "/data/local/tmp"


@dataclass
class BatchPullResult:
    """One file recovered from a batch archive, staged locally."""

    device_path: str  # original absolute path on the device
    local_path: Path  # where it was extracted to on the workstation


def chunk_files(files: List[str], chunk_size: int = DEFAULT_CHUNK_SIZE) -> List[List[str]]:
    """Split *files* into fixed-size chunks, preserving order.

    Every file appears in exactly one chunk — unlike an earlier version of this
    module, nothing here silently truncates the list.
    """
    if chunk_size <= 0:
        chunk_size = DEFAULT_CHUNK_SIZE
    return [files[i : i + chunk_size] for i in range(0, len(files), chunk_size)]


def pull_chunk(
    files: List[str],
    adb: Adb,
    staging_dir: Path,
    *,
    tar_timeout: int = 180,
    pull_timeout: int = 600,
) -> List[BatchPullResult]:
    """Tar *files* on the device, pull the archive once, extract locally.

    Returns one :class:`BatchPullResult` per file actually recovered from the
    archive. Files that could not be read on-device (permission denied, since
    deleted, etc.) are simply missing from the result. Returns ``[]`` — never
    raises — on total chunk failure, so the caller can retry the whole chunk
    file-by-file without special-casing exceptions.
    """
    if not files:
        return []

    staging_dir.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex[:12]
    remote_list = f"{REMOTE_TMP_DIR}/triage_batch_{token}.list"
    remote_archive = f"{REMOTE_TMP_DIR}/triage_batch_{token}.tar.gz"
    local_list = staging_dir / f".batch_{token}.list"
    local_archive = staging_dir / f".batch_{token}.tar.gz"

    try:
        # Newline-separated, pushed as a file and read with `tar -T`: no shell
        # quoting at all, so filenames with spaces/quotes/unicode need no escaping
        # and there's no argv-length limit from listing hundreds of paths inline.
        local_list.write_text("\n".join(files) + "\n", encoding="utf-8")

        push_res = adb.push(local_list, remote_list, timeout=60)
        if not push_res.ok:
            logger.debug("batch chunk %s: pushing file list failed: %s", token, push_res.stderr)
            return []

        # 2>/dev/null: a file that vanished or is permission-denied between
        # enumeration and tar-time must not fail the whole chunk -- tar skips it
        # and keeps going. We don't trust the exit code for that reason (toybox
        # tar's exit status for "some members unreadable" isn't reliably 0); the
        # existence+size check below is the real success signal.
        adb.shell(
            f"tar -czf '{remote_archive}' -T '{remote_list}' 2>/dev/null",
            timeout=tar_timeout,
        )
        check = adb.shell(f"[ -s '{remote_archive}' ] && echo OK", timeout=15)
        if "OK" not in check.stdout:
            logger.debug("batch chunk %s: no archive produced on device", token)
            return []

        pull_res = adb.pull(remote_archive, local_archive, timeout=pull_timeout)
        if not pull_res.ok or not local_archive.exists():
            logger.debug("batch chunk %s: pulling archive failed: %s", token, pull_res.stderr)
            return []

        return _extract(local_archive, staging_dir, token)
    except Exception as exc:  # pragma: no cover - defensive, must never raise out
        logger.warning("batch chunk %s failed: %s", token, exc)
        return []
    finally:
        # Cleanup must itself never raise -- a device that dropped mid-operation
        # (the case this most needs to run for) means this shell call can fail
        # too, and that must not shadow/replace whatever the caller should see
        # (nothing: this function's contract is "never raises").
        try:
            adb.shell(f"rm -f '{remote_list}' '{remote_archive}'", timeout=15)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("batch chunk %s: device cleanup failed: %s", token, exc)
        for f in (local_list, local_archive):
            try:
                f.unlink(missing_ok=True)
            except OSError:
                pass


def _extract(archive_path: Path, staging_dir: Path, token: str) -> List[BatchPullResult]:
    """Extract *archive_path* into a unique subfolder, mapping each member back
    to its original device path.

    ``tar`` strips the leading ``/`` from absolute member names on creation (a
    long-standing convention in both GNU and toybox tar), so restoring it
    recovers the original absolute device path.
    """
    out_dir = staging_dir / f".batch_{token}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_root = out_dir.resolve()
    results: List[BatchPullResult] = []
    try:
        with tarfile.open(archive_path, "r:gz") as tar:
            for member in tar.getmembers():
                if not member.isfile():
                    continue
                name = member.name.lstrip("/")
                if not name:
                    continue
                dest = (out_dir / name).resolve()
                # Path-traversal guard: a corrupt or hostile archive must never be
                # able to extract outside out_dir (e.g. a "../../.." member name).
                if dest != out_root and out_root not in dest.parents:
                    logger.warning("batch: refusing unsafe archive member %r", member.name)
                    continue
                tar.extract(member, path=out_dir, set_attrs=False)
                results.append(BatchPullResult(device_path="/" + name, local_path=dest))
    except Exception as exc:
        logger.warning("batch: failed to extract archive %s: %s", archive_path, exc)
    return results
