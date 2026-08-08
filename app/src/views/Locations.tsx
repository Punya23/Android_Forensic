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
 *
 * ---------------------------------------------------------------------------
 * Extended section (below the map): the older "photo geotagging" analysis module
 * (engine/triage/pipeline.py's `location_analysis_result`, Tasks 6-10). This is a
 * SEPARATE extraction pass from the one above — it scans only WhatsApp/Telegram/
 * SMS/Instagram media folders in the staging + artifacts tree and attributes every
 * GPS point to the specific app it came through, which `locations.json` cannot do
 * (its "exif" source is app-agnostic). It also derives home/work/frequent places
 * and simple movement-based anomalies from that same app-tagged point set.
 *
 * Data sources: derived/media_locations.json (array), derived/location_places.json
 * (object), derived/location_anomalies.json (array), derived/location_summary.json
 * (object). derived/location_timeline.json is deliberately NOT fetched here: its
 * "events" are the same media_locations dicts reshaped, and its "statistics" are a
 * subset of location_summary.statistics — fetching it would duplicate, not add,
 * information already rendered below (the one extra field, a per-app breakdown, is
 * computed client-side from media_locations instead of a second round trip).
 *
 * NOTE: some media in real acquisitions carry EXIF GPS {lat: 0, lon: 0} — cameras
 * write this "null island" placeholder when GPS tagging failed rather than omitting
 * the tag. Anything at (0, 0) is flagged inline rather than plotted or reasoned
 * about as a real position off the coast of West Africa.
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
// @ts-ignore — ships no bundled types; leaflet.markercluster's own types via
// @types/leaflet.markercluster cover the underlying L.markerClusterGroup() call.
// react-leaflet-markercluster (the older, unmaintained package) targets
// react-leaflet v3's context API and crashes under v4 with "No context provided:
// useLeafletContext()"; this fork is built on @react-leaflet/core for v4.
import MarkerClusterGroup from "@changey/react-leaflet-markercluster";
import type { LocationPoint, Severity } from "../lib/types";
import { useDataset, fmtTs } from "../lib/hooks";
import { api } from "../lib/api";
import { SectionHeader, EmptyState, StatCard } from "../components/common";
import { SeverityBadge } from "../components/Badges";

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

/**
 * Square-ish DivIcon for the extended (messaging-app media) marker layer, coloured
 * by source app. Deliberately shaped differently from `buildIcon`'s circular EXIF
 * markers so the two point sets stay visually distinguishable when both are shown.
 */
function buildMediaIcon(color: string): L.DivIcon {
  return L.divIcon({
    className: "",
    html: `<div style="
      width:12px;height:12px;
      border-radius:3px;
      background:${color};
      border:2px solid rgba(255,255,255,0.85);
      box-shadow:0 1px 4px rgba(0,0,0,0.6);
    "></div>`,
    iconSize: [12, 12],
    iconAnchor: [6, 6],
    popupAnchor: [0, -10],
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
// Extended location analysis — types
//
// Mirrors the exact shapes written by engine/triage/pipeline.py's Task 6-10
// helpers (media_location.py, place_identification.py, location_anomaly.py,
// location_summary.py). Defined locally rather than in lib/types.ts, following
// the same convention AntiForensics.tsx uses for its dataset-specific shapes.
// ---------------------------------------------------------------------------

/** One row of derived/media_locations.json — a messaging-app media GPS point. */
interface MediaGpsPoint {
  file: string;
  gps: { lat: number; lon: number } | null;
  altitude: number | null;
  timestamp: string | null;
  source: string; // "whatsapp" | "telegram" | "sms" | "instagram"
  media_type: string; // "image" | "video"
  device_make: string | null;
  device_model: string | null;
  software: string | null;
  filename_meta: { date: string | null; app: string | null; type: string | null; seq: string | null; raw: string };
}

/** A geographic cluster from gps_clustering.py, labelled by place_identification.py. */
interface PlaceCluster {
  center: { lat: number; lon: number };
  count: number;
  first_visit: string | null;
  last_visit: string | null;
  bounds: { min_lat: number; max_lat: number; min_lon: number; max_lon: number };
  place_type: "home" | "work" | "frequent";
  place_name: string; // NOT a reverse-geocoded name — just "<lat>, <lon>" formatted.
  confidence: number; // 0..1
}

/** derived/location_places.json */
interface LocationPlaces {
  home: PlaceCluster | null;
  work: PlaceCluster | null;
  frequent_places: PlaceCluster[];
  total_clusters: number;
  total_gps_points: number;
}

/** One row of derived/location_anomalies.json. */
interface LocationAnomaly {
  type: string; // "late_night_visit" | "unusual_location" | "new_location"
  location: { lat: number | null; lon: number | null };
  timestamp: string | null;
  severity: Severity; // "critical" | "warn" | "info" — matches lib/types exactly
  explanation: string;
}

/** derived/location_summary.json (top-level fields actually used by this view). */
interface LocationSummary {
  total_locations: number;
  unique_places: number;
  time_span: string;
  movement_pattern: string;
  statistics: {
    count: number;
    with_gps: number;
    unique_places: number;
    time_span: string;
    time_span_days: number | null;
    first_event: string | null;
    last_event: string | null;
    movement_pattern: string;
  };
  anomaly_stats: {
    total_anomalies: number;
    by_type: Record<string, number>;
    by_severity: Record<string, number>;
  };
}

// ---------------------------------------------------------------------------
// Extended location analysis — helpers
// ---------------------------------------------------------------------------

/** Per-app marker/badge colour, matching the palette the engine's own HTML report uses. */
const APP_COLORS: Record<string, string> = {
  whatsapp: "#25D366",
  telegram: "#2AABEE",
  sms: "#FF6B35",
  instagram: "#C13584",
};
function appColor(source: string): string {
  return APP_COLORS[source] ?? "#888888";
}
function appLabel(source: string): string {
  if (!source) return "Unknown";
  return source.charAt(0).toUpperCase() + source.slice(1);
}

/**
 * Cameras/apps write EXIF GPS {lat: 0, lon: 0} when tagging failed rather than
 * omitting the tag — a real "null island" false-position artefact seen in actual
 * acquisitions, not a hypothetical. Treated as absent-GPS, never as a real fix
 * off the coast of West Africa.
 */
function isNullIsland(lat: number | null | undefined, lon: number | null | undefined): boolean {
  return lat != null && lon != null && Math.abs(lat) < 0.0001 && Math.abs(lon) < 0.0001;
}

function fmtPct(n: number | null | undefined): string {
  if (n == null || isNaN(n)) return "—";
  return `${Math.round(n * 100)}%`;
}

const ANOMALY_TYPE_LABEL: Record<string, string> = {
  late_night_visit: "Late-night visit",
  unusual_location: "Unusual location",
  new_location: "New location",
};
function anomalyTypeLabel(t: string): string {
  return ANOMALY_TYPE_LABEL[t] ?? t;
}

function gmapsUrl(lat: number, lon: number): string {
  return `https://www.google.com/maps?q=${lat},${lon}`;
}

/** One row of the home/work/frequent places table. Honest about what "not identified" means. */
function PlaceRow({ label, place }: { label: string; place: PlaceCluster | null }) {
  if (!place) {
    const why =
      label === "Home"
        ? "no cluster of night-time (22:00–06:00 UTC) points met the minimum for this heuristic"
        : label === "Work"
        ? "no cluster of work-hours (09:00–17:00 UTC, Mon–Fri) points met the minimum for this heuristic"
        : "fewer than 3 visits within 500 m of any point in this set";
    return (
      <tr>
        <td className="td text-xs font-medium text-ink">{label}</td>
        <td className="td text-xs text-muted italic" colSpan={4}>
          Not identified — {why}. Not a finding that no such place exists.
        </td>
      </tr>
    );
  }
  return (
    <tr>
      <td className="td text-xs font-medium text-ink">{label}</td>
      <td className="td font-mono text-xs">
        {place.center.lat.toFixed(6)}, {place.center.lon.toFixed(6)}{" "}
        <a
          href={gmapsUrl(place.center.lat, place.center.lon)}
          target="_blank"
          rel="noreferrer"
          className="text-accent"
        >
          Map ↗
        </a>
      </td>
      <td className="td text-xs">{place.count}</td>
      <td className="td text-xs text-muted">
        {fmtTs(place.first_visit)} → {fmtTs(place.last_visit)}
      </td>
      <td className="td text-xs">{fmtPct(place.confidence)}</td>
    </tr>
  );
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

  // -- Extended location analysis (media_locations / places / anomalies / summary) --
  const { data: mediaLocations, loading: mediaLoading } = useDataset<MediaGpsPoint>(
    caseId,
    "media_locations"
  );
  const { data: anomalies, loading: anomaliesLoading } = useDataset<LocationAnomaly>(
    caseId,
    "location_anomalies"
  );
  const [places, setPlaces] = useState<LocationPlaces | null>(null);
  const [locSummary, setLocSummary] = useState<LocationSummary | null>(null);
  useEffect(() => {
    let alive = true;
    api
      .dataset<LocationPlaces>(caseId, "location_places")
      .then((d) => alive && setPlaces(d && Object.keys(d).length ? d : null))
      .catch(() => alive && setPlaces(null));
    api
      .dataset<LocationSummary>(caseId, "location_summary")
      .then((d) => alive && setLocSummary(d && Object.keys(d).length ? d : null))
      .catch(() => alive && setLocSummary(null));
    return () => {
      alive = false;
    };
  }, [caseId]);

  /** Whether to overlay the messaging-app media points on the map above (opt-in). */
  const [showMediaGps, setShowMediaGps] = useState(false);

  /** Media points with real (non-null-island) coordinates — the only ones plotted. */
  const mediaMappable = useMemo(
    () =>
      mediaLocations.filter(
        (m) => m.gps && m.gps.lat != null && m.gps.lon != null && !isNullIsland(m.gps.lat, m.gps.lon)
      ),
    [mediaLocations]
  );
  const mediaNullIslandCount = mediaLocations.length - mediaMappable.length;

  /** Per-app counts — computed client-side instead of fetching location_timeline. */
  const mediaBySource = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const m of mediaLocations) {
      counts[m.source] = (counts[m.source] ?? 0) + 1;
    }
    return counts;
  }, [mediaLocations]);

  // Only EXIF / MediaStore points are useful for photo-tracing; skip pure
  // "dumpsys" last-known-fix points from the movement slider (they have no image).
  const photoPoints = useMemo(
    () => data.filter((p) => p.source !== "dumpsys"),
    [data]
  );

  /** photoPoints with a real (non-null-island) fix — the only ones plotted or
   * flown to. Same split as mediaMappable above; null-island rows still appear in
   * the raw table below, just flagged instead of pinned. */
  const mappablePoints = useMemo(
    () => photoPoints.filter((p) => !isNullIsland(p.latitude, p.longitude)),
    [photoPoints]
  );
  const photoNullIslandCount = photoPoints.length - mappablePoints.length;

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
  // Same date-range/source filters applied to two different base sets: `sorted`
  // (mappable-only — drives the map, bounds-fit, and the fly-to list panel) and
  // `sortedAll` (everything, including null-island rows — drives the raw table so
  // a photo with a broken GPS tag doesn't just disappear from it).
  function sortAndFilter(pts: LocationPoint[]): LocationPoint[] {
    let out = [...pts].sort((a, b) => {
      const ta = parseTs(a.timestamp)?.getTime() ?? 0;
      const tb = parseTs(b.timestamp)?.getTime() ?? 0;
      return tb - ta; // most recent first
    });

    if (sourceFilter !== "all") {
      out = out.filter((p) => p.source === sourceFilter);
    }

    if (fromDate) {
      const from = new Date(fromDate).getTime();
      out = out.filter((p) => {
        const t = parseTs(p.timestamp);
        return t ? t.getTime() >= from : false;
      });
    }
    if (toDate) {
      // include the whole day
      const to = new Date(toDate).getTime() + 86_399_999;
      out = out.filter((p) => {
        const t = parseTs(p.timestamp);
        return t ? t.getTime() <= to : false;
      });
    }

    return out;
  }

  const sorted = useMemo(
    () => sortAndFilter(mappablePoints),
    [mappablePoints, sourceFilter, fromDate, toDate]
  );
  const sortedAll = useMemo(
    () => sortAndFilter(photoPoints),
    [photoPoints, sourceFilter, fromDate, toDate]
  );

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
  // "Geotagged" means a real fix — null-island rows aren't a position, so they're
  // not counted here even though they still show up in the raw table below.
  const totalGps = mappablePoints.length;
  const filteredCount = sorted.length;

  return (
    <div className="p-4 h-full flex flex-col gap-3 overflow-y-auto">
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

      {photoNullIslandCount > 0 && (
        <p className="text-xs text-warn bg-warn/5 border border-warn/30 rounded px-3 py-2 shrink-0 leading-relaxed">
          ⚠ {photoNullIslandCount} photo{photoNullIslandCount === 1 ? "" : "s"} carried EXIF GPS of exactly
          0.000000, 0.000000 — almost certainly a camera/app writing a placeholder when GPS tagging failed,
          not a real position. Excluded from the map and count above; still listed in the raw table below,
          flagged, for completeness.
        </p>
      )}

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

        {/* Overlay toggle for the extended (messaging-app media) point set */}
        {mediaMappable.length > 0 && (
          <label className="flex items-center gap-1.5 text-xs text-muted ml-2">
            <input
              type="checkbox"
              checked={showMediaGps}
              onChange={(e) => setShowMediaGps(e.target.checked)}
            />
            Overlay messaging-app media GPS ({mediaMappable.length})
          </label>
        )}

        {/* Filter result count */}
        <span className="text-xs text-muted ml-auto">
          {filteredCount === totalGps
            ? `${totalGps} points`
            : `${filteredCount} / ${totalGps} points`}
        </span>
      </div>

      {/* ── Main content: map + list ────────────────────────────────────── */}
      <div className="flex gap-3 h-[520px] shrink-0">
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

              {/* Optional overlay: messaging-app media GPS (media_locations), app-attributed */}
              {showMediaGps && mediaMappable.length > 0 && (
                <MarkerClusterGroup chunkedLoading maxClusterRadius={50} showCoverageOnHover={false}>
                  {mediaMappable.map((m, i) => {
                    const lat = m.gps!.lat;
                    const lon = m.gps!.lon;
                    const filename = m.file.split(/[\\/]/).pop() ?? m.file;
                    const device =
                      [m.device_make, m.device_model].filter(Boolean).join(" ") || "Unknown device";
                    return (
                      <Marker
                        key={`media-${i}-${lat}-${lon}`}
                        position={[lat, lon]}
                        icon={buildMediaIcon(appColor(m.source))}
                      >
                        <Popup minWidth={200} maxWidth={260} className="location-popup">
                          <div style={{ fontFamily: "inherit", fontSize: 12 }}>
                            <div
                              style={{
                                display: "inline-block",
                                padding: "2px 6px",
                                borderRadius: 3,
                                fontSize: 10,
                                fontWeight: 700,
                                color: "#fff",
                                background: appColor(m.source),
                                marginBottom: 6,
                              }}
                            >
                              {appLabel(m.source).toUpperCase()}
                            </div>
                            <div style={{ fontWeight: 600, marginBottom: 2, wordBreak: "break-all" }}>
                              {filename}
                            </div>
                            <div style={{ color: "#8a939d", marginBottom: 4 }}>
                              {m.timestamp ? fmtTs(m.timestamp) : "No timestamp"}
                            </div>
                            <div style={{ fontFamily: "monospace", fontSize: 11 }}>
                              {lat.toFixed(6)}, {lon.toFixed(6)}
                            </div>
                            <div style={{ color: "#8a939d", marginTop: 2 }}>{device}</div>
                            {m.altitude != null && (
                              <div style={{ color: "#8a939d" }}>Alt: {m.altitude.toFixed(1)} m</div>
                            )}
                            <div style={{ marginTop: 6 }}>
                              <a
                                href={gmapsUrl(lat, lon)}
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
              )}
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
            {showMediaGps && mediaMappable.length > 0 && (
              <span className="flex items-center gap-1.5 pt-1 border-t border-line mt-1">
                <span className="h-2.5 w-2.5 rounded-[3px] bg-muted inline-block" />
                Messaging-app media (square, colour = app)
              </span>
            )}
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
              {sortedAll.slice(0, 500).map((p, i) => {
                const filename = p.source_file
                  ? p.source_file.split(/[\\/]/).pop() ?? p.label
                  : p.label;
                const nullIsland = isNullIsland(p.latitude, p.longitude);
                return (
                  <tr
                    key={i}
                    className={nullIsland ? "" : "hover:bg-panel cursor-pointer"}
                    onClick={nullIsland ? undefined : () => handleItemClick(p, i)}
                  >
                    <td className="td text-xs truncate max-w-[200px]" title={filename}>
                      {filename}
                    </td>
                    <td className="td font-mono text-xs">{fmtTs(p.timestamp)}</td>
                    {nullIsland ? (
                      <td className="td text-xs text-warn" colSpan={2} title="GPS tag present but zero-filled — no real fix">
                        ⚠ no GPS fix (0.000000, 0.000000)
                      </td>
                    ) : (
                      <>
                        <td className="td font-mono text-xs">{p.latitude.toFixed(6)}</td>
                        <td className="td font-mono text-xs">{p.longitude.toFixed(6)}</td>
                      </>
                    )}
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

      {/* ══════════════════════════════════════════════════════════════════
          Extended location analysis — messaging-app media, places, anomalies.
          Older "photo geotagging" module (Tasks 6-10); separate data sources
          from everything above (see file header comment for why it's kept
          distinct rather than merged into the EXIF trace).
         ══════════════════════════════════════════════════════════════════ */}
      <hr className="border-line shrink-0" />

      <div className="shrink-0 pb-4">
        <SectionHeader
          title="Messaging-App Media, Places & Anomalies"
          sub="A second, narrower GPS extraction restricted to WhatsApp / Telegram / SMS / Instagram media — every point below is attributed to the app it came through, which the EXIF trace above cannot do."
        />

        {mediaLoading || anomaliesLoading ? (
          <div className="text-xs text-muted py-4">Loading extended location analysis…</div>
        ) : mediaLocations.length === 0 ? (
          <EmptyState
            title="No messaging-app media with GPS data"
            detail="No WhatsApp, Telegram, SMS/MMS or Instagram media in the staging/artifacts tree carried EXIF GPS data. This does not mean no such media exists — GPS tagging may have been off, or EXIF may have been stripped in transit (common for images forwarded through messaging apps)."
          />
        ) : (
          <>
            {/* Summary strip (derived/location_summary.json) */}
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3 mb-3">
              <StatCard n={mediaLocations.length} label="Messaging-app GPS points" />
              <StatCard n={mediaMappable.length} label="With real coordinates" />
              <StatCard n={locSummary?.unique_places ?? "—"} label="Clusters (≤0.5 km)" />
              <StatCard n={locSummary?.time_span ?? "—"} label="Time span" />
              <StatCard n={locSummary?.movement_pattern ?? "—"} label="Dominant movement" tone="text-accent" />
              <StatCard
                n={anomalies.length}
                label="Anomalies flagged"
                tone={anomalies.length > 0 ? "text-warn" : "text-ink"}
              />
            </div>

            {mediaNullIslandCount > 0 && (
              <p className="text-xs text-warn bg-warn/5 border border-warn/30 rounded px-3 py-2 mb-4 leading-relaxed">
                ⚠ {mediaNullIslandCount} point{mediaNullIslandCount === 1 ? "" : "s"} carried EXIF GPS of
                exactly 0.000000, 0.000000 — almost certainly a camera/app writing a placeholder when GPS
                tagging failed, not a real position. Excluded from the map overlay and from "with real
                coordinates" above; still listed in the raw table below for completeness.
              </p>
            )}

            {/* Per-app breakdown — computed client-side rather than fetching location_timeline */}
            <div className="flex flex-wrap items-center gap-3 mb-5">
              <span className="text-[11px] text-muted">By app:</span>
              {Object.entries(mediaBySource).map(([src, n]) => (
                <span key={src} className="flex items-center gap-1.5 text-xs">
                  <span
                    className="h-2 w-2 rounded-[2px] inline-block"
                    style={{ background: appColor(src) }}
                  />
                  {appLabel(src)} <span className="text-muted">{n}</span>
                </span>
              ))}
            </div>

            {/* Places (derived/location_places.json) */}
            <h3 className="text-sm font-semibold text-ink mb-1">Places (home / work / frequent)</h3>
            <p className="text-[11px] text-muted mb-2 leading-relaxed">
              Home and work are a time-of-day heuristic over this app-tagged point set only (most-visited
              cluster during 22:00–06:00 UTC = home; 09:00–17:00 UTC weekdays = work) — not a place
              lookup, and not run over the full EXIF trace above. Confidence reflects visit count, hour
              coverage and days spanned, not certainty. Coordinates are raw lat/lon; no reverse-geocoding
              is performed, so no place name is ever asserted.
            </p>
            {!places ? (
              <div className="card p-3 text-xs text-muted mb-6">
                Place identification did not run or produced no result for this case.
              </div>
            ) : (
              <div className="card overflow-auto mb-6">
                <table className="w-full text-sm">
                  <thead>
                    <tr>
                      <th className="th w-24">Type</th>
                      <th className="th">Coordinates</th>
                      <th className="th w-16">Visits</th>
                      <th className="th w-56">First → last visit</th>
                      <th className="th w-24">Confidence</th>
                    </tr>
                  </thead>
                  <tbody>
                    <PlaceRow label="Home" place={places.home} />
                    <PlaceRow label="Work" place={places.work} />
                    {places.frequent_places.slice(0, 15).map((p, i) => (
                      <PlaceRow key={i} label={`Frequent #${i + 1}`} place={p} />
                    ))}
                  </tbody>
                </table>
                {places.frequent_places.length > 15 && (
                  <div className="text-[11px] text-muted px-3 py-2 border-t border-line">
                    +{places.frequent_places.length - 15} more frequent cluster(s) not shown.
                  </div>
                )}
              </div>
            )}

            {/* Anomalies (derived/location_anomalies.json) */}
            <h3 className="text-sm font-semibold text-ink mb-1">Anomalies</h3>
            <p className="text-[11px] text-muted mb-2 leading-relaxed">
              Three simple heuristics over the app-tagged point set: a late-night timestamp, a
              statistical outlier from the geographic centroid, or a point never seen in the earlier 70%
              of the timeline. None of these is evidence of wrongdoing on its own — each has ordinary
              explanations (a night shift, travel, a new phone, a photo forwarded from elsewhere).
            </p>
            {anomalies.length === 0 ? (
              <div className="card p-3 text-xs text-muted mb-6">
                No anomalies were flagged. This means these specific checks found nothing in the
                messaging-app media points collected — it is not a finding that movement was
                unremarkable, and a different or larger media set could change the picture.
              </div>
            ) : (
              <div className="card divide-y divide-line mb-6">
                {anomalies.map((a, i) => {
                  const nullIsland = isNullIsland(a.location.lat, a.location.lon);
                  const hasCoords = a.location.lat != null && a.location.lon != null;
                  return (
                    <div key={i} className="p-3 flex flex-wrap items-center gap-2">
                      <SeverityBadge s={a.severity} />
                      <span className="text-xs font-medium text-ink">{anomalyTypeLabel(a.type)}</span>
                      <span className="text-xs font-mono text-muted">{fmtTs(a.timestamp)}</span>
                      <span className="text-xs font-mono text-muted">
                        {hasCoords
                          ? `${a.location.lat!.toFixed(5)}, ${a.location.lon!.toFixed(5)}`
                          : "—"}
                      </span>
                      {hasCoords && !nullIsland && (
                        <a
                          href={gmapsUrl(a.location.lat as number, a.location.lon as number)}
                          target="_blank"
                          rel="noreferrer"
                          className="text-xs text-accent"
                        >
                          Map ↗
                        </a>
                      )}
                      <div className="w-full text-xs text-muted leading-relaxed">
                        {a.explanation}
                        {nullIsland && (
                          <span className="text-warn">
                            {" "}
                            — this coordinate is the null-island placeholder (0, 0); "distance from
                            centroid" here most likely reflects a missing GPS tag, not an actual unusual
                            location.
                          </span>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}

            {/* Raw media_locations table */}
            <details className="shrink-0">
              <summary className="text-xs text-muted cursor-pointer hover:text-ink transition-colors select-none">
                Show messaging-app media GPS table ({mediaLocations.length} rows)
              </summary>
              <div className="card mt-2 max-h-52 overflow-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr>
                      <th className="th">App</th>
                      <th className="th">Filename</th>
                      <th className="th">Timestamp</th>
                      <th className="th">Latitude</th>
                      <th className="th">Longitude</th>
                      <th className="th">Device</th>
                    </tr>
                  </thead>
                  <tbody>
                    {mediaLocations.slice(0, 500).map((m, i) => {
                      const filename = m.file.split(/[\\/]/).pop() ?? m.file;
                      const lat = m.gps?.lat;
                      const lon = m.gps?.lon;
                      const nullIsland = isNullIsland(lat, lon);
                      return (
                        <tr key={i} className="hover:bg-panel">
                          <td className="td text-xs">
                            <span style={{ color: appColor(m.source) }}>{appLabel(m.source)}</span>
                          </td>
                          <td className="td text-xs truncate max-w-[200px]" title={filename}>
                            {filename}
                          </td>
                          <td className="td font-mono text-xs">{fmtTs(m.timestamp)}</td>
                          <td className="td font-mono text-xs">
                            {lat != null ? lat.toFixed(6) : "—"}
                            {nullIsland && <span className="text-warn ml-1">⚠ likely no GPS</span>}
                          </td>
                          <td className="td font-mono text-xs">{lon != null ? lon.toFixed(6) : "—"}</td>
                          <td className="td text-xs text-muted">
                            {[m.device_make, m.device_model].filter(Boolean).join(" ") || "—"}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </details>
          </>
        )}
      </div>
    </div>
  );
}
