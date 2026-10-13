import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { fixtureCommits, fixtureHeader } from '../behaviours/extension-fixture';
import type { FeedCommit } from '../contracts/viewer-feed';
import { useRunSession } from './useRunSession';

const stream = vi.hoisted(() => ({ deliver: undefined as ((commit: FeedCommit) => void) | undefined, finish: undefined as (() => void) | undefined, status: undefined as ((status:string)=>void) | undefined }));
const control=vi.hoisted(()=>({request:vi.fn(),serialize:vi.fn(async(action:()=>Promise<unknown>)=>action()),following:vi.fn()}));
// This suite isolates journal navigation; source production has its own tests.
vi.mock('./useOperatorInput', () => ({useOperatorInput: (_api:unknown,_run:string,mode:string,following:boolean) => {control.following(mode,following);return {serialize:control.serialize};}}));
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
beforeEach(() => { stream.deliver = undefined; stream.finish = undefined; stream.status=undefined; control.request.mockReset();control.serialize.mockClear();control.following.mockClear();sessionStorage.clear(); });
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
  expect(control.request).not.toHaveBeenCalled();
});

it('T3-001: holds the producer before requesting a real history pause, then resumes on Back to live',async()=>{
  let releaseWrite!:()=>void;
  const write=new Promise<void>(resolve=>{releaseWrite=resolve;});
  control.serialize.mockImplementationOnce(async action=>{await write;return action();});
  control.request.mockImplementation(async(path:string)=>({id:'run',requested:path.endsWith('/pause')?'pause':'resume',status:path.endsWith('/pause')?'paused':'running'}));
  const {result}=await loaded('live');
  act(()=>stream.status!('running'));
  act(()=>result.current!.seekOffset(0));
  expect(result.current!.followingLive).toBe(false);
  expect(control.following).toHaveBeenLastCalledWith('live',false);
  expect(control.serialize).toHaveBeenCalledTimes(1);
  expect(control.request).not.toHaveBeenCalled();
  await act(async()=>releaseWrite());
  await waitFor(()=>expect(control.request).toHaveBeenCalledWith('/v1/runs/run/pause',expect.objectContaining({method:'POST'})));
  expect(result.current!.status).toBe('paused');
  act(()=>result.current!.seekCut(1));
  expect(control.request).toHaveBeenCalledTimes(1);
  act(()=>result.current!.backToLive());
  await waitFor(()=>expect(control.request).toHaveBeenCalledWith('/v1/runs/run/resume',expect.objectContaining({method:'POST'})));
  expect(result.current!.followingLive).toBe(true);
  expect(result.current!.status).toBe('running');
  expect(control.following).toHaveBeenLastCalledWith('live',true);
});

it('T3-001: a quick return to live waits for the history pause acknowledgement before resuming',async()=>{
  let acknowledge!:()=>void;
  const requests:string[]=[];
  control.request.mockImplementation(async(path:string)=>{
    requests.push(path);
    if(path.endsWith('/pause'))await new Promise<void>(resolve=>{acknowledge=resolve;});
    return {id:'run',status:path.endsWith('/pause')?'paused':'running'};
  });
  const {result}=await loaded('live');act(()=>stream.status!('waiting_for_input'));
  act(()=>result.current!.seekCut(1));
  await waitFor(()=>expect(requests).toEqual(['/v1/runs/run/pause']));
  act(()=>result.current!.backToLive());
  expect(requests).toEqual(['/v1/runs/run/pause']);
  await act(async()=>acknowledge());
  await waitFor(()=>expect(requests).toEqual(['/v1/runs/run/pause','/v1/runs/run/resume']));
});

it('T3-001: history in a manually paused run never resumes that run',async()=>{
  const {result}=await loaded('live');act(()=>stream.status!('paused'));
  act(()=>result.current!.seekCut(1));act(()=>result.current!.backToLive());
  await act(async()=>Promise.resolve());
  expect(control.request).not.toHaveBeenCalled();
  expect(result.current!.status).toBe('paused');
});

it('T3-001: exposes a rejected history pause without claiming the run paused',async()=>{
  control.request.mockRejectedValue(Error('HTTP 409: Run has no active worker'));
  const {result}=await loaded('live');act(()=>stream.status!('running'));
  act(()=>result.current!.seekCut(1));
  await waitFor(()=>expect(result.current!.error).toContain('HTTP 409'));
  expect(result.current!.status).toBe('running');
  expect(control.request).toHaveBeenCalledTimes(1);
  act(()=>result.current!.backToLive());
  await waitFor(()=>expect(result.current!.error).toBeUndefined());
  expect(control.request).toHaveBeenCalledTimes(1);
});

it('T3-001: refresh restores history and the server pause, with no implicit resume',async()=>{
  control.request.mockImplementation(async(path:string)=>({id:'run',status:path.endsWith('/pause')?'paused':'running'}));
  const first=await loaded('live');act(()=>stream.status!('running'));act(()=>first.result.current!.seekCut(1));
  await waitFor(()=>expect(first.result.current!.status).toBe('paused'));
  first.unmount();control.request.mockClear();
  const second=await loaded('live');act(()=>stream.status!('paused'));
  await waitFor(()=>expect(second.result.current!.cut).toBe(1));
  expect(second.result.current!.followingLive).toBe(false);
  expect(control.request).not.toHaveBeenCalled();
  act(()=>second.result.current!.backToLive());
  await waitFor(()=>expect(control.request).toHaveBeenCalledWith('/v1/runs/run/resume',expect.objectContaining({method:'POST'})));
});
