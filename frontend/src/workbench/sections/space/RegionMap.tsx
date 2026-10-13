/**
 * RegionMap — the Leaflet map behind the Region block (region editing + geometry review)
 * and the AddObjects "Pick on map" flow. Plain Leaflet inside React refs (no react-leaflet
 * runtime), glass styling via sections/map.css on top of workbench.css vars.
 *
 * Coordinates come in/go out as WGS84 bounds [west, south, east, north] and ENU metres
 * relative to `origin`, both exactly per workbench/geo.ts (the scene-compiler formula, C13).
 */
import { Component, useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react';
import * as Leaflet from 'leaflet';
import 'leaflet/dist/leaflet.css';
import '../map.css';
import { enuToLonLat, lonLatToEnu, validEnuOrigin, validWgs84Bounds, type EnuOrigin } from '../../geo';
import type { GeometryFeature, RegionRoad } from './model';

export interface RegionMapBasemap { url: string; attribution: string; max_zoom?: number }
export interface RegionMapInstance { east: number; north: number; color: string; label: string; letter?: string }
export interface RegionMapGeometry {
  origin: EnuOrigin;
  buildings?: GeometryFeature[];
  /** C10 preview footprints are ENU; saved scene.geojson footprints are WGS84. */
  buildingFrame?: 'enu' | 'wgs84';
  roads?: RegionRoad[]; // C10 and saved scenes both use records with ENU points
  truncated?: boolean;
}

export interface RegionMapProps {
  basemap: RegionMapBasemap | null;
  basemapLoading?: boolean;
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

const toBoundsExpression = (bounds: number[]): L.LatLngBoundsExpression =>
  [[bounds[1], bounds[0]], [bounds[3], bounds[2]]];

/** Keep a Leaflet render/effect failure local, leaving the surrounding bounds form usable. */
class MapBoundary extends Component<{ children: ReactNode; L: RegionMapProps['L'] }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    return this.state.failed ? <div className="wb-empty" role="alert">
      <p>{this.props.L('Map unavailable — bounds can still be typed', '地图不可用——仍可手动输入范围')}</p>
      <button type="button" className="wb-btn small" onClick={() => this.setState({ failed: false })}>
        {this.props.L('Retry map', '重试地图')}</button>
    </div> : this.props.children;
  }
}

export function RegionMap(props: RegionMapProps) {
  return <MapBoundary L={props.L}><RegionMapCanvas {...props} /></MapBoundary>;
}

function RegionMapCanvas({ basemap, basemapLoading, coverage, bounds, onBoundsChange, geometry, instances, pick, outsideWarning, height, className, L }: RegionMapProps) {
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
  const latest = useRef({ coverage, bounds, onBoundsChange, geometry, instances, pick, basemap });
  latest.current = { coverage, bounds, onBoundsChange, geometry, instances, pick, basemap };

  const [expanded, setExpanded] = useState(false);
  const [wheelArmed, setWheelArmed] = useState(false);
  const [tileState, setTileState] = useState<'disabled' | 'loading' | 'live' | 'error'>('disabled');
  const [drawing, setDrawing] = useState(false);
  const [outside, setOutside] = useState(false);
  const fittedCoverage = useRef<string>();
  const expandId = useId();
  const drawingRef = useRef(false);
  const changeDrawing = (active: boolean) => { drawingRef.current = active; setDrawing(active); };

  const sizeStyle = { height: height !== undefined && !expanded ? `${height}px` : undefined };

  // --- create / destroy the map once ---
  useEffect(() => {
    if (!hostRef.current || mapRef.current) return;
    const map = Leaflet.map(hostRef.current, {
      zoomControl: true, attributionControl: true,
      scrollWheelZoom: false, // armed only on click, off again on leave (spec 1)
      zoomAnimation: false, fadeAnimation: false, markerZoomAnimation: false,
    });
    mapRef.current = map;
    // Leaflet's renderer needs a centre/zoom before ANY tiles, vectors or markers attach.
    // The empty-state view is only a camera, never a source for authored coordinates.
    const state = latest.current;
    const extent = validWgs84Bounds(state.bounds) ? state.bounds : validWgs84Bounds(state.coverage) ? state.coverage : null;
    const origin = validEnuOrigin(state.geometry?.origin) ? state.geometry.origin : null;
    map.setView(extent ? [(extent[1] + extent[3]) / 2, (extent[0] + extent[2]) / 2]
      : origin ? [origin.lat, origin.lon] : [0, 0], extent || origin ? 14 : 2, { animate: false });
    if (extent) map.fitBounds(toBoundsExpression(extent), { animate: false, padding: Leaflet.point(8, 8), maxZoom: 19 });
    fittedCoverage.current = validWgs84Bounds(state.coverage) ? state.coverage.join(',') : undefined;
    map.on('click', () => {
      if (drawingRef.current) return;
      map.scrollWheelZoom.enable(); setWheelArmed(true);
    });
    map.on('mouseout', () => { map.scrollWheelZoom.disable(); setWheelArmed(false); });
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
      if (!state.onBoundsChange || !drawingRef.current || !start) return;
      const w = Math.min(start.lng, event.latlng.lng), e = Math.max(start.lng, event.latlng.lng);
      const s = Math.min(start.lat, event.latlng.lat), n = Math.max(start.lat, event.latlng.lat);
      const coverageNow = state.coverage;
      let next = [w, s, e, n];
      if (validWgs84Bounds(coverageNow)) next = clampBoundsTo(next, coverageNow);
      if (validWgs84Bounds(next)) state.onBoundsChange(next);
      changeDrawing(false);
    });
    // Pick mode click.
    map.on('click', (event: L.LeafletMouseEvent) => {
      const state = latest.current;
      if (!state.pick || drawingRef.current) return;
      const origin = state.geometry?.origin;
      if (!validEnuOrigin(origin)) return;
      const enu = lonLatToEnu(event.latlng.lng, event.latlng.lat, origin);
      const coverageNow = state.coverage;
      const inside = !validWgs84Bounds(coverageNow)
        || (event.latlng.lng >= coverageNow[0] && event.latlng.lng <= coverageNow[2]
          && event.latlng.lat >= coverageNow[1] && event.latlng.lat <= coverageNow[3]);
      setOutside(!inside);
      state.pick.onPick(enu.east, enu.north, inside);
    });
    return () => { map.remove(); mapRef.current = null; };
  }, []);

  // Drawing owns the drag gesture. Restore the exact navigation state on completion/cancel.
  useEffect(() => {
    const map = mapRef.current;
    if (!drawing || !map) return;
    const wasDragging = map.dragging.enabled(), wasDoubleClicking = map.doubleClickZoom.enabled();
    map.dragging.disable(); map.doubleClickZoom.disable(); map.scrollWheelZoom.disable();
    setWheelArmed(false);
    return () => {
      drawStartRef.current = null;
      drawRectRef.current?.remove();
      drawRectRef.current = null;
      if (mapRef.current !== map) return;
      if (wasDragging) map.dragging.enable();
      if (wasDoubleClicking) map.doubleClickZoom.enable();
    };
  }, [drawing]);

  // --- basemap tile layer (C9): report real load errors without replacing geography ---
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (tilesRef.current) { tilesRef.current.remove(); tilesRef.current = null; }
    if (!basemap?.url) { setTileState('disabled'); return; }
    setTileState('loading');
    const tiles = Leaflet.tileLayer(basemap.url, { attribution: basemap.attribution, maxZoom: basemap.max_zoom ?? 19 });
    let failed = false;
    tiles.on('tileload', () => { if (!failed) setTileState('live'); });
    tiles.on('tileerror', () => { failed = true; setTileState('error'); });
    tiles.addTo(map);
    tilesRef.current = tiles;
    return () => { tiles.off(); tiles.remove(); if (tilesRef.current === tiles) tilesRef.current = null; };
  }, [basemap]);

  // --- extract coverage rectangle: dashed #1f7ae0 "4 4", fill-opacity .04 ---
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (coverageRef.current) { coverageRef.current.remove(); coverageRef.current = null; }
    if (!validWgs84Bounds(coverage)) return;
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
    if (!validWgs84Bounds(bounds)) return;
    const layer = Leaflet.rectangle(toBoundsExpression(bounds), {
      color: '#1f7ae0', weight: 2, fillOpacity: 0.1, interactive: false,
    }).addTo(map);
    boundsRef.current = layer;
    if (!onBoundsChange || drawing) return;
    const write = (next: number[]) => {
      const clamped = validWgs84Bounds(latest.current.coverage)
        ? clampBoundsTo(next, latest.current.coverage!) : next;
      if (clamped[2] - clamped[0] <= 1e-9 || clamped[3] - clamped[1] <= 1e-9) return;
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
  }, [bounds, onBoundsChange, drawing]);

  // --- compiled/preview geometry: buildings, roads, origin crosshair (spec 4) ---
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    buildingsRef.current?.remove(); buildingsRef.current = null;
    roadsRef.current?.remove(); roadsRef.current = null;
    originRef.current?.remove(); originRef.current = null;
    if (!geometry) return;
    if (!validEnuOrigin(geometry.origin)) throw new TypeError('Map geometry has no valid scene origin.');
    const toLatLng = (point: [number, number]): L.LatLngExpression => {
      const { lat, lon } = enuToLonLat(point[0], point[1], geometry!.origin);
      return [lat, lon];
    };
    if (geometry.buildings?.length) {
      const group = Leaflet.layerGroup().addTo(map);
      buildingsRef.current = group;
      for (const polygon of geometryPolygons(geometry.buildings)) {
        const toBuildingLatLng = geometry.buildingFrame === 'wgs84'
          ? (point: [number, number]): L.LatLngExpression => [point[1], point[0]] : toLatLng;
        Leaflet.polygon(polygon.map(ring => ring.map(toBuildingLatLng)), {
          color: '#1f7ae0', weight: 0.6, opacity: 0.6, fillColor: '#38c6f4', fillOpacity: 0.35, interactive: false,
        }).addTo(group);
      }
    }
    if (geometry.roads?.length) {
      const group = Leaflet.layerGroup().addTo(map);
      roadsRef.current = group;
      for (const points of enuLines(geometry.roads)) {
        Leaflet.polyline(points.map(toLatLng), { color: '#14202e', weight: 2, interactive: false }).addTo(group);
      }
    }
    const center = enuToLonLat(0, 0, geometry.origin);
    originRef.current = Leaflet.marker([center.lat, center.lon], {
      interactive: false, keyboard: false,
      icon: Leaflet.divIcon({ className: 'wb-map-origin', iconSize: [12, 12], iconAnchor: [6, 6] }),
    }).addTo(map);
  }, [geometry]);

  // --- existing places and fleet starts in their role colours ---
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
      const icon = document.createElement('span');
      icon.className = 'wb-map-dot'; icon.style.backgroundColor = instance.color;
      icon.textContent = instance.letter ?? '';
      const dot = Leaflet.marker([lat, lon], { keyboard: true,
        icon: Leaflet.divIcon({ className: '', html: icon, iconSize: [18, 18], iconAnchor: [9, 9] }),
        title: instance.label,
      }).addTo(group);
      const tooltip = document.createElement('span'); tooltip.textContent = instance.label;
      dot.bindTooltip(tooltip, { direction: 'top', offset: [0, -4] });
    }
  }, [instances, geometry]);

  // --- pick marker (AddObjects) ---
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    pickMarkerRef.current?.remove();
    pickMarkerRef.current = null;
    if (!pick?.marker || !pick.marker.every(Number.isFinite) || !geometry) return;
    const { lat, lon } = enuToLonLat(pick.marker[0], pick.marker[1], geometry.origin);
    pickMarkerRef.current = Leaflet.marker([lat, lon], {
      interactive: false, keyboard: false,
      icon: Leaflet.divIcon({ className: 'wb-map-pick-marker', iconSize: [10, 10], iconAnchor: [5, 5] }),
    }).addTo(map);
  }, [pick?.marker, geometry]);

  useEffect(() => {
    if (!pick?.marker || !pick.marker.every(Number.isFinite) || !geometry || !validWgs84Bounds(coverage)) { setOutside(false); return; }
    const point = enuToLonLat(pick.marker[0], pick.marker[1], geometry.origin);
    setOutside(point.lon < coverage[0] || point.lon > coverage[2] || point.lat < coverage[1] || point.lat > coverage[3]);
  }, [pick?.marker, geometry, coverage]);

  useEffect(() => {
    const control = mapRef.current?.zoomControl.getContainer();
    const labels = [L('Zoom in', '放大'), L('Zoom out', '缩小')];
    control?.querySelectorAll('a').forEach((button, index) => {
      button.title = labels[index]; button.setAttribute('aria-label', labels[index]);
    });
  }, [L]);

  // Fit a newly selected extract; equal arrays from parent renders keep the user's camera.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !validWgs84Bounds(coverage) || fittedCoverage.current === coverage.join(',')) return;
    map.fitBounds(toBoundsExpression(coverage), {
      animate: false, padding: Leaflet.point(8, 8), maxZoom: 19,
    });
    fittedCoverage.current = coverage.join(',');
  }, [coverage]);

  // Expanded height changes need a re-measure.
  useEffect(() => { mapRef.current?.invalidateSize({ animate: false }); }, [expanded]);

  const size = useMemo(() => {
    if (!validWgs84Bounds(bounds)) return null;
    const origin: EnuOrigin = geometry?.origin ?? { lat: bounds[1], lon: bounds[0], alt: 0 };
    const width = lonLatToEnu(bounds[2], bounds[1], origin).east - lonLatToEnu(bounds[0], bounds[1], origin).east;
    const depth = lonLatToEnu(bounds[0], bounds[3], origin).north - lonLatToEnu(bounds[0], bounds[1], origin).north;
    if (!Number.isFinite(width) || !Number.isFinite(depth)) return null;
    return { width, depth };
  }, [bounds, geometry?.origin]);

  const formatKm = (metres: number) => `${(metres / 1000).toFixed(2)} km`;

  // Workbench's window-capture handler yields to a dialog containing focus. This
  // nonmodal drawing dialog lets local Escape cancel before the panel can close.
  return <div className={`wb-map${expanded ? ' wb-map-expanded' : ''}${pick ? ' wb-map-pick' : ''}${tileState === 'live' ? ' wb-map-basemap' : ''} ${className ?? ''}`}
    role={drawing ? 'dialog' : undefined} aria-label={drawing ? L('Draw area', '框选范围') : undefined}
    onKeyDownCapture={event => {
      if (event.key !== 'Escape' || !drawingRef.current) return;
      event.preventDefault(); event.stopPropagation(); changeDrawing(false);
    }}>
    <div ref={hostRef} className="wb-map-leaflet" tabIndex={0} aria-label={L('Region map', '区域地图')}
      style={sizeStyle} data-testid="wb-map-surface" />
    <div className="wb-map-toolbar">
      {size && <span className="wb-map-chip wb-mono" data-testid="wb-map-size">{formatKm(size.width)} × {formatKm(size.depth)}</span>}
      {onBoundsChange && <button type="button" className="wb-btn small" aria-pressed={drawing}
        onClick={() => { changeDrawing(!drawingRef.current); hostRef.current?.focus(); }}>{L('Draw area', '框选范围')}</button>}
      {drawing && <span className="wb-map-chip" role="status">{L('Drag to select bounds; Esc cancels', '拖动选择范围；Esc 取消')}</span>}
      <span className="wb-map-spacer" />
      {!wheelArmed && !pick && <span className="wb-map-chip" role="status">{L('Click the map to zoom with the wheel', '点击地图后即可滚轮缩放')}</span>}
      {pick && <span className="wb-map-chip" role="status">{L('Click to place — east/north fill in automatically', '点击放置——东/北坐标会自动填入')}</span>}
      {pick && outside && outsideWarning && <span className="wb-map-chip warn" role="alert">{outsideWarning}</span>}
      {basemapLoading && <span className="wb-map-chip" role="status">{L('Loading map background…', '正在加载地图底图…')}</span>}
      {tileState === 'disabled' && !basemapLoading && <span className="wb-map-chip" role="status">{L('No basemap configured — bounds and geometry are shown', '未配置底图——显示范围与几何')}</span>}
      {tileState === 'error' && <span className="wb-map-chip warn" role="status">{L('Basemap tiles unavailable — bounds and geometry are shown', '底图瓦片不可用——显示范围与几何')}</span>}
    </div>
    <button type="button" className="wb-btn small ghost wb-map-expand" aria-label={L('Expand map', '展开地图')}
      aria-expanded={expanded} aria-controls={expandId} title={L('Expand map', '展开地图')}
      onClick={() => setExpanded(previous => !previous)}>⤢</button>
    <span id={expandId} hidden>{L('Region map', '区域地图')}</span>
  </div>;
}

type MapRing = Array<[number, number]>;
/** Preserve polygon holes and every MultiPolygon member, in the input coordinate frame. */
export const geometryPolygons = (features: GeometryFeature[]): MapRing[][] => {
  const polygons: MapRing[][] = [];
  const ring = (raw: unknown): MapRing => {
    if (!Array.isArray(raw) || raw.length < 3) throw new TypeError('Building ring needs at least three points.');
    return raw.map(point => {
      if (!Array.isArray(point) || !Number.isFinite(point[0]) || !Number.isFinite(point[1])) throw new TypeError('Building coordinates must be finite numbers.');
      return [point[0], point[1]] as [number, number];
    });
  };
  const polygon = (raw: unknown): MapRing[] => {
    if (!Array.isArray(raw) || !raw.length) throw new TypeError('Building polygon has no rings.');
    return raw.map(ring);
  };
  for (const feature of features) {
    const geometry = feature.geometry;
    if (!geometry) continue;
    if (geometry.type === 'Polygon') {
      polygons.push(polygon(geometry.coordinates));
    } else if (geometry.type === 'MultiPolygon') {
      if (!Array.isArray(geometry.coordinates)) throw new TypeError('Building multipolygon has no polygons.');
      polygons.push(...geometry.coordinates.map(polygon));
    }
  }
  return polygons;
};

export const enuRings = (features: GeometryFeature[]): MapRing[] => geometryPolygons(features).map(polygon => polygon[0]);

/** C10 preview.geometry / compiled roads ENU polylines → map polylines. */
export const enuLines = (lines: RegionRoad[] | undefined | null): MapRing[] => (lines ?? []).map(road => {
  if (!Array.isArray(road.points) || road.points.length < 2) throw new TypeError('Road needs at least two points.');
  return road.points.map(point => {
    if (!Array.isArray(point) || !Number.isFinite(point[0]) || !Number.isFinite(point[1])) throw new TypeError('Road coordinates must be finite numbers.');
    return [point[0], point[1]] as [number, number];
  });
});
