import { useState, type FormEvent } from "react";
import { api } from "../lib/api";
import type { Health } from "../lib/types";

/**
 * Sign-in gate. One examiner account, credentials issued by whoever set up this
 * installation (ERAKSHAK_AUTH_USER / ERAKSHAK_AUTH_PASS on the engine — see
 * triage/server.py). The token this returns is held in memory by the engine, so a
 * restarted engine logs everyone out; that's intentional, not a bug.
 */
export function LoginView({
  health,
  onSuccess,
}: {
  health: Health | null;
  onSuccess: (username: string) => void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!username || !password || loading) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.login(username, password);
      onSuccess(res.username);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="h-screen flex items-center justify-center bg-panel px-4">
      <form onSubmit={submit} className="card w-full max-w-sm p-8">
        <div className="text-center mb-6">
          <div className="text-2xl font-bold text-accent">eRakshak</div>
          <div className="text-sm text-muted mt-1">Android Rapid Evidence Triage</div>
        </div>

        <label className="label" htmlFor="login-username">
          Examiner ID
        </label>
        <input
          id="login-username"
          className="input mb-4"
          autoFocus
          autoComplete="username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
        />

        <label className="label" htmlFor="login-password">
          Password
        </label>
        <input
          id="login-password"
          type="password"
          className="input"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />

        {error && <div className="text-xs text-deletion mt-3">{error}</div>}

        <button type="submit" className="btn-accent w-full mt-5" disabled={loading || !username || !password}>
          {loading ? "Signing in…" : "Sign in"}
        </button>

        <div className="flex items-center justify-center gap-1.5 text-xs text-muted mt-6">
          <span className={`h-2 w-2 rounded-full ${health ? "bg-live" : "bg-deletion"}`} />
          {health ? `engine v${health.version} — online` : "engine offline — start the engine first"}
        </div>
      </form>
    </div>
  );
}
