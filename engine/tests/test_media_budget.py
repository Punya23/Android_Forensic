"""Demo run: every non-media file is collected, photos/videos share one hard byte budget.

Police demo constraint: the whole acquisition has to finish in ~15 minutes, and media is what is
slow. So ``media_mode="budget"`` caps photos+videos (from every folder) at N bytes, newest first,
while documents, chat databases, recordings and the trash are still taken whole. A second,
live guard (:class:`MediaBudget`) counts what really landed on the laptop and stops media — and
only media — once the cap or the time box is reached. Both must say what was left behind.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from triage.caps import MediaBudget, apply_media_policy, select_files  # noqa: E402

MB = 1024 * 1024
GB = 1024 * MB


def _e(path, size_mb, mtime):
    return (path, int(size_mb * MB), mtime)


def _library():
    """A small phone: photos + videos in several folders, plus things that must never be capped."""
    return (
        [_e(f"/sdcard/DCIM/Camera/IMG_{i}.jpg", 4, 1000 + i) for i in range(50)]  # 200 MB
        + [_e(f"/sdcard/DCIM/Camera/VID_{i}.mp4", 60, 2000 + i) for i in range(10)]  # 600 MB
        + [_e(f"/sdcard/Android/media/com.whatsapp/WhatsApp/Media/WhatsApp Images/I{i}.jpg", 1, 3000 + i) for i in range(40)]
        + [_e("/sdcard/Pictures/Screenshots/s.png", 1, 5000)]
        + [_e("/sdcard/Download/case.pdf", 300, 10), _e("/sdcard/Movies/.trashed-1-x.mp4", 500, 20)]
        + [_e("/sdcard/Android/media/com.whatsapp/WhatsApp/Databases/msgstore.db.crypt15", 200, 30)]
        + [_e("/sdcard/Recordings/Call/rec.m4a", 50, 40), _e("/sdcard/Music/song.mp3", 9, 50)]
    )


def test_budget_caps_media_from_every_folder_not_just_camera():
    kept, rep = apply_media_policy(_library(), "budget", 300 * MB)
    paths = [e[0] for e in kept]
    media_bytes = sum(e[1] for e in kept if e[0].endswith((".jpg", ".png", ".mp4")) and ".trashed-" not in e[0])
    assert media_bytes <= 300 * MB
    assert rep["media_kept_bytes"] == media_bytes
    # not camera-only: WhatsApp images share the budget instead of being excluded outright
    assert any("WhatsApp Images" in p for p in paths)
    assert rep["mode"] == "budget" and rep["cap_bytes"] == 300 * MB
    assert rep["left_outside_camera_files"] == 0  # nothing is excluded for its folder
    assert rep["left_over_cap_files"] == rep["media_available_files"] - rep["media_kept_files"] > 0


def test_budget_never_exempts_a_screen_recording_or_big_video():
    entries = [_e("/sdcard/Movies/Screenrecord/screenrecord_1.mp4", 900, 5), _e("/sdcard/DCIM/Camera/a.jpg", 5, 4)]
    kept, rep = apply_media_policy(entries, "budget", 100 * MB)
    assert [e[0] for e in kept] == ["/sdcard/DCIM/Camera/a.jpg"]  # the 900 MB video is left, not "protected"
    assert rep["left_over_cap_bytes"] == 900 * MB


def test_budget_keeps_everything_that_is_not_media_and_the_trash():
    kept, _ = apply_media_policy(_library(), "budget", 1 * MB)
    paths = {e[0] for e in kept}
    assert "/sdcard/Download/case.pdf" in paths
    assert "/sdcard/Movies/.trashed-1-x.mp4" in paths  # deleted-but-recoverable stays whole
    assert "/sdcard/Recordings/Call/rec.m4a" in paths
    assert "/sdcard/Android/media/com.whatsapp/WhatsApp/Databases/msgstore.db.crypt15" in paths


def test_budget_newest_first_and_a_file_that_does_not_fit_never_blocks_a_smaller_one():
    entries = [_e("/sdcard/DCIM/Camera/new_big.mp4", 80, 9), _e("/sdcard/DCIM/Camera/old_small.mp4", 10, 1)]
    kept, _ = apply_media_policy(entries, "budget", 50 * MB)
    assert [e[0] for e in kept] == ["/sdcard/DCIM/Camera/old_small.mp4"]


def test_budget_is_deterministic():
    lib = _library()
    a, _ = apply_media_policy(lib, "budget", 250 * MB)
    b, _ = apply_media_policy(list(reversed(lib)), "budget", 250 * MB)
    assert sorted(e[0] for e in a) == sorted(e[0] for e in b)


def test_budget_zero_means_no_limit():
    lib = _library()
    kept, rep = apply_media_policy(lib, "budget", 0)
    assert rep["left_over_cap_files"] == 0 and len(kept) == len(lib)


def test_select_files_budget_end_to_end_leaves_music_and_reports():
    lib = _library()

    class _Src:
        def list_files(self, root):  # pragma: no cover - the index path is used
            return []

        def list_indexed_files(self):
            return lib

    chosen, rep = select_files(_Src(), ["/sdcard"], max_files=10_000, media_mode="budget", media_cap_bytes=300 * MB)
    assert "/sdcard/Music/song.mp3" not in chosen  # music is not evidence; reported as skipped, not silently lost
    assert rep["media_policy"]["mode"] == "budget"
    assert rep["skipped_files"] >= 1


# --- live guard -----------------------------------------------------------------------------------


def test_media_budget_stops_media_at_the_cap_and_only_media():
    b = MediaBudget(cap_bytes=10 * MB)
    b.start()
    assert not b.should_skip("/sdcard/DCIM/Camera/a.jpg")
    b.add("/sdcard/DCIM/Camera/a.jpg", 6 * MB)
    b.add("/sdcard/Download/x.pdf", 500 * MB)  # documents never count toward the media cap
    assert b.stop_reason() is None
    b.add("/sdcard/DCIM/Camera/b.mp4", 6 * MB)
    assert b.stop_reason() == "size"
    assert b.should_skip("/sdcard/DCIM/Camera/c.jpg")
    assert not b.should_skip("/sdcard/Download/y.pdf")  # non-media carries on after media stops
    assert not b.should_skip("/sdcard/Movies/.trashed-1-z.mp4")  # protected trash is never stopped
    rep = b.report()
    assert rep["stopped_by"] == "size" and rep["media_files_pulled"] == 2 and rep["media_files_left"] == 1


def test_media_budget_time_box():
    t = [100.0]
    b = MediaBudget(time_limit_s=60, clock=lambda: t[0])
    b.start()
    assert b.stop_reason() is None
    t[0] = 161.0
    assert b.stop_reason() == "time" and b.should_skip("/sdcard/DCIM/Camera/a.jpg")
    t[0] = 0.0  # reason is sticky: a clock hiccup must not resume media
    assert b.stop_reason() == "time"


def test_media_budget_not_started_has_no_time_stop():
    b = MediaBudget(time_limit_s=1, clock=lambda: 1e9)
    assert b.stop_reason() is None


# --- wired into the pull ----------------------------------------------------------------------------


def test_pull_worker_leaves_media_once_the_budget_is_spent_but_still_pulls_documents():
    import threading

    from triage.pipeline import _pull_and_process_file

    class _Src:
        method = "fake"

        def __init__(self):
            self.pulled = []

        def pull_file(self, device_path, staging):
            self.pulled.append(device_path)
            return None  # "failed/absent" is enough: we only assert whether a pull was attempted

    class _Case:
        def log(self, *a, **k):
            pass

    b = MediaBudget(cap_bytes=1)
    b.start()
    b.add("/sdcard/DCIM/Camera/a.jpg", 5)  # budget now spent
    src = _Src()
    args = (src, Path("/tmp/none"), _Case(), threading.Lock(), 0.0, False)
    assert _pull_and_process_file("/sdcard/DCIM/Camera/b.jpg", *args, media_budget=b) is None
    _pull_and_process_file("/sdcard/Download/c.pdf", *args, media_budget=b)
    assert src.pulled == ["/sdcard/Download/c.pdf"]  # the photo was never requested from the phone
    assert b.report()["media_files_left"] == 1


def test_a_budget_run_pulls_documents_before_media_and_records_the_budget(tmp_path):
    import json

    from tests.test_phase2_pipeline import build
    from triage.acquire import MockDeviceSource
    from triage.pipeline import PipelineConfig, run_acquisition

    corpus = tmp_path / "device"
    corpus.mkdir()
    build(corpus)
    cfg = PipelineConfig(
        case_id="BUD-1", examiner="T", cases_root=tmp_path / "cases", media_mode="budget", media_cap_bytes=50 * GB
    )
    case_dir = Path(run_acquisition(MockDeviceSource(corpus), cfg)["case_dir"])
    caps = json.loads((case_dir / "derived" / "acquisition_caps.json").read_text())
    assert caps["media_policy"]["mode"] == "budget" and caps["truncated_files"] == 0
    mb = json.loads((case_dir / "derived" / "media_budget.json").read_text())
    assert mb["cap_bytes"] == 50 * GB and mb["media_files_left"] == 0 and mb["stopped_by"] is None


def test_caps_report_counts_what_the_media_policy_left_on_the_phone():
    """'selected 120 of 120' used to hide 251 photos/videos the policy had already dropped."""
    from triage.caps import select_files

    lib = _library()

    class _Src:
        def list_files(self, root):  # pragma: no cover - the index path is used
            return []

        def list_indexed_files(self):
            return lib

    _, rep = select_files(_Src(), ["/sdcard"], max_files=10_000, media_mode="budget", media_cap_bytes=300 * MB)
    mp = rep["media_policy"]
    assert rep["available_files"] == rep["selected_files"] + rep["skipped_files"]
    assert rep["skipped_files"] >= mp["left_over_cap_files"] > 0
    assert rep["skipped_bytes"] >= mp["left_over_cap_bytes"]


def test_a_directory_listed_as_a_file_is_not_ingested(tmp_path):
    from triage.acquire.real import RealDeviceSource

    class _Adb:
        def pull(self, remote, local, timeout=300):
            local.mkdir(parents=True)  # `adb pull <dir>` recurses and creates a directory
            (local / "inner.jpg").write_bytes(b"x")
            return type("R", (), {"ok": True})()

    assert RealDeviceSource(_Adb()).pull_file("/sdcard/Pictures/Screenshots", tmp_path) is None
    assert list(tmp_path.iterdir()) == []  # and the half-pulled tree is removed


def test_a_time_target_gives_media_what_is_left_and_the_run_reports_its_timing(tmp_path):
    import json

    from tests.test_phase2_pipeline import build
    from triage.acquire import MockDeviceSource
    from triage.pipeline import POST_PULL_RESERVE_S, PipelineConfig, run_acquisition

    corpus = tmp_path / "device"
    corpus.mkdir()
    build(corpus)
    cfg = PipelineConfig(
        case_id="TIME-1", examiner="T", cases_root=tmp_path / "cases", media_mode="budget",
        media_cap_bytes=50 * GB, time_budget_s=900,
    )
    case_dir = Path(run_acquisition(MockDeviceSource(corpus), cfg)["case_dir"])
    mb = json.loads((case_dir / "derived" / "media_budget.json").read_text())
    assert 60 <= mb["time_limit_s"] <= 900 - POST_PULL_RESERVE_S  # what is left after the reserve
    timing = json.loads((case_dir / "derived" / "run_timing.json").read_text())
    assert timing["budget_s"] == 900 and timing["within_budget"] is True
    assert [s["stage"] for s in timing["stages"]][:2] == ["init", "device"] and timing["stages"][-1]["stage"] == "report"  # written just before "done"


def test_huge_carved_bodies_are_clipped_before_they_reach_the_dashboard():
    from triage.models import Message
    from triage.pipeline import MAX_BODY_CHARS, _clip_bodies

    rows = [Message(app="telegram", sender="x", body="a" * 5_000_000), Message(app="sms", sender="y", body="short")]
    dicts = [{"body": "b" * 100_000}, {"body": "ok"}]
    assert _clip_bodies(rows) == 1 and _clip_bodies(dicts) == 1
    assert len(rows[0].body) < MAX_BODY_CHARS + 60 and "5000000 characters in total" in rows[0].body
    assert rows[1].body == "short" and dicts[1]["body"] == "ok"
