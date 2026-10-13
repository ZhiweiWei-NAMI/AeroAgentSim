import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useOperatorInput } from './useOperatorInput';

const controls = vi.hoisted(() => ({
  create: vi.fn(), hold: vi.fn(async () => undefined), resume: vi.fn(), dispose: vi.fn(),
}));
vi.mock('./operator-source-client', () => ({ createOperatorSource: (...args:unknown[]) => { controls.create(...args); return controls; } }));

beforeEach(() => { vi.clearAllMocks(); localStorage.clear(); });
afterEach(cleanup);

// A stable api identity, exactly as the real Workbench supplies it.
const api = { base: 'http://api' } as unknown as Parameters<typeof useOperatorInput>[0];

describe('operator input producer independence (T3-001)', () => {
  it('T2-R3-007: retains one producer across viewing rerenders with no cursor control', async () => {
    const view = renderHook(() => useOperatorInput(api, 'run-1', 'live'));
    await act(async () => { await Promise.resolve(); });
    view.rerender();
    await act(async () => { await Promise.resolve(); });
    expect(controls.create).toHaveBeenCalledTimes(1);
    expect(controls.create).toHaveBeenCalledWith(api, 'run-1', {
      acknowledged: undefined, onReceipt: expect.any(Function), onError: expect.any(Function),
    });
    expect(controls.dispose).not.toHaveBeenCalled();
    expect(controls.hold).not.toHaveBeenCalled();
    expect(controls.resume).not.toHaveBeenCalled();
  });
  it('a replay session has no producer at all', async () => {
    renderHook(() => useOperatorInput(api, 'run-2', 'replay'));
    await act(async () => { await Promise.resolve(); });
    expect(controls.create).not.toHaveBeenCalled();
  });
  it('attaches and disposes the producer only when the live session lifecycle changes', () => {
    const view = renderHook(({ mode }) => useOperatorInput(api, 'run-2', mode), { initialProps: { mode: 'replay' as 'live' | 'replay' } });
    expect(controls.create).not.toHaveBeenCalled();
    view.rerender({ mode: 'live' });
    expect(controls.create).toHaveBeenCalledTimes(1);
    view.rerender({ mode: 'replay' });
    expect(controls.dispose).toHaveBeenCalledTimes(1);
  });
  it('serializes actions through the shared queue', async () => {
    const order: number[] = [];
    const view = renderHook(() => useOperatorInput(api, 'run-3', 'live'));
    await act(async () => {
      await Promise.all([
        view.result.current.serialize(async () => { order.push(1); }),
        view.result.current.serialize(async () => { order.push(2); }),
      ]);
    });
    expect(order).toEqual([1, 2]);
    expect(controls.hold).toHaveBeenCalledTimes(2);
    expect(controls.resume).toHaveBeenCalledTimes(2);
  });
});
