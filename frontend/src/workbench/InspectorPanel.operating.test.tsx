import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { InspectorPanel } from './InspectorPanel';
import { TemporalFeedStore } from '../feeds/temporal-store';
import { at, emptyCommit, fixtureCommits, fixtureHeader, vehicle } from '../behaviours/extension-fixture';
import { I18nProvider } from '../i18n/I18nProvider';

vi.mock('../behaviours/RunOperations', () => ({ RunOperations: () => null }));
vi.mock('../pages/InspectionPanel', () => ({ InspectionPanel: () => <div data-testid="inspection-fallback" /> }));
vi.mock('../trace/EntityStory', () => ({ MomentsList: () => null, ChangeList: () => null }));

afterEach(()=>{cleanup();localStorage.clear();});

function setup(selected = vehicle) {
  const store = new TemporalFeedStore(fixtureHeader);
  fixtureCommits.forEach(commit => store.ingest(commit));
  store.seek(fixtureCommits.at(-1)!.at.ns);
  return render(<I18nProvider>
    <InspectorPanel api={{} as never} runId="run" store={store} revision={1} selected={selected} mode="replay" canOperate={false} following={false}
      onSelect={vi.fn()} onSeekCut={vi.fn()} onTrace={vi.fn()} onFocus={vi.fn()} onFollow={vi.fn()} onShowGraph={vi.fn()} onClose={vi.fn()} />
  </I18nProvider>);
}

it('leads with the operating state before the full fact list', () => {
  setup();
  const summary = screen.getByTestId('wb-operating');
  const fallback = screen.getByTestId('inspection-fallback');
  // The operating summary renders before the exhaustive InspectionPanel.
  expect(summary.compareDocumentPosition(fallback) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

it('keeps the exhaustive dump under a collapsed All fields disclosure', () => {
  setup();
  const details=screen.getByTestId('wb-all-fields');
  expect(details).not.toHaveAttribute('open');
  expect(screen.getByTestId('inspection-fallback')).not.toBeVisible();
  fireEvent.click(within(details).getByText('All fields'));
  expect(screen.getByTestId('inspection-fallback')).toBeVisible();
});

it.each(['en-US','zh-CN'])('T3-R2-001: labels charging, energy, SoC, use, velocity and lowalt.position separately, with units in %s',locale=>{
  localStorage.setItem('aeroagentsim.locale',locale);
  const definitions=[
    {fieldId:'lowalt.charging',displayName:'Charging / 充电中',unit:null,valueType:'boolean',value:false},
    {fieldId:'lowalt.motion_energy_j',displayName:'Remaining energy / 剩余能量',unit:'J',valueType:'number',value:240000},
    {fieldId:'lowalt.soc',displayName:'State of charge / 荷电状态',unit:'1',valueType:'number',value:1},
    {fieldId:'lowalt.energy_used_j',displayName:'Energy used / 已用能量',unit:'J',valueType:'number',value:0},
    {fieldId:'lowalt.velocity',displayName:'Velocity / 速度向量',unit:'m/s',valueType:'vector',value:[0,0,0]},
    {fieldId:'lowalt.position',displayName:'Position / 位置',unit:'m',valueType:'vector',value:[-13.05,-100.04,0]},
  ];
  const store=new TemporalFeedStore({...fixtureHeader,fields:definitions});
  store.ingest({...emptyCommit(1),created:[{...vehicle,typeId:'vehicle'}],facts:definitions.map(field=>({entity:vehicle,fieldId:field.fieldId,value:field.value,producer:'actual-engine',validFrom:at('0')}))});
  store.seek('0');
  render(<I18nProvider><InspectorPanel api={{} as never} runId="run" store={store} revision={1} selected={vehicle} mode="replay" canOperate={false} following={false}
    onSelect={vi.fn()} onSeekCut={vi.fn()} onTrace={vi.fn()} onFocus={vi.fn()} onFollow={vi.fn()} onShowGraph={vi.fn()} onClose={vi.fn()}/></I18nProvider>);
  const summary=screen.getByTestId('wb-operating');
  const expected=locale==='en-US'?['Charging','Remaining energy (J)','State of charge (ratio)','Energy used (J)','Velocity (m/s)','Position (m)']
    :['充电中','剩余能量 (焦耳)','荷电状态 (比例)','已用能量 (焦耳)','速度向量 (米/秒)','位置 (米)'];
  for(const label of expected)expect(summary).toHaveTextContent(label);
  expect(summary).toHaveTextContent('240,000');
  expect(summary).toHaveTextContent('-13.05');
  expect(summary).toHaveTextContent('-100.04');
  expect(summary).not.toHaveTextContent(/Battery|电量/);
  expect(screen.getByTestId('wb-all-fields')).not.toHaveAttribute('open');
});

it.each(['en-US', 'zh-CN'])('T3-R3-004: shows the recorded task label in %s with the complete transport reference in a tooltip', locale => {
  localStorage.setItem('aeroagentsim.locale', locale);
  const task = { id: 'medical-delivery-01', generation: 0 };
  const ref = { $ref: { epoch: '0', generation: 0, id: task.id, run_id: 'traffic-accident', type_id: 'task' } };
  const store = new TemporalFeedStore({ ...fixtureHeader, entities: [{ id: task.id, type: 'task', label: 'Medical delivery', label_zh: '医疗配送' }],
    fields: [{ fieldId: 'current_task', displayName: 'Current Task / 当前任务', valueType: 'ref' }] });
  store.ingest({ ...emptyCommit(1), created: [{ ...vehicle, typeId: 'vehicle' }, { ...task, typeId: 'task' }],
    facts: [{ entity: vehicle, fieldId: 'current_task', value: ref, producer: 'task-engine', validFrom: at('0') }] });
  store.seek('0');
  const props = { api: {} as never, runId: 'run', store, revision: 1, selected: vehicle, mode: 'replay' as const, canOperate: false, following: false,
    onSelect: vi.fn(), onSeekCut: vi.fn(), onTrace: vi.fn(), onFocus: vi.fn(), onFollow: vi.fn(), onShowGraph: vi.fn(), onClose: vi.fn() };
  const view = render(<I18nProvider><InspectorPanel {...props} /></I18nProvider>);
  const summary = screen.getByTestId('wb-operating');
  const taskValue = within(summary).getByText(locale === 'en-US' ? 'Medical delivery' : '医疗配送');
  expect(taskValue).toHaveAttribute('title', JSON.stringify(ref));
  expect(summary).not.toHaveTextContent(/\$ref|run_id|generation/);
  // The current cut controls label resolution: an absent or other-generation task
  // must not borrow a current/future object's display name.
  store.ingest({ ...emptyCommit(2, '10'), facts: [{ entity: vehicle, fieldId: 'current_task', value: { $ref: { ...ref.$ref, generation: 1 } }, producer: 'task-engine', validFrom: at('10') }] });
  store.seek('10');
  view.rerender(<I18nProvider><InspectorPanel {...props} revision={2} /></I18nProvider>);
  expect(within(summary).getByText(task.id)).toBeVisible();
  expect(summary).not.toHaveTextContent(locale === 'en-US' ? 'Medical delivery' : '医疗配送');
});
