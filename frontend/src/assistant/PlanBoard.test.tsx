import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useState } from 'react';
import { PlanBoard } from './PlanBoard';
import type { AssistantApi, PlanVersion, Session } from './api';
import type { AssistantSession } from './useAssistant';
import { I18nProvider } from '../i18n/I18nProvider';
import { RunStory } from './RunStory';
import { draftChanges } from './draftChanges';

beforeEach(()=>{vi.clearAllMocks();vi.mocked(assistant.send).mockResolvedValue(undefined);localStorage.setItem('aeroagentsim.locale','en-US');sessionStorage.clear();});
afterEach(() => {cleanup();localStorage.clear();vi.restoreAllMocks();});

const plan = (version: number, variant?: string): PlanVersion => ({
  version, created_at: '', note: variant ?? '', variant: variant ?? null, parent: version > 1 ? 1 : null,
  base: {}, base_label: 'your scene', title: `Plan ${variant ?? version}`, summary: `Summary ${version}`,
  operations: [], assumptions: ['Three UAVs fly the route'], open_questions: [],
  compile: { ok: true, errors: [], warnings: [] }, validation: { state: 'valid' },
  scene: {
    places: [{ id: 'pad-west', role: 'origin', label: 'West pad' }, { id: 'tower', role: 'destination', label: 'Tower' }],
    fleets: [], workloads: [], events: [],
  },
  diff_base: { added: [], changed: [], removed: [] },
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
  expect(await screen.findByTestId('as-plan-state')).toHaveTextContent(/Applied as version 3|已应用为版本 3/);
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
  expect(await screen.findByTestId('as-plan-state')).toHaveTextContent(/Superseded · v6|已被取代 · v6/);
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
  board(agent,{stage:'configured',canInitialize:true});
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
  expect(screen.getByTestId('as-plan-state')).toHaveTextContent('Applied as version 3');
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
  expect(summary).toHaveTextContent('Add a place');expect(summary).toHaveTextContent(/Capacity\s*2/);
  expect(summary).toHaveTextContent(/Charging power \(W\)\s*5000/);expect(summary).toHaveTextContent(/Count\s*3/);
  expect(summary).toHaveTextContent('Meal courier');
  expect(summary).not.toHaveTextContent(/add_place|add_fleet|op-1|\{/);
});
