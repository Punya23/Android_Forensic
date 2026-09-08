"""_batch_pull_files (triage.pipeline) end-to-end against a real Case.

Exercises the chunked tar-pull orchestrator against a real `custody.Case` (so
manifest/hash correctness is checked for real), backed by the same `FakeAdb`
used in test_batch_transfer.py wrapped in a genuine `RealDeviceSource` (its
constructor only stores the adb object, so a duck-typed fake is enough to
satisfy the `isinstance(source, RealDeviceSource)` gate in run_acquisition).
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from tests.test_batch_transfer import FakeAdb
from triage.acquire.real import RealDeviceSource
from triage.custody import Case, CaseMeta
from triage.hashing import file_hashes
from triage.pipeline import _batch_pull_files


def _noop_progress(stage, pct, detail):
    pass


@pytest.fixture()
def device_tree(tmp_path: Path) -> dict[str, Path]:
    src = tmp_path / "device_src"
    src.mkdir()
    tree: dict[str, Path] = {}
    for i in range(11):
        f = src / f"IMG_{i:03d}.jpg"
        f.write_bytes(f"fake jpeg bytes #{i}".encode() * 50)
        tree[f"/sdcard/DCIM/Camera/IMG_{i:03d}.jpg"] = f
    return tree


@pytest.fixture()
def case(tmp_path: Path) -> Case:
    return Case.create(tmp_path / "cases", CaseMeta(case_id="BATCH-1", examiner="Tester"))


def test_batch_pull_ingests_every_file_with_correct_hash(tmp_path, device_tree, case):
    source = RealDeviceSource(adb=FakeAdb(device_tree))
    files = list(device_tree.keys())

    results, leftover = _batch_pull_files(
        files=files,
        source=source,
        staging=tmp_path / "staging",
        case=case,
        progress=_noop_progress,
        pull_start=0.0,
        total=len(files),
        ingest_lock=threading.Lock(),
        chunk_size=3,  # force multiple chunks with only 11 files
        max_workers=4,
    )

    assert leftover == []
    assert len(results) == len(files)
    assert len(case.manifest) == len(files)

    by_source = {rec.source_path: rec for rec in case.manifest}
    assert set(by_source) == set(files)
    for dev_path, local_path in device_tree.items():
        rec = by_source[dev_path]
        assert rec.sha256 == file_hashes(local_path)["sha256"]
        assert "(batch tar)" in rec.method
        assert (case.root / rec.stored_path).read_bytes() == local_path.read_bytes()


def test_batch_pull_manifest_reaches_disk_after_call(tmp_path, device_tree, case):
    """_batch_pull_files flushes per chunk (flush=False + explicit flush_manifest),
    so manifest.json on disk must reflect every ingested file once the call returns
    -- not just case.manifest in memory."""
    import json

    source = RealDeviceSource(adb=FakeAdb(device_tree))
    files = list(device_tree.keys())

    _batch_pull_files(
        files=files,
        source=source,
        staging=tmp_path / "staging",
        case=case,
        progress=_noop_progress,
        pull_start=0.0,
        total=len(files),
        ingest_lock=threading.Lock(),
        chunk_size=4,
    )

    on_disk = json.loads((case.root / "manifest.json").read_text())
    assert len(on_disk) == len(files)


def test_batch_pull_leftover_excludes_files_missing_from_archive(tmp_path, device_tree, case):
    missing = sorted(device_tree)[3]
    reduced = {k: v for k, v in device_tree.items() if k != missing}
    source = RealDeviceSource(adb=FakeAdb(reduced))
    files = list(device_tree.keys())  # ask for ALL, including the one the device lacks

    results, leftover = _batch_pull_files(
        files=files,
        source=source,
        staging=tmp_path / "staging",
        case=case,
        progress=_noop_progress,
        pull_start=0.0,
        total=len(files),
        ingest_lock=threading.Lock(),
        chunk_size=20,  # one chunk
    )

    assert leftover == [missing]
    assert len(results) == len(files) - 1
    assert missing not in {rec.source_path for rec in case.manifest}


def test_batch_pull_whole_chunk_failure_leaves_all_as_leftover(tmp_path, device_tree, case):
    source = RealDeviceSource(adb=FakeAdb(device_tree, break_tar=True))
    files = list(device_tree.keys())

    results, leftover = _batch_pull_files(
        files=files,
        source=source,
        staging=tmp_path / "staging",
        case=case,
        progress=_noop_progress,
        pull_start=0.0,
        total=len(files),
        ingest_lock=threading.Lock(),
        chunk_size=5,
    )

    assert results == []
    assert sorted(leftover) == sorted(files)
    assert case.manifest == []  # nothing was ingested -- correctness over speed
