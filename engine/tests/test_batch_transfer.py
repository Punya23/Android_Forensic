"""Unit tests for triage.forensics.batch_transfer (tar-based bulk pull).

No real device is available in CI, so `Adb` is stood in for by `FakeAdb`: a
duck-typed object backed by a dict simulating "the device filesystem", built
from real local file content so tarring/extracting is exercised for real (only
the transport -- adb push/shell/pull -- is faked).
"""

from __future__ import annotations

import re
import tarfile
from pathlib import Path

import pytest

from triage.adb import AdbResult
from triage.forensics.batch_transfer import chunk_files, pull_chunk


def _quoted_paths(cmd: str) -> list[str]:
    return re.findall(r"'([^']*)'", cmd)


class FakeAdb:
    """Stands in for `Adb`, simulating a device filesystem with real bytes."""

    def __init__(self, device_files: dict[str, Path], *, break_tar: bool = False):
        self.device_files = device_files  # device path -> local Path with real content
        self._device_fs: dict[str, bytes] = {}  # remote path -> bytes
        self.break_tar = break_tar
        self.calls: list[str] = []

    def push(self, local: Path, remote: str, timeout: int = 60) -> AdbResult:
        self.calls.append(f"push {remote}")
        self._device_fs[remote] = local.read_bytes()
        return AdbResult("push", 0, "", "")

    def shell(self, cmd: str, timeout: int = 120) -> AdbResult:
        self.calls.append(cmd)
        if cmd.startswith("tar -czf"):
            if self.break_tar:
                return AdbResult(cmd, 1, "", "tar: not found")
            paths = _quoted_paths(cmd)
            remote_archive, remote_list = paths[0], paths[1]
            listing = self._device_fs.get(remote_list, b"").decode("utf-8").splitlines()
            import io

            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w:gz") as tf:
                for dev_path in listing:
                    src = self.device_files.get(dev_path)
                    if src is not None and src.exists():
                        # tar strips the leading '/' from absolute member names.
                        tf.add(src, arcname=dev_path.lstrip("/"))
                    # else: simulate permission-denied/vanished -- silently absent,
                    # same as toybox tar skipping an unreadable file.
            self._device_fs[remote_archive] = buf.getvalue()
            return AdbResult(cmd, 0, "", "")
        if cmd.startswith("[ -s "):
            remote = _quoted_paths(cmd)[0]
            present = bool(self._device_fs.get(remote))
            return AdbResult(cmd, 0, "OK\n" if present else "", "")
        if cmd.startswith("rm -f"):
            for p in _quoted_paths(cmd):
                self._device_fs.pop(p, None)
            return AdbResult(cmd, 0, "", "")
        return AdbResult(cmd, 0, "", "")

    def pull(self, remote: str, local: Path, timeout: int = 300) -> AdbResult:
        self.calls.append(f"pull {remote}")
        data = self._device_fs.get(remote)
        if data is None:
            return AdbResult("pull", 1, "", "remote object does not exist")
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(data)
        return AdbResult("pull", 0, "", "")


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


def test_pull_chunk_cleans_up_device_and_local_temp_files(tmp_path, device_tree):
    staging = tmp_path / "staging"
    adb = FakeAdb(device_tree)
    files = list(device_tree.keys())

    pull_chunk(files, adb, staging)

    # No leftover batch archive/list on the simulated device...
    assert not any("triage_batch_" in k for k in adb._device_fs)
    # ...nor locally (only the extracted-files subfolder should remain).
    leftover_local = [p for p in staging.glob(".batch_*") if p.is_file()]
    assert leftover_local == []


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
    # The caller (pipeline._batch_pull_files) is expected to compute the
    # leftover as `set(files) - recovered` and retry it individually -- this
    # must never raise or silently claim `missing` was transferred.


# ---------------------------------------------------------------------------
# pull_chunk -- total chunk failure (must return [], never raise)
# ---------------------------------------------------------------------------
def test_pull_chunk_returns_empty_list_when_tar_unsupported(tmp_path, device_tree):
    staging = tmp_path / "staging"
    adb = FakeAdb(device_tree, break_tar=True)
    files = list(device_tree.keys())

    results = pull_chunk(files, adb, staging)

    assert results == []


def test_pull_chunk_returns_empty_list_when_push_fails(tmp_path, device_tree, monkeypatch):
    staging = tmp_path / "staging"
    adb = FakeAdb(device_tree)
    monkeypatch.setattr(adb, "push", lambda *a, **k: AdbResult("push", 1, "", "no space"))
    files = list(device_tree.keys())

    results = pull_chunk(files, adb, staging)

    assert results == []


def test_pull_chunk_never_raises_on_adb_exception(tmp_path, device_tree, monkeypatch):
    """A chunk failing must degrade to '[]', never propagate -- the caller relies
    on this to fall back to per-file pulls without a broad except at the call site."""
    staging = tmp_path / "staging"
    adb = FakeAdb(device_tree)

    def _boom(*a, **k):
        raise RuntimeError("adb daemon vanished")

    monkeypatch.setattr(adb, "shell", _boom)
    files = list(device_tree.keys())

    results = pull_chunk(files, adb, staging)

    assert results == []


# ---------------------------------------------------------------------------
# extraction safety
# ---------------------------------------------------------------------------
def test_extract_refuses_path_traversal_member(tmp_path):
    from triage.forensics.batch_transfer import _extract

    archive = tmp_path / "evil.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        import io

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

    staging = tmp_path / "staging"
    results = _extract(archive, staging, "tok123")

    assert not (tmp_path.parent / "etc" / "evil_cron").exists()
    assert any(str(tmp_path) in str(r.local_path.resolve()) for r in results)
    assert len(results) == 1
    assert results[0].device_path == "/sdcard/DCIM/ok.jpg"
