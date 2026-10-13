import { operation } from '../operations';
import type { Draft } from './model';

/** Params for the C11 operations the Tasks / Components / Run forms stage (schemas on GET /v1/studio/operations). */

export const TASK_KINDS_C11 = ['parcel', 'food', 'medical', 'inspection', 'survey', 'spraying', 'passenger', 'patrol', 'incident_response'] as const;

/** RELEASE shape from operations.py: scheduled {times_s} or poisson {rate_per_s, start_s, end_s}. */
export type Release = {
  mode: 'scheduled'; times_s: number[];
} | {
  mode: 'poisson'; rate_per_s: number | undefined; start_s: number | undefined; end_s: number | undefined; burst_size?: number;
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
  helpers?: { vehicles?: string[]; ask_radius_m?: number; mode?: 'prescribed' | 'agent'; selection?: 'nearest' | 'first' };
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
    if (part.helpers.selection) helpers.selection = part.helpers.selection;
    out.helpers = helpers;
  }
  return out;
}

/** set_wind takes speed only — the operation schema has no direction parameter. */
export const setWindOp = (speed_mps: number) => operation('set_wind', { speed_mps });

/** add_gust: end must be after start and within the run horizon; speed ≥ 0. Returns the params or a field error. */
export function gustProblem(gust: { start_s: string; end_s: string; speed_mps: string; duration_s: number | undefined }, zh: boolean): Record<string, string> | undefined {
  const L = (en: string, cn: string) => (zh ? cn : en);
  const errors: Record<string, string> = {};
  const start = Number(gust.start_s), end = Number(gust.end_s), speed = Number(gust.speed_mps);
  if (gust.start_s.trim() === '' || !Number.isFinite(start) || start < 0) errors.start_s = L('Enter a start time of 0 s or more.', '请输入不小于 0 秒的开始时间。');
  if (gust.end_s.trim() === '' || !Number.isFinite(end) || end < 0) errors.end_s = L('Enter an end time of 0 s or more.', '请输入不小于 0 秒的结束时间。');
  if (gust.speed_mps.trim() === '' || !Number.isFinite(speed) || speed < 0) errors.speed_mps = L('Enter a wind speed of 0 m/s or more.', '请输入不小于 0 m/s 的风速。');
  if (Number.isFinite(start) && Number.isFinite(end) && start >= 0 && end >= 0 && end <= start) errors.end_s = L('End must be after start.', '结束必须晚于开始。');
  if (!errors.end_s && gust.duration_s !== undefined && Number.isFinite(end) && end > gust.duration_s) errors.end_s = L(`Must stay within the run duration (${gust.duration_s} s).`, `必须处于运行时长（${gust.duration_s} 秒）之内。`);
  return Object.keys(errors).length ? errors : undefined;
}
export const gustOp = (gust: { start_s: number; end_s: number; speed_mps: number }) =>
  operation('add_gust', { start_s: gust.start_s, end_s: gust.end_s, speed_mps: gust.speed_mps });

/** Incidents: one point event with a place/ENU position and a time in seconds. */
export const addIncidentOp = (input: { id: string; at: string; time_s: number }) =>
  operation('add_incident', { id: input.id, kind: 'person_fall', at: input.at, time_s: input.time_s });
export const setIncidentOp = (input: { id: string; time_s: number }) =>
  operation('set_incident', { id: input.id, time_s: input.time_s });

/** Run horizon in seconds (set_duration when lowalt, run.until_ns otherwise). */
export const durationOp = (seconds: number) => operation('set_duration', { seconds });
export const untilNs = (seconds: number) => Math.round(seconds * 1e9);

/** Inline "no facility yet" helper: the ids the order forms may pick endpoints from. */
export function placeIds(draft: Draft): string[] {
  return ((Array.isArray(draft.entities) ? draft.entities : []) as Draft[])
    .map(entity => String(entity?.id ?? ''))
    .filter(Boolean);
}
