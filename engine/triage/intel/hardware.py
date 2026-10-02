"""Workstation hardware probe + local-model provisioning for the case-intelligence LLM.

This module exists because :mod:`.llm` used to assume Ollama and a chat model were
already on the machine — true on the machine that built the feature, not true on an
examiner's freshly-imaged workstation. eRakshak ships to multiple examiners on machines
this codebase has never seen, so the check has to run *in the engine*, on whatever box
it happens to start on, not once by hand on a developer's laptop.

Three responsibilities, each independently best-effort and non-fatal:

    1. :func:`detect_hardware`   — RAM / CPU / platform of *this* machine, right now.
    2. :func:`recommend_model`   — which locally-pulled-and-run chat model that hardware
       can carry without starving the OS, the dashboard, and the rest of the engine.
    3. :func:`ensure_local_model` — make it so: install the Ollama binary if missing,
       pull the recommended model if none is present, using only the vendor's own
       official, non-interactive install paths (Homebrew / winget / Ollama's documented
       Linux installer) — never an arbitrary third-party script.

Every function here returns a plain dict and never raises: a probe or install failure is
a normal outcome (offline examiner machine, locked-down corporate image, no admin
rights), not an engine-startup error. The heuristic provider is always there to fall
back on — see the module docstring in :mod:`.llm`. Nothing here reads or transmits case
data; it only asks the OS about itself and asks Ollama's own binary/registry for
software, matching the "case text never leaves the workstation" rule.

**Escape hatch:** ``SNAGR_LLM_AUTOINSTALL=0`` disables every install/pull action in this
module outright (detection still runs and is still reported). Real forensic workstations
are very often deliberately offline or locked to IT-approved software; the engine must
never reach out to the network on its own without a way to turn that off.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from typing import Optional

log = logging.getLogger("triage.intel.hardware")

#: Master switch. Checked once per call, not cached, so a test or an operator can flip
#: it mid-process.
def _autoinstall_enabled() -> bool:
    return os.environ.get("SNAGR_LLM_AUTOINSTALL", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


# --- 1. hardware probe --------------------------------------------------------
def detect_hardware() -> dict:
    """Best-effort snapshot of this workstation: RAM, CPU cores, OS, chip family.

    Every field defaults to a safe "don't know" value rather than raising — a probe
    that fails is a machine :func:`recommend_model` should treat conservatively
    (assume the smallest tier), not a crash.
    """
    system = platform.system().lower()  # "darwin" | "linux" | "windows"
    ram_gb = _detect_ram_gb(system)
    gpu = _detect_gpu(system)
    available = _detect_available_ram_gb(system)
    vram_gb, vram_kind = _detect_vram(gpu, ram_gb)
    return {
        "platform": system,
        "arch": platform.machine() or "unknown",
        "cpu_cores": os.cpu_count() or 1,
        "ram_gb": round(ram_gb, 1) if ram_gb else None,
        # Free right now (not total): what a model can actually be given.
        "available_ram_gb": round(available, 1) if available is not None else None,
        "gpu": gpu,
        # Memory the GPU can use: unified (Apple silicon: ~2/3 of RAM) or dedicated VRAM.
        "vram_gb": round(vram_gb, 1) if vram_gb else None,
        "vram_kind": vram_kind,
    }


def _detect_available_ram_gb(system: str) -> Optional[float]:
    """RAM that is free or immediately reclaimable right now; ``None`` if unknown."""
    try:
        if system == "linux":
            with open("/proc/meminfo") as fh:
                for line in fh:
                    if line.startswith("MemAvailable:"):
                        return int(line.split()[1]) / (1024 * 1024)
        if system == "darwin":
            out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=3.0, check=True).stdout
            page = int(re.search(r"page size of (\d+) bytes", out).group(1))
            pages = sum(
                int(re.search(rf"{label}:\s+(\d+)", out).group(1))
                for label in ("Pages free", "Pages inactive", "Pages speculative")
            )
            return pages * page / (1024 ** 3)
        if system == "windows":
            return _windows_memory().ullAvailPhys / (1024 ** 3)
    except Exception as exc:
        log.debug("available-RAM detection failed on %s: %s", system, exc)
    return None


def _detect_vram(gpu: str, ram_gb: Optional[float]) -> tuple[Optional[float], Optional[str]]:
    """``(GB the GPU can use, "unified" | "dedicated")`` or ``(None, None)``."""
    try:
        if gpu == "apple_silicon" and ram_gb:
            return ram_gb * 0.66, "unified"  # macOS lets the GPU wire ~2/3 of RAM by default
        if gpu == "nvidia":
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5.0, check=True,
            ).stdout
            return max(float(line) for line in out.split() if line.strip()) / 1024, "dedicated"
    except Exception as exc:
        log.debug("VRAM detection failed: %s", exc)
    return None, None


def _windows_memory():
    class _MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    stat = _MEMORYSTATUSEX()
    stat.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))  # type: ignore[attr-defined]
    return stat


def _detect_ram_gb(system: str) -> Optional[float]:
    try:
        if system == "linux":
            with open("/proc/meminfo") as fh:
                for line in fh:
                    if line.startswith("MemTotal:"):
                        kib = int(line.split()[1])
                        return kib / (1024 * 1024)
            return None
        if system == "darwin":
            out = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=3.0, check=True,
            )
            return int(out.stdout.strip()) / (1024 ** 3)
        if system == "windows":
            # ctypes GlobalMemoryStatusEx — the standard stdlib-only way to read total
            # physical RAM on Windows without an extra dependency.
            class _MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = _MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))  # type: ignore[attr-defined]
            return stat.ullTotalPhys / (1024 ** 3)
    except Exception as exc:
        log.debug("RAM detection failed on %s: %s", system, exc)
    return None


def _detect_gpu(system: str) -> str:
    """Coarse GPU family — used only to log/explain a recommendation, never to gate
    it (VRAM reporting is too inconsistent across vendors/drivers to size a model on
    without an extra dependency this offline tool won't add)."""
    try:
        if system == "darwin":
            out = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True, text=True, timeout=3.0, check=True,
            )
            return "apple_silicon" if "Apple" in out.stdout else "intel_mac"
        if system == "linux" and shutil.which("nvidia-smi"):
            return "nvidia"
        if system == "windows" and shutil.which("nvidia-smi"):
            return "nvidia"
    except Exception:
        pass
    return "unknown"


# --- 2. model fit: which local model can this laptop actually carry? ------------
#: RAM kept for the OS, the Electron dashboard and the Python engine — never given to a model.
HEADROOM_GB = 3.0
#: A model may use at most this share of the machine even when more happens to be free.
MAX_RAM_FRACTION = 0.6
#: Ollama's default context for machines under 24 GB. KV cache grows linearly with it.
DEFAULT_NUM_CTX = 4096
_RUNTIME_OVERHEAD_GB = 0.5
#: A model above this share of the budget is "tight": it loads, but a browser tab or a
#: second model tips the machine into swap, where every prompt takes minutes.
_TIGHT_SHARE = 0.75

#: (Ollama name, billions of parameters, GB on disk at Ollama's default 4-bit quantisation):
#: the chat models worth offering to download, weakest to strongest.
CATALOG: tuple[tuple[str, float, float], ...] = (
    ("llama3.2:1b", 1.2, 1.3),
    ("qwen2.5:3b-instruct", 3.1, 1.9),
    ("qwen2.5:7b-instruct", 7.6, 4.7),
    ("llama3.1:8b", 8.0, 4.9),
    ("qwen2.5:14b-instruct", 14.8, 9.0),
    ("qwen2.5:32b-instruct", 32.8, 19.0),
)


def model_footprint_gb(params_b: float, disk_gb: float, num_ctx: int = DEFAULT_NUM_CTX) -> float:
    """RAM/VRAM a loaded model really occupies: weights + KV cache + runtime overhead.

    KV cache is ~0.0156 MB per token per billion parameters (an 8B model: 0.125 MB/token,
    so 0.5 GB at 4k context but 4 GB at 32k) — the part a file-size check never sees.
    """
    kv_gb = num_ctx * max(0.0156 * params_b, 0.05) / 1024
    return round(disk_gb + kv_gb + _RUNTIME_OVERHEAD_GB, 2)


def memory_budget_gb(hw: dict) -> float:
    """What a model may use: the smaller of what is free and 60% of the machine, minus the
    headroom the OS and the rest of the tool need. Unknown free RAM is taken as 60% of
    total — the cautious direction."""
    total = hw.get("ram_gb") or 0.0
    available = hw.get("available_ram_gb")
    if available is None:
        available = total * MAX_RAM_FRACTION
    return round(max(0.0, min(available, total * MAX_RAM_FRACTION) - HEADROOM_GB), 2)


def _billions(parameter_size: str) -> float:
    m = re.match(r"([\d.]+)\s*([BM])", (parameter_size or "").strip(), re.IGNORECASE)
    if not m:
        return 0.0
    return float(m.group(1)) / (1000.0 if m.group(2).upper() == "M" else 1.0)


def _row(hw: dict, budget: float, num_ctx: int, name: str, params_b: float, disk_gb: float, installed: bool) -> dict:
    footprint = model_footprint_gb(params_b, disk_gb, num_ctx)
    verdict = "too_big" if footprint > budget else "tight" if footprint > _TIGHT_SHARE * budget else "fits"
    vram, kind = hw.get("vram_gb"), hw.get("vram_kind")
    speed = "cpu" if not kind else "gpu" if vram and footprint <= vram else "cpu-offload"
    avail = hw.get("available_ram_gb")
    return {
        "name": name,
        "installed": installed,
        "params_b": params_b,
        "footprint_gb": footprint,
        "verdict": verdict,
        "speed": speed,
        "needs_download_gb": None if installed else disk_gb,
        "reason": (
            f"needs ~{footprint} GB, budget {budget} GB "
            f"({hw.get('ram_gb')} GB RAM{f', {avail} GB free' if avail is not None else ''})"
        ),
    }


def assess_models(hw: dict, installed: list[dict], num_ctx: int = DEFAULT_NUM_CTX) -> list[dict]:
    """Every chat model — pulled or downloadable — with its footprint and a verdict
    (``fits`` / ``tight`` / ``too_big``) against this machine's memory budget. Embedding
    models are not chat candidates and are left out. Strongest first."""
    budget = memory_budget_gb(hw)
    rows: dict[str, dict] = {}
    for m in installed:
        if m.get("embedding_only") or not m.get("name"):
            continue
        rows[m["name"]] = _row(
            hw, budget, num_ctx, m["name"], _billions(m.get("parameter_size", "")),
            (m.get("size_bytes") or 0) / 1e9, True,
        )
    for name, params_b, disk_gb in CATALOG:
        rows.setdefault(name, _row(hw, budget, num_ctx, name, params_b, disk_gb, False))
    return sorted(rows.values(), key=lambda r: (-r["params_b"], r["name"]))


def select_model(rows: list[dict], installed_only: bool = False) -> Optional[str]:
    """The strongest model that comfortably ``fits`` — never one that is merely ``tight`` —
    preferring an already-pulled one on a tie. ``None`` when nothing fits."""
    fits = [r for r in rows if r["verdict"] == "fits" and (r["installed"] or not installed_only)]
    return max(fits, key=lambda r: (r["params_b"], r["installed"]))["name"] if fits else None


def recommend_model(hw: Optional[dict] = None) -> dict:
    """The model to download for this machine (``None`` when nothing fits: stay on the
    heuristic back-end). Unknown RAM counts as none."""
    hw = hw or detect_hardware()
    name = select_model(assess_models(hw, []))
    disk = next((d for n, _, d in CATALOG if n == name), None)
    return {
        "model": name,
        "note": f"~{disk:g} GB download" if name else "not enough free memory for a local model — heuristic only",
        "ram_gb": hw.get("ram_gb"),
    }


def _ollama_loaded(host: Optional[str] = None) -> list[dict]:
    """Models Ollama has resident in memory right now (``/api/ps``) — what actually costs RAM."""
    host = (host or os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")).rstrip("/")
    try:
        with urllib.request.urlopen(f"{host}/api/ps", timeout=3.0) as resp:
            models = json.loads(resp.read().decode("utf-8")).get("models") or []
    except Exception:
        return []
    return [
        {"name": m.get("name", ""), "size_gb": round((m.get("size") or 0) / 1e9, 1),
         "vram_gb": round((m.get("size_vram") or 0) / 1e9, 1)}
        for m in models
    ]


def fit_report(num_ctx: int = DEFAULT_NUM_CTX) -> dict:
    """Everything the dashboard / CLI shows: this machine, each pulled and downloadable
    model with its verdict, what to use, and what is eating memory right now."""
    from .llm import list_ollama_models

    hw = detect_hardware()
    rows = assess_models(hw, list_ollama_models(), num_ctx)
    loaded = _ollama_loaded()
    advice = []
    resident = round(sum(m["size_gb"] for m in loaded), 1)
    if len(loaded) > 1:
        advice.append(
            f"{len(loaded)} models are loaded at once ({resident} GB). Set OLLAMA_MAX_LOADED_MODELS=1 "
            "so only the one in use stays in memory."
        )
    elif loaded and resident > memory_budget_gb(hw):
        advice.append(f"{loaded[0]['name']} holds {resident} GB, more than this machine's {memory_budget_gb(hw)} GB budget.")
    chosen = select_model(rows, installed_only=True)
    if chosen is None and any(r["installed"] for r in rows):
        advice.append("No pulled model fits comfortably — use a smaller one or free memory; staying on the heuristic back-end.")
    return {
        "hardware": hw,
        "budget_gb": memory_budget_gb(hw),
        "num_ctx": num_ctx,
        "models": rows,
        "use_installed": chosen,
        "download_recommendation": select_model(rows),
        "loaded": loaded,
        "advice": advice,
    }


def _print_report() -> None:  # pragma: no cover - interactive
    r = fit_report()
    hw = r["hardware"]
    print(f"Machine: {hw['ram_gb']} GB RAM ({hw['available_ram_gb']} GB free), GPU {hw['gpu']}"
          + (f", {hw['vram_gb']} GB {hw['vram_kind']} VRAM" if hw["vram_gb"] else ""))
    print(f"Model budget: {r['budget_gb']} GB (after {HEADROOM_GB:g} GB headroom, max {MAX_RAM_FRACTION:.0%} of RAM), context {r['num_ctx']}\n")
    print(f"{'model':26} {'state':14} {'needs':>8}  {'verdict':8} speed")
    for m in r["models"]:
        state = "installed" if m["installed"] else f"download {m['needs_download_gb']:g} GB"
        print(f"{m['name']:26} {state:14} {m['footprint_gb']:>6} GB  {m['verdict']:8} {m['speed']}")
    print(f"\nUse (already pulled): {r['use_installed'] or 'none fits — heuristic'}")
    print(f"Best to download:     {r['download_recommendation'] or 'none fits'}")
    for m in r["loaded"]:
        print(f"Loaded now: {m['name']} {m['size_gb']} GB")
    for a in r["advice"]:
        print(f"! {a}")


if __name__ == "__main__":  # pragma: no cover
    _print_report()


# --- 3. provisioning: install the binary, pull the model ----------------------
def ensure_ollama_binary() -> dict:
    """Install the Ollama CLI/daemon if it is not already on ``PATH``.

    Uses only the vendor's own official, non-interactive install paths — Homebrew on
    macOS, winget on Windows, Ollama's documented Linux install script — never an
    arbitrary or third-party script. Returns immediately (no pull, no daemon start);
    callers decide separately whether/what model to pull.
    """
    if shutil.which("ollama"):
        return {"installed": True, "already_present": True, "method": "", "error": ""}
    if not _autoinstall_enabled():
        return {
            "installed": False, "already_present": False, "method": "",
            "error": "SNAGR_LLM_AUTOINSTALL=0 — auto-install disabled; install "
            "Ollama manually from https://ollama.com/download if you want local AI.",
        }

    system = platform.system().lower()
    try:
        if system == "darwin":
            if shutil.which("brew"):
                subprocess.run(
                    ["brew", "install", "ollama"],
                    capture_output=True, text=True, timeout=300, check=True,
                )
                return {"installed": True, "already_present": False, "method": "brew", "error": ""}
            return {
                "installed": False, "already_present": False, "method": "",
                "error": "Homebrew not found — install it, or install Ollama "
                "yourself from https://ollama.com/download (staying on Homebrew-only "
                "here rather than piping an install script to a shell unattended).",
            }
        if system == "linux":
            # Ollama's own documented non-interactive installer for Linux
            # (https://ollama.com/download/linux) — the standard automated-deploy path,
            # not a third-party script.
            subprocess.run(
                "curl -fsSL https://ollama.com/install.sh | sh",
                shell=True, capture_output=True, text=True, timeout=300, check=True,
            )
            return {"installed": True, "already_present": False, "method": "ollama.com/install.sh", "error": ""}
        if system == "windows":
            if shutil.which("winget"):
                subprocess.run(
                    ["winget", "install", "--id", "Ollama.Ollama", "-e", "--silent",
                     "--accept-package-agreements", "--accept-source-agreements"],
                    capture_output=True, text=True, timeout=300, check=True,
                )
                return {"installed": True, "already_present": False, "method": "winget", "error": ""}
            return {
                "installed": False, "already_present": False, "method": "",
                "error": "winget not found — install Ollama manually from "
                "https://ollama.com/download.",
            }
        return {
            "installed": False, "already_present": False, "method": "",
            "error": f"unrecognised platform {system!r} — install Ollama manually.",
        }
    except Exception as exc:
        return {"installed": False, "already_present": False, "method": "", "error": str(exc)}


def _ollama_reachable(host: str) -> bool:
    """Same check as ``OllamaProvider._ping()`` in :mod:`.llm`, duplicated rather than
    imported — ``.llm`` is the higher-level module (imports ``.hardware`` itself via
    ``autodetect_and_configure``) and this file must stay usable standalone."""
    try:
        req = urllib.request.Request(f"{host}/api/tags")
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            return resp.status == 200
    except Exception:
        return False


def ensure_ollama_running(host: Optional[str] = None, timeout_s: float = 15.0) -> dict:
    """Make sure the Ollama *daemon* is actually listening, not just installed.

    These are not the same thing, and the gap between them silently breaks the next
    step (a pull) in a way that reads as "the model download failed" rather than what
    actually happened: Homebrew's own ``ollama`` formula installs the CLI only and
    explicitly does **not** start or enable a background service — its own
    ``brew info ollama`` caveat says so in as many words, confirmed against a real
    install while building this. A workstation where the binary was installed this
    way (via :func:`ensure_ollama_binary`'s brew path, or by an examiner running
    ``brew install ollama`` by hand at any point in the past) has nothing on
    ``127.0.0.1:11434`` until something starts it — and every call site in this
    module before this one assumed "binary on PATH" meant "ready to pull".

    Best-effort and non-fatal like everything else here: if nothing answers, spawns
    ``ollama serve`` — the one command that exists identically regardless of how the
    binary got onto this machine (brew, winget, the official installer, a manual
    download) — detached so it outlives this call, then polls briefly for it to come
    up. Never raises; a platform where this also doesn't work just stays on the
    heuristic provider, same as every other failure mode in this module.
    """
    host = (host or os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")).rstrip("/")
    if _ollama_reachable(host):
        return {"running": True, "started": False, "error": ""}
    if not shutil.which("ollama"):
        return {"running": False, "started": False, "error": "ollama binary not on PATH"}

    try:
        popen_kwargs: dict = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "stdin": subprocess.DEVNULL,
        }
        if platform.system().lower() == "windows":
            # POSIX's start_new_session isn't meaningful here; these two flags are
            # Windows' own way of detaching a child so it survives this process.
            popen_kwargs["creationflags"] = (
                subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
            )
        else:
            popen_kwargs["start_new_session"] = True
        subprocess.Popen(["ollama", "serve"], **popen_kwargs)
    except Exception as exc:
        return {"running": False, "started": False, "error": str(exc)}

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _ollama_reachable(host):
            return {"running": True, "started": True, "error": ""}
        time.sleep(0.5)
    return {
        "running": False,
        "started": True,
        "error": f"started `ollama serve` but it did not answer within {timeout_s:.0f}s",
    }


#: Models with a pull already running in this process. Guards against a second
#: ``autodetect_and_configure(force=True)`` (or any other caller) starting a duplicate
#: multi-GB download for a pull that is already in flight — wasteful, not merely
#: idempotent, since two concurrent `ollama pull` processes each re-negotiate the
#: download rather than sharing one. Mutated from both the calling thread (the
#: check-and-add below) and each pull's own background thread (the discard in
#: ``finally``), so it is guarded by ``_pulling_lock`` rather than left as a bare
#: check-then-act on a plain set — two near-simultaneous callers could otherwise both
#: observe "not pulling yet" before either records intent.
_pulling: set[str] = set()
_pulling_lock = threading.Lock()


def pull_model_async(model: str, on_done=None) -> None:
    """Kick off ``ollama pull <model>`` in the background and return immediately.

    A model pull is a multi-GB download that can take minutes on a slow line; engine
    startup (CLI or server) must not block on it. ``on_done(success: bool, model: str)``
    — if given — is called from the background thread once the pull finishes (the
    model name is passed back rather than left for the caller to capture by closure,
    since the caller may not otherwise know which model this thread was pulling), so a
    caller (e.g. :func:`.llm.autodetect_and_configure`) can flip the active provider
    over the moment the model is actually usable, with no polling required.

    A no-op (does not call ``on_done``) if *model* already has a pull running in this
    process — see ``_pulling``.
    """
    with _pulling_lock:
        if model in _pulling:
            log.debug("Pull of %s already in progress — not starting a second one.", model)
            return
        _pulling.add(model)

    def _run() -> None:
        success = False
        try:
            log.info("Pulling local model %s in the background…", model)
            result = subprocess.run(
                ["ollama", "pull", model],
                capture_output=True, text=True, timeout=1800,  # 30 min ceiling
            )
            success = result.returncode == 0
            if not success:
                log.warning("ollama pull %s failed: %s", model, result.stderr[-500:])
        except Exception as exc:
            log.warning("ollama pull %s errored: %s", model, exc)
        finally:
            with _pulling_lock:
                _pulling.discard(model)
            if on_done is not None:
                try:
                    on_done(success, model)
                except Exception:
                    pass

    threading.Thread(target=_run, name=f"ollama-pull-{model}", daemon=True).start()


def ensure_local_model(existing_models: list[str], on_done=None) -> dict:
    """Top-level entry point: install Ollama if missing, and start pulling a
    hardware-appropriate model in the background if nothing chat-capable is pulled
    yet. Always returns immediately — never blocks on a download.

    ``existing_models`` must already be filtered to chat-capable model names (from
    :func:`.llm.list_ollama_models`, excluding embedding-only entries) — this does
    nothing when the operator already has a real chat model and only acts on a
    genuinely bare install; a caller that passes an unfiltered list (embedding models
    included) would wrongly read "nomic-embed-text pulled" as "a chat model is
    already pulled" and skip provisioning forever. ``on_done(success: bool, model:
    str)``, if given, is forwarded to :func:`pull_model_async` — see its docstring.
    """
    if existing_models:
        return {"action": "none", "reason": "a chat model is already pulled"}
    if not _autoinstall_enabled():
        return {"action": "none", "reason": "SNAGR_LLM_AUTOINSTALL=0"}

    hw = detect_hardware()
    pick = recommend_model(hw)
    if not pick["model"]:
        log.info(
            "Local AI summary stays off: %s (%.1f GB RAM detected)",
            pick["note"], hw.get("ram_gb") or 0.0,
        )
        return {"action": "none", "reason": pick["note"], "hardware": hw}

    model = pick["model"]

    def _start_then_pull() -> None:
        """Runs off the calling thread in both branches below. A binary on PATH is
        not the same as a reachable daemon (see :func:`ensure_ollama_running`'s
        docstring) — checking/starting it here, not just after a fresh install,
        also self-heals the "installed once, service never started/has since died"
        case, which is at least as common as "never installed at all" on a real
        examiner's machine.
        """
        running = ensure_ollama_running()
        if not running["running"]:
            log.info(
                "Ollama binary is present but no daemon is reachable, and starting "
                "one failed: %s", running["error"],
            )
            if on_done is not None:
                try:
                    on_done(False, model)
                except Exception:
                    pass
            return
        pull_model_async(model, on_done=on_done)

    if shutil.which("ollama"):
        log.info(
            "Hardware detected (%.1f GB RAM, %s) → pulling %s (%s) in the background.",
            hw.get("ram_gb") or 0.0, hw.get("gpu"), model, pick["note"],
        )
        threading.Thread(
            target=_start_then_pull, name=f"ollama-pull-{model}", daemon=True
        ).start()
        return {
            "action": "pulling",
            "model": model,
            "hardware": hw,
            "install": {"installed": True, "already_present": True, "method": "", "error": ""},
        }

    # Binary missing: installing it (brew/winget/the Linux script) is itself a
    # network call that can take minutes — exactly the "never block startup" problem
    # the model pull above is already careful to avoid. So install-then-start-then-pull
    # run together in ONE background thread; ensure_ollama_binary() must never be
    # called synchronously from here, or engine startup stalls on precisely the fresh,
    # never-configured machine this feature exists to help.
    log.info(
        "Ollama not installed (%.1f GB RAM, %s) → installing and pulling %s (%s) in "
        "the background.",
        hw.get("ram_gb") or 0.0, hw.get("gpu"), model, pick["note"],
    )

    def _install_then_pull() -> None:
        binary = ensure_ollama_binary()
        if not binary["installed"]:
            log.info("Could not provision Ollama: %s", binary["error"])
            if on_done is not None:
                try:
                    on_done(False, model)
                except Exception:
                    pass
            return
        # A fresh install (brew in particular — see ensure_ollama_running's
        # docstring) is exactly the case with the highest odds nothing is listening
        # yet, so this reuses the same start-then-pull path rather than calling
        # pull_model_async directly.
        _start_then_pull()

    threading.Thread(
        target=_install_then_pull, name=f"ollama-install-{model}", daemon=True
    ).start()
    return {"action": "installing", "model": model, "hardware": hw}
