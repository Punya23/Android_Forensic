# SNAGR on dedicated hardware (Raspberry Pi 5)

Two switchable modes, one image. `deploy/set-network-mode.sh` flips between
them. Neither mode ever talks to anything outside the LAN — there is no
cloud/relay component anywhere in this tool (the AI evidence summary already
runs on a local Ollama model, see `engine/triage/intel/`).

- **airgapped** (default) — the Pi's own attached screen is the GUI. Engine
  binds `127.0.0.1` only. No radio needs to be on at all during acquisition.
- **lan** — no screen on the Pi. The engine serves the dashboard itself over
  HTTP; the investigator's laptop opens `http://<pi-ip>:5057` in a browser.
  Still nothing beyond the LAN the laptop is actually on — the engine binds
  one explicit interface, not `0.0.0.0`, and a firewall ruleset only opens
  that one port to that one subnet.

Reports leave the Pi three ways regardless of mode: a USB drive copy, the
`deploy/send-report-email.sh` one-shot outbound send, or (lan mode only) the
laptop just viewing/printing the report in its browser.

## 1. Base image

- Raspberry Pi OS Lite, **64-bit**, flashed via Raspberry Pi Imager.
- In the imager's advanced options (or via `raspi-config` after first boot):
  enable SSH for setup only, set hostname `snagr`, **do not** enable Wi-Fi
  yet — bring it up manually only when you deliberately need it (LAN mode /
  the email step). This keeps the box airgapped by default from the first
  boot, not just after you remember to run the toggle script.
- After first boot, confirm the default radio state:
  ```bash
  sudo rfkill block wifi bluetooth
  rfkill list   # both should show "Soft blocked: yes"
  ```

## 2. Create the service account

```bash
sudo useradd -r -m -d /opt/snagr -s /usr/sbin/nologin snagr
sudo mkdir -p /opt/snagr/cases /etc/snagr
sudo chown -R snagr:snagr /opt/snagr
```

`snagr` is unprivileged and cannot log in interactively — it only runs the
two services below.

## 3. USB / adb access (no root needed on the Pi side)

```bash
# udev rule so the `snagr` user can talk to a plugged-in Android device
# without running the engine as root.
echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="*", MODE="0666", GROUP="plugdev"' \
  | sudo tee /etc/udev/rules.d/51-android.rules
sudo usermod -aG plugdev snagr
sudo udevadm control --reload-rules
```

## 4. Build the bundle — on the Pi itself

Cross-compiling PyInstaller output for arm64 from an x86 dev machine is more
trouble than it's worth. Build natively on the Pi (or an identical arm64
box) so the `triage-engine` binary and bundled `adb` are the right arch:

```bash
sudo apt update && sudo apt install -y python3-venv python3-pip nodejs npm git
git clone <your fork/remote> /opt/snagr-src && cd /opt/snagr-src

cd engine
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt pyinstaller
cd ..

cd app
npm install
cd ..

python3 build_package.py --platform linux --version <x.y.z>
```

This produces `dist/SNAGR-<x.y.z>/` — copy its contents into `/opt/snagr`:

```bash
sudo cp -r dist/SNAGR-*/* /opt/snagr/
sudo chown -R snagr:snagr /opt/snagr
```

**Known gap in the bundle script:** `build_package.py` copies the raw
Electron `main.cjs`/preload/etc. but not `node_modules`, and `run.sh` expects
a global `electron` binary on `PATH`. Install one:

```bash
sudo npm install -g electron
```

(A proper `electron-builder` packaged binary would remove this dependency —
worth doing separately if you want a single self-contained artifact; out of
scope for wiring the hardware modes.)

## 5. Local LLM (AI evidence summary)

Nothing to do — `engine/triage/intel/hardware.py` auto-detects RAM/CPU at
engine startup and pulls a RAM-sized Ollama model on first run via the
official Linux installer (works on arm64). If you'd rather pre-pull it once
over a wired connection instead of on first field use:

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:3b   # or whatever hardware.py's tier picks for this box's RAM
```

To disable the auto-install entirely (e.g. this Pi will never be online
long enough to fetch a model): set `SNAGR_LLM_AUTOINSTALL=0` in
`/etc/snagr/auth.env` (below) — the AI summary feature then honestly reports
"no reachable model" instead of trying to fetch one.

## 6. Auth — required before either mode goes live

`/etc/snagr/auth.env` (create it, `chmod 600`, owned by `snagr:snagr`):

```
SNAGR_AUTH_USER=examiner
SNAGR_AUTH_PASS=<a real password — do not ship this file in git>
# SNAGR_LLM_AUTOINSTALL=0   # uncomment if this box should never fetch models
```

`--network-mode lan` **refuses to start** if this isn't set (or if
`SNAGR_DEMO=1` is set) — see `engine/triage/server.py`. Airgapped mode will
start without it, but don't leave real evidence behind demo credentials
(`examiner`/`snagr`) even on a loopback-only box.

## 7. Install the systemd units + firewall ruleset

```bash
sudo cp deploy/snagr-kiosk.service deploy/snagr-engine.service /etc/systemd/system/
sudo cp deploy/nftables-lan.conf /opt/snagr/deploy/nftables-lan.conf   # keep with the install
sudo cp deploy/set-network-mode.sh deploy/send-report-email.sh /opt/snagr/deploy/
sudo chmod +x /opt/snagr/deploy/*.sh
sudo systemctl daemon-reload
```

**Edit `deploy/nftables-lan.conf` before relying on it** — the sample subnet
(`192.168.1.0/24`) needs to match wherever the investigator's laptop
actually is.

## 8. Pick a mode

```bash
# Airgapped (default): kiosk on the Pi's own HDMI display, radios stay off.
sudo /opt/snagr/deploy/set-network-mode.sh airgapped

# LAN: no screen needed on the Pi, laptop browser is the GUI.
sudo /opt/snagr/deploy/set-network-mode.sh lan 192.168.1.50
```

The script writes `/etc/snagr/network-mode.env`, applies (or flushes) the
firewall ruleset, and enables exactly one of the two systemd units — they
`Conflicts=` each other so both can never be bound to port 5057 at once.

Check status any time:

```bash
systemctl status snagr-kiosk.service snagr-engine.service
```

## 9. Getting reports off the box

- **USB**: `cp /opt/snagr/cases/<CASE_ID>/report.html /media/usb/` (or the
  Export button in the dashboard, which zips the whole case).
- **Email, one shot, outbound only**:
  ```bash
  sudo /opt/snagr/deploy/send-report-email.sh CASE-0001 investigator@example.com
  ```
  Brings Wi-Fi up, sends directly to your mail provider over TLS via
  `msmtp` (no relay/middleware — install and configure `msmtp` with your own
  account first, see comments in the script), then blocks the radio again.
- **LAN mode**: the laptop already has it open in a browser — print to PDF
  or use the dashboard's own export button.

## 10. Physical hardening notes

- Fanless case, USB-C PD power bank as UPS if this needs to survive a
  transport gap without losing chain-of-custody continuity.
- Label the box with its `snagr-kiosk`/`snagr-engine` mode and the last
  `set-network-mode.sh` run, for the evidence log.
- The report's "Network posture" line (in the Case card) now records which
  mode an acquisition actually ran under — check it lands as expected before
  relying on it in testimony.
