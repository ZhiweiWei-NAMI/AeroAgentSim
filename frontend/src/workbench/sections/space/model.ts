import type { ValidationIssue, WorkbenchApi } from '../../api';
import type { Workspace } from '../../../studio/api';
import type { EnuOrigin } from '../../geo';
import type { RegionMapBasemap, RegionMapGeometry } from './RegionMap';

/** Shapes of the server contracts this section consumes (see brief: C1, C2). */
export interface ExtractInfo {
  id: string; label?: string; label_zh?: string; bounds?: number[]; attribution?: string;
  buildings?: number; roads?: number; basemap?: RegionMapBasemap | null;
}
export interface MapCatalog { extracts: ExtractInfo[]; basemap?: RegionMapBasemap | null }
export interface GeometryFeature { properties?: Record<string, unknown>; geometry?: { type?: string; coordinates?: unknown } | null }
export interface RegionRoad { id: string; points: number[][]; tags?: Record<string, string> }
export interface RegionPreview {
  preview: {
    extract: string; bounds: number[]; origin: { lat: number; lon: number; alt: number };
    previous_origin: { lat: number; lon: number; alt: number } | null; origin_shift_m: number | null;
    buildings: number; roads: number; attribution: string; height_source: string;
    diagnostics: string[]; affected_entities: string[]; network_invalidated: boolean; city_conflict: boolean;
    geometry?: { buildings: { features: GeometryFeature[] }; roads: RegionRoad[]; truncated: boolean };
  };
}
/** Network result as written to draft.network by the backend. */
export interface NetworkResult { status?: string; edge_count?: number; lane_count?: number; command?: string; location?: Record<string, unknown> | null }

export async function fetchMapCatalog(api: WorkbenchApi, signal?: AbortSignal): Promise<MapCatalog> {
  const catalog = await api.request<MapCatalog>('/v1/studio/catalog', { signal });
  if (!Array.isArray(catalog?.extracts)) throw new TypeError('The map catalog has no extract list.');
  return catalog;
}

/** An extract override (including explicit null) takes precedence over the shared C9 tiles. */
export function catalogBasemap(catalog: MapCatalog | undefined, extract: string): RegionMapBasemap | null {
  const row = catalog?.extracts.find(item => item.id === extract);
  const tile = row?.basemap !== undefined ? row.basemap : catalog?.basemap;
  return tile ?? null;
}

export function extractLabel(item: ExtractInfo, L: (en: string, zh: string) => string): string {
  const name = L(item.label ?? item.id, item.label_zh ?? item.label ?? item.id);
  const counts: string[] = [];
  if (Number.isInteger(item.buildings) && item.buildings! >= 0) counts.push(L(`${item.buildings} buildings`, `${item.buildings} 栋建筑`));
  if (Number.isInteger(item.roads) && item.roads! >= 0) counts.push(L(`${item.roads} roads`, `${item.roads} 条道路`));
  return counts.length ? `${name} — ${counts.join(' · ')}` : name;
}

export function regionEndpoint(id: string) { return `/v1/studio/workspaces/${encodeURIComponent(id)}/region`; }
export function networkEndpoint(id: string) { return `/v1/studio/workspaces/${encodeURIComponent(id)}/network`; }

/** Server answer for C1 with dry_run=true; unavailable endpoints reject. */
export async function previewRegion(api: WorkbenchApi, id: string, body: { extract: string; bounds: number[]; alt: number; level_height_m: number }): Promise<RegionPreview['preview']> {
  const answer = await api.request<RegionPreview | Workspace>(regionEndpoint(id), { body: { ...body, dry_run: true } });
  if (answer && typeof answer === 'object' && 'preview' in answer && answer.preview) return (answer as RegionPreview).preview;
  throw new TypeError('preview: server returned no preview object');
}

export function applyRegion(api: WorkbenchApi, id: string, body: { extract: string; bounds: number[]; alt: number; level_height_m: number }) {
  return api.request<Workspace>(regionEndpoint(id), { body });
}
export function generateNetwork(api: WorkbenchApi, id: string) {
  return api.request<NetworkResult>(networkEndpoint(id), { body: {} });
}

/** distance in metres between two ENU origins via local equirectangular approximation — a review figure, not a survey. */
export function originShiftM(a: { lat: number; lon: number }, b: { lat: number; lon: number }): number {
  const latMid = (a.lat + b.lat) / 2 * Math.PI / 180;
  const dLat = (b.lat - a.lat) * 111320, dLon = (b.lon - a.lon) * 111320 * Math.cos(latMid);
  return Math.hypot(dLat, dLon);
}

/** Filled once per edited field; an empty string means "not entered yet", never a substituted value. */
export interface RegionDraft { west: string; south: string; east: string; north: string; alt: string; level: string; extract: string }
export type RegionErrors = Partial<Record<'west' | 'south' | 'east' | 'north' | 'alt' | 'level', string>>;

const num = (raw: string): number | undefined => /^-?(?:0|[1-9]\d*)(?:\.\d+)?$/.test(raw.trim()) && Number.isFinite(Number(raw)) ? Number(raw) : undefined;

export function validateRegion(draft: RegionDraft, bounds?: number[]): { errors: RegionErrors; body?: { extract: string; bounds: number[]; alt: number; level_height_m: number } } {
  const errors: RegionErrors = {};
  const w = num(draft.west), s = num(draft.south), e = num(draft.east), n = num(draft.north), alt = num(draft.alt), level = num(draft.level);
  if (w === undefined || w < -180 || w > 180) errors.west = 'Longitude between -180 and 180 degrees.';
  if (e === undefined || e < -180 || e > 180) errors.east = 'Longitude between -180 and 180 degrees.';
  if (s === undefined || s < -90 || s > 90) errors.south = 'Latitude between -90 and 90 degrees.';
  if (n === undefined || n < -90 || n > 90) errors.north = 'Latitude between -90 and 90 degrees.';
  if (w !== undefined && e !== undefined && w >= e) errors.west = 'West must be smaller than east (WGS84 degrees, no antimeridian wrap).';
  if (s !== undefined && n !== undefined && s >= n) errors.south = 'South must be smaller than north.';
  if (alt === undefined) errors.alt = 'Ground altitude in metres is required.';
  if (level === undefined || level <= 0) errors.level = 'Level height must be a positive number of metres.';
  if (bounds && bounds.length === 4 && w !== undefined && s !== undefined && e !== undefined && n !== undefined
    && (w < bounds[0] - 1e-9 || s < bounds[1] - 1e-9 || e > bounds[2] + 1e-9 || n > bounds[3] + 1e-9)) errors.west = 'Bounds must stay inside the extract coverage.';
  if (Object.keys(errors).length || !draft.extract) return { errors };
  return { errors, body: { extract: draft.extract, bounds: [w!, s!, e!, n!], alt: alt!, level_height_m: level! } };
}

/** A compiled region as GET workspace returns it, plus its derived scene summary. */
export interface CompiledRegion {
  region: { extract: string; bounds: number[]; alt: number; level_height_m: number };
  scene?: { origin?: EnuOrigin; ground?: { width_m: number; depth_m: number }; diagnostics?: string[]; attribution?: string; roads?: RegionRoad[]; geojson?: { features?: GeometryFeature[] } };
  network?: NetworkResult;
}
export function readRegion(workspace: { region?: unknown; scene?: unknown; network?: unknown } | undefined): CompiledRegion | undefined {
  if (!workspace || typeof workspace.region !== 'object' || workspace.region === null) return undefined;
  const region = workspace.region as CompiledRegion['region'];
  return { region, scene: workspace.scene as CompiledRegion['scene'], network: workspace.network as CompiledRegion['network'] | undefined };
}

/** Hydrate authored settings from the applied region; an absent value stays visibly empty. */
export function regionDraft(region?: CompiledRegion['region']): RegionDraft {
  const text = (value: unknown) => typeof value === 'number' && Number.isFinite(value) ? String(value) : '';
  return region ? {
    extract: region.extract, west: text(region.bounds?.[0]), south: text(region.bounds?.[1]),
    east: text(region.bounds?.[2]), north: text(region.bounds?.[3]), alt: text(region.alt), level: text(region.level_height_m),
  } : { extract: '', west: '', south: '', east: '', north: '', alt: '0', level: '3.5' };
}

/** Saved building GeoJSON is WGS84; saved road records already contain ENU metres. */
export function sceneMapGeometry(scene: CompiledRegion['scene']): RegionMapGeometry | null {
  return scene?.origin ? { origin: scene.origin, buildings: scene.geojson?.features, buildingFrame: 'wgs84', roads: scene.roads } : null;
}

/** C10 footprints and roads both use metres relative to this preview's origin. */
export function previewMapGeometry(preview: RegionPreview['preview']): RegionMapGeometry {
  return { origin: preview.origin, buildings: preview.geometry?.buildings.features, buildingFrame: 'enu',
    roads: preview.geometry?.roads, truncated: preview.geometry?.truncated };
}

/** Sourced vs estimated vs missing-height counts, straight from the compiled GeoJSON properties. */
export function heightSummary(scene: CompiledRegion['scene']): { sourced: number; estimated: number; missing: number } | undefined {
  const features = (scene?.geojson?.features ?? []).filter(feature => feature.geometry?.type === 'Polygon' || feature.geometry?.type === 'MultiPolygon');
  if (!features.length) return undefined;
  let sourced = 0, estimated = 0, missing = 0;
  for (const feature of features) {
    const props = feature.properties ?? {};
    if (typeof props.height_source === 'string' && props.height_source.includes('osm.height')) sourced++;
    else if (typeof props.height_source === 'string') estimated++;
    else missing++;
  }
  return { sourced, estimated, missing };
}

export const cityConflictIssue = (issues: ValidationIssue[]) => issues.find(issue => issue.section === 'space' && issue.path === '/city');
export const hasCity = (draft: Record<string, unknown>) => typeof draft.city === 'string' && draft.city !== 'none' && draft.city !== '';
