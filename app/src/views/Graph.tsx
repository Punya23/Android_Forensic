import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { X } from "lucide-react";
import { api } from "../lib/api";
import type { CommunicationGraph, GraphNode } from "../lib/types";
import { SectionHeader } from "../components/common";
import { DatasetEmpty } from "../lib/capabilities";

const CHANNEL_COLOR: Record<string, string> = {
  whatsapp: "#4fb477",
  telegram: "#5b9bd5",
  sms: "#d8a53c",
  call: "#d3625f",
  instagram: "#c25ec9",
  snapchat: "#e0c53c",
  "app-db": "#8a939d",
  device: "#d8823c",
};
function channelColor(ch: string): string {
  if (CHANNEL_COLOR[ch]) return CHANNEL_COLOR[ch];
  // app:<name> channels (e.g. "app:chatcache") get a stable colour derived from the name
  // rather than falling back to one shared grey for every unrecognised channel — with
  // dozens of discovered-app channels in a real case, grey-for-everything is exactly the
  // "doesn't look right" flatness this view was rebuilt to fix.
  let h = 0;
  for (let i = 0; i < ch.length; i++) h = (h * 31 + ch.charCodeAt(i)) >>> 0;
  return `hsl(${h % 360}, 55%, 60%)`;
}

type Vec = { x: number; y: number };
type SimNode = Vec & { vx: number; vy: number; pinned: boolean };

const W = 1000;
const H = 640;
const ZOOM_MIN = 0.15;
const ZOOM_MAX = 4;

/** Deterministic initial layout: a ring, so nodes start apart rather than stacked at the
 * origin (which the repulsion force can't meaningfully push apart from a shared point). */
function seedPositions(nodes: GraphNode[]): Record<string, SimNode> {
  const pos: Record<string, SimNode> = {};
  const others = nodes.filter((n) => n.type !== "owner");
  const R = Math.min(W, H) * 0.32;
  others.forEach((n, i) => {
    const a = (i / Math.max(others.length, 1)) * Math.PI * 2;
    pos[n.id] = { x: W / 2 + R * Math.cos(a), y: H / 2 + R * Math.sin(a), vx: 0, vy: 0, pinned: false };
  });
  const owner = nodes.find((n) => n.type === "owner");
  if (owner) pos[owner.id] = { x: W / 2, y: H / 2, vx: 0, vy: 0, pinned: true };
  return pos;
}

/** One tick of a hand-rolled force simulation: nodes repel each other (so labels don't
 * overlap), edges pull their endpoints together (so connected pairs end up near each
 * other), everything is drawn weakly toward the centre (so the graph doesn't drift off
 * canvas), and velocity damps each tick (so it settles instead of oscillating forever).
 * No dependency pulled in for this — a hundred-node graph is cheap enough that a plain
 * O(n²) repulsion pass runs in well under a frame budget. */
function tick(positions: Record<string, SimNode>, edges: { source: string; target: string }[], ids: string[]) {
  const REPULSION = 18000;
  const SPRING = 0.02;
  const REST_LENGTH = 90;
  const CENTER_PULL = 0.01;
  const DAMPING = 0.82;

  for (const id of ids) {
    const n = positions[id];
    if (!n || n.pinned) continue;
    let fx = 0;
    let fy = 0;
    for (const other of ids) {
      if (other === id) continue;
      const o = positions[other];
      if (!o) continue;
      let dx = n.x - o.x;
      let dy = n.y - o.y;
      let d2 = dx * dx + dy * dy;
      if (d2 < 1) d2 = 1;
      const f = REPULSION / d2;
      const d = Math.sqrt(d2);
      fx += (dx / d) * f;
      fy += (dy / d) * f;
    }
    fx += (W / 2 - n.x) * CENTER_PULL;
    fy += (H / 2 - n.y) * CENTER_PULL;
    n.vx = (n.vx + fx) * DAMPING;
    n.vy = (n.vy + fy) * DAMPING;
  }
  for (const e of edges) {
    const a = positions[e.source];
    const b = positions[e.target];
    if (!a || !b) continue;
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const d = Math.max(Math.sqrt(dx * dx + dy * dy), 1);
    const stretch = d - REST_LENGTH;
    const fx = (dx / d) * stretch * SPRING;
    const fy = (dy / d) * stretch * SPRING;
    if (!a.pinned) {
      a.vx += fx;
      a.vy += fy;
    }
    if (!b.pinned) {
      b.vx -= fx;
      b.vy -= fy;
    }
  }
  let energy = 0;
  for (const id of ids) {
    const n = positions[id];
    if (!n || n.pinned) continue;
    n.x += n.vx;
    n.y += n.vy;
    energy += n.vx * n.vx + n.vy * n.vy;
  }
  return energy;
}

export function GraphView({ caseId }: { caseId: string }) {
  const [graph, setGraph] = useState<CommunicationGraph | null>(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<GraphNode | null>(null);
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [settled, setSettled] = useState(false);
  // Ids of nodes currently expanded to show their per-channel sub-nodes.
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
  const [, forceRender] = useState(0);

  const svgRef = useRef<SVGSVGElement>(null);
  const positionsRef = useRef<Record<string, SimNode>>({});
  const rafRef = useRef<number | null>(null);
  const viewRef = useRef({ x: 0, y: 0, k: 1 });
  const dragRef = useRef<{ kind: "node" | "pan"; id?: string; startClientX: number; startClientY: number; startView: Vec } | null>(null);

  useEffect(() => {
    setLoading(true);
    api
      .dataset<CommunicationGraph>(caseId, "graph")
      .then((g) => setGraph((g as CommunicationGraph)?.nodes ? (g as CommunicationGraph) : null))
      .catch(() => setGraph(null))
      .finally(() => setLoading(false));
  }, [caseId]);

  // (Re)seed the simulation whenever the case's graph data changes, and run it until it
  // settles (or a hard tick cap, so a pathological graph can't spin the CPU forever).
  const restart = useCallback(() => {
    if (!graph) return;
    positionsRef.current = seedPositions(graph.nodes);
    viewRef.current = { x: 0, y: 0, k: 1 };
    setSettled(false);
    setExpandedIds(new Set());
    let ticks = 0;
    const ids = graph.nodes.map((n) => n.id);
    const edges = graph.edges;
    const step = () => {
      const energy = tick(positionsRef.current, edges, ids);
      ticks += 1;
      forceRender((v) => v + 1);
      if (energy > 0.05 && ticks < 500) {
        rafRef.current = requestAnimationFrame(step);
      } else {
        setSettled(true);
        rafRef.current = null;
      }
    };
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    rafRef.current = requestAnimationFrame(step);
  }, [graph]);

  useEffect(() => {
    restart();
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph]);

  const screenToGraph = useCallback((clientX: number, clientY: number): Vec => {
    const svg = svgRef.current;
    if (!svg) return { x: 0, y: 0 };
    const rect = svg.getBoundingClientRect();
    const { x: vx, y: vy, k } = viewRef.current;
    return {
      x: (((clientX - rect.left) / rect.width) * W - vx) / k,
      y: (((clientY - rect.top) / rect.height) * H - vy) / k,
    };
  }, []);

  const onPointerDownNode = useCallback(
    (e: React.PointerEvent, id: string) => {
      e.stopPropagation();
      (e.target as Element).setPointerCapture(e.pointerId);
      dragRef.current = { kind: "node", id, startClientX: e.clientX, startClientY: e.clientY, startView: { x: 0, y: 0 } };
      const n = positionsRef.current[id];
      if (n) n.pinned = true;
    },
    []
  );

  const onPointerDownBackground = useCallback((e: React.PointerEvent) => {
    (e.target as Element).setPointerCapture(e.pointerId);
    dragRef.current = {
      kind: "pan",
      startClientX: e.clientX,
      startClientY: e.clientY,
      startView: { x: viewRef.current.x, y: viewRef.current.y },
    };
  }, []);

  const onPointerMove = useCallback(
    (e: React.PointerEvent) => {
      const drag = dragRef.current;
      if (!drag) return;
      if (drag.kind === "node" && drag.id) {
        const p = screenToGraph(e.clientX, e.clientY);
        const n = positionsRef.current[drag.id];
        if (n) {
          n.x = p.x;
          n.y = p.y;
          n.vx = 0;
          n.vy = 0;
        }
        forceRender((v) => v + 1);
      } else if (drag.kind === "pan") {
        const svg = svgRef.current;
        const rect = svg?.getBoundingClientRect();
        const scaleX = rect ? W / rect.width : 1;
        const scaleY = rect ? H / rect.height : 1;
        viewRef.current.x = drag.startView.x + (e.clientX - drag.startClientX) * scaleX;
        viewRef.current.y = drag.startView.y + (e.clientY - drag.startClientY) * scaleY;
        forceRender((v) => v + 1);
      }
    },
    [screenToGraph]
  );

  const onPointerUp = useCallback((e: React.PointerEvent) => {
    if (dragRef.current) {
      try {
        (e.target as Element).releasePointerCapture(e.pointerId);
      } catch {
        /* already released */
      }
    }
    dragRef.current = null;
  }, []);

  // A plain JSX `onWheel` is attached by React as a passive listener (for scroll
  // performance), so `preventDefault()` inside it is silently ignored — the browser
  // logs "Unable to preventDefault inside passive event listener invocation" and the
  // page scrolls behind the canvas while the graph also tries to zoom. Attaching the
  // listener natively with `{ passive: false }` is the only way to actually claim the
  // wheel gesture for the canvas.
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const handler = (e: WheelEvent) => {
      e.preventDefault();
      const rect = svg.getBoundingClientRect();
      const before = { x: (e.clientX - rect.left) / rect.width, y: (e.clientY - rect.top) / rect.height };
      const { x, y, k } = viewRef.current;
      const nextK = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, k * (e.deltaY > 0 ? 0.9 : 1.1)));
      // Zoom toward the cursor: keep the graph-space point under the cursor fixed.
      const graphX = (before.x * W - x) / k;
      const graphY = (before.y * H - y) / k;
      viewRef.current = { x: before.x * W - graphX * nextK, y: before.y * H - graphY * nextK, k: nextK };
      forceRender((v) => v + 1);
    };
    svg.addEventListener("wheel", handler, { passive: false });
    return () => svg.removeEventListener("wheel", handler);
  }, []);

  const fitToView = useCallback(() => {
    const ids = Object.keys(positionsRef.current);
    if (!ids.length) return;
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const id of ids) {
      const n = positionsRef.current[id];
      minX = Math.min(minX, n.x);
      minY = Math.min(minY, n.y);
      maxX = Math.max(maxX, n.x);
      maxY = Math.max(maxY, n.y);
    }
    const pad = 60;
    const bw = Math.max(maxX - minX + pad * 2, 1);
    const bh = Math.max(maxY - minY + pad * 2, 1);
    const k = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.min(W / bw, H / bh)));
    const cx = (minX + maxX) / 2;
    const cy = (minY + maxY) / 2;
    viewRef.current = { x: W / 2 - cx * k, y: H / 2 - cy * k, k };
    forceRender((v) => v + 1);
  }, []);

  const nodeById = useMemo(() => new Map(graph?.nodes.map((n) => [n.id, n]) ?? []), [graph]);
  const maxEdge = useMemo(() => Math.max(...(graph?.edges.map((e) => e.weight) ?? [1]), 1), [graph]);
  const maxNode = useMemo(
    () => Math.max(...(graph?.nodes.filter((n) => n.type !== "owner").map((n) => n.weight) ?? [1]), 1),
    [graph]
  );

  const matchesQuery = useCallback(
    (n: GraphNode) => !query.trim() || n.label.toLowerCase().includes(query.trim().toLowerCase()),
    [query]
  );

  // A node is explorable when its interactions break down across more than one channel —
  // expanding it reveals one sub-node per channel (e.g. "whatsapp: 12", "sms: 4") instead of
  // only the flattened total. Nodes from a graph built before channel_weights existed simply
  // have nothing to expand.
  const channelBreakdown = useCallback((n: GraphNode): [string, number][] => {
    const cw = n.channel_weights;
    if (!cw) return [];
    return Object.entries(cw).sort((a, b) => b[1] - a[1]);
  }, []);

  const toggleExpand = useCallback((id: string, e: React.MouseEvent | React.PointerEvent) => {
    e.stopPropagation();
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  if (loading) return <div className="p-8 text-muted">Loading communication graph…</div>;
  if (!graph || graph.nodes.length <= 1) {
    return (
      <div className="p-6 h-full">
        <SectionHeader title="Communication Network" />
        <DatasetEmpty
          dataset="graph"
          title="No communication network"
          detail="No messages or calls were attributable to participants."
        />
      </div>
    );
  }

  const { x: vx, y: vy, k } = viewRef.current;

  return (
    <div className="p-6 h-full flex flex-col">
      <SectionHeader
        title="Communication Network"
        sub={`${graph.stats.participants} participants · ${graph.stats.interactions} interactions · ${graph.stats.channels.join(", ")}`}
      />
      <div className="flex items-center gap-2 mb-2">
        <input
          className="input max-w-xs"
          placeholder="Filter by name…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <button className="btn-ghost text-xs" onClick={fitToView}>
          Fit to view
        </button>
        <button className="btn-ghost text-xs" onClick={restart}>
          Reset layout
        </button>
        <span className="text-[11px] text-muted ml-auto">
          Scroll to zoom · drag background to pan · drag a node to reposition it
          {!settled && " · settling…"}
        </span>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-4 flex-1 min-h-0">
        <div className="lg:col-span-3 card p-0 relative overflow-hidden">
          <svg
            ref={svgRef}
            viewBox={`0 0 ${W} ${H}`}
            className="w-full h-full touch-none select-none"
            style={{ cursor: dragRef.current?.kind === "pan" ? "grabbing" : "grab" }}
            onPointerDown={onPointerDownBackground}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            onPointerLeave={onPointerUp}
            onClick={() => setSelected(null)}
          >
            <g transform={`translate(${vx},${vy}) scale(${k})`}>
              {graph.edges.map((e, i) => {
                const a = positionsRef.current[e.source];
                const b = positionsRef.current[e.target];
                if (!a || !b) return null;
                const color = channelColor(e.channels[0] ?? "device");
                const srcNode = nodeById.get(e.source);
                const dstNode = nodeById.get(e.target);
                const dim =
                  !!query.trim() &&
                  !(srcNode && matchesQuery(srcNode)) &&
                  !(dstNode && matchesQuery(dstNode));
                return (
                  <line
                    key={i}
                    x1={a.x} y1={a.y} x2={b.x} y2={b.y}
                    stroke={color}
                    strokeOpacity={dim ? 0.12 : 0.45}
                    strokeWidth={(1 + (e.weight / maxEdge) * 5) / k}
                  />
                );
              })}
              {graph.nodes.map((n) => {
                const p = positionsRef.current[n.id];
                if (!p) return null;
                const isOwner = n.type === "owner";
                const r = isOwner ? 26 : 8 + (n.weight / maxNode) * 16;
                const color = isOwner ? "#d8823c" : channelColor(n.channels[0] ?? "app-db");
                const match = matchesQuery(n);
                const isSelected = selected?.id === n.id;
                const isHover = hoverId === n.id;
                const breakdown = channelBreakdown(n);
                const expandable = breakdown.length > 1;
                const isExpanded = expandedIds.has(n.id);
                return (
                  <g key={n.id}>
                    <g
                      onPointerDown={(e) => onPointerDownNode(e, n.id)}
                      onPointerEnter={() => setHoverId(n.id)}
                      onPointerLeave={() => setHoverId((h) => (h === n.id ? null : h))}
                      onClick={(e) => {
                        e.stopPropagation();
                        setSelected(n);
                      }}
                      className="cursor-pointer"
                      opacity={query.trim() && !match ? 0.15 : 1}
                    >
                      <circle
                        cx={p.x} cy={p.y} r={r}
                        fill={color}
                        fillOpacity={isOwner ? 0.95 : 0.85}
                        stroke={isSelected ? "#1a1d21" : "#0d0f12"}
                        strokeWidth={(isSelected ? 3 : 2) / k}
                      />
                      {(isHover || isSelected || k > 0.6) && (
                        <text
                          x={p.x} y={p.y + r + 12 / k}
                          textAnchor="middle"
                          fontSize={(isOwner ? 13 : 11) / Math.max(k, 0.6)}
                          className="fill-ink"
                          fontWeight={isOwner ? 700 : 400}
                        >
                          {n.label.length > 22 ? n.label.slice(0, 21) + "…" : n.label}
                        </text>
                      )}
                    </g>
                    {expandable && (
                      <g
                        onPointerDown={(e) => e.stopPropagation()}
                        onClick={(e) => toggleExpand(n.id, e)}
                        className="cursor-pointer"
                      >
                        <circle
                          cx={p.x + r * 0.68} cy={p.y - r * 0.68} r={7 / k}
                          fill="#1a1d21" fillOpacity={0.9}
                          stroke="#fff" strokeWidth={1 / k}
                        />
                        <text
                          x={p.x + r * 0.68} y={p.y - r * 0.68 + 3.5 / k}
                          textAnchor="middle"
                          fontSize={10 / k}
                          fontWeight={700}
                          fill="#fff"
                        >
                          {isExpanded ? "−" : "+"}
                        </text>
                      </g>
                    )}
                  </g>
                );
              })}
              {graph.nodes.flatMap((n) => {
                const p = positionsRef.current[n.id];
                if (!p || !expandedIds.has(n.id)) return [];
                const breakdown = channelBreakdown(n);
                if (breakdown.length <= 1) return [];
                const parentTotal = breakdown.reduce((sum, [, w]) => sum + w, 0) || 1;
                const dist = (n.type === "owner" ? 26 : 8 + (n.weight / maxNode) * 16) + 36;
                return breakdown.map(([channel, weight], i) => {
                  const angle = (i / breakdown.length) * Math.PI * 2 - Math.PI / 2;
                  const cx = p.x + Math.cos(angle) * dist;
                  const cy = p.y + Math.sin(angle) * dist;
                  const cr = Math.max(5, 4 + (weight / parentTotal) * 12);
                  const color = channelColor(channel);
                  const child: GraphNode = {
                    id: `${n.id}::${channel}`,
                    label: `${n.label} → ${channel}`,
                    type: "channel",
                    weight,
                    channels: [channel],
                  };
                  const isSelected = selected?.id === child.id;
                  return (
                    <g key={child.id}>
                      <line
                        x1={p.x} y1={p.y} x2={cx} y2={cy}
                        stroke={color} strokeOpacity={0.5} strokeWidth={1.5 / k} strokeDasharray={`${3 / k} ${2 / k}`}
                      />
                      <g
                        onClick={(e) => {
                          e.stopPropagation();
                          setSelected(child);
                        }}
                        className="cursor-pointer"
                      >
                        <circle
                          cx={cx} cy={cy} r={cr}
                          fill={color} fillOpacity={0.85}
                          stroke={isSelected ? "#1a1d21" : "#0d0f12"}
                          strokeWidth={(isSelected ? 3 : 1.5) / k}
                        />
                        <text
                          x={cx} y={cy + cr + 11 / k}
                          textAnchor="middle"
                          fontSize={9.5 / Math.max(k, 0.6)}
                          className="fill-ink"
                        >
                          {channel} · {weight}
                        </text>
                      </g>
                    </g>
                  );
                });
              })}
            </g>
          </svg>
          {selected && (
            <div className="absolute top-3 left-3 card p-3 text-xs bg-panel-2/95 max-w-[220px]">
              <div className="flex items-center justify-between gap-2">
                <div className="font-semibold text-ink truncate">{selected.label}</div>
                <button className="text-muted hover:text-ink shrink-0" onClick={() => setSelected(null)}>
                  <X className="h-4 w-4" strokeWidth={1.75} aria-hidden />
                </button>
              </div>
              <div className="text-muted mt-1">
                {selected.weight} interaction{selected.weight === 1 ? "" : "s"}
              </div>
              <div className="flex flex-wrap gap-1 mt-1.5">
                {selected.channels.map((c) => (
                  <span
                    key={c}
                    className="text-[9px] px-1.5 py-0.5 rounded"
                    style={{ background: channelColor(c) + "33", color: channelColor(c) }}
                  >
                    {c}
                  </span>
                ))}
              </div>
            </div>
          )}
          <div className="absolute bottom-2 right-2 text-[10px] text-muted/70 font-mono">
            {Math.round(k * 100)}%
          </div>
        </div>
        <div className="card overflow-auto">
          <div className="px-3 py-2 text-[11px] uppercase tracking-wider text-muted border-b border-line">Key participants</div>
          {graph.stats.top_contacts.map((t, i) => {
            const node = graph.nodes.find((n) => n.label === t.label && n.type !== "owner");
            return (
              <button
                key={i}
                className="w-full text-left px-3 py-2 border-b border-line/50 last:border-0 hover:bg-panel-2"
                onClick={() => {
                  if (!node) return;
                  setSelected(node);
                  const p = positionsRef.current[node.id];
                  if (p) {
                    viewRef.current = { x: W / 2 - p.x * viewRef.current.k, y: H / 2 - p.y * viewRef.current.k, k: viewRef.current.k };
                    forceRender((v) => v + 1);
                  }
                }}
              >
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium truncate">{t.label}</span>
                  <span className="text-xs font-mono text-accent">{t.weight}</span>
                </div>
                <div className="flex gap-1 mt-1 flex-wrap">
                  {t.channels.map((c) => (
                    <span
                      key={c}
                      className="text-[9px] px-1 rounded"
                      style={{ background: channelColor(c) + "33", color: channelColor(c) }}
                    >
                      {c}
                    </span>
                  ))}
                </div>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
