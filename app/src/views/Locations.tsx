import { useMemo, useState } from "react";
import type { LocationPoint } from "../lib/types";
import { useDataset, fmtTs } from "../lib/hooks";
import { SectionHeader, EmptyState } from "../components/common";

// Offline-safe map: an SVG scatter plot of GPS points in their bounding box. No external
// tile server (the tool must work with zero network at a scene). Points are colour-coded
// by source (EXIF photo vs dumpsys last-known-fix).
export function LocationsView({ caseId }: { caseId: string }) {
  const { data, loading } = useDataset<LocationPoint>(caseId, "locations");
  const [hover, setHover] = useState<LocationPoint | null>(null);

  const bounds = useMemo(() => {
    if (data.length === 0) return null;
    const lats = data.map((p) => p.latitude);
    const lons = data.map((p) => p.longitude);
    const pad = 0.02;
    return {
      minLat: Math.min(...lats) - pad,
      maxLat: Math.max(...lats) + pad,
      minLon: Math.min(...lons) - pad,
      maxLon: Math.max(...lons) + pad,
    };
  }, [data]);

  if (loading) return <div className="p-8 text-muted">Loading locations…</div>;
  if (data.length === 0)
    return <EmptyState title="No location data" detail="No GPS EXIF in pulled photos and no last-known fixes from dumpsys location." />;

  const W = 800;
  const H = 460;
  function project(p: LocationPoint) {
    if (!bounds) return { x: 0, y: 0 };
    const x = ((p.longitude - bounds.minLon) / (bounds.maxLon - bounds.minLon)) * W;
    const y = H - ((p.latitude - bounds.minLat) / (bounds.maxLat - bounds.minLat)) * H;
    return { x, y };
  }

  return (
    <div className="p-6 h-full flex flex-col">
      <SectionHeader title="Locations" sub={`${data.length} points · offline plot (no network)`} />
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 flex-1 min-h-0">
        <div className="lg:col-span-2 card p-3 relative">
          <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-full bg-panel rounded">
            {/* grid */}
            {Array.from({ length: 9 }).map((_, i) => (
              <line key={`v${i}`} x1={(i * W) / 8} y1={0} x2={(i * W) / 8} y2={H} stroke="#2b323a" strokeWidth={1} />
            ))}
            {Array.from({ length: 6 }).map((_, i) => (
              <line key={`h${i}`} x1={0} y1={(i * H) / 5} x2={W} y2={(i * H) / 5} stroke="#2b323a" strokeWidth={1} />
            ))}
            {data.map((p, i) => {
              const { x, y } = project(p);
              const isExif = p.source === "exif";
              return (
                <g key={i} onMouseEnter={() => setHover(p)} onMouseLeave={() => setHover(null)} className="cursor-pointer">
                  <circle cx={x} cy={y} r={hover === p ? 9 : 6} fill={isExif ? "#5b9bd5" : "#d8823c"} fillOpacity={0.85} stroke="#0d0f12" strokeWidth={1.5} />
                </g>
              );
            })}
          </svg>
          {hover && (
            <div className="absolute top-4 left-4 card p-2 text-xs bg-panel-2/95 pointer-events-none">
              <div className="font-mono">{hover.latitude.toFixed(5)}, {hover.longitude.toFixed(5)}</div>
              <div className="text-muted">{hover.source} · {hover.label}</div>
            </div>
          )}
          <div className="absolute bottom-4 right-4 flex gap-3 text-[10px] text-muted">
            <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-recovered" /> EXIF photo</span>
            <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-accent" /> last-known fix</span>
          </div>
        </div>
        <div className="card overflow-auto">
          <table className="w-full text-sm">
            <thead>
              <tr>
                <th className="th">Coordinates</th>
                <th className="th">Source</th>
              </tr>
            </thead>
            <tbody>
              {data.map((p, i) => (
                <tr key={i} className="hover:bg-panel cursor-pointer" onMouseEnter={() => setHover(p)}>
                  <td className="td font-mono text-xs">
                    {p.latitude.toFixed(5)}, {p.longitude.toFixed(5)}
                    <div className="text-muted/60 text-[10px]">{fmtTs(p.timestamp)}</div>
                  </td>
                  <td className="td text-xs">{p.source}<div className="text-muted/60">{p.label}</div></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
