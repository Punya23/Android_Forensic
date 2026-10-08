"""The custody record: phases, the evidence-set hash, and handovers that go into the chain."""

from __future__ import annotations

import pytest

from triage.custody import Case, CaseMeta
from triage.custody_record import (
    TRANSFER_ACTIONS,
    add_transfer,
    build_record,
    build_timeline,
    evidence_set_hash,
)


def _case(tmp_path):
    case = Case.create(tmp_path, CaseMeta(case_id="C-1", examiner="Punya", legal_authority="Warrant 7"))
    case.log("device.intake", "Device recorded")
    case.log("tier1.helper.install", "install helper", alters_device=True)
    case.log("tier1.helper.uninstall", "remove helper", alters_device=True)
    case.log("report.generate", "report")
    return case


def test_timeline_separates_device_changes_from_restoration(tmp_path):
    phases = {p["key"]: p for p in build_timeline(_case(tmp_path).read_audit())}
    assert phases["opened"]["actors"] == ["Punya"]
    assert phases["altered"]["device_altering"] == 1  # the install is a change to the device
    assert phases["restored"]["device_altering"] == 1  # the uninstall puts it back
    assert [p for p in phases] == ["opened", "intake", "altered", "restored", "reported"]


def test_handover_is_in_the_hash_chain_and_tamper_is_detected(tmp_path):
    case = _case(tmp_path)
    entry = add_transfer(case, {"action": "received", "from_person": "Insp. Rao", "to_person": "Punya", "purpose": "analysis"}, "admin")
    assert entry["audit_entry_hash"] == case.audit_head
    rec = build_record(case)
    assert rec["integrity"]["audit_chain"]["valid"] is True
    assert rec["transfers"][0]["to_person"] == "Punya" and rec["transfers"][0]["recorded_by"] == "admin"

    # Editing the handover's audit line afterwards must break the chain, at that line.
    text = case._audit_path.read_text().replace("Insp. Rao", "Someone Else")
    case._audit_path.write_text(text)
    assert build_record(case)["integrity"]["audit_chain"]["valid"] is False


def test_handover_validation(tmp_path):
    case = _case(tmp_path)
    for bad in ({"action": "stolen", "to_person": "x"}, {"action": "received"}, {"action": "received", "to_person": "x" * 201}):
        with pytest.raises(ValueError):
            add_transfer(case, bad, "admin")
    assert add_transfer(case, {"action": "sealed"}, "admin")["action"] == "sealed"  # sealing needs no names
    assert set(TRANSFER_ACTIONS) >= {"received", "released", "sealed"}


def test_evidence_set_hash_is_order_independent_and_content_sensitive():
    class R:
        def __init__(self, h, p): self.sha256, self.source_path = h, p

    a, b = R("aa", "/x"), R("bb", "/y")
    assert evidence_set_hash([a, b]) == evidence_set_hash([b, a])
    assert evidence_set_hash([a, b]) != evidence_set_hash([a, R("bc", "/y")])
