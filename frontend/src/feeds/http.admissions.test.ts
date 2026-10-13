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

  it('yields no receipts on 404 (older server) instead of failing the operate panel', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('not found', { status: 404 })));
    expect(await new RunsApi('http://api').admissions('run-1')).toEqual([]);
  });

  it('drops malformed rows but keeps every well-formed one, with unknown target_cut as null', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify([
      { status: 'applied' },
      { id: 'cmd-2', status: 'rejected', detail: 'late' },
      { id: 'cmd-3', status: 'applied' },
    ]), { status: 200 })));
    const receipts = await new RunsApi('http://api').admissions('run-1');
    expect(receipts.map(row => row.id)).toEqual(['cmd-2', 'cmd-3']);
    expect(receipts[0].target_cut).toBeNull();
    expect(receipts[0].detail).toBe('late');
  });
});
