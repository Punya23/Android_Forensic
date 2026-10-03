import { useEffect, useRef } from "react";
import { LAND_H, LAND_W, landCells } from "../lib/landMask";

export interface GlobePoint {
  lat: number;
  lon: number;
  label?: string;
}

const RAD = Math.PI / 180;
const ZOOM_MIN = 0.7;
const ZOOM_MAX = 3.5;

/** Land dots as [lat, lon] in radians, from the 2-degree mask. Built once. */
let LAND_DOTS: [number, number][] | null = null;
function landDots(): [number, number][] {
  if (LAND_DOTS) return LAND_DOTS;
  const cells = landCells();
  const out: [number, number][] = [];
  for (let r = 0; r < LAND_H; r++) {
    for (let c = 0; c < LAND_W; c++) {
      if (cells[r * LAND_W + c]) out.push([(90 - (r + 0.5) * (180 / LAND_H)) * RAD, (-180 + (c + 0.5) * (360 / LAND_W)) * RAD]);
    }
  }
  LAND_DOTS = out;
  return out;
}

/** Spherical mean of the points, as [lat, lon] in radians — where to point the globe first. */
function centroid(points: GlobePoint[]): [number, number] {
  if (!points.length) return [20 * RAD, 78 * RAD];
  let x = 0, y = 0, z = 0;
  for (const p of points) {
    x += Math.cos(p.lat * RAD) * Math.cos(p.lon * RAD);
    y += Math.cos(p.lat * RAD) * Math.sin(p.lon * RAD);
    z += Math.sin(p.lat * RAD);
  }
  return [Math.atan2(z, Math.hypot(x, y)), Math.atan2(y, x)];
}

/**
 * A dotted, draggable, zoomable globe with a marker per point — drawn on a 2D canvas from a
 * bundled land mask, so it works offline and needs no map tiles or 3D library. Every marker is
 * a real coordinate from the case; nothing is invented to fill the sphere.
 */
export function DotGlobe({ points, className = "" }: { points: GlobePoint[]; className?: string }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const hover = useRef<{ x: number; y: number } | null>(null);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const [lat0, lon0] = centroid(points);
    const view = { lat: lat0, lon: lon0, k: 1 };
    let drag: { x: number; y: number } | null = null;
    let width = 0;
    let height = 0;
    let raf = 0;
    let colors = { ink: "255,255,255", accent: "40,112,255" };
    let frame = 0;
    const still = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    const dots = landDots();

    const resize = () => {
      const r = canvas.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      width = r.width;
      height = r.height;
      canvas.width = Math.max(1, Math.round(width * dpr));
      canvas.height = Math.max(1, Math.round(height * dpr));
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    const ro = new ResizeObserver(resize);
    ro.observe(canvas);
    resize();

    const project = (lat: number, lon: number, R: number, cx: number, cy: number) => {
      const dl = lon - view.lon;
      const cosc = Math.sin(view.lat) * Math.sin(lat) + Math.cos(view.lat) * Math.cos(lat) * Math.cos(dl);
      return {
        cosc,
        x: cx + R * Math.cos(lat) * Math.sin(dl),
        y: cy - R * (Math.cos(view.lat) * Math.sin(lat) - Math.sin(view.lat) * Math.cos(lat) * Math.cos(dl)),
      };
    };

    const draw = (t: number) => {
      if (frame++ % 30 === 0) {
        const cs = getComputedStyle(canvas);
        colors = {
          ink: cs.getPropertyValue("--color-ink").trim().replace(/\s+/g, ","),
          accent: cs.getPropertyValue("--color-accent").trim().replace(/\s+/g, ","),
        };
      }
      if (!drag && !still) view.lon += 0.0025;
      ctx.clearRect(0, 0, width, height);
      const R = (Math.min(width, height) / 2) * 0.88 * view.k;
      const cx = width / 2;
      const cy = height / 2;

      ctx.beginPath();
      ctx.arc(cx, cy, R, 0, Math.PI * 2);
      ctx.strokeStyle = `rgba(${colors.ink},0.10)`;
      ctx.lineWidth = 1;
      ctx.stroke();

      const size = Math.max(0.8, R / 150);
      for (const [lat, lon] of dots) {
        const p = project(lat, lon, R, cx, cy);
        if (p.cosc <= 0) continue;
        ctx.fillStyle = `rgba(${colors.ink},${(0.12 + 0.5 * p.cosc).toFixed(3)})`;
        ctx.fillRect(p.x - size / 2, p.y - size / 2, size, size);
      }

      let hit: { x: number; y: number; label: string } | null = null;
      const pulse = (t / 1400) % 1;
      for (const pt of points) {
        const p = project(pt.lat * RAD, pt.lon * RAD, R, cx, cy);
        if (p.cosc <= 0) continue;
        ctx.beginPath();
        ctx.arc(p.x, p.y, 3.2, 0, Math.PI * 2);
        ctx.fillStyle = `rgb(${colors.accent})`;
        ctx.fill();
        ctx.beginPath();
        ctx.arc(p.x, p.y, 3.2 + pulse * 9, 0, Math.PI * 2);
        ctx.strokeStyle = `rgba(${colors.accent},${(0.5 * (1 - pulse)).toFixed(3)})`;
        ctx.stroke();
        const h = hover.current;
        if (h && pt.label && Math.hypot(h.x - p.x, h.y - p.y) < 9) hit = { x: p.x, y: p.y, label: pt.label };
      }
      if (hit) {
        const text = hit.label.length > 48 ? `${hit.label.slice(0, 47)}…` : hit.label;
        ctx.font = "11px Space Grotesk Variable, system-ui, sans-serif";
        const w = ctx.measureText(text).width + 14;
        const x = Math.min(Math.max(hit.x - w / 2, 4), width - w - 4);
        const y = Math.max(hit.y - 30, 4);
        ctx.fillStyle = "rgba(0,0,0,0.78)";
        ctx.beginPath();
        ctx.roundRect(x, y, w, 20, 6);
        ctx.fill();
        ctx.fillStyle = "#fff";
        ctx.fillText(text, x + 7, y + 14);
      }
      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);

    const down = (e: PointerEvent) => {
      canvas.setPointerCapture(e.pointerId);
      drag = { x: e.clientX, y: e.clientY };
    };
    const move = (e: PointerEvent) => {
      const r = canvas.getBoundingClientRect();
      hover.current = { x: e.clientX - r.left, y: e.clientY - r.top };
      if (!drag) return;
      const R = (Math.min(width, height) / 2) * 0.88 * view.k;
      view.lon -= (e.clientX - drag.x) / R;
      view.lat = Math.max(-1.2, Math.min(1.2, view.lat + (e.clientY - drag.y) / R));
      drag = { x: e.clientX, y: e.clientY };
    };
    const up = () => {
      drag = null;
    };
    const leave = () => {
      hover.current = null;
      drag = null;
    };
    const wheel = (e: WheelEvent) => {
      e.preventDefault(); // claim the gesture so the page does not scroll behind the globe
      view.k = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, view.k * (e.deltaY > 0 ? 0.92 : 1.08)));
    };
    canvas.addEventListener("pointerdown", down);
    canvas.addEventListener("pointermove", move);
    canvas.addEventListener("pointerup", up);
    canvas.addEventListener("pointerleave", leave);
    canvas.addEventListener("wheel", wheel, { passive: false });
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      canvas.removeEventListener("pointerdown", down);
      canvas.removeEventListener("pointermove", move);
      canvas.removeEventListener("pointerup", up);
      canvas.removeEventListener("pointerleave", leave);
      canvas.removeEventListener("wheel", wheel);
    };
  }, [points]);

  return (
    <canvas
      ref={ref}
      className={`w-full h-full touch-none cursor-grab active:cursor-grabbing ${className}`}
      role="img"
      aria-label="Globe showing where the case's recorded locations are"
    />
  );
}
