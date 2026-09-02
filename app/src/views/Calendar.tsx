import { useMemo, useState } from "react";
import type { CalendarEvent } from "../lib/types";
import { useDataset, fmtTs } from "../lib/hooks";
import { Filters, SectionHeader, EmptyState } from "../components/common";

export function CalendarView({ caseId }: { caseId: string }) {
  const { data, loading } = useDataset<CalendarEvent>(caseId, "calendar");
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    const q = query.toLowerCase();
    return data
      .filter(
        (e) =>
          !q ||
          e.title.toLowerCase().includes(q) ||
          (e.location || "").toLowerCase().includes(q) ||
          (e.description || "").toLowerCase().includes(q)
      )
      .sort((a, b) => (b.dtstart || "").localeCompare(a.dtstart || ""));
  }, [data, query]);

  if (loading) return <div className="p-8 text-muted">Loading calendar…</div>;
  if (data.length === 0)
    return (
      <EmptyState
        dataset="calendar"
        title="No calendar events acquired"
        detail="Calendar events require the Tier-1 Collector helper's full collection (READ_CALENDAR). This device was acquired without it."
      />
    );

  return (
    <div className="p-6 h-full flex flex-col">
      <SectionHeader title="Calendar" sub={`${data.length} event(s)`} />
      <Filters query={query} onQuery={setQuery} placeholder="Search title, location, or notes…" />
      <div className="card overflow-auto flex-1">
        <table className="w-full text-sm">
          <thead>
            <tr>
              <th className="th w-44">Start</th>
              <th className="th">Title</th>
              <th className="th">Location</th>
              <th className="th w-32">Calendar</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((e, i) => (
              <tr key={i}>
                <td className="td text-xs font-mono">{e.all_day ? (e.dtstart || "").slice(0, 10) : fmtTs(e.dtstart)}</td>
                <td className="td font-medium">{e.title}</td>
                <td className="td text-muted">{e.location || "—"}</td>
                <td className="td text-xs text-muted">{e.calendar || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
