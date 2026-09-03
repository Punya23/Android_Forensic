"""Tests for triage/intel/case_qa.py — "ask this case" retrieval + grounded Q&A.

Properties under test:
    * passages flatten every wired dataset with real citations, never a fabricated one;
    * retrieval finds the actually-relevant passage and ranks it first;
    * with no LLM configured, the answer is the passages themselves — no synthesis;
    * a working provider only ever adds a synthesis on top of the SAME passages, never
      a different retrieval;
    * nothing raises on empty/malformed input.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from triage.intel.case_qa import Passage, answer_question, build_passages, retrieve  # noqa: E402
from triage.intel.llm import HeuristicProvider, LLMProvider  # noqa: E402


class StubLLM(LLMProvider):
    name = "stub"
    available = True
    degraded_from = ""

    def __init__(self, response="stub answer [P-00001]"):
        self.response = response
        self.calls = []

    def extract_json(self, system, prompt, schema_hint=None):
        return None

    def generate(self, system, prompt):
        self.calls.append((system, prompt))
        return self.response


_DERIVED = {
    "messages": [
        {"body": "Meet at warehouse 9 at 9pm", "sender": "Imran", "timestamp": "2026-07-06T21:00:00Z", "source_file": "m.db"},
        {"body": "ok see you there", "sender": "Rahul", "timestamp": "2026-07-06T21:01:00Z", "source_file": "m.db"},
    ],
    "calls": [{"call_type": "incoming", "number": "+91999", "name": "Rahul", "timestamp": "2026-07-06T20:00:00Z", "source_file": "c.json"}],
    "contacts": [{"name": "Imran K", "number": "+91998"}],
    "browser": [{"title": "warehouse rental listings", "url": "http://x.test", "timestamp": "2026-07-04T00:00:00Z"}],
}


# --- passage building -----------------------------------------------------------
def test_build_passages_covers_every_wired_source():
    passages = build_passages(_DERIVED)
    types = {p.source_type for p in passages}
    assert types == {"messages", "calls", "contacts", "browser"}


def test_passage_citation_is_real_not_fabricated():
    passages = build_passages(_DERIVED)
    msg = next(p for p in passages if p.source_type == "messages" and "warehouse" in p.text)
    assert msg.source_file == "m.db"
    assert msg.timestamp == "2026-07-06T21:00:00Z"


def test_empty_body_rows_are_skipped():
    derived = {"messages": [{"body": "", "sender": "x", "timestamp": "t", "source_file": "m.db"}]}
    assert build_passages(derived) == []


def test_max_per_source_cap():
    derived = {"messages": [{"body": f"msg {i}", "timestamp": "t", "source_file": "m.db"} for i in range(10)]}
    passages = build_passages(derived, max_per_source=3)
    assert len(passages) == 3


def test_non_dict_rows_do_not_crash():
    derived = {"messages": ["not a dict", 123, None]}
    assert build_passages(derived) == []


# --- retrieval ---------------------------------------------------------------
def test_retrieve_finds_the_relevant_passage_first():
    passages = build_passages(_DERIVED)
    top, mode = retrieve("where are they meeting", passages)
    assert mode == "lexical"
    assert top
    assert "warehouse" in top[0].text.lower()


def test_retrieve_empty_passages_returns_empty():
    top, mode = retrieve("anything", [])
    assert top == []
    assert mode == "none"


def test_retrieve_respects_top_k():
    passages = [Passage(id=f"P-{i}", text=f"warehouse {i} meeting", source_type="messages") for i in range(20)]
    top, _ = retrieve("warehouse meeting", passages, top_k=3)
    assert len(top) <= 3


# --- answer_question: no-model path --------------------------------------------
def test_no_provider_returns_passages_only_no_synthesis():
    passages = build_passages(_DERIVED)
    bundle = answer_question("where are they meeting", passages, provider=HeuristicProvider())
    assert bundle["answer"] == ""
    assert bundle["method"] == "retrieval-only"
    assert bundle["passages"]
    assert "no synthesized answer" in bundle["disclaimer"].lower() or "no model" in bundle["disclaimer"].lower()


def test_empty_question_returns_no_op_bundle():
    passages = build_passages(_DERIVED)
    bundle = answer_question("", passages)
    assert bundle["method"] == "none"
    assert bundle["passages"] == []


def test_no_matching_passages_returns_empty_not_an_error():
    passages = build_passages(_DERIVED)
    bundle = answer_question("zzz_no_such_term_qqq", passages, provider=HeuristicProvider())
    assert bundle["passages"] == []
    assert bundle["answer"] == ""


# --- answer_question: LLM synthesis path ----------------------------------------
def test_llm_synthesis_only_runs_over_retrieved_passages():
    passages = build_passages(_DERIVED)
    stub = StubLLM()
    bundle = answer_question("where are they meeting", passages, provider=stub)
    assert bundle["method"] == "llm:stub"
    assert bundle["answer"] == "stub answer [P-00001]"
    # The prompt handed to the model must be built from the SAME passages returned.
    _, prompt = stub.calls[0]
    for p in bundle["passages"]:
        assert p["id"] in prompt


def test_llm_not_consulted_when_nothing_retrieved():
    passages = build_passages(_DERIVED)
    stub = StubLLM()
    answer_question("zzz_no_such_term_qqq", passages, provider=stub)
    assert stub.calls == []


def test_system_prompt_demands_citation_and_non_fabrication():
    from triage.intel.case_qa import _QA_SYSTEM

    assert "ONLY" in _QA_SYSTEM
    assert "cite" in _QA_SYSTEM.lower()
    assert "does not answer" in _QA_SYSTEM.lower()
