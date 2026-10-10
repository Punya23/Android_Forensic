"""Unit tests for triage.forensics.batch_transfer (streamed tar bulk pull).

No real device is available in CI, so `Adb` is stood in for by `FakeAdb`: a duck-typed object
backed by a dict simulating "the device filesystem", built from real local file content so the
tar stream is produced and parsed for real (only the transport -- `adb shell -T -n tar` -- is
faked). Measured against a real Android 16 emulator separately: 352 files / 379 MB in 4 s.
"""

from __future__ import annotations

import io
import os
import shlex
import tarfile
import threading
from pathlib import Path

import pytest

from triage.forensics.batch_transfer import _extract_stream, chunk_files, pull_chunk


class FakeStream:
    """Stands in for `ShellStream`: a tar byte stream plus the kill/close surface."""

    def __init__(self, stdout, returncode: int = 0, stderr: str = ""):
        self.stdout = stdout
        self.returncode = returncode
        self._stderr = stderr
        self.killed = False

    def kill(self) -> None:
        self.killed = True
        closer = getattr(self.stdout, "unblock", None)
        if closer:
            closer()

    def stderr_tail(self, n: int = 400) -> str:
        return self._stderr[-n:]

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        pass


class FakeAdb:
    """Stands in for `Adb`, simulating a device filesystem with real bytes."""

    def __init__(
        self,
        device_files: dict[str, Path],
        *,
        break_tar: bool = False,
        truncate_at: int | None = None,
        no_stream: bool = False,
    ):
        self.device_files = device_files  # device path -> local Path with real content
        self.break_tar = break_tar
        self.truncate_at = truncate_at
        self.no_stream = no_stream
        self.calls: list[str] = []

    def stream_shell(self, cmd: str):
        self.calls.append(cmd)
        if self.no_stream:
            return None
        if self.break_tar:
            return FakeStream(io.BytesIO(b""), returncode=1, stderr="tar: not found")
        argv = shlex.split(cmd)  # the real phone's sh does exactly this to the quoted paths
        assert argv[:4] == ["tar", "-c", "-f", "-"], argv[:4]
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            for dev_path in argv[4:]:
                src = self.device_files.get(dev_path)
                if src is not None and src.exists():
                    # tar strips the leading '/' from absolute member names.
                    tf.add(src, arcname=dev_path.lstrip("/"))
                # else: simulate permission-denied/vanished -- silently absent, as toybox tar
                # prints a notice to stderr and carries on.
        data = buf.getvalue()
        if self.truncate_at is not None:
            data = data[: self.truncate_at]
        return FakeStream(io.BytesIO(data))


@pytest.fixture()
def device_tree(tmp_path: Path) -> dict[str, Path]:
    """A little fixture 'device' with real files under tmp_path/device_src."""
    src = tmp_path / "device_src"
    src.mkdir()
    tree: dict[str, Path] = {}
    for i in range(7):
        f = src / f"IMG_{i:03d}.jpg"
        f.write_bytes(f"fake jpeg bytes #{i}".encode() * 100)
        tree[f"/sdcard/DCIM/Camera/IMG_{i:03d}.jpg"] = f
    return tree


# ---------------------------------------------------------------------------
# chunk_files
# ---------------------------------------------------------------------------
def test_chunk_files_covers_every_file_no_truncation():
    """The old stub silently dropped anything past the 50th file -- must never
    happen again: every input file must appear in exactly one output chunk."""
    files = [f"/sdcard/DCIM/f{i}.jpg" for i in range(437)]
    chunks = chunk_files(files, chunk_size=150)
    flattened = [f for c in chunks for f in c]
    assert flattened == files
    assert len(chunks) == 3  # 150 + 150 + 137


def test_chunk_files_respects_chunk_size():
    files = [f"f{i}" for i in range(10)]
    chunks = chunk_files(files, chunk_size=4)
    assert [len(c) for c in chunks] == [4, 4, 2]


def test_chunk_files_empty_input():
    assert chunk_files([], chunk_size=150) == []


def test_chunk_files_cuts_by_bytes_and_a_big_file_travels_alone():
    mb = 1024 * 1024
    files = ["a", "b", "c", "huge", "d"]
    sizes = {"a": 100 * mb, "b": 100 * mb, "c": 100 * mb, "huge": 900 * mb, "d": 1 * mb}
    chunks = chunk_files(files, chunk_size=150, sizes=sizes, max_bytes=250 * mb)
    assert chunks == [["a", "b"], ["c"], ["huge"], ["d"]]
    assert [f for c in chunks for f in c] == files  # still every file exactly once, in order


def test_chunk_files_keeps_the_command_line_short():
    files = ["/sdcard/" + "x" * 200 + str(i) for i in range(100)]
    chunks = chunk_files(files, chunk_size=1000, max_chars=5_000)
    assert len(chunks) > 1
    assert all(sum(len(f) + 3 for f in c) <= 5_000 for c in chunks)
    assert [f for c in chunks for f in c] == files


# ---------------------------------------------------------------------------
# pull_chunk -- happy path
# ---------------------------------------------------------------------------
def test_pull_chunk_recovers_every_file_with_correct_content(tmp_path, device_tree):
    staging = tmp_path / "staging"
    adb = FakeAdb(device_tree)
    files = list(device_tree.keys())

    results = pull_chunk(files, adb, staging)

    assert {r.device_path for r in results} == set(files)
    for r in results:
        assert r.local_path.exists()
        assert r.local_path.read_bytes() == device_tree[r.device_path].read_bytes()


def test_pull_chunk_touches_nothing_on_the_device(tmp_path, device_tree):
    """A Tier-0 pull must not write a list or archive to the phone: one stream, no push/rm."""
    adb = FakeAdb(device_tree)

    pull_chunk(list(device_tree), adb, tmp_path / "staging")

    assert len(adb.calls) == 1 and adb.calls[0].startswith("tar -c -f - ")
    assert not any("/data/local/tmp" in c or "gzip" in c or " -z" in c for c in adb.calls)


def test_pull_chunk_survives_spaces_quotes_and_unicode_in_names(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    tree = {}
    for i, name in enumerate(["IMG with space.jpg", "it's quoted.jpg", "é$HOME `x`.jpg", 'say "hi".jpg']):
        f = src / f"f{i}"
        f.write_bytes(f"content {i}".encode())
        tree[f"/sdcard/DCIM/Camera/{name}"] = f

    results = pull_chunk(list(tree), FakeAdb(tree), tmp_path / "staging")

    assert {r.device_path for r in results} == set(tree)
    for r in results:
        assert r.local_path.read_bytes() == tree[r.device_path].read_bytes()


def test_pull_chunk_empty_input_returns_empty():
    assert pull_chunk([], adb=FakeAdb({}), staging_dir=Path("/tmp/unused")) == []


# ---------------------------------------------------------------------------
# pull_chunk -- partial failure (some files unreadable on-device)
# ---------------------------------------------------------------------------
def test_pull_chunk_partial_failure_returns_only_recovered_files(tmp_path, device_tree):
    staging = tmp_path / "staging"
    files = list(device_tree.keys())
    # Simulate one file being permission-denied / vanished on-device: the fake
    # tar step silently skips any path not present in `device_files`.
    missing = files[2]
    reduced_tree = {k: v for k, v in device_tree.items() if k != missing}
    adb = FakeAdb(reduced_tree)

    results = pull_chunk(files, adb, staging)

    recovered = {r.device_path for r in results}
    assert missing not in recovered
    assert recovered == set(files) - {missing}
    # The caller (pipeline._batch_pull_files) computes the leftover as `set(files) - recovered`
    # and retries it individually -- this must never raise or claim `missing` was transferred.


def test_pull_chunk_stream_cut_mid_file_keeps_only_whole_files(tmp_path, device_tree):
    files = list(device_tree.keys())
    full = FakeAdb(device_tree).stream_shell("tar -c -f - " + " ".join(shlex.quote(f) for f in files[:3]))
    with tarfile.open(fileobj=io.BytesIO(full.stdout.getvalue())) as t:
        cut_at = t.getmembers()[2].offset_data + 500  # 500 bytes into the 3rd member's data

    results = pull_chunk(files[:3], FakeAdb(device_tree, truncate_at=cut_at), tmp_path / "staging")

    assert {r.device_path for r in results} == set(files[:2])
    assert all(r.local_path.read_bytes() == device_tree[r.device_path].read_bytes() for r in results)
    leftover = [p for p in (tmp_path / "staging").rglob("*") if p.is_file() and p not in {r.local_path for r in results}]
    assert leftover == []  # the half-written third file was deleted, not left to be ingested


# ---------------------------------------------------------------------------
# pull_chunk -- total chunk failure (must return [], never raise)
# ---------------------------------------------------------------------------
def test_pull_chunk_returns_empty_list_when_tar_unsupported(tmp_path, device_tree):
    results = pull_chunk(list(device_tree), FakeAdb(device_tree, break_tar=True), tmp_path / "staging")

    assert results == []


def test_pull_chunk_returns_empty_list_when_the_stream_cannot_start(tmp_path, device_tree):
    assert pull_chunk(list(device_tree), FakeAdb(device_tree, no_stream=True), tmp_path / "staging") == []


def test_pull_chunk_never_raises_on_adb_exception(tmp_path, device_tree, monkeypatch):
    """A chunk failing must degrade to '[]', never propagate -- the caller relies
    on this to fall back to per-file pulls without a broad except at the call site."""
    adb = FakeAdb(device_tree)

    def _boom(*a, **k):
        raise RuntimeError("adb daemon vanished")

    monkeypatch.setattr(adb, "stream_shell", _boom)

    assert pull_chunk(list(device_tree), adb, tmp_path / "staging") == []


def test_a_stalled_stream_is_killed_by_the_idle_watchdog(tmp_path, device_tree):
    """A cable pulled or a phone gone to sleep produces silence, not an error: the stream must be
    killed (and the files it did deliver kept) rather than hang the whole acquisition."""
    r, w = os.pipe()
    reader = os.fdopen(r, "rb", buffering=0)
    files = list(device_tree)[:2]
    good = FakeAdb(device_tree).stream_shell("tar -c -f - " + " ".join(shlex.quote(f) for f in files[:1]))
    os.write(w, good.stdout.getvalue().rstrip(b"\0"))  # the first file, then silence (no end marker)

    class Stuck(FakeStream):
        def kill(self):
            super().kill()
            os.close(w)  # what killing the adb process does: the pipe reaches EOF

    stream = Stuck(reader)

    class StuckAdb:
        def stream_shell(self, cmd):
            return stream

    results = pull_chunk(files, StuckAdb(), tmp_path / "staging", idle_timeout=0.3)

    assert stream.killed
    assert [x.device_path for x in results] == files[:1]


# ---------------------------------------------------------------------------
# extraction safety
# ---------------------------------------------------------------------------
def test_extract_refuses_path_traversal_member(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        payload = b"pwned"
        info = tarfile.TarInfo(name="../../../etc/evil_cron")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))
        # A legitimate member alongside the hostile one, to confirm the safe
        # member still extracts even though the unsafe one is refused.
        good_payload = b"legit"
        good = tarfile.TarInfo(name="sdcard/DCIM/ok.jpg")
        good.size = len(good_payload)
        tf.addfile(good, io.BytesIO(good_payload))
    buf.seek(0)

    results = _extract_stream(buf, tmp_path / "staging")

    assert not (tmp_path.parent / "etc" / "evil_cron").exists()
    assert len(results) == 1
    assert results[0].device_path == "/sdcard/DCIM/ok.jpg"
    assert results[0].local_path.read_bytes() == b"legit"
