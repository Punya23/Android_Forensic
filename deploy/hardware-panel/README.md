# SNAGR hardware panel — ESP32 demo build

4-day-build physical panel: a real kill switch + status LED, wirelessly
linked to the laptop running the actual engine. Not a prop board blinking
on its own — the button calls the same `CancellationToken` the dashboard's
own Cancel button uses, and the LED polls the engine's real state. See
`engine/triage/server.py`'s `/api/hardware/status` and
`/api/hardware/killswitch` for the two endpoints this talks to.

## What it does, honestly

- **Kill switch**: press it, the running acquisition gets cancelled — a real
  cooperative cancel, same code path as the dashboard, same audit trail.
- **Status LED**: blue = idle, amber = acquisition running, green = done,
  red = cancelled/error. Polled from the engine every ~700ms, not simulated.
- **Wireless**: the ESP32 runs its own WiFi access point. The laptop joins
  it directly — no venue WiFi, no router, no internet needed anywhere in the
  loop. This is the same "no cloud, no relay" story the rest of the project
  already tells, just extended to the physical panel.

What it is *not*: it doesn't do any forensic computation. All of that stays
exactly where it already lives — the Python engine on the laptop. Say that
plainly if asked; it's a control-panel prototype for the appliance
described in "Future scope" below, not a claim that compute moved to it.

## Bill of materials (~₹700-900 / $10-12, one trip)

| Part | Notes |
|---|---|
| ESP32 DevKit (WROOM-32) | Any generic dev board works |
| WS2812B single LED or small ring | "NeoPixel"-compatible; a ring of 8 looks better on a table than one pixel |
| 12mm momentary push button (arcade-style if you can find one) | The kill switch — bigger/redder reads better to judges |
| Breadboard + jumper wires (M-M, M-F) | |
| USB cable for the board | Also powers it — see note on battery below |
| Optional: small project box / acrylic panel | Mount the button + LED so it doesn't look like loose breadboard wiring |
| Optional: piezo buzzer | One beep on "done" — cheap, adds a nice sensory hit |
| Optional: 18650 + TP4056 charge module | Only if you want it untethered from USB too — the WiFi link is already wireless regardless of power source |

## Build order (fits in an afternoon)

1. Wire per the comment block at the top of `snagr_panel.ino`.
2. Arduino IDE: install "ESP32" boards via Boards Manager (espressif URL),
   install "Adafruit NeoPixel" via Library Manager.
3. Edit `AP_PASSWORD` in the sketch to something real, flash it.
4. Power the board, open Serial Monitor (115200 baud) — confirms the AP is
   up and prints its own IP (normally `192.168.4.1`).
5. On the laptop, join WiFi network `SNAGR-Panel`.
6. Check the laptop's IP on that connection (`ifconfig`/`ipconfig`) —
   usually `192.168.4.2`. If it's something else, update `ENGINE_HOST` in
   the sketch to match and reflash.
7. Start the engine bound to that address:
   ```bash
   cd engine
   SNAGR_AUTH_PASS=<something-real> python -m triage.server \
     --network-mode lan --host 192.168.4.2 --port 5057
   ```
   (`--network-mode lan` is required — the engine's default `airgapped` mode
   binds loopback-only and the ESP32, being a separate device, can't reach
   it there. This is the same flag introduced in `deploy/pi5-setup.md` for
   the full-appliance case; the demo is a live instance of it.)
8. Open the dashboard as normal (`app/`, dev or built) on the same laptop —
   it still talks to `127.0.0.1`/its own origin exactly as before; the panel
   is an additional, independent client of two new endpoints, not a
   replacement for the dashboard's own controls.
9. Start an acquisition from the dashboard, watch the LED go amber, press
   the button — LED goes red, dashboard shows "cancelled" like it would from
   its own Cancel button. That's the whole demo.

## For the submission: system architecture slide

Two boxes, one arrow, labelled honestly:

```
[ ESP32 panel ]  <--WiFi (own AP, LAN-only)-->  [ Laptop: engine + dashboard ]
  kill switch                                      all acquisition, parsing,
  status LED                                        AI summary — all local
```

Caption it as what it is: *"Physical control-panel prototype, wirelessly
paired to the compute unit. No cloud or third-party service anywhere in the
path — the AP is the ESP32's own, not an internet connection."*

## Future scope of hardware (the slide this sets up)

State plainly that today's compute lives on a laptop, and the roadmap is
folding it into the same box the panel is already talking to:

- **Now**: ESP32 front panel <-> laptop (engine + dashboard).
- **Near-term**: ESP32 front panel <-> dedicated appliance (Pi 5 / mini PC —
  see `deploy/pi5-setup.md`, already built: `--network-mode airgapped`
  kiosk-on-its-own-screen, or `--network-mode lan` for a browser-based GUI).
  Same two endpoints, same firmware — only `ENGINE_HOST` changes from the
  laptop's IP to the appliance's.
- **Further out**: the panel gains its own small screen for case ID / stage
  text instead of just a color, and the kill switch becomes a proper
  E-stop wired for physical, not just cooperative-software, interrupt.

This framing is deliberately conservative — it's what's actually true of
the code today, and it reads as a team that understands the gap between a
demo and a product rather than one hoping nobody asks.
