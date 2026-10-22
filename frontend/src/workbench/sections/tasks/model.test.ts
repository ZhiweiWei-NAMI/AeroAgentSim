import { describe, expect, it } from 'vitest';
import { engineOf, clockS, handoffPointIds, nsToS, orderStreamId, pointDeg, pointOfDeg, scalarNumber, siteIds, taskConfig, vehicleIds, weatherOf, windDirection, windFromDeg, windSpeed } from './model';
import { addOrdersParams, cooperationParams, gustProblem, incidentProblem, rainPresetOf, unknownEndpoints, visibilityPresetOf, weatherParams } from './ops';

const draft = {
  entities: [
    { id: 'kitchen-1', type: 'lowalt:Restaurant', facts: { 'lowalt.site_position': [0, 0, 0] } },
    { id: 'courier-1', type: 'lowalt:UAV', facts: { 'lowalt.soc': 90 } },
    { id: 'handoff-1', type: 'lowalt:HandoffPoint', facts: { 'lowalt.site_position': [10, 0, 0] } },
  ],
  engines: { weather: { plugin: 'environment' }, delivery: { plugin: 'lowalt_tasks', config: { generators: [{ id: 'parcels', kind: 'parcel' }] } } },
};

describe('real form sources', () => {
  it('finds engines by their runtime plugin', () => {
    expect(engineOf(draft, 'environment')?.name).toBe('weather');
    expect(taskConfig(draft)?.generators).toHaveLength(1);
    expect(engineOf(draft, 'sumo')).toBeUndefined();
    expect(taskConfig({})).toBeUndefined();
  });

  it('does not infer low-altitude tasks from an engine key named tasks', () => {
    const generic = { engines: { tasks: { plugin: 'sumo', config: { generators: [{ id: 'road-orders' }] } } } };
    expect(taskConfig(generic)).toBeUndefined();
    expect(engineOf(generic, 'lowalt_tasks')).toBeUndefined();
    const installed = { engines: { dispatch: { plugin: 'lowalt_tasks', config: { generators: [{ id: 'air-orders' }] } } } };
    expect(engineOf(installed, 'lowalt_tasks')?.name).toBe('dispatch');
    expect(taskConfig(installed)?.generators).toHaveLength(1);
  });

  it('reads numeric tags and wind vectors without treating missing values as zero', () => {
    expect(nsToS(1_500_000_000)).toBe(1.5);
    expect(nsToS({ $integer: '1500000000' })).toBe(1.5);
    expect(windSpeed([3, 4, 0])).toBe(5);
    expect(windDirection([0, 5, 0])).toBe(90);
    expect(windDirection([0, 0, 0])).toBeUndefined();
    expect(windSpeed([null, null, 0])).toBeUndefined();
    for (const invalid of [undefined, null, '', false, { $number: '' }, { $number: 'NaN' }]) expect(scalarNumber(invalid)).toBeUndefined();
  });

  it('lists facilities, handoff points and actual authored/generated UAV members separately', () => {
    expect(siteIds(draft)).toEqual(['kitchen-1', 'handoff-1']);
    expect(handoffPointIds(draft)).toEqual(['handoff-1']);
    expect(vehicleIds({ ...draft, generators: { uavs: { type: 'lowalt:UAV', count: 2, prefix: 'drone-' } } })).toEqual(['courier-1', 'drone-1', 'drone-2']);
    expect(vehicleIds({ generators: { bad: { type: 'lowalt:UAV', count: null, prefix: 'drone-' } } })).toEqual([]);
  });
});

describe('template workload identities (T4-R2-011)', () => {
  it.each([
    [{ id: 'explicit', tasks: ['meal-1', 'meal-2'] }, 'explicit'],
    [{ kind: 'food', tasks: ['meal-1', 'meal-2', 'meal-6'] }, 'meal'],
    [{ tasks: ['survey.1', 'survey.2'] }, 'survey'],
    [{ tasks: ['only-1'] }, 'only-1'],
    [{ tasks: ['one-1', 'two-2'] }, 'one-1'],
    [{ kind: 'food', tasks: [] }, 'food'],
  ])('matches the server workload selector for %j', (stream, expected) => expect(orderStreamId(stream)).toBe(expected));
  it.each([{ id: undefined, tasks: ['meal-1', 'meal-2'] }, { id: null }, { id: '' }, {}, { tasks: [null] }])('does not invent an ID for %j', stream => {
    expect(orderStreamId(stream)).toBeUndefined();
  });
});

describe('C11 operation parameters', () => {
  it('keeps exact scheduled times, selected facilities and optional fields', () => {
    expect(addOrdersParams({ id: 'rush', kind: 'food', count: 2, from: ['kitchen-1'], to: ['customer'], release: { mode: 'scheduled', times_s: [0, 1.5] }, priority: 2, payload_kg: 1.5, delivery_window_s: 240 })).toMatchObject({
      from: ['kitchen-1'], to: ['customer'], release: { mode: 'scheduled', times_s: [0, 1.5] }, priority: 2, payload_kg: 1.5, delivery_window_s: 240,
    });
    expect(unknownEndpoints({ from: ['kitchen-1'], to: ['ghost'] }, siteIds(draft))).toEqual(['ghost']);
  });

  it('includes handoff assignments only when requested and keeps current assignments otherwise', () => {
    expect(cooperationParams({ watchers: { vehicles: ['u1'], notice_radius_m: 120 }, handoff_points: ['handoff-1'] })).toEqual({ watchers: { vehicles: ['u1'], notice_radius_m: 120 }, handoff_points: ['handoff-1'] });
    expect(cooperationParams({ helpers: { mode: 'agent', goal: 'Help nearby flights' } })).toEqual({ helpers: { mode: 'agent', goal: 'Help nearby flights' } });
    expect(cooperationParams({ handoff_points: [] })).toEqual({ handoff_points: [] });
    expect(cooperationParams({})).toEqual({});
  });

  it('rejects invalid gust times and wind speeds without numeric coercion', () => {
    expect(gustProblem({ start_s: '1.5', end_s: '2.5', speed_mps: '9', duration_s: 360 }, false)).toBeUndefined();
    expect(gustProblem({ start_s: '90', end_s: '60', speed_mps: '9', duration_s: 360 }, false)?.end_s).toMatch(/after start/);
    expect(gustProblem({ start_s: '300', end_s: '400', speed_mps: '9', duration_s: 360 }, false)?.end_s).toMatch(/360/);
    expect(gustProblem({ start_s: '1', end_s: '2', speed_mps: '-1', duration_s: 360 }, true)?.speed_mps).toContain('请输入');
    expect(gustProblem({ start_s: '0x10', end_s: '20', speed_mps: '9', duration_s: 360 }, false)?.start_s).toBeTruthy();
  });

  it('validates incident radius and end against start and the run horizon', () => {
    expect(incidentProblem({ time_s: '90', end_s: '240', radius_m: '30', duration_s: 360 }, false)).toEqual({});
    expect(incidentProblem({ time_s: '90', end_s: '60', radius_m: '30' }, false)?.end_s).toMatch(/after start/);
    expect(incidentProblem({ time_s: '90', end_s: '400', radius_m: '30', duration_s: 360 }, false)?.end_s).toMatch(/360/);
    expect(incidentProblem({ time_s: '90', radius_m: '900' }, false)?.radius_m).toMatch(/500/);
    expect(incidentProblem({ time_s: '90', radius_m: '0' }, false)?.radius_m).toBeTruthy();
    // A blank or non-numeric radius is a hard error; a blank end stays optional.
    expect(incidentProblem({ time_s: '90', radius_m: '' }, false)?.radius_m).toMatch(/Required/);
    expect(incidentProblem({ time_s: '90', radius_m: 'wide' }, false)?.radius_m).toMatch(/finite number/);
    expect(incidentProblem({ time_s: '90', end_s: 'late' }, false)?.end_s).toMatch(/finite number/);
    expect(incidentProblem({ time_s: '90', radius_m: '900' }, true)?.radius_m).toContain('不能大于');
    expect(incidentProblem({ time_s: '90', radius_m: '30', end_s: '' }, false)).toEqual({});
  });

  it('maps weather presets to one explicit set_weather parameter set', () => {
    expect(weatherParams({ rain: 'moderate', visibility: 'fog', light: 'day', rainCustom: '', visibilityCustom: '' })).toEqual({ precipitation_mm_h: 4, visibility_m: 500, light: 'day' });
    expect(weatherParams({ rain: 'none', visibility: 'clear', light: 'dusk', rainCustom: '', visibilityCustom: '' })).toEqual({ precipitation_mm_h: 0, visibility_m: null, light: 'dusk' });
    expect(weatherParams({ rain: 'custom', visibility: 'custom', light: 'day', rainCustom: '7.5', visibilityCustom: '800' })).toEqual({ precipitation_mm_h: 7.5, visibility_m: 800, light: 'day' });
    expect(weatherParams({ rain: 'custom', visibility: 'fog', light: 'day', rainCustom: 'wet', visibilityCustom: '' })).toBeUndefined();
    expect(rainPresetOf(0)).toBe('none'); expect(rainPresetOf(1)).toBe('light'); expect(rainPresetOf(4)).toBe('moderate');
    expect(rainPresetOf(10)).toBe('heavy'); expect(rainPresetOf(6)).toBe('custom');
    expect(visibilityPresetOf(null)).toBe('clear'); expect(visibilityPresetOf(50000)).toBe('clear');
    expect(visibilityPresetOf(5000)).toBe('haze'); expect(visibilityPresetOf(2000)).toBe('mist');
    expect(visibilityPresetOf(500)).toBe('fog'); expect(visibilityPresetOf(150)).toBe('dense'); expect(visibilityPresetOf(800)).toBe('custom');
  });

  it('derives meteorological wind direction, presets and clocks from the draft', () => {
    expect(windFromDeg([4.24, -4.24, 0])).toBe(315);
    expect(windFromDeg([6, 0, 0])).toBe(270);
    expect(windFromDeg([0, 0, 0])).toBe(270);
    expect(pointDeg('NW')).toBe(315); expect(pointOfDeg(225)).toBe('SW'); expect(pointOfDeg(350)).toBe('N');
    expect(weatherOf({ config: { profiles: { weather: { 'lowalt.precipitation': { mode: 'constant', value: 4 / 3600 } } } } }))
      .toEqual({ precipitationMmH: 4, visibilityM: null, light: 'day' });
    expect(weatherOf({ config: { profiles: { weather: { 'lowalt.visibility': { mode: 'constant', value: 50000 }, 'lowalt.light': { mode: 'constant', value: 'dusk' } } } } }))
      .toEqual({ precipitationMmH: 0, visibilityM: null, light: 'dusk' });
    expect(weatherOf({})).toEqual({ precipitationMmH: 0, visibilityM: null, light: 'day' });
    expect(clockS(90)).toBe('01:30'); expect(clockS(240)).toBe('04:00'); expect(clockS(undefined)).toBe('—');
  });
});
