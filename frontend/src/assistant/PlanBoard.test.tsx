import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useState } from 'react';
import { PlanBoard } from './PlanBoard';
import type { AssistantApi, OperationDescriptors, PlanVersion, Session } from './api';
import type { AssistantSession } from './useAssistant';
import { I18nProvider } from '../i18n/I18nProvider';
import { RunStory } from './RunStory';
import { draftChanges } from './draftChanges';

beforeEach(()=>{vi.clearAllMocks();vi.mocked(assistant.send).mockResolvedValue(undefined);localStorage.setItem('aeroagentsim.locale','en-US');sessionStorage.clear();});
afterEach(() => {cleanup();localStorage.clear();vi.restoreAllMocks();});

const operationDescriptors: OperationDescriptors = {api_version:1,families:[{name:'lowalt',operations:[
  {id:'lowalt.add_place',aliases:['add_place'],title:{en:'Add facility',zh:'添加设施'},parameters:{type:'object'},prerequisites:[]},
  {id:'lowalt.add_fleet',aliases:['add_fleet'],title:{en:'Add UAV group',zh:'添加无人机群'},parameters:{type:'object'},prerequisites:[]},
  {id:'lowalt.add_lowalt_components',aliases:['add_lowalt_components'],title:{en:'Install lowalt components',zh:'安装低空组件'},parameters:{type:'object'},prerequisites:[]},
]}]};

const plan = (version: number, variant?: string): PlanVersion => ({
  version, created_at: '', note: variant ?? '', variant: variant ?? null, parent: version > 1 ? 1 : null,
  base: {}, base_label: 'your scene', title: `Plan ${variant ?? version}`, summary: `Summary ${version}`,
  operations: [], assumptions: ['Three UAVs fly the route'], open_questions: [], operationCopy: {descriptors:operationDescriptors},
  compile: { ok: true, errors: [], warnings: [] }, validation: { state: 'valid' },
  scene: {
    places: [{ id: 'pad-west', role: 'origin', label: 'West pad' }, { id: 'tower', role: 'destination', label: 'Tower' }],
    fleets: [], workloads: [], events: [],
  },
  diff_base: { added: [], changed: [{kind:'scene',id:'environment',label:'Background wind',fields:[{name:'wind.speed_mps',before:0,after:3}]}], removed: [] },
  diff_previous: version === 5 ? {
    added: [{ kind: 'cooperation', id: 'coop', label: 'Fixed handoff rule' }], changed: [], removed: [],
  } : {
    added: [], removed: [],
    // v6 differs only in who decides: the cooperation rule becomes agent-decided.
    changed: [{ kind: 'cooperation', id: 'coop', label: 'Courier decides', fields: [{ name: 'mode', before: 'prescribed', after: 'agent' }] }],
  },
  interactions: undefined, expectations: [],
}) as unknown as PlanVersion;

const session: Session = {
  id: 's1', created_at: '', updated_at: '', workspace_id: null, title: 't', busy: false, last_seq: 0,
  events: [], plan: plan(6, 'B'), plan_versions: [
    { version: 1, note: '', compile_ok: true, validation_state: 'valid', created_at: '' },
    { version: 5, note: 'A', variant: 'A', compile_ok: true, validation_state: 'valid', created_at: '' },
    { version: 6, note: 'B', variant: 'B', compile_ok: true, validation_state: 'valid', created_at: '' },
  ], alternatives: [5, 6], applied: [],
};

const assistant = {
  session, events: [], busy: false, error: undefined, pending: undefined, plan: session.plan, applied: undefined,
  send: vi.fn().mockResolvedValue({ turn: 1, seq: 1 }), cancel: vi.fn(), choose: vi.fn().mockResolvedValue(session),
  apply: vi.fn(), refresh: vi.fn(),
} as unknown as AssistantSession;

const api = {
  plan: vi.fn(async (_id: string, version: number) => plan(version, version === 5 ? 'A' : 'B')),
} as unknown as AssistantApi;

const renderBoard = () => render(<Harness />);

const boardWith = (current: AssistantSession, viewing?: number, onViewVersion = vi.fn()) => <I18nProvider>
  <PlanBoard api={api} assistant={current} stage="proposed" terminal={false} initializing={false}
    canInitialize={false} onInitialize={vi.fn()} onApplied={vi.fn()} onHover={vi.fn()} onStep={vi.fn()}
    onContract={vi.fn()} onSeek={vi.fn()} viewing={viewing} onViewVersion={onViewVersion} onClose={vi.fn()} />
</I18nProvider>;

it.each(['en-US','zh-CN'])('T2-R6-004: retains the chosen answer through 202, gateway refusal, retry and server resolution in %s',async locale=>{
  localStorage.setItem('aeroagentsim.locale',locale);
  const zh=locale==='zh-CN';
  const question='How many drones can charge at once? / 充电站需要同时为几架无人机充电（容量）？';
  const questioned={...plan(4),open_questions:[{question,options:['1 drone / 1 架','3 drones / 3 架']},{question:'Charging power? / 充电功率？'}]};
  const send=vi.fn().mockResolvedValue(undefined);
  const current={...assistant,plan:questioned,session:{...session,plan:questioned,alternatives:[]},send};
  const view=render(boardWith(current));
  fireEvent.click(screen.getByRole('button',{name:zh?'3 架':'3 drones'}));
  const text=zh?'关于“充电站需要同时为几架无人机充电（容量）？”：3 架':'For "How many drones can charge at once?": 3 drones';
  await waitFor(()=>expect(send).toHaveBeenCalledWith(text,{locale}));
  expect(screen.getAllByTestId('as-question')).toHaveLength(2);
  view.rerender(boardWith({...current,busy:true}));
  expect(screen.getByRole('button',{name:zh?'重试':'Retry'})).toBeDisabled();
  // The server accepted the message, but the turn failed with no new plan.
  const events: Session['events']=[{kind:'user',seq:60,at:'',text},{kind:'error',seq:61,at:'',message:'HTTP 429'}, {kind:'turn_finished',seq:62,at:'',turn:9}];
  const failed={...current,events,session:{...current.session,events}};
  view.rerender(boardWith(failed));
  expect(screen.getAllByTestId('as-question')).toHaveLength(2);
  expect(screen.getByTestId('as-question-answer')).toHaveTextContent(zh?'3 架':'3 drones');
  expect(screen.getByTestId('as-question-answer')).toHaveTextContent(zh?'仍未解决':'still unresolved');
  fireEvent.click(screen.getByRole('button',{name:zh?'重试':'Retry'}));
  await waitFor(()=>expect(send).toHaveBeenCalledTimes(2));
  expect(send.mock.calls[1]).toEqual([text,{locale}]);
  // Only a server revision that resolves the question removes its controls.
  const resolved={...questioned,version:5,open_questions:questioned.open_questions.slice(1)};
  view.rerender(boardWith({...failed,plan:resolved,session:{...failed.session,plan:resolved}}));
  expect(screen.getAllByTestId('as-question')).toHaveLength(1);
  expect(screen.queryByTestId('as-question-answer')).toBeNull();
});

it('T2-R6-004: restores an unresolved answer from server events after remount, ignoring the old answered cache',()=>{
  const question='How many drones can charge at once?';
  const questioned={...plan(4),open_questions:[{question,options:['1 drone','3 drones']}]};
  sessionStorage.setItem('aas-answered:s1',JSON.stringify([question]));
  const text=`For "${question}": 3 drones`;
  const events: Session['events']=[{kind:'user',seq:60,at:'',text},{kind:'error',seq:61,at:'',message:'HTTP 429'}];
  const current={...assistant,events:[],plan:questioned,session:{...session,plan:questioned,events}};
  const view=render(boardWith(current));
  expect(screen.getByTestId('as-question')).toBeVisible();
  expect(screen.getByTestId('as-question-answer')).toHaveTextContent('3 drones');
  view.unmount();render(boardWith(current));
  expect(screen.getByTestId('as-question-answer')).toHaveTextContent('still unresolved');
  fireEvent.click(screen.getByRole('button',{name:'Retry'}));
  expect(assistant.send).toHaveBeenCalledWith(text,{locale:'en-US'});
});

it('T2-R6-004: keeps a rejected answer and exposes the submission error with Retry',async()=>{
  const questioned={...plan(4),open_questions:[{question:'Capacity?',options:['3 drones']}]};
  const send=vi.fn().mockRejectedValue(new Error('HTTP 503'));
  render(boardWith({...assistant,send,plan:questioned,session:{...session,plan:questioned}}));
  fireEvent.click(screen.getByRole('button',{name:'3 drones'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('HTTP 503');
  expect(screen.getByTestId('as-question-answer')).toHaveTextContent('3 drones');
  expect(screen.getByRole('button',{name:'Retry'})).toBeEnabled();
  expect(screen.getByTestId('as-question')).toBeVisible();
});

it.each(['en-US','zh-CN'])('T2-R6-002: explains exactly which base revision Confirm saves, then follows the selected four-drone alternative in %s',async locale=>{
  localStorage.setItem('aeroagentsim.locale',locale);
  const zh=locale==='zh-CN';
  const withFleet=(version: number,count: number): PlanVersion=>({...plan(version),scene:{fleets:[{id:'meal-fleet',role:'meal_courier',label:'Meal drones',label_zh:'外卖无人机',count,members:Array.from({length:count},(_,i)=>`meal-${i+1}`)}],places:[],workloads:[],events:[]}});
  const base=withFleet(1,3),alternative=withFleet(2,4);
  const versions=[1,2,3].map(version=>({version,note:'',created_at:'',compile_ok:true,validation_state:'valid' as const}));
  const current={...assistant,plan:base,session:{...session,plan:base,alternatives:[2,3],plan_versions:versions}};
  const view=render(boardWith(current));
  expect(screen.getByTestId('as-confirm-scope')).toHaveTextContent(zh?'v1，共 3 架无人机':'v1 with 3 UAVs');
  expect(screen.getByText(zh?/尚未选中/:/have not been selected/)).toHaveTextContent('v2');
  expect(screen.getByRole('button',{name:zh?'选择备选方案':'Choose an alternative'})).toBeEnabled();
  vi.mocked(api.plan).mockResolvedValueOnce(alternative);
  view.rerender(boardWith(current,2));
  const choose=await screen.findByTestId('as-choose');
  expect(screen.queryByTestId('as-confirm')).toBeNull();
  fireEvent.click(choose);
  expect(assistant.choose).toHaveBeenCalledWith(2);
  view.rerender(boardWith({...current,plan:alternative,session:{...current.session,plan:alternative}}));
  expect(screen.getByTestId('as-confirm-scope')).toHaveTextContent(zh?'v2，共 4 架无人机':'v2 with 4 UAVs');
  vi.mocked(assistant.apply).mockResolvedValueOnce({...applied[0],plan_version:2});
  fireEvent.click(screen.getByTestId('as-confirm'));
  await waitFor(()=>expect(assistant.apply).toHaveBeenCalledWith(2));
});

function Harness() {
  const [viewing, setViewing] = useState<number | undefined>();
  return <I18nProvider>
    <PlanBoard api={api} assistant={assistant} stage="proposed" terminal={false} initializing={false}
      canInitialize={false} onInitialize={() => {}} onApplied={() => {}} onHover={() => {}} onStep={() => {}}
      onContract={() => {}} onSeek={() => {}} viewing={viewing} onViewVersion={setViewing} onClose={() => {}} />
  </I18nProvider>;
}

it('compares alternatives side by side without applying or choosing anything', async () => {
  renderBoard();
  fireEvent.click(await screen.findByTestId('as-alt-compare'));
  // Both variants appear in one view.
  expect(await screen.findByTestId('as-alt-5')).toBeVisible();
  expect(screen.getByTestId('as-alt-6')).toBeVisible();
  // Common items appear once per column; differences are listed per option.
  expect(screen.getAllByText('West pad').length).toBeGreaterThanOrEqual(2);
  expect(screen.getByText('Fixed handoff rule')).toBeVisible();
  expect(screen.getByText('Courier decides')).toBeVisible();
  // Assumptions are shown per option.
  expect(screen.getAllByText('Three UAVs fly the route').length).toBeGreaterThanOrEqual(2);
  // Read-only: the comparison panel offers no apply and no choose; only the footer keeps the explicit confirm path.
  const panel = screen.getByTestId('as-alternatives');
  expect(within(panel).queryByTestId('as-confirm')).toBeNull();
  expect(within(panel).queryByTestId('as-choose')).toBeNull();
  expect(assistant.apply).not.toHaveBeenCalled();
  expect(assistant.choose).not.toHaveBeenCalled();
  // Opening one for review only switches which version the board shows; nothing applies.
  fireEvent.click(screen.getByTestId('as-alt-review-5'));
  await waitFor(() => expect(screen.getByTestId('as-alt-review-5')).toBeDisabled());
  expect(api.plan).toHaveBeenCalledWith('s1', 5, expect.anything());
  expect(assistant.apply).not.toHaveBeenCalled();
});

it('keeps the comparison closed by default and explains the read-only rule', async () => {
  renderBoard();
  expect(screen.queryByTestId('as-alternatives')).toBeNull();
  fireEvent.click(await screen.findByTestId('as-alt-compare'));
  expect(await screen.findByTestId('as-alternatives')).toHaveTextContent(/Confirm and apply/);
  // Closing restores the plain board.
  fireEvent.click(screen.getByRole('button', { name: /Close the side-by-side comparison/i }));
  expect(screen.queryByTestId('as-alternatives')).toBeNull();
});

const rejectedPlan = {
  ...plan(7), validation: { state: 'invalid', errors: ['range check failed'] },
  compile: { ok: false, warnings: [], errors: [{ op_id: 'op-2', field: 'speed_mps', message: 'wind speed must be between 0 and 20 m/s' }] },
} as unknown as PlanVersion;

it('shows per-operation errors with a Revise affordance and never crashes the board', async () => {
  const rejected: Session = { ...session, plan: rejectedPlan };
  const reviseAssistant = { ...assistant, session: rejected, plan: rejectedPlan };
  render(<I18nProvider><PlanBoard api={api} assistant={reviseAssistant} stage="proposed" terminal={false} initializing={false}
    canInitialize={false} onInitialize={() => {}} onApplied={() => {}} onHover={() => {}} onStep={() => {}}
    onContract={() => {}} onSeek={() => {}} viewing={undefined} onViewVersion={() => {}} onClose={() => {}} /></I18nProvider>);
  // The board labels the state as rejected, not as a valid proposal.
  expect(await screen.findByTestId('as-plan-state')).toHaveTextContent(/not applied|未应用/);
  const errors = await screen.findByTestId('as-op-errors');
  expect(errors).toHaveTextContent('op-2');
  expect(errors).toHaveTextContent('wind speed must be between 0 and 20 m/s');
  expect(within(errors).getAllByTestId('as-revise')).toHaveLength(2); // one compile + one validation error
  // Confirm stays disabled for a rejected version.
  expect(screen.getByTestId('as-confirm')).toBeDisabled();
  // Revise names the operation, field and problem to the assistant.
  fireEvent.click(within(errors).getAllByTestId('as-revise')[0]);
  await waitFor(() => expect(reviseAssistant.send).toHaveBeenCalledWith(expect.stringContaining('op-2'),{locale:'en-US'}));
  expect(String(vi.mocked(reviseAssistant.send).mock.calls[0][0])).toContain('speed_mps');
});

const applied = [{ plan_version: 6, workspace_id: 'w1', version: 3, revision: 1, applied_at: '2025-01-01T00:00:00Z',
  report: { created: [], changed: [], removed: [] } }];

it('labels an applied plan as applied and never resurrects it as a draft', async () => {
  const appliedSession: Session = { ...session, applied };
  const appliedAssistant = { ...assistant, session: appliedSession, applied: applied[0] };
  render(<I18nProvider><PlanBoard api={api} assistant={appliedAssistant} stage="proposed" terminal={false} initializing={false}
    canInitialize={false} onInitialize={() => {}} onApplied={() => {}} onHover={() => {}} onStep={() => {}}
    onContract={() => {}} onSeek={() => {}} viewing={undefined} onViewVersion={() => {}} onClose={() => {}} /></I18nProvider>);
  // Stage 'proposed' from the workbench must not win over the server's apply record.
  expect(await screen.findByTestId('as-plan-state')).toHaveTextContent(/Applied as project version 3|已应用为项目版本 3/);
  expect(screen.getByTestId('as-apply-report')).toBeVisible();
  expect(screen.queryByTestId('as-confirm')).toBeNull();
  // Initialize remains the only forward action, and only when the workbench allows it.
  expect(screen.getByTestId('as-initialize')).toBeDisabled(); // canInitialize is false
  // The versions row for the applied plan is not presented as the open draft.
  expect(screen.getByTestId('as-version-5')).toBeVisible();
});

it('marks a superseded applied plan and hides Initialize for it', async () => {
  const superseding = { ...applied[0], plan_version: 99, version: 4, applied_at: '2025-01-02T00:00:00Z' };
  const supersededSession: Session = { ...session, applied: [...applied, superseding] };
  const supersededAssistant = { ...assistant, session: supersededSession, applied: superseding };
  render(<I18nProvider><PlanBoard api={api} assistant={supersededAssistant} stage="configured" terminal={false} initializing={false}
    canInitialize onInitialize={() => {}} onApplied={() => {}} onHover={() => {}} onStep={() => {}}
    onContract={() => {}} onSeek={() => {}} viewing={undefined} onViewVersion={() => {}} onClose={() => {}} /></I18nProvider>);
  expect(await screen.findByTestId('as-plan-state')).toHaveTextContent(/Superseded plan · v6|已被取代的方案 · v6/);
  expect(screen.queryByTestId('as-initialize')).toBeNull();
  expect(screen.getByText(/the project now uses version 4|项目现在使用版本 4/)).toBeVisible();
});

it('labels which plan version the story follows', () => {
  const base = { startNs: undefined, terminal: true, expectations: [] };
  const { rerender } = render(<I18nProvider><RunStory {...base} story={{ run_id: 'r1', terminal: true, plan_version: 3, expectations: [], events: [] }} planVersion={3} /></I18nProvider>);
  expect(screen.getByTestId('as-story-plan')).toHaveTextContent(/plan v3|方案 v3/);
  // A run of another plan says so plainly instead of mixing the two up.
  rerender(<I18nProvider><RunStory {...base} story={{ run_id: 'r1', terminal: true, plan_version: 3, expectations: [], events: [] }} planVersion={6} /></I18nProvider>);
  expect(screen.getByTestId('as-story-plan')).toHaveTextContent(/followed plan v3/);
});

function board(agent:AssistantSession,props:Partial<Parameters<typeof PlanBoard>[0]>={}) {
  return render(<I18nProvider><PlanBoard api={api} assistant={agent} stage="proposed" terminal={false} initializing={false} canInitialize={false}
    onInitialize={vi.fn()} onApplied={vi.fn()} onHover={vi.fn()} onStep={vi.fn()} onContract={vi.fn()} onSeek={vi.fn()} onViewVersion={vi.fn()} onClose={vi.fn()} {...props}/></I18nProvider>);
}
const appliedAssistant=():AssistantSession=>({...assistant,session:{...session,applied},applied:applied[0]});

it.each(['en-US','zh-CN'])('T2-AS-002/T3-R2-006: structured validation errors remain reviewable and localized in %s',async locale=>{
  localStorage.setItem('aeroagentsim.locale',locale);
  const issue={op_id:'op-4',field:'mode',message:'Starting scene has no interactive ingress stream / 起点场景没有交互输入流'};
  const failed:PlanVersion={...plan(7),compile:{ok:false,warnings:[],errors:[issue]},validation:{state:'invalid',errors:[issue]}};
  const agent={...assistant,plan:failed,session:{...session,plan:failed}};
  board(agent);
  const errors=screen.getByTestId('as-op-errors');
  expect(errors).toHaveTextContent('op-4');expect(errors).toHaveTextContent('mode');
  expect(errors).toHaveTextContent(locale==='en-US'?'Starting scene has no interactive ingress stream':'起点场景没有交互输入流');
  expect(errors).not.toHaveTextContent(locale==='en-US'?'起点场景没有交互输入流':'Starting scene has no interactive ingress stream');
  expect(within(errors).getAllByTestId('as-revise')).toHaveLength(1);
  expect(screen.getByTestId('as-confirm')).toBeDisabled();
  fireEvent.click(within(errors).getByTestId('as-revise'));
  await waitFor(()=>expect(agent.send).toHaveBeenCalledWith(expect.stringContaining('op-4'),{locale}));
});

it('T3-R2-006: reviewing an earlier failed revision retains its diagnostics and targeted repair',async()=>{
  vi.mocked(api.plan).mockResolvedValueOnce({...rejectedPlan,version:5,scene:undefined});
  board(assistant,{viewing:5});
  const errors=await screen.findByTestId('as-op-errors');
  expect(errors).toHaveTextContent('op-2');expect(errors).toHaveTextContent('speed_mps');
  expect(screen.queryByTestId('as-confirm')).toBeNull();
  fireEvent.click(within(errors).getAllByTestId('as-revise')[0]);
  await waitFor(()=>expect(assistant.send).toHaveBeenCalledWith(expect.stringContaining('plan version 5, operation op-2'),{locale:'en-US'}));
});

it('T2-AS-002: contains an unexpected board render failure and leaves repair controls available',async()=>{
  const log=vi.spyOn(console,'error').mockImplementation(()=>undefined);
  const malformed={...plan(7),scene:{places:[null]}} as unknown as PlanVersion;
  const agent={...assistant,plan:malformed,session:{...session,plan:malformed}};
  render(<I18nProvider><p>Existing conversation</p><PlanBoard api={api} assistant={agent} stage="proposed" terminal={false} initializing={false} canInitialize={false}
    onInitialize={vi.fn()} onApplied={vi.fn()} onHover={vi.fn()} onStep={vi.fn()} onContract={vi.fn()} onSeek={vi.fn()} onViewVersion={vi.fn()} onClose={vi.fn()}/></I18nProvider>);
  expect(screen.getByTestId('as-board-failed')).toBeVisible();
  expect(screen.getByText('Existing conversation')).toBeVisible();
  expect(screen.queryByTestId('as-confirm')).toBeNull();
  fireEvent.click(screen.getByRole('button',{name:'Ask the assistant to fix it'}));
  await waitFor(()=>expect(agent.send).toHaveBeenCalledWith('Fix the problems in this plan.',{locale:'en-US'}));
  log.mockRestore();
});

it.each(['en-US','zh-CN'])('T2-R2-003/T2-AS-015: Summarise sends the selected run and current UI locale %s',async locale=>{
  localStorage.setItem('aeroagentsim.locale',locale);
  const agent=appliedAssistant();agent.session!.locale=locale==='en-US'?'zh-CN':'en-US';
  board(agent,{stage:'running',terminal:true,runId:'run-selected-not-a-plan'});
  fireEvent.click(screen.getByRole('button',{name:locale==='en-US'?'Summarise the run':'总结运行'}));
  await waitFor(()=>expect(agent.send).toHaveBeenCalledWith(locale==='en-US'?'Summarise what happened in this run compared with the plan.':'对照方案总结这次运行发生了什么。',{run_id:'run-selected-not-a-plan',locale}));
});

it('T2-R2-012: keeps What will I see after confirmation, with current locale context',async()=>{
  const agent=appliedAssistant();
  board(agent,{stage:'configured',canInitialize:true,latestVersion:3});
  expect(screen.getByTestId('as-initialize')).toBeEnabled();
  const button=screen.getByTestId('as-what-to-watch');expect(button).toBeVisible();fireEvent.click(button);
  await waitFor(()=>expect(agent.send).toHaveBeenCalledWith('What should I watch for when I run it?',{locale:'en-US'}));
});

it('T2-R2-003: requires a selected run before a run summary can be requested',()=>{
  board(appliedAssistant(),{stage:'running',terminal:true});
  expect(screen.getByRole('button',{name:'Summarise the run'})).toBeDisabled();
  expect(screen.getByText('Select a run in Records to ask about its results.')).toBeVisible();
  expect(assistant.send).not.toHaveBeenCalled();
});

it('T2-AS-007: explains actual capacity and position changes next to the historical applied plan',()=>{
  const agent=appliedAssistant();
  agent.draftState={dirty:false,latestVersion:4,changes:draftChanges(
    {entities:[{id:'pad-central',facts:{'lowalt.site_capacity':2,'lowalt.site_position':[0,0,0]}}]},
    {entities:[{id:'pad-central',facts:{'lowalt.site_capacity':4,'lowalt.site_position':[50,0,0]}}]})};
  board(agent,{stage:'configured',canInitialize:true});
  const notice=screen.getByTestId('as-draft-changed');
  expect(notice).toHaveTextContent('Draft changed since this plan');
  expect(notice).toHaveTextContent('pad-central');
  expect(notice).toHaveTextContent('2 → 4');expect(notice).toHaveTextContent('0, 0, 0 → 50, 0, 0');
  expect(screen.getByTestId('as-plan-state')).toHaveTextContent('Applied as project version 3');
  expect(screen.getByTestId('as-initialize')).toHaveTextContent('Initialize version 4');
});

it('keeps initialization disabled while the changed draft still needs validation and saving',()=>{
  const agent=appliedAssistant();agent.draftState={dirty:true,latestVersion:3,changes:[{path:['run','until_ns'],before:1000000000,after:600000000000}]};
  board(agent,{stage:'configured',canInitialize:true});
  expect(screen.getByTestId('as-initialize')).toBeDisabled();
  expect(screen.getByText('Validate and save the changed draft as a version before initializing.')).toBeVisible();
  expect(screen.getByTestId('as-what-to-watch')).toBeEnabled();
});

it('T2-R2-011: revision summaries show the real arrangement rather than operation JSON',()=>{
  const operations=[{id:'op-1',op:'add_place',params:{id:'pad-central',role:'charging_pad',at:[0,0,0],capacity:2,power_w:5000}},
    {id:'op-2',op:'add_fleet',params:{id:'fleet-extra',role:'meal_courier',count:3,start:'pad-central'}}];
  const revision={...plan(7),operations,summary:operations.map(op=>`${op.id} ${op.op}: ${JSON.stringify(op.params)}`).join('; ')};
  board({...assistant,plan:revision,session:{...session,plan:revision}});
  const summary=screen.getByTestId('as-arrangement');
  expect(summary).toHaveTextContent('Add facility');expect(summary).toHaveTextContent(/Capacity\s*2/);
  expect(summary).toHaveTextContent(/Charging power \(W\)\s*5000/);expect(summary).toHaveTextContent(/Count\s*3/);
  expect(summary).toHaveTextContent('Meal courier');
  expect(summary).not.toHaveTextContent(/add_place|add_fleet|op-1|\{/);
});

it('T2-R3-001: confirms a validated new revision without changing the selected completed run',async()=>{
  const revision=plan(7);
  const agent={...assistant,plan:revision,applied:undefined,session:{...session,plan:revision,applied}};
  const record={...applied[0],plan_version:7,version:4};
  vi.mocked(agent.apply).mockResolvedValueOnce(record);
  const onApplied=vi.fn();
  board(agent,{stage:'running',terminal:true,runId:'run-project-v3',onApplied});
  expect(screen.getByTestId('as-plan-board')).toHaveAttribute('data-stage','proposed');
  expect(screen.getByTestId('as-confirm')).toBeEnabled();
  expect(screen.queryByTestId('as-tab-story')).toBeNull();
  expect(screen.getByTestId('as-pinned-run-notice')).toHaveTextContent('Run run-project-v3 keeps its fixed version');
  fireEvent.click(screen.getByTestId('as-confirm'));
  await waitFor(()=>expect(onApplied).toHaveBeenCalledWith(record));
  expect(agent.apply).toHaveBeenCalledWith(7);
  expect(agent.session.applied).toEqual(applied);
});

it.each(['pending','invalid'] as const)('T2-R3-001: the run context cannot bypass %s proposal validation',state=>{
  const revision={...plan(7),validation:{state}};
  board({...assistant,plan:revision,applied:undefined,session:{...session,plan:revision,applied}},{stage:'running',terminal:true,runId:'older-run'});
  expect(screen.getByTestId('as-plan-board')).toHaveAttribute('data-stage','proposed');
  expect(screen.getByTestId('as-confirm')).toBeDisabled();
});

it.each(['en-US','zh-CN'])('T4-R3-002: replaces legacy pre-compilation prose with the real expanded changes in %s',locale=>{
  localStorage.setItem('aeroagentsim.locale',locale);
  const operations=[{id:'pad',op:'add_place',params:{id:'qa-pad',role:'charging_pad',at:'center',power_w:6000}}];
  const revision:PlanVersion={...plan(7),operations,
    summary:'No simulation components beyond that.',assumptions:['No lowalt engines, drones, fleets, or orders are added, as requested.'],
    compile:{ok:true,errors:[],warnings:[],operations:[{id:'auto-lowalt-components',op:'add_lowalt_components',params:{}},...operations]},
    diff_base:{added:[{kind:'components',id:'lowalt_components',label:'6 required drone scene engines',label_zh:'6 个必需的无人机场景引擎',
      fields:[{name:'engines',before:[],after:['airspace','behaviour','energy','environment','motion','tasks'],label:'Engines',label_zh:'引擎',
        before_text:'Empty',before_text_zh:'空',after_text:'airspace, behaviour, energy, environment, motion, tasks',after_text_zh:'airspace, behaviour, energy, environment, motion, tasks'}]},
      {kind:'place',id:'qa-pad',label:'qa-pad'}],changed:[],removed:[]}};
  board({...assistant,plan:revision,session:{...session,plan:revision}});
  const changes=screen.getByTestId('as-compiled-changes');
  expect(changes).toHaveTextContent(locale==='en-US'?'What this changes':'实际变更');
  expect(changes).toHaveTextContent(locale==='en-US'?'6 required drone scene engines':'6 个必需的无人机场景引擎');
  expect(changes).toHaveTextContent('airspace, behaviour, energy, environment, motion, tasks');
  expect(changes).toHaveTextContent('qa-pad');
  expect(screen.getByTestId('as-arrangement')).toHaveTextContent(locale==='en-US'?'Install lowalt components':'安装低空组件');
  expect(screen.getByTestId('as-plan-board')).not.toHaveTextContent(/No simulation components|No lowalt engines/);
  expect(screen.queryByText(/Assumptions \(/)).toBeNull();
});

it.each(['en-US','zh-CN'])('T2-R4-003: labels a legacy empty base unchanged and offers alternative selection in %s',locale=>{
  localStorage.setItem('aeroagentsim.locale',locale);
  const unchanged:PlanVersion={...plan(1),base:{workspace:'w1',revision:1},title:'Central charging expansion',summary:'Adds a central charging station and two couriers',diff_base:{added:[],changed:[],removed:[]},diff_previous:null};
  const agent={...assistant,plan:unchanged,session:{...session,plan:unchanged}};
  board(agent);
  expect(screen.getByRole('heading',{name:locale==='en-US'?'Unchanged scene':'场景未改变'})).toBeVisible();
  expect(screen.getByTestId('as-confirm')).toBeDisabled();
  expect(screen.getByText(locale==='en-US'?'This proposal makes no changes to the current scene.':'此提议没有改变当前场景。')).toBeVisible();
  expect(screen.queryByText('Central charging expansion')).toBeNull();
  expect(screen.queryByText('Adds a central charging station and two couriers')).toBeNull();
  fireEvent.click(screen.getByRole('button',{name:locale==='en-US'?'Choose an alternative':'选择备选方案'}));
  expect(screen.getByTestId('as-alternatives')).toBeVisible();
  expect(agent.apply).not.toHaveBeenCalled();
});

it('T2-R4-003: a same-value operation is still unchanged and cannot be confirmed',()=>{
  const unchanged:PlanVersion={...plan(7),base:{workspace:'w1'},operations:[{id:'same-wind',op:'set_wind',params:{speed_mps:3}}],diff_base:{added:[],changed:[],removed:[]}};
  board({...assistant,plan:unchanged,session:{...session,plan:unchanged}});
  expect(screen.getByTestId('as-confirm')).toBeDisabled();
  expect(screen.getByText('There are no changes to confirm.')).toBeVisible();
});

it('T2-R4-001: Confirm passes the authoritative apply record and waits for the shell refresh even before a project loads',async()=>{
  const revision=plan(7);
  const agent={...assistant,plan:revision,session:{...session,plan:revision}};
  const record={...applied[0],plan_version:7,version:4,revision:6};
  vi.mocked(agent.apply).mockResolvedValueOnce(record);
  let finish!:()=>void;
  const refresh=vi.fn(()=>new Promise<void>(resolve=>{finish=resolve;}));
  const onApplied=vi.fn();
  board(agent,{onApplied,onProjectChanged:refresh});
  fireEvent.click(screen.getByTestId('as-confirm'));
  await waitFor(()=>expect(refresh).toHaveBeenCalledTimes(1));
  expect(onApplied).toHaveBeenCalledWith(record);
  expect(screen.getByTestId('as-confirm')).toBeDisabled();
  expect(screen.getByTestId('as-confirm')).toHaveTextContent('Applying…');
  finish();
  await waitFor(()=>expect(screen.getByTestId('as-confirm')).toHaveTextContent('Confirm and apply'));
});

it('T2-R4-001: awaits the existing onApplied refresh callback and displays a refresh failure',async()=>{
  const revision=plan(7);
  const agent={...assistant,plan:revision,session:{...session,plan:revision}};
  vi.mocked(agent.apply).mockResolvedValueOnce({...applied[0],plan_version:7});
  const onApplied=vi.fn().mockRejectedValue(Error('Project refresh failed'));
  board(agent,{onApplied});
  fireEvent.click(screen.getByTestId('as-confirm'));
  expect(await screen.findByRole('alert')).toHaveTextContent('Project refresh failed');
});

it('T2-R4-003: an unchanged alternative stays plainly labelled and cannot be chosen for application',async()=>{
  const unchanged:PlanVersion={...plan(5,'Charging expansion'),base:{workspace:'w1'},summary:'Adds a charging station',diff_base:{added:[],changed:[],removed:[]}};
  vi.mocked(api.plan).mockResolvedValueOnce(unchanged);
  board(assistant,{viewing:5});
  expect(await screen.findByRole('heading',{name:'Unchanged scene'})).toBeVisible();
  expect(screen.getByTestId('as-choose')).toBeDisabled();
  expect(screen.queryByText('Adds a charging station')).toBeNull();
  expect(assistant.choose).not.toHaveBeenCalled();
});
