import { describe, expect, it } from 'vitest';
import { addGust, engineOf, newCooperation, newOrderStream, removeGust, taskConfig, windDirection, windSpeed, nsToS } from './model';

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
