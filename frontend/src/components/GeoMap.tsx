import { useMemo } from "react";
import { Finding } from "../types";

interface Props {
  findings: Finding[];
}

interface GeoPoint {
  lat: number;
  lon: number;
  confidence: number;
  source: string;
  label: string | null;
}

interface Centroid {
  lat: number;
  lon: number;
  meanConfidence: number;
  radiusKm: number;
  bestLabel: string | null;
}

const MAP_WIDTH = 720;
const MAP_HEIGHT = 360;
const MIN_RADIUS_KM = 25;
const EARTH_RADIUS_KM = 6371;

// Simplified continent silhouettes in an equirectangular projection, each path
// drawn as a polygon in a 720x360 canvas (lon -180..180 → x 0..720, lat
// 90..-90 → y 0..360). Hand-traced from a low-res CC0 outline; precision is
// only as good as a "this is roughly Earth" reference needs to be.
const CONTINENT_PATHS: string[] = [
  // North America
  "M 100 80 L 175 70 L 215 95 L 250 110 L 245 135 L 270 155 L 250 180 L 215 195 L 180 200 L 165 215 L 140 220 L 130 200 L 115 175 L 90 145 L 80 110 Z",
  // Greenland
  "M 270 55 L 305 50 L 320 80 L 305 105 L 280 100 L 270 80 Z",
  // South America
  "M 220 215 L 255 210 L 280 235 L 295 270 L 285 305 L 265 330 L 245 345 L 235 320 L 225 285 L 215 245 Z",
  // Europe
  "M 360 90 L 405 85 L 430 100 L 425 125 L 395 130 L 365 120 Z",
  // Africa
  "M 370 145 L 425 140 L 455 170 L 470 215 L 450 270 L 415 295 L 390 285 L 380 245 L 370 200 Z",
  // Middle East / West Asia
  "M 435 130 L 475 130 L 490 155 L 470 170 L 440 165 Z",
  // Asia
  "M 440 75 L 540 65 L 605 80 L 645 105 L 660 135 L 615 155 L 580 165 L 540 155 L 500 145 L 470 125 L 445 105 Z",
  // SE Asia / India
  "M 510 160 L 555 165 L 580 185 L 595 215 L 575 230 L 545 215 L 525 195 Z",
  // Australia
  "M 580 250 L 635 245 L 665 265 L 660 290 L 625 295 L 590 280 Z",
];

function projectLonLat(lon: number, lat: number): { x: number; y: number } {
  const x = ((lon + 180) / 360) * MAP_WIDTH;
  const y = ((90 - lat) / 180) * MAP_HEIGHT;
  return { x, y };
}

function haversineKm(a: GeoPoint, b: { lat: number; lon: number }): number {
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

function extractPoints(findings: Finding[]): GeoPoint[] {
  const out: GeoPoint[] = [];
  for (const f of findings) {
    const md = f.metadata as Record<string, unknown> | undefined;
    if (!md) continue;
    const lat = typeof md.lat === "number" ? md.lat : null;
    const lon = typeof md.lon === "number" ? md.lon : null;
    if (lat === null || lon === null) continue;
    if (lat < -90 || lat > 90 || lon < -180 || lon > 180) continue;
    const city = typeof md.city === "string" ? md.city : "";
    const country = typeof md.country === "string" ? md.country : "";
    const label = [city, country].filter(Boolean).join(", ") || null;
    out.push({
      lat,
      lon,
      confidence: Math.max(0, Math.min(1, f.confidence)),
      source: f.source,
      label,
    });
  }
  return out;
}

function computeCentroid(points: GeoPoint[]): Centroid | null {
  if (points.length === 0) return null;
  // Weighted by confidence (with a small floor so zero-conf points still count).
  let totalW = 0;
  let lat = 0;
  let lon = 0;
  for (const p of points) {
    const w = p.confidence + 0.1;
    totalW += w;
    lat += p.lat * w;
    lon += p.lon * w;
  }
  lat /= totalW;
  lon /= totalW;

  let maxDist = 0;
  let weightedConf = 0;
  for (const p of points) {
    const d = haversineKm(p, { lat, lon });
    if (d > maxDist) maxDist = d;
    weightedConf += p.confidence * (p.confidence + 0.1);
  }
  weightedConf /= totalW;

  // Radius reflects the spread of evidence. With a single point, fall back to
  // a confidence-derived ring so the map still communicates uncertainty.
  let radiusKm: number;
  if (points.length === 1) {
    radiusKm = Math.max(MIN_RADIUS_KM, (1 - weightedConf) * 1500);
  } else {
    radiusKm = Math.max(MIN_RADIUS_KM, maxDist);
  }

  // Pick the most informative city label among the contributing points.
  const labelCounts = new Map<string, number>();
  for (const p of points) {
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

function radiusKmToSvgPixels(radiusKm: number): number {
  const degrees = radiusKm / 111;
  const px = (degrees / 180) * MAP_HEIGHT;
  return Math.max(6, Math.min(MAP_WIDTH * 0.6, px));
}

export default function GeoMap({ findings }: Props) {
  const points = useMemo(() => extractPoints(findings), [findings]);
  const centroid = useMemo(() => computeCentroid(points), [points]);

  if (!centroid || points.length === 0) {
    return (
      <section className="rounded-xl border border-nodoxx-border/30 bg-nodoxx-panel/50 p-6">
        <header className="mb-3 flex items-center gap-2">
          <h2 className="text-sm font-semibold tracking-wide text-nodoxx-text">
            Estimated Location
          </h2>
          <span className="rounded-full border border-nodoxx-border/40 bg-nodoxx-bg px-2 py-0.5 font-mono text-[11px] tabular-nums text-nodoxx-muted">
            {points.length}
          </span>
        </header>
        <div className="flex h-40 items-center justify-center rounded-lg border border-dashed border-nodoxx-border/40 text-xs text-nodoxx-muted">
          Awaiting geolocation evidence with coordinates...
        </div>
      </section>
    );
  }

  const center = projectLonLat(centroid.lon, centroid.lat);
  const radiusPx = radiusKmToSvgPixels(centroid.radiusKm);

  return (
    <section className="rounded-xl border border-nodoxx-border/30 bg-nodoxx-panel/50 p-4 sm:p-6">
      <header className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold tracking-wide text-nodoxx-text">
            Estimated Location
          </h2>
          <span className="rounded-full border border-nodoxx-border/40 bg-nodoxx-bg px-2 py-0.5 font-mono text-[11px] tabular-nums text-nodoxx-muted">
            {points.length} {points.length === 1 ? "signal" : "signals"}
          </span>
        </div>
        <div className="flex items-center gap-3 font-mono text-[11px] uppercase tracking-wider text-nodoxx-muted">
          <span>
            <span className="text-nodoxx-muted/70">centroid </span>
            <span className="text-nodoxx-text">
              {centroid.lat.toFixed(2)}, {centroid.lon.toFixed(2)}
            </span>
          </span>
          <span>
            <span className="text-nodoxx-muted/70">radius </span>
            <span className="text-nodoxx-text">
              {formatKm(centroid.radiusKm)}
            </span>
          </span>
        </div>
      </header>

      {centroid.bestLabel && (
        <p className="mb-3 font-mono text-xs text-nodoxx-accent/90">
          Best match: <span className="text-nodoxx-text">{centroid.bestLabel}</span>
        </p>
      )}

      <div className="relative overflow-hidden rounded-lg border border-nodoxx-border/30 bg-nodoxx-bg">
        <svg
          viewBox={`0 0 ${MAP_WIDTH} ${MAP_HEIGHT}`}
          className="block h-auto w-full"
          role="img"
          aria-label="Estimated location map"
        >
          {/* Background graticule */}
          <g stroke="rgba(31,63,99,0.35)" strokeWidth={0.5} fill="none">
            {[-60, -30, 0, 30, 60].map((lat) => {
              const y = ((90 - lat) / 180) * MAP_HEIGHT;
              return (
                <line key={`lat-${lat}`} x1={0} x2={MAP_WIDTH} y1={y} y2={y} />
              );
            })}
            {[-150, -120, -90, -60, -30, 0, 30, 60, 90, 120, 150].map((lon) => {
              const x = ((lon + 180) / 360) * MAP_WIDTH;
              return (
                <line key={`lon-${lon}`} x1={x} x2={x} y1={0} y2={MAP_HEIGHT} />
              );
            })}
          </g>

          {/* Continent silhouettes */}
          <g
            fill="rgba(31,63,99,0.45)"
            stroke="rgba(0,212,255,0.18)"
            strokeWidth={0.6}
          >
            {CONTINENT_PATHS.map((d, i) => (
              <path key={`c-${i}`} d={d} />
            ))}
          </g>

          {/* Equator emphasis */}
          <line
            x1={0}
            x2={MAP_WIDTH}
            y1={MAP_HEIGHT / 2}
            y2={MAP_HEIGHT / 2}
            stroke="rgba(148,163,184,0.25)"
            strokeWidth={0.8}
            strokeDasharray="3 4"
          />

          {/* Confidence circle */}
          <circle
            cx={center.x}
            cy={center.y}
            r={radiusPx}
            fill="rgba(0,212,255,0.12)"
            stroke="rgba(0,212,255,0.65)"
            strokeWidth={1.2}
            strokeDasharray="4 3"
          />

          {/* Inner ring for emphasis */}
          <circle
            cx={center.x}
            cy={center.y}
            r={Math.max(4, radiusPx * 0.3)}
            fill="rgba(0,212,255,0.18)"
            stroke="rgba(0,212,255,0.85)"
            strokeWidth={1}
          />

          {/* Evidence points */}
          <g>
            {points.map((p, i) => {
              const { x, y } = projectLonLat(p.lon, p.lat);
              const r = 2.5 + p.confidence * 3;
              return (
                <circle
                  key={`p-${i}`}
                  cx={x}
                  cy={y}
                  r={r}
                  fill="rgba(0,212,255,0.95)"
                  stroke="#0a0f1e"
                  strokeWidth={0.8}
                >
                  <title>
                    {p.source} · {p.lat.toFixed(3)}, {p.lon.toFixed(3)} ·{" "}
                    {(p.confidence * 100).toFixed(0)}%
                  </title>
                </circle>
              );
            })}
          </g>

          {/* Centroid crosshair */}
          <g stroke="#00d4ff" strokeWidth={1.2}>
            <line
              x1={center.x - 7}
              x2={center.x + 7}
              y1={center.y}
              y2={center.y}
            />
            <line
              x1={center.x}
              x2={center.x}
              y1={center.y - 7}
              y2={center.y + 7}
            />
          </g>
        </svg>

        <div className="pointer-events-none absolute bottom-2 left-2 rounded bg-nodoxx-bg/80 px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-nodoxx-muted backdrop-blur">
          equirectangular · weighted by confidence
        </div>
      </div>

      <ul className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-2">
        {points.slice(0, 6).map((p, i) => (
          <li
            key={`leg-${i}`}
            className="flex items-center justify-between gap-3 rounded-md border border-nodoxx-border/30 bg-nodoxx-bg/60 px-3 py-2 text-xs"
          >
            <span className="flex items-center gap-2 truncate text-nodoxx-text/90">
              <span
                aria-hidden="true"
                className="inline-block h-2 w-2 shrink-0 rounded-full bg-nodoxx-accent"
              />
              <span className="truncate font-mono">{p.source}</span>
            </span>
            <span className="shrink-0 font-mono text-nodoxx-muted">
              {p.label ?? `${p.lat.toFixed(2)}, ${p.lon.toFixed(2)}`}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function formatKm(km: number): string {
  if (km < 10) return `${km.toFixed(1)} km`;
  if (km < 1000) return `${Math.round(km)} km`;
  return `${(km / 1000).toFixed(1)}k km`;
}
