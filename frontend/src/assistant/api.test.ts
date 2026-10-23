import { afterEach, describe, expect, it, vi } from 'vitest';
import { AssistantApi, compiledParts, type OperationDescriptors, type PlanVersion, type Session } from './api';

afterEach(()=>{vi.unstubAllGlobals();});

const plan = (ok: boolean) => ({
  version: 2, created_at: '', note: '', base: {}, title: 't', summary: '', operations: [], assumptions: [], open_questions: [],
  compile: { ok, errors: ok ? [] : [{ message: 'bad' }], warnings: [] }, validation: { state: ok ? 'pending' : 'invalid' },
  scene: {}, interactions: {}, expectations: [], diff_base: {}, diff_previous: null,
}) as unknown as PlanVersion;

describe('compiledParts', () => {
  it('drops the empty scene parts of a draft that did not compile', () => {
    const shown = compiledParts(plan(false));
    expect(shown.scene).toBeUndefined();
    expect(shown.interactions).toBeUndefined();
    expect(shown.compile.errors).toHaveLength(1);
  });
  it('keeps a compiled plan as it is', () => {
    const compiled = plan(true);
    expect(compiledParts(compiled)).toBe(compiled);
  });
});

it('T2-R2-003/T2-AS-015: transports run_id and the current locale in the supported message context',async()=>{
  const fetch=vi.fn(async()=>({ok:true,text:async()=>'{"turn":1,"seq":2}'}));vi.stubGlobal('fetch',fetch);
  await new AssistantApi('http://api').send('session/id','Summarise this run.',{run_id:'actual-run',locale:'en-US'});
  expect(fetch).toHaveBeenCalledTimes(1);
  const [url,options]=fetch.mock.calls[0] as unknown as [string,RequestInit];
  expect(url).toBe('http://api/v1/assistant/sessions/session%2Fid/messages');
  expect(JSON.parse(String(options.body))).toEqual({text:'Summarise this run.',context:{run_id:'actual-run',locale:'en-US'}});
});

it('sends the active locale when reading a recorded Story', async () => {
  const fetch = vi.fn(async () => ({ ok: true, text: async () => '{"expectations":[],"events":[]}' }));
  vi.stubGlobal('fetch', fetch);
  await new AssistantApi('http://api').story('session/id', 'actual-run', undefined, 'zh-CN');
  expect((fetch.mock.calls[0] as unknown as [string])[0]).toBe('http://api/v1/assistant/sessions/session%2Fid/story/actual-run?locale=zh-CN');
});

const descriptors: OperationDescriptors = {api_version:1,families:[{name:'example',operations:[{
  id:'example.annotate',title:{en:'Propose annotation',zh:'提议标注'},summary:{en:'Configuration only.',zh:'仅配置。'},
  parameters:{type:'object',properties:{}},prerequisites:[],
}]}]};

function session(id='session/id'): Session {
  return {id,created_at:'',updated_at:'',workspace_id:null,title:'',busy:false,last_seq:0,
    events:[],plan:plan(true),plan_versions:[],alternatives:[],applied:[]};
}

it('coalesces in-flight descriptor reads and fetches once per conversation, not per locale',async()=>{
  const fetch=vi.fn(async()=>({ok:true,text:async()=>JSON.stringify(descriptors)}));
  vi.stubGlobal('fetch',fetch);
  const api=new AssistantApi('http://api');
  const first=api.operations('s1'),concurrent=api.operations('s1');
  expect(concurrent).toBe(first);
  expect(await first).toEqual(descriptors);
  expect(await api.operations('s1')).toEqual(descriptors);
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(fetch.mock.calls[0]).toEqual(['http://api/v1/assistant/operations',{method:'GET',signal:undefined}]);
  await api.operations('s2');
  expect(fetch).toHaveBeenCalledTimes(2);
});

it('loads copy on session creation and reuses it for refreshed, chosen and historical plans without changing their recipes',async()=>{
  const raw=session();
  raw.plan!.operations=[{id:'annotation',op:'example.annotate',params:{text:'Proposal only / 仅提议'}}];
  raw.plan!.compile.operations=[...raw.plan!.operations];
  const fetch=vi.fn(async(url: string)=>({ok:true,text:async()=>JSON.stringify(
    url.endsWith('/operations')?descriptors:url.endsWith('/sessions')?{...raw,plan:null}:url.endsWith('/plans/2')?raw.plan:raw)}));
  vi.stubGlobal('fetch',fetch);
  const api=new AssistantApi('http://api');
  const created=await api.create(undefined,'en-US');
  expect(created.plan).toBeNull();
  expect(fetch.mock.calls.filter(([url])=>url.endsWith('/operations'))).toHaveLength(1);
  await api.operations(created.id);
  const refreshed=await api.session('session/id');
  const chosen=await api.choose('session/id',2);
  const historical=await api.plan('session/id',2);
  for(const shown of [refreshed.plan,chosen.plan,historical]) {
    expect(shown?.operationCopy?.descriptors).toEqual(descriptors);
    expect(shown?.operationCopy?.descriptors).toBe(refreshed.plan?.operationCopy?.descriptors);
    expect(shown?.operations).toEqual(raw.plan?.operations);
    expect(shown?.compile).toEqual(raw.plan?.compile);
    expect(shown?.validation).toEqual(raw.plan?.validation);
  }
  expect(fetch.mock.calls.filter(([url])=>url.endsWith('/operations'))).toHaveLength(1);
  const chooseCall=fetch.mock.calls.find(([url])=>url.endsWith('/choose')) as unknown as [string,RequestInit];
  expect(JSON.parse(String(chooseCall[1].body))).toEqual({});
  expect(raw.plan?.operationCopy).toBeUndefined();
  expect(fetch.mock.calls.some(([url])=>/\/apply|\/messages|\/connection/.test(url))).toBe(false);
});

it('records a descriptor failure once and keeps plans readable without replacing it with an empty catalogue',async()=>{
  const fetch=vi.fn(async(url: string)=>url.endsWith('/operations')
    ? {ok:false,status:422,text:async()=>'{"detail":"Family unavailable / 操作族不可用"}'}
    : {ok:true,status:200,text:async()=>JSON.stringify(session())});
  vi.stubGlobal('fetch',fetch);
  const api=new AssistantApi('http://api');
  const first=await api.session('session/id');
  const copy=await first.plan?.operationCopy?.pending ?? first.plan?.operationCopy;
  const again=await api.session('session/id');
  expect(copy?.error).toContain('Family unavailable');
  expect(copy?.descriptors).toBeUndefined();
  expect(again.plan?.operationCopy).toBe(copy);
  expect(first.plan?.compile.ok).toBe(true);
  await expect(api.operations('session/id')).rejects.toMatchObject({status:422,detail:'Family unavailable / 操作族不可用'});
  expect(fetch.mock.calls.filter(([url])=>url.endsWith('/operations'))).toHaveLength(1);
});

it.each(['fetch','body'])('returns usable sessions and plans immediately when descriptor %s never resolves',async stalled=>{
  const raw=session();
  raw.plan!.operations=[{id:'annotation',op:'example.annotate',params:{text:'Still usable'}}];
  const never=new Promise<never>(()=>{});
  const fetch=vi.fn(async(url: string)=>{
    if(url.endsWith('/operations'))return stalled==='fetch'?never:{ok:true,text:()=>never};
    const value=url.endsWith('/sessions')?{...raw,plan:null}:url.endsWith('/plans/2')?raw.plan
      :url.includes('/events?')?{events:[],last_seq:0,busy:false}:url.endsWith('/messages')?{turn:1,seq:1}:raw;
    return {ok:true,text:async()=>JSON.stringify(value)};
  });
  vi.stubGlobal('fetch',fetch);
  const api=new AssistantApi('http://api');
  expect((await api.create()).id).toBe(raw.id);
  const current=await api.session(raw.id),historical=await api.plan(raw.id,2),chosen=await api.choose(raw.id,2);
  expect(current.plan?.operationCopy?.pending).toBeInstanceOf(Promise);
  expect(current.plan?.operationCopy?.descriptors).toBeUndefined();
  expect(current.plan?.operationCopy?.error).toBeUndefined();
  for(const shown of [current.plan,historical,chosen.plan]) {
    expect(shown?.operationCopy).toBe(current.plan?.operationCopy);
    expect(shown?.operations).toEqual(raw.plan!.operations);
    expect(shown?.compile.ok).toBe(true);
  }
  await expect(api.events(raw.id,0,20)).resolves.toMatchObject({busy:false});
  await expect(api.send(raw.id,'Continue')).resolves.toEqual({turn:1,seq:1});
  expect(fetch.mock.calls.filter(([url])=>url.endsWith('/operations'))).toHaveLength(1);
});

it('isolates consumer cancellation while copy is pending and reuses the late copy',async()=>{
  let finishCopy!:(text: string)=>void;
  const body=new Promise<string>(resolve=>{finishCopy=resolve;});
  let planStarted!:()=>void;
  const started=new Promise<void>(resolve=>{planStarted=resolve;});
  const fetch=vi.fn(async(url: string,options?: RequestInit)=>{
    if(url.endsWith('/operations'))return {ok:true,text:()=>body};
    if(url.endsWith('/plans/2') && options?.signal)return new Promise<never>((_resolve,reject)=>{
      options.signal!.addEventListener('abort',()=>reject(new DOMException('Cancelled','AbortError')),{once:true});
      planStarted();
    });
    return {ok:true,text:async()=>JSON.stringify(url.endsWith('/plans/2')?plan(true):session())};
  });
  vi.stubGlobal('fetch',fetch);
  const api=new AssistantApi('http://api'),consumer=new AbortController();
  const current=await api.session('session/id',consumer.signal);
  const copy=current.plan!.operationCopy!;
  expect(copy.pending).toBeInstanceOf(Promise);
  consumer.abort();
  const reading=new AbortController(),cancelled=api.plan('session/id',2,reading.signal);
  const rejection=expect(cancelled).rejects.toMatchObject({name:'AbortError'});
  await started;
  reading.abort();
  await rejection;
  const another=await api.session('session/id');
  expect(another.plan?.operationCopy).toBe(copy);
  const descriptorCall=fetch.mock.calls.find(([url])=>url.endsWith('/operations'))!;
  expect(descriptorCall[1]?.signal).toBeUndefined();
  finishCopy(JSON.stringify(descriptors));
  await expect(copy.pending).resolves.toEqual({descriptors});
  const historical=await api.plan('session/id',2);
  expect(historical.operationCopy).toEqual({descriptors});
  expect(historical.operationCopy?.pending).toBeUndefined();
  expect(fetch.mock.calls.filter(([url])=>url.endsWith('/operations'))).toHaveLength(1);
});

it('rejects unsupported descriptor versions instead of rendering them as known operations',async()=>{
  const fetch=vi.fn(async()=>({ok:true,text:async()=>JSON.stringify({...descriptors,api_version:2})}));
  vi.stubGlobal('fetch',fetch);
  await expect(new AssistantApi('http://api').operations('s1')).rejects.toThrow('Unsupported assistant operation descriptors');
});
