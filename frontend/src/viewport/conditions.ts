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
  incidents: Array<{ id: string; kind: 'fire' | 'person_fall'; label: string; labelZh?: string; position: [number, number, number]; radiusM: number;
    phase: 'pending' | 'active' | 'ended'; sinceS: number; activeS: number; response?: string; startS?: number }>;
}

/** SI symbols in both languages. Speed 1 decimal; rain integer ≥ 1 else 1 decimal; visibility m below 1000 else km. */
export function formatMmH(mmH: number): string {
  return mmH >= 1 ? String(Math.round(mmH)) : mmH.toFixed(1);
}
export function formatVisibility(m: number): string {
  if (m < 1000) return `${Math.round(m)} m`;
  const km = m / 1000;
  return `${Number.isInteger(km) ? km : km.toFixed(1)} km`;
}

type Cut = { entities: Map<string, { key: { id: string; generation: number | string }; typeId: string; fields: Map<string, { value: unknown }> }>; header?: RunHeader };

const scalar = (value: unknown): number | undefined => typeof value === 'number' && Number.isFinite(value) ? value : undefined;
const text = (value: unknown): string | undefined => typeof value === 'string' && value ? value : undefined;
const field = (entity: { fields: Map<string, { value: unknown }> }, id: string) => entity.fields.get(id)?.value;
const INTEGER = /^-?\d+$/;
const integer = (value: unknown): number | bigint | undefined => {
  if (typeof value === 'number' && Number.isSafeInteger(value)) return value;
  const token = typeof value === 'string' ? value : value && typeof value === 'object' ? (value as { $integer?: unknown }).$integer : undefined;
  if (typeof token !== 'string' || !INTEGER.test(token)) return undefined;
  const number = Number(token); return Number.isSafeInteger(number) ? number : BigInt(token);
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

/** Gust is true only when the run header's authored gust intervals contain the cut — never guessed. */
function gustEnd(header: RunHeader | undefined, ns: string): number | undefined {
  if (!header?.engines) return undefined;
  const number = Number(ns), at = Number.isSafeInteger(number) ? number : BigInt(ns);
  let latest: number | undefined;
  for (const engine of header.engines) {
    if (engine.plugin !== 'environment') continue;
    if (engine.gusts) for (let i = 0; i < engine.gusts.length; i++) {
      const interval = engine.gusts[i], start = interval[0], end = interval[1];
      if (start <= at && at < end) latest = Math.max(latest ?? end, end);
    }
  }
  return latest === undefined ? undefined : latest / 1e9;
}

export function conditionsAt(entities: Cut['entities'] | Cut, ns: string, header?: RunHeader): Conditions {
  const map = entities instanceof Map ? entities : entities.entities;
  return deriveConditions(map, ns, header ?? (entities instanceof Map ? undefined : entities.header),
    { wind: null, precipitationMmH: 0, visibilityM: null, light: 'day', incidents: [] },
    { east: 0, north: 0, speed: 0, fromDeg: 270, gust: false });
}

/** This cache owns its mutable result. Only HUD notifications take snapshots. */
export class ConditionsCache {
  private entities?: Cut['entities'];
  private header?: RunHeader;
  private ns?: string;
  private revision = -1;
  private value: Conditions = { wind: null, precipitationMmH: 0, visibilityM: null, light: 'day', incidents: [] };
  private wind: NonNullable<Conditions['wind']> = { east: 0, north: 0, speed: 0, fromDeg: 270, gust: false };
  at(entities: Cut['entities'], ns: string, revision: number, header?: RunHeader): Conditions {
    if (this.entities !== entities || this.header !== header || this.ns !== ns || this.revision !== revision) {
      deriveConditions(entities, ns, header, this.value, this.wind);
      this.entities = entities; this.header = header; this.ns = ns; this.revision = revision;
    }
    return this.value;
  }
}

/** Indexed at journal ingestion, so playback only visits weather/incident entities. */
export function isConditionField(id: string): boolean {
  return id === 'lowalt.wind' || id === 'oo:digital_twin.wind.horizontalVelocity'
    || id === 'lowalt.precipitation' || id === 'lowalt.visibility' || id === 'lowalt.light' || id.startsWith('lowalt.incident_');
}

function deriveConditions(entities: Cut['entities'], ns: string, header: RunHeader | undefined, result: Conditions,
  wind: NonNullable<Conditions['wind']>): Conditions {
  let precipitationMmH = 0, visibilityM: number | null = null, light: 'day' | 'dusk' = 'day';
  const incidents = result.incidents;
  let incidentCount = 0, eastSum = 0, northSum = 0, vectorCount = 0;
  for (const entity of entities.values()) {
    const velocity = field(entity, 'lowalt.wind') ?? field(entity, 'oo:digital_twin.wind.horizontalVelocity');
    if (Array.isArray(velocity)) {
      if (Array.isArray(velocity[0])) {
        for (let i = 0; i < velocity.length; i++) {
          const row = velocity[i], east = scalar(row[0]), north = scalar(row[1]);
          if (east !== undefined && north !== undefined) { eastSum += east; northSum += north; vectorCount++; }
        }
      } else {
        const east = scalar(velocity[0]), north = scalar(velocity[1]);
        if (east !== undefined && north !== undefined) { eastSum += east; northSum += north; vectorCount++; }
      }
    }
    const rain = scalar(field(entity, 'lowalt.precipitation'));
    if (rain !== undefined) precipitationMmH = rain * 3600;
    const visibility = scalar(field(entity, 'lowalt.visibility'));
    if (visibility !== undefined) visibilityM = visibility >= 50000 ? null : visibility;
    const value = text(field(entity, 'lowalt.light'));
    if (value === 'dusk' || value === 'day') light = value;
  }
  const number = Number(ns), at = Number.isSafeInteger(number) ? number : BigInt(ns);
  for (const entity of entities.values()) {
    if (entity.typeId !== 'lowalt:Incident') continue;
    const position = field(entity, 'lowalt.incident_position');
    if (!Array.isArray(position) || position.length !== 3 || !position.every(Number.isFinite)) continue;
    const kind = text(field(entity, 'lowalt.incident_kind')) === 'fire' ? 'fire' as const : 'person_fall' as const;
    const startNs = integer(field(entity, 'lowalt.incident_time_ns'));
    const endNs = integer(field(entity, 'lowalt.incident_end_ns'));
    if (startNs === undefined) continue;
    const activeField = field(entity, 'lowalt.incident_active');
    const state = text(field(entity, 'lowalt.incident_state'));
    // A missing incident_active field means a legacy recording: derive activation from the
    // committed response state (anything other than "pending" means it occurred), and treat
    // legacy incidents as never ended. Explicit false stays an ended-or-pending fact.
    const legacy = typeof activeField !== 'boolean';
    const active = legacy ? state !== 'pending' : activeField === true;
    const end = legacy || endNs === undefined || endNs < 0n ? undefined : endNs;
    // Pending stays at the initial cut, before the occurrence wake commits (at < start time).
    const phase: Conditions['incidents'][number]['phase'] = at < startNs ? 'pending' : active ? 'active' : 'ended';
    const sinceNs = active ? startNs : end ?? startNs;
    const sinceS = Math.max(0, (typeof at === 'number' && typeof sinceNs === 'number' ? at - sinceNs : Number(BigInt(at) - BigInt(sinceNs))) / 1e9);
    // Activation age keeps advancing after ending; activeS - sinceS is the emission window.
    const activeS = Math.max(0, (typeof at === 'number' && typeof startNs === 'number' ? at - startNs : Number(BigInt(at) - BigInt(startNs))) / 1e9);
    let authored: NonNullable<RunHeader['entities']>[number] | undefined;
    if (header?.entities) for (let i = 0; i < header.entities.length; i++) {
      const row = header.entities[i];
      if (row.id === entity.key.id && row.type === entity.typeId) { authored = row; break; }
    }
    const label = authored?.label ?? entity.key.id;
    const labelZh = authored?.label_zh;
    const response = text(field(entity, 'lowalt.incident_responder')) || undefined;
    let incident = incidents[incidentCount];
    if (!incident) incidents[incidentCount] = incident = { id: '', kind, label: '', position: [0,0,0], radiusM: 0, phase, sinceS: 0, activeS: 0 };
    incident.id = entity.key.id; incident.kind = kind; incident.label = label; incident.labelZh = labelZh;
    incident.position = position as [number, number, number]; incident.radiusM = scalar(field(entity, 'lowalt.incident_radius_m')) ?? (kind === 'fire' ? 30 : 6);
    incident.phase = phase; incident.sinceS = sinceS; incident.activeS = activeS; incident.response = response; incident.startS = Number(startNs) / 1e9;
    incidentCount++;
  }
  incidents.length = incidentCount;
  result.precipitationMmH = precipitationMmH; result.visibilityM = visibilityM; result.light = light;
  if (vectorCount) {
    wind.east = eastSum / vectorCount; wind.north = northSum / vectorCount;
    wind.speed = Math.hypot(wind.east, wind.north); wind.fromDeg = windFromDeg(wind.east, wind.north);
    wind.gustEndS = gustEnd(header, ns); wind.gust = wind.gustEndS !== undefined; result.wind = wind;
  } else result.wind = null;
  return result;
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
const compassZh = ['北', '东北', '东', '东南', '南', '西南', '西', '西北'];
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
    incidents.push({ id: String(incident.id), kind, label: String(incident.label ?? incident.id), labelZh: typeof incident.label_zh === 'string' ? incident.label_zh : undefined,
      position: incident.position as [number, number, number],
      radiusM: typeof incident.radius_m === 'number' ? incident.radius_m : kind === 'fire' ? 30 : 6, phase, sinceS, activeS: Math.max(0, previewS - startS), startS });
  }
  const wind = windOf(vectors, gust);
  if (wind && gustEndS !== undefined) wind.gustEndS = gustEndS;
  return { wind, precipitationMmH, visibilityM, light, incidents };
}

/** Eight-point compass name for a meteorological from-direction. */
export function compassPoint(fromDeg: number): string {
  return compass[Math.round((((fromDeg % 360) + 360) % 360) / 45) % 8][0];
}

/** Chinese compass name for the wind line: “西北风 6.0 m/s”. */
export function compassPointZh(fromDeg: number): string {
  return compassZh[Math.round((((fromDeg % 360) + 360) % 360) / 45) % 8];
}

/** Shared phase copy for the run's entity labels and the draft's incident pins. */
export function incidentLabel(row: Conditions['incidents'][number], zh: boolean, label = (zh ? row.labelZh : undefined) ?? row.label): string {
  const clock = (s: number) => `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
  const kind = row.kind === 'fire' ? zh ? '火情' : 'Fire' : zh ? '行人跌倒' : 'Person fell';
  // Unlabelled incidents keep the kind's default label ("Building fire" / "建筑火情" for fire).
  const name = !label || label === row.id ? (row.kind === 'fire' ? zh ? '建筑火情' : 'Building fire' : kind) : label;
  if (row.phase === 'pending') return zh ? `${kind} ${clock(row.startS!)} 开始` : `${kind} at ${clock(row.startS!)}`;
  if (row.phase === 'ended') return `${kind} · ${zh ? '已结束' : 'ended'}`;
  return `${name} · ${zh ? '已持续' : 'Active'} ${clock(row.sinceS)}`;
}
