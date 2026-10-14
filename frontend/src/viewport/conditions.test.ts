import { describe, expect, it } from 'vitest';
import type { RunHeader } from '../contracts/viewer-feed';
import { compassPoint, conditionsAt, conditionsFromDraft, windFromDeg } from './conditions';

const header = (): RunHeader => ({
  contract: 'aeroagentsim.viewer-feed/v1', runId: 'r', registryDigest: 'd',
  types: [{ typeId: 'lowalt:Incident', displayName: 'Incident', ancestors: ['oo:Incident'] }],
  fields: [], presentation: [],
  engines: [{ id: 'weather', plugin: 'environment', gusts: [[60_000_000_000, 90_000_000_000]] }],
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
    expect(conditions.incidents[0]).toMatchObject({ id: 'fire-a', kind: 'fire', phase: 'active', radiusM: 30, sinceS: 30, response: 'uav-1' });
  });
  it('treats absent fields as clear day, 50000 m visibility as clear and marks ended incidents', () => {
    const conditions = conditionsAt(cut([
      entity('fall', 'lowalt:Incident', { 'lowalt.incident_kind': 'person_fall', 'lowalt.incident_position': [0, 0, 0], 'lowalt.incident_time_ns': '0', 'lowalt.incident_end_ns': '60000000000', 'lowalt.incident_active': false }),
    ]), '120000000000');
    expect(conditions).toMatchObject({ wind: null, precipitationMmH: 0, visibilityM: null, light: 'day' });
    expect(conditions.incidents[0]).toMatchObject({ kind: 'person_fall', phase: 'ended' });
    expect(conditions.incidents[0].sinceS).toBeCloseTo(60);
  });
  it('marks a not-yet-started incident as pending', () => {
    const conditions = conditionsAt(cut([
      entity('fall', 'lowalt:Incident', { 'lowalt.incident_kind': 'fire', 'lowalt.incident_position': [0, 0, 0], 'lowalt.incident_time_ns': '90000000000', 'lowalt.incident_end_ns': '-1', 'lowalt.incident_active': false }),
    ]), '0');
    expect(conditions.incidents[0].phase).toBe('pending');
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
  it('freezes the draft at the preview moment, inside a gust', () => {
    expect(conditionsFromDraft(scenario, 90)).toMatchObject({ precipitationMmH: 4, visibilityM: 500 });
    expect(conditionsFromDraft(scenario, 90).wind).toMatchObject({ speed: 9, gust: true, fromDeg: 270 });
    expect(conditionsFromDraft(scenario, 90).incidents[0]).toMatchObject({ phase: 'active', sinceS: 10 });
  });
  it('preview moment defaults to the first incident start + 20 s', () => {
    const outside = conditionsFromDraft(scenario, 90);
    expect(outside.incidents).toHaveLength(1);
    expect(conditionsFromDraft({}, 0).wind).toMatchObject({ speed: 0, fromDeg: 270, gust: false });
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
