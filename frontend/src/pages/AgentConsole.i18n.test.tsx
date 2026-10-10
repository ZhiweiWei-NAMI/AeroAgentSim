import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { I18nProvider } from '../i18n/I18nProvider';
import AgentConsole from './AgentConsole';

vi.mock('../feeds/http', () => ({
  RunsApi: class { constructor(public base:string) {} },
  HttpViewerFeed: class {
    constructor(_api:unknown,_run:string,_mode:string,private status:(value:string)=>void) {}
    async header() { return {start:{ns:'0'}}; }
    async subscribe(_offset:number,receive:(commit:unknown)=>void) {
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
afterEach(()=>{cleanup();localStorage.clear();vi.restoreAllMocks();});

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
