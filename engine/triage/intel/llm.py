"""Pluggable LLM provider for the case-intelligence layer.

Two interchangeable back-ends, selected by the ``SNAGR_LLM`` environment variable:

    * ``heuristic`` (default)  — no external calls at all. Pure regex/lexical extraction.
      The tool is fully functional offline with zero configuration, which matters for a
      field-deployable forensic device and for a demo with no network.
    * ``ollama``               — a local model over Ollama's HTTP API (``localhost:11434``).
      Case data never leaves the machine → the legally-safest option for real seized
      evidence.

There is deliberately no cloud/hosted back-end: case text must never leave the
workstation, so no provider in this module makes an off-device call.

Every provider implements the same two-method contract:
    * ``extract_json(system, prompt, schema_hint)`` → dict | None
    * ``generate(system, prompt)``                  → str  | None

A provider returns ``None`` (never raises) when it cannot answer, so callers can always
fall back to the deterministic ontology path. This keeps the honesty model intact: the
LLM is an *assist*, never a hard dependency.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from typing import Optional


class LLMProvider(ABC):
    """Common interface for all case-intelligence back-ends."""

    name: str = "base"
    available: bool = False
    #: Back-end that was asked for but was unreachable, when this provider is standing
    #: in for it. A degraded run and a deliberately offline one both report themselves
    #: as "heuristic"; only this separates them, and an examiner reviewing the plan
    #: later has to be able to tell "no model configured" from "the model was down".
    degraded_from: str = ""

    @abstractmethod
    def extract_json(
        self, system: str, prompt: str, schema_hint: Optional[dict] = None
    ) -> Optional[dict]:
        """Return a parsed JSON object, or None if the provider can't answer."""

    @abstractmethod
    def generate(self, system: str, prompt: str) -> Optional[str]:
        """Return free-text, or None if the provider can't answer."""


# --- heuristic (offline default) --------------------------------------------
class HeuristicProvider(LLMProvider):
    """No-LLM back-end. Deliberately returns ``None`` for both methods so callers use
    their deterministic fallbacks (ontology classification + rule-based scoring).

    It is *always available* — the tool must run with no model and no network.
    """

    name = "heuristic"
    available = True

    def extract_json(self, system, prompt, schema_hint=None):  # noqa: D401
        return None

    def generate(self, system, prompt):
        return None


# --- Ollama (local) ----------------------------------------------------------
class OllamaProvider(LLMProvider):
    """Local model via Ollama's HTTP API. Keeps all case data on-device."""

    name = "ollama"

    def __init__(
        self,
        host: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 60.0,
    ) -> None:
        self.host = (
            host or os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
        ).rstrip("/")
        self.model = model or os.environ.get("SNAGR_LLM_MODEL", "llama3.1")
        self.timeout = timeout
        self.available = self._ping()

    def _ping(self) -> bool:
        try:
            req = urllib.request.Request(f"{self.host}/api/tags")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _chat(self, system: str, prompt: str, force_json: bool) -> Optional[str]:
        body = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "options": {"temperature": 0.1},
        }
        if force_json:
            body["format"] = "json"
        try:
            data = json.dumps(body).encode("utf-8")
            req = urllib.request.Request(
                f"{self.host}/api/chat",
                data=data,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            return (payload.get("message", {}) or {}).get("content")
        except Exception:
            return None

    def extract_json(self, system, prompt, schema_hint=None):
        raw = self._chat(system, prompt, force_json=True)
        return _safe_json(raw)

    def generate(self, system, prompt):
        return self._chat(system, prompt, force_json=False)


# --- selection ---------------------------------------------------------------
def get_provider(kind: Optional[str] = None) -> LLMProvider:
    """Return the configured provider, falling back to heuristic when unavailable.

    Selection order: explicit *kind* arg → ``SNAGR_LLM`` env → ``heuristic``. If the
    chosen back-end reports itself unavailable (server down), we degrade to the
    heuristic provider rather than fail — the analysis still runs, just deterministically.
    Any unrecognized *kind* (including a stale ``"anthropic"``/``"claude"`` from an old
    config) also falls through to heuristic rather than erroring.
    """
    kind = (kind or os.environ.get("SNAGR_LLM", "heuristic")).strip().lower()
    if kind == "ollama":
        p = OllamaProvider()
        return p if p.available else _degraded("ollama")
    return HeuristicProvider()


def list_ollama_models(host: Optional[str] = None, timeout: float = 3.0) -> list[dict]:
    """Models actually pulled on this workstation, or ``[]`` if Ollama is unreachable.

    The dashboard offers "Ollama (local model)" as a back-end; without this the
    examiner picks it blind and only discovers at plan time whether anything is
    installed. Names come from the daemon, so the list can never claim a model the
    machine does not have.
    """
    host = (host or os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")).rstrip("/")
    try:
        req = urllib.request.Request(f"{host}/api/tags")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return []
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return []
    out = []
    for m in payload.get("models") or []:
        if not isinstance(m, dict):
            continue
        name = str(m.get("name", ""))
        if not name:
            continue
        caps = m.get("capabilities") or []
        details = m.get("details") or {}
        out.append(
            {
                "name": name,
                "size_bytes": int(m.get("size") or 0),
                "parameter_size": str(details.get("parameter_size", "")),
                "quantization": str(details.get("quantization_level", "")),
                # An embedding-only model cannot answer a chat prompt, and a chat model
                # is a poor embedder. Separating them stops the UI offering either for
                # the wrong job.
                "embedding_only": "completion" not in caps and "embed" in name.lower(),
            }
        )
    out.sort(key=lambda m: m["name"])
    return out


def provider_status(kind: Optional[str] = None) -> dict:
    """Which back-ends this workstation can actually use, and why not when it cannot.

    Reports every back-end rather than only the configured one, because the useful
    question at acquisition time is "what are my options here", and because an offline
    default that was *chosen* and one that was *fallen back to* must be told apart.
    """
    configured = (kind or os.environ.get("SNAGR_LLM", "heuristic")).strip().lower()
    models = list_ollama_models()
    chat_models = [m for m in models if not m["embedding_only"]]
    return {
        "configured": configured,
        "chat_model": os.environ.get("SNAGR_LLM_MODEL", "llama3.1"),
        "providers": [
            {
                "name": "heuristic",
                "label": "Heuristic (offline, deterministic)",
                "available": True,
                "local": True,
                "reason": "",
                "note": "No model, no network. Regex and ontology only — always available.",
            },
            {
                "name": "ollama",
                "label": "Ollama (local model)",
                "available": bool(chat_models),
                "local": True,
                "reason": (
                    ""
                    if chat_models
                    else "Ollama is not running on this workstation, or no chat model is pulled."
                ),
                "models": [m["name"] for m in chat_models],
                "note": "Case text never leaves this machine — the safest option for real evidence.",
            },
        ],
        "embedding_models": [m["name"] for m in models if m["embedding_only"]],
    }


def _degraded(requested: str) -> LLMProvider:
    """A heuristic provider that remembers which back-end it is standing in for."""
    provider = HeuristicProvider()
    provider.degraded_from = requested
    return provider


def _safe_json(raw: Optional[str]) -> Optional[dict]:
    """Best-effort parse of a JSON object out of a model response."""
    if not raw:
        return None
    raw = raw.strip()
    # Strip markdown fences if the model added them despite instructions.
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except Exception:
        # Last resort: grab the outermost {...} span.
        start, end = raw.find("{"), raw.rfind("}")
        if 0 <= start < end:
            try:
                obj = json.loads(raw[start : end + 1])
                return obj if isinstance(obj, dict) else None
            except Exception:
                return None
    return None
