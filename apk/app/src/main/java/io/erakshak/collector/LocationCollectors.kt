package io.erakshak.collector

import android.Manifest
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothManager
import android.content.Context
import android.location.LocationManager
import android.net.wifi.WifiManager
import android.os.Build
import org.json.JSONArray
import org.json.JSONObject

/**
 * Location-bearing collectors.
 *
 * Three separate artifacts, all Tier 1 (helper APK, non-root) and all geolocatable:
 *
 *  * [LocationCollector] — the OS's last-known fix per provider. This is the only *direct*
 *    coordinate the platform hands a non-privileged app; everything else in the tool is
 *    inferred from media EXIF, cell towers or app databases.
 *  * [WifiCollector] — saved networks and visible APs. A BSSID is a fixed physical radio, so
 *    the set of BSSIDs a phone has seen is a location trail even when GPS was off. Saved
 *    networks are *historic* presence; scan results are *present* location.
 *  * [BluetoothCollector] — bonded devices (car kits, home speakers, another suspect's phone)
 *    place the device near a known object at pairing time.
 *
 * Every collector returns a [CollectionResult] rather than throwing, so a denied permission
 * shows up as a labelled `denied` row in `collector_manifest.json` instead of aborting the run.
 * That distinction matters: "no location recorded" and "we were not allowed to look" are
 * different findings and must never collapse into the same empty file.
 */
object LocationCollector {

    fun collect(ctx: Context): CollectionResult {
        val fine = ctx.granted(Manifest.permission.ACCESS_FINE_LOCATION)
        val coarse = ctx.granted(Manifest.permission.ACCESS_COARSE_LOCATION)
        if (!fine && !coarse) {
            return CollectionResult(
                "location", "location.json", JSONArray(), 0, CollectionResult.DENIED,
                "neither ACCESS_FINE_LOCATION nor ACCESS_COARSE_LOCATION granted"
            )
        }

        return try {
            val lm = ctx.getSystemService(Context.LOCATION_SERVICE) as? LocationManager
                ?: return CollectionResult(
                    "location", "location.json", JSONArray(), 0,
                    CollectionResult.UNSUPPORTED, "no LOCATION_SERVICE on this device"
                )

            val out = JSONArray()
            // getProviders(true) omits disabled providers, and on some OEM builds omits "fused"
            // entirely, so union it with the well-known provider names before querying.
            val providers = LinkedHashSet<String>()
            runCatching { providers.addAll(lm.getProviders(true)) }
            providers += LocationManager.GPS_PROVIDER
            providers += LocationManager.NETWORK_PROVIDER
            providers += LocationManager.PASSIVE_PROVIDER
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                providers += LocationManager.FUSED_PROVIDER
            }

            for (provider in providers) {
                val loc = runCatching { lm.getLastKnownLocation(provider) }.getOrNull() ?: continue
                out.put(
                    JSONObject()
                        .put("provider", provider)
                        .put("latitude", loc.latitude)
                        .put("longitude", loc.longitude)
                        .put("altitude", if (loc.hasAltitude()) loc.altitude else JSONObject.NULL)
                        .put("accuracy", if (loc.hasAccuracy()) loc.accuracy else JSONObject.NULL)
                        .put("bearing", if (loc.hasBearing()) loc.bearing else JSONObject.NULL)
                        .put("speed", if (loc.hasSpeed()) loc.speed else JSONObject.NULL)
                        .put("time", loc.time)
                        .put("elapsed_realtime_ns", loc.elapsedRealtimeNanos)
                        // Anti-forensic signal: a mocked fix means a spoofing app was installed.
                        .put("is_mock", isMock(loc))
                        .put(
                            "provider_enabled",
                            runCatching { lm.isProviderEnabled(provider) }.getOrDefault(false)
                        )
                )
            }

            // Record which providers exist but held no fix, so an empty result is explainable.
            val meta = JSONObject()
                .put("provider", "_meta")
                .put("providers_seen", JSONArray().also { a -> providers.forEach { a.put(it) } })
                .put("permission", if (fine) "fine" else "coarse")
                .put("collected_at_ms", System.currentTimeMillis())
            out.put(meta)

            // Length includes the _meta row; report the real fix count.
            val fixes = out.length() - 1
            CollectionResult(
                "location", "location.json", out, fixes,
                if (fixes == 0) CollectionResult.EMPTY else CollectionResult.OK,
                if (fixes == 0) "providers queried but no cached fix present" else null
            )
        } catch (e: SecurityException) {
            CollectionResult("location", "location.json", JSONArray(), 0, CollectionResult.DENIED, e.message)
        } catch (e: Exception) {
            CollectionResult("location", "location.json", JSONArray(), 0, CollectionResult.ERROR, e.message)
        }
    }

    @Suppress("DEPRECATION")
    private fun isMock(loc: android.location.Location): Boolean = runCatching {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) loc.isMock else loc.isFromMockProvider
    }.getOrDefault(false)
}

/**
 * Saved networks, the current association and a scan snapshot.
 *
 * BSSIDs resolve to street-level coordinates through public WiFi-geolocation databases, so this
 * is a location artifact as much as a network one. `configuredNetworks` is deprecated and
 * returns an empty list to non-system apps on Android 10+ — we still call it because it works on
 * the older devices that make up much of the field fleet, and label the result accordingly.
 */
object WifiCollector {

    fun collect(ctx: Context): CollectionResult {
        if (!ctx.granted(Manifest.permission.ACCESS_WIFI_STATE)) {
            return CollectionResult(
                "wifi", "wifi.json", JSONArray(), 0, CollectionResult.DENIED,
                "ACCESS_WIFI_STATE not granted"
            )
        }
        return try {
            val wm = ctx.applicationContext.getSystemService(Context.WIFI_SERVICE) as? WifiManager
                ?: return CollectionResult(
                    "wifi", "wifi.json", JSONArray(), 0,
                    CollectionResult.UNSUPPORTED, "no WIFI_SERVICE on this device"
                )
            val out = JSONArray()

            // 1. Currently associated network — the device's location right now.
            @Suppress("DEPRECATION")
            val info = runCatching { wm.connectionInfo }.getOrNull()
            if (info != null && info.networkId != -1) {
                val ssid = (info.ssid ?: "").trim('"')
                out.put(
                    JSONObject()
                        .put("type", "current_connection")
                        .put("ssid", ssid)
                        .put("bssid", info.bssid ?: "")
                        .put("rssi", info.rssi)
                        .put("link_speed_mbps", info.linkSpeed)
                        .put("ip_address", intToIp(info.ipAddress))
                        .put("frequency_mhz", info.frequency)
                        .put("hidden", ssid.isEmpty())
                )
            }

            // 2. Saved / configured networks — historic presence at each location.
            @Suppress("DEPRECATION")
            val configured = runCatching { wm.configuredNetworks }.getOrNull()
            if (configured != null) {
                for (cfg in configured) {
                    val ssid = (cfg.SSID ?: "").trim('"')
                    out.put(
                        JSONObject()
                            .put("type", "saved_network")
                            .put("ssid", ssid)
                            .put("bssid", cfg.BSSID ?: "")
                            .put("network_id", cfg.networkId)
                            .put("priority", cfg.priority)
                            .put("hidden", cfg.hiddenSSID)
                            .put(
                                "status", when (cfg.status) {
                                    android.net.wifi.WifiConfiguration.Status.CURRENT -> "current"
                                    android.net.wifi.WifiConfiguration.Status.ENABLED -> "enabled"
                                    android.net.wifi.WifiConfiguration.Status.DISABLED -> "disabled"
                                    else -> "unknown"
                                }
                            )
                    )
                }
            }

            // 3. Scan results — APs physically in range at collection time.
            @Suppress("DEPRECATION")
            val scanResults = runCatching { wm.scanResults }.getOrNull()
            if (scanResults != null) {
                for (sr in scanResults) {
                    out.put(
                        JSONObject()
                            .put("type", "scan_result")
                            .put("ssid", sr.SSID ?: "")
                            .put("bssid", sr.BSSID ?: "")
                            .put("capabilities", sr.capabilities ?: "")
                            .put("frequency_mhz", sr.frequency)
                            .put("level_dbm", sr.level)
                            .put("timestamp_us", sr.timestamp)
                    )
                }
            }

            CollectionResult(
                "wifi", "wifi.json", out, out.length(),
                if (out.length() == 0) CollectionResult.EMPTY else CollectionResult.OK,
                if (configured == null && Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q)
                    "saved-network list unavailable to non-system apps on Android 10+"
                else null
            )
        } catch (e: SecurityException) {
            CollectionResult("wifi", "wifi.json", JSONArray(), 0, CollectionResult.DENIED, e.message)
        } catch (e: Exception) {
            CollectionResult("wifi", "wifi.json", JSONArray(), 0, CollectionResult.ERROR, e.message)
        }
    }

    private fun intToIp(ip: Int): String =
        "${ip and 0xff}.${ip shr 8 and 0xff}.${ip shr 16 and 0xff}.${ip shr 24 and 0xff}"
}

/** Bluetooth adapter identity plus bonded (paired) devices. */
object BluetoothCollector {

    fun collect(ctx: Context): CollectionResult {
        // Android 12+ split the old BLUETOOTH permission into CONNECT/SCAN runtime permissions.
        val hasPerm = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            ctx.granted(Manifest.permission.BLUETOOTH_CONNECT)
        } else {
            @Suppress("DEPRECATION")
            ctx.granted(Manifest.permission.BLUETOOTH)
        }
        if (!hasPerm) {
            return CollectionResult(
                "bluetooth", "bluetooth.json", JSONArray(), 0, CollectionResult.DENIED,
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S)
                    "BLUETOOTH_CONNECT not granted" else "BLUETOOTH not granted"
            )
        }
        return try {
            val manager = ctx.applicationContext.getSystemService(Context.BLUETOOTH_SERVICE)
                as? BluetoothManager
            @Suppress("DEPRECATION")
            val adapter = manager?.adapter ?: BluetoothAdapter.getDefaultAdapter()
            ?: return CollectionResult(
                "bluetooth", "bluetooth.json", JSONArray(), 0,
                CollectionResult.UNSUPPORTED, "no Bluetooth adapter on this device"
            )

            val out = JSONArray()
            out.put(
                JSONObject()
                    .put("type", "adapter")
                    .put("enabled", runCatching { adapter.isEnabled }.getOrDefault(false))
                    .put("name", safeName(adapter))
                    .put("address", safeAddress(adapter))
                    .put(
                        "state", when (runCatching { adapter.state }.getOrDefault(-1)) {
                            BluetoothAdapter.STATE_ON -> "on"
                            BluetoothAdapter.STATE_OFF -> "off"
                            BluetoothAdapter.STATE_TURNING_ON -> "turning_on"
                            BluetoothAdapter.STATE_TURNING_OFF -> "turning_off"
                            else -> "unknown"
                        }
                    )
            )

            val bonded: Set<BluetoothDevice>? =
                runCatching { adapter.bondedDevices }.getOrNull()
            bonded?.forEach { dev ->
                val o = JSONObject().put("type", "bonded_device").put("bond_state", "bonded")
                try {
                    o.put("name", dev.name ?: "")
                    o.put("address", dev.address ?: "")
                    o.put("device_class", dev.bluetoothClass?.deviceClass ?: -1)
                    o.put(
                        "device_type", when (dev.type) {
                            BluetoothDevice.DEVICE_TYPE_CLASSIC -> "classic"
                            BluetoothDevice.DEVICE_TYPE_LE -> "ble"
                            BluetoothDevice.DEVICE_TYPE_DUAL -> "dual"
                            else -> "unknown"
                        }
                    )
                    o.put("uuids", JSONArray().also { arr ->
                        dev.uuids?.forEach { u -> arr.put(u.toString()) }
                    })
                } catch (e: SecurityException) {
                    o.put("name", "[permission_denied]")
                    o.put("address", "[permission_denied]")
                }
                out.put(o)
            }

            CollectionResult(
                "bluetooth", "bluetooth.json", out, out.length(),
                if (out.length() == 0) CollectionResult.EMPTY else CollectionResult.OK
            )
        } catch (e: SecurityException) {
            CollectionResult("bluetooth", "bluetooth.json", JSONArray(), 0, CollectionResult.DENIED, e.message)
        } catch (e: Exception) {
            CollectionResult("bluetooth", "bluetooth.json", JSONArray(), 0, CollectionResult.ERROR, e.message)
        }
    }

    private fun safeName(a: BluetoothAdapter): String =
        runCatching { a.name ?: "" }.getOrDefault("[permission_denied]")

    @Suppress("DEPRECATION")
    private fun safeAddress(a: BluetoothAdapter): String =
        runCatching { a.address ?: "" }.getOrDefault("[permission_denied]")
}
