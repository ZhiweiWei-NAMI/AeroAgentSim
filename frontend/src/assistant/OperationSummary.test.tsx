import { act, cleanup, fireEvent, render, renderHook, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { I18nProvider, useI18n } from '../i18n/I18nProvider';
import { AssistantApi, type Operation, type OperationCopy, type OperationDescriptors, type PlanVersion, type Session } from './api';
import { useAssistantSession } from './useAssistant';
import { useOperationCopy } from './useOperationCopy';
import { operationName, PlanSummary } from './OperationSummary';
import { PlanChanges } from './PlanChanges';
import summarySource from './OperationSummary.tsx?raw';
import changesSource from './PlanChanges.tsx?raw';

afterEach(()=>{cleanup();localStorage.clear();vi.unstubAllGlobals();});

const descriptors: OperationDescriptors={api_version:1,families:[
  {name:'garden',operations:[{
    id:'garden.irrigate',title:{en:'Propose irrigation',zh:'提议灌溉'},
    summary:{en:'Configure a watering proposal, not an executed task.',zh:'配置灌溉提议，而非已执行的任务。'},
    parameters:{type:'object',properties:{}},prerequisites:[],
  }]},
  {name:'lowalt',operations:[{
    id:'lowalt.add_fleet',aliases:['add_fleet'],title:{en:'Provider-defined group title',zh:'操作族定义的机群标题'},
    summary:{en:'Provider-defined group summary.',zh:'操作族定义的机群说明。'},
    parameters:{type:'object',properties:{}},prerequisites:[],
  }]},
]};

function plan(operations: Operation[]=[{id:'watering',op:'garden.irrigate',params:{count:2}}]): PlanVersion {
  return {version:1,created_at:'',note:'',base:{},title:'Proposal',
    summary:operations.map(op=>`${op.op}: ${JSON.stringify(op.params)}`).join('; '),operations,
    assumptions:[],open_questions:[],compile:{ok:true,errors:[],warnings:[]},validation:{state:'valid'},
    diff_base:{added:[],changed:[],removed:[]},operationCopy:{descriptors}};
}

it.each(['en-US','zh-CN'])('renders a novel family title and summary in both summary and changes in %s',locale=>{
  localStorage.setItem('aeroagentsim.locale',locale);
  const proposed=plan(),copy=descriptors.families[0].operations[0],language=locale==='zh-CN'?'zh':'en';
  render(<I18nProvider><PlanSummary plan={proposed}/><PlanChanges plan={proposed}/></I18nProvider>);
  for(const row of [screen.getByTestId('as-arrangement'),screen.getByTestId('as-op')]) {
    expect(row).toHaveTextContent(copy.title[language]);
    expect(row).toHaveTextContent(copy.summary![language]);
    expect(row).not.toHaveTextContent(copy.title[language==='en'?'zh':'en']);
  }
});

it.each(['en-US','zh-CN'])('uses supplied copy even for builtin IDs and their explicit legacy aliases in %s',locale=>{
  localStorage.setItem('aeroagentsim.locale',locale);
  const proposed=plan([{id:'canonical',op:'lowalt.add_fleet',params:{}},{id:'legacy',op:'add_fleet',params:{}}]);
  render(<I18nProvider><PlanSummary plan={proposed}/><PlanChanges plan={proposed}/></I18nProvider>);
  const language=locale==='zh-CN'?'zh':'en',copy=descriptors.families[1].operations[0];
  expect(within(screen.getByTestId('as-arrangement')).getAllByText(copy.title[language])).toHaveLength(2);
  for(const row of screen.getAllByTestId('as-op')) {
    expect(row.querySelector('.as-op-title')).toHaveTextContent(copy.title[language]);
    expect(row).toHaveTextContent(copy.summary![language]);
  }
});

it.each(['en-US','zh-CN'])('keeps an unknown namespaced ID and an undeclared short name unknown in %s',locale=>{
  localStorage.setItem('aeroagentsim.locale',locale);
  const proposed=plan([{id:'future',op:'future.add_fleet',params:{}},{id:'short',op:'irrigate',params:{}}]);
  render(<I18nProvider><PlanSummary plan={proposed}/><PlanChanges plan={proposed}/></I18nProvider>);
  const unknown=locale==='zh-CN'?'未知操作':'Unknown operation';
  for(const name of ['future.add_fleet','irrigate']) {
    expect(screen.getAllByText(`${unknown} · ${name}`)).toHaveLength(2);
  }
  expect(screen.queryByText('Provider-defined group title')).toBeNull();
  expect(screen.queryByText('操作族定义的机群标题')).toBeNull();
  expect(screen.queryByText('Propose irrigation')).toBeNull();
  expect(screen.queryByText('提议灌溉')).toBeNull();
});

it('has no builtin copy fallback when descriptors are missing',()=>{
  expect(operationName('add_fleet',(en)=>en)).toBe('Unknown operation · add_fleet');
  expect(operationName('add_fleet',(_en,zh)=>zh)).toBe('未知操作 · add_fleet');
});

function LocaleSwitch() {
  const {locale,setLocale}=useI18n();
  return <button onClick={()=>setLocale(locale==='zh-CN'?'en-US':'zh-CN')}>Switch locale / 切换语言</button>;
}

it('switches copy from the same descriptor snapshot when the active locale changes',()=>{
  localStorage.setItem('aeroagentsim.locale','en-US');
  const proposed=plan();
  render(<I18nProvider><LocaleSwitch/><PlanSummary plan={proposed}/><PlanChanges plan={proposed}/></I18nProvider>);
  expect(screen.getByTestId('as-arrangement')).toHaveTextContent('Propose irrigation');
  fireEvent.click(screen.getByRole('button',{name:'Switch locale / 切换语言'}));
  expect(screen.getByTestId('as-arrangement')).toHaveTextContent('提议灌溉');
  expect(screen.getByTestId('as-op')).toHaveTextContent('配置灌溉提议，而非已执行的任务。');
  expect(proposed.operationCopy?.descriptors).toBe(descriptors);
});

it.each(['en-US','zh-CN'])('shows descriptor failures without hiding the proposal or inventing copy in %s',locale=>{
  localStorage.setItem('aeroagentsim.locale',locale);
  const proposed={...plan(),operationCopy:{error:'Family unavailable / 操作族不可用'}};
  render(<I18nProvider><PlanSummary plan={proposed}/><PlanChanges plan={proposed}/></I18nProvider>);
  for(const alert of screen.getAllByRole('status')) {
    expect(alert).toHaveTextContent(locale==='zh-CN'?'操作说明不可用':'Operation descriptions are unavailable');
    expect(alert).toHaveTextContent(locale==='zh-CN'?'操作族不可用':'Family unavailable');
  }
  expect(screen.getByTestId('as-arrangement')).toHaveTextContent(`${locale==='zh-CN'?'未知操作':'Unknown operation'} · garden.irrigate`);
  expect(screen.getByTestId('as-op')).toHaveTextContent('garden.irrigate');
});

it('renders optional summaries as absent and descriptor strings as text, not markup',()=>{
  localStorage.setItem('aeroagentsim.locale','en-US');
  const proposed=plan(),copy=descriptors.families[0].operations[0];
  const {summary,...titleOnly}=copy;
  proposed.operationCopy={descriptors:{api_version:1,families:[{name:'garden',operations:[{
    ...titleOnly,title:{en:'<img src=x onerror=alert(1)>',zh:'<script>说明</script>'},
  }]}]}};
  const view=render(<I18nProvider><PlanSummary plan={proposed}/><PlanChanges plan={proposed}/></I18nProvider>);
  expect(screen.getAllByText('<img src=x onerror=alert(1)>')).toHaveLength(2);
  expect(view.container.querySelector('img, script')).toBeNull();
  expect(screen.queryByText(copy.summary!.en)).toBeNull();
});

it('labels effective prerequisite operations without changing the authored recipes or approval state',()=>{
  localStorage.setItem('aeroagentsim.locale','en-US');
  const proposed=plan([{id:'group',op:'add_fleet',params:{count:2}}]);
  proposed.compile.operations=[{id:'auto',op:'add_lowalt_components',params:{}},...proposed.operations];
  proposed.operationCopy={descriptors:{...descriptors,families:descriptors.families.map(family=>family.name==='lowalt'
    ? {...family,operations:[...family.operations,{
      id:'lowalt.add_lowalt_components',aliases:['add_lowalt_components'],title:{en:'Descriptor setup title',zh:'说明中的准备标题'},
      summary:{en:'Proposed setup only.',zh:'仅提议准备。'},parameters:{type:'object'},prerequisites:[],
    }]} : family)}};
  render(<I18nProvider><PlanSummary plan={proposed}/><PlanChanges plan={proposed}/></I18nProvider>);
  expect(screen.getByTestId('as-arrangement')).toHaveTextContent('Descriptor setup title');
  expect(screen.getAllByTestId('as-op')[0]).toHaveTextContent('Proposed setup only.');
  expect(proposed.operations).toEqual([{id:'group',op:'add_fleet',params:{count:2}}]);
  expect(proposed.compile.ok).toBe(true);
  expect(proposed.validation.state).toBe('valid');
});

function conversation(): Session {
  const proposed=plan();
  delete proposed.operationCopy;
  return {id:'s1',created_at:'',updated_at:'',workspace_id:null,title:'Conversation',busy:false,last_seq:0,
    events:[],plan:proposed,plan_versions:[],alternatives:[],applied:[]};
}

function LiveConversation({api}: {api: AssistantApi}) {
  const assistant=useAssistantSession(api,'s1');
  return <>
    <button disabled={!assistant.session} onClick={()=>void assistant.send('Continue')}>Send</button>
    <button onClick={()=>void assistant.cancel()}>Cancel</button>
    {assistant.error && <p role="alert">{assistant.error}</p>}
    {assistant.plan && <><PlanSummary plan={assistant.plan}/><PlanChanges plan={assistant.plan}/></>}
  </>;
}

it('starts event polling and allows sending and cancelling while copy never resolves, even after remount',async()=>{
  localStorage.setItem('aeroagentsim.locale','en-US');
  const raw=conversation(),never=new Promise<never>(()=>{}),pollSignals: AbortSignal[]=[];
  const fetch=vi.fn(async(url: string,options?: RequestInit)=>{
    if(url.endsWith('/operations'))return never;
    if(url.includes('/events?')) {
      pollSignals.push(options!.signal!);
      return new Promise<never>((_resolve,reject)=>options!.signal!.addEventListener('abort',
        ()=>reject(new DOMException('Cancelled','AbortError')),{once:true}));
    }
    return {ok:true,text:async()=>JSON.stringify(url.endsWith('/messages')?{turn:1,seq:1}
      :url.endsWith('/cancel')?{cancelled:true}:raw)};
  });
  vi.stubGlobal('fetch',fetch);
  const api=new AssistantApi('http://api');
  expect((await api.create()).id).toBe('s1');
  const view=render(<I18nProvider><LiveConversation api={api}/></I18nProvider>);
  await waitFor(()=>expect(screen.getByRole('button',{name:'Send'})).toBeEnabled());
  expect(screen.getByTestId('as-arrangement')).toHaveTextContent('Unknown operation · garden.irrigate');
  expect(screen.getByTestId('as-op')).toHaveTextContent('Unknown operation · garden.irrigate');
  expect(pollSignals).toHaveLength(1);
  expect(screen.queryByRole('status')).toBeNull();
  fireEvent.click(screen.getByRole('button',{name:'Send'}));
  fireEvent.click(screen.getByRole('button',{name:'Cancel'}));
  await waitFor(()=>expect(fetch.mock.calls.some(([url])=>url.endsWith('/messages'))).toBe(true));
  await waitFor(()=>expect(fetch.mock.calls.some(([url])=>url.endsWith('/cancel'))).toBe(true));
  expect(screen.queryByRole('alert')).toBeNull();
  view.unmount();
  expect(pollSignals[0].aborted).toBe(true);
  render(<I18nProvider><LiveConversation api={api}/></I18nProvider>);
  await waitFor(()=>expect(screen.getByRole('button',{name:'Send'})).toBeEnabled());
  expect(pollSignals).toHaveLength(2);
  expect(fetch.mock.calls.filter(([url])=>url.endsWith('/operations'))).toHaveLength(1);
  expect(fetch.mock.calls.find(([url])=>url.endsWith('/operations'))![1]?.signal).toBeUndefined();
});

function trackCopyObservers(copy: OperationCopy) {
  const subscribe=copy.subscribe!;
  const active=new Set<(value: OperationCopy)=>void>();
  const observers: Array<(value: OperationCopy)=>void>=[];
  vi.spyOn(copy,'subscribe').mockImplementation(listener=>{
    const observer=vi.fn(listener);
    observers.push(observer);
    active.add(observer);
    const unsubscribe=subscribe(observer);
    return ()=>{unsubscribe();active.delete(observer);};
  });
  return {active,observers};
}

it('releases observers on repeated unmounts without cancelling or duplicating a stalled copy request',async()=>{
  let finish!:(text: string)=>void;
  const body=new Promise<string>(resolve=>{finish=resolve;});
  const fetch=vi.fn(async(url: string,options?: RequestInit)=>({ok:true,
    text:()=>url.endsWith('/operations')?body:Promise.resolve(JSON.stringify(conversation()))}));
  vi.stubGlobal('fetch',fetch);
  const api=new AssistantApi('http://api'),copy=(await api.session('s1')).plan!.operationCopy!;
  const {active,observers}=trackCopyObservers(copy),then=vi.spyOn(copy.pending!,'then');
  for(let mount=0;mount<10;mount++) {
    expect((await api.session('s1')).plan!.operationCopy).toBe(copy);
    const view=renderHook(()=>useOperationCopy(copy));
    expect(active.size).toBe(1);
    view.unmount();
    expect(active.size).toBe(0);
  }
  const mounted=renderHook(()=>useOperationCopy(copy));
  expect(active.size).toBe(1);
  expect(then).not.toHaveBeenCalled();
  expect(fetch.mock.calls.filter(([url])=>url.endsWith('/operations'))).toHaveLength(1);
  expect(fetch.mock.calls.find(([url])=>url.endsWith('/operations'))![1]?.signal).toBeUndefined();
  await act(async()=>{finish(JSON.stringify(descriptors));await copy.pending;});
  expect(mounted.result.current?.descriptors).toEqual(descriptors);
  for(const observer of observers.slice(0,-1))expect(observer).not.toHaveBeenCalled();
  expect(observers[observers.length-1]).toHaveBeenCalledTimes(1);
  mounted.unmount();
  expect(active.size).toBe(0);
  // A plan can still hold the original pending wrapper after the cache has settled.
  const late=renderHook(()=>useOperationCopy(copy));
  expect(late.result.current?.descriptors).toEqual(descriptors);
  late.unmount();
  expect(active.size).toBe(0);
  expect(fetch.mock.calls.filter(([url])=>url.endsWith('/operations'))).toHaveLength(1);
});

it('ignores the old pending source after switching subscriptions and resolving it late',async()=>{
  const finishes: Array<(text: string)=>void>=[];
  const fetch=vi.fn(async(url: string)=>({ok:true,text:()=>url.endsWith('/operations')
    ?new Promise<string>(resolve=>{finishes.push(resolve);})
    :Promise.resolve(JSON.stringify({...conversation(),id:url.endsWith('/s1')?'s1':'s2'}))}));
  vi.stubGlobal('fetch',fetch);
  const api=new AssistantApi('http://api');
  const old=(await api.session('s1')).plan!.operationCopy!,current=(await api.session('s2')).plan!.operationCopy!;
  const oldObservers=trackCopyObservers(old),currentObservers=trackCopyObservers(current);
  const view=renderHook(({copy})=>useOperationCopy(copy),{initialProps:{copy:old}});
  expect(oldObservers.active.size).toBe(1);
  view.rerender({copy:current});
  expect(oldObservers.active.size).toBe(0);
  expect(currentObservers.active.size).toBe(1);
  expect(view.result.current).toBe(current);
  await act(async()=>{finishes[0](JSON.stringify(descriptors));await old.pending;});
  expect(view.result.current).toBe(current);
  expect(oldObservers.observers[0]).not.toHaveBeenCalled();
  const latest: OperationDescriptors={api_version:1,families:[]};
  await act(async()=>{finishes[1](JSON.stringify(latest));await current.pending;});
  expect(view.result.current?.descriptors).toEqual(latest);
  view.unmount();
  expect(currentObservers.active.size).toBe(0);
  expect(fetch.mock.calls.filter(([url])=>url.endsWith('/operations'))).toHaveLength(2);
});

it.each(['en-US','zh-CN'])('updates both mounted label views when pending copy arrives late in %s',async locale=>{
  localStorage.setItem('aeroagentsim.locale',locale);
  let finish!:(text: string)=>void;
  const body=new Promise<string>(resolve=>{finish=resolve;});
  const raw=conversation();
  const fetch=vi.fn(async(url: string)=>({ok:true,text:()=>url.endsWith('/operations')?body:Promise.resolve(JSON.stringify(raw))}));
  vi.stubGlobal('fetch',fetch);
  const api=new AssistantApi('http://api'),current=await api.session('s1'),proposed=current.plan!;
  render(<I18nProvider><PlanSummary plan={proposed}/><PlanChanges plan={proposed}/></I18nProvider>);
  const unknown=locale==='zh-CN'?'未知操作':'Unknown operation',language=locale==='zh-CN'?'zh':'en';
  expect(screen.getAllByText(`${unknown} · garden.irrigate`)).toHaveLength(2);
  await act(async()=>{finish(JSON.stringify(descriptors));await proposed.operationCopy!.pending;});
  const copy=descriptors.families[0].operations[0];
  for(const row of [screen.getByTestId('as-arrangement'),screen.getByTestId('as-op')]) {
    expect(row).toHaveTextContent(copy.title[language]);
    expect(row).toHaveTextContent(copy.summary![language]);
  }
  expect(screen.queryByText(`${unknown} · garden.irrigate`)).toBeNull();
  expect((await api.session('s1')).plan?.operationCopy?.descriptors).toEqual(descriptors);
  expect(fetch.mock.calls.filter(([url])=>url.endsWith('/operations'))).toHaveLength(1);
  expect(proposed.operations).toEqual(raw.plan!.operations);
  expect(proposed.validation).toEqual(raw.plan!.validation);
});

it.each(['en-US','zh-CN'])('shows an unobtrusive notice if pending copy fails late in %s',async locale=>{
  localStorage.setItem('aeroagentsim.locale',locale);
  let fail!:(problem: Error)=>void;
  const body=new Promise<string>((_resolve,reject)=>{fail=reject;});
  const fetch=vi.fn(async(url: string)=>({ok:true,text:()=>url.endsWith('/operations')?body:Promise.resolve(JSON.stringify(conversation()))}));
  vi.stubGlobal('fetch',fetch);
  const api=new AssistantApi('http://api'),current=await api.session('s1'),proposed=current.plan!;
  render(<I18nProvider><PlanSummary plan={proposed}/><PlanChanges plan={proposed}/></I18nProvider>);
  expect(screen.queryByRole('status')).toBeNull();
  await act(async()=>{fail(new Error('Family unavailable / 操作族不可用'));await proposed.operationCopy!.pending;});
  expect(screen.getAllByRole('status')).toHaveLength(2);
  for(const notice of screen.getAllByRole('status')) {
    expect(notice).toHaveClass('as-quiet');
    expect(notice).toHaveTextContent(locale==='zh-CN'?'操作说明不可用':'Operation descriptions are unavailable');
  }
  expect(screen.getByTestId('as-op')).toHaveTextContent(`${locale==='zh-CN'?'未知操作':'Unknown operation'} · garden.irrigate`);
  expect(screen.queryByRole('alert')).toBeNull();
  expect((await api.session('s1')).plan?.operationCopy?.error).toContain('Family unavailable');
  expect(fetch.mock.calls.filter(([url])=>url.endsWith('/operations'))).toHaveLength(1);
});

it('does not contain a hardcoded operation-label map in either operation-copy consumer',()=>{
  for(const source of [summarySource,changesSource]) {
    expect(source).not.toMatch(/\b(?:const|let)\s+OPS\b/);
    expect(source).not.toMatch(/\b(?:add|set|remove|update|schedule)_[a-z_]+\s*:\s*\[/);
    expect(source).not.toContain('Add a group of UAVs');
    expect(source).not.toContain('添加无人机编队');
  }
});
