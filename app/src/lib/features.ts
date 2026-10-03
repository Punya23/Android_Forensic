/**
 * Root (Tier-2) acquisition is hidden until it has been validated on real rooted handsets.
 *
 * Android 12+ blocks every non-root route to another app's private storage (`adb backup`
 * excludes it, `run-as` needs a debuggable build), so Instagram/Snapchat/Telegram/WhatsApp
 * databases, saved Wi-Fi keys and system stores are reachable only through `su`. That path
 * is implemented and unit-tested against fixtures but has not been run on real hardware, so
 * the examiner is not offered it by default. Flip to true once it has been proven on a
 * rooted device. Account-data exports (Instagram/Snapchat/Telegram) need no device and stay
 * available either way.
 *
 * This engine never roots a phone itself (see triage/capabilities.py for why — mainly that
 * unlocking the bootloader wipes the evidence on almost every device). An examiner who roots
 * the device by other means can reveal the same Tier-2 UI for just that session from the
 * Acquisition screen; the real `su -c id` device check still gates every checkbox either way.
 */
export const ROOT_ACQUISITION = false;
