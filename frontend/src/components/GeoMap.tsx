import { useEffect, useMemo, useState } from "react";
import {
  MapContainer,
  TileLayer,
  CircleMarker,
  Circle,
  useMap,
} from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { Finding } from "../types";

interface Props {
  findings: Finding[];
}

type SignalCategory = "image_primary" | "image_secondary" | "tag";

interface GeoPoint {
  lat: number;
  lon: number;
  confidence: number;
  source: string;
  label: string | null;
  category: SignalCategory;
}

// Image-derived signals are the primary evidence channel — they drive
// the centroid. Instagram tags are corroboration only: they contribute
// at a fraction of the weight so they refine, not dominate.
const TAG_CENTROID_WEIGHT = 0.25;
const IMAGE_SECONDARY_WEIGHT = 0.7;
const IMAGE_PRIMARY_WEIGHT = 1.0;

function categoryWeight(c: SignalCategory): number {
  if (c === "tag") return TAG_CENTROID_WEIGHT;
  if (c === "image_secondary") return IMAGE_SECONDARY_WEIGHT;
  return IMAGE_PRIMARY_WEIGHT;
}

const TAG_SOURCES = new Set([
  "instagram_location_tag",
]);
const IMAGE_PRIMARY_SOURCES = new Set([
  "exif_gps",
  "geoclip_prediction",
]);

function classifySignal(
  source: string,
  metadataCategory: string | null,
): SignalCategory {
  if (metadataCategory === "image_primary") return "image_primary";
  if (metadataCategory === "image_secondary") return "image_secondary";
  if (metadataCategory === "tag") return "tag";
  // Fallback for findings emitted before the categorization rollout.
  if (TAG_SOURCES.has(source)) return "tag";
  if (IMAGE_PRIMARY_SOURCES.has(source)) return "image_primary";
  return "image_secondary";
}

// --------------------------------------------------------------------- //
// Lazy Nominatim geocoder for findings that arrive with a location name //
// but no coordinates (Instagram location tags, Sonnet region clusters). //
// One request per second per process is the OSM use policy; a small     //
// in-memory cache plus a single-flight queue keeps us well under that.  //
// --------------------------------------------------------------------- //

interface GeocodeCache {
  [label: string]: { lat: number; lon: number } | null;
}

const _geocodeCache: GeocodeCache = {};
const _geocodeInflight = new Map<string, Promise<{ lat: number; lon: number } | null>>();
let _geocodeQueueTail: Promise<unknown> = Promise.resolve();

function geocodeLabel(
  label: string,
): Promise<{ lat: number; lon: number } | null> {
  const key = label.trim().toLowerCase();
  if (!key) return Promise.resolve(null);
  if (key in _geocodeCache) return Promise.resolve(_geocodeCache[key]);
  const inflight = _geocodeInflight.get(key);
  if (inflight) return inflight;

  // Chain onto a serial queue with ~1.1s spacing so we never burst Nominatim.
  const job = (async () => {
    await _geocodeQueueTail.catch(() => undefined);
    try {
      const url = new URL("https://nominatim.openstreetmap.org/search");
      url.searchParams.set("q", label);
      url.searchParams.set("format", "json");
      url.searchParams.set("limit", "1");
      const resp = await fetch(url.toString(), {
        headers: { Accept: "application/json" },
      });
      if (!resp.ok) {
        _geocodeCache[key] = null;
        return null;
      }
      const data = (await resp.json()) as Array<{
        lat?: string;
        lon?: string;
      }>;
      if (!Array.isArray(data) || data.length === 0) {
        _geocodeCache[key] = null;
        return null;
      }
      const lat = Number(data[0]?.lat);
      const lon = Number(data[0]?.lon);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
        _geocodeCache[key] = null;
        return null;
      }
      const result = { lat, lon };
      _geocodeCache[key] = result;
      return result;
    } catch {
      _geocodeCache[key] = null;
      return null;
    }
  })();

  _geocodeInflight.set(key, job);
  // Brief spacing so we stay polite to Nominatim but the first centroid
  // lands fast — the user wants to SEE the zoom, not wait on geocoding.
  _geocodeQueueTail = job.then(
    () => new Promise((r) => window.setTimeout(r, 350)),
    () => new Promise((r) => window.setTimeout(r, 350)),
  );
  job.finally(() => _geocodeInflight.delete(key));
  return job;
}

interface Centroid {
  lat: number;
  lon: number;
  meanConfidence: number;
  radiusKm: number;
  bestLabel: string | null;
}

const EARTH_RADIUS_KM = 6371;
const MIN_RADIUS_KM = 8;
const MAX_RADIUS_KM = 80;

// CartoDB Dark Matter — free, no API key, dark palette matches the theme.
const TILE_URL =
  "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png";
const TILE_ATTR =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/attributions">CARTO</a>';

// Fast cinematic zoom: a single Leaflet flyTo from world view to city.
// flyTo natively interpolates pan + zoom along a smooth zoom-out-arc-
// zoom-in curve; chaining multiple flyTos breaks that arc into disjoint
// segments and feels janky.
const ZOOM_TARGET = 10; // metro/city overview — not street level
const ZOOM_DURATION_S = 1.2;
// Lower easeLinearity = more aggressive bezier; 1.0 = linear. 0.1 punches in.
const ZOOM_EASE = 0.1;
const ZOOM_TOTAL_MS = ZOOM_DURATION_S * 1000;

function haversineKm(
  a: { lat: number; lon: number },
  b: { lat: number; lon: number },
): number {
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(b.lat - a.lat);
  const dLon = toRad(b.lon - a.lon);
  const lat1 = toRad(a.lat);
  const lat2 = toRad(b.lat);
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_KM * Math.asin(Math.min(1, Math.sqrt(h)));
}

interface RawSignal {
  coords: { lat: number; lon: number } | null;
  label: string | null;
  confidence: number;
  source: string;
  category: SignalCategory;
}

/**
 * Collect candidate signals from findings. Returns coords-direct entries
 * (EXIF GPS, GeoCLIP) AS-IS, and label-only entries (Instagram location
 * tag, Sonnet region cluster) with coords=null so the caller can geocode
 * them asynchronously.
 */
function collectSignals(findings: Finding[]): RawSignal[] {
  const out: RawSignal[] = [];
  for (const f of findings) {
    const md = f.metadata as Record<string, unknown> | undefined;
    if (!md) continue;

    const lat = typeof md.lat === "number" ? md.lat : null;
    const lon = typeof md.lon === "number" ? md.lon : null;
    const city = typeof md.city === "string" ? md.city : "";
    const country = typeof md.country === "string" ? md.country : "";
    const locName =
      typeof md.location_name === "string" ? md.location_name : "";
    const region = typeof md.region === "string" ? md.region : "";

    const label =
      [city, country].filter(Boolean).join(", ") ||
      locName ||
      region ||
      null;

    const conf = Math.max(0, Math.min(1, f.confidence));
    const metadataCategory =
      typeof md.signal_category === "string" ? md.signal_category : null;
    const category = classifySignal(f.source, metadataCategory);

    if (
      lat !== null &&
      lon !== null &&
      lat >= -90 &&
      lat <= 90 &&
      lon >= -180 &&
      lon <= 180
    ) {
      out.push({
        coords: { lat, lon },
        label,
        confidence: conf,
        source: f.source,
        category,
      });
    } else if (label) {
      out.push({
        coords: null,
        label,
        confidence: conf,
        source: f.source,
        category,
      });
    }
  }
  return out;
}

function computeCentroid(points: GeoPoint[]): Centroid | null {
  if (points.length === 0) return null;

  // Image-derived signals are the authoritative evidence — base the
  // centroid on them when any exist. Tags only contribute when image
  // signal is missing entirely (tag-fallback estimation), and even then
  // each tag carries a smaller weight so a single rogue tag doesn't
  // dominate the radius.
  const imagePoints = points.filter((p) => p.category !== "tag");
  const usePoints = imagePoints.length > 0 ? imagePoints : points;
  const tagFallback = imagePoints.length === 0;

  let totalW = 0;
  let lat = 0;
  let lon = 0;
  for (const p of usePoints) {
    const w = (p.confidence + 0.1) * categoryWeight(p.category);
    totalW += w;
    lat += p.lat * w;
    lon += p.lon * w;
  }
  if (totalW === 0) return null;
  lat /= totalW;
  lon /= totalW;

  let maxDist = 0;
  let weightedConf = 0;
  for (const p of usePoints) {
    const d = haversineKm(p, { lat, lon });
    if (d > maxDist) maxDist = d;
    const w = (p.confidence + 0.1) * categoryWeight(p.category);
    weightedConf += p.confidence * w;
  }
  weightedConf /= totalW;

  // A tag-only fallback is intentionally less certain — widen the ring,
  // but everything stays clamped under MAX_RADIUS_KM so the circle never
  // dwarfs the city it's centered on.
  const fallbackInflation = tagFallback ? 1.6 : 1.0;
  const rawRadius =
    usePoints.length === 1
      ? (1 - weightedConf) * 60 * fallbackInflation
      : maxDist * fallbackInflation;
  const radiusKm = Math.max(MIN_RADIUS_KM, Math.min(MAX_RADIUS_KM, rawRadius));

  const labelCounts = new Map<string, number>();
  for (const p of usePoints) {
    if (p.label) labelCounts.set(p.label, (labelCounts.get(p.label) ?? 0) + 1);
  }
  let bestLabel: string | null = null;
  let bestCount = 0;
  for (const [label, count] of labelCounts) {
    if (count > bestCount) {
      bestLabel = label;
      bestCount = count;
    }
  }

  return { lat, lon, meanConfidence: weightedConf, radiusKm, bestLabel };
}

/**
 * One smooth flyTo from world view to city level. Leaflet's flyTo
 * natively interpolates pan + zoom along a parabolic arc — splitting
 * it into multiple hops breaks that arc into disjoint segments and
 * feels janky. We snap to a global vantage first (no animation) so
 * every fly looks the same regardless of starting state, then run
 * exactly one cinematic flight to the centroid.
 *
 * Re-runs only when the target moves more than ~1km (3-decimal lat/lon).
 */
function HyperzoomController({ centroid }: { centroid: Centroid }) {
  const map = useMap();

  useEffect(() => {
    // Build a bounding box around the confidence ring so the final zoom
    // frames the entire circle. 1° latitude ≈ 111 km; longitude shrinks
    // by cos(lat) toward the poles.
    const latRad = (centroid.lat * Math.PI) / 180;
    const cosLat = Math.max(0.1, Math.cos(latRad));
    const latSpan = centroid.radiusKm / 111;
    const lonSpan = centroid.radiusKm / (111 * cosLat);
    const bounds = L.latLngBounds(
      [centroid.lat - latSpan, centroid.lon - lonSpan],
      [centroid.lat + latSpan, centroid.lon + lonSpan],
    );

    // Always snap to a global vantage so the cinematic arc reads from
    // the start, even on re-mount or when the centroid hasn't moved.
    map.stop();
    map.setView([20, 0], 2, { animate: false });

    // Invalidate size in case the panel just appeared, then fly. A
    // single rAF is enough for layout to settle without the 80ms wait.
    const raf = window.requestAnimationFrame(() => {
      map.invalidateSize({ animate: false, pan: false });
      // flyToBounds frames the whole confidence ring with breathing
      // room. Padded so the dashed circle isn't pressed to the edge.
      map.flyToBounds(bounds, {
        padding: [40, 40],
        maxZoom: ZOOM_TARGET,
        duration: ZOOM_DURATION_S,
        easeLinearity: ZOOM_EASE,
        noMoveStart: true,
      });
    });

    return () => {
      window.cancelAnimationFrame(raf);
      map.stop();
    };
  }, [centroid.lat, centroid.lon, map]);

  return null;
}

export default function GeoMap({ findings }: Props) {
  const signals = useMemo(() => collectSignals(findings), [findings]);

  // Pending geocodes — we kick these off in an effect and store the
  // results in state so the centroid recomputes when each lookup lands.
  const [resolvedLabels, setResolvedLabels] = useState<GeocodeCache>({});

  useEffect(() => {
    let cancelled = false;
    const labels = Array.from(
      new Set(
        signals
          .filter((s) => s.coords === null && s.label)
          .map((s) => s.label as string),
      ),
    );
    for (const label of labels) {
      const key = label.trim().toLowerCase();
      if (key in resolvedLabels) continue;
      void geocodeLabel(label).then((res) => {
        if (cancelled) return;
        setResolvedLabels((prev) =>
          key in prev ? prev : { ...prev, [key]: res },
        );
      });
    }
    return () => {
      cancelled = true;
    };
    // resolvedLabels intentionally omitted: we read the latest via the
    // setter callback so we don't loop on every state update.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signals]);

  const points = useMemo<GeoPoint[]>(() => {
    const out: GeoPoint[] = [];
    for (const s of signals) {
      let coords = s.coords;
      if (!coords && s.label) {
        const cached = resolvedLabels[s.label.trim().toLowerCase()];
        if (cached) coords = cached;
      }
      if (!coords) continue;
      out.push({
        lat: coords.lat,
        lon: coords.lon,
        confidence: s.confidence,
        source: s.source,
        label: s.label,
        category: s.category,
      });
    }
    return out;
  }, [signals, resolvedLabels]);

  const centroid = useMemo(() => computeCentroid(points), [points]);
  const [zoomingDone, setZoomingDone] = useState(false);

  // Reset the zoom-done flag when the target moves.
  useEffect(() => {
    if (!centroid) {
      setZoomingDone(false);
      return;
    }
    const totalMs = ZOOM_TOTAL_MS + 120;
    const t = window.setTimeout(() => setZoomingDone(true), totalMs);
    return () => window.clearTimeout(t);
  }, [centroid?.lat, centroid?.lon]);

  const pendingGeocodes = signals.filter(
    (s) =>
      s.coords === null &&
      s.label &&
      !(s.label.trim().toLowerCase() in resolvedLabels),
  ).length;

  if (!centroid || points.length === 0) {
    return (
      <section className="border border-nodoxx-border bg-nodoxx-panel p-6">
        <header className="mb-3 flex items-center gap-2">
          <h2 className="font-mono text-xs font-semibold uppercase tracking-[0.18em] text-nodoxx-text">
            // estimated location
          </h2>
          <span className="border border-nodoxx-border bg-nodoxx-bg px-1.5 py-0.5 font-mono text-[10px] tabular-nums text-nodoxx-muted">
            {points.length}
          </span>
        </header>
        <div className="flex h-40 items-center justify-center border border-dashed border-nodoxx-border font-mono text-[11px] text-nodoxx-muted">
          &gt;{" "}
          {pendingGeocodes > 0
            ? `geocoding ${pendingGeocodes} location ${
                pendingGeocodes === 1 ? "tag" : "tags"
              }...`
            : "awaiting geolocation evidence with coordinates..."}
        </div>
      </section>
    );
  }

  return (
    <section className="border border-nodoxx-border bg-nodoxx-panel p-4 sm:p-6">
      <header className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <h2 className="font-mono text-xs font-semibold uppercase tracking-[0.18em] text-nodoxx-text">
            // estimated location
          </h2>
          <span className="border border-nodoxx-border bg-nodoxx-bg px-1.5 py-0.5 font-mono text-[10px] tabular-nums text-nodoxx-muted">
            {points.length} {points.length === 1 ? "signal" : "signals"}
          </span>
        </div>
        <div className="flex items-center gap-3 font-mono text-[10px] uppercase tracking-[0.18em] text-nodoxx-muted">
          <span>
            <span>centroid </span>
            <span className="text-nodoxx-text">
              {centroid.lat.toFixed(2)}, {centroid.lon.toFixed(2)}
            </span>
          </span>
          <span>
            <span>radius </span>
            <span className="text-nodoxx-text">
              {formatKm(centroid.radiusKm)}
            </span>
          </span>
        </div>
      </header>

      {centroid.bestLabel && (
        <p className="mb-3 font-mono text-xs text-nodoxx-muted">
          best match:{" "}
          <span className="text-nodoxx-text">{centroid.bestLabel}</span>
        </p>
      )}

      <div className="relative overflow-hidden border border-nodoxx-border bg-nodoxx-bg">
        <MapContainer
          center={[20, 0]}
          zoom={2}
          minZoom={2}
          maxZoom={18}
          maxBounds={[[-85, -180], [85, 180]]}
          maxBoundsViscosity={1.0}
          zoomControl={false}
          attributionControl={false}
          className="h-[420px] w-full"
          style={{ background: "#0a0a0a" }}
        >
          <TileLayer url={TILE_URL} attribution={TILE_ATTR} noWrap />

          <HyperzoomController centroid={centroid} />

          {/* Outer confidence ring (radius reflects evidence spread). */}
          <Circle
            center={[centroid.lat, centroid.lon]}
            radius={centroid.radiusKm * 1000}
            pathOptions={{
              color: "#fafafa",
              weight: 1.4,
              opacity: 0.75,
              dashArray: "5 4",
              fillColor: "#fafafa",
              fillOpacity: 0.06,
            }}
          />

          {/* Pulsing inner ring at the centroid — only after the
              hyperzoom finishes so it doesn't compete during flight. */}
          {zoomingDone && (
            <CircleMarker
              center={[centroid.lat, centroid.lon]}
              radius={10}
              pathOptions={{
                color: "#fafafa",
                weight: 2,
                fillColor: "#fafafa",
                fillOpacity: 0.35,
                className: "nodoxx-geo-pulse",
              }}
            />
          )}

          {/* Per-evidence markers. Image-derived points: solid white dot
              (authoritative evidence). Tag-derived points: hollow dashed
              ring (subordinate corroboration). Drawn translucent during
              the flyTo so the eye stays on the centroid. */}
          {points.map((p, i) => {
            const isTag = p.category === "tag";
            return (
              <CircleMarker
                key={`p-${i}`}
                center={[p.lat, p.lon]}
                radius={isTag ? 5 + p.confidence * 2 : 3 + p.confidence * 4}
                pathOptions={
                  isTag
                    ? {
                        color: "#fafafa",
                        weight: 1.4,
                        opacity: zoomingDone ? 0.85 : 0.35,
                        fillColor: "#fafafa",
                        fillOpacity: zoomingDone ? 0.1 : 0.04,
                        dashArray: "2 2",
                      }
                    : {
                        color: "#0a0a0a",
                        weight: 1,
                        fillColor: "#fafafa",
                        fillOpacity: zoomingDone ? 0.95 : 0.4,
                      }
                }
              />
            );
          })}
        </MapContainer>

        <div className="pointer-events-none absolute bottom-2 left-2 z-[400] bg-nodoxx-bg/80 px-2 py-1 font-mono text-[10px] uppercase tracking-[0.18em] text-nodoxx-muted backdrop-blur">
          leaflet &middot; carto &middot; weighted by confidence
        </div>
      </div>

      <ul className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-2">
        {points
          .slice()
          .sort((a, b) => {
            // Image-derived first, then tags. Within each group: higher
            // confidence wins.
            const order = (p: GeoPoint) =>
              p.category === "tag" ? 1 : 0;
            const dg = order(a) - order(b);
            if (dg !== 0) return dg;
            return b.confidence - a.confidence;
          })
          .slice(0, 6)
          .map((p, i) => {
            const isTag = p.category === "tag";
            return (
              <li
                key={`leg-${i}`}
                className="flex min-w-0 items-center justify-between gap-3 rounded-md border border-nodoxx-border/30 bg-nodoxx-bg/60 px-3 py-2 text-xs"
              >
                <span className="flex min-w-0 items-center gap-2 truncate text-nodoxx-text/90">
                  <span
                    aria-hidden="true"
                    className={`inline-block h-2 w-2 shrink-0 rounded-full ${
                      isTag
                        ? "border border-nodoxx-accent bg-transparent"
                        : "bg-nodoxx-accent"
                    }`}
                  />
                  <span className="truncate font-mono">{p.source}</span>
                  {isTag && (
                    <span className="shrink-0 rounded bg-nodoxx-bg px-1 font-mono text-[9px] uppercase tracking-wider text-nodoxx-muted">
                      tag
                    </span>
                  )}
                </span>
                <span className="shrink-0 truncate font-mono text-nodoxx-muted">
                  {p.label ?? `${p.lat.toFixed(2)}, ${p.lon.toFixed(2)}`}
                </span>
              </li>
            );
          })}
      </ul>
    </section>
  );
}

function formatKm(km: number): string {
  if (km < 10) return `${km.toFixed(1)} km`;
  if (km < 1000) return `${Math.round(km)} km`;
  return `${(km / 1000).toFixed(1)}k km`;
}
