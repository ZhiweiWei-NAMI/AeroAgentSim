import { act,cleanup,fireEvent,render,screen,waitFor } from '@testing-library/react';
import { afterEach,expect,it,vi } from 'vitest';
import { AssistantPanel } from './AssistantPanel';
import type { Connection,PlanVersion,Session } from './api';
import type { AssistantSession } from './useAssistant';
import { I18nProvider } from '../i18n/I18nProvider';

afterEach(()=>{cleanup();localStorage.clear();sessionStorage.clear();});
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

function composer(assistant: AssistantSession) {
  const connection:Connection={configured:true,source:'operator',base_url:'http://api',model:'test',has_key:true,status:'ok',detail:null,tested_at:null};
  return <I18nProvider><AssistantPanel assistant={assistant} connection={connection} connectionSaving={false} onConnect={async()=>undefined} onTest={async()=>undefined}
    project={{id:'first-send',name:'Scene'}} onClose={vi.fn()} onShowPlan={vi.fn()} onShowChanges={vi.fn()}/></I18nProvider>;
}
function readySession(): Session {
  return {id:'s',created_at:'',updated_at:'',workspace_id:'first-send',title:'Scene',busy:false,last_seq:0,events:[],plan:null,plan_versions:[],alternatives:[],applied:[]};
}
function assistantState(send: AssistantSession['send']): AssistantSession {
  return {session:undefined,events:[],plan:undefined,applied:undefined,busy:false,pending:undefined,error:undefined,send,choose:vi.fn(),cancel:vi.fn(),apply:vi.fn(),refresh:vi.fn()};
}

it.each(['en-US','zh-CN'])('T2-R5-001: retains the first prompt during session resolution and sends it once ready in %s',async locale=>{
  localStorage.setItem('aeroagentsim.locale',locale);
  const send=vi.fn().mockResolvedValue(undefined), assistant=assistantState(send);
  const {rerender}=render(composer(assistant));
  const input=screen.getByTestId('as-input'), button=screen.getByTestId('as-send');
  const prompt='Add a central charging station, capacity 2.';
  fireEvent.change(input,{target:{value:prompt}});
  expect(button).toBeDisabled();
  expect(button).toHaveAccessibleName(locale==='en-US'?'Connecting…':'正在连接…');
  expect(screen.getByRole('status')).toHaveTextContent(locale==='en-US'?'Your message will stay here':'你的消息会保留在这里');
  fireEvent.keyDown(input,{key:'Enter'});
  fireEvent.submit(button.closest('form')!);
  expect(send).not.toHaveBeenCalled();
  expect(input).toHaveValue(prompt);
  expect(sessionStorage.getItem('aas-composer:first-send')).toBe(prompt);
  rerender(composer({...assistant,session:readySession()}));
  expect(button).toBeEnabled();
  fireEvent.click(button);
  await waitFor(()=>expect(input).toHaveValue(''));
  expect(send).toHaveBeenCalledTimes(1);
  expect(send).toHaveBeenCalledWith(prompt,{locale});
});

it('T2-R5-001: clears only an accepted submission and retains a rejected message for retry',async()=>{
  let accept!:()=>void;
  const send=vi.fn().mockImplementationOnce(()=>new Promise<void>(resolve=>{accept=resolve;})).mockRejectedValueOnce(Error('HTTP 503'));
  render(composer({...assistantState(send),session:readySession()}));
  const input=screen.getByTestId('as-input'), button=screen.getByTestId('as-send');
  fireEvent.change(input,{target:{value:'Keep this prompt'}});
  fireEvent.click(button);
  expect(input).toHaveValue('Keep this prompt');
  expect(button).toBeDisabled();
  fireEvent.change(input,{target:{value:'Next prompt'}});
  await act(async()=>accept());
  expect(input).toHaveValue('Next prompt');
  fireEvent.click(button);
  await waitFor(()=>expect(button).toBeEnabled());
  expect(input).toHaveValue('Next prompt');
  expect(send).toHaveBeenCalledTimes(2);
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
