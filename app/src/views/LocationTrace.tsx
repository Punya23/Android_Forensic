/**
 * LocationTraceView — every location source the acquisition found, in one map and table.
 *
 * The existing "Location Tracing" view plots `locations.json`, which is photo EXIF and the
 * last-known fix. This view reads `location_traces.json`: the unified dataset that also folds
 * in locations shared in chats, Google Maps navigation history and saved places, the
 * Play-services geolocation cache, cell towers, and map links opened in a browser.
 *
 * The design point of this screen is the distinction the merge makes possible. A single
 * "47 locations" figure invites the reading that the device was at 47 places, when most rows
 * are commonly places the user only looked up. So:
 *
 *   - Presence and interest are counted separately in the header, never summed into one number.
 *   - Every marker is coloured by category, and interest markers are drawn hollow so a glance
 *     at the map cannot mistake them for positions.
 *   - An incoming location share is badged as the counterparty's position, not the suspect's.
 *   - Rows with no coordinate (a map search by name) are kept and listed, because a search is
 *     a real trace even though it cannot be plotted.
 *
 * Data: derived/location_traces.json, location_trace_summary.json, location_impossible_travel.json
 */
import { useEffect, useMemo, useState } from "react";
import { MapContainer, TileLayer, Marker, Popup, Polyline, useMap } from "react-leaflet";
import L from "leaflet";
import type {
  ImpossibleTravel,
  LocationCategory,
  LocationTraceRow,
  LocationTraceSummary,
} from "../lib/types";
import { useDataset, fmtTs } from "../lib/hooks";
import { api } from "../lib/api";
import { SectionHeader, EmptyState } from "../components/common";

const MAP_TILE_URL = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";
const MAP_TILE_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

/** How many rows to render in the table before the "show all" control appears. */
const TABLE_CAP = 300;

// ---------------------------------------------------------------------------
// Category presentation
// ---------------------------------------------------------------------------

interface CategoryStyle {
  label: string;
  short: string;
  colour: string;
  /** What this category actually proves — shown in the legend and popups. */
  means: string;
}

const CATEGORY: Record<LocationCategory, CategoryStyle> = {
  device_fix: {
    label: "Device position fix",
    short: "GPS fix",
    colour: "#1f9d55",
    means: "The OS recorded this position for this device.",
  },
  media_capture: {
    label: "Photo / video captured here",
    short: "Media",
    colour: "#2b6cb0",
    means: "A photo or video carries this coordinate in its metadata.",
  },
  shared_location: {
    label: "Location shared in a conversation",
    short: "Shared",
    colour: "#b7791f",
    means: "A location was sent or received in a chat.",
  },
  network_inferred: {
    label: "Inferred from cell tower / WiFi",
    short: "Network",
    colour: "#6b46c1",
    means: "Derived from radio observations, not a satellite fix. Accuracy is coarse.",
  },
  navigation: {
    label: "Navigation destination / origin",
    short: "Navigation",
    colour: "#c05621",
    means: "The user entered this as a route endpoint — intent to travel, not arrival.",
  },
  interest: {
    label: "Looked up — not a position",
    short: "Interest",
    colour: "#718096",
    means: "A place searched, viewed or saved. It does not place the device here.",
  },
};

const CATEGORY_ORDER: LocationCategory[] = [
  "device_fix",
  "media_capture",
  "shared_location",
  "network_inferred",
  "navigation",
  "interest",
];

function styleFor(category: string): CategoryStyle {
  return CATEGORY[category as LocationCategory] ?? CATEGORY.interest;
}

/**
 * Filled marker = the device was here. Hollow = a place that was merely looked at.
 * The fill is doing real work: it is the difference between evidence of presence and
 * evidence of interest, and it has to survive being glanced at rather than read.
 */
function buildIcon(row: LocationTraceRow, highlighted: boolean): L.DivIcon {
  const { colour } = styleFor(row.category);
  const size = highlighted ? 18 : 13;
  const fill = row.is_presence ? colour : "transparent";
  const ring = row.is_presence ? "2px solid rgba(255,255,255,0.9)" : `2px dashed ${colour}`;
  return L.divIcon({
    className: "",
    html: `<div style="
      width:${size}px;height:${size}px;border-radius:50%;
      background:${fill};border:${ring};
      box-shadow:0 1px 4px rgba(0,0,0,0.5);
    "></div>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
    popupAnchor: [0, -(size / 2 + 4)],
  });
}

// ---------------------------------------------------------------------------
// Map helpers
// ---------------------------------------------------------------------------

function BoundsController({ points }: { points: LocationTraceRow[] }) {
  const map = useMap();
  useEffect(() => {
    const coords = points
      .filter((p) => p.latitude !== null && p.longitude !== null)
      .map((p) => [p.latitude as number, p.longitude as number] as [number, number]);
    if (coords.length === 0) return;
    map.fitBounds(L.latLngBounds(coords), { padding: [32, 32], maxZoom: 15 });
  }, [map, points]);
  return null;
}

function FlyController({ target }: { target: LocationTraceRow | null }) {
  const map = useMap();
  useEffect(() => {
    if (!target || target.latitude === null || target.longitude === null) return;
    map.flyTo([target.latitude, target.longitude], 16, { duration: 0.8 });
  }, [map, target]);
  return null;
}

// ---------------------------------------------------------------------------
// Main view
// ---------------------------------------------------------------------------

/** Per-source roll-ups — summarise_shared_locations / summarise_url_locations / get_location_summary. */
interface SharedLocationSummary {
  total: number;
  by_app: Record<string, number>;
  by_kind: Record<string, number>;
  live_shares: number;
  undated: number;
  first_seen: string | null;
  last_seen: string | null;
}
interface UrlLocationSummary {
  total: number;
  with_coordinates: number;
  searches_only: number;
  by_provider: Record<string, number>;
  destinations: number;
  note: string;
}
interface MapsLocationSummary {
  total: number;
  with_timestamp: number;
  unique_places: number;
  date_range?: { first?: string; last?: string };
  sources: Record<string, number>;
}

export function LocationTraceView({ caseId }: { caseId: string }) {
  const { data, loading } = useDataset<LocationTraceRow>(caseId, "location_traces");
  const [summary, setSummary] = useState<LocationTraceSummary | null>(null);
  const [anomalies, setAnomalies] = useState<ImpossibleTravel[]>([]);
  const [sharedSummary, setSharedSummary] = useState<SharedLocationSummary | null>(null);
  const [urlSummary, setUrlSummary] = useState<UrlLocationSummary | null>(null);
  const [mapsSummary, setMapsSummary] = useState<MapsLocationSummary | null>(null);
  const [showSources, setShowSources] = useState(false);

  const [enabled, setEnabled] = useState<Set<LocationCategory>>(
    () => new Set(CATEGORY_ORDER)
  );
  const [presenceOnly, setPresenceOnly] = useState(false);
  const [showPath, setShowPath] = useState(true);
  const [showAll, setShowAll] = useState(false);
  const [flyTarget, setFlyTarget] = useState<LocationTraceRow | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .dataset<LocationTraceSummary>(caseId, "location_trace_summary")
      .then((d) => alive && setSummary(d))
      .catch(() => alive && setSummary(null));
    api
      .dataset<ImpossibleTravel[]>(caseId, "location_impossible_travel")
      .then((d) => alive && setAnomalies(Array.isArray(d) ? d : []))
      .catch(() => alive && setAnomalies([]));
    // Per-source roll-ups feeding this unified trace. The trace already merges these
    // sources' rows, but these carry fields the merged view doesn't (e.g. shared-location
    // by-app breakdown, URL-derived by-provider breakdown, Maps' bounding box) — shown as a
    // collapsed "by source" panel rather than duplicating the map/table above.
    api
      .dataset<SharedLocationSummary>(caseId, "shared_location_summary")
      .then((d) => alive && setSharedSummary(d && d.total > 0 ? d : null))
      .catch(() => alive && setSharedSummary(null));
    api
      .dataset<UrlLocationSummary>(caseId, "url_location_summary")
      .then((d) => alive && setUrlSummary(d && d.total > 0 ? d : null))
      .catch(() => alive && setUrlSummary(null));
    api
      .dataset<MapsLocationSummary>(caseId, "maps_location_summary")
      .then((d) => alive && setMapsSummary(d && d.total > 0 ? d : null))
      .catch(() => alive && setMapsSummary(null));
    return () => {
      alive = false;
    };
  }, [caseId]);

  const filtered = useMemo(() => {
    return data.filter((r) => {
      if (!enabled.has(r.category as LocationCategory)) return false;
      if (presenceOnly && !r.is_presence) return false;
      return true;
    });
  }, [data, enabled, presenceOnly]);

  const mappable = useMemo(
    () => filtered.filter((r) => r.latitude !== null && r.longitude !== null),
    [filtered]
  );

  /**
   * The movement path is drawn from presence points only, in time order. Joining interest
   * points would draw a journey through places the user merely searched for — a route the
   * device never took, rendered as if it had.
   */
  const path = useMemo(() => {
    if (!showPath) return [];
    return mappable
      .filter((r) => r.is_presence && r.timestamp)
      .sort((a, b) => (a.timestamp ?? "").localeCompare(b.timestamp ?? ""))
      .map((r) => [r.latitude as number, r.longitude as number] as [number, number]);
  }, [mappable, showPath]);

  const unplottable = useMemo(
    () => filtered.filter((r) => r.latitude === null || r.longitude === null),
    [filtered]
  );

  const visibleRows = showAll ? filtered : filtered.slice(0, TABLE_CAP);

  function toggle(category: LocationCategory) {
    setEnabled((prev) => {
      const next = new Set(prev);
      if (next.has(category)) next.delete(category);
      else next.add(category);
      return next;
    });
  }

  if (loading) {
    return <EmptyState title="Loading location trace…" />;
  }

  if (data.length === 0) {
    return (
      <EmptyState
        title="No location trace for this case"
        detail={
          "No reachable artifact recorded a location. This is not evidence that the device " +
          "was never anywhere — media may carry no GPS tags, location services may have been " +
          "off, and app-private location stores need a root or full-image acquisition."
        }
      />
    );
  }

  const presence = summary?.presence_points ?? data.filter((r) => r.is_presence).length;
  const interest = summary?.interest_points ?? 0;

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-start justify-between gap-3">
        <SectionHeader
          title="Location Trace"
          sub={
            `${data.length} row(s) from ${summary?.sources_present?.length ?? "?"} source(s) — ` +
            `${presence} place the device, ${interest} record interest only`
          }
        />
        <a
          className="btn-ghost text-xs py-1 shrink-0 mt-1"
          href={`${api.base}/case/${caseId}/location_trace_geojson`}
          download={`${caseId}_location_trace.geojson`}
          title="Every plottable row above, as a GeoJSON FeatureCollection for GIS/mapping tools"
        >
          Export GeoJSON
        </a>
      </div>

      {(sharedSummary || urlSummary || mapsSummary) && (
        <div className="rounded-md border border-line px-4 py-3 text-sm">
          <button
            className="w-full flex items-center justify-between text-left"
            onClick={() => setShowSources((o) => !o)}
          >
            <span className="font-medium">Per-source breakdown</span>
            <span className="text-xs opacity-60">{showSources ? "hide" : "show"}</span>
          </button>
          {showSources && (
            <div className="mt-2 grid grid-cols-1 sm:grid-cols-3 gap-4 text-xs">
              {sharedSummary && (
                <div>
                  <div className="font-medium mb-1">Shared in conversations</div>
                  <div>{sharedSummary.total} share(s) · {sharedSummary.live_shares} live · {sharedSummary.undated} undated</div>
                  <div className="opacity-70 mt-1">
                    {Object.entries(sharedSummary.by_app)
                      .map(([app, n]) => `${app}: ${n}`)
                      .join(" · ")}
                  </div>
                </div>
              )}
              {urlSummary && (
                <div>
                  <div className="font-medium mb-1">From map links</div>
                  <div>
                    {urlSummary.total} link(s) · {urlSummary.with_coordinates} with coordinates ·{" "}
                    {urlSummary.searches_only} search-only
                  </div>
                  <div className="opacity-70 mt-1">
                    {Object.entries(urlSummary.by_provider)
                      .map(([p, n]) => `${p}: ${n}`)
                      .join(" · ")}
                  </div>
                  <div className="opacity-60 mt-1">{urlSummary.note}</div>
                </div>
              )}
              {mapsSummary && (
                <div>
                  <div className="font-medium mb-1">Google Maps</div>
                  <div>
                    {mapsSummary.total} record(s) · {mapsSummary.unique_places} unique place(s)
                  </div>
                  {mapsSummary.date_range?.first && (
                    <div className="opacity-70 mt-1">
                      {fmtTs(mapsSummary.date_range.first)} → {fmtTs(mapsSummary.date_range.last)}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* The distinction this whole screen exists to preserve. Stated before any map. */}
      <div className="rounded-md border border-amber-300/40 bg-amber-50/60 dark:bg-amber-950/20 px-4 py-3 text-sm leading-relaxed">
        <strong>Read the category before the coordinate.</strong> Only{" "}
        <strong>{presence}</strong> row(s) place this device at a coordinate. The other{" "}
        {interest} record a place that was searched, viewed or saved — that evidences interest
        in a location, not presence at it. An incoming location share records where the{" "}
        <em>other party</em> said they were. Absence of a location is not evidence the device
        was never somewhere; it means no artifact reachable at the tiers used recorded one.
      </div>

      {anomalies.length > 0 && (
        <div className="rounded-md border border-red-300/40 bg-red-50/60 dark:bg-red-950/20 px-4 py-3 text-sm">
          <div className="font-medium mb-1">
            {anomalies.length} impossible-travel anomaly(ies) — requires verification
          </div>
          <ul className="space-y-1 text-xs">
            {anomalies.slice(0, 5).map((a, i) => (
              <li key={i} className="font-mono">
                {fmtTs(a.from.timestamp)} → {fmtTs(a.to.timestamp)} · {a.distance_km} km in{" "}
                {a.hours} h = {a.implied_kmh} km/h ({a.from.source} → {a.to.source})
              </li>
            ))}
          </ul>
          <p className="mt-2 text-xs opacity-80">
            At least one reading in each pair is wrong or came from elsewhere. Common causes: a
            location-spoofing app, a mis-parsed or non-UTC timestamp, media copied onto the
            device, or a shared device. None of these is a finding on its own.
          </p>
        </div>
      )}

      {/* Category legend doubles as the filter — the reader learns the taxonomy by using it. */}
      <div className="flex flex-wrap gap-2 items-center">
        {CATEGORY_ORDER.map((key) => {
          const style = CATEGORY[key];
          const count = data.filter((r) => r.category === key).length;
          const on = enabled.has(key);
          if (count === 0) return null;
          return (
            <button
              key={key}
              onClick={() => toggle(key)}
              title={style.means}
              className={`flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs transition ${
                on ? "opacity-100" : "opacity-40"
              }`}
              style={{ borderColor: style.colour }}
            >
              <span
                className="inline-block h-2.5 w-2.5 rounded-full"
                style={{
                  background: CATEGORY_ORDER.indexOf(key) < 4 ? style.colour : "transparent",
                  border: `2px solid ${style.colour}`,
                }}
              />
              {style.label}
              <span className="opacity-60">{count}</span>
            </button>
          );
        })}
        <label className="ml-2 flex items-center gap-1.5 text-xs">
          <input
            type="checkbox"
            checked={presenceOnly}
            onChange={(e) => setPresenceOnly(e.target.checked)}
          />
          Presence only
        </label>
        <label className="flex items-center gap-1.5 text-xs">
          <input
            type="checkbox"
            checked={showPath}
            onChange={(e) => setShowPath(e.target.checked)}
          />
          Draw movement path
        </label>
      </div>

      <div className="h-[420px] rounded-md overflow-hidden border border-line">
        <MapContainer center={[20, 78]} zoom={4} style={{ height: "100%", width: "100%" }}>
          <TileLayer url={MAP_TILE_URL} attribution={MAP_TILE_ATTRIBUTION} />
          <BoundsController points={mappable} />
          <FlyController target={flyTarget} />
          {path.length > 1 && (
            <Polyline positions={path} pathOptions={{ color: "#1f9d55", weight: 2, opacity: 0.6 }} />
          )}
          {mappable.map((r, i) => (
            <Marker
              key={i}
              position={[r.latitude as number, r.longitude as number]}
              icon={buildIcon(r, flyTarget === r)}
            >
              <Popup>
                <div className="text-xs space-y-1">
                  <div className="font-semibold">{styleFor(r.category).label}</div>
                  <div className="opacity-70">{styleFor(r.category).means}</div>
                  <div className="font-mono">
                    {(r.latitude as number).toFixed(6)}, {(r.longitude as number).toFixed(6)}
                  </div>
                  <div>{fmtTs(r.timestamp)}</div>
                  {r.place_name && <div>{r.place_name}</div>}
                  {r.address && <div className="opacity-70">{r.address}</div>}
                  {r.flags.includes("counterparty-position") && (
                    <div className="text-red-700">
                      Position claimed by the other party, not this device.
                    </div>
                  )}
                  <div className="opacity-60">
                    {r.source_label} · {r.tier}
                  </div>
                  <div className="opacity-50">{r.provenance}</div>
                </div>
              </Popup>
            </Marker>
          ))}
        </MapContainer>
      </div>

      {unplottable.length > 0 && (
        <div className="rounded-md border border-line px-4 py-3 text-sm">
          <div className="font-medium mb-1">
            {unplottable.length} trace(s) with no coordinate
          </div>
          <p className="text-xs opacity-70 mb-2">
            A place searched by name is a location trace even though it cannot be plotted.
            Dropping these would lose evidence of interest in a place.
          </p>
          <ul className="text-xs space-y-0.5">
            {unplottable.slice(0, 20).map((r, i) => (
              <li key={i}>
                <span className="font-mono opacity-60">{fmtTs(r.timestamp)}</span>{" "}
                {r.place_name || r.label} <span className="opacity-50">— {r.source_label}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-xs uppercase opacity-60">
            <tr>
              <th className="py-2 pr-3">Timestamp (UTC)</th>
              <th className="py-2 pr-3">What it evidences</th>
              <th className="py-2 pr-3">Coordinates</th>
              <th className="py-2 pr-3">Place / detail</th>
              <th className="py-2 pr-3">Source</th>
              <th className="py-2 pr-3">Tier</th>
            </tr>
          </thead>
          <tbody>
            {visibleRows.map((r, i) => {
              const style = styleFor(r.category);
              return (
                <tr
                  key={i}
                  className="border-t border-line hover:bg-black/5 dark:hover:bg-white/5 cursor-pointer"
                  onClick={() => setFlyTarget(r)}
                >
                  <td className="py-1.5 pr-3 font-mono text-xs whitespace-nowrap">
                    {r.timestamp ? fmtTs(r.timestamp) : "undated"}
                  </td>
                  <td className="py-1.5 pr-3">
                    <span
                      className="inline-flex items-center gap-1.5 rounded px-1.5 py-0.5 text-xs"
                      style={{ color: style.colour, background: `${style.colour}1a` }}
                    >
                      {style.short}
                    </span>
                    {r.flags.includes("counterparty-position") && (
                      <span className="ml-1 rounded bg-red-100 px-1.5 py-0.5 text-[10px] text-red-800">
                        OTHER PARTY
                      </span>
                    )}
                    {r.flags.includes("live-location") && (
                      <span className="ml-1 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] text-amber-800">
                        LIVE
                      </span>
                    )}
                  </td>
                  <td className="py-1.5 pr-3 font-mono text-xs">
                    {r.latitude !== null && r.longitude !== null
                      ? `${r.latitude.toFixed(6)}, ${r.longitude.toFixed(6)}`
                      : "—"}
                  </td>
                  <td className="py-1.5 pr-3">{r.place_name || r.label || "—"}</td>
                  <td className="py-1.5 pr-3 text-xs opacity-70">{r.source_label}</td>
                  <td className="py-1.5 pr-3 text-xs opacity-70">{r.tier}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {!showAll && filtered.length > TABLE_CAP && (
          <button
            className="mt-3 text-sm underline opacity-70"
            onClick={() => setShowAll(true)}
          >
            Show all {filtered.length} rows
          </button>
        )}
      </div>
    </div>
  );
}
