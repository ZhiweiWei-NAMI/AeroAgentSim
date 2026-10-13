import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useOperatorInput } from './useOperatorInput';

const controls = vi.hoisted(() => ({
  setFollowing: vi.fn(), hold: vi.fn(async () => undefined), resume: vi.fn(), dispose: vi.fn(),
}));
vi.mock('./operator-source-client', () => ({ createOperatorSource: () => controls }));

afterEach(cleanup);

// A stable api identity, exactly as the real Workbench supplies it.
const api = { base: 'http://api' } as unknown as Parameters<typeof useOperatorInput>[0];

describe('operator input producer independence (T3-001)', () => {
  it('keeps the session source alive while the view browses history; only followingLive gates production', async () => {
    const view = renderHook(({ live }) => useOperatorInput(api, 'run-1', 'live', live), { initialProps: { live: true } });
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await Promise.resolve(); });
    expect(controls.setFollowing).toHaveBeenCalledWith(true);
    expect(controls.dispose).not.toHaveBeenCalled();
    // A seek stops following live but keeps the session live: the producer is
    // paused, not disposed, so no input session restart or watermark loss.
    view.rerender({ live: false });
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await Promise.resolve(); });
    expect(controls.setFollowing).toHaveBeenLastCalledWith(false);
    expect(controls.dispose).not.toHaveBeenCalled();
    // Back to live resumes the same producer cleanly.
    view.rerender({ live: true });
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await Promise.resolve(); });
    expect(controls.setFollowing).toHaveBeenLastCalledWith(true);
  });
  it('a replay session has no producer at all', async () => {
    controls.setFollowing.mockClear(); controls.dispose.mockClear();
    renderHook(() => useOperatorInput(api, 'run-2', 'replay', false));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await Promise.resolve(); });
    expect(controls.setFollowing).not.toHaveBeenCalled();
  });
  it('serializes actions through the shared queue', async () => {
    const order: number[] = [];
    const view = renderHook(() => useOperatorInput(api, 'run-3', 'live', true));
    await act(async () => {
      await Promise.all([
        view.result.current.serialize(async () => { order.push(1); }),
        view.result.current.serialize(async () => { order.push(2); }),
      ]);
    });
    expect(order).toEqual([1, 2]);
  });
});
