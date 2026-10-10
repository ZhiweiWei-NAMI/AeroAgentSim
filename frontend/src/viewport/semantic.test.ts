import { describe, expect, it } from 'vitest';
import { activeZones, semanticVehicle, businessStyle, displayName, humanizeId, labelInterest, recentlyInvolved } from './semantic';
import type { RunHeader, EntityKey } from '../contracts/viewer-feed';
import type { EntityState } from './feed-store';

const header: RunHeader = {
  contract: 'aeroagentsim.viewer-feed/v1', runId: 'r', registryDigest: 'd',
  types: [
    { typeId: 'lowalt:AirspaceZone', displayName: 'Airspace zone', ancestors: ['oo:ModelObject'] },
    { typeId: 'lowalt:UAV', displayName: 'Drone', ancestors: ['oo:UAV'] },
    { typeId: 'lowalt:Vertiport', displayName: 'Vertiport', ancestors: ['lowalt:Site', 'oo:Facility'] },
  ],
  fields: [{ fieldId: 'lowalt.zone_kind', displayName: 'Zone kind', valueType: 'string', schema: { type: 'string', enum: ['no_fly', 'restricted', 'altitude_band', 'corridor'] } }],
  presentation: [],
  start: { ns: '0', microstep: 0 },
};

function entity(id: string, generation: number, typeId: string, fields: Record<string, unknown>): EntityState {
  const map = new Map<string, { value: unknown } & Record<string, unknown>>();
  for (const [fieldId, value] of Object.entries(fields)) map.set(fieldId, { value } as { value: unknown } & Record<string, unknown>);
  return { key: { id, generation }, typeId, fields: map as unknown as EntityState['fields'] };
}

function store(entities: EntityState[]) {
  return { entities: new Map(entities.map(item => [`${item.key.id}:${item.key.generation}`, item])), edges: new Map() };
}

describe('active airspace zones from committed facts', () => {
  it('keeps routine background updates on hover, while selected, active and alerted actors remain labelled', () => {
    const background=entity('vehicle.005',0,'aas:TrafficRoadVehicle',{'traffic.actor.role':'background'});
    const involved=new Set(['vehicle.005:0']);
    expect(labelInterest(background,undefined,involved)).toBe(false);
    expect(labelInterest(background,background.key,involved)).toBe(true);
    // Even a routine task does not earn a label; an actual alert does.
    expect(labelInterest(background,undefined,involved,true)).toBe(false);
    expect(labelInterest(background,undefined,involved,false,involved)).toBe(true);
    expect(humanizeId('uav.bravo','UAV')).toBe('UAV Bravo');
  });
  it('returns only active zones with real bounds and validates against the registry enum', () => {
    const zones = activeZones(header, store([
      entity('central-no-fly', 0, 'lowalt:AirspaceZone', {
        'lowalt.zone_kind': 'no_fly', 'lowalt.zone_lower': [-55, -55, 0], 'lowalt.zone_upper': [55, 55, 120],
        'lowalt.zone_active': true, 'lowalt.zone_dynamic': false, 'lowalt.zone_start_ns': '0', 'lowalt.zone_end_ns': '300000000000',
      }),
      entity('quiet-zone', 0, 'lowalt:AirspaceZone', {
        'lowalt.zone_kind': 'restricted', 'lowalt.zone_lower': [0, 0, 0], 'lowalt.zone_upper': [10, 10, 30], 'lowalt.zone_active': false,
      }),
    ]));
    expect(zones).toHaveLength(1);
    expect(zones[0]).toMatchObject({ id: 'central-no-fly', kind: 'no_fly', lower: [-55, -55, 0], upper: [55, 55, 120], startNs: '0' });
  });
  it('throws on malformed zone data instead of repairing it', () => {
    const broken = store([entity('bad-zone', 0, 'lowalt:AirspaceZone', {
      'lowalt.zone_kind': 'no_fly', 'lowalt.zone_lower': [-55, -55], 'lowalt.zone_upper': [55, 55, 120], 'lowalt.zone_active': true,
    })]);
    expect(() => activeZones(header, broken)).toThrow('bad-zone');
    const unknown = store([entity('odd-zone', 0, 'lowalt:AirspaceZone', {
      'lowalt.zone_kind': 'blast_radius', 'lowalt.zone_lower': [0, 0, 0], 'lowalt.zone_upper': [1, 1, 1], 'lowalt.zone_active': true,
    })]);
    expect(() => activeZones(header, unknown)).toThrow('blast_radius');
    const inactive = store([entity('lazy-zone', 0, 'lowalt:AirspaceZone', { 'lowalt.zone_kind': 'no_fly', 'lowalt.zone_lower': [0, 0, 0], 'lowalt.zone_upper': [1, 1, 1] })]);
    expect(() => activeZones(header, inactive)).toThrow('lazy-zone');
  });
});

describe('semantic identity and business style from actual fields', () => {
  it('uses authored names first and humanizes opaque ids without inventing semantics', () => {
    const named = entity('uav-3', 0, 'lowalt:UAV', { 'oo:name': 'Courier 7' });
    expect(displayName(named, header)).toBe('Courier 7');
    const unnamed = entity('vertiport-1', 0, 'lowalt:Vertiport', {});
    expect(displayName(unnamed, header)).toBe('Vertiport 1');
    expect(humanizeId('vertiport-north', 'lowalt:Vertiport')).toBe('Vertiport North');
  });
  it('derives business style from the vehicle class or current task, never both forged', () => {
    const drone = entity('uav-3', 0, 'lowalt:UAV', { 'lowalt.vehicle_class': 'parcel', 'lowalt.soc': 0.42 });
    expect(businessStyle(semanticVehicle(drone))).toMatchObject({ glyph: 'package', color: '#e69f00', label: 'Parcel' });
    const medicalTask = entity('task-9', 0, 'lowalt:Task', { 'lowalt.task_kind': 'medical' });
    const plainDrone = entity('uav-4', 0, 'lowalt:UAV', { 'lowalt.vehicle_class': 'parcel' });
    expect(businessStyle(semanticVehicle(plainDrone, medicalTask)).label).toBe('Medical');
    const mystery = entity('uav-5', 0, 'lowalt:UAV', {});
    expect(businessStyle(semanticVehicle(mystery)).glyph).toBe('unknown');
    expect(semanticVehicle(mystery).vehicleClass).toBeUndefined();
  });
});

describe('simple-mode label interest', () => {
  it('labels selected, task-active or recently event-involved entities only', () => {
    const drone = entity('uav-3', 0, 'lowalt:UAV', {});
    const key: EntityKey = { id: 'uav-3', generation: 0 };
    expect(labelInterest(drone, key)).toBe(true);
    expect(labelInterest(drone, { id: 'uav-9', generation: 0 }, new Set(['uav-3:0']))).toBe(true);
    const task = entity('task-1', 0, 'lowalt:Task', { 'lowalt.task_state': 'picked_up' });
    expect(labelInterest(drone, undefined, undefined, true) && !!task).toBe(true);
    expect(labelInterest(drone)).toBe(false);
    const done = entity('task-2', 0, 'lowalt:Task', { 'lowalt.task_state': 'completed' });
    expect(done.fields.get('lowalt.task_state')!.value).toBe('completed');
  });
  it('leaves background road vehicles unlabelled unless selected or alerting', () => {
    const background = entity('vehicle.013', 0, 'aas:TrafficRoadVehicle', { 'traffic.actor.role': 'background' });
    const key: EntityKey = { id: 'vehicle.013', generation: 0 };
    // Routine motion and even route tasks stay unlabelled in the default view.
    expect(labelInterest(background)).toBe(false);
    expect(labelInterest(background, undefined, undefined, true)).toBe(false);
    expect(labelInterest(background, undefined, new Set(['vehicle.013:0']))).toBe(false);
    // Selected or actually alerting: labelled.
    expect(labelInterest(background, key)).toBe(true);
    expect(labelInterest(background, undefined, undefined, false, new Set(['vehicle.013:0']))).toBe(true);
  });
  it('collects recent event subjects within the window and drops commands and stale events', () => {
    const messages = [
      { id: 'a', kind: 'event' as const, schemaId: 'lowalt.task.started', source: 's', at: { ns: '9000000000', microstep: 0 }, payload: {}, subjects: [{ id: 'uav-3', generation: 0 }] },
      { id: 'b', kind: 'event' as const, schemaId: 'lowalt.task.created', source: 's', at: { ns: '1000', microstep: 0 }, payload: {}, subjects: [{ id: 'uav-4', generation: 0 }] },
      { id: 'c', kind: 'event' as const, schemaId: 'lowalt.task.completed', source: 's', at: { ns: '11000000000', microstep: 0 }, payload: {}, subjects: [{ id: 'uav-5', generation: 0 }] },
      { id: 'd', kind: 'command' as const, schemaId: 'lowalt.motion.move_to', source: 's', at: { ns: '9500000000', microstep: 0 }, payload: {}, subjects: [{ id: 'uav-6', generation: 0 }] },
    ];
    const involved = recentlyInvolved(messages, 2_000_000_000, '10000000000');
    expect(involved.has('uav-3:0')).toBe(true);
    expect(involved.has('uav-4:0')).toBe(false);
    expect(involved.has('uav-5:0')).toBe(false);
    expect(involved.has('uav-6:0')).toBe(false);
  });
});
