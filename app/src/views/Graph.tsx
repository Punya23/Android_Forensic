import { useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import type { CommunicationGraph, GraphNode } from "../lib/types";
import { SectionHeader, EmptyState } from "../components/common";

const CHANNEL_COLOR: Record<string, string> = {
  whatsapp: "#4fb477",
  telegram: "#5b9bd5",
  sms: "#d8a53c",
  call: "#d3625f",
  "app-db": "#8a939d",
  device: "#d8823c",
};

// Offline communication-network view. Nodes are laid out on a radial ring around the
// device-owner hub (deterministic, no physics/network needed); edge thickness encodes
// interaction volume. This is the "social graph / link analysis" a commercial suite shows.
export function GraphView({ caseId }: { caseId: string }) {
  const [graph, setGraph] = useState<CommunicationGraph | null>(null);
  const [hover, setHover] = useState<GraphNode | null>(null);

  useEffect(() => {
    api.dataset<CommunicationGraph>(caseId, "graph").then((g) => setGraph(g as any)).catch(() => setGraph(null));
  }, [caseId]);

  const layout = useMemo(() => {
    if (!graph) return null;
    const W = 900;
    const H = 560;
    const cx = W / 2;
    const cy = H / 2;
    const owner = graph.nodes.find((n) => n.type === "owner");
    const others = graph.nodes.filter((n) => n.type !== "owner");
    const R = Math.min(W, H) / 2 - 80;
    const pos: Record<string, { x: number; y: number }> = {};
    if (owner) pos[owner.id] = { x: cx, y: cy };
    others.forEach((n, i) => {
      const angle = (i / Math.max(others.length, 1)) * Math.PI * 2 - Math.PI / 2;
      // Heavier contacts sit slightly closer to the hub.
      const maxW = Math.max(...others.map((o) => o.weight), 1);
      const r = R * (1 - 0.25 * (n.weight / maxW));
      pos[n.id] = { x: cx + r * Math.cos(angle), y: cy + r * Math.sin(angle) };
    });
    return { W, H, pos, owner, others };
  }, [graph]);

  if (!graph) return <div className="p-8 text-muted">Loading communication graph…</div>;
  if (graph.nodes.length <= 1)
    return <EmptyState title="No communication network" detail="No messages or calls were attributable to participants." />;

  const { W, H, pos } = layout!;
  const maxEdge = Math.max(...graph.edges.map((e) => e.weight), 1);
  const maxNode = Math.max(...graph.nodes.filter((n) => n.type !== "owner").map((n) => n.weight), 1);

  return (
    <div className="p-6 h-full flex flex-col">
      <SectionHeader
        title="Communication Network"
        sub={`${graph.stats.participants} participants · ${graph.stats.interactions} interactions · ${graph.stats.channels.join(", ")}`}
      />
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-4 flex-1 min-h-0">
        <div className="lg:col-span-3 card p-2 relative overflow-hidden">
          <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-full">
            {graph.edges.map((e, i) => {
              const a = pos[e.source];
              const b = pos[e.target];
              if (!a || !b) return null;
              const color = CHANNEL_COLOR[e.channels[0]] ?? "#8a939d";
              return (
                <line
                  key={i}
                  x1={a.x} y1={a.y} x2={b.x} y2={b.y}
                  stroke={color}
                  strokeOpacity={0.5}
                  strokeWidth={1 + (e.weight / maxEdge) * 5}
                />
              );
            })}
            {graph.nodes.map((n) => {
              const p = pos[n.id];
              if (!p) return null;
              const isOwner = n.type === "owner";
              const r = isOwner ? 26 : 8 + (n.weight / maxNode) * 16;
              const color = isOwner ? "#d8823c" : CHANNEL_COLOR[n.channels[0]] ?? "#8a939d";
              return (
                <g key={n.id} onMouseEnter={() => setHover(n)} onMouseLeave={() => setHover(null)} className="cursor-pointer">
                  <circle cx={p.x} cy={p.y} r={r} fill={color} fillOpacity={isOwner ? 0.95 : 0.85} stroke="#0d0f12" strokeWidth={2} />
                  <text x={p.x} y={p.y + r + 12} textAnchor="middle" fontSize={isOwner ? 13 : 11}
                        fill="#e9ebe6" fontWeight={isOwner ? 700 : 400}>
                    {n.label.length > 18 ? n.label.slice(0, 17) + "…" : n.label}
                  </text>
                </g>
              );
            })}
          </svg>
          {hover && (
            <div className="absolute top-3 left-3 card p-2.5 text-xs bg-panel-2/95 pointer-events-none">
              <div className="font-semibold text-ink">{hover.label}</div>
              <div className="text-muted mt-0.5">{hover.weight} interaction{hover.weight === 1 ? "" : "s"}</div>
              <div className="text-muted">via {hover.channels.join(", ")}</div>
            </div>
          )}
        </div>
        <div className="card overflow-auto">
          <div className="px-3 py-2 text-[11px] uppercase tracking-wider text-muted border-b border-line">Key participants</div>
          {graph.stats.top_contacts.map((t, i) => (
            <div key={i} className="px-3 py-2 border-b border-line/50 last:border-0">
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium truncate">{t.label}</span>
                <span className="text-xs font-mono text-accent">{t.weight}</span>
              </div>
              <div className="flex gap-1 mt-1">
                {t.channels.map((c) => (
                  <span key={c} className="text-[9px] px-1 rounded" style={{ background: (CHANNEL_COLOR[c] ?? "#555") + "33", color: CHANNEL_COLOR[c] ?? "#aaa" }}>
                    {c}
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
