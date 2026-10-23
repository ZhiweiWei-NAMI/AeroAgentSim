import { expect, it } from 'vitest';
import { metricLabel, metricHelp, metricUnit, metricValueText, metricDirection, type MetricMetadata } from './metric-help';
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

it('labels every C2 rescue KPI id in EN and ZH', () => {
  const expected: Array<[string, string, string]> = [
    ['rescue_success', 'Rescue success', '救援成功'],
    ['rescue_time_to_confirmation_s', 'Time to confirmation (s)', '确认用时（秒）'],
    ['rescue_time_to_delivery_s', 'Time to delivery (s)', '投送用时（秒）'],
    ['rescue_plans', 'Plans', '规划次数'],
    ['rescue_bids', 'Bids', '参与提议'],
    ['rescue_candidates', 'Role candidates', '候选角色数'],
    ['rescue_radio_packets', 'Radio packets', '无线报文'],
    ['rescue_radio_delivered', 'Radio delivered', '无线送达'],
    ['rescue_radio_bytes', 'Radio bytes', '无线字节'],
    ['rescue_bus_messages', 'Bus messages', '总线消息'],
    ['rescue_bus_bytes', 'Bus bytes', '总线字节'],
    ['rescue_model_calls', 'Model calls', '模型调用'],
  ];
  for (const [id, en, zh] of expected) {
    // The id is the raw label before a value lands, which is exactly what the maps must resolve.
    expect(metricLabel(id, id, 'en-US'), id).toBe(en);
    expect(metricLabel(id, id, 'zh-CN'), id).toBe(zh);
    expect(metricHelp(id, 'en-US'), id).toBeTruthy();
    expect(metricHelp(id, 'zh-CN'), id).toBeTruthy();
  }
  // No help/label invented for an unknown id.
  expect(metricLabel('not_a_metric', 'not_a_metric', 'en-US')).toBe('not a metric');
});

it('declares SI units for the rescue KPIs and formats rescue_success as Yes/No', () => {
  expect(metricUnit('rescue_time_to_confirmation_s')).toBe('s');
  expect(metricUnit('rescue_radio_bytes')).toBe('B');
  expect(metricUnit('rescue_plans')).toBe('count');
  expect(metricUnit('rescue_success')).toBe('count');
  expect(metricValueText('rescue_success', 1, 'count', 'en-US')).toBe('Yes');
  expect(metricValueText('rescue_success', 0, 'count', 'en-US')).toBe('No');
  expect(metricValueText('rescue_success', 1, 'count', 'zh-CN')).toBe('是');
  expect(metricValueText('rescue_success', 0, 'count', 'zh-CN')).toBe('否');
  // Null stays an explicit dash, never a fabricated No.
  expect(metricValueText('rescue_success', null, 'count', 'en-US')).toBe('—');
  // A count tally drops its dimensionless unit; seconds and bytes keep theirs.
  expect(metricValueText('rescue_plans', 4, 'count', 'en-US')).toBe('4');
  expect(metricValueText('rescue_time_to_confirmation_s', 12.345, 's', 'en-US')).toBe('12.3 s');
  expect(metricValueText('rescue_radio_bytes', 900, 'B', 'en-US')).toBe('900 B');
});

it('gives descriptor text and units precedence over every legacy ID-based fallback', () => {
  const row: MetricMetadata = {label: 'plugin:custom_label', label_zh: '插件自定义标签', unit: 'ha', direction: null,
    aggregation: 'Sum the observed area.', missing_data_policy: 'None until an area is recorded.'};
  expect(metricLabel('rescue_success', row, 'en-US')).toBe(row.label);
  expect(metricLabel('rescue_success', row, 'zh-CN')).toBe(row.label_zh);
  expect(metricUnit('rescue_success', row)).toBe('ha');
  expect(metricUnit('energy_used_kj', {...row, unit: ''})).toBe('');
  expect(metricHelp('rescue_success', 'en-US', row)).toBe(`Aggregation: ${row.aggregation}\nMissing data: ${row.missing_data_policy}`);
  expect(metricHelp('rescue_success', 'zh-CN', row)).toBe(`汇总方式：${row.aggregation}\n缺失数据：${row.missing_data_policy}`);
  expect(metricDirection(row)).toBe('neutral');
  expect(metricValueText('rescue_success', 0, row.unit, 'zh-CN', row)).toBe('0 ha');
  expect(metricValueText('rescue_success', null, row.unit, 'zh-CN', row)).toBe('—');
});

it('does not fill partial descriptors with legacy help, labels or direction', () => {
  const row: MetricMetadata = {label: 'provider_label', unit: '', direction: null};
  expect(metricLabel('tasks_completed', row, 'zh-CN')).toBe(row.label);
  expect(metricUnit('energy_used_kj', row)).toBe('');
  expect(metricHelp('tasks_completed', 'en-US', row)).toBeUndefined();
  expect(metricDirection(row)).toBe('neutral');
  expect(metricDirection({...row, direction: undefined})).toBe('neutral');
  expect(metricDirection({...row, direction: 'higher'})).toBe('max');
  expect(metricDirection({...row, direction: 'lower'})).toBe('min');
});
