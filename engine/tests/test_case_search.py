"""The "Ask This Case" search engine: question / case brief -> JSON search spec -> real grep.

The old Ask embedded every passage through Ollama on every question (minutes on a real
case) and ranked a word-bag, so a literal name or number could be missed. The engine now
greps the case's evidence directly; the local model only helps pick the terms.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from triage.intel.case_qa import Passage, answer_question  # noqa: E402
from triage.intel.llm import LLMProvider  # noqa: E402
from triage.intel.search import (  # noqa: E402
    SearchSpec,
    Term,
    brief_spec,
    deterministic_spec,
    grep,
    llm_spec,
)


class FakeLLM(LLMProvider):
    name = "fake"
    available = True
    degraded_from = ""

    def __init__(self, payload=None, delay=0.0, boom=False):
        self.payload, self.delay, self.boom, self.calls = payload, delay, boom, 0

    def extract_json(self, system, prompt, schema_hint=None):
        self.calls += 1
        time.sleep(self.delay)
        if self.boom:
            raise RuntimeError("model fell over")
        return self.payload

    def generate(self, system, prompt):
        return None


def P(i, text, **kw):
    return Passage(id=f"P-{i:05d}", text=text, source_type=kw.pop("source_type", "messages"), **kw)


def texts(spec: SearchSpec) -> dict[str, Term]:
    return {t.text.lower(): t for t in spec.terms}


# --- deterministic term extraction ------------------------------------------------------
def test_question_filler_is_dropped_and_names_numbers_are_kept():
    spec = deterministic_spec("What did Rahul say about the payment to 9820044711?")
    got = texts(spec)
    assert {"rahul", "payment", "9820044711"} <= set(got)
    assert not {"what", "did", "say", "about", "the"} & set(got)
    assert got["rahul"].kind == "name" and got["9820044711"].kind == "number"
    assert got["rahul"].weight > got["payment"].weight


def test_quoted_text_is_one_phrase_and_upi_ids_survive():
    got = texts(deterministic_spec('find "pier 4" and shop@paytm'))
    assert got["pier 4"].kind == "phrase"
    assert "shop@paytm" in got


def test_deterministic_spec_is_capped_and_never_empty_for_real_words():
    assert len(deterministic_spec(" ".join(f"word{i}x" for i in range(100))).terms) <= 24
    assert deterministic_spec("the of and").terms == []


# --- grep: real matches -----------------------------------------------------------------
def test_grep_finds_literal_name_and_number_a_wordbag_would_miss():
    passages = [
        P(1, "Imran: lunch?"),
        P(2, "Rahul: the cash transfer to account 4471 is done"),
        P(3, "Call from +91 98200 44711 (Imran K)", source_type="calls"),
    ]
    assert [h.passage.id for h in grep(deterministic_spec("account 4471"), passages).hits] == ["P-00002"]
    ids = [h.passage.id for h in grep(deterministic_spec("98200-44711"), passages).hits]
    assert ids == ["P-00003"], "a number matches across +91 / spaces / dashes"


def test_stems_match_word_forms_but_not_inside_other_words():
    passages = [P(1, "Meet at warehouse 9"), P(2, "payments pending"), P(3, "the location is fine")]
    assert [h.passage.id for h in grep(deterministic_spec("meeting"), passages).hits] == ["P-00001"]
    assert [h.passage.id for h in grep(deterministic_spec("payment"), passages).hits] == ["P-00002"]
    assert grep(deterministic_spec("cat"), passages).hits == []


def test_rarer_terms_outrank_common_ones_and_hits_report_highlights():
    passages = [P(i, "meet me later") for i in range(1, 20)] + [P(99, "meet at pier four tonight")]
    result = grep(deterministic_spec("meet pier"), passages)
    top = result.hits[0]
    assert top.passage.id == "P-00099"
    assert {"meet", "pier"} <= {m.lower() for m in top.matched}
    assert all(top.passage.text[s:e].lower() in ("meet", "pier") for s, e in top.spans)
    assert result.term_hits["pier"] == 1 and result.term_hits["meet"] == 20


def test_boost_terms_never_create_hits_on_their_own():
    spec = SearchSpec(terms=[Term("payment", "word", 1.0, "question"), Term("imran", "name", 1.0, "brief", role="boost")])
    passages = [P(1, "Imran: lunch?"), P(2, "payment done"), P(3, "Imran: payment done")]
    ids = [h.passage.id for h in grep(spec, passages).hits]
    assert "P-00001" not in ids and ids[0] == "P-00003", "boost ranks, it does not match"


def test_grep_is_fast_on_a_very_large_case():
    passages = [P(i, f"message {i} about nothing in particular with some words") for i in range(60000)]
    passages[40000] = P(40000, "the hawala payment to shop@paytm")
    t0 = time.perf_counter()
    result = grep(deterministic_spec("hawala shop@paytm"), passages)
    assert [h.passage.id for h in result.hits] == ["P-40000"]
    assert time.perf_counter() - t0 < 3.0


# --- local model: terms only, validated, never load-bearing -----------------------------
def test_llm_adds_variants_but_literal_question_terms_are_always_kept():
    base = deterministic_spec("did Rahul mention paisa transfer")
    fake = FakeLLM({"terms": [{"term": "hawala", "kind": "word", "weight": 3}, {"term": "angadiya", "kind": "word"}]})
    spec = llm_spec("did Rahul mention paisa transfer", fake, base)
    got = texts(spec)
    assert spec.method == "llm:fake"
    assert {"rahul", "paisa", "hawala", "angadiya"} <= set(got)
    assert got["hawala"].source == "llm" and got["rahul"].source == "question"


def test_a_repeated_question_reuses_the_models_terms_without_a_second_call():
    fake = FakeLLM({"terms": [{"term": "hawala"}]})
    base = deterministic_spec("Rahul paisa")
    llm_spec("Rahul paisa", fake, base)
    again = llm_spec("Rahul paisa", fake, base)
    assert fake.calls == 1 and "hawala" in texts(again) and again.method == "llm:fake"


def test_llm_output_is_sanitised():
    junk = {"terms": [
        {"term": "(a+)+$", "kind": "regex", "weight": 3},      # regex is never accepted
        {"term": "x" * 200, "kind": "word"},                    # too long
        {"term": "", "kind": "word"}, {"term": 5, "kind": "word"}, "not a dict",
        {"term": "ok term", "kind": "weird", "weight": 99},     # bad kind -> word, weight clamped
    ] + [{"term": f"extra{i}", "kind": "word"} for i in range(60)]}
    spec = llm_spec("q", FakeLLM(junk), deterministic_spec("q"))
    got = texts(spec)
    assert "(a+)+$" not in got and not any(len(k) > 64 for k in got)
    assert got["ok term"].kind == "word" and got["ok term"].weight <= 3
    assert sum(1 for t in spec.terms if t.source == "llm") <= 20


@pytest.mark.parametrize("fake", [FakeLLM(None), FakeLLM({"nope": 1}), FakeLLM(boom=True)])
def test_llm_failure_degrades_to_deterministic_and_says_so(fake):
    base = deterministic_spec("Rahul payment")
    spec = llm_spec("Rahul payment", fake, base)
    assert spec.method == "deterministic" and set(texts(spec)) == set(texts(base))
    assert spec.notes


def test_slow_model_is_cut_off_and_search_still_runs():
    t0 = time.perf_counter()
    spec = llm_spec("Rahul payment", FakeLLM({"terms": []}, delay=3.0), deterministic_spec("Rahul payment"), timeout=0.2)
    assert time.perf_counter() - t0 < 1.5
    assert spec.method == "deterministic" and any("timed out" in n for n in spec.notes)


class WriterLLM(FakeLLM):
    """Terms from extract_json, a synthesis from generate (optionally slow)."""

    def __init__(self, gen_delay=0.0, **kw):
        super().__init__(**kw)
        self.gen_delay, self.generated = gen_delay, 0

    def generate(self, system, prompt):
        self.generated += 1
        time.sleep(self.gen_delay)
        return "Rahul says the cash moved [P-00001]"


def test_synthesis_is_optional_so_grep_results_never_wait_for_the_model():
    llm = WriterLLM(payload={"terms": []})
    bundle = answer_question("Rahul cash", [P(1, "Rahul: cash done")], provider=llm, synthesize=False)
    assert llm.generated == 0 and bundle["answer"] == "" and bundle["passages"]
    assert answer_question("Rahul cash", [P(1, "Rahul: cash done")], provider=llm)["answer"].startswith("Rahul says")


def test_slow_synthesis_is_cut_off_and_the_hits_still_come_back():
    t0 = time.perf_counter()
    bundle = answer_question("Rahul cash", [P(1, "Rahul: cash done")], provider=WriterLLM(gen_delay=3.0, payload={"terms": []}),
                             synth_timeout=0.2)
    assert time.perf_counter() - t0 < 1.5
    assert bundle["answer"] == "" and bundle["passages"] and any("summary" in n for n in bundle["search"]["notes"])


def test_a_model_still_busy_with_an_earlier_request_is_skipped_not_queued():
    slow = FakeLLM({"terms": []}, delay=1.0)
    llm_spec("Rahul", slow, deterministic_spec("Rahul"), timeout=0.1)  # times out; worker keeps running
    second = FakeLLM({"terms": [{"term": "hawala"}]})
    spec = llm_spec("Rahul", second, deterministic_spec("Rahul"))
    assert second.calls == 0 and spec.method == "deterministic" and any("busy" in n for n in spec.notes)
    time.sleep(1.2)  # let the slow worker finish so later tests see a free model


# --- case brief JSON -> search spec -----------------------------------------------------
BRIEF = {"suspects": ["Imran K"], "victims": [], "other_entities": ["Mumbai"], "locations": ["pier 4"],
         "keywords": ["narcotic", "consignment"]}


def test_brief_json_becomes_match_terms_with_roles_weighted():
    got = texts(brief_spec(BRIEF))
    assert {"imran k", "imran", "mumbai", "pier 4", "narcotic", "consignment"} <= set(got)
    assert got["imran k"].weight > got["mumbai"].weight and got["imran k"].source == "brief"
    assert all(t.role == "match" for t in got.values())
    assert brief_spec({}).terms == []


# --- answers are the real grep output ---------------------------------------------------
def test_answer_returns_hits_with_the_terms_that_were_searched():
    passages = [P(1, "Rahul: The cash transfer to account 4471 is done", source_file="chat.db"), P(2, "unrelated")]
    bundle = answer_question("what did Rahul say about account 4471", passages, brief=BRIEF)
    assert [p["id"] for p in bundle["passages"]] == ["P-00001"]
    assert bundle["passages"][0]["matched"] and bundle["passages"][0]["spans"]
    assert bundle["search"]["method"] == "deterministic"
    assert {t["text"].lower() for t in bundle["search"]["terms"]} >= {"rahul", "4471"}
    assert bundle["search"]["scanned"] == 2 and bundle["search"]["term_hits"]["4471"] == 1


def test_no_hits_still_shows_what_was_searched_for():
    bundle = answer_question("zebra", [P(1, "hello")])
    assert bundle["passages"] == [] and bundle["search"]["term_hits"] == {"zebra": 0}


# --- through the HTTP route -------------------------------------------------------------
@pytest.fixture()
def client(tmp_path):
    from tests.test_whatsapp_batch_import_route import _make_server_app
    from triage.custody import Case, CaseMeta

    app, _ = _make_server_app(tmp_path)
    case = Case.create(tmp_path, CaseMeta(case_id="ASK-1", examiner="Insp. Rao"))
    case.write_derived("messages", [
        {"app": "whatsapp", "sender": "Rahul", "body": "The cash transfer to account 4471 is done",
         "timestamp": "2026-07-06T21:00:00Z", "source_file": "chat.db"},
        {"app": "sms", "sender": "Mom", "body": "dinner at 8", "timestamp": "2026-07-06T22:00:00Z", "source_file": "sms.db"},
    ])
    case.write_derived("notifications", [{"title": "Imran K", "text": "consignment lands 2140, pier 4",
                                          "timestamp": "2026-07-06T18:20:00Z", "app_name": "WhatsApp"}])
    c = app.test_client()
    data = c.post("/api/auth/login", json={"username": "admin", "password": "snagr-demo"}).get_json()
    c.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {data['token']}"
    c.environ_base["HTTP_X_CSRF_TOKEN"] = data["csrf_token"]
    c.case = case
    return c


def ask(c, **body):
    return c.post("/api/case/ASK-1/ask", json={"llm_provider": "heuristic", **body})


def test_ask_route_greps_real_hits_quickly_without_any_embedding_call(client):
    t0 = time.perf_counter()
    r = ask(client, question="what did Rahul say about account 4471?")
    assert r.status_code == 200 and time.perf_counter() - t0 < 2.0
    body = r.get_json()
    assert [p["source_file"] for p in body["passages"]] == ["chat.db"]
    assert body["passages_available"] == 3 and body["search"]["scanned"] == 3
    assert ask(client, question="").status_code == 400


def test_ask_route_searches_notifications_too(client):
    assert ask(client, question="where does the consignment land").get_json()["passages"][0]["source_type"] == "notifications"


def test_ask_route_sees_evidence_added_after_the_first_question(client):
    assert ask(client, question="hawala").get_json()["passages"] == []
    msgs = client.case.read_derived("messages") + [
        {"app": "sms", "sender": "X", "body": "hawala settled", "timestamp": "t", "source_file": "n.db"}]
    client.case.write_derived("messages", msgs)
    assert len(ask(client, question="hawala").get_json()["passages"]) == 1, "a stale passage cache hid new evidence"


def test_brief_scope_route_uses_the_stored_case_profile_json(client):
    assert ask(client, scope="brief").status_code == 409, "no brief on this case"
    client.case.write_derived("case_profile", {"suspects": ["Imran K"], "locations": ["pier 4"], "keywords": []})
    body = ask(client, scope="brief").get_json()
    assert [p["source_type"] for p in body["passages"]] == ["notifications"]
    assert body["search"]["terms"][0]["source"] == "brief"


def test_brief_scope_greps_the_brief_terms_and_needs_no_question():
    passages = [P(1, "pier 4 at midnight"), P(2, "lunch")]
    bundle = answer_question("", passages, brief=BRIEF, scope="brief")
    assert [p["id"] for p in bundle["passages"]] == ["P-00001"]
    assert bundle["search"]["terms"][0]["source"] == "brief"


# --- carved blobs must not crowd out the real messages --------------------------------------
def _msg_row(body, conf="live", sender="Rahul"):
    return {"app": "whatsapp", "sender": sender, "body": body, "timestamp": None, "source_file": "x", "confidence": conf}


def test_short_message_outranks_a_huge_blob_containing_the_same_terms():
    from triage.intel.case_qa import build_passages
    from triage.intel.search import deterministic_spec, grep

    blob = "\x00junk" * 3000 + " cash transfer weapon " + "\x01junk" * 3000
    ps = build_passages({"messages": [_msg_row(blob, "carved", "<recovered>"), _msg_row("The cash transfer to account 4471 is done")]})
    hits = grep(deterministic_spec("cash transfer weapon"), ps, top_k=5).hits
    assert hits[0].passage.confidence == "live"


def test_near_identical_blobs_collapse_to_one_hit_but_short_repeats_stay():
    from triage.intel.case_qa import build_passages
    from triage.intel.search import deterministic_spec, grep

    blobs = [_msg_row(f"{i}" * 800 + "x" * 100 + " the cash transfer is done " + "z" * 800, "carved", "<recovered>") for i in range(6)]
    repeats = [_msg_row("ok send the cash"), _msg_row("ok send the cash")]
    ps = build_passages({"messages": blobs + repeats})
    hits = grep(deterministic_spec("cash"), ps, top_k=10).hits
    assert sum(1 for h in hits if h.passage.confidence == "carved") == 1
    assert sum(1 for h in hits if h.passage.text.endswith("send the cash")) == 2


def test_instruction_verbs_are_not_search_terms():
    # "search for shubham call" once searched for the word "search" and matched 37 Google URLs.
    from triage.intel.search import deterministic_spec

    texts = [t.text.lower() for t in deterministic_spec("search for shubham call").terms]
    assert texts == ["shubham", "call"]
    assert [t.text.lower() for t in deterministic_spec("please look up and list anything related to Rahul").terms] == ["rahul"]
