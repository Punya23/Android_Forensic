"""Batch Transfer — stream many files off the phone as one tar, with nothing staged on the phone.

``adb pull`` pays a fixed per-file cost (sync-protocol round trip, plus a FUSE ``open()`` on
Android 11+), and a phone full of thumbnails-sized chat photos spends far more time on that than
on moving bytes. So the files are grouped into chunks and each chunk is read by ONE
``adb shell tar -c -f - <paths>`` whose stdout is parsed as it arrives (``tarfile`` stream mode)
and extracted locally.

Compared with the earlier "tar.gz into /data/local/tmp, then pull it" design this

* writes nothing to the phone (no list file, no archive — a Tier-0 pull stays Tier-0),
* does not gzip JPEG/MP4 on the phone's CPU (they do not compress), and
* has no single 600 s deadline over a huge archive: a chunk is cut by bytes, and a stream that
  produces no bytes for ``IDLE_TIMEOUT_S`` is killed.

Measured on an Android 16 emulator: 352 files / 379 MB in 4 s versus 37 s for tar.gz-via-file.

Every step degrades gracefully. A stream that fails, stalls or is cut off hands back the files it
completed; the caller retries the rest one by one. This module never *drops* a file: it only ever
returns fewer than it was asked for, which the caller treats as "needs the slow path", not "gone".
"""

from __future__ import annotations

import logging
import shutil
import tarfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, List, Optional

from ..adb import Adb, sh_quote

logger = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE = 150  # most files in one stream
MAX_CHUNK_BYTES = 384 * 1024**2  # most bytes in one stream; a single bigger file gets a stream to itself
# The whole `tar ...` line is one argv element of `sh -c`; Linux caps one argument at 128 KiB.
MAX_CMD_CHARS = 60_000
IDLE_TIMEOUT_S = 60.0  # a stream that delivers no bytes for this long is stuck (cable, phone asleep)


@dataclass
class BatchPullResult:
    """One file recovered from a batch stream, staged locally."""

    device_path: str  # original absolute path on the device
    local_path: Path  # where it was extracted to on the workstation


def chunk_files(
    files: List[str],
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    sizes: Optional[dict[str, int]] = None,
    max_bytes: int = MAX_CHUNK_BYTES,
    max_chars: int = MAX_CMD_CHARS,
) -> List[List[str]]:
    """Split *files* into chunks, preserving order.

    A chunk holds at most ``chunk_size`` files, at most ``max_bytes`` of data when *sizes* (device
    path to bytes) knows them — a lone file larger than that is a chunk of its own — and few enough
    characters to fit one command line. Every file appears in exactly one chunk; nothing is
    truncated (an earlier version silently dropped everything past the 50th file).
    """
    if chunk_size <= 0:
        chunk_size = DEFAULT_CHUNK_SIZE
    sizes = sizes or {}
    chunks: List[List[str]] = []
    cur: List[str] = []
    cur_bytes = cur_chars = 0
    for f in files:
        b, c = sizes.get(f, 0), len(f) + 3
        if cur and (len(cur) >= chunk_size or cur_bytes + b > max_bytes or cur_chars + c > max_chars):
            chunks.append(cur)
            cur, cur_bytes, cur_chars = [], 0, 0
        cur.append(f)
        cur_bytes += b
        cur_chars += c
    if cur:
        chunks.append(cur)
    return chunks


class _Watched:
    """File-like wrapper that stamps the time of the last bytes received, for the idle watchdog."""

    def __init__(self, f: BinaryIO):
        self._f = f
        self.last = time.monotonic()

    def read(self, n: int = -1) -> bytes:
        data = self._f.read(n)
        if data:
            self.last = time.monotonic()
        return data


def pull_chunk(
    files: List[str],
    adb: Adb,
    staging_dir: Path,
    *,
    idle_timeout: float = IDLE_TIMEOUT_S,
) -> List[BatchPullResult]:
    """Stream *files* off the device as one tar and extract each into *staging_dir*.

    Returns one :class:`BatchPullResult` per file completely recovered. Files that could not be
    read on the phone (permission denied, deleted since listing) are simply missing, and so is
    everything after a stream that dies part-way. Never raises: the caller retries the
    difference file by file.
    """
    if not files:
        return []
    token = uuid.uuid4().hex[:12]
    out_dir = staging_dir / f".batch_{token}"
    try:
        staging_dir.mkdir(parents=True, exist_ok=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        stream = adb.stream_shell("tar -c -f - " + " ".join(sh_quote(f) for f in files))
        if stream is None:
            return []
        with stream:
            watched = _Watched(stream.stdout)
            done = threading.Event()

            def _watchdog() -> None:
                while not done.wait(min(1.0, idle_timeout / 4)):
                    if time.monotonic() - watched.last > idle_timeout:
                        logger.warning("batch stream %s: no data for %.0fs, killing it", token, idle_timeout)
                        stream.kill()
                        return

            threading.Thread(target=_watchdog, name="batch-idle-watchdog", daemon=True).start()
            try:
                results = _extract_stream(watched, out_dir)
            finally:
                done.set()
            if not results:
                logger.debug("batch stream %s recovered nothing: %s", token, stream.stderr_tail())
                shutil.rmtree(out_dir, ignore_errors=True)
            return results
    except Exception as exc:  # pragma: no cover - defensive, must never raise out
        logger.warning("batch chunk %s failed: %s", token, exc)
        return []


def _extract_stream(fileobj, out_dir: Path) -> List[BatchPullResult]:
    """Extract the regular files of a tar byte stream into *out_dir*, mapping each member back to
    its original device path.

    ``tar`` strips the leading ``/`` from absolute member names (GNU and toybox alike), so
    restoring it recovers the device path. A member is only reported once it is fully on disk at
    its declared size; a stream that ends or breaks mid-member leaves that member out (and its
    partial file deleted) and stops, since the rest of the stream cannot be trusted.
    """
    out_root = out_dir.resolve()
    results: List[BatchPullResult] = []
    dest: Optional[Path] = None
    try:
        with tarfile.open(fileobj=fileobj, mode="r|") as tar:
            for member in tar:
                if not member.isfile():
                    continue
                name = member.name.lstrip("/")
                if not name:
                    continue
                dest = (out_dir / name).resolve()
                # Path-traversal guard: a corrupt or hostile stream must never be able to extract
                # outside out_dir (e.g. a "../../.." member name).
                if dest != out_root and out_root not in dest.parents:
                    logger.warning("batch: refusing unsafe archive member %r", member.name)
                    dest = None
                    continue
                tar.extract(member, path=out_dir, set_attrs=False)
                if dest.stat().st_size != member.size:
                    raise tarfile.ReadError(f"short member {member.name!r}")
                results.append(BatchPullResult(device_path="/" + name, local_path=dest))
                dest = None
    except Exception as exc:
        logger.warning("batch: stream ended early (%d file(s) recovered): %s", len(results), exc)
        if dest is not None:
            try:
                dest.unlink(missing_ok=True)
            except OSError:
                pass
    return results
