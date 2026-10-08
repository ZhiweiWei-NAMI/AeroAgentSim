import { describe, expect, it } from 'vitest';
import type { FeedCommit, RunHeader } from '../contracts/viewer-feed';
import { FeedStore } from './feed-store';
import { entityId } from './bindings';

const header: RunHeader = { contract: 'aeroagentsim.viewer-feed/v1', runId: 'test', registryDigest: 'test', start: { ns: '0', microstep: 0 },
  types: [{ typeId: 'fixture:Generic', displayName: 'Generic', ancestors: [] }], fields: [], presentation: [] };
const key = { id: 'any-object', generation: 0 };
function commit(index: number, ns: string, changes: Partial<FeedCommit> = {}): FeedCommit {
  return { commitIndex: index, at: { ns, microstep: 0 }, created: [], removed: [], facts: [], retracted: [], edges: [], messages: [], receipts: [], ...changes };
}
const fact = (value: unknown, ns: string) => ({ entity: key, fieldId: 'any-position', value, producer: 'fixture', validFrom: { ns, microstep: 0 } });

describe('display and exact commit boundaries', () => {
  it('interpolates without revising the exact inspector value or source stamp', () => {
    const store = new FeedStore(header);
    store.ingest(commit(0, '100', { created: [{ ...key, typeId: 'fixture:Generic' }], facts: [fact([1, 2, 3], '70')] }));
    store.ingest(commit(1, '300', { facts: [fact([5, 6, 7], '250')] }));
    store.seek('200');
    expect(store.sample(key, 'any-position', '200')).toEqual([3, 4, 5]);
    expect(store.entities.get(entityId(key))!.fields.get('any-position')!.value).toEqual([1, 2, 3]);
    expect(store.entities.get(entityId(key))!.fields.get('any-position')!.validFrom.ns).toBe('70');
    expect(store.commits[0].facts[0].value).toEqual([1, 2, 3]);
  });
  it('shows the requested equal-time commit before a later removal microstep', () => {
    const store = new FeedStore(header);
    store.ingest(commit(0, '100', { created: [{ ...key, typeId: 'fixture:Generic' }], facts: [fact([1, 2, 3], '100')] }));
    store.ingest(commit(1, '100', { at: { ns: '100', microstep: 1 }, removed: [key] }));
    store.seek('100', 0);
    expect(store.entities.has(entityId(key))).toBe(true);
    expect(store.sample(key, 'any-position', '100')).toEqual([1, 2, 3]);
    store.seek('100', 1);
    expect(store.entities.has(entityId(key))).toBe(false);
    expect(store.sample(key, 'any-position', '100')).toBeUndefined();
  });
  it('does not interpolate a teleport encoded as retract and new fact in one commit', () => {
    const store = new FeedStore(header);
    store.ingest(commit(0, '100', { created: [{ ...key, typeId: 'fixture:Generic' }], facts: [fact([1, 2, 3], '100')] }));
    store.ingest(commit(1, '300', { retracted: [{ entity: key, fieldId: 'any-position' }], facts: [fact([1000, 2, 3], '300')] }));
    store.seek('200');
    expect(store.sample(key, 'any-position', '200')).toEqual([1, 2, 3]);
    store.seek('300');
    expect(store.sample(key, 'any-position', '300')).toEqual([1000, 2, 3]);
    expect(store.entities.get(entityId(key))!.fields.has('any-position')).toBe(true);
  });
  it('never changes a missing/null numeric component to zero', () => {
    const store = new FeedStore(header);
    store.ingest(commit(0, '100', { created: [{ ...key, typeId: 'fixture:Generic' }], facts: [fact([null, 2, 3], '100')] }));
    store.seek('100');
    expect(store.sample(key, 'any-position', '100')).toBeUndefined();
    expect(store.entities.get(entityId(key))!.fields.get('any-position')!.value).toEqual([null, 2, 3]);
  });
  it('holds until an explicitly marked discontinuity without retracting the exact fact', () => {
    const store = new FeedStore(header);
    store.ingest(commit(0, '100', { created: [{ ...key, typeId: 'fixture:Generic' }], facts: [fact([1, 2, 3], '100')] }));
    store.ingest(commit(1, '300', { facts: [{ ...fact([1000, 2, 3], '300'), discontinuity: true }] }));
    store.seek('200'); expect(store.sample(key, 'any-position', '200')).toEqual([1, 2, 3]);
    store.seek('300'); expect(store.sample(key, 'any-position', '300')).toEqual([1000, 2, 3]);
    expect(store.entities.get(entityId(key))!.fields.get('any-position')!.value).toEqual([1000, 2, 3]);
  });
  it('keeps only asserted relations at the current cut and restores them on rewind', () => {
    const store = new FeedStore(header);
    const other = { id: 'other', generation: 0 };
    const edge = { edgeId: 'e1', relationId: 'r1', source: key, target: other };
    store.ingest(commit(0, '100', { created: [key, other].map(key => ({ ...key, typeId: 'fixture:Generic' })), edges: [{ ...edge, op: 'assert' }] }));
    store.ingest(commit(1, '200', { edges: [{ ...edge, op: 'close' }] }));
    store.seek('200'); expect(store.edges.has('e1')).toBe(false);
    store.seek('100'); expect(store.edges.has('e1')).toBe(true);
  });
});
