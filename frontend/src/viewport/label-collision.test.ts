import { expect, it, vi } from 'vitest';
import * as T from 'three';
import { EntityLayer } from './entities';
import { FeedStore } from './feed-store';
import type { RunHeader } from '../contracts/viewer-feed';
import type { Assets } from './assets';
import type { StaticOcclusion } from './occlusion';

it('keeps the selected UAV label readable when multiple active actors share one position', () => {
  const header: RunHeader = { contract: 'aeroagentsim.viewer-feed/v1', runId: 'labels', registryDigest: 'd', start: { ns: '0', microstep: 0 }, fields: [],
    types: [{ typeId: 'lowalt:UAV', displayName: 'UAV', ancestors: [] }],
    presentation: [{ typeId: 'lowalt:UAV', positionField: 'pos', frame: 'enu', visual: { kind: 'marker' } }] };
  const store = new FeedStore(header), at = { ns: '0', microstep: 0 }, keys = ['uav-1', 'uav-2', 'uav-3'].map(id => ({ id, generation: 0 }));
  store.ingest({ commitIndex: 1, at, created: keys.map(key => ({ ...key, typeId: 'lowalt:UAV' })), removed: [], retracted: [], edges: [], receipts: [],
    facts: keys.flatMap(entity => [
      { entity, fieldId: 'pos', value: [0, 0, 10], producer: 'engine', validFrom: at },
      { entity, fieldId: 'lowalt.vehicle_class', value: 'parcel', producer: 'engine', validFrom: at },
    ]), messages: [{ id: 'active', kind: 'event', schemaId: 'lowalt.task.started', at, source: 'engine', subjects: keys, payload: {} }] });
  store.seek('0');
  const overlay = document.createElement('div');
  Object.defineProperties(overlay, { clientWidth: { value: 800 }, clientHeight: { value: 600 } });
  const width = vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(240), height = vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(28);
  const layer = new EntityLayer(header, {} as Assets, overlay, () => {}, () => {}); layer.semantic = true;
  const camera = new T.PerspectiveCamera(48, 4 / 3); camera.position.set(22, 30, 24); camera.lookAt(0, 10, 0);
  try {
    layer.update(store, '0', camera, keys[1]);
    const visible = [...overlay.querySelectorAll<HTMLButtonElement>('.entity-label')].filter(label => !label.hidden);
    expect(visible).toHaveLength(1);
    expect(visible[0].dataset.entityId).toBe('uav-2');
    expect(visible[0]).toHaveClass('selected');
  } finally { layer.dispose(); width.mockRestore(); height.mockRestore(); }
});

it('anchors incident labels and pin obstacles to the same exact surface as the FX pin', () => {
  const header: RunHeader = { contract: 'aeroagentsim.viewer-feed/v1', runId: 'surface', registryDigest: 'd', start: { ns: '0', microstep: 0 }, fields: [],
    types: [{ typeId: 'lowalt:UAV', displayName: 'UAV', ancestors: [] }, { typeId: 'lowalt:Incident', displayName: 'Incident', ancestors: [] }],
    presentation: ['lowalt:UAV', 'lowalt:Incident'].map(typeId => ({ typeId, positionField: 'pos', frame: 'enu', visual: { kind: 'marker' } })) };
  const at = { ns: '0', microstep: 0 }, uav = { id: 'uav-1', generation: 0 }, fire = { id: 'fire-a', generation: 0 }, store = new FeedStore(header);
  store.ingest({ commitIndex: 1, at, created: [{ ...uav, typeId: 'lowalt:UAV' }, { ...fire, typeId: 'lowalt:Incident' }], removed: [], retracted: [], edges: [], receipts: [],
    facts: [uav, fire].map(entity => ({ entity, fieldId: 'pos', value: [0, 0, 0], producer: 'engine', validFrom: at })), messages: [] }); store.seek('0');
  const overlay = document.createElement('div');
  Object.defineProperties(overlay, { clientWidth: { value: 800 }, clientHeight: { value: 600 } });
  const width = vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(240), height = vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(28);
  const layer = new EntityLayer(header, {} as Assets, overlay, () => {}, () => {}), roof = vi.fn(() => 0);
  const occlusion = { liftCamera: vi.fn(() => new T.Vector3(0, 400, 0)) } as unknown as StaticOcclusion;
  const camera = new T.PerspectiveCamera(48, 4 / 3); camera.position.set(0, 70, 120); camera.lookAt(0, 10, 0);
  try {
    layer.update(store, '0', camera, uav, true, undefined, occlusion, undefined, [
      { id: 'fire-a', kind: 'fire', label: 'Building fire', position: [0, 0, 0], radiusM: 30, phase: 'active', sinceS: 5, activeS: 5, startS: 0 },
    ], roof);
    expect(roof).toHaveBeenCalledWith(0, 0); expect(occlusion.liftCamera).not.toHaveBeenCalled();
    const label = overlay.querySelector<HTMLButtonElement>('[data-entity-id="fire-a"]')!;
    expect(label.hidden).toBe(false);
    const anchor = new T.Vector3(0, 16, 0).project(camera);
    expect(parseFloat(label.style.top)).toBeCloseTo((1 - anchor.y) * 300 - 14);
    const other = overlay.querySelector<HTMLButtonElement>('[data-entity-id="uav-1"]')!;
    expect(other.hidden).toBe(false);
    expect(Math.abs(parseFloat(other.style.left) - parseFloat(label.style.left))).toBeGreaterThan(34 + other.offsetWidth / 2);
    expect(Math.abs(parseFloat(other.style.left) - parseFloat(label.style.left)) > other.offsetWidth
      || Math.abs(parseFloat(other.style.top) - parseFloat(label.style.top)) > other.offsetHeight).toBe(true);
  } finally { layer.dispose(); width.mockRestore(); height.mockRestore(); }
});

it('moves UAV labels away from a fixed incident pin (the pin wins label collisions)', () => {
  const header: RunHeader = { contract: 'aeroagentsim.viewer-feed/v1', runId: 'pin', registryDigest: 'd', start: { ns: '0', microstep: 0 }, fields: [],
    types: [{ typeId: 'lowalt:UAV', displayName: 'UAV', ancestors: [] }],
    presentation: [{ typeId: 'lowalt:UAV', positionField: 'pos', frame: 'enu', visual: { kind: 'marker' } }] };
  const store = new FeedStore(header), at = { ns: '0', microstep: 0 }, key = { id: 'uav-1', generation: 0 };
  store.ingest({ commitIndex: 1, at, created: [{ ...key, typeId: 'lowalt:UAV' }], removed: [], retracted: [], edges: [], receipts: [],
    facts: [{ entity: key, fieldId: 'pos', value: [0, 0, 30], producer: 'engine', validFrom: at }], messages: [] });
  store.seek('0');
  const overlay = document.createElement('div');
  Object.defineProperties(overlay, { clientWidth: { value: 800 }, clientHeight: { value: 600 } });
  const width = vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(240), height = vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(28);
  const layer = new EntityLayer(header, {} as Assets, overlay, () => {}, () => {});
  const camera = new T.PerspectiveCamera(48, 4 / 3); camera.position.set(0, 20, 90); camera.lookAt(0, 25, 0);
  try {
    // The UAV shares the fire's position: its label must not sit on the pin rect.
    layer.update(store, '0', camera, key, true, undefined, undefined, undefined, [
      { id: 'fire-a', kind: 'fire', label: 'Building fire', position: [0, 0, 30], radiusM: 30, phase: 'active', sinceS: 5, activeS: 5, startS: 0 },
    ]);
    const label = overlay.querySelector<HTMLButtonElement>('.entity-label');
    expect(label).toBeTruthy();
    expect(label!.hidden).toBe(false);
    // The pin rect spans ±34 px around the screen centre (the pin's anchor); a 240 px label
    // that overlaps the pin would cover it, so it must be placed clear of the centre.
    const left = parseFloat(label!.style.left) - label!.offsetWidth / 2, right = parseFloat(label!.style.left) + label!.offsetWidth / 2;
    expect(left).not.toBeNaN();
    expect(right < 400 - 34 || left > 400 + 34).toBe(true);
  } finally { layer.dispose(); width.mockRestore(); height.mockRestore(); }
});

it('refreshes panel obstacles only when the viewport layout changes', () => {
  const header: RunHeader = { contract: 'aeroagentsim.viewer-feed/v1', runId: 'layout', registryDigest: 'd', start: { ns: '0', microstep: 0 }, fields: [], types: [], presentation: [] };
  const wb = document.createElement('div'); wb.className = 'wb';
  const overlay = document.createElement('div'), panel = document.createElement('aside'); panel.className = 'wb-panel'; wb.append(overlay, panel); document.body.append(wb);
  Object.defineProperties(overlay, { clientWidth: { value: 800 }, clientHeight: { value: 600 } });
  const measure = vi.spyOn(panel, 'getBoundingClientRect').mockReturnValue(new DOMRect(0, 0, 180, 600));
  const layer = new EntityLayer(header, {} as Assets, overlay, () => {}, () => {}), store = new FeedStore(header), camera = new T.PerspectiveCamera(), origin = new T.Vector3();
  try {
    for (let i = 0; i < 300; i++) layer.update(store, '0', camera, undefined, false, origin, undefined, undefined, undefined, undefined, 'layout-a');
    expect(measure).toHaveBeenCalledTimes(1);
    layer.update(store, '0', camera, undefined, false, origin, undefined, undefined, undefined, undefined, 'layout-b');
    expect(measure).toHaveBeenCalledTimes(2);
  } finally { layer.dispose(); wb.remove(); }
});

it('keeps label collision dimensions while the camera moves and refreshes text and viewport changes', () => {
  const key = { id: 'drone', generation: 0 }, at = { ns: '0', microstep: 0 };
  const header: RunHeader = { contract: 'aeroagentsim.viewer-feed/v1', runId: 'metrics', registryDigest: 'd', start: at, fields: [],
    types: [{ typeId: 'lowalt:UAV', displayName: 'UAV', ancestors: [] }],
    entities: [{ id: 'drone', type: 'lowalt:UAV', label: 'Parcel drone', label_zh: '包裹无人机' }],
    presentation: [{ typeId: 'lowalt:UAV', positionField: 'pos', frame: 'enu', visual: { kind: 'marker' } }] };
  const store = new FeedStore(header);
  store.ingest({ commitIndex: 1, at, created: [{ ...key, typeId: 'lowalt:UAV' }], removed: [], retracted: [], edges: [], receipts: [],
    facts: [{ entity: key, fieldId: 'pos', value: [0, 0, 10], producer: 'engine', validFrom: at }], messages: [] }); store.seek('0');
  const overlay = document.createElement('div'); let viewportWidth = 800;
  Object.defineProperties(overlay, { clientWidth: { get: () => viewportWidth }, clientHeight: { value: 600 } });
  const width = vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(160);
  const height = vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(28);
  const layer = new EntityLayer(header, {} as Assets, overlay, () => {}, () => {}); layer.semantic = true;
  const camera = new T.PerspectiveCamera(48, 4 / 3); camera.position.set(22, 30, 24); camera.lookAt(0, 10, 0);
  try {
    layer.update(store, '0', camera, key); layer.update(store, '0', camera, key);
    width.mockClear(); height.mockClear();
    for (let i = 0; i < 20; i++) { camera.position.x += 0.01; layer.update(store, '0', camera, key); }
    expect(width).not.toHaveBeenCalled(); expect(height).not.toHaveBeenCalled();
    layer.locale = 'zh-CN'; layer.update(store, '0', camera, key);
    expect(width).toHaveBeenCalledTimes(1); expect(height).toHaveBeenCalledTimes(1);
    expect(overlay.querySelector('.entity-label')).toHaveTextContent('包裹无人机');
    viewportWidth = 850; layer.update(store, '0', camera, key);
    expect(width).toHaveBeenCalledTimes(2); expect(height).toHaveBeenCalledTimes(2);
  } finally { layer.dispose(); width.mockRestore(); height.mockRestore(); }
});
