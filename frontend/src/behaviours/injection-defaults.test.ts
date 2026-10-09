import { describe, expect, it } from 'vitest';
import { emptyTransport, newIdempotencyKey, receiptSummary, transportForPoint } from './injection-defaults';
import { TemporalFeedStore } from '../feeds/temporal-store';
import { fixtureHeader } from './extension-fixture';

const point = { id:'accident', stream_id:'operator', command:'aas.runtime.inject_event', target:'behaviour', emits:'accident' };

describe('injection defaults', () => {
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
