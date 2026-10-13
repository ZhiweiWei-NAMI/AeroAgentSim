/**
 * RegionMap — the Leaflet map behind the Region block (region editing + geometry review)
 * and the AddObjects "Pick on map" flow. Plain Leaflet inside React refs (no react-leaflet
 * runtime), glass styling via sections/map.css on top of workbench.css vars.
 *
 * Coordinates come in/go out as WGS84 bounds [west, south, east, north] and ENU metres
 * relative to `origin`, both exactly per workbench/geo.ts (the scene-compiler formula, C13).
 */
import { useEffect, useId, useMemo, useRef, useState } from 'react';
import * as Leaflet from 'leaflet';
import 'leaflet/dist/leaflet.css';
import '../map.css';
import { enuToLonLat, lonLatToEnu, type EnuOrigin } from '../../geo';

export interface RegionMapBasemap { url: string; attribution: string }
export interface RegionMapInstance { east: number; north: number; color: string; label: string }
export interface RegionMapGeometry {
  origin: EnuOrigin;
  buildings?: Array<Array<[number, number]>>; // ENU metre rings (outer ring per building)
  roads?: Array<Array<[number, number]>>; // ENU metre polylines
  truncated?: boolean;
}

export interface RegionMapProps {
  basemap: RegionMapBasemap | null;
  /** Extract coverage to fit initially and clamp against; null skips fitting. */
  coverage?: number[] | null; // [w, s, e, n] WGS84
  bounds?: number[] | null; // selected [w, s, e, n] WGS84; editable when onBoundsChange is set
  onBoundsChange?: (bounds: number[]) => void;
  geometry?: RegionMapGeometry | null;
  instances?: RegionMapInstance[];
  /** Pick mode: crosshair cursor, one click writes a marker through onPick. */
  pick?: { onPick: (east: number, north: number, inside: boolean) => void; marker?: [number, number] | null };
  outsideWarning?: string;
  /** Height in px when collapsed (default 300; pick mode uses 260). */
  height?: number;
  className?: string;
  L: (en: string, zh: string) => string;
}

const clampBoundsTo = (bounds: number[], coverage: number[]): number[] => [
  Math.min(Math.max(bounds[0], coverage[0]), coverage[2]),
  Math.min(Math.max(bounds[1], coverage[1]), coverage[3]),
  Math.min(Math.max(bounds[2], coverage[0]), coverage[2]),
  Math.min(Math.max(bounds[3], coverage[1]), coverage[3]),
];

const reducedMotion = () =>
  typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

const toBoundsExpression = (bounds: number[]): L.LatLngBoundsExpression =>
  [[bounds[1], bounds[0]], [bounds[3], bounds[2]]];

export function RegionMap({ basemap, coverage, bounds, onBoundsChange, geometry, instances, pick, outsideWarning, height, className, L }: RegionMapProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);
  const tilesRef = useRef<L.TileLayer | null>(null);
  const coverageRef = useRef<L.Rectangle | null>(null);
  const boundsRef = useRef<L.Rectangle | null>(null);
  const handlesRef = useRef<L.LayerGroup | null>(null);
  const buildingsRef = useRef<L.LayerGroup | null>(null);
  const roadsRef = useRef<L.LayerGroup | null>(null);
  const originRef = useRef<L.Marker | null>(null);
  const instancesRef = useRef<L.LayerGroup | null>(null);
  const pickMarkerRef = useRef<L.Marker | null>(null);
  const drawRectRef = useRef<L.Rectangle | null>(null);
  const drawStartRef = useRef<L.LatLng | null>(null);
  const suppressRef = useRef(false); // don't echo drag-driven writes back into the map
  const latest = useRef({ coverage, bounds, onBoundsChange, geometry, instances, pick, basemap });
  latest.current = { coverage, bounds, onBoundsChange, geometry, instances, pick, basemap };

  const [expanded, setExpanded] = useState(false);
  const [wheelArmed, setWheelArmed] = useState(false);
  const [basemapLive, setBasemapLive] = useState(false);
  const [basemapDown, setBasemapDown] = useState(false);
  const [drawing, setDrawing] = useState(false);
  const [outside, setOutside] = useState(false);
  const [fitted, setFitted] = useState(false);
  const expandId = useId();
  const drawingRef = useRef(false);
  useEffect(() => { drawingRef.current = drawing; }, [drawing]);

  const sizeStyle = { height: height !== undefined && !expanded ? `${height}px` : undefined };

  // --- create / destroy the map once ---
  useEffect(() => {
    if (!hostRef.current || mapRef.current) return;
    const map = Leaflet.map(hostRef.current, {
      zoomControl: true, attributionControl: true,
      scrollWheelZoom: false, // armed only on click, off again on leave (spec 1)
    });
    mapRef.current = map;
    map.on('click', () => { setWheelArmed(true); map.options.scrollWheelZoom = true; });
    map.on('mouseout', () => { setWheelArmed(false); map.options.scrollWheelZoom = false; });
    map.on('mousedown', (event: L.LeafletMouseEvent) => {
      const state = latest.current;
      if (!state.onBoundsChange || !drawingRef.current) return;
      drawStartRef.current = event.latlng;
    });
    map.on('mousemove', (event: L.LeafletMouseEvent) => {
      const state = latest.current;
      const start = drawStartRef.current;
      if (!state.onBoundsChange || !drawingRef.current || !start) return;
      const w = Math.min(start.lng, event.latlng.lng), e = Math.max(start.lng, event.latlng.lng);
      const s = Math.min(start.lat, event.latlng.lat), n = Math.max(start.lat, event.latlng.lat);
      if (!drawRectRef.current) {
        drawRectRef.current = Leaflet.rectangle(toBoundsExpression([w, s, e, n]), {
          color: '#1f7ae0', weight: 1.5, dashArray: '4 4', fillOpacity: 0.04, interactive: false,
        }).addTo(map);
      } else drawRectRef.current.setBounds(toBoundsExpression([w, s, e, n]));
    });
    map.on('mouseup', (event: L.LeafletMouseEvent) => {
      const state = latest.current;
      const start = drawStartRef.current;
      if (!state.onBoundsChange || !drawingRef.current || !start || !drawRectRef.current) { drawStartRef.current = null; return; }
      const w = Math.min(start.lng, event.latlng.lng), e = Math.max(start.lng, event.latlng.lng);
      const s = Math.min(start.lat, event.latlng.lat), n = Math.max(start.lat, event.latlng.lat);
      drawRectRef.current.remove(); drawRectRef.current = null; drawStartRef.current = null;
      const coverageNow = state.coverage;
      let next = [w, s, e, n];
      if (coverageNow && coverageNow.length === 4) next = clampBoundsTo(next, coverageNow);
      if (next[2] - next[0] > 1e-9 && next[3] - next[1] > 1e-9) state.onBoundsChange?.(next);
    });
    // Pick mode click.
    map.on('dblclick', () => { /* avoid interference while drawing */ });
    map.on('click', (event: L.LeafletMouseEvent) => {
      const state = latest.current;
      if (!state.pick || drawingRef.current) return;
      const origin = state.geometry?.origin;
      if (!origin) return;
      const enu = lonLatToEnu(event.latlng.lng, event.latlng.lat, origin);
      const coverageNow = state.coverage;
      const inside = !coverageNow || coverageNow.length !== 4
        || (event.latlng.lng >= coverageNow[0] && event.latlng.lng <= coverageNow[2]
          && event.latlng.lat >= coverageNow[1] && event.latlng.lat <= coverageNow[3]);
      setOutside(!inside);
      state.pick.onPick(enu.east, enu.north, inside);
    });
    return () => { map.remove(); mapRef.current = null; };
  }, []);

  // Esc cancels an active "Draw area" drag (spec 3).
  useEffect(() => {
    if (!drawing) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      setDrawing(false);
      drawStartRef.current = null;
      drawRectRef.current?.remove();
      drawRectRef.current = null;
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [drawing]);

  // --- basemap tile layer (C9): null or first tile error → hide + notice chip ---
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (tilesRef.current) { tilesRef.current.remove(); tilesRef.current = null; }
    setBasemapLive(false);
    setBasemapDown(false);
    if (!basemap?.url) { setBasemapDown(true); return; }
    const tiles = Leaflet.tileLayer(basemap.url, { attribution: basemap.attribution, maxZoom: 19 });
    let firstSuccess = false, firstError = false;
    tiles.on('tileload', () => { if (!firstSuccess) { firstSuccess = true; setBasemapLive(true); } });
    tiles.on('tileerror', () => { if (!firstSuccess && !firstError) { firstError = true; tiles.remove(); tilesRef.current = null; setBasemapDown(true); } });
    tiles.addTo(map);
    tilesRef.current = tiles;
    // A few seconds with zero loaded tiles (e.g. fully offline) also counts as down.
    const timer = window.setTimeout(() => { if (!firstSuccess && !firstError) { tiles.remove(); tilesRef.current = null; setBasemapDown(true); } }, 8000);
    return () => window.clearTimeout(timer);
  }, [basemap]);

  // --- extract coverage rectangle: dashed #1f7ae0 "4 4", fill-opacity .04 ---
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (coverageRef.current) { coverageRef.current.remove(); coverageRef.current = null; }
    if (!coverage || coverage.length !== 4) return;
    coverageRef.current = Leaflet.rectangle(toBoundsExpression(coverage), {
      color: '#1f7ae0', weight: 1.5, dashArray: '4 4', fillOpacity: 0.04, interactive: false,
    }).addTo(map);
  }, [coverage]);

  // --- selected bounds rectangle + 4 draggable corner handles ---
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (boundsRef.current) { boundsRef.current.remove(); boundsRef.current = null; }
    handlesRef.current?.remove(); handlesRef.current = null;
    if (!bounds || bounds.length !== 4 || !onBoundsChange) return;
    const layer = Leaflet.rectangle(toBoundsExpression(bounds), {
      color: '#1f7ae0', weight: 2, fillOpacity: 0.1, interactive: false,
    }).addTo(map);
    boundsRef.current = layer;
    const write = (next: number[]) => {
      const clamped = latest.current.coverage && latest.current.coverage!.length === 4
        ? clampBoundsTo(next, latest.current.coverage!) : next;
      if (clamped[2] - clamped[0] <= 1e-9 || clamped[3] - clamped[1] <= 1e-9) return;
      suppressRef.current = true; // our own write-back; do not re-sync from props
      latest.current.onBoundsChange?.(clamped);
    };
    // Corner handles: small white circles, 2px accent border (CSS class .wb-map-handle).
    const group = Leaflet.layerGroup().addTo(map);
    handlesRef.current = group;
    const corners: Array<[string, number, number]> = [
      ['nw', bounds[0], bounds[3]], ['ne', bounds[2], bounds[3]],
      ['sw', bounds[0], bounds[1]], ['se', bounds[2], bounds[1]],
    ];
    for (const [key, lon, lat] of corners) {
      const handle = Leaflet.marker([lat, lon], {
        draggable: true, keyboard: false,
        icon: Leaflet.divIcon({ className: `wb-map-handle ${key}`, iconSize: [10, 10], iconAnchor: [5, 5] }),
      }).addTo(group);
      handle.on('drag', (event: L.LeafletEvent) => {
        const position = (event.target as L.Marker).getLatLng();
        const b = boundsRef.current?.getBounds();
        if (!b) return;
        const next: number[] = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
        if (key.includes('w')) next[0] = position.lng; else next[2] = position.lng;
        if (key.includes('n')) next[3] = position.lat; else next[1] = position.lat;
        layer.setBounds(toBoundsExpression(next));
      });
      handle.on('dragend', (event: L.LeafletEvent) => {
        const position = (event.target as L.Marker).getLatLng();
        const b = layer.getBounds();
        const next: number[] = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
        if (key.includes('w')) next[0] = position.lng; else next[2] = position.lng;
        if (key.includes('n')) next[3] = position.lat; else next[1] = position.lat;
        write(next);
      });
    }
    return () => { group.remove(); };
  }, [bounds, onBoundsChange]);

  // --- two-way sync: typed bounds move the rectangle; drag writes are not echoed ---
  useEffect(() => {
    if (suppressRef.current) { suppressRef.current = false; return; }
    if (!bounds || bounds.length !== 4 || !boundsRef.current) return;
    boundsRef.current.setBounds(toBoundsExpression(bounds));
    if (handlesRef.current) {
      const corners: Array<[string, number, number]> = [
        ['nw', bounds[0], bounds[3]], ['ne', bounds[2], bounds[3]],
        ['sw', bounds[0], bounds[1]], ['se', bounds[2], bounds[1]],
      ];
      let index = 0;
      handlesRef.current.eachLayer(child => {
        (child as L.Marker).setLatLng([corners[index][2], corners[index][1]]);
        index++;
      });
    }
  }, [bounds]);

  // --- compiled/preview geometry: buildings, roads, origin crosshair (spec 4) ---
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    buildingsRef.current?.remove(); buildingsRef.current = null;
    roadsRef.current?.remove(); roadsRef.current = null;
    originRef.current?.remove(); originRef.current = null;
    if (!geometry) return;
    const toLatLng = (point: [number, number]): L.LatLngExpression => {
      const { lat, lon } = enuToLonLat(point[0], point[1], geometry!.origin);
      return [lat, lon];
    };
    if (geometry.buildings?.length) {
      const group = Leaflet.layerGroup().addTo(map);
      buildingsRef.current = group;
      for (const ring of geometry.buildings) {
        if (!ring || ring.length < 3) continue;
        Leaflet.polygon(ring.map(toLatLng), {
          color: '#5b6b7d', weight: 0.6, opacity: 0.6, fillColor: '#5b6b7d', fillOpacity: 0.35, interactive: false,
        }).addTo(group);
      }
    }
    if (geometry.roads?.length) {
      const group = Leaflet.layerGroup().addTo(map);
      roadsRef.current = group;
      for (const points of geometry.roads) {
        if (!points || points.length < 2) continue;
        Leaflet.polyline(points.map(toLatLng), { color: '#e8a317', weight: 1.5, interactive: false }).addTo(group);
      }
    }
    const center = enuToLonLat(0, 0, geometry.origin);
    originRef.current = Leaflet.marker([center.lat, center.lon], {
      interactive: false, keyboard: false,
      icon: Leaflet.divIcon({ className: 'wb-map-origin', iconSize: [12, 12], iconAnchor: [6, 6] }),
    }).addTo(map);
  }, [geometry]);

  // --- existing instances as 8px dots in their presentation colour (spec 5) ---
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    instancesRef.current?.remove();
    instancesRef.current = null;
    if (!instances?.length || !geometry) return;
    const group = Leaflet.layerGroup().addTo(map);
    instancesRef.current = group;
    for (const instance of instances) {
      const { lat, lon } = enuToLonLat(instance.east, instance.north, geometry.origin);
      const dot = Leaflet.circleMarker([lat, lon], {
        radius: 4, color: instance.color, weight: 1, fillColor: instance.color, fillOpacity: 0.9,
      }).addTo(group);
      dot.bindTooltip(instance.label, { direction: 'top', offset: [0, -4] });
    }
  }, [instances, geometry]);

  // --- pick marker (AddObjects) ---
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    pickMarkerRef.current?.remove();
    pickMarkerRef.current = null;
    if (!pick?.marker || !geometry) return;
    const { lat, lon } = enuToLonLat(pick.marker[0], pick.marker[1], geometry.origin);
    pickMarkerRef.current = Leaflet.marker([lat, lon], {
      interactive: false, keyboard: false,
      icon: Leaflet.divIcon({ className: 'wb-map-pick-marker', iconSize: [10, 10], iconAnchor: [5, 5] }),
    }).addTo(map);
  }, [pick?.marker, geometry]);

  // --- initial view: fit the extract coverage once, no animation under reduced motion ---
  useEffect(() => {
    const map = mapRef.current;
    if (!map || fitted || !coverage || coverage.length !== 4) return;
    map.fitBounds(toBoundsExpression(coverage), {
      animate: !reducedMotion(), padding: Leaflet.point(8, 8),
    });
    setFitted(true);
  }, [coverage, fitted]);

  // Expanded height changes need a re-measure.
  useEffect(() => { mapRef.current?.invalidateSize({ animate: false }); }, [expanded]);

  const size = useMemo(() => {
    if (!bounds || bounds.length !== 4) return null;
    const origin: EnuOrigin = geometry?.origin ?? { lat: bounds[1], lon: bounds[0], alt: 0 };
    const width = lonLatToEnu(bounds[2], bounds[1], origin).east - lonLatToEnu(bounds[0], bounds[1], origin).east;
    const depth = lonLatToEnu(bounds[0], bounds[3], origin).north - lonLatToEnu(bounds[0], bounds[1], origin).north;
    if (!Number.isFinite(width) || !Number.isFinite(depth)) return null;
    return { width, depth };
  }, [bounds, geometry?.origin]);

  const formatKm = (metres: number) => `${(metres / 1000).toFixed(2)} km`;

  return <div className={`wb-map${expanded ? ' wb-map-expanded' : ''}${pick ? ' wb-map-pick' : ''}${basemapLive ? ' wb-map-basemap' : ''} ${className ?? ''}`}>
    <div ref={hostRef} className="wb-map-leaflet" style={sizeStyle} data-testid="wb-map-surface" />
    <div className="wb-map-toolbar">
      {size && <span className="wb-map-chip wb-mono" data-testid="wb-map-size">{formatKm(size.width)} × {formatKm(size.depth)}</span>}
      {onBoundsChange && <button type="button" className="wb-btn small" aria-pressed={drawing}
        onClick={() => setDrawing(previous => !previous)}>{L('Draw area', '框选范围')}</button>}
      <span className="wb-map-spacer" />
      {!wheelArmed && !pick && <span className="wb-map-chip" role="status">{L('Click the map to zoom with the wheel', '点击地图后即可滚轮缩放')}</span>}
      {pick && <span className="wb-map-chip" role="status">{L('Click to place — east/north fill in automatically', '点击放置——东/北坐标会自动填入')}</span>}
      {pick && outside && outsideWarning && <span className="wb-map-chip warn" role="alert">{outsideWarning}</span>}
      {basemapDown && <span className="wb-map-chip warn" role="status">{L('Basemap offline — outlines and compiled geometry still shown', '底图离线——轮廓与已编译几何仍会显示')}</span>}
    </div>
    <button type="button" className="wb-btn small ghost wb-map-expand" aria-label={L('Expand map', '展开地图')}
      aria-expanded={expanded} aria-controls={expandId} title={L('Expand map', '展开地图')}
      onClick={() => setExpanded(previous => !previous)}>⤢</button>
    <span id={expandId} hidden>{L('Region map', '区域地图')}</span>
  </div>;
}

/** Bounded helper for the C10 preview / compiled-scene ENU polygons → map rings. */
export const enuRings = (features: Array<{ geometry?: { type?: unknown; coordinates?: unknown } | null }>): Array<Array<[number, number]>> => {
  const rings: Array<Array<[number, number]>> = [];
  for (const feature of features) {
    const geometry = feature.geometry;
    if (!geometry) continue;
    if (geometry.type === 'Polygon') {
      const outer = (geometry.coordinates as number[][][])?.[0];
      if (Array.isArray(outer)) rings.push(outer.map(point => [point[0], point[1]] as [number, number]));
    }
  }
  return rings;
};

/** C10 preview.geometry / compiled roads ENU polylines → map polylines. */
export const enuLines = (lines: number[][][] | undefined | null): Array<Array<[number, number]>> =>
  (lines ?? []).filter(Array.isArray).map(points => Array.isArray(points)
    ? points.filter(Array.isArray).map(point => [point[0], point[1]] as [number, number])
    : []);
