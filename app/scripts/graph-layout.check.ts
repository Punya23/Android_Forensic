// Runnable check for the Communication Network layout: `npm run check:graph`.
//
// The first layout was a force simulation tuned for "a hundred-node graph". On a real
// handset (case 2345: 1289 nodes, 812 of them saved contacts with no recorded interaction)
// it diverged — per-node energy stayed ~6e5 and never decayed, so nodes flew off the canvas
// — and cost ~30 ms per frame. This pins the properties that matter.
import assert from "node:assert/strict";
import {
  CANVAS_H,
  CANVAS_W,
  LAYOUT_ITERATIONS,
  layoutStep,
  layoutTemperature,
  maxNodeRadius,
  seedPositions,
  selectDrawn,
} from "../src/lib/graphLayout.ts";

type N = { id: string; label: string; type: "owner" | "contact"; weight: number; channels: string[] };
type E = { source: string; target: string; weight: number; channels: string[] };

function star(active: number, dormant: number): { nodes: N[]; edges: E[] } {
  const nodes: N[] = [{ id: "owner:self", label: "Device", type: "owner", weight: 0, channels: ["device"] }];
  const edges: E[] = [];
  for (let i = 0; i < active + dormant; i++) {
    const weight = i < active ? active - i : 0; // strongest first
    nodes.push({ id: `num:${i}`, label: `c${i}`, type: "contact", weight, channels: weight ? ["call"] : [] });
    if (weight) edges.push({ source: "owner:self", target: `num:${i}`, weight, channels: ["call"] });
  }
  return { nodes, edges };
}

function layout(g: { nodes: N[]; edges: E[] }) {
  const pos = seedPositions(g.nodes as never);
  const ids = g.nodes.map((n) => n.id);
  let moved = Infinity;
  for (let i = 0; i < LAYOUT_ITERATIONS; i++) moved = layoutStep(pos, g.edges, ids, layoutTemperature(i));
  return { pos, ids, moved };
}

// 1. Saved contacts with no interaction are not drawn, but they are counted, never lost.
{
  const g = star(480, 720);
  const drawn = selectDrawn(g.nodes as never, g.edges as never);
  assert.equal(drawn.nodes.length, 481, "owner + every contact with recorded interaction");
  assert.equal(drawn.dormant, 720);
  assert.equal(drawn.truncated, 0);
  assert.equal(drawn.edges.length, 480);
}

// 2. Past the cap, the strongest participants are kept and the cut is reported.
{
  const g = star(480, 0);
  const drawn = selectDrawn(g.nodes as never, g.edges as never, 100);
  assert.equal(drawn.nodes.length, 101);
  assert.equal(drawn.truncated, 380);
  assert.ok(drawn.nodes.some((n) => n.id === "num:0") && !drawn.nodes.some((n) => n.id === "num:479"));
  assert.ok(drawn.edges.every((e) => drawn.nodes.some((n) => n.id === e.target)), "no edge to a dropped node");
}

// 3. The layout is bounded, finite, settles, and does not stack nodes — at 28 and at 480.
for (const active of [27, 480]) {
  const g = star(active, 0);
  const t0 = Date.now();
  const { pos, ids, moved } = layout(g);
  const ms = Date.now() - t0;

  for (const id of ids) {
    const p = pos[id];
    assert.ok(Number.isFinite(p.x) && Number.isFinite(p.y), `${id} is finite`);
    assert.ok(p.x >= 0 && p.x <= CANVAS_W && p.y >= 0 && p.y <= CANVAS_H, `${id} is on the canvas (${p.x}, ${p.y})`);
  }
  assert.deepEqual([pos["owner:self"].x, pos["owner:self"].y], [CANVAS_W / 2, CANVAS_H / 2], "owner stays pinned at the centre");
  assert.ok(moved < 0.5, `layout has cooled (mean move ${moved})`);
  assert.ok(ms < 4000, `layout of ${active + 1} nodes took ${ms} ms`);

  // Nearest-neighbour distance per node: none stacked, and a typical node clear of its
  // neighbour at the radius the view will draw it (a dense hairball reads as "broken").
  const nearestOf = ids
    .filter((id) => id !== "owner:self")
    .map((id) =>
      Math.min(
        ...ids.filter((o) => o !== id).map((o) => Math.hypot(pos[id].x - pos[o].x, pos[id].y - pos[o].y))
      )
    )
    .sort((a, b) => a - b);
  assert.ok(nearestOf[0] > 0.5, `nodes are not stacked (closest pair ${nearestOf[0].toFixed(2)} px)`);
  const median = nearestOf[nearestOf.length >> 1];
  assert.ok(median >= maxNodeRadius(ids.length), `typical node overlaps its neighbour (median gap ${median.toFixed(1)} px)`);
}

// 4. Node radius shrinks as the count grows, and is unchanged for small graphs.
assert.equal(maxNodeRadius(28), 24);
assert.ok(maxNodeRadius(480) < 14 && maxNodeRadius(480) >= 5);
assert.ok(maxNodeRadius(2000) >= 5, "never smaller than a visible dot");

console.log("graph layout: ok");
