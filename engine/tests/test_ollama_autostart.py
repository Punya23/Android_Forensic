"""The local model must come up by itself and say why when it cannot answer.

Before: a stopped Ollama silently degraded to 'no model', and a model that failed to load
(typically not enough free memory for the 8B) produced an empty stream the UI described as
'no summary'. Now the daemon is started on demand, the daemon's own error is kept and
shown, and a memory failure retries once on the smallest installed chat model.
"""

from __future__ import annotations

import io
import json

from triage.intel import llm
from triage.intel.case_qa import build_passages, stream_answer


class _Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _lines(*objs):
    return _Resp(b"".join(json.dumps(o).encode() + b"\n" for o in objs))


def test_ensure_ollama_starts_the_daemon_when_down(monkeypatch):
    monkeypatch.setenv("SNAGR_OLLAMA_AUTOSTART", "1")
    up = {"v": False}
    spawned = []
    monkeypatch.setattr(llm, "_ollama_up", lambda host, timeout=1.0: up["v"])
    monkeypatch.setattr(llm.shutil, "which", lambda n: "/usr/local/bin/ollama")
    monkeypatch.setattr(llm.subprocess, "Popen", lambda cmd, **kw: (spawned.append(cmd), up.update(v=True))[0])
    assert llm.ensure_ollama_running("http://127.0.0.1:11434", wait=2) is True
    assert spawned == [["/usr/local/bin/ollama", "serve"]]


def test_ensure_ollama_never_spawns_for_a_remote_host_or_missing_binary(monkeypatch):
    monkeypatch.setenv("SNAGR_OLLAMA_AUTOSTART", "1")
    monkeypatch.setattr(llm, "_ollama_up", lambda host, timeout=1.0: False)
    spawned = []
    monkeypatch.setattr(llm.subprocess, "Popen", lambda cmd, **kw: spawned.append(cmd))
    monkeypatch.setattr(llm.shutil, "which", lambda n: "/usr/local/bin/ollama")
    assert llm.ensure_ollama_running("http://10.0.0.5:11434", wait=0.1) is False
    monkeypatch.setattr(llm.shutil, "which", lambda n: None)
    monkeypatch.setattr(llm.os.path, "exists", lambda p: False)
    assert llm.ensure_ollama_running("http://127.0.0.1:11434", wait=0.1) is False
    assert spawned == []


def _provider(monkeypatch, replies):
    monkeypatch.setattr(llm.OllamaProvider, "_ping", lambda self: True)
    monkeypatch.setattr(llm, "list_ollama_models", lambda host=None, timeout=3.0: [
        {"name": "llama3.1:8b", "size_bytes": 5_000_000_000, "embedding_only": False},
        {"name": "llama3.2:1b", "size_bytes": 1_300_000_000, "embedding_only": False},
    ])
    monkeypatch.setattr("triage.intel.hardware.unload_other_models", lambda *a, **k: None)
    sent = []

    def fake_urlopen(req, timeout=None):
        sent.append(json.loads(req.data)["model"])
        return replies.pop(0)

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    p = llm.OllamaProvider(model="llama3.1:8b")
    return p, sent


def test_memory_failure_is_kept_and_retried_on_the_smallest_model(monkeypatch):
    err = _lines({"error": "model requires more system memory (6.1 GiB) than is available (4.0 GiB)"})
    ok = _lines({"message": {"content": "Hello"}}, {"message": {"content": " there"}, "done": True})
    p, sent = _provider(monkeypatch, [err, ok])
    assert "".join(p.stream("s", "p")) == "Hello there"
    assert sent == ["llama3.1:8b", "llama3.2:1b"]


def test_unrecoverable_failure_is_reported_not_swallowed(monkeypatch):
    err = lambda: _lines({"error": "boom"})  # noqa: E731
    p, _ = _provider(monkeypatch, [err(), err()])
    assert list(p.stream("s", "p")) == []
    assert "boom" in p.last_error


def test_empty_answer_note_carries_the_models_error():
    class Dead:
        name = "ollama"
        last_error = "model requires more system memory"

        def is_usable(self):
            return True

        def extract_json(self, *a, **k):
            return None

        def stream(self, *a, **k):
            return iter(())

    rows = {"messages": [{"app": "w", "sender": "Rahul", "body": "cash transfer done", "timestamp": None, "source_file": "x", "confidence": "live"}]}
    done = list(stream_answer("cash", build_passages(rows), provider=Dead()))[-1]
    assert "system memory" in done["note"]


def test_warm_route_returns_immediately(tmp_path, monkeypatch):
    from tests.test_whatsapp_batch_import_route import _make_server_app

    called = []
    monkeypatch.setattr(llm.OllamaProvider, "warm", lambda self: called.append(1) or True)
    monkeypatch.setattr(llm.OllamaProvider, "_ping", lambda self: False)
    app, _ = _make_server_app(tmp_path)
    c = app.test_client()
    t = c.post("/api/auth/login", json={"username": "admin", "password": "snagr-demo"}).get_json()
    c.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {t['token']}"
    c.environ_base["HTTP_X_CSRF_TOKEN"] = t["csrf_token"]
    r = c.post("/api/llm/warm")
    assert r.status_code == 202 and r.get_json() == {"warming": True}
