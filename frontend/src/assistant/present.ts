import type { Policy, Release } from './api';

export type Lang = (en: string, zh: string) => string;

/** Simulated seconds as m:ss — the clock users see in the workbench. */
export function clock(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return '—';
  const whole = Math.max(0, Math.round(seconds)), minutes = Math.floor(whole / 60);
  return `${String(minutes).padStart(2, '0')}:${String(whole % 60).padStart(2, '0')}`;
}

export function pick(row: { label?: string; label_zh?: string; text?: string; text_zh?: string } | undefined, zh: boolean): string {
  if (!row) return '';
  return (zh ? row.label_zh ?? row.text_zh : undefined) ?? row.label ?? row.text ?? '';
}

const CJK = /[\u3400-\u9fff]/;
/** Platform messages carry both languages as "English / 中文"; show the reader's half. */
export function bi(text: string | null | undefined, zh: boolean): string {
  if (!text) return '';
  for (let at = text.indexOf(' / '); at >= 0; at = text.indexOf(' / ', at + 3)) {
    const en = text.slice(0, at), cn = text.slice(at + 3);
    if (!CJK.test(en) && CJK.test(cn)) return zh ? cn : en;
  }
  return text;
}

const PLACE_ROLES: Record<string, [string, string]> = {
  kitchen: ['Kitchen', '厨房'], restaurant: ['Kitchen', '厨房'], customer: ['Drop-off', '收货点'], vertiport: ['Vertiport', '起降场'], depot: ['Depot', '仓库'],
  charging_pad: ['Charging pad', '充电位'], handoff_point: ['Handoff point', '交接点'], incident_site: ['Incident site', '事发地点'],
  inspection_start: ['Inspection start', '巡检起点'], inspection_end: ['Inspection end', '巡检终点'], site: ['Site', '站点'],
};
export const placeRole = (role: string, L: Lang) => { const row = PLACE_ROLES[role]; return row ? L(row[0], row[1]) : role.replace(/_/g, ' '); };

const KINDS: Record<string, [string, string, string, string]> = {
  parcel: ['parcel', 'parcels', '包裹', '包裹'], food: ['meal order', 'meal orders', '餐食订单', '餐食订单'], medical: ['medical delivery', 'medical deliveries', '医疗配送', '医疗配送'],
  inspection: ['inspection', 'inspections', '巡检', '巡检'], spraying: ['spraying job', 'spraying jobs', '喷洒作业', '喷洒作业'], passenger: ['passenger flight', 'passenger flights', '载客航班', '载客航班'],
  patrol: ['patrol', 'patrols', '巡逻', '巡逻'], incident_response: ['incident response', 'incident responses', '事件响应', '事件响应'],
};
export const kindNoun = (kind: string, count: number, L: Lang) => { const row = KINDS[kind]; return row ? L(count === 1 ? row[0] : row[1], row[2]) : kind.replace(/_/g, ' '); };

export function releaseText(release: Release | undefined, L: Lang): string {
  if (!release) return '';
  if (release.mode === 'scheduled' && release.times_s?.length) {
    const times = release.times_s;
    return times.length <= 4 ? L(`at ${times.map(clock).join(', ')}`, `于 ${times.map(clock).join('、')}`) : L(`${clock(times[0])} – ${clock(times.at(-1))}`, `${clock(times[0])} – ${clock(times.at(-1))}`);
  }
  if (release.mode === 'poisson' && release.rate_per_s) {
    const every = 1 / release.rate_per_s;
    const window = release.start_s !== undefined && release.end_s !== undefined ? L(` between ${clock(release.start_s)} and ${clock(release.end_s)}`, `（${clock(release.start_s)}–${clock(release.end_s)}）`) : '';
    return L(`about one every ${every < 90 ? `${Math.round(every)} s` : `${(every / 60).toFixed(1)} min`}${window}`, `约每 ${every < 90 ? `${Math.round(every)} 秒` : `${(every / 60).toFixed(1)} 分钟`}一单${window}`);
  }
  return release.mode;
}

export const OPTION_TEXT: Record<string, [string, string]> = {
  respond_now: ['go to the incident right away', '立即前往事发地点'],
  request_handoff: ['ask a nearby courier to take over its delivery', '请附近的配送员接手当前配送'],
  finish_then_respond: ['finish the current delivery, then go', '先完成当前配送，再前往'],
  report_only: ['report it and carry on', '上报后继续原任务'],
  accept: ['accept', '接受'], decline: ['decline', '拒绝'],
};
export const optionText = (option: string, L: Lang) => { const row = OPTION_TEXT[option]; return row ? L(row[0], row[1]) : option.replace(/_/g, ' '); };

/** Constraints a policy enforces, in the words a user would use. */
export function constraintTexts(policy: Policy | undefined, L: Lang): string[] {
  const rules = (policy?.rules ?? {}) as Record<string, unknown>, out: string[] = [];
  const protectedKinds = Array.isArray(rules.protected_task_kinds) ? rules.protected_task_kinds as string[] : [];
  if (protectedKinds.length) out.push(L(`Never interrupts ${protectedKinds.map(kind => kindNoun(kind, 2, L)).join(' or ')}`, `不中断${protectedKinds.map(kind => kindNoun(kind, 2, L)).join('或')}`));
  if (rules.require_replacement === true) out.push(L('Leaves a delivery only after someone accepts it', '只有在有人接手后才离开当前配送'));
  if (typeof rules.min_battery_pct === 'number') out.push(L(`Needs at least ${rules.min_battery_pct}% battery`, `电量至少 ${rules.min_battery_pct}%`));
  if (typeof rules.max_response_distance_m === 'number') out.push(L(`Responds within ${rules.max_response_distance_m} m`, `响应范围 ${rules.max_response_distance_m} 米内`));
  if (rules.accept_when === 'idle') out.push(L('Accepts only when it has no task of its own', '仅在没有自身任务时接受'));
  if (rules.accept_when === 'always') out.push(L('Always accepts when asked', '被请求时总是接受'));
  if (policy?.selection === 'nearest') out.push(L('If several accept, the nearest one takes it', '多人接受时，由最近者接手'));
  if (policy?.selection === 'first') out.push(L('If several accept, the first to answer takes it', '多人接受时，由最先回复者接手'));
  return out;
}

export function valueText(value: unknown, unit?: string): string {
  if (typeof value === 'boolean') return value ? '✓' : '✗';
  if (typeof value === 'number') return `${Number.isInteger(value) ? value : value.toFixed(value < 10 ? 2 : 1)}${unit ? ` ${unit}` : ''}`;
  if (Array.isArray(value)) return value.map(item => valueText(item)).join(', ');
  if (value && typeof value === 'object') return JSON.stringify(value);
  return value === undefined || value === null ? '—' : String(value);
}
