import { useEffect, useRef } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

const GLOBE_RADIUS = 1.0;
const WORLD_GEOJSON_URL = "/world-land-110m.geojson";

export interface GlobePoint {
  lat: number;
  lon: number;
  confidence: number;
  category: "image_primary" | "image_secondary" | "tag";
  label: string | null;
  source: string;
}

export interface GlobeCentroid {
  lat: number;
  lon: number;
  radiusKm: number;
}

interface Props {
  points: GlobePoint[];
  centroid: GlobeCentroid | null;
  height?: number;
}

interface SceneRefs {
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  renderer: THREE.WebGLRenderer;
  controls: OrbitControls;
  earthGroup: THREE.Group;
  markersGroup: THREE.Group;
  centroidGroup: THREE.Group;
  raycaster: THREE.Raycaster;
  pointer: THREE.Vector2;
  hoveredKey: string | null;
  initialAimDone: boolean;
  rafId: number;
  destroyed: boolean;
}

function latLonToVec3(lat: number, lon: number, r: number): THREE.Vector3 {
  const phi = (90 - lat) * (Math.PI / 180);
  const theta = lon * (Math.PI / 180);
  return new THREE.Vector3(
    r * Math.sin(phi) * Math.sin(theta),
    r * Math.cos(phi),
    r * Math.sin(phi) * Math.cos(theta),
  );
}

function disposeObject(obj: THREE.Object3D) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const o = obj as any;
  if (o.geometry) o.geometry.dispose();
  if (o.material) {
    const mat = o.material;
    if (Array.isArray(mat)) mat.forEach((m: THREE.Material) => m.dispose());
    else (mat as THREE.Material).dispose();
  }
}

function clearGroup(group: THREE.Group) {
  while (group.children.length) {
    const c = group.children[0];
    group.remove(c);
    c.traverse((child) => disposeObject(child));
  }
}

function densifyRing(
  coords: [number, number][],
  maxDeg: number,
): [number, number][][] {
  // Returns one or more sub-rings, splitting at antimeridian crossings so we
  // don't draw straight lines across the Pacific when a polygon wraps.
  if (coords.length < 2) return [coords];
  const segments: [number, number][][] = [];
  let current: [number, number][] = [coords[0]];
  for (let i = 0; i < coords.length - 1; i++) {
    const [lon1, lat1] = coords[i];
    const [lon2, lat2] = coords[i + 1];
    const dLon = lon2 - lon1;
    if (Math.abs(dLon) > 180) {
      // Antimeridian crossing — close current segment, start a new one
      if (current.length > 1) segments.push(current);
      current = [coords[i + 1]];
      continue;
    }
    const dLat = lat2 - lat1;
    const dist = Math.hypot(dLon, dLat);
    const steps = Math.max(1, Math.ceil(dist / maxDeg));
    for (let s = 1; s <= steps; s++) {
      const t = s / steps;
      current.push([lon1 + dLon * t, lat1 + dLat * t]);
    }
  }
  if (current.length > 1) segments.push(current);
  return segments;
}

function buildGraticule(): THREE.LineSegments {
  const positions: number[] = [];
  const r = GLOBE_RADIUS * 1.0008;
  // Latitude lines (parallels), skip poles
  for (let lat = -60; lat <= 60; lat += 30) {
    for (let lon = -180; lon < 180; lon += 3) {
      const a = latLonToVec3(lat, lon, r);
      const b = latLonToVec3(lat, lon + 3, r);
      positions.push(a.x, a.y, a.z, b.x, b.y, b.z);
    }
  }
  // Longitude lines (meridians)
  for (let lon = -180; lon < 180; lon += 30) {
    for (let lat = -85; lat < 85; lat += 3) {
      const a = latLonToVec3(lat, lon, r);
      const b = latLonToVec3(lat + 3, lon, r);
      positions.push(a.x, a.y, a.z, b.x, b.y, b.z);
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  const mat = new THREE.LineBasicMaterial({
    color: 0xffffff,
    transparent: true,
    opacity: 0.09,
  });
  return new THREE.LineSegments(geo, mat);
}

function buildEquator(): THREE.LineLoop {
  const positions: number[] = [];
  const r = GLOBE_RADIUS * 1.0015;
  for (let lon = -180; lon < 180; lon += 2) {
    const p = latLonToVec3(0, lon, r);
    positions.push(p.x, p.y, p.z);
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  const mat = new THREE.LineBasicMaterial({
    color: 0xffffff,
    transparent: true,
    opacity: 0.18,
  });
  return new THREE.LineLoop(geo, mat);
}

async function buildLandLines(): Promise<THREE.LineSegments | null> {
  try {
    const resp = await fetch(WORLD_GEOJSON_URL);
    if (!resp.ok) return null;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const geojson: any = await resp.json();
    const positions: number[] = [];
    const r = GLOBE_RADIUS * 1.002;

    function processRing(ring: number[][]) {
      const segments = densifyRing(ring as [number, number][], 1.6);
      for (const seg of segments) {
        for (let i = 0; i < seg.length - 1; i++) {
          const a = latLonToVec3(seg[i][1], seg[i][0], r);
          const b = latLonToVec3(seg[i + 1][1], seg[i + 1][0], r);
          positions.push(a.x, a.y, a.z, b.x, b.y, b.z);
        }
      }
    }

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    function processGeom(geom: any) {
      if (!geom) return;
      if (geom.type === "Polygon") {
        for (const ring of geom.coordinates) processRing(ring);
      } else if (geom.type === "MultiPolygon") {
        for (const poly of geom.coordinates) {
          for (const ring of poly) processRing(ring);
        }
      } else if (geom.type === "GeometryCollection") {
        for (const g of geom.geometries) processGeom(g);
      }
    }

    if (geojson.type === "FeatureCollection") {
      for (const f of geojson.features) processGeom(f.geometry);
    } else if (geojson.type === "Feature") {
      processGeom(geojson.geometry);
    } else {
      processGeom(geojson);
    }

    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    const mat = new THREE.LineBasicMaterial({
      color: 0xffffff,
      transparent: true,
      opacity: 0.55,
    });
    return new THREE.LineSegments(geo, mat);
  } catch {
    return null;
  }
}

export default function Globe({ points, centroid, height = 460 }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const refs = useRef<SceneRefs | null>(null);

  // Mount: build scene once
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const width = container.clientWidth;
    const h = container.clientHeight || height;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x000000);

    const camera = new THREE.PerspectiveCamera(40, width / h, 0.1, 100);
    camera.position.set(0, 0, 3.0);

    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    } catch {
      return;
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(width, h);
    renderer.domElement.style.display = "block";
    renderer.domElement.style.cursor = "grab";
    container.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enablePan = false;
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.rotateSpeed = 0.45;
    controls.zoomSpeed = 0.6;
    controls.minDistance = 1.35;
    controls.maxDistance = 5;

    const earthGroup = new THREE.Group();
    scene.add(earthGroup);

    // Solid black occluder so back-side lines don't show through
    const occluder = new THREE.Mesh(
      new THREE.SphereGeometry(GLOBE_RADIUS * 0.997, 64, 64),
      new THREE.MeshBasicMaterial({ color: 0x000000 }),
    );
    earthGroup.add(occluder);

    // Subtle radial outer halo (atmosphere ring) — backside-rendered shell
    const halo = new THREE.Mesh(
      new THREE.SphereGeometry(GLOBE_RADIUS * 1.04, 64, 64),
      new THREE.MeshBasicMaterial({
        color: 0xffffff,
        transparent: true,
        opacity: 0.04,
        side: THREE.BackSide,
      }),
    );
    earthGroup.add(halo);

    earthGroup.add(buildGraticule());
    earthGroup.add(buildEquator());

    // Country borders (async fetch)
    void buildLandLines().then((lines) => {
      if (lines && refs.current && !refs.current.destroyed) {
        earthGroup.add(lines);
      } else if (lines) {
        disposeObject(lines);
      }
    });

    const markersGroup = new THREE.Group();
    earthGroup.add(markersGroup);

    const centroidGroup = new THREE.Group();
    earthGroup.add(centroidGroup);

    const clock = new THREE.Clock();
    let isInteracting = false;
    controls.addEventListener("start", () => {
      isInteracting = true;
      renderer.domElement.style.cursor = "grabbing";
    });
    controls.addEventListener("end", () => {
      isInteracting = false;
      renderer.domElement.style.cursor = "grab";
    });

    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2(-2, -2);

    function onPointerMove(e: PointerEvent) {
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
    }
    function onPointerLeave() {
      pointer.set(-2, -2);
    }
    renderer.domElement.addEventListener("pointermove", onPointerMove);
    renderer.domElement.addEventListener("pointerleave", onPointerLeave);

    let rafId = 0;
    function animate() {
      const t = clock.getElapsedTime();

      // Subtle idle rotation when user not interacting
      if (!isInteracting) {
        earthGroup.rotation.y += 0.0006;
      }

      // Pulse ring animation
      centroidGroup.children.forEach((child) => {
        const ud = child.userData;
        if (ud && ud.isPulse) {
          const phase = (t * 0.5 + (ud.phase as number)) % 1;
          const scale = 1 + phase * 3.2;
          child.scale.setScalar(scale);
          const mesh = child as THREE.Mesh;
          (mesh.material as THREE.MeshBasicMaterial).opacity =
            (1 - phase) * 0.55;
        }
      });

      // Hover raycast against marker dots
      const r = refs.current;
      if (r && pointer.x > -1.5) {
        raycaster.setFromCamera(pointer, camera);
        const hits = raycaster.intersectObjects(markersGroup.children, false);
        const hit = hits.find((h) => h.object.userData.label !== undefined);
        const key = hit ? (hit.object.userData.key as string) : null;
        if (key !== r.hoveredKey) {
          r.hoveredKey = key;
          const tip = tooltipRef.current;
          if (tip) {
            if (hit) {
              const ud = hit.object.userData;
              tip.textContent = ud.label || `${ud.lat.toFixed(2)}, ${ud.lon.toFixed(2)}`;
              tip.style.opacity = "1";
              const rect = renderer.domElement.getBoundingClientRect();
              const cx = ((pointer.x + 1) / 2) * rect.width;
              const cy = ((1 - pointer.y) / 2) * rect.height;
              tip.style.left = `${cx + 12}px`;
              tip.style.top = `${cy + 12}px`;
            } else {
              tip.style.opacity = "0";
            }
          }
          renderer.domElement.style.cursor = hit
            ? "pointer"
            : isInteracting
              ? "grabbing"
              : "grab";
        } else if (hit && tooltipRef.current) {
          // Update tooltip position only
          const rect = renderer.domElement.getBoundingClientRect();
          const cx = ((pointer.x + 1) / 2) * rect.width;
          const cy = ((1 - pointer.y) / 2) * rect.height;
          tooltipRef.current.style.left = `${cx + 12}px`;
          tooltipRef.current.style.top = `${cy + 12}px`;
        }
      }

      controls.update();
      renderer.render(scene, camera);
      rafId = requestAnimationFrame(animate);
      if (refs.current) refs.current.rafId = rafId;
    }
    animate();

    const ro = new ResizeObserver(() => {
      const w = container.clientWidth;
      const hh = container.clientHeight || height;
      if (w === 0 || hh === 0) return;
      renderer.setSize(w, hh);
      camera.aspect = w / hh;
      camera.updateProjectionMatrix();
    });
    ro.observe(container);

    refs.current = {
      scene,
      camera,
      renderer,
      controls,
      earthGroup,
      markersGroup,
      centroidGroup,
      raycaster,
      pointer,
      hoveredKey: null,
      initialAimDone: false,
      rafId,
      destroyed: false,
    };

    return () => {
      const r = refs.current;
      if (r) r.destroyed = true;
      cancelAnimationFrame(rafId);
      ro.disconnect();
      renderer.domElement.removeEventListener("pointermove", onPointerMove);
      renderer.domElement.removeEventListener("pointerleave", onPointerLeave);
      controls.dispose();
      scene.traverse((obj) => disposeObject(obj));
      renderer.dispose();
      if (renderer.domElement.parentElement === container) {
        container.removeChild(renderer.domElement);
      }
      refs.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Rebuild markers when points change
  useEffect(() => {
    const r = refs.current;
    if (!r) return;
    clearGroup(r.markersGroup);
    points.forEach((p, i) => {
      const center = latLonToVec3(p.lat, p.lon, GLOBE_RADIUS * 1.005);
      const isTag = p.category === "tag";
      const sz = isTag ? 0.014 : 0.010 + p.confidence * 0.012;
      const dot = new THREE.Mesh(
        new THREE.SphereGeometry(sz, 12, 12),
        new THREE.MeshBasicMaterial({
          color: 0xffffff,
          transparent: true,
          opacity: isTag ? 0.7 : 0.95,
        }),
      );
      dot.position.copy(center);
      dot.userData.label = p.label ?? "";
      dot.userData.lat = p.lat;
      dot.userData.lon = p.lon;
      dot.userData.key = `m-${i}`;
      r.markersGroup.add(dot);

      // Soft halo
      const halo = new THREE.Mesh(
        new THREE.SphereGeometry(sz * 2.4, 14, 14),
        new THREE.MeshBasicMaterial({
          color: 0xffffff,
          transparent: true,
          opacity: isTag ? 0.05 : 0.10,
          depthWrite: false,
        }),
      );
      halo.position.copy(center);
      r.markersGroup.add(halo);
    });
  }, [points]);

  // Update centroid pulse + first-time camera aim
  useEffect(() => {
    const r = refs.current;
    if (!r) return;
    clearGroup(r.centroidGroup);
    if (!centroid) return;

    const center = latLonToVec3(centroid.lat, centroid.lon, GLOBE_RADIUS * 1.012);
    const outward = center.clone().multiplyScalar(2);

    // Bright centroid dot
    const dot = new THREE.Mesh(
      new THREE.SphereGeometry(0.022, 18, 18),
      new THREE.MeshBasicMaterial({ color: 0xffffff }),
    );
    dot.position.copy(center);
    r.centroidGroup.add(dot);

    // Inner radius indicator (static)
    const baseRadius = Math.max(
      0.025,
      Math.min(0.18, centroid.radiusKm / 6371),
    );
    const staticRing = new THREE.Mesh(
      new THREE.RingGeometry(baseRadius * 0.97, baseRadius, 64),
      new THREE.MeshBasicMaterial({
        color: 0xffffff,
        transparent: true,
        opacity: 0.5,
        side: THREE.DoubleSide,
      }),
    );
    staticRing.position.copy(center);
    staticRing.lookAt(outward);
    r.centroidGroup.add(staticRing);

    // Animated pulse rings (3 staggered)
    for (let i = 0; i < 3; i++) {
      const ring = new THREE.Mesh(
        new THREE.RingGeometry(baseRadius * 0.97, baseRadius, 64),
        new THREE.MeshBasicMaterial({
          color: 0xffffff,
          transparent: true,
          opacity: 0.55,
          side: THREE.DoubleSide,
          depthWrite: false,
        }),
      );
      ring.position.copy(center);
      ring.lookAt(outward);
      ring.userData.isPulse = true;
      ring.userData.phase = i / 3;
      r.centroidGroup.add(ring);
    }

    // First-time camera aim only — don't rip the camera around mid-audit
    if (!r.initialAimDone) {
      const camPos = latLonToVec3(centroid.lat, centroid.lon, 2.7);
      r.camera.position.copy(camPos);
      r.camera.lookAt(0, 0, 0);
      r.controls.update();
      r.initialAimDone = true;
    }
  }, [centroid?.lat, centroid?.lon, centroid?.radiusKm]);

  return (
    <div
      ref={containerRef}
      className="relative w-full overflow-hidden bg-black"
      style={{ height: `${height}px` }}
    >
      <div
        ref={tooltipRef}
        className="pointer-events-none absolute z-10 max-w-[220px] truncate border border-kali-border bg-kali-bg px-2 py-1 font-mono text-[10px] text-kali-text opacity-0 transition-opacity"
        style={{ left: 0, top: 0 }}
      />
    </div>
  );
}
