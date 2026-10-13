import { cleanup,render,screen } from '@testing-library/react';
import { afterEach,expect,it,vi } from 'vitest';
import { AssistantPanel } from './AssistantPanel';
import type { Connection,PlanVersion,Session } from './api';
import type { AssistantSession } from './useAssistant';
import { I18nProvider } from '../i18n/I18nProvider';

afterEach(()=>{cleanup();localStorage.clear();});
it.each(['en-US','zh-CN'])('T3-R2-006/T4-R2-014: structured failed-plan transcript messages stay readable in %s',locale=>{
  localStorage.setItem('aeroagentsim.locale',locale);
  const events=[{seq:1,at:'',kind:'plan_validated' as const,version:1,valid:false,errors:[{op_id:'op-4',field:'mode',message:'Starting scene has no interactive ingress stream / 起点场景没有交互输入流'}]}];
  const session:Session={id:'s',created_at:'',updated_at:'',workspace_id:'w',title:'Scene',busy:false,last_seq:1,events,plan:null,plan_versions:[],alternatives:[],applied:[]};
  const assistant:AssistantSession={session,events,plan:undefined,applied:undefined,busy:false,pending:undefined,error:undefined,send:vi.fn(),choose:vi.fn(),cancel:vi.fn(),apply:vi.fn(),refresh:vi.fn()};
  const connection:Connection={configured:true,source:'operator',base_url:'http://api',model:'test',has_key:true,status:'ok',detail:null,tested_at:null};
  render(<I18nProvider><AssistantPanel assistant={assistant} connection={connection} connectionSaving={false} onConnect={async()=>undefined} onTest={async()=>undefined}
    onClose={vi.fn()} onShowPlan={vi.fn()} onShowChanges={vi.fn()}/></I18nProvider>);
  const transcript=screen.getByTestId('as-transcript');
  expect(transcript).toHaveTextContent(locale==='en-US'?'Starting scene has no interactive ingress stream':'起点场景没有交互输入流');
  expect(transcript).not.toHaveTextContent(locale==='en-US'?'起点场景没有交互输入流':'Starting scene has no interactive ingress stream');
  expect(transcript).not.toHaveTextContent('[object Object]');
});

it.each(['en-US','zh-CN'])('T4-R3-002: the plan chip discloses compiled prerequisites instead of a legacy model note in %s',locale=>{
  localStorage.setItem('aeroagentsim.locale',locale);
  const operations=[{id:'pad',op:'add_place',params:{id:'qa-pad',role:'charging_pad',at:'center'}}];
  const plan:PlanVersion={version:1,created_at:'',note:'No simulation components beyond that.',base:{},title:'Pad',summary:'Only a pad',assumptions:[],open_questions:[],operations,
    compile:{ok:true,errors:[],warnings:[],operations:[{id:'auto',op:'add_lowalt_components',params:{}},...operations]},validation:{state:'valid'}};
  const events=[{seq:1,at:'',kind:'plan' as const,version:1,note:plan.note}];
  const session:Session={id:'s',created_at:'',updated_at:'',workspace_id:'w',title:'Scene',busy:false,last_seq:1,events,plan,plan_versions:[],alternatives:[],applied:[]};
  const assistant:AssistantSession={session,events,plan,applied:undefined,busy:false,pending:undefined,error:undefined,send:vi.fn(),choose:vi.fn(),cancel:vi.fn(),apply:vi.fn(),refresh:vi.fn()};
  render(<I18nProvider><AssistantPanel assistant={assistant} connectionSaving={false} onConnect={async()=>undefined} onTest={async()=>undefined}
    onClose={vi.fn()} onShowPlan={vi.fn()} onShowChanges={vi.fn()}/></I18nProvider>);
  const chip=screen.getByTestId('as-plan-chip');
  expect(chip).toHaveTextContent(locale==='en-US'?'Includes the required drone scene components':'包含必需的无人机场景组件');
  expect(chip).not.toHaveTextContent('No simulation components beyond that');
  expect(chip).not.toHaveTextContent(locale==='en-US'?'包含必需的无人机场景组件':'Includes the required drone scene components');
});
