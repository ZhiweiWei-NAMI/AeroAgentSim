import { expect, it } from 'vitest';
import { TemporalFeedStore } from '../feeds/temporal-store';
import { emptyCommit, fixtureHeader, vehicle } from '../behaviours/extension-fixture';
import { entityLabel } from '../pages/inspection-format';
import { displayName } from './semantic';
import { entityId } from './bindings';

it('keeps authored identity in standalone state/role labels as well as the 3D and inspector display name', () => {
 const header = { ...fixtureHeader, entities: [{ id: vehicle.id, type: 'aas:Vehicle', label: 'Incident drone Alpha', label_zh: '应急无人机 阿尔法' }] };
 const store = new TemporalFeedStore(header);
 store.ingest({ ...emptyCommit(1), created: [{ ...vehicle, typeId: fixtureHeader.types[0].typeId }] }); store.seek('0', 1);
 const entity = store.entities.get(entityId(vehicle))!;
 expect(entityLabel(entity)).toBe(displayName(entity, header)); expect(entityLabel(entity)).toBe('Incident drone Alpha');
 expect(entityLabel(entity, 'zh-CN')).toBe(displayName(entity, header, 'zh-CN')); expect(entityLabel(entity, 'zh-CN')).toBe('应急无人机 阿尔法');
 expect([...entity.fields]).toEqual([]); // Visual metadata never enters recorded state.
});
it('uses the same committed name priority in standalone and spatial views without changing raw facts', () => {
 const entity = { key: vehicle, typeId: 'aas:Vehicle', fields: new Map<string, any>([['name', { value: 'Secondary name' }], ['traffic.actor.label', { value: 'Primary name' }]]) };
 expect(entityLabel(entity)).toBe(displayName(entity, fixtureHeader)); expect(entityLabel(entity)).toBe('Primary name');
 expect(entity.fields.get('name').value).toBe('Secondary name');
});
