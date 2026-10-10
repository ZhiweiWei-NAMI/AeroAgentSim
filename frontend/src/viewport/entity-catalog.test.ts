import { describe, expect, it } from 'vitest';
import * as T from 'three';
import type { FeedCommit, RunHeader } from '../contracts/viewer-feed';
import { EntityLayer } from './entities';
import { FeedStore } from './feed-store';
import { parseModelCatalog } from './model-catalog';
import type { Assets } from './assets';

const catalog = parseModelCatalog({
  sedan: { url: '/m/sedan.glb', kind: 'road_vehicle', sizeM: [4.5, 1.8, 1.5], forward: '+z', up: '+y' },
  police: { url: '/m/police.glb', kind: 'road_vehicle', sizeM: [4.8, 1.9, 1.7], forward: '+z', up: '+y' },
  x500: { url: '/m/x500.glb', lodUrl: '/m/x500-lod.glb', kind: 'uav', sizeM: [1, 1, 0.52], forward: '+z', up: '+y' },
});
const header = (withHint: boolean): RunHeader => ({
  contract: 'aeroagentsim.viewer-feed/v1', runId: 'r', registryDigest: 'd', start: { ns: '0', microstep: 0 },
  types: [{ typeId: 'aas:TrafficRoadVehicle', displayName: 'Car', ancestors: [] }, { typeId: 'lowalt:UAV', displayName: 'UAV', ancestors: [] }],
  fields: [],
  presentation: [
    { typeId: 'aas:TrafficRoadVehicle', positionField: 'pos', orientationField: 'att', frame: 'enu', visual: { kind: 'model', asset: 'procedural:car', ...(withHint ? { catalog: 'road_vehicle' as const } : {}) } },
    { typeId: 'lowalt:UAV', positionField: 'upos', frame: 'enu', visual: { kind: 'marker', color: '#50c9ff', ...(withHint ? { catalog: 'uav' as const } : {}) } },
  ],
});
const commit = (facts: FeedCommit['facts'], created: FeedCommit['created']): FeedCommit => ({
  commitIndex: 0, at: { ns: '0', microstep: 0 }, created, removed: [], facts, retracted: [], edges: [], messages: [], receipts: [],
});
const fact = (id: string, fieldId: string, value: unknown) => ({ entity: { id, generation: 0 }, fieldId, value, producer: 'p', validFrom: { ns: '0', microstep: 0 } });

/** Stub loader: a car-shaped box (origin at its centre, forward +z), one mesh. */
function stubAssets(requested: string[]): Assets {
  return {
    model: async (url: string) => {
      requested.push(url);
      const group = new T.Group();
      group.add(new T.Mesh(new T.BoxGeometry(1.222, 0.974, 3.1), new T.MeshStandardMaterial()));
      return group;
    },
  } as unknown as Assets;
}
const tick = () => new Promise(resolve => setTimeout(resolve, 0));

function populated(withHint: boolean, entities: Array<{ id: string; type: string; facts: Record<string, unknown> }>) {
  const h = header(withHint), store = new FeedStore(h);
  store.ingest(commit(entities.flatMap(e => Object.entries(e.facts).map(([f, v]) => fact(e.id, f, v))), entities.map(e => ({ id: e.id, generation: 0, typeId: e.type }))));
  store.seek('0');
  return { h, store };
}
const camera = () => { const c = new T.PerspectiveCamera(); c.position.set(0, 20, 20); c.lookAt(0, 0, 0); return c; };
function instanceMatrices(layer: EntityLayer): T.Matrix4[] {
  const out: T.Matrix4[] = [];
  layer.root.traverse(node => {
    if (node instanceof T.InstancedMesh && node.count > 0 && node.geometry instanceof T.BoxGeometry) { const m = new T.Matrix4(); node.getMatrixAt(0, m); out.push(m); }
  });
  return out;
}

describe('EntityLayer catalogued models', () => {
  const car = { id: 'vehicle.001', type: 'aas:TrafficRoadVehicle', facts: { pos: [10, 5, 0], att: [0, 0, Math.SQRT1_2, Math.SQRT1_2], 'traffic.actor.role': 'responder' } };
  it('without a catalog keeps the procedural stand-in and requests nothing', async () => {
    const requested: string[] = [], { h, store } = populated(true, [car]);
    const layer = new EntityLayer(h, stubAssets(requested), document.createElement('div'), () => {}, () => {});
    layer.update(store, '0', camera()); await tick(); layer.update(store, '0', camera());
    expect(requested).toEqual([]);
    expect(layer.positions.size).toBe(1);
  });
  it('a binding without the catalog hint is untouched even when a catalog is installed', async () => {
    const requested: string[] = [], { h, store } = populated(false, [car]);
    const layer = new EntityLayer(h, stubAssets(requested), document.createElement('div'), () => {}, () => {});
    layer.setCatalog(catalog);
    layer.update(store, '0', camera()); await tick(); layer.update(store, '0', camera());
    expect(requested).toEqual([]);
  });
  it('draws a responder as the catalogued police car, grounded, facing its heading, after the model loads', async () => {
    const requested: string[] = [], { h, store } = populated(true, [car]);
    const layer = new EntityLayer(h, stubAssets(requested), document.createElement('div'), () => {}, () => {});
    layer.setCatalog(catalog);
    layer.update(store, '0', camera());        // stand-in while the GLB loads
    await tick(); await tick();
    layer.update(store, '0', camera());
    expect(requested).toEqual(['/m/police.glb']);
    const [matrix] = instanceMatrices(layer);
    expect(matrix).toBeDefined();
    const position = new T.Vector3(), quaternion = new T.Quaternion(), scale = new T.Vector3();
    matrix.decompose(position, quaternion, scale);
    // Entity at ENU (10, 5, 0) -> render (10, 0, -5); the box centre sits half its fitted height above the road.
    expect(position.x).toBeCloseTo(10, 5); expect(position.z).toBeCloseTo(-5, 5);
    expect(position.y).toBeCloseTo(0.974 * (4.8 / 3.1) / 2, 4);
    // Yaw +90 degrees (north): the model's long axis (+z) points along render -z (north).
    const rotation = new T.Matrix4().extractRotation(matrix);
    const nose = new T.Vector3(0, 0, 1).applyMatrix4(rotation);
    expect(nose.z).toBeCloseTo(-1, 5); expect(nose.x).toBeCloseTo(0, 5); expect(nose.y).toBeCloseTo(0, 5);
    const roof = new T.Vector3(0, 1, 0).applyMatrix4(rotation);
    expect(roof.y).toBeCloseTo(1, 5);
    expect(scale.x).toBeCloseTo(4.8 / 3.1, 5);
  });
  it('faces an aircraft without an orientation field along its velocity and centres it on its position', async () => {
    const requested: string[] = [], uav = { id: 'uav.1', type: 'lowalt:UAV', facts: { upos: [0, 0, 30], 'lowalt.velocity': [0, 6, 0], 'lowalt.vehicle_class': 'parcel' } };
    const { h, store } = populated(true, [uav]);
    const layer = new EntityLayer(h, stubAssets(requested), document.createElement('div'), () => {}, () => {});
    layer.setCatalog(catalog);
    const near = new T.PerspectiveCamera(); near.position.set(0, 30, 10); // within LOD distance
    layer.update(store, '0', near); await tick(); await tick(); layer.update(store, '0', near);
    expect(requested).toEqual(['/m/x500.glb']);
    const [matrix] = instanceMatrices(layer);
    const position = new T.Vector3(); matrix.decompose(position, new T.Quaternion(), new T.Vector3());
    expect(position.y).toBeCloseTo(30, 5);
    expect(layer.orientations.get('uav.1:0')).toBeDefined();
  });
  it('falls back to the stand-in when a catalogued model fails to load', async () => {
    const errors: string[] = [];
    const failing = { model: async () => { throw Error('HTTP 404'); } } as unknown as Assets;
    const { h, store } = populated(true, [car]);
    const layer = new EntityLayer(h, failing, document.createElement('div'), () => {}, () => {}, message => errors.push(message));
    layer.setCatalog(catalog);
    layer.update(store, '0', camera()); await tick(); await tick(); layer.update(store, '0', camera());
    expect(errors.some(message => message.includes('unavailable') && message.includes('police'))).toBe(true);
    expect(layer.positions.size).toBe(1);
  });
});
