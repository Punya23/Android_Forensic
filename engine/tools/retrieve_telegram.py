#!/usr/bin/env python3
"""
retrieve_telegram.py
================================================================================
eRakshak -- Telegram Forensic Retrieval Tool
================================================================================

What this tool extracts WITHOUT root (via ADB + content provider):
  - Telegram media: images, video, audio, documents, stories
  - Telegram contacts/participants (via content://com.android.contacts)
  - Recent Telegram notifications (via dumpsys)
  - App metadata: version, install time, permissions

What requires ROOT to get (not available on this device):
  - cache4.db  -- the encrypted message database
  - usernames, chat history, message text
  (Use recover_telegram_cli.py if you have root + the DB)

Output:
  - telegram_media/           -- all pulled media files
  - telegram_summary.json     -- index of files + metadata
  - telegram_notifications.json -- recent TG notifications from dumpsys

Usage:
    python tools/retrieve_telegram.py
    python tools/retrieve_telegram.py --out ./my_output
    python tools/retrieve_telegram.py --serial DEVICE_SERIAL
    python tools/retrieve_telegram.py --no-media   # skip media pull
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

# ---- Paths -------------------------------------------------------------------
_TG_PKG        = "org.telegram.messenger"
_TG_MEDIA_PATH = f"/sdcard/Android/data/{_TG_PKG}/files/Telegram/"

# ---- ANSI colours ------------------------------------------------------------
_IS_TTY = sys.stdout.isatty()
def _c(code: str, t: str) -> str:
    return f"\033[{code}m{t}\033[0m" if _IS_TTY else t

RED    = lambda t: _c("31;1", t)
GREEN  = lambda t: _c("32;1", t)
YELLOW = lambda t: _c("33;1", t)
CYAN   = lambda t: _c("36;1", t)
BOLD   = lambda t: _c("1", t)
DIM    = lambda t: _c("2", t)

_ADB: str = "adb"

def safe_print(*args, **kwargs):
    enc = getattr(sys.stdout, "encoding", "utf-8") or "utf-8"
    safe = []
    for a in args:
        s = str(a)
        try:
            s.encode(enc)
        except (UnicodeEncodeError, LookupError):
            s = s.encode(enc, errors="replace").decode(enc, errors="replace")
        safe.append(s)
    print(*safe, **kwargs)

# ---- ADB helpers -------------------------------------------------------------
def _find_adb() -> str:
    adb = shutil.which("adb")
    if adb:
        return adb
    local = os.environ.get("LOCALAPPDATA", "")
    for c in [
        Path(local) / "Android" / "Sdk" / "platform-tools" / "adb.exe",
        Path.home() / "AppData" / "Local" / "Android" / "Sdk" / "platform-tools" / "adb.exe",
    ]:
        if c.exists():
            return str(c)
    return "adb"

def _adb(serial: str | None, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    cmd = [_ADB] + (["-s", serial] if serial else []) + list(args)
    return subprocess.run(
        cmd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout
    )

def _adb_out(serial: str | None, *args: str, timeout: int = 30) -> str:
    r = _adb(serial, *args, timeout=timeout)
    return r.stdout.strip()

def _get_devices() -> list[dict]:
    r = subprocess.run([_ADB, "devices", "-l"], capture_output=True, text=True, timeout=10)
    out = []
    for line in r.stdout.splitlines()[1:]:
        line = line.strip()
        if not line or "offline" in line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        model = next((p.split(":")[-1] for p in parts if p.startswith("model:")), "unknown")
        out.append({"serial": parts[0], "state": parts[1], "model": model})
    return out

def _fmt_size(b: int) -> str:
    if b >= 1_048_576: return f"{b/1_048_576:.1f} MB"
    if b >= 1024:      return f"{b/1024:.0f} KB"
    return f"{b} B"

def _sep(ch: str = "-", w: int = 78) -> str:
    return ch * w

# ---- Steps -------------------------------------------------------------------

def step_check_device(serial: str | None) -> str:
    print(f"\n{BOLD('[ 1/5 ]  Checking ADB connection...')}")
    devices = [d for d in _get_devices() if d["state"] == "device"]
    if not devices:
        print(RED("  x  No device connected!"))
        sys.exit(1)
    chosen = next((d for d in devices if d["serial"] == serial), devices[0]) if serial else devices[0]
    print(GREEN(f"  OK  {chosen['model']}  ({chosen['serial']})"))
    return chosen["serial"]


def step_check_telegram(serial: str) -> dict:
    print(f"\n{BOLD('[ 2/5 ]  Checking Telegram installation...')}")
    r = _adb(serial, "shell", "pm", "list", "packages", "-f", _TG_PKG)
    if _TG_PKG not in r.stdout:
        print(RED(f"  x  Telegram ({_TG_PKG}) is NOT installed on this device."))
        sys.exit(0)

    # Get version
    ver_r  = _adb_out(serial, "shell", "dumpsys", "package", _TG_PKG)
    version = ""
    inst_ts = ""
    m = re.search(r'versionName=(\S+)', ver_r)
    if m: version = m.group(1)
    m2 = re.search(r'firstInstallTime=(\S+)', ver_r)
    if m2: inst_ts = m2.group(1)

    # Root check
    root_r = _adb(serial, "shell", "su", "-c", "echo root_ok")
    is_root = "root_ok" in root_r.stdout

    info = {
        "package":        _TG_PKG,
        "version":        version,
        "first_install":  inst_ts,
        "rooted":         is_root,
        "db_accessible":  is_root,
    }

    print(GREEN(f"  OK  Telegram v{version} installed"))
    if is_root:
        print(GREEN("  OK  Device is ROOTED -- full database extraction possible!"))
    else:
        print(YELLOW("  !   Device is NOT rooted -- only media files can be extracted."))
        print(DIM("      For full message/chat history, root is required."))
    return info


def step_pull_media(serial: str, out_dir: Path) -> list[dict]:
    print(f"\n{BOLD('[ 3/5 ]  Pulling Telegram media files...')}")
    media_dir = out_dir / "telegram_media"
    media_dir.mkdir(parents=True, exist_ok=True)

    # Count first
    count_r = _adb_out(serial, "shell",
        f"find '{_TG_MEDIA_PATH}' -type f 2>/dev/null | wc -l")
    total = int(count_r.strip()) if count_r.strip().isdigit() else 0
    print(DIM(f"     {total} file(s) found in Telegram storage"))

    if total == 0:
        print(YELLOW("  !  No media files found in accessible Telegram paths."))
        return []

    # Pull entire Telegram folder tree
    r = _adb(serial, "pull", _TG_MEDIA_PATH, str(media_dir), timeout=300)
    print(DIM(f"     adb pull: {r.stdout.strip()[:80]}"))

    # Index everything that was pulled
    index: list[dict] = []
    cat_map = {
        "Telegram Images":    "image",
        "Telegram Video":     "video",
        "Telegram Audio":     "audio",
        "Telegram Documents": "document",
        "Telegram Files":     "file",
        "Telegram Stories":   "story",
    }
    for f in sorted(media_dir.rglob("*")):
        if not f.is_file():
            continue
        category = "other"
        for folder_name, cat in cat_map.items():
            if folder_name in str(f):
                category = cat
                break
        index.append({
            "filename":  f.name,
            "category":  category,
            "size_bytes": f.stat().st_size,
            "path":      str(f),
            "extension": f.suffix.lower().lstrip("."),
        })

    if index:
        print(GREEN(f"  OK  {len(index)} file(s) pulled -> {media_dir}"))
    return index


def step_pull_notifications(serial: str, out_dir: Path) -> list[dict]:
    print(f"\n{BOLD('[ 4/5 ]  Extracting Telegram notifications from system...')}")
    r = _adb(serial, "shell", "dumpsys", "notification", "--noredact", timeout=15)
    if r.returncode != 0 or not r.stdout:
        print(YELLOW("  !  dumpsys failed"))
        return []

    records = []
    blocks = re.split(r'NotificationRecord\(', r.stdout)
    for block in blocks[1:]:
        pkg_m = re.search(r'pkg=(\S+)', block)
        if not pkg_m or _TG_PKG not in pkg_m.group(1):
            continue
        rec: dict = {"source": "dumpsys", "package": _TG_PKG}
        when_m  = re.search(r'when=(\d{10,})',                   block)
        title_m = re.search(r'android\.title=String \((.+?)\)',  block)
        text_m  = re.search(r'android\.text=String \((.+?)\)',   block)
        big_m   = re.search(r'android\.bigText=String \((.+?)\)',block)
        ch_m    = re.search(r'channel=(\S+)',                    block)
        if when_m:  rec["post_time"]  = int(when_m.group(1))
        if title_m: rec["title"]      = title_m.group(1)
        if text_m:  rec["text"]       = text_m.group(1)[:500]
        if big_m:   rec["big_text"]   = big_m.group(1)[:500]
        if ch_m:    rec["channel_id"] = ch_m.group(1).rstrip(')')
        records.append(rec)

    local = out_dir / "telegram_notifications.json"
    local.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")

    if records:
        print(GREEN(f"  OK  {len(records)} Telegram notification(s) -> {local}"))
    else:
        print(YELLOW("  !  No active Telegram notifications in system right now."))
    return records


def step_try_root_db(serial: str, out_dir: Path, is_root: bool) -> dict:
    print(f"\n{BOLD('[ 5/5 ]  Attempting database extraction...')}")

    if not is_root:
        print(YELLOW("  !  Skipped -- device is NOT rooted."))
        print(DIM("     The Telegram message database (cache4.db) lives in:"))
        print(DIM(f"     /data/data/{_TG_PKG}/files/"))
        print(DIM("     It is encrypted and only accessible with root."))
        print(DIM("     To decrypt it, you also need the account's auth key from tgnet.dat"))
        return {"status": "not_rooted"}

    print(DIM("     Device is rooted -- copying database..."))
    db_paths = [
        f"/data/data/{_TG_PKG}/files/cache4.db",
        f"/data/data/{_TG_PKG}/files/cache4-wal",
        f"/data/data/{_TG_PKG}/files/cache4-shm",
        f"/data/data/{_TG_PKG}/files/tgnet.dat",
        f"/data/data/{_TG_PKG}/files/config.dat",
    ]
    db_dir = out_dir / "telegram_db"
    db_dir.mkdir(parents=True, exist_ok=True)
    pulled = []
    for path in db_paths:
        # Copy to sdcard first (root), then adb pull
        fname = Path(path).name
        tmp = f"/sdcard/Download/_tg_{fname}"
        r1 = _adb(serial, "shell", "su", "-c", f"cp '{path}' '{tmp}' 2>/dev/null && echo ok")
        if "ok" in r1.stdout:
            r2 = _adb(serial, "pull", tmp, str(db_dir / fname))
            _adb(serial, "shell", "su", "-c", f"rm '{tmp}'")
            if r2.returncode == 0:
                pulled.append(fname)
                print(GREEN(f"  OK  Pulled: {fname}"))

    if not pulled:
        print(YELLOW("  !  Could not copy any DB files via root."))
        return {"status": "root_copy_failed"}

    # Run the existing telegram parser on cache4.db if pulled
    db_file = db_dir / "cache4.db"
    if db_file.exists():
        print(GREEN(f"\n  OK  cache4.db pulled ({_fmt_size(db_file.stat().st_size)})"))
        print(DIM("     Running recover_telegram_cli.py on extracted DB..."))
        tool = Path(__file__).parent / "recover_telegram_cli.py"
        py   = sys.executable
        r3   = subprocess.run(
            [py, str(tool), str(db_file), "--output", str(db_dir / "telegram_messages.json")],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120
        )
        print(r3.stdout[:2000])
        if r3.returncode == 0:
            print(GREEN("  OK  Message recovery complete!"))
            return {"status": "success", "db_dir": str(db_dir), "pulled": pulled}

    return {"status": "partial", "pulled": pulled}


# ---- Report ------------------------------------------------------------------

def print_report(media: list[dict], notifs: list[dict], db_result: dict, out_dir: Path) -> None:
    print(f"\n{BOLD(_sep('='))}")
    print(f"{BOLD('  TELEGRAM FORENSIC SUMMARY')}")
    print(BOLD(_sep('=')))

    # Media by category
    print(f"\n  {BOLD('Media files pulled:')}")
    by_cat: dict[str, list] = {}
    for f in media:
        by_cat.setdefault(f["category"], []).append(f)

    if not by_cat:
        print(YELLOW("  No media files pulled."))
    else:
        print(f"  {'CATEGORY':<20} {'COUNT':>6}  {'TOTAL SIZE':>12}")
        print(f"  {_sep('-', 42)}")
        for cat, files in sorted(by_cat.items()):
            total = sum(f["size_bytes"] for f in files)
            print(f"  {cat.upper():<20} {len(files):>6}  {_fmt_size(total):>12}")

    # File listing
    if media:
        print(f"\n  {BOLD('File index (newest first):')}")
        print(f"  {'CATEGORY':<14} {'SIZE':>8}  FILENAME")
        print(f"  {_sep('-', 60)}")
        for f in sorted(media, key=lambda x: x["size_bytes"], reverse=True)[:30]:
            safe_print(f"  {f['category']:<14} {_fmt_size(f['size_bytes']):>8}  {f['filename']}")

    # Notifications
    print(f"\n  {BOLD('Active Telegram notifications:')}")
    if not notifs:
        print(YELLOW("  No active Telegram notifications found."))
    else:
        for n in notifs:
            ts = ""
            if n.get("post_time"):
                ts = datetime.datetime.fromtimestamp(n["post_time"]/1000).strftime("%Y-%m-%d %H:%M:%S")
            safe_print(f"  [{ts}]  {n.get('title','--')} -- {n.get('text','--')[:80]}")

    # DB status
    print(f"\n  {BOLD('Database extraction:')}")
    status = db_result.get("status", "unknown")
    if status == "success":
        print(GREEN(f"  OK  Full DB pulled + messages recovered!"))
        print(GREEN(f"      -> {db_result.get('db_dir')}"))
    elif status == "not_rooted":
        print(YELLOW("  !   Device not rooted -- message DB not accessible."))
        print(DIM("      To get full chat history, root the device first."))
    else:
        print(YELLOW(f"  !   Status: {status}"))

    # Output files
    print(f"\n  {BOLD('Output files:')}")
    for fname in ["telegram_summary.json", "telegram_notifications.json"]:
        p = out_dir / fname
        if p.exists():
            print(GREEN(f"  OK  {p}  ({_fmt_size(p.stat().st_size)})"))
    tg_media = out_dir / "telegram_media"
    if tg_media.exists():
        files = [f for f in tg_media.rglob("*") if f.is_file()]
        total = sum(f.stat().st_size for f in files)
        print(GREEN(f"  OK  {tg_media}/  ({len(files)} files, {_fmt_size(total)})"))

    print(BOLD(_sep('=')))


# ---- HTML Gallery ------------------------------------------------------------

def generate_html_gallery(media: list[dict], tg_info: dict, out_dir: Path,
                           device_serial: str, ext_at: str) -> Path | None:
    """Generate a self-contained HTML gallery with base64-embedded images."""
    import base64

    images = [m for m in media if m.get("extension", "") in ("jpg","jpeg","png","gif","webp","bmp")]
    if not images:
        return None

    print(f"\n{BOLD('Generating HTML media gallery...')}")

    cat_color = {
        "image":    ("#2ca5e0", "rgba(44,165,224,.18)", "rgba(44,165,224,.3)"),
        "story":    ("#e040fb", "rgba(224,64,251,.16)",  "rgba(224,64,251,.28)"),
        "video":    ("#00e676", "rgba(0,230,118,.16)",   "rgba(0,230,118,.28)"),
        "document": ("#f5c842", "rgba(245,200,66,.16)",  "rgba(245,200,66,.28)"),
        "other":    ("#8890a6", "rgba(136,144,166,.16)", "rgba(136,144,166,.28)"),
    }

    cards_html = ""
    for f in sorted(images, key=lambda x: x.get("size_bytes", 0), reverse=True):
        fp = Path(f["path"])
        if not fp.exists():
            continue
        b64  = base64.b64encode(fp.read_bytes()).decode()
        ext  = f.get("extension", "jpg")
        cat  = f.get("category", "other")
        sz   = _fmt_size(f.get("size_bytes", 0))
        name = f["filename"]
        fg, bg, bd = cat_color.get(cat, cat_color["other"])
        mime = "jpeg" if ext in ("jpg","jpeg") else ext
        data_uri = f"data:image/{mime};base64,{b64}"
        cards_html += f"""
    <div class="card" onclick="openModal('{data_uri}','{name}','{sz}')">
      <div class="img-wrap">
        <img src="{data_uri}" alt="{name}" loading="lazy"/>
        <div class="overlay"><span class="zoom-icon">&#128269;</span></div>
      </div>
      <div class="card-info">
        <span class="cat-badge" style="color:{fg};background:{bg};border-color:{bd}">{cat.upper()}</span>
        <div class="fname">{name}</div>
        <div class="fsize">{sz}</div>
      </div>
    </div>"""

    version     = tg_info.get("version", "—")
    installed   = tg_info.get("first_install", "—")
    root_badge  = '<span class="badge badge-ok">ROOTED</span>' if tg_info.get("rooted") else '<span class="badge badge-warn">NOT ROOTED</span>'
    db_badge    = '<span class="badge badge-ok">ACCESSIBLE</span>' if tg_info.get("db_accessible") else '<span class="badge badge-warn">INACCESSIBLE</span>'
    total_sz    = _fmt_size(sum(m.get("size_bytes",0) for m in images))

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>eRakshak — Telegram Media Gallery</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
  *,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
  :root{{
    --bg:#0d0f14;--surface:#161a23;--surface2:#1e2330;--border:#2a2f3e;
    --accent:#2ca5e0;--accent2:#1a7fb5;--text:#e8eaf0;--muted:#8890a6;
    --gold:#f5c842;--ok:#1c7d3f;--ok-bg:#e4f4ea;
  }}
  body{{background:var(--bg);color:var(--text);font-family:'Inter',sans-serif;min-height:100vh}}
  .header{{background:linear-gradient(135deg,#0f172a 0%,#1a1f35 50%,#0f1a2e 100%);
    border-bottom:1px solid var(--border);padding:24px 40px;display:flex;align-items:center;gap:20px}}
  .tg-logo{{width:52px;height:52px;border-radius:14px;flex-shrink:0;
    background:linear-gradient(135deg,#2ca5e0,#1a7fb5);
    display:flex;align-items:center;justify-content:center;
    font-size:28px;box-shadow:0 4px 20px rgba(44,165,224,.4)}}
  .header-text h1{{font-size:22px;font-weight:700}}
  .header-text .sub{{font-size:13px;color:var(--muted);margin-top:3px}}
  .meta-bar{{background:var(--surface);border-bottom:1px solid var(--border);
    padding:14px 40px;display:flex;gap:32px;flex-wrap:wrap}}
  .meta-item{{display:flex;flex-direction:column}}
  .meta-label{{font-size:10px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}}
  .meta-value{{font-size:13px;font-weight:500;margin-top:2px}}
  .badge{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:10px;font-weight:700;letter-spacing:.06em}}
  .badge-warn{{background:rgba(245,200,66,.14);color:var(--gold);border:1px solid rgba(245,200,66,.3)}}
  .badge-ok{{background:rgba(28,125,63,.18);color:#3dba6f;border:1px solid rgba(28,125,63,.35)}}
  .stats-strip{{background:var(--surface2);border-bottom:1px solid var(--border);
    padding:14px 40px;display:flex;gap:32px;flex-wrap:wrap;align-items:center}}
  .stat{{display:flex;align-items:center;gap:10px}}
  .stat-num{{font-size:24px;font-weight:700;color:var(--accent)}}
  .stat-lbl{{font-size:11px;color:var(--muted);line-height:1.4}}
  .warn-banner{{margin:20px 40px 0;background:rgba(245,200,66,.07);
    border:1px solid rgba(245,200,66,.28);border-left:4px solid var(--gold);
    border-radius:6px;padding:12px 18px;font-size:13px;color:#d4b84a}}
  .warn-banner b{{color:var(--gold)}}
  .gallery-header{{padding:28px 40px 14px;display:flex;align-items:center;justify-content:space-between}}
  .gallery-header h2{{font-size:17px;font-weight:600}}
  .gallery-header .count{{font-size:13px;color:var(--muted)}}
  .gallery{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));
    gap:18px;padding:0 40px 48px}}
  .card{{background:var(--surface);border:1px solid var(--border);border-radius:12px;
    overflow:hidden;cursor:pointer;transition:transform .2s,box-shadow .2s,border-color .2s}}
  .card:hover{{transform:translateY(-5px);box-shadow:0 14px 36px rgba(0,0,0,.55);border-color:var(--accent)}}
  .img-wrap{{position:relative;aspect-ratio:9/16;overflow:hidden;background:#0a0c12}}
  .img-wrap img{{width:100%;height:100%;object-fit:cover;display:block;transition:transform .3s}}
  .card:hover .img-wrap img{{transform:scale(1.05)}}
  .overlay{{position:absolute;inset:0;background:rgba(44,165,224,0);
    display:flex;align-items:center;justify-content:center;transition:background .2s}}
  .card:hover .overlay{{background:rgba(44,165,224,.18)}}
  .zoom-icon{{font-size:34px;opacity:0;transition:opacity .2s}}
  .card:hover .zoom-icon{{opacity:1}}
  .card-info{{padding:12px 14px 14px}}
  .cat-badge{{display:inline-block;margin-bottom:6px;padding:2px 8px;border-radius:4px;
    font-size:9px;font-weight:700;letter-spacing:.08em}}
  .fname{{font-size:11px;color:var(--muted);word-break:break-all;line-height:1.4;margin-top:2px}}
  .fsize{{font-size:12px;color:var(--accent);margin-top:5px;font-weight:600}}
  .modal{{display:none;position:fixed;inset:0;z-index:1000;background:rgba(0,0,0,.92);
    backdrop-filter:blur(10px);align-items:center;justify-content:center;flex-direction:column}}
  .modal.open{{display:flex}}
  .modal img{{max-height:82vh;max-width:88vw;border-radius:10px;box-shadow:0 0 80px rgba(44,165,224,.35)}}
  .modal-meta{{margin-top:14px;display:flex;gap:20px;align-items:center}}
  .modal-fname{{font-size:12px;color:var(--muted)}}
  .modal-sz{{font-size:12px;color:var(--accent);font-weight:600}}
  .modal-close{{position:absolute;top:20px;right:28px;background:var(--surface2);
    border:1px solid var(--border);color:var(--text);font-size:20px;width:38px;height:38px;
    border-radius:8px;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:background .15s}}
  .modal-close:hover{{background:var(--accent2)}}
  footer{{text-align:center;padding:20px 40px;font-size:11px;color:var(--muted);
    border-top:1px solid var(--border)}}
</style>
</head>
<body>
<div class="header">
  <div class="tg-logo">&#9992;</div>
  <div class="header-text">
    <h1>Telegram Media Gallery</h1>
    <div class="sub">eRakshak Forensic Triage &nbsp;&middot;&nbsp; Device: {device_serial}</div>
  </div>
</div>
<div class="meta-bar">
  <div class="meta-item"><span class="meta-label">Extracted</span><span class="meta-value">{ext_at}</span></div>
  <div class="meta-item"><span class="meta-label">Telegram Version</span><span class="meta-value">{version}</span></div>
  <div class="meta-item"><span class="meta-label">First Installed</span><span class="meta-value">{installed}</span></div>
  <div class="meta-item"><span class="meta-label">Root Access</span><span class="meta-value">{root_badge}</span></div>
  <div class="meta-item"><span class="meta-label">Message DB</span><span class="meta-value">{db_badge}</span></div>
</div>
<div class="stats-strip">
  <div class="stat"><div class="stat-num">{len(images)}</div><div class="stat-lbl">Images<br>Pulled</div></div>
  <div class="stat"><div class="stat-num">{total_sz}</div><div class="stat-lbl">Total<br>Size</div></div>
  <div class="stat"><div class="stat-num">{len(set(m['category'] for m in images))}</div><div class="stat-lbl">Media<br>Types</div></div>
</div>
<div class="warn-banner">
  <b>&#9888; Forensic Note:</b> Only media accessible without root is shown.
  Full chat history requires root access to <code>/data/data/org.telegram.messenger/files/cache4.db</code>.
</div>
<div class="gallery-header">
  <h2>&#128247; Retrieved Media Files</h2>
  <span class="count">{len(images)} image(s)</span>
</div>
<div class="gallery">{cards_html}</div>
<div class="modal" id="modal" onclick="closeModal()">
  <button class="modal-close" onclick="closeModal()">&#10005;</button>
  <img id="modal-img" src="" alt=""/>
  <div class="modal-meta">
    <span class="modal-fname" id="modal-fname"></span>
    <span class="modal-sz"    id="modal-sz"></span>
  </div>
</div>
<footer>eRakshak Forensic Tool &nbsp;&middot;&nbsp; Triage Preview &nbsp;&middot;&nbsp; {ext_at} &nbsp;&middot;&nbsp; Do not alter.</footer>
<script>
  function openModal(src,name,sz){{
    document.getElementById('modal-img').src=src;
    document.getElementById('modal-fname').textContent=name;
    document.getElementById('modal-sz').textContent=sz;
    document.getElementById('modal').classList.add('open');
    event.stopPropagation();
  }}
  function closeModal(){{
    document.getElementById('modal').classList.remove('open');
    document.getElementById('modal-img').src='';
  }}
  document.addEventListener('keydown',e=>{{if(e.key==='Escape')closeModal();}});
</script>
</body>
</html>"""

    gallery_path = out_dir / "telegram_media_gallery.html"
    gallery_path.write_text(html, encoding="utf-8")
    print(GREEN(f"  OK  Gallery -> {gallery_path}  ({_fmt_size(gallery_path.stat().st_size)})"))
    return gallery_path


# ---- Main --------------------------------------------------------------------

def main() -> None:
    global _ADB
    parser = argparse.ArgumentParser(
        description="eRakshak -- Telegram Forensic Retrieval Tool"
    )
    parser.add_argument("--serial",   help="Target specific device serial")
    parser.add_argument("--out",      help="Output directory (default: Desktop/Android_Forensic)")
    parser.add_argument("--no-media", action="store_true", help="Skip pulling media files")
    args = parser.parse_args()

    _ADB    = _find_adb()
    out_dir = Path(args.out) if args.out else (Path.home() / "Desktop" / "Android_Forensic")
    out_dir.mkdir(parents=True, exist_ok=True)

    print()
    print(BOLD("=" * 78))
    print(BOLD("  eRakshak  Telegram Forensic Retrieval Session"))
    print(BOLD("=" * 78))
    print(DIM(f"  ADB:     {_ADB}"))
    print(DIM(f"  Output:  {out_dir}"))
    print(DIM(f"  Started: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"))
    print(BOLD("=" * 78))

    serial  = step_check_device(args.serial)
    tg_info = step_check_telegram(serial)

    media: list[dict] = []
    if not args.no_media:
        media = step_pull_media(serial, out_dir)

    notifs    = step_pull_notifications(serial, out_dir)
    db_result = step_try_root_db(serial, out_dir, tg_info.get("rooted", False))

    # Save summary JSON
    summary = {
        "extracted_at":   datetime.datetime.now().isoformat(),
        "device_serial":  serial,
        "telegram":       tg_info,
        "media_count":    len(media),
        "media_files":    media,
        "notifications":  notifs,
        "db_extraction":  db_result,
    }
    summary_path = out_dir / "telegram_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print_report(media, notifs, db_result, out_dir)

    gallery = generate_html_gallery(
        media, tg_info, out_dir, serial,
        datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    if gallery:
        print(GREEN(f"  Gallery: {gallery}"))

    print(f"\n{GREEN(BOLD('  Session complete!'))}")
    print(BOLD("=" * 78))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{YELLOW('Interrupted.')}")
        sys.exit(0)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"\n{RED(f'Error: {exc}')}")
        import traceback; traceback.print_exc()
        sys.exit(3)
