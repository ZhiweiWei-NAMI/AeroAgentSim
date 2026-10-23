import { translate } from '../i18n/translate';
import type { KpiValue } from './api';

export type MetricMetadata = Omit<KpiValue, 'value'>;

/** Even a partial descriptor blocks ID-based guesses; explicit null is meaningful. */
export function hasMetricDescriptor(row?: MetricMetadata): boolean {
  return !!row && (row.label_zh !== undefined || row.direction !== undefined ||
    row.aggregation !== undefined || row.missing_data_policy !== undefined);
}

/** Normalize the declared direction, never infer one from a label, unit or value. */
export function metricDirection(row?: MetricMetadata): 'min' | 'max' | 'neutral' {
  if (row?.direction === 'higher' || row?.direction === 'max') return 'max';
  if (row?.direction === 'lower' || row?.direction === 'min') return 'min';
  return 'neutral';
}

/**
 * LEGACY FALLBACK ONLY: these ID tables serve rows without descriptor fields
 * (and older ID-only callers). Descriptor data always takes precedence, even
 * for a builtin ID replaced by an installed provider. Do not add plugin IDs here.
 */
export const LEGACY_METRIC_DIRECTIONS: Record<string, 'min' | 'max' | 'neutral'> = {
  tasks_completed: 'max', tasks_created: 'neutral',
  mean_task_latency_s: 'min', p95_task_latency_s: 'min',
  energy_used_kj: 'min', distance_flown_km: 'neutral',
  separation_conflicts: 'min', geofence_violations: 'min', weather_holds: 'min',
  rescue_success: 'max', rescue_time_to_confirmation_s: 'min', rescue_time_to_delivery_s: 'min',
};

/** Legacy help for runs that predate descriptor metadata. */
function legacyExplanations(locale: string): Record<string, string> {
  const t = (key: string) => translate(locale, key);
  return {
    tasks_created: t('How many tasks the scenario generated in total during the run.'),
    tasks_assigned: t('Tasks that a drone actually accepted or was given.'),
    tasks_completed: t('Tasks finished successfully. More is usually better.'),
    tasks_failed: t('Tasks that were cancelled or could not be finished. Fewer is better.'),
    completion_rate: t('Share of created tasks that finished successfully.'),
    medical_mean_task_latency_s: locale === 'zh-CN' ? '医疗任务从出现到完成的平均时长。' : 'Average time between a medical task appearing and being completed.',
    mean_task_latency_s: t('Average time between a task appearing and being completed. Lower is better.'),
    p95_task_latency_s: t('95% of completed tasks took this long or less, from creation to completion.'),
    on_time_rate: t('Share of deliveries that arrived before their committed deadline.'),
    energy_used_kj: t('Total energy the fleet spent, in kilojoules. Lower is usually better.'),
    distance_flown_km: t('Total kilometres flown by the whole fleet.'),
    separation_conflicts: t('Times two aircraft came closer than the safe separation distance. Fewer is better.'),
    geofence_violations: t('Times an aircraft entered a restricted area. Fewer is better.'),
    weather_holds: t('Times aircraft had to hold and wait because of weather.'),
    accident_reported: t('Reports of detected accidents recorded during the run.'),
    award_committed: t('Incident-response tasks assigned to a responder.'),
    captures_stored: t('Images or other captures stored during the run.'),
    uploads_accepted: t('Data uploads accepted by the ground segment.'),
    rescue_success: t('The centre received the delivery acknowledgement before the deadline (for a negative or uncertain observation: correctly resolved with no delivery). 1 is success, 0 is not.'),
    rescue_time_to_confirmation_s: t('Time from the incident being noticed to the report reaching the centre, in seconds. Lower is better.'),
    rescue_time_to_delivery_s: t('Time from the incident being noticed to the delivery being completed, in seconds. Lower is better.'),
    rescue_plans: t('Number of plan_computed events: how many times the centre proposed a plan.'),
    rescue_bids: t('Number of bids the vehicles submitted for the plan.'),
    rescue_candidates: t('Total role candidates across all bids.'),
    rescue_radio_packets: t('Radio packets sent over the native radio link.'),
    rescue_radio_delivered: t('Radio packets the radio link confirmed as delivered.'),
    rescue_radio_bytes: t('Radio bytes sent over the native radio link.'),
    rescue_bus_messages: t('rescue.* messages published on the reliable event bus.'),
    rescue_bus_bytes: t('Bytes of those reliable-bus messages.'),
    rescue_model_calls: t('Model calls the run made.'),
  };
}

/** Descriptor prose is opaque plugin text, not a shared translation-catalog key. */
export function metricHelp(id: string, locale = 'en-US', row?: MetricMetadata): string | undefined {
  if (hasMetricDescriptor(row)) {
    const zh = locale === 'zh-CN';
    return [
      row?.aggregation ? `${zh ? '汇总方式：' : 'Aggregation: '}${row.aggregation}` : undefined,
      row?.missing_data_policy ? `${zh ? '缺失数据：' : 'Missing data: '}${row.missing_data_policy}` : undefined,
    ].filter(Boolean).join('\n') || undefined;
  }
  return legacyExplanations(locale)[id];
}

/** Legacy units only; descriptor rows supply their own unit before the first sample. */
const LEGACY_METRIC_UNITS: Record<string, string> = {
  tasks_created: 'count', tasks_assigned: 'count', tasks_completed: 'count', tasks_failed: 'count',
  completion_rate: 'ratio', mean_task_latency_s: 's', p95_task_latency_s: 's',
  medical_mean_task_latency_s: 's', medical_p95_task_latency_s: 's', on_time_rate: 'ratio',
  energy_used_kj: 'kJ', distance_flown_km: 'km', spraying_distance_m: 'm',
  battery_swaps: 'count', inspection_observations: 'count', patrol_visits: 'count',
  separation_conflicts: 'count', geofence_violations: 'count', weather_holds: 'count',
  accident_reported: 'count', award_committed: 'count', captures_stored: 'count', uploads_accepted: 'count',
  rescue_success: 'count', rescue_time_to_confirmation_s: 's', rescue_time_to_delivery_s: 's',
  rescue_plans: 'count', rescue_bids: 'count', rescue_candidates: 'count',
  rescue_radio_packets: 'count', rescue_radio_delivered: 'count', rescue_radio_bytes: 'B',
  rescue_bus_messages: 'count', rescue_bus_bytes: 'B', rescue_model_calls: 'count',
};

/** An explicit descriptor unit (including an empty unit) is never replaced by an ID guess. */
export function metricUnit(id: string, row?: MetricMetadata): string | undefined {
  if (hasMetricDescriptor(row)) return row?.unit;
  return row?.unit || LEGACY_METRIC_UNITS[id];
}

/** Descriptor values are numeric; the old Yes/No special case is legacy-only. */
export function metricValueText(id: string, value: number | null, unit: string, locale = 'en-US', row?: MetricMetadata): string {
  if (value === null || !Number.isFinite(value)) return '—';
  if (!hasMetricDescriptor(row) && id === 'rescue_success') return translate(locale, value ? 'Yes' : 'No');
  if (unit === 'count') unit = '';
  const abs = Math.abs(value);
  const digits = Number.isInteger(value) ? 0 : abs >= 1000 ? 0 : abs >= 10 ? 1 : 2;
  return `${value.toFixed(digits)}${unit ? ` ${unit}` : ''}`;
}

/** Prefer the row's active-locale label; the string signature remains for legacy callers. */
export function metricLabel(id: string, label: string | MetricMetadata, locale = 'en-US', labelZh?: string): string {
  if (typeof label !== 'string') {
    const row = label;
    if (hasMetricDescriptor(row)) return locale === 'zh-CN' && row.label_zh?.trim() ? row.label_zh : row.label;
    labelZh = row.label_zh;
    label = row.label;
  }
  if (locale === 'zh-CN' && labelZh?.trim()) return labelZh;
  // Legacy bilingual labels only; never translate or humanize descriptor text.
  const t = (key: string) => translate(locale, key);
  const labels: Record<string, string> = {
    tasks_created: t('Tasks created'), tasks_assigned: t('Tasks assigned'),
    tasks_completed: t('Tasks completed'), tasks_failed: t('Tasks failed'),
    completion_rate: t('Completion rate'),
    mean_task_latency_s: t('Mean task latency'),
    medical_mean_task_latency_s: locale === 'zh-CN' ? '医疗任务平均用时' : 'Medical task latency',
    p95_task_latency_s: t('P95 task latency'),
    on_time_rate: t('On-time rate'),
    energy_used_kj: t('Energy used'), distance_flown_km: t('Distance flown'),
    separation_conflicts: t('Separation conflicts'), geofence_violations: t('Geofence violations'),
    weather_holds: t('Weather holds'), accident_reported: t('Accidents reported'),
    award_committed: t('Awards committed'), captures_stored: t('Captures stored'),
    uploads_accepted: t('Uploads accepted'),
    medical_p95_task_latency_s: locale === 'zh-CN' ? '医疗任务 P95 用时' : 'Medical P95 task latency',
    spraying_distance_m: locale === 'zh-CN' ? '喷洒距离' : 'Spraying distance',
    battery_swaps: locale === 'zh-CN' ? '电池更换次数' : 'Battery swaps',
    inspection_observations: locale === 'zh-CN' ? '巡检观测次数' : 'Inspection observations',
    patrol_visits: locale === 'zh-CN' ? '巡逻到访次数' : 'Patrol visits',
    rescue_success: locale === 'zh-CN' ? '救援成功' : 'Rescue success',
    rescue_time_to_confirmation_s: locale === 'zh-CN' ? '确认用时（秒）' : 'Time to confirmation (s)',
    rescue_time_to_delivery_s: locale === 'zh-CN' ? '投送用时（秒）' : 'Time to delivery (s)',
    rescue_plans: locale === 'zh-CN' ? '规划次数' : 'Plans',
    rescue_bids: locale === 'zh-CN' ? '参与提议' : 'Bids',
    rescue_candidates: locale === 'zh-CN' ? '候选角色数' : 'Role candidates',
    rescue_radio_packets: locale === 'zh-CN' ? '无线报文' : 'Radio packets',
    rescue_radio_delivered: locale === 'zh-CN' ? '无线送达' : 'Radio delivered',
    rescue_radio_bytes: locale === 'zh-CN' ? '无线字节' : 'Radio bytes',
    rescue_bus_messages: locale === 'zh-CN' ? '总线消息' : 'Bus messages',
    rescue_bus_bytes: locale === 'zh-CN' ? '总线字节' : 'Bus bytes',
    rescue_model_calls: locale === 'zh-CN' ? '模型调用' : 'Model calls',
  };
  return locale === 'zh-CN' || label === id || /[_:]/.test(label) ? labels[id] ?? label.replace(/_/g, ' ') : label;
}
