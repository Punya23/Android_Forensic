import { useMemo, useState } from "react";

/** A timestamp that can be a real date: epoch-zero and far-future stamps are missing data, not events. */
export function plausibleTime(iso: string): number | null {
  const t = Date.parse(iso);
  const year = new Date(t).getUTCFullYear();
  return Number.isFinite(t) && year >= 2005 && year <= new Date().getUTCFullYear() + 1 ? t : null;
}

/**
 * Activity terrain: one ridge per day (newest in front), 24 hourly columns, height = how many
 * dated events fell in that hour. Drawn from the case's own timeline; stamps that cannot be real
 * dates are left out and counted by the caller.
 */
export function ActivityTerrain({ stamps }: { stamps: string[] }) {
  const rows = useMemo(() => {
    const byDay = new Map<string, number[]>();
    for (const s of stamps) {
      const t = plausibleTime(s);
      if (t === null) continue;
      const d = new Date(t);
      const key = d.toISOString().slice(0, 10);
      const hours = byDay.get(key) ?? new Array(24).fill(0);
      hours[d.getUTCHours()] += 1;
      byDay.set(key, hours);
    }
    return [...byDay.entries()].sort(([a], [b]) => a.localeCompare(b)).slice(-18);
  }, [stamps]);

  if (!rows.length) return <div className="text-xs text-muted py-10 text-center">No dated events in this case.</div>;
  const W = 640;
  const H = 190;
  const max = Math.max(1, ...rows.flatMap(([, h]) => h));
  const step = rows.length > 1 ? 110 / (rows.length - 1) : 0;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-44" preserveAspectRatio="none" role="img" aria-label="Events per hour, one ridge per day">
      {rows.map(([day, hours], r) => {
        const base = 60 + r * step;
        const pts = hours.map((v, h) => [(h / 23) * W, base - (v / max) * 52] as const);
        const line = pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
        const front = r / Math.max(rows.length - 1, 1);
        return (
          <g key={day}>
            <path d={`${line} L${W},${H} L0,${H} Z`} fill="rgb(var(--color-panel))" fillOpacity="0.92" />
            <path d={line} fill="none" stroke="rgb(var(--color-accent))" strokeOpacity={0.25 + 0.65 * front} strokeWidth="1.1" vectorEffect="non-scaling-stroke" />
          </g>
        );
      })}
    </svg>
  );
}

export interface FlowPerson {
  label: string;
  weight: number;
  channels: string[];
}

/**
 * Communication flow: the device on the left fans out in curves to one lane per top participant;
 * each lane's bar is that person's share of recorded interactions. Hover a lane for its detail.
 */
export function FlowFeed({ people, total }: { people: FlowPerson[]; total: number }) {
  const [hover, setHover] = useState<number | null>(null);
  if (!people.length) return <div className="text-xs text-muted py-16 text-center">No participants with recorded calls or messages.</div>;
  const W = 640;
  const H = 250;
  const max = Math.max(...people.map((p) => p.weight), 1);
  const lane = (i: number) => 18 + (people.length > 1 ? (i * (H - 36)) / (people.length - 1) : (H - 36) / 2);
  const active = people[hover ?? 0];
  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-64" role="img" aria-label="Interactions per participant">
        {people.map((p, i) => {
          const y = lane(i);
          const on = hover === null ? i === 0 : hover === i;
          return (
            <g key={i} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} className="cursor-default">
              {[-6, 0, 6].map((o) => (
                <path
                  key={o}
                  d={`M18,${H / 2 + o} C190,${H / 2 + o} 190,${y + o / 3} 300,${y + o / 3}`}
                  fill="none"
                  stroke="rgb(var(--color-accent))"
                  strokeOpacity={on ? 0.7 : 0.16}
                  strokeWidth="1"
                />
              ))}
              <rect x="300" y={y - 3} width={Math.max(6, (p.weight / max) * 250)} height="6" rx="3" fill="rgb(var(--color-accent))" fillOpacity={on ? 0.95 : 0.45} />
              <rect x="296" y={y - 12} width="340" height="24" fill="transparent" />
            </g>
          );
        })}
        <circle cx="18" cy={H / 2} r="4" fill="rgb(var(--color-accent))" />
      </svg>
      <div className="absolute right-2 top-2 w-52 rounded-xl border border-ink/10 bg-panel/85 backdrop-blur p-3 text-sm">
        <div className="font-medium truncate">{active.label}</div>
        <div className="text-[11px] text-muted mt-0.5">{active.channels.join(" · ") || "channel unknown"}</div>
        <div className="mt-2 text-xs text-muted">
          {active.weight} interaction{active.weight === 1 ? "" : "s"}
          {total > 0 && ` · ${Math.round((active.weight / total) * 100)}% of all`}
        </div>
        <div className="mt-1.5 h-1 rounded-full bg-ink/10 overflow-hidden">
          <div className="h-full bg-accent" style={{ width: `${Math.round((active.weight / max) * 100)}%` }} />
        </div>
      </div>
    </div>
  );
}

/** A row of ticks, `value / max` of them lit — the 'system health' strip from the reference. */
export function TickMeter({ value, max, ticks = 28 }: { value: number; max: number; ticks?: number }) {
  const lit = max > 0 ? Math.max(value > 0 ? 1 : 0, Math.round((value / max) * ticks)) : 0;
  return (
    <div className="flex items-end gap-[3px] h-4" aria-hidden>
      {Array.from({ length: ticks }, (_, i) => (
        <span key={i} className={`w-[2px] rounded-full ${i < lit ? "h-4 bg-accent" : "h-2 bg-ink/20"}`} />
      ))}
    </div>
  );
}
