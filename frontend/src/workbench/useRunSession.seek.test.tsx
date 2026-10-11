import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { fixtureCommits, fixtureHeader } from '../behaviours/extension-fixture';
import type { FeedCommit } from '../contracts/viewer-feed';
import { useRunSession } from './useRunSession';

const stream = vi.hoisted(() => ({ deliver: undefined as ((commit: FeedCommit) => void) | undefined, finish: undefined as (() => void) | undefined }));
vi.mock('../feeds/http', () => ({
  RunsApi: class {},
  HttpViewerFeed: class {
    async header() { return { ...fixtureHeader, kernelRunId: 'fixture-kernel-run' }; }
    subscribe(_from: number, deliver: (commit: FeedCommit) => void) {
      stream.deliver = deliver;
      return new Promise<void>(resolve => { stream.finish = resolve; });
    }
  },
}));
beforeEach(() => { stream.deliver = undefined; stream.finish = undefined; });
afterEach(cleanup);
async function loaded() {
  const hook = renderHook(() => useRunSession('http://api', 'run', 'replay'));
  await waitFor(() => expect(hook.result.current).toBeDefined());
  act(() => stream.deliver!(fixtureCommits[0]));
  return hook;
}

it('waits for the actual photo cut, preserving the displayed moment until its journal arrives', async () => {
  const { result } = await loaded();
  act(() => result.current!.seekCut(4));
  expect(result.current!.pendingCut).toBe(4);
  expect(result.current!.error).toBeUndefined();
  expect(result.current!.clock.ns).toBe('0');
  act(() => { fixtureCommits.slice(1).forEach(commit => stream.deliver!(commit)); stream.finish!(); });
  await waitFor(() => expect(result.current!.journalComplete).toBe(true));
  await waitFor(() => expect(result.current!.store.viewCursor?.knownAt).toBe(4));
  expect(result.current!.pendingCut).toBeUndefined();
  expect(result.current!.cut).toBe(4);
  expect(result.current!.clock.ns).toBe('9007199254740993');
  expect(result.current!.error).toBeUndefined();
});
it('reports an absent cut only after the complete journal, and recovers on a valid seek', async () => {
  const { result } = await loaded();
  act(() => result.current!.seekCut(99));
  expect(result.current!.error).toBeUndefined();
  act(() => stream.finish!());
  await waitFor(() => expect(result.current!.error).toContain('absent from the complete journal'));
  expect(result.current!.clock.ns).toBe('0');
  act(() => result.current!.seekCut(1));
  expect(result.current!.error).toBeUndefined();
  expect(result.current!.cut).toBe(1);
});
it('lets a new timeline choice cancel a pending photo moment', async () => {
  const { result } = await loaded();
  act(() => result.current!.seekCut(4));
  act(() => result.current!.seekOffset(0));
  act(() => { fixtureCommits.slice(1).forEach(commit => stream.deliver!(commit)); stream.finish!(); });
  await waitFor(() => expect(result.current!.journalComplete).toBe(true));
  expect(result.current!.pendingCut).toBeUndefined();
  expect(result.current!.cut).toBe(1);
  expect(result.current!.clock.ns).toBe('0');
});
