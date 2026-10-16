/** Structures shared by the Tasks/Components forms; they mirror what the assistant compiler writes
 *  (src/aeroagentsim/assistant/{operations,compiler}.py) so manual edits and assistant edits stay equivalent. */
import { windFromDeg as fromDegrees } from '../../../viewport/conditions';
export const SECOND = 1e9;

export const TASK_KINDS = ['parcel', 'food', 'medical', 'inspection', 'survey', 'spraying', 'passenger', 'patrol', 'incident_response'] as const;
export type TaskKind = (typeof TASK_KINDS)[number];

export type Draft = Record<string, any>;

/** ns ↔ s conversions. Authored engine timing is integer nanoseconds; forms show seconds. */
export const sToNs = (seconds: number) => Math.round(seconds * SECOND);
export function scalarNumber(value: unknown): number | undefined {
  if (typeof value === 'number') return Number.isFinite(value) ? value : undefined;
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined;
  const token = (value as Draft).$number ?? (value as Draft).$integer;
  if (typeof token !== 'string' || !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(token)) return undefined;
  const parsed = Number(token);
  return Number.isFinite(parsed) ? parsed : undefined;
}
export const nsToS = (ns: unknown): number | undefined => {
  const value = scalarNumber(ns);
  return value === undefined ? undefined : value / SECOND;
};

/** Find an engine by the plugin that actually provides a capability, not by its authored key. */
export function engineOf(draft: Draft, plugin: string): { name: string; engine: Draft } | undefined {
  const engines = draft.engines as Record<string, Draft> | undefined;
  for (const [name, engine] of Object.entries(engines ?? {})) if (engine?.plugin === plugin) return { name, engine };
  return undefined;
}

export function taskConfig(draft: Draft): Draft | undefined {
  return (engineOf(draft, 'lowalt_tasks') ?? (draft.engines?.tasks ? { name: 'tasks', engine: draft.engines.tasks } : undefined))?.engine?.config;
}

/** Facility ids the order forms may pick endpoints from. */
export function siteIds(draft: Draft): string[] {
  return ((Array.isArray(draft.entities) ? draft.entities : []) as Draft[])
    .filter(entity => entity?.facts?.['lowalt.site_position'] !== undefined)
    .map(entity => entity.id).filter((id): id is string => typeof id === 'string' && id.trim() !== '');
}

/** Vehicle ids cooperation may reference (UAV instances, not facilities). */
export function vehicleIds(draft: Draft): string[] {
  const explicit = ((Array.isArray(draft.entities) ? draft.entities : []) as Draft[])
    .filter(entity => /UAV|Aircraft|Vehicle/i.test(String(entity?.type ?? '')) || entity?.facts?.['lowalt.soc'] !== undefined)
    .map(entity => entity.id).filter((id): id is string => typeof id === 'string' && id.trim() !== '');
  const generated = Object.values((draft.generators ?? {}) as Record<string, Draft>).flatMap(generator => {
    const count = scalarNumber(generator?.count);
    return /UAV|Aircraft|Vehicle/i.test(String(generator?.type ?? '')) && typeof generator.prefix === 'string'
      && count !== undefined && Number.isSafeInteger(count) && count > 0
      ? Array.from({ length: count }, (_, index) => `${generator.prefix}${index + 1}`) : [];
  });
  return [...new Set([...explicit, ...generated])];
}

/** Match compiler._group_id on the actual generator, including id-less template workloads. */
export function orderStreamId(stream: Draft): string | undefined {
  if ('id' in stream) return typeof stream.id === 'string' && stream.id.trim() !== '' ? stream.id : undefined;
  if (!Array.isArray(stream.tasks) || stream.tasks.some(task => typeof task !== 'string' || !task.trim())) return undefined;
  if (!stream.tasks.length) return typeof stream.kind === 'string' && stream.kind.trim() ? stream.kind : undefined;
  const prefix = stream.tasks[0].replace(/[-.]\d+$/, '');
  return stream.tasks.length > 1 && stream.tasks.every(task => task.replace(/[-.]\d+$/, '') === prefix)
    ? prefix : stream.tasks[0];
}

export function handoffPointIds(draft: Draft, cooperation?: Draft): string[] {
  const places = ((Array.isArray(draft.entities) ? draft.entities : []) as Draft[])
    .filter(entity => entity?.type === 'lowalt:HandoffPoint').map(entity => entity.id);
  const assigned = Array.isArray(cooperation?.handoff_points) ? cooperation.handoff_points.map((point: unknown) => typeof point === 'string' ? point : (point as Draft)?.id) : [];
  return [...new Set([...places, ...assigned].filter((id): id is string => typeof id === 'string' && id.trim() !== ''))];
}

export function windSpeed(value: unknown): number | undefined {
  const vec = Array.isArray(value) ? value : [];
  const x = scalarNumber(vec[0]), y = scalarNumber(vec[1]);
  return x !== undefined && y !== undefined ? Number(Math.hypot(x, y).toFixed(6)) : undefined;
}
export function windDirection(value: unknown): number | undefined {
  const vec = Array.isArray(value) ? value : [];
  const x = scalarNumber(vec[0]), y = scalarNumber(vec[1]);
  if (x === undefined || y === undefined || (x === 0 && y === 0)) return undefined;
  return Number((((Math.atan2(y, x) * 180) / Math.PI + 360) % 360).toFixed(1));
}

/** Meteorological FROM-direction (deg clockwise from north), matching set_wind.from_deg (spec §7). */
export function windFromDeg(value: unknown): number | undefined {
  const vec = Array.isArray(value) ? value : [];
  const x = scalarNumber(vec[0]), y = scalarNumber(vec[1]);
  if (x === undefined || y === undefined) return undefined;
  if (x === 0 && y === 0) return 270;
  return Number(fromDegrees(x, y).toFixed(1));
}

export const COMPASS_POINTS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'] as const;
export const pointDeg = (point: string): number => (COMPASS_POINTS.indexOf(point as typeof COMPASS_POINTS[number]) * 45 + 360) % 360;
export const pointOfDeg = (deg: number): string => COMPASS_POINTS[Math.round(((deg % 360) + 360) % 360 / 45) % 8];

/** Weather profile on the lowalt:Environment entity (spec §7): kg m⁻² s⁻¹ ×3600 → mm/h, 50000 m = clear. */
export function weatherOf(environment: Draft): { precipitationMmH: number; visibilityM: number | null; light: 'day' | 'dusk' } {
  const profiles = (environment?.config?.profiles ?? {}) as Record<string, Draft>;
  for (const profile of Object.values(profiles)) {
    const precipitation = scalarNumber(profile?.['lowalt.precipitation']?.value);
    const visibility = scalarNumber(profile?.['lowalt.visibility']?.value);
    const light = profile?.['lowalt.light']?.value;
    if (precipitation !== undefined || visibility !== undefined || light !== undefined)
      return { precipitationMmH: precipitation === undefined ? 0 : Math.round(precipitation * 3600 * 100) / 100,
        visibilityM: visibility === undefined || visibility >= 50000 ? null : visibility, light: light === 'dusk' ? 'dusk' : 'day' };
  }
  return { precipitationMmH: 0, visibilityM: null, light: 'day' };
}

/** Clock text mm:ss for HUD chips and incident rows; seconds fall back to one decimal. */
export function clockS(seconds: number | undefined): string {
  if (seconds === undefined || !Number.isFinite(seconds)) return '—';
  const total = Math.max(0, Math.round(seconds)), m = Math.floor(total / 60), s = total % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}
