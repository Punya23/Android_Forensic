# -*- mode: python ; coding: utf-8 -*-
# SNAGR — PyInstaller build spec
#
# Usage:
#   cd engine
#   pyinstaller snagr.spec --noconfirm
#
# Output: engine/dist/triage-engine[.exe]
#
# The packaged binary is a self-contained one-directory bundle
# (onedir mode chosen over onefile for faster startup on Windows).
#
# The Electron build (app/electron:build) is responsible for bundling
# this output into the final distributable via electron-builder.

import sys
from pathlib import Path

ROOT = Path(SPECPATH)           # engine/
PROJECT = ROOT.parent           # Android_Forensic/

# ---------------------------------------------------------------------------
# Hidden imports — modules loaded dynamically that PyInstaller cannot detect
# ---------------------------------------------------------------------------
HIDDEN = [
    # Flask ecosystem
    "flask",
    "flask_cors",
    "flask_socketio",
    "engineio",
    "socketio",
    "simple_websocket",
    # Pillow codecs loaded lazily
    "PIL._imaging",
    "PIL.JpegImagePlugin",
    "PIL.PngImagePlugin",
    "PIL.WebPImagePlugin",
    # piexif
    "piexif",
    "piexif._exif",
    # sqlite3 is stdlib but PyInstaller sometimes misses platform DLL
    "sqlite3",
    "_sqlite3",
    # Optional ML (gracefully absent if sklearn not installed)
    "sklearn",
    "sklearn.ensemble",
    "sklearn.ensemble._iforest",
    "sklearn.utils._cython_blas",
    "sklearn.neighbors._typedefs",
    "sklearn.utils._weight_vector",
    # Our own dynamic imports
    "triage.intel",
    "triage.intel.casebank",
    "triage.intel.knowledge_graph",
    "triage.intel.llm",
    "triage.intel.ontology",
    "triage.intel.planner",
    "triage.forensics.section65b",
    "triage.forensics.location_summary",
    "triage.ai.behavioral_analysis",
    "triage.recovery.sqlite_recovery",
    "triage.recovery.sqbrite",
    "triage.parsers.telegram",
    "triage.parsers.wifi",
    "triage.parsers.whatsapp_backup",
    "triage.parsers.whatsapp_db",
    "triage.parsers.whatsapp_e2e",
    "triage.parsers.instagram",
    "triage.parsers.snapchat",
]

# ---------------------------------------------------------------------------
# Data files to bundle alongside the binary
# ---------------------------------------------------------------------------
DATAS = [
    # Jinja2 templates (used by report.py)
    ("triage/templates", "triage/templates"),
    # ALEAPP plugins (optional; skip if absent)
    # ("../tools/ALEAPP/scripts", "scripts"),
]

# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
a = Analysis(
    ["triage/server.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=DATAS,
    hiddenimports=HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Heavy packages not needed at runtime
        "IPython", "jupyter", "notebook",
        "matplotlib", "scipy",
        "tkinter",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="triage-engine",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,           # UPX can trigger AV false-positives — keep off for field deployments
    console=True,        # keep console visible so field examiners can see engine logs
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="triage-engine",
)
