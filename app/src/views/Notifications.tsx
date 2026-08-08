/**
 * Notifications — the OS notification shade's retained history
 * (Tier 2, root; `adb shell dumpsys notification --history`).
 *
 * Forensic value and the honesty problem in one sentence: the notification service keeps a
 * circular ring buffer that can retain a message's title/text preview even after the sender app
 * itself has had the underlying message deleted — but the buffer is capped, so an empty or thin
 * result here proves nothing about what was actually shown on the device.
 */
import { useMemo, useState } from "react";
import { useDataset, fmtTs } from "../lib/hooks";
import { StatCard } from "../components/common";

/** A row parsed from `dumpsys notification --history` (engine: parsers/notification.py). */
export interface NotificationRecord {
  package: string;
  app_name: string;
  key: string;
  timestamp: string;
  title: string;
  text: string;
  priority: "none" | "min" | "low" | "default" | "high" | "max";
  is_comm: boolean;
}

const PRIORITY_META: Record<NotificationRecord["priority"], { label: string; cls: string }> = {
  max: { label: "MAX", cls: "bg-deletion/20 text-deletion border border-deletion/40" },
  high: { label: "HIGH", cls: "bg-warn/20 text-warn border border-warn/40" },
  default: { label: "DEFAULT", cls: "bg-recovered/20 text-recovered border border-recovered/40" },
  low: { label: "LOW", cls: "bg-panel-2 text-muted border border-line" },
  min: { label: "MIN", cls: "bg-panel-2 text-muted border border-line" },
  none: { label: "NONE", cls: "bg-panel-2 text-muted border border-line" },
};

function PriorityBadge({ value }: { value: string }) {
  const meta = PRIORITY_META[value as NotificationRecord["priority"]] ?? PRIORITY_META.default;
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded font-semibold whitespace-nowrap ${meta.cls}`}>
      {meta.label}
    </span>
  );
}

type CommFilter = "all" | "comm" | "other";

export function NotificationsView({ caseId }: { caseId: string }) {
  const { data, loading } = useDataset<NotificationRecord>(caseId, "notifications");
  const [query, setQuery] = useState("");
  const [commFilter, setCommFilter] = useState<CommFilter>("all");

  const commCount = useMemo(() => data.filter((n) => n.is_comm).length, [data]);
  const uniqueApps = useMemo(() => new Set(data.map((n) => n.package)).size, [data]);
  const urgentCount = useMemo(
    () => data.filter((n) => n.priority === "high" || n.priority === "max").length,
    [data]
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return data
      .filter((n) => {
        if (commFilter === "comm" && !n.is_comm) return false;
        if (commFilter === "other" && n.is_comm) return false;
        if (!q) return true;
        return (
          (n.title || "").toLowerCase().includes(q) ||
          (n.text || "").toLowerCase().includes(q) ||
          (n.app_name || "").toLowerCase().includes(q) ||
          (n.package || "").toLowerCase().includes(q)
        );
      })
      .sort((a, b) => (b.timestamp || "").localeCompare(a.timestamp || ""));
  }, [data, query, commFilter]);

  if (loading) return <div className="p-8 text-muted text-sm animate-pulse">Loading notification history…</div>;

  const Header = (
    <div className="mb-5">
      <h1 className="text-xl font-bold mb-1 flex items-center gap-2">
        <span>🔔</span> Notifications
        <span className="text-xs font-normal text-muted bg-panel-2 border border-line rounded px-2 py-0.5 ml-1">
          Tier 2 — Root
        </span>
      </h1>
      <p className="text-sm text-muted leading-relaxed">
        The notification shade's retained history, read from{" "}
        <code className="font-mono">dumpsys notification --history</code>. Title and body text can
        survive here after the sender app has had the underlying message deleted.
      </p>
    </div>
  );

  if (data.length === 0) {
    return (
      <div className="p-6 max-w-3xl mx-auto">
        {Header}
        <div className="card p-6 border-warn/40 bg-warn/5">
          <div className="text-warn font-semibold mb-2">No notification history in this case</div>
          <p className="text-sm text-muted leading-relaxed">
            Reading notification history requires a <strong>Tier-2 (root)</strong> acquisition with
            <code className="text-ink font-mono mx-1">dumpsys notification --history</code>
            support on the device. A Tier-0/Tier-1 acquisition, a device that denied the dump, or a
            build without history support will all leave this page empty.
          </p>
          <p className="text-sm text-muted leading-relaxed mt-2">
            <strong className="text-ink">An empty page is not evidence that no notifications
            occurred.</strong> It means this build could not read the ring buffer — nothing more.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 max-w-6xl mx-auto">
      {Header}

      {/* Honesty banner — governs every reading of this page. */}
      <div className="card p-4 mb-4 border-warn/40 bg-warn/5">
        <div className="text-sm font-semibold text-warn mb-1">
          This is what the shade retained, not a complete log
        </div>
        <p className="text-xs text-warn leading-relaxed">
          The OS keeps notification history in a capped ring buffer (typically the last 50–500
          entries across all apps). Older notifications are silently overwritten with no trace left
          behind, and windows the app chose not to post to the shade never appear here at all. A
          contact or topic missing from this list may still have been discussed at length — it is
          equally consistent with the buffer having rolled over.
        </p>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <StatCard n={data.length} label="Notifications retained" />
        <StatCard n={commCount} label="From communication apps" tone="text-accent" />
        <StatCard n={uniqueApps} label="Distinct apps" />
        <StatCard n={urgentCount} label="High / max priority" tone="text-deletion" />
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <input
          className="input max-w-sm"
          placeholder="Search title, text or app…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <div className="flex items-center gap-1 text-xs">
          {([
            ["all", "All"],
            ["comm", "Communication apps"],
            ["other", "Other apps"],
          ] as [CommFilter, string][]).map(([key, label]) => (
            <button
              key={key}
              onClick={() => setCommFilter(key)}
              className={`px-2.5 py-1 rounded border transition-colors ${
                commFilter === key
                  ? "bg-accent/15 text-accent border-accent/40"
                  : "border-line text-muted hover:text-ink"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        <span className="text-[11px] text-muted ml-auto">Sorted newest first</span>
      </div>

      <div className="card overflow-auto">
        <table className="w-full text-sm">
          <thead>
            <tr>
              <th className="th w-44">App</th>
              <th className="th w-40">Time (device clock)</th>
              <th className="th">Title / text</th>
              <th className="th w-24">Priority</th>
              <th className="th w-16">Comm</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={5} className="text-center py-8 text-muted text-xs">
                  No notifications match your filter.
                </td>
              </tr>
            ) : (
              filtered.map((n, i) => (
                <tr key={n.key || `${n.package}_${i}`}>
                  <td className="td align-top">
                    <div className="text-ink text-xs">{n.app_name || "Unknown app"}</div>
                    <div className="text-[10px] text-muted font-mono break-all">{n.package}</div>
                  </td>
                  <td className="td align-top font-mono text-xs">
                    {n.timestamp ? fmtTs(n.timestamp) : <span className="text-muted italic">not recorded</span>}
                  </td>
                  <td className="td align-top">
                    {n.title && <div className="text-ink text-xs font-medium break-words">{n.title}</div>}
                    {n.text ? (
                      <div className="text-xs text-muted leading-relaxed break-words mt-0.5">{n.text}</div>
                    ) : (
                      !n.title && <span className="text-muted italic text-xs">no preview text retained</span>
                    )}
                  </td>
                  <td className="td align-top">
                    <PriorityBadge value={n.priority} />
                  </td>
                  <td className="td align-top">
                    {n.is_comm ? (
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-live/15 text-live">comm</span>
                    ) : (
                      <span className="text-[10px] text-muted">—</span>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-muted mt-3 leading-relaxed">
        Deduplicated by notification key and capped by the collector at 500 entries. A key that
        looks re-used across apps is a re-issued/updated notification, not two separate events.
      </p>
    </div>
  );
}
