import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { fixtureCommits, fixtureHeader } from '../behaviours/extension-fixture';
import type { FeedCommit } from '../contracts/viewer-feed';
import { useRunSession } from './useRunSession';

const stream = vi.hoisted(() => ({ deliver: undefined as ((commit: FeedCommit) => void) | undefined, finish: undefined as (() => void) | undefined, status: undefined as ((status:string)=>void) | undefined }));
const control=vi.hoisted(()=>({request:vi.fn(),serialize:vi.fn(async(action:()=>Promise<unknown>)=>action()),source:vi.fn()}));
// This suite isolates journal navigation; source production has its own tests.
vi.mock('./useOperatorInput', () => ({useOperatorInput: (...args:unknown[]) => {control.source(...args);return {serialize:control.serialize};}}));
vi.mock('../feeds/http', () => ({
  RunsApi: class {request=control.request;},
  HttpViewerFeed: class {
    constructor(_api:unknown,_id:string,_mode:string,status:(value:string)=>void) { stream.status=status; }
    async header() { return { ...fixtureHeader, kernelRunId: 'fixture-kernel-run' }; }
    subscribe(_from: number, deliver: (commit: FeedCommit) => void) {
      stream.deliver = deliver;
      return new Promise<void>(resolve => { stream.finish = resolve; });
    }
  },
}));
beforeEach(() => { stream.deliver = undefined; stream.finish = undefined; stream.status=undefined; control.request.mockReset();control.serialize.mockClear();control.source.mockClear();sessionStorage.clear(); });
afterEach(cleanup);
async function loaded(mode:'live'|'replay'='replay', initialCut?:number) {
  const hook = renderHook(() => useRunSession('http://api', 'run', mode, initialCut));
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
  expect(control.request).not.toHaveBeenCalled();
});

it.each(['running','waiting_for_input'])('T2-R3-007 / T3-001: seeking and playback leave the %s simulation and source alone',async status=>{
  const {result}=await loaded('live');
  act(()=>{fixtureCommits.slice(1,3).forEach(commit=>stream.deliver!(commit));stream.status!(status);});
  const sourceMode=()=>expect(control.source).toHaveBeenLastCalledWith(expect.anything(),'run','live');
  act(()=>result.current!.seekOffset(0));
  expect(result.current!.followingLive).toBe(false);sourceMode();
  act(()=>result.current!.seekCut(2));sourceMode();
  act(()=>result.current!.togglePlayback());
  expect(result.current!.clock.playing).toBe(true);sourceMode();
  act(()=>result.current!.setSpeed(4));
  act(()=>result.current!.togglePlayback());
  expect(result.current!.clock.playing).toBe(false);sourceMode();
  act(()=>result.current!.backToLive());
  expect(result.current!.followingLive).toBe(true);sourceMode();
  expect(result.current!.status).toBe(status);
  expect(control.serialize).not.toHaveBeenCalled();
  expect(control.request).not.toHaveBeenCalled();
});

it('T4-R3-003: returning to live uses the delivered head and keeps following later commits',async()=>{
  const {result}=await loaded('live');act(()=>stream.status!('waiting_for_input'));
  act(()=>result.current!.seekCut(1));
  act(()=>fixtureCommits.slice(1,3).forEach(commit=>stream.deliver!(commit)));
  await waitFor(()=>expect(result.current!.store.viewCursor?.knownAt).toBe(1));
  expect(result.current!.clock.ns).toBe('0');
  expect(result.current!.clock.end).toBe('10');
  act(()=>result.current!.backToLive());
  expect(result.current!.clock.ns).toBe('10');
  expect(result.current!.store.viewCursor?.knownAt).toBe(3);
  act(()=>stream.deliver!(fixtureCommits[3]));
  await waitFor(()=>expect(result.current!.store.viewCursor?.knownAt).toBe(4));
  expect(result.current!.clock.ns).toBe(fixtureCommits[3].at.ns);
  expect(result.current!.store.liveFollow).toBe(true);
  expect(control.request).not.toHaveBeenCalled();
});

it('T2-R3-007: cancelling an undelivered history cut reattaches immediately without run controls',async()=>{
  const {result}=await loaded('live');act(()=>stream.status!('running'));
  act(()=>result.current!.seekCut(4));
  expect(result.current!.pendingCut).toBe(4);
  act(()=>result.current!.backToLive());
  expect(result.current!.pendingCut).toBeUndefined();
  act(()=>fixtureCommits.slice(1).forEach(commit=>stream.deliver!(commit)));
  await waitFor(()=>expect(result.current!.store.viewCursor?.knownAt).toBe(4));
  expect(result.current!.followingLive).toBe(true);
  expect(result.current!.cut).toBeUndefined();
  expect(control.serialize).not.toHaveBeenCalled();
  expect(control.request).not.toHaveBeenCalled();
});

it('T2-R3-007: history in a manually paused run never resumes that run',async()=>{
  const {result}=await loaded('live');act(()=>stream.status!('paused'));
  act(()=>result.current!.seekCut(1));act(()=>result.current!.backToLive());
  await act(async()=>Promise.resolve());
  expect(control.request).not.toHaveBeenCalled();
  expect(result.current!.status).toBe('paused');
  expect(result.current!.store.liveFollow).toBe(false);
  expect(control.source).toHaveBeenLastCalledWith(expect.anything(),'run','live');
});

it('T2-R3-007: refreshing a historical link observes the server pause without claiming ownership of it',async()=>{
  sessionStorage.setItem('aas-history-pause:http%3A%2F%2Fapi/run',JSON.stringify({cut:1}));
  const first=await loaded('live',1);act(()=>stream.status!('paused'));
  expect(first.result.current!.cut).toBe(1);
  first.unmount();control.request.mockClear();
  const second=await loaded('live',1);act(()=>stream.status!('paused'));
  expect(second.result.current!.cut).toBe(1);
  expect(second.result.current!.followingLive).toBe(false);
  expect(control.request).not.toHaveBeenCalled();
  act(()=>second.result.current!.backToLive());
  expect(second.result.current!.status).toBe('paused');
  expect(control.request).not.toHaveBeenCalled();
  expect(control.serialize).not.toHaveBeenCalled();
});
