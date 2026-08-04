#!/usr/bin/env python3
"""
retrieve_recordings_notifications.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
eRakshak — Call Recordings & Notification History Retrieval Tool
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

A self-contained forensic session tool that:
  1. Verifies an Android device is connected via ADB
  2. Installs / updates the eRakshak Collector APK on the device
  3. Grants required permissions automatically
  4. Triggers the call-recordings dump  -> pulls recordings.json
  5. Triggers the notification-history dump -> pulls notifications.json
  6. (Optional) Pulls the actual audio recording files
  7. Prints a formatted summary table to the terminal
  8. Saves everything to an output directory

Usage (from the engine/ directory):
    python tools/retrieve_recordings_notifications.py
    python tools/retrieve_recordings_notifications.py --pull-audio
    python tools/retrieve_recordings_notifications.py --out ./my_output --pull-audio
    python tools/retrieve_recordings_notifications.py --serial DEVICE_SERIAL
    python tools/retrieve_recordings_notifications.py --notifications-only
    python tools/retrieve_recordings_notifications.py --recordings-only

Exit codes:
    0 -- success
    1 -- no device connected
    2 -- APK install failed
    3 -- unexpected error
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# ---- Paths -------------------------------------------------------------------

_SCRIPT_DIR   = Path(__file__).resolve().parent
_ENGINE_ROOT  = _SCRIPT_DIR.parent
_PROJECT_ROOT = _ENGINE_ROOT.parent
_APK_PATH     = (
    _PROJECT_ROOT / "apk" / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
)
_APK_PKG      = "io.erakshak.collector"
_APK_ACTIVITY = f"{_APK_PKG}/.MainActivity"

# ---- ANSI colours (auto-disabled on non-TTY) ---------------------------------

_IS_TTY = sys.stdout.isatty()

def _c(code: str, t: str) -> str:
    return f"\033[{code}m{t}\033[0m" if _IS_TTY else t

RED    = lambda t: _c("31;1", t)
GREEN  = lambda t: _c("32;1", t)
YELLOW = lambda t: _c("33;1", t)
CYAN   = lambda t: _c("36;1", t)
BOLD   = lambda t: _c("1", t)
DIM    = lambda t: _c("2", t)

def safe_print(*args, **kwargs):
    """Print to terminal, replacing emoji/chars that Windows cp1252 cannot encode."""
    enc = getattr(sys.stdout, "encoding", "utf-8") or "utf-8"
    safe_args = []
    for a in args:
        s = str(a)
        try:
            s.encode(enc)
        except (UnicodeEncodeError, LookupError):
            s = s.encode(enc, errors="replace").decode(enc, errors="replace")
        safe_args.append(s)
    print(*safe_args, **kwargs)

_ADB: str = "adb"  # set in main()

# ---- ADB helpers -------------------------------------------------------------

def _find_adb() -> str:
    adb = shutil.which("adb")
    if adb:
        return adb
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        Path(local) / "Android" / "Sdk" / "platform-tools" / "adb.exe",
        Path.home() / "AppData" / "Local" / "Android" / "Sdk" / "platform-tools" / "adb.exe",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return "adb"


def _adb(serial: str | None, *args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    cmd = [_ADB] + (["-s", serial] if serial else []) + list(args)
    # Use encoding='utf-8' with errors='replace' so emoji/Unicode from Android
    # doesn't crash on Windows cp1252 terminals.
    return subprocess.run(
        cmd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout
    )


def _adb_out(serial: str | None, *args: str, timeout: int = 30) -> str:
    r = _adb(serial, *args, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or r.stdout.strip())
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

# ---- Formatters --------------------------------------------------------------

def _fmt_size(b: int) -> str:
    if b >= 1_048_576: return f"{b/1_048_576:.1f} MB"
    if b >= 1024:      return f"{b/1024:.0f} KB"
    return f"{b} B"

def _fmt_dur(ms: int | None) -> str:
    if not ms: return "--"
    s = ms // 1000
    return f"{s//60}m {s%60:02d}s"

def _fmt_ts(ms: int | None) -> str:
    if not ms: return "--"
    try:
        return datetime.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ms)

def _sep(ch: str = "-", w: int = 78) -> str:
    return ch * w

# ---- Session steps -----------------------------------------------------------

def step_check_device(serial: str | None) -> str:
    print(f"\n{BOLD('[ 1/6 ]  Checking ADB device connection...')}")
    devices = [d for d in _get_devices() if d["state"] == "device"]

    if not devices:
        print(RED("  x  No device connected!"))
        print(DIM("     1. Wake and unlock your phone"))
        print(DIM("     2. Plug in the USB cable"))
        print(DIM("     3. Tap 'Allow USB debugging' on the phone screen"))
        print(DIM("     4. Set USB mode to 'File Transfer (MTP)'"))
        sys.exit(1)

    chosen = next((d for d in devices if d["serial"] == serial), devices[0]) if serial else devices[0]
    if serial and chosen["serial"] != serial:
        print(RED(f"  x  Serial {serial!r} not found."))
        sys.exit(1)

    if len(devices) > 1:
        print(YELLOW(f"  !  Multiple devices found -- using: {chosen['serial']}"))

    print(GREEN(f"  OK  {chosen['model']}  ({chosen['serial']})"))
    return chosen["serial"]


def step_install_apk(serial: str) -> None:
    print(f"\n{BOLD('[ 2/6 ]  Installing eRakshak Collector APK...')}")

    if not _APK_PATH.exists():
        r = _adb(serial, "shell", "pm", "list", "packages", _APK_PKG)
        if _APK_PKG in r.stdout:
            print(GREEN("  OK  Collector already installed -- skipping"))
            return
        print(RED(f"  x  APK not found: {_APK_PATH}"))
        print(DIM("     Run:  cd apk && .\\gradlew assembleDebug"))
        sys.exit(2)

    print(DIM(f"     {_APK_PATH}  ({_fmt_size(_APK_PATH.stat().st_size)})"))
    try:
        out = _adb_out(serial, "install", "-r", "-g", str(_APK_PATH), timeout=90)
        print(GREEN(f"  OK  {out.splitlines()[0][:60]}"))
    except RuntimeError as e:
        print(RED(f"  x  Install failed: {e}"))
        sys.exit(2)


def step_grant_permissions(serial: str) -> None:
    print(f"\n{BOLD('[ 3/6 ]  Granting permissions...')}")
    perms = [
        "android.permission.READ_EXTERNAL_STORAGE",
        "android.permission.READ_MEDIA_IMAGES",
        "android.permission.READ_MEDIA_VIDEO",
        "android.permission.READ_MEDIA_AUDIO",
        "android.permission.ACCESS_FINE_LOCATION",
        "android.permission.ACCESS_COARSE_LOCATION",
    ]
    ok = sum(
        1 for p in perms
        if _adb(serial, "shell", "pm", "grant", _APK_PKG, p).returncode == 0
    )
    print(GREEN(f"  OK  {ok}/{len(perms)} permissions granted"))


def step_dump_recordings(serial: str, out_dir: Path) -> list[dict]:
    print(f"\n{BOLD('[ 4/6 ]  Dumping call recordings...')}")
    _adb(serial, "shell", "am", "start", "-n", _APK_ACTIVITY, "--es", "action", "dump_recordings")
    print(DIM("     Collector running on device, waiting 6s..."))
    time.sleep(6)

    local = out_dir / "recordings.json"
    r = _adb(serial, "pull", "/sdcard/Download/recordings.json", str(local))
    if r.returncode != 0 or not local.exists():
        print(YELLOW("  !  recordings.json not pulled -- no call recordings found on device"))
        print(DIM("     Common reasons: recordings stored in a non-standard OEM path,"))
        print(DIM("     or this device does not have automatic call recording enabled."))
        return []

    try:
        recs = json.loads(local.read_text(encoding="utf-8"))
        recs = recs if isinstance(recs, list) else []
    except Exception as e:
        print(YELLOW(f"  !  Parse error: {e}"))
        return []

    print(GREEN(f"  OK  {len(recs)} recording(s) found  ->  {local}"))
    return recs




def _parse_dumpsys_notifications(raw: str) -> list[dict]:
    """Parse `dumpsys notification --noredact` output into structured records."""
    import re
    records = []
    blocks = re.split(r'NotificationRecord\(', raw)
    for block in blocks[1:]:
        rec: dict = {"source": "dumpsys"}
        pkg_m   = re.search(r'pkg=(\S+)',                      block)
        when_m  = re.search(r'when=(\d{10,})',                 block)
        ch_m    = re.search(r'channel=(\S+)',                  block)
        title_m = re.search(r'android\.title=String \((.+?)\)',   block)
        text_m  = re.search(r'android\.text=String \((.+?)\)',    block)
        big_m   = re.search(r'android\.bigText=String \((.+?)\)', block)
        sub_m   = re.search(r'android\.subText=String \((.+?)\)', block)
        imp_m   = re.search(r'importance=(\d+)',               block)
        if pkg_m:   rec["package"]    = pkg_m.group(1)
        if when_m:  rec["post_time"]  = int(when_m.group(1))
        if ch_m:    rec["channel_id"] = ch_m.group(1).rstrip(')')
        if title_m: rec["title"]      = title_m.group(1)
        if text_m:  rec["text"]       = text_m.group(1)[:500]
        if big_m:   rec["big_text"]   = big_m.group(1)[:500]
        if sub_m:   rec["sub_text"]   = sub_m.group(1)
        if imp_m:   rec["importance"] = int(imp_m.group(1))
        if rec.get("package"):
            rec.setdefault("app_label", rec["package"])
            records.append(rec)
    return records


def step_dump_notifications(serial: str, out_dir: Path) -> list[dict]:
    print(f"\n{BOLD('[ 5/6 ]  Dumping notification history...')}")

    # Method 1: dumpsys notification (no permission required -- reads live notifications)
    print(DIM("     [1] Trying dumpsys notification (no permission needed)..."))
    r = _adb(serial, "shell", "dumpsys", "notification", "--noredact", timeout=15)
    dumpsys_notifs: list[dict] = []
    if r.returncode == 0 and r.stdout:
        dumpsys_notifs = _parse_dumpsys_notifications(r.stdout)
        print(GREEN(f"  OK  {len(dumpsys_notifs)} live notification(s) via dumpsys"))
    else:
        print(YELLOW("  !  dumpsys notification failed"))

    # Method 2: APK history API (requires Notification Access grant in Settings)
    apk_notifs: list[dict] = []
    r2 = _adb(serial, "shell", "settings", "get", "secure", "enabled_notification_listeners")
    listener_granted = _APK_PKG in (r2.stdout or "")
    if listener_granted:
        print(DIM("     [2] Notification Access granted -- pulling history via APK..."))
        _adb(serial, "shell", "am", "start", "-n", _APK_ACTIVITY, "--es", "action", "dump_notifications")
        time.sleep(6)
        tmp = out_dir / "_notifications_apk_tmp.json"
        r3 = _adb(serial, "pull", "/sdcard/Download/notifications.json", str(tmp))
        if r3.returncode == 0 and tmp.exists():
            try:
                data = json.loads(tmp.read_text(encoding="utf-8"))
                if isinstance(data, list) and data:
                    apk_notifs = data
                    print(GREEN(f"  OK  {len(apk_notifs)} historical notification(s) via APK"))
            except Exception:
                pass
    else:
        print(YELLOW("  !  Notification Access NOT granted -- APK history skipped."))
        print(YELLOW("     To get full history: Settings -> Apps -> Special app access"))
        print(YELLOW("     -> Notification access -> eRakshak Collector -> ON"))

    # Merge, de-duplicate by (package, post_time, title)
    seen: set[tuple] = set()
    merged: list[dict] = []
    for n in dumpsys_notifs + apk_notifs:
        key = (n.get("package",""), n.get("post_time",0), n.get("title",""))
        if key not in seen:
            seen.add(key)
            merged.append(n)

    local = out_dir / "notifications.json"
    local.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")

    if merged:
        print(GREEN(f"  OK  {len(merged)} total notification(s) saved -> {local}"))
    else:
        print(YELLOW("  !  No notifications found on device right now."))
    return merged


def step_pull_audio(serial: str, recordings: list[dict], out_dir: Path) -> int:
    audio_dir = out_dir / "audio_recordings"
    audio_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n{BOLD(f'[ 6/6 ]  Pulling {len(recordings)} audio file(s)...')}")

    pulled = failed = 0
    for rec in recordings:
        path = rec.get("path", "")
        if not path:
            continue
        fname = Path(path).name
        local = audio_dir / fname
        r = _adb(serial, "pull", path, str(local), timeout=120)
        if r.returncode == 0 and local.exists():
            pulled += 1
            print(GREEN(f"  OK  {fname}  ({_fmt_size(local.stat().st_size)})"))
        else:
            failed += 1
            print(YELLOW(f"  !  Failed: {fname}"))

    if pulled:
        print(GREEN(f"  OK  {pulled}/{len(recordings)} files pulled  ->  {audio_dir}"))
    else:
        print(YELLOW("  !  No audio files pulled"))
    return pulled

# ---- Report printing ---------------------------------------------------------

def print_recordings_report(recordings: list[dict]) -> None:
    print(f"\n{BOLD(_sep('='))}")
    print(f"{BOLD('  CALL RECORDINGS')}  --  {CYAN(str(len(recordings)))} file(s)")
    print(BOLD(_sep('=')))

    if not recordings:
        print(f"  {YELLOW('No call recordings found.')}")
        print(DIM("  Enable automatic call recording in Phone app settings and retry."))
        print(BOLD(_sep('-')))
        return

    header = f"  {'DATE/TIME':<22} {'CONTACT/NUMBER':<22} {'DURATION':<10} {'SIZE':<10} FORMAT"
    print(header)
    print(f"  {_sep('-', 74)}")

    for r in sorted(recordings, key=lambda x: x.get("date_ms") or 0, reverse=True)[:50]:
        ts   = _fmt_ts(r.get("date_ms"))
        hint = (r.get("contact_hint") or r.get("title") or "--")[:22]
        dur  = _fmt_dur(r.get("duration_ms"))
        sz   = _fmt_size(r.get("size_bytes", 0))
        ext  = (r.get("extension") or "?").upper()
        print(f"  {ts:<22} {hint:<22} {dur:<10} {sz:<10} {ext}")

    if len(recordings) > 50:
        print(DIM(f"  ... +{len(recordings)-50} more in recordings.json"))
    print(BOLD(_sep('-')))


def print_notifications_report(notifications: list[dict]) -> None:
    print(f"\n{BOLD(_sep('='))}")
    print(f"{BOLD('  NOTIFICATION HISTORY')}  --  {CYAN(str(len(notifications)))} record(s)")
    print(BOLD(_sep('=')))

    if not notifications:
        print(f"  {YELLOW('No notification history found.')}")
        print(DIM("  Grant Notification Access to eRakshak Collector and retry."))
        print(BOLD(_sep('-')))
        return

    # Per-app breakdown
    by_app: dict[str, int] = {}
    for n in notifications:
        app = n.get("app_label") or n.get("package") or "Unknown"
        by_app[app] = by_app.get(app, 0) + 1

    print(f"  {BOLD('App breakdown:')}")
    for app, cnt in sorted(by_app.items(), key=lambda x: -x[1])[:15]:
        bar = "#" * min(cnt, 35)
        print(f"    {app:<28} {str(cnt):>4}  {DIM(bar)}")
    if len(by_app) > 15:
        print(DIM(f"    ... +{len(by_app)-15} more apps"))

    print(f"\n  {BOLD('Recent notifications (newest first):')}")
    print(f"  {'DATE/TIME':<22} {'APP':<18} {'TITLE':<22} BODY")
    print(f"  {_sep('-', 74)}")

    for n in sorted(notifications, key=lambda x: x.get("post_time") or 0, reverse=True)[:50]:
        ts    = _fmt_ts(n.get("post_time"))
        app   = (n.get("app_label") or n.get("package") or "--")[:18]
        title = (n.get("title") or "--")[:22]
        body  = (n.get("text") or n.get("big_text") or "--")[:50]
        safe_print(f"  {ts:<22} {app:<18} {title:<22} {body}")

    if len(notifications) > 50:
        print(DIM(f"  ... +{len(notifications)-50} more in notifications.json"))
    print(BOLD(_sep('-')))

# ---- Main --------------------------------------------------------------------

def main() -> None:
    global _ADB

    parser = argparse.ArgumentParser(
        description="eRakshak -- Call Recordings & Notification History Retrieval",
    )
    parser.add_argument("--serial",              help="Target specific device serial")
    parser.add_argument("--out",                 help="Output directory (default: Desktop/Android_Forensic)")
    parser.add_argument("--pull-audio",          action="store_true", help="Also pull audio files from device")
    parser.add_argument("--recordings-only",     action="store_true", help="Skip notifications")
    parser.add_argument("--notifications-only",  action="store_true", help="Skip call recordings")
    parser.add_argument("--skip-install",        action="store_true", help="Skip APK install")
    args = parser.parse_args()

    _ADB    = _find_adb()
    out_dir = Path(args.out) if args.out else (Path.home() / "Desktop" / "Android_Forensic")
    out_dir.mkdir(parents=True, exist_ok=True)

    print()
    print(BOLD("=" * 78))
    print(BOLD("  eRakshak  Call Recordings & Notification History Retrieval Session"))
    print(BOLD("=" * 78))
    print(DIM(f"  ADB:        {_ADB}"))
    print(DIM(f"  Output:     {out_dir}"))
    print(DIM(f"  Started:    {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"))
    print(BOLD("=" * 78))

    serial = step_check_device(args.serial)

    if not args.skip_install:
        step_install_apk(serial)
    else:
        print(f"\n{BOLD('[ 2/6 ]  APK install skipped')}")

    step_grant_permissions(serial)

    recordings: list[dict] = []
    if not args.notifications_only:
        recordings = step_dump_recordings(serial, out_dir)
    else:
        print(f"\n{BOLD('[ 4/6 ]  Recordings skipped (--notifications-only)')}")

    notifications: list[dict] = []
    if not args.recordings_only:
        notifications = step_dump_notifications(serial, out_dir)
    else:
        print(f"\n{BOLD('[ 5/6 ]  Notifications skipped (--recordings-only)')}")

    if args.pull_audio and recordings:
        step_pull_audio(serial, recordings, out_dir)
    else:
        print(f"\n{BOLD('[ 6/6 ]')}  {DIM('Audio pull skipped (add --pull-audio to download files)')}")

    # Summary
    print(f"\n\n{BOLD('=' * 78)}")
    print(BOLD("  SESSION RESULTS"))
    print(BOLD("=" * 78))
    print_recordings_report(recordings)
    print_notifications_report(notifications)

    print(f"\n{BOLD('Output files saved:')}")
    for fname in ["recordings.json", "notifications.json"]:
        p = out_dir / fname
        if p.exists():
            print(GREEN(f"  OK  {p}  ({_fmt_size(p.stat().st_size)})"))
    if args.pull_audio:
        ad = out_dir / "audio_recordings"
        if ad.exists():
            files = [f for f in ad.iterdir() if f.is_file()]
            total = sum(f.stat().st_size for f in files)
            print(GREEN(f"  OK  {ad}/  ({len(files)} files, {_fmt_size(total)})"))

    print(f"\n{BOLD('=' * 78)}")
    print(GREEN(BOLD("  Session complete!")))
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
