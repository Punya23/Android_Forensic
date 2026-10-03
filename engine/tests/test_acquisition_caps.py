"""A demo-sized run: cap the pull per category and overall, newest files first, and say so.

A 128 GB phone cannot be pulled in a ten-minute demo, so the examiner caps the run (e.g. 5 GB
overall, 100 MB per category). The selection must be deterministic, never exceed a cap, take the
newest files of each category, share the total fairly between categories, and report exactly what
was left behind — a capped acquisition is a partial one and has to be disclosed as such.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from triage.caps import apply_caps, bucket_of  # noqa: E402

MB = 1024 * 1024


def _e(path, size_mb, mtime):
    return (path, int(size_mb * MB), mtime)


def test_bucket_is_the_app_else_the_file_kind():
    assert bucket_of("/sdcard/Android/media/com.whatsapp/WhatsApp/Media/a.jpg") == "whatsapp"
    assert bucket_of("/sdcard/DCIM/Camera/a.jpg") == "image"
    assert bucket_of("/sdcard/DCIM/Camera/v.mp4") == "video"
    assert bucket_of("/sdcard/Download/report.pdf") == "document"


def test_per_bucket_cap_is_never_exceeded_and_newest_wins():
    entries = [_e(f"/sdcard/DCIM/{i}.jpg", 40, i) for i in range(5)]  # 200 MB of photos
    chosen, rep = apply_caps(entries, bucket_bytes=100 * MB, total_bytes=0)
    assert chosen == ["/sdcard/DCIM/4.jpg", "/sdcard/DCIM/3.jpg"]  # newest two, 80 MB; a third would be 120
    b = rep["buckets"]["image"]
    assert (b["available_files"], b["selected_files"], b["skipped_files"]) == (5, 2, 3)
    assert b["selected_bytes"] <= 100 * MB


def test_total_cap_is_shared_between_buckets():
    entries = [_e(f"/sdcard/DCIM/{i}.jpg", 30, i) for i in range(10)] + [
        _e(f"/sdcard/Movies/{i}.mp4", 30, i) for i in range(10)
    ]
    chosen, rep = apply_caps(entries, bucket_bytes=0, total_bytes=120 * MB)
    kinds = [bucket_of(p) for p in chosen]
    assert rep["selected_bytes"] <= 120 * MB and len(chosen) == 4
    assert kinds.count("image") == 2 and kinds.count("video") == 2  # fair, not photos-first


def test_no_caps_selects_everything_and_unknown_sizes_are_kept():
    entries = [_e("/sdcard/a.jpg", 1, 1), ("/sdcard/b.jpg", 0, 0)]
    chosen, rep = apply_caps(entries, bucket_bytes=0, total_bytes=0)
    assert len(chosen) == 2 and rep["skipped_files"] == 0


def test_selection_is_deterministic():
    entries = [_e(f"/sdcard/DCIM/{i}.jpg", 10, 5) for i in range(20)]  # all the same mtime
    assert apply_caps(entries, 50 * MB, 0)[0] == apply_caps(list(reversed(entries)), 50 * MB, 0)[0]


def test_adb_detailed_listing_parses_size_mtime_and_paths_with_spaces():
    from triage.adb import Adb, AdbResult

    adb = Adb.__new__(Adb)
    adb.shell = lambda cmd, timeout=60: type("R", (), {"ok": True, "stdout": "1048576 1751900000 /sdcard/DCIM/Camera/IMG 1.jpg\nbad line\n12 7 /sdcard/a.txt\n"})()
    assert adb.list_files_detailed("/sdcard/DCIM") == [
        ("/sdcard/DCIM/Camera/IMG 1.jpg", 1048576, 1751900000),
        ("/sdcard/a.txt", 12, 7),
    ]


class _Src:
    NOW = 1_800_000_000

    def __init__(self, files):
        self.files = files
        self.asked = []  # the day windows requested, in order

    def list_files(self, root):
        return [p for p, _, _ in self.files if p.startswith(root)]

    def list_files_detailed(self, root, days=None):
        if root == "/sdcard/DCIM":
            self.asked.append(days)
        return [e for e in self.files if e[0].startswith(root) and (days is None or e[2] >= self.NOW - days * 86400)]


def test_select_files_uncapped_keeps_plain_listing_order_and_dedupes():
    from triage.caps import select_files

    src = _Src([("/sdcard/DCIM/a.jpg", 5, 1), ("/sdcard/DCIM/b.jpg", 5, 2)])
    files, report = select_files(src, ["/sdcard/DCIM", "/sdcard"], max_files=10)
    assert files == ["/sdcard/DCIM/a.jpg", "/sdcard/DCIM/b.jpg"] and report is None


def test_select_files_capped_returns_a_report():
    from triage.caps import select_files

    src = _Src([(f"/sdcard/DCIM/{i}.jpg", 40 * MB, i) for i in range(5)])
    files, report = select_files(src, ["/sdcard/DCIM"], max_files=10, bucket_bytes=100 * MB)
    assert len(files) == 2 and report["skipped_files"] == 3


def test_a_capped_run_records_what_it_selected(tmp_path):
    from tests.test_phase2_pipeline import build
    from triage.acquire import MockDeviceSource
    from triage.pipeline import PipelineConfig, run_acquisition

    corpus = tmp_path / "device"
    corpus.mkdir()
    build(corpus)
    cfg = PipelineConfig(case_id="CAP-1", examiner="T", cases_root=tmp_path / "cases", cap_total_bytes=5 * 1024**3, cap_bucket_bytes=100 * MB)
    case_dir = Path(run_acquisition(MockDeviceSource(corpus), cfg)["case_dir"])
    import json

    rep = json.loads((case_dir / "derived" / "acquisition_caps.json").read_text())
    assert rep["total_cap_bytes"] == 5 * 1024**3 and rep["bucket_cap_bytes"] == 100 * MB
    assert rep["selected_files"] == rep["available_files"] > 0  # the mock corpus is far under the caps


def test_capped_listing_stops_at_the_recent_window_when_categories_are_full():
    from triage.caps import select_files

    recent = [(f"/sdcard/DCIM/r{i}.jpg", 40 * MB, _Src.NOW - i * 3600) for i in range(50)]  # 2 GB in the last 2 days
    old = [(f"/sdcard/DCIM/o{i}.jpg", 40 * MB, _Src.NOW - 400 * 86400) for i in range(500)]  # 20 GB, a year old
    src = _Src(recent + old)
    files, rep = select_files(src, ["/sdcard/DCIM"], max_files=5000, bucket_bytes=100 * MB, total_bytes=0, now=_Src.NOW)
    assert len(files) == 2 and all("/r" in f for f in files)
    assert src.asked[-1] is not None and 365 not in src.asked  # never scanned the old 20 GB
    assert rep["window_days"] == src.asked[-1]


def test_capped_listing_widens_the_window_until_there_is_something_to_take():
    from triage.caps import select_files

    old = [(f"/sdcard/DCIM/o{i}.jpg", 10 * MB, _Src.NOW - 200 * 86400) for i in range(30)]
    src = _Src(old)
    files, rep = select_files(src, ["/sdcard/DCIM"], max_files=5000, bucket_bytes=100 * MB, total_bytes=0, now=_Src.NOW)
    assert len(files) == 10 and 7 in src.asked and 365 in src.asked and rep["window_days"] == 365


def test_adb_media_index_listing_parses_rows_and_normalises_the_prefix():
    from triage.adb import Adb

    out = (
        "Row: 0 _data=/storage/emulated/0/Android/media/com.whatsapp/WhatsApp/.trash/x, _size=NULL, date_modified=1790973114\n"
        "Row: 1 _data=/storage/emulated/0/DCIM/Camera/IMG 1.jpg, _size=488567, date_modified=1790886305\n"
        "Row: 2 _data=/storage/emulated/0/Download/a, b.pdf, _size=12, date_modified=7\n"
    )
    adb = Adb.__new__(Adb)
    adb.shell = lambda cmd, timeout=60: type("R", (), {"ok": True, "stdout": out})()
    assert adb.list_indexed_files() == [
        ("/sdcard/DCIM/Camera/IMG 1.jpg", 488567, 1790886305),
        ("/sdcard/Download/a, b.pdf", 12, 7),
    ]


def test_capped_selection_prefers_the_media_index_and_never_walks_the_storage():
    from triage.caps import select_files

    class Indexed(_Src):
        def list_indexed_files(self):
            return [(f"/sdcard/DCIM/{i}.jpg", 40 * MB, i) for i in range(50)] + [("/sdcard/Android/data/x/y.bin", 1, 1)]

    src = Indexed([])
    files, rep = select_files(src, ["/sdcard/DCIM"], max_files=5000, bucket_bytes=100 * MB, total_bytes=0)
    assert files == ["/sdcard/DCIM/49.jpg", "/sdcard/DCIM/48.jpg"]  # newest two; the file outside the roots is ignored
    assert src.asked == [] and rep["listing"] == "media index"
