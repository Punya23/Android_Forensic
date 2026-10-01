import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { X } from "lucide-react";
import { api } from "../lib/api";
import type { CommunicationGraph, GraphNode } from "../lib/types";
import { SectionHeader } from "../components/common";
import { DatasetEmpty } from "../lib/capabilities";
import {
  CANVAS_H as H,
  CANVAS_W as W,
  LAYOUT_ITERATIONS,
  layoutStep,
  layoutTemperature,
  maxNodeRadius,
  seedPositions,
  selectDrawn,
  type SimNode,
  type Vec,
} from "../lib/graphLayout";

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

const ZOOM_MIN = 0.15;
const ZOOM_MAX = 4;

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

  // A failed fetch is not "no communication network": the two are told apart on screen.
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [rebuilding, setRebuilding] = useState(false);
  const [rebuildNote, setRebuildNote] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    api
      .dataset<CommunicationGraph>(caseId, "graph")
      .then((g) => setGraph((g as CommunicationGraph)?.nodes ? (g as CommunicationGraph) : null))
      .catch((err) => {
        setGraph(null);
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => setLoading(false));
  }, [caseId, reloadKey]);

  // Re-derive graph / financial trail / risk from the case's stored messages, calls and
  // contacts — for a case built by older engine code, or after an import.
  const rebuild = useCallback(async () => {
    setRebuilding(true);
    setRebuildNote(null);
    try {
      const r = await api.rebuildAnalysis(caseId);
      setRebuildNote(`Rebuilt from ${r.messages} messages — ${r.participants} participants.`);
      setReloadKey((k) => k + 1);
    } catch (err) {
      setRebuildNote(`Rebuild failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setRebuilding(false);
    }
  }, [caseId]);

  // Only participants with recorded interaction are drawn (see lib/graphLayout).
  const drawn = useMemo(() => (graph ? selectDrawn(graph.nodes, graph.edges) : null), [graph]);

  // (Re)seed the layout whenever the case's graph data changes. Each frame runs as many
  // iterations as fit in ~8 ms, so the page stays responsive; the temperature reaches zero
  // after LAYOUT_ITERATIONS, so the layout always ends.
  const restart = useCallback(() => {
    if (!drawn) return;
    positionsRef.current = seedPositions(drawn.nodes);
    viewRef.current = { x: 0, y: 0, k: 1 };
    setSettled(false);
    setExpandedIds(new Set());
    let iter = 0;
    const ids = drawn.nodes.map((n) => n.id);
    const edges = drawn.edges;
    const step = () => {
      const t0 = performance.now();
      let moved = 0;
      do {
        moved = layoutStep(positionsRef.current, edges, ids, layoutTemperature(iter));
        iter += 1;
      } while (iter < LAYOUT_ITERATIONS && performance.now() - t0 < 8);
      forceRender((v) => v + 1);
      if (iter < LAYOUT_ITERATIONS && moved > 0.05) {
        rafRef.current = requestAnimationFrame(step);
      } else {
        setSettled(true);
        rafRef.current = null;
      }
    };
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    rafRef.current = requestAnimationFrame(step);
  }, [drawn]);

  useEffect(() => {
    restart();
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drawn]);

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

  // Frame the settled layout once, so a large graph opens readable rather than as a dot.
  useEffect(() => {
    if (settled) fitToView();
  }, [settled, fitToView]);

  const nodeById = useMemo(() => new Map(drawn?.nodes.map((n) => [n.id, n]) ?? []), [drawn]);
  const maxEdge = useMemo(() => Math.max(...(drawn?.edges.map((e) => e.weight) ?? [1]), 1), [drawn]);
  const maxNode = useMemo(
    () => Math.max(...(drawn?.nodes.filter((n) => n.type !== "owner").map((n) => n.weight) ?? [1]), 1),
    [drawn]
  );
  // Past ~80 nodes, a label on every node overprints into noise: label the strongest 20
  // until the user zooms in (hover and selection always label).
  const labelCutoff = useMemo(() => {
    const weights = (drawn?.nodes ?? []).filter((n) => n.type !== "owner").map((n) => n.weight).sort((a, b) => b - a);
    return weights.length > 80 ? weights[19] : 0;
  }, [drawn]);
  // Node radius shrinks as the canvas fills, so a real handset's graph is not a blob.
  const rMax = useMemo(() => maxNodeRadius(drawn?.nodes.length ?? 0), [drawn]);
  const nodeRadius = useCallback(
    (n: GraphNode) => {
      if (n.type === "owner") return 26;
      const lo = Math.min(8, rMax / 2);
      return lo + (n.weight / maxNode) * (rMax - lo);
    },
    [rMax, maxNode]
  );

  // The device can hold one saved contact name against several identifiers, so two
  // "Key participants" rows can carry an identical label (CASE-REAL-005 has "Vedant Yeole"
  // as both num:+917875091022 and num:+919284078848). Append the identifier only when a
  // name appears on more than one row — the common case stays clean, and the duplicate
  // rows become tellable apart. The counts stay separate: merging them would be an
  // unevidenced claim that the two identifiers are one person. Mirrors _participant_label
  // in engine/triage/report/html_report.py.
  const participantLabel = useMemo(() => {
    const counts = new Map<string, number>();
    for (const t of graph?.stats.top_contacts ?? []) counts.set(t.label, (counts.get(t.label) ?? 0) + 1);
    return (t: { id?: string; label: string }): string => {
      if ((counts.get(t.label) ?? 0) < 2) return t.label;
      const id = t.id ?? "";
      const ident = id.includes(":") ? id.slice(id.indexOf(":") + 1) : id; // "num:+9178…" -> "+9178…"
      if (!ident || ident === t.label) return t.label; // graph.json predating the id: degrade quietly
      return `${t.label} (${ident})`;
    };
  }, [graph]);

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

  const rebuildButton = (
    <button className="btn-ghost text-xs" disabled={rebuilding} onClick={rebuild}>
      {rebuilding ? "Rebuilding…" : "Rebuild analysis"}
    </button>
  );

  if (loading) return <div className="p-8 text-muted">Loading communication graph…</div>;
  if (error) {
    return (
      <div className="p-6 h-full">
        <SectionHeader title="Communication Network" />
        <div role="alert" className="card p-4 text-sm">
          <div className="text-red-500">Couldn't load the communication graph: {error}</div>
          <div className="flex gap-2 mt-3">
            <button className="btn-ghost text-xs" onClick={() => setReloadKey((k) => k + 1)}>
              Retry
            </button>
            {rebuildButton}
          </div>
          {rebuildNote && <div className="text-muted mt-2">{rebuildNote}</div>}
        </div>
      </div>
    );
  }
  if (!graph || !drawn || drawn.nodes.length <= 1) {
    return (
      <div className="p-6 h-full">
        <SectionHeader title="Communication Network" />
        <DatasetEmpty
          dataset="graph"
          title="No communication network"
          detail={
            "No messages or calls were attributable to participants" +
            (drawn && drawn.dormant > 0
              ? `; ${drawn.dormant} saved contact(s) have no recorded interaction.`
              : ".")
          }
        />
        <div className="flex items-center gap-2 justify-center">
          {rebuildButton}
          {rebuildNote && <span className="text-xs text-muted">{rebuildNote}</span>}
        </div>
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
      <div className="flex flex-wrap items-center gap-2 mb-2">
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
        {rebuildButton}
        <span className="text-[11px] text-muted ml-auto">
          Scroll to zoom · drag background to pan · drag a node to reposition it
          {!settled && " · settling…"}
        </span>
      </div>
      {(drawn.dormant > 0 || drawn.truncated > 0 || rebuildNote) && (
        <div className="text-[11px] text-muted mb-2" role="status">
          {drawn.dormant > 0 &&
            `${drawn.dormant} saved contact(s) with no recorded call or message are not drawn (see Contacts). `}
          {drawn.truncated > 0 &&
            `Drawing the ${drawn.nodes.length - 1} strongest of ${drawn.nodes.length - 1 + drawn.truncated} participants by interaction volume. `}
          {rebuildNote}
        </div>
      )}
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
              {drawn.edges.map((e, i) => {
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
              {drawn.nodes.map((n) => {
                const p = positionsRef.current[n.id];
                if (!p) return null;
                const isOwner = n.type === "owner";
                const r = nodeRadius(n);
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
                      {(isHover || isSelected || (k > 0.6 && n.weight >= labelCutoff) || k > 2.2) && (
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
              {drawn.nodes.flatMap((n) => {
                const p = positionsRef.current[n.id];
                if (!p || !expandedIds.has(n.id)) return [];
                const breakdown = channelBreakdown(n);
                if (breakdown.length <= 1) return [];
                const parentTotal = breakdown.reduce((sum, [, w]) => sum + w, 0) || 1;
                const dist = nodeRadius(n) + 36;
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
          {(graph.stats.identity_normalisation?.merged_participants ?? 0) > 0 && (
            <div className="px-3 py-2 text-[10px] leading-relaxed text-muted border-b border-line">
              {graph.stats.identity_normalisation!.merged_identifiers} identifier(s) differing
              only by a dialing prefix ({graph.stats.identity_normalisation!.country_code}, 00 or a
              leading 0) are counted as one participant, so these totals are higher than a
              per-identifier count. Two different numbers are never merged. Full list in the report.
            </div>
          )}
          {graph.stats.top_contacts.map((t, i) => {
            // Resolve by id: matching on the label alone picks the first node with that
            // name, which silently pans to the wrong participant when a name is duplicated.
            // The label match remains only for graph.json files written before the id existed.
            const node =
              (t.id ? nodeById.get(t.id) : undefined) ??
              graph.nodes.find((n) => n.label === t.label && n.type !== "owner");
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
                  <span className="text-sm font-medium truncate" title={participantLabel(t)}>
                    {participantLabel(t)}
                  </span>
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
