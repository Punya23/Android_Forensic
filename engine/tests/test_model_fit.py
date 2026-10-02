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
    assert memory_budget_gb(hw(16, available=12)) <= 16 * 0.6, "never more than 60% of the machine"
    assert memory_budget_gb(hw(8, available=1)) == 0.0, "OS + dashboard headroom comes off first"
    assert memory_budget_gb(hw(16)) > 0, "unknown free RAM is assumed 60% free, not zero"


# --- the reported bug: 16 GB laptop, 8B installed ------------------------------------------
def test_a_16gb_laptop_does_not_get_the_8b_model():
    rows = by_name(assess_models(hw(16, available=9), [installed("llama3.1:8b", 4.9, "8.0B")]))
    assert rows["llama3.1:8b"]["verdict"] in ("tight", "too_big")
    assert select_model(list(rows.values())) != "llama3.1:8b"


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


def test_autodetect_picks_the_pulled_model_that_fits_not_the_largest(monkeypatch):
    models = [installed("llama3.1:8b", 4.9, "8.0B"), installed("llama3.2:1b", 1.3, "1.2B")]
    result = _autodetect(monkeypatch, hw(16, available=9), models)
    assert result["autodetected"] and result["model"] == "llama3.2:1b"


def test_autodetect_stays_on_heuristic_and_says_why_when_no_pulled_model_fits(monkeypatch):
    result = _autodetect(monkeypatch, hw(8, available=3), [installed("llama3.1:8b", 4.9, "8.0B")])
    assert not result["autodetected"] and result["provider"] == "heuristic"
    assert "fit" in result["reason"] and "llama3.1:8b" in result["reason"]


def test_fit_route_reports_the_machine_every_model_and_the_choice(tmp_path, monkeypatch):
    from tests.test_whatsapp_batch_import_route import _make_server_app
    from triage.intel import hardware

    monkeypatch.setattr(hardware, "detect_hardware", lambda: hw(16, available=9, gpu="apple_silicon", vram_gb=10.6, vram_kind="unified"))
    app, _ = _make_server_app(tmp_path)
    c = app.test_client()
    token = c.post("/api/auth/login", json={"username": "admin", "password": "snagr-demo"}).get_json()["token"]
    body = c.get("/api/llm/fit", headers={"Authorization": f"Bearer {token}"}).get_json()
    assert body["hardware"]["ram_gb"] == 16 and body["budget_gb"] == 6.0
    assert {"llama3.1:8b", "llama3.2:1b"} <= {m["name"] for m in body["models"]}
    assert body["download_recommendation"] == "qwen2.5:3b-instruct"


# --- the old API keeps working on top of it ---------------------------------------------------
@pytest.mark.parametrize("ram,model", [(4, None), (None, None), (12, "qwen2.5:3b-instruct"), (16, "qwen2.5:3b-instruct"),
                                       (20, "llama3.1:8b"), (30, "qwen2.5:14b-instruct"), (64, "qwen2.5:32b-instruct")])
def test_recommend_model_is_hardware_fit_not_a_ram_tier(ram, model):
    assert recommend_model({"ram_gb": ram})["model"] == model
