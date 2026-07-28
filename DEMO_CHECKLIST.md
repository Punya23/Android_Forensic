# eRakshak — Demo Day Checklist & Fallback Recording Script
## ERH26_PS_02 · Android Forensic Triage Tool

> Print this doc and keep it beside the demo station.

---

## T-2 Days (Record the Fallback Video)

### What to Record
A complete run of the golden demo path on the **actual demo device** with pre-seeded data.
The recording serves as a polished fallback if live ADB fails on stage.

### Setup
- [ ] Use **OBS Studio** or built-in screen recorder (Win+G / QuickTime).
- [ ] Set resolution: **1920×1080**, frame rate: **30 fps**.
- [ ] Select the eRakshak window only (not full desktop — hides unrelated tabs).
- [ ] Plug in demo phone with USB Debugging **already enabled**.
- [ ] Confirm `adb devices` shows the device before recording starts.
- [ ] Disable all system notifications (Do Not Disturb mode).

### Golden Path Script (record in this order)

**Segment 1 — Device Connect (0:00–0:30)**
1. Open eRakshak dashboard.
2. Navigate to **Acquisition** tab.
3. Show device info panel auto-populating (model, serial, Android version).
4. Speak aloud: *"eRakshak detects the connected device over ADB and records its identity
   in the chain-of-custody log before a single byte is collected."*

**Segment 2 — Case Setup & Plan (0:30–1:30)**
5. Enter examiner name, case number, and a 1-sentence case description.
6. Click **Preview Plan** — show the AI triage plan panel populate.
7. Enable Tier-1 toggles (Contacts, Call Log, SMS).
8. Speak: *"The AI planner recommends a targeted artifact list based on the crime type.
   Every decision is logged. The examiner retains full control."*

**Segment 3 — Acquisition (1:30–3:30)**
9. Click **Start Acquisition**.
10. Show the 14-stage progress bar advancing.
11. Highlight: DCIM pull → WhatsApp media → Telegram → Wi-Fi → Contacts → Calls.
12. Show the audit log entry in real time.
13. Speak: *"Every command sent to the device is recorded in the append-only audit log
   with a timestamp. This forms our chain of custody."*

**Segment 4 — Evidence Review (3:30–5:30)**
14. Navigate to **Messages** — show WhatsApp conversations with confidence badges.
15. Navigate to **Locations** — show map with geotagged photo pins clustered.
16. Navigate to **Recovered** — show at least one carved/recovered deleted row
    with its confidence badge (RECOVERED_VERIFIED or CARVED_PARTIAL).
17. Navigate to **Timeline** — show date-grouped cross-artifact event feed.
18. Navigate to **Wi-Fi** — show 3+ saved networks with BSSID and timestamps.
19. Navigate to **Telegram** — show conversations with media thumbnails.
20. Speak: *"Recovered items carry explicit confidence labels — LIVE, RECOVERED,
   CARVED, or DELETION_DETECTED. We never claim more than we can prove."*

**Segment 5 — Chain of Custody & Report (5:30–7:00)**
21. Navigate to **Chain of Custody** — show device intake, pre-state, audit entries.
22. Navigate to **Report** tab.
23. Click **Generate Report**.
24. Show the HTML report loading in the iframe.
25. Scroll to the **Section 65B certificate** block.
26. Speak: *"The report includes a legally-formatted Section 65B certificate under
   the Indian Evidence Act. It uses 'minimally-invasive, fully-logged acquisition'
   language aligned with the Supreme Court's guidance in Arjun Panditrao Khotkar."*
27. Click **Download PDF** to demonstrate PDF export.

**Segment 6 — Bonus: Case Intel (7:00–8:00)** *(if time allows)*
28. Navigate to **Case Intelligence**.
29. Show the crime ontology → artifact priority output.

### Post-Recording
- [ ] Export recording as `erakshak_demo_golden_path.mp4` (H.264, max 500 MB).
- [ ] Upload to shared drive and commit path to the README.
- [ ] Do a dry run of switching to the video mid-presentation (practice the hand-off).

---

## T-1 Day Checklist (Hardware & Software Validation)

### Demo Device
- [ ] Phone is fully charged (≥ 90 %).
- [ ] USB Debugging is enabled and **always allowed** for the demo laptop.
- [ ] `adb devices` on demo laptop shows device as `device` (not `unauthorized`).
- [ ] Pre-seeded data installed:
  - [ ] WhatsApp conversations (at least 3 chats, including a deleted message scenario)
  - [ ] Telegram group with media
  - [ ] 10+ geotagged photos in DCIM
  - [ ] At least 2 saved Wi-Fi networks
  - [ ] Call log entries

### Software
- [ ] Python engine starts cleanly: `cd engine && python -m triage.server`
  - [ ] `GET http://127.0.0.1:5057/api/health` returns `{"adb": true}`
- [ ] `npm run dev` (or `electron:dev`) starts the dashboard.
- [ ] Full golden path runs end-to-end on demo device — time it: target < 5 minutes.
- [ ] Report generates successfully (no HTTP 404 on the report iframe).
- [ ] PDF export opens correctly.

### Backup Plan
- [ ] Fallback video is ready on a **USB stick** (not just cloud storage).
- [ ] USB stick also contains a pre-generated case folder (`_demo_case/`) that
      can be dragged into `_test_output/` to demo from static data.
- [ ] Demo laptop has offline map tiles cached if the venue has poor internet.

---

## T-0 (Demo Day)

### 30 Minutes Before
- [ ] Plug in demo phone — confirm `adb devices`.
- [ ] Start Python engine (or run `electron:dev`).
- [ ] Open dashboard to **Acquisition** tab, device panel shows correct model.
- [ ] Set display to 1920×1080 (or match projector resolution).
- [ ] Close all unrelated windows and browser tabs.
- [ ] Silence all notifications.
- [ ] Put a sticky note on the laptop: **"USB cable is ADB, not charging!"**

### If ADB Fails on Stage
1. **Stay calm** — say: *"The device is not connecting over USB; I'll switch to our
   pre-recorded demonstration."*
2. Press **Win+Tab** → open the fallback MP4 in VLC (pre-opened, paused at 0:00).
3. OR load the static demo case:
   ```
   # In a terminal:
   copy _demo_case _test_output\DEMO-STATIC
   # Then in the dashboard select DEMO-STATIC from the case list
   ```
4. Continue narrating as if it were live — the judges care about your explanation.

### After the Demo
- [ ] Delete any real personal data from the demo device (it was seeded with synthetic data,
      but confirm nothing real was pulled).
- [ ] Export the demo case folder as evidence for your own records.

---

## APK Build (Collector Helper — do this T-2 or earlier)

```powershell
# From the project root on Windows:
cd apk
.\gradlew assembleDebug

# Output:
# apk\app\build\outputs\apk\debug\app-debug.apk

# Test install on demo device:
adb install app\app\build\outputs\apk\debug\app-debug.apk

# Grant permissions:
adb shell pm grant com.erakshak.collector android.permission.READ_CONTACTS
adb shell pm grant com.erakshak.collector android.permission.READ_CALL_LOG
adb shell pm grant com.erakshak.collector android.permission.READ_SMS
adb shell pm grant com.erakshak.collector android.permission.READ_MEDIA_IMAGES
adb shell pm grant com.erakshak.collector android.permission.READ_MEDIA_VIDEO

# Verify it runs without crash:
adb shell am start -n com.erakshak.collector/.MainActivity
```

If the APK fails to install, document the error and skip Tier-1 in the demo
(Tier-0 WhatsApp media + Telegram is still demo-worthy without it).

---

## PyInstaller Build (Portable Packaging — T-2)

```powershell
# Step 1: Install build dependencies
pip install pyinstaller

# Step 2: Build the engine bundle
cd engine
pyinstaller erakshak.spec --noconfirm --clean

# Step 3: (Optional) Run the packaging script for a full distributable
cd ..
python build_package.py --version 0.1.0 --platform win32

# Output: dist/eRakshak-0.1.0/
#   engine/triage-engine.exe
#   adb/adb.exe
#   app/  (Vite dist)
#   run.bat
```

---

## Key Legal Language to Recite on Stage

| What to say | What NOT to say |
|---|---|
| "Minimally-invasive, fully-logged acquisition" | ~~"Read-only acquisition"~~ |
| "We log every command sent to the device" | ~~"We don't touch the device"~~ |
| "Artifacts have per-file SHA-256 hashes recorded at extraction" | ~~"The device is bit-for-bit imaged"~~ |
| "Confidence labels tell you exactly how certain each finding is" | ~~"We recovered all deleted messages"~~ |
| "Section 65B certificate is generated automatically from the audit log" | ~~"The report is court-admissible on its own"~~ |

---

*Last updated: 2026-07-28 | Owner: Vaishnavi (eRakshak team)*
