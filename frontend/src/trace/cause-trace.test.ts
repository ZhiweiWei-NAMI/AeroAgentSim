import { describe, expect, it } from 'vitest';
import type { TraceModel } from './cause-trace';
import { changesFor, describeExpression, explain, momentsFor, showValue } from './cause-trace';
import { car, incident, traceStore as store } from './trace-fixture';

describe('cause trace', () => {
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
    expect(next.trigger).toMatchObject({ kind: 'event', ref: { kind: 'step', instanceId: 'chain:a' } });
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
