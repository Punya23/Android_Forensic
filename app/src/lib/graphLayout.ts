// Layout for the Communication Network view: which participants are drawn, and where.
//
// Fruchterman–Reingold with a cooling temperature. The displacement of a node per step is
// capped by the temperature, which falls to zero over LAYOUT_ITERATIONS — so the layout is
// bounded and always ends, whatever the graph. (The previous spring/repulsion simulation had
// no such cap: on a real handset it diverged and never settled.)
import type { GraphEdge, GraphNode } from "./types";

export const CANVAS_W = 1000;
export const CANVAS_H = 640;
/** Most participants drawn at once; the strongest by interaction volume are kept. */
export const MAX_DRAWN = 500;
/** The temperature reaches 0 here, so the layout always ends. */
export const LAYOUT_ITERATIONS = 300;

const MARGIN = 20;
const GRAVITY = 0.05;
const GOLDEN_ANGLE = 2.399963229728653;

export type Vec = { x: number; y: number };
export type SimNode = Vec & { vx: number; vy: number; pinned: boolean };

export interface DrawnGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
  /** Saved contacts with no recorded call or message — counted, not drawn. */
  dormant: number;
  /** Participants with recorded interaction cut by MAX_DRAWN (weakest first). */
  truncated: number;
}

/** The part of the graph worth a canvas: the owner plus participants with recorded
 * interaction, strongest first, capped at `max`. Nothing is discarded silently — the counts
 * of what was left out are returned so the view can say so. */
export function selectDrawn(nodes: GraphNode[], edges: GraphEdge[], max = MAX_DRAWN): DrawnGraph {
  const owner = nodes.filter((n) => n.type === "owner");
  const others = nodes.filter((n) => n.type !== "owner");
  const active = others.filter((n) => n.weight > 0).sort((a, b) => b.weight - a.weight);
  const kept = [...owner, ...active.slice(0, max)];
  const ids = new Set(kept.map((n) => n.id));
  return {
    nodes: kept,
    edges: edges.filter((e) => ids.has(e.source) && ids.has(e.target)),
    dormant: others.length - active.length,
    truncated: Math.max(active.length - max, 0),
  };
}

/** Largest node radius that keeps neighbours apart at this node count: 24 px for a sparse
 * graph, shrinking as the canvas fills (never below a visible dot). */
export function maxNodeRadius(count: number): number {
  return Math.min(24, Math.max(5, 0.35 * Math.sqrt((CANVAS_W * CANVAS_H) / Math.max(count, 1))));
}

/** Deterministic start: an even spiral over the canvas, owner pinned at the centre. */
export function seedPositions(nodes: GraphNode[]): Record<string, SimNode> {
  const pos: Record<string, SimNode> = {};
  const others = nodes.filter((n) => n.type !== "owner");
  const reach = Math.min(CANVAS_W, CANVAS_H) * 0.45;
  others.forEach((n, i) => {
    const r = reach * Math.sqrt((i + 0.5) / Math.max(others.length, 1));
    const a = i * GOLDEN_ANGLE;
    pos[n.id] = { x: CANVAS_W / 2 + r * Math.cos(a), y: CANVAS_H / 2 + r * Math.sin(a), vx: 0, vy: 0, pinned: false };
  });
  const owner = nodes.find((n) => n.type === "owner");
  if (owner) pos[owner.id] = { x: CANVAS_W / 2, y: CANVAS_H / 2, vx: 0, vy: 0, pinned: true };
  return pos;
}

/** Maximum displacement allowed at iteration `i`: linear cooling to 0 at LAYOUT_ITERATIONS. */
export function layoutTemperature(i: number): number {
  return (CANVAS_W / 10) * Math.max(1 - i / LAYOUT_ITERATIONS, 0);
}

/** One iteration. Returns the mean displacement of the nodes that moved. */
export function layoutStep(
  positions: Record<string, SimNode>,
  edges: { source: string; target: string }[],
  ids: string[],
  temperature: number
): number {
  const n = ids.length;
  if (n === 0) return 0;
  const k = Math.sqrt((CANVAS_W * CANVAS_H) / n); // ideal spacing: nodes fill the canvas
  const k2 = k * k;
  const xs = new Float64Array(n);
  const ys = new Float64Array(n);
  const fx = new Float64Array(n);
  const fy = new Float64Array(n);
  const index = new Map<string, number>();
  ids.forEach((id, i) => {
    xs[i] = positions[id].x;
    ys[i] = positions[id].y;
    index.set(id, i);
  });

  // Repulsion between every pair (each pair once).
  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      const dx = xs[i] - xs[j];
      const dy = ys[i] - ys[j];
      const d2 = Math.max(dx * dx + dy * dy, 0.01);
      const f = k2 / d2;
      fx[i] += dx * f;
      fy[i] += dy * f;
      fx[j] -= dx * f;
      fy[j] -= dy * f;
    }
  }
  // Attraction along edges, and a weak pull to the centre so components stay on canvas.
  for (const e of edges) {
    const a = index.get(e.source);
    const b = index.get(e.target);
    if (a === undefined || b === undefined) continue;
    const dx = xs[a] - xs[b];
    const dy = ys[a] - ys[b];
    const f = Math.sqrt(dx * dx + dy * dy) / k;
    fx[a] -= dx * f;
    fy[a] -= dy * f;
    fx[b] += dx * f;
    fy[b] += dy * f;
  }

  let moved = 0;
  let movable = 0;
  for (let i = 0; i < n; i++) {
    const node = positions[ids[i]];
    if (node.pinned) continue;
    fx[i] += (CANVAS_W / 2 - xs[i]) * GRAVITY;
    fy[i] += (CANVAS_H / 2 - ys[i]) * GRAVITY;
    const len = Math.hypot(fx[i], fy[i]);
    if (len > 0) {
      const step = Math.min(len, temperature);
      node.x = Math.min(Math.max(xs[i] + (fx[i] / len) * step, MARGIN), CANVAS_W - MARGIN);
      node.y = Math.min(Math.max(ys[i] + (fy[i] / len) * step, MARGIN), CANVAS_H - MARGIN);
      moved += step;
    }
    movable++;
  }
  return movable ? moved / movable : 0;
}
