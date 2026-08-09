# Network artifacts — Wi-Fi, Bluetooth, USB, hotspot

[← back to README](../README.md)

What an Android device actually stores about the networks and devices it has
talked to, which of it SNAGR can read, and — the part that decides whether a
finding survives cross-examination — what each artifact does and does not prove
about **when**.

---

## The short answer

| Question | Answer | Tier |
|---|---|---|
| Are Wi-Fi passwords stored on the device? | **Yes**, in plaintext, for every network the user saved | 2 (root) |
| Does it store *when* it last joined a network? | **No.** Only "has ever connected" and an ordering | 2 (root) |
| Can we tell which networks it actually used vs merely saved? | **Yes** — `HasEverConnected` | 2 (root) |
| Can we get an approximate *when* for network use? | **Yes** — netstats hour buckets, per SSID | 0 (non-root) |
| Are Bluetooth pairings stored? | **Yes**, with link keys and a bond-record write time | 2 (root) |
| Does the bond timestamp mean "they were connected then"? | **No.** It is when the pairing record was written | 2 (root) |
| Is there a real Bluetooth connection *time* anywhere? | **Yes** — OPP file transfers, per transfer | 2 (root) |
| Can we tell which Bluetooth device connected most recently? | **Yes, as a rank.** The field is a counter, not a date | 2 (root) |
| Can a non-root helper APK list saved networks? | **No.** Android 10+ blocks it | 1 |

---

## Wi-Fi

### Where the credentials live

`triage/parsers/wifi.py`, pulled by the `--tier2-wifi` stage. Every path in
`WIFI_CONFIG_PATHS` is probed and **all** hits are parsed, because a device
upgraded across Android 11 can still carry the pre-APEX store — often the only
place a since-forgotten network survives.

| Path | Android | Contents |
|---|---|---|
| `/data/misc/apexdata/com.android.wifi/WifiConfigStore.xml` | 11+ | Saved networks + PSKs |
| `/data/misc/apexdata/com.android.wifi/WifiConfigStoreSoftAp.xml` | 11+ | This device's own hotspot |
| `/data/misc/wifi/WifiConfigStore.xml` | 9–10 | Saved networks + PSKs |
| `/data/misc/wifi/WifiConfigStoreSoftAp.xml` | 9–10 | This device's own hotspot |
| `/data/misc/wifi/wpa_supplicant.conf` | ≤ 8 | Saved networks + PSKs |

> **The Android 11 move is the whole ballgame.** Probing only `/data/misc/wifi/`
> on a current device finds nothing and reports "no Wi-Fi config found", which
> reads as *the device had no saved networks*. That was a live bug in this
> engine; the fix is why the path list exists as data rather than as two
> hardcoded strings.

Networks recovered from a legacy store are deduped against the current one on
`(SSID, is_softap)`, so a stale copy adds forgotten networks without
double-counting live ones.

`WifiConfigStoreSoftAp.xml` is a different fact from the rest of the list: it is
the hotspot this device **offered**, passphrase included, not a network it
joined. Flagged `is_softap=True`. Matching it against another device's saved
networks is a direct device-to-device link.

### What it says about "when"

Android does **not** persist a per-network "last connected at `<datetime>`".
Reporting one would mean inventing it. What the store does carry:

- **`has_ever_connected`** — the network was successfully joined at least once,
  at an unrecorded time. `False` means saved-but-never-joined, which is the
  difference between "was at this address" and "was told the Wi-Fi password".
- **`is_most_recently_connected`** — this was the last network joined as of the
  last store write. An ordering fact.
- **`timestamps`** — every epoch-valued field the store carried, kept under its
  **original field name** (`ConnectChoiceTimestamp` stays
  `ConnectChoiceTimestamp`). A reader sees *which* event was timestamped rather
  than being handed an unlabelled "last seen". Values that are
  elapsed-realtime-since-boot counters are dropped rather than rendered as 1970
  dates.

Also extracted: `creator` / `last_update_by` (which package saved or last
changed the entry), `default_gateway_mac` (the AP-side MAC — geolocatable via a
BSSID lookup), `randomized_mac`, `metered`, `network_status`, `hidden`.

### Where a real "when" does come from — Tier 0

`triage/parsers/wifi_live.py`, non-root, always on unless `--no-wifi-live`:

- **`dumpsys netstats --full --uid`** → per-SSID byte counters in **hour
  buckets**. This is the closest thing to a Wi-Fi usage timeline that exists
  without root. Every bucket is marked `approximate=True` and means "traffic
  crossed this SSID somewhere inside this hour" — never a join or leave time.
- **`dumpsys wifi`** → the current association, the saved-network list as the
  system sees it, scan results.
- **`dumpsys connectivity`** → active network, tethering block.

All volatile: it is the state at capture time, and nothing polls it.

---

## Hotspot

`triage/parsers/hotspot.py`, folded into `collect_wifi_live` output.

**Hosted** (did this device share its connection) is read from explicit state
lines — `SoftApManager … current state: Started/Tethered`,
`WIFI_AP_STATE_ENABLED`, `mWifiApState=13` — plus the `dumpsys connectivity`
tethered-interface list, plus the presence of a SoftAp config on a root pull.

> Searching `dumpsys wifi` for the *word* "SoftAp" does not work: every modern
> Android prints its `SoftApManager` state machine whether or not a hotspot
> exists, so the check returns True for every device ever seized. That was also
> a live bug here.

The result is **tri-state**. `None` means the build reported no AP state at all,
which is not the same finding as "the hotspot was off". And because Android
keeps no hotspot history, even a confident `False` only describes capture time.

**Connected to somebody's hotspot** has no reliable Tier-0 marker. What SNAGR
reports is an **SSID naming heuristic** (`AndroidAP`, `Hotspot`, `iPhone`, …),
labelled as one in the evidence line. An SSID is freely chosen: a home router
can carry these names and a hotspot can be renamed to avoid them. Treat as a
lead. With no saved-network list available the result is `None` with the reason,
not `False`.

---

## Bluetooth

Four artifacts, three tiers, and only some of them carry a clock.

### 1. `dumpsys bluetooth_manager` — Tier 0

`triage/parsers/bluetooth.py`. Bonded devices and adapter state, non-root.
Android 8+ redacts MAC octets for non-privileged callers, so addresses here are
often partial and cannot identify a specific device on their own — the dashboard
renders them as visibly partial rather than as whole identifiers.

### 2. `bt_config.conf` — Tier 2 (root)

`triage/parsers/bt_config.py`, via `--tier2-bt-config`. Reads
`/data/misc/bluedroid/bt_config.conf` (plus `.bak`): per-device `Name`,
`DevClass`, `LinkKey`, address type, LE keys, and a `Timestamp`.

> **That `Timestamp` is the most over-claimed field in Bluetooth forensics.** It
> records when the *pairing record was written*. It is not a connection time,
> not a last-used time, and not evidence the two devices were near each other at
> any later moment. A bond persists until explicitly removed, so the record can
> be years old and the paired device may never have been near the handset again.

Encrypted bond stores (`bt_config.conf.encrypted`, Android 13+ on some stacks)
are detected and reported as *present but unreadable* — the bond list is
**unknown**, which is a different finding from empty.

### 3. `btopp.db` — Tier 2 (root) — **the one with a real clock**

`triage/parsers/bt_transfer.py`. The Object Push Profile transfer log, at
`/data/user_de/0/com.android.bluetooth/databases/btopp.db` (and the
credential-encrypted `/data/data/…` path; on a locked FBE device only the `_de`
copy is readable at all). `-wal` sidecars are pulled too, since the newest
transfers usually live in the WAL and pulling the `.db` alone silently loses
them.

Each row: peer BD_ADDR, filename, MIME type, byte counts, direction, outcome,
and a **wall-clock epoch-ms timestamp**. A transfer row cannot exist unless the
two devices held an active link at that moment — which makes this the strongest
Bluetooth "when" available short of an HCI snoop log.

Caveats the parser attaches per row:

- The time is the **device clock**. If the clock was wrong, so is this.
- `status != 200` means the row records an **attempt**. The link existed; the
  file did not necessarily arrive.
- OPP covers **file transfers only**. Audio streaming, tethering, keyboards and
  every other profile leave no row — an empty log is not evidence of no
  Bluetooth activity.

Deleted rows are carved through the standard `triage.recovery` machinery and
badged `Recovered`/`Carved`, so a cleared transfer history still yields rows.

### 4. `bluetooth_db` — Tier 2 (root) — order, not time

Android 11+ Room database behind `DatabaseManager`. Its
`metadata.last_active_time` column **is not a time**: AOSP assigns it from a
process-wide counter (`sCurrentConnectionNumber++`) on each connection. SNAGR
exposes it as a **rank** — rank 1 is the most recently connected device — and
derives no date from it. Any tool that renders this field as a timestamp is
wrong.

---

## USB connection state

`get_usb_state` in `triage/acquire/real.py`, captured into both the pre- and
post-acquisition device-state snapshots, so a cable pulled mid-run shows up in
the diff and explains a truncated pull.

Three probes, reported separately rather than averaged:

| Probe | Source | Measures |
|---|---|---|
| `battery` | `dumpsys battery` → `USB powered` | A cable supplying power is attached |
| `usb_state` | `/sys/class/android_usb/android0/state` | Gadget stack enumerated against a host |
| `typec_role` | `/sys/class/typec/port0/data_role` | Which side of the link this device is |

Any positive probe reports connected; all-negative reports disconnected; nothing
legible stays `None`.

Two traps worth naming, because an earlier version fell into both:

- **`adb devices` is not a probe.** We are talking to the device over ADB, so it
  passes unconditionally — over USB or over TCP alike. In a 2-of-3 majority vote
  it could carry the verdict on its own.
- **The connected role is `device`, not `host`.** A phone plugged into a
  workstation is the device/UFP side. Reading `host` as "connected" inverts the
  test and returns False on exactly the setup a forensic capture runs on.
  `host` means OTG — the phone is powering something else.

The transport of the ADB session itself (USB vs TCP) is recorded separately as
`transport`: it is a fact about the examiner's setup, not about the device.

---

## Helper APK (Tier 1, non-root)

`WifiCollector` and `BluetoothCollector` in
`apk/app/src/main/java/io/erakshak/collector/LocationCollectors.kt`. What a
sideloaded app can and cannot see:

| Wanted | Non-root app |
|---|---|
| Current association (SSID, BSSID, RSSI, IP) | ✅ |
| Scan results (APs in range now) | ✅ — but only with the location toggle ON |
| Saved-network list | ❌ on Android 10+ — returns an **empty list**, not an error |
| Wi-Fi passwords | ❌ ever |
| Bonded Bluetooth devices | ✅ with `BLUETOOTH_CONNECT` |
| Bluetooth link keys | ❌ ever |

Each of those failure modes produces an empty result that looks identical to a
genuine absence, so the collector records the reason in
`collector_manifest.json` rather than letting the empty file speak for itself:
the saved-list block on Android 10+, an empty scan while the location toggle is
off, an empty bond set while the adapter is off.

---

## Running it

```bash
cd engine

# Wi-Fi credentials + own-hotspot config, Bluetooth bonds + transfers + order
python -m triage.cli acquire --serial <SERIAL> --case CASE01 --examiner "Name" \
    --tier2-wifi --tier2-bt-config

# Tier 0 Wi-Fi live state + hotspot posture runs by default; disable with:
#   --no-wifi-live

# Tests
python -m pytest tests/test_network_artifacts.py tests/test_wifi_live.py \
    tests/test_bt_config.py tests/test_forensic_modules.py -v
```

Datasets written to the case folder: `wifi`, `wifi_live`, `bluetooth`,
`bluetooth_bonds`, `bluetooth_bond_report`, `bluetooth_transfers`,
`bluetooth_transfer_summary`, `bluetooth_connection_order`, `collector_wifi`,
`collector_bluetooth`. All are written even when empty, so "collected, nothing
found" stays distinguishable from "never collected".
