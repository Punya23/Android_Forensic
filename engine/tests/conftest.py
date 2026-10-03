"""pytest conftest — makes ``engine/`` importable as the root for ``triage`` and ``tools``."""

import sys
import threading
from pathlib import Path

import pytest

# The tests live in  engine/tests/
# The triage package lives in  engine/triage/
# The tools package lives in  engine/tools/
# Adding engine/ to sys.path satisfies all ``from triage.xxx`` and ``from tools.xxx`` imports.
_ENGINE_ROOT = Path(__file__).resolve().parent.parent  # …/Android_Forensic/engine
if str(_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_ENGINE_ROOT))


@pytest.fixture(autouse=True)
def _hermetic_llm_env(monkeypatch):
    """Keep the suite independent of whatever Ollama this machine happens to run.

    Server/CLI startup calls ``triage.intel.llm.autodetect_and_configure()``, which
    probes Ollama, may kick off a multi-GB ``ollama pull``, and writes
    ``os.environ["SNAGR_LLM"]="ollama"`` for the rest of the process — so a test that
    merely builds the app flips every later test's default provider. An explicit
    ``SNAGR_LLM`` makes autodetect a no-op; ``SNAGR_LLM_AUTOINSTALL=0`` is the second
    guard against any install/pull. Tests that need other values set them via their own
    ``monkeypatch`` (applied after this fixture); monkeypatch restores both on teardown.
    """
    monkeypatch.setenv("SNAGR_LLM", "heuristic")
    monkeypatch.setenv("SNAGR_LLM_AUTOINSTALL", "0")
    monkeypatch.setenv("SNAGR_OLLAMA_AUTOSTART", "0")  # tests must never spawn a real daemon
    # A test that abandons a slow fake model must not leave the next test seeing "busy".
    from triage.intel import search

    monkeypatch.setattr(search, "_MODEL_BUSY", threading.Lock())
    monkeypatch.setattr(search, "_SPEC_CACHE", {})
