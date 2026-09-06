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
import logging
import os
import platform
import shutil
import subprocess
import threading
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
    return {
        "platform": system,
        "arch": platform.machine() or "unknown",
        "cpu_cores": os.cpu_count() or 1,
        "ram_gb": round(ram_gb, 1) if ram_gb else None,
        "gpu": _detect_gpu(system),
    }


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


# --- 2. model tier selection ---------------------------------------------------
# (ram_ceiling_gb, model, note) — a machine qualifies for the first tier whose ceiling
# it is strictly below (see recommend_model), so the tuple's first field is an upper
# bound, not a minimum. RAM-gated, not VRAM-gated: Ollama on a machine with no
# dedicated GPU still runs on CPU, just slower — the ceiling that actually crashes the
# box is unified/system RAM, since the model, the OS, the Electron dashboard and the
# Python engine all share it. Each tier leaves roughly half the machine's RAM for
# everything else, which is conservative on purpose — a forensic workstation running
# out of memory mid-acquisition is a worse failure than an examiner getting the
# second-best model.
_MODEL_TIERS: list[tuple[float, Optional[str], str]] = [
    (10.0, None, "below 10 GB RAM — local AI summaries stay off; heuristic only"),
    (16.0, "qwen2.5:3b-instruct", "~2 GB download"),
    (24.0, "llama3.1:8b", "~4.7 GB download"),
    (40.0, "qwen2.5:14b-instruct", "~9 GB download"),
    (float("inf"), "qwen2.5:32b-instruct", "~19 GB download"),
]


def recommend_model(hw: Optional[dict] = None) -> dict:
    """Pick the strongest chat model this machine's RAM can carry.

    ``model`` is ``None`` when the machine is below the minimum tier — the caller
    must not attempt a pull in that case and should stay on the heuristic provider.
    Unknown RAM (probe failed) is treated as the minimum tier: the safe direction to
    guess wrong in is "smaller model", not "crash a low-memory machine".
    """
    hw = hw or detect_hardware()
    ram = hw.get("ram_gb")
    if not ram:
        ram = 0.0
    for ceiling, model, note in _MODEL_TIERS:
        if ram < ceiling:
            return {"model": model, "note": note, "ram_gb": hw.get("ram_gb")}
    # Unreachable (last ceiling is inf) but keeps the function total.
    return {"model": None, "note": "no tier matched", "ram_gb": hw.get("ram_gb")}


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

    if shutil.which("ollama"):
        # Binary already present — only the pull (already backgrounded) is needed.
        log.info(
            "Hardware detected (%.1f GB RAM, %s) → pulling %s (%s) in the background.",
            hw.get("ram_gb") or 0.0, hw.get("gpu"), model, pick["note"],
        )
        pull_model_async(model, on_done=on_done)
        return {
            "action": "pulling",
            "model": model,
            "hardware": hw,
            "install": {"installed": True, "already_present": True, "method": "", "error": ""},
        }

    # Binary missing: installing it (brew/winget/the Linux script) is itself a
    # network call that can take minutes — exactly the "never block startup" problem
    # the model pull above is already careful to avoid. So install-then-pull run
    # together in ONE background thread; ensure_ollama_binary() must never be called
    # synchronously from here, or engine startup stalls on precisely the fresh,
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
        pull_model_async(model, on_done=on_done)

    threading.Thread(
        target=_install_then_pull, name=f"ollama-install-{model}", daemon=True
    ).start()
    return {"action": "installing", "model": model, "hardware": hw}
