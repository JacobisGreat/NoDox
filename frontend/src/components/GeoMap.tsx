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
  if (TAG_SOURCES.has(source)) return "tag";
  if (IMAGE_PRIMARY_SOURCES.has(source)) return "image_primary";
  return "image_secondary";
}

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

const TILE_URL =
  "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png";
const TILE_ATTR =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/attributions">CARTO</a>';

const ZOOM_TARGET = 10;
const ZOOM_DURATION_S = 1.2;
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

function HyperzoomController({ centroid }: { centroid: Centroid }) {
  const map = useMap();

  useEffect(() => {
    const latRad = (centroid.lat * Math.PI) / 180;
    const cosLat = Math.max(0.1, Math.cos(latRad));
    const latSpan = centroid.radiusKm / 111;
    const lonSpan = centroid.radiusKm / (111 * cosLat);
    const bounds = L.latLngBounds(
      [centroid.lat - latSpan, centroid.lon - lonSpan],
      [centroid.lat + latSpan, centroid.lon + lonSpan],
    );

    map.stop();
    map.setView([20, 0], 2, { animate: false });

    const raf = window.requestAnimationFrame(() => {
      map.invalidateSize({ animate: false, pan: false });
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
      <section className="border border-kali-border bg-kali-surface p-6">
        <header className="mb-3 flex items-center gap-2">
          <h2 className="font-mono text-[13px] font-bold uppercase tracking-label text-kali-text">
            // estimated location
          </h2>
          <span className="border border-kali-border bg-kali-bg px-1.5 py-0.5 font-mono text-[10px] tabular-nums text-kali-dim">
            {points.length}
          </span>
        </header>
        <div className="flex h-40 items-center justify-center border border-dashed border-kali-border font-mono text-[11px] text-kali-dim">
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
    <section className="border border-kali-border bg-kali-surface p-4 transition-colors hover:border-kali-text sm:p-6">
      <header className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <h2 className="font-mono text-[13px] font-bold uppercase tracking-label text-kali-text">
            // estimated location
          </h2>
          <span className="border border-kali-border bg-kali-bg px-1.5 py-0.5 font-mono text-[10px] tabular-nums text-kali-dim">
            {points.length} {points.length === 1 ? "signal" : "signals"}
          </span>
        </div>
        <div className="flex items-center gap-3 font-mono text-[10px] uppercase tracking-label text-kali-label">
          <span>
            <span>centroid </span>
            <span className="text-kali-text">
              {centroid.lat.toFixed(2)}, {centroid.lon.toFixed(2)}
            </span>
          </span>
          <span>
            <span>radius </span>
            <span className="text-kali-text">
              {formatKm(centroid.radiusKm)}
            </span>
          </span>
        </div>
      </header>

      {centroid.bestLabel && (
        <p className="mb-3 font-mono text-[11px] text-kali-dim">
          best match:{" "}
          <span className="text-kali-text">{centroid.bestLabel}</span>
        </p>
      )}

      <div className="relative overflow-hidden border border-kali-border bg-kali-bg">
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
          style={{ background: "#000000" }}
        >
          <TileLayer url={TILE_URL} attribution={TILE_ATTR} noWrap />

          <HyperzoomController centroid={centroid} />

          <Circle
            center={[centroid.lat, centroid.lon]}
            radius={centroid.radiusKm * 1000}
            pathOptions={{
              color: "#FFFFFF",
              weight: 1,
              opacity: 0.75,
              dashArray: "5 4",
              fillColor: "#FFFFFF",
              fillOpacity: 0.06,
            }}
          />

          {zoomingDone && (
            <CircleMarker
              center={[centroid.lat, centroid.lon]}
              radius={10}
              pathOptions={{
                color: "#FFFFFF",
                weight: 1,
                fillColor: "#FFFFFF",
                fillOpacity: 0.35,
                className: "animate-running-pulse",
              }}
            />
          )}

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
                        color: "#FFFFFF",
                        weight: 1,
                        opacity: zoomingDone ? 0.85 : 0.35,
                        fillColor: "#FFFFFF",
                        fillOpacity: zoomingDone ? 0.1 : 0.04,
                        dashArray: "2 2",
                      }
                    : {
                        color: "#000000",
                        weight: 1,
                        fillColor: "#FFFFFF",
                        fillOpacity: zoomingDone ? 0.95 : 0.4,
                      }
                }
              />
            );
          })}
        </MapContainer>

        <div className="pointer-events-none absolute bottom-2 left-2 z-[400] bg-kali-bg px-2 py-1 font-mono text-[10px] uppercase tracking-label text-kali-label">
          leaflet &middot; carto &middot; weighted by confidence
        </div>
      </div>

      <ul className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-2">
        {points
          .slice()
          .sort((a, b) => {
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
                className="flex min-w-0 items-center justify-between gap-3 border border-kali-border bg-kali-bg px-3 py-2 font-mono text-[11px]"
              >
                <span className="flex min-w-0 items-center gap-2 truncate text-kali-text">
                  <span
                    aria-hidden="true"
                    className={`inline-block h-2 w-2 shrink-0 ${
                      isTag
                        ? "border border-kali-text bg-transparent"
                        : "bg-kali-text"
                    }`}
                  />
                  <span className="truncate">{p.source}</span>
                  {isTag && (
                    <span className="shrink-0 border border-kali-border bg-kali-bg px-1 font-mono text-[9px] uppercase tracking-label text-kali-label">
                      tag
                    </span>
                  )}
                </span>
                <span className="shrink-0 truncate font-mono text-kali-dim">
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
