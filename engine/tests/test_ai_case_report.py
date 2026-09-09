"""AI Case Report section (html_report.py's ``_ai_case_report_section``) — the fix for
the gap the eRakshak honesty invariants exist to catch: an AI-generated narrative that
is computed but never reaches the document a police officer actually reads. Checked
here, not just in ai_summary.py/investigator.py/entity_links.py's own unit tests,
because "the module works" and "the report renders it" are different claims — see
test_report_sections.py's own docstring for why this file exists as integration, not
unit, coverage.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from triage.custody import Case, CaseMeta
from triage.report import generate_report


@pytest.fixture()
def case(tmp_path: Path) -> Case:
    c = Case.create(tmp_path / "cases", CaseMeta(case_id="AICR-1", examiner="Insp. Rao"))
    payload = tmp_path / "evidence.txt"
    payload.write_text("some evidence bytes")
    c.ingest_file(payload, source_path="/sdcard/evidence.txt", tier="tier0", method="mock")
    return c


def render(case: Case) -> str:
    return generate_report(case.root).read_text(encoding="utf-8")


_PROFILE = {
    "crime_type": "drug_trafficking",
    "crime_label": "Drug Trafficking",
    "description": "Suspect Rahul Sharma allegedly supplied drugs.",
    "roles": [
        {
            "role": "accused",
            "label": "Accused",
            "name": "Rahul Sharma",
            "evidence": "named in brief",
            "adverse": True,
        }
    ],
    "suspects": ["Rahul Sharma"],
}


def test_no_case_brief_renders_no_ai_case_report_section(case: Case):
    html = render(case)
    assert "AI Case Report" not in html
    assert "Raw Evidence Data" not in html


def test_ai_case_report_appears_and_precedes_raw_data_when_brief_present(case: Case):
    case.write_derived("case_profile", _PROFILE)
    html = render(case)
    assert "AI Case Report" in html
    assert "Raw Evidence Data" in html
    assert html.index("AI Case Report") < html.index("Raw Evidence Data")


def test_nav_strip_anchors_match_actual_heading_ids(case: Case):
    case.write_derived("case_profile", _PROFILE)
    html = render(case)
    assert 'href="#ai-case-report"' in html
    assert 'id="ai-case-report"' in html
    assert 'href="#raw-evidence-data"' in html
    assert 'id="raw-evidence-data"' in html


def test_ungenerated_summary_shows_honest_reason_not_a_blank_or_fake_narrative(case: Case):
    case.write_derived("case_profile", _PROFILE)
    case.write_derived(
        "ai_evidence_summary",
        {
            "generated": False,
            "reason": "no local model is reachable — see /api/llm/status",
        },
    )
    html = render(case)
    assert "No AI-authored narrative available" in html
    assert "no local model is reachable" in html
    # Never silently absent, and never a fabricated prose block in its place.
    assert "unaffected" in html


def test_generated_summary_narrative_and_disclaimer_render(case: Case):
    case.write_derived("case_profile", _PROFILE)
    case.write_derived(
        "ai_evidence_summary",
        {
            "generated": True,
            "provider": "ollama",
            "model": "qwen2.5:3b-instruct",
            "narrative": "Rahul Sharma exchanged messages consistent with coordination (F-MSG-0007).",
            "disclaimer": "AI-generated investigative aid, independently review before use.",
        },
    )
    html = render(case)
    assert "Rahul Sharma exchanged messages" in html
    assert "F-MSG-0007" in html
    assert "independently review before use" in html


def test_hostile_narrative_text_is_escaped_not_executed(case: Case):
    case.write_derived("case_profile", _PROFILE)
    case.write_derived(
        "ai_evidence_summary",
        {
            "generated": True,
            "narrative": "<script>alert(1)</script>",
            "disclaimer": "",
        },
    )
    html = render(case)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_investigation_hypotheses_and_status_render(case: Case):
    case.write_derived("case_profile", _PROFILE)
    case.write_derived(
        "investigation_trace",
        {
            "hypotheses": [
                {
                    "id": "H-CHANNEL-GAP",
                    "question": "Which named parties have a Contacts entry but no "
                    "message/call finding?",
                    "status": "answered",
                    "detail": "No gap found for Rahul Sharma.",
                }
            ],
            "linked_findings": [],
            "narrative": "",
            "disclaimer": "Investigative lead generation, not a determination of guilt.",
        },
    )
    html = render(case)
    assert "Deep investigation" in html
    assert "Which named parties have a Contacts entry" in html
    # "answered" is rendered lowercase; text-transform:uppercase is CSS-only styling.
    assert ">answered<" in html
    assert "not a determination of guilt" in html


def test_entity_links_table_and_reason_both_render_honestly(case: Case):
    case.write_derived("case_profile", _PROFILE)
    case.write_derived(
        "entity_links",
        {
            "entities": [
                {
                    "entity": "Rahul Sharma",
                    "occurrence_count": 3,
                    "datasets": ["calls", "contacts", "messages"],
                    "occurrences": [],
                    "truncated": 0,
                }
            ],
            "entity_count": 1,
            "passages_scanned": 3,
            "reason": "",
            "disclaimer": "Substring match, not identity resolution.",
        },
    )
    html = render(case)
    assert "Entity cross-links" in html
    assert "Rahul Sharma" in html
    assert "calls, contacts, messages" in html
    assert "Substring match, not identity resolution." in html


def test_entity_links_empty_reason_is_shown_not_a_blank_table(case: Case):
    case.write_derived("case_profile", _PROFILE)
    case.write_derived(
        "entity_links",
        {
            "entities": [],
            "entity_count": 0,
            "passages_scanned": 0,
            "reason": "no messages/calls/browser/location/contact rows were collected",
            "disclaimer": "Substring match, not identity resolution.",
        },
    )
    html = render(case)
    assert "no messages/calls/browser/location/contact rows were collected" in html
