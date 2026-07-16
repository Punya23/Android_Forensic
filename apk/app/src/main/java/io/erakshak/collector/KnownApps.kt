package io.erakshak.collector

/**
 * Investigative classification of installed packages.
 *
 * Forensic tools (Cellebrite/Oxygen/Magnet) surface "apps of interest" — messaging, social,
 * crypto wallets, dating, and especially *vault / anti-forensic* apps that hide content or
 * wipe evidence. We classify by an explicit package table first, then fall back to a name
 * heuristic so unknown vault/hider apps ("Calculator Vault", "Hide It Pro", …) still flag.
 *
 * `category` values mirror the engine's normalized taxonomy so the dashboard can group them:
 *   messaging · social · crypto · dating · browser · anti_forensic · cloud · other
 */
object KnownApps {

    /** package → (friendly name, category). */
    private val TABLE: Map<String, Pair<String, String>> = mapOf(
        // Messaging
        "com.whatsapp" to ("WhatsApp" to "messaging"),
        "com.whatsapp.w4b" to ("WhatsApp Business" to "messaging"),
        "org.telegram.messenger" to ("Telegram" to "messaging"),
        "org.telegram.messenger.web" to ("Telegram X" to "messaging"),
        "org.thunderdog.challegram" to ("Telegram X" to "messaging"),
        "org.thoughtcrime.securesms" to ("Signal" to "messaging"),
        "com.instagram.android" to ("Instagram" to "messaging"),
        "com.snapchat.android" to ("Snapchat" to "messaging"),
        "com.facebook.orca" to ("Messenger" to "messaging"),
        "com.facebook.mlite" to ("Messenger Lite" to "messaging"),
        "com.viber.voip" to ("Viber" to "messaging"),
        "jp.naver.line.android" to ("LINE" to "messaging"),
        "com.tencent.mm" to ("WeChat" to "messaging"),
        "com.discord" to ("Discord" to "messaging"),
        "com.wire" to ("Wire" to "messaging"),
        "ch.threema.app" to ("Threema" to "messaging"),
        "com.wickr.enterprise" to ("Wickr" to "messaging"),
        "com.wickr.me" to ("Wickr Me" to "messaging"),
        "org.session.securesms" to ("Session" to "messaging"),
        "im.vector.app" to ("Element (Matrix)" to "messaging"),
        "org.briarproject.briar.android" to ("Briar" to "messaging"),
        "kik.android" to ("Kik" to "messaging"),
        "com.skype.raider" to ("Skype" to "messaging"),
        "com.google.android.apps.messaging" to ("Google Messages (RCS)" to "messaging"),
        // Social
        "com.facebook.katana" to ("Facebook" to "social"),
        "com.twitter.android" to ("X (Twitter)" to "social"),
        "com.zhiliaoapp.musically" to ("TikTok" to "social"),
        "com.ss.android.ugc.trill" to ("TikTok" to "social"),
        "com.reddit.frontpage" to ("Reddit" to "social"),
        "com.linkedin.android" to ("LinkedIn" to "social"),
        // Crypto wallets
        "io.metamask" to ("MetaMask" to "crypto"),
        "com.wallet.crypto.trustapp" to ("Trust Wallet" to "crypto"),
        "com.coinbase.android" to ("Coinbase" to "crypto"),
        "exodusmovement.exodus" to ("Exodus" to "crypto"),
        "piuk.blockchain.android" to ("Blockchain.com" to "crypto"),
        "com.binance.dev" to ("Binance" to "crypto"),
        // Dating
        "com.tinder" to ("Tinder" to "dating"),
        "com.bumble.app" to ("Bumble" to "dating"),
        "co.hinge.app" to ("Hinge" to "dating"),
        "com.grindrapp.android" to ("Grindr" to "dating"),
        "com.okcupid.okcupid" to ("OkCupid" to "dating"),
        // Browsers (incl. privacy)
        "com.android.chrome" to ("Chrome" to "browser"),
        "org.mozilla.firefox" to ("Firefox" to "browser"),
        "com.brave.browser" to ("Brave" to "browser"),
        "com.duckduckgo.mobile.android" to ("DuckDuckGo" to "browser"),
        "org.torproject.torbrowser" to ("Tor Browser" to "browser"),
        "com.sec.android.app.sbrowser" to ("Samsung Internet" to "browser"),
        // Known vault / anti-forensic apps
        "com.domobile.applockwatcher" to ("AppLock (DoMobile)" to "anti_forensic"),
        "com.domobile.applock" to ("AppLock" to "anti_forensic"),
        "com.netqin.ps" to ("Vault (NQ)" to "anti_forensic"),
        "com.kaspersky.vault" to ("Kaspersky Vault" to "anti_forensic"),
        "com.keepsafe.app" to ("KeepSafe Photo Vault" to "anti_forensic"),
        "com.thinkyeah.galleryvault" to ("GalleryVault" to "anti_forensic"),
        "com.applock.vault.hidephotos" to ("Vault - Hide Photos" to "anti_forensic"),
        "com.calculator.vault.hider" to ("Calculator Vault" to "anti_forensic"),
        "com.privacy.hider" to ("Privacy Hider" to "anti_forensic"),
        "com.enchantedcloud.photovault" to ("Photo Vault" to "anti_forensic"),
        // Cloud / file transfer (common exfil surfaces)
        "com.google.android.apps.docs" to ("Google Drive" to "cloud"),
        "com.dropbox.android" to ("Dropbox" to "cloud"),
        "com.microsoft.skydrive" to ("OneDrive" to "cloud"),
        "mega.privacy.android.app" to ("MEGA" to "cloud"),
        "com.lenovo.anyshare.gps" to ("SHAREit" to "cloud"),
    )

    /** Substrings that mark a likely vault / content-hider even if the package is unknown. */
    private val VAULT_HINTS = listOf(
        "vault", "applock", "app.lock", "hideit", "hide.it", "hidephoto", "hidepic",
        "privatespace", "private.space", "secretbox", "photohide", "gallerylock",
        "calculatorvault", "hidepictures",
    )

    data class Classification(val friendlyName: String?, val category: String, val notable: Boolean)

    /** Classify a package by its id and (best-effort) human label. */
    fun classify(packageName: String, label: String?): Classification {
        TABLE[packageName]?.let { (name, cat) ->
            return Classification(name, cat, notable = true)
        }
        val hay = (packageName + " " + (label ?: "")).lowercase()
        if (VAULT_HINTS.any { hay.contains(it) }) {
            return Classification(null, "anti_forensic", notable = true)
        }
        return Classification(null, "other", notable = false)
    }

    /** Account authenticator type → owning app, for the Accounts collector. */
    fun accountTypeToApp(type: String): String? = when {
        type.contains("whatsapp") -> "WhatsApp"
        type.contains("telegram") -> "Telegram"
        type.contains("instagram") -> "Instagram"
        type.contains("snapchat") -> "Snapchat"
        type.contains("signal") -> "Signal"
        type.contains("google") -> "Google"
        type.contains("facebook") -> "Facebook"
        type.contains("twitter") -> "X (Twitter)"
        type.contains("microsoft") -> "Microsoft"
        else -> null
    }
}
