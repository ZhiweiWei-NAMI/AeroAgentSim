import type { ValidationIssue, WorkbenchApi } from '../../api';
import type { Workspace } from '../../../studio/api';

/** Shapes of the server contracts this section consumes (see brief: C1, C2). */
export interface ExtractInfo { id: string; label?: string; bounds?: number[]; attribution?: string }
export interface RegionPreview {
  preview: {
    extract: string; bounds: number[]; origin: { lat: number; lon: number; alt: number };
    previous_origin: { lat: number; lon: number; alt: number } | null; origin_shift_m: number | null;
    buildings: number; roads: number; attribution: string; height_source: string;
    diagnostics: string[]; affected_entities: string[]; network_invalidated: boolean; city_conflict: boolean;
  };
}
/** Network result as written to draft.network by the backend. */
export interface NetworkResult { status?: string; edge_count?: number; lane_count?: number; command?: string; location?: Record<string, unknown> | null }

export async function fetchExtracts(api: WorkbenchApi, signal?: AbortSignal): Promise<ExtractInfo[]> {
  const catalog = await api.request<{ extracts?: ExtractInfo[] }>('/v1/studio/catalog', { signal });
  return Array.isArray(catalog.extracts) ? catalog.extracts : [];
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
  scene?: { origin?: { lat: number; lon: number; alt: number }; ground?: { width_m: number; depth_m: number }; diagnostics?: string[]; attribution?: string; roads?: unknown[]; geojson?: { features?: Array<{ properties?: Record<string, unknown> }> } };
  network?: NetworkResult;
}
export function readRegion(workspace: { region?: unknown; scene?: unknown; network?: unknown } | undefined): CompiledRegion | undefined {
  if (!workspace || typeof workspace.region !== 'object' || workspace.region === null) return undefined;
  const region = workspace.region as CompiledRegion['region'];
  return { region, scene: workspace.scene as CompiledRegion['scene'], network: workspace.network as CompiledRegion['network'] | undefined };
}

/** Sourced vs estimated vs missing-height counts, straight from the compiled GeoJSON properties. */
export function heightSummary(scene: CompiledRegion['scene']): { sourced: number; estimated: number; missing: number } | undefined {
  const features = scene?.geojson?.features ?? [];
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
