/** Structures shared by the Tasks/Components forms; they mirror what the assistant compiler writes
 *  (src/aeroagentsim/assistant/{operations,compiler}.py) so manual edits and assistant edits stay equivalent. */
export const SECOND = 1e9;

export const TASK_KINDS = ['parcel', 'food', 'medical', 'inspection', 'survey', 'spraying', 'passenger', 'patrol', 'incident_response'] as const;
export type TaskKind = (typeof TASK_KINDS)[number];

export type Draft = Record<string, any>;

/** ns ↔ s conversions. Authored engine timing is integer nanoseconds; forms show seconds. */
export const sToNs = (seconds: number) => Math.round(seconds * SECOND);
export const nsToS = (ns: unknown): number | undefined => {
  const value = typeof ns === 'number' ? ns : (ns as any)?.$number ?? (ns as any)?.$integer;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed / SECOND : undefined;
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
    .filter(entity => entity?.facts?.lowalt_site_position !== undefined || entity?.facts?.['lowalt.site_position'] !== undefined)
    .map(entity => String(entity.id));
}

/** Vehicle ids cooperation may reference (UAV instances, not facilities). */
export function vehicleIds(draft: Draft): string[] {
  return ((Array.isArray(draft.entities) ? draft.entities : []) as Draft[])
    .filter(entity => /UAV|Aircraft|Vehicle/i.test(String(entity?.type ?? '')) || entity?.facts?.['lowalt.soc'] !== undefined)
    .map(entity => String(entity.id));
}

export interface GustInput { start_s: number; end_s: number; speed_mps: number; direction_deg: number }

/** Same writes as assistant add_gust: mode becomes gust, disjoint [start_ns,end_ns) value vector. */
export function addGust(profile: Draft, gust: GustInput): Draft {
  const value = windVector(gust.speed_mps, gust.direction_deg, Array.isArray(profile?.value) ? profile.value : [0, 0, 0]);
  const entry = { start_ns: sToNs(gust.start_s), end_ns: sToNs(gust.end_s), value };
  return { ...profile, mode: 'gust', gusts: [...(Array.isArray(profile?.gusts) ? profile.gusts : []), entry] };
}

export function removeGust(profile: Draft, index: number): Draft {
  return { ...profile, gusts: (profile.gusts as Draft[]).filter((_, i) => i !== index) };
}

/** Wind is an ENU vector; the form exposes east speed + direction in degrees. */
export function windVector(speed_mps: number, direction_deg: number, previous: unknown[]): number[] {
  const rad = (direction_deg * Math.PI) / 180, base = Array.isArray(previous) ? previous : [0, 0, 0];
  return [Number((speed_mps * Math.cos(rad)).toFixed(6)), Number((speed_mps * Math.sin(rad)).toFixed(6)), Number(base[2]) || 0];
}
export function windSpeed(value: unknown): number | undefined {
  const vec = Array.isArray(value) ? value : [];
  const x = Number(vec[0]), y = Number(vec[1]);
  return Number.isFinite(x) && Number.isFinite(y) ? Number(Math.hypot(x, y).toFixed(6)) : undefined;
}
export function windDirection(value: unknown): number | undefined {
  const vec = Array.isArray(value) ? value : [];
  const x = Number(vec[0]), y = Number(vec[1]);
  if (!Number.isFinite(x) || !Number.isFinite(y) || (x === 0 && y === 0)) return undefined;
  return Number((((Math.atan2(y, x) * 180) / Math.PI + 360) % 360).toFixed(1));
}

/** One new workload group, matching compiler _add_orders output for the same inputs. */
export function newOrderStream(id: string, kind: TaskKind, count: number, until_ns: number): Draft {
  const arrivals = { mode: 'poisson', rate_per_s: 0.05, start_ns: 0, end_ns: Math.max(1, Math.round(until_ns)) };
  return {
    id, kind, tasks: Array.from({ length: count }, (_, i) => `${id}-${i + 1}`),
    origins: [], destinations: [], priority: 1, payload_kg: 1, arrivals,
    ...(kind === 'food' ? { delivery_window_s: 240 } : {}),
  };
}

/** Compiler cooperation skeleton (compiler._cooperation). */
export function newCooperation(): Draft {
  return {
    handoff_points: [], incidents: [],
    watchers: { vehicles: [], notice_radius_m: 150.0 },
    responder: { mode: 'prescribed', order: ['respond_now', 'request_handoff', 'finish_then_respond', 'report_only'],
      rules: { protected_task_kinds: ['medical'], require_replacement: true, min_battery_pct: 30.0, max_response_distance_m: 600.0 } },
    helpers: { vehicles: [], ask_radius_m: 500.0, mode: 'prescribed', rules: { accept_when: 'idle', min_battery_pct: 25.0 }, selection: 'nearest' },
    photograph: { dwell_s: 5.0, altitude_m: 35.0 },
  };
}

/** Scheduled release authored in seconds; assistant stores times_ns. */
export function scheduledArrivals(times_s: number[]): Draft {
  return { mode: 'scheduled', times_ns: times_s.map(sToNs) };
}
