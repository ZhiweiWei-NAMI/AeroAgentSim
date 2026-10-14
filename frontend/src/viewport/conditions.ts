import type { RunHeader } from '../contracts/viewer-feed';

/**
 * Conditions derived at one cut: pure input to the atmosphere layer, the HUD and
 * the draft preview. Weather fields ride the existing `lowalt:Environment`
 * entity (id usually `weather`) as journaled profiles; absent fields mean clear
 * daylight with no rain.
 */

/** Visual only — shown in the 3D view and UAV captures; flight, sensing and task outcomes don't use it. */
export interface Conditions {
  wind: { east: number; north: number; speed: number; fromDeg: number; gust: boolean } | null;
  precipitationMmH: number;
  visibilityM: number | null;
  light: 'day' | 'dusk';
  incidents: Array<{ id: string; kind: 'fire' | 'person_fall'; label: string; position: [number, number, number]; radiusM: number;
    phase: 'pending' | 'active' | 'ended'; sinceS: number; response?: string }>;
}

type Cut = { entities: Map<string, { key: { id: string; generation: number | string }; typeId: string; fields: Map<string, { value: unknown }> }>; header?: RunHeader };

const scalar = (value: unknown): number | undefined => typeof value === 'number' && Number.isFinite(value) ? value : undefined;
const text = (value: unknown): string | undefined => typeof value === 'string' && value ? value : undefined;
const field = (entity: { fields: Map<string, { value: unknown }> }, id: string) => entity.fields.get(id)?.value;

/** Meteorological from-direction of an ENU vector; zero vector defaults to a west wind (from 270). */
export function windFromDeg(east: number, north: number): number {
  if (east === 0 && north === 0) return 270;
  return ((Math.atan2(-east, -north) * 180) / Math.PI + 360) % 360;
}

function windAt(cut: Cut, ns?: string): Conditions['wind'] {
  const values: Array<[number, number]> = [];
  for (const entity of cut.entities.values()) {
    const value = field(entity, 'lowalt.wind');
    if (Array.isArray(value)) {
      const inner = Array.isArray(value[0]) ? value[0] : value;
      const east = scalar(inner[0]), north = scalar(inner[1]);
      if (east !== undefined && north !== undefined) values.push([east, north]);
    }
  }
  if (!values.length) return null;
  // All configured wind profiles receive the same op; average keeps one display vector honest.
  const east = values.reduce((sum, [e]) => sum + e, 0) / values.length;
  const north = values.reduce((sum, [, n]) => sum + n, 0) / values.length;
  const speed = Math.hypot(east, north);
  const gust = ns !== undefined && gustActive(cut.header, ns);
  return { east, north, speed, fromDeg: windFromDeg(east, north), gust };
}

/** Gust is true only when the run header's authored gust intervals contain the cut — never guessed. */
function gustActive(header: RunHeader | undefined, ns: string): boolean {
  if (!header?.engines) return false;
  const at = BigInt(ns);
  for (const engine of header.engines) {
    if (engine.plugin !== 'environment') continue;
    const gusts = (engine as { gusts?: Array<[string | number, string | number]> }).gusts;
    if (gusts) return gusts.some(([start, end]) => BigInt(start) <= at && at < BigInt(end));
  }
  return false;
}

export function conditionsAt(entities: Cut['entities'] | Cut, ns: string, header?: RunHeader): Conditions {
  const cut: Cut = { entities: entities instanceof Map ? entities : (entities as Cut).entities, header };
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
    const startNs = scalar(field(entity, 'lowalt.incident_time_ns')) ?? 0;
    const endNs = scalar(field(entity, 'lowalt.incident_end_ns')) ?? -1;
    const active = field(entity, 'lowalt.incident_active') === true;
    const end = endNs > 0 ? endNs : undefined;
    const phase: Conditions['incidents'][number]['phase'] = !active && at < startNs ? 'pending' : active ? 'active' : 'ended';
    const sinceNs = active ? startNs : end ?? startNs;
    const sinceS = sinceNs <= 0 ? 0 : Math.max(0, Number(at - BigInt(Math.round(sinceNs))) / 1e9);
    const label = text(field(entity, 'lowalt.incident_label')) ?? entity.key.id;
    const response = text(field(entity, 'lowalt.incident_responder')) || undefined;
    incidents.push({ id: entity.key.id, kind, label, position: position as [number, number, number], radiusM: scalar(field(entity, 'lowalt.incident_radius_m')) ?? (kind === 'fire' ? 30 : 6), phase, sinceS: Math.max(0, sinceS), response });
  }
  return { wind: windAt(cut, ns), precipitationMmH, visibilityM, light, incidents };
}

const sToNs = (s: number) => Math.round(s * 1e9);

/** Draft preview moment: the first incident's start + 20 s, or 0. */
export function previewMoment(scenario: Record<string, any>): number {
  const incidents = scenario?.cooperation?.incidents;
  return Array.isArray(incidents) && incidents.length ? (typeof incidents[0]?.at_s === 'number' ? incidents[0].at_s : 0) + 20 : 0;
}

const compass: Array<[string, number]> = [['N', 0], ['NE', 45], ['E', 90], ['SE', 135], ['S', 180], ['SW', 225], ['W', 270], ['NW', 315]];
/** `conditionsFromDraft` mirrors the compiler: constant profiles on the lowalt:Environment weather entity. */
export function conditionsFromDraft(scenario: Record<string, any>, previewS: number): Conditions {
  const at = sToNs(previewS);
  let east = 0, north = 0, gust = false, precipitationMmH = 0, visibilityM: number | null = null, light: 'day' | 'dusk' = 'day';
  for (const engine of Object.values(scenario?.engines ?? {}) as any[]) {
    if (engine?.plugin !== 'environment') continue;
    for (const profile of Object.values(engine.config?.profiles ?? {}) as any[]) {
      const wind = profile?.['lowalt.wind'];
      if (wind) {
        const vector = (v: unknown): [number, number] | undefined => Array.isArray(v) ? (Array.isArray(v[0]) ? [Number(v[0][0]), Number(v[0][1])] : [Number(v[0]), Number(v[1])]) : undefined;
        let chosen = vector(wind.value);
        for (const entry of (Array.isArray(wind.gusts) ? wind.gusts : []) as any[]) {
          if (Number(entry.start_ns) <= at && at < Number(entry.end_ns)) { chosen = vector(entry.value); gust = true; break; }
        }
        if (chosen && chosen.every(Number.isFinite)) { east = chosen[0]; north = chosen[1]; }
      }
      const rain = Number(profile?.['lowalt.precipitation']?.value);
      if (Number.isFinite(rain)) precipitationMmH = rain * 3600;
      const visibility = Number(profile?.['lowalt.visibility']?.value);
      if (Number.isFinite(visibility)) visibilityM = visibility >= 50000 ? null : visibility;
      const value = profile?.['lowalt.light']?.value;
      if (value === 'dusk' || value === 'day') light = value;
    }
  }
  // Preview lighting is display metadata in the scene presentation, not a journaled field.
  const sceneLight = scenario?.scene?.lighting;
  if ((sceneLight === 'dusk' || sceneLight === 'day') && light === 'day' && !scenario?.engines) light = sceneLight;
  const incidents: Conditions['incidents'] = [];
  for (const incident of (scenario?.cooperation?.incidents ?? []) as any[]) {
    if (!Array.isArray(incident?.position) || incident.position.length !== 3) continue;
    const startS = typeof incident.at_s === 'number' ? incident.at_s : 0;
    const endS = typeof incident.end_s === 'number' && incident.end_s > startS ? incident.end_s : undefined;
    const phase = at < sToNs(startS) ? 'pending' as const : endS !== undefined && at >= sToNs(endS) ? 'ended' as const : 'active' as const;
    const sinceS = Math.max(0, (phase === 'active' ? previewS - startS : phase === 'ended' ? previewS - endS! : startS - previewS));
    const kind = incident.kind === 'fire' ? 'fire' as const : 'person_fall' as const;
    incidents.push({ id: String(incident.id), kind, label: String(incident.label ?? incident.id), position: incident.position as [number, number, number],
      radiusM: typeof incident.radius_m === 'number' ? incident.radius_m : kind === 'fire' ? 30 : 6, phase, sinceS });
  }
  const speed = Math.hypot(east, north);
  return { wind: { east, north, speed, fromDeg: windFromDeg(east, north), gust }, precipitationMmH, visibilityM, light, incidents };
}

/** Compass point name for a from-direction (16-point reads better than raw degrees in HUD copy). */
export function compassPoint(fromDeg: number): string {
  return compass[Math.round((((fromDeg % 360) + 360) % 360) / 45) % 8][0];
}
