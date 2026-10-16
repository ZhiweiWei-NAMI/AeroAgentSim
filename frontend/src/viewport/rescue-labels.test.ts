import { describe, expect, it } from 'vitest';
import { semanticMarkers, semanticLinks, rescueLinkColor, rescueUavLabel, rescueLabel, semanticVehicle } from './semantic';
import type { RunHeader } from '../contracts/viewer-feed';
import type { EntityState } from './feed-store';

const header: RunHeader = {
  contract: 'aeroagentsim.viewer-feed/v1', runId: 'r', registryDigest: 'd',
  types: [{ typeId: 'lowalt:UAV', displayName: 'UAV', ancestors: [] }],
  fields: [], presentation: [], start: { ns: '0', microstep: 0 },
};

function entity(id: string, typeId: string, fields: Record<string, unknown>): EntityState {
  const map = new Map<string, { value: unknown } & Record<string, unknown>>();
  for (const [fieldId, value] of Object.entries(fields)) map.set(fieldId, { value } as { value: unknown } & Record<string, unknown>);
  return { key: { id, generation: 0 }, typeId, fields: map as unknown as EntityState['fields'] };
}

function store(entities: EntityState[]) {
  return { header, entities: new Map(entities.map(item => [`${item.key.id}:${item.key.generation}`, item])), edges: new Map() };
}

describe('C1 rescue.markers parsing', () => {
  it('parses inspect/relay/command markers from the committed field', () => {
    const markers = semanticMarkers(store([entity('env', 'lowalt:Environment', {
      'rescue.markers': [
        { id: 'inspect-1', kind: 'inspect', position: [10, 2, 30] },
        { id: 'relay-1', kind: 'relay', position: [0, 40, 0] },
        { id: 'command', kind: 'command', position: [-5, 0, 12] },
      ],
    })]));
    expect(markers).toEqual([
      { id: 'inspect-1', kind: 'inspect', position: [10, 2, 30] },
      { id: 'relay-1', kind: 'relay', position: [0, 40, 0] },
      { id: 'command', kind: 'command', position: [-5, 0, 12] },
    ]);
  });
  it('throws on malformed marker records instead of repairing them', () => {
    const bad = store([entity('env', 'lowalt:Environment', { 'rescue.markers': [{ id: 'x', kind: 'inspect', position: [1, 2] }] })]);
    expect(() => semanticMarkers(bad)).toThrow('rescue marker x position');
    const unknown = store([entity('env', 'lowalt:Environment', { 'rescue.markers': [{ id: 'x', kind: 'beacon', position: [1, 2, 3] }] })]);
    expect(() => semanticMarkers(unknown)).toThrow('beacon');
  });
});

describe('C1 rescue.links parsing', () => {
  it('parses every hop and state, including delivered links kept for the faded view', () => {
    const links = semanticLinks(store([entity('env', 'lowalt:Environment', {
      'rescue.links': [
        { id: 'l1', from: 'uav-2', to: 'uav-relay-1', kind: 'report', state: 'sending' },
        { id: 'l2', from: 'uav-relay-1', to: 'centre', kind: 'report', state: 'delivered' },
        { id: 'l3', from: 'uav-relay-1', to: 'centre', kind: 'relay_coverage', state: 'holding' },
        { id: 'l4', from: 'uav-2', to: 'centre', kind: 'delivery_ack', state: 'failed' },
      ],
    })]));
    expect(links).toHaveLength(4);
    expect(links[1]).toMatchObject({ from: 'uav-relay-1', to: 'centre', state: 'delivered' });
  });
  it('throws on unknown kinds or states', () => {
    const bad = store([entity('env', 'lowalt:Environment', { 'rescue.links': [{ id: 'l', from: 'a', to: 'b', kind: 'telemetry', state: 'sending' }] })]);
    expect(() => semanticLinks(bad)).toThrow('telemetry');
    const badState = store([entity('env', 'lowalt:Environment', { 'rescue.links': [{ id: 'l', from: 'a', to: 'b', kind: 'report', state: 'queued' }] })]);
    expect(() => semanticLinks(badState)).toThrow('queued');
  });
  it('colors report amber, ack green, relay coverage in the relay role colour and failed red', () => {
    expect(rescueLinkColor({ kind: 'report', state: 'sending' })).toBe('#f0a020');
    expect(rescueLinkColor({ kind: 'delivery_ack', state: 'sending' })).toBe('#3fb67a');
    expect(rescueLinkColor({ kind: 'relay_coverage', state: 'holding' })).toBe('#cc79a7');
    expect(rescueLinkColor({ kind: 'report', state: 'failed' })).toBe('#e5484d');
    expect(rescueLinkColor({ kind: 'report', state: 'delivered' })).toBe('#f0a020');
  });
});

describe('UAV rescue labels never expose action ids', () => {
  const uav = entity('U3', 'lowalt:UAV', {});
  it('shows id, role and status words only', () => {
    const vehicle = semanticVehicle(uav, undefined);
    vehicle.rescueRole = 'relay'; vehicle.rescueStatus = 'holding';
    expect(rescueUavLabel(vehicle)).toBe('U3 · Relay · Holding');
    expect(rescueUavLabel(vehicle, 'zh-CN')).toBe('U3 · 中继 · 驻留');
    vehicle.rescueTask = 'R7/a3';
    expect(rescueUavLabel(vehicle)).not.toContain('R7/a3');
  });
  it('collapses equal role and status words to one word', () => {
    const vehicle = semanticVehicle(uav, undefined);
    vehicle.rescueRole = 'idle'; vehicle.rescueStatus = 'idle';
    expect(rescueUavLabel(vehicle)).toBe('U1 · Idle'.replace('U1', 'U3'));
  });
  it('reads the committed role as "On existing mission"', () => {
    const vehicle = semanticVehicle(uav, undefined);
    vehicle.rescueRole = 'committed'; vehicle.rescueStatus = 'continuing';
    expect(rescueUavLabel(vehicle)).toBe('U3 · On existing mission · Continuing');
    expect(rescueUavLabel(vehicle, 'zh-CN')).toBe('U3 · 执行原任务 · 执行中');
    expect(rescueLabel('committed')).toBe('On existing mission');
  });
});
