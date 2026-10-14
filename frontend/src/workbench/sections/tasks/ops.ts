import { parseStagedNumber } from './op-ui';

/** Params for the C11 operations the Tasks / Components / Run forms stage (schemas on GET /v1/studio/operations). */

export const TASK_KINDS_C11 = ['parcel', 'food', 'medical', 'inspection', 'survey', 'spraying', 'passenger', 'patrol', 'incident_response'] as const;

export function taskKindLabel(kind: string, L: (en: string, zh: string) => string): string {
  const labels: Record<string, [string, string]> = {
    parcel: ['Parcel', '包裹'], food: ['Meal', '餐食'], medical: ['Medical', '医疗'],
    inspection: ['Inspection', '巡检'], survey: ['Survey', '测绘'], spraying: ['Spraying', '喷洒'],
    passenger: ['Passenger', '乘客'], patrol: ['Patrol', '巡逻'], incident_response: ['Incident response', '事件响应'],
  };
  return labels[kind] ? L(...labels[kind]) : L('Unsupported kind', '不支持的类型');
}

/** RELEASE shape from operations.py: scheduled {times_s} or poisson {rate_per_s, start_s, end_s}. */
export type Release = {
  mode: 'scheduled'; times_s: number[];
} | {
  mode: 'poisson'; rate_per_s: number | undefined; start_s?: number; end_s?: number; burst_size?: number;
};

export interface OrdersInput {
  id: string; kind: string; count: number; from: string[]; to: string[];
  release: Release; priority?: number; payload_kg?: number; delivery_window_s?: number;
}

/** add_orders params: only non-optional staged fields are sent (server owns defaults). */
export function addOrdersParams(input: OrdersInput): Record<string, unknown> {
  return {
    id: input.id, kind: input.kind, count: input.count, from: input.from, to: input.to, release: input.release,
    ...(input.priority !== undefined ? { priority: input.priority } : {}),
    ...(input.payload_kg !== undefined ? { payload_kg: input.payload_kg } : {}),
    ...(input.kind === 'food' && input.delivery_window_s !== undefined ? { delivery_window_s: input.delivery_window_s } : {}),
  };
}

/** True when any chosen endpoint is not an existing place id (inline warning, not a silent default). */
export function unknownEndpoints(input: { from: string[]; to: string[] }, places: string[]): string[] {
  return [...input.from, ...input.to].filter(id => !places.includes(id));
}

/** set_cooperation merges into existing cooperation; the server validates vehicles/rules. */
export function cooperationParams(part: {
  watchers?: { vehicles?: string[]; notice_radius_m?: number };
  responder?: { mode?: 'prescribed' | 'agent'; goal?: string; order?: string[] };
  helpers?: { vehicles?: string[]; ask_radius_m?: number; mode?: 'prescribed' | 'agent'; goal?: string; selection?: 'nearest' | 'first' };
  handoff_points?: string[];
}): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  if (part.watchers) {
    const watchers: Record<string, unknown> = {};
    if (part.watchers.vehicles) watchers.vehicles = part.watchers.vehicles;
    if (part.watchers.notice_radius_m !== undefined) watchers.notice_radius_m = part.watchers.notice_radius_m;
    out.watchers = watchers;
  }
  if (part.responder) {
    const responder: Record<string, unknown> = {};
    if (part.responder.mode) responder.mode = part.responder.mode;
    if (part.responder.goal) responder.goal = part.responder.goal;
    if (part.responder.order) responder.order = part.responder.order;
    out.responder = responder;
  }
  if (part.helpers) {
    const helpers: Record<string, unknown> = {};
    if (part.helpers.vehicles) helpers.vehicles = part.helpers.vehicles;
    if (part.helpers.ask_radius_m !== undefined) helpers.ask_radius_m = part.helpers.ask_radius_m;
    if (part.helpers.mode) helpers.mode = part.helpers.mode;
    if (part.helpers.goal) helpers.goal = part.helpers.goal;
    if (part.helpers.selection) helpers.selection = part.helpers.selection;
    out.helpers = helpers;
  }
  if (part.handoff_points) out.handoff_points = part.handoff_points;
  return out;
}

/** Incident kinds and the affected-radius defaults the backend applies (spec §1.1). */
export const INCIDENT_KINDS = ['person_fall', 'fire'] as const;
export const incidentRadiusDefault = (kind: string): number => kind === 'fire' ? 30 : 6;

/** add_incident / set_incident: end > start, within the run horizon, radius 1..500. */
export function incidentProblem(incident: { time_s: string; end_s?: string; radius_m?: string; duration_s?: number }, zh: boolean): Record<string, string> {
  const L = (en: string, cn: string) => (zh ? cn : en);
  const errors: Record<string, string> = {};
  const time = parseStagedNumber(incident.time_s), end = incident.end_s === undefined ? undefined : parseStagedNumber(incident.end_s);
  const radius = incident.radius_m === undefined || incident.radius_m === '' ? undefined : parseStagedNumber(incident.radius_m);
  if (radius !== undefined && (radius < 1 || radius > 500)) errors.radius_m = L('Enter an affected radius between 1 and 500 m.', '影响半径必须介于 1 与 500 米之间。');
  if (end !== undefined && time !== undefined && end <= time) errors.end_s = L('End must be after start.', '结束必须晚于开始。');
  if (end !== undefined && incident.duration_s !== undefined && end > incident.duration_s)
    errors.end_s = L(`Must stay within the run duration (${incident.duration_s} s).`, `必须处于运行时长（${incident.duration_s} 秒）之内。`);
  return errors;
}

/** Preset ids matching the current draft values; authored values that are not presets fall back to custom. */
export const rainPresetOf = (mmH: number): string => mmH <= 0 ? 'none' : mmH === 1 ? 'light' : mmH === 4 ? 'moderate' : mmH === 10 ? 'heavy' : 'custom';
export const visibilityPresetOf = (m: number | null): string => m === null || m >= 50000 ? 'clear'
  : m === 5000 ? 'haze' : m === 2000 ? 'mist' : m === 500 ? 'fog' : m === 150 ? 'dense' : 'custom';

/** One set_weather op from the staged Conditions fields; 0 and null are written explicitly so the state is known (spec §1.2). */
export function weatherParams(input: { rain: string; visibility: string; light: string; rainCustom: string; visibilityCustom: string }): Record<string, unknown> | undefined {
  const presetRain: Record<string, number | undefined> = { none: 0, light: 1, moderate: 4, heavy: 10, custom: parseStagedNumber(input.rainCustom) };
  const presetVisibility: Record<string, number | null | undefined> = { clear: null, haze: 5000, mist: 2000, fog: 500, dense: 150, custom: parseStagedNumber(input.visibilityCustom) };
  const rain = presetRain[input.rain], visibility = presetVisibility[input.visibility];
  if (rain === undefined || visibility === undefined || !['day', 'dusk'].includes(input.light)) return undefined;
  return { precipitation_mm_h: rain, visibility_m: visibility, light: input.light };
}

/** add_gust: end must be after start and within the run horizon; speed ≥ 0. Returns the params or a field error. */
export function gustProblem(gust: { start_s: string; end_s: string; speed_mps: string; duration_s: number | undefined }, zh: boolean): Record<string, string> | undefined {
  const L = (en: string, cn: string) => (zh ? cn : en);
  const errors: Record<string, string> = {};
  const start = parseStagedNumber(gust.start_s), end = parseStagedNumber(gust.end_s), speed = parseStagedNumber(gust.speed_mps);
  if (start === undefined || start < 0) errors.start_s = L('Enter a start time of 0 s or more.', '请输入不小于 0 秒的开始时间。');
  if (end === undefined || end < 0) errors.end_s = L('Enter an end time of 0 s or more.', '请输入不小于 0 秒的结束时间。');
  if (speed === undefined || speed < 0) errors.speed_mps = L('Enter a wind speed of 0 m/s or more.', '请输入不小于 0 m/s 的风速。');
  if (start !== undefined && end !== undefined && start >= 0 && end >= 0 && end <= start) errors.end_s = L('End must be after start.', '结束必须晚于开始。');
  if (!errors.end_s && gust.duration_s !== undefined && end !== undefined && end > gust.duration_s) errors.end_s = L(`Must stay within the run duration (${gust.duration_s} s).`, `必须处于运行时长（${gust.duration_s} 秒）之内。`);
  return Object.keys(errors).length ? errors : undefined;
}
