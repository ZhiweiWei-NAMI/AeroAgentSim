import { describe, expect, it } from 'vitest';
import { actualTargetSummary, emptyTransport, newIdempotencyKey, receiptSummary, transportForPoint } from './injection-defaults';
import { TemporalFeedStore } from '../feeds/temporal-store';
import { fixtureHeader } from './extension-fixture';

const point = { id:'accident', stream_id:'operator', command:'aas.runtime.inject_event', target:'behaviour', emits:'accident' };

describe('injection defaults', () => {
  it('formats multiple real incident labels without passing the array index as a locale', () => {
    const store = new TemporalFeedStore({ ...fixtureHeader, types: [{ typeId: 'aas:TrafficIncident', displayName: 'Incident', ancestors: [] }] });
    const at = { ns: '0', microstep: 0 };
    const entities = [{ id: 'incident-1', generation: 0 }, { id: 'incident-2', generation: 0 }];
    store.ingest({ commitIndex: 1, at, created: entities.map(entity => ({ ...entity, typeId: 'aas:TrafficIncident' })), removed: [],
      facts: entities.map((entity, index) => ({ entity, fieldId: 'label', value: `Incident ${index + 1}`, producer: 'recorded', validFrom: at })), retracted: [], edges: [], messages: [], receipts: [] });
    store.seek('0', 1);
    expect(actualTargetSummary(point, store)).toContain('Incident 1, Incident 2');
  });
  it('derives operator transport only from actual acknowledged progress and reports missing support', () => {
    const withOperator = transportForPoint(point, { operator: true, operatorNs: 2_000_000_000n });
    expect(withOperator).toEqual({ ...emptyTransport('aas.runtime.inject_event'), target:'behaviour', stream_id:'operator',
      at_ns:'2000000001', clock_id:'canonical', mapping_id:'canonical', numerator:'2000000001', denominator:'1' });
    // Without authored operator progress the transport stays empty: no invented timing.
    expect(transportForPoint(point, { operator: false, operatorNs: 0n })).toEqual({
      ...emptyTransport('aas.runtime.inject_event'), target:'behaviour', stream_id:'operator',
      at_ns:'', clock_id:'', mapping_id:'', numerator:'', denominator:'', idempotency_key:'' });
    const header = { ...fixtureHeader, kernelRunId: undefined };
    const store = new TemporalFeedStore(header as typeof fixtureHeader);
    expect(newIdempotencyKey()).toMatch(/^[0-9a-f-]{36}$/);
    expect(receiptSummary({ disposition: 'accepted', status: 'accepted' })).toContain('execution receipts');
    expect(receiptSummary({ disposition: 'rejected', code: 'LATE_INGRESS' })).toContain('LATE_INGRESS');
    expect(receiptSummary({ stream_id: 'operator' })).toContain('without an admission disposition');
    expect(store.header.kernelRunId).toBeUndefined();
  });
});
