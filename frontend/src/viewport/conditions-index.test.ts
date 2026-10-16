import { expect, it } from 'vitest';
import { FeedStore } from './feed-store';
import { TemporalFeedStore } from '../feeds/temporal-store';
import { conditionsAt, ConditionsCache } from './conditions';
import type { FeedCommit, RunHeader } from '../contracts/viewer-feed';

const at = (ns: string) => ({ ns, microstep: 0 });
const header: RunHeader = { contract: 'aeroagentsim.viewer-feed/v1', runId: 'r', registryDigest: 'd', start: at('0'),
  types: [{ typeId: 'lowalt:Environment', displayName: 'Weather', ancestors: [] }], fields: [], presentation: [] };
const commit = (index: number, ns: string): FeedCommit => ({ commitIndex: index, at: at(ns), created: [], removed: [], facts: [], retracted: [], edges: [], messages: [], receipts: [] });
it.each([FeedStore, TemporalFeedStore])('indexes real weather records through corrections, removal and backward seeks (%s)', Store => {
  const store = new Store(header), cache = new ConditionsCache(), a = { id: 'a', generation: 0 }, b = { id: 'b', generation: 0 };
  const first = commit(0, '0'); first.created = [{ ...a, typeId: 'lowalt:Environment' }, { ...b, typeId: 'lowalt:Environment' }];
  first.facts = [{ entity: b, fieldId: 'lowalt.precipitation', value: 4 / 3600, producer: 'weather', validFrom: at('0') }];
  store.ingest(first); store.seek('0');
  const second = commit(1, '1'); second.facts = [{ entity: a, fieldId: 'lowalt.precipitation', value: 1 / 3600, producer: 'weather', validFrom: at('1') }];
  store.ingest(second); store.seek('1');
  expect([...store.conditionEntities.keys()]).toEqual(['a:0', 'b:0']); // same authoritative override order
  expect(store.conditionEntities.get('a:0')).toBe(store.entities.get('a:0'));
  expect(cache.at(store.conditionEntities, '1', store.revision, header)).toEqual(conditionsAt(store.entities, '1', header));
  const third = commit(2, '1'); third.removed = [b]; store.ingest(third); store.seek('1');
  expect(cache.at(store.conditionEntities, '1', store.revision, header).precipitationMmH).toBe(1);
  store.seek('0');
  expect(cache.at(store.conditionEntities, '0', store.revision, header).precipitationMmH).toBe(4);
  store.seek('1', 1); // same ns, different known cut
  expect(cache.at(store.conditionEntities, '1', store.revision, header)).toEqual(conditionsAt(store.entities, '1', header));
});
