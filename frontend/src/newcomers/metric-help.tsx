/**
 * Plain-language explanations for the metric ids the simulation service actually
 * publishes (see services/kpis.py METRICS). Ids outside this list get no
 * explanation rather than an invented one.
 */
export const METRIC_HELP: Record<string, string> = {
  tasks_created: 'How many tasks the scenario generated in total during the run.',
  tasks_assigned: 'Tasks that a drone actually accepted or was given.',
  tasks_completed: 'Tasks finished successfully. More is usually better.',
  tasks_failed: 'Tasks that were cancelled or could not be finished. Fewer is better.',
  completion_rate: 'Share of created tasks that finished successfully.',
  mean_task_latency_s: 'Average time between a task appearing and being completed. Lower is better.',
  p95_task_latency_s: '95% of completed tasks took this long or less, from creation to completion.',
  on_time_rate: 'Share of deliveries that arrived before their committed deadline.',
  energy_used_kj: 'Total energy the fleet spent, in kilojoules. Lower is usually better.',
  distance_flown_km: 'Total kilometres flown by the whole fleet.',
  separation_conflicts: 'Times two aircraft came closer than the safe separation distance. Fewer is better.',
  geofence_violations: 'Times an aircraft entered a restricted area. Fewer is better.',
  weather_holds: 'Times aircraft had to hold and wait because of weather.',
  accident_reported: 'Reports of detected accidents recorded during the run.',
  award_committed: 'Incident-response tasks assigned to a responder.',
  captures_stored: 'Images or other captures stored during the run.',
  uploads_accepted: 'Data uploads accepted by the ground segment.',
};

/** Explanation for a known metric id, or undefined so callers never invent one. */
export function metricHelp(id: string): string | undefined {
  return METRIC_HELP[id];
}
