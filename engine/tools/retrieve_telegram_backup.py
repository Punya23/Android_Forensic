#!/usr/bin/env python3
"""
retrieve_telegram_backup.py
================================================================================
SNAGR -- Telegram Backup Data Recovery Tool
================================================================================

WHAT THIS TOOL COVERS
─────────────────────
Telegram chats can be recovered from backup data via FOUR possible methods:

  METHOD 1 ▸ Telegram In-App Export  (BEST — works without root)
    • Settings → Privacy and Security → Export Telegram Data
    • Exports: all chats, messages, media as JSON + HTML
    • This tool PARSES those exported files and generates a viewer

  METHOD 2 ▸ ADB Backup + android-backup-extractor  (BLOCKED on Android 12+)
    • `adb backup org.telegram.messenger` produces a .ab file
    • On Android 12+/Telegram production build: only a 47-byte header is created
    • Telegram explicitly sets allowBackup="false" — no data is captured

  METHOD 3 ▸ Google Drive Backup  (NOT extractable via ADB)
    • Telegram IS registered in Google Backup (84 state bytes confirmed)
    • BUT Google Drive backups are encrypted with the device's Google account key
    • Cannot be decrypted without Google's servers — forensically inaccessible

  METHOD 4 ▸ Root + cache4.db Decryption  (needs root — not available)
    • /data/data/org.telegram.messenger/files/cache4.db
    • Encrypted with a key stored in tgnet.dat
    • Requires root AND a tool like telegram-database-reader

THIS TOOL IMPLEMENTS:
  ✓ Method 1 — Parse and display Telegram JSON export (result.json)
  ✓ Method 2 — Try ADB backup, detect if it's empty, explain why
  ✓ Generates a dark HTML viewer for exported chat data

USAGE
─────
  # Parse an exported Telegram JSON (from in-app export):
  python tools/retrieve_telegram_backup.py --export path/to/result.json

  # Try ADB backup (will likely fail on Android 12+ Telegram):
  python tools/retrieve_telegram_backup.py --adb-backup

  # Both:
  python tools/retrieve_telegram_backup.py --adb-backup --export result.json

  # With device serial:
  python tools/retrieve_telegram_backup.py --serial DEVICE_SERIAL --adb-backup
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import struct
import subprocess
import sys
import shutil
import time
import zlib
from pathlib import Path

# ---- ANSI colours ------------------------------------------------------------
_IS_TTY = sys.stdout.isatty()
def _c(code: str, t: str) -> str:
    return f"\033[{code}m{t}\033[0m" if _IS_TTY else t

RED    = lambda t: _c("31;1", t)
GREEN  = lambda t: _c("32;1", t)
YELLOW = lambda t: _c("33;1", t)
CYAN   = lambda t: _c("36;1", t)
BOLD   = lambda t: _c("1",    t)
DIM    = lambda t: _c("2",    t)

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

def _sep(ch="-", w=78): return ch * w
def _fmt_size(b: int) -> str:
    if b >= 1_048_576: return f"{b/1_048_576:.1f} MB"
    if b >= 1024:      return f"{b/1024:.0f} KB"
    return f"{b} B"

# ---- ADB helpers -------------------------------------------------------------
def _find_adb() -> str:
    a = shutil.which("adb")
    if a: return a
    lad = os.environ.get("LOCALAPPDATA", "")
    for c in [
        Path(lad) / "Android" / "Sdk" / "platform-tools" / "adb.exe",
        Path.home() / "AppData" / "Local" / "Android" / "Sdk" / "platform-tools" / "adb.exe",
    ]:
        if c.exists(): return str(c)
    return "adb"

def _adb(serial, *args, timeout=60):
    cmd = [_ADB] + (["-s", serial] if serial else []) + list(args)
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)

def _get_device(serial=None) -> str | None:
    r = subprocess.run([_ADB, "devices", "-l"], capture_output=True, text=True, timeout=10)
    for line in r.stdout.splitlines()[1:]:
        line = line.strip()
        if not line or "offline" in line: continue
        parts = line.split()
        if len(parts) < 2 or parts[1] != "device": continue
        if serial is None or parts[0] == serial:
            return parts[0]
    return None

# ═══════════════════════════════════════════════════════════════════════════════
# METHOD 1 ── PARSE TELEGRAM JSON EXPORT
# ═══════════════════════════════════════════════════════════════════════════════

def _ts(epoch_or_str) -> str:
    """Convert timestamp to readable string."""
    try:
        if isinstance(epoch_or_str, int):
            return datetime.datetime.fromtimestamp(epoch_or_str).strftime("%Y-%m-%d %H:%M:%S")
        return str(epoch_or_str)[:19]
    except Exception:
        return str(epoch_or_str)

def parse_telegram_export(export_path: Path) -> dict:
    """
    Parse Telegram's result.json export file.
    Returns structured summary with chats, messages, contacts.
    """
    print(f"\n{BOLD('Parsing Telegram export...')}")
    raw = json.loads(export_path.read_text(encoding="utf-8", errors="replace"))

    summary = {
        "export_source":  str(export_path),
        "parsed_at":      datetime.datetime.now().isoformat(),
        "account":        raw.get("personal_information", {}),
        "chats":          [],
        "total_messages": 0,
        "contacts":       raw.get("contacts", {}).get("list", []),
        "frequent_contacts": raw.get("frequent_contacts", {}).get("list", []),
    }

    chats_raw = raw.get("chats", {}).get("list", [])
    for chat in chats_raw:
        chat_name  = chat.get("name") or chat.get("id") or "Unknown"
        chat_type  = chat.get("type", "unknown")
        messages   = chat.get("messages", [])

        parsed_msgs = []
        for m in messages:
            if m.get("type") != "message":
                continue
            content = m.get("text", "")
            if isinstance(content, list):
                # Telegram uses list of {type,text} for formatted text
                content = "".join(
                    p.get("text","") if isinstance(p, dict) else str(p)
                    for p in content
                )
            parsed_msgs.append({
                "id":        m.get("id"),
                "date":      m.get("date"),
                "from":      m.get("from") or m.get("actor"),
                "from_id":   m.get("from_id") or m.get("actor_id"),
                "text":      str(content)[:1000],
                "media":     m.get("media_type") or ("photo" if m.get("photo") else None),
                "file":      m.get("file"),
                "reply_to":  m.get("reply_to_message_id"),
                "forwarded": m.get("forwarded_from"),
                "edited":    m.get("edited"),
            })

        chat_entry = {
            "name":          chat_name,
            "type":          chat_type,
            "id":            chat.get("id"),
            "message_count": len(parsed_msgs),
            "messages":      parsed_msgs,
            "first_msg":     parsed_msgs[0]["date"]  if parsed_msgs else None,
            "last_msg":      parsed_msgs[-1]["date"] if parsed_msgs else None,
        }
        summary["chats"].append(chat_entry)
        summary["total_messages"] += len(parsed_msgs)

    summary["chat_count"] = len(summary["chats"])
    print(GREEN(f"  OK  {summary['chat_count']} chat(s), {summary['total_messages']} message(s) parsed"))
    return summary


def print_export_summary(summary: dict) -> None:
    print(f"\n{BOLD(_sep('='))}")
    print(BOLD("  TELEGRAM EXPORT SUMMARY"))
    print(BOLD(_sep('=')))

    acc = summary.get("account", {})
    if acc:
        name = acc.get("first_name","") + " " + acc.get("last_name","")
        safe_print(f"  Account:  {name.strip()} (@{acc.get('username','')})")
        safe_print(f"  Phone:    {acc.get('phone_number','—')}")

    print(f"\n  {BOLD('Chats:')}")
    print(f"  {'TYPE':<14} {'MESSAGES':>9}  {'FIRST MSG':<22}  {'LAST MSG':<22}  NAME")
    print(f"  {_sep('-', 90)}")
    for c in sorted(summary["chats"], key=lambda x: x["message_count"], reverse=True)[:30]:
        first = _ts(c["first_msg"]) if c["first_msg"] else "—"
        last  = _ts(c["last_msg"])  if c["last_msg"]  else "—"
        safe_print(f"  {c['type']:<14} {c['message_count']:>9}  {first:<22}  {last:<22}  {c['name']}")

    print(f"\n  Total: {summary['chat_count']} chats, {summary['total_messages']} messages")
    contacts = summary.get("contacts", [])
    if contacts:
        print(f"  Contacts: {len(contacts)}")

    print(BOLD(_sep('=')))


# ═══════════════════════════════════════════════════════════════════════════════
# METHOD 2 ── ADB BACKUP (attempt + explain)
# ═══════════════════════════════════════════════════════════════════════════════

def try_adb_backup(serial: str, out_dir: Path) -> dict:
    print(f"\n{BOLD('[ ADB Backup ]  Attempting adb backup of Telegram...')}")
    print(DIM("     Note: Telegram sets allowBackup=false; blocked on Android 12+"))
    print(DIM("     You may see a confirmation dialog on device -- tap 'Back up my data'"))

    ab_path = out_dir / "telegram_backup.ab"
    cmd = [_ADB, "-s", serial, "backup", "-noapk", "-f", str(ab_path), "org.telegram.messenger"]
    print(DIM(f"     Running: {' '.join(cmd)}"))

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=30)
    except subprocess.TimeoutExpired:
        print(YELLOW("  !  Timeout (30s) -- user did not confirm on device"))
        return {"status": "timeout"}

    size = ab_path.stat().st_size if ab_path.exists() else 0

    if not ab_path.exists() or size < 100:
        print(YELLOW(f"  !  Backup file is only {size} bytes -- Telegram data was BLOCKED"))
        print(YELLOW("     Telegram v12+ has allowBackup=false (app restriction, not a bug)"))
        print(DIM("     → Use METHOD 1: In-app export (Settings → Privacy → Export Telegram Data)"))
        return {"status": "blocked", "file_size": size}

    print(GREEN(f"  OK  Backup file: {ab_path}  ({_fmt_size(size)})"))
    print(DIM("     Attempting to extract..."))

    # Try to parse the .ab file (Android Backup format)
    extracted = _extract_ab_file(ab_path, out_dir / "telegram_ab_extracted")
    return {"status": "success", "file": str(ab_path), "size": size, "extracted": extracted}


def _extract_ab_file(ab_path: Path, out_dir: Path) -> list[str]:
    """
    Android Backup (.ab) format:
      Line 1: ANDROID BACKUP
      Line 2: version (e.g. 5)
      Line 3: compressed (0/1)
      Line 4: encryption (none / AES-256)
      Rest:   zlib-compressed tar stream (if not encrypted)
    """
    data = ab_path.read_bytes()
    lines = data.split(b"\n", 4)
    if len(lines) < 5 or lines[0] != b"ANDROID BACKUP":
        print(YELLOW("  !  Not a valid Android Backup file"))
        return []

    compressed  = lines[2].strip() == b"1"
    encryption  = lines[3].strip().decode()
    payload     = lines[4]

    if encryption != "none":
        print(YELLOW(f"  !  Backup is encrypted ({encryption}) -- cannot extract"))
        return []

    if compressed:
        try:
            payload = zlib.decompress(payload)
        except Exception as e:
            print(YELLOW(f"  !  Decompression failed: {e}"))
            return []

    # payload is now a .tar stream -- write it and extract with Python tarfile
    import tarfile, io
    out_dir.mkdir(parents=True, exist_ok=True)
    extracted_files = []
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as tf:
            for member in tf.getmembers():
                try:
                    tf.extract(member, out_dir)
                    extracted_files.append(member.name)
                except Exception:
                    pass
        print(GREEN(f"  OK  Extracted {len(extracted_files)} file(s) -> {out_dir}"))
    except Exception as e:
        print(YELLOW(f"  !  Tar extraction error: {e}"))

    return extracted_files


# ═══════════════════════════════════════════════════════════════════════════════
# HTML CHAT VIEWER
# ═══════════════════════════════════════════════════════════════════════════════

def generate_chat_viewer(summary: dict, out_dir: Path) -> Path:
    """Generate a dark HTML chat viewer from parsed Telegram export."""
    print(f"\n{BOLD('Generating HTML chat viewer...')}")

    account   = summary.get("account", {})
    acc_name  = (account.get("first_name","") + " " + account.get("last_name","")).strip()
    acc_phone = account.get("phone_number","—")
    acc_user  = account.get("username","")
    chats     = summary.get("chats", [])
    parsed_at = summary.get("parsed_at","")[:19].replace("T"," ")

    # Build sidebar chat list
    sidebar_items = ""
    for i, c in enumerate(chats):
        msg_count = c["message_count"]
        last = _ts(c["last_msg"]) if c["last_msg"] else "—"
        preview = ""
        if c["messages"]:
            last_m = c["messages"][-1]
            preview = (last_m.get("text","") or "")[:60].replace("<","&lt;").replace(">","&gt;")
        type_icon = {"personal_chat":"👤","bot_chat":"🤖","private_group":"👥",
                     "public_group":"🌐","private_supergroup":"👥","public_channel":"📢",
                     "saved_messages":"📌"}.get(c["type"],"💬")
        sidebar_items += f"""
    <div class="chat-item" onclick="showChat({i})" id="si-{i}">
      <div class="chat-icon">{type_icon}</div>
      <div class="chat-meta">
        <div class="chat-name">{c['name'][:30]}</div>
        <div class="chat-preview">{preview or f'{msg_count} messages'}</div>
      </div>
      <div class="chat-badge">{msg_count}</div>
    </div>"""

    # Build message panes (one per chat, hidden by default)
    chat_panes = ""
    for i, c in enumerate(chats):
        msgs_html = ""
        for m in c["messages"]:
            sender  = (m.get("from") or "Unknown").replace("<","&lt;")
            text    = (m.get("text") or "").replace("<","&lt;").replace(">","&gt;").replace("\n","<br>")
            ts      = _ts(m.get("date",""))
            media   = m.get("media","")
            fwd     = m.get("forwarded","")
            replied = m.get("reply_to","")
            media_tag = f'<div class="msg-media">📎 {media}</div>' if media else ""
            fwd_tag   = f'<div class="msg-fwd">↪ Forwarded from {fwd}</div>' if fwd else ""
            rep_tag   = f'<div class="msg-reply">↩ Reply to #{replied}</div>' if replied else ""
            msgs_html += f"""
      <div class="msg">
        <div class="msg-header">
          <span class="msg-sender">{sender}</span>
          <span class="msg-time">{ts}</span>
        </div>
        {fwd_tag}{rep_tag}
        <div class="msg-body">{text or media_tag}</div>
        {media_tag if text and media else ""}
      </div>"""

        first = _ts(c["first_msg"]) if c["first_msg"] else "—"
        last  = _ts(c["last_msg"])  if c["last_msg"]  else "—"
        chat_panes += f"""
  <div class="chat-pane" id="cp-{i}" style="display:none">
    <div class="pane-header">
      <div class="pane-title">{c['name']}</div>
      <div class="pane-sub">{c['type']} &nbsp;·&nbsp; {c['message_count']} messages &nbsp;·&nbsp; {first} → {last}</div>
    </div>
    <div class="messages" id="msgs-{i}">{msgs_html}</div>
  </div>"""

    contacts_html = ""
    for ct in summary.get("contacts",[])[:100]:
        fn   = ct.get("first_name","")
        ln   = ct.get("last_name","")
        ph   = ct.get("phone_number","")
        un   = ct.get("username","")
        contacts_html += f'<div class="contact"><span class="cname">{fn} {ln}</span><span class="cphone">{ph}</span><span class="cuser">@{un}</span></div>'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>SNAGR — Telegram Chat Viewer</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
  *,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
  :root{{
    --bg:#0d0f14;--surface:#161a23;--surface2:#1e2330;--surface3:#252b3b;
    --border:#2a2f3e;--accent:#2ca5e0;--accent2:#1a7fb5;
    --text:#e8eaf0;--muted:#8890a6;--gold:#f5c842;
    --msg-bg:#1e2330;--msg-own:#1a3a52;
  }}
  body{{background:var(--bg);color:var(--text);font-family:'Inter',sans-serif;height:100vh;overflow:hidden;display:flex;flex-direction:column}}

  /* Top bar */
  .topbar{{background:linear-gradient(135deg,#0f172a,#1a1f35);border-bottom:1px solid var(--border);
    padding:14px 24px;display:flex;align-items:center;gap:16px;flex-shrink:0}}
  .topbar .logo{{width:36px;height:36px;border-radius:10px;background:linear-gradient(135deg,#2ca5e0,#1a7fb5);
    display:flex;align-items:center;justify-content:center;font-size:18px}}
  .topbar h1{{font-size:16px;font-weight:700}}
  .topbar .sub{{font-size:12px;color:var(--muted);margin-left:auto}}
  .topbar .acc{{font-size:12px;color:var(--accent)}}

  /* Stats strip */
  .stats{{background:var(--surface);border-bottom:1px solid var(--border);
    padding:10px 24px;display:flex;gap:28px;flex-shrink:0}}
  .stat{{display:flex;flex-direction:column}}
  .stat-num{{font-size:18px;font-weight:700;color:var(--accent)}}
  .stat-lbl{{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}}

  /* Main layout */
  .main{{display:flex;flex:1;overflow:hidden}}

  /* Sidebar */
  .sidebar{{width:300px;flex-shrink:0;background:var(--surface);border-right:1px solid var(--border);
    display:flex;flex-direction:column;overflow:hidden}}
  .sidebar-header{{padding:12px 16px;border-bottom:1px solid var(--border);font-size:12px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}}
  .sidebar-list{{overflow-y:auto;flex:1}}
  .chat-item{{display:flex;align-items:center;gap:10px;padding:12px 16px;cursor:pointer;
    border-bottom:1px solid var(--border);transition:background .15s}}
  .chat-item:hover,.chat-item.active{{background:var(--surface2)}}
  .chat-icon{{font-size:20px;flex-shrink:0;width:36px;height:36px;display:flex;align-items:center;justify-content:center;
    background:var(--surface3);border-radius:10px}}
  .chat-meta{{flex:1;min-width:0}}
  .chat-name{{font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
  .chat-preview{{font-size:11px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:2px}}
  .chat-badge{{background:var(--accent);color:#fff;border-radius:10px;font-size:10px;font-weight:700;
    padding:2px 7px;flex-shrink:0;min-width:24px;text-align:center}}

  /* Tabs */
  .tabs{{display:flex;border-bottom:1px solid var(--border);background:var(--surface)}}
  .tab{{padding:10px 20px;font-size:12px;font-weight:600;cursor:pointer;color:var(--muted);
    border-bottom:2px solid transparent;transition:color .15s,border-color .15s}}
  .tab.active{{color:var(--accent);border-color:var(--accent)}}

  /* Chat pane area */
  .pane-area{{flex:1;display:flex;flex-direction:column;overflow:hidden}}
  .welcome{{flex:1;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:12px;color:var(--muted)}}
  .welcome .icon{{font-size:48px}}

  .pane-header{{background:var(--surface2);border-bottom:1px solid var(--border);padding:14px 20px;flex-shrink:0}}
  .pane-title{{font-size:15px;font-weight:700}}
  .pane-sub{{font-size:11px;color:var(--muted);margin-top:3px}}

  .messages{{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:8px}}
  .msg{{background:var(--msg-bg);border:1px solid var(--border);border-radius:10px;padding:10px 14px;max-width:85%}}
  .msg-header{{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px}}
  .msg-sender{{font-size:12px;font-weight:700;color:var(--accent)}}
  .msg-time{{font-size:10px;color:var(--muted)}}
  .msg-body{{font-size:13px;line-height:1.5;word-break:break-word}}
  .msg-fwd{{font-size:11px;color:#f5c842;margin-bottom:4px}}
  .msg-reply{{font-size:11px;color:var(--muted);border-left:2px solid var(--accent);padding-left:8px;margin-bottom:4px}}
  .msg-media{{font-size:12px;color:var(--muted);font-style:italic;margin-top:4px}}

  /* Contacts pane */
  .contacts-pane{{flex:1;overflow-y:auto;padding:16px;display:none}}
  .contact{{display:flex;gap:12px;padding:10px 14px;background:var(--surface2);border-radius:8px;margin-bottom:6px}}
  .cname{{font-size:13px;font-weight:600;flex:1}}
  .cphone{{font-size:12px;color:var(--accent);width:130px}}
  .cuser{{font-size:11px;color:var(--muted)}}

  /* Scrollbar */
  ::-webkit-scrollbar{{width:4px}}
  ::-webkit-scrollbar-track{{background:var(--surface)}}
  ::-webkit-scrollbar-thumb{{background:var(--border);border-radius:2px}}
</style>
</head>
<body>
<div class="topbar">
  <div class="logo">✈</div>
  <h1>Telegram Chat Viewer</h1>
  <span class="acc">{acc_name} {'(@'+acc_user+')' if acc_user else ''} &nbsp;·&nbsp; {acc_phone}</span>
  <span class="sub">SNAGR &nbsp;·&nbsp; Parsed: {parsed_at}</span>
</div>
<div class="stats">
  <div class="stat"><div class="stat-num">{summary['chat_count']}</div><div class="stat-lbl">Chats</div></div>
  <div class="stat"><div class="stat-num">{summary['total_messages']:,}</div><div class="stat-lbl">Messages</div></div>
  <div class="stat"><div class="stat-num">{len(summary.get('contacts',[]))}</div><div class="stat-lbl">Contacts</div></div>
</div>
<div class="main">
  <div class="sidebar">
    <div class="tabs">
      <div class="tab active" onclick="switchTab('chats',this)">Chats</div>
      <div class="tab" onclick="switchTab('contacts',this)">Contacts</div>
    </div>
    <div class="sidebar-list" id="tab-chats">
      {sidebar_items}
    </div>
    <div class="sidebar-list" id="tab-contacts" style="display:none">
      <div style="padding:12px">{contacts_html or '<div style="color:var(--muted);font-size:12px">No contacts exported</div>'}</div>
    </div>
  </div>

  <div class="pane-area" id="pane-area">
    <div class="welcome" id="welcome">
      <div class="icon">💬</div>
      <div>Select a chat to view messages</div>
    </div>
    {chat_panes}
  </div>
</div>

<script>
  let activeChatIdx = null;
  function showChat(i) {{
    document.getElementById('welcome').style.display='none';
    document.querySelectorAll('.chat-pane').forEach(p=>p.style.display='none');
    document.querySelectorAll('.chat-item').forEach(c=>c.classList.remove('active'));
    document.getElementById('cp-'+i).style.display='flex';
    document.getElementById('cp-'+i).style.flexDirection='column';
    document.getElementById('si-'+i).classList.add('active');
    // scroll to bottom
    const msgs = document.getElementById('msgs-'+i);
    if(msgs) msgs.scrollTop = msgs.scrollHeight;
    activeChatIdx = i;
  }}
  function switchTab(tab, el) {{
    document.getElementById('tab-chats').style.display    = tab==='chats'    ? 'block' : 'none';
    document.getElementById('tab-contacts').style.display = tab==='contacts' ? 'block' : 'none';
    document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
    el.classList.add('active');
  }}
  // Open first chat by default if any
  if(document.querySelector('.chat-item')) showChat(0);
</script>
</body>
</html>"""

    viewer_path = out_dir / "telegram_chat_viewer.html"
    viewer_path.write_text(html, encoding="utf-8")
    print(GREEN(f"  OK  Chat viewer -> {viewer_path}  ({_fmt_size(viewer_path.stat().st_size)})"))
    return viewer_path


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    global _ADB
    parser = argparse.ArgumentParser(
        description="SNAGR -- Telegram Backup Data Recovery Tool"
    )
    parser.add_argument("--export",     help="Path to Telegram result.json export file")
    parser.add_argument("--adb-backup", action="store_true", help="Attempt ADB backup of Telegram")
    parser.add_argument("--serial",     help="ADB device serial")
    parser.add_argument("--out",        help="Output directory",
                        default=str(Path.home() / "Desktop" / "Android_Forensic"))
    args = parser.parse_args()

    _ADB    = _find_adb()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print()
    print(BOLD("=" * 78))
    print(BOLD("  SNAGR  Telegram Backup Recovery Tool"))
    print(BOLD("=" * 78))
    print(DIM(f"  ADB:    {_ADB}"))
    print(DIM(f"  Output: {out_dir}"))
    print(BOLD("=" * 78))

    did_something = False

    # ── ADB Backup attempt ────────────────────────────────────────────────────
    if args.adb_backup:
        did_something = True
        serial = _get_device(args.serial)
        if not serial:
            print(RED("  x  No device connected -- connect phone via USB"))
        else:
            print(GREEN(f"  OK  Device: {serial}"))
            result = try_adb_backup(serial, out_dir)
            print(DIM(f"     ADB backup result: {result}"))

    # ── Parse in-app export ───────────────────────────────────────────────────
    if args.export:
        did_something = True
        export_path = Path(args.export)
        if not export_path.exists():
            print(RED(f"  x  Export file not found: {export_path}"))
            sys.exit(1)
        summary = parse_telegram_export(export_path)

        # Save parsed JSON
        parsed_json = out_dir / "telegram_parsed_export.json"
        parsed_json.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8"
        )
        print(GREEN(f"  OK  Parsed export -> {parsed_json}"))

        print_export_summary(summary)
        viewer = generate_chat_viewer(summary, out_dir)
        print(f"\n  {BOLD('Open in browser:')} {viewer}")

    if not did_something:
        print(YELLOW("\n  No action specified. Usage:"))
        print(DIM("    # Parse Telegram in-app export (recommended):"))
        print(DIM("    python retrieve_telegram_backup.py --export result.json"))
        print()
        print(DIM("    # Try ADB backup (usually blocked on Android 12+):"))
        print(DIM("    python retrieve_telegram_backup.py --adb-backup"))
        print()
        print(BOLD("  HOW TO GET result.json (Telegram in-app export):"))
        print(f"  {'-'*60}")
        print("  1. Open Telegram on your phone")
        print("  2. Settings -> Privacy and Security -> Export Telegram Data")
        print("  3. Select: Personal chats [x]  Media [x]  Contacts [x]")
        print("  4. Format: Machine-readable JSON")
        print("  5. Tap Export -> share result.zip to PC")
        print("  6. Unzip -> run: python retrieve_telegram_backup.py --export result.json")
        print()
        print(BOLD("  WHY ADB BACKUP DOESN'T WORK:"))
        print("  Telegram has allowBackup=false in its manifest (verified on v12.9.1)")
        print("  Android 12+ enforces this -- only 47 bytes are written (header only)")
        print("  Google Drive Backup IS registered but is encrypted -- not extractable")

    print(f"\n{BOLD('=' * 78)}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{YELLOW('Interrupted.')}")
        sys.exit(0)
    except Exception as exc:
        print(RED(f"\nError: {exc}"))
        import traceback; traceback.print_exc()
        sys.exit(3)
