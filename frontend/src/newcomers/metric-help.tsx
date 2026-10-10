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
  };
}

export const METRIC_HELP = explanations('en-US');
export function metricHelp(id: string, locale = 'en-US'): string | undefined {
  return explanations(locale)[id];
}

/** English catalog labels remain verbatim. Known KPI ids get natural Chinese labels. */
export function metricLabel(id: string, label: string, locale = 'en-US', labelZh?: string): string {
  if (locale !== 'zh-CN') return label;
  if (labelZh !== undefined) return labelZh;
  const t = (key: string) => translate(locale, key);
  const labels: Record<string, string> = {
    tasks_created: t('Tasks created'), tasks_assigned: t('Tasks assigned'),
    tasks_completed: t('Tasks completed'), tasks_failed: t('Tasks failed'),
    completion_rate: t('Completed tasks / created tasks'),
    mean_task_latency_s: t('Mean task latency'),
    p95_task_latency_s: t('95th percentile task latency'),
    on_time_rate: t('On-time rate over deliveries with a committed deadline'),
    energy_used_kj: t('Energy used'), distance_flown_km: t('Distance flown'),
    separation_conflicts: t('Separation conflicts'), geofence_violations: t('Geofence violations'),
    weather_holds: t('Weather holds'), accident_reported: t('Accidents reported'),
    award_committed: t('Awards committed'), captures_stored: t('Captures stored'),
    uploads_accepted: t('Uploads accepted'),
  };
  return labels[id] ?? label;
}
