import { describe, expect, it } from 'vitest';
import type { FeedCommit, RunHeader } from '../contracts/viewer-feed';
import { nanos, fraction } from './time';
import { SampleBuffer, slerp } from './samples';
import { FeedStore } from './feed-store';
import { resolveBinding, entityId } from './bindings';
import { PlaybackClock } from './clock';

const header: RunHeader = { contract: 'aeroagentsim.viewer-feed/v1', runId: 'fixture', registryDigest: 'fixture', start: { ns: '0', microstep: 0 },
  types: [{ typeId: 'generic:Base', displayName: 'Base', ancestors: [] }, { typeId: 'generic:Middle', displayName: 'Middle', ancestors: ['generic:Base'] }, { typeId: 'generic:Leaf', displayName: 'Leaf', ancestors: ['generic:Middle', 'generic:Base'] }],
  fields: [], presentation: [{ typeId: 'generic:Base', positionField: 'p', frame: 'enu', visual: { kind: 'marker' } }, { typeId: 'generic:Middle', positionField: 'q', frame: 'ned', visual: { kind: 'label' } }] };
const key = { id: 'arbitrary', generation: 3 };
const transaction = (index: number, ns: string, changes: Partial<FeedCommit> = {}): FeedCommit => ({ commitIndex: index, at: { ns, microstep: 0 }, created: [], removed: [], facts: [], retracted: [], edges: [], messages: [], receipts: [], ...changes });
const pose = (entity: typeof key, value: unknown, ns = '0') => ({ entity, fieldId: 'p', value, producer: 'source', validFrom: { ns, microstep: 0 } });

describe('int64 and descriptor resolution', () => {
  it('interpolates a small interval near signed int64 maximum losslessly', () => {
    expect(nanos('9223372036854775807')).toBe(9223372036854775807n);
    expect(fraction(9223372036854775805n, 9223372036854775803n, 9223372036854775807n)).toBe(0.5);
    const buffer = new SampleBuffer();
    buffer.push('9223372036854775803', 0, [0, 2, 4]); buffer.push('9223372036854775807', 1, [4, 6, 8]);
    expect(buffer.sample('9223372036854775805')).toEqual([2, 4, 6]);
  });
  it.each(['01', '1.0', '-0', '9223372036854775808', 'NaN'])('rejects invalid nanoseconds %s', value => expect(() => nanos(value)).toThrow());
  it('selects direct bindings or the nearest actual ancestor, without kind/directory rules', () => {
    expect(resolveBinding(header, 'generic:Leaf')!.positionField).toBe('q');
    expect(resolveBinding(header, 'generic:Base')!.positionField).toBe('p');
    expect(resolveBinding(header, 'a-drone-name')).toBeUndefined();
    expect(entityId(key)).toBe('arbitrary:3');
  });
});

describe('display sample buffer', () => {
  it('has no sample before the first, interpolates a bracket, and holds after the last', () => {
    const buffer = new SampleBuffer(); buffer.push('100', 0, [2, 4, 6]); buffer.push('300', 1, [4, 6, 8]);
    expect(buffer.sample('99')).toBeUndefined(); expect(buffer.sample('200')).toEqual([3, 5, 7]);
    expect(buffer.sample('500')).toEqual([4, 6, 8]);
  });
  it('holds before a retraction and never bridges its invalid interval', () => {
    const buffer = new SampleBuffer(); buffer.push('100', 0, [2, 4, 6]); buffer.retract('200', 1); buffer.push('300', 2, [8, 8, 8]);
    expect(buffer.sample('150')).toEqual([2, 4, 6]); expect(buffer.sample('200')).toBeUndefined();
    expect(buffer.sample('250')).toBeUndefined(); expect(buffer.sample('300')).toEqual([8, 8, 8]);
  });
  it('slerps quaternions on the shortest path and rejects invalid orientation', () => {
    const half = slerp([0, 0, 0, 1], [0, 0, Math.SQRT1_2, Math.SQRT1_2], 0.5)!;
    expect(half[2]).toBeCloseTo(Math.sin(Math.PI / 8), 12); expect(half[3]).toBeCloseTo(Math.cos(Math.PI / 8), 12);
    const arc = slerp([0, 0, Math.sin(5 * Math.PI / 12), Math.cos(5 * Math.PI / 12)], [0, 0, -Math.sin(5 * Math.PI / 12), Math.cos(5 * Math.PI / 12)], 0.5)!;
    expect(Math.abs(arc[2])).toBeCloseTo(1); expect(arc[3]).toBeCloseTo(0);
    expect(slerp([0, 0, 0, 0], [0, 0, 0, 1], 0.5)).toBeUndefined();
  });
});

describe('committed feed application', () => {
  it('creates/removes generations independently and rewinds facts', () => {
    const store = new FeedStore(header), next = { ...key, generation: 4 };
    store.ingest(transaction(0, '100', { created: [{ ...key, typeId: 'generic:Leaf' }], facts: [pose(key, [1, 2, 3])] }));
    store.ingest(transaction(1, '200', { removed: [key], created: [{ ...next, typeId: 'generic:Leaf' }], facts: [pose(next, [100, 200, 300])] }));
    store.seek('300'); expect(store.entities.has(entityId(key))).toBe(false); expect(store.entities.has(entityId(next))).toBe(true);
    expect(store.sample(key, 'p', '250')).toBeUndefined(); expect(store.sample(next, 'p', '150')).toBeUndefined();
    store.seek('150'); expect(store.entities.has(entityId(key))).toBe(true); expect(store.entities.has(entityId(next))).toBe(false);
    expect(store.sample(key, 'p', '150')).toEqual([1, 2, 3]);
  });
  it('applies retracts and journal messages/receipts at their exact committed cut', () => {
    const store = new FeedStore(header);
    store.ingest(transaction(0, '100', { created: [{ ...key, typeId: 'generic:Base' }], facts: [pose(key, [1, 2, 3])], messages: [{ id: 'cmd', kind: 'command', schemaId: 'schema', source: key.id, at: { ns: '100', microstep: 0 }, payload: {} }], receipts: [{ commandId: 'cmd', status: 'accepted' }] }));
    store.ingest(transaction(1, '200', { retracted: [{ entity: key, fieldId: 'p' }] }));
    store.seek('200'); expect(store.entities.get(entityId(key))!.fields.has('p')).toBe(false); expect(store.messages).toHaveLength(1); expect(store.receipts[0].status).toBe('accepted');
    store.seek('99'); expect(store.entities.size).toBe(0); expect(store.messages).toHaveLength(0); expect(store.receipts).toHaveLength(0);
  });
  it('rejects unordered or absent-entity facts without partially indexing a transaction', () => {
    const store = new FeedStore(header);
    expect(() => store.ingest(transaction(0, '100', { facts: [pose(key, [1, 2, 3])] }))).toThrow(/absent/);
    expect(store.commits).toHaveLength(0);
    store.ingest(transaction(0, '100', { created: [{ ...key, typeId: 'generic:Leaf' }] }));
    expect(() => store.ingest(transaction(0, '100'))).toThrow(/order/);
  });
});

describe('playback clock', () => {
  it('plays, pauses, changes speed, seeks, clamps and extends a live frontier above 2^53', () => {
    const start = 9007199254740993000n, clock = new PlaybackClock(start.toString(), (start + 3_000_000_000n).toString());
    clock.play(); clock.update(10); clock.update(11); expect(clock.ns).toBe((start + 1_000_000_000n).toString());
    clock.setSpeed(2); clock.update(11); clock.update(11.5); expect(clock.ns).toBe((start + 2_000_000_000n).toString());
    clock.pause(); clock.update(100); expect(clock.ns).toBe((start + 2_000_000_000n).toString());
    clock.seek(start.toString()); clock.play(); clock.update(101); clock.update(200); expect(clock.ns).toBe(clock.end);
    clock.end = (start + 5_000_000_000n).toString(); clock.update(201); expect(clock.ns).toBe(clock.end);
    clock.seek((start - 1n).toString()); expect(clock.ns).toBe(start.toString());
    expect(() => clock.setSpeed(0)).toThrow();
  });
});
