import { expect, it } from 'vitest';
import { metricLabel, metricHelp } from './metric-help';
it('labels medical latency before its first value in both languages', () => {
  expect(metricLabel('medical_mean_task_latency_s', 'medical_mean_task_latency_s', 'en-US')).toBe('Medical task latency');
  expect(metricLabel('medical_mean_task_latency_s', 'medical_mean_task_latency_s', 'zh-CN')).toBe('医疗任务平均用时');
  expect(metricHelp('medical_mean_task_latency_s', 'zh-CN')).toContain('医疗任务');
});
it('preserves supplied bilingual catalog labels and supplies friendly labels for known ids', () => {
  expect(metricLabel('tasks_completed', 'Successful flights', 'en-US')).toBe('Successful flights');
  expect(metricLabel('tasks_completed', 'tasks_completed', 'en-US')).toBe('Tasks completed');
  expect(metricLabel('tasks_completed', 'tasks_completed', 'zh-CN', '本轮完成')).toBe('本轮完成');
});
