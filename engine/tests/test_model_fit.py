"""Model-fit checker: which local model can this laptop actually carry?

The old logic picked the LARGEST installed model whatever the machine, and a RAM-only tier
table sent any 16 GB laptop to an 8B model — so Ollama sat at ~20 GB on a 16 GB Mac and
every prompt crawled through swap. The checker sizes each model's real footprint (weights +
KV cache + runtime overhead) against what is actually free.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from triage.intel.hardware import (  # noqa: E402
    CATALOG,
    assess_models,
    memory_budget_gb,
    model_footprint_gb,
    recommend_model,
    select_model,
)

CATALOG_NAMES = [c[0] for c in CATALOG]


def hw(total, available=None, **kw):
    return {"ram_gb": total, "available_ram_gb": available, **kw}


def installed(name, gb, params):
    return {"name": name, "size_bytes": int(gb * 1e9), "parameter_size": params, "embedding_only": False}


def by_name(rows):
    return {r["name"]: r for r in rows}


# --- footprint & budget ------------------------------------------------------------------
def test_footprint_is_weights_plus_kv_cache_plus_overhead_and_grows_with_context():
    small = model_footprint_gb(8.0, 4.9, num_ctx=4096)
    big = model_footprint_gb(8.0, 4.9, num_ctx=32768)
    assert 5.5 < small < 6.5, small
    assert big > small + 3, "a long context costs gigabytes of KV cache"


def test_budget_uses_what_is_free_not_just_what_is_installed():
    assert memory_budget_gb(hw(16, available=4)) < memory_budget_gb(hw(16, available=12))
    assert memory_budget_gb(hw(16, available=14)) <= 16 * 0.7, "never more than 70% of the machine"
    assert memory_budget_gb(hw(8, available=1)) == 0.0, "OS + dashboard headroom comes off first"
    assert memory_budget_gb(hw(16)) > 0, "unknown free RAM is assumed 60% free, not zero"


# --- 16 GB laptop, 8B installed: use it when memory allows, not when the laptop is busy ----------
def test_a_16gb_laptop_runs_the_8b_when_memory_allows_and_not_when_busy():
    roomy = assess_models(hw(16, available=9), [installed("llama3.1:8b", 4.9, "8.0B")])
    assert select_model(roomy, installed_only=True, allow_tight=True) == "llama3.1:8b"
    busy = by_name(assess_models(hw(16, available=4), [installed("llama3.1:8b", 4.9, "8.0B")]))
    assert busy["llama3.1:8b"]["verdict"] == "too_big"
    assert select_model(list(busy.values()), installed_only=True, allow_tight=True) is None


def test_a_roomy_machine_gets_the_strongest_model_that_fits():
    assert select_model(assess_models(hw(64, available=48), [])) == "qwen2.5:32b-instruct"
    assert select_model(assess_models(hw(30, available=22), [])) == "qwen2.5:14b-instruct"


def test_nothing_fits_a_tiny_machine_and_the_checker_says_so():
    rows = assess_models(hw(4, available=2), [])
    assert select_model(rows) is None
    assert all(r["verdict"] == "too_big" and "budget" in r["reason"] for r in rows)


# --- installed vs needs-download -----------------------------------------------------------
def test_rows_mark_installed_and_downloadable_models_and_never_list_embedders():
    inst = [installed("llama3.2:1b", 1.3, "1.2B"), installed("nomic-embed-text", 0.27, "137M") | {"embedding_only": True}]
    rows = by_name(assess_models(hw(16, available=12), inst))
    assert rows["llama3.2:1b"]["installed"] is True
    assert rows["llama3.1:8b"]["installed"] is False and rows["llama3.1:8b"]["needs_download_gb"] == 4.9
    assert "nomic-embed-text" not in rows
    assert set(CATALOG_NAMES) <= set(rows)


def test_installed_only_selection_never_proposes_a_download():
    rows = assess_models(hw(64, available=48), [installed("llama3.2:1b", 1.3, "1.2B")])
    assert select_model(rows, installed_only=True) == "llama3.2:1b"
    assert select_model(rows) == "qwen2.5:32b-instruct"


def test_installed_model_outside_the_catalog_is_sized_from_what_ollama_reports():
    rows = by_name(assess_models(hw(16, available=12), [installed("mistral:7b", 4.1, "7.2B")]))
    assert rows["mistral:7b"]["installed"] and 5 < rows["mistral:7b"]["footprint_gb"] < 6


# --- GPU / VRAM ------------------------------------------------------------------------------
def test_dedicated_gpu_that_cannot_hold_the_model_is_flagged_as_slow_cpu_offload():
    rows = by_name(assess_models(hw(32, available=24, vram_gb=4, vram_kind="dedicated"), []))
    assert rows["llama3.1:8b"]["speed"] == "cpu-offload"
    assert rows["llama3.2:1b"]["speed"] == "gpu"


def test_apple_unified_memory_counts_as_gpu_memory():
    assert by_name(assess_models(hw(16, available=12, vram_gb=10.6, vram_kind="unified"), []))["llama3.2:1b"]["speed"] == "gpu"


def test_no_gpu_means_cpu():
    assert by_name(assess_models(hw(16, available=12), []))["llama3.2:1b"]["speed"] == "cpu"


# --- the engine's auto-selection uses it -------------------------------------------------------
def _autodetect(monkeypatch, machine, models):
    from triage.intel import hardware, llm

    monkeypatch.setenv("SNAGR_LLM_MODEL", "")  # empty so autodetect may set it; restored on teardown
    monkeypatch.setattr(llm, "list_ollama_models", lambda *a, **k: models)
    monkeypatch.setattr(hardware, "detect_hardware", lambda: machine)
    monkeypatch.setattr(hardware, "ensure_local_model", lambda *a, **k: {"action": "none", "reason": "test"})
    return llm.autodetect_and_configure(force=True)


def test_autodetect_picks_the_strongest_pulled_model_the_free_memory_carries(monkeypatch):
    models = [installed("llama3.1:8b", 4.9, "8.0B"), installed("llama3.2:1b", 1.3, "1.2B")]
    result = _autodetect(monkeypatch, hw(16, available=9), models)
    assert result["autodetected"] and result["model"] == "llama3.1:8b"
    busy = _autodetect(monkeypatch, hw(16, available=3.5), models)
    assert busy["autodetected"] and busy["model"] == "llama3.2:1b"


def test_autodetect_stays_on_heuristic_and_says_why_when_no_pulled_model_fits(monkeypatch):
    result = _autodetect(monkeypatch, hw(8, available=3), [installed("llama3.1:8b", 4.9, "8.0B")])
    assert not result["autodetected"] and result["provider"] == "heuristic"
    assert "fit" in result["reason"] and "llama3.1:8b" in result["reason"]


def test_fit_route_reports_the_machine_every_model_and_the_choice(tmp_path, monkeypatch):
    from tests.test_whatsapp_batch_import_route import _make_server_app
    from triage.intel import hardware

    monkeypatch.setattr(hardware, "detect_hardware", lambda: hw(16, available=9, gpu="apple_silicon", vram_gb=10.6, vram_kind="unified"))
    monkeypatch.setattr(hardware, "_ollama_loaded", lambda host=None: [])  # not whatever this machine has resident
    app, _ = _make_server_app(tmp_path)
    c = app.test_client()
    token = c.post("/api/auth/login", json={"username": "admin", "password": "snagr-demo"}).get_json()["token"]
    body = c.get("/api/llm/fit", headers={"Authorization": f"Bearer {token}"}).get_json()
    assert body["hardware"]["ram_gb"] == 16 and body["budget_gb"] == 8.0
    assert {"llama3.1:8b", "llama3.2:1b"} <= {m["name"] for m in body["models"]}
    assert body["download_recommendation"] == "llama3.1:8b"
    assert body["use_installed"] == "llama3.1:8b"


def test_a_busy_laptop_with_a_few_gb_free_can_still_run_the_smallest_model():
    """Free memory already excludes what the OS and dashboard occupy, so the headroom kept on
    top of it is small — otherwise a normally-busy 16 GB laptop is told nothing fits."""
    rows = assess_models(hw(16, available=4.7), [installed("llama3.2:1b", 1.3, "1.2B")])
    assert select_model(rows, installed_only=True, max_params_b=4.0) == "llama3.2:1b"


# --- small model by default ---------------------------------------------------------------------
def test_a_size_cap_keeps_a_big_machine_on_a_small_model():
    rows = assess_models(hw(64, available=48), [installed("llama3.1:8b", 4.9, "8.0B"), installed("llama3.2:1b", 1.3, "1.2B")])
    assert select_model(rows, installed_only=True) == "llama3.1:8b"
    assert select_model(rows, installed_only=True, max_params_b=4.0) == "llama3.2:1b"
    assert select_model(rows, max_params_b=4.0) == "qwen2.5:3b-instruct"


def test_autodetect_honours_the_operators_size_cap(monkeypatch):
    models = [installed("llama3.1:8b", 4.9, "8.0B"), installed("llama3.2:1b", 1.3, "1.2B")]
    assert _autodetect(monkeypatch, hw(64, available=48), models)["model"] == "llama3.1:8b"  # default cap is 8B
    monkeypatch.setenv("SNAGR_LLM_MAX_PARAMS_B", "4")  # operator wants a small, quick model
    assert _autodetect(monkeypatch, hw(64, available=48), models)["model"] == "llama3.2:1b"


# --- never more than one model resident ---------------------------------------------------------
def test_unload_other_models_evicts_everything_but_the_one_in_use(monkeypatch):
    from triage.intel import hardware

    posts = []
    monkeypatch.setattr(hardware, "_ollama_loaded", lambda host=None: [
        {"name": "llama3.2:1b"}, {"name": "llama3.1:8b"}, {"name": "nomic-embed-text:latest"}])
    monkeypatch.setattr(hardware, "_post_json", lambda url, body, timeout=5.0: posts.append(body) or True)
    gone = hardware.unload_other_models("llama3.2:1b")
    assert gone == ["llama3.1:8b", "nomic-embed-text:latest"]
    assert posts == [{"model": "llama3.1:8b", "keep_alive": 0}, {"model": "nomic-embed-text:latest", "keep_alive": 0}]


def test_an_untagged_model_name_matches_its_latest_tag(monkeypatch):
    from triage.intel import hardware

    monkeypatch.setattr(hardware, "_ollama_loaded", lambda host=None: [{"name": "nomic-embed-text:latest"}])
    monkeypatch.setattr(hardware, "_post_json", lambda *a, **k: pytest.fail("evicted the model in use"))
    assert hardware.unload_other_models("nomic-embed-text") == []


class _Resp:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        import json
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_every_chat_call_evicts_other_models_first(monkeypatch):
    import urllib.request

    from triage.intel import hardware
    from triage.intel.llm import OllamaProvider

    evicted = []
    monkeypatch.setattr(hardware, "unload_other_models", lambda keep, host=None: evicted.append(keep) or [])
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp({"message": {"content": "ok"}}))
    p = OllamaProvider.__new__(OllamaProvider)
    p.host, p.model, p.timeout, p.available = "http://x", "llama3.2:1b", 5, True
    assert p.generate("s", "p") == "ok" and evicted == ["llama3.2:1b"]


def test_embedding_calls_evict_the_chat_model_and_do_not_linger(monkeypatch):
    import json
    import urllib.request

    from triage.intel import hardware
    from triage.intel.embeddings import LocalEmbedder

    evicted, sent = [], []
    monkeypatch.setattr(hardware, "unload_other_models", lambda keep, host=None: evicted.append(keep) or [])

    def fake_urlopen(req, timeout=None):
        sent.append(json.loads(req.data))
        return _Resp({"embedding": [0.1, 0.2]})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    e = LocalEmbedder.__new__(LocalEmbedder)
    e.host, e.model, e.timeout, e._last_exclusive = "http://x", "nomic-embed-text", 5, 0.0
    assert e._embed_one("hello") == [0.1, 0.2]
    assert evicted == ["nomic-embed-text"] and sent[0]["keep_alive"] == "30s"


# --- the old API keeps working on top of it ---------------------------------------------------
@pytest.mark.parametrize("ram,model", [(4, None), (None, None), (12, "llama3.1:8b"), (16, "llama3.1:8b"),
                                       (20, "llama3.1:8b"), (30, "llama3.1:8b"), (64, "llama3.1:8b")])
def test_recommend_model_is_hardware_fit_capped_at_the_8b_default(ram, model):
    assert recommend_model({"ram_gb": ram})["model"] == model


# --- OllamaProvider must only ever ask for a model that is actually pulled ----------------
def _installed(monkeypatch, names):
    from triage.intel import llm

    monkeypatch.setattr(
        llm,
        "list_ollama_models",
        lambda *a, **k: [
            {"name": n, "size_bytes": sz, "embedding_only": "embed" in n} for n, sz in names
        ],
    )
    monkeypatch.setattr(llm.OllamaProvider, "_ping", lambda self: True)
    monkeypatch.delenv("SNAGR_LLM_MODEL", raising=False)
    return llm


def test_unset_model_steps_down_to_the_smallest_installed_chat_model_when_memory_is_tight(monkeypatch):
    import triage.intel.hardware as hwmod

    monkeypatch.setattr(hwmod, "detect_hardware", lambda: hw(16, available=3.0))
    llm = _installed(
        monkeypatch,
        [("llama3.1:8b", 4_900_000_000), ("llama3.2:1b", 1_300_000_000), ("nomic-embed-text:latest", 270_000_000)],
    )
    assert llm.OllamaProvider().model == "llama3.2:1b"  # not the bare, uninstalled "llama3.1"


def test_bare_tag_resolves_to_the_installed_tag(monkeypatch):
    llm = _installed(monkeypatch, [("llama3.1:8b", 4_900_000_000)])
    monkeypatch.setenv("SNAGR_LLM_MODEL", "llama3.1")
    assert llm.OllamaProvider().model == "llama3.1:8b"


def test_explicit_installed_model_is_kept(monkeypatch):
    llm = _installed(monkeypatch, [("llama3.1:8b", 4_900_000_000), ("llama3.2:1b", 1_300_000_000)])
    assert llm.OllamaProvider(model="llama3.1:8b").model == "llama3.1:8b"


def test_chat_requests_pin_a_small_context_window(monkeypatch):
    # Ollama's default context can be 128k tokens: a 1B model then sits at ~6 GB resident.
    import io
    import json as _json

    llm = _installed(monkeypatch, [("llama3.2:1b", 1_300_000_000)])
    sent = []

    class _Resp(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        sent.append(_json.loads(req.data.decode()))
        return _Resp(b'{"message": {"content": "hi"}, "done": true}\n')

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr("triage.intel.hardware.unload_other_models", lambda *a, **k: None)
    p = llm.OllamaProvider()
    p.generate("s", "p")
    list(p.stream("s", "p"))
    assert len(sent) == 2
    assert all(0 < b["options"]["num_ctx"] <= 8192 for b in sent)
    # an uncapped JSON-mode call on a small model can run until the 60 s timeout, holding the
    # one-model lock the whole time and starving the answer behind it
    assert all(0 < b["options"]["num_predict"] <= 1024 for b in sent)


# --- quality-first selection: use the good model the laptop can really carry ---------------
def test_default_cap_allows_a_7_to_8b_model(monkeypatch):
    monkeypatch.delenv("SNAGR_LLM_MAX_PARAMS_B", raising=False)
    from triage.intel.hardware import preferred_max_params_b

    assert preferred_max_params_b() >= 8.0


def test_macos_reclaimable_memory_counts_not_just_free_and_inactive(monkeypatch):
    from triage.intel import hardware as hwmod

    vm = "Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages free: 6000.\nPages inactive: 242000.\nPages speculative: 3000.\n"
    mp = "The system has 17179869184 (1048576 pages with a page size of 16384).\nSystem-wide memory free percentage: 45%\n"

    class _R:
        def __init__(self, out):
            self.stdout = out

    monkeypatch.setattr(hwmod.subprocess, "run", lambda cmd, **k: _R(mp if cmd[0] == "memory_pressure" else vm))
    monkeypatch.setattr(hwmod, "_detect_ram_gb", lambda system: 16.0)
    got = hwmod._detect_available_ram_gb("darwin")
    assert got is not None and got > 7.0, "macOS says 45% of 16 GB is reclaimable (~7.2 GB), far above free+inactive (~4 GB)"


def test_a_tight_but_loadable_model_is_used_when_asked():
    rows = assess_models(hw(16, available=8.0), [{"name": "llama3.1:8b", "size_bytes": 4_900_000_000, "parameter_size": "8.0B"},
                                                  {"name": "llama3.2:1b", "size_bytes": 1_300_000_000, "parameter_size": "1.2B"}])
    by = {r["name"]: r for r in rows}
    assert by["llama3.1:8b"]["verdict"] in ("tight", "fits")
    assert select_model(rows, installed_only=True, max_params_b=8.0, allow_tight=True) == "llama3.1:8b"


def test_a_general_model_beats_a_coder_model_of_similar_size():
    rows = assess_models(hw(32, available=24.0), [
        {"name": "qwen2.5-coder:7b", "size_bytes": 4_700_000_000, "parameter_size": "7.6B"},
        {"name": "deepseek-coder:6.7b", "size_bytes": 3_800_000_000, "parameter_size": "7B"},
        {"name": "llama3.1:8b", "size_bytes": 4_900_000_000, "parameter_size": "8.0B"},
    ])
    assert select_model(rows, installed_only=True, max_params_b=8.0, allow_tight=True) == "llama3.1:8b"
    only_coders = [r for r in rows if "coder" in r["name"]]
    assert select_model(only_coders, installed_only=True, max_params_b=8.0, allow_tight=True) == "qwen2.5-coder:7b"


def test_model_is_chosen_per_request_from_current_memory(monkeypatch):
    # The engine auto-pinned the 8B model at startup while memory was free; when the laptop
    # is busy later, requests must step down to the small model instead of swapping.
    from triage.intel import llm

    monkeypatch.setattr(llm.OllamaProvider, "_ping", lambda self: True)
    monkeypatch.setattr(
        llm, "list_ollama_models",
        lambda *a, **k: [
            {"name": "llama3.1:8b", "size_bytes": 4_900_000_000, "parameter_size": "8.0B", "embedding_only": False},
            {"name": "llama3.2:1b", "size_bytes": 1_300_000_000, "parameter_size": "1.2B", "embedding_only": False},
        ],
    )
    monkeypatch.setenv("SNAGR_LLM_MODEL", "llama3.1:8b")
    monkeypatch.setattr(llm, "_AUTO_PINNED", {"model": "llama3.1:8b"})
    import triage.intel.hardware as hwmod

    monkeypatch.setattr(hwmod, "detect_hardware", lambda: hw(16, available=3.5))
    assert llm.OllamaProvider().model == "llama3.2:1b"
    monkeypatch.setattr(hwmod, "detect_hardware", lambda: hw(16, available=9.0))
    assert llm.OllamaProvider().model == "llama3.1:8b"


def test_an_operator_pinned_model_is_never_second_guessed(monkeypatch):
    from triage.intel import llm

    monkeypatch.setattr(llm.OllamaProvider, "_ping", lambda self: True)
    monkeypatch.setattr(
        llm, "list_ollama_models",
        lambda *a, **k: [{"name": "llama3.1:8b", "size_bytes": 4_900_000_000, "parameter_size": "8.0B", "embedding_only": False},
                         {"name": "llama3.2:1b", "size_bytes": 1_300_000_000, "parameter_size": "1.2B", "embedding_only": False}],
    )
    monkeypatch.setenv("SNAGR_LLM_MODEL", "llama3.1:8b")
    monkeypatch.setattr(llm, "_AUTO_PINNED", {})  # not set by autodetect: the operator chose it
    import triage.intel.hardware as hwmod

    monkeypatch.setattr(hwmod, "detect_hardware", lambda: hw(16, available=3.0))
    assert llm.OllamaProvider().model == "llama3.1:8b"


def test_memory_a_model_already_holds_counts_as_available_to_it(monkeypatch):
    # Once the 8B is loaded the free-memory figure drops by its own 5.3 GB. Re-picking from that
    # alone would evict it for the 1B and flip models on every question.
    from triage.intel import hardware as hwmod

    chat = [installed("llama3.1:8b", 4.9, "8.0B"), installed("llama3.2:1b", 1.3, "1.2B")]
    monkeypatch.setattr(hwmod, "detect_hardware", lambda: hw(16, available=3.5))
    monkeypatch.setattr(hwmod, "_ollama_loaded", lambda host=None: [])
    assert hwmod.pick_model_now(chat) == "llama3.2:1b"  # nothing resident, a busy laptop: step down
    monkeypatch.setattr(hwmod, "_ollama_loaded", lambda host=None: [{"name": "llama3.1:8b", "size_gb": 5.3}])
    assert hwmod.pick_model_now(chat) == "llama3.1:8b"  # it is already paid for: keep it
