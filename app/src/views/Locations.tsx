/**
 * LocationsView — Forensic Location Tracing
 *
 * Visualises GPS coordinates extracted from image EXIF metadata on an interactive
 * Leaflet map. Shows where the subject's device was when each geotagged photo was
 * taken, ordered chronologically — useful for building a movement timeline.
 *
 * Data source: derived/locations.json  (LocationPoint[])
 * Each point carries: latitude, longitude, source ("exif" | "mediastore" | …),
 * timestamp (ISO-8601 or null), label (filename / description), source_file.
 *
 * FORENSIC DISCLAIMER: Locations are extracted from image EXIF metadata only.
 * They are not real-time GPS tracks. Not all images carry EXIF GPS (user may have
 * disabled location tagging). Photos shared via messaging apps may have had EXIF
 * stripped. Coordinates should be independently verified before reliance in court.
 */
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  MapContainer,
  TileLayer,
  Marker,
  Popup,
  useMap,
} from "react-leaflet";
import L from "leaflet";
// @ts-ignore — leaflet.markercluster ships its own typings via @types/leaflet.markercluster
import MarkerClusterGroup from "react-leaflet-markercluster";
import type { LocationPoint } from "../lib/types";
import { useDataset, fmtTs } from "../lib/hooks";
import { api } from "../lib/api";
import { SectionHeader, EmptyState } from "../components/common";

// ---------------------------------------------------------------------------
// Configuration — change the tile URL here to switch providers.
// No API key required for OpenStreetMap. Must work offline: if tiles fail to
// load Leaflet renders a blank grey background; markers still appear.
// ---------------------------------------------------------------------------
const MAP_TILE_URL = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";
const MAP_TILE_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

/** How many points to show before the "Show all" button appears. */
const INITIAL_CAP = 200;
/** How many items to show in the right-panel recent images list. */
const RECENT_COUNT = 20;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Age-based marker colour: green = ≤7 days, amber = ≤30 days, red = older/unknown */
function markerColor(ts: string | null | undefined): string {
  if (!ts) return "#d3625f"; // red — no timestamp
  const age = Date.now() - new Date(ts).getTime();
  const days = age / 86_400_000;
  if (days <= 7) return "#4fb477";  // green
  if (days <= 30) return "#d8a53c"; // amber
  return "#d3625f";                 // red
}

/** Build a custom DivIcon so we can colour-code markers without external images. */
function buildIcon(color: string, highlighted = false): L.DivIcon {
  const size = highlighted ? 16 : 12;
  const border = highlighted ? "3px solid #fff" : "2px solid rgba(0,0,0,0.5)";
  return L.divIcon({
    className: "",
    html: `<div style="
      width:${size}px;height:${size}px;
      border-radius:50%;
      background:${color};
      border:${border};
      box-shadow:0 1px 4px rgba(0,0,0,0.6);
    "></div>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
    popupAnchor: [0, -(size / 2 + 4)],
  });
}

/** Parse an ISO timestamp to a Date, returning null if invalid. */
function parseTs(ts: string | null | undefined): Date | null {
  if (!ts) return null;
  const d = new Date(ts);
  return isNaN(d.getTime()) ? null : d;
}

/** Format a date as YYYY-MM-DD for <input type="date"> */
function toDateInput(d: Date): string {
  return d.toISOString().slice(0, 10);
}

// ---------------------------------------------------------------------------
// Sub-component: auto-fit map bounds whenever the filtered point list changes
// ---------------------------------------------------------------------------
function BoundsController({ points }: { points: LocationPoint[] }) {
  const map = useMap();
  useEffect(() => {
    if (points.length === 0) return;
    const latlngs = points.map((p) => [p.latitude, p.longitude] as [number, number]);
    map.fitBounds(L.latLngBounds(latlngs), { padding: [32, 32], maxZoom: 15 });
  }, [map, points]);
  return null;
}

// ---------------------------------------------------------------------------
// Sub-component: fly to a point when triggered from the image list panel
// ---------------------------------------------------------------------------
function FlyController({
  target,
}: {
  target: LocationPoint | null;
}) {
  const map = useMap();
  useEffect(() => {
    if (!target) return;
    map.flyTo([target.latitude, target.longitude], 15, { duration: 0.8 });
  }, [map, target]);
  return null;
}

// ---------------------------------------------------------------------------
// Main view
// ---------------------------------------------------------------------------
export function LocationsView({ caseId }: { caseId: string }) {
  const { data, loading } = useDataset<LocationPoint>(caseId, "locations");

  // Only EXIF / MediaStore points are useful for photo-tracing; skip pure
  // "dumpsys" last-known-fix points from the movement slider (they have no image).
  const photoPoints = useMemo(
    () => data.filter((p) => p.source !== "dumpsys"),
    [data]
  );

  // --- Date range filter state ---
  const [fromDate, setFromDate] = useState<string>("");
  const [toDate, setToDate] = useState<string>("");
  // Source filter
  const [sourceFilter, setSourceFilter] = useState<"all" | "exif" | "mediastore">("all");
  // Cap for initial render (performance)
  const [showAll, setShowAll] = useState(false);
  // Which point was selected from the panel list (triggers map fly-to)
  const [flyTarget, setFlyTarget] = useState<LocationPoint | null>(null);
  // Whether map tiles are available (detected via tile error events)
  const [tilesOffline, setTilesOffline] = useState(false);

  // Ref to all open marker refs, keyed by index, so we can programmatically open popups
  const markerRefs = useRef<Map<number, L.Marker>>(new Map());

  // --- Derived: sorted, filtered list ---
  const sorted = useMemo(() => {
    let pts = [...photoPoints].sort((a, b) => {
      const ta = parseTs(a.timestamp)?.getTime() ?? 0;
      const tb = parseTs(b.timestamp)?.getTime() ?? 0;
      return tb - ta; // most recent first
    });

    if (sourceFilter !== "all") {
      pts = pts.filter((p) => p.source === sourceFilter);
    }

    if (fromDate) {
      const from = new Date(fromDate).getTime();
      pts = pts.filter((p) => {
        const t = parseTs(p.timestamp);
        return t ? t.getTime() >= from : false;
      });
    }
    if (toDate) {
      // include the whole day
      const to = new Date(toDate).getTime() + 86_399_999;
      pts = pts.filter((p) => {
        const t = parseTs(p.timestamp);
        return t ? t.getTime() <= to : false;
      });
    }

    return pts;
  }, [photoPoints, sourceFilter, fromDate, toDate]);

  /** Points actually rendered on the map (capped for initial performance) */
  const visiblePoints = useMemo(
    () => (showAll ? sorted : sorted.slice(0, INITIAL_CAP)),
    [sorted, showAll]
  );

  /** 20 most-recent points that have timestamps (for the image list panel) */
  const recentItems = useMemo(
    () =>
      sorted
        .filter((p) => p.timestamp)
        .slice(0, RECENT_COUNT),
    [sorted]
  );

  // Auto-set date range from data bounds when data first loads
  useEffect(() => {
    if (photoPoints.length === 0) return;
    const dates = photoPoints
      .map((p) => parseTs(p.timestamp))
      .filter((d): d is Date => d !== null)
      .map((d) => d.getTime());
    if (dates.length === 0) return;
    setFromDate(toDateInput(new Date(Math.min(...dates))));
    setToDate(toDateInput(new Date(Math.max(...dates))));
  }, [photoPoints]);

  const handleItemClick = useCallback(
    (p: LocationPoint, idx: number) => {
      setFlyTarget(p);
      // Open popup after flyTo animation finishes (~900ms)
      setTimeout(() => {
        const marker = markerRefs.current.get(idx);
        marker?.openPopup();
      }, 950);
    },
    []
  );

  // --- Render guards ---
  if (loading)
    return (
      <div className="flex items-center justify-center h-full text-muted text-sm">
        Loading location data…
      </div>
    );

  if (data.length === 0)
    return (
      <EmptyState
        title="No geotagged images found"
        detail="No GPS EXIF data was found in pulled photos and no last-known fixes were collected. The device owner may have disabled location tagging, or no images with GPS metadata were acquired."
      />
    );

  // Edge case: data exists but all filtered out
  const totalGps = photoPoints.length;
  const filteredCount = sorted.length;

  return (
    <div className="p-4 h-full flex flex-col gap-3 overflow-hidden">
      {/* ── Header ─────────────────────────────────────────────────────── */}
      <SectionHeader
        title="Location Tracing"
        sub={`${totalGps} geotagged point${totalGps !== 1 ? "s" : ""} · source: EXIF photo metadata`}
        right={
          <span className="text-xs text-warn bg-warn/10 border border-warn/30 rounded px-2 py-0.5">
            EXIF metadata only — not real-time GPS
          </span>
        }
      />

      {/* ── Filters bar ────────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-center gap-2 shrink-0">
        <span className="text-xs text-muted">From</span>
        <input
          type="date"
          className="input w-auto text-xs"
          value={fromDate}
          onChange={(e) => setFromDate(e.target.value)}
        />
        <span className="text-xs text-muted">To</span>
        <input
          type="date"
          className="input w-auto text-xs"
          value={toDate}
          onChange={(e) => setToDate(e.target.value)}
        />

        {/* Source filter */}
        <div className="flex gap-1 ml-2">
          {(["all", "exif", "mediastore"] as const).map((s) => (
            <button
              key={s}
              onClick={() => setSourceFilter(s)}
              className={`px-3 py-1 text-xs rounded border transition-colors ${
                sourceFilter === s
                  ? "bg-accent text-black border-accent"
                  : "border-line text-muted hover:bg-panel"
              }`}
            >
              {s === "all" ? "All sources" : s === "exif" ? "EXIF photos" : "MediaStore"}
            </button>
          ))}
        </div>

        {/* Filter result count */}
        <span className="text-xs text-muted ml-auto">
          {filteredCount === totalGps
            ? `${totalGps} points`
            : `${filteredCount} / ${totalGps} points`}
        </span>
      </div>

      {/* ── Main content: map + list ────────────────────────────────────── */}
      <div className="flex gap-3 flex-1 min-h-0">
        {/* Map panel — 65% width */}
        <div className="flex-1 min-w-0 card relative flex flex-col overflow-hidden">
          {/* Offline warning banner */}
          {tilesOffline && (
            <div className="absolute top-2 left-1/2 -translate-x-1/2 z-[999] bg-panel-2/95 border border-warn/40 text-warn text-xs px-3 py-1 rounded shadow pointer-events-none">
              Map tiles unavailable offline — markers show relative positions only
            </div>
          )}

          {filteredCount === 0 ? (
            <div className="flex items-center justify-center h-full text-muted text-sm">
              No points match the current filters.
            </div>
          ) : (
            <MapContainer
              center={[0, 0]}
              zoom={2}
              className="flex-1"
              // Disable attributionControl; we add it manually in the tile layer
            >
              {/* Tile layer — degrades gracefully if offline */}
              <TileLayer
                url={MAP_TILE_URL}
                attribution={MAP_TILE_ATTRIBUTION}
                errorTileUrl="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
                eventHandlers={{
                  tileerror: () => setTilesOffline(true),
                  tileload: () => setTilesOffline(false),
                }}
              />

              {/* Auto-fit bounds when filter changes */}
              <BoundsController points={visiblePoints} />
              {/* Fly to selected item from list panel */}
              <FlyController target={flyTarget} />

              {/* Marker cluster group — handles hundreds of overlapping markers */}
              <MarkerClusterGroup
                chunkedLoading
                maxClusterRadius={50}
                showCoverageOnHover={false}
              >
                {visiblePoints.map((p, i) => {
                  const color = markerColor(p.timestamp);
                  const filename = p.source_file
                    ? p.source_file.split(/[\\/]/).pop() ?? p.label
                    : p.label;
                  // Derive artifact_id from source_file (format: artifacts/<id>/filename)
                  const artifactId = p.source_file
                    ? p.source_file.split(/[\\/]/)[1] ?? null
                    : null;
                  const thumbUrl = artifactId
                    ? api.mediaUrl(caseId, artifactId)
                    : null;

                  return (
                    <Marker
                      key={`${i}-${p.latitude}-${p.longitude}`}
                      position={[p.latitude, p.longitude]}
                      icon={buildIcon(color)}
                      ref={(ref) => {
                        if (ref) markerRefs.current.set(i, ref);
                        else markerRefs.current.delete(i);
                      }}
                    >
                      <Popup
                        minWidth={220}
                        maxWidth={280}
                        className="location-popup"
                      >
                        <div style={{ fontFamily: "inherit", fontSize: 12 }}>
                          {/* Thumbnail */}
                          {thumbUrl && (
                            <img
                              src={thumbUrl}
                              alt={filename}
                              onError={(e) => {
                                (e.target as HTMLImageElement).style.display = "none";
                              }}
                              style={{
                                width: "100%",
                                maxHeight: 140,
                                objectFit: "cover",
                                borderRadius: 4,
                                marginBottom: 6,
                                background: "#1c2127",
                              }}
                            />
                          )}
                          {/* Metadata */}
                          <div style={{ fontWeight: 600, marginBottom: 2, wordBreak: "break-all" }}>
                            {filename}
                          </div>
                          <div style={{ color: "#8a939d", marginBottom: 4 }}>
                            {p.timestamp ? fmtTs(p.timestamp) : "No timestamp"}
                          </div>
                          <div style={{ fontFamily: "monospace", fontSize: 11 }}>
                            {p.latitude.toFixed(6)}, {p.longitude.toFixed(6)}
                          </div>
                          <div
                            style={{
                              marginTop: 6,
                              padding: "2px 6px",
                              background:
                                p.source === "exif" ? "#1c3a4a" : "#2a3a1c",
                              borderRadius: 3,
                              fontSize: 10,
                              display: "inline-block",
                              color: p.source === "exif" ? "#5b9bd5" : "#4fb477",
                            }}
                          >
                            {p.source}
                          </div>
                          {/* Deep link to Google Maps (opens in default browser if online) */}
                          <div style={{ marginTop: 6 }}>
                            <a
                              href={`https://www.google.com/maps?q=${p.latitude},${p.longitude}`}
                              target="_blank"
                              rel="noreferrer"
                              style={{ color: "#d8823c", fontSize: 11 }}
                            >
                              Open in Google Maps ↗
                            </a>
                          </div>
                        </div>
                      </Popup>
                    </Marker>
                  );
                })}
              </MarkerClusterGroup>
            </MapContainer>
          )}

          {/* "Show all" button when capped */}
          {!showAll && sorted.length > INITIAL_CAP && (
            <div className="absolute bottom-3 left-1/2 -translate-x-1/2 z-[999]">
              <button
                onClick={() => setShowAll(true)}
                className="btn-accent text-xs shadow-lg"
              >
                Show all {sorted.length} points (currently showing {INITIAL_CAP})
              </button>
            </div>
          )}

          {/* Legend */}
          <div className="absolute bottom-3 right-3 z-[999] card p-2 text-[10px] text-muted flex flex-col gap-1 pointer-events-none">
            <span className="flex items-center gap-1.5">
              <span className="h-2.5 w-2.5 rounded-full bg-live inline-block" />
              ≤ 7 days old
            </span>
            <span className="flex items-center gap-1.5">
              <span className="h-2.5 w-2.5 rounded-full bg-carved inline-block" />
              ≤ 30 days old
            </span>
            <span className="flex items-center gap-1.5">
              <span className="h-2.5 w-2.5 rounded-full bg-deletion inline-block" />
              Older / no date
            </span>
          </div>
        </div>

        {/* ── Right panel: recent images list ─────────────────────────── */}
        <div className="w-72 shrink-0 card flex flex-col overflow-hidden">
          <div className="p-3 border-b border-line shrink-0">
            <div className="text-xs font-medium text-ink">
              Recent geotagged images
            </div>
            <div className="text-[10px] text-muted mt-0.5">
              Click to centre map on location
            </div>
          </div>

          <div className="flex-1 overflow-y-auto">
            {recentItems.length === 0 ? (
              <div className="p-4 text-xs text-muted">
                No timestamped photos match the current filters.
              </div>
            ) : (
              recentItems.map((p, idx) => {
                const filename = p.source_file
                  ? p.source_file.split(/[\\/]/).pop() ?? p.label
                  : p.label;
                const artifactId = p.source_file
                  ? p.source_file.split(/[\\/]/)[1] ?? null
                  : null;
                const thumbUrl = artifactId
                  ? api.mediaUrl(caseId, artifactId)
                  : null;
                const color = markerColor(p.timestamp);

                return (
                  <button
                    key={idx}
                    onClick={() => handleItemClick(p, idx)}
                    className="w-full text-left flex gap-2 p-2 border-b border-line hover:bg-panel transition-colors"
                  >
                    {/* Thumbnail */}
                    <div className="shrink-0 w-12 h-12 rounded overflow-hidden bg-panel flex items-center justify-center">
                      {thumbUrl ? (
                        <img
                          src={thumbUrl}
                          alt={filename}
                          className="w-full h-full object-cover"
                          onError={(e) => {
                            const el = e.currentTarget.parentElement;
                            if (el) el.innerHTML = "<span style='font-size:18px'>🖼</span>";
                          }}
                        />
                      ) : (
                        <span className="text-lg">🖼</span>
                      )}
                    </div>

                    {/* Info */}
                    <div className="min-w-0 flex-1">
                      <div
                        className="text-[11px] font-medium text-ink truncate"
                        title={filename}
                      >
                        {filename}
                      </div>
                      <div className="text-[10px] text-muted mt-0.5">
                        {fmtTs(p.timestamp)}
                      </div>
                      <div className="text-[10px] font-mono text-muted/70 mt-0.5">
                        {p.latitude.toFixed(4)}, {p.longitude.toFixed(4)}
                      </div>
                      {/* Age dot */}
                      <span
                        className="inline-block mt-1 h-1.5 w-1.5 rounded-full"
                        style={{ background: color }}
                      />
                    </div>
                  </button>
                );
              })
            )}
          </div>

          {/* Disclaimer footer */}
          <div className="p-2 border-t border-line text-[9px] text-muted/70 leading-relaxed shrink-0">
            Locations from EXIF metadata only. Coordinates may not reflect
            actual movement if photos were shared or edited. Verify independently.
          </div>
        </div>
      </div>

      {/* ── Below-map data table ────────────────────────────────────────── */}
      <details className="shrink-0">
        <summary className="text-xs text-muted cursor-pointer hover:text-ink transition-colors select-none">
          Show raw data table ({filteredCount} rows)
        </summary>
        <div className="card mt-2 max-h-52 overflow-auto">
          <table className="w-full text-sm">
            <thead>
              <tr>
                <th className="th">Filename</th>
                <th className="th">Timestamp</th>
                <th className="th">Latitude</th>
                <th className="th">Longitude</th>
                <th className="th">Source</th>
              </tr>
            </thead>
            <tbody>
              {sorted.slice(0, 500).map((p, i) => {
                const filename = p.source_file
                  ? p.source_file.split(/[\\/]/).pop() ?? p.label
                  : p.label;
                return (
                  <tr
                    key={i}
                    className="hover:bg-panel cursor-pointer"
                    onClick={() => handleItemClick(p, i)}
                  >
                    <td className="td text-xs truncate max-w-[200px]" title={filename}>
                      {filename}
                    </td>
                    <td className="td font-mono text-xs">{fmtTs(p.timestamp)}</td>
                    <td className="td font-mono text-xs">{p.latitude.toFixed(6)}</td>
                    <td className="td font-mono text-xs">{p.longitude.toFixed(6)}</td>
                    <td className="td text-xs">
                      <span
                        className={`text-[10px] ${
                          p.source === "exif" ? "text-recovered" : "text-live"
                        }`}
                      >
                        {p.source}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
