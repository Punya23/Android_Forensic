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
import shutil
import subprocess
import time
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

    def stream(self, system: str, prompt: str):
        """Yield the answer in chunks. Providers without real streaming yield the whole
        :meth:`generate` result once, so callers need only one code path."""
        text = self.generate(system, prompt)
        if text:
            yield text

    def is_usable(self) -> bool:
        """Whether this provider can actually be asked to write prose/JSON right now.

        ``available`` alone isn't enough: :class:`HeuristicProvider` reports itself
        ``available`` by design (the tool must run with no model configured) but
        deliberately never answers a narration request — so every caller across the
        intel layer that wants "is this a real, usable model" rather than "did the
        provider construct successfully" needs this same extra ``name != "heuristic"``
        check. Centralised here so analysis.py, investigator.py and ai_summary.py
        can't each grow a slightly different copy of it.
        """
        return bool(self.available) and self.name != "heuristic"


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
#: Output caps. A search-term object is ~100 tokens and a grounded summary of eight passages
#: ~250; anything beyond that on a small model is rambling, not answer.
JSON_MAX_TOKENS = 150
STREAM_MAX_TOKENS = 400


def _options(max_tokens: int) -> dict:
    """Per-request model options: low temperature, a small context window and a hard cap on
    output tokens, so no call can run unbounded while it holds the one-model lock."""
    # repeat_penalty: greedy decoding in JSON mode made an 8B model loop the same terms until the
    # token cap (~15 s); a mild penalty ends the object instead.
    return {"temperature": 0.1, "num_ctx": _num_ctx(), "num_predict": max_tokens, "repeat_penalty": 1.2}


def _num_ctx() -> int:
    """Context window requested for every chat call. Prompts here are short (a question plus a
    few trimmed passages), but Ollama's default window can be 128k tokens and a model's KV cache
    scales with it — a 1B model then sits at ~6 GB resident. Override: ``SNAGR_LLM_NUM_CTX``."""
    try:
        return max(512, int(os.environ.get("SNAGR_LLM_NUM_CTX", "4096")))
    except ValueError:
        return 4096



_OLLAMA_APP_BIN = "/Applications/Ollama.app/Contents/Resources/ollama"


def _ollama_up(host: str, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(f"{host.rstrip('/')}/api/tags", timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def ensure_ollama_running(host: Optional[str] = None, wait: float = 15.0) -> bool:
    """True when the Ollama daemon answers, starting it if it is installed here but stopped.

    Only ever spawns for a loopback host (a remote daemon is not ours to start) and can be
    turned off with ``SNAGR_OLLAMA_AUTOSTART=0``. The daemon is detached, so it outlives the
    engine, as the Ollama app itself would."""
    host = (host or os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")).rstrip("/")
    if _ollama_up(host):
        return True
    if os.environ.get("SNAGR_OLLAMA_AUTOSTART", "1") == "0":
        return False
    if not any(h in host for h in ("//127.0.0.1", "//localhost", "//[::1]")):
        return False
    binary = shutil.which("ollama") or (_OLLAMA_APP_BIN if os.path.exists(_OLLAMA_APP_BIN) else None)
    if not binary:
        return False
    try:
        subprocess.Popen([binary, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        return False
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if _ollama_up(host):
            return True
        time.sleep(0.3)
    return False


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
        self.timeout = timeout
        # A stopped daemon is started rather than reported as "no model".
        self.available = self._ping() or ensure_ollama_running(self.host)
        #: Why the last stream() produced nothing — the daemon's own words, shown to the examiner.
        self.last_error = ""
        self.model = self._resolve_model(model or os.environ.get("SNAGR_LLM_MODEL", ""))

    def _resolve_model(self, wanted: str) -> str:
        """The name of a model that is actually pulled, so a request can never 404.

        A bare tag ("llama3.1") resolves to the installed one ("llama3.1:8b"); with nothing
        asked for, the smallest pulled chat model is used — the same bias towards a small,
        fast model as :func:`autodetect_and_configure`. An explicit name that is not
        installed is kept as given (the failure is then the truthful one)."""
        if not self.available:
            return wanted or "llama3.1"
        installed = list_ollama_models(self.host)
        chat = [m for m in installed if not m.get("embedding_only")]
        names = [m["name"] for m in chat]
        # No model named, or the name is just what autodetect chose at startup: decide now, from
        # the memory free *now*. An operator-pinned model is never second-guessed.
        if not wanted or wanted == _AUTO_PINNED.get("model"):
            from . import hardware

            return hardware.pick_model_now(installed) or "llama3.1"
        if wanted in names:
            return wanted
        if wanted:
            base = wanted.split(":")[0]
            same = [n for n in names if n.split(":")[0] == base]
            return same[0] if same else wanted
        return "llama3.1"

    def _ping(self) -> bool:
        try:
            req = urllib.request.Request(f"{self.host}/api/tags")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _chat(self, system: str, prompt: str, force_json: bool, max_tokens: int = 512) -> Optional[str]:
        body = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "options": _options(max_tokens),
            # Unload soon after use (Ollama's default is 5 min) so a finished request does
            # not keep gigabytes resident next to the dashboard and the engine.
            "keep_alive": os.environ.get("SNAGR_LLM_KEEP_ALIVE", "2m"),
        }
        if force_json:
            body["format"] = "json"
        try:
            from .hardware import unload_other_models

            unload_other_models(self.model, self.host)  # one resident model at a time
        except Exception:
            pass
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

    def _smaller_model(self) -> Optional[str]:
        """The smallest other installed chat model — the retry target when the pick will not load."""
        chat = [m for m in list_ollama_models(self.host) if not m.get("embedding_only") and m["name"] != self.model]
        return min(chat, key=lambda m: m["size_bytes"])["name"] if chat else None

    def _stream_once(self, system, prompt):
        body = {
            "model": self.model,
            "stream": True,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "options": _options(STREAM_MAX_TOKENS),
            "keep_alive": os.environ.get("SNAGR_LLM_KEEP_ALIVE", "2m"),
        }
        try:
            from .hardware import unload_other_models

            unload_other_models(self.model, self.host)  # one resident model at a time
        except Exception:
            pass
        try:
            req = urllib.request.Request(
                f"{self.host}/api/chat",
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            # A cold model is read from disk before the first byte, so the first read can
            # take far longer than the gap between tokens.
            with urllib.request.urlopen(req, timeout=max(self.timeout, 120.0)) as resp:
                for line in resp:  # newline-delimited JSON, one object per chunk
                    if not line.strip():
                        continue
                    part = json.loads(line.decode("utf-8"))
                    if part.get("error"):
                        self.last_error = str(part["error"])
                        return
                    chunk = (part.get("message", {}) or {}).get("content")
                    if chunk:
                        yield chunk
                    if part.get("done"):
                        return
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("error", "")
            except Exception:
                detail = ""
            self.last_error = detail or f"HTTP {exc.code}"
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"

    def stream(self, system, prompt):
        """Yield content chunks from Ollama as they are generated (``stream: true``).

        Never raises: the caller already holds the grep matches, so a model that cannot answer
        costs the summary, not the result. What went wrong is kept in ``last_error``. If the
        chosen model fails to start (typically not enough free memory for it), the request is
        retried once on the smallest other installed chat model."""
        self.last_error = ""
        got = False
        for chunk in self._stream_once(system, prompt):
            got = True
            yield chunk
        if got or not self.last_error:
            return
        alt = self._smaller_model()
        if alt:
            first = self.last_error
            self.last_error = ""
            self.model = alt
            for chunk in self._stream_once(system, prompt):
                yield chunk
            if self.last_error:
                self.last_error = f"{first}; retry on {alt}: {self.last_error}"

    def warm(self) -> bool:
        """Load the model now (one token), so the first real question is not a cold start."""
        return bool("".join(self._stream_once("Reply with one word.", "ok")))

    def extract_json(self, system, prompt, schema_hint=None):
        raw = self._chat(system, prompt, force_json=True, max_tokens=JSON_MAX_TOKENS)
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


#: Result of the most recent :func:`autodetect_and_configure` call, so
#: ``provider_status`` can report *why* the current back-end is what it is (chosen by
#: the operator vs. picked automatically at engine start).
_last_autodetect: dict = {}

#: The model autodetect chose at engine start (as opposed to one an operator pinned). Requests
#: re-pick from current memory only while the configured name is this one.
_AUTO_PINNED: dict = {}


def autodetect_and_configure(force: bool = False) -> dict:
    """Probe Ollama once at engine start and, if the operator hasn't already made an
    explicit choice, wire the engine to the strongest local chat model available.

    Never overrides an explicit ``SNAGR_LLM`` (an examiner who picked "heuristic" on
    purpose, or named a specific model, must stay picked) and never raises — a probe
    failure just leaves the always-available heuristic default in place. This is the
    only function that mutates the environment; callers just invoke it once after the
    engine comes up and log the result.
    """
    global _last_autodetect
    explicit = os.environ.get("SNAGR_LLM", "").strip()
    if explicit and not force:
        _last_autodetect = {
            "autodetected": False,
            "reason": f"SNAGR_LLM={explicit!r} was already set — leaving it alone",
            "provider": explicit,
        }
        return _last_autodetect

    models = list_ollama_models()
    # Choose by what this machine can carry, not by size: the largest pulled model on a
    # 16 GB laptop pushed Ollama to ~20 GB and every prompt into swap.
    from .hardware import assess_models, detect_hardware, preferred_max_params_b, select_model

    fit = assess_models(detect_hardware(), models)
    best = select_model(fit, installed_only=True, max_params_b=preferred_max_params_b(), allow_tight=True)
    pulled = [r for r in fit if r["installed"]]
    if not best and pulled:
        smallest = min(pulled, key=lambda r: r["footprint_gb"])
        _last_autodetect = {
            "autodetected": False,
            "provider": "heuristic",
            "reason": f"no pulled model fits this machine's free memory (smallest: {smallest['name']}, "
            f"{smallest['reason']}) — staying on heuristic",
        }
        return _last_autodetect
    if not best:
        # Nothing usable yet. Before settling for heuristic, try to provision a local
        # model ourselves — this is the multi-examiner-machine path: the engine may be
        # starting for the first time on a workstation nobody has ever run `ollama
        # pull` on. This never blocks: both the binary install and the (slow, multi-GB)
        # model pull run in a background thread, and this returns immediately on
        # heuristic. ``_on_pull_done`` fires from that background thread and flips the
        # switch the moment the model is actually usable — no polling needed, because
        # get_provider() reads SNAGR_LLM/SNAGR_LLM_MODEL fresh on every call.
        from .hardware import ensure_local_model

        def _on_pull_done(success: bool, model: str) -> None:
            global _last_autodetect
            if not success:
                _last_autodetect = {
                    "autodetected": False,
                    "reason": f"background pull of {model} failed — staying on heuristic",
                    "provider": "heuristic",
                }
                return
            os.environ["SNAGR_LLM"] = "ollama"
            os.environ.setdefault("SNAGR_LLM_MODEL", model)
            _last_autodetect = {
                "autodetected": True,
                "provider": "ollama",
                "model": os.environ["SNAGR_LLM_MODEL"],
                "reason": f"local model pulled automatically ({model})",
            }

        # Chat-capable only — `best` is already None precisely because
        # select_model() found no pulled chat model that fits among `models`, so passing the
        # unfiltered list back in here would let a pulled *embedding* model (e.g.
        # nomic-embed-text, which docs/SETUP.md tells examiners to pull separately)
        # read as "a chat model is already pulled" and permanently skip provisioning.
        chat_models = [m["name"] for m in models if not m.get("embedding_only")]
        provisioning = ensure_local_model(chat_models, on_done=_on_pull_done)
        if provisioning.get("action") in ("installing", "pulling"):
            _last_autodetect = {
                "autodetected": False,
                "reason": (
                    f"no local model was pulled yet — downloading "
                    f"{provisioning['model']} in the background based on this "
                    "machine's hardware; will switch over automatically once it "
                    "finishes"
                ),
                "provider": "heuristic",
                "provisioning": provisioning,
            }
            return _last_autodetect
        _last_autodetect = {
            "autodetected": False,
            "reason": provisioning.get("reason", "Ollama unreachable, or no chat model pulled"),
            "provider": "heuristic",
            "hardware": provisioning.get("hardware"),
        }
        return _last_autodetect

    os.environ["SNAGR_LLM"] = "ollama"
    # Respect an explicitly-pinned model even if the operator didn't also set
    # SNAGR_LLM; only fill in the model when nothing was pinned.
    if not os.environ.get("SNAGR_LLM_MODEL", "").strip():
        os.environ["SNAGR_LLM_MODEL"] = best
        _AUTO_PINNED["model"] = best
    _last_autodetect = {
        "autodetected": True,
        "provider": "ollama",
        "model": os.environ["SNAGR_LLM_MODEL"],
        "reason": f"picked largest chat model pulled ({os.environ['SNAGR_LLM_MODEL']})",
    }
    return _last_autodetect


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
        "autodetect": _last_autodetect,
        "hardware": _hardware_snapshot(),
    }


#: Fallback shape for _hardware_snapshot()'s failure path — same keys the dashboard's
#: HardwareInfo type declares as required, all null/unknown rather than the object
#: missing outright. An empty dict would be truthy in the frontend's `hardware &&`
#: guard while `hardware.recommended_model` was undefined, crashing the Acquisition
#: view on exactly the "detection failed" case this is meant to degrade gracefully on.
_HARDWARE_UNKNOWN: dict = {
    "platform": "unknown",
    "arch": "unknown",
    "cpu_cores": 0,
    "ram_gb": None,
    "gpu": "unknown",
    "recommended_model": {"model": None, "note": "hardware detection failed", "ram_gb": None},
}


def _hardware_snapshot() -> dict:
    """This workstation's RAM/CPU/GPU plus what model that would earn it, for the
    dashboard's LLM-status panel. Best-effort like everything else in this module —
    never raises, degrades to :data:`_HARDWARE_UNKNOWN` (never an empty dict — see its
    comment) on any detection failure."""
    try:
        from .hardware import detect_hardware, recommend_model

        hw = detect_hardware()
        return {**hw, "recommended_model": recommend_model(hw)}
    except Exception:
        return dict(_HARDWARE_UNKNOWN)


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
