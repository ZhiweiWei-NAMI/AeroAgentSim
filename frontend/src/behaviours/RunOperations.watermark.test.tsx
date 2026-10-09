import { act, cleanup, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { RunsApi } from '../feeds/http';
import { TemporalFeedStore } from '../feeds/temporal-store';
import { emptyCommit, fixtureHeader } from './extension-fixture';
import { ConsoleNotifications } from '../console/Notifications';
import { RunOperations } from './RunOperations';

it('advances an idle operator source from acknowledged watermarks, capped at the authored end', async () => {
  vi.useFakeTimers();
  const api = new RunsApi('http://api'), watermarks: number[] = [];
  const request = vi.spyOn(api, 'request').mockImplementation(async (path, options) => {
    if (path.endsWith('/configuration')) return { scenario: {
      id: 'traffic-accident', run: { advance_ns: 1_000_000_000, until_ns: 2_500_000_000 },
      ingress_streams: [{ id: 'operator', initial_watermark_ns: 0 }], behaviours: [],
    } };
    if (!path.endsWith('/watermark')) throw Error(`Unexpected API request: ${path}`);
    const body = JSON.parse(String(options?.body)) as {stream_id: string; watermark_ns: number};
    expect(body.stream_id).toBe('operator');
    watermarks.push(body.watermark_ns);
    return {contract: 'aeroagentsim.watermark-receipt/v2', stream_id: 'operator', watermark_ns: body.watermark_ns};
  });
  try {
    const store = new TemporalFeedStore(fixtureHeader);
    store.ingest(emptyCommit(1)); store.seek('0', 1);
    await act(async () => { render(<ConsoleNotifications><RunOperations api={api} runId="idle" store={store} onSelect={() => {}} onSeek={() => {}} mode="live" /></ConsoleNotifications>); });
    await act(async () => { await vi.advanceTimersByTimeAsync(4000); });
    expect(store.commits.at(-1)?.at.ns).toBe('0');
    expect(watermarks).toEqual([1_000_000_000, 2_000_000_000, 2_500_000_000]);
    expect(screen.getByText(/closed through 2.5 s/)).toBeVisible();
  } finally { cleanup(); request.mockRestore(); vi.useRealTimers(); }
});
