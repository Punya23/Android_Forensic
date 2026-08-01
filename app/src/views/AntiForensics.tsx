/**
 * AntiForensics — structural observations about containers, secondary users and
 * evidence-handling behaviour on the device (Tier 2, root).
 *
 * This view deliberately makes NO claim about intent. A work profile, a dual-messenger clone,
 * a Secure Folder and a privacy-focused app are ordinary consumer features that millions of
 * people use for entirely mundane reasons. What is rendered here is what the filesystem and the
 * user manager actually contain; the innocent reading of every observation is printed next to
 * it, not tucked into a tooltip.
 */
import { useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import { useDataset } from "../lib/hooks";
import { StatCard } from "../components/common";

/** A row from the Android user manager (/data/system/users/). */
export interface AndroidUserRecord {
  user_id: number | string;
  name: string;
  user_type: string;
  flag_labels: string[];
  container_kind: string;
  likely_feature: string;
  extractable: "extractable" | "present-locked" | "unknown";
  data_dirs: string[];
  caveats: string[];
}

/** A single structural observation. `severity` ranks how unusual it is, never how culpable. */
export interface AntiForensicFinding {
  kind: string;
  subject: string;
  detail: string;
  severity: "info" | "warn" | "critical";
  evidence: string[];
  confidence: string;
  caveats: string[];
}

/** Optional roll-up. All fields optional — the engine may write none of them. */
export interface AntiForensicsSummary {
  innocent_explanations?: string;
  innocent_explanation?: string;
  note?: string;
  caveats?: string[];
  users?: number;
  findings?: number;
}

const CONF_COLORS: Record<string, { bg: string; text: string; border: string }> = {
  live: { bg: "#e4f4ea", text: "#1c7d3f", border: "#1c7d3f" },
  recovered: { bg: "#e2ecfa", text: "#2258a8", border: "#2258a8" },
  carved: { bg: "#f6ecd4", text: "#a6741a", border: "#a6741a" },
  deletion: { bg: "#f6dedd", text: "#a5322f", border: "#a5322f" },
};

const SEVERITY_ORDER: Array<AntiForensicFinding["severity"]> = ["critical", "warn", "info"];

const SEVERITY_META: Record<
  AntiForensicFinding["severity"],
  { label: string; pill: string; note: string }
> = {
  critical: {
    label: "Notable",
    pill: "bg-deletion/20 text-deletion",
    note: "Structurally unusual on a stock consumer device — still not, on its own, evidence of wrongdoing.",
  },
  warn: {
    label: "Worth checking",
    pill: "bg-warn/20 text-warn",
    note: "Common enough on ordinary devices; recorded so it can be accounted for rather than discovered later.",
  },
  info: {
    label: "Contextual",
    pill: "bg-recovered/20 text-recovered",
    note: "Background detail about how this device is configured.",
  },
};

const EXTRACTABILITY: Record<
  AndroidUserRecord["extractable"],
  { label: string; cls: string; detail: string }
> = {
  extractable: {
    label: "extractable",
    cls: "bg-live/15 text-live",
    detail: "Container data was readable in this acquisition.",
  },
  "present-locked": {
    label: "present, locked — not extractable",
    cls: "bg-deletion/20 text-deletion",
    detail:
      "The container exists on disk and holds data. It is under a separate credential (e.g. Secure Folder / work-profile lock) and was not decrypted, so its contents could not be read. This is emphatically NOT an empty container.",
  },
  unknown: {
    label: "unknown",
    cls: "bg-panel text-muted",
    detail: "Extractability could not be determined from what was collected. Treat as untested, not as absent.",
  },
};

function ConfidenceBadge({ value }: { value: string }) {
  const c = CONF_COLORS[value] ?? CONF_COLORS.carved;
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
      {(value || "unknown").toUpperCase()}
    </span>
  );
}

function Chips({ items, empty }: { items: string[]; empty: string }) {
  if (!items || items.length === 0) {
    return <span className="text-[11px] text-muted italic">{empty}</span>;
  }
  return (
    <span className="flex flex-wrap gap-1">
      {items.map((s, i) => (
        <span
          key={i}
          className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-panel border border-line text-muted break-all"
        >
          {s}
        </span>
      ))}
    </span>
  );
}

/** Caveats are always rendered inline. Never collapsed, never a tooltip. */
function Caveats({ items, label }: { items: string[]; label?: string }) {
  if (!items || items.length === 0) return null;
  return (
    <div className="mt-1.5">
      {label && (
        <div className="text-[10px] uppercase tracking-wider text-muted mb-0.5">{label}</div>
      )}
      <ul className="space-y-0.5">
        {items.map((c, i) => (
          <li key={i} className="text-[11px] text-warn leading-relaxed">
            ⚠ {c}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function AntiForensicsView({ caseId }: { caseId: string }) {
  const { data: users, loading: usersLoading } = useDataset<AndroidUserRecord>(caseId, "android_users");
  const { data: findings, loading: findingsLoading } = useDataset<AntiForensicFinding>(
    caseId,
    "antiforensic_findings"
  );
  const [summary, setSummary] = useState<AntiForensicsSummary>({});

  useEffect(() => {
    let alive = true;
    api
      .dataset<AntiForensicsSummary>(caseId, "antiforensics_summary")
      .then((s) => alive && setSummary(s || {}))
      .catch(() => alive && setSummary({}));
    return () => {
      alive = false;
    };
  }, [caseId]);

  const grouped = useMemo(() => {
    const m = new Map<AntiForensicFinding["severity"], AntiForensicFinding[]>();
    for (const sev of SEVERITY_ORDER) m.set(sev, []);
    for (const f of findings) {
      const sev: AntiForensicFinding["severity"] = SEVERITY_ORDER.includes(f.severity) ? f.severity : "info";
      m.get(sev)?.push(f);
    }
    return m;
  }, [findings]);

  const innocent = summary.innocent_explanations ?? summary.innocent_explanation ?? "";
  const lockedUsers = users.filter((u) => u.extractable === "present-locked").length;
  const loading = usersLoading || findingsLoading;

  if (loading) return <div className="p-8 text-muted text-sm animate-pulse">Loading structural observations…</div>;

  return (
    <div className="p-6 max-w-6xl mx-auto">
      {/* Header */}
      <div className="mb-5">
        <h1 className="text-xl font-bold mb-1 flex items-center gap-2">
          <span>🧩</span> Containers &amp; Structural Observations
          <span className="text-xs font-normal text-muted bg-panel-2 border border-line rounded px-2 py-0.5 ml-1">
            Tier 2 — Root
          </span>
        </h1>
        <p className="text-sm text-muted leading-relaxed">
          Secondary users, work profiles, cloned-app containers and other configuration facts read
          from the user manager and the data partition.
        </p>
      </div>

      {/* THE standing banner. Nothing in this view asserts intent. */}
      <div className="card p-4 mb-4 border-warn/40 bg-warn/5">
        <div className="text-sm font-semibold text-warn mb-1">
          These are observations about how the device is configured — not conclusions about anyone.
        </div>
        <p className="text-xs text-warn leading-relaxed">
          Nothing on this page asserts intent, concealment or guilt, and none of it should be
          summarised that way in a report. A work profile is issued by employers. A dual-app or
          cloned-messenger container is a shipped OEM feature used to run two accounts on one
          handset. A Secure Folder is marketed to ordinary consumers for photos and banking apps. A
          privacy-focused browser, an encrypted notes app or a VPN is a normal thing to install.
          Guest and second users exist so families can share a phone. Each finding below carries its
          own caveats and its innocent readings; read them as part of the finding, not as a
          disclaimer attached to it.
        </p>
      </div>

      {/* Verbatim engine paragraph, if one exists. */}
      {innocent && (
        <div className="card p-4 mb-4">
          <div className="text-[10px] uppercase tracking-wider text-muted mb-1">
            Innocent explanations — rendered verbatim from the engine's report
          </div>
          <p className="text-sm text-ink leading-relaxed whitespace-pre-line">{innocent}</p>
        </div>
      )}

      {users.length === 0 && findings.length === 0 ? (
        /* Honest empty state. */
        <div className="card p-6 max-w-3xl">
          <div className="text-warn font-semibold mb-2">No container or user data acquired</div>
          <p className="text-sm text-muted leading-relaxed">
            The user manager (<code className="text-ink">/data/system/users/</code>) and per-user data
            directories (<code className="text-ink">/data/user/&lt;id&gt;</code>) are readable only by
            the <strong>Tier-2 (root)</strong> collector. A Tier-0/Tier-1 acquisition cannot enumerate
            them at all, so an empty page here means <strong>not acquired</strong>.
          </p>
          <p className="text-sm text-muted leading-relaxed mt-2">
            It does <em>not</em> mean the device has a single user, and it does not mean no work
            profile, Secure Folder or cloned-app container exists. If the device was seized before
            first unlock, secondary-user data is credential-encrypted and cannot be read even with
            root — again a "could not read", not a "nothing there".
          </p>
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
            <StatCard n={users.length} label="Users / containers" />
            <StatCard n={lockedUsers} label="Present, locked" tone="text-deletion" />
            <StatCard n={findings.length} label="Observations" tone="text-accent" />
            <StatCard
              n={(grouped.get("critical") ?? []).length}
              label="Notable"
              tone="text-deletion"
            />
          </div>

          {/* Users / containers */}
          <h2 className="text-sm font-semibold text-ink mb-2">Users and containers</h2>
          {users.length === 0 ? (
            <div className="card p-4 mb-6 text-xs text-muted leading-relaxed">
              No user records were parsed. Observations below were derived from other sources; the
              absence of a user table is a collection gap, not a finding that the device has one
              user.
            </div>
          ) : (
            <div className="card overflow-auto mb-2">
              <table className="w-full text-sm">
                <thead>
                  <tr>
                    <th className="th w-16">User</th>
                    <th className="th">Name / type</th>
                    <th className="th w-40">Container kind</th>
                    <th className="th w-48">Likely OEM feature</th>
                    <th className="th w-56">Extractability</th>
                    <th className="th">Data directories</th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((u, i) => {
                    const ex = EXTRACTABILITY[u.extractable] ?? EXTRACTABILITY.unknown;
                    return (
                      <tr key={i}>
                        <td className="td font-mono text-xs align-top">{u.user_id}</td>
                        <td className="td align-top">
                          <div className="text-ink">{u.name || <span className="text-muted italic">unnamed</span>}</div>
                          <div className="text-[11px] text-muted font-mono">{u.user_type || "—"}</div>
                          <div className="mt-1">
                            <Chips items={u.flag_labels} empty="no flags recorded" />
                          </div>
                        </td>
                        <td className="td align-top text-xs">
                          {u.container_kind || <span className="text-muted italic">unclassified</span>}
                        </td>
                        <td className="td align-top text-xs">
                          {u.likely_feature ? (
                            <>
                              <span className="text-ink">{u.likely_feature}</span>
                              <div className="text-[10px] text-muted mt-0.5">
                                inferred mapping — approximate
                              </div>
                            </>
                          ) : (
                            <span className="text-muted italic">not mapped</span>
                          )}
                        </td>
                        <td className="td align-top">
                          <span className={`text-[10px] px-1.5 py-0.5 rounded ${ex.cls}`}>{ex.label}</span>
                          <div className="text-[11px] text-muted leading-relaxed mt-1">{ex.detail}</div>
                        </td>
                        <td className="td align-top">
                          <Chips items={u.data_dirs} empty="no directories recorded" />
                          <Caveats items={u.caveats} />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          <p className="text-xs text-muted mb-6 leading-relaxed">
            A locked container's size or file count says nothing about what is inside it, and its
            existence says nothing about why it was created. Report it as "present and not
            extractable at this acquisition".
          </p>

          {/* Findings grouped by severity */}
          <h2 className="text-sm font-semibold text-ink mb-2">Observations</h2>
          {findings.length === 0 ? (
            <div className="card p-4 text-xs text-muted leading-relaxed">
              No structural observations were recorded. That means nothing unusual was{" "}
              <em>observed</em> by the checks this build runs — it is not a finding that the device
              is unmodified, and it does not rule out containers or tooling the collector does not
              look for.
            </div>
          ) : (
            <div className="space-y-5">
              {SEVERITY_ORDER.map((sev) => {
                const rows = grouped.get(sev) ?? [];
                if (rows.length === 0) return null;
                const meta = SEVERITY_META[sev];
                return (
                  <div key={sev}>
                    <div className="flex items-center gap-2 mb-1">
                      <span className={`text-[10px] px-1.5 py-0.5 rounded ${meta.pill}`}>
                        {meta.label.toUpperCase()}
                      </span>
                      <span className="text-[11px] text-muted">
                        {rows.length} observation{rows.length === 1 ? "" : "s"}
                      </span>
                    </div>
                    <p className="text-[11px] text-muted mb-2 leading-relaxed">{meta.note}</p>
                    <div className="space-y-2">
                      {rows.map((f, i) => (
                        <div key={i} className="card p-3">
                          <div className="flex flex-wrap items-center gap-2 mb-1">
                            <span className={`text-[10px] px-1.5 py-0.5 rounded ${meta.pill}`}>
                              {meta.label}
                            </span>
                            <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-panel border border-line text-muted">
                              {f.kind}
                            </span>
                            <span className="text-sm text-ink font-medium break-all">{f.subject}</span>
                            <span className="ml-auto">
                              <ConfidenceBadge value={f.confidence} />
                            </span>
                          </div>
                          <p className="text-xs text-muted leading-relaxed">{f.detail}</p>
                          <div className="mt-1.5">
                            <div className="text-[10px] uppercase tracking-wider text-muted mb-0.5">
                              Evidence
                            </div>
                            <Chips items={f.evidence} empty="no evidence paths recorded" />
                          </div>
                          <Caveats items={f.caveats} label="Caveats and innocent readings" />
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {summary.note && (
            <div className="card p-3 mt-5 text-xs leading-relaxed">
              <div className="text-[10px] uppercase tracking-wider text-muted mb-1">
                Engine summary (verbatim)
              </div>
              <p className="text-ink whitespace-pre-line">{summary.note}</p>
            </div>
          )}
          {summary.caveats && summary.caveats.length > 0 && (
            <div className="card p-3 mt-3">
              <div className="text-[10px] uppercase tracking-wider text-muted mb-1">
                Dataset-level caveats
              </div>
              <Caveats items={summary.caveats} />
            </div>
          )}
        </>
      )}
    </div>
  );
}
