#!/usr/bin/env python3
"""
eRakshak — Portable Package Build Script
=========================================

Produces a self-contained folder:

  dist/eRakshak-<version>/
    engine/          ← PyInstaller one-dir bundle (Python not required on target)
    adb/             ← bundled adb binary for the target platform
    app/             ← Electron renderer build (Vite dist/)
    electron/        ← Electron main process scripts
    run.bat          ← Windows launcher
    run.sh           ← macOS/Linux launcher
    README.md

Usage
-----
    python build_package.py [--version 1.0.0] [--platform win32|darwin|linux]

Prerequisites
-------------
    pip install pyinstaller
    cd engine && pip install -e .
    cd app && npm install
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent        # Android_Forensic/
ENGINE_DIR  = ROOT / "engine"
APP_DIR     = ROOT / "app"
DIST_ROOT   = ROOT / "dist"

# Platform-specific adb binary name
_ADB_BINS = {
    "win32":  "adb.exe",
    "darwin": "adb",
    "linux":  "adb",
}


def run(cmd: list[str], cwd: Path | None = None) -> None:
    print(f"\n▶  {' '.join(cmd)}", flush=True)
    result = subprocess.run(cmd, cwd=str(cwd or ROOT))
    if result.returncode != 0:
        sys.exit(f"Command failed with code {result.returncode}: {' '.join(cmd)}")


def find_adb() -> Path | None:
    """Return the path to the system adb binary, if found."""
    adb = shutil.which("adb")
    return Path(adb) if adb else None


# ---------------------------------------------------------------------------
# Build steps
# ---------------------------------------------------------------------------

def build_engine() -> Path:
    """Run PyInstaller and return the output directory."""
    print("\n━━━ Step 1/4  Building Python engine ━━━")
    spec = ENGINE_DIR / "erakshak.spec"
    if not spec.exists():
        sys.exit(f"erakshak.spec not found at {spec}. Run this script from the project root.")
    run(
        [sys.executable, "-m", "PyInstaller", str(spec), "--noconfirm", "--clean"],
        cwd=ENGINE_DIR,
    )
    out = ENGINE_DIR / "dist" / "triage-engine"
    if not out.exists():
        sys.exit(f"PyInstaller output not found at {out}")
    return out


def build_frontend() -> Path:
    """Run vite build and return the dist directory."""
    print("\n━━━ Step 2/4  Building Electron renderer (Vite) ━━━")
    run(["npm", "run", "build"], cwd=APP_DIR)
    out = APP_DIR / "dist"
    if not out.exists():
        sys.exit(f"Vite build output not found at {out}")
    return out


def bundle(version: str, engine_dir: Path, frontend_dir: Path, target_platform: str) -> Path:
    """Assemble the portable folder."""
    print("\n━━━ Step 3/4  Assembling portable bundle ━━━")
    dest = DIST_ROOT / f"eRakshak-{version}"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    # Engine binary
    shutil.copytree(engine_dir, dest / "engine", dirs_exist_ok=True)

    # Renderer
    shutil.copytree(frontend_dir, dest / "app", dirs_exist_ok=True)

    # Electron scripts (main, preload, electron modules)
    for item in (APP_DIR / "electron").iterdir():
        target = dest / "electron" / item.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)

    # Bundled adb binary
    adb_src = find_adb()
    if adb_src:
        adb_dest = dest / "adb" / _ADB_BINS.get(target_platform, "adb")
        adb_dest.parent.mkdir(exist_ok=True)
        shutil.copy2(adb_src, adb_dest)
        print(f"  Bundled adb from {adb_src}")
    else:
        print("  ⚠  adb not found on PATH — target machine must have adb available.")

    # Launchers
    _write_launchers(dest, version)

    # README
    _write_readme(dest, version, adb_bundled=adb_src is not None)

    return dest


def _write_launchers(dest: Path, version: str) -> None:
    win_bat = dest / "run.bat"
    win_bat.write_text(
        "@echo off\r\n"
        "echo eRakshak v" + version + " — starting engine and dashboard...\r\n"
        'set ADB_PATH=%~dp0adb\r\n'
        'set PATH=%ADB_PATH%;%PATH%\r\n'
        'start /B "" "%~dp0engine\\triage-engine.exe" --port 5057 --cases .\\cases\r\n'
        "timeout /t 3 /nobreak >nul\r\n"
        'start "" "%~dp0electron\\main.cjs"\r\n',
        encoding="utf-8",
    )
    linux_sh = dest / "run.sh"
    linux_sh.write_text(
        "#!/usr/bin/env bash\n"
        f"# eRakshak v{version}\n"
        'SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"\n'
        'export PATH="$SCRIPT_DIR/adb:$PATH"\n'
        '"$SCRIPT_DIR/engine/triage-engine" --port 5057 --cases "$SCRIPT_DIR/cases" &\n'
        "ENGINE_PID=$!\n"
        "sleep 2\n"
        'electron "$SCRIPT_DIR/electron/main.cjs"\n'
        "kill $ENGINE_PID 2>/dev/null\n",
        encoding="utf-8",
    )
    linux_sh.chmod(0o755)


def _write_readme(dest: Path, version: str, adb_bundled: bool) -> None:
    adb_note = (
        "adb is bundled in the `adb/` folder and added to PATH automatically."
        if adb_bundled
        else "**adb must be installed separately** and available on PATH."
    )
    (dest / "README.md").write_text(
        f"# eRakshak v{version} — Portable Bundle\n\n"
        "## Quick Start\n\n"
        "**Windows**\n```\nrun.bat\n```\n\n"
        "**macOS / Linux**\n```\nbash run.sh\n```\n\n"
        f"## ADB\n{adb_note}\n\n"
        "## Case Data\nCases are stored in the `cases/` directory beside this README.\n\n"
        "## Requirements\n- No Python installation required on the target machine.\n"
        "- No Node.js required — Electron is bundled separately via electron-builder.\n",
        encoding="utf-8",
    )


def verify(bundle_path: Path) -> None:
    print("\n━━━ Step 4/4  Verifying bundle ━━━")
    expected = [
        "engine",
        "app",
        "electron",
        "run.bat",
        "run.sh",
        "README.md",
    ]
    for item in expected:
        p = bundle_path / item
        status = "✅" if p.exists() else "❌ MISSING"
        print(f"  {status}  {item}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="eRakshak portable package builder")
    parser.add_argument("--version",  default="0.1.0")
    parser.add_argument("--platform", default=sys.platform,
                        choices=["win32", "darwin", "linux"])
    parser.add_argument("--skip-engine",   action="store_true",
                        help="Skip PyInstaller (use existing engine/dist/)")
    parser.add_argument("--skip-frontend", action="store_true",
                        help="Skip Vite build (use existing app/dist/)")
    args = parser.parse_args()

    print(f"Building eRakshak v{args.version} for {args.platform}")

    engine_dir  = build_engine()  if not args.skip_engine   else ENGINE_DIR / "dist" / "triage-engine"
    frontend_dir = build_frontend() if not args.skip_frontend else APP_DIR / "dist"

    bundle_path = bundle(args.version, engine_dir, frontend_dir, args.platform)
    verify(bundle_path)

    size_mb = sum(f.stat().st_size for f in bundle_path.rglob("*") if f.is_file()) / 1_048_576
    print(f"\n✅  Bundle ready at: {bundle_path}")
    print(f"   Total size: {size_mb:.1f} MB")


if __name__ == "__main__":
    main()
