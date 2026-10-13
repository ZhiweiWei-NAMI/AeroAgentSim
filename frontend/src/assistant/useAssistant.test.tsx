import { act,cleanup,renderHook,waitFor } from '@testing-library/react';
import { afterEach,expect,it,vi } from 'vitest';
import { AssistantApi,type Session } from './api';
import { useAssistantSession } from './useAssistant';

afterEach(cleanup);

it('T2-R5-001: rejects sends before the session exists, then submits the original prompt',async()=>{
  let resolve!: (session:Session)=>void;
  const send=vi.fn().mockResolvedValue(undefined);
  const api={send,session:vi.fn(()=>new Promise<Session>(accept=>{resolve=accept;})),
    events:vi.fn((_id:string,_seq:number,_wait:number,signal:AbortSignal)=>new Promise((_accept,reject)=>signal.addEventListener('abort',()=>reject(Error('aborted')))))} as unknown as AssistantApi;
  const {result,rerender}=renderHook(({id}:{id:string|undefined})=>useAssistantSession(api,id),{initialProps:{id:undefined as string|undefined}});
  const prompt='Add a central charging station, capacity 2.';
  await act(async()=>{await expect(result.current.send(prompt)).rejects.toThrow('connecting');});
  expect(send).not.toHaveBeenCalled();
  rerender({id:'s'});
  await act(async()=>{await expect(result.current.send(prompt)).rejects.toThrow('connecting');});
  expect(send).not.toHaveBeenCalled();
  const session:Session={id:'s',created_at:'',updated_at:'',workspace_id:'w',title:'Scene',busy:false,last_seq:0,events:[],plan:null,plan_versions:[],alternatives:[],applied:[]};
  await act(async()=>resolve(session));
  await waitFor(()=>expect(result.current.session?.id).toBe('s'));
  await act(async()=>result.current.send(prompt));
  expect(send).toHaveBeenCalledTimes(1);
  expect(send).toHaveBeenCalledWith('s',prompt,undefined);
  expect(result.current.pending?.text).toBe(prompt);
});
