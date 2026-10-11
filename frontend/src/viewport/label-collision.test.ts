import { expect, it, vi } from 'vitest';
import * as T from 'three';
import { EntityLayer } from './entities';
import { FeedStore } from './feed-store';
import type { RunHeader } from '../contracts/viewer-feed';
import type { Assets } from './assets';

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
