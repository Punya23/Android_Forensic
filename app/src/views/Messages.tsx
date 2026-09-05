import { useEffect, useMemo, useState } from "react";
import type { Message } from "../lib/types";
import { useDataset, fmtTs } from "../lib/hooks";
import { ConfidenceBadge } from "../components/Badges";
import { TagButton } from "../lib/tagStore";
import { Filters, SectionHeader, EmptyState } from "../components/common";

const APP_COLORS: Record<string, string> = {
  whatsapp: "text-live",
  telegram: "text-recovered",
  sms: "text-warn",
  recovered: "text-carved",
};

// A real device's SMS/WhatsApp/recovered history can run into the tens of thousands of
// rows — rendering all of them into one unvirtualized table locks up the tab. Capped
// like Aleapp.tsx's table, with the same "N of M — click to show all" control so the
// cap is disclosed rather than silently hiding rows.
const TABLE_CAP = 1000;

export function MessagesView({ caseId }: { caseId: string }) {
  const { data, loading } = useDataset<Message>(caseId, "messages");
  const [query, setQuery] = useState("");
  const [showDeletedOnly, setShowDeletedOnly] = useState(false);
  const [app, setApp] = useState<string>("all");
  const [showAll, setShowAll] = useState(false);

  const apps = useMemo(() => Array.from(new Set(data.map((m) => m.app))).sort(), [data]);

  const filtered = useMemo(() => {
    const q = query.toLowerCase();
    return data.filter((m) => {
      if (showDeletedOnly && m.confidence === "live") return false;
      if (app !== "all" && m.app !== app) return false;
      if (!q) return true;
      return (
        m.body.toLowerCase().includes(q) ||
        m.sender.toLowerCase().includes(q) ||
        m.app.toLowerCase().includes(q)
      );
    });
  }, [data, query, showDeletedOnly, app]);

  useEffect(() => setShowAll(false), [query, showDeletedOnly, app]);
  const visible = showAll ? filtered : filtered.slice(0, TABLE_CAP);

  const deletedCount = data.filter((m) => m.confidence !== "live").length;

  if (loading) return <div className="p-8 text-muted">Loading messages…</div>;
  if (data.length === 0)
    return <EmptyState dataset="messages" title="No messages" detail="No chat exports were ingested and no chat databases yielded rows." />;

  return (
    <div className="p-6 h-full flex flex-col">
      <SectionHeader
        title="Messages"
        sub={`${data.length} total · ${deletedCount} recovered/deleted`}
        right={
          <label className="flex items-center gap-2 text-sm text-muted cursor-pointer">
            <input type="checkbox" checked={showDeletedOnly} onChange={(e) => setShowDeletedOnly(e.target.checked)} />
            deleted only
          </label>
        }
      />
      <div className="flex flex-wrap items-center gap-2 mb-2">
        <button
          onClick={() => setApp("all")}
          className={`px-2.5 py-1 rounded-full text-xs border ${app === "all" ? "border-accent bg-accent/15 text-accent" : "border-line text-muted"}`}
        >
          all
        </button>
        {apps.map((a) => (
          <button
            key={a}
            onClick={() => setApp(a)}
            className={`px-2.5 py-1 rounded-full text-xs border ${app === a ? "border-accent bg-accent/15 text-accent" : "border-line text-muted"} ${APP_COLORS[a] ?? ""}`}
          >
            {a} <span className="opacity-60">{data.filter((m) => m.app === a).length}</span>
          </button>
        ))}
      </div>
      <Filters query={query} onQuery={setQuery} placeholder="Search message text, sender…" />
      <div className="card overflow-auto flex-1">
        <table className="w-full text-sm">
          <thead>
            <tr>
              <th className="th w-8"></th>
              <th className="th w-40">Time</th>
              <th className="th w-24">App</th>
              <th className="th w-36">Sender</th>
              <th className="th">Message</th>
              <th className="th w-28">Confidence</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((m, i) => (
              <tr key={i} className={m.confidence !== "live" ? "bg-carved/5" : ""}>
                <td className="td"><TagButton refId={`message:${i}`} kind="message" label={`${m.sender}: ${m.body.slice(0, 40)}`} /></td>
                <td className="td font-mono text-xs text-muted whitespace-nowrap">{fmtTs(m.timestamp)}</td>
                <td className="td">
                  <span className={APP_COLORS[m.app] ?? "text-ink"}>{m.app}</span>
                </td>
                <td className="td">{m.sender}</td>
                <td className="td">
                  <div className="whitespace-pre-wrap">{highlight(m.body, query)}</div>
                  {m.provenance && (
                    <div className="text-[10px] text-muted/70 font-mono mt-0.5">{m.provenance}</div>
                  )}
                </td>
                <td className="td"><ConfidenceBadge c={m.confidence} /></td>
              </tr>
            ))}
          </tbody>
        </table>
        {!showAll && filtered.length > TABLE_CAP && (
          <div className="text-center py-3">
            <button className="btn-ghost text-xs" onClick={() => setShowAll(true)}>
              Showing first {TABLE_CAP.toLocaleString()} of {filtered.length.toLocaleString()} messages — click to show all
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function highlight(text: string, q: string) {
  if (!q) return text;
  const idx = text.toLowerCase().indexOf(q.toLowerCase());
  if (idx < 0) return text;
  return (
    <>
      {text.slice(0, idx)}
      <mark className="bg-accent/40 text-ink rounded px-0.5">{text.slice(idx, idx + q.length)}</mark>
      {text.slice(idx + q.length)}
    </>
  );
}
