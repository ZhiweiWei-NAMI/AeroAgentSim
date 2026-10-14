import type { RunHeader } from '../contracts/viewer-feed';

/**
 * Conditions derived at one cut: pure input to the atmosphere layer, the HUD and
 * the draft preview. Weather fields ride the existing `lowalt:Environment`
 * entity (id usually `weather`) as journaled profiles; absent fields mean clear
 * daylight with no rain.
 */

/** Visual only — shown in the 3D view and UAV captures; flight, sensing and task outcomes don't use it. */
export interface Conditions {
  wind: { east: number; north: number; speed: number; fromDeg: number; gust: boolean; gustEndS?: number } | null;
  precipitationMmH: number;
  visibilityM: number | null;
  light: 'day' | 'dusk';
  incidents: Array<{ id: string; kind: 'fire' | 'person_fall'; label: string; position: [number, number, number]; radiusM: number;
    phase: 'pending' | 'active' | 'ended'; sinceS: number; response?: string; startS?: number }>;
}

type Cut = { entities: Map<string, { key: { id: string; generation: number | string }; typeId: string; fields: Map<string, { value: unknown }> }>; header?: RunHeader };

const scalar = (value: unknown): number | undefined => typeof value === 'number' && Number.isFinite(value) ? value : undefined;
const text = (value: unknown): string | undefined => typeof value === 'string' && value ? value : undefined;
const field = (entity: { fields: Map<string, { value: unknown }> }, id: string) => entity.fields.get(id)?.value;
const integer = (value: unknown): bigint | undefined => {
  if (typeof value === 'number' && Number.isSafeInteger(value)) return BigInt(value);
  const token = typeof value === 'string' ? value : value && typeof value === 'object' ? (value as { $integer?: unknown }).$integer : undefined;
  return typeof token === 'string' && /^-?\d+$/.test(token) ? BigInt(token) : undefined;
};
const windVectors = (value: unknown): Array<[number, number]> => {
  if (!Array.isArray(value)) return [];
  const rows = Array.isArray(value[0]) ? value : [value];
  return rows.flatMap(row => {
    const east = scalar(row[0]), north = scalar(row[1]);
    return east !== undefined && north !== undefined ? [[east, north] as [number, number]] : [];
  });
};
const windOf = (values: Array<[number, number]>, gust: boolean): Conditions['wind'] => {
  if (!values.length) return null;
  const east = values.reduce((sum, [e]) => sum + e, 0) / values.length;
  const north = values.reduce((sum, [, n]) => sum + n, 0) / values.length;
  return { east, north, speed: Math.hypot(east, north), fromDeg: windFromDeg(east, north), gust };
};

/** Meteorological from-direction of an ENU vector; zero vector defaults to a west wind (from 270). */
export function windFromDeg(east: number, north: number): number {
  if (east === 0 && north === 0) return 270;
  return ((Math.atan2(-east, -north) * 180) / Math.PI + 360) % 360;
}

function windAt(cut: Cut, ns?: string): Conditions['wind'] {
  const values: Array<[number, number]> = [];
  for (const entity of cut.entities.values()) {
    values.push(...windVectors(field(entity, 'lowalt.wind') ?? field(entity, 'oo:digital_twin.wind.horizontalVelocity')));
  }
  if (!values.length) return null;
  // All configured wind profiles receive the same op; average keeps one display vector honest.
  const gustEndS = ns !== undefined ? gustEnd(cut.header, ns) : undefined;
  const wind = windOf(values, gustEndS !== undefined);
  if (wind && gustEndS !== undefined) wind.gustEndS = gustEndS;
  return wind;
}

/** Gust is true only when the run header's authored gust intervals contain the cut — never guessed. */
function gustEnd(header: RunHeader | undefined, ns: string): number | undefined {
  if (!header?.engines) return undefined;
  const at = BigInt(ns);
  let latest: number | undefined;
  for (const engine of header.engines) {
    if (engine.plugin !== 'environment') continue;
    for (const [start, end] of engine.gusts ?? []) if (BigInt(start) <= at && at < BigInt(end)) latest = Math.max(latest ?? end, end);
  }
  return latest === undefined ? undefined : latest / 1e9;
}

export function conditionsAt(entities: Cut['entities'] | Cut, ns: string, header?: RunHeader): Conditions {
  const cut: Cut = entities instanceof Map ? { entities, header } : { ...entities, header: header ?? entities.header };
  let precipitationMmH = 0, visibilityM: number | null = null, light: 'day' | 'dusk' = 'day';
  const incidents: Conditions['incidents'] = [];
  for (const entity of cut.entities.values()) {
    const rain = scalar(field(entity, 'lowalt.precipitation'));
    if (rain !== undefined) precipitationMmH = rain * 3600;
    const visibility = scalar(field(entity, 'lowalt.visibility'));
    if (visibility !== undefined) visibilityM = visibility >= 50000 ? null : visibility;
    const value = text(field(entity, 'lowalt.light'));
    if (value === 'dusk' || value === 'day') light = value;
  }
  const at = BigInt(ns);
  for (const entity of cut.entities.values()) {
    if (entity.typeId !== 'lowalt:Incident') continue;
    const position = field(entity, 'lowalt.incident_position');
    if (!Array.isArray(position) || position.length !== 3 || !position.every(Number.isFinite)) continue;
    const kind = text(field(entity, 'lowalt.incident_kind')) === 'fire' ? 'fire' as const : 'person_fall' as const;
    const startNs = integer(field(entity, 'lowalt.incident_time_ns'));
    const endNs = integer(field(entity, 'lowalt.incident_end_ns'));
    if (startNs === undefined) continue;
    const active = field(entity, 'lowalt.incident_active') === true;
    const end = endNs !== undefined && endNs >= 0n ? endNs : undefined;
    const phase: Conditions['incidents'][number]['phase'] = !active && at < startNs ? 'pending' : active ? 'active' : 'ended';
    const sinceNs = active ? startNs : end ?? startNs;
    const sinceS = Math.max(0, Number(at - sinceNs) / 1e9);
    const label = header?.entities?.find(row => row.id === entity.key.id && row.type === entity.typeId)?.label ?? entity.key.id;
    const response = text(field(entity, 'lowalt.incident_responder')) || undefined;
    incidents.push({ id: entity.key.id, kind, label, position: position as [number, number, number], radiusM: scalar(field(entity, 'lowalt.incident_radius_m')) ?? (kind === 'fire' ? 30 : 6), phase, sinceS: Math.max(0, sinceS), response, startS: Number(startNs) / 1e9 });
  }
  return { wind: windAt(cut, ns), precipitationMmH, visibilityM, light, incidents };
}

const sToNs = (s: number) => Math.round(s * 1e9);

function draftIncidents(scenario: Record<string, any>): any[] {
  const tasks = (Object.values(scenario?.engines ?? {}) as any[]).find(engine => engine?.plugin === 'lowalt_tasks');
  const incidents = tasks?.config?.cooperation?.incidents ?? scenario?.cooperation?.incidents;
  return Array.isArray(incidents) ? incidents : [];
}

/** Draft preview moment: the first incident's start + 20 s, or 0. */
export function previewMoment(scenario: Record<string, any>): number {
  const incidents = draftIncidents(scenario);
  return Array.isArray(incidents) && incidents.length ? (typeof incidents[0]?.at_s === 'number' ? incidents[0].at_s : 0) + 20 : 0;
}

const compass: Array<[string, number]> = [['N', 0], ['NE', 45], ['E', 90], ['SE', 135], ['S', 180], ['SW', 225], ['W', 270], ['NW', 315]];
/** `conditionsFromDraft` mirrors the compiler: constant profiles on the lowalt:Environment weather entity. */
export function conditionsFromDraft(scenario: Record<string, any>, previewS: number): Conditions {
  const at = sToNs(previewS);
  let gust = false, precipitationMmH = 0, visibilityM: number | null = null, light: 'day' | 'dusk' = 'day';
  const vectors: Array<[number, number]> = [];
  let gustEndS: number | undefined;
  for (const engine of Object.values(scenario?.engines ?? {}) as any[]) {
    if (engine?.plugin !== 'environment') continue;
    for (const profile of Object.values(engine.config?.profiles ?? {}) as any[]) {
      const wind = profile?.['lowalt.wind'] ?? profile?.['oo:digital_twin.wind.horizontalVelocity'];
      if (wind) {
        let chosen = wind.value;
        for (const entry of (Array.isArray(wind.gusts) ? wind.gusts : []) as any[]) {
          if (entry.start_ns <= at && at < entry.end_ns) { chosen = entry.value; gust = true; gustEndS = Math.max(gustEndS ?? entry.end_ns / 1e9, entry.end_ns / 1e9); break; }
        }
        vectors.push(...windVectors(chosen));
      }
      const rain = scalar(profile?.['lowalt.precipitation']?.value);
      if (rain !== undefined) precipitationMmH = rain * 3600;
      const visibility = scalar(profile?.['lowalt.visibility']?.value);
      if (visibility !== undefined) visibilityM = visibility >= 50000 ? null : visibility;
      const value = profile?.['lowalt.light']?.value;
      if (value === 'dusk' || value === 'day') light = value;
    }
  }
  const incidents: Conditions['incidents'] = [];
  for (const incident of draftIncidents(scenario)) {
    if (!Array.isArray(incident?.position) || incident.position.length !== 3) continue;
    const startS = typeof incident.at_s === 'number' ? incident.at_s : 0;
    const endS = typeof incident.end_s === 'number' && incident.end_s > startS ? incident.end_s : undefined;
    const phase = at < sToNs(startS) ? 'pending' as const : endS !== undefined && at >= sToNs(endS) ? 'ended' as const : 'active' as const;
    const sinceS = Math.max(0, (phase === 'active' ? previewS - startS : phase === 'ended' ? previewS - endS! : startS - previewS));
    const kind = incident.kind === 'fire' ? 'fire' as const : 'person_fall' as const;
    incidents.push({ id: String(incident.id), kind, label: String(incident.label ?? incident.id), position: incident.position as [number, number, number],
      radiusM: typeof incident.radius_m === 'number' ? incident.radius_m : kind === 'fire' ? 30 : 6, phase, sinceS, startS });
  }
  const wind = windOf(vectors, gust);
  if (wind && gustEndS !== undefined) wind.gustEndS = gustEndS;
  return { wind, precipitationMmH, visibilityM, light, incidents };
}

/** Eight-point compass name for a meteorological from-direction. */
export function compassPoint(fromDeg: number): string {
  return compass[Math.round((((fromDeg % 360) + 360) % 360) / 45) % 8][0];
}
