"""Pluggable LLM provider for the case-intelligence layer.

Three interchangeable back-ends, selected by the ``ERAKSHAK_LLM`` environment variable:

    * ``heuristic`` (default)  — no external calls at all. Pure regex/lexical extraction.
      The tool is fully functional offline with zero configuration, which matters for a
      field-deployable forensic device and for a demo with no network.
    * ``ollama``               — a local model over Ollama's HTTP API (``localhost:11434``).
      Case data never leaves the machine → the legally-safest option for real seized
      evidence.
    * ``anthropic``            — the Claude API. Best extraction/analysis quality, but it
      sends case text off-device, so it is opt-in only and must never be the default for
      real evidence handling.

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
        self.model = model or os.environ.get("ERAKSHAK_LLM_MODEL", "llama3.1")
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


# --- Anthropic / Claude (cloud, opt-in) -------------------------------------
class AnthropicProvider(LLMProvider):
    """Claude API back-end. Highest quality; sends case text off-device, so opt-in only.

    Implemented over plain ``urllib`` against the Messages API so the engine needs no extra
    Python dependency. Requires ``ANTHROPIC_API_KEY``.
    """

    name = "anthropic"
    API_URL = "https://api.anthropic.com/v1/messages"
    API_VERSION = "2023-06-01"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 60.0,
    ) -> None:
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        # Default to a current, capable general model; override via env.
        self.model = model or os.environ.get("ERAKSHAK_LLM_MODEL", "claude-sonnet-5")
        self.timeout = timeout
        self.available = bool(self.api_key)

    def _messages(self, system: str, prompt: str) -> Optional[str]:
        if not self.api_key:
            return None
        body = {
            "model": self.model,
            "max_tokens": 2048,
            "temperature": 0.1,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }
        try:
            data = json.dumps(body).encode("utf-8")
            req = urllib.request.Request(
                self.API_URL,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": self.api_key,
                    "anthropic-version": self.API_VERSION,
                },
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            blocks = payload.get("content", [])
            return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        except Exception:
            return None

    def extract_json(self, system, prompt, schema_hint=None):
        hint = ""
        if schema_hint:
            hint = (
                "\n\nReturn ONLY a JSON object matching this shape (no prose, no "
                f"markdown fences):\n{json.dumps(schema_hint, indent=2)}"
            )
        return _safe_json(self._messages(system, prompt + hint))

    def generate(self, system, prompt):
        return self._messages(system, prompt)


# --- selection ---------------------------------------------------------------
def get_provider(kind: Optional[str] = None) -> LLMProvider:
    """Return the configured provider, falling back to heuristic when unavailable.

    Selection order: explicit *kind* arg → ``ERAKSHAK_LLM`` env → ``heuristic``. If the
    chosen back-end reports itself unavailable (no key / server down), we degrade to the
    heuristic provider rather than fail — the analysis still runs, just deterministically.
    """
    kind = (kind or os.environ.get("ERAKSHAK_LLM", "heuristic")).strip().lower()
    if kind == "ollama":
        p = OllamaProvider()
        return p if p.available else _degraded("ollama")
    if kind in ("anthropic", "claude"):
        p = AnthropicProvider()
        return p if p.available else _degraded(kind)
    return HeuristicProvider()


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
