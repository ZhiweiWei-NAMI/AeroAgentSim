import { describe, expect, it } from 'vitest';
import * as T from 'three';
import { fitModel, hash32, parseModelCatalog, selectCatalogModel, selectRoadVehicle, selectUav, yawFromVelocity, TAXI_PER_MILLE } from './model-catalog';

const row = (kind: string, sizeM: number[], extra: object = {}) => ({ url: `/m/${kind}.glb`, kind, label: kind, sizeM, forward: '+z', up: '+y', ...extra });
const raw = {
  sedan: row('road_vehicle', [4.5, 1.8, 1.5]), taxi: row('road_vehicle', [4.7, 1.85, 1.65]), police: row('road_vehicle', [4.8, 1.9, 1.7]),
  bicycle: row('bicycle', [1.75, 0.7, 1.05]),
  x500: row('uav', [1, 1, 0.52], { lodUrl: '/m/x500-lod.glb' }), 'quadcopter-40': row('uav', [1.3, 1.07, 0.43]), 'air-taxi': row('uav', [4.2, 3.46, 1.4]),
};
const catalog = parseModelCatalog(raw);

describe('model catalog parsing', () => {
  it('treats an absent catalog (no pack, v1.0 pack) as empty', () => {
    expect(parseModelCatalog(undefined)).toEqual({});
    expect(parseModelCatalog(null)).toEqual({});
  });
  it('keeps catalogued fields and the optional LOD', () => {
    expect(catalog.x500).toMatchObject({ kind: 'uav', sizeM: [1, 1, 0.52], forward: '+z', up: '+y', lodUrl: '/m/x500-lod.glb' });
    expect(catalog.sedan.lodUrl).toBeUndefined();
  });
  it.each([
    ['unknown kind', { a: row('boat', [1, 1, 1]) }],
    ['missing url', { a: { ...row('uav', [1, 1, 1]), url: '' } }],
    ['bad size', { a: row('uav', [1, 0, 1]) }],
    ['same axis for forward and up', { a: row('uav', [1, 1, 1], { forward: '+y' }) }],
    ['unknown axis', { a: row('uav', [1, 1, 1], { up: 'y' }) }],
    ['not an object', [1, 2]],
  ])('rejects malformed entries: %s', (_name, value) => expect(() => parseModelCatalog(value)).toThrow(/model catalog/));
});

describe('road vehicle selection', () => {
  it('uses an explicit class or role first', () => {
    expect(selectRoadVehicle({ id: 'a', vehicleClass: 'taxi' }, catalog)).toEqual({ id: 'taxi', reason: 'class' });
    expect(selectRoadVehicle({ id: 'a', vehicleClass: 'police' }, catalog)).toEqual({ id: 'police', reason: 'class' });
    expect(selectRoadVehicle({ id: 'a', role: 'responder' }, catalog)).toEqual({ id: 'police', reason: 'role' });
    expect(selectRoadVehicle({ id: 'a', vehicleClass: 'bicycle' }, catalog)).toEqual({ id: 'bicycle', reason: 'class' });
  });
  it('does not draw an uncatalogued stated class (bus) as a sedan', () => {
    expect(selectRoadVehicle({ id: 'a', vehicleClass: 'bus' }, catalog)).toBeUndefined();
  });
  it('gives generic road vehicles a deterministic sedan/taxi variety', () => {
    const ids = Array.from({ length: 400 }, (_, i) => `vehicle.${String(i).padStart(3, '0')}`);
    const picks = ids.map(id => selectRoadVehicle({ id, vehicleClass: 'road_vehicle', role: 'background' }, catalog)!);
    expect(picks.every(pick => pick.reason === 'variety')).toBe(true);
    expect(ids.map(id => selectRoadVehicle({ id }, catalog)!.id)).toEqual(picks.map(pick => pick.id));
    const taxis = picks.filter(pick => pick.id === 'taxi').length / picks.length;
    expect(taxis).toBeGreaterThan(TAXI_PER_MILLE / 1000 - 0.08);
    expect(taxis).toBeLessThan(TAXI_PER_MILLE / 1000 + 0.08);
    expect(new Set(picks.map(pick => pick.id))).toEqual(new Set(['sedan', 'taxi']));
  });
  it('falls back without a catalog or without road models', () => {
    expect(selectRoadVehicle({ id: 'a' }, {})).toBeUndefined();
    expect(selectRoadVehicle({ id: 'a' }, parseModelCatalog({ x500: raw.x500 }))).toBeUndefined();
    expect(selectCatalogModel('road_vehicle', { id: 'a' }, {})).toBeUndefined();
  });
  it('hashes stably', () => {
    expect(hash32('vehicle.001')).toBe(hash32('vehicle.001'));
    expect(hash32('vehicle.001')).not.toBe(hash32('vehicle.002'));
  });
});

describe('UAV selection', () => {
  it('maps vehicle classes to catalogued airframes', () => {
    expect(selectUav({ id: 'u', vehicleClass: 'passenger' }, catalog)).toEqual({ id: 'air-taxi', reason: 'class' });
    expect(selectUav({ id: 'u', vehicleClass: 'spraying' }, catalog)?.id).toBe('quadcopter-40');
    expect(selectUav({ id: 'u', vehicleClass: 'parcel' }, catalog)?.id).toBe('x500');
    expect(selectUav({ id: 'u' }, catalog)?.id).toBe('x500');
  });
  it('uses what the catalog has, or nothing', () => {
    expect(selectUav({ id: 'u', vehicleClass: 'passenger' }, parseModelCatalog({ 'quadcopter-40': raw['quadcopter-40'] }))?.id).toBe('quadcopter-40');
    expect(selectUav({ id: 'u' }, parseModelCatalog({ sedan: raw.sedan }))).toBeUndefined();
    expect(selectUav({ id: 'u' }, {})).toBeUndefined();
  });
});

describe('fit: scale, orientation and ground offset (body frame: x forward, y left, z up)', () => {
  const carBox = new T.Box3(new T.Vector3(-0.611, -0.487, -1.55), new T.Vector3(0.611, 0.487, 1.55)); // Car_6: forward +z, origin centred
  it('turns +z forward into +x, scales to the target length and puts the wheels on z = 0', () => {
    const fit = fitModel(carBox, catalog.sedan);
    expect(fit.scale).toBeCloseTo(4.5 / 3.1, 6);
    const front = new T.Vector3(0, 0, 1.55).applyMatrix4(fit.matrix);
    expect(front.x).toBeCloseTo(2.25, 6); expect(front.y).toBeCloseTo(0, 6);
    expect(new T.Vector3(0, -0.487, 0).applyMatrix4(fit.matrix).z).toBeCloseTo(0, 6);
    expect(new T.Vector3(0, 0.487, 0).applyMatrix4(fit.matrix).z).toBeCloseTo(0.974 * 4.5 / 3.1, 3);
    // Model +x is the car's right-hand... left is +y in the body frame: the model's +x side maps to body -y or +y consistently.
    expect(Math.abs(new T.Vector3(0.611, 0, 0).applyMatrix4(fit.matrix).y)).toBeCloseTo(0.611 * 4.5 / 3.1, 6);
    expect(fit.size.x).toBeCloseTo(4.5, 6);
    expect(fit.size.y).toBeLessThanOrEqual(1.8 + 1e-9); expect(fit.size.z).toBeLessThanOrEqual(1.5 + 1e-9);
  });
  it('is a proper rotation: left follows up x forward', () => {
    const fit = fitModel(carBox, catalog.sedan);
    // forward +z, up +y => left = up x forward = +x.
    expect(new T.Vector3(1, 0, 0).applyMatrix4(fit.matrix).y).toBeGreaterThan(0);
  });
  it('handles an off-centre origin (taxi box)', () => {
    const taxi = new T.Box3(new T.Vector3(-1.06, -0.02, -2.887), new T.Vector3(1.058, 1.812, 2.567));
    const fit = fitModel(taxi, catalog.taxi);
    const rotated = new T.Box3();
    for (const x of [taxi.min.x, taxi.max.x]) for (const y of [taxi.min.y, taxi.max.y]) for (const z of [taxi.min.z, taxi.max.z]) rotated.expandByPoint(new T.Vector3(x, y, z).applyMatrix4(fit.matrix));
    expect(rotated.min.z).toBeCloseTo(0, 6);
    expect((rotated.min.x + rotated.max.x) / 2).toBeCloseTo(0, 6);
    expect((rotated.min.y + rotated.max.y) / 2).toBeCloseTo(0, 6);
    expect(rotated.max.x - rotated.min.x).toBeCloseTo(4.7, 6);
  });
  it('centres a UAV on its position instead of grounding it', () => {
    const box = new T.Box3(new T.Vector3(-1.55, -0.793, -1.55), new T.Vector3(1.55, 0.793, 1.55));
    const fit = fitModel(box, catalog.x500);
    expect(fit.scale).toBeCloseTo(1 / 3.1, 6);
    expect(new T.Vector3(0, -0.793, 0).applyMatrix4(fit.matrix).z).toBeCloseTo(-0.793 / 3.1, 6);
  });
  it('supports other forward/up axes', () => {
    const box = new T.Box3(new T.Vector3(-2, -1, -0.5), new T.Vector3(2, 1, 0.5)); // length along x, up is z
    const fit = fitModel(box, { forward: '+x', up: '+z', sizeM: [2, 1, 0.5], kind: 'road_vehicle' });
    expect(new T.Vector3(2, 0, 0).applyMatrix4(fit.matrix).x).toBeCloseTo(1, 6);
    expect(new T.Vector3(0, 0, 0.5).applyMatrix4(fit.matrix).z).toBeCloseTo(0.5, 6);
    const backwards = fitModel(box, { forward: '-x', up: '+z', sizeM: [2, 1, 0.5], kind: 'road_vehicle' });
    expect(new T.Vector3(-2, 0, 0).applyMatrix4(backwards.matrix).x).toBeCloseTo(1, 6);
  });
  it('rejects empty bounds', () => expect(() => fitModel(new T.Box3(), catalog.sedan)).toThrow(/no geometry/));
});

describe('heading from velocity', () => {
  it('is atan2(north, east) and undefined when nearly still', () => {
    expect(yawFromVelocity([0, 5, 0])).toBeCloseTo(Math.PI / 2, 9);
    expect(yawFromVelocity([-3, 0, 1])).toBeCloseTo(Math.PI, 9);
    expect(yawFromVelocity([0.1, 0.1, 0])).toBeUndefined();
    expect(yawFromVelocity([NaN, 1, 0])).toBeUndefined();
  });
});
