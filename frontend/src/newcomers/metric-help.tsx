import { translate } from '../i18n/translate';

/** Only explanations for metric ids the service actually publishes, never invented help. */
function explanations(locale: string): Record<string, string> {
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

export function metricHelp(id: string, locale = 'en-US'): string | undefined {
  return explanations(locale)[id];
}

/** Declared units for known metric ids, so a metric keeps its SI unit before its first value arrives. */
const METRIC_UNITS: Record<string, string> = {
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

/** Known metric unit from the id alone; `count` is a dimensionless tally the value formatter drops. */
export function metricUnit(id: string): string | undefined {
  return METRIC_UNITS[id];
}

/** Boolean-like KPIs read as a word: rescue_success is 0/1 on the wire but Yes/No for people. */
export function metricValueText(id: string, value: number | null, unit: string, locale = 'en-US'): string {
  if (value === null || !Number.isFinite(value)) return '—';
  if (id === 'rescue_success') return translate(locale, value ? 'Yes' : 'No');
  if (unit === 'count') unit = '';
  const abs = Math.abs(value);
  const digits = Number.isInteger(value) ? 0 : abs >= 1000 ? 0 : abs >= 10 ? 1 : 2;
  return `${value.toFixed(digits)}${unit ? ` ${unit}` : ''}`;
}

/** Known ids have readable bilingual labels even before a KPI value arrives. */
export function metricLabel(id: string, label: string, locale = 'en-US', labelZh?: string): string {
  if (locale === 'zh-CN' && labelZh?.trim()) return labelZh;
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
