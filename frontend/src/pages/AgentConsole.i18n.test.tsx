import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { I18nProvider } from '../i18n/I18nProvider';
import AgentConsole from './AgentConsole';

const mockFeed = vi.hoisted(() => ({ commits: undefined as any[] | undefined, signal: undefined as AbortSignal | undefined }));
vi.mock('../feeds/http', () => ({
  RunsApi: class { constructor(public base:string) {} },
  HttpViewerFeed: class {
    constructor(_api:unknown,_run:string,_mode:string,private status:(value:string)=>void) {}
    async header() { return {start:{ns:'0'}}; }
    async subscribe(_offset:number,receive:(commit:unknown)=>void,signal:AbortSignal) {
      mockFeed.signal = signal;
      if (mockFeed.commits) {
        mockFeed.commits.forEach(receive);
        return;
      }
      const record=(phase:string,data:unknown)=>({id:phase,kind:'event',schemaId:'aas.langgraph.record',at:{ns:'1000000000'},payload:{decision_id:'decision.raw.id',phase,data_json:JSON.stringify(data)}});
      receive({commitIndex:1,at:{ns:'1000000000'},created:[{id:'uav.bravo'}],facts:[],edges:[],receipts:[],messages:[
        record('observation',{initial:{observation:{fields:[{entity:{id:'uav.bravo'},field:'traffic.actor.role',status:'known',value:'candidate'}]}}}),
        record('output',{kind:'event',schema:'traffic.proposal.bid',payload:{accept:true}}),
        record('finished',{outputs:[]}),
      ]});
      this.status('completed');
    }
  },
}));
afterEach(()=>{cleanup();localStorage.clear();mockFeed.commits=undefined;mockFeed.signal=undefined;vi.restoreAllMocks();});

it('offers a Chinese empty state when no run is selected',()=>{
 localStorage.setItem('aeroagentsim.locale','zh-CN');
 render(<MemoryRouter initialEntries={['/agents']}><I18nProvider><AgentConsole/></I18nProvider></MemoryRouter>);
 expect(screen.getByRole('heading',{name:'智能体决策'})).toBeTruthy();
 expect(screen.getByText('选择一次运行')).toBeTruthy();
 expect(screen.getByRole('link',{name:'浏览运行'})).toHaveAttribute('href',expect.stringContaining('/inspect?api='));
});

it('localizes decision phases and proposals without rewriting recorded schema or field IDs',async()=>{
 localStorage.setItem('aeroagentsim.locale','zh-CN');
 render(<MemoryRouter initialEntries={['/agents/run']}><I18nProvider><AgentConsole/></I18nProvider></MemoryRouter>);
 await screen.findByText('拟发布事件');
 expect(screen.getByText('traffic.proposal.bid')).toBeTruthy();
 expect(screen.getByText('uav.bravo.traffic.actor.role')).toBeTruthy();
 expect(screen.getByText(/1 个字段/)).toBeTruthy();
 expect(screen.getByText('已完成')).toBeTruthy();
 expect(screen.getByRole('button',{name:'提议详情'})).toBeTruthy();
 expect(screen.queryByText('Proposed event')).toBeNull();
});

it('keeps multiple proposals and correlates full actual command receipt histories',async()=>{
 const record=(id:string,phase:string,data:unknown)=>({id,kind:'event',schemaId:'aas.agent.record',at:{ns:'1000000000'},payload:{decision_id:'d/1',phase,data_json:JSON.stringify(data)}});
 mockFeed.commits=[
  {commitIndex:1,at:{ns:'1000000000'},created:[{id:'resource',generation:0}],facts:[],edges:[],receipts:[],messages:[
   record('obs-1','observation',{fields:[{field:'position',entity:{id:'resource'},status:'known',value:[1,2,3]}]}),
   record('cmd-a','command',{call_id:'call-a',schema:'move',target:'motion',payload:{entity:'resource'},decision_summary:'First'}),
   record('cmd-b','command',{call_id:'call-b',schema:'move',target:'motion',payload:{entity:'resource'},decision_summary:'Second'}),
  ]},
  {commitIndex:2,at:{ns:'1000000000'},created:[],facts:[],edges:[],messages:[],receipts:[
   {commandId:'kernel-a',status:'submitted'},{commandId:'kernel-a',status:'succeeded'},{commandId:'kernel-b',status:'submitted'},
  ]},
  {commitIndex:3,at:{ns:'1000000000'},created:[],facts:[],edges:[],receipts:[],messages:[
   record('rec-1','receipt',{command_id:'kernel-a',call_id:'call-a',status:'submitted'}),
   record('rec-2','receipt',{command_id:'kernel-a',call_id:'call-a',status:'succeeded'}),
   record('rec-3','receipt',{command_id:'kernel-b',call_id:'call-b',status:'submitted'}),
  ]},
 ];
 const page=render(<MemoryRouter initialEntries={['/agents/run-1?api=http://127.0.0.1:8002']}><AgentConsole/></MemoryRouter>);
 expect(await screen.findAllByText('Proposed call')).toHaveLength(2);
 expect(screen.getAllByText('Committed receipt:')).toHaveLength(3);
 expect(screen.getByText('resource.position')).toBeInTheDocument();
 expect(screen.getByRole('link',{name:'Resource'})).toHaveAttribute('href','/runs/run-1?api=http%3A%2F%2F127.0.0.1%3A8002&mode=replay&entity=resource');
 page.unmount();
 expect(mockFeed.signal?.aborted).toBe(true);
});

it('shows malformed observations as errors',async()=>{
 mockFeed.commits=[
  {commitIndex:1,at:{ns:'1000000000'},created:[],facts:[],edges:[],receipts:[],messages:[
   {id:'bad-obs',kind:'event',schemaId:'aas.agent.record',at:{ns:'1000000000'},payload:{decision_id:'d/1',phase:'observation',data_json:JSON.stringify({fields:[{field:'secret'}]})}},
  ]},
 ];
 render(<MemoryRouter initialEntries={['/agents/run-1']}><AgentConsole/></MemoryRouter>);
 expect(await screen.findByText('A decision record could not be read.')).toBeInTheDocument();
});
