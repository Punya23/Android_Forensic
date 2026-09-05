import { useMemo, useState } from "react";
import { AlertTriangle } from "lucide-react";
import { useDataset, fmtTs } from "../lib/hooks";
import { SectionHeader, SortTh, useSort } from "../components/common";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/**
 * One account registered with Android's AccountManager, as reported by
 * `dumpsys account`. The engine has shipped both `name`/`type` and
 * `email`/`account_type` spellings, so both are accepted and neither is assumed.
 */
export interface GoogleAccount {
  name?: string;
  email?: string;
  type?: string;
  account_type?: string;
  last_sync?: string;
  source?: string;
  is_primary?: boolean;
  is_google?: boolean;
  caveats?: string[];
  warnings?: string[];
}

function acctName(a: GoogleAccount): string {
  return (a.name ?? a.email ?? "").trim();
}

function acctType(a: GoogleAccount): string {
  return (a.type ?? a.account_type ?? "").trim();
}

// ---------------------------------------------------------------------------
// Presentational helpers
// ---------------------------------------------------------------------------

function TypeBadge({ value }: { value: string }) {
  const isGoogle = value.toLowerCase().includes("google");
  return (
    <span
      title={value || "Account type not reported"}
      style={{
        display: "inline-block",
        padding: "1px 8px",
        borderRadius: 4,
        fontSize: 11,
        fontWeight: 600,
        fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
        color: isGoogle ? "#2258a8" : "#555",
        background: isGoogle ? "#e2ecfa" : "#f0f0f0",
        whiteSpace: "nowrap",
      }}
    >
      {value || "unknown type"}
    </span>
  );
}

function CaveatList({ items }: { items: string[] }) {
  if (items.length === 0) return null;
  return (
    <ul className="mt-1 space-y-0.5">
      {items.map((c, i) => (
        <li key={i} className="text-[11px] text-warn leading-snug">
          <span className="inline-flex items-center gap-1">
            <AlertTriangle className="inline h-3.5 w-3.5" strokeWidth={1.75} aria-hidden />
            {c}
          </span>
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// View
// ---------------------------------------------------------------------------

export function GoogleAccountsView({ caseId }: { caseId: string }) {
  const { data, loading } = useDataset<GoogleAccount>(caseId, "google_accounts");
  const [filter, setFilter] = useState("");

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return data;
    return data.filter(
      (a) => acctName(a).toLowerCase().includes(q) || acctType(a).toLowerCase().includes(q),
    );
  }, [data, filter]);
  // Hooks must run unconditionally on every render — computed here, before either
  // early return below, rather than after the empty-state check.
  const sort = useSort<GoogleAccount>(filtered);

  if (loading) return <div className="p-8 text-muted text-sm animate-pulse">Loading registered accounts…</div>;

  const tierBadge = (
    <span className="text-xs font-normal text-muted bg-panel-2 border border-line rounded px-2 py-0.5">
      Tier 0 — Read-only
    </span>
  );

  // Honest empty state: [] is genuinely ambiguous here, and the view must say so
  // rather than assert "no accounts on the device".
  if (data.length === 0) {
    return (
      <div className="p-6">
        <SectionHeader title="Registered Accounts" sub="dumpsys account" right={tierBadge} />
        <div className="card p-6 max-w-2xl">
          <div className="text-warn font-semibold mb-2">Empty dataset — cause not determinable from this view</div>
          <p className="text-sm text-muted leading-relaxed">
            An empty account list has two very different meanings and this view cannot distinguish them on its
            own:
          </p>
          <ul className="text-sm text-muted leading-relaxed mt-2 space-y-1 list-disc pl-5">
            <li>
              <strong className="text-ink">Not collected</strong> — the acquisition never ran{" "}
              <code className="text-ink">dumpsys account</code>, or the shell read failed. Nothing is known
              about accounts on this device.
            </li>
            <li>
              <strong className="text-ink">No accounts registered</strong> — the command ran and AccountManager
              reported nothing, meaning no account was registered at capture time.
            </li>
          </ul>
          <p className="text-sm text-muted leading-relaxed mt-2">
            Resolve it in the chain-of-custody trail: a{" "}
            <code className="text-ink">shell.dumpsys</code> event with command{" "}
            <code className="text-ink">dumpsys account</code> means the read happened and the second reading
            applies. No such event means the first.
          </p>
          <p className="text-sm text-muted leading-relaxed mt-2">
            Note also that some OEM and hardened builds redact account names from{" "}
            <code className="text-ink">dumpsys</code> output for a non-privileged shell, which produces an
            empty list on a device that does have accounts signed in.
          </p>
        </div>
      </div>
    );
  }

  const googleCount = data.filter((a) => acctType(a).toLowerCase().includes("google")).length;
  const withSync = data.filter((a) => a.last_sync).length;

  return (
    <div className="p-6 max-w-4xl mx-auto">
      <SectionHeader
        title="Registered Accounts"
        sub={`${data.length} account${data.length === 1 ? "" : "s"} registered with AccountManager · ${googleCount} Google`}
        right={tierBadge}
      />

      {/* Forensic caveat — presence is not ownership. */}
      <div className="card p-3 mb-4 border-warn/40 bg-warn/5 text-xs text-warn leading-relaxed">
        <span className="font-semibold">Forensic notice: </span>
        <code className="font-mono">dumpsys account</code> lists the accounts registered with Android's{" "}
        AccountManager <strong>at the moment of capture</strong>. It shows{" "}
        <strong>presence, not ownership</strong>. A signed-in account is not proof that the account holder was
        using — or ever used — this device: accounts are added by anyone with the unlocked handset, survive a
        change of user, are provisioned by an employer or a shop, and remain listed long after the person
        stopped using the phone. Nothing here evidences activity; corroborate with artifacts that carry
        timestamps and content.
      </div>

      {/* Summary tiles */}
      <div className="flex flex-wrap gap-3 mb-4">
        <div className="card px-4 py-3 min-w-[140px] flex-1">
          <div className="text-2xl font-bold text-ink">{data.length}</div>
          <div className="text-[11px] uppercase tracking-wider text-muted mt-0.5">Accounts listed</div>
        </div>
        <div className="card px-4 py-3 min-w-[140px] flex-1">
          <div className="text-2xl font-bold text-accent">{googleCount}</div>
          <div className="text-[11px] uppercase tracking-wider text-muted mt-0.5">Google accounts</div>
        </div>
        <div className="card px-4 py-3 min-w-[140px] flex-1">
          <div className="text-2xl font-bold text-ink">{withSync}</div>
          <div className="text-[11px] uppercase tracking-wider text-muted mt-0.5">With a sync time</div>
          {withSync < data.length && (
            <div className="text-[10px] text-muted mt-1 leading-snug">
              {data.length - withSync} row(s) carry no sync timestamp
            </div>
          )}
        </div>
      </div>

      {data.length > 6 && (
        <div className="mb-3">
          <input
            className="input max-w-sm"
            placeholder="Filter by account or type…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        </div>
      )}

      {/* Account table */}
      <div className="card overflow-auto">
        <table className="w-full text-sm">
          <thead>
            <tr>
              <SortTh className="th" label="Account" sortKeyName="account" getValue={(a) => acctName(a)} sort={sort} />
              <SortTh className="th w-56" label="Account type" sortKeyName="account_type" getValue={(a) => acctType(a)} sort={sort} />
              <SortTh className="th w-48" label="Last sync (UTC)" sortKeyName="last_sync" getValue={(a) => a.last_sync} sort={sort} />
              <SortTh className="th w-36" label="Read from" sortKeyName="source" getValue={(a) => a.source} sort={sort} />
            </tr>
          </thead>
          <tbody>
            {sort.sorted.length === 0 ? (
              <tr>
                <td colSpan={4} className="td text-center text-muted text-xs py-6">
                  No accounts match your filter.
                </td>
              </tr>
            ) : (
              sort.sorted.map((a, i) => {
                const name = acctName(a);
                const rowCaveats = [...(a.caveats ?? []), ...(a.warnings ?? [])];
                return (
                  <tr key={`${name}-${i}`}>
                    <td className="td">
                      <div className="font-mono text-xs text-ink break-all select-all">
                        {name || <span className="text-muted italic font-sans">name not reported</span>}
                      </div>
                      {a.is_primary && (
                        <div
                          className="text-[10px] text-muted mt-0.5"
                          title="Inferred: the first Google account seen in the dumpsys output. Android does not label a primary account here."
                        >
                          inferred first Google account — <span className="text-warn">approximate, not an OS-declared &ldquo;primary&rdquo;</span>
                        </div>
                      )}
                      <CaveatList items={rowCaveats} />
                    </td>
                    <td className="td">
                      <TypeBadge value={acctType(a)} />
                    </td>
                    <td className="td font-mono text-xs text-muted">
                      {a.last_sync ? (
                        fmtTs(a.last_sync)
                      ) : (
                        <span
                          className="italic"
                          title="dumpsys reported no sync time near this account entry — it does not mean the account never synced"
                        >
                          not reported
                        </span>
                      )}
                    </td>
                    <td className="td font-mono text-[11px] text-muted">{a.source || "dumpsys account"}</td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      <p className="text-[11px] text-muted mt-3 mb-6 leading-snug">
        <strong className="text-warn">Last sync</strong> is scraped from the text near each account entry in the{" "}
        <code className="font-mono">dumpsys</code> dump and is <strong>approximate</strong>: it reflects a sync
        adapter&rsquo;s bookkeeping, not a user action, and a missing value means the dump did not carry one —
        not that the account never synced. Account names are reproduced verbatim from the device; they are
        strings chosen by whoever added the account and are not verified identities.
      </p>
    </div>
  );
}
