import { describe, expect, it } from 'vitest';
import { addGust, engineOf, newCooperation, newOrderStream, removeGust, taskConfig, windDirection, windSpeed, nsToS } from './model';
import { addOrdersParams, cooperationParams, gustProblem, setWindOp, gustOp, unknownEndpoints } from './ops';
import { operation } from '../operations';

const draft = {
  run: { until_ns: 360_000_000_000 },
  entities: [
    { id: 'kitchen-1', type: 'lowalt:Restaurant', facts: { 'lowalt.site_position': [0, 0, 0] } },
    { id: 'courier-1', type: 'lowalt:UAV', facts: { 'lowalt.soc': 90 } },
  ],
  engines: {
    weather: { plugin: 'environment', config: { profiles: { weather: { 'lowalt.wind': { mode: 'constant', value: [2, 0, 0] } } } } },
    tasks: { plugin: 'lowalt_tasks', config: { generators: [{ id: 'parcels', kind: 'parcel' }] } },
  },
};

describe('tasks/shared model helpers', () => {
  it('finds engines by runtime plugin, not authored key', () => {
    expect(engineOf(draft, 'environment')?.name).toBe('weather');
    expect(taskConfig(draft)?.generators).toHaveLength(1);
    expect(engineOf(draft, 'sumo')).toBeUndefined();
    expect(taskConfig({})).toBeUndefined();
  });

  it('adds and removes gusts as exact nanosecond intervals like assistant add_gust', () => {
    const base = { mode: 'constant', value: [2, 0, 0] };
    const withGust = addGust(base, { start_s: 170, end_s: 200, speed_mps: 14, direction_deg: 90 });
    expect(withGust.mode).toBe('gust');
    expect(withGust.gusts).toEqual([{ start_ns: 170_000_000_000, end_ns: 200_000_000_000, value: [0, 14, 0] }]);
    expect(removeGust(withGust, 0).gusts).toEqual([]);
    expect(removeGust(withGust, 0).mode).toBe('gust');
  });

  it('derives speed and direction from a wind vector', () => {
    expect(windSpeed([3, 4, 0])).toBe(5);
    expect(windDirection([0, 5, 0])).toBe(90);
    expect(windDirection([0, 0, 0])).toBeUndefined();
    expect(windSpeed('x')).toBeUndefined();
    expect(nsToS(1_500_000_000)).toBe(1.5);
    expect(nsToS(undefined)).toBeUndefined();
  });

  it('creates order streams and the compiler cooperation skeleton', () => {
    const stream = newOrderStream('rush', 'food', 2, 360_000_000_000);
    expect(stream.tasks).toEqual(['rush-1', 'rush-2']);
    expect(stream.arrivals.mode).toBe('poisson');
    expect(stream.delivery_window_s).toBe(240);
    expect(newOrderStream('p', 'parcel', 1, 0).delivery_window_s).toBeUndefined();
    const coop = newCooperation();
    expect(coop.responder.order).toContain('request_handoff');
    expect(coop.helpers.selection).toBe('nearest');
    expect(coop.watchers.notice_radius_m).toBeGreaterThan(0);
  });
});

describe('C11 operation param builders', () => {
  it('add_orders sends only staged fields and keeps the delivery window food-only', () => {
    const params = addOrdersParams({
      id: 'rush', kind: 'food', count: 2, from: ['kitchen-1'], to: ['cust'],
      release: { mode: 'scheduled', times_s: [0, 30] }, priority: 2, payload_kg: 3, delivery_window_s: 240,
    });
    expect(params).toEqual({
      id: 'rush', kind: 'food', count: 2, from: ['kitchen-1'], to: ['cust'],
      release: { mode: 'scheduled', times_s: [0, 30] }, priority: 2, payload_kg: 3, delivery_window_s: 240,
    });
    const parcel = addOrdersParams({ id: 'p', kind: 'parcel', count: 1, from: ['a'], to: ['b'], release: { mode: 'poisson', rate_per_s: 0.1, start_s: 0, end_s: 60 }, delivery_window_s: 99 });
    expect('delivery_window_s' in parcel).toBe(false);
  });

  it('flags endpoints that are not existing places', () => {
    expect(unknownEndpoints({ from: ['a'], to: ['b'] }, ['a', 'b'])).toEqual([]);
    expect(unknownEndpoints({ from: ['a'], to: ['ghost'] }, ['a', 'b'])).toEqual(['ghost']);
  });

  it('cooperation params carry only the picked parts', () => {
    expect(cooperationParams({ watchers: { vehicles: ['u1'], notice_radius_m: 120 } })).toEqual({
      watchers: { vehicles: ['u1'], notice_radius_m: 120 },
    });
    expect(cooperationParams({ helpers: { mode: 'agent' } })).toEqual({ helpers: { mode: 'agent' } });
    expect(cooperationParams({})).toEqual({});
  });

  it('set_wind takes speed only; add_gust keeps the three staged numbers', () => {
    expect(setWindOp(4)).toEqual({ id: expect.any(String), op: 'set_wind', params: { speed_mps: 4 } });
    expect(gustOp({ start_s: 60, end_s: 90, speed_mps: 9 }).op).toBe('add_gust');
    expect(gustOp({ start_s: 60, end_s: 90, speed_mps: 9 }).params).toEqual({ start_s: 60, end_s: 90, speed_mps: 9 });
    expect(operation('set_duration', { seconds: 360 }).op).toBe('set_duration');
  });

  it('gust validation refuses inverted windows, out-of-horizon ends and negative speed', () => {
    expect(gustProblem({ start_s: '60', end_s: '90', speed_mps: '9', duration_s: 360 }, false)).toBeUndefined();
    expect(gustProblem({ start_s: '90', end_s: '60', speed_mps: '9', duration_s: 360 }, false)?.end_s).toBeTruthy();
    expect(gustProblem({ start_s: '300', end_s: '400', speed_mps: '9', duration_s: 360 }, false)?.end_s).toMatch(/360/);
    expect(gustProblem({ start_s: '10', end_s: '20', speed_mps: '-1', duration_s: 360 }, false)?.speed_mps).toBeTruthy();
    expect(gustProblem({ start_s: '', end_s: '20', speed_mps: '1', duration_s: 360 }, false)?.start_s).toBeTruthy();
    expect(gustProblem({ start_s: '10', end_s: '20', speed_mps: '1', duration_s: undefined }, false)).toBeUndefined();
  });
});
