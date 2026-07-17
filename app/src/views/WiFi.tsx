import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { WifiNetwork } from "../lib/types";

const CONF_COLORS: Record<string, { bg: string; text: string; border: string }> = {
  live:      { bg: "#e4f4ea", text: "#1c7d3f", border: "#1c7d3f" },
  recovered: { bg: "#e2ecfa", text: "#2258a8", border: "#2258a8" },
  carved:    { bg: "#f6ecd4", text: "#a6741a", border: "#a6741a" },
  deletion:  { bg: "#f6dedd", text: "#a5322f", border: "#a5322f" },
};

const SEC_COLORS: Record<string, { bg: string; text: string }> = {
  "WPA/WPA2": { bg: "#e2ecfa", text: "#2258a8" },
  "WPA3":     { bg: "#e4f4ea", text: "#1c7d3f" },
  "WEP":      { bg: "#f6ecd4", text: "#a6741a" },
  "OPEN":     { bg: "#f6dedd", text: "#a5322f" },
};

function ConfidenceBadge({ value }: { value: string }) {
  const c = CONF_COLORS[value] ?? CONF_COLORS.live;
  return (
    <span
      style={{
        display: "inline-block",
        padding: "1px 8px",
        borderRadius: 4,
        fontSize: 11,
        fontWeight: 600,
        color: c.text,
        background: c.bg,
        border: `1px solid ${c.border}`,
        whiteSpace: "nowrap",
      }}
    >
      {value.toUpperCase()}
    </span>
  );
}

function SecurityBadge({ value }: { value: string }) {
  const c = SEC_COLORS[value] ?? { bg: "#f0f0f0", text: "#555" };
  return (
    <span
      style={{
        display: "inline-block",
        padding: "1px 8px",
        borderRadius: 4,
        fontSize: 11,
        fontWeight: 600,
        color: c.text,
        background: c.bg,
        whiteSpace: "nowrap",
      }}
    >
      {value || "OPEN"}
    </span>
  );
}

function PasswordCell({ password }: { password: string }) {
  const [revealed, setRevealed] = useState(false);
  if (!password) {
    return <span className="text-muted text-xs italic">— open / enterprise</span>;
  }
  return (
    <span className="flex items-center gap-2">
      <span
        className="font-mono text-sm select-all"
        style={{ letterSpacing: revealed ? "0" : "0.15em" }}
      >
        {revealed ? password : "•".repeat(Math.min(password.length, 16))}
      </span>
      <button
        className="text-xs text-accent underline underline-offset-2 shrink-0"
        onClick={() => setRevealed((r) => !r)}
        title={revealed ? "Hide password" : "Reveal password"}
      >
        {revealed ? "hide" : "reveal"}
      </button>
    </span>
  );
}

export function WifiView({ caseId }: { caseId: string }) {
  const [networks, setNetworks] = useState<WifiNetwork[] | null>(null);
  const [filter, setFilter] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .dataset<WifiNetwork[]>(caseId, "wifi")
      .then(setNetworks)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [caseId]);

  if (error) {
    return (
      <div className="p-8 text-sm text-deletion">
        Failed to load Wi-Fi data: {error}
      </div>
    );
  }

  if (networks === null) {
    return (
      <div className="p-8 text-muted text-sm animate-pulse">
        Loading Wi-Fi credentials…
      </div>
    );
  }

  const filtered = networks.filter((n) => {
    const q = filter.toLowerCase();
    return (
      n.ssid.toLowerCase().includes(q) ||
      n.security.toLowerCase().includes(q) ||
      n.source_file.toLowerCase().includes(q)
    );
  });

  const withPassword = networks.filter((n) => n.password).length;

  return (
    <div className="p-6 max-w-5xl mx-auto">
      {/* Header */}
      <div className="mb-5">
        <h1 className="text-xl font-bold mb-1 flex items-center gap-2">
          <span>📶</span> Wi-Fi Passwords
          <span className="text-xs font-normal text-muted bg-panel-2 border border-line rounded px-2 py-0.5 ml-1">
            Tier 2 — Root
          </span>
        </h1>
        <p className="text-sm text-muted">
          Stored Wi-Fi credentials recovered from the device's system
          configuration file. No active cracking was performed — passwords are
          reproduced verbatim from the OS's plaintext storage.
        </p>
      </div>

      {/* Forensic disclaimer */}
      <div className="card p-3 mb-4 border-warn/40 bg-warn/5 text-xs text-warn leading-relaxed">
        <span className="font-semibold">Forensic notice: </span>
        Credentials were pulled via{" "}
        <code className="font-mono">su&nbsp;-c&nbsp;cp</code> from{" "}
        <code className="font-mono">/data/misc/wifi/</code> (root required). The
        original file was not modified. Every step is recorded in the chain-of-custody
        audit trail under <em>tier2.wifi.*</em> events.
      </div>

      {networks.length === 0 ? (
        /* Empty state */
        <div className="card p-10 text-center text-muted">
          <div className="text-4xl mb-3 opacity-40">📶</div>
          <div className="font-medium mb-1">No Wi-Fi credentials recovered</div>
          <div className="text-sm">
            Enable <strong>Tier-2 Wi-Fi Credentials</strong> on the next
            acquisition, or the device may have no saved networks.
          </div>
        </div>
      ) : (
        <>
          {/* Summary bar */}
          <div className="flex flex-wrap gap-3 mb-4">
            {[
              { label: "Total networks", value: networks.length },
              { label: "With password", value: withPassword },
              { label: "Open / enterprise", value: networks.length - withPassword },
            ].map(({ label, value }) => (
              <div
                key={label}
                className="card px-4 py-2 flex flex-col items-center min-w-[110px]"
              >
                <span className="text-xl font-bold text-accent">{value}</span>
                <span className="text-xs text-muted mt-0.5">{label}</span>
              </div>
            ))}
          </div>

          {/* Filter */}
          <div className="mb-3">
            <input
              className="input max-w-sm"
              placeholder="Filter by SSID, security, or source…"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
          </div>

          {/* Table */}
          <div className="card overflow-auto">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="border-b border-line text-xs uppercase tracking-wider text-muted">
                  <th className="text-left py-2 px-3 font-semibold">SSID</th>
                  <th className="text-left py-2 px-3 font-semibold">Security</th>
                  <th className="text-left py-2 px-3 font-semibold">Password</th>
                  <th className="text-left py-2 px-3 font-semibold">Confidence</th>
                  <th className="text-left py-2 px-3 font-semibold">Source file</th>
                </tr>
              </thead>
              <tbody>
                {filtered.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="text-center py-8 text-muted text-xs">
                      No networks match your filter.
                    </td>
                  </tr>
                ) : (
                  filtered.map((n, i) => (
                    <tr
                      key={i}
                      className="border-b border-line/50 hover:bg-panel-2/50 transition-colors"
                    >
                      <td className="py-2.5 px-3 font-medium">{n.ssid || <span className="text-muted italic">—</span>}</td>
                      <td className="py-2.5 px-3">
                        <SecurityBadge value={n.security} />
                      </td>
                      <td className="py-2.5 px-3">
                        <PasswordCell password={n.password} />
                      </td>
                      <td className="py-2.5 px-3">
                        <ConfidenceBadge value={n.confidence} />
                      </td>
                      <td className="py-2.5 px-3 font-mono text-xs text-muted">
                        {n.source_file}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>

          {filtered.length < networks.length && (
            <p className="text-xs text-muted mt-2">
              Showing {filtered.length} of {networks.length} networks
            </p>
          )}
        </>
      )}
    </div>
  );
}
