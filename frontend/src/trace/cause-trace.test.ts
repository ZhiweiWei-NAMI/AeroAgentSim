import { describe, expect, it } from 'vitest';
import type { TraceModel } from './cause-trace';
import { changesFor, describeExpression, explain, momentsFor, showValue } from './cause-trace';
import { car, incident, traceStore as store } from './trace-fixture';
import { TemporalFeedStore } from '../feeds/temporal-store';
import { emptyCommit, fixtureHeader, vehicle } from '../behaviours/extension-fixture';

describe('cause trace', () => {
  it('shows an actual business event without inventing a chain or denying that it was recorded', () => {
    const value = new TemporalFeedStore(fixtureHeader);
    value.ingest({ ...emptyCommit(1), created: [{ ...vehicle, typeId: 'vehicle' }] });
    value.ingest({ ...emptyCommit(2, '10'), events: [{ at_ns: '10', category: 'logistics', severity: 'notice', text: 'Courier delayed', text_zh: '配送员延迟', entities: [vehicle.id] }] });
    value.seek('10');
    const ref = { kind: 'event' as const, atNs: '10', text: 'Courier delayed' };
    const model = explain(value, ref)!;
    expect(model).toMatchObject({ heading: 'Courier delayed', cut: 2, at: { ns: '10', microstep: 0 }, involved: [vehicle], eventEntities: [vehicle.id], effects: [], followUps: [] });
    expect(model.step).toBeUndefined(); expect(model.trigger).toBeUndefined();
    expect(model.notes[0]).toContain('no linked chain step');
    expect(explain(value, ref, 'zh-CN')).toMatchObject({ heading: '配送员延迟' });
  });
  it('shows the precision needed to understand a strict battery threshold', () => {
    expect(showValue(store(), 0.849659614252597)).toBe('0.8496596');
  });
  it('explains an event as trigger, chain step, before/after writes and follow-ups', () => {
    const model = explain(store(), { kind: 'event', text: 'Car stopped' }) as TraceModel;
    expect(model.focus).toBe('step');
    expect(model.step).toMatchObject({ chainId: 'demo.alert', fromState: 'idle', toState: 'raised', stepId: 'raise' });
    expect(model.trigger?.kind).toBe('predicate');
    expect(model.trigger?.predicate?.expression).toBe("Car 1's speed = 0");
    const read = model.trigger!.predicate!.reads[0];
    expect(read).toMatchObject({ fieldId: 'demo.car.speed_mps', value: '0', writer: 'motion' });
    expect(model.effects).toHaveLength(1);
    expect(model.effects[0]).toMatchObject({ fieldId: 'demo.incident.active', before: 'false', after: 'true', writer: 'behaviour', declaredWriter: 'behaviour', writerOk: true });
    const labels = model.followUps.map(item => item.label).join('|');
    expect(labels).toContain('Announced "Demo Alert Raised"');
    expect(model.followUps.some(item => item.kind === 'step' && item.ref?.kind === 'step')).toBe(true);
    expect(model.involved.map(key => key.id).sort()).toEqual(['car.1', 'incident.1']);
  });
  it('walks upstream from a step trigger to the predicate and downstream to the reacting chain', () => {
    const value = store();
    const step = explain(value, { kind: 'step', instanceId: 'chain:a', revision: 1 })!;
    const predicate = explain(value, step.trigger!.ref!)!;
    expect(predicate.focus).toBe('predicate');
    expect(predicate.followUps[0].ref).toEqual({ kind: 'step', instanceId: 'chain:a', revision: '1' });
    const next = explain(value, step.followUps.find(item => item.kind === 'step')!.ref!)!;
    expect(next.step?.chainId).toBe('demo.watch');
    expect(next.trigger).toMatchObject({ kind: 'event', basis: 'recorded', ref: { kind: 'step', instanceId: 'chain:a' } });
    expect(predicate.followUps[0].basis).toBe('recorded');
  });
  it('attributes a change written by an engine, not a chain', () => {
    const model = explain(store(), { kind: 'change', entity: car, fieldId: 'demo.car.speed_mps', cut: 2 })!;
    expect(model.focus).toBe('change');
    expect(model.effects[0]).toMatchObject({ before: '5', after: '0', writer: 'motion' });
    expect(model.followUps.some(item => item.kind === 'predicate')).toBe(true);
  });
  it('lists moments and per-entity changes with their cause', () => {
    const value = store();
    expect(momentsFor(value, incident).map(row => row.kind)).toEqual(['event', 'step', 'step']);
    const { rows } = changesFor(value, incident);
    expect(rows.map(row => [row.fieldLabel, row.before, row.after])).toEqual([['active', undefined, 'false'], ['active', 'false', 'true']]);
    expect(rows[1].cause).toBe('Demo Alert (step "raise")');
  });
  it('describes expressions in plain words', () => {
    const text = describeExpression({ op: 'and', args: [{ op: 'gt', args: [{ op: 'len', args: [{ field: 'a.b.blocked_by', role: 'v', path: [] }] }, { literal: 0 }] }, { op: 'lte', args: [{ parameter: 'radius_m' }, { literal: 3 }] }] }, role => `the ${role}`, { radius_m: 20 });
    expect(text).toBe("number of the v's blocked by > 0 AND radius m (20) ≤ 3");
  });
  it('reports an unloaded reference as absent', () => {
    expect(explain(store(), { kind: 'step', instanceId: 'missing', revision: 1 })).toBeUndefined();
  });
});

it('follows recorded operator provenance through readiness, dispatch and delivery without linking unrelated orders', () => {
  const value = new TemporalFeedStore(fixtureHeader);
  const message = (id: string, schemaId: string, ns: string, payload: unknown) => ({ id, schemaId, kind: 'event' as const, source: 'tasks', at: { ns, microstep: 0 }, payload, subjects: [vehicle] });
  value.ingest({ ...emptyCommit(1), created: [{ ...vehicle, typeId: 'vehicle' }] });
  value.ingest({ ...emptyCommit(2, '10'), messages: [message('delay', 'lowalt.food.preparation_delayed', '10', { tasks: ['order-a'] })], events: [{ at_ns: '10', category: 'logistics', severity: 'warning', text: 'Preparation delayed', entities: [vehicle.id] }] });
  value.ingest({ ...emptyCommit(3, '20'), messages: [message('ready', 'lowalt.food.ready', '20', { task: 'order-a', provenance: { message_ids: ['delay'] } }), message('unrelated', 'lowalt.food.ready', '20', { task: 'order-b' })] });
  value.ingest({ ...emptyCommit(4, '30'), messages: [message('assigned', 'lowalt.task.assigned', '30', { task: 'order-a', provenance: { message_ids: ['ready', 'delay'] } })] });
  value.ingest({ ...emptyCommit(5, '40'), messages: [message('delivered', 'lowalt.food.delivered', '40', { task: 'order-a', provenance: { message_ids: ['assigned', 'delay'] } })] });
  value.seek('40');
  const model = explain(value, { kind: 'event', atNs: '10', text: 'Preparation delayed', entities: [vehicle.id] })!;
  expect(model.focus).toBe('message');
  expect(model.followUps.map(row => row.ref?.kind === 'message' ? row.ref.messageId : undefined)).toEqual(['ready', 'assigned', 'delivered']);
  expect(model.followUps.every(row => row.basis === 'recorded')).toBe(true);
  expect(explain(value, model.followUps.at(-1)!.ref!)!.trigger!.ref).toMatchObject({ kind: 'message', messageId: 'assigned' });
  expect(explain(value, model.followUps[0].ref!, 'zh-CN')!.followUps).toHaveLength(2);
});

it('labels schema/payload matches as recorded links and same-source facts as same-cut context', () => {
  const model = explain(store(), { kind: 'message', messageId: 'm1', cut: 4 })!;
  expect(model.focus).toBe('message');
  expect(model.sender?.basis).toBe('recorded');
  expect(model.effects).toHaveLength(1);
  expect(model.effects[0]).toMatchObject({ fieldId: 'demo.incident.active', basis: 'same-cut' });
  expect(model.followUps.length).toBeGreaterThan(0);
  expect(model.followUps.every(row => row.basis === 'recorded')).toBe(true);
  // The effect is committed evidence at the same cut, never promoted to a cause.
  expect(model.effects[0].writer).toBe('behaviour');
});

it('uses an injection execution receipt to link the actual ingress command to its authored event', () => {
  const value = new TemporalFeedStore({ ...fixtureHeader, behaviour: { extensions: [], injectionPoints: [{ id: 'delay', emits: 'lowalt.operator.restaurant_delay' }] } });
  const payload = { restaurant: 'east', delay_s: 20 };
  value.ingest({ ...emptyCommit(1), messages: [{ id: 'command-1', schemaId: 'aas.runtime.inject_event', kind: 'command', source: 'operator', target: 'behaviour', at: { ns: '0', microstep: 0 }, payload: { injection_point: 'delay', payload } }] });
  value.ingest({ ...emptyCommit(2, '10'), receipts: [{ commandId: 'command-1', status: 'succeeded', result: { injection_point: 'delay' } }], messages: [{ id: 'event-1', schemaId: 'lowalt.operator.restaurant_delay', kind: 'event', source: 'behaviour', at: { ns: '10', microstep: 0 }, payload }] });
  value.seek('10');
  expect(explain(value, { kind: 'message', messageId: 'command-1', cut: 1 })!.followUps[0].ref).toMatchObject({ kind: 'message', messageId: 'event-1' });
  expect(explain(value, { kind: 'message', messageId: 'event-1', cut: 2 })!.trigger?.ref).toMatchObject({ kind: 'message', messageId: 'command-1' });
});

it('resolves recorded business event keys for closure and rerouting emitted in the same callback', () => {
  const value = new TemporalFeedStore(fixtureHeader);
  const event = (id: string, schemaId: string, provenance: unknown) => ({ id, schemaId, kind: 'event' as const, source: 'tasks', at: { ns: '10', microstep: 0 }, payload: { provenance } });
  value.ingest({ ...emptyCommit(1, '10'), messages: [
    event('closed', 'lowalt.site.closed', { message_ids: [], event_key: 'tasks/operator-1/1' }),
    event('rerouted', 'lowalt.task.replanned', { message_ids: [], event_key: 'tasks/operator-1/2', parent_event_keys: ['tasks/operator-1/1'] }),
    event('other', 'lowalt.task.replanned', { message_ids: [], parent_event_keys: ['tasks/operator-2/1'] }),
  ] });
  expect(explain(value, { kind: 'message', messageId: 'closed', cut: 1 })!.followUps.map(row => row.ref)).toEqual([{ kind: 'message', messageId: 'rerouted', cut: 1 }]);
  expect(explain(value, { kind: 'message', messageId: 'rerouted', cut: 1 })!.trigger?.ref).toEqual({ kind: 'message', messageId: 'closed', cut: 1 });
});

it('continues along the same recorded chain instance across engine and receipt callbacks', () => {
  const value = store(), first = value.chainHistory.find(row => row.value.instanceId === 'chain:a')!.value;
  value.ingest({ ...emptyCommit(6, '3000000000'), chainInstances: [{ ...first, revision: 2, state: 'reported', transitionId: 'report', validFrom: { ns: '3000000000', microstep: 0 } }] });
  value.ingest({ ...emptyCommit(7, '4000000000'), chainInstances: [{ ...first, instanceId: 'unrelated', revision: 2, state: 'completed', transitionId: 'report', validFrom: { ns: '4000000000', microstep: 0 } }] });
  const next = explain(value, { kind: 'step', instanceId: 'chain:a', revision: 1 })!.followUps.filter(row => row.detail.startsWith('continued'));
  expect(next.map(row => row.ref)).toEqual([{ kind: 'step', instanceId: 'chain:a', revision: '2' }]);
});

it('explains recorded kitchen readiness as clock times with bilingual business names', () => {
  const fieldId = 'lowalt.food_expected_ready_ns';
  const value = new TemporalFeedStore({ ...fixtureHeader, fields: [...fixtureHeader.fields, { fieldId, displayName: fieldId, valueType: 'integer', unit: 'ns' }] });
  value.ingest({ ...emptyCommit(1), created: [{ ...vehicle, typeId: 'vehicle' }], facts: [{ entity: vehicle, fieldId, value: 38_000_000_000, producer: 'tasks', validFrom: { ns: '0', microstep: 0 } }] });
  value.ingest({ ...emptyCommit(2, '20000000000'), facts: [{ entity: vehicle, fieldId, value: { $integer: '58000000000' }, producer: 'tasks', validFrom: { ns: '20000000000', microstep: 0 } }] });
  const focus = { kind: 'change' as const, entity: vehicle, fieldId, cut: 2 };
  expect(explain(value, focus, 'zh-CN')!.effects[0]).toMatchObject({ fieldLabel: '预计出餐时间', before: '00:38.0', after: '00:58.0', unit: undefined });
  expect(explain(value, focus)!.effects[0].fieldLabel).toBe('expected readiness');
});
