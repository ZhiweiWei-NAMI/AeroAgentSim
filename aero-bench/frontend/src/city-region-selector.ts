import { parseOsmJson, type OsmNode } from "./osm2world/source";
import "./city-region-selector.css";

export const SHANGHAI_SOURCE_ID = "shanghai-central-osm-v1";
export const SHANGHAI_SOURCE_SHA256 = "d0f3f30f846e8145aecd497e20ee95bc58e5cb0b3d4b4f46d1339e285217b847";
export const SHANGHAI_SOURCE_URL = "/osm2world/shanghai-hongqiao.osm.json";

export interface SceneOrigin {
  readonly latitude_deg: number;
  readonly longitude_deg: number;
  readonly ellipsoid_height_m: number;
  readonly geoid_undulation_m: number;
  readonly amsl_m: number;
}

export const SHANGHAI_ORIGIN: SceneOrigin = {
  latitude_deg: 31.2304, longitude_deg: 121.4737,
  ellipsoid_height_m: 50, geoid_undulation_m: 30, amsl_m: 20,
};

export interface SceneSelection {
  readonly schema_version: "aero-bench.scene-selection/v1";
  readonly source_id: string;
  readonly source_sha256: string;
  readonly origin: SceneOrigin;
  readonly bounds_enu_m: {
    readonly min_east_m: number; readonly max_east_m: number;
    readonly min_north_m: number; readonly max_north_m: number;
  };
}

export interface GeographicBounds {
  readonly minlat: number; readonly maxlat: number;
  readonly minlon: number; readonly maxlon: number;
}

export interface MapFeature {
  readonly id: number;
  readonly kind: "building" | "road";
  readonly points: readonly (readonly [number, number])[]; // longitude, latitude
}

export interface RegionSource {
  readonly source_id: string;
  readonly source_sha256: string;
  readonly bytes: Uint8Array<ArrayBuffer>;
  readonly origin: SceneOrigin;
}

export interface LoadedRegionSource {
  readonly source_id: string;
  readonly source_sha256: string;
  readonly origin: SceneOrigin;
  readonly bounds: GeographicBounds;
  readonly features: readonly MapFeature[];
}

export interface RegionSelectorOptions {
  readonly source: RegionSource;
  readonly onSelection: (selection: SceneSelection, geographicBounds: GeographicBounds) => void;
}

const RADIANS = Math.PI / 180;
const A = 6378137.0;
const F = 1 / 298.257223563;
const E2 = F * (2 - F);
const SOURCE_EDGE_INSET_DEG = 2e-8; // Same local-source acceptance policy as the authoring registry.

function finite(value: number, label: string): void {
  if (!Number.isFinite(value)) throw new Error(`${label} must be finite`);
}

function validateOrigin(origin: SceneOrigin): void {
  for (const [key, value] of Object.entries(origin)) finite(value, `origin.${key}`);
  if (origin.latitude_deg <= -90 || origin.latitude_deg >= 90 || origin.longitude_deg < -180 || origin.longitude_deg >= 180) throw new Error("invalid origin WGS84 coordinates");
  if (Math.abs(origin.ellipsoid_height_m - (origin.amsl_m + origin.geoid_undulation_m)) > 1e-9) throw new Error("origin height relation is invalid");
}

function ecefForGeodetic(latitude: number, longitude: number, height: number): readonly [number, number, number] {
  const lat = latitude * RADIANS, lon = longitude * RADIANS;
  const n = A / Math.sqrt(1 - E2 * Math.sin(lat) ** 2);
  return [(n + height) * Math.cos(lat) * Math.cos(lon),
    (n + height) * Math.cos(lat) * Math.sin(lon),
    (n * (1 - E2) + height) * Math.sin(lat)];
}

export function validateGeographicBounds(bounds: GeographicBounds, sourceBounds?: GeographicBounds): void {
  for (const [key, value] of Object.entries(bounds)) finite(value, `bounds.${key}`);
  if (bounds.minlat <= -90 || bounds.maxlat >= 90 || bounds.minlat >= bounds.maxlat) throw new Error("invalid latitude bounds");
  if (bounds.minlon < -180 || bounds.maxlon > 180 || bounds.minlon >= bounds.maxlon) throw new Error("invalid longitude bounds or antimeridian crossing");
  if (sourceBounds && (bounds.minlat < sourceBounds.minlat || bounds.maxlat > sourceBounds.maxlat || bounds.minlon < sourceBounds.minlon || bounds.maxlon > sourceBounds.maxlon)) throw new Error("selection exceeds source bounds");
}

/** Same WGS84 ECEF and ENU rotation as aero_bench.world.frame_math.EnuTransform. */
export function geodeticToEnu(latitude_deg: number, longitude_deg: number, origin: SceneOrigin): { east: number; north: number; up: number } {
  validateOrigin(origin);
  finite(latitude_deg, "latitude_deg"); finite(longitude_deg, "longitude_deg");
  if (latitude_deg <= -90 || latitude_deg >= 90 || longitude_deg < -180 || longitude_deg > 180) throw new Error("invalid WGS84 coordinates");
  const target = ecefForGeodetic(latitude_deg, longitude_deg, origin.ellipsoid_height_m);
  const base = ecefForGeodetic(origin.latitude_deg, origin.longitude_deg, origin.ellipsoid_height_m);
  const dx = target[0] - base[0], dy = target[1] - base[1], dz = target[2] - base[2];
  const lat0 = origin.latitude_deg * RADIANS, lon0 = origin.longitude_deg * RADIANS;
  const slat = Math.sin(lat0), clat = Math.cos(lat0), slon = Math.sin(lon0), clon = Math.cos(lon0);
  return {
    east: -slon * dx + clon * dy,
    north: -slat * clon * dx - slat * slon * dy + clat * dz,
    up: clat * clon * dx + clat * slon * dy + slat * dz,
  };
}

/** Inverse of Python EnuTransform.enu_to_geodetic at ENU up=0. */
function enuToGeodeticAtZeroUp(east: number, north: number, origin: SceneOrigin): { latitude_deg: number; longitude_deg: number } {
  const base = ecefForGeodetic(origin.latitude_deg, origin.longitude_deg, origin.ellipsoid_height_m);
  const lat0 = origin.latitude_deg * RADIANS, lon0 = origin.longitude_deg * RADIANS;
  const slat = Math.sin(lat0), clat = Math.cos(lat0), slon = Math.sin(lon0), clon = Math.cos(lon0);
  const x = base[0] - slon * east - slat * clon * north;
  const y = base[1] + clon * east - slat * slon * north;
  const z = base[2] + clat * north;
  const distance = Math.hypot(x, y);
  let latitude = Math.atan2(z, distance * (1 - E2));
  let altitude = 0;
  let converged = false;
  for (let step = 0; step < 16; step += 1) {
    const radius = A / Math.sqrt(1 - E2 * Math.sin(latitude) ** 2);
    const nextLatitude = Math.atan2(z + E2 * radius * Math.sin(latitude), distance);
    const nextAltitude = distance / Math.cos(nextLatitude) - radius;
    if (nextLatitude === latitude && nextAltitude === altitude) { converged = true; break; }
    latitude = nextLatitude; altitude = nextAltitude;
  }
  if (!converged) throw new Error("ENU to WGS84 inverse did not converge");
  return { latitude_deg: latitude / RADIANS, longitude_deg: Math.atan2(y, x) / RADIANS };
}

/** Mirror the registry's v1 ENU boundary check before emitting a request. */
function assertEnuBoundsInsideSource(bounds: SceneSelection["bounds_enu_m"], source: LoadedRegionSource): void {
  const map = source.bounds, origin = source.origin;
  if (!(map.minlat <= origin.latitude_deg && origin.latitude_deg <= map.maxlat
    && map.minlon <= origin.longitude_deg && origin.longitude_deg <= map.maxlon)) throw new Error("地图原点超出 OSM 数据边界");
  for (let index = 0; index <= 128; index += 1) {
    const t = index / 128;
    const east = bounds.min_east_m + t * (bounds.max_east_m - bounds.min_east_m);
    const north = bounds.min_north_m + t * (bounds.max_north_m - bounds.min_north_m);
    for (const [sampleEast, sampleNorth] of [
      [east, bounds.min_north_m], [east, bounds.max_north_m],
      [bounds.min_east_m, north], [bounds.max_east_m, north],
    ]) {
      const point = enuToGeodeticAtZeroUp(sampleEast!, sampleNorth!, origin);
      if (!(map.minlat + SOURCE_EDGE_INSET_DEG <= point.latitude_deg && point.latitude_deg <= map.maxlat - SOURCE_EDGE_INSET_DEG
        && map.minlon + SOURCE_EDGE_INSET_DEG <= point.longitude_deg && point.longitude_deg <= map.maxlon - SOURCE_EDGE_INSET_DEG)) {
        throw new Error("ENU 外包越过 OSM 数据边界，请向地图内侧缩小选区");
      }
    }
  }
}

/** Exact extrema for the published Shanghai latitude domain at constant origin height.
 * East varies monotonically with longitude; north varies monotonically with latitude
 * and is smallest at the longitude closest to the origin. Sampling that meridian is
 * necessary: four corners alone miss the south-edge minimum by ~4.4 cm here.
 */
export function enuBoundsForGeographic(bounds: GeographicBounds, origin: SceneOrigin): SceneSelection["bounds_enu_m"] {
  validateGeographicBounds(bounds);
  validateOrigin(origin);
  // This proof uses positive northern latitudes and a small longitude offset.
  if (bounds.minlat < 0 || bounds.maxlat > 60 || Math.abs(bounds.minlon - origin.longitude_deg) >= 1 || Math.abs(bounds.maxlon - origin.longitude_deg) >= 1 || Math.abs(bounds.minlat - origin.latitude_deg) >= 1 || Math.abs(bounds.maxlat - origin.latitude_deg) >= 1) throw new Error("selection is outside the supported local ENU domain");
  const nearestLon = Math.max(bounds.minlon, Math.min(bounds.maxlon, origin.longitude_deg));
  const points = [
    [bounds.minlat, bounds.minlon], [bounds.minlat, bounds.maxlon],
    [bounds.maxlat, bounds.minlon], [bounds.maxlat, bounds.maxlon],
    [bounds.minlat, nearestLon], [bounds.maxlat, nearestLon],
  ] as const;
  const projected = points.map(([lat, lon]) => geodeticToEnu(lat, lon, origin));
  const east = projected.map(point => point.east), north = projected.map(point => point.north);
  const result = {
    min_east_m: Math.min(...east), max_east_m: Math.max(...east),
    min_north_m: Math.min(...north), max_north_m: Math.max(...north),
  };
  if (!(result.min_east_m < result.max_east_m && result.min_north_m < result.max_north_m)) throw new Error("degenerate ENU bounds");
  return result;
}

export function sceneSelectionForBounds(source: LoadedRegionSource, bounds: GeographicBounds): SceneSelection {
  validateGeographicBounds(bounds, source.bounds);
  const selection: SceneSelection = {
    schema_version: "aero-bench.scene-selection/v1",
    source_id: source.source_id,
    source_sha256: source.source_sha256,
    origin: { ...source.origin },
    bounds_enu_m: enuBoundsForGeographic(bounds, source.origin),
  };
  assertEnuBoundsInsideSource(selection.bounds_enu_m, source);
  return selection;
}

export async function loadRegionSource(source: RegionSource): Promise<LoadedRegionSource> {
  if (!/^[0-9a-f]{64}$/.test(source.source_sha256) || !source.source_id) throw new Error("invalid source identity");
  validateOrigin(source.origin);
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", source.bytes))).map(byte => byte.toString(16).padStart(2, "0")).join("");
  if (digest !== source.source_sha256) throw new Error(`OSM source SHA-256 mismatch: ${digest}`);
  const parsed = parseOsmJson(JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(source.bytes)));
  if (!parsed.bounds) throw new Error("published OSM source lacks declared bounds");
  validateGeographicBounds(parsed.bounds);
  if (Math.abs(parsed.bounds.minlat) > 60 || Math.abs(parsed.bounds.maxlat) > 60
    || parsed.bounds.maxlat - parsed.bounds.minlat > 0.02
    || parsed.bounds.maxlon - parsed.bounds.minlon > 0.025) throw new Error("OSM source exceeds the v1 local bounds policy");
  const nodes = new Map<number, OsmNode>();
  for (const element of parsed.elements) if (element.type === "node") nodes.set(element.id, element);
  const features: MapFeature[] = [];
  for (const element of parsed.elements) {
    if (element.type !== "way") continue;
    if (!element.tags?.building && !element.tags?.highway) continue;
    const points = element.nodes.map(id => {
      const node = nodes.get(id)!; // parseOsmJson verified all way references.
      return [node.lon, node.lat] as const;
    });
    if (element.tags?.building) features.push({ id: element.id, kind: "building", points });
    if (element.tags?.highway) features.push({ id: element.id, kind: "road", points });
  }
  return { source_id: source.source_id, source_sha256: digest, origin: { ...source.origin }, bounds: parsed.bounds, features };
}

/** Fetches published local bytes. Callers can instead supply bytes from the source API. */
export async function fetchPublishedOsmSource(url = SHANGHAI_SOURCE_URL, signal?: AbortSignal): Promise<RegionSource> {
  const resolved = new URL(url, window.location.href);
  if (resolved.origin !== window.location.origin) throw new Error("OSM source must be same-origin");
  const response = await fetch(resolved, { signal });
  if (!response.ok) throw new Error(`OSM source request failed: HTTP ${response.status}`);
  return { source_id: SHANGHAI_SOURCE_ID, source_sha256: SHANGHAI_SOURCE_SHA256,
    bytes: new Uint8Array(await response.arrayBuffer()), origin: SHANGHAI_ORIGIN };
}

interface Drag { readonly mode: "pan" | "select"; readonly startX: number; readonly startY: number; readonly startVisible: GeographicBounds; }

export class CityRegionSelector {
  readonly element: HTMLElement;
  readonly source: LoadedRegionSource;
  private readonly canvas: HTMLCanvasElement;
  private readonly ctx: CanvasRenderingContext2D;
  private readonly summary: HTMLElement;
  private readonly status: HTMLElement;
  private readonly sourceStatus: string;
  private readonly onSelection: RegionSelectorOptions["onSelection"];
  private readonly resizeObserver: ResizeObserver;
  private visible: GeographicBounds;
  private selection: GeographicBounds | null = null;
  private restoredSelection: SceneSelection | null = null;
  private draft: GeographicBounds | null = null;
  private mode: "pan" | "select" = "select";
  private drag: Drag | null = null;

  private constructor(container: HTMLElement, source: LoadedRegionSource, onSelection: RegionSelectorOptions["onSelection"]) {
    this.source = source;
    this.visible = { ...source.bounds };
    this.onSelection = onSelection;
    this.element = document.createElement("section"); this.element.className = "city-region-selector";
    const header = document.createElement("header"); header.className = "region-header";
    const title = document.createElement("div");
    const eyebrow = document.createElement("span"); eyebrow.className = "region-eyebrow"; eyebrow.textContent = "OSM · 本地地图";
    const heading = document.createElement("h2"); heading.textContent = "选择城市区域";
    title.append(eyebrow, heading);
    const toolbar = document.createElement("div"); toolbar.className = "region-toolbar";
    const button = (text: string, action: () => void): HTMLButtonElement => {
      const item = document.createElement("button"); item.type = "button"; item.textContent = text; item.addEventListener("click", action); toolbar.append(item); return item;
    };
    const selectButton = button("框选范围", () => setMode("select"));
    const panButton = button("拖移地图", () => setMode("pan"));
    const setMode = (mode: "pan" | "select") => {
      this.mode = mode; this.canvas.dataset.mode = mode;
      selectButton.setAttribute("aria-pressed", String(mode === "select"));
      panButton.setAttribute("aria-pressed", String(mode === "pan"));
    };
    button("+", () => this.zoom(0.75)); button("−", () => this.zoom(1 / 0.75));
    button("全图", () => { this.visible = { ...source.bounds }; this.draw(); });
    header.append(title, toolbar);
    const map = document.createElement("div"); map.className = "region-map";
    this.canvas = document.createElement("canvas"); this.canvas.setAttribute("aria-label", "OSM 道路和建筑地图");
    const context = this.canvas.getContext("2d"); if (!context) throw new Error("2D canvas is required for the OSM map");
    this.ctx = context;
    map.append(this.canvas);
    const guide = document.createElement("div"); guide.className = "region-map-guide"; guide.textContent = "拖动画出矩形区域 · 滚轮缩放"; map.append(guide);
    const footer = document.createElement("footer"); footer.className = "region-footer";
    const selectionDetails = document.createElement("div"); selectionDetails.className = "region-selection-details";
    this.summary = document.createElement("div"); this.summary.className = "region-summary";
    const boundaryNote = document.createElement("div"); boundaryNote.className = "region-boundary-note";
    boundaryNote.textContent = "贴边框选的 ENU 外包可能越过 OSM 边界；请向内侧留出余量。";
    selectionDetails.append(this.summary, boundaryNote);
    this.status = document.createElement("div"); this.status.className = "region-status";
    this.sourceStatus = `${source.features.filter(item => item.kind === "road").length.toLocaleString()} 条道路 · ${source.features.filter(item => item.kind === "building").length.toLocaleString()} 栋建筑 · SHA-256 已核验`;
    this.status.textContent = this.sourceStatus;
    footer.append(selectionDetails, this.status);
    this.element.append(header, map, footer); container.append(this.element);
    setMode("select");
    this.canvas.addEventListener("pointerdown", this.onPointerDown);
    this.canvas.addEventListener("pointermove", this.onPointerMove);
    this.canvas.addEventListener("pointerup", this.onPointerUp);
    this.canvas.addEventListener("pointercancel", this.onPointerCancel);
    this.canvas.addEventListener("wheel", this.onWheel, { passive: false });
    this.resizeObserver = new ResizeObserver(() => this.draw()); this.resizeObserver.observe(map);
    this.updateSummary(); this.draw();
  }

  static async mount(container: HTMLElement, options: RegionSelectorOptions): Promise<CityRegionSelector> {
    const source = await loadRegionSource(options.source);
    return new CityRegionSelector(container, source, options.onSelection);
  }

  getSelection(): GeographicBounds | null { return this.selection && { ...this.selection }; }

  /** Display the exact published ENU crop without creating a new geographic request. */
  showRestoredSelection(selection: SceneSelection): void {
    if (selection.source_id !== this.source.source_id || selection.source_sha256 !== this.source.source_sha256
      || (Object.keys(this.source.origin) as (keyof SceneOrigin)[])
        .some(key => selection.origin[key] !== this.source.origin[key])) {
      throw new Error("恢复选区与当前 OSM 来源或坐标原点不一致");
    }
    const bounds = selection.bounds_enu_m;
    if (![bounds.min_east_m, bounds.max_east_m, bounds.min_north_m, bounds.max_north_m].every(Number.isFinite)
      || bounds.min_east_m >= bounds.max_east_m || bounds.min_north_m >= bounds.max_north_m) {
      throw new Error("恢复选区的 ENU 外包无效");
    }
    assertEnuBoundsInsideSource(bounds, this.source);
    this.selection = null;
    this.restoredSelection = selection;
    this.draft = null;
    this.updateSummary(); this.draw();
  }

  setSelection(bounds: GeographicBounds): SceneSelection {
    const selection = sceneSelectionForBounds(this.source, bounds);
    this.selection = { ...bounds }; this.restoredSelection = null; this.draft = null;
    this.status.textContent = this.sourceStatus; this.status.classList.remove("region-error");
    this.updateSummary(); this.draw(); this.onSelection(selection, { ...bounds });
    return selection;
  }

  destroy(): void { this.resizeObserver.disconnect(); this.element.remove(); }

  private updateSummary(): void {
    if (this.restoredSelection) {
      const bounds = this.restoredSelection.bounds_enu_m;
      this.summary.textContent = `已恢复选区 · ENU 东向 ${bounds.min_east_m.toFixed(1)}–${bounds.max_east_m.toFixed(1)} m，北向 ${bounds.min_north_m.toFixed(1)}–${bounds.max_north_m.toFixed(1)} m · ${(bounds.max_east_m - bounds.min_east_m).toFixed(1)} × ${(bounds.max_north_m - bounds.min_north_m).toFixed(1)} m`;
      return;
    }
    if (!this.selection) {
      const bounds = this.source.bounds;
      this.summary.textContent = `尚未选择区域。地图范围：${bounds.minlat}°–${bounds.maxlat}° N，${bounds.minlon}°–${bounds.maxlon}° E。`;
      return;
    }
    const bounds = this.selection;
    const enu = enuBoundsForGeographic(bounds, this.source.origin);
    const width = enu.max_east_m - enu.min_east_m, height = enu.max_north_m - enu.min_north_m;
    this.summary.textContent = `纬度 ${bounds.minlat.toFixed(6)}°–${bounds.maxlat.toFixed(6)}° N · 经度 ${bounds.minlon.toFixed(6)}°–${bounds.maxlon.toFixed(6)}° E · ENU 外包 ${width.toFixed(1)} × ${height.toFixed(1)} m · 面积 ${(width * height / 1e6).toFixed(3)} km²`;
  }

  private pixel(event: PointerEvent | WheelEvent): { x: number; y: number } {
    const rect = this.canvas.getBoundingClientRect();
    return { x: Math.max(0, Math.min(rect.width, event.clientX - rect.left)), y: Math.max(0, Math.min(rect.height, event.clientY - rect.top)) };
  }
  private location(x: number, y: number, bounds = this.visible): { lat: number; lon: number } {
    return { lon: bounds.minlon + x / this.canvas.clientWidth * (bounds.maxlon - bounds.minlon),
      lat: bounds.maxlat - y / this.canvas.clientHeight * (bounds.maxlat - bounds.minlat) };
  }
  private geographicRect(x0: number, y0: number, x1: number, y1: number): GeographicBounds {
    const a = this.location(x0, y0), b = this.location(x1, y1);
    return { minlat: Math.max(this.source.bounds.minlat, Math.min(a.lat, b.lat)),
      maxlat: Math.min(this.source.bounds.maxlat, Math.max(a.lat, b.lat)),
      minlon: Math.max(this.source.bounds.minlon, Math.min(a.lon, b.lon)),
      maxlon: Math.min(this.source.bounds.maxlon, Math.max(a.lon, b.lon)) };
  }
  private onPointerDown = (event: PointerEvent): void => {
    if (event.button !== 0) return;
    const { x, y } = this.pixel(event);
    this.drag = { mode: this.mode, startX: x, startY: y, startVisible: { ...this.visible } };
    this.canvas.setPointerCapture(event.pointerId);
  };
  private onPointerMove = (event: PointerEvent): void => {
    if (!this.drag) return;
    const { x, y } = this.pixel(event), drag = this.drag;
    if (drag.mode === "select") this.draft = this.geographicRect(drag.startX, drag.startY, x, y);
    else {
      const width = drag.startVisible.maxlon - drag.startVisible.minlon, height = drag.startVisible.maxlat - drag.startVisible.minlat;
      const lonShift = -(x - drag.startX) / this.canvas.clientWidth * width;
      const latShift = (y - drag.startY) / this.canvas.clientHeight * height;
      const lon = Math.max(this.source.bounds.minlon - drag.startVisible.minlon, Math.min(this.source.bounds.maxlon - drag.startVisible.maxlon, lonShift));
      const lat = Math.max(this.source.bounds.minlat - drag.startVisible.minlat, Math.min(this.source.bounds.maxlat - drag.startVisible.maxlat, latShift));
      this.visible = { minlon: drag.startVisible.minlon + lon, maxlon: drag.startVisible.maxlon + lon,
        minlat: drag.startVisible.minlat + lat, maxlat: drag.startVisible.maxlat + lat };
    }
    this.draw();
  };
  private onPointerUp = (event: PointerEvent): void => {
    if (!this.drag) return;
    const { x, y } = this.pixel(event), drag = this.drag;
    this.drag = null;
    if (this.canvas.hasPointerCapture(event.pointerId)) this.canvas.releasePointerCapture(event.pointerId);
    if (drag.mode === "select" && Math.abs(x - drag.startX) >= 3 && Math.abs(y - drag.startY) >= 3) {
      try { this.setSelection(this.geographicRect(drag.startX, drag.startY, x, y)); }
      catch (error) { this.status.textContent = error instanceof Error ? error.message : String(error); this.status.classList.add("region-error"); }
    }
    this.draft = null; this.draw();
  };
  private onPointerCancel = (): void => { this.drag = null; this.draft = null; this.draw(); };
  private onWheel = (event: WheelEvent): void => {
    event.preventDefault(); const point = this.pixel(event);
    this.zoom(event.deltaY < 0 ? 0.83 : 1 / 0.83, point.x, point.y);
  };
  private zoom(factor: number, x = this.canvas.clientWidth / 2, y = this.canvas.clientHeight / 2): void {
    const location = this.location(x, y), source = this.source.bounds;
    const sourceWidth = source.maxlon - source.minlon, sourceHeight = source.maxlat - source.minlat;
    const width = Math.min(sourceWidth, Math.max(sourceWidth / 64, (this.visible.maxlon - this.visible.minlon) * factor));
    const height = Math.min(sourceHeight, Math.max(sourceHeight / 64, (this.visible.maxlat - this.visible.minlat) * factor));
    const minlon = Math.max(source.minlon, Math.min(source.maxlon - width, location.lon - x / this.canvas.clientWidth * width));
    const minlat = Math.max(source.minlat, Math.min(source.maxlat - height, location.lat - (1 - y / this.canvas.clientHeight) * height));
    this.visible = { minlon, maxlon: minlon + width, minlat, maxlat: minlat + height }; this.draw();
  }
  private draw(): void {
    const width = this.canvas.clientWidth, height = this.canvas.clientHeight;
    if (!width || !height) return;
    const ratio = window.devicePixelRatio || 1;
    this.canvas.width = Math.round(width * ratio); this.canvas.height = Math.round(height * ratio);
    const ctx = this.ctx; ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.fillStyle = "#edf2ee"; ctx.fillRect(0, 0, width, height);
    const bounds = this.visible;
    const x = (lon: number) => (lon - bounds.minlon) / (bounds.maxlon - bounds.minlon) * width;
    const y = (lat: number) => (bounds.maxlat - lat) / (bounds.maxlat - bounds.minlat) * height;
    // The file includes referenced nodes outside its declared extract bounds.
    // Canvas clipping keeps them available for ways crossing the extract edge.
    for (const kind of ["building", "road"] as const) {
      ctx.beginPath();
      for (const feature of this.source.features) {
        if (feature.kind !== kind) continue;
        let index = 0;
        for (const [lon, lat] of feature.points) {
          if (index++ === 0) ctx.moveTo(x(lon), y(lat)); else ctx.lineTo(x(lon), y(lat));
        }
        if (kind === "building") ctx.closePath();
      }
      if (kind === "building") { ctx.fillStyle = "#c8d3ca"; ctx.fill(); ctx.strokeStyle = "#9daa9e"; ctx.lineWidth = 0.6; ctx.stroke(); }
      else { ctx.strokeStyle = "#f8fbf7"; ctx.lineWidth = 3; ctx.stroke(); ctx.strokeStyle = "#81968a"; ctx.lineWidth = 0.9; ctx.stroke(); }
    }
    const rect = (geographic: GeographicBounds, color: string, fill: string) => {
      const left = x(geographic.minlon), top = y(geographic.maxlat);
      const right = x(geographic.maxlon), bottom = y(geographic.minlat);
      ctx.fillStyle = fill; ctx.fillRect(left, top, right - left, bottom - top);
      ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.setLineDash([8, 4]); ctx.strokeRect(left, top, right - left, bottom - top); ctx.setLineDash([]);
    };
    if (this.selection) rect(this.selection, "#047b60", "#2cc29225");
    if (this.restoredSelection) {
      const enu = this.restoredSelection.bounds_enu_m;
      const corners = [
        [enu.min_east_m, enu.min_north_m], [enu.max_east_m, enu.min_north_m],
        [enu.max_east_m, enu.max_north_m], [enu.min_east_m, enu.max_north_m],
      ] as const;
      ctx.beginPath();
      corners.forEach(([east, north], index) => {
        const point = enuToGeodeticAtZeroUp(east, north, this.source.origin);
        if (index === 0) ctx.moveTo(x(point.longitude_deg), y(point.latitude_deg));
        else ctx.lineTo(x(point.longitude_deg), y(point.latitude_deg));
      });
      ctx.closePath(); ctx.fillStyle = "#2cc29225"; ctx.fill();
      ctx.strokeStyle = "#047b60"; ctx.lineWidth = 2; ctx.setLineDash([8, 4]); ctx.stroke(); ctx.setLineDash([]);
    }
    if (this.draft) rect(this.draft, "#ef8e2f", "#ee9b3c36");
    ctx.fillStyle = "#456458"; ctx.font = "11px system-ui, sans-serif";
    ctx.fillText(`${bounds.maxlat.toFixed(5)}° N`, 10, 17);
    ctx.fillText(`${bounds.minlon.toFixed(5)}° E`, 10, height - 11);
    const rightLabel = `${bounds.maxlon.toFixed(5)}° E`; ctx.fillText(rightLabel, width - ctx.measureText(rightLabel).width - 10, height - 11);
  }
}
