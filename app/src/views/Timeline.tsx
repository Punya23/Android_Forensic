import { useEffect, useMemo, useState } from "react";
import {
  MessageSquare,
  Send,
  Paperclip,
  Phone,
  Image,
  FolderOpen,
  MapPin,
  Calendar,
  Bell,
  Bluetooth,
  Link2,
  Wifi,
  RadioTower,
  Hourglass,
  Search,
  Circle,
  ArrowUpRight,
  type LucideIcon,
} from "lucide-react";
import type { TimelineEvent } from "../lib/types";
import { useDataset, fmtTs } from "../lib/hooks";
import { ConfidenceBadge } from "../components/Badges";
import { Filters, SectionHeader, EmptyState } from "../components/common";
import type { ViewKey } from "../components/Sidebar";

// Every event kind the engine's build_timeline() can emit. An unlisted kind still
// renders (see the fallback at the row), but it loses its icon and colour, so anything
// added on the engine side belongs here too.
const KIND_META: Record<string, { icon: LucideIcon; color: string }> = {
  message: { icon: MessageSquare, color: "border-recovered" },
  telegram_message: { icon: Send, color: "border-recovered" },
  telegram_media: { icon: Paperclip, color: "border-carved" },
  call: { icon: Phone, color: "border-live" },
  media: { icon: Image, color: "border-carved" },
  media_inventory: { icon: FolderOpen, color: "border-carved" },
  location: { icon: MapPin, color: "border-accent" },
  calendar: { icon: Calendar, color: "border-live" },
  notification: { icon: Bell, color: "border-accent" },
  bluetooth: { icon: Bluetooth, color: "border-accent" },
  // A bond event is a pairing record, not a connection — the engine words the summary
  // accordingly; the distinct icon keeps it from reading as live connectivity.
  bluetooth_bond: { icon: Link2, color: "border-recovered" },
  // OPP file transfers — unlike a bond record, a transfer requires an active link at
  // that moment, so it carries a real wall-clock time (see build_transfer_timeline()).
  bluetooth_transfer: { icon: ArrowUpRight, color: "border-live" },
  celltower: { icon: Wifi, color: "border-muted" },
  wifi: { icon: RadioTower, color: "border-accent" },
  screen: { icon: Hourglass, color: "border-live" },
  search: { icon: Search, color: "border-accent" },
};

// Where each event kind's underlying record actually lives — the same "go to" jump
// Tagged.tsx already offers for its own kinds, extended to cover every kind the
// timeline can emit. Switches view, same as Tagged.tsx/GlobalSearch.tsx already do;
// it never claims to scroll to the exact row, because the timeline's own `ref` field
// is a source filename for most kinds (not a stable per-row id), and this app doesn't
// pretend otherwise anywhere else it does the same kind of jump.
const KIND_TO_VIEW: Record<string, ViewKey> = {
  message: "messages",
  telegram_message: "telegram",
  telegram_media: "telegram",
  call: "calls",
  media: "media",
  media_inventory: "mediainv",
  location: "loctrace",
  calendar: "calendar",
  notification: "notifications",
  bluetooth: "bluetooth",
  bluetooth_bond: "bluetooth",
  bluetooth_transfer: "bluetooth",
  celltower: "celltower",
  wifi: "wifi_live",
  screen: "screentime",
  search: "search",
};

// A device with years of history can produce many thousands of timeline events —
// capped like Aleapp.tsx's table, with the same disclosed "show all" control.
const TABLE_CAP = 1500;

export function TimelineView({ caseId, setView }: { caseId: string; setView: (v: ViewKey) => void }) {
  const { data, loading } = useDataset<TimelineEvent>(caseId, "timeline");
  const [query, setQuery] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [kinds, setKinds] = useState<Set<string>>(new Set());
  const [showAll, setShowAll] = useState(false);

  const filtered = useMemo(() => {
    const q = query.toLowerCase();
    return data.filter((e) => {
      if (q && !e.summary.toLowerCase().includes(q)) return false;
      if (kinds.size && !kinds.has(e.kind)) return false;
      if (from && e.timestamp < from) return false;
      if (to && e.timestamp > to + "T23:59:59") return false;
      return true;
    });
  }, [data, query, from, to, kinds]);

  useEffect(() => setShowAll(false), [query, from, to, kinds]);
  const visible = showAll ? filtered : filtered.slice(0, TABLE_CAP);

  if (loading) return <div className="p-8 text-muted">Loading timeline…</div>;
  if (data.length === 0)
    return <EmptyState dataset="timeline" title="No timeline events" detail="Timeline needs timestamped artifacts (messages, calls, geotagged media)." />;

  const allKinds = Array.from(new Set(data.map((e) => e.kind)));

  function toggleKind(k: string) {
    const next = new Set(kinds);
    next.has(k) ? next.delete(k) : next.add(k);
    setKinds(next);
  }

  return (
    <div className="p-6 h-full flex flex-col">
      <SectionHeader title="Timeline" sub={`${data.length} events across calls, messages, media & locations`} />
      <Filters query={query} onQuery={setQuery} from={from} to={to} onFrom={setFrom} onTo={setTo} placeholder="Search events…" />
      <div className="flex flex-wrap gap-2 mb-4">
        {allKinds.map((k) => {
          const Icon = KIND_META[k]?.icon;
          return (
            <button
              key={k}
              onClick={() => toggleKind(k)}
              className={`px-3 py-1 rounded-full text-xs border transition-colors ${
                kinds.has(k) || kinds.size === 0 ? "border-accent/60 text-ink" : "border-line text-muted"
              }`}
            >
              {Icon && <Icon className="inline h-3.5 w-3.5" strokeWidth={1.75} aria-hidden />} {k}
            </button>
          );
        })}
      </div>
      <div className="overflow-auto flex-1 pl-2">
        <div className="border-l-2 border-line ml-2">
          {visible.map((e, i) => {
            const meta = KIND_META[e.kind] ?? { icon: Circle, color: "border-muted" };
            const target = KIND_TO_VIEW[e.kind];
            return (
              <div key={i} className="relative pl-6 pb-4 group">
                <div className={`absolute -left-[7px] top-1 h-3 w-3 rounded-full bg-panel-2 border-2 ${meta.color}`} />
                <div className="flex items-center gap-2 text-xs text-muted font-mono">
                  {fmtTs(e.timestamp)}
                  {e.confidence !== "live" && <ConfidenceBadge c={e.confidence} />}
                  {target && (
                    <button
                      className="text-[11px] text-recovered opacity-0 group-hover:opacity-100 hover:underline ml-auto"
                      onClick={() => setView(target)}
                      title={`Open the ${target} view — the timeline doesn't pin an exact row, only which view holds this record`}
                    >
                      go to {target}{" "}
                      <ArrowUpRight className="inline h-3.5 w-3.5" strokeWidth={1.75} aria-hidden />
                    </button>
                  )}
                </div>
                <div className="text-sm mt-0.5">
                  <span className="mr-1.5">
                    <meta.icon className="inline h-3.5 w-3.5" strokeWidth={1.75} aria-hidden />
                  </span>
                  {e.summary}
                </div>
              </div>
            );
          })}
        </div>
        {!showAll && filtered.length > TABLE_CAP && (
          <div className="text-center py-3">
            <button className="btn-ghost text-xs" onClick={() => setShowAll(true)}>
              Showing first {TABLE_CAP.toLocaleString()} of {filtered.length.toLocaleString()} events — click to show all
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
