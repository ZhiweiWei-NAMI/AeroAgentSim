import { describe, expect, it, vi } from 'vitest';
import type { RunHeader } from '../contracts/viewer-feed';
import { ConditionsCache, compassPoint, conditionsAt, conditionsFromDraft, formatMmH, formatVisibility, incidentLabel, previewMoment, windFromDeg } from './conditions';

const header = (): RunHeader => ({
  contract: 'aeroagentsim.viewer-feed/v1', runId: 'r', registryDigest: 'd',
  types: [{ typeId: 'lowalt:Incident', displayName: 'Incident', ancestors: ['oo:Incident'] }],
  entities: [{ id: 'fire-a', type: 'lowalt:Incident', label: 'Building fire' }],
  fields: [], presentation: [],
  engines: [{ id: 'weather', plugin: 'environment', gusts: [[60_000_000_000, 150_000_000_000]] }],
  start: { ns: '0', microstep: 0 },
});

function entity(id: string, typeId: string, fields: Record<string, unknown>) {
  const map = new Map(Object.entries(fields).map(([fieldId, value]) => [fieldId, { value }]));
  return { key: { id, generation: 0 }, typeId, fields: map };
}
const cut = (rows: ReturnType<typeof entity>[]) => new Map(rows.map(row => [`${row.key.id}:0`, row]));

describe('conditionsAt', () => {
  it('reads weather profiles, gust intervals, light and incidents from the committed cut', () => {
    const ns = (s: number) => (s * 1e9).toString();
    const conditions = conditionsAt(cut([
      entity('weather', 'lowalt:Environment', {
        'lowalt.wind': [6, 0, 0],
        'lowalt.precipitation': 4 / 3600,
        'lowalt.visibility': 500,
        'lowalt.light': 'dusk',
      }),
      entity('fire-a', 'lowalt:Incident', {
        'lowalt.incident_kind': 'fire', 'lowalt.incident_position': [20, 40, 0], 'lowalt.incident_radius_m': 30,
        'lowalt.incident_time_ns': ns(90), 'lowalt.incident_end_ns': ns(240), 'lowalt.incident_active': true,
        'lowalt.incident_responder': 'uav-1',
      }),
    ]), ns(120), header());
    expect(conditions).toMatchObject({ precipitationMmH: 4, visibilityM: 500, light: 'dusk' });
    expect(conditions.wind).toMatchObject({ speed: 6, fromDeg: 270, gust: true });
    expect(conditions.incidents).toHaveLength(1);
    expect(conditions.incidents[0]).toMatchObject({ id: 'fire-a', kind: 'fire', label: 'Building fire', phase: 'active', radiusM: 30, sinceS: 30, activeS: 30, response: 'uav-1' });
  });
  it('treats absent fields as clear day, 50000 m visibility as clear and marks ended incidents', () => {
    const conditions = conditionsAt(cut([
      entity('fall', 'lowalt:Incident', { 'lowalt.incident_kind': 'person_fall', 'lowalt.incident_position': [0, 0, 0], 'lowalt.incident_time_ns': '0', 'lowalt.incident_end_ns': '60000000000', 'lowalt.incident_active': false }),
    ]), '120000000000');
    expect(conditions).toMatchObject({ wind: null, precipitationMmH: 0, visibilityM: null, light: 'day' });
    expect(conditions.incidents[0]).toMatchObject({ kind: 'person_fall', phase: 'ended' });
    expect(conditions.incidents[0].sinceS).toBeCloseTo(60);
    expect(conditions.incidents[0].activeS).toBe(120);
  });
  it('marks a not-yet-started incident as pending', () => {
    const conditions = conditionsAt(cut([
      entity('fall', 'lowalt:Incident', { 'lowalt.incident_kind': 'fire', 'lowalt.incident_position': [0, 0, 0], 'lowalt.incident_time_ns': '90000000000', 'lowalt.incident_end_ns': '-1', 'lowalt.incident_active': false }),
    ]), '0');
    expect(conditions.incidents[0].phase).toBe('pending');
    expect(conditions.incidents[0].activeS).toBe(0);
  });
  it('derives legacy incidents (no incident_active field) from the committed response state', () => {
    const legacy = (state: string) => cut([
      entity('fire', 'lowalt:Incident', {
        'lowalt.incident_kind': 'fire', 'lowalt.incident_position': [0, 0, 0],
        'lowalt.incident_time_ns': '60000000000', 'lowalt.incident_end_ns': '120000000000', 'lowalt.incident_state': state,
      }),
    ]);
    // A committed non-pending response means it occurred; legacy incidents never end.
    expect(conditionsAt(legacy('occurred'), '90000000000').incidents[0]).toMatchObject({ phase: 'active', sinceS: 30 });
    // Before the start time it stays pending, even with a committed response state.
    expect(conditionsAt(legacy('occurred'), '0').incidents[0].phase).toBe('pending');
    // "pending" response state before the start time stays pending (initial cut).
    expect(conditionsAt(legacy('pending'), '30000000000').incidents[0].phase).toBe('pending');
  });
  it('carries label and label_zh and picks the pin label by locale', () => {
    const localized = header();
    localized.entities = [{ id: 'fire-a', type: 'lowalt:Incident', label: 'Building fire', label_zh: '建筑火情' }];
    const rows = cut([entity('fire-a', 'lowalt:Incident', {
      'lowalt.incident_kind': 'fire', 'lowalt.incident_position': [0, 0, 0],
      'lowalt.incident_time_ns': '0', 'lowalt.incident_active': true,
    })]);
    const row = conditionsAt(rows, '20000000000', localized).incidents[0];
    expect(row).toMatchObject({ label: 'Building fire', labelZh: '建筑火情' });
    expect(incidentLabel(row, true)).toBe('建筑火情 · 已持续 00:20');
    expect(incidentLabel(row, false)).toBe('Building fire · Active 00:20');
    // Unlabelled incidents fall back to the kind's default label.
    const plain = conditionsAt(cut([entity('fire-b', 'lowalt:Incident', {
      'lowalt.incident_kind': 'fire', 'lowalt.incident_position': [0, 0, 0], 'lowalt.incident_time_ns': '0', 'lowalt.incident_active': true,
    })]), '20000000000').incidents[0];
    expect(incidentLabel(plain, false)).toBe('Building fire · Active 00:20');
    expect(incidentLabel(plain, true)).toBe('建筑火情 · 已持续 00:20');
    expect(incidentLabel({ ...plain, phase: 'ended' }, true)).toBe('火情 · 已结束');
  });
  it('formats SI units with bounded precision in both languages', () => {
    expect(formatMmH(4)).toBe('4'); expect(formatMmH(7.6)).toBe('8'); expect(formatMmH(0.6)).toBe('0.6');
    expect(formatVisibility(800)).toBe('800 m'); expect(formatVisibility(2000)).toBe('2 km'); expect(formatVisibility(5000)).toBe('5 km');
    expect(formatVisibility(1500)).toBe('1.5 km');
  });
  it('keeps elapsed time for a zero-start incident and reads oo wind with later engine gusts', () => {
    const feedHeader = header();
    feedHeader.engines = [{ id: 'empty', plugin: 'environment' }, ...feedHeader.engines!];
    const conditions = conditionsAt(cut([
      entity('wind', 'oo:WindField', { 'oo:digital_twin.wind.horizontalVelocity': [[6, -6]] }),
      entity('fire', 'lowalt:Incident', { 'lowalt.incident_kind': 'fire', 'lowalt.incident_position': [0, 0, 0], 'lowalt.incident_time_ns': 0, 'lowalt.incident_end_ns': -1, 'lowalt.incident_active': true }),
    ]), '120000000000', feedHeader);
    expect(conditions.incidents[0]).toMatchObject({ phase: 'active', sinceS: 120, activeS: 120, startS: 0 });
    expect(conditions.wind).toMatchObject({ fromDeg: 315, gust: true, gustEndS: 150 });
    expect(conditionsAt(cut([entity('wind', 'oo:WindField', { 'oo:digital_twin.wind.horizontalVelocity': [[6, -6]] })]), '150000000000', feedHeader).wind?.gust).toBe(false);
  });
  it('keeps activation and end ages distinct for nonzero starts and lossless integer facts', () => {
    const rows = cut([entity('fire', 'lowalt:Incident', {
      'lowalt.incident_kind': 'fire', 'lowalt.incident_position': [0, 0, 0],
      'lowalt.incident_time_ns': { $integer: '90000000000' }, 'lowalt.incident_end_ns': { $integer: '120000000000' }, 'lowalt.incident_active': false,
    })]);
    expect(conditionsAt(rows, '120000000000').incidents[0]).toMatchObject({ phase: 'ended', sinceS: 0, activeS: 30 });
    const row = conditionsAt(rows, '125000000000').incidents[0];
    expect(row).toMatchObject({ phase: 'ended', sinceS: 5, activeS: 35 });
    expect(row.activeS - row.sinceS).toBe(30);
  });
});

describe('conditionsFromDraft', () => {
  const scenario = {
    engines: { weather: { plugin: 'environment', config: { profiles: { weather: {
      'lowalt.wind': { value: [6, 0, 0], gusts: [{ start_ns: 6e10, end_ns: 9e10, value: [9, 0, 0] }] },
      'lowalt.precipitation': { value: 4 / 3600 }, 'lowalt.visibility': { value: 500 },
    } } } } },
    cooperation: { incidents: [{ id: 'fire-a', kind: 'fire', position: [20, 40, 0], at_s: 90, radius_m: 30 }] },
  };
  it('uses half-open gust intervals and incident elapsed time at the preview cut', () => {
    expect(conditionsFromDraft(scenario, 90)).toMatchObject({ precipitationMmH: 4, visibilityM: 500 });
    expect(conditionsFromDraft(scenario, 80).wind).toMatchObject({ speed: 9, gust: true, fromDeg: 270 });
    expect(conditionsFromDraft(scenario, 90).wind).toMatchObject({ speed: 6, gust: false, fromDeg: 270 });
    expect(conditionsFromDraft(scenario, 100).incidents[0]).toMatchObject({ phase: 'active', sinceS: 10, activeS: 10 });
  });
  it('preview moment defaults to the first incident start + 20 s', () => {
    expect(previewMoment(scenario)).toBe(110);
    const outside = conditionsFromDraft(scenario, previewMoment(scenario));
    expect(outside.incidents).toHaveLength(1);
    expect(outside.incidents[0].sinceS).toBe(20);
    expect(conditionsFromDraft({}, 0).wind).toBeNull();
  });
  it('reads incidents from the authored task engine and daylight from journaled profiles', () => {
    const draft = { engines: { ...scenario.engines, tasks: { plugin: 'lowalt_tasks', config: { cooperation: scenario.cooperation } } }, scene: { lighting: 'dusk' } };
    expect(previewMoment(draft)).toBe(110);
    expect(conditionsFromDraft(draft, 110)).toMatchObject({ light: 'day', incidents: [{ id: 'fire-a', phase: 'active', sinceS: 20 }] });
  });
  it('retains activation age after ending and formats phase-aware incident labels', () => {
    const draft = { cooperation: { incidents: [{ ...scenario.cooperation.incidents[0], label: 'Building fire', end_s: 120 }] } };
    const pending = conditionsFromDraft(draft, 80).incidents[0];
    expect(pending).toMatchObject({ phase: 'pending', activeS: 0, startS: 90 });
    expect(incidentLabel(pending, false)).toBe('Fire at 01:30');
    expect(incidentLabel(pending, true)).toBe('火情 01:30 开始');
    const active = conditionsFromDraft(draft, 100).incidents[0];
    expect(incidentLabel(active, false)).toBe('Building fire · Active 00:10');
    expect(incidentLabel(active, true, '建筑火情')).toBe('建筑火情 · 已持续 00:10');
    const ended = conditionsFromDraft(draft, 125).incidents[0];
    expect(ended).toMatchObject({ phase: 'ended', activeS: 35, sinceS: 5 });
    expect(incidentLabel(ended, false)).toBe('Fire · ended');
    expect(incidentLabel(ended, true)).toBe('火情 · 已结束');
  });
});

describe('wind direction helpers', () => {
  it('converts ENU vectors to meteorological from-directions', () => {
    expect(windFromDeg(1, 0)).toBeCloseTo(270); // from west, blowing east
    expect(windFromDeg(0, -1)).toBeCloseTo(0); // from north
    expect(windFromDeg(-1, 1)).toBeCloseTo(135);
    expect(windFromDeg(0, 0)).toBe(270);
    expect(compassPoint(292)).toBe('W');
    expect(compassPoint(315)).toBe('NW');
  });
});


it('caches by exact cut and revision, reuses storage, and invalidates same-time corrections', () => {
  const h = header(), cache = new ConditionsCache();
  const weather = entity('weather', 'lowalt:Environment', { 'lowalt.wind': [6, 0, 0], 'lowalt.precipitation': 4 / 3600 });
  const fire = entity('fire-a', 'lowalt:Incident', { 'lowalt.incident_position': [0,0,0], 'lowalt.incident_kind': 'fire',
    'lowalt.incident_time_ns': '0', 'lowalt.incident_active': true });
  const rows = cut([weather, fire]), c = cache.at(rows, '30000000000', 1, h), wind = c.wind, incident = c.incidents[0];
  const scan = vi.spyOn(rows, 'values');
  for (let i = 0; i < 300; i++) expect(cache.at(rows, '30000000000', 1, h)).toBe(c);
  expect(scan).not.toHaveBeenCalled();
  cache.at(rows, '31000000000', 2, h);
  expect(c.wind).toBe(wind); expect(c.incidents[0]).toBe(incident); expect(incident.sinceS).toBe(31);
  weather.fields.set('lowalt.wind', { value: [0, 9, 0] });
  cache.at(rows, '31000000000', 3, h); expect(c.wind?.north).toBe(9);
  expect(c).toEqual(conditionsAt(rows, '31000000000', h));
  cache.at(rows, '30000000000', 4, h); expect(incident.sinceS).toBe(30);
});

it('retains nanosecond boundary precision beyond safe integer time', () => {
  const start = 9007199254740993n, rows = cut([entity('fire-a', 'lowalt:Incident', {
    'lowalt.incident_position': [0,0,0], 'lowalt.incident_time_ns': start.toString(), 'lowalt.incident_active': true,
  })]);
  expect(conditionsAt(rows, (start - 1n).toString()).incidents[0].phase).toBe('pending');
  expect(conditionsAt(rows, (start + 1n).toString()).incidents[0].sinceS).toBe(1e-9);
});
