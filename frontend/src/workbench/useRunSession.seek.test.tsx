import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { fixtureCommits, fixtureHeader } from '../behaviours/extension-fixture';
import type { FeedCommit } from '../contracts/viewer-feed';
import { useRunSession } from './useRunSession';

const stream = vi.hoisted(() => ({ deliver: undefined as ((commit: FeedCommit) => void) | undefined, finish: undefined as (() => void) | undefined, status: undefined as ((status:string)=>void) | undefined }));
// This suite isolates journal navigation; source production has its own tests.
vi.mock('./useOperatorInput', () => ({useOperatorInput: () => ({})}));
vi.mock('../feeds/http', () => ({
  RunsApi: class {},
  HttpViewerFeed: class {
    constructor(_api:unknown,_id:string,_mode:string,status:(value:string)=>void) { stream.status=status; }
    async header() { return { ...fixtureHeader, kernelRunId: 'fixture-kernel-run' }; }
    subscribe(_from: number, deliver: (commit: FeedCommit) => void) {
      stream.deliver = deliver;
      return new Promise<void>(resolve => { stream.finish = resolve; });
    }
  },
}));
beforeEach(() => { stream.deliver = undefined; stream.finish = undefined; stream.status=undefined; });
afterEach(cleanup);
async function loaded(mode:'live'|'replay'='replay') {
  const hook = renderHook(() => useRunSession('http://api', 'run', mode));
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

it('returns from a completed run’s historical cut to its real final head without resuming simulation',async()=>{
  const {result}=await loaded('live');
  act(()=>{fixtureCommits.slice(1).forEach(commit=>stream.deliver!(commit));stream.status!('completed');stream.finish!();});
  await waitFor(()=>expect(result.current!.journalComplete).toBe(true));
  act(()=>result.current!.seekCut(1));
  expect(result.current!.followingLive).toBe(false);expect(result.current!.clock.ns).toBe('0');
  expect(result.current!.status).toBe('completed');
  act(()=>result.current!.backToLive());
  expect(result.current!.cut).toBeUndefined();expect(result.current!.followingLive).toBe(true);
  expect(result.current!.clock.ns).toBe(fixtureCommits.at(-1)!.at.ns);
  expect(result.current!.store.viewCursor?.knownAt).toBe(fixtureCommits.at(-1)!.commitIndex);
  expect(result.current!.status).toBe('completed');expect(result.current!.store.liveFollow).toBe(false);
});
