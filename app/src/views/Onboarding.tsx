/**
 * Shown once per session right after sign-in, before the case dashboard. Not a
 * feature tour — three honesty facts an examiner needs before they start an
 * acquisition, so the tier badges they'll see everywhere else aren't a surprise.
 */
export function OnboardingView({ username, onContinue }: { username: string | null; onContinue: () => void }) {
  return (
    <div className="h-screen flex items-center justify-center bg-panel px-4 overflow-auto">
      <div className="card w-full max-w-xl p-8 my-8">
        {username && <div className="text-xs uppercase tracking-wider text-muted mb-1">Signed in as {username}</div>}
        <h1 className="text-xl font-semibold text-ink mb-4">Welcome to SNAGR</h1>
        <p className="text-sm text-muted leading-relaxed mb-5">
          Field-deployable Android rapid evidence triage. Every artifact this tool collects is
          tagged with a tier, so it's always clear how it was obtained.
        </p>

        <ul className="space-y-3 mb-6">
          <li className="flex gap-3 items-start">
            <span className="text-xs font-mono px-1.5 py-0.5 rounded bg-panel border border-line text-muted shrink-0">
              Tier 0
            </span>
            <span className="text-sm text-ink">
              Zero device-state change — <code>adb pull</code> of shared storage, <code>dumpsys</code>.
            </span>
          </li>
          <li className="flex gap-3 items-start">
            <span className="text-xs font-mono px-1.5 py-0.5 rounded bg-panel border border-line text-muted shrink-0">
              Tier 1
            </span>
            <span className="text-sm text-ink">
              Non-root but state-changing — sideloads a Collector APK for content-provider access.
            </span>
          </li>
          <li className="flex gap-3 items-start">
            <span className="text-xs font-mono px-1.5 py-0.5 rounded bg-panel border border-line text-muted shrink-0">
              Tier 2
            </span>
            <span className="text-sm text-ink">
              Root-only — app-private databases (WhatsApp, Telegram, Instagram, Snapchat).
            </span>
          </li>
        </ul>

        <p className="text-xs text-muted leading-relaxed mb-6">
          Every artifact also carries a confidence badge (LIVE / RECOVERED_VERIFIED / CARVED_PARTIAL
          / DELETION_DETECTED) and every action is chain-of-custody logged. This tool never claims
          "read-only" — Tier 1/2 collection changes device state, and that's disclosed, not hidden.
        </p>

        <button className="btn-accent w-full" onClick={onContinue}>
          Continue to dashboard →
        </button>
      </div>
    </div>
  );
}
