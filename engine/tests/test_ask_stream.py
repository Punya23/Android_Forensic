"""Ask This Case streams: grep hits first (instant), then the model's answer token by token.

The blocking /ask path made the examiner stare at nothing until a whole answer was ready.
stream_answer() yields the real matches immediately, then streams a model answer built from
only the best few passages, and degrades to the matches alone when there is no usable model.
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.test_whatsapp_batch_import_route import _make_server_app  # noqa: E402
from triage.custody import Case, CaseMeta  # noqa: E402
from triage.intel import search  # noqa: E402
from triage.intel.case_qa import SYNTH_TOP_N, build_passages, stream_answer  # noqa: E402

ROWS = {
    "messages": [
        {"app": "whatsapp", "sender": "Rahul", "body": f"the cash transfer {i} is done", "timestamp": f"2026-07-0{1 + i % 9}T10:00:00Z",
         "source_file": "msgstore.db", "confidence": "live"}
        for i in range(20)
    ]
}


class FakeModel:
    name = "fake"
    available = True

    def __init__(self, chunks=("It ", "was ", "done [P-00001]."), usable=True):
        self.chunks, self.usable, self.prompts = chunks, usable, []

    def is_usable(self):
        return self.usable

    def extract_json(self, *a, **k):
        return None

    def generate(self, system, prompt):
        return "".join(self.chunks)

    def stream(self, system, prompt):
        self.prompts.append(prompt)
        yield from self.chunks


def _events(model, question="who did the cash transfer", **kw):
    return list(stream_answer(question, build_passages(ROWS), provider=model, **kw))


def test_hits_arrive_before_any_token_then_done():
    ev = _events(FakeModel())
    kinds = [e["type"] for e in ev]
    assert kinds[0] == "search" and kinds[-1] == "done"
    assert kinds.count("token") == 3
    assert ev[0]["bundle"]["passages"], "grep matches are in the first event"
    assert "".join(e["text"] for e in ev if e["type"] == "token") == "It was done [P-00001]."
    assert ev[-1]["method"] == "llm:fake"


def test_model_sees_only_the_best_few_passages():
    model = FakeModel()
    _events(model)
    assert model.prompts[0].count("[P-") <= SYNTH_TOP_N


def test_no_usable_model_returns_matches_only():
    ev = _events(FakeModel(usable=False))
    assert [e["type"] for e in ev] == ["search", "done"]
    assert ev[-1]["method"] == "grep"
    assert ev[0]["bundle"]["passages"]


def test_busy_model_degrades_to_matches_with_a_reason():
    assert search._MODEL_BUSY.acquire(blocking=False)
    try:
        ev = _events(FakeModel(), busy_wait=0.05)
    finally:
        search._MODEL_BUSY.release()
    assert [e["type"] for e in ev] == ["search", "done"]
    assert "busy" in ev[-1]["note"]


def test_waits_for_an_earlier_request_to_finish_then_streams():
    # a cold model's term-extraction call can still hold the lock when the summary starts
    assert search._MODEL_BUSY.acquire(blocking=False)
    threading.Timer(0.15, search._MODEL_BUSY.release).start()
    ev = _events(FakeModel(), busy_wait=3.0)
    assert [e["type"] for e in ev].count("token") == 3


def test_model_lock_is_released_after_streaming():
    _events(FakeModel())
    assert search._MODEL_BUSY.acquire(blocking=False)
    search._MODEL_BUSY.release()


def _client(tmp_path, case_id):
    app, _ = _make_server_app(tmp_path)
    case = Case.create(tmp_path, CaseMeta(case_id=case_id, examiner="Insp. Rao"))
    c = app.test_client()
    data = c.post("/api/auth/login", json={"username": "admin", "password": "snagr-demo"}).get_json()
    c.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {data['token']}"
    c.environ_base["HTTP_X_CSRF_TOKEN"] = data["csrf_token"]
    return c, case


def test_stream_route_emits_ndjson(tmp_path):
    c, case = _client(tmp_path, "S-1")
    case.write_derived("messages", ROWS["messages"])
    resp = c.post("/api/case/S-1/ask/stream", json={"question": "cash transfer", "llm_provider": "heuristic"})
    assert resp.status_code == 200
    assert resp.mimetype == "application/x-ndjson"
    events = [json.loads(line) for line in resp.get_data(as_text=True).splitlines() if line]
    assert events[0]["type"] == "search" and events[0]["bundle"]["passages"]
    assert events[-1]["type"] == "done"


def test_stream_route_requires_a_question(tmp_path):
    c, _ = _client(tmp_path, "S-2")
    assert c.post("/api/case/S-2/ask/stream", json={"question": ""}).status_code == 400


def test_abandoned_stream_frees_the_model():
    # a client disconnect closes the generator mid-answer; the model lock must be released
    gen = stream_answer("cash transfer", build_passages(ROWS), provider=FakeModel())
    next(gen)  # search event
    next(gen)  # first token — lock now held
    gen.close()
    freed = []

    def probe():
        if search._MODEL_BUSY.acquire(blocking=False):
            search._MODEL_BUSY.release()
            freed.append(True)

    t = threading.Thread(target=probe)
    t.start()
    t.join()
    assert freed


def test_model_prompt_windows_on_the_match_and_cleans_binary_junk():
    junk = "\x00\x01�\x07" * 200  # a carved page: hundreds of bytes before the real text
    rows = {"messages": [{"app": "whatsapp", "sender": "<recovered>", "body": junk + " the cash transfer is done " + junk,
                           "timestamp": None, "source_file": "msgstore.db", "confidence": "carved"}]}
    model = FakeModel()
    list(stream_answer("cash transfer", build_passages(rows), provider=model))
    prompt = model.prompts[0]
    assert "cash transfer is done" in prompt, "the window must reach the matched text, not the start of the blob"
    assert not any(c in prompt for c in "\x00\x01\x07�"), "binary junk must not reach the model"


def test_prompt_leads_with_live_evidence_and_skips_junk_snippets():
    rows = {"messages": [
        {"app": "w", "sender": "<recovered>", "body": "cash transfer " + "\x01\x02ÿþ" * 60, "timestamp": None,
         "source_file": "x", "confidence": "carved"},
        {"app": "w", "sender": "<freeblock-carved>", "body": "the cash transfer to account 4471 is done", "timestamp": None,
         "source_file": "x", "confidence": "carved"},
        {"app": "w", "sender": "Rahul", "body": "the cash transfer to account 4471 is done", "timestamp": None,
         "source_file": "x", "confidence": "live"},
    ]}
    model = FakeModel()
    list(stream_answer("cash transfer", build_passages(rows), provider=model))
    lines = [ln for ln in model.prompts[0].splitlines() if ln.startswith("[P-")]
    assert "Rahul" in lines[0], "live evidence must come first"
    assert all("ÿ" not in ln for ln in lines), "a mostly-junk snippet must not be offered to the model"
