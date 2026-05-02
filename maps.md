 Use this stack:

  1. maplibre-gl as the map engine (replace Leaflet) with projection: "globe" for the 3D earth.
  2. three.js via a MapLibre custom layer for the globe effects (atmosphere glow, pulse rings, arcs).
  3. Vector tile API: MapTiler or self-hosted PMTiles (for smooth GPU rendering and fewer raster-tile bottlenecks).
  4. Geocoding API: backend-only, not browser-side Nominatim calls.

  Why this is the right fit for your current code:

  - You’re currently on Leaflet + raster tiles + client geocoding (frontend/src/components/GeoMap.tsx:2, frontend/src/components/GeoMap.tsx:75, frontend/src/
    components/GeoMap.tsx:402).
  - You already have backend geocoding infrastructure you can reuse/extend (backend/app/services/geocode_text.py:1, backend/app/pipelines/
    geolocation.py:383).
  - Your SSE contract is already stable for streaming findings, so map updates can remain event-driven (backend/app/schemas/events.py:1, frontend/src/hooks/
    useSSE.ts:70).

  Map API design I’d use:

  1. GET /api/map/session/{session_id}/features → GeoJSON FeatureCollection of all geolocation points.
  2. Keep using existing SSE finding events for incremental updates; frontend updates one GeoJSON source (setData) on a 150–250ms throttle.
  3. POST /api/map/geocode (server-side only) for unresolved labels, with cache + rate limiting.
  4. Optional fallback chain: local corpus geocoder first, paid geocoder second.

  Important note:
  Inference from docs + your code: move off browser Nominatim. Public Nominatim policy is restrictive for app traffic and requires careful identification/
  rate behavior that frontend calls don’t control well.

  Sources:

  - https://maplibre.org/roadmap/maplibre-gl-js/globe-view/
  - https://maplibre.org/maplibre-gl-js/docs/examples/add-a-3d-model-to-globe-using-threejs/
  - https://maplibre.org/projects/gl-js/
  - https://docs.protomaps.com/pmtiles/maplibre
  - https://operations.osmfoundation.org/policies/nominatim/
  - https://deck.gl/docs/api-reference/core/globe-view

  If you want, I can implement Phase 1 next: swap GeoMap from Leaflet to MapLibre with globe mode + your current heat/cluster logic.