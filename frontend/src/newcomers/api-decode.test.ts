import {expect, it} from 'vitest';
import {decodeAlgorithms, decodeComparison, decodeKpis, decodeTemplates} from './api-decode';
import {validateBusinessEvents, validateKpiValues} from './validation';
import {businessProjection} from './projection';
import {emptyCommit} from '../behaviours/extension-fixture';

const template = {
  format: 'aeroagentsim.template/v1', id: 'bilingual', title: 'Delivery', title_zh: '配送',
  summary: 'Deliver parcels.', summary_zh: '配送包裹。', category: 'logistics', difficulty: 'beginner', duration_s: 30, scenario: 'delivery.yaml',
  observe: ['Watch deliveries.'], observe_zh: ['观察配送进度。'], try: ['Change the mode.'], try_zh: ['调整配送方式。'], kpis: ['tasks_completed'],
  knobs: [{id: 'mode', label: 'Mode', label_zh: '配送方式', description: 'Choose a mode.', description_zh: '选择配送方式。', type: 'choice', default: 'normal', options: ['normal', 'rush'], options_zh: ['普通', '加急']}],
};

it('preserves valid additive template fields and aligned option labels without rewriting typed values', () => {
  const decoded = decodeTemplates([template])[0];
  expect(decoded).toEqual(template);
  expect(decoded.knobs[0].options).toEqual(['normal', 'rush']);
  expect(decoded.knobs[0].options_zh).toEqual(['普通', '加急']);
  expect(decoded.knobs[0].default).toBe('normal');
  const legacy = {...template, title_zh: undefined, summary_zh: undefined, observe_zh: undefined, try_zh: undefined, knobs: [{...template.knobs[0], label_zh: undefined, description_zh: undefined, options_zh: undefined}]};
  expect(decodeTemplates([legacy])[0].title).toBe('Delivery');
});

it('rejects malformed optional translations rather than exposing invalid values to React', () => {
  expect(() => decodeTemplates([{...template, title_zh: 123}])).toThrow('translated text');
  expect(() => decodeTemplates([{...template, observe_zh: ['okay', false]}])).toThrow('translated list');
  expect(() => decodeTemplates([{...template, knobs: [{...template.knobs[0], options_zh: ['普通']}]}])).toThrow('translated list');
  expect(() => decodeAlgorithms({allocation: [{id: 'nearest', label: 'Nearest', description: 'Closest.', label_zh: false}]})).toThrow('translated text');
});

it('preserves algorithm labels/descriptions and comparison titles, with technical ids unchanged', () => {
  const descriptor = {id: 'nearest', label: 'Nearest drone', label_zh: '最近的无人机', description: 'Choose the nearest available drone.', description_zh: '选择最近的空闲无人机。'};
  expect(decodeAlgorithms({allocation: [descriptor]})).toEqual([{...descriptor, slot: 'allocation'}]);
  const run = {id: 'r-1', title: 'Delivery run', title_zh: '配送运行', template_id: 'bilingual', knobs: {mode: 'normal', enabled: false, count: 2}, algorithms: {allocation: 'nearest'}, kpis: {tasks_completed: {value: 4, unit: 'count', label: 'Delivered', label_zh: '已送达'}}};
  expect(decodeComparison({runs: [run]}).runs[0]).toEqual(run);
});

it('retains Chinese business narratives through committed-cut projection without mutating originals', () => {
  const event = {at_ns: '10', category: 'logistics', severity: 'notice', text: 'A parcel arrived.', text_zh: '包裹已送达。', entities: ['drone.1']};
  const events = validateBusinessEvents([event]);
  expect(events[0]).toEqual(event);
  const first = {...emptyCommit(1), at: {ns: '10', microstep: 0}, events};
  const second = {...emptyCommit(2), at: {ns: '10', microstep: 1}, events: [{...events[0], text_zh: '后续事件。'}]};
  expect(businessProjection([first, second], 1, '10').events.map(row => row.text_zh)).toEqual(['包裹已送达。']);
  expect(event.text).toBe('A parcel arrived.');
  expect(() => validateBusinessEvents([{...event, text_zh: {text: 'bad'}}])).toThrow('Expected text');
  expect(validateKpiValues({tasks_completed: {value: null, unit: 'count', label: 'Delivered', label_zh: '已送达'}}).tasks_completed.value).toBeNull();
});

it.each(['higher', 'lower', null])('preserves additive KPI descriptor fields in final, series and comparison rows (%s)', direction => {
  const row = {value: null, unit: 'm', label: 'Measured distance', label_zh: '已测距离', direction,
    aggregation: 'Sum observed segments.', missing_data_policy: 'None before a segment; zero is measured.'};
  const values = {'external.distance': row};
  const legacy = {value: 0, unit: 'count', label: 'Legacy count'};
  const payload = {run_id: 'r-1', final: {...values, legacy}, series: [{at_ns: '10', kpis: values}]};
  const before = JSON.stringify(payload);
  const decoded = decodeKpis(payload);
  expect(decoded.final).toEqual(payload.final);
  expect(decoded.series[0].kpis).toEqual(values);
  expect(decoded.final.legacy).not.toHaveProperty('direction');
  expect(decodeComparison({runs: [{id: 'r-1', title: 'Run', knobs: {}, algorithms: {}, kpis: values}]}).runs[0].kpis).toEqual(values);
  expect(JSON.stringify(payload)).toBe(before);
});

it.each([
  {label_zh: false}, {aggregation: {}}, {missing_data_policy: null},
  {direction: 'up'}, {direction: 0}, {direction: ['higher']},
])('rejects malformed descriptor fields rather than exposing them as UI copy: %j', fields => {
  expect(() => validateKpiValues({external: {value: 0, unit: 'm', label: 'Distance', ...fields}})).toThrow();
});

it.each(['min', 'max', 'neutral'])('retains the legacy direction alias %s without rewriting it', direction => {
  const row = {value: 0, unit: 'm', label: 'Distance', direction};
  expect(validateKpiValues({external: row})).toEqual({external: row});
});
