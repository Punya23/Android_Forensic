/**
 * Root (Tier-2) acquisition is hidden until it has been validated on real rooted handsets.
 *
 * Android 12+ blocks every non-root route to another app's private storage (`adb backup`
 * excludes it, `run-as` needs a debuggable build), so Instagram/Snapchat/Telegram/WhatsApp
 * databases, saved Wi-Fi keys and system stores are reachable only through `su`. That path
 * is implemented and unit-tested against fixtures but has not been run on real hardware, so
 * the examiner is not offered it. Flip to true once it has been proven on a rooted device.
 * Account-data exports (Instagram/Snapchat/Telegram) need no device and stay available.
 */
export const ROOT_ACQUISITION = false;
