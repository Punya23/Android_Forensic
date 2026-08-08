"""End-to-end pipeline test against a generated mock corpus."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.make_corpus import build  # noqa: E402
from triage.acquire import MockDeviceSource  # noqa: E402
from triage.pipeline import PipelineConfig, run_acquisition  # noqa: E402


@pytest.fixture()
def corpus(tmp_path):
    dest = tmp_path / "device"
    dest.mkdir()
    build(dest)
    return dest


def test_full_acquisition(corpus, tmp_path):
    source = MockDeviceSource(corpus)
    cfg = PipelineConfig(
        case_id="TEST-001",
        examiner="Tester",
        legal_authority="warrant#1",
        cases_root=tmp_path / "cases",
    )
    summary = run_acquisition(source, cfg)

    counts = summary["counts"]
    # Core artifacts must all be present.
    assert counts["messages"] > 0
    assert counts["contacts"] == 4
    assert counts["calls"] == 3
    assert counts["media"] == 5  # 3 DCIM + 1 WA + 1 TG
    assert counts["locations"] >= 4  # EXIF GPS + dumpsys fixes
    assert counts["recovered"] > 0  # deleted rows carved
    assert counts["flags"] > 0  # keyword hits
    assert counts["browser"] > 0  # browser history parsed
    assert counts["screenshots"] == 1  # manual screen capture

    # New analysis blocks must be populated.
    assert summary["risk"]["level"] in ("red", "amber", "green")
    assert summary["throughput"]["mb_per_min"] >= 0
    assert summary["graph_stats"]["participants"] > 0

    case_dir = Path(summary["case_dir"])
    assert (case_dir / "report.html").exists()
    assert (case_dir / "manifest.json").exists()
    assert (case_dir / "audit.jsonl").exists()

    # MediaStore trash fusion: analyze_mediastore_trash() must actually be called by the
    # pipeline (it shipped dead — defined, dashboard-rendered, report-rendered, but never
    # invoked — until this wiring). The mock corpus plants one item that is trashed in
    # BOTH the MediaStore catalogue and on disk (.trashed-<epoch>-IMG_evidence.jpg), so a
    # working fusion must report it recovered, not merely present-but-empty.
    import json as _json

    mediastore_trash = _json.loads((case_dir / "derived" / "mediastore_trash.json").read_text())
    assert mediastore_trash["summary"]["total"] >= 1
    assert mediastore_trash["summary"]["file_recovered"] >= 1
    assert any(i["file_recoverable"] for i in mediastore_trash["items"])

    # WhatsApp Media-folder cataloguing: `_process_whatsapp_media` used to look in two
    # locations neither of which the pulled files were ever actually staged/ingested
    # under, so `whatsapp_media` was always empty on a real device. The mock corpus
    # plants a file under the real APP_MEDIA_ROOTS["whatsapp"] path — this must now
    # find it.
    wa_media = _json.loads((case_dir / "derived" / "whatsapp_media.json").read_text())
    assert len(wa_media) >= 1

    # Chain of custody: at least the device-intake and enumerate actions logged.
    audit_lines = (case_dir / "audit.jsonl").read_text().splitlines()
    actions = {__import__("json").loads(l)["action"] for l in audit_lines}
    assert "device.intake" in actions
    assert "artifact.ingest" in actions
    assert "report.generate" in actions


def test_narrative_deleted_messages_recovered(corpus, tmp_path):
    source = MockDeviceSource(corpus)
    cfg = PipelineConfig(
        case_id="TEST-002", examiner="Tester", cases_root=tmp_path / "cases"
    )
    summary = run_acquisition(source, cfg)
    import json

    rec = json.loads(
        (Path(summary["case_dir"]) / "derived" / "recovered.json").read_text(
            encoding="utf-8"
        )
    )
    all_text = " ".join(str(v) for r in rec for v in r["values"] if isinstance(v, str))
    # The deleted WhatsApp evidence must be recoverable from msgstore.db.
    assert "4471" in all_text
    assert "weapon" in all_text.lower()


def test_manifest_hashes_present(corpus, tmp_path):
    source = MockDeviceSource(corpus)
    cfg = PipelineConfig(
        case_id="TEST-003", examiner="Tester", cases_root=tmp_path / "cases"
    )
    summary = run_acquisition(source, cfg)
    import json

    manifest = json.loads((Path(summary["case_dir"]) / "manifest.json").read_text())
    assert len(manifest) > 0
    for art in manifest:
        assert len(art["sha256"]) == 64  # every artifact hashed
        assert art["tier"] == "tier0"


def test_tier1_contacts_requested_on_mock_is_logged_as_skipped(corpus, tmp_path):
    source = MockDeviceSource(corpus)
    cfg = PipelineConfig(
        case_id="TEST-004",
        examiner="Tester",
        cases_root=tmp_path / "cases",
        tier1_contacts=True,
    )
    summary = run_acquisition(source, cfg)
    import json

    audit = [
        json.loads(line)
        for line in (Path(summary["case_dir"]) / "audit.jsonl").read_text().splitlines()
    ]
    tier1_events = [e for e in audit if e["action"] == "tier1.helper.contacts"]
    assert tier1_events
    assert tier1_events[-1]["result"] == "skipped"
