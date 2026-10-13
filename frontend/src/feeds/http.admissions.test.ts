import { afterEach, describe, expect, it, vi } from 'vitest';
import { RunsApi, type AdmissionReceipt } from './http';

afterEach(() => { vi.unstubAllGlobals(); });

describe('journaled admission receipts (C7)', () => {
  it('reads id, submitted time, target cut and status from the run journal', async () => {
    const rows: AdmissionReceipt[] = [{ id: 'cmd-1', submitted_at: '2026-10-13T08:00:00Z', target_cut: 130, kind: 'ingress', status: 'admitted' }];
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(rows), { status: 200 })));
    const receipts = await new RunsApi('http://api').admissions('run-1');
    expect(receipts).toEqual([rows[0]]);
  });

  it('surfaces unavailable receipt storage on 404', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('not found', { status: 404 })));
    await expect(new RunsApi('http://api').admissions('run-1')).rejects.toThrow('HTTP 404');
  });

  it('rejects malformed rows visibly instead of silently losing receipts', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify([
      { status: 'applied' }, { id: 'cmd-2', status: 'rejected', detail: 'late' },
    ]), { status: 200 })));
    await expect(new RunsApi('http://api').admissions('run-1')).rejects.toThrow('expected string');
  });

  it('preserves absent fields, recorded Instants, execution data and extension fields', async () => {
    const rows: AdmissionReceipt[] = [
      { id: 'cmd-2', status: 'rejected', detail: 'late' },
      { id: 'cmd-3', status: 'applied', submitted_at: { ns: '16000000000', microstep: 4 },
        requested_at: { ns: '16000000000', microstep: 0 }, target_cut: 226,
        execution_status: 'succeeded', execution_cut: 228,
        execution_result: { injection_point: 'restaurant_delay', exact: { $integer: '9223372036854775815' } },
        idempotency_key: 'actual-request', boundary_ns: '16000000000' },
    ];
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(rows))));
    const receipts = await new RunsApi('http://api').admissions('run-1');
    expect(receipts).toEqual(rows);
    expect(receipts[0]).not.toHaveProperty('target_cut');
  });
});
