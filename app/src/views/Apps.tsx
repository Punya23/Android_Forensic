/**
 * Apps — installed-app inventory with investigative classification.
 *
 * Highlights "apps of interest" (messaging / social / crypto / dating) and especially
 * vault / anti-forensic apps, mirroring how commercial tools surface high-value apps.
 */
import { useMemo, useState } from "react";
import type { InstalledApp } from "../lib/types";
import { useDataset, fmtTs } from "../lib/hooks";
import { Filters, SectionHeader, EmptyState, StatCard } from "../components/common";

const CAT_STYLE: Record<string, string> = {
  messaging: "bg-accent/15 text-accent",
  social: "bg-accent/15 text-accent",
  crypto: "bg-recovered/15 text-recovered",
  dating: "bg-recovered/15 text-recovered",
  browser: "bg-panel-2 text-muted",
  cloud: "bg-recovered/15 text-recovered",
  anti_forensic: "bg-deletion/20 text-deletion",
  other: "bg-panel-2 text-muted",
};

function CategoryBadge({ category }: { category: string }) {
  const cls = CAT_STYLE[category] ?? CAT_STYLE.other;
  const label = category === "anti_forensic" ? "vault / anti-forensic" : category;
  return <span className={`text-[10px] px-1.5 py-0.5 rounded ${cls}`}>{label}</span>;
}

export function AppsView({ caseId }: { caseId: string }) {
  const { data, loading } = useDataset<InstalledApp>(caseId, "apps");
  const [query, setQuery] = useState("");
  const [onlyNotable, setOnlyNotable] = useState(true);
  const [hideSystem, setHideSystem] = useState(true);

  const filtered = useMemo(() => {
    const q = query.toLowerCase();
    return data
      .filter((a) => (onlyNotable ? a.notable : true))
      .filter((a) => (hideSystem ? !a.is_system : true))
      .filter(
        (a) =>
          !q ||
          a.package.toLowerCase().includes(q) ||
          (a.label || "").toLowerCase().includes(q) ||
          (a.friendly_name || "").toLowerCase().includes(q)
      )
      .sort((a, b) => Number(b.notable) - Number(a.notable) || (b.last_update || "").localeCompare(a.last_update || ""));
  }, [data, query, onlyNotable, hideSystem]);

  if (loading) return <div className="p-8 text-muted">Loading installed apps…</div>;
  if (data.length === 0)
    return (
      <EmptyState
        title="No app inventory acquired"
        detail="Installed-app inventory requires the Tier-1 Collector helper's full collection (dump_all). Enable it for a real device, or it was not run at this tier."
      />
    );

  const notable = data.filter((a) => a.notable);
  const antiForensic = data.filter((a) => a.category === "anti_forensic");
  const messaging = data.filter((a) => a.category === "messaging");

  return (
    <div className="p-6 h-full flex flex-col">
      <SectionHeader title="Installed Apps" sub={`${data.length} packages · ${notable.length} of interest`} />

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-4">
        <StatCard n={data.length} label="Total apps" />
        <StatCard n={messaging.length} label="Messaging apps" tone="text-accent" />
        <StatCard n={notable.length} label="Apps of interest" tone="text-recovered" />
        <StatCard n={antiForensic.length} label="Vault / anti-forensic" tone={antiForensic.length ? "text-deletion" : "text-ink"} />
      </div>

      <div className="flex flex-wrap items-center gap-4 mb-3">
        <Filters query={query} onQuery={setQuery} placeholder="Search app or package…" />
        <label className="flex items-center gap-1.5 text-xs text-muted cursor-pointer">
          <input type="checkbox" checked={onlyNotable} onChange={(e) => setOnlyNotable(e.target.checked)} />
          Notable only
        </label>
        <label className="flex items-center gap-1.5 text-xs text-muted cursor-pointer">
          <input type="checkbox" checked={hideSystem} onChange={(e) => setHideSystem(e.target.checked)} />
          Hide system apps
        </label>
      </div>

      <div className="card overflow-auto flex-1">
        <table className="w-full text-sm">
          <thead>
            <tr>
              <th className="th">App</th>
              <th className="th">Package</th>
              <th className="th w-28">Category</th>
              <th className="th w-24">Version</th>
              <th className="th w-40">Installed</th>
              <th className="th w-40">Updated</th>
              <th className="th w-24">Dangerous perms</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((a, i) => (
              <tr key={i} className={a.category === "anti_forensic" ? "bg-deletion/5" : ""}>
                <td className="td font-medium">{a.friendly_name || a.label || a.package}</td>
                <td className="td font-mono text-xs text-muted">{a.package}</td>
                <td className="td"><CategoryBadge category={a.category} /></td>
                <td className="td font-mono text-xs">{a.version_name || "—"}</td>
                <td className="td text-xs font-mono text-muted">{fmtTs(a.first_install)}</td>
                <td className="td text-xs font-mono text-muted">{fmtTs(a.last_update)}</td>
                <td className="td text-center">
                  {a.dangerous_granted.length > 0 ? (
                    <span title={a.dangerous_granted.join(", ")} className="text-carved font-medium">
                      {a.dangerous_granted.length}
                    </span>
                  ) : (
                    <span className="text-muted">0</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
